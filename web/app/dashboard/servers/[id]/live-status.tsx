"use client";

import { useCallback, useEffect, useRef, useState } from "react";

/**
 * Polls the server's reported status.
 *
 * The agent reports on its own schedule, so this only reflects what the agent last
 * said. That is the point: the panel never shows a server as running because a
 * button was clicked, and it never shows one as running after the agent has gone
 * quiet. `unavailable` here means "no agent is reporting", which is different from
 * `offline` and is shown differently.
 *
 * Polling stops while the tab is hidden, so a dashboard left open overnight does
 * not keep hammering the database.
 */

type Payload = {
  status: string;
  agentReachable: boolean;
  runtime: Record<string, unknown>;
  lastSeenAt: string | null;
  logLines: string[];
  logAt: string | null;
};

const LABELS: Record<string, string> = {
  online: "Online",
  offline: "Offline",
  starting: "Starting",
  stopping: "Stopping",
  unavailable: "No agent reporting",
  error: "Error",
  unknown: "Unknown",
};

export function LiveStatus({
  serverId,
  initialStatus,
  agentReachable,
}: {
  serverId: string;
  initialStatus: string;
  agentReachable: boolean;
}) {
  const [data, setData] = useState<Payload | null>(null);
  const [showLogs, setShowLogs] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const load = useCallback(async () => {
    try {
      const response = await fetch(`/api/servers/${serverId}/status`, { cache: "no-store" });
      if (response.ok) setData((await response.json()) as Payload);
    } catch {
      // A failed poll keeps the last known values on screen, which is more useful
      // than blanking the panel every time the network hiccups.
    } finally {
      // Chain the next poll rather than using an interval, so a slow response
      // cannot pile up overlapping requests.
      if (!document.hidden) {
        timer.current = setTimeout(load, 5000);
      }
    }
  }, [serverId]);

  useEffect(() => {
    void load();
    const onVisible = () => {
      if (!document.hidden) void load();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      document.removeEventListener("visibilitychange", onVisible);
      if (timer.current) clearTimeout(timer.current);
    };
  }, [load]);

  const status = data?.status ?? initialStatus;
  const reachable = data?.agentReachable ?? agentReachable;
  const players = data?.runtime?.players_online;
  const playersMax = data?.runtime?.players_max;

  return (
    <section className="panel">
      <div className="card-heading">
        <div>
          <h2>Status</h2>
          <p className="subtitle">
            {reachable
              ? `Reported ${relative(data?.lastSeenAt ?? null)}`
              : "This server's agent is not reporting, so its state is unknown."}
          </p>
        </div>
        <span className={`status-pill${status === "online" && reachable ? "" : " muted"}`}>
          <i /> {LABELS[status] ?? status}
        </span>
      </div>

      <div className="card-stats">
        <span>
          Players{" "}
          <b>
            {typeof players === "number"
              ? typeof playersMax === "number"
                ? `${players} / ${playersMax}`
                : String(players)
              : "unknown"}
          </b>
        </span>
        <span>
          Version <b>{String(data?.runtime?.minecraft_version ?? "—")}</b>
        </span>
        <span>
          Uptime <b>{formatDuration(data?.runtime?.uptime_seconds)}</b>
        </span>
      </div>

      <div className="button-row">
        <button
          type="button"
          className="secondary"
          onClick={() => void load()}
          aria-label="Refresh status now"
        >
          Refresh
        </button>
      </div>

      {showLogs ? <LogView payload={data} /> : null}
      <div className="button-row">
        <button
          type="button"
          className="link-button"
          onClick={() => setShowLogs((open) => !open)}
        >
          {showLogs ? "Hide log lines" : "Show last log lines"}
        </button>
      </div>
    </section>
  );
}

/**
 * Log lines the agent returned for the most recent `server.logs.tail` command.
 *
 * These are real lines from `logs/latest.log` on the server machine, read through
 * the agent. If no tail has run yet it says so rather than showing a placeholder
 * that could be mistaken for an empty log.
 */
function LogView({ payload }: { payload: Payload | null }) {
  const lines = payload?.logLines ?? [];
  return (
    <div className="stack">
      <p className="empty">
        {lines.length
          ? `From the agent, read ${relative(payload?.logAt ?? null)}.`
          : "No log tail has been fetched yet. Use “Fetch logs” on the server page."}
      </p>
      {lines.length ? (
        <pre className="log-box">{lines.join("\n")}</pre>
      ) : null}
    </div>
  );
}

function relative(timestamp: string | null): string {
  if (!timestamp) return "never";
  const parsed = Date.parse(timestamp);
  if (Number.isNaN(parsed)) return "at an unknown time";

  const seconds = Math.max(0, Math.round((Date.now() - parsed) / 1000));
  if (seconds < 10) return "just now";
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}

function formatDuration(value: unknown): string {
  if (typeof value !== "number" || value <= 0) return "—";
  const days = Math.floor(value / 86400);
  const hours = Math.floor((value % 86400) / 3600);
  const minutes = Math.floor((value % 3600) / 60);
  if (days) return `${days}d ${hours}h`;
  if (hours) return `${hours}h ${minutes}m`;
  return `${minutes}m`;
}