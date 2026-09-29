import os
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, APIRouter
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse
from lib.config import MAX_UPLOAD
from lib.db import db, client, ensure_indexes
from lib import jobs
from routers.catalog import router as catalog_router
from routers.registration import router as registration_router

logging.basicConfig(level=logging.INFO)

@asynccontextmanager
async def lifespan(app):
    if os.environ['APP_MODE']!='single_operator':
        raise RuntimeError('Shared organisational mode is blocked until access controls are implemented.')
    await ensure_indexes()
    await db.runs.create_index('id',unique=True)
    await db.references.create_index('id',unique=True)
    await db.runs.update_many({'status':{'$in':['queued','running']}},{'$set':{'status':'failed','stage':'Interrupted by restart','progress':100},'$push':{'diagnostics':'Worker interrupted by service restart; submit a new run.'}})
    yield
    await jobs.shutdown()
    client.close()

app=FastAPI(title='Moon Match Points',lifespan=lifespan)

class BodyTooLarge(Exception):
    pass

class UploadBudget:
    def __init__(self,app):
        self.app=app
    async def __call__(self,scope,receive,send):
        if scope['type']!='http':
            return await self.app(scope,receive,send)
        total=0; limit=MAX_UPLOAD+1024**2
        headers=dict(scope.get('headers',[]))
        try:
            if int(headers.get(b'content-length',b'0'))>limit:
                raise BodyTooLarge()
            async def bounded_receive():
                nonlocal total
                message=await receive()
                total+=len(message.get('body',b''))
                if total>limit:
                    raise BodyTooLarge()
                return message
            await self.app(scope,bounded_receive,send)
        except BodyTooLarge:
            await JSONResponse({'detail':f'Upload request exceeds {MAX_UPLOAD//1024**2} MiB plus multipart allowance.'},status_code=413)(scope,receive,send)

app.add_middleware(UploadBudget)
app.add_middleware(CORSMiddleware,allow_origins=[origin.strip().rstrip('/') for origin in os.environ['CORS_ORIGINS'].split(',') if origin.strip()],allow_credentials=False,allow_methods=['GET','POST','PUT','DELETE'],allow_headers=['Content-Type'])
api=APIRouter(prefix='/api')
@api.get('/')
async def root():
    return {'service':'moon-match-points','mode':'single_operator','shared_access_enabled':False}
api.include_router(catalog_router)
api.include_router(registration_router)
app.include_router(api)