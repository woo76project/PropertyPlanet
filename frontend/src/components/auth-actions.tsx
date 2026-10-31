import { Show, SignInButton, SignOutButton, SignUpButton } from "@clerk/nextjs";
import Link from "next/link";
import type { ReactNode } from "react";

export function JoinButton({ children = "Get started free", className = "button button-primary" }: {
  children?: ReactNode;
  className?: string;
}) {
  return <>
    <Show when="signed-out">
      <SignUpButton mode="modal" fallbackRedirectUrl="/account">
        <button className={className}>{children}</button>
      </SignUpButton>
    </Show>
    <Show when="signed-in"><Link className={className} href="/properties">Browse properties <span aria-hidden="true">↗</span></Link></Show>
  </>;
}

export default function AuthActions() {
  return <div className="auth-actions">
    <Show when="signed-out">
      <SignInButton mode="modal" fallbackRedirectUrl="/account"><button className="nav-signin">Sign in</button></SignInButton>
    </Show>
    <Show when="signed-in"><SignOutButton><button className="nav-signin">Sign out</button></SignOutButton></Show>
    <Show when="signed-out"><JoinButton className="button button-small button-primary">Get started <span aria-hidden="true">↗</span></JoinButton></Show>
  </div>;
}
