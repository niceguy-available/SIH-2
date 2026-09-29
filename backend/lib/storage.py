"""Private object storage; MongoDB manifests remain the source of truth."""
import hashlib
import os
import shutil
import threading
from pathlib import Path
import requests
from lib.config import DATA_ROOT

STORAGE_BASE = (os.environ.get('INTEGRATION_PROXY_URL') or '').strip() or 'https://integrations.emergentagent.com'
STORAGE_URL = STORAGE_BASE.rstrip('/') + '/objstore/api/v1/storage'
_key = None
_lock = threading.Lock()

def init_storage(force=False):
    global _key
    with _lock:
        if _key and not force:
            return _key
        response = requests.post(f'{STORAGE_URL}/init', json={'emergent_key': os.environ['EMERGENT_LLM_KEY']}, timeout=30)
        response.raise_for_status()
        _key = response.json()['storage_key']
        return _key

def sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as file:
        for chunk in iter(lambda: file.read(1024**2), b''):
            digest.update(chunk)
    return digest.hexdigest()

# Self-hosted deployments without an Emergent key keep objects on local disk.
LOCAL = os.environ.get('STORAGE_BACKEND', 'local' if not os.environ.get('EMERGENT_LLM_KEY') else 'emergent') == 'local'
LOCAL_ROOT = DATA_ROOT / 'objects'

def _local_object(storage_path):
    target = (LOCAL_ROOT / storage_path.removeprefix('local:')).resolve()
    if LOCAL_ROOT.resolve() not in target.parents:
        raise ValueError('Object path escapes local storage')
    return target

def put_file(path: Path, object_path: str, content_type: str):
    if LOCAL:
        storage_path = f'local:moon-match-points/{object_path}'
        target = _local_object(storage_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
        return {'storage_path': storage_path, 'size': target.stat().st_size, 'sha256': sha256(path), 'content_type': content_type, 'is_deleted': False}
    key = init_storage()
    with path.open('rb') as file:
        response = requests.put(f'{STORAGE_URL}/objects/moon-match-points/{object_path}', headers={'X-Storage-Key': key, 'Content-Type': content_type}, data=file, timeout=90)
    response.raise_for_status()
    result = response.json()
    return {'storage_path': result['path'], 'size': result['size'], 'sha256': sha256(path), 'content_type': content_type, 'is_deleted': False}

def restore_file(manifest, destination):
    from lib.config import MAX_UPLOAD
    if manifest['storage_path'].startswith('local:'):
        shutil.copyfile(_local_object(manifest['storage_path']), destination)
        if sha256(destination) != manifest['sha256']:
            Path(destination).unlink(missing_ok=True)
            raise ValueError('Object checksum mismatch')
        return
    with requests.get(f"{STORAGE_URL}/objects/{manifest['storage_path']}", headers={'X-Storage-Key': init_storage()}, stream=True, timeout=60) as response:
        response.raise_for_status()
        total = 0
        try:
            with open(destination, 'wb') as file:
                for chunk in response.iter_content(1024**2):
                    total += len(chunk)
                    if total > max(MAX_UPLOAD, manifest['size']) or total > 512*1024**2:
                        raise ValueError('Object exceeds the download limit')
                    file.write(chunk)
            if sha256(destination) != manifest['sha256']:
                raise ValueError('Object checksum mismatch')
        except Exception:
            Path(destination).unlink(missing_ok=True)
            raise