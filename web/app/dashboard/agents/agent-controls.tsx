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
      {result.token ? (
        <>
          <p className="empty">
            Paste these into the agent&rsquo;s environment on the machine running Minecraft, then
            start it with <code>python3 agent/agent.py</code>. The token is shown once only.
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