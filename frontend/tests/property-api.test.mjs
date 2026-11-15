import { test, afterEach } from "node:test";
import assert from "node:assert/strict";
import { deleteProperty, loadPropertyPage, loadPropertyTotal, normalizePropertyData, saveProperty, searchClueError, PropertyApiError } from "../src/lib/property-api.ts";

const originalFetch = globalThis.fetch;
const originalBase = process.env.NEXT_PUBLIC_API_URL;
afterEach(() => {
  globalThis.fetch = originalFetch;
  if (originalBase === undefined) delete process.env.NEXT_PUBLIC_API_URL;
  else process.env.NEXT_PUBLIC_API_URL = originalBase;
});
const profile = { role: "User", status: "Active", username: "demo", first_name: "Demo", last_name: "User", phone: "demo" };
const row = { id: 5, address: "Sample address", owner_name: "Sample owner", phone_01: "DEMO-PHONE", phone_02: null, ic_number: "DEMO-IC", remark: "Call later" };
const page = { properties: [row], pagination: { limit: 20, has_more: false, next_before_id: null } };
const query = { search: "", limit: 20, beforeId: null };

test("normal and every builder operator reject trimmed clues below three characters before sending requests", async () => {
  let calls = 0;
  globalThis.fetch = () => { calls++; throw new Error("Must not send invalid clues"); };
  for (const value of ["w", "ab", " ab "]) {
    await assert.rejects(loadPropertyPage("token", { ...query, search: value }), (e) => e.status === 422);
    for (const operator of ["equals", "starts_with", "contains", "ends_with"]) {
      assert.ok(searchClueError(value, operator));
      await assert.rejects(loadPropertyPage("token", { ...query, builder: { match: "all", conditions: [{ field: "address", operator, value }] } }), (e) => e.status === 422);
    }
  }
  assert.equal(calls, 0);
  assert.equal(searchClueError(" 吉隆坡 "), null);
  assert.equal(searchClueError("ABC%_!"), null);
  assert.ok(searchClueError("%%%"));
  assert.ok(searchClueError("a b c"));
});
test("total properties uses the authenticated summary endpoint without page filters", async () => {
  const calls = mockResponses([{ total: 500123 }]);
  assert.equal(await loadPropertyTotal("admin-token"), 500123);
  assert.equal(calls[0].url.pathname, "/properties/summary");
  assert.equal(calls[0].url.search, "");
  assert.equal(calls[0].options.headers.Authorization, "Bearer admin-token");
});

test("property totals accept zero and reject unavailable or malformed counts", async () => {
  mockResponses([{ total: 0 }]);
  assert.equal(await loadPropertyTotal("token"), 0);
  for (const total of [-1, "500000", 1.5, null]) {
    mockResponses([{ total }]);
    await assert.rejects(loadPropertyTotal("token"), /count could not be loaded/);
  }
});
function mockResponses(responses) {
  process.env.NEXT_PUBLIC_API_URL = "https://example.invalid/";
  const calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push({ url: new URL(url), options });
    assert.ok(responses.length, "Unexpected additional API request");
    const next = responses.shift();
    return new Response(next.status === 204 ? null : JSON.stringify(next.body ?? next), { status: typeof next.status === "number" ? next.status : 200 });
  };
  return calls;
}

test("free users request only previews and private fields are excluded from results", async () => {
  const calls = mockResponses([profile, { balance: 0, unlimited: false }, page]);
  const signal = new AbortController().signal;
  const result = await loadPropertyPage("sample-token", query, signal);
  assert.equal(result.mode, "free");
  assert.deepEqual(calls.map(({ url }) => url.pathname), ["/auth/me", "/credits", "/properties/preview"]);
  assert.deepEqual(result.page.properties[0], { id: row.id, address: row.address, owner_name: row.owner_name });
  for (const { options } of calls) {
    assert.equal(options.headers.Authorization, "Bearer sample-token");
    assert.equal(options.credentials, "omit");
    assert.equal(options.cache, "no-store");
    assert.equal(options.redirect, "error");
    assert.equal(options.signal, signal);
  }
});

test("a credit search uses POST and reports the charged balance", async () => {
  const calls = mockResponses([profile, { balance: 2, unlimited: false }, { ...page, balance: 1, charged: true, unlimited: false }]);
  const result = await loadPropertyPage("token", { ...query, search: "Sample", searchId: "request-1" });
  assert.equal(result.mode, "credit");
  assert.equal(result.balance, 1);
  assert.equal(result.charged, true);
  assert.equal(calls[2].url.pathname, "/properties/search");
  assert.equal(calls[2].options.method, "POST");
  assert.equal(JSON.parse(calls[2].options.body).request_id, "request-1");
  assert.equal(result.page.properties[0].owner_name, row.owner_name);
});

test("search text, page size and cursor are encoded as query parameters", async () => {
  const calls = mockResponses([profile, { balance: 0, unlimited: false }, { ...page, pagination: { ...page.pagination, limit: 50 } }]);
  const result = await loadPropertyPage("sample-token", { search: "  Condo & 50%_!  ", limit: 50, beforeId: 10 });
  assert.equal(result.mode, "free");
  assert.equal(result.charged, false);
  assert.equal(result.page.properties[0].owner_name, row.owner_name);
  assert.equal(calls[2].url.pathname, "/properties/preview");
  assert.ok(calls.every(({ options }) => options.method === "GET"));
  assert.equal(calls[2].url.searchParams.get("search"), "Condo & 50%_!");
  assert.equal(calls[2].url.searchParams.get("limit"), "50");
  assert.equal(calls[2].url.searchParams.get("before_id"), "10");
});

test("incomplete profiles stop before billing or property requests", async () => {
  const calls = mockResponses([{ ...profile, username: " " }]);
  await assert.rejects(loadPropertyPage("sample-token", query), (error) => error instanceof PropertyApiError && error.status === 428);
  assert.equal(calls.length, 1);
});

test("builder clues and request ID survive paging without starting a new search", async () => {
  const calls = mockResponses([profile, { balance: 0, unlimited: false }, { ...page, balance: 0, charged: false, unlimited: false }]);
  const builder = { match: "any", conditions: [{ field: "owner_name", operator: "equals", value: "Tan" }] };
  await loadPropertyPage("token", { ...query, builder, searchId: "existing", beforeId: 25, });
  const body = JSON.parse(calls[2].options.body);
  assert.deepEqual(body.filters, builder);
  assert.equal(body.request_id, "existing");
  assert.equal(body.before_id, 25);
});

test("free previews support public builder fields", async () => {
  const calls = mockResponses([profile, { balance: 0, unlimited: false }, page]);
  const builder = { match: "all", conditions: [{ field: "address", operator: "contains", value: "Damansara" }] };
  const result = await loadPropertyPage("sample-token", { ...query, builder });
  assert.equal(result.mode, "free");
  assert.equal(calls[2].url.pathname, "/properties/preview");
  assert.deepEqual(JSON.parse(calls[2].url.searchParams.get("filters")), builder);
});

test("owner clues never reach free preview endpoints", async () => {
  const calls = mockResponses([profile, { balance: 0, unlimited: false }]);
  const builder = { match: "all", conditions: [{ field: "owner_name", operator: "equals", value: "Tan" }] };
  await assert.rejects(loadPropertyPage("token", { ...query, builder }), /Use a search credit/);
  assert.equal(calls.length, 2);
});

test("suspended accounts stop before billing or property requests", async () => {
  const calls = mockResponses([{ ...profile, status: "Suspended" }]);
  await assert.rejects(loadPropertyPage("sample-token", query), (error) => error instanceof PropertyApiError && error.status === 403);
  assert.equal(calls.length, 1);
});

test("insufficient credits never downgrade a full search silently", async () => {
  const calls = mockResponses([profile, { balance: 0, unlimited: false }, { status: 402, body: { detail: "You need a search credit" } }]);
  await assert.rejects(loadPropertyPage("token", { ...query, search: "Sample", searchId: "new" }), (e) => e.status === 402);
  assert.equal(calls.length, 3);
});

test("other permission failures never fall back to previews", async () => {
  const calls = mockResponses([profile, { balance: 2, unlimited: true }, { status: 403, body: { detail: "Account suspended" } }]);
  await assert.rejects(loadPropertyPage("sample-token", query), /Account suspended/);
  assert.equal(calls.length, 3);
});

test("credit service failures are shown instead of guessing access", async () => {
  const calls = mockResponses([profile, { status: 503, body: { detail: "Service unavailable" } }]);
  await assert.rejects(loadPropertyPage("sample-token", query), /Service unavailable/);
  assert.equal(calls.length, 2);
});

test("malformed credit responses do not grant access", async () => {
  const calls = mockResponses([profile, { has_access: "true" }]);
  await assert.rejects(loadPropertyPage("sample-token", query), /credit balance could not be checked/);
  assert.equal(calls.length, 2);
});

test("missing next-page cursors are rejected", async () => {
  mockResponses([profile, { balance: 0, unlimited: false }, { ...page, pagination: { limit: 20, has_more: true, next_before_id: null } }]);
  await assert.rejects(loadPropertyPage("sample-token", query), /invalid pagination/);
});

test("valid next-page cursors and empty final pages are supported", async () => {
  mockResponses([profile, { balance: 0, unlimited: false }, { ...page, pagination: { limit: 20, has_more: true, next_before_id: 5 } }]);
  assert.equal((await loadPropertyPage("sample-token", query)).page.pagination.next_before_id, 5);
  mockResponses([profile, { balance: 0, unlimited: false }, { ...page, properties: [] }]);
  assert.deepEqual((await loadPropertyPage("sample-token", query)).page.properties, []);
});

test("Admin management rejects ordinary paid users before requesting property data", async () => {
  const calls = mockResponses([profile]);
  await assert.rejects(loadPropertyPage("sample-token", query, undefined, true), (e) => e instanceof PropertyApiError && e.status === 403);
  assert.equal(calls.length, 1);
});

test("Admin management loads the full record endpoint", async () => {
  const calls = mockResponses([{ ...profile, role: "Admin" }, page]);
  assert.equal((await loadPropertyPage("sample-token", query, undefined, true)).mode, "admin");
  assert.equal(calls[1].url.pathname, "/properties");
  assert.ok(calls.every(({ url }) => url.pathname !== "/credits"));
});

test("Admin browsing ignores search credit IDs and never requests a balance or charge", async () => {
  const calls = mockResponses([{ ...profile, role: "Admin" }, page]);
  const result = await loadPropertyPage("token", { ...query, search: "Garden", searchId: "old-request" });
  assert.equal(result.mode, "admin");
  assert.equal(result.unlimited, true);
  assert.equal(result.charged, false);
  assert.deepEqual(calls.map(({ url }) => url.pathname), ["/auth/me", "/properties"]);
  assert.equal(calls[1].options.method, "GET");
});

const inputs = { owner_name: "  Sample owner ", phone_01: " DEMO-PHONE ", phone_02: " ", ic_number: "", address: " Sample address ", remark: " Call later ", };
test("form normalization trims text and converts empty optional fields to null", () => {
  assert.deepEqual(normalizePropertyData(inputs), { owner_name: "Sample owner", phone_01: "DEMO-PHONE", phone_02: null, ic_number: null, address: "Sample address", remark: "Call later" });
});

test("forms reject whitespace-only required fields, excessive lengths", () => {
  assert.throws(() => normalizePropertyData({ ...inputs, owner_name: " " }), /required/);
  assert.throws(() => normalizePropertyData({ ...inputs, address: "a".repeat(1001) }), /1000/);
});

for (const [id, method, path] of [[null, "POST", "/properties"], [5, "PATCH", "/properties/5"]]) {
  test(`${method} sends only property fields and validates the saved record`, async () => {
    const calls = mockResponses([row]);
    const data = normalizePropertyData(inputs);
    assert.equal((await saveProperty("sample-token", data, id)).id, 5);
    assert.equal(calls[0].url.pathname, path);
    assert.equal(calls[0].options.method, method);
    assert.equal(calls[0].options.headers["Content-Type"], "application/json");
    assert.deepEqual(JSON.parse(calls[0].options.body), data);
  });
}

test("duplicate, permission and validation failures are surfaced", async () => {
  mockResponses([{ status: 409, body: { detail: "An identical property record already exists" } }]);
  await assert.rejects(saveProperty("sample-token", normalizePropertyData(inputs), null), /identical property/);
  mockResponses([{ status: 403, body: { detail: "Admin access required" } }]);
  await assert.rejects(deleteProperty("sample-token", 5), (e) => e instanceof PropertyApiError && e.status === 403);
  mockResponses([{ status: 422, body: { detail: [{ msg: "Invalid value" }] } }]);
  await assert.rejects(saveProperty("sample-token", normalizePropertyData(inputs), null), /details are invalid/);
});

test("delete accepts the backend's empty 204 response", async () => {
  const calls = mockResponses([{ status: 204 }]);
  await deleteProperty("sample-token", 5);
  assert.equal(calls[0].url.pathname, "/properties/5");
  assert.equal(calls[0].options.method, "DELETE");
  assert.equal(calls[0].options.body, undefined);
});

test("invalid record IDs never produce mutation requests", async () => {
  const calls = mockResponses([]);
  await assert.rejects(deleteProperty("sample-token", -1), /Invalid property ID/);
  await assert.rejects(saveProperty("sample-token", normalizePropertyData(inputs), 0), /Invalid property ID/);
  assert.equal(calls.length, 0);
});
