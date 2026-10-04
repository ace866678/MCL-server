# Control plane web app

The Next.js app for the Supabase-backed control plane: sign in, register the agents that run
Minecraft on your own hardware, and manage the server records. `/admin` is the older status-page
surface kept for the VM deployment &mdash; it reaches the Minecraft server through the HTTP bridge
running on that machine.

**This app does not host the Minecraft server.** Vercel cannot: serverless functions are
request-scoped and time-limited, there is no UDP for Bedrock, and the runtime image is not yours to
choose. The server runs on your own machine; the agent on that machine dials out to this app.

## Deploying to Vercel

1. Import the repository. The root `vercel.json` sets **root directory** to `web`; setting the same
   value in the project's settings overrides it. Everything outside `web/` is the server side and is
   not part of this build.
2. Add the environment variables below.
3. Deploy. No further build configuration is needed &mdash; Next.js is detected automatically.

With the root directory at `web`, `npm ci` and `npm run build` resolve against `web/package.json`,
which is where the lockfile and the dependencies live.

| Variable | Required | What it is |
| --- | --- | --- |
| `NEXT_PUBLIC_SUPABASE_URL` | yes | Project URL. `SUPABASE_URL` is accepted too, for a self-hosted instance. |
| `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY` | yes | Publishable/anon key. `SUPABASE_ANON_KEY` is accepted too. |
| `MINECRAFT_BRIDGE_URL` | for `/admin` | Public URL of the bridge, e.g. `https://foo.trycloudflare.com`. Reached over a tunnel from the VM, never directly. |
| `MINECRAFT_BRIDGE_TOKEN` | for `/admin` | `BRIDGE_TOKEN` from `/etc/minecraft-bridge.env` on the VM. Server-side only; never exposed to the browser. |
| `ADMIN_PASSWORD` | for `/admin` | Gate for backups and console commands. Without it the controls render disabled. |
| `NEXT_PUBLIC_DEV_SUPABASE_REDIRECT_URL` | no | Overrides the sign-up confirmation redirect in local development, because Supabase only allowlists its own callback URLs. |

Get the bridge token from the VM with `sudo cat /etc/minecraft-bridge.env`.

Every route is `force-dynamic`, so each request re-reads the session. A missing Supabase
configuration renders the "being provisioned" landing page rather than throwing, and a dead bridge
says so instead of failing the page. Apply the migrations in `../backend/supabase/migrations/` before the first sign-in.

## Local development

```bash
npm install
NEXT_PUBLIC_SUPABASE_URL=http://127.0.0.1:54321 \
NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY=your-anon-key \
npm run dev
```

Without Supabase variables the app still boots and serves the landing page, so the layout can be
worked on without a database.

To exercise `/admin` with no Minecraft server, run the bridge against a mock RCON server:

```bash
python3 ../bridge/test_bridge.py --mock-server   # prints a token and port
```

## Checks

```bash
npm run typecheck
npm run build
```

`npm run build` is the same build Vercel runs, so running it locally is how you find out whether a
deploy will succeed before pushing.

## Layout

| Path | What it does |
| --- | --- |
| `app/page.tsx` | Landing page when signed out, server list when signed in |
| `app/login/page.tsx` | Supabase email/password sign-in and sign-up |
| `app/dashboard/agents` | Register and remove agents; the agent token is shown once |
| `app/dashboard/servers/new` | Create a server record against a registered agent |
| `app/dashboard/servers/[id]` | One server: its configuration and agent |
| `app/admin/page.tsx` | Bridge-backed status, players, backup and console, behind the admin gate |
| `app/actions.ts`, `app/dashboard/actions.ts` | Server actions; every one re-checks the session rather than trusting the UI |
| `proxy.ts` | Refreshes the Supabase session cookie before any route renders |
| `lib/bridge.ts` | Server-side bridge client. The token stays here. |
| `lib/auth.ts` | HMAC-signed session cookie for `/admin` |
| `lib/supabase/` | Supabase clients and the URL/key resolution they share |
