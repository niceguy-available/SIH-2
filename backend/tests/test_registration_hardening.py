"""Regression tests for registration API hardening and gate enforcement."""

import json
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


@pytest.fixture(scope="session")
def api_client():
    """Shared HTTP client for API integration tests."""
    session = requests.Session()
    return session


@pytest.fixture(scope="session")
def synthetic_workspace(tmp_path_factory):
    """Deterministic SYNTHETIC fixtures (GeoTIFF uint16 + PNG) with known translation."""
    root = tmp_path_factory.mktemp("mmp_synth")
    width, height = 2300, 2100
    dx, dy = 11.0, -7.0

    rng = np.random.default_rng(2026)
    y, x = np.mgrid[0:height, 0:width]
    base = (
        10000
        + 8000 * np.sin(x / 27.0)
        + 6000 * np.cos(y / 31.0)
        + 2500 * np.sin((x + y) / 19.0)
        + 1500 * (rng.standard_normal((height, width)))
    )
    base = cv2.GaussianBlur(base.astype(np.float32), (0, 0), 1.2)
    base = np.clip(base, 0, 65535).astype(np.uint16)

    # Translate synthetic source relative to reference.
    matrix = np.array([[1.0, 0.0, dx], [0.0, 1.0, dy]], dtype=np.float32)
    source_u16 = cv2.warpAffine(base, matrix, (width, height), flags=cv2.INTER_LINEAR, borderValue=0)

    ref_png = (base / 257).astype(np.uint8)
    src_png = (source_u16 / 257).astype(np.uint8)

    proj = "+proj=eqc +lat_ts=0 +lat_0=0 +lon_0=0 +a=1737400 +b=1737400 +units=m +no_defs"
    crs = CRS.from_string(proj)
    transform = from_origin(10000, 10000, 30, 30)

    reference_tif = root / "SYNTHETIC_reference_u16_lunar.tif"
    source_tif = root / "SYNTHETIC_source_u16_lunar.tif"
    reference_png = root / "SYNTHETIC_reference.png"
    source_png = root / "SYNTHETIC_source.png"
    corrupt_tif = root / "SYNTHETIC_corrupt.tif"

    with rasterio.open(
        reference_tif,
        "w",
        driver="GTiff",
        width=width,
        height=height,
        count=1,
        dtype="uint16",
        crs=crs,
        transform=transform,
        nodata=0,
    ) as ds:
        ds.write(base, 1)

    with rasterio.open(
        source_tif,
        "w",
        driver="GTiff",
        width=width,
        height=height,
        count=1,
        dtype="uint16",
        crs=crs,
        transform=transform,
        nodata=0,
    ) as ds:
        ds.write(source_u16, 1)

    cv2.imwrite(str(reference_png), ref_png)
    cv2.imwrite(str(source_png), src_png)
    corrupt_tif.write_bytes(b"NOT_A_VALID_RASTER")

    return {
        "reference_tif": reference_tif,
        "source_tif": source_tif,
        "reference_png": reference_png,
        "source_png": source_png,
        "corrupt_tif": corrupt_tif,
        "dx": dx,
        "dy": dy,
        "width": width,
        "height": height,
    }


@pytest.fixture(scope="session")
def test_state():
    """Shared mutable test state across workflow tests."""
    return {"reference_id": None, "run_id": None, "created_runs": [], "created_refs": []}


def upload_reference(client, path: Path, provenance: str, band: int = 1):
    with path.open("rb") as file_obj:
        return client.post(
            f"{API}/catalog/references",
            data={"provenance": provenance, "band": str(band)},
            files={"reference_image": (path.name, file_obj, "image/tiff")},
            timeout=60,
        )


def submit_run(client, source_path: Path, reference_id: str, method="sift", refine="true", sensor="optical", source_band=1):
    with source_path.open("rb") as file_obj:
        return client.post(
            f"{API}/registration/run",
            data={
                "reference_id": reference_id,
                "method": method,
                "refine": refine,
                "sensor": sensor,
                "source_band": str(source_band),
            },
            files={"source_image": (source_path.name, file_obj, "image/tiff")},
            timeout=120,
        )


def poll_run(client, run_id: str, timeout_seconds: int = 180):
    deadline = time.time() + timeout_seconds
    last = None
    while time.time() < deadline:
        response = client.get(f"{API}/registration/runs/{run_id}", timeout=30)
        assert response.status_code == 200
        payload = response.json()
        last = payload
        if payload["status"] in {"completed", "failed", "cancelled"}:
            return payload
        time.sleep(1.0)
    raise AssertionError(f"Run {run_id} did not reach terminal status. Last state: {last}")


def test_capabilities_contract(api_client):
    """registration capabilities baseline + queue limits contract."""
    response = api_client.get(f"{API}/registration/capabilities", timeout=30)
    assert response.status_code == 200
    data = response.json()
    assert data["max_active_jobs"] == 1
    assert data["max_queued_jobs"] == 2
    assert data["max_upload_mib"] == 128


def test_reference_upload_and_provenance_rules(api_client, synthetic_workspace, test_state):
    """reference upload validations: provenance min length and selected band support."""
    short = upload_reference(api_client, synthetic_workspace["reference_tif"], "abcd", 1)
    assert short.status_code == 422

    ok = upload_reference(
        api_client,
        synthetic_workspace["reference_tif"],
        "SYNTHETIC fixture reference geotiff with local lunar CRS and known transform.",
        1,
    )
    assert ok.status_code == 200
    row = ok.json()
    assert row["metadata"]["selected_band"] == 1
    assert row["metadata"]["dtype"] == "uint16"
    assert row["metadata"]["crs"] is not None
    assert row["metadata"]["lunar_georeferencing_valid"] is True
    test_state["reference_id"] = row["id"]
    test_state["created_refs"].append(row["id"])


def test_registration_run_translation_and_rmse(api_client, synthetic_workspace, test_state):
    """run lifecycle: POST /run -> poll /runs/{id}; translation and RMSE consistency checks."""
    reference_id = test_state["reference_id"]
    assert reference_id, "Reference ID missing from prior test setup"

    response = submit_run(api_client, synthetic_workspace["source_tif"], reference_id, method="sift", refine="true", sensor="optical")
    assert response.status_code == 202
    run = response.json()
    run_id = run["id"]
    test_state["created_runs"].append(run_id)

    terminal = poll_run(api_client, run_id)
    assert terminal["status"] == "completed"
    assert terminal["metrics"]["inlier_count"] >= 20
    assert terminal["method"] in {"SIFT", "ORB"}

    inliers = [p for p in terminal["match_points"] if p["status"] == "inlier"]
    residuals = np.array([p["residual_error"] for p in inliers], dtype=float)
    calc_rmse = float(np.sqrt(np.mean(residuals**2)))
    assert abs(calc_rmse - float(terminal["metrics"]["rmse"])) < 1e-6

    # Source was generated by +dx,+dy shift; fitted source->reference offset should be ~(-dx,-dy).
    assert abs(float(terminal["metrics"]["offset_x"]) + synthetic_workspace["dx"]) < 3.0
    assert abs(float(terminal["metrics"]["offset_y"]) + synthetic_workspace["dy"]) < 3.0
    test_state["run_id"] = run_id


def test_checkpoints_thresholds_geotiff_gate_and_report(api_client, test_state):
    """independent checkpoints, policy floors, and geotiff gate enforcement."""
    run_id = test_state["run_id"]
    assert run_id, "Run ID missing from prior test setup"

    run = api_client.get(f"{API}/registration/runs/{run_id}", timeout=30).json()
    fit = np.array([[p["source_x"], p["source_y"]] for p in run["match_points"]], dtype=float)

    # Build independent points away from fitting correspondences.
    points = []
    candidates = [(120.0, 130.0), (500.0, 1700.0), (1600.0, 700.0), (2100.0, 1900.0), (900.0, 900.0)]
    matrix = np.array(run["affine_matrix"], dtype=float)
    for sx, sy in candidates:
        if fit.size and np.min(np.linalg.norm(fit - np.array([sx, sy]), axis=1)) < 3.0:
            continue
        rp = np.array([sx, sy]) @ matrix[:, :2].T + matrix[:, 2]
        points.append({"source_x": sx, "source_y": sy, "reference_x": float(rp[0]), "reference_y": float(rp[1])})
        if len(points) == 3:
            break
    assert len(points) >= 3

    valid_cp = api_client.post(
        f"{API}/registration/runs/{run_id}/checkpoints",
        json={"points": points, "provenance": "SYNTHETIC independent checkpoints for gate validation.", "independent": True},
        timeout=30,
    )
    assert valid_cp.status_code == 200
    after_valid = valid_cp.json()
    assert after_valid["checkpoint_accuracy"]["count"] >= 3
    assert after_valid["quality_gate"]["checks"]

    invalid_floor = api_client.put(
        f"{API}/registration/runs/{run_id}/thresholds",
        json={
            "min_inliers": 10,
            "min_inlier_ratio": 0.3,
            "max_rmse": 2.0,
            "min_overlap": 0.1,
            "min_coverage": 0.2,
            "max_checkpoint_rmse": 3.0,
        },
        timeout=30,
    )
    assert invalid_floor.status_code == 422

    strict = api_client.put(
        f"{API}/registration/runs/{run_id}/thresholds",
        json={
            "min_inliers": 20,
            "min_inlier_ratio": 0.5,
            "max_rmse": 0.01,
            "min_overlap": 0.2,
            "min_coverage": 0.25,
            "max_checkpoint_rmse": 2.0,
        },
        timeout=30,
    )
    assert strict.status_code == 200
    strict_run = strict.json()
    assert strict_run["quality_gate"]["export_allowed"] is False

    geotiff_blocked = api_client.get(f"{API}/registration/{run_id}/geotiff", timeout=30)
    assert geotiff_blocked.status_code == 403

    report = api_client.get(f"{API}/registration/{run_id}/report", timeout=30)
    assert report.status_code == 200
    report_json = report.json()
    assert report_json["id"] == run_id


def test_adjustment_creates_child_and_parent_immutable(api_client, test_state):
    """adjustment run creation and immutability of original run."""
    run_id = test_state["run_id"]
    assert run_id

    parent_before = api_client.get(f"{API}/registration/runs/{run_id}", timeout=30).json()

    child_response = api_client.post(
        f"{API}/registration/runs/{run_id}/adjust",
        json={"offset_x": 1.5, "offset_y": -2.0},
        timeout=30,
    )
    assert child_response.status_code == 202
    child = child_response.json()
    test_state["created_runs"].append(child["id"])
    assert child["parent_run_id"] == run_id

    child_terminal = poll_run(api_client, child["id"])
    assert child_terminal["status"] in {"completed", "failed"}

    parent_after = api_client.get(f"{API}/registration/runs/{run_id}", timeout=30).json()
    assert parent_after["id"] == parent_before["id"]
    assert parent_after.get("parent_run_id") is None
    assert parent_after["metrics"]["offset_x"] == parent_before["metrics"]["offset_x"]

    too_large = api_client.post(
        f"{API}/registration/runs/{run_id}/adjust",
        json={"offset_x": 25.0, "offset_y": 0.0},
        timeout=30,
    )
    assert too_large.status_code == 422


def test_corrupt_reference_upload_rejected_422(api_client, synthetic_workspace):
    """reference corruption handling with honest 422 failure reason."""
    response = upload_reference(
        api_client,
        synthetic_workspace["corrupt_tif"],
        "SYNTHETIC corrupt raster for validation",
        1,
    )
    assert response.status_code == 422
    assert "Corrupt" in response.text or "unreadable" in response.text or "Unsupported" in response.text


def test_corrupt_source_persists_failure_report(api_client, synthetic_workspace, test_state):
    """submitted corrupt source should produce failed run with persisted report."""
    reference_id = test_state["reference_id"]
    assert reference_id

    response = submit_run(api_client, synthetic_workspace["corrupt_tif"], reference_id)
    assert response.status_code == 202
    run_id = response.json()["id"]
    test_state["created_runs"].append(run_id)

    terminal = poll_run(api_client, run_id)
    assert terminal["status"] == "failed"
    report = api_client.get(f"{API}/registration/{run_id}/report", timeout=30)
    assert report.status_code == 200
    details = report.json()
    assert details["status"] == "failed"
    assert len(details.get("diagnostics", [])) > 0


def test_iirs_diagnostic_only_blocks_export(api_client, synthetic_workspace, test_state):
    """IIRS runs are diagnostic-only: similarity N/A and geotiff blocked."""
    reference_id = test_state["reference_id"]
    response = submit_run(api_client, synthetic_workspace["source_tif"], reference_id, sensor="iirs", method="auto")
    assert response.status_code == 202
    run_id = response.json()["id"]
    test_state["created_runs"].append(run_id)

    terminal = poll_run(api_client, run_id)
    assert terminal["status"] == "completed"
    assert terminal["metrics"]["image_similarity"] is None

    geotiff = api_client.get(f"{API}/registration/{run_id}/geotiff", timeout=30)
    assert geotiff.status_code == 403


def test_png_reference_is_diagnostic_only_and_blocks_geotiff(api_client, synthetic_workspace, test_state):
    """non-georeferenced PNG reference may run diagnostics but must block geotiff export."""
    upload = upload_reference(
        api_client,
        synthetic_workspace["reference_png"],
        "SYNTHETIC PNG reference (no CRS metadata) diagnostic workflow.",
        1,
    )
    assert upload.status_code == 200
    ref = upload.json()
    test_state["created_refs"].append(ref["id"])
    assert ref["metadata"]["lunar_georeferencing_valid"] is False

    run_response = submit_run(api_client, synthetic_workspace["source_png"], ref["id"], method="sift", sensor="optical")
    assert run_response.status_code == 202
    run_id = run_response.json()["id"]
    test_state["created_runs"].append(run_id)
    terminal = poll_run(api_client, run_id)
    assert terminal["status"] == "completed"

    geotiff = api_client.get(f"{API}/registration/{run_id}/geotiff", timeout=30)
    assert geotiff.status_code == 403


def test_invalid_or_missing_reference_rejected(api_client, synthetic_workspace, test_state):
    """run endpoint rejects blind/invalid reference selection and unsupported source extension."""
    with synthetic_workspace["source_png"].open("rb") as file_obj:
        missing = api_client.post(
            f"{API}/registration/run",
            data={"method": "sift", "refine": "true", "sensor": "optical", "source_band": "1"},
            files={"source_image": ("SYNTHETIC_source.png", file_obj, "image/png")},
            timeout=30,
        )
    assert missing.status_code == 422

    with synthetic_workspace["source_png"].open("rb") as file_obj:
        invalid = api_client.post(
            f"{API}/registration/run",
            data={"reference_id": "00000000-0000-0000-0000-000000000001", "method": "sift", "refine": "true", "sensor": "optical", "source_band": "1"},
            files={"source_image": ("SYNTHETIC_source.png", file_obj, "image/png")},
            timeout=30,
        )
    assert invalid.status_code == 422

    with synthetic_workspace["source_png"].open("rb") as file_obj:
        unsupported = api_client.post(
            f"{API}/registration/run",
            data={"reference_id": test_state["reference_id"], "method": "sift", "refine": "true", "sensor": "optical", "source_band": "1"},
            files={"source_image": ("SYNTHETIC_source.img", file_obj, "application/octet-stream")},
            timeout=30,
        )
    assert unsupported.status_code == 415


def test_catalog_context_cache_fetch_and_explicit_cached_use(api_client):
    """curated provider context fetch and explicit cached-use status contract."""
    catalog = api_client.get(f"{API}/catalog", timeout=30)
    assert catalog.status_code == 200
    rows = catalog.json()
    assert rows, "Curated catalog unexpectedly empty"
    item_id = rows[0]["id"]

    fetched = api_client.post(f"{API}/catalog/context/{item_id}", timeout=60)
    assert fetched.status_code == 200
    fetched_row = fetched.json()
    assert fetched_row["status"] == "context-only"

    cached = api_client.post(f"{API}/catalog/context/{item_id}?use_cache=true", timeout=30)
    assert cached.status_code == 200
    cached_row = cached.json()
    assert cached_row["status"] == "cached-context-only"


def test_concurrency_capacity_and_cancellation(api_client, synthetic_workspace, test_state):
    """admission cap (1 active + 2 queued), cancellation, and terminal report availability."""
    reference_id = test_state["reference_id"]
    assert reference_id

    accepted_ids = []
    codes = []
    for _ in range(4):
        response = submit_run(api_client, synthetic_workspace["source_tif"], reference_id, method="orb", refine="false", sensor="optical")
        codes.append(response.status_code)
        if response.status_code == 202:
            run_id = response.json()["id"]
            accepted_ids.append(run_id)
            test_state["created_runs"].append(run_id)
        time.sleep(0.2)

    assert codes.count(202) >= 3
    assert 429 in codes

    for run_id in accepted_ids:
        cancel = api_client.post(f"{API}/registration/runs/{run_id}/cancel", timeout=30)
        assert cancel.status_code in {200, 409}

    for run_id in accepted_ids:
        terminal = poll_run(api_client, run_id)
        assert terminal["status"] in {"completed", "failed", "cancelled"}
        report = api_client.get(f"{API}/registration/{run_id}/report", timeout=30)
        assert report.status_code == 200
