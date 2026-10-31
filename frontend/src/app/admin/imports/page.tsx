import AdminSectionNav from "@/components/admin-section-nav";
import Link from "next/link";
import ImportManager from "@/components/import-manager";
export default function ImportsPage() {
  return <main className="property-page section-shell"><div className="workspace-breadcrumb"><Link href="/admin/properties">Manage properties</Link><span>/</span><span>Imports</span></div><div className="property-page-heading"><span className="eyebrow">ADMIN IMPORTS</span><h1>From spreadsheet to workspace.</h1><p>Upload property records, follow the progress, and review any rows that need attention.</p></div><AdminSectionNav /><ImportManager /></main>;
}
