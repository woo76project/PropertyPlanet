import { test, afterEach } from "node:test";
import assert from "node:assert/strict";
import { loadCredits, CreditsError } from "../src/lib/credits-api.ts";
import { assignUserCredits, adjustUserCredits } from "../src/lib/admin-users-api.ts";
const originalFetch = globalThis.fetch;
const originalBase = process.env.NEXT_PUBLIC_API_URL;
afterEach(() => { globalThis.fetch = originalFetch; if (originalBase === undefined) delete process.env.NEXT_PUBLIC_API_URL; else process.env.NEXT_PUBLIC_API_URL = originalBase; });
const account = { balance: 0, unlimited: false, price_myr: "1.00", purchase_url: "https://wa.me/12025550123?text=Hi%20I%20want%20credits" };
function mock(data, status = 200) {
  process.env.NEXT_PUBLIC_API_URL = "https://example.invalid";
  const calls = [];
  globalThis.fetch = async (url, options) => { calls.push({ url, options }); return new Response(JSON.stringify(data), { status }); };
  return calls;
}
test("zero balances and configured WhatsApp messages are supported", async () => {
  const calls = mock(account);
  assert.deepEqual(await loadCredits("token"), account);
  assert.equal(calls[0].url, "https://example.invalid/credits");
  assert.equal(calls[0].options.credentials, "omit");
  assert.equal(calls[0].options.cache, "no-store");
});
test("a missing WhatsApp contact is explicit", async () => {
  mock({ ...account, purchase_url: null });
  assert.equal((await loadCredits("token")).purchase_url, null);
});
test("malformed balances and unsafe purchase destinations are rejected", async () => {
  for (const data of [{ ...account, balance: -1 }, { ...account, balance: "10" }, { ...account, price_myr: "NaN" },
    { ...account, purchase_url: "javascript:alert(1)" }, { ...account, purchase_url: "https://wa.me.evil.invalid/12025550123?text=Hi" }]) {
    mock(data); await assert.rejects(loadCredits("token"));
  }
});
test("permission failures remain failures", async () => {
  mock({ detail: "Complete your profile first" }, 403);
  await assert.rejects(loadCredits("token"), (e) => e instanceof CreditsError && e.status === 403);
});
test("assignments send their retry ID with the positive amount and reference", async () => {
  const calls = mock({ balance: 25, assigned: 10, duplicate: false });
  assert.deepEqual(await assignUserCredits("token", 2, 10, " transfer ", "same-id"), { balance: 25, unlimited: false });
  assert.equal(calls[0].options.method, "POST");
  assert.deepEqual(JSON.parse(calls[0].options.body), { request_id: "same-id", amount: 10, reference: "transfer" });
});
test("assignment client validation prevents invalid requests", async () => {
  const calls = mock({});
  for (const amount of [0, -1, 1.5]) await assert.rejects(assignUserCredits("token", 2, amount, "transfer", "id"));
  await assert.rejects(assignUserCredits("token", 2, 1, " ", "id"));
  assert.equal(calls.length, 0);
});

const adjustment = { request_id: "same-adjustment", direction: "subtract", amount: 1, reason: " Correct extra credit ", expected_balance: 11 };
test("adjustment sends the reviewed balance, signed direction, reason and stable request ID", async () => {
  const calls = mock({ balance: 10, adjusted: -1, duplicate: false });
  assert.deepEqual(await adjustUserCredits("token", 2, adjustment), { balance: 10, unlimited: false });
  assert.equal(calls[0].url, "https://example.invalid/admin/users/2/credits/adjustments");
  assert.equal(calls[0].options.method, "POST");
  assert.deepEqual(JSON.parse(calls[0].options.body), { ...adjustment, reason: "Correct extra credit" });
  mock({ balance: 8, adjusted: -1, duplicate: true });
  assert.deepEqual(await adjustUserCredits("token", 2, adjustment), { balance: 8, unlimited: false });
});

test("invalid credit adjustments cannot send requests", async () => {
  const calls = mock({});
  for (const change of [{ amount: 0 }, { amount: -1 }, { amount: 1.5 }, { amount: 1000001 },
    { direction: "set" }, { amount: 12 }, { reason: " " }, { expected_balance: -1 },
    { direction: "add", expected_balance: 1000000000 }]) {
    await assert.rejects(adjustUserCredits("token", 2, { ...adjustment, ...change }));
  }
  assert.equal(calls.length, 0);
});

test("adjustment failures and mismatched success responses stay failures", async () => {
  for (const data of [{ balance: -1, adjusted: -1, duplicate: false }, { balance: 11, adjusted: -1, duplicate: false },
    { balance: 10, adjusted: 1, duplicate: false }, { balance: 10, adjusted: -1 }]) {
    mock(data); await assert.rejects(adjustUserCredits("token", 2, adjustment));
  }
  mock({ detail: "Balance changed. Refresh and review again." }, 409);
  await assert.rejects(adjustUserCredits("token", 2, adjustment), /Balance changed/);
});
