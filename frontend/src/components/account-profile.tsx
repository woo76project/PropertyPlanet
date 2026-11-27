"use client";

import { SignInButton, useAuth } from "@clerk/nextjs";
import Link from "next/link";
import { useEffect, useState } from "react";
import ProfileForm from "@/components/profile-form";
import PasswordSetup from "@/components/password-setup";
import AccountDeletion from "@/components/account-deletion";
import { profileError, profileIsComplete, requestProfile, type Profile, type ProfileDetails } from "@/lib/profile-api";

type ProfileResult = { profile: Profile } | { error: string };

export default function AccountProfile() {
  const { isLoaded, isSignedIn, sessionId } = useAuth();
  if (!isLoaded) return <p role="status">Loading your session…</p>;
  if (!isSignedIn) return <section className="signed-out-account"><h2>Your next move starts here.</h2><p>Sign in to complete your profile and access your workspace.</p><SignInButton mode="modal" fallbackRedirectUrl="/account"><button className="button button-primary">Sign in to continue</button></SignInButton></section>;
  return <div key={sessionId}><SignedInProfile /><AccountDeletion /></div>;
}

function SignedInProfile() {
  const { getToken } = useAuth();
  const [result, setResult] = useState<ProfileResult | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    async function loadProfile() {
      try {
        const token = await getToken();
        if (controller.signal.aborted) return;
        if (!token) throw new Error("Your session has expired. Please sign in again.");
        const profile = await requestProfile(token, undefined, controller.signal);
        if (!controller.signal.aborted) setResult({ profile });
      } catch (error) {
        if (!controller.signal.aborted) setResult({ error: profileError(error) });
      }
    }
    void loadProfile();
    return () => controller.abort();
  }, [getToken, attempt]);

  async function saveProfile(details: ProfileDetails) {
    setSaving(true);
    setSaveError(null);
    setSaved(false);
    try {
      const token = await getToken();
      if (!token) throw new Error("Your session has expired. Please sign in again.");
      const profile = await requestProfile(token, details);
      setResult({ profile });
      setEditing(false);
      setSaved(true);
    } catch (error) {
      setSaveError(profileError(error));
    } finally {
      setSaving(false);
    }
  }

  if (!result) return <p role="status">Loading your account…</p>;
  if ("error" in result) return <section className="profile-load-error"><h2>We could not open your profile.</h2><p role="alert">{result.error}</p><button className="button button-secondary" onClick={() => { setResult(null); setAttempt((value) => value + 1); }}>Try again</button></section>;

  const profile = result.profile;
  const complete = profileIsComplete(profile);
  return <div className="account-grid">
    <section className="profile-card" aria-labelledby="account-heading">
      <div className="profile-card-heading"><span className="eyebrow">{complete ? "YOUR DETAILS" : "ONE MORE STEP"}</span>{complete && <span className="profile-badge">Profile complete</span>}</div>
      <h2 id="account-heading">{complete ? "Make yourself at home." : "Let’s get to know you."}</h2>
      <p className="profile-intro">{complete ? "Your profile is ready. You can review and update your details here." : "Complete your profile before exploring property records. Your email is already linked to your sign-in account."}</p>
      <div className="account-email"><span>Email address</span><strong>{profile.email}</strong></div>
      {saved && <p className="form-success" role="status">Your profile has been saved.</p>}
      {complete && <div className="billing-actions account-browse"><Link className="button button-primary" href="/properties">Browse properties <span aria-hidden="true">↗</span></Link></div>}
      {!complete || editing ? <ProfileForm key={profile.username ?? "new"} profile={profile} saving={saving} error={saveError} onSave={saveProfile} onCancel={complete ? () => { setEditing(false); setSaveError(null); } : undefined} />
        : <><dl className="profile-summary"><div><dt>Username</dt><dd>{profile.username}</dd></div><div><dt>Name</dt><dd>{profile.first_name} {profile.last_name}</dd></div><div><dt>Phone number</dt><dd>{profile.phone}</dd></div></dl><button className="button button-secondary" onClick={() => { setEditing(true); setSaved(false); }}>Edit profile</button></>}
    </section>
    {complete && <aside className="profile-sidebar"><PasswordSetup /></aside>}
  </div>;
}
