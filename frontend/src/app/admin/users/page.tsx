import AdminUsers from "@/components/admin-users";
export default function UsersPage() {
  return <main className="property-page section-shell"><div className="workspace-breadcrumb"><span>Administration</span><span>/</span><span>User management</span></div><div className="property-page-heading"><span className="eyebrow">USER MANAGEMENT</span><h1>The people in your workspace.</h1><p>Review profiles, assign search credits, and manage account access.</p></div><AdminUsers /></main>;
}
