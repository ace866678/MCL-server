import Link from "next/link";
import { redirect } from "next/navigation";
import { supabaseConfigured } from "@/lib/supabase/env";
import { createClient } from "@/lib/supabase/server";
import { deleteAgent } from "../actions";
import { RegisterAgentForm } from "./agent-controls";

export const dynamic = "force-dynamic";

type Agent = {
  id: string;
  name: string;
  platform: string;
  last_seen_at: string | null;
};

export default async function AgentsPage() {
  if (!supabaseConfigured()) redirect("/");

  const supabase = await createClient();
  const { data, error } = await supabase
    .from("agents")
    .select("id,name,platform,last_seen_at")
    .order("created_at", { ascending: false });

  const agents: Agent[] = Array.isArray(data) ? data : [];

  return (
    <main>
      <section className="section-heading">
        <div>
          <p className="eyebrow">YOUR HARDWARE</p>
          <h2>Agents</h2>
        </div>
        <Link className="button-link" href="/">
          Back to servers
        </Link>
      </section>

      {error ? <div className="notice error">Could not load agents: {error.message}</div> : null}

      <RegisterAgentForm />

      {agents.length ? (
        <div className="server-grid">
          {agents.map((agent) => (
            <article className="server-card" key={agent.id}>
              <div className="card-heading">
                <div>
                  <h3>{agent.name}</h3>
                  <p>{agent.platform}</p>
                </div>
                <span className={`status-pill${agent.last_seen_at ? "" : " muted"}`}>
                  <i /> {agent.last_seen_at ? "Reporting" : "Awaiting agent"}
                </span>
              </div>
              <div className="card-stats">
                <span>
                  Last seen
                  <b>{agent.last_seen_at ? new Date(agent.last_seen_at).toLocaleString() : "never"}</b>
                </span>
              </div>
              <form action={deleteAgent}>
                <input type="hidden" name="id" value={agent.id} />
                <button type="submit" className="secondary">
                  Remove agent
                </button>
              </form>
            </article>
          ))}
        </div>
      ) : (
        <div className="empty-state">
          <h3>No agents yet</h3>
          <p>Register one above, then run agent/agent.py on the machine that hosts Minecraft.</p>
        </div>
      )}
    </main>
  );
}