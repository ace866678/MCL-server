import { createServerClient } from "@supabase/ssr";
import { cookies } from "next/headers";
import { requireSupabase } from "./env";

export async function createClient() {
  const cookieStore = await cookies();
  const { url, key } = requireSupabase();
  return createServerClient(url, key, {
    cookies: {
      getAll() {
        return cookieStore.getAll();
      },
      // Server Components cannot set cookies. Refreshing the session happens in
      // proxy.ts, so a write here is only a no-op safety net.
      setAll(cookiesToSet) {
        try {
          cookiesToSet.forEach(({ name, value, options }) =>
            cookieStore.set(name, value, options),
          );
        } catch {
          // Called from a Server Component: ignore, the middleware already ran.
        }
      },
    },
  });
}