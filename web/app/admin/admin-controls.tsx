"use client";

import { useActionState } from "react";
import { backup, console, login, logout, type ActionResult } from "../actions";

const PRESETS = ["list", "tps", "save-all", "whitelist on", "whitelist off"];

function Result({ result }: { result: ActionResult | null }) {
  if (!result) return null;
  return (
    <p className="result" role="status">
      {result.message}
    </p>
  );
}

export function LoginForm() {
  const [state, action, pending] = useActionState(login, null);
  return (
    <form action={action}>
      <input
        type="password"
        name="password"
        placeholder="Admin password"
        autoComplete="current-password"
        required
      />
      <button type="submit" disabled={pending}>
        {pending ? "Checking..." : "Sign in"}
      </button>
      <Result result={state} />
    </form>
  );
}

export function SignOutButton() {
  return (
    <form action={logout}>
      <button type="submit" className="secondary">
        Sign out
      </button>
    </form>
  );
}

export function BackupButton() {
  const [state, action, pending] = useActionState(backup, null);
  return (
    <div>
      <form action={action}>
        <button type="submit" disabled={pending}>
          {pending ? "Backing up..." : "Back up now"}
        </button>
      </form>
      <Result result={state} />
    </div>
  );
}

export function ConsoleForm() {
  const [state, action, pending] = useActionState(console, null);
  return (
    <div>
      <form action={action}>
        <input
          type="text"
          name="command"
          placeholder="list"
          list="console-presets"
          autoComplete="off"
          spellCheck={false}
          required
        />
        <datalist id="console-presets">
          {PRESETS.map((preset) => (
            <option key={preset} value={preset} />
          ))}
        </datalist>
        <button type="submit" disabled={pending}>
          {pending ? "Sending..." : "Send"}
        </button>
      </form>
      <Result result={state} />
    </div>
  );
}