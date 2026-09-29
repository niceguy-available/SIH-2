"""One cancellable child process per run; parent owns deadlines and persistence."""
import json
import platform
from pathlib import Path
import cv2
import numpy as np
import rasterio
from lib.raster import load_analysis, export_tiled
from lib.registration_engine import solve
from lib.quality import evaluate_gate
from lib.config import VERSION, BASELINE

def process_run(run, source_path, reference_path, directory, queue):
    directory = Path(directory)
    report = dict(run)
    try:
        queue.put((15,'Validating raster metadata'))
        source, smask, smeta = load_analysis(source_path, run['source_band'])
        reference, rmask, rmeta = load_analysis(reference_path, run['reference_band'])
        cv2.imwrite(str(directory/'source.png'),source)
        cv2.imwrite(str(directory/'reference.png'),reference)
        report.update(source_metadata=smeta, reference_metadata=rmeta, source_width=smeta['width'],source_height=smeta['height'],reference_width=rmeta['width'],reference_height=rmeta['height'])
        queue.put((35,'Multiscale features · mutual matching'))
        if run.get('sensor') == 'iirs':
            report['diagnostics'].append('IIRS band-aware cross-sensor validation is not complete. GeoTIFF export is blocked.')
        result = solve(source,reference,smask,rmask,smeta,rmeta,run['requested_method'],run['refine'],run.get('adjustment'))
        queue.put((70,'Recomputing residuals and quality checks'))
        preview, valid = result.pop('preview'),result.pop('valid_mask')
        if run.get('sensor') == 'iirs':
            result['metrics']['image_similarity'] = None
        report.update(result, status='completed')
        if report.get('checkpoint_accuracy'):
            checkpoint = dict(report['checkpoint_accuracy'])
            points = checkpoint['points']
            source_points = np.array([[p['source_x'],p['source_y']] for p in points])
            reference_points = np.array([[p['reference_x'],p['reference_y']] for p in points])
            transform = np.array(report['affine_matrix'])
            errors = np.linalg.norm(reference_points-(source_points@transform[:,:2].T+transform[:,2]),axis=1)
            checkpoint.update(rmse=float(np.sqrt(np.mean(errors**2))),max_error=float(errors.max()),residuals=errors.tolist())
            report['checkpoint_accuracy']=checkpoint
        report['quality_gate'] = evaluate_gate(report)
        cv2.imwrite(str(directory/'preview.png'),preview)
        cv2.imwrite(str(directory/'mask.png'),valid)
        if report['quality_gate']['export_allowed']:
            queue.put((80,'Writing native-resolution tiled GeoTIFF'))
            export_tiled(source_path,reference_path,np.array(result['affine_matrix']),directory/'geotiff.tif',run['source_band'])
        if not rmeta['lunar_georeferencing_valid']:
            report['diagnostics'].append(rmeta['georeferencing_note'])
        report['diagnostics'].append('Acceptance limits are provisional. Fitting residuals are not independent-checkpoint accuracy. Native OHRC/TMC-2/IIRS support remains unverified.')
        if not report['quality_gate']['passed']:
            report['diagnostics'].append('Registration checks failed. This is a diagnostic solution, not an accepted product.')
    except Exception as exc:
        report.update(status='failed', stage='Registration rejected')
        report['diagnostics'].append(str(exc))
        report['quality_gate'] = evaluate_gate(report)
    report['software'] = {'engine':VERSION,'baseline_commit':BASELINE,'opencv':cv2.__version__,'rasterio':rasterio.__version__,'gdal':rasterio.__gdal_version__,'numpy':np.__version__,'python':platform.python_version(),'ransac_seed':2026}
    report.update(progress=100,stage='Complete' if report['status']=='completed' else 'Registration rejected')
    (directory/'report.json').write_text(json.dumps(report,indent=2,allow_nan=False))