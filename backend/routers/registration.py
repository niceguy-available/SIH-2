import asyncio
import json
import uuid
import shutil
from pathlib import Path
import numpy as np
from fastapi import APIRouter, File, Form, UploadFile, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from models.registration import RunRecord, Thresholds, CheckpointSubmission, Adjustment
from lib.db import db
from lib.config import DATA_ROOT, MAX_ACTIVE, MAX_QUEUED, MAX_UPLOAD, MAX_JOB_SECONDS, MAX_PIXELS
from lib.uploads import save_upload
from lib.storage import put_file,restore_file
from lib.quality import evaluate_gate
from lib.jobs import active,admission_lock,launch,public_run
from routers.catalog import resolve_reference

router=APIRouter(prefix='/registration',tags=['registration'])

@router.get('/capabilities')
async def capabilities():
    return dict(mode='single_operator',shared_access_enabled=False,max_upload_mib=MAX_UPLOAD//1024**2,max_pixels=MAX_PIXELS,max_job_seconds=MAX_JOB_SECONDS,max_active_jobs=MAX_ACTIVE,max_queued_jobs=MAX_QUEUED,native_sensor_support='unverified; original samples and metadata pending',supported='Preprocessed PNG/JPEG/WebP/TIFF, explicit band; local affine geometry only',limits_status='provisional',thresholds=Thresholds().model_dump(),retention='Manual removal; private object storage supports soft deletion only.')

async def get_run(rid):
    try:
        uuid.UUID(rid)
    except ValueError:
        raise HTTPException(404,'Run not found.')
    row=await db.runs.find_one({'id':rid,'is_deleted':False},{'_id':0})
    if not row:
        raise HTTPException(404,'Run not found.')
    return row

@router.post('/run',response_model=RunRecord,status_code=202)
async def run_registration(source_image:UploadFile=File(...),reference_id:str=Form(...),method:str=Form('auto'),refine:bool=Form(True),source_band:int=Form(1,ge=1,le=32),sensor:str=Form('optical'),reference_selection:str=Form('',max_length=4000)):
    if method not in {'auto','sift','orb'} or sensor not in {'optical','ohrc','tmc2','iirs'}:
        raise HTTPException(422,'Unsupported method or sensor label.')
    async with admission_lock:
        if len(active)>=MAX_ACTIVE+MAX_QUEUED:
            raise HTTPException(429,'Job capacity reached; wait or cancel a queued run.')
        reference=await resolve_reference(reference_id)
        path=await save_upload(source_image)
        rid=str(uuid.uuid4())
        record=RunRecord(id=rid,status='queued',stage='Queued',source_filename=Path(source_image.filename or 'source').name[:200],reference_id=reference_id,reference_title=reference['title'],reference_status=reference['status'],reference_provenance=reference['provenance'],reference_sha256=reference['sha256'],reference_metadata=reference['metadata'],source_band=source_band,reference_band=reference['metadata']['selected_band'],sensor=sensor,requested_method=method,refine=refine,source_path=str(path),reference_path=reference['path'],is_deleted=False,report_url=f'/api/registration/{rid}/report',preview_url=f'/api/registration/{rid}/preview',source_url=f'/api/registration/{rid}/source',reference_url=f'/api/registration/{rid}/reference',geotiff_url=f'/api/registration/{rid}/geotiff')
        row=record.model_dump(mode='json')
        row['diagnostics']=[f"Reference: {reference['title']} ({reference['status']})."]+([f'Reference selection: {reference_selection.strip()}'] if reference_selection.strip() else [])
        try:
            row['source_manifest']=await asyncio.to_thread(put_file,path,f'uploads/{rid}{path.suffix}','application/octet-stream')
            row['source_sha256']=row['source_manifest']['sha256']
            await db.runs.insert_one(dict(row))
        except Exception as exc:
            path.unlink(missing_ok=True)
            raise HTTPException(503,'Cannot persist reproducible run. Object storage or database unavailable.') from exc
        launch(row,path,reference['path'])
        return RunRecord(**public_run(row))

@router.post('/compare')
async def compare_disabled():
    raise HTTPException(409,'For bounded, reproducible engine comparison, submit one SIFT run and one ORB run against the same cached reference. Both remain in run history.')

@router.get('/runs',response_model=list[RunRecord])
async def runs():
    rows=await db.runs.find({'is_deleted':False},{'_id':0}).sort('generated_at',-1).to_list(100)
    return [RunRecord(**public_run(row)) for row in rows]

@router.get('/runs/{rid}',response_model=RunRecord)
async def poll(rid:str):
    return RunRecord(**public_run(await get_run(rid)))

@router.post('/runs/{rid}/cancel',response_model=RunRecord)
async def cancel(rid:str):
    row=await get_run(rid)
    if rid not in active:
        raise HTTPException(409,'Run is already finished.')
    active[rid]['cancel']=True
    await db.runs.update_one({'id':rid},{'$set':{'stage':'Cancellation requested'}})
    row['stage']='Cancellation requested'
    return RunRecord(**public_run(row))

@router.put('/runs/{rid}/thresholds',response_model=RunRecord)
async def thresholds(rid:str,limits:Thresholds):
    row=await get_run(rid)
    if row['status'] in {'queued','running'}:
        raise HTTPException(409,'Wait until the run is finished before changing thresholds.')
    row['thresholds']=limits.model_dump()
    row['quality_gate']=evaluate_gate(row)
    await db.runs.update_one({'id':rid},{'$set':{'thresholds':row['thresholds'],'quality_gate':row['quality_gate']}})
    return RunRecord(**public_run(row))

@router.post('/runs/{rid}/checkpoints',response_model=RunRecord)
async def checkpoints(rid:str,submission:CheckpointSubmission):
    row=await get_run(rid)
    if row['status']!='completed' or not row.get('affine_matrix'):
        raise HTTPException(409,'Checkpoint evaluation requires a completed transform.')
    sp=np.array([[p.source_x,p.source_y] for p in submission.points]); rp=np.array([[p.reference_x,p.reference_y] for p in submission.points])
    if np.any(sp >= [row['source_width'],row['source_height']]) or np.any(rp >= [row['reference_width'],row['reference_height']]):
        raise HTTPException(422,'Checkpoint coordinates must be inside native source/reference grids.')
    if len(np.unique(sp,axis=0))!=len(sp) or len(np.unique(rp,axis=0))!=len(rp):
        raise HTTPException(422,'Checkpoint locations must be unique.')
    fit=np.array([[p['source_x'],p['source_y']] for p in row['match_points']])
    if len(fit) and np.any(np.linalg.norm(sp[:,None,:]-fit[None,:,:],axis=2)<2):
        raise HTTPException(422,'Checkpoints must be independent of fitting correspondences; points within 2 source pixels are not accepted.')
    matrix=np.array(row['affine_matrix']); residual=rp-(sp@matrix[:,:2].T+matrix[:,2])
    errors=np.linalg.norm(residual,axis=1)
    row['checkpoint_accuracy']={'count':len(sp),'rmse':float(np.sqrt(np.mean(errors**2))),'max_error':float(errors.max()),'residuals':errors.tolist(),'units':'native reference px','provenance':submission.provenance,'independence':'operator-declared; not externally certified','points':[p.model_dump() for p in submission.points]}
    row['quality_gate']=evaluate_gate(row)
    await db.runs.update_one({'id':rid},{'$set':{'checkpoint_accuracy':row['checkpoint_accuracy'],'quality_gate':row['quality_gate']}})
    return RunRecord(**public_run(row))

@router.post('/runs/{rid}/adjust',response_model=RunRecord,status_code=202)
async def adjust(rid:str,adjustment:Adjustment):
    row=await get_run(rid)
    if row['status']!='completed':
        raise HTTPException(409,'Adjustments require a completed run.')
    async with admission_lock:
        if len(active)>=MAX_ACTIVE+MAX_QUEUED:
            raise HTTPException(429,'Job capacity reached.')
        ref=await resolve_reference(row['reference_id'])
        path=Path(row['source_path'])
        if not path.exists():
            await asyncio.to_thread(restore_file,row['source_manifest'],path)
        new_id=str(uuid.uuid4())
        base={k:row[k] for k in ['source_filename','reference_id','reference_title','reference_status','reference_provenance','reference_sha256','reference_metadata','source_band','reference_band','sensor','requested_method','refine','source_path','reference_path','source_manifest','source_sha256','thresholds']}
        previous=row.get('adjustment',[0,0])
        if row.get('checkpoint_accuracy'):
            base['checkpoint_accuracy']=row['checkpoint_accuracy']
        base.update(id=new_id,status='queued',stage='Queued adjustment',parent_run_id=rid,adjustment=[previous[0]+adjustment.offset_x,previous[1]+adjustment.offset_y],is_deleted=False)
        for artifact in ['source','preview','reference','geotiff','report']:
            base[f'{artifact}_url']=f'/api/registration/{new_id}/{artifact}'
        new=RunRecord(**base).model_dump(mode='json')
        await db.runs.insert_one(dict(new)); launch(new,path,ref['path'])
        return RunRecord(**public_run(new))

@router.delete('/runs/{rid}')
async def delete_run(rid:str):
    row=await get_run(rid)
    if rid in active:
        raise HTTPException(409,'Cancel the job and wait before removal.')
    await db.runs.update_one({'id':rid},{'$set':{'is_deleted':True}})
    shutil.rmtree(DATA_ROOT/'results'/rid,ignore_errors=True)
    if not await db.runs.count_documents({'source_path':row['source_path'],'is_deleted':False}):
        Path(row['source_path']).unlink(missing_ok=True)
    return {'is_deleted':True,'physical_object_retention':'Soft deletion only; stored objects are not physically erased.'}

@router.get('/{rid}/{artifact}')
async def artifact(rid:str,artifact:str):
    row=await get_run(rid)
    if artifact=='report':
        report=public_run(row);report['quality_gate']=evaluate_gate(row)
        return JSONResponse(report,headers={'Content-Disposition':f'attachment; filename="moon-diagnostics-{rid}.json"','Cache-Control':'no-store'})
    if artifact=='geotiff' and not evaluate_gate(row)['export_allowed']:
        raise HTTPException(403,'GeoTIFF blocked: passing server-side checks and valid local lunar georeferencing are required. Diagnostics remain available.')
    manifest=row.get('artifacts',{}).get(artifact)
    if artifact not in {'source','reference','preview','mask','geotiff'} or not manifest:
        raise HTTPException(404,'Artifact unavailable; diagnostic report remains available.')
    suffix='.tif' if artifact=='geotiff' else '.png'
    path=DATA_ROOT/'results'/rid/(artifact+suffix)
    if not path.exists():
        try:
            await asyncio.to_thread(restore_file,manifest,path)
        except Exception as exc:
            raise HTTPException(503,'Private artifact storage unavailable; diagnostics remain available.') from exc
    return FileResponse(path,media_type=manifest['content_type'],filename=f'moon-{rid}-{artifact}{suffix}',headers={'Cache-Control':'no-store'})