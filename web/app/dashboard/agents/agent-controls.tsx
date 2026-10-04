"use client";

import { useActionState } from "react";
import { registerAgent, type AgentCredentials } from "../actions";

function Result({ result }: { result: AgentCredentials | null }) {
  if (!result) return null;
  return (
    <div className="stack">
      <p className="result" data-ok={result.ok} role="status">
        {result.message}
      </p>
      {result.hint ? <div className="notice error">{result.hint}</div> : null}
      {result.token && result.env ? (
        <>
          <p className="empty">
            Put these three values in <code>agent/agent.json</code> on the machine that runs
            Minecraft, then start the agent with <code>python3 agent/agent.py</code>. The token is
            shown once only, and the panel stores just its SHA-256 &mdash; if you lose it, remove the
            agent and register a new one.
          </p>
          <pre className="token-box">{result.env}</pre>
        </>
      ) : null}
    </div>
  );
}

export function RegisterAgentForm() {
  const [state, action, pending] = useActionState(registerAgent, null);
  return (
    <section className="panel">
      <h2>Register an agent</h2>
      <p className="empty">
        The agent runs on the device that hosts Minecraft. It only makes outbound requests, so no
        port needs to be opened on your router.
      </p>
      <form action={action}>
        <label className="field">
          Name
          <input name="name" placeholder="home-server" required maxLength={80} />
        </label>
        <label className="field">
          Platform
          <select name="platform" defaultValue="linux" required>
            <option value="linux">Linux</option>
            <option value="windows">Windows</option>
          </select>
        </label>
        <button type="submit" disabled={pending}>
          {pending ? "Registering..." : "Register agent"}
        </button>
      </form>
      <Result result={state} />
    </section>
  );
}