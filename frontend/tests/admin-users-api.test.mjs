import { test, afterEach } from "node:test";
import assert from "node:assert/strict";
import { AdminUsersError, canChangeUser, changeUserStatus, loadAdminUsers, loadUserCredits } from "../src/lib/admin-users-api.ts";
const originalFetch = globalThis.fetch;
const originalBase = process.env.NEXT_PUBLIC_API_URL;
afterEach(() => { globalThis.fetch = originalFetch; if (originalBase === undefined) delete process.env.NEXT_PUBLIC_API_URL; else process.env.NEXT_PUBLIC_API_URL = originalBase; });
const user = { id: 2, username: "sample", email: "sample@example.invalid", first_name: "Sample", last_name: "User", phone: "DEMO-PHONE", role: "User", status: "Active", created_at: "2026-10-17T00:00:00Z", updated_at: "2026-10-17T00:00:00Z" };
const query = { search: "", status: "", beforeId: null, limit: 20 };
function mockResponses(responses) {
  process.env.NEXT_PUBLIC_API_URL = "https://example.invalid";
  const calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push({ url: new URL(url), options }); assert.ok(responses.length, "Unexpected request");
    const next = responses.shift();
    return new Response(JSON.stringify(next.body ?? next), { status: next.httpStatus ?? 200 });
  };
  return calls;
}
test("user search, status, page size and cursor are encoded as query parameters", async () => {
  const calls = mockResponses([{ users: [user], pagination: { limit: 50, has_more: true, next_before_id: 2 } }]);
  const result = await loadAdminUsers("sample-token", { search: "  Name & 50%_  ", status: "Suspended", limit: 50, beforeId: 10 });
  assert.equal(result.pagination.next_before_id, 2);
  assert.deepEqual(Object.fromEntries(calls[0].url.searchParams), { limit: "50", search: "Name & 50%_", status: "Suspended", before_id: "10" });
  assert.equal(calls[0].options.headers.Authorization, "Bearer sample-token");
  assert.equal(calls[0].options.cache, "no-store");
  assert.equal(calls[0].options.credentials, "omit");
});
test("Admin, deleting and deleted accounts are protected from status controls", () => {
  assert.equal(canChangeUser(user), true);
  assert.equal(canChangeUser({ ...user, status: "Suspended" }), true);
  assert.equal(canChangeUser({ ...user, role: "Admin" }), false);
  for (const status of ["Deleting", "Deleted"]) assert.equal(canChangeUser({ ...user, status }), false);
});
test("incomplete and deleted profiles can be displayed without inventing missing data", async () => {
  mockResponses([{ users: [{ ...user, username: null, first_name: null, last_name: null, phone: null, status: "Deleted" }], pagination: { limit: 20, has_more: false, next_before_id: null } }]);
  const result = await loadAdminUsers("sample-token", query);
  assert.equal(result.users[0].username, null);
  assert.equal(result.users[0].status, "Deleted");
});
test("permission failures do not return a user list", async () => {
  mockResponses([{ httpStatus: 403, body: { detail: "Admin access required" } }]);
  await assert.rejects(loadAdminUsers("sample-token", query), (e) => e instanceof AdminUsersError && e.status === 403);
});
test("malformed users and pagination are rejected", async () => {
  for (const body of [
    { users: [user], pagination: { limit: 20, has_more: true, next_before_id: null } },
    { users: [{ ...user, role: "Staff" }], pagination: { limit: 20, has_more: false, next_before_id: null } },
  ]) { mockResponses([body]); await assert.rejects(loadAdminUsers("sample-token", query)); }
});
test("subscription lookup retrieves only the selected user's summary", async () => {
  const billing = { balance: 25, unlimited: false };
  const calls = mockResponses([billing]);
  assert.deepEqual(await loadUserCredits("sample-token", 2), billing);
  assert.equal(calls[0].url.pathname, "/admin/users/2/credits");
  assert.equal(calls[0].options.method, "GET");
});
test("status changes use PATCH with only the requested status", async () => {
  for (const status of ["Suspended", "Active"]) {
    const calls = mockResponses([{ ...user, status }]);
    assert.equal((await changeUserStatus("sample-token", 2, status)).status, status);
    assert.equal(calls[0].options.method, "PATCH");
    assert.equal(calls[0].url.pathname, "/admin/users/2/status");
    assert.deepEqual(JSON.parse(calls[0].options.body), { status });
  }
});
test("billing cancellation failures stay failures and are not reported as suspension success", async () => {
  mockResponses([{ httpStatus: 503, body: { detail: "Suspension could not be completed because billing cancellation failed; retry" } }]);
  await assert.rejects(changeUserStatus("sample-token", 2, "Suspended"), /billing cancellation failed/);
});
test("invalid targets, status values and mismatched mutation responses are rejected", async () => {
  const calls = mockResponses([]);
  await assert.rejects(loadUserCredits("sample-token", 0), /Invalid user ID/);
  await assert.rejects(changeUserStatus("sample-token", 2, "Deleted"), /Active or Suspended/);
  assert.equal(calls.length, 0);
  mockResponses([{ ...user, id: 3, status: "Suspended" }]);
  await assert.rejects(changeUserStatus("sample-token", 2, "Suspended"), /Could not confirm/);
});
