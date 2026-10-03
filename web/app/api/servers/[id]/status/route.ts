import { NextResponse } from "next/server";
import { supabaseConfigured } from "@/lib/supabase/env";
import { createClient } from "@/lib/supabase/server";

/**
 * Live status for one server, polled by the control panel.
 *
 * Read-only, and scoped by RLS to the caller's own rows, so there is nothing here
 * a signed-in owner could use against somebody else's server. It returns only what
 * the agent last reported -- it never infers a state, and `unavailable` is a real
 * answer meaning "no agent is reporting", not "stopped".
 *
 * The most recent completed `server.logs.tail` result is included so the console
 * can show real log lines without holding a websocket open.
 */

export const dynamic = "force-dynamic";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

// An agent that has not been seen in this window is not treated as reporting.
// Three heartbeat windows: enough to ride out one dropped poll, short enough that
// a dead agent does not keep showing a stale "online".
const AGENT_STALE_MS = 90_000;

export async function GET(request: Request, { params }: { params: Promise<{ id: string }> }) {
  if (!supabaseConfigured()) {
    return NextResponse.json({ error: "Supabase is not configured" }, { status: 503 });
  }

  const { id } = await params;
  if (!UUID.test(id)) {
    return NextResponse.json({ error: "invalid server id" }, { status: 400 });
  }

  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) return NextResponse.json({ error: "not signed in" }, { status: 401 });

  const { data: server, error } = await supabase
    .from("servers")
    .select("id,status,runtime,last_seen_at")
    .eq("id", id)
    .maybeSingle();

  if (error) return NextResponse.json({ error: error.message }, { status: 500 });
  // Not this owner's server reads as missing rather than forbidden: there is
  // nothing here to disclose.
  if (!server) return NextResponse.json({ error: "not found" }, { status: 404 });

  const { data: logCommand } = await supabase
    .from("server_commands")
    .select("id,result,finished_at")
    .eq("server_id", id)
    .eq("command", "server.logs.tail")
    .in("status", ["succeeded", "failed"])
    .order("finished_at", { ascending: false })
    .limit(1)
    .maybeSingle();

  const lastSeen = server.last_seen_at ? Date.parse(server.last_seen_at) : 0;
  const agentReachable = Number.isFinite(lastSeen) && Date.now() - lastSeen < AGENT_STALE_MS;

  const result = logCommand?.result as { lines?: unknown } | null;

  return NextResponse.json(
    {
      status: agentReachable ? server.status : "unavailable",
      agentReachable,
      runtime: server.runtime ?? {},
      lastSeenAt: server.last_seen_at,
      logLines: Array.isArray(result?.lines) ? result.lines.slice(-200) : [],
      logAt: logCommand?.finished_at ?? null,
      // Sent so the browser can back off when it is merely waiting for a poll.
      serverTime: new Date().toISOString(),
    },
    { headers: { "cache-control": "no-store" } },
  );
}