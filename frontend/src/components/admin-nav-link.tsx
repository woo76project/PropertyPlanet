"use client";
import { useAuth } from "@clerk/nextjs";
import HeaderNavLink from "@/components/header-nav-link";
import { useEffect, useState } from "react";

export default function AdminNavLink() {
  const { isSignedIn, sessionId } = useAuth();
  if (!isSignedIn) return null;
  // Keep the role state across navigation; reset it when the session changes.
  return <CheckedAdminLink key={sessionId} />;
}

function CheckedAdminLink() {
  const { getToken } = useAuth();
  const [role, setRole] = useState<"Admin" | "User" | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    async function check() {
      const base = process.env.NEXT_PUBLIC_API_URL;
      if (!base) return;
      try {
        const token = await getToken();
        if (!token || controller.signal.aborted) return;
        const response = await fetch(`${base.replace(/\/$/, "")}/auth/me`, {
          headers: { Authorization: `Bearer ${token}` }, credentials: "omit", cache: "no-store",
          redirect: "error", signal: controller.signal,
        });
        const profile = response.ok ? await response.json() : null;
        if (!controller.signal.aborted) setRole(profile?.status === "Active" && ["Admin", "User"].includes(profile?.role) ? profile.role : null);
      } catch { if (!controller.signal.aborted) setRole(null); }
    }
    void check();
    window.addEventListener("focus", check);
    return () => { controller.abort(); window.removeEventListener("focus", check); };
  }, [getToken]);
  return role === "Admin" ? <><HeaderNavLink href="/admin/properties" alsoActive={["/admin/imports"]}>Manage properties</HeaderNavLink><HeaderNavLink href="/admin/users">User management</HeaderNavLink><HeaderNavLink href="/admin/audit">Audit history</HeaderNavLink></> : null;
}
