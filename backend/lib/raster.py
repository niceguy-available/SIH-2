"""Bounded Rasterio reads; no native sensor decoding, no invented CRS."""
from pathlib import Path
import math
import cv2
import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.transform import Affine
from pyproj import CRS, Transformer
from lib.config import MAX_PIXELS, MAX_DECODED, MAX_DIMENSION

class RegistrationError(ValueError):
    pass

def inspect_raster(path, band=1):
    try:
        with rasterio.Env(GDAL_NUM_THREADS='1', GDAL_CACHEMAX=64*1024**2):
            with rasterio.open(path) as ds:
                if ds.driver not in {'GTiff', 'PNG', 'JPEG', 'WEBP'}:
                    raise RegistrationError('Unsupported container. Native OHRC, TMC-2, IIRS, ISIS/PDS and archives are not supported.')
                if ds.width < 32 or ds.height < 32 or ds.width*ds.height > MAX_PIXELS:
                    raise RegistrationError(f'Raster dimensions outside limits: 32 px minimum side; {MAX_PIXELS:,} pixels maximum.')
                if ds.count > 32 or ds.width*ds.height*ds.count*np.dtype(ds.dtypes[0]).itemsize > MAX_DECODED:
                    raise RegistrationError('Decoded raster exceeds the band or decompression budget.')
                if not 1 <= band <= ds.count:
                    raise RegistrationError(f'Band must be between 1 and {ds.count}.')
                if ds.dtypes[band-1] not in {'uint8', 'uint16', 'int16', 'float32'}:
                    raise RegistrationError('Supported analysis-band types: uint8, uint16, int16, float32.')
                lunar = False
                georef_note = 'Missing lunar CRS or affine georeferencing; diagnostic-only reference.'
                if ds.crs:
                    crs = CRS.from_wkt(ds.crs.to_wkt())
                    radius = crs.ellipsoid.semi_major_metre
                    lunar = 1730000 < radius < 1745000 and ds.transform != Affine.identity() and abs(ds.transform.determinant) > 1e-15
                    # This prototype accepts local, north-up projected lunar grids only.
                    local = crs.is_projected and abs(ds.transform.b) < 1e-10 and abs(ds.transform.d) < 1e-10 and ds.transform.a > 0 and ds.transform.e < 0
                    local = local and max(ds.bounds.right-ds.bounds.left, ds.bounds.top-ds.bounds.bottom) <= 300000
                    if local:
                        to_geo = Transformer.from_crs(crs, crs.geodetic_crs, always_xy=True)
                        corners = [to_geo.transform(x,y) for x,y in [(ds.bounds.left,ds.bounds.bottom),(ds.bounds.left,ds.bounds.top),(ds.bounds.right,ds.bounds.bottom),(ds.bounds.right,ds.bounds.top)]]
                        local = all(math.isfinite(lon+lat) and abs(lat)<75 for lon,lat in corners) and max(lon for lon,lat in corners)-min(lon for lon,lat in corners)<180
                    lunar = lunar and local
                    georef_note = 'Local projected lunar grid, verified from file CRS and affine.' if lunar else 'Unsupported/non-lunar geometry: geographic wrap, global extents, rotated grids and polar validation require review.'
                return {'width': ds.width, 'height': ds.height, 'bands': ds.count, 'selected_band': band, 'dtype': ds.dtypes[band-1], 'driver': ds.driver, 'crs': ds.crs.to_wkt() if ds.crs else None, 'transform': list(ds.transform)[:6], 'bounds': list(ds.bounds), 'nodata': float(ds.nodata) if ds.nodata is not None and math.isfinite(ds.nodata) else None, 'resolution': list(ds.res), 'lunar_georeferencing_valid': bool(lunar), 'georeferencing_note': georef_note, 'native_sensor_support': 'unverified'}
    except RegistrationError:
        raise
    except Exception as exc:
        raise RegistrationError('Corrupt or unreadable raster; no pixels were substituted.') from exc

def load_analysis(path, band=1):
    metadata = inspect_raster(path, band)
    ratio = min(1.0, MAX_DIMENSION/max(metadata['width'], metadata['height']))
    width, height = max(1, round(metadata['width']*ratio)), max(1, round(metadata['height']*ratio))
    with rasterio.open(path) as ds:
        arr = ds.read(band, out_shape=(height, width), masked=True, resampling=Resampling.average)
    valid = ~np.ma.getmaskarray(arr) & np.isfinite(arr.data)
    if np.count_nonzero(valid) < 1024:
        raise RegistrationError('Insufficient valid pixels after nodata masking.')
    lo, hi = np.percentile(arr.data[valid], [1, 99])
    if hi-lo < 1e-7:
        raise RegistrationError('Textureless or fully shadowed image; no reliable correspondence field.')
    gray = np.clip((arr.data.astype(np.float32)-lo)*255/(hi-lo), 0, 255)
    gray[~valid] = 0
    gray = np.nan_to_num(gray).astype(np.uint8)
    # Conservative deep-shadow/saturated exclusion, applied before CLAHE.
    mask = valid & (gray > 6) & (gray < 254)
    mask = cv2.erode(mask.astype(np.uint8)*255, np.ones((3,3), np.uint8))
    metadata.update(analysis_width=width, analysis_height=height, analysis_scale_x=width/metadata['width'], analysis_scale_y=height/metadata['height'], valid_fraction=float(np.mean(mask > 0)), normalization_percentiles=[float(lo), float(hi)])
    return gray, mask, metadata

def grid_matrix(metadata):
    sx, sy = metadata['analysis_scale_x'], metadata['analysis_scale_y']
    # Pixel-center conversion for reduced Rasterio grids.
    return np.array([[sx, 0, (sx-1)/2], [0, sy, (sy-1)/2], [0, 0, 1]], np.float64)

def export_tiled(source_path, reference_path, matrix, destination, band=1):
    """Warp the ORIGINAL selected band in bounded output tiles to the reference grid."""
    from rasterio.windows import Window
    matrix3 = np.vstack([matrix, [0,0,1]])
    inverse = np.linalg.inv(matrix3)
    with rasterio.Env(GDAL_CACHEMAX=64*1024**2), rasterio.open(source_path) as src, rasterio.open(reference_path) as ref:
        profile = dict(driver='GTiff', width=ref.width, height=ref.height, count=1, dtype=src.dtypes[band-1], crs=ref.crs, transform=ref.transform, tiled=True, blockxsize=256, blockysize=256, compress='deflate', BIGTIFF='IF_SAFER')
        with rasterio.open(destination, 'w', **profile) as output:
            output.update_tags(registration='Moon Match Points; provisional quality gate', source_band=str(band), accuracy='Fitting residuals are not independent accuracy', pixel_transform=str(matrix))
            for y in range(0, ref.height, 256):
                for x in range(0, ref.width, 256):
                    h, w = min(256, ref.height-y), min(256, ref.width-x)
                    corners = np.array([[x,y,1], [x+w,y,1], [x,y+h,1], [x+w,y+h,1]]) @ inverse.T
                    left = max(0, int(np.floor(corners[:,0].min()))-2)
                    top = max(0, int(np.floor(corners[:,1].min()))-2)
                    right = min(src.width, int(np.ceil(corners[:,0].max()))+2)
                    bottom = min(src.height, int(np.ceil(corners[:,1].max()))+2)
                    tile = np.zeros((h,w), dtype=src.dtypes[band-1])
                    valid = np.zeros((h,w), dtype=np.uint8)
                    if right > left and bottom > top:
                        block = src.read(band, window=Window(left, top, right-left, bottom-top), masked=True)
                        local = np.array(matrix, dtype=np.float64).copy()
                        local[:,2] += local[:,:2] @ np.array([left,top]) - np.array([x,y])
                        tile = cv2.warpAffine(block.filled(0), local, (w,h), flags=cv2.INTER_LINEAR)
                        valid = cv2.warpAffine((~np.ma.getmaskarray(block)).astype(np.uint8)*255, local, (w,h), flags=cv2.INTER_NEAREST)
                        valid &= ref.read_masks(1, window=Window(x,y,w,h))
                    output.write(tile, 1, window=Window(x,y,w,h))
                    output.write_mask(valid, window=Window(x,y,w,h))