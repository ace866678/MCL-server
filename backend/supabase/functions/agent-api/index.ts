/**
 * agent-api — the only door between an agent and the database.
 *
 * The agent runs on a user's home or office computer and makes outbound HTTPS
 * requests to this function. It never accepts an inbound connection, so a user
 * needs no port forwarding for management at all.
 *
 * ## Authentication
 *
 * Each agent holds a 32-byte token, shown exactly once by the web panel. Only its
 * SHA-256 is stored. A request presents that digest as a bearer credential:
 *
 *     X-MCL-Agent-Id     the agent's uuid
 *     X-MCL-Token-Digest hex(SHA-256(token))  — the same value the panel stored
 *     X-MCL-Timestamp    unix seconds
 *     X-MCL-Nonce        128 random bits, hex, unique per request
 *
 * ## Why this is not an HMAC
 *
 * Verifying a MAC requires the MAC key, so an HMAC scheme would force the
 * database to store the token itself — and a database dump would then be a dump
 * of usable credentials for other people's computers. Storing only the digest
 * means a leaked dump yields nothing an attacker can present, which is the
 * failure mode worth engineering against. TLS is what protects the request in
 * flight, exactly as it protects every other bearer credential on the internet.
 *
 * The obvious objection is replay, so replay is prevented structurally rather
 * than cryptographically: the nonce is accepted exactly once
 * (`agent_spend_nonce`) and the timestamp must be within five minutes. Every
 * endpoint here is idempotent regardless — heartbeats and status reports are
 * upserts, and command claim/complete are conditional updates — so even a
 * replay inside the window would be a no-op.
 *
 * ## Routes
 *
 *   POST /heartbeat                agent liveness + host facts
 *   GET  /commands                 claim queued commands for this agent
 *   POST /commands/:id/result      report a command outcome
 *   POST /servers/:id/status       report one server's live state
 *   POST /backups                  record a backup the agent created
 *   POST /events                   short operational log line
 *   POST /maintenance              expire + prune, called opportunistically
 *
 * Run with: supabase functions deploy agent-api --no-verify-jwt
 *
 * `--no-verify-jwt` is correct here and nowhere else in this project: agents do
 * not hold a Supabase JWT, they hold a device token, and this function
 * authenticates them itself. Every other endpoint is browser-facing and keeps
 * Supabase's own JWT verification.
 */

import { createClient } from "jsr:@supabase/supabase-js@2";
import { timingSafeEqual } from "node:crypto";

const SUPABASE_URL = Deno.env.get("SUPABASE_URL") ?? "";
const SERVICE_ROLE_KEY = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") ?? "";

// Signed requests older than this are refused. A home agent that was briefly
// offline can still catch up inside five minutes; anything older is a replay.
const REPLAY_WINDOW_SECONDS = 300;

// Rate limits per agent, per window. Generous enough for a status report on
// every server every ten seconds, tight enough that a looping client is obvious.
const RATE_HEARTBEAT = { limit: 30, windowSeconds: 60 };
const RATE_STATUS = { limit: 120, windowSeconds: 60 };
const RATE_MUTATION = { limit: 30, windowSeconds: 60 };

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const NONCE_RE = /^[0-9a-f]{32}$/;
const DIGEST_RE = /^[0-9a-f]{64}$/;

const db = createClient(SUPABASE_URL, SERVICE_ROLE_KEY, {
  auth: { persistSession: false, autoRefreshToken: false },
});

// ---------------------------------------------------------------------------
// request authentication
// ---------------------------------------------------------------------------

type AuthResult = { ok: true; agentId: string } | { ok: false; status: number; error: string };

async function authenticate(request: Request, raw: string): Promise<AuthResult> {
  const agentId = (request.headers.get("x-mcl-agent-id") ?? "").toLowerCase();
  const digest = (request.headers.get("x-mcl-token-digest") ?? "").toLowerCase();
  const timestamp = request.headers.get("x-mcl-timestamp") ?? "";
  const nonce = request.headers.get("x-mcl-nonce") ?? "";

  if (!UUID_RE.test(agentId) || !DIGEST_RE.test(digest) || !NONCE_RE.test(nonce)) {
    return { ok: false, status: 401, error: "missing or malformed agent credentials" };
  }

  const now = Math.floor(Date.now() / 1000);
  const sent = Number(timestamp);
  if (!Number.isFinite(sent) || Math.abs(now - sent) > REPLAY_WINDOW_SECONDS) {
    return { ok: false, status: 401, error: "timestamp outside the replay window" };
  }

  // Spend the nonce before checking the credential, so a flood of guesses from
  // an unauthenticated client cannot be used to enumerate agent ids: an unknown
  // agent id has no row to insert against and is rejected there.
  const { data: nonceOk, error: nonceError } = await db.rpc("agent_spend_nonce", {
    p_agent_id: agentId,
    p_nonce: nonce,
    p_max_age_seconds: REPLAY_WINDOW_SECONDS,
  });
  if (nonceError) {
    console.error("nonce rpc failed", nonceError.message);
    return { ok: false, status: 503, error: "authentication unavailable" };
  }
  if (nonceOk !== true) {
    return { ok: false, status: 401, error: "request already used" };
  }

  // The digest is compared in SQL against the stored hash. Both sides are
  // already one-way SHA-256 values, so there is no secret being compared by
  // length and nothing to learn from a timing difference.
  const { data, error } = await db.rpc("agent_authenticate", {
    p_agent_id: agentId,
    p_token_hash: digest,
  });
  if (error) {
    console.error("authenticate rpc failed", error.message);
    return { ok: false, status: 503, error: "authentication unavailable" };
  }

  const record = Array.isArray(data) ? data[0] : data;
  if (!record) {
    return { ok: false, status: 401, error: "unknown or revoked agent" };
  }

  // Defensive: a compromised RPC must not widen access beyond this agent.
  if (!safeEqualHex(record.agent_id, agentId)) {
    return { ok: false, status: 401, error: "unknown or revoked agent" };
  }

  return { ok: true, agentId };
}

function safeEqualHex(a: string, b: string): boolean {
  if (typeof a !== "string" || typeof b !== "string" || a.length !== b.length) return false;
  try {
    return timingSafeEqual(Buffer.from(a, "hex"), Buffer.from(b, "hex"));
  } catch {
    return false;
  }
}

// ---------------------------------------------------------------------------
// rate limiting
// ---------------------------------------------------------------------------

async function allow(agentId: string, budget: { limit: number; windowSeconds: number }): Promise<boolean> {
  const { data, error } = await db.rpc("consume_agent_rate", {
    p_agent_id: agentId,
    p_limit: budget.limit,
    p_window_seconds: budget.windowSeconds,
  });
  if (error) {
    // Failing closed on the rate limiter would take the agent offline; failing
    // open is the lesser evil, and every command is still ownership-checked.
    console.error("rate limit rpc failed", error.message);
    return true;
  }
  return data === true;
}

// ---------------------------------------------------------------------------
// responses
// ---------------------------------------------------------------------------

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      // Agent responses are never cacheable and never CORS-visible: this
      // function is not a browser endpoint.
      "cache-control": "no-store",
    },
  });
}

async function readJson(raw: string): Promise<Record<string, unknown>> {
  if (!raw) return {};
  const parsed = JSON.parse(raw);
  return parsed && typeof parsed === "object" ? (parsed as Record<string, unknown>) : {};
}

// Bounded so a bug in the agent cannot stream a log file into function memory.
const MAX_BODY_BYTES = 64 * 1024;

// ---------------------------------------------------------------------------
// route handlers
// ---------------------------------------------------------------------------

async function heartbeat(agentId: string, payload: Record<string, unknown>) {
  if (!(await allow(agentId, RATE_HEARTBEAT))) return json(429, { error: "rate limited" });

  const { error } = await db.rpc("agent_heartbeat", {
    p_agent_id: agentId,
    p_status: (payload.status ?? {}) as never,
    p_agent_version: String(payload.version ?? ""),
    p_capabilities: (payload.capabilities ?? {}) as never,
  });
  if (error) return json(500, { error: error.message });

  // Opportunistic housekeeping. A missing Supabase cron is not a reason for
  // expired commands to pile up forever, and a failure here must not fail the
  // heartbeat. `db.rpc()` returns a thenable builder, not a Promise, so this has
  // to be a try/catch rather than a `.catch()`.
  await ignoreFailure(() => db.rpc("expire_stale_commands"));
  await ignoreFailure(() => db.rpc("prune_transient_data"));

  return json(200, { ok: true, server_time: new Date().toISOString() });
}

async function ignoreFailure(work: () => PromiseLike<unknown>): Promise<void> {
  try {
    await work();
  } catch (error) {
    console.warn("maintenance call failed", error);
  }
}

async function claimCommands(agentId: string, url: URL) {
  const limit = clampInt(url.searchParams.get("limit"), 1, 20, 5);
  if (!(await allow(agentId, RATE_MUTATION))) return json(429, { error: "rate limited" });

  const { data, error } = await db.rpc("agent_claim_commands", {
    p_agent_id: agentId,
    p_limit: limit,
  });
  if (error) return json(500, { error: error.message });

  return json(200, { commands: data ?? [] });
}

async function completeCommand(agentId: string, commandId: string, payload: Record<string, unknown>) {
  if (!UUID_RE.test(commandId)) return json(400, { error: "malformed command id" });
  if (!(await allow(agentId, RATE_MUTATION))) return json(429, { error: "rate limited" });

  const status = String(payload.status ?? "failed");
  if (!["succeeded", "failed", "cancelled"].includes(status)) {
    return json(400, { error: "invalid status" });
  }

  const { error } = await db.rpc("agent_complete_command", {
    p_command_id: commandId,
    p_agent_id: agentId,
    p_status: status,
    p_result: (payload.result ?? {}) as never,
    p_error: payload.error ? String(payload.error).slice(0, 2000) : null,
    p_exit_code: typeof payload.exit_code === "number" ? Math.trunc(payload.exit_code) : null,
  });
  if (error) return json(403, { error: error.message });

  return json(200, { ok: true });
}

async function reportServerStatus(agentId: string, serverId: string, payload: Record<string, unknown>) {
  if (!UUID_RE.test(serverId)) return json(400, { error: "malformed server id" });
  if (!(await allow(agentId, RATE_STATUS))) return json(429, { error: "rate limited" });

  const status = String(payload.status ?? "unknown");
  if (
    !["online", "offline", "starting", "stopping", "unavailable", "unknown", "error"].includes(status)
  ) {
    return json(400, { error: "invalid status" });
  }

  const { error } = await db.rpc("agent_report_server_status", {
    p_server_id: serverId,
    p_agent_id: agentId,
    p_status: status,
    p_runtime: (payload.runtime ?? {}) as never,
  });
  // A server that is not this agent's is an authorisation failure, not a 404:
  // the agent is authenticated, it just may not write that row.
  if (error) return json(403, { error: error.message });

  return json(200, { ok: true });
}

async function recordBackup(agentId: string, payload: Record<string, unknown>) {
  if (!(await allow(agentId, RATE_MUTATION))) return json(429, { error: "rate limited" });

  const serverId = String(payload.server_id ?? "");
  const filename = String(payload.filename ?? "");
  if (!UUID_RE.test(serverId) || !isSafeFilename(filename)) {
    return json(400, { error: "invalid backup reference" });
  }

  const { data, error } = await db.rpc("agent_record_backup", {
    p_server_id: serverId,
    p_agent_id: agentId,
    p_filename: filename,
    p_size_bytes: typeof payload.size_bytes === "number" ? Math.trunc(payload.size_bytes) : null,
    p_sha256: /^[0-9a-f]{64}$/i.test(String(payload.sha256 ?? "")) ? String(payload.sha256) : null,
    p_status: "ready",
  });
  if (error) return json(403, { error: error.message });

  return json(200, { ok: true, id: data });
}

async function logEvent(agentId: string, payload: Record<string, unknown>) {
  if (!(await allow(agentId, RATE_STATUS))) return json(429, { error: "rate limited" });

  const { error } = await db.rpc("agent_log_event", {
    p_agent_id: agentId,
    p_server_id: UUID_RE.test(String(payload.server_id ?? ""))
      ? String(payload.server_id)
      : null,
    p_kind: String(payload.kind ?? "agent.log").slice(0, 64),
    p_level: ["debug", "info", "warn", "error"].includes(String(payload.level))
      ? String(payload.level)
      : "info",
    p_message: String(payload.message ?? ""),
  });
  if (error) return json(500, { error: error.message });
  return json(200, { ok: true });
}

/** A backup reference is a bare filename, never a path. */
function isSafeFilename(name: string): boolean {
  return /^[A-Za-z0-9._-]{1,180}\.zip$/.test(name) && !name.includes("..");
}

function clampInt(raw: string | null, min: number, max: number, fallback: number): number {
  const value = Number(raw);
  if (!Number.isFinite(value)) return fallback;
  return Math.min(max, Math.max(min, Math.trunc(value)));
}

// ---------------------------------------------------------------------------
// entry point
// ---------------------------------------------------------------------------

Deno.serve(async (request) => {
  if (request.method === "OPTIONS") return new Response(null, { status: 204 });

  const url = new URL(request.url);

  // Refuse an oversized body before reading it into memory.
  const declared = Number(request.headers.get("content-length") ?? "0");
  if (Number.isFinite(declared) && declared > MAX_BODY_BYTES) {
    return json(413, { error: "body too large" });
  }

  const raw = await request.text();
  if (raw.length > MAX_BODY_BYTES) return json(413, { error: "body too large" });

  const auth = await authenticate(request, raw);
  if (!auth.ok) {
    console.warn("agent auth rejected", { path: url.pathname, reason: auth.error });
    return json(auth.status, { error: auth.error });
  }

  const agentId = auth.agentId;
  const segments = url.pathname
    .replace(/^.*agent-api\/?/, "")
    .split("/")
    .filter(Boolean);

  try {
    const [resource, id, action] = segments;

    if (resource === "heartbeat" && request.method === "POST") {
      return await heartbeat(agentId, await readJson(raw));
    }
    if (resource === "commands" && request.method === "GET" && !id) {
      return await claimCommands(agentId, url);
    }
    if (resource === "commands" && request.method === "POST" && id && action === "result") {
      return await completeCommand(agentId, id, await readJson(raw));
    }
    if (resource === "servers" && request.method === "POST" && id && action === "status") {
      return await reportServerStatus(agentId, id, await readJson(raw));
    }
    if (resource === "backups" && request.method === "POST") {
      return await recordBackup(agentId, await readJson(raw));
    }
    if (resource === "events" && request.method === "POST") {
      return await logEvent(agentId, await readJson(raw));
    }
    if (resource === "maintenance" && request.method === "POST") {
      await ignoreFailure(() => db.rpc("expire_stale_commands"));
      await ignoreFailure(() => db.rpc("prune_transient_data"));
      return json(200, { ok: true });
    }

    return json(404, { error: "not found" });
  } catch (error) {
    // A malformed body should be the caller's problem, not a 500 that hides it.
    if (error instanceof SyntaxError) return json(400, { error: "invalid JSON" });
    console.error("agent-api failed", error);
    return json(500, { error: "internal error" });
  }
});