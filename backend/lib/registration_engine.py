"""Hardened descendant of baseline SIFT/ORB, mutual ratio matches and RANSAC.

Only local optical affine geometry is supported. Residuals use native reference
pixel centers. No sensor-product or subpixel accuracy guarantee is implied.
"""
import math
import cv2
import numpy as np
from lib.raster import RegistrationError, grid_matrix

cv2.setNumThreads(1)
MIN_MATCHES = 20
RATIO_TEST = 0.70

def illumination_normalized(gray):
    return cv2.createCLAHE(clipLimit=2, tileGridSize=(8,8)).apply(gray)

def detector_for(method):
    if method == 'orb':
        return cv2.ORB_create(nfeatures=6000, scaleFactor=1.15, nlevels=12, edgeThreshold=15), cv2.NORM_HAMMING
    return cv2.SIFT_create(nfeatures=6000, contrastThreshold=0.025, edgeThreshold=12), cv2.NORM_L2

def matched_features(source_gray, reference_gray, source_mask, reference_mask, method):
    detector, norm = detector_for(method)
    # SIFT octaves / ORB pyramid are the multiscale feature search, not a forced resize fit.
    kp1, d1 = detector.detectAndCompute(source_gray, source_mask)
    kp2, d2 = detector.detectAndCompute(reference_gray, reference_mask)
    if d1 is None or d2 is None or min(len(d1),len(d2)) < MIN_MATCHES:
        raise RegistrationError('Too few features after nodata/shadow masking.')
    matcher = cv2.BFMatcher(norm)
    def ratio(pairs):
        return {p[0].queryIdx:p[0] for p in pairs if len(p)==2 and p[0].distance < RATIO_TEST*p[1].distance}
    forward = ratio(matcher.knnMatch(d1,d2,k=2))
    reverse = ratio(matcher.knnMatch(d2,d1,k=2))
    matches = [m for m in forward.values() if m.trainIdx in reverse and reverse[m.trainIdx].trainIdx == m.queryIdx]
    if len(matches) < MIN_MATCHES:
        raise RegistrationError('Ambiguous or insufficient mutual matches. No overlap, repeated terrain or cross-sensor differences may be responsible.')
    return np.float64([kp1[m.queryIdx].pt for m in matches]), np.float64([kp2[m.trainIdx].pt for m in matches]), matches

def geometry_valid(matrix):
    if not np.isfinite(matrix).all():
        return False
    singular = np.linalg.svd(matrix[:,:2], compute_uv=False)
    return bool(np.linalg.det(matrix[:,:2]) > 0 and singular.min() >= .25 and singular.max() <= 4 and singular.max()/singular.min() <= 1.25)

def transform_points(points, matrix):
    return points @ matrix[:,:2].T + matrix[:,2]

def coverage(points, width, height):
    if len(points) < 3:
        return 0.0
    cells = {(min(3,max(0,int(x/width*4))), min(3,max(0,int(y/height*4)))) for x,y in points}
    area = cv2.contourArea(cv2.convexHull(points.astype(np.float32)))/(width*height)
    return float(min(len(cells)/16, area))

def solve(source, reference, smask, rmask, smeta, rmeta, method='auto', refine=True, offset=None):
    sgray, rgray = illumination_normalized(source), illumination_normalized(reference)
    attempts = []
    for engine in (['sift','orb'] if method=='auto' else [method]):
        try:
            sp, rp, matches = matched_features(sgray,rgray,smask,rmask,engine)
            cv2.setRNGSeed(2026)
            reproj = 2.0 * min(rmeta['analysis_scale_x'], rmeta['analysis_scale_y'])
            matrix, mask = cv2.estimateAffinePartial2D(sp, rp, method=cv2.RANSAC, ransacReprojThreshold=reproj, maxIters=6000, confidence=.999, refineIters=20)
            if matrix is None or mask is None or int(mask.sum()) < MIN_MATCHES:
                raise RegistrationError('Robust estimation rejected the correspondence set.')
            inliers = mask.ravel().astype(bool)
            # A full affine can reduce bias, but never relax shape/plausibility checks.
            tuned, _ = cv2.estimateAffine2D(sp[inliers],rp[inliers],method=cv2.LMEDS,refineIters=20)
            if tuned is not None and geometry_valid(tuned) and np.mean(np.linalg.norm(transform_points(sp[inliers],tuned)-rp[inliers],axis=1)) < np.mean(np.linalg.norm(transform_points(sp[inliers],matrix)-rp[inliers],axis=1)):
                matrix = tuned
            if not geometry_valid(matrix):
                raise RegistrationError('Implausible scale, reflection, shear or anisotropy; local affine support exceeded.')
            ecc = None
            if refine and inliers.sum() >= MIN_MATCHES:
                try:
                    warped = cv2.warpAffine(sgray,matrix,(reference.shape[1],reference.shape[0]))
                    valid = cv2.warpAffine(smask,matrix,(reference.shape[1],reference.shape[0]),flags=cv2.INTER_NEAREST) & rmask
                    delta = np.eye(2,3,dtype=np.float32)
                    correlation, delta = cv2.findTransformECC(rgray,warped,delta,cv2.MOTION_AFFINE,(cv2.TERM_CRITERIA_EPS|cv2.TERM_CRITERIA_COUNT,40,1e-5),valid,5)
                    candidate = (np.vstack([cv2.invertAffineTransform(delta),[0,0,1]]) @ np.vstack([matrix,[0,0,1]]))[:2]
                    before = np.linalg.norm(transform_points(sp[inliers],matrix)-rp[inliers],axis=1)
                    after = np.linalg.norm(transform_points(sp[inliers],candidate)-rp[inliers],axis=1)
                    if geometry_valid(candidate) and np.max(np.abs(delta-np.eye(2,3))) < 2 and np.mean(after**2) <= np.mean(before**2):
                        matrix, ecc = candidate, float(correlation)
                except cv2.error:
                    pass
            full_matrix = (np.linalg.inv(grid_matrix(rmeta)) @ np.vstack([matrix,[0,0,1]]) @ grid_matrix(smeta))[:2]
            full_sp = transform_points(sp,np.linalg.inv(grid_matrix(smeta))[:2])
            full_rp = transform_points(rp,np.linalg.inv(grid_matrix(rmeta))[:2])
            if offset:
                full_matrix[:,2] += np.array(offset)
                matrix = (grid_matrix(rmeta) @ np.vstack([full_matrix,[0,0,1]]) @ np.linalg.inv(grid_matrix(smeta)))[:2]
            return evaluate_solution(source,reference,smask,rmask,smeta,rmeta,matrix,full_matrix,full_sp,full_rp,engine,ecc,inliers)
        except (RegistrationError, cv2.error) as exc:
            attempts.append(f'{engine.upper()}: {exc}')
    raise RegistrationError(' | '.join(attempts))

def evaluate_solution(source,reference,smask,rmask,smeta,rmeta,matrix,full_matrix,sp,rp,engine,ecc,original_inliers):
    predicted = transform_points(sp,full_matrix)
    errors = np.linalg.norm(predicted-rp,axis=1)
    inliers = original_inliers & (errors <= 2.0)
    size = (reference.shape[1],reference.shape[0])
    warped = cv2.warpAffine(source,matrix,size,flags=cv2.INTER_LINEAR)
    valid = (cv2.warpAffine(smask,matrix,size,flags=cv2.INTER_NEAREST)>0) & (rmask>0)
    overlap = float(valid.sum()/max(1,(rmask>0).sum()))
    similarity = None
    if valid.sum() >= 1024 and overlap >= .05 and np.std(reference[valid])>2 and np.std(warped[valid])>2:
        similarity = float(np.clip(100*(1-np.mean(np.abs(warped[valid].astype(float)-reference[valid]))/255),0,100))
    rmse = float(np.sqrt(np.mean(errors[inliers]**2))) if inliers.any() else None
    metrics = {'rmse':rmse, 'median_residual':float(np.median(errors[inliers])) if inliers.any() else None, 'p95_residual':float(np.percentile(errors[inliers],95)) if inliers.any() else None, 'inlier_count':int(inliers.sum()), 'total_matches':len(sp), 'inlier_ratio':float(inliers.mean()), 'overlap':overlap, 'spatial_coverage':min(coverage(sp[inliers],smeta['width'],smeta['height']),coverage(rp[inliers],rmeta['width'],rmeta['height'])), 'offset_x':float(full_matrix[0,2]), 'offset_y':float(full_matrix[1,2]), 'rotation_deg':math.degrees(math.atan2(full_matrix[1,0],full_matrix[0,0])), 'scale_ratio':float(np.linalg.norm(full_matrix[:,0])), 'image_similarity':similarity, 'similarity_definition':'0–100 normalized absolute-intensity similarity index on valid illuminated overlap; not accuracy or probability.', 'geometry_valid':geometry_valid(full_matrix), 'ecc_correlation':ecc, 'pixel_units':'native reference pixels', 'independent_accuracy':None}
    # All correspondences are retained, not a cherry-picked low-residual subset.
    points = [dict(index=i+1,source_x=float(s[0]),source_y=float(s[1]),reference_x=float(r[0]),reference_y=float(r[1]),predicted_x=float(p[0]),predicted_y=float(p[1]),residual_error=float(e),status='inlier' if inside else 'outlier') for i,(s,r,p,e,inside) in enumerate(zip(sp,rp,predicted,errors,inliers))]
    return {'metrics':metrics, 'match_points':points, 'affine_matrix':full_matrix.tolist(), 'analysis_matrix':matrix.tolist(), 'method':engine.upper(), 'preview':warped, 'valid_mask':valid.astype(np.uint8)*255}