# Moon Match Points — PRD & Status

## Problem statement
Single-operator lunar image registration research workspace for Chandrayaan-2 optical
frames against LROC lunar references. Align a source (moving) image to a reference
(fixed) image, report trustworthy metrics, and export a quality-gated registered product.
Core philosophy: **an honest failure report is preferable to a convincing false match**;
never blindly substitute unrelated images.

Reference database (live): https://lroc.im-ldi.com/images/downloads/  (QuickMap viewer:
https://quickmap.lroc.im-ldi.com/). Note: the user's pasted URL `lroc.im.-ldi.com` was a
typo (unreachable); correct host is `lroc.im-ldi.com`.

Baseline repo: https://github.com/niceguy-available/SIH (commit 3504c49 == BASELINE_COMMIT).
This app IS that project (hardened). Website/design parity confirmed — no visual changes wanted.

## Architecture
- Frontend: React 18 + TypeScript (CRA/craco), Tailwind, shadcn/ui, TanStack Query, sonner.
  - `src/pages/Home.tsx` (workspace + Library + Run history views), `src/components/*`,
    `src/lib/api.ts` (typed fetch over REACT_APP_BACKEND_URL + /api).
- Backend: FastAPI (all routes under /api), MongoDB (motor), OpenCV (SIFT/ORB+RANSAC+ECC),
  rasterio/pyproj georeferencing, spawn-process bounded worker.
  - `routers/catalog.py` (catalog, search, auto-reference, references, context),
    `routers/registration.py` (run/runs/checkpoints/thresholds/adjust/artifacts),
    `lib/registration_engine.py`, `lib/raster.py`, `lib/jobs.py`, `lib/worker.py`,
    `lib/quality.py`, `lib/storage.py` (Emergent Object Storage), `lib/uploads.py`.
- Persistence: MongoDB (source of truth: runs, references, provider_checks) + Emergent
  Object Storage (durable artifacts). Pod-local `data/` is scratch/cache only.
- Env: APP_MODE=single_operator (shared mode blocked), MAX_UPLOAD_MB=128, MAX_RASTER_PIXELS=16M,
  MAX_JOB_SECONDS=120, MAX_ACTIVE_JOBS=1, MAX_QUEUED_JOBS=2.

## Implemented (2026-06)
- **Automatic coordinate-matched LROC reference retrieval + persistent cache** (DONE, tested 100%).
  - `POST /api/catalog/auto-reference?latitude&longitude&prefer_cache` — matches local curated
    footprint DB (`match_footprints`), reuses persistent cache first (`from_cache=true`, no LROC
    request), else fetches from allowlisted LROC URL, stores to object storage, tags `product_id`.
    Honest 422 (bad coords) / 404 (no match) / 503 (all fetches fail, no substitution).
  - Frontend "Auto-match from LROC" block in reference panel (lat/long → Auto-fetch → select +
    cache/fresh status + toast). Workflow: LROC → auto fetch → local cache/DB → reuse across sessions.
- **Release-gate deliberate-failure matrix** (DONE, 6/6 pass) — `backend/tests/test_deliberate_failures.py`:
  known rotation+scale recovery; no-overlap → honest failure; repeated terrain → not force-locked;
  severe shadow → honest failure; global geographic reference → export 403; unknown product → 404.
- **Object storage** put/restore round-trip verified (sha256 match). `backend/tests/test_auto_reference.py`
  (created by testing agent) covers auto-reference + search + capabilities.
- Pre-existing hardening: upload limits/quality gates, bounded cancellable jobs, SIFT/ORB+RANSAC+ECC,
  nodata/shadow masks, metrics (RMSE/inliers/overlap/coverage/0-100 similarity), independent checkpoints
  vs fitting residuals, quality-gated GeoTIFF export, run history, SIFT-vs-ORB compare.

## Backlog / next (P0→P2)
- P1: Band-aware IIRS cross-sensor validation (currently diagnostic-only, export blocked).
- P1: Native Chandrayaan-2 sensor product parsing (OHRC/TMC-2/IIRS) — BLOCKED on real native
  sample containers; user attached only PNG/JPEG/WebP demo frames (work as diagnostic sources).
- P2: Access controls to unblock shared organisational use (currently single_operator only).
- Optional: suppress benign React hydration warning (ve-dynamic <span> inside <option>).
- Deferred (do NOT implement): whole-Moon blind/content retrieval, raw-sensor orthorectification,
  batch expansion, unrelated UI redesign.

## Test credentials
None — no auth (single-operator prototype).
