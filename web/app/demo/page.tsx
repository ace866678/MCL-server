"use client";

import Link from "next/link";
import { useEffect, useRef, useState, type FormEvent } from "react";

type View = "overview" | "agents" | "server";
type ConsoleLevel = "INFO" | "WARN" | "COMMAND" | "PLAYER";
type ConsoleEntry = {
  id: string;
  time: string;
  level: ConsoleLevel;
  message: string;
};

const INITIAL_LOGS: ConsoleEntry[] = [
  { id: "boot-1", time: "11:03:08", level: "INFO", message: "Starting minecraft server version 26.2" },
  { id: "boot-2", time: "11:03:08", level: "INFO", message: "Loading properties" },
  { id: "boot-3", time: "11:03:09", level: "INFO", message: "This server is running Paper version 26.2-129 (MC: 26.2)" },
  { id: "boot-4", time: "11:03:09", level: "INFO", message: "Geyser is listening for Bedrock connections on UDP port 19132" },
  { id: "boot-5", time: "11:03:10", level: "INFO", message: "Preparing level \"world\"" },
  { id: "boot-6", time: "11:03:14", level: "INFO", message: "Done (4.108s)! For help, type \"help\"" },
  { id: "boot-7", time: "11:04:02", level: "PLAYER", message: "Steve joined the game" },
  { id: "boot-8", time: "11:04:12", level: "PLAYER", message: ".Alex joined the game (Bedrock)" },
  { id: "boot-9", time: "11:04:44", level: "INFO", message: "Steve issued server command: /spawn" },
];

const QUICK_COMMANDS = ["list", "tps", "version", "save-all"];

export default function DemoPage() {
  const [view, setView] = useState<View>("overview");
  const [serverOnline, setServerOnline] = useState(true);
  const [whitelistEnabled, setWhitelistEnabled] = useState(true);
  const [notice, setNotice] = useState("Demo only: commands below are simulated in your browser and never reach a Minecraft server.");
  const [command, setCommand] = useState("");
  const [consoleLines, setConsoleLines] = useState<ConsoleEntry[]>(INITIAL_LOGS);
  const consoleEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    consoleEndRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [consoleLines]);

  function addConsoleLine(message: string, level: ConsoleLevel = "INFO") {
    const time = new Date().toLocaleTimeString("en-GB", {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
    });
    setConsoleLines((current) =>
      [...current, { id: String(Date.now()) + "-" + current.length, time, level, message }].slice(-250),
    );
  }

  function runCommand(value: string) {
    const cleaned = value.trim().replace(/^\/+/, "");
    if (!cleaned) {
      setNotice("Enter a command first.");
      return;
    }

    setNotice("Simulated locally. No command was sent to Minecraft.");
    addConsoleLine("> " + cleaned, "COMMAND");
    setCommand("");

    const parts = cleaned.split(/\s+/);
    const name = (parts[0] ?? "").toLowerCase();
    const args = parts.slice(1);
    const remainder = args.join(" ");

    switch (name) {
      case "help":
        addConsoleLine("Demo commands: list, tps, version, say <message>, time query daytime, weather query, whitelist list|on|off, save-all");
        addConsoleLine("Power, player moderation, file access, and plugin commands are disabled in this demo.", "WARN");
        break;
      case "list":
        addConsoleLine("There are 4 of a max of 20 players online: Steve, Alex, .Alex, Builder");
        break;
      case "tps":
        addConsoleLine("TPS from last 1m, 5m, 15m: 19.98, 19.99, 20.00");
        addConsoleLine("Sample performance only — no live server metrics are connected.", "WARN");
        break;
      case "version":
      case "ver":
        addConsoleLine("This server is running Paper version 26.2-129 (MC: 26.2) (Implementing API version 26.2-R0.1-SNAPSHOT)");
        addConsoleLine("Java: 25+ · Geyser: 2.11.3 · Floodgate: 2.2.5 (sample versions)");
        break;
      case "say":
        if (!remainder) {
          addConsoleLine("Usage: say <message>", "WARN");
        } else {
          addConsoleLine("[Server] " + remainder, "PLAYER");
          addConsoleLine("Message displayed to sample players only.", "WARN");
        }
        break;
      case "time":
        if (args[0]?.toLowerCase() === "query" && args[1]?.toLowerCase() === "daytime") {
          addConsoleLine("The time is 6000");
        } else {
          addConsoleLine("Only time query daytime is enabled in this demo.", "WARN");
        }
        break;
      case "weather":
        if (args[0]?.toLowerCase() === "query") {
          addConsoleLine("The weather is clear");
        } else {
          addConsoleLine("Only weather query is enabled in this demo.", "WARN");
        }
        break;
      case "whitelist":
        if (args[0]?.toLowerCase() === "list") {
          addConsoleLine("There are 2 whitelisted players: Steve, Builder");
        } else if (args[0]?.toLowerCase() === "on") {
          setWhitelistEnabled(true);
          addConsoleLine("Whitelist is now enabled (demo state only).", "WARN");
        } else if (args[0]?.toLowerCase() === "off") {
          setWhitelistEnabled(false);
          addConsoleLine("Whitelist is now disabled (demo state only).", "WARN");
        } else {
          addConsoleLine("Usage: whitelist list|on|off", "WARN");
        }
        break;
      case "save-all":
        addConsoleLine("Saving the game...");
        addConsoleLine("Saved the world");
        addConsoleLine("Demo only: no world files were written.", "WARN");
        break;
      case "stop":
      case "restart":
      case "op":
      case "deop":
      case "ban":
      case "pardon":
      case "kick":
      case "give":
      case "tp":
      case "summon":
      case "reload":
        addConsoleLine("That command is disabled in read-only demo mode. No server was changed.", "WARN");
        break;
      default:
        addConsoleLine("Unknown or unsupported demo command: " + name + ". Type help to see the sample commands.", "WARN");
    }
  }

  function submitConsole(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    runCommand(command);
  }

  function showNotice(message: string) {
    setNotice(message);
  }

  return (
    <main>
      <header className="topbar">
        <div>
          <p className="eyebrow">MCL CONTROL PLANE · DEMO</p>
          <h1>Demo workspace</h1>
          <p className="subtitle">Explore the Minecraft server panel before connecting authentication or an agent.</p>
        </div>
        <div className="button-row">
          <Link className="button-link" href="/login">Login setup</Link>
          <Link className="button-link secondary" href="/">Home</Link>
        </div>
      </header>

      <div className="notice" role="status">
        <strong>Sandbox mode.</strong> Server names, metrics, logs, players, and agent check-ins below are fictional sample data.
        Commands only update this page. Nothing here can control a real server or alter a Minecraft world.
      </div>

      <nav className="button-row" aria-label="Demo sections">
        <button className={view === "overview" ? "" : "secondary"} aria-pressed={view === "overview"} onClick={() => setView("overview")}>Overview</button>
        <button className={view === "agents" ? "" : "secondary"} aria-pressed={view === "agents"} onClick={() => setView("agents")}>Agents</button>
        <button className={view === "server" ? "" : "secondary"} aria-pressed={view === "server"} onClick={() => setView("server")}>Server details & console</button>
      </nav>

      {notice ? <div className="notice" role="status" style={{ marginTop: "1rem" }}>{notice}</div> : null}

      {view === "overview" ? (
        <>
          <section className="hero-panel">
            <div>
              <span className="status-pill"><i /> Sample network</span>
              <h2>Your Minecraft servers at a glance.</h2>
              <p>Inspect example health, player counts, agent check-ins, and a Minecraft-style terminal. The numbers are illustrative only.</p>
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
                <div><h3>Survival SMP</h3><p>Paper · Minecraft 26.2 · Home PC</p></div>
                <span className="status-pill"><i /> Online (sample)</span>
              </div>
              <div className="card-stats"><span>Players<b>4 / 20</b></span><span>Agent seen<b>12 seconds ago</b></span></div>
              <button className="secondary" onClick={() => setView("server")}>Open console</button>
            </article>
            <article className="server-card">
              <div className="card-heading">
                <div><h3>Creative Lab</h3><p>Paper · Minecraft 26.2 · Test PC</p></div>
                <span className="status-pill muted"><i /> Offline (sample)</span>
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
              <div><h2>Runtime</h2><p className="empty">Paper · Minecraft 26.2 · Sample metrics</p></div>
              <span className={"status-pill" + (serverOnline ? "" : " muted")}><i /> {serverOnline ? "Online (simulated)" : "Offline (simulated)"}</span>
            </div>
            <div className="card-stats">
              <span>Players<b>{serverOnline ? "4 / 20" : "0 / 20"}</b></span>
              <span>Java<b>25+</b></span>
              <span>Heap limit<b>4 GB</b></span>
              <span>Memory in use<b>2.1 GB</b></span>
              <span>CPU<b>18%</b></span>
              <span>Uptime<b>{serverOnline ? "2d 04h" : "Stopped"}</b></span>
              <span>Whitelist<b>{whitelistEnabled ? "Enabled" : "Disabled"}</b></span>
              <span>Public address<b>sample only</b></span>
            </div>
          </section>
          <section className="panel">
            <h2>Power controls</h2>
            <p className="empty">The real Minecraft panel uses an authenticated server bridge. Here, controls only change this demo page.</p>
            <div className="button-row">
              <button onClick={() => { setServerOnline(true); showNotice("Demo simulation: server marked online in this page only."); }}>Simulate start</button>
              <button className="secondary" onClick={() => { setServerOnline(false); showNotice("Demo simulation: server marked offline in this page only."); }}>Simulate stop</button>
              <button className="secondary" onClick={() => showNotice("Demo only: restart was not sent to a server.")}>Simulate restart</button>
              <button className="secondary" onClick={() => showNotice("Demo only: installation requires a connected local agent.")}>Install (sample)</button>
            </div>
          </section>

          <section className="panel">
            <div className="section-heading console-section-heading">
              <div><p className="eyebrow">PAPER / MINECRAFT</p><h2>Server console</h2></div>
              <span className="status-pill muted"><i /> Simulated terminal</span>
            </div>
            <p className="empty">A console layout based on the project&apos;s Minecraft admin console. Try sample commands; output is generated locally and never sent to the server.</p>
            <div className="minecraft-console">
              <div className="minecraft-console-toolbar">
                <span><i className="console-indicator" /> Survival SMP <span className="console-divider">/</span> latest.log</span>
                <button type="button" className="console-clear" onClick={() => { setConsoleLines([]); showNotice("Console output cleared in this browser only."); }}>Clear output</button>
              </div>
              <div className="minecraft-console-output" role="log" aria-label="Simulated Minecraft console output" aria-live="polite">
                {consoleLines.length ? consoleLines.map((line) => (
                  <div className="minecraft-console-line" data-level={line.level} key={line.id}>
                    <span className="console-time">[{line.time}]</span>{" "}
                    {line.level === "COMMAND" ? (
                      <span className="console-command">{line.message}</span>
                    ) : (
                      <>
                        <span className={"console-level console-level-" + line.level.toLowerCase()}>[{line.level}]</span>{" "}
                        <span>{line.message}</span>
                      </>
                    )}
                  </div>
                )) : <p className="console-empty">No output. Run a sample command below to populate the terminal.</p>}
                <div ref={consoleEndRef} />
              </div>
              <form className="minecraft-console-form" onSubmit={submitConsole}>
                <label htmlFor="demo-console-command" className="console-prompt">&gt;</label>
                <input
                  id="demo-console-command"
                  aria-label="Minecraft console command"
                  placeholder="Type a command, e.g. list"
                  value={command}
                  onChange={(event) => setCommand(event.target.value)}
                  autoComplete="off"
                  spellCheck={false}
                />
                <button type="submit">Run demo command</button>
              </form>
            </div>
            <div className="console-quick-commands">
              <span>Quick commands</span>
              {QUICK_COMMANDS.map((preset) => (
                <button key={preset} type="button" className="secondary" onClick={() => runCommand(preset)}>{preset}</button>
              ))}
              <button type="button" className="secondary" onClick={() => runCommand("help")}>help</button>
            </div>
            <p className="subtitle">Read-only demo. Commands such as stop, restart, op, ban, give, and file access are blocked. Connect the local agent and authentication before enabling a real console.</p>
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
        <p className="empty">Ready to connect real authentication later? Return to <Link href="/login">login setup</Link>. Demo activity is not saved, and commands are never sent to a live server.</p>
      </footer>
    </main>
  );
}
