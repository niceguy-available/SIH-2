import uuid
import tempfile
from pathlib import Path
from fastapi import HTTPException
from lib.config import DATA_ROOT, MAX_UPLOAD, MAX_CACHE

async def save_upload(upload, folder='uploads'):
    suffix = Path(upload.filename or '').suffix.lower()
    if suffix not in {'.tif','.tiff','.png','.jpg','.jpeg','.webp'}:
        raise HTTPException(415,'Use PNG, JPEG, WebP or a preprocessed TIFF band. Archives/native sensor containers are unsupported.')
    if sum(p.stat().st_size for p in DATA_ROOT.rglob('*') if p.is_file()) + MAX_UPLOAD > MAX_CACHE:
        raise HTTPException(507,'Local processing cache is full. Remove old runs/references before uploading.')
    # Scratch staging for GDAL only. Callers must persist to private object storage
    # before accepting the upload; this is not the durable artifact store.
    scratch = tempfile.NamedTemporaryFile(dir=DATA_ROOT/folder, suffix=suffix, delete=False)
    path = Path(scratch.name)
    total = 0
    try:
        with scratch as file:
            while chunk := await upload.read(1024**2):
                total += len(chunk)
                if total > MAX_UPLOAD:
                    raise HTTPException(413,f'Upload exceeds {MAX_UPLOAD//1024**2} MiB.')
                file.write(chunk)
        if not total:
            raise HTTPException(422,'The uploaded file is empty.')
        return path
    except Exception:
        path.unlink(missing_ok=True)
        raise
    finally:
        await upload.close()