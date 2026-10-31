"use client";

import { useState, type FormEvent } from "react";
import type { CreditAdjustment } from "@/lib/admin-users-api";

export default function CreditAdjustmentForm({ balance, busy, onAdjust }: {
  balance: number; busy: boolean; onAdjust: (change: CreditAdjustment) => Promise<boolean>;
}) {
  const [open, setOpen] = useState(false);
  const [direction, setDirection] = useState<"add" | "subtract">("subtract");
  const [amount, setAmount] = useState("");
  const [reason, setReason] = useState("");
  const [review, setReview] = useState<CreditAdjustment | null>(null);
  const [error, setError] = useState<string | null>(null);
  function prepare(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const count = Number(amount);
    const after = balance + (direction === "add" ? count : -count);
    if (!Number.isSafeInteger(count) || count < 1 || count > 1000000 || !reason.trim() || reason.trim().length > 200) {
      setError("Enter a positive whole amount and a reason (up to 200 characters)."); return;
    }
    if (after < 0 || after > 1000000000) { setError("Adjustment cannot make the balance negative or exceed the limit."); return; }
    setError(null);
    setReview({ request_id: crypto.randomUUID(), direction, amount: count, reason: reason.trim(), expected_balance: balance });
  }
  if (!open) return <button className="button button-secondary" disabled={busy} onClick={() => setOpen(true)}>Adjust credits</button>;
  if (review) {
    const changed = review.expected_balance !== balance;
    const after = review.expected_balance + (review.direction === "add" ? review.amount : -review.amount);
    return <section className="user-status-confirm" aria-label="Confirm credit adjustment">
      <h4>Confirm credit adjustment</h4>
      <p>{review.direction === "add" ? "Add" : "Subtract"} {review.amount.toLocaleString()} credits</p>
      <p><strong>{review.expected_balance.toLocaleString()} → {after.toLocaleString()} credits</strong></p>
      <p>Reason: {review.reason}</p>
      {changed && <p className="form-error" role="alert">The balance has changed. Go back and review the adjustment again.</p>}
      <div className="billing-actions">
        <button className={`button ${review.direction === "subtract" ? "button-danger" : "button-primary"}`} disabled={busy || changed} onClick={() => { void onAdjust(review); }}>{busy ? "Updating…" : "Confirm adjustment"}</button>
        <button className="button button-secondary" disabled={busy} onClick={() => setReview(null)}>Back</button>
      </div>
      <p>If confirmation fails, retry here before starting another adjustment.</p>
    </section>;
  }
  return <form className="credit-assignment" aria-label="Adjust search credits" onSubmit={prepare}>
    <h4>Correct a credit balance</h4>
    <div className="builder-row">
      <label>Action<select value={direction} disabled={busy} onChange={(e) => setDirection(e.target.value as "add" | "subtract")}><option value="subtract">Subtract credits</option><option value="add">Add credits</option></select></label>
      <label>Amount<input type="number" min={1} max={1000000} step={1} required value={amount} disabled={busy} onChange={(e) => setAmount(e.target.value)} /></label>
      <label className="builder-clue">Reason<input value={reason} maxLength={200} required disabled={busy} placeholder="e.g. Correct accidental extra credit" onChange={(e) => setReason(e.target.value)} /></label>
    </div>
    {error && <p className="form-error" role="alert">{error}</p>}
    <div className="billing-actions"><button className="button button-primary" disabled={busy}>Review adjustment</button><button type="button" className="button button-secondary" disabled={busy} onClick={() => setOpen(false)}>Cancel</button></div>
  </form>;
}
