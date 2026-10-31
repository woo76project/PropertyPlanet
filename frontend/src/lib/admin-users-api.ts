export type AccountStatus = "Active" | "Suspended" | "Deleting" | "Deleted";
export type AdminUser = {
  id: number; username: string | null; email: string; first_name: string | null; last_name: string | null;
  phone: string | null; role: "Admin" | "User"; status: AccountStatus; created_at: string; updated_at: string;
  credits: number;
};
export type UsersQuery = { search: string; status: "" | "Active" | "Suspended"; limit: number; beforeId: number | null };
export type UsersPage = { users: AdminUser[]; pagination: { limit: number; has_more: boolean; next_before_id: number | null } };
export type UserCredits = { balance: number; unlimited: boolean };
export type CreditAdjustment = { request_id: string; direction: "add" | "subtract"; amount: number; reason: string; expected_balance: number };
export class AdminUsersError extends Error {
  status: number;
  constructor(status: number, message: string) { super(message); this.status = status; }
}
export function canChangeUser(user: Pick<AdminUser, "role" | "status">) {
  return user.role === "User" && ["Active", "Suspended"].includes(user.status);
}
function userPath(id: number) {
  if (!Number.isSafeInteger(id) || id < 1) throw new Error("Invalid user ID.");
  return `/admin/users/${id}`;
}
async function request(token: string, path: string, signal?: AbortSignal, status?: "Active" | "Suspended", assignment?: { request_id: string; amount: number; reference: string } | CreditAdjustment) {
  const base = process.env.NEXT_PUBLIC_API_URL;
  if (!base) throw new Error("User management has not been configured yet.");
  const response = await fetch(`${base.replace(/\/$/, "")}${path}`, {
    method: assignment ? "POST" : status ? "PATCH" : "GET", signal, credentials: "omit", cache: "no-store", redirect: "error",
    headers: { Authorization: `Bearer ${token}`, ...(status || assignment ? { "Content-Type": "application/json" } : {}) },
    body: assignment ? JSON.stringify(assignment) : status ? JSON.stringify({ status }) : undefined,
  });
  const data = await response.json().catch(() => null);
  if (!response.ok) throw new AdminUsersError(response.status, typeof data?.detail === "string" ? data.detail : "User management is temporarily unavailable. Please try again.");
  return data;
}
function readUser(data: unknown): AdminUser {
  if (!data || typeof data !== "object") throw new Error("Invalid user response.");
  const user = data as AdminUser;
  if (!Number.isSafeInteger(user.id) || user.id < 1 || typeof user.email !== "string"
    || !["Admin", "User"].includes(user.role) || !["Active", "Suspended", "Deleting", "Deleted"].includes(user.status)
    || ![user.username, user.first_name, user.last_name, user.phone].every((value) => value === null || typeof value === "string")
    || ![user.created_at, user.updated_at].every((value) => typeof value === "string" && Number.isFinite(Date.parse(value)))) throw new Error("Invalid user response.");
  if (user.credits !== undefined && user.credits !== null && (!Number.isSafeInteger(user.credits) || user.credits < 0)) throw new Error("Invalid credit balance.");
  return { ...user, credits: user.credits ?? 0 };
}
export async function loadAdminUsers(token: string, query: UsersQuery, signal?: AbortSignal): Promise<UsersPage> {
  const params = new URLSearchParams({ limit: String(query.limit) });
  if (query.search.trim()) params.set("search", query.search.trim());
  if (query.status) params.set("status", query.status);
  if (query.beforeId !== null) { userPath(query.beforeId); params.set("before_id", String(query.beforeId)); }
  const data = await request(token, `/admin/users?${params}`, signal);
  const page = data?.pagination;
  if (!Array.isArray(data?.users) || !page || !Number.isInteger(page.limit) || page.limit < 1 || page.limit > 100
    || data.users.length > page.limit || typeof page.has_more !== "boolean"
    || (page.has_more && (!data.users.length || !Number.isSafeInteger(page.next_before_id) || page.next_before_id < 1))) throw new Error("Invalid user list response.");
  return { users: data.users.map(readUser), pagination: { limit: page.limit, has_more: page.has_more, next_before_id: page.has_more ? page.next_before_id : null } };
}
export async function loadUserCredits(token: string, id: number, signal?: AbortSignal): Promise<UserCredits> {
  const data = await request(token, `${userPath(id)}/credits`, signal);
  if (!data || !Number.isSafeInteger(data.balance) || data.balance < 0 || typeof data.unlimited !== "boolean") throw new Error("Invalid credit balance response.");
  return { balance: data.balance, unlimited: data.unlimited };
}
export async function assignUserCredits(token: string, id: number, amount: number, reference: string, requestId: string, signal?: AbortSignal): Promise<UserCredits> {
  if (!Number.isSafeInteger(amount) || amount < 1 || amount > 1000000 || !reference.trim() || reference.trim().length > 200) throw new Error("Enter a positive whole credit amount and a payment reference (up to 200 characters).");
  const data = await request(token, `${userPath(id)}/credits`, signal, undefined, { request_id: requestId, amount, reference: reference.trim() });
  if (!data || !Number.isSafeInteger(data.balance) || data.balance < 0 || data.assigned !== amount || typeof data.duplicate !== "boolean") throw new Error("Assignment could not be confirmed. Retry the same assignment before entering another.");
  return { balance: data.balance, unlimited: false };
}
export async function changeUserStatus(token: string, id: number, status: "Active" | "Suspended", signal?: AbortSignal) {
  if (!["Active", "Suspended"].includes(status)) throw new Error("Choose Active or Suspended.");
  const user = readUser(await request(token, `${userPath(id)}/status`, signal, status));
  if (user.id !== id || user.status !== status) throw new Error("Could not confirm the account change. Refresh the user list before trying again.");
  return user;
}
export async function adjustUserCredits(token: string, id: number, adjustment: CreditAdjustment, signal?: AbortSignal): Promise<UserCredits> {
  const { amount, direction, expected_balance: before, reason } = adjustment;
  if (!["add", "subtract"].includes(direction) || !Number.isSafeInteger(amount) || amount < 1 || amount > 1000000
    || !Number.isSafeInteger(before) || before < 0 || before > 1000000000 || !reason.trim() || reason.trim().length > 200) {
    throw new Error("Enter an add/subtract amount, a valid balance and a reason (up to 200 characters).");
  }
  const delta = direction === "add" ? amount : -amount;
  if (before + delta < 0 || before + delta > 1000000000) throw new Error("Adjustment cannot make the balance negative or exceed the limit.");
  const data = await request(token, `${userPath(id)}/credits/adjustments`, signal, undefined, { ...adjustment, reason: reason.trim() });
  if (!data || !Number.isSafeInteger(data.balance) || data.balance < 0 || data.balance > 1000000000
    || data.adjusted !== delta || typeof data.duplicate !== "boolean" || (!data.duplicate && data.balance !== before + delta)) {
    throw new Error("Adjustment could not be confirmed. Retry the same adjustment before starting another.");
  }
  return { balance: data.balance, unlimited: false };
}
export function adminUsersError(error: unknown) {
  return error instanceof TypeError ? "Unable to confirm the latest account state. Refresh the user list before trying again."
    : error instanceof Error ? error.message : "User management is temporarily unavailable.";
}
