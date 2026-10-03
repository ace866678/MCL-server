/**
 * Supabase connection settings.
 *
 * Both the publishable (`NEXT_PUBLIC_*`) and the server-only names are accepted
 * so the same code works on Vercel, where the value is public, and against a
 * self-hosted project where the URL/key stay private.
 */

export function supabaseUrl(): string | undefined {
  return process.env.NEXT_PUBLIC_SUPABASE_URL ?? process.env.SUPABASE_URL;
}

export function supabaseKey(): string | undefined {
  return (
    process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY ??
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY ??
    process.env.SUPABASE_PUBLISHABLE_KEY ??
    process.env.SUPABASE_ANON_KEY
  );
}

export function supabaseConfigured(): boolean {
  return Boolean(supabaseUrl() && supabaseKey());
}

/** Both values, or a message naming what is missing. */
export function requireSupabase(): { url: string; key: string } {
  const url = supabaseUrl();
  const key = supabaseKey();
  if (!url || !key) {
    throw new Error(
      "Supabase is not configured. Set NEXT_PUBLIC_SUPABASE_URL and NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY (or SUPABASE_URL and SUPABASE_PUBLISHABLE_KEY) on this deployment.",
    );
  }
  return { url, key };
}