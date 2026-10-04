import { createServerClient } from "@supabase/ssr";
import { NextResponse, type NextRequest } from "next/server";
import { supabaseKey, supabaseUrl } from "./env";

/**
 * Refreshes the Supabase session cookie on every request.
 *
 * Runs before routes render, so anything under app/ can call `auth.getUser()`
 * and trust the result. Returns the request untouched when Supabase is not
 * configured, which keeps a local `next dev` bootable without a database.
 */
export async function updateSession(request: NextRequest) {
  const url = supabaseUrl();
  const key = supabaseKey();
  if (!url || !key) return NextResponse.next({ request });

  let response = NextResponse.next({ request });
  const supabase = createServerClient(url, key, {
    cookies: {
      getAll() {
        return request.cookies.getAll();
      },
      setAll(cookiesToSet) {
        // The request cookies have to be refreshed too, or the very next
        // read inside this same request still sees the stale session.
        cookiesToSet.forEach(({ name, value }) => request.cookies.set(name, value));
        response = NextResponse.next({ request });
        cookiesToSet.forEach(({ name, value, options }) =>
          response.cookies.set(name, value, options),
        );
      },
    },
  });

  // getUser() revalidates against Supabase rather than reading the JWT, which
  // is what makes the session trustworthy on the server.
  await supabase.auth.getUser();
  return response;
}