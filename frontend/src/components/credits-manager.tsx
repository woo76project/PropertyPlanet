"use client";
import { SignInButton, useAuth } from "@clerk/nextjs";
import Link from "next/link";
import { useEffect, useState } from "react";
import { CreditsError, loadCredits, type CreditAccount } from "@/lib/credits-api";

export default function CreditsManager() {
  const { isLoaded, isSignedIn, sessionId } = useAuth();
  if (!isLoaded) return <p role="status">Loading your session…</p>;
  if (!isSignedIn) return <section className="property-state"><h2>Get search credits.</h2><p>Sign in and complete your profile to request credits.</p><SignInButton mode="modal" fallbackRedirectUrl="/credits"><button className="button button-primary">Sign in to continue</button></SignInButton></section>;
  return <SignedInCredits key={sessionId} />;
}

function SignedInCredits() {
  const { getToken } = useAuth();
  const [result, setResult] = useState<{ data: CreditAccount } | { error: string; status: number } | null>(null);
  const [revision, setRevision] = useState(0);
  function refresh() { setResult(null); setRevision((value) => value + 1); }
  useEffect(() => {
    const onFocus = () => { setResult(null); setRevision((value) => value + 1); };
    window.addEventListener("focus", onFocus); return () => window.removeEventListener("focus", onFocus);
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    async function load() {
      try {
        const token = await getToken();
        if (!token) throw new CreditsError(401, "Sign in again to view your credits.");
        if (controller.signal.aborted) return;
        const data = await loadCredits(token, controller.signal);
        if (!controller.signal.aborted) setResult({ data });
      } catch (error) {
        if (!controller.signal.aborted) setResult({ error: error instanceof Error ? error.message : "Credits could not be loaded.", status: error instanceof CreditsError ? error.status : 503 });
      }
    }
    void load(); return () => controller.abort();
  }, [getToken, revision]);
  const account = result && "data" in result ? result.data : null;
  return <>
    <div className="browser-toolbar"><span className="browser-toolbar-caption">Request credits, then return here after Admin confirms your payment.</span><button className="browser-refresh" disabled={!result} onClick={refresh}>↻ Refresh credits</button></div>
    {!result && <p role="status">Loading credits…</p>}
    {result && "error" in result && <section className="property-state"><p role="alert">{result.error}</p>{[403, 404, 428].includes(result.status) && <Link href="/account" className="button button-secondary">Open your account</Link>}<button className="button button-secondary" onClick={refresh}>Try again</button></section>}
    {account && <div className="billing-grid"><section className="billing-card"><span className="eyebrow">SEARCH CREDITS</span><h2>{account.unlimited ? "Unlimited Admin searches" : `${account.balance.toLocaleString()} credits available`}</h2><p>Free accounts start with zero credits. One credit unlocks a full-record search with matching records, including its result pages while you stay on the page. Zero results or a failed request means no credit is deducted. Refreshing, closing the tab or signing out clears the search results.</p>
      {!account.unlimited && <><dl className="billing-summary"><div><dt>Price per credit</dt><dd>RM{Number(account.price_myr).toFixed(2)}</dd></div><div><dt>Activation</dt><dd>Admin assigns credits after confirming payment</dd></div></dl>
      <p>Tap Subscribe to open WhatsApp with your username and a purchase message. Confirm the number of credits and payment details with the administrator.</p>
      {account.purchase_url ? <a className="button button-primary" href={account.purchase_url} target="_blank" rel="noopener noreferrer">Subscribe via WhatsApp ↗</a> : <><button className="button button-primary" disabled>Subscribe via WhatsApp</button><p className="billing-notice">Credit purchases will be available once the administrator adds the WhatsApp contact.</p></>}
      <p className="billing-fine-print">Sending a message or making a payment does not automatically add credits. Admin confirms and assigns them to your account.</p></>}
      <Link className="button button-secondary" href="/properties">Browse properties →</Link>
    </section><aside className="billing-benefits"><span className="eyebrow">HOW IT WORKS</span><h2>Credits for your next search.</h2><ol><li>Subscribe through WhatsApp.</li><li>Agree on credits and complete payment.</li><li>Admin adds credits to your account.</li><li>Search to unlock full property and owner details.</li></ol><p>Public property previews remain free. Starting a different search uses a new credit. Paging or changing page size within the current search is included.</p></aside></div>}
  </>;
}
