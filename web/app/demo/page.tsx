"use client";

import Link from "next/link";
import { useState, type FormEvent } from "react";

type View = "overview" | "agents" | "server";

export default function DemoPage() {
  const [view, setView] = useState<View>("overview");
  const [serverOnline, setServerOnline] = useState(true);
  const [notice, setNotice] = useState("Demo only: actions below change sample UI state and never contact a real server.");
  const [command, setCommand] = useState("");

  function showNotice(message: string) {
    setNotice(message);
  }

  function submitConsole(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!command.trim()) {
      setNotice("Enter a sample command first.");
      return;
    }
    setNotice(`Demo only: “${command.trim()}” was not sent to a Minecraft server.`);
    setCommand("");
  }

  return (
    <main>
      <header className="topbar">
        <div>
          <p className="eyebrow">MCL CONTROL PLANE · DEMO</p>
          <h1>Demo workspace</h1>
          <p className="subtitle">Explore the panel without connecting an account or server.</p>
        </div>
        <div className="button-row">
          <Link className="button-link" href="/login">Login setup</Link>
          <Link className="button-link secondary" href="/">Home</Link>
        </div>
      </header>

      <div className="notice" role="status">
        <strong>Sandbox mode.</strong> All server names, metrics, agents, and activity below are fictional sample data.
        Nothing on this page can control a real server.
      </div>

      <nav className="button-row" aria-label="Demo sections">
        <button className={view === "overview" ? "" : "secondary"} aria-pressed={view === "overview"} onClick={() => setView("overview")}>Overview</button>
        <button className={view === "agents" ? "" : "secondary"} aria-pressed={view === "agents"} onClick={() => setView("agents")}>Agents</button>
        <button className={view === "server" ? "" : "secondary"} aria-pressed={view === "server"} onClick={() => setView("server")}>Server details</button>
      </nav>

      {notice ? <div className="notice" role="status" style={{ marginTop: "1rem" }}>{notice}</div> : null}

      {view === "overview" ? (
        <>
          <section className="hero-panel">
            <div>
              <span className="status-pill"><i /> Sample network</span>
              <h2>Your Minecraft servers at a glance.</h2>
              <p>Inspect example health, player counts, agent check-ins, and the server detail layout. The numbers are illustrative only.</p>
            </div>
            <button onClick={() => { setView("agents"); showNotice("Opened the sample agent list."); }}>Explore agents</button>
          </section>

          <section className="section-heading">
            <div><p className="eyebrow">YOUR INFRASTRUCTURE</p><h2>Servers</h2></div>
            <button className="secondary" onClick={() => showNotice("Demo only: creating a server is disabled until authentication and a backend are connected.")}>+ New server</button>
          </section>

          <div className="server-grid">
            <article className="server-card">
              <div className="card-heading">
                <div><h3>Survival SMP</h3><p>Paper · Minecraft 1.21.8 · Home PC</p></div>
                <span className="status-pill"><i /> Online</span>
              </div>
              <div className="card-stats"><span>Players<b>4 / 20</b></span><span>Agent seen<b>12 seconds ago</b></span></div>
              <button className="secondary" onClick={() => setView("server")}>View sample details</button>
            </article>
            <article className="server-card">
              <div className="card-heading">
                <div><h3>Creative Lab</h3><p>Paper · Minecraft 1.21.8 · Test PC</p></div>
                <span className="status-pill muted"><i /> Offline</span>
              </div>
              <div className="card-stats"><span>Players<b>0 / 10</b></span><span>Agent seen<b>18 minutes ago</b></span></div>
              <button className="secondary" onClick={() => { setServerOnline(false); setView("server"); }}>View sample details</button>
            </article>
          </div>
        </>
      ) : null}

      {view === "agents" ? (
        <>
          <section className="section-heading">
            <div><p className="eyebrow">YOUR HARDWARE</p><h2>Agents</h2></div>
            <button onClick={() => showNotice("Demo only: real agent registration needs the backend and account authentication.")}>Register agent</button>
          </section>
          <div className="server-grid">
            <article className="server-card">
              <div className="card-heading"><div><h3>Home PC</h3><p>Windows · Agent v1.0.0</p></div><span className="status-pill"><i /> Reporting</span></div>
              <div className="card-stats"><span>Last seen<b>12 seconds ago</b></span><span>Managed servers<b>1</b></span></div>
              <button className="secondary" onClick={() => showNotice("Demo only: this sample agent cannot be removed.")}>Remove agent (sample)</button>
            </article>
            <article className="server-card">
              <div className="card-heading"><div><h3>Test PC</h3><p>Linux · Agent v1.0.0</p></div><span className="status-pill muted"><i /> Awaiting agent</span></div>
              <div className="card-stats"><span>Last seen<b>18 minutes ago</b></span><span>Managed servers<b>1</b></span></div>
              <button className="secondary" onClick={() => showNotice("Demo only: this sample agent cannot be removed.")}>Remove agent (sample)</button>
            </article>
          </div>
        </>
      ) : null}

      {view === "server" ? (
        <>
          <section className="section-heading">
            <div><p className="eyebrow">SAMPLE SERVER</p><h2>Survival SMP</h2></div>
            <button className="secondary" onClick={() => setView("overview")}>Back to overview</button>
          </section>
          <section className="panel">
            <div className="card-heading">
              <div><h2>Runtime</h2><p className="empty">Paper · Minecraft 1.21.8 · Sample metrics</p></div>
              <span className={`status-pill${serverOnline ? "" : " muted"}`}><i /> {serverOnline ? "Online (simulated)" : "Offline (simulated)"}</span>
            </div>
            <div className="card-stats">
              <span>Players<b>{serverOnline ? "4 / 20" : "0 / 20"}</b></span>
              <span>Java<b>21+</b></span>
              <span>Heap limit<b>4 GB</b></span>
              <span>Memory in use<b>2.1 GB</b></span>
              <span>CPU<b>18%</b></span>
              <span>Uptime<b>{serverOnline ? "2d 04h" : "Stopped"}</b></span>
              <span>Public address<b>sample only</b></span>
            </div>
          </section>
          <section className="panel">
            <h2>Power controls</h2>
            <p className="empty">These buttons only change the sample badge on this page. They do not contact an agent.</p>
            <div className="button-row">
              <button onClick={() => { setServerOnline(true); showNotice("Demo simulation: server marked online in this page only."); }}>Simulate start</button>
              <button className="secondary" onClick={() => { setServerOnline(false); showNotice("Demo simulation: server marked offline in this page only."); }}>Simulate stop</button>
              <button className="secondary" onClick={() => showNotice("Demo only: restart was not sent to a server.")}>Simulate restart</button>
              <button className="secondary" onClick={() => showNotice("Demo only: installation requires a connected local agent.")}>Install (sample)</button>
            </div>
          </section>
          <section className="panel">
            <h2>Console</h2>
            <p className="empty">Try typing a command to test the interface. It will not be executed.</p>
            <form onSubmit={submitConsole}>
              <input aria-label="Sample console command" placeholder="e.g. list" value={command} onChange={(event) => setCommand(event.target.value)} />
              <button type="submit">Try sample command</button>
            </form>
          </section>
          <section className="panel">
            <h2>Recent activity</h2>
            <ul className="record-list">
              <li><span>Server heartbeat received</span><b>INFO</b><time>12 seconds ago</time></li>
              <li><span>Backup completed successfully</span><b>INFO</b><time>2 hours ago</time></li>
              <li><span>Player joined: Alex</span><b>INFO</b><time>3 hours ago</time></li>
            </ul>
          </section>
        </>
      ) : null}

      <footer className="panel" style={{ marginTop: "1.5rem" }}>
        <p className="empty">Ready to connect real authentication later? Return to <Link href="/login">login setup</Link>. No demo data is saved.</p>
      </footer>
    </main>
  );
}
