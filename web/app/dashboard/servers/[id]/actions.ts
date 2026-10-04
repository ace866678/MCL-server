"use server";

import { revalidatePath } from "next/cache";
import { createClient } from "@/lib/supabase/server";

/**
 * Server actions for one server's control panel.
 *
 * Every one of these enqueues a command rather than doing anything directly. The
 * browser cannot touch the machine running Minecraft -- only the agent there can,
 * over an authenticated outbound connection -- so "start" here means "ask the
 * agent to start it", and the panel reflects the result only once the agent has
 * reported back.
 *
 * Ownership is enforced in SQL by `enqueue_command`, which re-reads the caller's
 * own row through `auth.uid()`. The checks here are for readable error messages,
 * not for security.
 */

export type CommandResult = { ok: boolean; message: string };

/** The ten values `server_commands.command`'s CHECK constraint accepts. */
const COMMANDS = [
  "server.start",
  "server.stop",
  "server.restart",
  "server.install",
  "server.backup",
  "server.settings.apply",
  "server.whitelist.add",
  "server.whitelist.remove",
  "server.console",
  "server.logs.tail",
] as const;

type CommandName = (typeof COMMANDS)[number];

/** Properties the agent will accept. Anything else never leaves the browser. */
const WRITABLE_PROPERTIES = new Set([
  "motd",
  "max-players",
  "gamemode",
  "difficulty",
  "pvp",
  "online-mode",
  "white-list",
  "view-distance",
  "simulation-distance",
]);

async function caller() {
  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  return user ? { supabase, user } : null;
}

async function enqueue(
  serverId: string,
  command: CommandName,
  args: Record<string, unknown> = {},
): Promise<CommandResult> {
  const session = await caller();
  if (!session) return { ok: false, message: "Sign in again." };

  const { data, error } = await session.supabase.rpc("enqueue_command", {
    p_server_id: serverId,
    p_command: command,
    p_args: args,
  });

  if (error) {
    // `enqueue_command` raises with a specific message for each refusal; those
    // are written for the user, so they are surfaced rather than replaced.
    return { ok: false, message: friendly(error.message) };
  }

  revalidatePath(`/dashboard/servers/${serverId}`);
  return {
    ok: true,
    message: `${label(command)} queued. It runs when the agent next reports in.`,
    // Not in the public type: used only to refresh the pending row.
    ...(data ? { commandId: data } : {}),
  } as CommandResult;
}

function friendly(message: string): string {
  const text = message.toLowerCase();
  if (text.includes("already pending")) {
    return "A command is already waiting for this server. Wait for it to finish, then try again.";
  }
  if (text.includes("not found")) return "That server does not exist, or is not yours.";
  if (text.includes("not available")) return "This server's agent is revoked or missing.";
  if (text.includes("not signed in")) return "Sign in again.";
  return message;
}

function label(command: CommandName): string {
  return command.replace(/^server\./, "").replace(/\./g, " ");
}

// ---- lifecycle -------------------------------------------------------------

export async function startServer(formData: FormData): Promise<CommandResult> {
  return enqueue(id(formData), "server.start");
}

export async function stopServer(formData: FormData): Promise<CommandResult> {
  return enqueue(id(formData), "server.stop");
}

export async function restartServer(formData: FormData): Promise<CommandResult> {
  return enqueue(id(formData), "server.restart");
}

/**
 * Install, optionally re-pinning to a different Minecraft version.
 *
 * The agent resolves the version against PaperMC at install time and verifies the
 * jar's SHA-256 against the digest Paper publishes, so a version chosen here can
 * never install an unverified build.
 */
export async function installServer(
  _previous: CommandResult | null,
  formData: FormData,
): Promise<CommandResult> {
  const serverId = id(formData);
  const version = String(formData.get("minecraft_version") ?? "").trim();
  const minMemory = String(formData.get("min_memory") ?? "").trim();
  const maxMemory = String(formData.get("max_memory") ?? "").trim();

  const args: Record<string, unknown> = {};
  if (version) args.minecraft_version = version;
  if (minMemory) args.min_memory = minMemory;
  if (maxMemory) args.max_memory = maxMemory;

  return enqueue(serverId, "server.install", args);
}

export async function backupServer(formData: FormData): Promise<CommandResult> {
  const keep = Number(formData.get("keep") ?? 5);
  return enqueue(id(formData), "server.backup", {
    label: "manual",
    keep: Number.isFinite(keep) ? Math.trunc(keep) : 5,
  });
}

// ---- console ---------------------------------------------------------------

/**
 * One console command. The agent re-validates it against its own allowlist --
 * this list is for a fast, readable rejection, not for security.
 */
export async function sendConsoleCommand(
  _previous: CommandResult | null,
  formData: FormData,
): Promise<CommandResult> {
  const line = String(formData.get("command") ?? "").trim().replace(/^\//, "");
  if (!line) return { ok: false, message: "Type a command first." };
  if (line.length > 200) return { ok: false, message: "Commands are limited to 200 characters." };

  const verb = line.split(" ")[0]?.toLowerCase() ?? "";
  if (!CONSOLE_ALLOWLIST.has(verb)) {
    return {
      ok: false,
      message: `/${verb} is not available here. Try: ${[...CONSOLE_ALLOWLIST].join(", ")}.`,
    };
  }
  return enqueue(id(formData), "server.console", { command: line });
}

/** Mirrors the agent's allowlist so the browser can say no before queueing. */
const CONSOLE_ALLOWLIST = new Set([
  "list",
  "help",
  "help2",
  "tps",
  "seed",
  "difficulty",
  "gamerule",
  "time",
  "weather",
  "whitelist",
  "kick",
  "ban",
  "ban-ip",
  "pardon",
  "pardon-ip",
  "op",
  "deop",
  "msg",
  "tell",
  "sendmessage",
  "say",
  "gamemode",
  "tp",
  "effect",
  "attribute",
  "clear",
  "give",
  "save-all",
  "save-off",
  "save-on",
  "ticking",
]);

// ---- whitelist -------------------------------------------------------------

export async function addToWhitelist(
  _previous: CommandResult | null,
  formData: FormData,
): Promise<CommandResult> {
  const player = String(formData.get("player") ?? "").trim();
  const invalid = validatePlayer(player);
  if (invalid) return { ok: false, message: invalid };
  return enqueue(id(formData), "server.whitelist.add", { player });
}

export async function removeFromWhitelist(
  _previous: CommandResult | null,
  formData: FormData,
): Promise<CommandResult> {
  const player = String(formData.get("player") ?? "").trim();
  const invalid = validatePlayer(player);
  if (invalid) return { ok: false, message: invalid };
  return enqueue(id(formData), "server.whitelist.remove", { player });
}

function validatePlayer(player: string): string | null {
  if (player.length < 3 || player.length > 16) {
    return "Player names are between 3 and 16 characters.";
  }
  if (!/^[A-Za-z0-9_]+$/.test(player)) {
    return "Player names may only contain letters, digits and underscores.";
  }
  return null;
}

// ---- settings --------------------------------------------------------------

/**
 * Apply server.properties changes.
 *
 * The value is written into `servers.config` here, which is the panel's record of
 * the operator's intent, and the change itself is queued as a command. The agent
 * rewrites the properties file server side, so an unknown key can never reach the
 * machine -- the allowlist below is a second, independent check.
 */
export async function applySettings(
  _previous: CommandResult | null,
  formData: FormData,
): Promise<CommandResult> {
  const session = await caller();
  if (!session) return { ok: false, message: "Sign in again." };

  const serverId = id(formData);
  const updates: Record<string, string> = {};

  for (const key of WRITABLE_PROPERTIES) {
    const raw = formData.get(key);
    if (raw === null) continue; // an unticked checkbox or absent field
    const value = String(raw).trim();
    if (value) updates[key] = value;
  }

  const message = validateProperties(updates);
  if (message) return { ok: false, message };
  if (!Object.keys(updates).length) {
    return { ok: false, message: "Change something first." };
  }

  const { error } = await session.supabase
    .from("servers")
    .update({ config: updates })
    .eq("id", serverId);
  if (error) return { ok: false, message: `Could not save: ${error.message}` };

  return enqueue(serverId, "server.settings.apply", { properties: updates });
}

function validateProperties(updates: Record<string, string>): string | null {
  for (const [key, value] of Object.entries(updates)) {
    if (value.length > 200) return `${key} is too long.`;
    // Control characters are refused everywhere a value reaches a file, a
    // console, or a terminal. A MOTD with an escape sequence in it is put in
    // front of every player who joins.
    if (/[\u0000-\u001f\u007f]/.test(value)) return `${key} contains control characters.`;
    if (key === "max-players") {
      if (!/^\d+$/.test(value)) return "Max players must be a whole number.";
      const players = Number(value);
      if (players < 1 || players > 1000) return "Max players must be between 1 and 1000.";
    }
    if (["pvp", "online-mode", "white-list"].includes(key) && !["true", "false"].includes(value)) {
      return `${key} must be true or false.`;
    }
    if (["view-distance", "simulation-distance"].includes(key)) {
      if (!/^\d+$/.test(value)) return `${key} must be a whole number.`;
    }
  }
  return null;
}

// ---- logs ------------------------------------------------------------------

/**
 * Ask the agent for the tail of `logs/latest.log`.
 *
 * This is a command like any other, so the agent reads the file where it lives
 * rather than the panel reaching across the network for it. The result comes back
 * on the command row, which the status endpoint serves to the console.
 */
export async function requestLogTail(formData: FormData): Promise<CommandResult> {
  const lines = Number(formData.get("lines") ?? 200);
  return enqueue(id(formData), "server.logs.tail", {
    lines: Number.isFinite(lines) ? Math.min(1000, Math.max(1, Math.trunc(lines))) : 200,
  });
}

// ---- helpers ---------------------------------------------------------------

function id(formData: FormData): string {
  const value = String(formData.get("server_id") ?? "");
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value)) {
    throw new Error("invalid server id");
  }
  return value;
}