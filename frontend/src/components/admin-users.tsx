"use client";
import { SignInButton, useAuth } from "@clerk/nextjs";
import Link from "next/link";
import { useEffect, useRef, useState, type FormEvent } from "react";
import AdminUserTable from "@/components/admin-user-table";
import AdminUserDetails, { type CreditsResult } from "@/components/admin-user-details";
import { AdminUsersError, adminUsersError, canChangeUser, changeUserStatus, assignUserCredits, adjustUserCredits, type CreditAdjustment, loadAdminUsers, loadUserCredits, type AdminUser, type UsersPage, type UsersQuery } from "@/lib/admin-users-api";
type Filters = Omit<UsersQuery, "beforeId"> & { cursors: (number | null)[] };
const defaults: Filters = { search: "", status: "", limit: 20, cursors: [null] };
export default function AdminUsers() {
  const { isLoaded, isSignedIn, sessionId } = useAuth();
  if (!isLoaded) return <p role="status">Loading your session…</p>;
  if (!isSignedIn) return <section className="property-state"><h2>Sign in as Admin.</h2><p>Only Admin can view and manage user accounts.</p><SignInButton mode="modal" fallbackRedirectUrl="/admin/users"><button className="button button-primary">Sign in to continue</button></SignInButton></section>;
  return <SignedInUsers key={sessionId} />;
}
function SignedInUsers() {
  const { getToken } = useAuth();
  const [filters, setFilters] = useState<Filters>(defaults);
  const [search, setSearch] = useState("");
  const [result, setResult] = useState<{ data: UsersPage } | { error: string; status: number } | null>(null);
  const [selected, setSelected] = useState<AdminUser | null>(null);
  const [credits, setCredits] = useState<CreditsResult | null>(null);
  const [intent, setIntent] = useState<"Active" | "Suspended" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [revision, setRevision] = useState(0);
  const [creditsRevision, setCreditsRevision] = useState(0);
  const action = useRef<AbortController | null>(null);
  useEffect(() => () => action.current?.abort(), []);
  useEffect(() => {
    function onFocus() { if (!selected && !action.current) { setResult(null); setRevision((v) => v + 1); } }
    window.addEventListener("focus", onFocus); return () => window.removeEventListener("focus", onFocus);
  }, [selected]);
  useEffect(() => {
    const controller = new AbortController();
    async function load() {
      try {
        const token = await getToken();
        if (controller.signal.aborted) return;
        if (!token) throw new AdminUsersError(401, "Your session has expired. Please sign in again.");
        const data = await loadAdminUsers(token, { search: filters.search, status: filters.status, limit: filters.limit, beforeId: filters.cursors.at(-1) ?? null }, controller.signal);
        if (!controller.signal.aborted) setResult({ data });
      } catch (error) { if (!controller.signal.aborted) setResult({ error: adminUsersError(error), status: error instanceof AdminUsersError ? error.status : 503 }); }
    }
    void load(); return () => controller.abort();
  }, [getToken, filters, revision]);
  const selectedId = selected?.id;
  useEffect(() => {
    if (!selectedId) return;
    const controller = new AbortController();
    async function load() {
      try {
        const token = await getToken();
        if (controller.signal.aborted) return;
        if (!token) throw new AdminUsersError(401, "Your session has expired. Please sign in again.");
        const data = await loadUserCredits(token, selectedId!, controller.signal);
        if (!controller.signal.aborted) setCredits({ data });
      } catch (error) { if (!controller.signal.aborted) setCredits({ error: adminUsersError(error) }); }
    }
    void load(); return () => controller.abort();
  }, [getToken, selectedId, creditsRevision]);
  function clearSelection() { setSelected(null); setCredits(null); setIntent(null); setError(null); }
  function updateFilters(next: Filters) { if (action.current) return; clearSelection(); setResult(null); setFilters(next); }
  function choose(user: AdminUser, change = false, forceOpen = false) {
    if (action.current) return;
    if (!change && !forceOpen && selected?.id === user.id) { clearSelection(); return; }
    setCredits(null); setSelected(user); setError(null); setNotice(null); setCreditsRevision((v) => v + 1);
    setIntent(change && canChangeUser(user) ? user.status === "Active" ? "Suspended" : "Active" : null);
  }
  function refresh() { if (action.current) return; clearSelection(); setResult(null); setRevision((v) => v + 1); }
  async function confirm() {
    if (!selected || !intent || !canChangeUser(selected) || action.current) return;
    const controller = new AbortController(); action.current = controller; setBusy(true); setError(null);
    try {
      const token = await getToken();
      if (controller.signal.aborted) return;
      if (!token) throw new AdminUsersError(401, "Your session has expired. Please sign in again.");
      await changeUserStatus(token, selected.id, intent, controller.signal);
      if (!controller.signal.aborted) {
        setNotice(intent === "Suspended" ? "User suspended. Remaining credits are retained." : "User reactivated. Existing credit balance is available.");
        clearSelection(); setResult(null); setFilters({ ...filters, cursors: [null] });
      }
    } catch (error) { if (!controller.signal.aborted) setError(adminUsersError(error)); }
    finally { if (!controller.signal.aborted) { action.current = null; setBusy(false); } }
  }
  async function assign(amount: number, reference: string, requestId: string) {
    if (!selected || action.current) return;
    const controller = new AbortController(); action.current = controller; setBusy(true); setError(null);
    try {
      const token = await getToken();
      if (controller.signal.aborted) return;
      if (!token) throw new AdminUsersError(401, "Sign in again to assign credits.");
      const assigned = await assignUserCredits(token, selected.id, amount, reference, requestId, controller.signal);
      if (!controller.signal.aborted) {
        setNotice(`${amount} credits assigned to user #${selected.id}. Balance: ${assigned.balance}.`);
        clearSelection(); setResult(null); setRevision((value) => value + 1);
      }
    } catch (error) { if (!controller.signal.aborted) setError(adminUsersError(error)); }
    finally { if (!controller.signal.aborted) { action.current = null; setBusy(false); } }
  }
  async function adjust(change: CreditAdjustment): Promise<boolean> {
    if (!selected || action.current) return false;
    const controller = new AbortController(); action.current = controller; setBusy(true); setError(null);
    try {
      const token = await getToken();
      if (controller.signal.aborted) return false;
      if (!token) throw new AdminUsersError(401, "Sign in again to adjust credits.");
      const updated = await adjustUserCredits(token, selected.id, change, controller.signal);
      if (controller.signal.aborted) return false;
      setNotice(`Credits adjusted for user #${selected.id}. Balance: ${updated.balance}.`);
      clearSelection(); setResult(null); setRevision((value) => value + 1);
      return true;
    } catch (error) {
      if (!controller.signal.aborted) {
        setError(adminUsersError(error));
        if (error instanceof AdminUsersError && error.status === 409) setCreditsRevision((value) => value + 1);
      }
      return false;
    } finally { if (!controller.signal.aborted) { action.current = null; setBusy(false); } }
  }
  function submit(event: FormEvent<HTMLFormElement>) { event.preventDefault(); updateFilters({ ...filters, search: search.trim(), cursors: [null] }); }
  const data = result && "data" in result ? result.data : null;
  const failure = result && "error" in result ? result : null;
  const locked = busy || !!intent;
  return <>
    <div className="browser-toolbar"><span className="browser-toolbar-caption">Review accounts and manage access.</span><button className="browser-refresh" disabled={locked} onClick={refresh}>↻ Refresh users</button></div>
    <form className="property-filters" onSubmit={submit} aria-label="User search"><div className="property-search-field"><label htmlFor="user-search">Search users</label><div><span aria-hidden="true">⌕</span><input id="user-search" maxLength={200} value={search} disabled={locked} onChange={(event) => setSearch(event.target.value)} placeholder="Username, name, email or phone…" /></div></div><div><label htmlFor="user-status">Account status</label><select id="user-status" disabled={locked} value={filters.status} onChange={(event) => updateFilters({ ...filters, status: event.target.value as Filters["status"], cursors: [null] })}><option value="">All statuses</option><option value="Active">Active</option><option value="Suspended">Suspended</option></select></div><div><label htmlFor="user-page-size">Per page</label><select id="user-page-size" disabled={locked} value={filters.limit} onChange={(event) => updateFilters({ ...filters, limit: Number(event.target.value), cursors: [null] })}>{[20, 50, 100].map((size) => <option key={size}>{size}</option>)}</select></div><button className="button button-primary" disabled={locked}>Search ↗</button></form>
    {(filters.search || filters.status || filters.limit !== 20) && <div className="active-property-filters"><span>{filters.search ? `Search: “${filters.search}”` : "Filtered users"}</span><button disabled={locked} onClick={() => { setSearch(""); updateFilters(defaults); }}>Clear filters</button></div>}
    {notice && <p className="form-success user-notice" role="status">{notice}</p>}
    {!result && <p className="property-state" role="status">Loading users…</p>}
    {failure && <section className="property-state"><h2>{failure.status === 403 ? "Admin access required." : "We could not load users."}</h2><p role="alert">{failure.error}</p>{failure.status === 404 || failure.error === "Complete your profile first" ? <Link className="button button-primary" href="/account">Complete your profile</Link> : <button className="button button-secondary" onClick={refresh}>Try again</button>}</section>}
    {data && <>
      <div className="property-results-heading"><p>{data.users.length} users on this page</p><span>Page {filters.cursors.length}</span></div><AdminUserTable users={data.users} locked={locked} expandedId={selected?.id ?? null} onSelect={(user) => choose(user)} onChange={(user) => choose(user, true)} onCredits={(user) => choose(user, false, true)} details={selected && <AdminUserDetails user={selected} credits={credits} intent={intent} busy={busy} error={error} onPrepare={() => { setIntent(selected.status === "Active" ? "Suspended" : "Active"); setError(null); }} onCancel={() => { setIntent(null); setError(null); }} onConfirm={() => { void confirm(); }} onRetry={() => { setCredits(null); setCreditsRevision((v) => v + 1); }} onAssign={assign} onAdjust={adjust} />} />
      <nav className="property-pagination" aria-label="User pages"><button className="button button-secondary" disabled={locked || filters.cursors.length === 1} onClick={() => updateFilters({ ...filters, cursors: filters.cursors.slice(0, -1) })}>← Previous</button><span>{data.pagination.has_more ? "More accounts to view" : "End of these results"}</span><button className="button button-secondary" disabled={locked || !data.pagination.has_more} onClick={() => { if (data.pagination.next_before_id) updateFilters({ ...filters, cursors: [...filters.cursors, data.pagination.next_before_id] }); }}>Next →</button></nav>
    </>}
  </>;
}
