export type CreditAccount = { balance: number; unlimited: boolean; price_myr: string; purchase_url: string | null };
export class CreditsError extends Error {
  status: number;
  constructor(status: number, message: string) { super(message); this.status = status; }
}
export async function loadCredits(token: string, signal?: AbortSignal): Promise<CreditAccount> {
  const base = process.env.NEXT_PUBLIC_API_URL;
  if (!base) throw new Error("The credit service has not been configured yet.");
  const response = await fetch(`${base.replace(/\/$/, "")}/credits`, {
    headers: { Authorization: `Bearer ${token}` }, signal,
    credentials: "omit", cache: "no-store", redirect: "error",
  });
  const data = await response.json().catch(() => null);
  if (!response.ok) throw new CreditsError(response.status, typeof data?.detail === "string" ? data.detail : "Credits could not be loaded.");
  if (!data || !Number.isSafeInteger(data.balance) || data.balance < 0 || typeof data.unlimited !== "boolean"
    || typeof data.price_myr !== "string" || !/^\d+(\.\d{1,2})?$/.test(data.price_myr) || Number(data.price_myr) <= 0) throw new Error("Invalid credit balance response.");
  if (data.purchase_url !== null) {
    let url: URL;
    try { url = new URL(data.purchase_url); } catch { throw new Error("Invalid WhatsApp destination."); }
    if (url.protocol !== "https:" || url.hostname !== "wa.me" || url.port || url.username || url.password
      || !/^\/[1-9]\d{6,14}$/.test(url.pathname) || !url.searchParams.get("text")) throw new Error("Invalid WhatsApp destination.");
  }
  return { balance: data.balance, unlimited: data.unlimited, price_myr: data.price_myr, purchase_url: data.purchase_url };
}
