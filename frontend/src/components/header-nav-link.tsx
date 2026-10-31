"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";

export default function HeaderNavLink({ href, activeRoot = href, alsoActive = [], children }: {
  href: string; activeRoot?: string; alsoActive?: string[]; children: ReactNode;
}) {
  const pathname = usePathname();
  const active = [activeRoot, ...alsoActive].some((root) => pathname === root || pathname.startsWith(`${root}/`));
  return <Link href={href} aria-current={active ? "page" : undefined}>{children}</Link>;
}
