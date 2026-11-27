import type { Metadata } from "next";
import Link from "next/link";

export const metadata: Metadata = {
  title: "Privacy Policy | Property Planet",
  description: "Privacy information for the Property Planet private project.",
  alternates: { canonical: "https://propertyplanet.cc/privacy" },
};

export default function PrivacyPage() {
  return <main className="privacy-page section-shell">
    <article className="privacy-document" aria-labelledby="privacy-title">
      <Link className="privacy-back" href="/">← Back to Property Planet</Link>
      <header className="privacy-heading">
        <span className="eyebrow">YOUR INFORMATION</span>
        <h1 id="privacy-title">Privacy policy.</h1>
        <p>Last updated: <time dateTime="2026-11-27">27 November 2026</time></p>
      </header>
      <p>Property Planet is a private project intended for authorized users.</p>

      <section aria-labelledby="privacy-information">
        <h2 id="privacy-information">1. Information handled</h2>
        <p>We handle account and profile information, sign-in details, records provided by administrators, and basic technical or activity information needed to operate the website. Uploaded records may contain personal information about people who do not have an account.</p>
        <p>If you choose Google sign-in, basic account information shared through the sign-in provider is used to authenticate you and manage your account. Cookies and similar browser storage support sign-in, sessions and security.</p>
      </section>

      <section aria-labelledby="privacy-use">
        <h2 id="privacy-use">2. Use and access</h2>
        <p>Information is used to provide website features, manage accounts and access, maintain records, resolve technical issues and respond to requests. Administrators and authorized users may access information according to their permissions.</p>
        <p>Authentication, hosting and storage providers process information needed to deliver their services. Information may be processed outside your country. We may also disclose information where required by law or needed to address misuse and protect the website or others.</p>
      </section>

      <section aria-labelledby="privacy-storage">
        <h2 id="privacy-storage">3. Storage and protection</h2>
        <p>We use access controls and security measures to protect information, although no online service can guarantee absolute security. Information is retained while needed for the project, account administration, operational history or applicable obligations. Temporary files are subject to cleanup.</p>
      </section>

      <section aria-labelledby="privacy-choices">
        <h2 id="privacy-choices">4. Your choices</h2>
        <p>You can review your profile and available account controls in <Link href="/account">My account</Link>. Contact us to request access, correction or deletion of information about you, including information in uploaded records. We may need to verify your identity before responding.</p>
        <p>Account deletion may retain limited operational or audit history and does not automatically delete separately uploaded records. You can also manage Google access through your <a href="https://myaccount.google.com/connections" target="_blank" rel="noopener noreferrer">Google Account connections</a>; removing that access does not itself delete your website account.</p>
      </section>

    </article>
  </main>;
}
