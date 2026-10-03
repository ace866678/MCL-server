# Status web app

A small Next.js app showing whether the Minecraft server is up, how many players are online, and
who they are. `/admin` can trigger a backup or run an allowlisted console command.

**This app does not host the Minecraft server.** Vercel cannot: serverless functions are
request-scoped and time-limited, there is no UDP for Bedrock, and the runtime image is not yours to
choose. The server runs on a VM (`mc deploy`); this talks to the bridge running there.

## Deploying to Vercel

1. Import the repository, and set the project's **root directory** to `web`. Everything outside
   `web/` is the server side and is not part of this build.
2. Add the environment variables below.
3. Deploy. No build configuration is needed &mdash; Next.js is detected automatically.

| Variable | Required | What it is |
| --- | --- | --- |
| `MINECRAFT_BRIDGE_URL` | yes | Public URL of the bridge, e.g. `https://foo.trycloudflare.com`. Reached over a tunnel from the VM, never directly. |
| `MINECRAFT_BRIDGE_TOKEN` | yes | `BRIDGE_TOKEN` from `/etc/minecraft-bridge.env` on the VM. Server-side only; never exposed to the browser. |
| `ADMIN_PASSWORD` | for `/admin` | Gate for backups and console commands. Without it the controls render disabled. |
| `MINECRAFT_JOIN_ADDRESS` | no | Hostname players connect to, shown at the bottom of the page. |

Get the token from the VM with `sudo cat /etc/minecraft-bridge.env`.

Both pages are `force-dynamic`, so each request asks the bridge. If the bridge is unreachable the
page says so and names the likely cause instead of throwing an error.

## Local development

```bash
npm install
MINECRAFT_BRIDGE_URL=http://127.0.0.1:8787 \\
MINECRAFT_BRIDGE_TOKEN=dev-token \\
ADMIN_PASSWORD=dev \\
npm run dev
```

To try it without a VM, run the bridge against a mock RCON server:

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
| `app/page.tsx` | Status: online/offline, counts, versions, who is online |
| `app/admin/page.tsx` | Backup and console, behind the admin gate |
| `app/actions.ts` | Server actions; every one re-checks the session rather than trusting the UI |
| `lib/bridge.ts` | Server-side bridge client. The token stays here. |
| `lib/auth.ts` | HMAC-signed session cookie for `/admin` |