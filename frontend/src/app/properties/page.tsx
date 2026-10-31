import Link from "next/link";
import PropertyBrowser from "@/components/property-browser";

export default function PropertiesPage() {
  return <main className="property-page section-shell"><div className="workspace-breadcrumb"><Link href="/account">My account</Link><span aria-hidden="true">/</span><span>Properties</span></div><PropertyBrowser /></main>;
}
