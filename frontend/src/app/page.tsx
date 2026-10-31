import Link from "next/link";
import { JoinButton } from "@/components/auth-actions";
import { Brand } from "@/components/site-header";
import ProductPreview from "@/components/product-preview";
import { auth } from "@clerk/nextjs/server";
import { redirect } from "next/navigation";

const features = [
  { icon: "⌕", name: "Find your focus.", text: "Search by property or location. Filter for sale or rent and find the opportunities that fit your work.", label: "LESS NOISE, MORE DIRECTION" },
  { icon: "↗", name: "Get to the connection.", text: "With search credits, unlock owner names and contact details without jumping between spreadsheets.", label: "DETAILS THAT MOVE YOU FORWARD" },
  { icon: "◈", name: "One organised workspace.", text: "Explore property records in one place, with clear previews and a simple path to full access.", label: "BUILT FOR PROPERTY AGENTS" },
];

export default async function Home() {
  const { userId } = await auth();
  if (userId) redirect("/properties");

  return <>
    <main>
      <section className="hero section-shell">
        <div className="hero-copy">
          <div className="hero-eyebrow"><span className="status-dot"/> YOUR NEXT OPPORTUNITY IS OUT THERE</div>
          <h1>A better place<br/>to find your<br/><span>next connection.</span></h1>
          <p className="hero-description">Property records. Owner connections. A clearer way forward. Your property search starts with Property Planets.</p>
          <div className="hero-actions"><JoinButton>Explore your possibilities <span aria-hidden="true">↗</span></JoinButton><a className="button button-quiet" href="#preview">Take a look <span aria-hidden="true">↓</span></a></div>
          <div className="hero-footnote"><span aria-hidden="true">✓</span> Free account to start <span className="footnote-divider"/> Upgrade when you’re ready</div>
        </div>
        <ProductPreview />
      </section>

      <div className="benefit-strip section-shell"><span>YOUR WORK, WITH A LITTLE MORE CLARITY.</span><div><span>◈</span> Sale & rental records</div><div><span>⌕</span> Focused property search</div><div><span>↗</span> Owner connections</div></div>

      <section className="section-shell features-section" id="features">
        <div className="section-heading"><div><span className="eyebrow">LESS FRICTION. MORE OPPORTUNITY.</span><h2>Get closer to<br/>your next conversation.</h2></div><p>A focused workspace for agents who want to spend less time looking for information and more time taking the next step.</p></div>
        <div className="feature-grid">{features.map((feature) => <article className="feature-card" key={feature.name}><span className="feature-icon" aria-hidden="true">{feature.icon}</span><span className="tiny-label">{feature.label}</span><h3>{feature.name}</h3><p>{feature.text}</p></article>)}</div>
      </section>

      <section className="how-section section-shell" id="how-it-works"><div className="section-heading"><div><span className="eyebrow">FROM SEARCH TO POSSIBILITY</span><h2>A simple way to get moving.</h2></div></div><div className="steps-grid">{[
        ["01", "Make yourself at home", "Create your free account and complete your agent profile."],
        ["02", "Explore the possibilities", "Browse property previews and narrow your search by address."],
        ["03", "Unlock the connection", "Use search credits to access owner details and take the next step."],
      ].map(([number, title, text]) => <article key={number}><span className="step-number">{number}</span><h3>{title}</h3><p>{text}</p></article>)}</div></section>

      <section className="membership-section section-shell" id="membership"><div className="section-heading centered"><span className="eyebrow">START CURIOUS. GO FURTHER.</span><h2>Your workspace. Your next step.</h2><p>Get a feel for the platform, then unlock the details when you’re ready.</p></div><div className="membership-grid">
        <article className="membership-card"><span className="plan-label">EXPLORE</span><h3>Start with a free account.</h3><p>Find your bearings before you commit.</p><div className="plan-price">Free<span>to get started</span></div><ul><li>Property names and addresses</li><li>Sale and rental previews</li><li>Search and filter property previews</li><li>Your own agent profile</li></ul><p className="plan-boundary">Owner and contact details stay private.</p><JoinButton className="button button-secondary">Create a free account <span aria-hidden="true">↗</span></JoinButton></article>
        <article className="membership-card paid-plan"><div className="plan-top"><span className="plan-label">CONNECT</span><span className="membership-tag">The full picture</span></div><h3>Turn information into opportunity.</h3><p>For agents ready to make the connection.</p><div className="plan-price">Credits<span>for full-record searches</span></div><ul><li>Everything in your free account</li><li>Owner names and phone numbers</li><li>Full property record details</li><li>Request credits through WhatsApp</li></ul><p className="plan-boundary">Admin adds credits after payment confirmation. Property management stays with Admin.</p><Link className="button button-primary" href="/credits">Buy search credits <span aria-hidden="true">↗</span></Link></article>
      </div></section>

      <section className="faq-section section-shell"><div><span className="eyebrow">A LITTLE MORE CLARITY</span><h2>Good questions.<br/>Straight answers.</h2></div><div className="faq-list">
        <details><summary>Who is Property Planets for?<span aria-hidden="true">+</span></summary><p>Property agents looking for records of properties available for sale or rent, and the owner details needed to make a connection.</p></details>
        <details><summary>What can I see with a free account?<span aria-hidden="true">+</span></summary><p>After completing your profile, you can explore property addresses. Owner names, phone numbers, IC numbers and remarks require a search credit.</p></details>
        <details><summary>Can members change the property records?<span aria-hidden="true">+</span></summary><p>Credits unlock read access for your searches. Only the platform administrator can add, change, delete, import or export property records.</p></details>
      </div></section>

      <section className="closing-section section-shell"><div className="closing-orbit" aria-hidden="true"/><span className="eyebrow">THERE’S A WORLD OF POSSIBILITY.</span><h2>Find your next<br/><span>connection.</span></h2><p>Start exploring. See where it takes you.</p><JoinButton>Get started free <span aria-hidden="true">↗</span></JoinButton></section>
    </main>
    <footer className="site-footer section-shell"><Brand/><p>Built for your next move.</p><span>© {new Date().getFullYear()} Property Planets</span></footer>
  </>;
}
