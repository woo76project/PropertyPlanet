"use client";
import { useState, type FormEvent } from "react";
import type { DeletionState } from "@/lib/account-deletion-api";
export default function AccountDeletionForm({ state, busy, error, onDelete }: {
  state: DeletionState; busy: boolean; error: string | null; onDelete: (confirmation: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [confirmation, setConfirmation] = useState("");
  if (!state.can_delete) return <section className="account-deletion protected-account"><span className="eyebrow">ACCOUNT PROTECTION</span><h2>Admin account protected.</h2><p>Admin accounts cannot be deleted through self-service account settings.</p></section>;
  function submit(event: FormEvent<HTMLFormElement>) { event.preventDefault(); if (!busy && confirmation === "DELETE") onDelete(confirmation); }
  return <section className="account-deletion" aria-labelledby="account-deletion-title"><span className="eyebrow">ACCOUNT DELETION</span><h2 id="account-deletion-title">Delete your account.</h2><p>This permanently removes your sign-in account and personal profile details. Unused search credits will be removed. Contact support about any outstanding payment questions.</p><p>We retain a minimal record to link credit and audit history. You’ll need to sign up again if you return.</p>
    {state.status === "Deleting" && <p className="form-error">Deletion is already in progress and your account is blocked. You can retry to finish it. If you lose access before it finishes, contact support.</p>}
    {!open ? <button type="button" className="button button-danger" onClick={() => setOpen(true)}>{state.status === "Deleting" ? "Retry account deletion" : "Delete account"}</button> : <form className="delete-account-confirmation" onSubmit={submit}><h3>This cannot be undone.</h3><label htmlFor="delete-account-confirmation">Type <strong>DELETE</strong> to confirm.</label><input id="delete-account-confirmation" value={confirmation} onChange={(event) => setConfirmation(event.target.value)} disabled={busy} autoComplete="off" autoCapitalize="off" spellCheck={false} maxLength={6} required aria-describedby="delete-account-help" /><p id="delete-account-help">Only this signed-in account will be deleted.</p>{error && <p className="form-error" role="alert">{error}</p>}<div className="billing-actions"><button className="button button-danger" disabled={busy || confirmation !== "DELETE"} type="submit">{busy ? "Deleting account…" : "Delete my account permanently"}</button><button className="button button-secondary" type="button" disabled={busy} onClick={() => { setOpen(false); setConfirmation(""); }}>Cancel</button></div></form>}
  </section>;
}
