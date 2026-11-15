import type { Metadata } from "next";
import Link from "next/link";

export const metadata: Metadata = {
  title: "Privacy Policy | Property Planet",
  description: "How Property Planet collects, uses, stores and shares account and property information.",
  alternates: { canonical: "https://propertyplanet.cc/privacy" },
};

export default function PrivacyPage() {
  return <main className="privacy-page section-shell">
    <article className="privacy-document" aria-labelledby="privacy-title">
      <Link className="privacy-back" href="/">← Back to Property Planet</Link>
      <header className="privacy-heading">
        <span className="eyebrow">YOUR INFORMATION</span>
        <h1 id="privacy-title">Privacy policy.</h1>
        <p>Last updated: <time dateTime="2026-11-16">16 November 2026</time></p>
      </header>
      <p>Property Planet (also displayed as Property Planets) is a property-record search service at <a href="https://propertyplanet.cc">propertyplanet.cc</a>. This policy explains how we handle information about visitors, account holders and people named in property records. For privacy questions, contact <a href="mailto:woo76.project@gmail.com">woo76.project@gmail.com</a>.</p>

      <section aria-labelledby="privacy-collect">
        <h2 id="privacy-collect">1. Information we collect</h2>
        <ul>
          <li><strong>Account information:</strong> email address, username, name, phone number, account identifier, role and account status. Clerk manages authentication credentials and sessions.</li>
          <li><strong>Google sign-in information:</strong> when you choose Google login, Google shares the basic identity information you authorize with Clerk, including your account identifier, email, name and, where available, profile picture. Our application stores the identity and profile fields needed for your account.</li>
          <li><strong>Property records:</strong> administrators enter or import owner names, addresses, phone numbers, identification numbers and remarks. These records can relate to people who do not have a Property Planet account.</li>
          <li><strong>Service activity:</strong> credit balances, credit assignments and adjustments, search transaction references and fingerprints, import progress, administrative audit records and support communications.</li>
          <li><strong>Technical information:</strong> our hosting and authentication providers may process IP addresses, browser and device information, request timestamps, cookies and diagnostic or security logs when providing the service.</li>
        </ul>
      </section>

      <section aria-labelledby="privacy-use">
        <h2 id="privacy-use">2. How we use information</h2>
        <p>We use information to create and secure accounts, authenticate users, provide property searches, control access to records, manage search credits, process imports, investigate errors or misuse and respond to support and privacy requests.</p>
        <p>Google account information is used for authentication and account management. Our Google login requests basic identity, email and profile access; it does not request access to your Gmail messages, Google Drive files or contacts. We do not sell Google account information or use it for advertising.</p>
      </section>

      <section aria-labelledby="privacy-sharing">
        <h2 id="privacy-sharing">3. Who can access information</h2>
        <p>Platform administrators can access account and operational information to manage the service. Signed-in users with completed profiles can view owner names and property addresses in free previews. Phone numbers, identification numbers and remarks are available through authorized full-record searches using search credits, or to administrators.</p>
        <p>Property-record access is a core function of this service. Google login information is handled separately from the property records administrators upload.</p>
        <p>We use Clerk for authentication, Google when you select Google login, Cloudflare for website hosting and DNS, Render for the backend API and Neon PostgreSQL for application data. These providers process information needed to deliver their services under their own terms and privacy policies. Information may be processed outside your country.</p>
        <p>If you contact us or arrange search credits through WhatsApp, your messages and contact details are also processed by WhatsApp. We may disclose information when required by applicable law or when necessary to investigate abuse and protect the service or other people.</p>
      </section>

      <section aria-labelledby="privacy-storage">
        <h2 id="privacy-storage">4. Storage and security</h2>
        <p>Application profiles, property records, credit records and operational history are stored in our database. Authentication data is managed by Clerk. Import files are temporarily stored by the backend while an administrator processes them and are removed after a successful import; failed or interrupted imports may retain files pending cleanup.</p>
        <p>We use HTTPS and application access controls to protect information. No online service or storage system can guarantee absolute security.</p>
      </section>

      <section aria-labelledby="privacy-cookies">
        <h2 id="privacy-cookies">5. Cookies and sessions</h2>
        <p>Clerk and the website use cookies and similar browser storage for sign-in, session continuity and security. Blocking these may prevent login or other account features from working. Google and other providers may also use cookies when you interact with their services.</p>
      </section>

      <section aria-labelledby="privacy-retention">
        <h2 id="privacy-retention">6. Retention and account deletion</h2>
        <p>We retain information while needed to operate the service, maintain transaction and audit history, resolve disputes and meet applicable obligations. Retention varies by the type and purpose of the information.</p>
        <p>Ordinary account holders can request account deletion through <Link href="/account">My account</Link>. Deletion removes the Clerk identity, clears application profile fields and retires the credit balance. A minimal history-linked account record and associated transaction or audit history may remain. Deleting your account does not automatically remove separately uploaded property records about you. Administrators must contact the operator for account deletion.</p>
      </section>

      <section aria-labelledby="privacy-requests">
        <h2 id="privacy-requests">7. Your choices and privacy requests</h2>
        <p>You can review or update your profile in My account and remove this application&apos;s Google access through your <a href="https://myaccount.google.com/connections" target="_blank" rel="noopener noreferrer">Google Account connections</a>. Removing Google access does not by itself delete your Property Planet account.</p>
        <p>If you want to request access, correction or deletion of information, or if a property record contains your details and you object to its inclusion, email <a href="mailto:woo76.project@gmail.com">woo76.project@gmail.com</a>. Identify the relevant account or property record. We may ask for information to verify your identity before acting. Please do not send passwords, login codes or full identification documents in your initial message.</p>
      </section>

      <section aria-labelledby="privacy-changes">
        <h2 id="privacy-changes">8. Changes and contact</h2>
        <p>We will update this page and its last-updated date when our information practices change. For questions about this policy or how Property Planet handles information, email <a href="mailto:woo76.project@gmail.com">woo76.project@gmail.com</a>.</p>
      </section>
    </article>
  </main>;
}
