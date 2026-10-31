export type DeletionState = { can_delete: boolean; status: "Active" | "Suspended" | "Deleting" | "Deleted" | "NoProfile" };
export class AccountDeletionError extends Error {
  status: number;
  constructor(status: number, message: string) { super(message); this.status = status; }
}
async function request(token: string, deleting: boolean, signal?: AbortSignal) {
  const base = process.env.NEXT_PUBLIC_API_URL;
  if (!base) throw new Error("Account settings have not been configured yet.");
  const response = await fetch(`${base.replace(/\/$/, "")}/auth/account`, {
    method: deleting ? "DELETE" : "GET", signal, credentials: "omit", cache: "no-store", redirect: "error",
    headers: { Authorization: `Bearer ${token}`, ...(deleting ? { "Content-Type": "application/json" } : {}) },
    body: deleting ? JSON.stringify({ confirmation: "DELETE" }) : undefined,
  });
  if (!response.ok) {
    const data = await response.json().catch(() => null);
    throw new AccountDeletionError(response.status, typeof data?.detail === "string" ? data.detail : "Account settings are temporarily unavailable. Please try again.");
  }
  return response;
}
export async function loadDeletionState(token: string, signal?: AbortSignal): Promise<DeletionState> {
  const data = await (await request(token, false, signal)).json();
  if (!data || typeof data.can_delete !== "boolean" || !["Active", "Suspended", "Deleting", "Deleted", "NoProfile"].includes(data.status)) throw new Error("Account settings returned an unexpected response.");
  return { can_delete: data.can_delete, status: data.status };
}
export async function deleteOwnAccount(token: string, confirmation: string, signal?: AbortSignal): Promise<void> {
  if (confirmation !== "DELETE") throw new Error("Type DELETE exactly to confirm account deletion.");
  const response = await request(token, true, signal);
  if (response.status !== 204) throw new Error("Account deletion could not be confirmed. Check your account status before trying again.");
}
export function deletionError(error: unknown) {
  return error instanceof TypeError ? "Could not confirm the account status. Refresh before trying again. If you can no longer sign in, contact support."
    : error instanceof Error ? error.message : "Account deletion could not be confirmed. Please try again later.";
}
