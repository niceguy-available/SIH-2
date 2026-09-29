"""Content-based ranking of cached reference images against an uploaded source.

The source is matched against every candidate reference with illumination-
normalised SIFT (scale/rotation invariant), mutual ratio-test matching and a
RANSAC similarity fit. Candidates are ranked by geometrically consistent
inliers, so the chosen reference is the one that actually shares terrain with
the source rather than the one with the largest catalogue footprint.
"""
import threading
from collections import OrderedDict
import cv2
import numpy as np
from lib.raster import load_analysis

MATCH_DIMENSION = 1024
MIN_CONFIDENT_INLIERS = 12
RATIO_TEST = 0.75
_cache = OrderedDict()
_cache_lock = threading.Lock()
_CACHE_SIZE = 64


def _prepare(gray, mask):
    scale = min(1.0, MATCH_DIMENSION/max(gray.shape))
    if scale < 1.0:
        size = (max(1, round(gray.shape[1]*scale)), max(1, round(gray.shape[0]*scale)))
        gray = cv2.resize(gray, size, interpolation=cv2.INTER_AREA)
        mask = cv2.resize(mask, size, interpolation=cv2.INTER_NEAREST)
    gray = cv2.createCLAHE(clipLimit=2, tileGridSize=(8, 8)).apply(gray)
    return gray, mask


def features_from_path(path, band=1):
    gray, mask, metadata = load_analysis(path, band)
    gray, mask = _prepare(gray, mask)
    detector = cv2.SIFT_create(nfeatures=4000, contrastThreshold=0.02, edgeThreshold=12)
    keypoints, descriptors = detector.detectAndCompute(gray, mask)
    points = np.float32([kp.pt for kp in keypoints]).reshape(-1, 2)
    return dict(points=points, descriptors=descriptors, shape=gray.shape, metadata=metadata)


def reference_features(path, band, key):
    """Reference descriptors are cached by checksum; references are immutable."""
    with _cache_lock:
        if key in _cache:
            _cache.move_to_end(key)
            return _cache[key]
    features = features_from_path(path, band)
    with _cache_lock:
        _cache[key] = features
        while len(_cache) > _CACHE_SIZE:
            _cache.popitem(last=False)
    return features


def score_pair(source, reference):
    result = dict(keypoints_source=len(source['points']), keypoints_reference=len(reference['points']), matches=0, inliers=0, inlier_ratio=0.0, scale=None, rotation_deg=None, score=0.0)
    d1, d2 = source['descriptors'], reference['descriptors']
    if d1 is None or d2 is None or len(d1) < 8 or len(d2) < 8:
        result['note'] = 'Too few features.'
        return result
    matcher = cv2.BFMatcher(cv2.NORM_L2)

    def ratio(pairs):
        return {p[0].queryIdx: p[0] for p in pairs if len(p) == 2 and p[0].distance < RATIO_TEST*p[1].distance}
    forward = ratio(matcher.knnMatch(d1, d2, k=2))
    reverse = ratio(matcher.knnMatch(d2, d1, k=2))
    matches = [m for m in forward.values() if m.trainIdx in reverse and reverse[m.trainIdx].trainIdx == m.queryIdx]
    result['matches'] = len(matches)
    if len(matches) < 4:
        result['note'] = 'Too few mutual matches.'
        return result
    sp = np.float32([source['points'][m.queryIdx] for m in matches])
    rp = np.float32([reference['points'][m.trainIdx] for m in matches])
    cv2.setRNGSeed(2026)
    matrix, mask = cv2.estimateAffinePartial2D(sp, rp, method=cv2.RANSAC, ransacReprojThreshold=3.0, maxIters=4000, confidence=.995)
    if matrix is None or mask is None:
        result['note'] = 'No consistent similarity transform.'
        return result
    inliers = mask.ravel().astype(bool)
    scale = float(np.linalg.norm(matrix[:, 0]))
    # Scale differences between sensors are expected, but a degenerate fit is not.
    if not np.isfinite(matrix).all() or not 1/50 <= scale <= 50:
        result['note'] = 'Degenerate transform.'
        return result
    count = int(inliers.sum())
    # Inliers spread over the source are stronger evidence than a clustered handful.
    spread = 0.0
    if count >= 3:
        hull = cv2.convexHull(sp[inliers])
        spread = float(min(1.0, cv2.contourArea(hull)/max(1.0, source['shape'][0]*source['shape'][1])))
    result.update(inliers=count, inlier_ratio=count/len(matches), scale=scale, rotation_deg=float(np.degrees(np.arctan2(matrix[1, 0], matrix[0, 0]))), spread=spread)
    result['score'] = float(count*(0.5+0.5*result['inlier_ratio'])*(0.5+0.5*np.sqrt(spread))) if count >= 4 else 0.0
    return result


def rank_references(source, candidates):
    """candidates: dicts with id, title, path, band, sha256 (plus passthrough fields)."""
    rows = []
    for candidate in candidates:
        row = {k: v for k, v in candidate.items() if k not in {'path', 'band'}}
        try:
            reference = reference_features(candidate['path'], candidate['band'], candidate['sha256'])
            row.update(score_pair(source, reference))
        except Exception as exc:
            row.update(score=0.0, inliers=0, matches=0, inlier_ratio=0.0, note=f'Unreadable: {exc}')
        rows.append(row)
    return sorted(rows, key=lambda r: (-r['score'], -r['inliers'], -r['matches']))
