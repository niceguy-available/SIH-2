import asyncio
import json
import multiprocessing as mp
import queue
import shutil
import time
from pathlib import Path
from datetime import datetime,timezone
from lib.config import DATA_ROOT,MAX_ACTIVE,MAX_QUEUED,MAX_JOB_SECONDS
from lib.db import db
from lib.worker import process_run
from lib.storage import put_file
from lib.quality import evaluate_gate

active = {}
semaphore = asyncio.Semaphore(MAX_ACTIVE)
admission_lock = asyncio.Lock()

def public_run(row):
    hidden={'_id','source_path','reference_path','artifacts','source_manifest'}
    return {k:v for k,v in row.items() if k not in hidden}

async def execute(run,source_path,reference_path):
    rid=run['id']; control=active[rid]
    directory=DATA_ROOT/'results'/rid
    directory.mkdir(exist_ok=True)
    report=dict(run)
    started=time.monotonic()
    try:
        async with semaphore:
            if control['cancel']:
                raise asyncio.CancelledError()
            await db.runs.update_one({'id':rid},{'$set':{'status':'running','stage':'Starting bounded worker','progress':10}})
            ctx=mp.get_context('spawn'); messages=ctx.Queue()
            child=ctx.Process(target=process_run,args=(run,str(source_path),str(reference_path),str(directory),messages),daemon=True)
            control['process']=child
            child.start(); deadline=time.monotonic()+MAX_JOB_SECONDS
            try:
                while child.is_alive():
                    if control['cancel']:
                        raise asyncio.CancelledError()
                    if time.monotonic()>deadline:
                        raise TimeoutError(f'Job exceeded {MAX_JOB_SECONDS} s processing budget.')
                    try:
                        while True:
                            progress,stage=messages.get_nowait()
                            await db.runs.update_one({'id':rid},{'$set':{'progress':progress,'stage':stage}})
                    except queue.Empty:
                        pass
                    await asyncio.sleep(.2)
            finally:
                if child.is_alive():
                    child.terminate()
                await asyncio.to_thread(child.join,3)
                if child.is_alive():
                    child.kill(); await asyncio.to_thread(child.join,1)
                messages.close()
            if not (directory/'report.json').exists():
                raise RuntimeError('Worker exited without a solution. Input or memory budget may be unsupported.')
            report=json.loads((directory/'report.json').read_text())
            if control['cancel']:
                raise asyncio.CancelledError()
            await db.runs.update_one({'id':rid},{'$set':{'stage':'Persisting private artifacts','progress':90}})
            artifacts={}
            for name,content_type in [('source.png','image/png'),('reference.png','image/png'),('preview.png','image/png'),('mask.png','image/png'),('geotiff.tif','image/tiff')]:
                path=directory/name
                if path.exists():
                    artifacts[name.split('.')[0]]=await asyncio.to_thread(put_file,path,f'runs/{rid}/{name}',content_type)
                if control['cancel']:
                    raise asyncio.CancelledError()
            report['artifacts']=artifacts
            report.update(progress=100,elapsed_seconds=round(time.monotonic()-started,3),completed_at=datetime.now(timezone.utc).isoformat())
    except asyncio.CancelledError:
        report.update(status='cancelled',progress=100,stage='Cancelled')
        report['diagnostics']=report.get('diagnostics',[])+['Operator cancelled this run; no scientific product released.']
        report['artifacts']={}
        for path in directory.glob('*'):
            path.unlink(missing_ok=True)
    except Exception as exc:
        report.update(status='failed',progress=100,stage='Processing failed')
        report['diagnostics']=report.get('diagnostics',[])+[str(exc)]
        report['artifacts']={}
    finally:
        report['quality_gate']=evaluate_gate(report)
        await db.runs.update_one({'id':rid},{'$set':report})
        active.pop(rid,None)

def launch(run,source_path,reference_path):
    active[run['id']]={'cancel':False,'process':None}
    active[run['id']]['task']=asyncio.create_task(execute(run,source_path,reference_path))

async def shutdown():
    for control in active.values():
        control['cancel']=True
    tasks=[entry['task'] for entry in list(active.values())]
    if tasks:
        await asyncio.gather(*tasks,return_exceptions=True)