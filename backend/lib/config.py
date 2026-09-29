import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / '.env')
DATA_ROOT = Path(os.environ['DATA_ROOT'])
MAX_UPLOAD = int(os.environ['MAX_UPLOAD_MB']) * 1024**2
MAX_PIXELS = int(os.environ['MAX_RASTER_PIXELS'])
MAX_DECODED = int(os.environ['MAX_DECODED_MB']) * 1024**2
MAX_DIMENSION = int(os.environ['MAX_ANALYSIS_DIMENSION'])
MAX_JOB_SECONDS = int(os.environ['MAX_JOB_SECONDS'])
MAX_ACTIVE = int(os.environ['MAX_ACTIVE_JOBS'])
MAX_QUEUED = int(os.environ['MAX_QUEUED_JOBS'])
MAX_CACHE = int(os.environ['MAX_LOCAL_CACHE_MB']) * 1024**2
BASELINE = os.environ['BASELINE_COMMIT']
VERSION = 'mmp-hardened-1.0'
for folder in ('uploads', 'references', 'results'):
    (DATA_ROOT / folder).mkdir(parents=True, exist_ok=True)