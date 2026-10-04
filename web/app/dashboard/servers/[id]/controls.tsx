"use client";

import { useActionState } from "react";
import {
  addToWhitelist,
  applySettings,
  backupServer,
  installServer,
  removeFromWhitelist,
  requestLogTail,
  sendConsoleCommand,
  startServer,
  stopServer,
  restartServer,
  type CommandResult,
} from "./actions";

/**
 * Control panel widgets.
 *
 * Every button enqueues a command and reports "queued" -- it does not wait for the
 * server to change state. That is deliberate: the agent polls on its own schedule,
 * so a button that blocked until the server was up would hang for as long as a
 * laptop takes to find wifi. The status card above these controls is what shows
 * the real outcome, and it refreshes on its own.
 */

function Message({ result }: { result: CommandResult | null }) {
  if (!result) return null;
  return (
    <p className="result" data-ok={result.ok} role="status">
      {result.message}
    </p>
  );
}

/**
 * A plain form button for the actions whose message needs no separate surface --
 * start, stop, restart, backup and log tail all report their outcome through the
 * status card. A form action must return void, so the result is discarded here.
 */
function Action({
  action,
  serverId,
  children,
  className,
  title,
}: {
  action: (formData: FormData) => Promise<CommandResult>;
  serverId: string;
  children: React.ReactNode;
  className?: string;
  title?: string;
}) {
  const submit = async (formData: FormData) => {
    await action(formData);
  };
  return (
    <form action={submit}>
      <input type="hidden" name="server_id" value={serverId} />
      <button type="submit" className={className} title={title}>
        {children}
      </button>
    </form>
  );
}

export function PowerControls({
  serverId,
  status,
  disabled,
}: {
  serverId: string;
  status: string;
  disabled: boolean;
}) {
  // A stopped server cannot be restarted, and a running one should be stopped
  // before it is restarted; offering both at once invites a confusing no-op.
  const online = status === "online";
  const transitional = status === "starting" || status === "stopping";

  return (
    <section className="panel">
      <h2>Controls</h2>
      {disabled ? (
        <p className="empty">
          The agent for this server is not reporting, so commands will sit in the queue until it
          comes back.
        </p>
      ) : null}
      <div className="button-row">
        {!online ? (
          <Action action={startServer} serverId={serverId}>
            Start
          </Action>
        ) : (
          <Action action={stopServer} serverId={serverId} className="secondary">
            Stop
          </Action>
        )}
        {online ? (
          <Action action={restartServer} serverId={serverId} className="secondary">
            Restart
          </Action>
        ) : null}
        <Action action={backupServer} serverId={serverId} className="secondary">
          Back up now
        </Action>
      </div>
      {transitional ? (
        <p className="empty">
          The server is {status}. Status refreshes on its own every few seconds.
        </p>
      ) : null}
    </section>
  );
}

export function InstallForm({ serverId, currentVersion }: { serverId: string; currentVersion: string }) {
  const [state, action, pending] = useActionState(installServer, null);
  return (
    <section className="panel">
      <h2>Install or change version</h2>
      <p className="empty">
        The agent resolves this version against PaperMC, downloads the build, and verifies its
        SHA-256 against the digest Paper publishes before running it. Changing version replaces the
        jars; your world data is untouched.
      </p>
      <form action={action}>
        <input type="hidden" name="server_id" value={serverId} />
        <label className="field">
          Minecraft version
          <input name="minecraft_version" defaultValue={currentVersion} maxLength={20} required />
        </label>
        <label className="field">
          Minimum memory
          <input name="min_memory" placeholder="2G" maxLength={8} />
        </label>
        <label className="field">
          Maximum memory
          <input name="max_memory" placeholder="4G" maxLength={8} />
        </label>
        <button type="submit" disabled={pending}>
          {pending ? "Queueing..." : "Install"}
        </button>
      </form>
      <Message result={state} />
    </section>
  );
}

export function ConsoleForm({ serverId }: { serverId: string }) {
  const [state, action, pending] = useActionState(sendConsoleCommand, null);
  return (
    <section className="panel">
      <h2>Console</h2>
      <p className="empty">
        Sent to the running server. Only read-only and low-impact commands are accepted; anything
        that could shut the server down or run a plugin command is refused.
      </p>
      <form action={action}>
        <input type="hidden" name="server_id" value={serverId} />
        <label className="field">
          Command
          <input name="command" placeholder="say server restarting in 5 minutes" maxLength={200} />
        </label>
        <button type="submit" disabled={pending}>
          {pending ? "Sending..." : "Send"}
        </button>
      </form>
      <Message result={state} />
      <div className="button-row">
        <Action action={requestLogTail} serverId={serverId} className="secondary">
          Fetch recent logs
        </Action>
      </div>
      <p className="empty">
        Reads the last 200 lines from the server&rsquo;s <code>latest.log</code> through the agent,
        then shows them above under Status.
      </p>
    </section>
  );
}

export function WhitelistForms({ serverId }: { serverId: string }) {
  const [added, addAction, addPending] = useActionState(addToWhitelist, null);
  const [removed, removeAction, removePending] = useActionState(removeFromWhitelist, null);
  return (
    <section className="panel">
      <h2>Whitelist</h2>
      <p className="empty">
        Writes <code>whitelist.json</code> in the server directory. Paper reloads it when the world
        next saves.
      </p>
      <form action={addAction}>
        <input type="hidden" name="server_id" value={serverId} />
        <label className="field">
          Player name
          <input name="player" placeholder="Steve_01" maxLength={16} required />
        </label>
        <button type="submit" disabled={addPending}>
          {addPending ? "Queueing..." : "Add"}
        </button>
        <button type="submit" formAction={removeAction} className="secondary" disabled={removePending}>
          {removePending ? "Queueing..." : "Remove"}
        </button>
      </form>
      <Message result={added} />
      <Message result={removed} />
    </section>
  );
}

export function SettingsForm({
  serverId,
  values,
}: {
  serverId: string;
  values: Record<string, string>;
}) {
  const [state, action, pending] = useActionState(applySettings, null);
  const bool = (key: string, fallback = "true") =>
    (values[key] ?? fallback) === "true" ? "true" : "false";

  return (
    <section className="panel">
      <h2>Settings</h2>
      <p className="empty">
        Written to <code>server.properties</code> on the machine running the server. Changing the
        MOTD or whitelist takes effect on restart.
      </p>
      <form action={action}>
        <input type="hidden" name="server_id" value={serverId} />
        <label className="field">
          MOTD
          <input name="motd" defaultValue={values.motd ?? ""} maxLength={120} />
        </label>
        <label className="field">
          Max players
          <input name="max-players" defaultValue={values["max-players"] ?? "20"} inputMode="numeric" />
        </label>
        <label className="field">
          Difficulty
          <select name="difficulty" defaultValue={values.difficulty ?? "normal"}>
            {["peaceful", "easy", "normal", "hard"].map((level) => (
              <option key={level} value={level}>
                {level}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          Gamemode
          <select name="gamemode" defaultValue={values.gamemode ?? "survival"}>
            {["survival", "creative", "adventure", "spectator"].map((mode) => (
              <option key={mode} value={mode}>
                {mode}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          View distance
          <input
            name="view-distance"
            defaultValue={values["view-distance"] ?? "10"}
            inputMode="numeric"
          />
        </label>
        <label className="field checkbox">
          <input type="checkbox" name="pvp" value="true" defaultChecked={bool("pvp") === "true"} />
          PvP enabled
        </label>
        <label className="field checkbox">
          <input
            type="checkbox"
            name="online-mode"
            value="true"
            defaultChecked={bool("online-mode") === "true"}
          />
          Online mode (authentication)
        </label>
        <label className="field checkbox">
          <input
            type="checkbox"
            name="white-list"
            value="true"
            defaultChecked={bool("white-list") === "true"}
          />
          Whitelist only
        </label>
        <button type="submit" disabled={pending}>
          {pending ? "Queueing..." : "Apply settings"}
        </button>
      </form>
      <Message result={state} />
    </section>
  );
}