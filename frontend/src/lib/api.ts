export const BASE = process.env.REACT_APP_BACKEND_URL;
if (!BASE) throw new Error('REACT_APP_BACKEND_URL is required');
export const artifactUrl = (path: string) => path.startsWith('/api/') ? `${BASE}${path}` : path;
async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const multipart = body instanceof FormData;
  const response = await fetch(`${BASE}/api${path}`, {method,headers:body && !multipart ? {'Content-Type':'application/json'} : undefined,body:body ? (multipart ? body : JSON.stringify(body)) as BodyInit : undefined});
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    const detail = typeof error.detail === 'string' ? error.detail : Array.isArray(error.detail) ? error.detail.map((x: {msg:string})=>x.msg).join('; ') : `Request failed (${response.status})`;
    throw new Error(detail);
  }
  return response.json();
}
export const apiGet = <T,>(path:string) => request<T>('GET',path);
export const apiPost = <T,>(path:string,body?:unknown) => request<T>('POST',path,body);
export const apiPut = <T,>(path:string,body:unknown) => request<T>('PUT',path,body);
export const apiDelete = <T,>(path:string) => request<T>('DELETE',path);
export const apiPostForm = apiPost;
export interface Metadata {width:number;height:number;bands:number;selected_band:number;dtype:string;crs:string|null;lunar_georeferencing_valid:boolean;georeferencing_note:string;analysis_width?:number;analysis_height?:number;resolution:number[];native_sensor_support:string;}
export interface ReferenceRecord {id:string;title:string;status:string;image_url:string;metadata:Metadata;provenance:string;sha256:string;created_at:string;from_cache?:boolean;matched_product_id?:string;matched_product_title?:string;match_distance_deg?:number;}
export interface CatalogItem {id:string;title:string;product_id:string;product_type:string;image_url:string;source_url:string;download_url:string;resolution:string;location:string;footprint:{bounds:{west:number;east:number;south:number;north:number};center_latitude:number;center_longitude:number;review_status:string;};}
export interface CoordinateMatch {item:CatalogItem;contains:boolean;distance_deg:number;}
export interface MatchPoint {index:number;source_x:number;source_y:number;reference_x:number;reference_y:number;predicted_x:number;predicted_y:number;residual_error:number;status:string;}
export interface Thresholds {min_inliers:number;min_inlier_ratio:number;max_rmse:number;min_overlap:number;min_coverage:number;max_checkpoint_rmse:number;}
export interface Metrics {rmse:number|null;median_residual:number|null;p95_residual:number|null;inlier_count:number;total_matches:number;inlier_ratio:number;overlap:number;spatial_coverage:number;image_similarity:number|null;offset_x:number;offset_y:number;rotation_deg:number;scale_ratio:number;ecc_correlation:number|null;}
export interface Gate {passed:boolean;export_allowed:boolean;checks:{key:string;label:string;passed:boolean;value?:number;limit?:number;unit?:string}[];independent_accuracy?:string;}
export interface RegistrationResult {id:string;status:string;stage:string;progress:number;source_filename:string;generated_at:string;diagnostics:string[];metrics:Metrics|null;match_points:MatchPoint[];quality_gate:Gate;thresholds:Thresholds;reference_id:string;reference_title:string;reference_status:string;reference_provenance:string;reference_sha256:string;source_sha256:string;reference_metadata?:Metadata;source_metadata?:Metadata;source_width:number;source_height:number;reference_width:number;reference_height:number;source_url:string;preview_url:string;reference_url:string;geotiff_url:string;report_url:string;affine_matrix?:number[][];method?:string;requested_method:string;elapsed_seconds?:number;parent_run_id?:string;checkpoint_accuracy?:{rmse:number;count:number;provenance:string;};}
export interface Capabilities {max_upload_mib:number;max_pixels:number;max_job_seconds:number;max_active_jobs:number;max_queued_jobs:number;thresholds:Thresholds;}
export const isRunning=(run:RegistrationResult|null)=>!!run && ['queued','running'].includes(run.status);
export const format=(value:number|null|undefined,digits=2)=>value==null?'N/A':value.toFixed(digits);
export async function downloadArtifact(path:string,filename:string) {
  const response=await fetch(artifactUrl(path));
  if (!response.ok) {const error=await response.json().catch(()=>({}));throw new Error(error.detail||'Artifact unavailable');}
  const url=URL.createObjectURL(await response.blob());
  const link=document.createElement('a');link.href=url;link.download=filename;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
}