import { test, afterEach } from "node:test";
import assert from "node:assert/strict";
import { AuditApiError, loadAuditPage, auditSummary } from "../src/lib/audit-api.ts";
const originalFetch = globalThis.fetch;
const originalBase = process.env.NEXT_PUBLIC_API_URL;
afterEach(() => { globalThis.fetch = originalFetch; if (originalBase === undefined) delete process.env.NEXT_PUBLIC_API_URL; else process.env.NEXT_PUBLIC_API_URL = originalBase; });
const query = { category: "", actorId: null, beforeId: null, limit: 20 };
const event = { id: 12, actor_user_id: 1, actor_username: "sample-admin", action: "property.created", entity_type: "property", entity_id: 4, details: {}, created_at: "2026-10-17T04:00:00Z" };
const page = (rows = [event], pagination = {}) => ({ audit_logs: rows, pagination: { limit: 20, has_more: false, next_before_id: null, ...pagination } });

test("credit adjustment audit includes signed change, before/after balances and reason", () => {
  const summary = auditSummary({ ...event, action: "user.credits_adjusted", details: { delta: -1, balance_before: 11, balance_after: 10, reason: "Correct extra credit" } });
  assert.ok(summary.includes("Credit adjustment: -1"));
  assert.ok(summary.includes("Previous balance: 11"));
  assert.ok(summary.includes("Credit balance: 10"));
  assert.ok(summary.includes("Reason: Correct extra credit"));
});
function mock(body, status = 200) {
  process.env.NEXT_PUBLIC_API_URL = "https://example.invalid/";
  const calls = [];
  globalThis.fetch = async (url, options) => { calls.push({ url: new URL(url), options }); return new Response(JSON.stringify(body), { status }); };
  return calls;
}
test("audit request authenticates and sends category, actor and pagination filters", async () => {
  const calls = mock(page()); const signal = new AbortController().signal;
  await loadAuditPage("sample-token", { ...query, category: "properties", actorId: 1, beforeId: 15 }, signal);
  assert.equal(calls[0].url.pathname, "/admin/audit-logs");
  assert.deepEqual(Object.fromEntries(calls[0].url.searchParams), { limit: "20", category: "properties", actor_user_id: "1", before_id: "15" });
  assert.equal(calls[0].options.headers.Authorization, "Bearer sample-token");
  assert.equal(calls[0].options.credentials, "omit"); assert.equal(calls[0].options.cache, "no-store");
  assert.equal(calls[0].options.signal, signal); assert.equal(calls[0].options.body, undefined);
});
test("invalid actor, cursor, category and limit never send requests", async () => {
  const calls = mock(page());
  for (const invalid of [{ actorId: 0 }, { actorId: 1.5 }, { beforeId: -1 }, { limit: 101 }, { limit: 0 }, { category: "unknown" }]) {
    await assert.rejects(loadAuditPage("token", { ...query, ...invalid }));
  }
  assert.equal(calls.length, 0);
});
test("username search trims and encodes text while a clicked account retains its internal ID", async () => {
  const calls = mock(page());
  await loadAuditPage("token", { ...query, actorUsername: "  Patrick_%  " });
  assert.equal(calls[0].url.searchParams.get("actor_username"), "Patrick_%");
  assert.equal(calls[0].url.searchParams.has("actor_user_id"), false);
  await loadAuditPage("token", { ...query, actorId: 1, actorUsername: "patrick" });
  assert.equal(calls[1].url.searchParams.get("actor_user_id"), "1");
  assert.equal(calls[1].url.searchParams.has("actor_username"), false);
  await assert.rejects(loadAuditPage("token", { ...query, actorUsername: "x".repeat(201) }), /200 characters/);
  assert.equal(calls.length, 2);
});
test("Admin authorization failures remain failures", async () => {
  mock({ detail: "Admin access required" }, 403);
  await assert.rejects(loadAuditPage("token", query), (error) => error instanceof AuditApiError && error.status === 403);
});
test("malformed audit entries are rejected", async () => {
  for (const invalid of [{ id: 0 }, { actor_user_id: null }, { action: "unknown" }, { details: [] }, { created_at: "not a date" }, { entity_id: -1 }]) {
    mock(page([{ ...event, ...invalid }])); await assert.rejects(loadAuditPage("token", query), /Invalid audit event/);
  }
});
test("further pages require a cursor and final empty pages are accepted", async () => {
  mock(page([event], { has_more: true })); await assert.rejects(loadAuditPage("token", query), /Invalid audit history/);
  mock(page([event], { has_more: true, next_before_id: 12 })); assert.equal((await loadAuditPage("token", query)).pagination.next_before_id, 12);
  mock(page([])); assert.deepEqual((await loadAuditPage("token", query)).audit_logs, []);
});
test("removed usernames remain readable without requiring current profile data", async () => {
  mock(page([{ ...event, actor_username: null }])); assert.equal((await loadAuditPage("token", query)).audit_logs[0].actor_user_id, 1);
});
test("import summaries show batch counts including zeros", () => {
  assert.deepEqual(auditSummary({ ...event, action: "properties.imported", details: { count: 5000, duplicates_skipped: 0, invalid_rows: 2, import_job_id: "sample-job" } }), ["Inserted: 5,000", "Duplicates skipped: 0", "Invalid rows: 2", "Import job: sample-job"]);
});
test("summaries show field names and billing effects without dumping arbitrary private metadata", () => {
  assert.deepEqual(auditSummary({ ...event, action: "property.updated", details: { fields: ["owner_name", "phone_01"], owner_name: "PRIVATE OWNER" } }), ["Updated fields: Name, Phone 01"]);
  assert.deepEqual(auditSummary({ ...event, action: "user.suspended", details: { previous_status: "Active", status: "Suspended", amount: 10, balance_after: 25 } }), ["Active → Suspended", "Credits added: 10", "Credit balance: 25"]);
  assert.deepEqual(auditSummary({ ...event, action: "properties.exported", details: { count: 9, search_used: true, listing_purpose: "SaleOrRent" } }), ["Exported: 9", "Search filter: Used", "Purpose: Sale or rent"]);
});

