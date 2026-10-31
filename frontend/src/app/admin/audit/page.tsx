import AuditHistory from "@/components/audit-history";
export default function AuditPage() {
  return <main className="property-page section-shell"><div className="workspace-breadcrumb"><span>Administration</span><span>/</span><span>Audit history</span></div><div className="property-page-heading"><span className="eyebrow">AUDIT HISTORY</span><h1>A record of what changed.</h1><p>Review property changes, imports, exports and user-status actions. All times are shown in Malaysia time.</p></div><AuditHistory /></main>;
}
