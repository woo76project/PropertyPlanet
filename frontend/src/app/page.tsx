import Link from "next/link";
import { JoinButton } from "@/components/auth-actions";
import { Brand } from "@/components/site-header";
import ProductPreview from "@/components/product-preview";
import { auth } from "@clerk/nextjs/server";
import { redirect } from "next/navigation";

export default async function Home() {
  const { userId } = await auth();
  if (userId) redirect("/properties");

  return <>
    <main>
      <section className="hero section-shell">
        <div className="hero-copy">
          <div className="hero-eyebrow"><span className="status-dot"/> YOUR NEXT OPPORTUNITY IS OUT THERE</div>
          <h1>A better place<br/>to find your<br/><span>next connection.</span></h1>
          <div className="hero-actions"><JoinButton>Explore your possibilities <span aria-hidden="true">↗</span></JoinButton><a className="button button-quiet" href="#preview">Take a look <span aria-hidden="true">↓</span></a></div>
          <div className="hero-footnote"><span aria-hidden="true">✓</span> Your private property workspace</div>
        </div>
        <ProductPreview />
      </section>

    </main>
    <footer className="site-footer section-shell"><Brand/><p>Built for your next move.</p><Link className="privacy-footer-link" href="/privacy">Privacy policy</Link><span>© {new Date().getFullYear()} Property Planets</span></footer>
  </>;
}
