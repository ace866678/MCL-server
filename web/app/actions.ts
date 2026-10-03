"use server";

import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";
import { sendConsoleCommand, triggerBackup } from "@/lib/bridge";
import { checkPassword, clearSessionCookie, isAdmin, setSessionCookie } from "@/lib/auth";

export type ActionResult = { ok: boolean; message: string };

/**
 * Console commands the admin page offers. The bridge enforces its own
 * allowlist as well; this list is what a human is invited to pick from.
 */
const CONSOLE_PRESETS = ["list", "tps", "save-all", "whitelist on", "whitelist off"];

export async function login(_previous: ActionResult | null, formData: FormData): Promise<ActionResult> {
  const password = String(formData.get("password") ?? "");
  if (!checkPassword(password)) {
    return { ok: false, message: "That password is not right." };
  }
  await setSessionCookie();
  redirect("/admin");
}

export async function logout(): Promise<void> {
  await clearSessionCookie();
  redirect("/admin");
}

export async function backup(_previous: ActionResult | null): Promise<ActionResult> {
  if (!(await isAdmin())) {
    return { ok: false, message: "Not signed in." };
  }
  const result = await triggerBackup();
  if (!result.ok) return { ok: false, message: result.error };
  // A backup stops the server, so the status on screen is now stale.
  revalidatePath("/");
  revalidatePath("/admin");
  return {
    ok: result.data.ok,
    message: result.data.ok
      ? "Backup finished. The server was stopped and restarted as part of it."
      : result.data.detail || "The backup did not succeed.",
  };
}

export async function console(
  _previous: ActionResult | null,
  formData: FormData,
): Promise<ActionResult> {
  if (!(await isAdmin())) {
    return { ok: false, message: "Not signed in." };
  }
  const command = String(formData.get("command") ?? "").trim();
  if (!command) return { ok: false, message: "Type a command first." };

  const result = await sendConsoleCommand(command);
  if (!result.ok) return { ok: false, message: result.error };
  revalidatePath("/");
  return {
    ok: result.data.ok,
    message: result.data.ok ? `Sent: ${command}` : result.data.detail || "The server refused it.",
  };
}

export { CONSOLE_PRESETS };