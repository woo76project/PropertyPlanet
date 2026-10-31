export const AUDIT_ACTIONS = {
  "property.created": "Property created", "property.updated": "Property updated", "property.deleted": "Property deleted",
  "user.suspended": "User suspended", "user.reactivated": "User reactivated",
  "user.credits_assigned": "Credits assigned",
  "user.credits_adjusted": "Credits adjusted",
  "properties.imported": "Properties imported", "properties.exported": "Properties exported",
} as const;
export type AuditAction = keyof typeof AUDIT_ACTIONS;
export const AUDIT_CATEGORIES = { properties: "Properties", users: "Users" } as const;
export type AuditCategory = keyof typeof AUDIT_CATEGORIES;
export type AuditEntry = {
  id: number; actor_user_id: number; actor_username: string | null; action: AuditAction;
  entity_type: string; entity_id: number | null; details: Record<string, unknown>; created_at: string;
};
export type AuditQuery = { category: "" | AuditCategory; actorId: number | null; actorUsername?: string; beforeId: number | null; limit: number };
export type AuditPage = { audit_logs: AuditEntry[]; pagination: { limit: number; has_more: boolean; next_before_id: number | null } };
export class AuditApiError extends Error {
  status: number;
  constructor(status: number, message: string) { super(message); this.status = status; }
}
const positiveId = (id: unknown): id is number => Number.isSafeInteger(id) && Number(id) > 0;
export async function loadAuditPage(token: string, query: AuditQuery, signal?: AbortSignal): Promise<AuditPage> {
  const base = process.env.NEXT_PUBLIC_API_URL;
  if (!base) throw new Error("Audit history has not been configured yet.");
  if ((query.actorId !== null && !positiveId(query.actorId)) || (query.beforeId !== null && !positiveId(query.beforeId))) throw new Error("Enter a valid positive user ID.");
  const username = query.actorUsername?.trim() ?? "";
  if (username.length > 200) throw new Error("Username search must be 200 characters or fewer.");
  if (!Number.isInteger(query.limit) || query.limit < 1 || query.limit > 100) throw new Error("Choose between 1 and 100 events per page.");
  if (query.category && !Object.hasOwn(AUDIT_CATEGORIES, query.category)) throw new Error("Choose a valid audit category.");
  const params = new URLSearchParams({ limit: String(query.limit) });
  if (query.category) params.set("category", query.category);
  if (query.actorId !== null) params.set("actor_user_id", String(query.actorId));
  else if (username) params.set("actor_username", username);
  if (query.beforeId !== null) params.set("before_id", String(query.beforeId));
  const response = await fetch(`${base.replace(/\/$/, "")}/admin/audit-logs?${params}`, {
    headers: { Authorization: `Bearer ${token}` }, signal, credentials: "omit", cache: "no-store", redirect: "error",
  });
  const data = await response.json().catch(() => null);
  if (!response.ok) throw new AuditApiError(response.status, typeof data?.detail === "string" ? data.detail : "Audit history is temporarily unavailable. Please try again.");
  const page = data?.pagination;
  if (!Array.isArray(data?.audit_logs) || !page || !Number.isInteger(page.limit) || page.limit < 1 || page.limit > 100
    || data.audit_logs.length > page.limit || typeof page.has_more !== "boolean"
    || (page.has_more && (!data.audit_logs.length || !positiveId(page.next_before_id)))) throw new Error("Invalid audit history response.");
  const audit_logs = data.audit_logs.map((entry: AuditEntry) => {
    if (!entry || !positiveId(entry.id) || !positiveId(entry.actor_user_id) || !(entry.actor_username === null || typeof entry.actor_username === "string")
      || !Object.hasOwn(AUDIT_ACTIONS, entry.action) || typeof entry.entity_type !== "string"
      || !(entry.entity_id === null || positiveId(entry.entity_id)) || !entry.details || typeof entry.details !== "object" || Array.isArray(entry.details)
      || typeof entry.created_at !== "string" || !Number.isFinite(Date.parse(entry.created_at))) throw new Error("Invalid audit event response.");
    return entry;
  });
  return { audit_logs, pagination: { limit: page.limit, has_more: page.has_more, next_before_id: page.has_more ? page.next_before_id : null } };
}
export function auditSummary(entry: AuditEntry): string[] {
  const result: string[] = [];
  const details = entry.details;
  const fields: Record<string, string> = { owner_name: "Name", phone_01: "Phone 01", phone_02: "Phone 02", ic_number: "IC number", address: "Address", remark: "Remark" };
  if (Array.isArray(details.fields)) {
    const names = details.fields.filter((field): field is string => typeof field === "string").map((field) => Object.hasOwn(fields, field) ? fields[field] : field.replaceAll("_", " "));
    if (names.length) result.push(`Updated fields: ${names.join(", ")}`);
  }
  if (typeof details.previous_status === "string" && typeof details.status === "string") result.push(`${details.previous_status} → ${details.status}`);
  const counts = [
    ["count", entry.action === "properties.exported" ? "Exported" : "Inserted"],
    ["duplicates_skipped", "Duplicates skipped"], ["invalid_rows", "Invalid rows"],
    ["amount", "Credits added"], ["balance_after", "Credit balance"],
  ];
  for (const [key, label] of counts) {
    const value = details[key];
    if (typeof value === "number" && Number.isSafeInteger(value) && value >= 0) result.push(`${label}: ${value.toLocaleString("en-MY")}`);
  }
  if (typeof details.import_job_id === "string") result.push(`Import job: ${details.import_job_id}`);
  if (typeof details.reference === "string") result.push(`Payment reference: ${details.reference}`);
  if (typeof details.delta === "number" && Number.isSafeInteger(details.delta)) result.push(`Credit adjustment: ${details.delta > 0 ? "+" : ""}${details.delta.toLocaleString("en-MY")}`);
  if (typeof details.balance_before === "number" && Number.isSafeInteger(details.balance_before) && details.balance_before >= 0) result.push(`Previous balance: ${details.balance_before.toLocaleString("en-MY")}`);
  if (typeof details.reason === "string") result.push(`Reason: ${details.reason}`);
  if (typeof details.search_used === "boolean") result.push(`Search filter: ${details.search_used ? "Used" : "Not used"}`);
  if (typeof details.listing_purpose === "string") result.push(`Purpose: ${details.listing_purpose === "SaleOrRent" ? "Sale or rent" : details.listing_purpose}`);
  return result.length ? result : ["No additional details recorded."];
}
export function auditError(error: unknown) {
  return error instanceof TypeError ? "Unable to reach the audit service. Please try again."
    : error instanceof Error ? error.message : "Audit history could not be loaded.";
}
