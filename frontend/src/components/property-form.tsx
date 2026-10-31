"use client";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { normalizePropertyData, type PropertyData, type PropertyRecord } from "@/lib/property-api";

export default function PropertyForm({ property, busy, error, onSave, onCancel }: {
  property: PropertyRecord | null; busy: boolean; error: string | null;
  onSave: (data: PropertyData) => void; onCancel: () => void;
}) {
  const firstInput = useRef<HTMLInputElement>(null);
  const [validation, setValidation] = useState<string | null>(null);
  useEffect(() => { firstInput.current?.focus(); }, []);
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    try {
      const data = normalizePropertyData(Object.fromEntries(
        ["owner_name", "phone_01", "phone_02", "ic_number", "address", "remark"]
          .map((name) => [name, String(form.get(name) ?? "")]),
      ) as Record<keyof PropertyData, string>);
      setValidation(null); onSave(data);
    } catch (error) { setValidation(error instanceof Error ? error.message : "Check your property details."); }
  }
  return <section className="property-editor" aria-labelledby="property-editor-title"><span className="eyebrow">{property ? `EDIT RECORD #${property.id}` : "NEW PROPERTY"}</span><h2 id="property-editor-title">{property ? "Update the details." : "Add a new connection."}</h2><p>Fields marked * are required.</p><form className="property-edit-form" onSubmit={submit}><fieldset disabled={busy}><div className="property-edit-grid">
    <div><label htmlFor="edit-owner-name">Name *</label><input ref={firstInput} id="edit-owner-name" name="owner_name" required maxLength={200} defaultValue={property?.owner_name ?? ""} /></div>
    <div><label htmlFor="edit-primary-phone">Phone 01 *</label><input type="tel" id="edit-primary-phone" name="phone_01" required maxLength={30} defaultValue={property?.phone_01 ?? ""} /></div>
    <div><label htmlFor="edit-alternative-phone">Phone 02</label><input type="tel" id="edit-alternative-phone" name="phone_02" maxLength={30} defaultValue={property?.phone_02 ?? ""} /></div>
    <div><label htmlFor="edit-ic">IC number</label><input id="edit-ic" name="ic_number" maxLength={30} defaultValue={property?.ic_number ?? ""} /></div>
    <div className="property-address"><label htmlFor="edit-address">Address</label><textarea id="edit-address" name="address" maxLength={1000} rows={3} defaultValue={property?.address ?? ""} /></div>
    <div className="property-address"><label htmlFor="edit-remark">Remark</label><textarea id="edit-remark" name="remark" maxLength={2000} rows={3} defaultValue={property?.remark ?? ""} /></div>
  </div>{(validation || error) && <p className="form-error" role="alert">{validation || error}</p>}<div className="billing-actions"><button className="button button-primary" type="submit">{busy ? "Saving…" : property ? "Save changes" : "Create property"}</button><button className="button button-secondary" type="button" onClick={onCancel}>Cancel</button></div></fieldset></form></section>;
}
