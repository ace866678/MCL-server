import Link from "next/link";
import { createClient } from "@/lib/supabase/server";

export const dynamic = "force-dynamic";

type ServerRow = {
  id: string;
  name: string;
  minecraft_version: string;
  server_software: string;
  agent_id: string;
  created_at: string;
};

function hasSupabaseConfig() {
  return Boolean(
    (process.env.NEXT_PUBLIC_SUPABASE_URL ?? process.env.SUPABASE_URL) &&
      (process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY ??
        process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY ??
        process.env.SUPABASE_PUBLISHABLE_KEY ??
        process.env.SUPABASE_ANON_KEY),
  );
}

export default async function Home() {
  if (!hasSupabaseConfig()) {
    return (
      <main className="landing">
        <p className="eyebrow">MCL CONTROL PLANE</p>
        <h1>Connect your own Minecraft hardware.</h1>
        <p className="hero-copy">The dashboard is ready. Supabase configuration is still being provisioned for this deployment.</p>
      </main>
    );
  }

  const supabase = await createClient();
  const { data: { user } } = await supabase.auth.getUser();

  if (!user) {
    return (
      <main className="landing">
        <div className="brand-mark">MCL<span>•</span></div>
        <p className="eyebrow">PERSONAL SERVER CONTROL</p>
        <h1>Your world, running on your hardware.</h1>
        <p className="hero-copy">A secure control plane for Minecraft servers on Windows and Linux. Connect an outbound agent, monitor health, and operate safely without exposing your router.</p>
        <Link className="button-link" href="/login">Sign in to dashboard</Link>
        <div className="feature-grid">
          <article><span className="feature-index">01</span><strong>Outbound by design</strong><span>Your device connects out. No public management port required.</span></article>
          <article><span className="feature-index">02</span><strong>Live operations</strong><span>See agent reachability and server health from one calm surface.</span></article>
          <article><span className="feature-index">03</span><strong>Owner scoped</strong><span>Supabase Row Level Security keeps infrastructure private to you.</span></article>
        </div>
      </main>
    );
  }

  const { data, error } = await supabase
    .from("servers")
    .select("id,name,minecraft_version,server_software,agent_id,created_at")
    .order("created_at", { ascending: false });
  const servers = (data ?? []) as ServerRow[];

  return (
    <main className="dashboard-shell">
      <header className="topbar">
        <div className="brand-mark">MCL<span>•</span></div>
        <div className="topbar-actions">
          <span className="user-chip">{user.email ?? "Account"}</span>
          <form action="/auth/signout" method="post"><button className="secondary" type="submit">Sign out</button></form>
        </div>
      </header>

      <section className="dashboard-intro">
        <div>
          <p className="eyebrow">OVERVIEW / {new Date().toLocaleDateString("en-US", { month: "short", day: "numeric" }).toUpperCase()}</p>
          <h1>Good to see you.</h1>
          <p className="subtitle">Your local infrastructure, in one place.</p>
        </div>
        <Link className="button-link" href="#connect-agent">Connect agent <span aria-hidden="true">↗</span></Link>
      </section>

      <section className="metric-grid" aria-label="Infrastructure summary">
        <article className="metric-card"><span className="metric-label">SERVERS</span><strong>{servers.length}</strong><span className="metric-foot">registered environments</span></article>
        <article className="metric-card"><span className="metric-label">AGENTS</span><strong>{new Set(servers.map((server) => server.agent_id)).size}</strong><span className="metric-foot">connected devices</span></article>
        <article className="metric-card"><span className="metric-label">SECURITY</span><strong className="metric-status"><i /> RLS active</strong><span className="metric-foot">owner-scoped access</span></article>
      </section>

      <section className="section-heading"><div><p className="eyebrow">YOUR INFRASTRUCTURE</p><h2>Servers</h2></div><Link className="text-link" href="#connect-agent">+ Add server</Link></section>
      {error ? <div className="notice error" role="alert">Database is not ready yet: {error.message}</div> : null}
      {servers.length > 0 ? <div className="server-grid">{servers.map((server) => <article className="server-card" key={server.id}><div className="card-heading"><div><span className="server-icon">MC</span><div><h3>{server.name}</h3><p>{server.server_software} · Minecraft {server.minecraft_version}</p></div></div><span className="status-pill muted"><i /> Awaiting agent</span></div><div className="card-stats"><span>Players <b>—</b></span><span>CPU <b>—</b></span><span>RAM <b>—</b></span></div><Link className="card-link" href="#connect-agent">Open server <span aria-hidden="true">→</span></Link></article>)}</div> : <div className="empty-state" id="connect-agent"><div className="empty-icon">+</div><div><h3>No servers yet</h3><p>Connect the agent on the device that runs Minecraft, then create your first server record.</p></div><Link className="button-link" href="#connect-agent">Connect first agent</Link></div>}
    </main>
  );
}
