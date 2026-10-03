import { getPlayers, getStatus } from "@/lib/bridge";

export const dynamic = "force-dynamic";

export default async function Home() {
  const [status, players] = await Promise.all([getStatus(), getPlayers()]);

  if (!status.ok) {
    return (
      <>
        <h1>Minecraft Server</h1>
        <p className="subtitle">Paper + Geyser &mdash; Java and Bedrock cross-play</p>
        <div className="panel">
          <div className="badge down">
            <span className="dot" />
            Bridge unreachable
          </div>
          <div className="notice error" style={{ marginTop: "1rem", marginBottom: 0 }}>
            {status.error}
          </div>
          <p className="motd">
            This page cannot reach the bridge on the game server. Check that it is running
            (<code>systemctl status minecraft-bridge</code>) and that the tunnel and the bridge URL
            in the Vercel environment variables are current.
          </p>
        </div>
      </>
    );
  }

  const server = status.data;
  const online = server.playersOnline ?? 0;
  const max = server.playersMax;
  const names = players.ok ? players.data.players : [];

  return (
    <>
      <h1>Minecraft Server</h1>
      <p className="subtitle">Paper + Geyser &mdash; Java and Bedrock cross-play</p>

      <section className="panel">
        <span className={server.running ? "badge up" : "badge down"}>
          <span className="dot" />
          {server.running ? "Online" : "Offline"}
        </span>

        {server.motd ? <p className="motd">{server.motd}</p> : null}

        <div className="grid">
          <div className="stat">
            <div className="label">Players</div>
            <div className="value">
              {max ? `${online} / ${max}` : online}
            </div>
          </div>
          <div className="stat">
            <div className="label">Minecraft</div>
            <div className="value">{server.minecraftVersion ?? "?"}</div>
          </div>
          <div className="stat">
            <div className="label">Paper</div>
            <div className="value">build {server.paperBuild ?? "?"}</div>
          </div>
          <div className="stat">
            <div className="label">Bedrock port</div>
            <div className="value">19132/udp</div>
          </div>
          <div className="stat">
            <div className="label">Java port</div>
            <div className="value">25565/tcp</div>
          </div>
          <div className="stat">
            <div className="label">Geyser</div>
            <div className="value">{server.geyserVersion ?? "?"}</div>
          </div>
        </div>

        {server.rcon !== "ok" ? (
          <div className="notice" style={{ marginTop: "1.25rem", marginBottom: 0 }}>
            Player counts are approximate: RCON reports &ldquo;{server.rcon}&rdquo;. The running
            state above comes from the process, so it stays accurate either way.
          </div>
        ) : null}
      </section>

      <section className="panel">
        <h2>Online now</h2>
        {names.length > 0 ? (
          <ul className="players">
            {names.map((player) => (
              <li key={player.name}>{player.name}</li>
            ))}
          </ul>
        ) : (
          <p className="empty">Nobody is online right now.</p>
        )}
      </section>

      <footer>
        <p>
          Java: <code>{process.env.MINECRAFT_JOIN_ADDRESS ?? "your-server-host"}</code> on 25565
          &nbsp;&middot;&nbsp; Bedrock on 19132 (UDP) &mdash; Bedrock usernames appear with a leading
          dot.
        </p>
      </footer>
    </>
  );
}