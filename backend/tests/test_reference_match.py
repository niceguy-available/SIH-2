"""Offline tests for content-based reference selection (no live server, mocked Mongo)."""
import os
import tempfile
from pathlib import Path

_DATA = Path(tempfile.mkdtemp(prefix='mmp-match-'))
for key, value in dict(DATA_ROOT=str(_DATA), MAX_UPLOAD_MB='64', MAX_RASTER_PIXELS='60000000', MAX_DECODED_MB='512', MAX_ANALYSIS_DIMENSION='2048', MAX_JOB_SECONDS='60', MAX_ACTIVE_JOBS='1', MAX_QUEUED_JOBS='2', MAX_LOCAL_CACHE_MB='4096', BASELINE_COMMIT='test', MONGO_URL='mongodb://localhost:1', DB_NAME='mmp_test').items():
    os.environ.setdefault(key, value)

import asyncio
import shutil
import cv2
import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
# Test-only dependency; skip cleanly where it is not installed.
AsyncMongoMockClient = pytest.importorskip('mongomock_motor').AsyncMongoMockClient
from lib.storage import sha256
from routers import catalog


def terrain(seed, size=640):
    """Synthetic cratered terrain; each seed is a different region."""
    rng = np.random.default_rng(seed)
    image = cv2.GaussianBlur(rng.normal(120, 25, (size, size)).astype(np.float32), (0, 0), 3)
    for _ in range(70):
        x, y, r = rng.integers(20, size-20), rng.integers(20, size-20), int(rng.integers(6, 40))
        cv2.circle(image, (int(x), int(y)), r, float(rng.integers(40, 90)), -1)
        cv2.circle(image, (int(x+r//4), int(y-r//4)), r, float(rng.integers(170, 230)), 2)
    return np.clip(image, 0, 255).astype(np.uint8)


def as_source(image):
    """Crop, rotate, rescale and re-illuminate: a different sensor/sun angle view."""
    h, w = image.shape
    crop = image[h//5:h//5+h//2, w//4:w//4+w//2]
    matrix = cv2.getRotationMatrix2D((crop.shape[1]/2, crop.shape[0]/2), 23, 0.7)
    warped = cv2.warpAffine(crop, matrix, (crop.shape[1], crop.shape[0]), borderValue=120)
    return np.clip(255*(warped/255.0)**1.5, 0, 255).astype(np.uint8)


@pytest.fixture()
def client(monkeypatch):
    shutil.copy(Path(__file__).resolve().parents[1]/'data'/'footprints.json', _DATA/'footprints.json')
    db = AsyncMongoMockClient()['mmp_test']
    monkeypatch.setattr(catalog, 'db', db)
    app = FastAPI()
    app.include_router(catalog.router, prefix='/api')
    references = {}
    for seed in (1, 2, 3):
        path = _DATA/'references'/f'region-{seed}.png'
        cv2.imwrite(str(path), terrain(seed))
        rid = f'ref-{seed}'
        references[seed] = rid
        asyncio.run(db.references.insert_one(dict(id=rid, title=f'Region {seed}', status='operator-upload', image_url=f'/api/catalog/references/{rid}/preview', metadata=dict(selected_band=1, width=640, height=640, lunar_georeferencing_valid=False), provenance='synthetic test region', sha256=sha256(path), path=str(path), manifest={}, is_deleted=False, created_at=f'2026-01-0{seed}T00:00:00Z')))
    return TestClient(app), references


def upload(test_client, image, **fields):
    ok, encoded = cv2.imencode('.png', image)
    assert ok
    data = {'fetch_missing': 'false', **fields}
    return test_client.post('/api/catalog/auto-reference/match', files={'source_image': ('source.png', encoded.tobytes(), 'image/png')}, data=data)


def test_selects_the_reference_that_matches_each_source(client):
    test_client, references = client
    for seed in (3, 1, 2):
        response = upload(test_client, as_source(terrain(seed)))
        assert response.status_code == 200, response.text
        body = response.json()
        assert body['id'] == references[seed]
        assert body['match_confident'] is True
        assert body['match_inliers'] >= 12
        assert len(body['candidates']) == 3
        assert body['diagnostics'], 'diagnostics must explain the selection'
        assert any('inliers' in line for line in body['diagnostics'])


def test_unrelated_source_is_flagged_low_confidence(client):
    test_client, _ = client
    response = upload(test_client, as_source(terrain(99)))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['match_confident'] is False
    assert any('Low confidence' in line for line in body['diagnostics'])


def test_rejects_single_coordinate(client):
    test_client, _ = client
    response = upload(test_client, as_source(terrain(1)), latitude='10')
    assert response.status_code == 422
