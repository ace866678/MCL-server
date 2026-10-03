import Link from "next/link";
import { notFound, redirect } from "next/navigation";
import { supabaseConfigured } from "@/lib/supabase/env";
import { createClient } from "@/lib/supabase/server";
import { deleteServer } from "../../actions";

export const dynamic = "force-dynamic";

type Server = {
  id: string;
  name: string;
  minecraft_version: string;
  server_software: string;
  agent_id: string;
  created_at: string;
};

type Agent = {
  id: string;
  name: string;
  platform: string;
  last_seen_at: string | null;
};

export default async function ServerPage({ params }: { params: Promise<{ id: string }> }) {
  if (!supabaseConfigured()) redirect("/");

  const { id } = await params;
  const supabase = await createClient();

  const { data } = await supabase
    .from("servers")
    .select("id,name,minecraft_version,server_software,agent_id,created_at")
    .eq("id", id)
    .maybeSingle();

  if (!data) notFound();
  const server: Server = data;

  // RLS means an id belonging to somebody else simply reads as missing.
  const { data: agentData } = await supabase
    .from("agents")
    .select("id,name,platform,last_seen_at")
    .eq("id", server.agent_id)
    .maybeSingle();
  const agent: Agent | null = agentData ?? null;

  return (
    <main>
      <section className="section-heading">
        <div>
          <p className="eyebrow">SERVER</p>
          <h2>{server.name}</h2>
        </div>
        <Link className="button-link" href="/">
          All servers
        </Link>
      </section>

      <section className="panel">
        <h2>Configuration</h2>
        <div className="card-stats">
          <span>
            Software
            <b>{server.server_software}</b>
          </span>
          <span>
            Minecraft
            <b>{server.minecraft_version}</b>
          </span>
          <span>
            Created
            <b>{new Date(server.created_at).toLocaleDateString()}</b>
          </span>
        </div>
      </section>

      <section className="panel">
        <h2>Agent</h2>
        {agent ? (
          <>
            <p className="empty">
              {agent.name} on {agent.platform} &mdash; last seen{" "}
              {agent.last_seen_at ? new Date(agent.last_seen_at).toLocaleString() : "never"}.
            </p>
            <Link href="/dashboard/agents">Manage agents</Link>
          </>
        ) : (
          <div className="notice error">
            The agent this server points at is gone. <Link href="/dashboard/agents">Register a new
            one</Link> and recreate the server.
          </div>
        )}
      </section>

      <section className="panel">
        <h2>Remove</h2>
        <p className="empty">
          Deletes the record only. Worlds and player data stay on the machine the agent runs on.
        </p>
        <form action={deleteServer}>
          <input type="hidden" name="id" value={server.id} />
          <button type="submit" className="secondary">
            Delete server record
          </button>
        </form>
      </section>
    </main>
  );
}