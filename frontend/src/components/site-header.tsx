import Link from "next/link";
import { Show } from "@clerk/nextjs";
import AuthActions from "@/components/auth-actions";
import AdminNavLink from "@/components/admin-nav-link";
import HeaderNavLink from "@/components/header-nav-link";
import Brand from "@/components/brand";

export { Brand };

export default function SiteHeader() {
  return <header className="site-header">
    <div className="site-header-inner">
      <Brand />
      <nav className="nav-links" aria-label="Main navigation">
        <Show when="signed-out"><Link href="/#features">Why Property Planets</Link><Link href="/#how-it-works">How it works</Link><Link href="/#membership">Search credits</Link></Show>
        <Show when="signed-in"><HeaderNavLink href="/properties">Properties</HeaderNavLink><HeaderNavLink href="/account">My account</HeaderNavLink><AdminNavLink /></Show>
      </nav>
      <AuthActions />
    </div>
  </header>;
}
