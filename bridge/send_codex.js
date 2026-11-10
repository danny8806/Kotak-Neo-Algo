#!/usr/bin/env node
// Send a Codex-generated report through the existing authenticated bridge.
// The bridge remains the authority for schema, date, universe, source, and deadline checks.
const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");

const root = path.resolve(__dirname, "..");
const envPath = path.join(root, ".env");
const reportPath = process.argv[2];
const MAX_ATTEMPTS = 3;
const RETRY_DELAYS_MS = [750, 1500];

function fail(message) {
  process.stderr.write(`NOT DELIVERED: ${message}\n`);
  process.exit(2);
}

function readEnv(file) {
  const values = {};
  for (const raw of fs.readFileSync(file, "utf8").split(/\r?\n/)) {
    const line = raw.trim();
    if (!line || line.startsWith("#")) continue;
    const at = line.indexOf("=");
    if (at < 0) continue;
    values[line.slice(0, at).trim()] = line.slice(at + 1).trim().replace(/^(['"])(.*)\1$/, "$2");
  }
  return values;
}

function canonicalize(value) {
  if (Array.isArray(value)) return value.map(canonicalize);
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.keys(value).sort().map((key) => [key, canonicalize(value[key])]));
  }
  return value;
}

function retryDelay(response, attempt) {
  const retryAfter = response?.headers?.get?.("retry-after");
  if (retryAfter) {
    const seconds = Number(retryAfter);
    if (Number.isFinite(seconds) && seconds >= 0) return Math.min(seconds * 1000, 5000);
    const dateDelay = Date.parse(retryAfter) - Date.now();
    if (Number.isFinite(dateDelay) && dateDelay > 0) return Math.min(dateDelay, 5000);
  }
  return RETRY_DELAYS_MS[Math.min(attempt - 1, RETRY_DELAYS_MS.length - 1)];
}

function transientStatus(status) {
  return status === 408 || status === 425 || status === 429 || status >= 500;
}

function safeNetworkReason(error) {
  const code = error?.cause?.code;
  return typeof code === "string" && /^[A-Z0-9_]+$/.test(code)
    ? `${error.name || "network error"}, ${code}`
    : (error?.name || "network error");
}

async function requestWithRetry(url, options, {
  fetchImpl = fetch,
  attempts = MAX_ATTEMPTS,
  timeoutMs = 15_000,
  wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
} = {}) {
  let lastError;
  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    let response;
    try {
      response = await fetchImpl(url, { ...options, signal: AbortSignal.timeout(timeoutMs) });
    } catch (error) {
      lastError = error;
      if (attempt === attempts) {
        throw new Error(`bridge request failed after ${attempts} attempts (${safeNetworkReason(error)})`);
      }
      await wait(RETRY_DELAYS_MS[Math.min(attempt - 1, RETRY_DELAYS_MS.length - 1)]);
      continue;
    }
    if (!transientStatus(response.status) || attempt === attempts) return response;
    await wait(retryDelay(response, attempt));
  }
  throw new Error(`bridge request failed (${safeNetworkReason(lastError)})`);
}

async function main() {
  if (!reportPath) fail("usage: node bridge/send_codex.js <report.json>");
  if (!fs.existsSync(envPath)) fail("project .env is missing");
  const env = readEnv(envPath);
  const endpoint = env.SIGNAL_BRIDGE_URL || "";
  const token = env.SIGNAL_BRIDGE_TOKEN || "";
  let url;
  try { url = new URL(endpoint); } catch { fail("SIGNAL_BRIDGE_URL is invalid"); }
  if (url.protocol !== "https:" || url.username || url.password || url.search || url.hash ||
      url.pathname.replace(/\/$/, "") !== "/api/v1/signals/incoming") {
    fail("SIGNAL_BRIDGE_URL must be the HTTPS signals/incoming endpoint");
  }
  if (token.length < 32) fail("SIGNAL_BRIDGE_TOKEN is missing or too short");

  let report;
  try { report = JSON.parse(fs.readFileSync(path.resolve(reportPath), "utf8")); }
  catch { fail("report file is missing or invalid JSON"); }
  if (!report || typeof report !== "object" || !/^\d{4}-\d{2}-\d{2}$/.test(report.session_date || "")) {
    fail("report must include a valid session_date");
  }
  const raw = Buffer.from(JSON.stringify(canonicalize(report)), "utf8");
  if (raw.length > 256_000) fail("report exceeds the bridge size limit");
  const digest = crypto.createHash("sha256").update(raw).digest("hex");

  let response;
  try {
    response = await requestWithRetry(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": `Bearer ${token}`,
        "Idempotency-Key": `codex-${report.session_date}-${digest.slice(0, 24)}`,
      },
      body: raw,
    });
  } catch (error) {
    fail(error.message || `bridge request failed (${error.name || "network error"})`);
  }
  let acknowledgement;
  try { acknowledgement = await response.json(); }
  catch { fail(`bridge returned HTTP ${response.status} with an invalid response`); }
  if (!response.ok || !["ACCEPTED", "DUPLICATE"].includes(acknowledgement.status) ||
      acknowledgement.session_date !== report.session_date ||
      acknowledgement.sha256 !== digest) {
    const detail = typeof acknowledgement.detail === "string" ? acknowledgement.detail : `HTTP ${response.status}`;
    fail(detail);
  }
  process.stdout.write(`${acknowledgement.status} ${acknowledgement.session_date} ${acknowledgement.sha256}\n`);
}

if (require.main === module) {
  main().catch((error) => fail(error.message || error.name || "sender error"));
}

module.exports = { requestWithRetry, transientStatus, safeNetworkReason };
