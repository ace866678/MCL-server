/**
 * Ambient declarations so `tsc --noEmit` can check the Edge Function in this
 * repository, where Deno is not installed.
 *
 * This is a check harness only — it is never deployed. The Edge Function runs on
 * Deno and imports `jsr:@supabase/supabase-js@2`, which plain `tsc` cannot
 * resolve; the local `supabase-js` types stand in for it. Everything else in
 * `index.ts` is checked exactly as written.
 *
 * When you have the Supabase CLI, `supabase functions serve` is the real check.
 */

declare namespace Deno {
  export function serve(handler: (request: Request) => Response | Promise<Response>): void;

  export const env: {
    get(key: string): string | undefined;
  };
}

/**
 * `jsr:` is a JSR scheme that `tsc` cannot resolve. Forwarding the types to the
 * identical npm package keeps the check meaningful: `db.rpc("agent_heartbeat", …)`
 * is still type-checked, including the argument shape.
 */
declare module "jsr:@supabase/supabase-js@2" {
  export * from "@supabase/supabase-js";
}