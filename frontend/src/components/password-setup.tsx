"use client";

import { useReverification, useUser } from "@clerk/nextjs";
import { isClerkAPIResponseError } from "@clerk/nextjs/errors";
import { useState, type FormEvent } from "react";
import PasswordInput from "@/components/password-input";

export default function PasswordSetup() {
  const { user } = useUser();
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [complete, setComplete] = useState(false);
  const addPassword = useReverification((newPassword: string) =>
    user?.updatePassword({ newPassword, signOutOfOtherSessions: true }));

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const values = new FormData(form);
    const password = String(values.get("password") ?? "");
    if (password !== values.get("confirm_password")) {
      setError("The passwords do not match.");
      return;
    }
    setError(null);
    setSaving(true);
    try {
      const updated = await addPassword(password);
      if (!updated) throw new Error("Verification was not completed. Please try again.");
      form.reset();
      setComplete(true);
    } catch (error) {
      setError(isClerkAPIResponseError(error)
        ? error.errors[0]?.longMessage ?? error.errors[0]?.message ?? "Unable to add your password."
        : error instanceof Error ? error.message : "Unable to add your password. Please try again.");
    } finally {
      setSaving(false);
    }
  }

  if (!user) return null;
  if (complete || user.passwordEnabled) {
    return <section className="password-card"><span className="profile-badge">Password set</span><h2>Another way to sign in.</h2><p>Your account has a password. You can use it with your username when username sign-in is enabled.</p></section>;
  }
  return <section className="password-card"><span className="eyebrow">OPTIONAL</span><h2>Add password sign-in.</h2><p>You can continue using Google. Add a separate password if you also want to sign in with your username.</p>
    <form onSubmit={submit} className="profile-form" aria-busy={saving}><fieldset disabled={saving}>
      <label htmlFor="new-password">New password</label><PasswordInput id="new-password" name="password" description="new password" />
      <label htmlFor="confirm-password">Confirm password</label><PasswordInput id="confirm-password" name="confirm_password" description="confirmation password" />
      <p className="field-help">Use a new password for this account. Your Google password is not needed. Other sessions will be signed out.</p>
      {error && <p className="form-error" role="alert">{error}</p>}
      <button className="button button-secondary" type="submit">{saving ? "Adding password…" : "Add password"}</button>
    </fieldset></form>
  </section>;
}
