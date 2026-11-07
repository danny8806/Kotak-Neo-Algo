const test = require("node:test");
const assert = require("node:assert/strict");
const { requestWithRetry } = require("../bridge/send_codex.js");

const URL = "https://receiver.example.org/api/v1/signals/incoming";

test("retries a transient network failure with the same idempotency key", async () => {
  let calls = 0;
  const keys = [];
  const response = { status: 200, ok: true };
  const result = await requestWithRetry(URL, {
    method: "POST",
    headers: { "Idempotency-Key": "stable-key" },
    body: "same payload",
  }, {
    fetchImpl: async (_url, options) => {
      calls += 1;
      keys.push(options.headers["Idempotency-Key"]);
      if (calls === 1) throw new TypeError("fetch failed", { cause: { code: "ECONNRESET" } });
      return response;
    },
    wait: async () => {},
  });

  assert.equal(result, response);
  assert.equal(calls, 2);
  assert.deepEqual(keys, ["stable-key", "stable-key"]);
});

test("retries server overload and honors bounded Retry-After", async () => {
  let calls = 0;
  const delays = [];
  const result = await requestWithRetry(URL, { method: "POST", body: "payload" }, {
    fetchImpl: async () => {
      calls += 1;
      return calls === 1
        ? { status: 503, headers: { get: () => "30" } }
        : { status: 200, ok: true };
    },
    wait: async (ms) => delays.push(ms),
  });

  assert.equal(result.status, 200);
  assert.equal(calls, 2);
  assert.deepEqual(delays, [5000]);
});

test("does not retry permanent client errors", async () => {
  let calls = 0;
  const response = { status: 401, ok: false };
  const result = await requestWithRetry(URL, { method: "POST" }, {
    fetchImpl: async () => { calls += 1; return response; },
    wait: async () => assert.fail("permanent response must not wait/retry"),
  });

  assert.equal(result, response);
  assert.equal(calls, 1);
});

test("reports bounded network failure after the final attempt", async () => {
  let calls = 0;
  await assert.rejects(requestWithRetry(URL, { method: "POST" }, {
    attempts: 3,
    fetchImpl: async () => {
      calls += 1;
      throw new TypeError("fetch failed", { cause: { code: "ETIMEDOUT" } });
    },
    wait: async () => {},
  }), /3 attempts \(TypeError, ETIMEDOUT\)/);
  assert.equal(calls, 3);
});
