import asyncio
import json
import math
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
import cv2
import httpx
from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Query
from fastapi.responses import FileResponse
from lib.db import db
from lib.config import DATA_ROOT
from lib.raster import load_analysis, RegistrationError
from lib.storage import put_file, sha256, restore_file
from lib.uploads import save_upload
from lib.reference_match import features_from_path, rank_references, MIN_CONFIDENT_INLIERS
from models.registration import ReferenceRecord

router = APIRouter(prefix='/catalog',tags=['catalog'])
DOWNLOADS = 'https://lroc.im-ldi.com/images/downloads/'
QUICKMAP = 'https://quickmap.lroc.im-ldi.com/'
CATALOG_PATH = DATA_ROOT/'footprints.json'

def curated_catalog():
    rows = json.loads(CATALOG_PATH.read_text())
    for row in rows:
        row.update(status='context-only',scientific_reference=False)
        row['footprint'].update(review_status='unverified context',crs='Not verified from pixels',resolution_m_per_pixel=None)
        row['resolution'] = 'Scientific pixel resolution unverified'
        row['footprint']['review_note'] = 'Inherited descriptive context, not validated pixel georeferencing.'
    return rows

@router.get('')
async def get_catalog(q: str='', limit: int=Query(20,ge=1,le=100)):
    rows = curated_catalog()
    return [row for row in rows if q.lower() in f"{row['title']} {row['product_id']}".lower()][:limit]

@router.get('/sources')
async def sources():
    return [dict(name='QuickMap',url=QUICKMAP,status='viewer',note='Programmatic regional retrieval unverified.'),dict(name='LROC Downloads',url=DOWNLOADS,status='curated products',note='Not an arbitrary-region search API. Posters/previews are not scientific rasters.')]

@router.post('/provider-check')
async def provider_check():
    results=[]
    async with httpx.AsyncClient(timeout=8,follow_redirects=False) as client:
        for name,url in [('QuickMap',QUICKMAP),('LROC Downloads',DOWNLOADS)]:
            try:
                async with client.stream('GET',url) as response:
                    results.append(dict(name=name,reachable=response.status_code==200,http_status=response.status_code,checked_at=datetime.now(timezone.utc).isoformat(),automation_verified=False))
            except httpx.HTTPError:
                results.append(dict(name=name,reachable=False,checked_at=datetime.now(timezone.utc).isoformat(),automation_verified=False))
    await db.provider_checks.insert_one({'results':results,'created_at':datetime.now(timezone.utc).isoformat()})
    return results

def contains_longitude(west,east,longitude):
    if east-west >= 359.999:
        return True
    longitude=(longitude+180)%360-180
    west=(west+180)%360-180
    east=(east+180)%360-180
    return west<=longitude<=east if west<=east else longitude>=west or longitude<=east

def match_footprints(latitude,longitude):
    rows=[]
    for item in curated_catalog():
        footprint=item['footprint']; bounds=footprint['bounds']
        contains=bounds['south']<=latitude<=bounds['north'] and (abs(latitude)==90 or contains_longitude(bounds['west'],bounds['east'],longitude))
        if not contains:
            continue
        lat1,lat2=map(math.radians,[latitude,footprint['center_latitude']])
        delta=math.radians((longitude-footprint['center_longitude']+180)%360-180)
        distance=math.degrees(math.acos(max(-1,min(1,math.sin(lat1)*math.sin(lat2)+math.cos(lat1)*math.cos(lat2)*math.cos(delta)))))
        rows.append(dict(item=item,contains=True,distance_deg=round(distance,3),scientific_pixel_coverage_verified=False))
    return sorted(rows,key=lambda x:x['distance_deg'])

def default_matches():
    # No coordinates supplied: rank curated products by footprint coverage (largest first)
    # so the widest-coverage lunar reference is chosen as the best default match.
    rows=[]
    for item in curated_catalog():
        bounds=item['footprint']['bounds']
        area=abs(bounds['east']-bounds['west'])*abs(bounds['north']-bounds['south'])
        rows.append(dict(item=item,contains=True,distance_deg=None,coverage_area=area,scientific_pixel_coverage_verified=False))
    return sorted(rows,key=lambda x:-x['coverage_area'])

@router.get('/search')
async def search(latitude:float=Query(...,ge=-90,le=90),longitude:float=Query(...,ge=-180,le=180)):
    if not math.isfinite(latitude+longitude):
        raise HTTPException(422,'Coordinates must be finite.')
    return match_footprints(latitude,longitude)

def selection_note(latitude,longitude,match,count):
    if latitude is None:
        return f"No coordinates or source image supplied: '{match['item']['title']}' chosen by largest footprint among {count} curated product(s). Use /catalog/auto-reference/match with the source image for content-based selection."
    return f"'{match['item']['title']}' footprint contains ({latitude:.4f}, {longitude:.4f}); {match['distance_deg']} deg from its centre; {count} covering product(s)."

@router.post('/auto-reference',response_model=ReferenceRecord)
async def auto_reference(latitude:float|None=Query(None,ge=-90,le=90),longitude:float|None=Query(None,ge=-180,le=180),prefer_cache:bool=Query(True)):
    # LROC -> automatic match (by coordinate, or best coverage when omitted) -> persistent cache -> reuse.
    if (latitude is None)!=(longitude is None):
        raise HTTPException(422,'Provide both latitude and longitude, or neither for the best-coverage reference.')
    if latitude is None:
        matches=default_matches()
    else:
        if not math.isfinite(latitude+longitude):
            raise HTTPException(422,'Coordinates must be finite.')
        matches=match_footprints(latitude,longitude)
    if not matches:
        raise HTTPException(404,'No curated LROC reference covers this coordinate. Upload a reference product instead; nothing was substituted.')
    if prefer_cache:
        for match in matches:
            cached=await db.references.find_one({'product_id':match['item']['id'],'is_deleted':False},{'_id':0,'path':0,'manifest':0},sort=[('created_at',-1)])
            if cached:
                cached.update(status='cached-context-only',from_cache=True,matched_product_id=match['item']['id'],matched_product_title=match['item']['title'],match_distance_deg=match['distance_deg'])
                cached['diagnostics']=[selection_note(latitude,longitude,match,len(matches)),'Reused the cached copy; no new LROC request.']
                return ReferenceRecord(**cached)
    errors=[]
    for match in matches:
        item=match['item']; path=DATA_ROOT/'references'/f'{uuid.uuid4()}.png'
        provenance_where=f"({latitude:.4f}, {longitude:.4f})" if latitude is not None else 'best-coverage default (no coordinate supplied)'
        try:
            await asyncio.to_thread(fetch_context,item,path)
            result=await store_reference(path,item['title'],1,f"Auto-fetched LROC context for {provenance_where}; {item['image_url']}; retrieved {datetime.now(timezone.utc).isoformat()}",'context-only')
            await db.references.update_one({'id':result.id},{'$set':{'product_id':item['id']}})
            row=result.model_dump(); row.update(from_cache=False,matched_product_id=item['id'],matched_product_title=item['title'],match_distance_deg=match['distance_deg'])
            row['diagnostics']=[selection_note(latitude,longitude,match,len(matches)),f"Fetched from {item['image_url']} and cached."]+[f'Earlier candidate failed: {e}' for e in errors]
            return ReferenceRecord(**row)
        except Exception as exc:
            path.unlink(missing_ok=True)
            errors.append(f"{item['id']}: {exc}")
    raise HTTPException(503,'LROC retrieval failed for every matching product; no substitute image was used. '+' | '.join(errors[:2]))

FETCH_RETRY_SECONDS=600
_fetch_failures={}

async def ensure_curated_cached(items,diagnostics):
    """Download curated LROC products that are not yet in the reference cache (once)."""
    for item in items:
        if await db.references.find_one({'product_id':item['id'],'is_deleted':False},{'_id':1}):
            continue
        # Do not stall every upload on a provider that just failed.
        if time.monotonic()-_fetch_failures.get(item['id'],-FETCH_RETRY_SECONDS)<FETCH_RETRY_SECONDS:
            diagnostics.append(f"LROC product '{item['title']}' skipped: fetch failed recently; retrying after {FETCH_RETRY_SECONDS//60} min.")
            continue
        path=DATA_ROOT/'references'/f'{uuid.uuid4()}.png'
        try:
            await asyncio.to_thread(fetch_context,item,path)
            result=await store_reference(path,item['title'],1,f"Auto-fetched LROC context for content matching; {item['image_url']}; retrieved {datetime.now(timezone.utc).isoformat()}",'context-only')
            await db.references.update_one({'id':result.id},{'$set':{'product_id':item['id']}})
            diagnostics.append(f"Fetched and cached LROC product '{item['title']}'.")
        except Exception as exc:
            path.unlink(missing_ok=True)
            _fetch_failures[item['id']]=time.monotonic()
            diagnostics.append(f"LROC product '{item['title']}' could not be fetched ({type(exc).__name__}); it was not considered.")

async def match_candidates(product_ids,diagnostics):
    """Every cached reference, newest first, de-duplicated by checksum.

    product_ids=None keeps all references; otherwise curated LROC references are
    limited to those product ids while operator uploads are always considered.
    """
    rows=await db.references.find({'is_deleted':False},{'_id':0}).sort('created_at',-1).to_list(500)
    seen=set(); candidates=[]
    for row in rows:
        # Repeated fetches of one LROC product and re-uploads of one file are ranked once.
        if row['sha256'] in seen or (row.get('product_id') and row['product_id'] in seen):
            continue
        if product_ids is not None and row.get('product_id') and row['product_id'] not in product_ids:
            continue
        try:
            await resolve_reference(row['id'])
        except HTTPException as exc:
            diagnostics.append(f"Reference '{row['title']}' skipped: {exc.detail}")
            continue
        seen.update({row['sha256'],row.get('product_id')}-{None})
        candidates.append(dict(id=row['id'],title=row['title'],product_id=row.get('product_id'),path=row['path'],band=row['metadata']['selected_band'],sha256=row['sha256']))
    return candidates

@router.post('/auto-reference/match',response_model=ReferenceRecord)
async def auto_reference_match(source_image:UploadFile=File(...),source_band:int=Form(1,ge=1,le=32),latitude:float|None=Form(None,ge=-90,le=90),longitude:float|None=Form(None,ge=-180,le=180),fetch_missing:bool=Form(True)):
    """Pick the cached/LROC reference whose content best matches the uploaded source."""
    if (latitude is None)!=(longitude is None):
        raise HTTPException(422,'Provide both latitude and longitude, or neither.')
    if latitude is not None and not math.isfinite(latitude+longitude):
        raise HTTPException(422,'Coordinates must be finite.')
    diagnostics=[]
    if latitude is None:
        curated=curated_catalog(); product_ids=None
        diagnostics.append('No coordinates supplied; every cached reference was ranked by image content.')
    else:
        curated=[m['item'] for m in match_footprints(latitude,longitude)]; product_ids={item['id'] for item in curated}
        diagnostics.append(f"Coordinates ({latitude:.4f}, {longitude:.4f}) limit LROC candidates to {len(curated)} covering footprint(s); operator uploads are always considered.")
    if fetch_missing:
        await ensure_curated_cached(curated,diagnostics)
    path=await save_upload(source_image)
    try:
        try:
            source=await asyncio.to_thread(features_from_path,path,source_band)
        except RegistrationError as exc:
            raise HTTPException(422,str(exc))
    finally:
        path.unlink(missing_ok=True)
    candidates=await match_candidates(product_ids,diagnostics)
    if not candidates:
        raise HTTPException(404,'No reference is cached and none could be fetched. Upload a reference product; nothing was substituted.')
    ranking=await asyncio.to_thread(rank_references,source,candidates)
    best=ranking[0]
    confident=best['inliers']>=MIN_CONFIDENT_INLIERS
    diagnostics.append(f"Source: {source['metadata']['width']}x{source['metadata']['height']} px, {len(source['points'])} SIFT keypoints; {len(ranking)} reference(s) ranked.")
    for rank,row in enumerate(ranking[:5],1):
        diagnostics.append(f"#{rank} {row['title']}: {row['inliers']} inliers / {row['matches']} mutual matches, score {row['score']:.1f}"+(f", scale {row['scale']:.3f}, rotation {row['rotation_deg']:.1f} deg" if row.get('scale') else '')+(f" ({row['note']})" if row.get('note') else ''))
    if not confident:
        diagnostics.append(f"Low confidence: best candidate has fewer than {MIN_CONFIDENT_INLIERS} consistent inliers. The source may not overlap any cached reference; upload or fetch a covering reference.")
    record=await db.references.find_one({'id':best['id']},{'_id':0,'path':0,'manifest':0})
    item=next((row for row in curated_catalog() if row['id']==record.get('product_id')),None)
    record.update(from_cache=True,matched_product_id=record.get('product_id'),matched_product_title=item['title'] if item else record['title'],match_distance_deg=None,match_score=best['score'],match_inliers=best['inliers'],match_confident=confident,candidates=[{k:v for k,v in row.items() if k!='sha256'} for row in ranking],diagnostics=diagnostics)
    return ReferenceRecord(**record)

@router.get('/references',response_model=list[ReferenceRecord])
async def list_references():
    return await db.references.find({'is_deleted':False},{'_id':0,'path':0,'manifest':0}).sort('created_at',-1).to_list(100)

async def store_reference(path,title,band,provenance,status='operator-upload'):
    gray,mask,metadata=await asyncio.to_thread(load_analysis,path,band)
    rid=str(uuid.uuid4())
    preview=DATA_ROOT/'references'/f'{rid}.png'
    await asyncio.to_thread(cv2.imwrite,str(preview),gray)
    manifest=await asyncio.to_thread(put_file,path,f'references/{rid}{path.suffix}','image/tiff' if path.suffix in {'.tif','.tiff'} else 'application/octet-stream')
    row=dict(id=rid,title=title[:200],status=status,image_url=f'/api/catalog/references/{rid}/preview',metadata=metadata,provenance=provenance,sha256=manifest['sha256'],path=str(path),manifest=manifest,is_deleted=False,created_at=datetime.now(timezone.utc).isoformat())
    await db.references.insert_one(dict(row))
    return ReferenceRecord(**{k:v for k,v in row.items() if k not in {'path','manifest'}})

@router.post('/references',response_model=ReferenceRecord)
async def upload_reference(reference_image:UploadFile=File(...),band:int=Form(1,ge=1,le=32),provenance:str=Form(...,min_length=5,max_length=2000)):
    path=await save_upload(reference_image,'references')
    try:
        return await store_reference(path,Path(reference_image.filename or 'Reference').name,band,provenance)
    except RegistrationError as exc:
        path.unlink(missing_ok=True)
        raise HTTPException(422,str(exc))
    except Exception as exc:
        path.unlink(missing_ok=True)
        raise HTTPException(503,'Reference storage unavailable. Nothing was substituted.') from exc

def fetch_context(item,path):
    # Only fixed curated URLs; no user URL, credentials, redirects or internal hosts.
    url=item['image_url']
    parsed=urlparse(url)
    if parsed.scheme!='https' or parsed.hostname!='lroc.im-ldi.com' or not parsed.path.startswith('/data/support/popular_downloads/'):
        raise RegistrationError('Reference URL is not allowlisted.')
    total=0
    with httpx.stream('GET',url,timeout=12,follow_redirects=False) as response:
        response.raise_for_status()
        with open(path,'wb') as file:
            for chunk in response.iter_bytes(65536):
                total+=len(chunk)
                if total>16*1024**2:
                    raise RegistrationError('Context preview exceeds 16 MiB fetch limit.')
                file.write(chunk)

@router.post('/context/{item_id}',response_model=ReferenceRecord)
async def cache_context(item_id:str,use_cache:bool=False):
    item=next((row for row in curated_catalog() if row['id']==item_id),None)
    if not item:
        raise HTTPException(404,'Unknown curated product.')
    if use_cache:
        cached=await db.references.find_one({'product_id':item_id,'is_deleted':False},{'_id':0,'path':0,'manifest':0},sort=[('created_at',-1)])
        if not cached:
            raise HTTPException(404,'No explicitly cached preview for this product.')
        cached['status']='cached-context-only'
        return ReferenceRecord(**cached)
    path=DATA_ROOT/'references'/f'{uuid.uuid4()}.png'
    try:
        await asyncio.to_thread(fetch_context,item,path)
        result=await store_reference(path,item['title'],1,f"Context preview only; {item['image_url']}; retrieved {datetime.now(timezone.utc).isoformat()}",'context-only')
        await db.references.update_one({'id':result.id},{'$set':{'product_id':item_id}})
        return result
    except Exception as exc:
        path.unlink(missing_ok=True)
        raise HTTPException(503,'Provider/context fetch unavailable. Select an explicitly cached reference or upload one; no fallback image was used.') from exc

async def resolve_reference(rid):
    row=await db.references.find_one({'id':rid,'is_deleted':False},{'_id':0})
    if not row:
        raise HTTPException(422,'Select or upload a specific reference. Blind automatic retrieval is unsupported.')
    path=Path(row['path'])
    if not path.exists():
        try:
            await asyncio.to_thread(restore_file,row['manifest'],path)
        except Exception as exc:
            raise HTTPException(503,'Cached reference unavailable; no substitute was used.') from exc
    if await asyncio.to_thread(sha256,path)!=row['sha256']:
        raise HTTPException(409,'Reference checksum failed; upload the product again.')
    return row

@router.get('/references/{rid}/preview')
async def reference_preview(rid:str):
    row=await resolve_reference(rid)
    path=DATA_ROOT/'references'/f'{row["id"]}.png'
    if not path.exists():
        gray,_,_=await asyncio.to_thread(load_analysis,row['path'],row['metadata']['selected_band'])
        await asyncio.to_thread(cv2.imwrite,str(path),gray)
    return FileResponse(path,media_type='image/png')

@router.delete('/references/{rid}')
async def delete_reference(rid:str):
    row=await db.references.find_one({'id':rid,'is_deleted':False},{'_id':0})
    if not row:
        raise HTTPException(404,'Reference not found.')
    if await db.runs.count_documents({'reference_id':rid,'status':{'$in':['queued','running']}}):
        raise HTTPException(409,'Reference is in use by a job.')
    await db.references.update_one({'id':rid},{'$set':{'is_deleted':True}})
    Path(row['path']).unlink(missing_ok=True)
    (DATA_ROOT/'references'/f'{rid}.png').unlink(missing_ok=True)
    return {'is_deleted':True,'physical_object_retention':'Provider storage has no delete API; object not physically erased.'}