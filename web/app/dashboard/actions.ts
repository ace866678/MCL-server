"use server";

import { createHash, randomBytes } from "node:crypto";
import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";
import { createClient } from "@/lib/supabase/server";

/**
 * Writes against the control-plane tables.
 *
 * Every action re-reads the caller's own row through Supabase rather than
 * trusting an id from the form, and RLS on `agents` / `servers` scopes the
 * rows to `auth.uid()`, so one owner cannot touch another's infrastructure.
 */

export type FormResult = { ok: boolean; message: string };

/** Only ever shown once: the database stores the hash, never the token. */
export type AgentCredentials = FormResult & {
  agentId?: string;
  token?: string;
  env?: string;
};

async function signedIn() {
  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  return user ? { supabase, user } : null;
}

export async function registerAgent(
  _previous: AgentCredentials | null,
  formData: FormData,
): Promise<AgentCredentials> {
  const session = await signedIn();
  if (!session) return { ok: false, message: "Sign in again." };

  const name = String(formData.get("name") ?? "").trim();
  const platform = String(formData.get("platform") ?? "").trim();
  if (!name) return { ok: false, message: "Give the agent a name." };
  if (platform !== "linux" && platform !== "windows") {
    return { ok: false, message: "Pick linux or windows." };
  }

  const token = randomBytes(32).toString("base64url");
  const { data: agent, error } = await session.supabase
    .from("agents")
    .insert({
      owner_id: session.user.id,
      name,
      platform,
      token_hash: sha256(token),
    })
    .select("id")
    .single();

  if (error || !agent) {
    return { ok: false, message: `Could not register the agent: ${error?.message ?? "unknown error"}` };
  }

  revalidatePath("/dashboard/agents");
  return {
    ok: true,
    message: `${name} registered. Copy the token now — it is not stored in readable form.`,
    agentId: agent.id,
    token,
    env: [
      `MCL_AGENT_ID=${agent.id}`,
      `MCL_AGENT_TOKEN=${token}`,
      "MCL_API_URL=https://<this deployment>",
    ].join("\n"),
  };
}

export async function deleteAgent(formData: FormData): Promise<void> {
  const session = await signedIn();
  const id = String(formData.get("id") ?? "");
  if (session && id) {
    await session.supabase.from("agents").delete().eq("id", id);
  }
  revalidatePath("/dashboard/agents");
  redirect("/dashboard/agents");
}

export async function createServer(
  _previous: FormResult | null,
  formData: FormData,
): Promise<FormResult> {
  const session = await signedIn();
  if (!session) return { ok: false, message: "Sign in again." };

  const name = String(formData.get("name") ?? "").trim();
  const agentId = String(formData.get("agent_id") ?? "");
  const minecraftVersion = String(formData.get("minecraft_version") ?? "").trim();

  if (!name) return { ok: false, message: "Give the server a name." };
  if (!minecraftVersion) return { ok: false, message: "Pick a Minecraft version." };
  if (!agentId) return { ok: false, message: "Pick the agent that runs it." };

  // The agent has to belong to this owner; RLS enforces it, this just gives a
  // readable message instead of a bare policy violation.
  const { data: agent } = await session.supabase
    .from("agents")
    .select("id")
    .eq("id", agentId)
    .maybeSingle();
  if (!agent) return { ok: false, message: "That agent is not registered to you." };

  const { error } = await session.supabase.from("servers").insert({
    owner_id: session.user.id,
    agent_id: agentId,
    name,
    minecraft_version: minecraftVersion,
  });

  if (error) {
    return { ok: false, message: `Could not create the server: ${error.message}` };
  }
  revalidatePath("/");
  redirect("/");
}

export async function deleteServer(formData: FormData): Promise<void> {
  const session = await signedIn();
  const id = String(formData.get("id") ?? "");
  if (session && id) {
    await session.supabase.from("servers").delete().eq("id", id);
  }
  revalidatePath("/");
  redirect("/");
}

function sha256(value: string): string {
  return createHash("sha256").update(value).digest("hex");
}