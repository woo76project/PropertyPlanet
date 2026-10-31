import { finishedImport, type ImportJob, type InvalidRow } from "@/lib/import-api";
export const importStatusLabels = { uploading: "Uploading", queued: "Waiting to process", processing: "Processing", completed: "Completed", completed_with_errors: "Completed with invalid rows", failed: "Failed", expired: "Upload expired" };
const fieldLabels: Record<string, string> = { owner_name: "Name", phone_01: "Phone 01", phone_02: "Phone 02", ic_number: "IC number", address: "Address", remark: "Remark" };
export default function ImportJobPanel({ job, rows, reportError, busy, uploadActive = false, onDownload, onRetry, onStart }: {
  job: ImportJob; rows: InvalidRow[]; reportError: string | null; busy: boolean;
  onDownload: () => void; onRetry: () => void; onStart: () => void;
  uploadActive?: boolean;
}) {
  const upload = job.status === "uploading";
  const complete = job.status === "completed" || job.status === "completed_with_errors";
  const estimated = !upload && !complete && job.total_records !== null;
  const progress = upload ? job.uploaded_bytes / job.file_size : complete ? 1 : job.total_records ? Math.min(.99, job.processed_rows / job.total_records) : job.status === "processing" || job.status === "queued" ? undefined : 0;
  const percentage = progress === undefined ? undefined : Math.floor(progress * 100);
  const progressLabel = upload
    ? `${(job.uploaded_bytes / 1048576).toFixed(1)} of ${(job.file_size / 1048576).toFixed(1)} MiB uploaded`
    : job.total_records === null
      ? job.processed_rows > 0 ? `${job.processed_rows.toLocaleString()} records processed` : "Opening the file"
      : `${job.processed_rows.toLocaleString()} of ${estimated ? "approximately " : ""}${job.total_records.toLocaleString()} records processed`;
  return <section className="import-job-panel" aria-labelledby="import-job-title"><div className="import-section-top"><div><span className="eyebrow">SELECTED IMPORT</span><h2 id="import-job-title">{job.filename}</h2></div><span className={`import-status ${job.status}`}>{upload && !uploadActive ? "Upload paused" : importStatusLabels[job.status]}</span></div>
    <p className="import-job-id">Job {job.id}</p>
    <div className="import-progress-label"><span>{progressLabel}</span>{percentage !== undefined && <strong>{estimated ? "~" : ""}{percentage}%</strong>}</div>
    <div className="import-progress-track" role="progressbar" aria-label={upload ? "Upload progress" : "Processing progress"} aria-valuemin={0} aria-valuemax={100} aria-valuenow={percentage} aria-valuetext={progressLabel}>
      <span className={progress === undefined ? "import-progress-indeterminate" : undefined} style={progress === undefined ? undefined : { width: `${progress * 100}%` }} />
    </div>
    {job.status === "queued" && <p className="import-help">Your file is uploaded. Processing will begin when the import worker picks it up.</p>}
    {job.status === "processing" && <p className="import-help">Processing continues in the background. You can leave this page and find this job in the history later.</p>}
    {upload && <p className="import-help">{uploadActive ? "Keep this page open until uploading finishes." : "Upload is paused or interrupted."} To resume an interrupted upload, select the same file below. Previously uploaded chunks are checked before continuing.</p>}
    <dl className="import-counts"><div><dt>Inserted</dt><dd>{job.imported_count.toLocaleString()}</dd></div><div><dt>Duplicates skipped</dt><dd>{job.duplicate_count.toLocaleString()}</dd></div><div><dt>Invalid rows</dt><dd>{job.invalid_count.toLocaleString()}</dd></div></dl>
    {job.error_message && <p className="form-error" role="alert">{job.error_message}</p>}
    {job.status === "failed" && <p className="import-help">Saved batches remain imported. Retry continues from the saved checkpoint. Failed and incomplete uploads expire after 24 hours without activity. If the file needs correction, upload a corrected file as a new job.</p>}
    {job.status === "expired" && <p className="import-help" role="status">This upload expired after 24 hours without activity. Choose New upload and select your file again. Previously imported records, history and error reports are retained.</p>}
    <div className="billing-actions">{job.status === "failed" && <button className="button button-secondary" disabled={busy} onClick={onRetry}>Retry processing</button>}{upload && job.uploaded_bytes === job.file_size && <button className="button button-primary" disabled={busy} onClick={onStart}>Start processing</button>}{finishedImport(job) && job.invalid_count > 0 && <button className="button button-secondary" disabled={busy} onClick={onDownload}>Download invalid-row report</button>}</div>
    {finishedImport(job) && job.invalid_count > 0 && <div className="import-invalid-rows"><h3>Rows to check</h3><p className="import-help">Preview of up to 50 invalid rows. The CSV report includes every invalid row and its errors. Row numbers refer to the original file, including the heading row.</p>{reportError && <p className="form-error" role="alert">{reportError}</p>}<ul>{rows.map((row) => <li key={row.row}><strong>Row {row.row.toLocaleString()}</strong><ul>{row.errors.map((error, index) => <li key={index}>{fieldLabels[error.field] || "Row"}: {error.message}</li>)}</ul></li>)}</ul></div>}
  </section>;
}
