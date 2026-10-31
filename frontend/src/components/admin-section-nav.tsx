import HeaderNavLink from "@/components/header-nav-link";
export default function AdminSectionNav() {
  return <nav className="admin-section-nav" aria-label="Property management sections"><HeaderNavLink href="/admin/properties">Property records</HeaderNavLink><HeaderNavLink href="/admin/imports">Excel / CSV imports</HeaderNavLink></nav>;
}
