"use client";

import Link from "next/link";
import { useAuth } from "@clerk/nextjs";

export default function Brand() {
  const { isSignedIn } = useAuth();
  return <Link href={isSignedIn ? "/properties" : "/"} className="brand" aria-label={isSignedIn ? "Property Planets properties" : "Property Planets home"}>
    <span className="brand-orbit" aria-hidden="true"><span /></span>
    <span>property<span className="brand-light">planets</span><span className="brand-period">.</span></span>
  </Link>;
}
