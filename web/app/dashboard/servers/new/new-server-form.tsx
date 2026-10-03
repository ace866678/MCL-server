"use client";

import { useActionState } from "react";
import { createServer } from "../../actions";

export type AgentOption = { id: string; name: string; platform: string };

export function NewServerForm({ agents }: { agents: AgentOption[] }) {
  const [state, action, pending] = useActionState(createServer, null);

  return (
    <section className="panel">
      <h2>Server details</h2>
      {agents.length === 0 ? (
        <p className="empty">
          No agents are registered yet, so there is nothing to run this server on.{" "}
          <a href="/dashboard/agents">Register an agent</a> first.
        </p>
      ) : (
        <>
          <form action={action}>
            <label className="field">
              Name
              <input name="name" placeholder="survival" required maxLength={80} />
            </label>
            <label className="field">
              Minecraft version
              <input name="minecraft_version" defaultValue="26.2" required maxLength={20} />
            </label>
            <label className="field">
              Agent
              <select name="agent_id" defaultValue={agents[0]?.id} required>
                {agents.map((agent) => (
                  <option key={agent.id} value={agent.id}>
                    {agent.name} ({agent.platform})
                  </option>
                ))}
              </select>
            </label>
            <button type="submit" disabled={pending}>
              {pending ? "Creating..." : "Create server"}
            </button>
          </form>
          {state ? (
            <p className="result" data-ok={state.ok} role="status">
              {state.message}
            </p>
          ) : null}
        </>
      )}
    </section>
  );
}