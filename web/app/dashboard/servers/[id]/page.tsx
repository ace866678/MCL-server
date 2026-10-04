import Link from "next/link";
import { notFound, redirect } from "next/navigation";
import { supabaseConfigured } from "@/lib/supabase/env";
import { createClient } from "@/lib/supabase/server";
import { deleteServer } from "../../actions";
import {
  ConsoleForm,
  InstallForm,
  PowerControls,
  SettingsForm,
  WhitelistForms,
} from "./controls";
import { LiveStatus } from "./live-status";

export const dynamic = "force-dynamic";

type ServerRow = {
  id: string;
  name: string;
  minecraft_version: string;
  server_software: string;
  agent_id: string;
  port: number;
  status: string;
  runtime: Record<string, unknown> | null;
  config: Record<string, unknown> | null;
  last_seen_at: string | null;
  created_at: string;
};

type AgentRow = {
  id: string;
  name: string;
  platform: string;
  last_seen_at: string | null;
  last_error: string | null;
};

export default async function ServerPage({ params }: { params: Promise<{ id: string }> }) {
  if (!supabaseConfigured()) redirect("/");

  const { id } = await params;
  const supabase = await createClient();

  const { data } = await supabase
    .from("servers")
    .select(
      "id,name,minecraft_version,server_software,agent_id,port,status,runtime,config,last_seen_at,created_at",
    )
    .eq("id", id)
    .maybeSingle();

  // RLS means an id belonging to somebody else reads as missing, which is the
  // right answer: there is nothing to show and nothing to leak.
  if (!data) notFound();
  const server: ServerRow = data;

  const { data: agentData } = await supabase
    .from("agents")
    .select("id,name,platform,last_seen_at,last_error")
    .eq("id", server.agent_id)
    .maybeSingle();
  const agent: AgentRow | null = agentData ?? null;

  // An agent that has not been seen in three heartbeat windows is treated as
  // unreachable. Its reported status could be minutes old, and showing a stale
  // "online" next to a dead agent is the failure this platform exists to avoid.
  const agentReachable = isRecent(agent?.last_seen_at ?? null);

  const [{ data: backups }, { data: events }] = await Promise.all([
    supabase
      .from("server_backups")
      .select("filename,size_bytes,created_at")
      .eq("server_id", server.id)
      .order("created_at", { ascending: false })
      .limit(10),
    supabase
      .from("agent_events")
      .select("kind,level,message,created_at")
      .eq("server_id", server.id)
      .order("created_at", { ascending: false })
      .limit(12),
  ]);

  const runtime = server.runtime ?? {};
  const pending = server.status === "starting" || server.status === "stopping";

  return (
    <>
      <section className="section-heading">
        <div>
          <p className="eyebrow">SERVER</p>
          <h2>{server.name}</h2>
        </div>
        <Link className="button-link" href="/">
          All servers
        </Link>
      </section>

      <LiveStatus
        serverId={server.id}
        initialStatus={agentReachable ? server.status : "unavailable"}
        agentReachable={agentReachable}
      />

      <section className="panel">
        <h2>Runtime</h2>
        <RuntimeFacts runtime={runtime} />
      </section>

      <PowerControls
        serverId={server.id}
        status={agentReachable ? server.status : "unavailable"}
        disabled={!agentReachable}
      />

      <InstallForm serverId={server.id} currentVersion={server.minecraft_version} />

      <ConsoleForm serverId={server.id} />

      <SettingsForm
        serverId={server.id}
        values={flatten(server.config ?? {})}
      />

      <WhitelistForms serverId={server.id} />

      <section className="panel">
        <h2>Backups</h2>
        <p className="empty">
          Archives live on the machine running the server. This list is a record of what the agent
          created, not files held in the cloud.
        </p>
        {backups && backups.length ? (
          <ul className="record-list">
            {backups.map((backup) => (
              <li key={backup.filename}>
                <span>{backup.filename}</span>
                <b>{formatBytes(Number(backup.size_bytes ?? 0))}</b>
                <time>{new Date(backup.created_at).toLocaleString()}</time>
              </li>
            ))}
          </ul>
        ) : (
          <p className="empty">No backups recorded yet.</p>
        )}
      </section>

      <section className="panel">
        <h2>Recent activity</h2>
        {events && events.length ? (
          <ul className="record-list">
            {events.map((event, index) => (
              <li key={`${event.created_at}-${index}`}>
                <span>{event.message || event.kind}</span>
                <b>{event.level}</b>
                <time>{new Date(event.created_at).toLocaleString()}</time>
              </li>
            ))}
          </ul>
        ) : (
          <p className="empty">Nothing reported yet.</p>
        )}
      </section>

      <section className="panel">
        <h2>Agent</h2>
        {agent ? (
          <>
            <p className="empty">
              {agent.name} on {agent.platform} &mdash; last seen{" "}
              {agent.last_seen_at ? new Date(agent.last_seen_at).toLocaleString() : "never"}.
              {agent.last_error ? ` Last error: ${agent.last_error}` : ""}
            </p>
            <Link href="/dashboard/agents">Manage agents</Link>
          </>
        ) : (
          <div className="notice error">
            The agent this server points at is gone. <Link href="/dashboard/agents">Register a new
            one</Link> and recreate the server.
          </div>
        )}
      </section>

      <section className="panel">
        <h2>Remove</h2>
        <p className="empty">
          Deletes this record only. Worlds, player data and backups stay on the machine the agent
          runs on &mdash; nothing on disk is deleted.
        </p>
        <form action={deleteServer}>
          <input type="hidden" name="id" value={server.id} />
          <button type="submit" className="secondary">
            Delete server record
          </button>
        </form>
      </section>
    </>
  );
}

/** Every fact the agent measured, and a dash for anything it could not. */
function RuntimeFacts({ runtime }: { runtime: Record<string, unknown> }) {
  const rows: Array<[string, string]> = [
    ["Players online", count(runtime.players_online, runtime.players_max)],
    ["MOTD", text(runtime.motd) || "—"],
    ["Minecraft", text(runtime.minecraft_version) || "—"],
    ["Paper build", text(runtime.paper_build) || "—"],
    ["Geyser / Floodgate", pair(runtime.geyser_version, runtime.floodgate_version)],
    ["Java", text(runtime.java_major) ? `${text(runtime.java_major)}+` : "—"],
    ["Heap limit", formatBytes(Number(runtime.memory_limit_bytes ?? 0)) || "—"],
    ["Memory in use", formatBytes(Number(runtime.memory_bytes ?? 0)) || "—"],
    ["CPU", percent(runtime.cpu_percent)],
    ["Uptime", duration(Number(runtime.uptime_seconds ?? 0)) || "—"],
    ["Listening on", text(runtime.port) ? `port ${text(runtime.port)}` : "—"],
    ["Disk free", formatBytes(Number(runtime.disk_free_bytes ?? 0)) || "—"],
  ];

  return (
    <div className="card-stats">
      {rows.map(([label, value]) => (
        <span key={label}>
          {label} <b>{value}</b>
        </span>
      ))}
      <Tunnel runtime={runtime} />
    </div>
  );
}

/** How players reach the server, including when the honest answer is "unknown". */
function Tunnel({ runtime }: { runtime: Record<string, unknown> }) {
  const tunnel = runtime.tunnel;
  if (!tunnel || typeof tunnel !== "object") {
    return (
      <span>
        Public address <b>unknown</b>
      </span>
    );
  }
  const info = tunnel as Record<string, unknown>;
  const address = text(info.address);
  const reachable = info.reachable;

  let label = "unknown";
  if (reachable === true) label = address || "relay active";
  else if (reachable === false) label = "not reachable";
  else if (address) label = `${address} (local only)`;

  return (
    <span title={text(info.detail)}>
      Public address <b>{label}</b>
    </span>
  );
}

// ---- formatting ------------------------------------------------------------

function text(value: unknown): string {
  if (value === null || value === undefined) return "";
  return typeof value === "string" ? value : String(value);
}

function count(online: unknown, max: unknown): string {
  const o = typeof online === "number" ? online : null;
  const m = typeof max === "number" ? max : null;
  if (o === null) return m === null ? "—" : `unknown / ${m}`;
  return m === null ? String(o) : `${o} / ${m}`;
}

function pair(first: unknown, second: unknown): string {
  const a = text(first);
  const b = text(second);
  if (a && b) return `${a} / ${b}`;
  return a || b || "—";
}

function percent(value: unknown): string {
  return typeof value === "number" ? `${value.toFixed(1)}%` : "—";
}

function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return "";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let size = bytes;
  let unit = 0;
  while (size >= 1024 && unit < units.length - 1) {
    size /= 1024;
    unit += 1;
  }
  return `${size >= 10 || unit === 0 ? Math.round(size) : size.toFixed(1)} ${units[unit]}`;
}

function duration(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds <= 0) return "";
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  if (days) return `${days}d ${hours}h`;
  if (hours) return `${hours}h ${minutes}m`;
  return `${minutes}m`;
}

/** `server.config` is the panel's record of the operator's last settings change. */
function flatten(config: Record<string, unknown>): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [key, value] of Object.entries(config)) {
    if (typeof value === "string" || typeof value === "number") out[key] = String(value);
  }
  return out;
}

function isRecent(timestamp: string | null): boolean {
  if (!timestamp) return false;
  const seen = Date.parse(timestamp);
  if (Number.isNaN(seen)) return false;
  // Three heartbeat windows. Generous enough to survive one dropped poll and a
  // slow reload, tight enough that a dead agent does not look alive for minutes.
  return Date.now() - seen < 90_000;
}