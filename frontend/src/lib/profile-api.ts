export type Profile = {
  role?: "Admin" | "User";
  username: string | null;
  email: string;
  first_name: string | null;
  last_name: string | null;
  phone: string | null;
};

export type ProfileDetails = Pick<Profile, "username" | "first_name" | "last_name" | "phone">;

export function profileIsComplete(profile: Profile) {
  return [profile.username, profile.first_name, profile.last_name, profile.phone]
    .every((value) => typeof value === "string" && value.trim().length > 0);
}

export async function requestProfile(token: string, details?: ProfileDetails, signal?: AbortSignal): Promise<Profile> {
  const apiUrl = process.env.NEXT_PUBLIC_API_URL;
  if (!apiUrl) throw new Error("The account service has not been configured yet.");
  const response = await fetch(`${apiUrl.replace(/\/$/, "")}/auth/profile`, {
    method: details ? "PATCH" : "POST",
    headers: { Authorization: `Bearer ${token}`, ...(details ? { "Content-Type": "application/json" } : {}) },
    body: details ? JSON.stringify(details) : undefined,
    credentials: "omit", cache: "no-store", redirect: "error", signal,
  });
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    const fallback: Record<number, string> = {
      401: "Your session has expired. Please sign in again.",
      403: "Your account is not available. Please contact support.",
      404: "Your profile could not be found. Please reload the page.",
      409: "That username is unavailable. Choose another username.",
      422: "Please check your profile details.",
    };
    throw new Error(typeof data?.detail === "string" ? data.detail
      : fallback[response.status] ?? "The account service is temporarily unavailable. Please try again.");
  }
  if (!data || typeof data.email !== "string" || !["username", "first_name", "last_name", "phone"]
    .every((field) => data[field] === null || typeof data[field] === "string")) {
    throw new Error("The account service returned an unexpected response. Please try again.");
  }
  return data;
}

export function profileError(error: unknown) {
  return error instanceof TypeError ? "Unable to reach the account service. Please try again later."
    : error instanceof Error ? error.message : "Your profile could not be saved. Please try again.";
}
