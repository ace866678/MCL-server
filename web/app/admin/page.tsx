import { getPlayers, getStatus } from "@/lib/bridge";
import { adminConfigured, isAdmin } from "@/lib/auth";
import { BackupButton, ConsoleForm, LoginForm, SignOutButton } from "./admin-controls";

export const dynamic = "force-dynamic";

export default async function AdminPage() {
  const signedIn = await isAdmin();
  // Only ask the bridge once there is a session to answer: an unauthenticated
  // visitor gets nothing out of it, and each call opens an RCON connection.
  const [status, players] = signedIn
    ? await Promise.all([getStatus(), getPlayers()])
    : [null, null];

  return (
    <>
      <h1>Server admin</h1>
      <p className="subtitle">
        Backups and console commands. Everything here runs as the <code>minecraft</code> user on
        the game server.
      </p>

      {!adminConfigured() ? (
        <div className="notice error">
          <code>ADMIN_PASSWORD</code> is not set on this deployment, so the controls are disabled.
          Add it in the Vercel project environment variables and redeploy.
        </div>
      ) : null}

      {!signedIn ? (
        <section className="panel">
          <h2>Sign in</h2>
          <LoginForm />
        </section>
      ) : (
        <>
          <section className="panel">
            <h2>State</h2>
            {status?.ok ? (
              <p className="empty">
                Server is {status.data.running ? "running" : "stopped"}
                {status.data.pid ? ` (pid ${status.data.pid})` : ""}.
              </p>
            ) : (
              <p className="empty">Bridge unreachable: {status?.error ?? "not checked"}</p>
            )}
            <SignOutButton />
          </section>

          <section className="panel">
            <h2>Players online</h2>
            {players?.ok ? (
              players.data.players.length ? (
                <p className="empty">
                  {players.data.players.map((player) => player.name).join(", ")}
                </p>
              ) : (
                <p className="empty">Nobody is connected.</p>
              )
            ) : (
              <p className="empty">
                Player counts come from RCON: {players?.error ?? "not checked"}
              </p>
            )}
          </section>

          <section className="panel">
            <h2>Backup</h2>
            <p className="empty">
              Stops the server, zips the worlds and player data into <code>backups/</code>, and
              starts it again. Expect a minute or two of downtime.
            </p>
            <BackupButton />
          </section>

          <section className="panel">
            <h2>Console</h2>
            <p className="empty">
              Runs one server command. The bridge only accepts its allowlist, so anything else is
              rejected before it reaches the server.
            </p>
            <ConsoleForm />
          </section>
        </>
      )}
    </>
  );
}