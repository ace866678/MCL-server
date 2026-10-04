"use client";

import { useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { createClient } from "@/lib/supabase/client";

export default function LoginPage() {
  const router = useRouter();
  const [mode, setMode] = useState<"login" | "signup">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setMessage("");
    const supabase = createClient();
    const result = mode === "login"
      ? await supabase.auth.signInWithPassword({ email, password })
      : await supabase.auth.signUp({ email, password, options: { emailRedirectTo: process.env.NEXT_PUBLIC_DEV_SUPABASE_REDIRECT_URL ?? `${window.location.origin}/auth/callback` } });
    setBusy(false);
    if (result.error) { setMessage(result.error.message); return; }
    if (mode === "signup") { setMessage("Check your email to confirm your account."); return; }
    router.push("/"); router.refresh();
  }

  return <main className="auth-shell"><section className="auth-card"><p className="eyebrow">MCL CONTROL PLANE</p><h1>{mode === "login" ? "Welcome back" : "Create your account"}</h1><p className="subtitle">Manage Minecraft servers running on your own Windows or Linux device.</p><form onSubmit={submit}><label>Email<input required type="email" value={email} onChange={(e) => setEmail(e.target.value)} /></label><label>Password<input required minLength={8} type="password" value={password} onChange={(e) => setPassword(e.target.value)} /></label><button disabled={busy}>{busy ? "Working…" : mode === "login" ? "Sign in" : "Create account"}</button></form>{message ? <p className="form-message">{message}</p> : null}<button className="link-button" onClick={() => setMode(mode === "login" ? "signup" : "login")}>{mode === "login" ? "Need an account? Sign up" : "Already have an account? Sign in"}</button></section></main>;
}
