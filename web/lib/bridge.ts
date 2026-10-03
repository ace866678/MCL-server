/**
 * Server-side client for the bridge running on the Minecraft VM.
 *
 * Everything here stays on the server: the bridge token is never sent to the
 * browser, and the pages render from data fetched during the request.
 */

export type ServerStatus = {
  running: boolean;
  pid: number | null;
  minecraftVersion: string | null;
  paperBuild: string | null;
  geyserVersion: string | null;
  floodgateVersion: string | null;
  motd: string | null;
  playersOnline: number | null;
  playersMax: number | null;
  crossplay: boolean;
  rcon: string;
};

export type Player = { name: string };

export type PlayersResult = { players: Player[]; rcon: string };

/** A bridge that is unreachable is a page, not a crash. */
export type BridgeResult<T> =
  | { ok: true; data: T; error?: never }
  | { ok: false; data: null; error: string };

const TIMEOUT_MS = 8000;

function config(): { url: string; token: string } {
  const url = process.env.MINECRAFT_BRIDGE_URL;
  const token = process.env.MINECRAFT_BRIDGE_TOKEN;
  if (!url || !token) {
    throw new Error(
      "MINECRAFT_BRIDGE_URL and MINECRAFT_BRIDGE_TOKEN are not set. Add both to the Vercel project environment.",
    );
  }
  return { url: url.replace(/\/+$/, ""), token };
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const { url, token } = config();
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
  try {
    const response = await fetch(`${url}${path}`, {
      ...init,
      signal: controller.signal,
      headers: {
        Authorization: `Bearer ${token}`,
        ...(init?.body ? { "Content-Type": "application/json" } : {}),
      },
      // Status changes slowly; a cached response for a few seconds keeps a
      // burst of visitors from hammering the bridge.
      cache: "no-store",
    });
    if (!response.ok) {
      const body = await response.text();
      throw new Error(`bridge returned ${response.status}: ${body.slice(0, 200)}`);
    }
    return (await response.json()) as T;
  } finally {
    clearTimeout(timer);
  }
}

export async function getStatus(): Promise<BridgeResult<ServerStatus>> {
  try {
    return { ok: true, data: await request<ServerStatus>("/status") };
  } catch (error) {
    return { ok: false, data: null, error: describe(error) };
  }
}

export async function getPlayers(): Promise<BridgeResult<PlayersResult>> {
  try {
    return { ok: true, data: await request<PlayersResult>("/players") };
  } catch (error) {
    return { ok: false, data: null, error: describe(error) };
  }
}

export async function sendConsoleCommand(
  command: string,
): Promise<BridgeResult<{ ok: boolean; detail: string }>> {
  try {
    return {
      ok: true,
      data: await request<{ ok: boolean; detail: string }>("/console", {
        method: "POST",
        body: JSON.stringify({ command }),
      }),
    };
  } catch (error) {
    return { ok: false, data: null, error: describe(error) };
  }
}

export async function triggerBackup(): Promise<BridgeResult<{ ok: boolean; detail: string }>> {
  try {
    return {
      ok: true,
      data: await request<{ ok: boolean; detail: string }>("/backup", { method: "POST" }),
    };
  } catch (error) {
    return { ok: false, data: null, error: describe(error) };
  }
}

function describe(error: unknown): string {
  if (error instanceof Error) {
    if (error.name === "AbortError") return "the bridge did not respond within 8s";
    return error.message;
  }
  return String(error);
}