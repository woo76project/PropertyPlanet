"use client";
import { SignInButton, useAuth } from "@clerk/nextjs";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import ImportJobPanel, { importStatusLabels } from "@/components/import-job-panel";
import ImportTemplates from "@/components/import-templates";
import { downloadImportFile, finishedImport, getInvalidRows, importError, ImportApiError, listImportJobs, pollImportJob, startImportJob, uploadImportFile, validateImportFile, type ImportHistory, type ImportJob, type InvalidRow } from "@/lib/import-api";

function saveDownload(blob: Blob, name: string) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a"); link.href = url; link.download = name;
  document.body.appendChild(link); link.click(); link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
export default function ImportManager() {
  const { isLoaded, isSignedIn, sessionId } = useAuth();
  if (!isLoaded) return <p role="status">Loading your session…</p>;
  if (!isSignedIn) return <section className="property-state"><h2>Sign in as Admin.</h2><p>Only Admin can upload property files and view import results.</p><SignInButton mode="modal" fallbackRedirectUrl="/admin/imports"><button className="button button-primary">Sign in to continue</button></SignInButton></section>;
  return <SignedInImports key={sessionId} />;
}
function SignedInImports() {
  const { getToken } = useAuth();
  const [history, setHistory] = useState<ImportHistory | null>(null);
  const [historyError, setHistoryError] = useState<{ message: string; status: number } | null>(null);
  const [cursors, setCursors] = useState<(string | null)[]>([null]);
  const [revision, setRevision] = useState(0);
  const [selected, setSelected] = useState<string | null>(null);
  const [job, setJob] = useState<ImportJob | null>(null);
  const [jobError, setJobError] = useState<string | null>(null);
  const [pollStopped, setPollStopped] = useState(false);
  const [pollRevision, setPollRevision] = useState(0);
  const [file, setFile] = useState<File | null>(null);
  const [fileKey, setFileKey] = useState(0);
  const [busy, setBusy] = useState<"upload" | "action" | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [rows, setRows] = useState<InvalidRow[]>([]);
  const [reportError, setReportError] = useState<string | null>(null);
  const operation = useRef<AbortController | null>(null);
  const mounted = useRef(false);
  const cursor = cursors[cursors.length - 1];
  async function token() {
    const value = await getToken();
    if (!value) throw new ImportApiError(401, "Your session has expired. Please sign in again.");
    return value;
  }
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; operation.current?.abort(); }; }, []);
  useEffect(() => {
    const controller = new AbortController();
    async function load() {
      try {
        const value = await getToken();
        if (!value) throw new ImportApiError(401, "Your session has expired. Please sign in again.");
        if (controller.signal.aborted) return;
        const data = await listImportJobs(value, cursor, controller.signal);
        if (!controller.signal.aborted) { setHistory(data); setHistoryError(null); }
      } catch (error) { if (!controller.signal.aborted) { setHistory(null); setHistoryError({ message: importError(error), status: error instanceof ImportApiError ? error.status : 503 }); } }
    }
    void load(); return () => controller.abort();
  }, [getToken, cursor, revision]);
  useEffect(() => {
    if (!selected) return;
    return pollImportJob(selected, getToken, (data) => {
      // During upload, the upload response supplies the newest progress.
      if (!operation.current) setJob(data);
      setJobError(null); setPollStopped(false);
    }, (error, retrying) => {
      setPollStopped(!retrying);
      setJobError(retrying ? "Connection interrupted. Reconnecting to check import progress…"
        : `Unable to check import progress. ${importError(error)}`);
      if (error instanceof ImportApiError && [401, 403].includes(error.status)) operation.current?.abort();
    });
  }, [getToken, selected, revision, pollRevision]);
  const reportId = job && finishedImport(job) && job.invalid_count > 0 ? job.id : null;
  useEffect(() => {
    if (!reportId) return;
    const controller = new AbortController();
    async function load() {
      try {
        const value = await getToken();
        if (!value) throw new ImportApiError(401, "Your session has expired. Please sign in again.");
        if (controller.signal.aborted) return;
        const data = await getInvalidRows(value, reportId!, controller.signal);
        if (!controller.signal.aborted) { setRows(data.rows); setReportError(null); }
      } catch (error) { if (!controller.signal.aborted) setReportError(importError(error)); }
    }
    void load(); return () => controller.abort();
  }, [getToken, reportId, revision]);

  function refresh() { setHistory(null); setHistoryError(null); setJobError(null); setPollStopped(false); setRevision((v) => v + 1); }
  function selectJob(next: ImportJob | null) {
    if (operation.current) return;
    setSelected(next?.id ?? null); setJob(next); setJobError(null); setPollStopped(false); setMessage(null);
    setRows([]); setReportError(null); setFile(null); setFileKey((v) => v + 1);
  }
  async function run(action: (signal: AbortSignal) => Promise<void>, kind: "upload" | "action") {
    if (operation.current || !history) return;
    const controller = new AbortController(); operation.current = controller;
    setBusy(kind); setMessage(null);
    try { await action(controller.signal); }
    catch (error) { if (!controller.signal.aborted && mounted.current) setMessage(importError(error)); }
    finally { if (mounted.current) { operation.current = null; setBusy(null); setRevision((v) => v + 1); } }
  }
  function upload() {
    if (!file) return;
    setRows([]); setReportError(null);
    void run(async (signal) => {
      await uploadImportFile(file, job?.status === "uploading" ? job : null, getToken, signal, (next) => {
        if (!signal.aborted && mounted.current) { setSelected(next.id); setJob(next); }
      }, (status) => { if (!signal.aborted && mounted.current) setMessage(status); });
      if (!signal.aborted && mounted.current) { setFile(null); setFileKey((v) => v + 1); setMessage("Upload complete. Your import is queued for background processing."); }
    }, "upload");
  }
  function download(id: string | null) {
    void run(async (signal) => { const blob = await downloadImportFile(await token(), id, signal); if (!signal.aborted) saveDownload(blob, id ? "invalid_property_rows.csv" : "property_template.xlsx"); }, "action");
  }
  function start(retry: boolean) {
    if (!job) return;
    void run(async (signal) => { const next = await startImportJob(await token(), job.id, retry, signal); if (!signal.aborted) { setJob(next); setMessage("Import queued for background processing."); } }, "action");
  }
  const needsProfile = historyError && [404, 428].includes(historyError.status);
  return <>
    <div className="browser-toolbar"><span className="browser-toolbar-caption">Upload once. Follow the results here.</span><button className="browser-refresh" disabled={!!busy} onClick={refresh}>↻ Refresh status</button></div>
    {historyError && <section className="property-state"><h2>{historyError.status === 403 ? "Admin access required." : "We could not load imports."}</h2><p role="alert">{historyError.message}</p>{needsProfile ? <Link className="button button-primary" href="/account">Complete your profile</Link> : <button className="button button-secondary" onClick={refresh}>Try again</button>}</section>}
    {!history && !historyError && <p className="property-state" role="status">Loading import history…</p>}
    {history && <>
      {message && <p className="import-message" role="status">{message}</p>}
      <div className="import-layout"><div>
        <section className="import-upload-card"><div className="import-section-top"><div><span className="eyebrow">EXCEL & CSV</span><h2>{job?.status === "uploading" ? "Resume this upload." : "Bring your records together."}</h2></div>{selected && <button className="button button-secondary" disabled={!!busy} onClick={() => selectJob(null)}>New upload</button>}</div>
          <p>Up to 500,000 records per file, with a maximum file size of 100 MiB. Valid rows are imported, identical records are skipped, and invalid rows are reported after processing.</p>
          <label className="import-file-label" htmlFor="property-import-file">{job?.status === "uploading" ? `Select the original file: ${job.filename}` : "Choose an Excel or CSV file"}</label><input key={fileKey} id="property-import-file" type="file" accept=".xlsx,.csv" disabled={!!busy} onChange={(event) => { const chosen = event.target.files?.[0] ?? null; setMessage(null); try { if (chosen) validateImportFile(chosen); setFile(chosen); } catch (error) { setFile(null); setMessage(importError(error)); } }} />
          {file && <p className="import-help">{file.name} · {(file.size / 1048576).toFixed(2)} MiB</p>}
          <div className="billing-actions"><button className="button button-primary" disabled={!file || !!busy} onClick={upload}>{busy === "upload" ? "Verifying & uploading…" : job?.status === "uploading" ? "Resume upload" : "Upload & import"}</button>{busy === "upload" && <button className="button button-secondary" onClick={() => { operation.current?.abort(); setMessage("Upload paused. Select this job and the same file to resume."); }}>Pause upload</button>}</div>
          <p className="import-help">Columns: Name, Phone 01, Phone 02, IC number, Address, Remark. Name and Phone 01 are required. Keep phone and IC columns as text to preserve leading zeros. Excel imports use the first worksheet.</p>
          <ImportTemplates busy={!!busy} onExcel={() => download(null)} onCsv={() => saveDownload(new Blob(["\ufeffName,Phone 01,Phone 02,IC number,Address,Remark\r\n"], { type: "text/csv;charset=utf-8" }), "property_template.csv")} />
        </section>
        {jobError && <div><p className="form-error" role={pollStopped ? "alert" : "status"}>{jobError}</p>{pollStopped && <button className="button button-secondary" onClick={() => { setJobError(null); setPollStopped(false); setPollRevision((v) => v + 1); }}>Retry status check</button>}</div>}
        {job && <ImportJobPanel job={job} rows={rows} reportError={reportError} busy={!!busy} uploadActive={busy === "upload"} onDownload={() => download(job.id)} onRetry={() => start(true)} onStart={() => start(false)} />}
      </div><aside className="import-history"><span className="eyebrow">IMPORT HISTORY</span><h2>Your recent files.</h2><p className="import-help">Select a job to see its latest progress and results.</p>{!history.jobs.length && <p className="import-help">No imports yet. Upload your first file to get started.</p>}<ul>{history.jobs.map((item) => { const row = job?.id === item.id ? job : item; return <li key={row.id}><button disabled={!!busy} aria-pressed={selected === row.id} onClick={() => selectJob(row)}><strong>{row.filename}</strong><span className={`import-status ${row.status}`}>{importStatusLabels[row.status]}</span><span>{new Intl.DateTimeFormat("en-MY", { dateStyle: "medium", timeZone: "Asia/Kuala_Lumpur" }).format(new Date(row.created_at))}</span></button></li>; })}</ul><nav className="import-history-pages" aria-label="Import history pages"><button disabled={!!busy || cursors.length === 1} onClick={() => { setHistory(null); setCursors(cursors.slice(0, -1)); }}>← Previous</button><span>{cursors.length}</span><button disabled={!!busy || !history.has_more} onClick={() => { if (history.next_before_id) { setHistory(null); setCursors([...cursors, history.next_before_id]); } }}>Next →</button></nav></aside></div>
    </>}
  </>;
}
