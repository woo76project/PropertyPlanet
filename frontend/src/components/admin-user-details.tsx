"use client";
import { useRef, useState, type FormEvent } from "react";
import { canChangeUser, type AdminUser, type UserCredits, type CreditAdjustment } from "@/lib/admin-users-api";
import CreditAdjustmentForm from "@/components/credit-adjustment-form";
export type CreditsResult = { data: UserCredits } | { error: string };
function date(value: string) { return new Intl.DateTimeFormat("en-MY", { dateStyle: "medium", timeZone: "Asia/Kuala_Lumpur" }).format(new Date(value)); }

export default function AdminUserDetails({ user, credits, intent, busy, error, onPrepare, onCancel, onConfirm, onRetry, onAssign, onAdjust }: {
  user: AdminUser; credits: CreditsResult | null; intent: "Active" | "Suspended" | null;
  busy: boolean; error: string | null; onPrepare: () => void;
  onCancel: () => void; onConfirm: () => void; onRetry: () => void;
  onAssign: (amount: number, reference: string, requestId: string) => Promise<void>;
  onAdjust: (change: CreditAdjustment) => Promise<boolean>;
}) {
  const complete = [user.username, user.first_name, user.last_name, user.phone].every((value) => typeof value === "string" && value.trim());
  return <section className="admin-user-details" aria-labelledby="user-details-title">
    <span className="eyebrow">USER #{user.id}</span><h2 id="user-details-title">{user.username || "Profile not completed"}</h2>
    <dl className="user-profile-grid"><div><dt>Name</dt><dd>{[user.first_name, user.last_name].filter(Boolean).join(" ") || "Not completed"}</dd></div><div><dt>Email</dt><dd>{user.status === "Deleted" ? "Removed" : user.email}</dd></div><div><dt>Phone</dt><dd>{user.phone || "Not provided"}</dd></div><div><dt>Profile</dt><dd>{complete ? "Complete" : "Incomplete"}</dd></div><div><dt>Role / status</dt><dd>{user.role} / {user.status}</dd></div><div><dt>Joined</dt><dd>{date(user.created_at)} (Malaysia time)</dd></div></dl>
    <div className="user-membership-details"><h3>Search credits</h3>
      {!credits && <p role="status">Loading credit balance…</p>}
      {credits && "error" in credits && <><p className="form-error" role="alert">{credits.error}</p><button className="button button-secondary" disabled={busy} onClick={onRetry}>Retry credit check</button></>}
      {credits && "data" in credits && <p><strong>{credits.data.unlimited ? "Unlimited Admin searches" : `${credits.data.balance.toLocaleString()} credits available`}</strong></p>}
      {user.role === "User" && user.status === "Active" && !intent && <AssignCreditsForm key={user.id} busy={busy} onAssign={onAssign} />}
      {user.role === "User" && user.status === "Active" && !intent && credits && "data" in credits && !credits.data.unlimited && <CreditAdjustmentForm key={`adjust-${user.id}`} balance={credits.data.balance} busy={busy} onAdjust={onAdjust} />}
      {user.role === "User" && user.status !== "Active" && <p>Only Active users can receive credits.</p>}
    </div>
    {error && <p className="form-error" role="alert">{error}</p>}
    {intent && <div className="user-status-confirm"><h3>{intent === "Suspended" ? "Suspend this user?" : "Reactivate this user?"}</h3><p>{intent === "Suspended" ? "Suspension blocks searches and credit use. The remaining balance is retained." : "Reactivation restores access to the existing credit balance. No credits are added."}</p><div className="billing-actions"><button className="button button-primary" disabled={busy} onClick={onConfirm}>{busy ? "Updating…" : "Confirm"}</button><button className="button button-secondary" disabled={busy} onClick={onCancel}>Cancel</button></div></div>}
    {!intent && canChangeUser(user) && <button className={`button ${user.status === "Active" ? "button-danger" : "button-secondary"}`} disabled={busy} onClick={onPrepare}>{user.status === "Active" ? "Suspend user" : "Reactivate user"}</button>}
    {!canChangeUser(user) && <p>{user.role === "Admin" ? "Admin accounts are protected." : "Deleted accounts cannot be reactivated."}</p>}
  </section>;
}

function AssignCreditsForm({ busy, onAssign }: { busy: boolean; onAssign: (amount: number, reference: string, requestId: string) => Promise<void> }) {
  const [amount, setAmount] = useState("");
  const [reference, setReference] = useState("");
  const [error, setError] = useState<string | null>(null);
  const requestId = useRef<string | null>(null);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    if (!Number.isSafeInteger(Number(amount)) || Number(amount) < 1 || Number(amount) > 1000000 || !reference.trim()) { setError("Enter a positive whole credit amount and a payment reference."); return; }
    requestId.current ??= crypto.randomUUID();
    setError(null);
    await onAssign(Number(amount), reference.trim(), requestId.current);
  }
  return <form className="credit-assignment" onSubmit={(event) => { void submit(event); }} aria-label="Assign search credits"><h4>Assign credits after confirming payment</h4><p>Check this user’s username against the WhatsApp purchase request.</p><div className="builder-row"><label>Credits to add<input type="number" min={1} max={1000000} step={1} required value={amount} disabled={busy} onChange={(event) => setAmount(event.target.value)} /></label><label className="builder-clue">Payment reference / note<input required maxLength={200} value={reference} disabled={busy} onChange={(event) => setReference(event.target.value)} placeholder="e.g. transfer reference" /></label><button className="button button-primary" disabled={busy} type="submit">{busy ? "Assigning…" : "Assign credits"}</button></div>{error && <p role="alert" className="form-error">{error}</p>}<p>Credits are added to the current balance. Retry the same assignment if confirmation fails.</p></form>;
}
