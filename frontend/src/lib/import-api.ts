export const MAX_IMPORT_BYTES = 100 * 1024 * 1024;
export const CHUNK_BYTES = 5 * 1024 * 1024;
export type ImportStatus = "uploading" | "queued" | "processing" | "completed" | "completed_with_errors" | "failed" | "expired";
export type ImportJob = {
  id: string; filename: string; file_size: number; status: ImportStatus; uploaded_bytes: number;
  total_records: number | null; processed_rows: number; imported_count: number;
  duplicate_count: number; invalid_count: number; error_message: string | null; created_at: string;
};
export type ImportHistory = { jobs: ImportJob[]; has_more: boolean; next_before_id: string | null };
export type InvalidRow = { row: number; errors: { field: string; message: string }[] };
export class ImportApiError extends Error {
  status: number;
  constructor(status: number, message: string) { super(message); this.status = status; }
}
class ImportRequestTimeout extends Error {}

async function withDeadline<T>(signal: AbortSignal | undefined, milliseconds: number, action: (signal: AbortSignal) => Promise<T>): Promise<T> {
  if (signal?.aborted) throw new DOMException("Request cancelled", "AbortError");
  const controller = new AbortController();
  let timedOut = false;
  const cancel = () => controller.abort();
  signal?.addEventListener("abort", cancel, { once: true });
  let rejectAbort: () => void = () => {};
  const aborted = new Promise<never>((_, reject) => {
    rejectAbort = () => reject(timedOut
      ? new ImportRequestTimeout("The import request timed out. Refresh the job status before resuming.")
      : new DOMException("Request cancelled", "AbortError"));
    controller.signal.addEventListener("abort", rejectAbort, { once: true });
  });
  const timer = setTimeout(() => { timedOut = true; controller.abort(); }, milliseconds);
  try {
    // The race also bounds token retrieval and response bodies, not just headers.
    return await Promise.race([action(controller.signal), aborted]);
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener("abort", cancel);
    controller.signal.removeEventListener("abort", rejectAbort);
  }
}

function retryDelay(signal: AbortSignal, milliseconds: number) {
  return new Promise<void>((resolve, reject) => {
    const cancel = () => { clearTimeout(timer); reject(new DOMException("Upload paused", "AbortError")); };
    const timer = setTimeout(() => { signal.removeEventListener("abort", cancel); resolve(); }, milliseconds);
    if (signal.aborted) cancel();
    else signal.addEventListener("abort", cancel, { once: true });
  });
}
export const finishedImport = (job: ImportJob) => ["completed", "completed_with_errors", "failed", "expired"].includes(job.status);
const validId = (value: unknown): value is string => typeof value === "string" && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value);
function jobPath(id: string) {
  if (!validId(id)) throw new Error("Invalid import job ID.");
  return `/properties/import-jobs/${id}`;
}
function readJob(value: unknown): ImportJob {
  if (!value || typeof value !== "object") throw new Error("Invalid import job response.");
  const job = value as ImportJob;
  if (!validId(job.id) || typeof job.filename !== "string" || !["uploading", "queued", "processing", "completed", "completed_with_errors", "failed", "expired"].includes(job.status)
    || ![job.file_size, job.uploaded_bytes, job.processed_rows, job.imported_count, job.duplicate_count, job.invalid_count].every((n) => Number.isSafeInteger(n) && n >= 0)
    || job.file_size < 1 || job.file_size > MAX_IMPORT_BYTES || job.uploaded_bytes > job.file_size
    || !(job.total_records === null || (Number.isSafeInteger(job.total_records) && job.total_records >= 0))
    || !(job.error_message === null || typeof job.error_message === "string")
    || typeof job.created_at !== "string" || !Number.isFinite(Date.parse(job.created_at))) throw new Error("Invalid import job response.");
  return job;
}
async function request(token: string, path: string, signal?: AbortSignal, method = "GET", body?: BodyInit, contentType?: string, format: "json" | "blob" = "json") {
  const base = process.env.NEXT_PUBLIC_API_URL;
  if (!base) throw new Error("The import service has not been configured yet.");
  return withDeadline(signal, method === "PUT" ? 60_000 : 30_000, async (requestSignal) => {
    const response = await fetch(`${base.replace(/\/$/, "")}${path}`, {
      method, body, signal: requestSignal, credentials: "omit", cache: "no-store", redirect: "error",
      headers: { Authorization: `Bearer ${token}`, ...(contentType ? { "Content-Type": contentType } : {}) },
    });
    if (!response.ok) {
      const error = await response.json().catch(() => null);
      throw new ImportApiError(response.status, typeof error?.detail === "string" ? error.detail
        : "The import request could not be completed. Refresh its status before trying again.");
    }
    return format === "blob" ? response.blob() : response.json();
  });
}
export function validateImportFile(file: Pick<File, "name" | "size">) {
  if (!/\.(csv|xlsx)$/i.test(file.name)) throw new Error("Choose an Excel .xlsx or CSV .csv file.");
  if (!file.size || file.size > MAX_IMPORT_BYTES) throw new Error("Choose a nonempty file of at most 100 MiB (104,857,600 bytes).");
  if (file.name.length > 255 || /[\x00-\x1f]/.test(file.name)) throw new Error("Use a filename of at most 255 characters without control characters.");
}
export async function listImportJobs(token: string, beforeId: string | null, signal?: AbortSignal): Promise<ImportHistory> {
  if (beforeId) jobPath(beforeId);
  const data = await request(token, `/properties/import-jobs?limit=5${beforeId ? `&before_id=${beforeId}` : ""}`, signal);
  if (!Array.isArray(data.jobs) || data.jobs.length > 5 || typeof data.has_more !== "boolean"
    || (data.has_more && (!data.jobs.length || !validId(data.next_before_id)))) throw new Error("Invalid import history response.");
  return { jobs: data.jobs.map(readJob), has_more: data.has_more, next_before_id: data.has_more ? data.next_before_id : null };
}
export async function getImportJob(token: string, id: string, signal?: AbortSignal) {
  return readJob(await request(token, jobPath(id), signal));
}
// One cancellable loop per selected job. Status failures never change job state.
export function pollImportJob(id: string, getToken: (options?: { skipCache?: boolean }) => Promise<string | null>, onJob: (job: ImportJob) => void, onError: (error: unknown, retrying: boolean) => void) {
  const controller = new AbortController();
  let timer: ReturnType<typeof setTimeout> | undefined;
  let failures = 0;
  async function poll() {
    try {
      const job = await withDeadline(controller.signal, 30_000, async (signal) => {
        async function load(fresh: boolean): Promise<ImportJob> {
          const token = await getToken(fresh ? { skipCache: true } : undefined);
          if (signal.aborted) throw new DOMException("Request cancelled", "AbortError");
          if (!token) throw new ImportApiError(401, "Your session has expired. Please sign in again.");
          return getImportJob(token, id, signal);
        }
        try { return await load(false); }
        catch (error) {
          // Retry a rejected/expired token once; a fresh 401 or any 403 stops.
          if (!(error instanceof ImportApiError) || error.status !== 401 || signal.aborted) throw error;
          return load(true);
        }
      });
      if (controller.signal.aborted) return;
      if (job.id !== id) throw new Error("Invalid import job response.");
      failures = 0;
      onJob(job);
      if (!finishedImport(job)) timer = setTimeout(() => { void poll(); }, 4000);
    } catch (error) {
      if (controller.signal.aborted) return;
      const temporary = error instanceof TypeError || error instanceof ImportRequestTimeout
        || (error instanceof ImportApiError && [408, 429, 500, 502, 503, 504].includes(error.status));
      const retrying = temporary && ++failures <= 5;
      onError(error, retrying);
      if (retrying) timer = setTimeout(() => { void poll(); }, Math.min(4000 * 2 ** (failures - 1), 30_000));
    }
  }
  void poll();
  return () => { controller.abort(); if (timer !== undefined) clearTimeout(timer); };
}
export async function startImportJob(token: string, id: string, retry: boolean, signal?: AbortSignal) {
  await request(token, `${jobPath(id)}/${retry ? "retry" : "start"}`, signal, "POST");
  return getImportJob(token, id, signal);
}
export async function uploadImportFile(file: File, existing: ImportJob | null, getToken: (options?: { skipCache?: boolean }) => Promise<string | null>, signal: AbortSignal, onJob: (job: ImportJob) => void, onStatus?: (message: string | null) => void) {
  validateImportFile(file);
  async function token(fresh = false) {
    const value = await withDeadline(signal, 30_000, () => getToken(fresh ? { skipCache: true } : undefined));
    if (signal.aborted) throw new DOMException("Upload paused", "AbortError");
    if (!value) throw new ImportApiError(401, "Your session has expired. Sign in again to resume.");
    return value;
  }
  let job: ImportJob;
  if (existing) {
    if (existing.filename !== file.name || existing.file_size !== file.size) throw new Error("Select the original file with the same filename and size to resume this upload.");
    job = await getImportJob(await token(), existing.id, signal);
    if (job.status !== "uploading") throw new Error("This job is already queued or processed. Refresh its status.");
  } else {
    const data = await request(await token(), "/properties/import-jobs", signal, "POST", JSON.stringify({ filename: file.name, file_size: file.size }), "application/json");
    if (data.chunk_bytes !== CHUNK_BYTES) throw new Error("The upload configuration has changed. Refresh this page.");
    job = readJob(data.job);
  }
  onJob(job);
  // Replay earlier chunks too: the server verifies their hashes so a different
  // file with the same name/size cannot silently corrupt a resumed upload.
  for (let offset = 0, index = 0; offset < file.size; offset += CHUNK_BYTES, index += 1) {
    const chunk = file.slice(offset, offset + CHUNK_BYTES);
    let data;
    let retriedAuthentication = false;
    for (let attempt = 0; attempt < 3; attempt += 1) {
      try {
        // A cached Clerk JWT may have little lifetime left when a 5 MiB upload
        // starts. Mint a fresh token immediately before each attempt.
        data = await request(await token(true), `${jobPath(job.id)}/chunks/${index}`, signal, "PUT", chunk, "application/octet-stream");
        break;
      } catch (error) {
        if (signal.aborted) throw error;
        const refreshAuthentication = error instanceof ImportApiError && error.status === 401 && !retriedAuthentication;
        if (refreshAuthentication) retriedAuthentication = true;
        const retryable = refreshAuthentication || error instanceof TypeError || error instanceof ImportRequestTimeout
          || (error instanceof ImportApiError && [408, 429, 500, 502, 503, 504].includes(error.status));
        if (!retryable) throw error;
        if (attempt === 2) throw new Error(`Upload stopped: chunk ${index + 1} failed after 3 attempts. Saved chunks are retained. Refresh status, select the same file and choose Resume upload.`);
        onStatus?.(refreshAuthentication
          ? `Refreshing your session and retrying chunk ${index + 1} (attempt ${attempt + 2} of 3)…`
          : `Connection interrupted. Retrying chunk ${index + 1} (attempt ${attempt + 2} of 3)…`);
        await retryDelay(signal, 1000 * (attempt + 1));
      }
    }
    onStatus?.(null);
    if (!Number.isSafeInteger(data.uploaded_bytes) || data.uploaded_bytes < job.uploaded_bytes || data.uploaded_bytes > file.size) throw new Error("Invalid upload progress. Refresh this job before resuming.");
    job = { ...job, uploaded_bytes: data.uploaded_bytes }; onJob(job);
  }
  job = await startImportJob(await token(), job.id, false, signal);
  onJob(job);
  return job;
}
export async function getInvalidRows(token: string, id: string, signal?: AbortSignal): Promise<{ rows: InvalidRow[]; hasMore: boolean }> {
  const data = await request(token, `${jobPath(id)}/errors?limit=50`, signal);
  if (!Array.isArray(data.invalid_rows) || data.invalid_rows.length > 50 || typeof data.has_more !== "boolean"
    || !data.invalid_rows.every((row: InvalidRow) => Number.isSafeInteger(row.row) && row.row >= 2 && Array.isArray(row.errors)
      && row.errors.every((error) => typeof error.field === "string" && typeof error.message === "string"))) throw new Error("Invalid row report response.");
  return { rows: data.invalid_rows, hasMore: data.has_more };
}
export async function downloadImportFile(token: string, id: string | null, signal?: AbortSignal) {
  return request(token, id ? `${jobPath(id)}/errors/download` : "/properties/import-template", signal, "GET", undefined, undefined, "blob");
}
export function importError(error: unknown) {
  return error instanceof TypeError ? "Unable to reach the import service. Refresh the job status before resuming."
    : error instanceof Error ? error.message : "The import request could not be completed.";
}
