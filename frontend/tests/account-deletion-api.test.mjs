import { test, afterEach } from "node:test";
import assert from "node:assert/strict";
import { AccountDeletionError, deleteOwnAccount, loadDeletionState } from "../src/lib/account-deletion-api.ts";
const originalFetch = globalThis.fetch;
const originalBase = process.env.NEXT_PUBLIC_API_URL;
afterEach(() => { globalThis.fetch = originalFetch; if (originalBase === undefined) delete process.env.NEXT_PUBLIC_API_URL; else process.env.NEXT_PUBLIC_API_URL = originalBase; });
function mockResponses(responses) {
  process.env.NEXT_PUBLIC_API_URL = "https://example.invalid/";
  const calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push({ url: new URL(url), options }); assert.ok(responses.length, "Unexpected extra request");
    const next = responses.shift();
    return new Response(next.httpStatus === 204 ? null : JSON.stringify(next.body ?? next), { status: next.httpStatus ?? 200 });
  };
  return calls;
}
test("account settings GET does not mutate the account and respects Admin protection", async () => {
  const calls = mockResponses([{ can_delete: false, status: "Active" }]);
  assert.equal((await loadDeletionState("sample-token")).can_delete, false);
  assert.equal(calls[0].options.method, "GET");
  assert.equal(calls[0].url.pathname, "/auth/account");
  assert.equal(calls[0].options.body, undefined);
  assert.equal(calls[0].options.headers.Authorization, "Bearer sample-token");
  assert.equal(calls[0].options.credentials, "omit");
  assert.equal(calls[0].options.cache, "no-store");
});
test("suspended, pending and incomplete accounts retain self-deletion controls", async () => {
  for (const status of ["Suspended", "Deleting", "NoProfile"]) {
    mockResponses([{ can_delete: true, status }]);
    assert.deepEqual(await loadDeletionState("sample-token"), { can_delete: true, status });
  }
});
test("wrong or missing confirmation never sends a deletion request", async () => {
  const calls = mockResponses([]);
  for (const value of ["", "delete", " DELETE ", "YES"]) await assert.rejects(deleteOwnAccount("sample-token", value), /Type DELETE exactly/);
  assert.equal(calls.length, 0);
});
test("deletion sends only DELETE confirmation and no target user identifier", async () => {
  const calls = mockResponses([{ httpStatus: 204 }]);
  await deleteOwnAccount("sample-token", "DELETE");
  assert.equal(calls[0].options.method, "DELETE");
  assert.deepEqual(JSON.parse(calls[0].options.body), { confirmation: "DELETE" });
  assert.equal(calls[0].url.pathname, "/auth/account");
  assert.equal(calls[0].url.search, "");
});
test("backend Admin protection and billing failures remain failures", async () => {
  mockResponses([{ httpStatus: 403, body: { detail: "Admin accounts require owner-managed deletion" } }]);
  await assert.rejects(deleteOwnAccount("sample-token", "DELETE"), (e) => e instanceof AccountDeletionError && e.status === 403);
  mockResponses([{ httpStatus: 503, body: { detail: "Billing cancellation failed; retry account deletion" } }]);
  await assert.rejects(deleteOwnAccount("sample-token", "DELETE"), /Billing cancellation failed/);
});
test("pending deletion and unexpected success responses do not trigger completion", async () => {
  mockResponses([{ httpStatus: 503, body: { detail: "Account deletion is pending; retry later" } }]);
  await assert.rejects(deleteOwnAccount("sample-token", "DELETE"), /deletion is pending/);
  mockResponses([{ body: { deleted: true } }]);
  await assert.rejects(deleteOwnAccount("sample-token", "DELETE"), /could not be confirmed/);
});
test("malformed eligibility responses cannot enable deletion", async () => {
  for (const body of [{ can_delete: "true", status: "Active" }, { can_delete: true, status: "Unknown" }]) {
    mockResponses([body]); await assert.rejects(loadDeletionState("sample-token"), /unexpected response/);
  }
});
