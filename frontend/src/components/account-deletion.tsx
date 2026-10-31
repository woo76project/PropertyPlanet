"use client";
import { useAuth, useClerk } from "@clerk/nextjs";
import { useEffect, useRef, useState } from "react";
import AccountDeletionForm from "@/components/account-deletion-form";
import { deleteOwnAccount, deletionError, loadDeletionState, type DeletionState } from "@/lib/account-deletion-api";
export default function AccountDeletion() {
  const { getToken } = useAuth();
  const { signOut } = useClerk();
  const [result, setResult] = useState<{ data: DeletionState } | { error: string } | null>(null);
  const [revision, setRevision] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const request = useRef<AbortController | null>(null);
  useEffect(() => () => request.current?.abort(), []);
  useEffect(() => {
    const controller = new AbortController();
    async function load() {
      try {
        const token = await getToken();
        if (controller.signal.aborted) return;
        if (!token) throw new Error("Your session has expired. Please sign in again.");
        const data = await loadDeletionState(token, controller.signal);
        if (!controller.signal.aborted) setResult({ data });
      } catch (error) { if (!controller.signal.aborted) setResult({ error: deletionError(error) }); }
    }
    void load(); return () => controller.abort();
  }, [getToken, revision]);
  async function remove(confirmation: string) {
    if (!result || !("data" in result) || !result.data.can_delete || request.current) return;
    const controller = new AbortController(); request.current = controller;
    setBusy(true); setError(null);
    try {
      const token = await getToken();
      if (controller.signal.aborted) return;
      if (!token) throw new Error("Your session has expired. Please sign in again.");
      await deleteOwnAccount(token, confirmation, controller.signal);
      if (controller.signal.aborted) return;
      // Clerk may already have invalidated the session. In either case, replace
      // the document so the old account's client state is discarded.
      try { await signOut({ redirectUrl: "/account/deleted" }); }
      catch { /* The deleted Clerk account may already have ended its session. */ }
      window.location.replace("/account/deleted");
    } catch (error) {
      if (!controller.signal.aborted) setError(deletionError(error));
    } finally { if (!controller.signal.aborted) { request.current = null; setBusy(false); } }
  }
  if (!result) return <p className="account-deletion-loading" role="status">Loading account settings…</p>;
  if ("error" in result) return <section className="account-deletion"><h2>Account settings</h2><p className="form-error" role="alert">{result.error}</p><button className="button button-secondary" onClick={() => { setResult(null); setRevision((v) => v + 1); }}>Retry account settings</button></section>;
  return <AccountDeletionForm state={result.data} busy={busy} error={error} onDelete={(confirmation) => { void remove(confirmation); }} />;
}
