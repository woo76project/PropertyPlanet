import { test, afterEach } from "node:test";
import assert from "node:assert/strict";
import { CHUNK_BYTES, MAX_IMPORT_BYTES, downloadImportFile, finishedImport, getImportJob, getInvalidRows, listImportJobs, pollImportJob, startImportJob, uploadImportFile, validateImportFile } from "../src/lib/import-api.ts";
const originalFetch = globalThis.fetch;
const originalBase = process.env.NEXT_PUBLIC_API_URL;
afterEach(() => { globalThis.fetch = originalFetch; if (originalBase === undefined) delete process.env.NEXT_PUBLIC_API_URL; else process.env.NEXT_PUBLIC_API_URL = originalBase; });
const id = "11111111-1111-4111-8111-111111111111";
const job = { id, filename: "sample.csv", file_size: 3, uploaded_bytes: 0, status: "uploading", total_records: null, processed_rows: 0, imported_count: 0, duplicate_count: 0, invalid_count: 0, error_message: null, created_at: "2026-10-17T00:00:00Z" };
async function flushTasks() { for (let i = 0; i < 40; i++) await Promise.resolve(); }

function uploadFetch(chunk) {
  process.env.NEXT_PUBLIC_API_URL = "https://example.invalid";
  const calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push({ url: new URL(url), options });
    if (options.method === "PUT") return chunk(url, options);
    if (options.method === "POST" && new URL(url).pathname.endsWith("/start")) return Response.json({ status: "queued" });
    if (options.method === "GET") return Response.json({ ...job, uploaded_bytes: 3, status: "queued" });
    return Response.json({ job, chunk_bytes: CHUNK_BYTES });
  };
  return calls;
}

test("a temporary chunk failure retries identical bytes without creating another job", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  let attempts = 0;
  const calls = uploadFetch(() => {
    if (++attempts === 1) throw new TypeError("Failed to fetch");
    return Response.json({ uploaded_bytes: 3 });
  });
  const statuses = [];
  const result = uploadImportFile(new File(["abc"], "sample.csv"), null, async () => "token", new AbortController().signal, () => {}, (status) => statuses.push(status));
  await flushTasks();
  t.mock.timers.tick(1000);
  assert.equal((await result).status, "queued");
  const chunks = calls.filter((call) => call.options.method === "PUT");
  assert.equal(chunks.length, 2);
  assert.equal(chunks[0].url.href, chunks[1].url.href);
  assert.equal(chunks[0].options.body, chunks[1].options.body);
  assert.equal(calls.filter((call) => call.options.method === "POST" && call.url.pathname === "/properties/import-jobs").length, 1);
  assert.match(statuses[0], /attempt 2 of 3/);
  assert.equal(statuses.at(-1), null);
});

test("a hung upload stops after three bounded attempts, even if fetch ignores abort", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const calls = uploadFetch(() => new Promise(() => {}));
  const signals = [];
  const result = uploadImportFile(new File(["abc"], "sample.csv"), null, async () => "token", new AbortController().signal, () => {});
  const checked = assert.rejects(result, /failed after 3 attempts.*Resume upload/);
  for (let attempt = 0; attempt < 3; attempt++) {
    await flushTasks();
    signals.push(calls.at(-1).options.signal);
    t.mock.timers.tick(60_000);
    await flushTasks();
    if (attempt < 2) t.mock.timers.tick(1000 * (attempt + 1));
  }
  await checked;
  assert.equal(calls.filter((call) => call.options.method === "PUT").length, 3);
  assert.ok(signals.every((signal) => signal.aborted));
  assert.equal(calls.some((call) => call.url.pathname.endsWith("/start")), false);
});

test("pause cancels retry backoff immediately and sends no further chunks", async () => {
  const controller = new AbortController();
  const calls = uploadFetch(() => { throw new TypeError("Disconnected"); });
  await assert.rejects(uploadImportFile(new File(["abc"], "sample.csv"), null, async () => "token", controller.signal, () => {}, () => controller.abort()), { name: "AbortError" });
  assert.equal(calls.filter((call) => call.options.method === "PUT").length, 1);
});

test("pause aborts an in-flight request without waiting for its timeout", async () => {
  const controller = new AbortController();
  let requestSignal;
  const calls = uploadFetch((_, options) => {
    requestSignal = options.signal;
    return new Promise(() => {});
  });
  const result = uploadImportFile(new File(["abc"], "sample.csv"), null, async () => "token", controller.signal, () => {});
  const checked = assert.rejects(result, { name: "AbortError" });
  await flushTasks();
  controller.abort();
  await checked;
  assert.equal(requestSignal.aborted, true);
  assert.equal(calls.filter((call) => call.options.method === "PUT").length, 1);
});

test("temporary server failures retry the chunk and recover", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  let attempts = 0;
  const calls = uploadFetch(() => ++attempts === 1
    ? Response.json({ detail: "Temporarily unavailable" }, { status: 503 })
    : Response.json({ uploaded_bytes: 3 }));
  const result = uploadImportFile(new File(["abc"], "sample.csv"), null, async () => "token", new AbortController().signal, () => {});
  await flushTasks();
  t.mock.timers.tick(1000);
  assert.equal((await result).status, "queued");
  assert.equal(calls.filter((call) => call.options.method === "PUT").length, 2);
});

test("permission and chunk-conflict failures are not retried", async () => {
  for (const status of [403, 409, 410, 413, 422]) {
    const calls = uploadFetch(() => Response.json({ detail: "Permanent upload failure" }, { status }));
    await assert.rejects(uploadImportFile(new File(["abc"], "sample.csv"), null, async () => "token", new AbortController().signal, () => {}), /Permanent upload failure/);
    assert.equal(calls.filter((call) => call.options.method === "PUT").length, 1);
  }
});

test("each chunk attempt bypasses Clerk's cached token and can recover from expiry", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  let attempts = 0;
  const options = [];
  const calls = uploadFetch(() => ++attempts === 1
    ? Response.json({ detail: "Invalid or expired session" }, { status: 401 })
    : Response.json({ uploaded_bytes: 3 }));
  const result = uploadImportFile(new File(["abc"], "sample.csv"), null, async (value) => {
    options.push(value);
    return value?.skipCache ? `fresh-${options.length}` : "cached";
  }, new AbortController().signal, () => {});
  await flushTasks();
  t.mock.timers.tick(1000);
  assert.equal((await result).status, "queued");
  const chunks = calls.filter((call) => call.options.method === "PUT");
  assert.equal(chunks.length, 2);
  assert.notEqual(chunks[0].options.headers.Authorization, chunks[1].options.headers.Authorization);
  assert.ok(chunks.every((call) => call.options.headers.Authorization.startsWith("Bearer fresh-")));
  assert.equal(options.filter((value) => value?.skipCache).length, 2);
});

test("persistent authentication rejection stops after one refresh retry", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const calls = uploadFetch(() => Response.json({ detail: "Invalid or expired session" }, { status: 401 }));
  const result = uploadImportFile(new File(["abc"], "sample.csv"), null, async () => "fresh", new AbortController().signal, () => {});
  const checked = assert.rejects(result, /Invalid or expired session/);
  await flushTasks();
  t.mock.timers.tick(1000);
  await checked;
  assert.equal(calls.filter((call) => call.options.method === "PUT").length, 2);
  assert.equal(calls.some((call) => call.url.pathname.endsWith("/start")), false);
});

test("a stalled response body is also bounded by the upload timeout", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  let attempts = 0;
  const calls = uploadFetch(() => {
    if (++attempts === 1) return { ok: true, json: () => new Promise(() => {}) };
    return Response.json({ uploaded_bytes: 3 });
  });
  const result = uploadImportFile(new File(["abc"], "sample.csv"), null, async () => "token", new AbortController().signal, () => {});
  await flushTasks();
  t.mock.timers.tick(60_000);
  await flushTasks();
  t.mock.timers.tick(1000);
  assert.equal((await result).status, "queued");
  assert.equal(calls.filter((call) => call.options.method === "PUT").length, 2);
});

test("a stalled token request times out without creating a job", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const calls = uploadFetch(() => Response.json({ uploaded_bytes: 3 }));
  const result = uploadImportFile(new File(["abc"], "sample.csv"), null, () => new Promise(() => {}), new AbortController().signal, () => {});
  const checked = assert.rejects(result, /timed out/);
  t.mock.timers.tick(30_000);
  await checked;
  assert.equal(calls.length, 0);
});
function mockResponses(responses) {
  process.env.NEXT_PUBLIC_API_URL = "https://example.invalid";
  const calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push({ url: new URL(url), options }); assert.ok(responses.length, "Unexpected extra request");
    const next = responses.shift();
    return new Response(next.raw ?? JSON.stringify(next.body ?? next), { status: next.httpStatus ?? 200 });
  };
  return calls;
}
test("file validation accepts Excel/CSV and enforces the exact byte limit", () => {
  for (const name of ["sample.csv", "SAMPLE.XLSX"]) validateImportFile({ name, size: MAX_IMPORT_BYTES });
  for (const file of [{ name: "a.xls", size: 3 }, { name: "a.csv", size: 0 }, { name: "a.csv", size: MAX_IMPORT_BYTES + 1 }]) assert.throws(() => validateImportFile(file));
});

test("streaming progress accepts committed rows before the final total is known", async () => {
  mockResponses([{ ...job, status: "processing", processed_rows: 5000, imported_count: 5000, total_records: null }]);
  const progress = await getImportJob("token", id);
  assert.equal(progress.processed_rows, 5000);
  assert.equal(progress.total_records, null);
});

test("expired uploads remain in history, stop polling and keep report access", async () => {
  const expired = { ...job, status: "expired", invalid_count: 1 };
  mockResponses([expired, { jobs: [expired], has_more: false, next_before_id: null }]);
  assert.equal(finishedImport(await getImportJob("token", id)), true);
  assert.equal((await listImportJobs("token", null)).jobs[0].status, "expired");
  mockResponses([{ body: { detail: "Upload expired. Create a new upload." }, httpStatus: 410 }]);
  await assert.rejects(startImportJob("token", id, true), /Upload expired/);
  mockResponses([{ invalid_rows: [], has_more: false }]);
  assert.deepEqual((await getInvalidRows("token", id)).rows, []);
});
test("history sends cursor and authorization, without credentials in URLs", async () => {
  const calls = mockResponses([{ jobs: [job], has_more: true, next_before_id: id }]);
  assert.equal((await listImportJobs("sample-token", id)).jobs[0].id, id);
  assert.equal(calls[0].url.searchParams.get("before_id"), id);
  assert.equal(calls[0].options.headers.Authorization, "Bearer sample-token");
  assert.equal(calls[0].options.credentials, "omit");
  assert.equal(calls[0].url.href.includes("sample-token"), false);
});
test("new uploads use raw sequential chunks then explicitly queue processing", async () => {
  const queued = { ...job, uploaded_bytes: 3, status: "queued" };
  const calls = mockResponses([{ job, chunk_bytes: CHUNK_BYTES }, { uploaded_bytes: 3 }, { job_id: id, status: "queued" }, queued]);
  let tokens = 0;
  const updates = [];
  const result = await uploadImportFile(new File(["abc"], "sample.csv"), null, async () => `token-${++tokens}`, new AbortController().signal, (value) => updates.push(value));
  assert.equal(result.status, "queued");
  assert.deepEqual(calls.map(({ options }) => options.method), ["POST", "PUT", "POST", "GET"]);
  assert.equal(await calls[1].options.body.text(), "abc");
  assert.equal(calls[1].options.headers["Content-Type"], "application/octet-stream");
  assert.equal(calls[1].url.pathname.endsWith("/chunks/0"), true);
  assert.equal(calls[2].url.pathname.endsWith("/start"), true);
  assert.equal(tokens, 3);
  assert.equal(updates[1].uploaded_bytes, 3);
});
test("resume verifies earlier chunks before appending the remaining bytes", async () => {
  const file = new File([new Uint8Array(CHUNK_BYTES), "end"], "sample.csv");
  const resumed = { ...job, file_size: file.size, uploaded_bytes: CHUNK_BYTES };
  const calls = mockResponses([resumed, { uploaded_bytes: CHUNK_BYTES }, { uploaded_bytes: file.size }, { status: "queued" }, { ...resumed, uploaded_bytes: file.size, status: "queued" }]);
  await uploadImportFile(file, resumed, async () => "sample-token", new AbortController().signal, () => {});
  assert.deepEqual(calls.filter(({ options }) => options.method === "PUT").map(({ url }) => url.pathname.split("/").at(-1)), ["0", "1"]);
  assert.equal(calls[1].options.body.size, CHUNK_BYTES);
  assert.equal(await calls[2].options.body.text(), "end");
  assert.equal(calls.some(({ url, options }) => url.pathname === "/properties/import-jobs" && options.method === "POST"), false);
});
test("a different resumed file is rejected without starting processing", async () => {
  const calls = mockResponses([job, { httpStatus: 409, body: { detail: "This chunk was already uploaded with different contents" } }]);
  await assert.rejects(uploadImportFile(new File(["xyz"], "sample.csv"), job, async () => "sample-token", new AbortController().signal, () => {}), /different contents/);
  assert.equal(calls.length, 2);
  mockResponses([]);
  await assert.rejects(uploadImportFile(new File(["abc"], "different.csv"), job, async () => "sample-token", new AbortController().signal, () => {}), /original file/);
});
test("paused uploads never create a job or send another chunk", async () => {
  const calls = mockResponses([]);
  const controller = new AbortController(); controller.abort();
  await assert.rejects(uploadImportFile(new File(["abc"], "sample.csv"), null, async () => "sample-token", controller.signal, () => {}), (e) => e.name === "AbortError");
  assert.equal(calls.length, 0);
});
test("backend permission failures remain errors", async () => {
  mockResponses([{ httpStatus: 403, body: { detail: "Admin access required" } }]);
  await assert.rejects(listImportJobs("sample-token", null), /Admin access required/);
});
test("invalid IDs and malformed progress are rejected", async () => {
  const calls = mockResponses([]);
  await assert.rejects(getImportJob("sample-token", "../other"), /Invalid import job/);
  assert.equal(calls.length, 0);
  mockResponses([{ ...job, uploaded_bytes: 4 }]);
  await assert.rejects(getImportJob("sample-token", id), /Invalid import job response/);
});
test("retry uses the retry route and reloads status", async () => {
  const calls = mockResponses([{ status: "queued" }, { ...job, status: "queued" }]);
  assert.equal((await startImportJob("sample-token", id, true)).status, "queued");
  assert.equal(calls[0].url.pathname.endsWith("/retry"), true);
  assert.equal(calls[0].options.method, "POST");
});
test("invalid-row previews and downloads use authenticated endpoints", async () => {
  const calls = mockResponses([{ invalid_rows: [{ row: 400000, errors: [{ field: "owner_name", message: "Required" }] }], has_more: false }, { raw: "Row,Field,Error\r\n400000,Owner Name,Required" }]);
  assert.equal((await getInvalidRows("sample-token", id)).rows[0].row, 400000);
  assert.equal(calls[0].url.searchParams.get("limit"), "50");
  assert.match(await (await downloadImportFile("sample-token", id)).text(), /400000/);
  assert.equal(calls[1].url.pathname.endsWith("/errors/download"), true);
});

function pollingFetch(handler) {
  process.env.NEXT_PUBLIC_API_URL = "https://example.invalid";
  let calls = 0;
  globalThis.fetch = (url, options) => handler(++calls, url, options);
  return () => calls;
}
const processingJob = { ...job, status: "processing", uploaded_bytes: 3, processed_rows: 1, imported_count: 1, total_records: 2 };

test("status polling keeps the four-second interval and stops for every terminal status", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  for (const status of ["completed", "completed_with_errors", "failed", "expired"]) {
    const calls = pollingFetch((n) => Response.json(n === 1 ? processingJob : { ...processingJob, status }));
    const updates = [];
    const stop = pollImportJob(id, async () => "token", (value) => updates.push(value), () => assert.fail("Unexpected polling error"));
    await flushTasks();
    assert.equal(calls(), 1);
    t.mock.timers.tick(3999); await flushTasks();
    assert.equal(calls(), 1);
    t.mock.timers.tick(1); await flushTasks();
    assert.equal(updates.at(-1).status, status);
    t.mock.timers.tick(60_000); await flushTasks();
    assert.equal(calls(), 2);
    stop();
  }
});

test("a network interruption retains progress and normal polling resumes after recovery", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const calls = pollingFetch((n) => {
    if (n === 2) throw new TypeError("Failed to fetch");
    return Response.json({ ...processingJob, processed_rows: n === 1 ? 1 : 2 });
  });
  const updates = [], errors = [];
  const stop = pollImportJob(id, async () => "token", (value) => updates.push(value), (...args) => errors.push(args));
  await flushTasks();
  t.mock.timers.tick(4000); await flushTasks();
  assert.equal(updates.length, 1);
  assert.equal(updates[0].processed_rows, 1);
  assert.equal(errors[0][1], true);
  t.mock.timers.tick(4000); await flushTasks();
  assert.equal(updates.at(-1).processed_rows, 2);
  t.mock.timers.tick(4000); await flushTasks();
  assert.equal(calls(), 4);
  stop();
});

test("temporary failures back off to 30 seconds, exhaust, and allow one manual restart", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const calls = pollingFetch(() => Response.json({ detail: "Unavailable" }, { status: 503 }));
  const errors = [];
  const stop = pollImportJob(id, async () => "token", () => assert.fail("No successful status"), (...args) => errors.push(args));
  await flushTasks();
  for (const delay of [4000, 8000, 16000, 30000, 30000]) {
    const previous = calls();
    t.mock.timers.tick(delay - 1); await flushTasks();
    assert.equal(calls(), previous);
    t.mock.timers.tick(1); await flushTasks();
    assert.equal(calls(), previous + 1);
  }
  assert.deepEqual(errors.map(([, retrying]) => retrying), [true, true, true, true, true, false]);
  t.mock.timers.tick(120_000); await flushTasks();
  assert.equal(calls(), 6);
  stop();
  const recovered = [];
  const restartedCalls = pollingFetch(() => Response.json(processingJob));
  const restart = pollImportJob(id, async () => "token", (value) => recovered.push(value), () => assert.fail("Recovered"));
  await flushTasks();
  t.mock.timers.tick(4000); await flushTasks();
  assert.equal(restartedCalls(), 2);
  assert.equal(recovered.length, 2);
  restart();
});

test("a timed-out status request retries and recovers", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  let firstSignal;
  const calls = pollingFetch((n, _, options) => {
    if (n === 1) { firstSignal = options.signal; return new Promise(() => {}); }
    return Response.json(processingJob);
  });
  const updates = [], errors = [];
  const stop = pollImportJob(id, async () => "token", (value) => updates.push(value), (...args) => errors.push(args));
  await flushTasks();
  t.mock.timers.tick(30_000); await flushTasks();
  assert.equal(firstSignal.aborted, true);
  assert.equal(errors[0][1], true);
  t.mock.timers.tick(4000); await flushTasks();
  assert.equal(calls(), 2);
  assert.equal(updates.length, 1);
  stop();
});

test("expired authentication retrieves a fresh Clerk token once and recovers", async () => {
  const headers = [], tokens = [], updates = [];
  const calls = pollingFetch((n, _, options) => {
    headers.push(options.headers.Authorization);
    return n === 1 ? Response.json({ detail: "Expired" }, { status: 401 }) : Response.json({ ...processingJob, status: "completed" });
  });
  const stop = pollImportJob(id, async (options) => { tokens.push(options); return options?.skipCache ? "fresh" : "cached"; }, (value) => updates.push(value), () => assert.fail("Authentication should recover"));
  await flushTasks();
  assert.equal(calls(), 2);
  assert.deepEqual(tokens, [undefined, { skipCache: true }]);
  assert.deepEqual(headers, ["Bearer cached", "Bearer fresh"]);
  assert.equal(updates.length, 1);
  stop();
});

test("persistent 401, missing sessions, and 403 stop without endless auth retries", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  for (const status of [401, 403, null]) {
    const calls = pollingFetch(() => Response.json({ detail: "Denied" }, { status: status ?? 401 }));
    const errors = [];
    let tokenCalls = 0;
    const stop = pollImportJob(id, async () => { tokenCalls++; return status === null ? null : "token"; }, () => assert.fail("Denied"), (...args) => errors.push(args));
    await flushTasks();
    t.mock.timers.tick(120_000); await flushTasks();
    assert.equal(calls(), status === null ? 0 : status === 401 ? 2 : 1);
    assert.equal(tokenCalls, status === 403 ? 1 : 2);
    assert.equal(errors.length, 1);
    assert.equal(errors[0][1], false);
    stop();
  }
});

test("unmount cancels an active status request and ignores its late response", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  let resolve, signal;
  const calls = pollingFetch((_, __, options) => { signal = options.signal; return new Promise((done) => { resolve = done; }); });
  const updates = [], errors = [];
  const stop = pollImportJob(id, async () => "token", (value) => updates.push(value), (...args) => errors.push(args));
  await flushTasks();
  stop();
  assert.equal(signal.aborted, true);
  resolve(Response.json(processingJob)); await flushTasks();
  t.mock.timers.tick(120_000); await flushTasks();
  assert.equal(calls(), 1);
  assert.equal(updates.length, 0);
  assert.equal(errors.length, 0);
});

test("switching jobs cancels the old loop and cannot publish the old job", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const nextId = "22222222-2222-4222-8222-222222222222";
  let resolveOld;
  const calls = pollingFetch((_, url) => url.endsWith(id)
    ? new Promise((done) => { resolveOld = done; })
    : Response.json({ ...processingJob, id: nextId }));
  const updates = [];
  const stopOld = pollImportJob(id, async () => "token", (value) => updates.push(value), () => assert.fail("Old request cancelled"));
  await flushTasks(); stopOld();
  const stopNext = pollImportJob(nextId, async () => "token", (value) => updates.push(value), () => assert.fail("New job valid"));
  await flushTasks();
  resolveOld(Response.json(processingJob)); await flushTasks();
  t.mock.timers.tick(4000); await flushTasks();
  assert.deepEqual(updates.map((value) => value.id), [nextId, nextId]);
  assert.equal(calls(), 3);
  stopNext();
});

test("pending requests never overlap and cancellation clears retry timers", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  let resolve;
  const calls = pollingFetch(() => new Promise((done) => { resolve = done; }));
  const stop = pollImportJob(id, async () => "token", () => {}, () => {});
  await flushTasks();
  t.mock.timers.tick(20_000); await flushTasks();
  assert.equal(calls(), 1);
  resolve(Response.json({ detail: "Temporary" }, { status: 504 })); await flushTasks();
  stop();
  t.mock.timers.tick(120_000); await flushTasks();
  assert.equal(calls(), 1);
});

test("a hung Clerk token request is bounded and cannot start a late fetch", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  let resolve;
  const calls = pollingFetch(() => assert.fail("Cancelled token must not send a request"));
  const errors = [];
  const stop = pollImportJob(id, () => new Promise((done) => { resolve = done; }), () => assert.fail("No response"), (...args) => errors.push(args));
  t.mock.timers.tick(30_000); await flushTasks();
  assert.equal(errors[0][1], true);
  stop(); resolve("late token"); await flushTasks();
  assert.equal(calls(), 0);
});
