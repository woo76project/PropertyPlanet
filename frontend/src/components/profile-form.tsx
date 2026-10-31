"use client";

import { useState, type FormEvent } from "react";
import PhoneInput from "react-phone-number-input/max";
import flags from "react-phone-number-input/flags";
import { canonicalProfilePhone, initialProfilePhone } from "@/lib/profile-phone";
import type { Profile, ProfileDetails } from "@/lib/profile-api";

export default function ProfileForm({ profile, saving, error, onSave, onCancel }: {
  profile: Profile;
  saving: boolean;
  error: string | null;
  onSave: (details: ProfileDetails) => Promise<void>;
  onCancel?: () => void;
}) {
  const [validation, setValidation] = useState<string | null>(null);
  const [phone, setPhone] = useState(() => initialProfilePhone(profile.phone));
  const [phoneError, setPhoneError] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setValidation(null);
    const values = new FormData(event.currentTarget);
    const details = {
      username: String(values.get("username") ?? "").trim().toLowerCase(),
      first_name: String(values.get("first_name") ?? "").trim(),
      last_name: String(values.get("last_name") ?? "").trim(),
      phone: canonicalProfilePhone(phone) ?? "",
    };
    if (!details.phone) {
      setPhoneError(true);
      setValidation("Choose your country and enter a valid phone number.");
      return;
    }
    setPhoneError(false);
    if (Object.values(details).some((value) => !value)) {
      setValidation("Please complete every field. Spaces alone do not count.");
      return;
    }
    await onSave(details);
  }

  return <form onSubmit={submit} className="profile-form" aria-busy={saving}>
    <fieldset disabled={saving}>
      <label htmlFor="profile-username">Username <span>Required</span></label>
      <input id="profile-username" name="username" autoComplete="username" defaultValue={profile.username ?? ""} required minLength={4} maxLength={50} pattern="[a-zA-Z0-9_]+" aria-describedby="username-help" />
      <p id="username-help" className="field-help">4–50 letters, numbers or underscores. Saved in lowercase. This will be your sign-in username.</p>
      <div className="profile-name-grid">
        <div><label htmlFor="profile-first-name">First name</label><input id="profile-first-name" name="first_name" autoComplete="given-name" defaultValue={profile.first_name ?? ""} required maxLength={100} /></div>
        <div><label htmlFor="profile-last-name">Last name</label><input id="profile-last-name" name="last_name" autoComplete="family-name" defaultValue={profile.last_name ?? ""} required maxLength={100} /></div>
      </div>
      <label htmlFor="profile-phone">Phone number</label>
      <PhoneInput id="profile-phone" name="phone" className="profile-phone" defaultCountry="MY" international
        countryCallingCodeEditable={false} countrySelectProps={{ unicodeFlags: false, "aria-label": "Phone country" }}
        addInternationalOption={false} flags={flags} disabled={saving} value={phone} onChange={(value) => { setPhone(value); setPhoneError(false); }}
        required autoComplete="tel" aria-describedby="phone-help" aria-invalid={phoneError || undefined} />
      <p id="phone-help" className="field-help">Choose your country, then enter your number. The country code is added automatically.</p>
      {(validation || error) && <p className="form-error" role="alert">{validation || error}</p>}
      <div className="profile-form-actions"><button type="submit" className="button button-primary">{saving ? "Saving your profile…" : "Save profile"}</button>{onCancel && <button type="button" className="button button-quiet" onClick={onCancel}>Cancel</button>}</div>
    </fieldset>
  </form>;
}
