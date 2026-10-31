import Link from "next/link";
export default function DeletedAccountPage() {
  return <main className="property-page section-shell"><section className="property-state"><span className="eyebrow">ACCOUNT DELETION</span><h1>Your account has been deleted.</h1><p>Your sign-in account and personal profile details have been removed. Your unused search credits have been removed. A minimal record remains for credit and audit history.</p><p>If you return, you’ll need to create a new account.</p><Link className="button button-primary" href="/">Return to home</Link></section></main>;
}
