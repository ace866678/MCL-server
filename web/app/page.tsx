import Link from "next/link";
import { supabaseConfigured } from "@/lib/supabase/env";
import { createClient } from "@/lib/supabase/server";

export const dynamic = "force-dynamic";

type Server = {
  id: string;
  name: string;
  minecraft_version: string;
  server_software: string;
  agent_id: string;
  status: string;
  runtime: Record<string, unknown> | null;
};

type Agent = { id: string; name: string; last_seen_at: string | null };

function Landing({ children }: { children: React.ReactNode }) {
  return (
    <main className="landing">
      <p className="eyebrow">MCL CONTROL PLANE</p>
      {children}
    </main>
  );
}

export default async function Home() {
  if (!supabaseConfigured()) {
    return (
      <Landing>
        <h1>Supabase connection is being provisioned.</h1>
        <p className="hero-copy">
          The control plane is ready, but the connected Supabase environment is not available to
          this preview process yet.
        </p>
      </Landing>
    );
  }

  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();

  if (!user) {
    return (
      <Landing>
        <h1>Your Minecraft server. Your hardware.</h1>
        <p className="hero-copy">
          A secure Vercel control panel for Minecraft servers running locally on Windows or Linux.
          No paid VM required.
        </p>
        <Link className="button-link" href="/login">
          Sign in to continue
        </Link>
        <div className="feature-grid">
          <article>
            <strong>Outbound agent</strong>
            <span>Your device connects out; no router management port required.</span>
          </article>
          <article>
            <strong>Real status</strong>
            <span>Offline means the local agent is unreachable, never an optimistic fake.</span>
          </article>
          <article>
            <strong>Isolated ownership</strong>
            <span>Supabase RLS keeps every server and agent scoped to its owner.</span>
          </article>
        </div>
      </Landing>
    );
  }

  const [{ data: serverRows, error }, { data: agentRows }] = await Promise.all([
    supabase
      .from("servers")
      .select("id,name,minecraft_version,server_software,agent_id,status,runtime,created_at")
      .order("created_at", { ascending: false }),
    supabase.from("agents").select("id,name,last_seen_at"),
  ]);

  const servers: Server[] = Array.isArray(serverRows) ? serverRows : [];
  const agents = new Map<string, Agent>(
    (Array.isArray(agentRows) ? agentRows : []).map((agent) => [agent.id, agent as Agent]),
  );

  return (
    <main>
      <header className="topbar">
        <div>
          <p className="eyebrow">MCL CONTROL PLANE</p>
          <h1>Dashboard</h1>
        </div>
        <form action="/auth/signout" method="post">
          <button className="secondary">Sign out</button>
        </form>
      </header>

      <section className="hero-panel">
        <div>
          <span className="status-pill">
            <i /> Agent network
          </span>
          <h2>Run Minecraft on your own device.</h2>
          <p>
            Install the local Server Agent, register it here, and manage Paper, Geyser, Floodgate,
            logs, backups, and safe commands from one place.
          </p>
        </div>
        <Link className="button-link" href="/dashboard/agents">
          Connect an agent
        </Link>
      </section>

      <section className="section-heading">
        <div>
          <p className="eyebrow">YOUR INFRASTRUCTURE</p>
          <h2>Servers</h2>
        </div>
        <Link className="button-link" href="/dashboard/servers/new">
          New server
        </Link>
      </section>

      {error ? <div className="notice error">Database is not ready yet: {error.message}</div> : null}

      {servers.length ? (
        <div className="server-grid">
          {servers.map((server) => {
            const agent = agents.get(server.agent_id);
            // A stale agent makes the reported status meaningless, so the card
            // says "no agent reporting" rather than repeating a status that was
            // measured minutes ago and may no longer be true.
            const reachable = isRecent(agent?.last_seen_at ?? null);
            const runtime = server.runtime ?? {};
            const online = reachable && server.status === "online";
            const players =
              typeof runtime.players_online === "number"
                ? typeof runtime.players_max === "number"
                  ? `${runtime.players_online} / ${runtime.players_max}`
                  : String(runtime.players_online)
                : "unknown";
            return (
              <article className="server-card" key={server.id}>
                <div className="card-heading">
                  <div>
                    <h3>{server.name}</h3>
                    <p>
                      {server.server_software} &middot; Minecraft {server.minecraft_version}
                      {agent ? ` · ${agent.name}` : ""}
                    </p>
                  </div>
                  <span className={`status-pill${online ? "" : " muted"}`}>
                    <i />{" "}
                    {!reachable
                      ? "No agent reporting"
                      : server.status === "online"
                        ? "Online"
                        : server.status === "offline"
                          ? "Offline"
                          : server.status}
                  </span>
                </div>
                <div className="card-stats">
                  <span>
                    Players <b>{players}</b>
                  </span>
                  <span>
                    Agent seen{" "}
                    <b>
                      {agent?.last_seen_at
                        ? new Date(agent.last_seen_at).toLocaleTimeString()
                        : "never"}
                    </b>
                  </span>
                </div>
                <Link href={`/dashboard/servers/${server.id}`}>Open server</Link>
              </article>
            );
          })}
        </div>
      ) : (
        <div className="empty-state">
          <h3>No servers yet</h3>
          <p>
            Create a server record after connecting the agent that runs Minecraft on your device.
          </p>
          <Link className="button-link" href="/dashboard/agents">
            Connect your first agent
          </Link>
        </div>
      )}
    </main>
  );
}

/**
 * Whether an agent's last heartbeat is recent enough to trust.
 *
 * A server's reported status is only meaningful while the agent that reported it
 * is still around. Three heartbeat windows: enough to ride out one dropped poll,
 * tight enough that a closed laptop does not keep showing "Online".
 */
function isRecent(timestamp: string | null): boolean {
  if (!timestamp) return false;
  const seen = Date.parse(timestamp);
  if (Number.isNaN(seen)) return false;
  return Date.now() - seen < 90_000;
}
