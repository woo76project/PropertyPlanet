"use client";

import { SignInButton, useAuth } from "@clerk/nextjs";
import Link from "next/link";
import { useEffect, useRef, useState, type FormEvent } from "react";
import PropertyResults from "@/components/property-results";
import PropertyForm from "@/components/property-form";
import SearchBuilder from "@/components/search-builder";
import { deleteProperty, loadPropertyPage, loadPropertyTotal, saveProperty, searchClueError, PropertyApiError, type PropertyData, type PropertyPageResult, type PropertyRecord, type SearchBuilder as Builder } from "@/lib/property-api";

type Filters = { search: string; limit: number; cursors: (number | null)[]; builder?: Builder | null; searchId?: string | null };
type Result = { data: PropertyPageResult } | { error: string; status: number };
const initialFilters: Filters = { search: "", limit: 20, cursors: [null] };

export default function PropertyBrowser({ adminMode = false }: { adminMode?: boolean }) {
  const { isLoaded, isSignedIn, sessionId } = useAuth();
  if (!isLoaded) return <p role="status">Loading your session…</p>;
  if (!isSignedIn) return <section className="property-state"><h2>{adminMode ? "Sign in as Admin." : "Your next opportunity starts here."}</h2><p>{adminMode ? "Only the platform Admin can manage property records." : "Sign in and complete your profile to browse property previews."}</p><SignInButton mode="modal" fallbackRedirectUrl={adminMode ? "/admin/properties" : "/properties"}><button className="button button-primary">Sign in to continue</button></SignInButton></section>;
  return <SignedInBrowser key={`${sessionId}-${adminMode}`} adminMode={adminMode} />;
}

function SignedInBrowser({ adminMode }: { adminMode: boolean }) {
  const { getToken } = useAuth();
  // Keep search conditions and paid request IDs in memory only. Reloading,
  // closing this page or signing out starts a fresh public-preview view.
  const [filters, setFilters] = useState<Filters>(initialFilters);
  const [search, setSearch] = useState("");
  const [builderReset, setBuilderReset] = useState(0);
  const [balance, setBalance] = useState<number | null>(null);
  const [unlimited, setUnlimited] = useState(false);
  const [result, setResult] = useState<Result | null>(null);
  const [revision, setRevision] = useState(0);
  const [totalProperties, setTotalProperties] = useState<number | null>(null);
  const [totalError, setTotalError] = useState(false);
  const [editor, setEditor] = useState<{ kind: "edit" | "delete"; property: PropertyRecord | null } | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [showCreditNotice, setShowCreditNotice] = useState(false);
  const mutation = useRef<AbortController | null>(null);
  const editorPanel = useRef<HTMLDivElement>(null);

  useEffect(() => () => mutation.current?.abort(), []);
  useEffect(() => { if (editor) editorPanel.current?.scrollIntoView({ block: "start", behavior: "smooth" }); }, [editor]);

  function refresh() { setResult(null); setRevision((value) => value + 1); }

  useEffect(() => {
    if (!adminMode) return;
    const controller = new AbortController();
    async function loadTotal() {
      try {
        const token = await getToken();
        if (!token || controller.signal.aborted) return;
        const total = await loadPropertyTotal(token, controller.signal);
        if (!controller.signal.aborted) { setTotalProperties(total); setTotalError(false); }
      } catch {
        if (!controller.signal.aborted) setTotalError(true);
      }
    }
    void loadTotal();
    return () => controller.abort();
  }, [adminMode, getToken, revision]);

  useEffect(() => {
    // Recheck balance after returning from WhatsApp or after an Admin assignment.
    function onFocus() { if (!mutation.current && !editor) { setResult(null); setRevision((value) => value + 1); } }
    window.addEventListener("focus", onFocus);
    return () => window.removeEventListener("focus", onFocus);
  }, [editor]);

  useEffect(() => {
    const controller = new AbortController();
    async function load() {
      try {
        const token = await getToken();
        if (controller.signal.aborted) return;
        if (!token) throw new PropertyApiError(401, "Your session has expired. Please sign in again.");
        const data = await loadPropertyPage(token, {
          search: filters.search, limit: filters.limit,
          beforeId: filters.cursors[filters.cursors.length - 1],
          builder: filters.builder,
          searchId: filters.searchId,
        }, controller.signal, adminMode);
        if (!controller.signal.aborted) {
          setResult({ data });
          setBalance(data.balance); setUnlimited(data.unlimited);
        }
      } catch (error) {
        if (!controller.signal.aborted && error instanceof PropertyApiError && error.status === 402) setBalance(0);
        if (!controller.signal.aborted) setResult({
          error: error instanceof TypeError ? "Unable to reach the property service. Please try again."
            : error instanceof Error ? error.message : "Properties could not be loaded.",
          status: error instanceof PropertyApiError ? error.status : 503,
        });
      }
    }
    void load();
    return () => controller.abort();
  }, [getToken, filters, revision, adminMode]);

  function openEditor(kind: "edit" | "delete", property: PropertyRecord | null) {
    if (mutation.current) return;
    setEditor({ kind, property }); setSaveError(null); setNotice(null);
  }
  async function changeProperty(details?: PropertyData) {
    if (!editor || mutation.current || !adminMode || !result || !("data" in result) || result.data.mode !== "admin") return;
    const controller = new AbortController();
    mutation.current = controller; setSaving(true); setSaveError(null);
    try {
      const token = await getToken();
      if (controller.signal.aborted) return;
      if (!token) throw new PropertyApiError(401, "Your session has expired. Please sign in again.");
      if (editor.kind === "delete" && editor.property) await deleteProperty(token, editor.property.id, controller.signal);
      else if (details) await saveProperty(token, details, editor.property?.id ?? null, controller.signal);
      else return;
      if (!controller.signal.aborted) {
        setNotice(editor.kind === "delete" ? "Property deleted." : editor.property ? "Property updated." : "Property created.");
        setEditor(null); setResult(null); setFilters({ ...filters, cursors: [null] });
        setRevision((value) => value + 1);
      }
    } catch (error) {
      if (!controller.signal.aborted) setSaveError(error instanceof TypeError
        ? "Unable to confirm the change. Refresh the records before trying again."
        : error instanceof Error ? error.message : "The change could not be saved.");
    } finally {
      if (!controller.signal.aborted) { mutation.current = null; setSaving(false); }
    }
  }

  function updateFilters(next: Filters) {
    setResult(null); setFilters(next);
  }
  function startSearch(next: Filters) {
    setShowCreditNotice(!adminMode && !unlimited && balance === 0);
    updateFilters({ ...next, cursors: [null], searchId: !unlimited && (balance ?? 0) > 0 && (next.search.trim() || next.builder) ? crypto.randomUUID() : null });
  }
  function submitSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const error = searchClueError(search);
    if (error) { setNotice(error); return; }
    setNotice(null);
    startSearch({ ...filters, search: search.trim(), builder: null });
  }
  const data = result && "data" in result ? result.data : null;
  const error = result && "error" in result ? result : null;
  const needsProfile = error && (error.status === 428 || error.status === 404 || error.error === "Complete your profile first");

  return <>
    {!adminMode && <div className="property-credit-heading"><h1 className="sr-only">Properties</h1>{balance !== null && !unlimited && <div className="property-credit-balance" role="status" aria-label={`Credit: ${balance.toLocaleString()}`}>
      <span className="property-credit-dot" aria-hidden="true" />
      <span>Credits</span>
      <span className="property-credit-divider" aria-hidden="true" />
      <strong>{balance.toLocaleString()}</strong>
    </div>}</div>}
    {data?.mode === "admin" && adminMode && <div className="admin-property-toolbar"><div><span className="eyebrow">PROPERTY MANAGEMENT</span><div className="admin-property-total">Total properties <strong>{totalError ? "Unavailable" : totalProperties === null ? "…" : totalProperties.toLocaleString("en-MY")}</strong></div><p>{totalError ? "Refresh to reload the total count. " : ""}Use the actions in each row to edit or delete a record.</p></div><button className="button button-primary" disabled={!!editor} onClick={() => openEditor("edit", null)}>+ Add property</button></div>}
    {notice && <p className="form-success" role="status">{notice}</p>}
    {adminMode && editor && data?.mode === "admin" && <div className="property-editor-anchor" ref={editorPanel}>{editor.kind === "edit" ? <PropertyForm key={editor.property?.id ?? "new"} property={editor.property} busy={saving} error={saveError} onSave={(details) => { void changeProperty(details); }} onCancel={() => { setEditor(null); setSaveError(null); }} /> : <section className="property-editor property-delete" aria-labelledby="delete-property-title"><span className="eyebrow">DELETE RECORD #{editor.property?.id}</span><h2 id="delete-property-title">Delete this property?</h2><p><strong>{editor.property?.owner_name}</strong></p><p>{editor.property?.address || "Address not provided"}</p><p>This permanently removes the record. It cannot be undone.</p>{saveError && <p className="form-error" role="alert">{saveError}</p>}<div className="billing-actions"><button className="button button-danger" disabled={saving} onClick={() => { void changeProperty(); }}>{saving ? "Deleting…" : "Confirm delete"}</button><button className="button button-secondary" disabled={saving} onClick={() => { setEditor(null); setSaveError(null); }}>Cancel</button></div></section>}</div>}
    <SearchBuilder key={builderReset} disabled={!!editor} ownerAllowed={unlimited || (balance ?? 0) > 0 || data?.mode === "credit"} onApply={(builder) => { setSearch(""); startSearch({ ...filters, search: "", builder }); }} />
    <form className="property-filters" onSubmit={submitSearch} aria-label="Property search">
      <div className="property-search-field"><label htmlFor="property-search">Search properties (at least 3 characters)</label><div><span aria-hidden="true">⌕</span><input disabled={!!editor} id="property-search" value={search} minLength={3} maxLength={200} required onChange={(event) => setSearch(event.target.value)} placeholder={data && data.mode !== "free" ? "Name, address, phone, IC or remark…" : "Property address…"} /></div></div>
      <div><label htmlFor="page-size">Per page</label><select disabled={!!editor} id="page-size" value={filters.limit} onChange={(event) => updateFilters({ ...filters, limit: Number(event.target.value), cursors: [null] })}>{[20, 50, 100].map((size) => <option key={size} value={size}>{size}</option>)}</select></div>
      <button disabled={!!editor || searchClueError(search) !== null} type="submit" className="button button-primary">Search <span aria-hidden="true">↗</span></button>
    </form>
    {search.trim() && searchClueError(search) && <p className="form-error" role="status">{searchClueError(search)}</p>}
    {showCreditNotice && !adminMode && !unlimited && balance === 0 && <p className="credit-search-notice" role="status">You have no search credits left. You can still search free previews. <Link href="/credits">Buy credits</Link> to unlock full details.</p>}
    {(filters.search || filters.builder || filters.limit !== 20) && <div className="active-property-filters"><span>{filters.builder ? `Builder: match ${filters.builder.match} of ${filters.builder.conditions.length} conditions` : filters.search ? `Search: “${filters.search}”` : "Filtered properties"}</span><button disabled={!!editor} onClick={() => { setSearch(""); setBuilderReset((value) => value + 1); updateFilters(initialFilters); }}>Clear filters</button></div>}
    {!result && <div className="property-state" role="status"><span className="property-loading-dot"/> Loading properties…</div>}
    {error && <section className="property-state"><h2>{needsProfile ? "One more step before exploring." : "We could not load your properties."}</h2><p role="alert">{error.error}</p>{needsProfile ? <Link className="button button-primary" href="/account">Complete your profile <span aria-hidden="true">↗</span></Link> : <><button className="button button-secondary" onClick={refresh}>Retry this search</button><Link className="button button-primary" href="/credits">Buy search credits</Link></>}</section>}
    {data && <>
      {data.mode === "credit" && !data.page.properties.length && <p className="form-success" role="status">No credits deducted for this search.</p>}
      <div className="property-results-heading"><p>{data.page.properties.length} {data.page.properties.length === 1 ? "property" : "properties"} on this page</p><span>Page {filters.cursors.length}</span></div>
      <PropertyResults key={`${filters.search}-${filters.cursors.length}-${revision}-${data.mode}`} properties={data.page.properties} mode={data.mode} editing={!!editor} onEdit={adminMode ? (property) => openEditor("edit", property) : undefined} onDelete={adminMode ? (property) => openEditor("delete", property) : undefined} />
      <nav className="property-pagination" aria-label="Property pages"><button className="button button-secondary" disabled={!!editor || filters.cursors.length === 1} onClick={() => updateFilters({ ...filters, cursors: filters.cursors.slice(0, -1) })}>← Previous</button><span>{data.page.pagination.has_more ? "More properties to explore" : "You’ve reached the end of these results"}</span><button className="button button-secondary" disabled={!!editor || !data.page.pagination.has_more} onClick={() => { if (data.page.pagination.next_before_id !== null) updateFilters({ ...filters, cursors: [...filters.cursors, data.page.pagination.next_before_id] }); }}>Next →</button></nav>
    </>}
  </>;
}
