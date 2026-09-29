"""Release-gate deliberate-failure matrix.

Verifies the honest-failure contract from the product spec: an honest failure
report (or a solution that never clears the quality gate / export) is required
over a forced, convincing false match. Also validates recovery of a KNOWN
rotation+scale+translation transform to sub-pixel-scale residuals.

All fixtures are SYNTHETIC and deterministic. Tests hit the live API.
"""

import os
import time
from pathlib import Path

import cv2
import numpy as np
import pytest
import rasterio
import requests
from rasterio.crs import CRS
from rasterio.transform import from_origin


def _read_frontend_base_url() -> str:
    env_base = os.environ.get("REACT_APP_BACKEND_URL", "").strip()
    if env_base:
        return env_base.rstrip("/")
    env_file = Path("/app/frontend/.env")
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if line.startswith("REACT_APP_BACKEND_URL="):
                value = line.split("=", 1)[1].strip().strip('"')
                if value:
                    return value.rstrip("/")
    raise RuntimeError("REACT_APP_BACKEND_URL is required for API tests")


BASE_URL = _read_frontend_base_url()
API = f"{BASE_URL}/api"
LUNAR_PROJ = "+proj=eqc +lat_ts=0 +lat_0=0 +lon_0=0 +a=1737400 +b=1737400 +units=m +no_defs"
LUNAR_GEOG = "+proj=longlat +a=1737400 +b=1737400 +no_defs"


def _rich_texture(width, height, seed):
    """Feature-rich lunar-like scene: multi-orientation gratings + blobs + light noise.

    Grating frequencies/phases/orientations are seed-derived so two different
    seeds produce GENUINELY unrelated terrain (no shared deterministic structure).
    """
    rng = np.random.default_rng(seed)
    y, x = np.mgrid[0:height, 0:width]
    field = np.full((height, width), 10000.0)
    for _ in range(5):
        theta = rng.uniform(0, np.pi)
        freq = rng.uniform(1 / 40.0, 1 / 15.0)
        phase = rng.uniform(0, 2 * np.pi)
        amp = rng.uniform(3000, 8000)
        field += amp * np.sin(freq * (np.cos(theta) * x + np.sin(theta) * y) + phase)
    for _ in range(60):
        cy, cx = rng.integers(0, height), rng.integers(0, width)
        rad = rng.integers(12, 40)
        field += 5000 * np.exp(-(((x - cx) ** 2 + (y - cy) ** 2) / (2 * rad ** 2)))
    field += 1200 * rng.standard_normal((height, width))
    field = cv2.GaussianBlur(field.astype(np.float32), (0, 0), 1.1)
    return np.clip(field, 0, 65535).astype(np.uint16)


def _periodic(width, height):
    """Strongly self-similar terrain: identical tiles -> ambiguous correspondences."""
    y, x = np.mgrid[0:height, 0:width]
    tile = 32000 + 30000 * (np.sin(2 * np.pi * x / 40.0) * np.cos(2 * np.pi * y / 40.0))
    return np.clip(tile, 0, 65535).astype(np.uint16)


def _write_tif(path, array, projected=True, res=30.0):
    crs = CRS.from_string(LUNAR_PROJ if projected else LUNAR_GEOG)
    if projected:
        transform = from_origin(10000, 10000, res, res)
    else:
        transform = from_origin(-180, 90, 0.1, 0.1)
    with rasterio.open(
        path, "w", driver="GTiff",
        width=array.shape[1], height=array.shape[0], count=1,
        dtype="uint16", crs=crs, transform=transform, nodata=0,
    ) as ds:
        ds.write(array, 1)


@pytest.fixture(scope="module")
def failure_workspace(tmp_path_factory):
    root = tmp_path_factory.mktemp("mmp_fail")
    w, h = 2000, 1800

    base = _rich_texture(w, h, 2026)
    independent = _rich_texture(w, h, 99991)  # unrelated scene, same size/georef

    # Known transform: rotate 3 deg + scale 1.03 about centre + translate.
    m_known = cv2.getRotationMatrix2D((w / 2, h / 2), 3.0, 1.03)
    m_known[0, 2] += 8.0
    m_known[1, 2] += -5.0
    src_known = cv2.warpAffine(base, m_known, (w, h), flags=cv2.INTER_LINEAR, borderValue=0)

    # Severe shadow: near-fully-zero frame with a tiny bright patch (<1024 valid px).
    shadow = np.zeros((h, w), dtype=np.uint16)
    shadow[10:30, 10:30] = 40000

    periodic = _periodic(w, h)
    periodic_shift = cv2.warpAffine(
        periodic, np.float32([[1, 0, 17], [0, 1, 0]]), (w, h), flags=cv2.INTER_LINEAR, borderValue=0
    )

    gw, gh = 3600, 1800
    geo_base = _rich_texture(gw, gh, 7777)
    geo_shift = cv2.warpAffine(
        geo_base, np.float32([[1, 0, 9], [0, 1, -6]]), (gw, gh), flags=cv2.INTER_LINEAR, borderValue=0
    )

    paths = {}
    for name, arr, proj in [
        ("ref_lunar", base, True),
        ("src_known", src_known, True),
        ("src_independent", independent, True),
        ("src_shadow", shadow, True),
        ("ref_periodic", periodic, True),
        ("src_periodic", periodic_shift, True),
        ("ref_geographic", geo_base, False),
        ("src_geographic", geo_shift, False),
    ]:
        p = root / f"SYNTHETIC_{name}.tif"
        _write_tif(p, arr, projected=proj)
        paths[name] = p

    paths["known_angle"] = 3.0
    paths["known_scale"] = 1.03
    return paths


@pytest.fixture(scope="module")
def client():
    return requests.Session()


def upload_reference(client, path, provenance, band=1):
    with path.open("rb") as fh:
        return client.post(
            f"{API}/catalog/references",
            data={"provenance": provenance, "band": str(band)},
            files={"reference_image": (path.name, fh, "image/tiff")},
            timeout=90,
        )


def submit_run(client, source_path, reference_id, method="sift", sensor="optical"):
    with source_path.open("rb") as fh:
        return client.post(
            f"{API}/registration/run",
            data={"reference_id": reference_id, "method": method, "refine": "true",
                  "sensor": sensor, "source_band": "1"},
            files={"source_image": (source_path.name, fh, "image/tiff")},
            timeout=120,
        )


def poll(client, run_id, timeout_seconds=180):
    deadline = time.time() + timeout_seconds
    last = None
    while time.time() < deadline:
        r = client.get(f"{API}/registration/runs/{run_id}", timeout=30)
        assert r.status_code == 200
        last = r.json()
        if last["status"] in {"completed", "failed", "cancelled"}:
            return last
        time.sleep(1.0)
    raise AssertionError(f"Run {run_id} never reached terminal state. Last: {last}")


def _cache_reference(client, path, provenance):
    resp = upload_reference(client, path, provenance)
    assert resp.status_code == 200, resp.text
    return resp.json()


# ----------------------------------------------------------------------------- #
# KNOWN TRANSFORM RECOVERY (positive control)
# ----------------------------------------------------------------------------- #
def test_known_rotation_scale_transform_recovered(client, failure_workspace):
    ref = _cache_reference(client, failure_workspace["ref_lunar"],
                           "SYNTHETIC lunar reference for known-transform recovery.")
    resp = submit_run(client, failure_workspace["src_known"], ref["id"], method="sift")
    assert resp.status_code == 202, resp.text
    run = poll(client, resp.json()["id"])
    assert run["status"] == "completed", run.get("diagnostics")
    metrics = run["metrics"]
    # source = R(3 deg, 1.03) * ref  ->  recovered source->reference map ~ inverse.
    assert abs(abs(metrics["rotation_deg"]) - failure_workspace["known_angle"]) < 1.0
    assert abs(metrics["scale_ratio"] - 1.0 / failure_workspace["known_scale"]) < 0.03
    assert metrics["rmse"] is not None and metrics["rmse"] < 1.5
    assert metrics["geometry_valid"] is True


# ----------------------------------------------------------------------------- #
# NO OVERLAP (unrelated scenes) -> honest failure, never a forced accepted match
# ----------------------------------------------------------------------------- #
def test_no_overlap_unrelated_scene_is_honest_failure(client, failure_workspace):
    ref = _cache_reference(client, failure_workspace["ref_lunar"],
                           "SYNTHETIC lunar reference for no-overlap negative test.")
    resp = submit_run(client, failure_workspace["src_independent"], ref["id"], method="auto")
    assert resp.status_code == 202, resp.text
    run = poll(client, resp.json()["id"])
    if run["status"] == "completed":
        # A solution is tolerated only if it is NEVER promoted to an accepted product.
        assert run["quality_gate"]["export_allowed"] is False
        assert run["quality_gate"]["passed"] is False
    else:
        assert run["status"] == "failed"
        assert len(run.get("diagnostics", [])) > 0


# ----------------------------------------------------------------------------- #
# REPEATED TERRAIN (self-similar tiles) -> ambiguity rejected, not a false lock
# ----------------------------------------------------------------------------- #
def test_repeated_terrain_ambiguity_not_forced(client, failure_workspace):
    ref = _cache_reference(client, failure_workspace["ref_periodic"],
                           "SYNTHETIC periodic terrain reference for ambiguity test.")
    resp = submit_run(client, failure_workspace["src_periodic"], ref["id"], method="auto")
    assert resp.status_code == 202, resp.text
    run = poll(client, resp.json()["id"])
    if run["status"] == "completed":
        assert run["quality_gate"]["export_allowed"] is False
    else:
        assert run["status"] == "failed"
        assert len(run.get("diagnostics", [])) > 0


# ----------------------------------------------------------------------------- #
# SEVERE SHADOW / TEXTURELESS SOURCE -> honest failure with diagnostic reason
# ----------------------------------------------------------------------------- #
def test_severe_shadow_source_fails_honestly(client, failure_workspace):
    ref = _cache_reference(client, failure_workspace["ref_lunar"],
                           "SYNTHETIC lunar reference for shadow-source negative test.")
    resp = submit_run(client, failure_workspace["src_shadow"], ref["id"], method="sift")
    assert resp.status_code == 202, resp.text
    run = poll(client, resp.json()["id"])
    assert run["status"] == "failed"
    text = " ".join(run.get("diagnostics", [])).lower()
    assert any(k in text for k in ["valid pixels", "shadow", "textureless", "insufficient"])


# ----------------------------------------------------------------------------- #
# LONGITUDE-WRAP / NON-PROJECTED GLOBAL GEOGRAPHIC GRID -> export blocked
# ----------------------------------------------------------------------------- #
def test_geographic_wrap_reference_blocks_export(client, failure_workspace):
    ref = _cache_reference(client, failure_workspace["ref_geographic"],
                           "SYNTHETIC global geographic (lon/lat) reference; not a local lunar grid.")
    assert ref["metadata"]["lunar_georeferencing_valid"] is False
    resp = submit_run(client, failure_workspace["src_geographic"], ref["id"], method="sift")
    assert resp.status_code == 202, resp.text
    run = poll(client, resp.json()["id"])
    # Even if pixel registration succeeds, invalid georeferencing must block GeoTIFF export.
    if run["status"] == "completed":
        assert run["quality_gate"]["export_allowed"] is False
    geotiff = client.get(f"{API}/registration/{run['id']}/geotiff", timeout=30)
    assert geotiff.status_code == 403
    report = client.get(f"{API}/registration/{run['id']}/report", timeout=30)
    assert report.status_code == 200


# ----------------------------------------------------------------------------- #
# PROVIDER / CONTEXT HONESTY (no network dependency): no silent substitution
# ----------------------------------------------------------------------------- #
def test_unknown_product_context_returns_honest_404(client):
    catalog = client.get(f"{API}/catalog", timeout=30)
    assert catalog.status_code == 200
    assert catalog.json()
    # An unknown curated product must 404 with no substituted image, never a fallback.
    missing = client.post(f"{API}/catalog/context/this-product-does-not-exist", timeout=30)
    assert missing.status_code == 404
    cached_missing = client.post(
        f"{API}/catalog/context/this-product-does-not-exist?use_cache=true", timeout=30
    )
    assert cached_missing.status_code == 404

