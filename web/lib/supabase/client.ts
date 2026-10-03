import { createBrowserClient } from "@supabase/ssr";
import { requireSupabase } from "./env";

export function createClient() {
  const { url, key } = requireSupabase();
  return createBrowserClient(url, key);
}