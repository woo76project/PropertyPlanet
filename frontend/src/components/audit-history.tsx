"use client";
import { SignInButton, useAuth } from "@clerk/nextjs";
import Link from "next/link";
import { useEffect, useState, type FormEvent } from "react";
import AuditTable from "@/components/audit-table";
import { AUDIT_CATEGORIES, AuditApiError, auditError, loadAuditPage, type AuditCategory, type AuditPage } from "@/lib/audit-api";
type Filters = { category: "" | AuditCategory; actorId: number | null; actorUsername: string; limit: number; cursors: (number | null)[] };
const initial: Filters = { category: "", actorId: null, actorUsername: "", limit: 20, cursors: [null] };
export default function AuditHistory() {
  const { isLoaded, isSignedIn, sessionId } = useAuth();
  if (!isLoaded) return <p role="status">Loading your session…</p>;
  if (!isSignedIn) return <section className="property-state"><h2>Sign in as Admin.</h2><p>Only Admin can view audit history.</p><SignInButton mode="modal" fallbackRedirectUrl="/admin/audit"><button className="button button-primary">Sign in to continue</button></SignInButton></section>;
  return <SignedInHistory key={sessionId} />;
}
function SignedInHistory() {
  const { getToken } = useAuth();
  const [filters, setFilters] = useState<Filters>(initial);
  const [actor, setActor] = useState("");
  const [result, setResult] = useState<{ data: AuditPage } | { error: string; status: number } | null>(null);
  const [revision, setRevision] = useState(0);
  function refresh() { setResult(null); setRevision((v) => v + 1); }
  useEffect(() => {
    function onFocus() { setResult(null); setRevision((v) => v + 1); }
    window.addEventListener("focus", onFocus); return () => window.removeEventListener("focus", onFocus);
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    async function load() {
      try {
        const token = await getToken();
        if (controller.signal.aborted) return;
        if (!token) throw new AuditApiError(401, "Your session has expired. Please sign in again.");
        const data = await loadAuditPage(token, { category: filters.category, actorId: filters.actorId, actorUsername: filters.actorUsername, limit: filters.limit, beforeId: filters.cursors.at(-1) ?? null }, controller.signal);
        if (!controller.signal.aborted) setResult({ data });
      } catch (error) { if (!controller.signal.aborted) setResult({ error: auditError(error), status: error instanceof AuditApiError ? error.status : 503 }); }
    }
    void load(); return () => controller.abort();
  }, [getToken, filters, revision]);
  function update(next: Filters) { setResult(null); setFilters(next); }
  function submit(event: FormEvent<HTMLFormElement>) { event.preventDefault(); update({ ...filters, actorId: null, actorUsername: actor.trim(), cursors: [null] }); }
  const data = result && "data" in result ? result.data : null;
  const failure = result && "error" in result ? result : null;
  return <>
    <div className="browser-toolbar"><span className="browser-toolbar-caption">Read-only history · Newest events first</span><button className="browser-refresh" disabled={!result} onClick={refresh}>↻ Refresh history</button></div>
    <form className="property-filters audit-filters" onSubmit={submit} aria-label="Audit filters"><div><label htmlFor="audit-category">Category</label><select id="audit-category" value={filters.category} onChange={(event) => update({ ...filters, category: event.target.value as Filters["category"], cursors: [null] })}><option value="">All categories</option>{Object.entries(AUDIT_CATEGORIES).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></div><div><label htmlFor="audit-actor">Performed by (username)</label><input id="audit-actor" type="search" maxLength={200} value={actor} placeholder="Search username" onChange={(event) => setActor(event.target.value)} /></div><div><label htmlFor="audit-page-size">Per page</label><select id="audit-page-size" value={filters.limit} onChange={(event) => update({ ...filters, limit: Number(event.target.value), cursors: [null] })}>{[20, 50, 100].map((size) => <option key={size}>{size}</option>)}</select></div><button className="button button-primary">Apply filters ↗</button></form>
    {(filters.category || filters.actorUsername || filters.limit !== 20) && <div className="active-property-filters"><span>{filters.category ? AUDIT_CATEGORIES[filters.category] : "All categories"}{filters.actorUsername ? ` · ${filters.actorUsername}` : ""}</span><button onClick={() => { setActor(""); update(initial); }}>Clear filters</button></div>}
    <p className="audit-help">Search by all or part of a username, or click a username to show events from that account. Large imports create one event per processed batch. Updated records show field names; previous field values are not stored in this history.</p>
    {!result && <p className="property-state" role="status">Loading audit history…</p>}
    {failure && <section className="property-state"><h2>{failure.status === 403 ? "Admin access required." : "We could not load audit history."}</h2><p role="alert">{failure.error}</p>{failure.status === 404 || failure.error === "Complete your profile first" ? <Link className="button button-primary" href="/account">Complete your profile</Link> : <button className="button button-secondary" onClick={refresh}>Try again</button>}</section>}
    {data && <><div className="property-results-heading"><p>{data.audit_logs.length} events on this page</p><span>Page {filters.cursors.length}</span></div><AuditTable entries={data.audit_logs} onActor={(id, username) => { setActor(username); update({ ...filters, actorId: id, actorUsername: username, cursors: [null] }); }} /><nav className="property-pagination" aria-label="Audit history pages"><button className="button button-secondary" disabled={filters.cursors.length === 1} onClick={() => update({ ...filters, cursors: filters.cursors.slice(0, -1) })}>← Previous</button><span>{data.pagination.has_more ? "More events to review" : "End of these results"}</span><button className="button button-secondary" disabled={!data.pagination.has_more} onClick={() => { if (data.pagination.next_before_id) update({ ...filters, cursors: [...filters.cursors, data.pagination.next_before_id] }); }}>Next →</button></nav></>}
  </>;
}


