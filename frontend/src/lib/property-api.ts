export type AccessMode = "free" | "credit" | "admin";
export type PropertyRecord = {
  id: number;
  address: string | null;
  owner_name?: string;
  phone_01?: string;
  phone_02?: string | null;
  ic_number?: string | null;
  remark?: string | null;
};
export type PropertyPage = {
  properties: PropertyRecord[];
  pagination: { limit: number; has_more: boolean; next_before_id: number | null };
};
export type SearchCondition = {
  field: "owner_name" | "address";
  operator: "contains" | "starts_with" | "ends_with" | "equals";
  value: string;
};
export type SearchBuilder = { match: "all" | "any"; conditions: SearchCondition[] };
export type PropertyQuery = { search: string; limit: number; beforeId: number | null; builder?: SearchBuilder | null; searchId?: string | null };
export type PropertyData = {
  owner_name: string; phone_01: string; phone_02: string | null;
  ic_number: string | null; address: string | null; remark: string | null;
};
export type PropertyPageResult = { mode: AccessMode; page: PropertyPage; balance: number; unlimited: boolean; charged: boolean };

export class PropertyApiError extends Error {
  status: number;
  constructor(status: number, message: string) { super(message); this.status = status; }
}

export function searchClueError(value: string): string | null {
  return value.trim().length < 1 ? "Enter at least one character for each search clue." : null;
}

async function apiGet(token: string, path: string, signal?: AbortSignal, method = "GET", body?: unknown) {
  const base = process.env.NEXT_PUBLIC_API_URL;
  if (!base) throw new Error("The property service has not been configured yet.");
  const response = await fetch(`${base.replace(/\/$/, "")}${path}`, {
    method, headers: { Authorization: `Bearer ${token}`, ...(body ? { "Content-Type": "application/json" } : {}) },
    body: body ? JSON.stringify(body) : undefined,
    credentials: "omit", cache: "no-store", redirect: "error", signal,
  });
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    throw new PropertyApiError(response.status, typeof data?.detail === "string" ? data.detail
      : response.status === 422 ? "Some property details are invalid. Check the form and try again."
      : "The property service is temporarily unavailable. Please try again.");
  }
  return data;
}

function readPage(data: unknown, fullAccess: boolean): PropertyPage {
  if (!data || typeof data !== "object" || !("properties" in data) || !("pagination" in data)) {
    throw new Error("The property service returned an unexpected response.");
  }
  const rows = data.properties;
  const pagination = data.pagination;
  if (!Array.isArray(rows) || rows.length > 100 || !pagination || typeof pagination !== "object"
    || !("limit" in pagination) || !Number.isInteger(pagination.limit) || Number(pagination.limit) < 1
    || Number(pagination.limit) > 100 || !("has_more" in pagination) || typeof pagination.has_more !== "boolean"
    || !("next_before_id" in pagination)
    || (pagination.has_more && (!Number.isSafeInteger(pagination.next_before_id) || Number(pagination.next_before_id) < 1))) {
    throw new Error("The property service returned invalid pagination.");
  }
  const properties = rows.map((row) => {
    if (!row || !Number.isSafeInteger(row.id) || row.id < 1
      || typeof row.owner_name !== "string"
      || (row.address !== null && typeof row.address !== "string")) {
      throw new Error("The property service returned an invalid property.");
    }
    const record: PropertyRecord = {
      id: row.id, address: row.address, owner_name: row.owner_name,
    };
    if (fullAccess) {
      if (typeof row.owner_name !== "string" || typeof row.phone_01 !== "string"
        || ![row.phone_02, row.ic_number, row.remark].every((value) => value === null || typeof value === "string")) {
        throw new Error("The property service returned incomplete owner details.");
      }
      Object.assign(record, { owner_name: row.owner_name, phone_01: row.phone_01, phone_02: row.phone_02, ic_number: row.ic_number, remark: row.remark });
    }
    return record;
  });
  if (rows.length > Number(pagination.limit) || (pagination.has_more && !rows.length)) {
    throw new Error("The property service returned invalid pagination.");
  }
  return { properties, pagination: { limit: Number(pagination.limit), has_more: pagination.has_more,
    next_before_id: pagination.has_more ? Number(pagination.next_before_id) : null } };
}

export async function loadPropertyPage(token: string, query: PropertyQuery, signal?: AbortSignal, adminOnly = false): Promise<PropertyPageResult> {
  if (query.builder && (query.builder.conditions.length > 10 || ["owner_name", "address"].some((field) => query.builder!.conditions.filter((row) => row.field === field).length > 5))) {
    throw new PropertyApiError(422, "Use up to ten conditions total and five per category.");
  }
  const clues = [
    ...(query.search.trim() ? [searchClueError(query.search)] : []),
    ...(query.builder?.conditions.map((row) => searchClueError(row.value)) ?? []),
  ];
  const invalidClue = clues.find((error) => error !== null);
  if (invalidClue) throw new PropertyApiError(422, invalidClue);
  const profile = await apiGet(token, "/auth/me", signal);
  if (!profile || !["Admin", "User"].includes(profile.role) || profile.status !== "Active") {
    throw new PropertyApiError(403, "Your account is not available. Please contact support.");
  }
  if (![profile.username, profile.first_name, profile.last_name, profile.phone]
    .every((value) => typeof value === "string" && value.trim())) {
    throw new PropertyApiError(428, "Complete your profile before browsing properties.");
  }
  if (adminOnly && profile.role !== "Admin") throw new PropertyApiError(403, "Only Admin can manage properties.");
  const credits = profile.role === "Admin" ? { balance: 0, unlimited: true } : await apiGet(token, "/credits", signal);
  if (!credits || !Number.isSafeInteger(credits.balance) || credits.balance < 0 || typeof credits.unlimited !== "boolean") throw new Error("Your credit balance could not be checked. Please try again.");
  const mode: AccessMode = profile.role === "Admin" ? "admin" : query.searchId ? "credit" : "free";
  const params = new URLSearchParams({ limit: String(query.limit) });
  if (query.builder) params.set("filters", JSON.stringify(query.builder));
  if (query.search.trim()) params.set("search", query.search.trim());
  if (query.beforeId !== null) params.set("before_id", String(query.beforeId));
  const data = mode === "credit"
    ? await apiGet(token, "/properties/search", signal, "POST", { request_id: query.searchId, search: query.search.trim(),
        filters: query.builder ?? null, before_id: query.beforeId, limit: query.limit })
    : await apiGet(token, `${mode === "free" ? "/properties/preview" : "/properties"}?${params}`, signal);
  if (mode === "credit" && (!Number.isSafeInteger(data?.balance) || data.balance < 0 || typeof data.charged !== "boolean")) throw new Error("The credit charge could not be confirmed. Refresh this search before starting another.");
  return { mode, page: readPage(data, mode !== "free"), balance: mode === "credit" ? data.balance : credits.balance,
    unlimited: credits.unlimited, charged: mode === "credit" && data.charged };
}

export async function loadPropertyTotal(token: string, signal?: AbortSignal): Promise<number> {
  const data = await apiGet(token, "/properties/summary", signal);
  if (!data || !Number.isSafeInteger(data.total) || data.total < 0) {
    throw new Error("The total property count could not be loaded.");
  }
  return data.total;
}

export function normalizePropertyData(input: Record<keyof PropertyData, string>): PropertyData {
  const fields = { owner_name: 200, phone_01: 30, phone_02: 30, ic_number: 30, address: 1000, remark: 2000 } as const;
  const values: Record<string, string | null> = {};
  for (const [name, max] of Object.entries(fields)) {
    const value = input[name as keyof PropertyData].trim();
    if (!value && ["owner_name", "phone_01"].includes(name)) throw new Error("Name and Phone 01 are required.");
    if (value.length > max) throw new Error(`${name.replaceAll("_", " ")} must be ${max} characters or fewer.`);
    values[name] = value || null;
  }
  return values as PropertyData;
}

export async function saveProperty(token: string, data: PropertyData, id: number | null, signal?: AbortSignal): Promise<PropertyRecord> {
  if (id !== null && (!Number.isSafeInteger(id) || id < 1)) throw new Error("Invalid property ID.");
  const row = await apiGet(token, id === null ? "/properties" : `/properties/${id}`, signal, id === null ? "POST" : "PATCH", data);
  return readPage({ properties: [row], pagination: { limit: 1, has_more: false, next_before_id: null } }, true).properties[0];
}

export async function deleteProperty(token: string, id: number, signal?: AbortSignal): Promise<void> {
  if (!Number.isSafeInteger(id) || id < 1) throw new Error("Invalid property ID.");
  await apiGet(token, `/properties/${id}`, signal, "DELETE");
}
