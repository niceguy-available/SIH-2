"""Auto-reference LROC coordinate-matched retrieval + persistent cache tests."""
import os
from pathlib import Path
import pytest
import requests


def _base_url() -> str:
    v = os.environ.get("REACT_APP_BACKEND_URL", "").strip()
    if not v:
        for line in Path("/app/frontend/.env").read_text().splitlines():
            if line.startswith("REACT_APP_BACKEND_URL="):
                v = line.split("=", 1)[1].strip().strip('"')
    return v.rstrip("/")


API = f"{_base_url()}/api"


@pytest.fixture(scope="module")
def client():
    return requests.Session()


# --- /api/catalog/auto-reference ---
class TestAutoReference:
    def test_tycho_coords_returns_reference(self, client):
        r = client.post(
            f"{API}/catalog/auto-reference",
            params={"latitude": -43.31, "longitude": -11.36},
            timeout=90,
        )
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["matched_product_id"] == "lroc-tycho-crater"
        assert d["matched_product_title"] == "Tycho Crater Reference"
        assert d["status"] in {"cached-context-only", "context-only"}
        assert "from_cache" in d
        assert d["sha256"] and len(d["sha256"]) == 64
        assert d["id"]

    def test_repeated_call_reuses_cache(self, client):
        r1 = client.post(
            f"{API}/catalog/auto-reference",
            params={"latitude": -43.31, "longitude": -11.36},
            timeout=90,
        )
        assert r1.status_code == 200
        r2 = client.post(
            f"{API}/catalog/auto-reference",
            params={"latitude": -43.31, "longitude": -11.36},
            timeout=90,
        )
        assert r2.status_code == 200
        d2 = r2.json()
        assert d2["from_cache"] is True
        # same reference id => reused (persistent cache).
        assert r1.json()["id"] == d2["id"]

    def test_prefer_cache_false_forces_fresh(self, client):
        cached = client.post(
            f"{API}/catalog/auto-reference",
            params={"latitude": -43.31, "longitude": -11.36},
            timeout=90,
        ).json()
        fresh = client.post(
            f"{API}/catalog/auto-reference",
            params={"latitude": -43.31, "longitude": -11.36, "prefer_cache": "false"},
            timeout=120,
        )
        assert fresh.status_code == 200, fresh.text
        f = fresh.json()
        assert f["from_cache"] is False
        assert f["id"] != cached["id"]
        assert f["sha256"]  # sha256 present; may equal since same source file

    def test_invalid_latitude_422(self, client):
        r = client.post(
            f"{API}/catalog/auto-reference",
            params={"latitude": 200, "longitude": 0},
            timeout=30,
        )
        assert r.status_code == 422

    def test_unknown_region_no_substitute(self, client):
        # Coordinate outside every curated footprint -> honest 404, never substituted.
        r = client.post(
            f"{API}/catalog/auto-reference",
            params={"latitude": 5.0, "longitude": 5.0},
            timeout=30,
        )
        # If curated footprints don't cover 5,5 -> 404. If they do, must return legit match.
        assert r.status_code in (200, 404)
        if r.status_code == 404:
            assert "substituted" in r.json().get("detail", "").lower() or \
                   "no curated" in r.json().get("detail", "").lower()


# --- /api/catalog/search regression ---
class TestCatalogSearch:
    def test_search_returns_ranked_matches(self, client):
        r = client.get(
            f"{API}/catalog/search",
            params={"latitude": -43.31, "longitude": -11.36},
            timeout=30,
        )
        assert r.status_code == 200
        rows = r.json()
        assert isinstance(rows, list) and len(rows) > 0
        # Ranked ascending by distance_deg
        distances = [row["distance_deg"] for row in rows]
        assert distances == sorted(distances)
        assert rows[0]["item"]["id"] == "lroc-tycho-crater"


# --- /api/registration/capabilities regression ---
class TestCapabilities:
    def test_capabilities_contract(self, client):
        r = client.get(f"{API}/registration/capabilities", timeout=30)
        assert r.status_code == 200
        d = r.json()
        assert d["max_active_jobs"] == 1
        assert d["max_queued_jobs"] == 2
        assert d["max_upload_mib"] == 128
