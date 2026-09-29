# Moon Match Points — pinned baseline and initial hardening audit

Date: 2026-09-29. Baseline: https://github.com/niceguy-available/SIH
Commit: `3504c49776f0f70ee2f2088659574fd3ceddf6dc` (read-only clone of main).
Original source available at `/tmp/moon-baseline` during this session; the commit is the reproducible durable identifier.

## Evidence from the baseline

| Area | Observed behavior | Consequence | Hardening |
|---|---|---|---|
| Input | `registration.py:read_source` reads entire file, then rejects >20 MB. OpenCV decodes without raster-header budgets. | Compressed raster allocation not bounded by upload size. | 128 MiB provisional streaming input / request cap, 16M-pixel / 256 MiB decoded / 32-band limits; explicit analysis band. |
| Pixel handling | `decode_image` normalizes everything to 8-bit BGR, reduces longest dimension to 1800, discards CRS. | Original dimensions/data lost; residual units become reduced pixels. | Rasterio metadata + explicit band, masked analysis pyramid, native center-coordinate transforms; original selected band exported by tiles. |
| Reference | `fetch_reference` loads `image_url`, which is a thumbnail/educational poster, not the scientific download. `resolve_solution` tries all four descriptive catalog items. | Unrelated global or poster content may be treated as scientific reference; blind matching and inferred footprint. | Explicit operator reference selection; curated previews clearly diagnostic-only; no auto substitution. |
| Metadata | Global/region bounds are descriptive manually supplied JSON. `76P` is used as 76 m/pixel without product metadata evidence. | Reference pixels and assumed resolution/CRS unverified. | Inherited descriptions marked unverified; physical resolution read from actual raster only. |
| Engine | Four matches minimum; mutual check drops to unilateral when weak. RANSAC partial affine then LMEDS full affine then ECC. | Weak/ambiguous fits can be accepted. | >=20 mutually ratio-tested matches, masked multiscale SIFT/ORB, deterministic RANSAC, bounded/guarded refinement, affine plausibility, overlap and coverage gates. |
| Metrics | Median inlier residual named `subpixel_accuracy`. MAE-like similarity called percent, includes unsuitable pixels. Selected 48 low-error inliers shown. | Fitting residual confused with independent accuracy. | Native reference-pixel fitting residuals; separate independent checkpoints, N/A without them; similarity 0–100 index on valid illuminated overlap, N/A for IIRS; full correspondence set in reports. |
| GeoTIFF | Pillow writes reduced RGB preview with handcrafted GeoKeys from descriptive bounds. | Unvalidated lunar CRS, data loss, misleading scientific artifact. | Rasterio tiled native-band GeoTIFF only for passing server checks and verified local north-up projected lunar reference grid. Global/geographic/polar/unvalidated geometry blocked. |
| Export | Download route has no quality check. Client blocks JSON when fit fails. | Direct API bypass; diagnostic failure evidence lost. | Gate recomputed on every GeoTIFF request; diagnostics always downloadable for recorded jobs, including failed/cancelled jobs. |
| Operation | Synchronous API, no queue admission/cancel/progress/provenance storage. | Concurrent work unbounded, no reproducible run record. | One killable worker / two queued jobs, 120-second processing deadline, Mongo records + private object storage + checksums. |

## Provider verification — 2026-09-29

- https://quickmap.lroc.im-ldi.com/ returned HTTP 200; it is a viewer. No supported programmatic arbitrary-region scientific pixel API verified.
- https://lroc.im-ldi.com/images/downloads/ returned HTTP 200. It explicitly describes a curated product collection. Scientific global/polar ISIS downloads are multi-GB archives; posters and preview PNGs are not region-search results.
- Actual allowlisted Tycho preview fetched and stored; SHA-256 `96cffe7afe9b5ee8978be0542c675b9a3f625cfab579c31b587d36ddfe7e87d9`. 385×578 RGB, no CRS. This is a **poster/context preview, not scientific reference pixels**.
- Provider checks measure page reachability, not pixel API availability. Fetch retries never select a different product. Explicit cached selection uses a previously stored record with provenance.

## Supported envelope (provisional, not mission acceptance)

- Preprocessed PNG/JPEG/WebP and GDAL-readable ordinary TIFF: uint8, uint16, int16, float32. Selected band only; <=32 bands / <=256 MiB total uncompressed raster. Rasterio reads bounded 1800px analysis arrays; OpenCV pyramids search features at multiple scales.
- Native OHRC, TMC-2, IIRS: **unverified**. No original samples or metadata attached at audit time. File extension TIFF does not certify sensor support. Native PDS/ISIS/IMG/HDF/raw decoding and orthorectification are unsupported.
- IIRS selectable for diagnostic exploration; intensity index N/A and export blocked until band-aware validation exists. PCA/gradient/mutual-information alternatives require real wavelength/response metadata and checkpoint tests.
- Scientific reference export envelope: north-up projected lunar ellipsoid radius 1,730–1,745 km; finite affine; maximum extent 300 km; geographic corners within ±75° and no longitude-wrap span. Lunar georeference validity here is metadata/geometry validation, not external survey certification.
- Export is **one original-resolution selected source band**, resampled bilinearly onto the reference CRS/affine/grid; validity mask included. No false claim of all-band/native sensor product preservation.
- Similarity index = `100*(1 - mean(abs(normalized warped-source - normalized reference))/255)` on the masked overlap; not used as a probability or export gate. Percentile normalization values and analysis scales retained.
- Fitting residuals and independent checkpoint residuals use native reference pixel centers. Independent points are operator-declared, distinct from fitting points, and are never used to estimate/refine the transform.
- Default gates: >=20 inliers, >=0.50 inlier ratio, <=1.5px fitting RMSE, >=0.20 valid-reference overlap, >=0.25 min(source/reference) spatial grid/hull coverage; no reflection, scale 0.25–4, anisotropy <=1.25. Independent checkpoints, when supplied, <=2px RMSE. These are conservative **provisional policy values**, not user-approved tolerances.

## Remaining release blockers

1. Original OHRC/TMC-2/IIRS samples with acquisition, bands/wavelengths, calibration, product level, map projection and independent ground/checkpoint truth.
2. User acceptance tolerances, supported physical resolutions/scale ratios, throughput/size/runtime targets.
3. Real scientific reference-provider retrieval interface and pixel-product provenance validation (automatic retrieval remains unverified).
4. Sensor/band-aware evaluation, severe illumination and terrain ambiguity datasets, wrapped/polar geometry validation (currently rejected for scientific exports).
5. Authentication/authorization, audit ownership, organisation isolation, retention and physical erasure policies before shared access. Runtime rejects any APP_MODE other than single_operator. **This is not authentication: the single-operator preview API must not contain confidential data or be shared.**
6. Managed storage supports soft deletion, not physical deletion. Local scratch cache is bounded; object retention is not yet an approved policy.

## Reproducing

Use the pinned baseline commit for before/after source comparison. Current API capabilities expose runtime limits. Each run records input SHA-256, reference product/provenance, selected bands, requested/resolved method, thresholds, affine matrices, every correspondence, OpenCV/Rasterio/GDAL/Numpy/Python versions and RNG seed. `backend/requirements.txt` is frozen. Frontend uses React + TypeScript; environment's CRA host replaces baseline's Vite host without changing the application stack or core two-column/comparison layout.

The old synchronous `/registration/run` is now a 202 asynchronous contract. Poll `/registration/runs/{id}`; diagnostics at `/{id}/report`. Engine comparison submits two independently bounded runs of the same files/reference. `/compare` instructs old clients to migrate to those reproducible runs.

Adjustments create child records, recompute transforms, all residuals, independent checkpoints and quality gates. Threshold changes are server-validated and cannot relax policy floors. Failed/cancelled/unsupported cases preserve the available diagnostic report, rather than fabricating correspondence or georeferencing.