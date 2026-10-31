import AdminSectionNav from "@/components/admin-section-nav";
import Link from "next/link";
import PropertyBrowser from "@/components/property-browser";
export default function AdminPropertiesPage() {
  return <main className="property-page section-shell"><div className="workspace-breadcrumb"><Link href="/properties">Properties</Link><span>/</span><span>Admin</span></div><div className="property-page-heading"><span className="eyebrow">PROPERTY MANAGEMENT</span><h1>Keep your records in order.</h1><p>Create, update and remove property records from your workspace.</p></div><AdminSectionNav /><PropertyBrowser adminMode /></main>;
}
