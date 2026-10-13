"use client";

import Link from "next/link";
import { useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { createClient } from "@/lib/supabase/client";
import { supabaseConfigured } from "@/lib/supabase/env";

export default function LoginPage() {
  const router = useRouter();
  const authConfigured = supabaseConfigured();
  const [mode, setMode] = useState<"login" | "signup">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!authConfigured) {
      setMessage("Authentication is not connected yet. Use the read-only demo to test the panel.");
      return;
    }

    setBusy(true);
    setMessage("");
    try {
      const supabase = createClient();
      const result = mode === "login"
        ? await supabase.auth.signInWithPassword({ email, password })
        : await supabase.auth.signUp({
            email,
            password,
            options: {
              emailRedirectTo: process.env.NEXT_PUBLIC_DEV_SUPABASE_REDIRECT_URL ?? `${window.location.origin}/auth/callback`,
            },
          });

      if (result.error) {
        setMessage(result.error.message);
        return;
      }
      if (mode === "signup") {
        setMessage("Check your email to confirm your account.");
        return;
      }
      router.push("/");
      router.refresh();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Login failed. Check your authentication settings and try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="auth-shell">
      <section className="auth-card">
        <p className="eyebrow">MCL CONTROL PLANE</p>
        <h1>{mode === "login" ? "Welcome back" : "Create your account"}</h1>
        <p className="subtitle">Manage Minecraft servers running on your own Windows or Linux device.</p>

        {!authConfigured ? (
          <div className="notice" style={{ marginTop: "1.25rem" }}>
            <strong>Login is not connected yet.</strong>
            <p className="empty" style={{ margin: ".4rem 0 0" }}>
              You can still explore the interface with sample data. Demo mode never connects to a real server or changes server data.
            </p>
          </div>
        ) : (
          <>
            <form onSubmit={submit}>
              <label>Email<input required type="email" autoComplete="email" value={email} onChange={(event) => setEmail(event.target.value)} /></label>
              <label>Password<input required minLength={8} type="password" autoComplete={mode === "login" ? "current-password" : "new-password"} value={password} onChange={(event) => setPassword(event.target.value)} /></label>
              <button disabled={busy}>{busy ? "Working…" : mode === "login" ? "Sign in" : "Create account"}</button>
            </form>
            <button className="link-button" onClick={() => { setMode(mode === "login" ? "signup" : "login"); setMessage(""); }}>
              {mode === "login" ? "Need an account? Sign up" : "Already have an account? Sign in"}
            </button>
          </>
        )}

        {message ? <p className="form-message" role="status">{message}</p> : null}
        <div style={{ marginTop: "1.25rem", paddingTop: "1rem", borderTop: "1px solid var(--border)" }}>
          <p className="subtitle" style={{ marginBottom: ".75rem" }}>Just want to test the UI?</p>
          <Link className="button-link" href="/demo">Open read-only demo</Link>
        </div>
        <p className="subtitle" style={{ marginTop: "1rem" }}>
          Real login and server controls will become available after an authentication provider and backend are configured.
        </p>
      </section>
    </main>
  );
}
