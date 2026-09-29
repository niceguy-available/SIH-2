"""Conservative prototype checks, not certified mission acceptance tolerances."""
import math
from models.registration import Thresholds

def evaluate_gate(report):
    limits = Thresholds(**report.get('thresholds', {}))
    metrics = report.get('metrics') or {}
    checks = []
    def add(key, label, passed, value=None, limit=None, unit=''):
        checks.append(dict(key=key, label=label, passed=bool(passed), value=value, limit=limit, unit=unit))
    for key, label, threshold, direction, unit in [
        ('inlier_count', 'Inlier count', limits.min_inliers, 'min', 'points'),
        ('inlier_ratio', 'Inlier ratio', limits.min_inlier_ratio, 'min', 'fraction'),
        ('rmse', 'Fitting RMSE', limits.max_rmse, 'max', 'reference px'),
        ('overlap', 'Valid overlap', limits.min_overlap, 'min', 'fraction'),
        ('spatial_coverage', 'Spatial coverage', limits.min_coverage, 'min', 'fraction'),
    ]:
        value = metrics.get(key)
        passed = value is not None and math.isfinite(value) and (value >= threshold if direction == 'min' else value <= threshold)
        add(key, label, passed, value, threshold, unit)
    add('geometry', 'Plausible local affine geometry', metrics.get('geometry_valid', False))
    add('sensor', 'Supported preprocessed optical band', report.get('sensor', 'optical') != 'iirs')
    source_metadata = report.get('source_metadata', {})
    add('source_geometry', 'Source geometry supported', not source_metadata.get('crs') or source_metadata.get('lunar_georeferencing_valid', False))
    checkpoint = report.get('checkpoint_accuracy')
    if checkpoint:
        add('checkpoints', 'Independent-checkpoint RMSE', checkpoint['rmse'] <= limits.max_checkpoint_rmse, checkpoint['rmse'], limits.max_checkpoint_rmse, 'reference px')
    passed = all(check['passed'] for check in checks)
    georef = report.get('reference_metadata', {}).get('lunar_georeferencing_valid', False)
    add('georeferencing', 'Valid local lunar georeferencing', georef)
    return {'passed': passed, 'export_allowed': passed and bool(georef) and report.get('status') == 'completed', 'checks': checks, 'policy': 'provisional-v1', 'independent_accuracy': 'operator-supplied checkpoints' if checkpoint else 'not evaluated'}