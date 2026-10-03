# Backend — Supabase control plane

Everything a Minecraft server needs to be managed from a browser, with no server
in this repository ever holding a credential for the machine the game runs on.

```
migrations/        the schema. pushed with `supabase db push`, never applied by hand.
functions/agent-api  the only door between an agent and the database.
config.toml        required by every `supabase` command
```

## Deploy

```sh
cd backend
supabase login
supabase link --project-ref <your-project-ref>

# Schema first: the Edge Function calls RPCs that must already exist.
supabase db push

# Then the function.
supabase functions deploy agent-api --no-verify-jwt
```

`--no-verify-jwt` is correct here and nowhere else. Agents do not hold a Supabase
JWT; they hold a device token, and this function authenticates them itself. Every
other endpoint is browser-facing and keeps Supabase's own JWT verification.

`config.toml` sets `[functions.agent-api] verify_jwt = false`, which is the same
setting persisted, so a later bare `supabase functions deploy agent-api` behaves
the same way.

### Local development

```sh
supabase start          # needs Docker; not available in every environment
supabase db reset       # replays migrations/ into the local database
supabase functions serve agent-api --no-verify-jwt
```

## Migrations

Applied in filename order. They only ever add: `alter table ... add column if not
exists`, `create table if not exists`, `create or replace function`. Nothing drops
a column, so replaying them against a project that already has the earlier ones is
safe and idempotent.

| File | What it adds |
| --- | --- |
| `0001_legacy_schema.sql` | The original `agents` + `servers` tables, kept verbatim so an existing project reproduces its own history |
| `0002_core.sql` | `profiles`, `server_settings`, runtime columns on `agents` and `servers`, seeded setting definitions |
| `0003_commands_backups.sql` | `server_commands` (the queue), `server_backups`, `agent_rate_buckets`, `agent_request_nonces`, `agent_events` |
| `0004_agent_rpc.sql` | Every `SECURITY DEFINER` function, plus `enqueue_command` for the browser |
| `0005_rls.sql` | Row-level security on every table, with column guards |
| `0006_agent_server_discovery.sql` | `agent_list_servers`, so an agent knows what it is responsible for before any command is queued |

## Authentication

Each agent holds a 32-byte token, shown exactly once by the panel. Only its
SHA-256 is stored. Every request presents that digest as a bearer credential:

| Header | Value |
| --- | --- |
| `X-MCL-Agent-Id` | the agent's uuid |
| `X-MCL-Token-Digest` | `hex(SHA-256(token))` — the value the panel stored |
| `X-MCL-Timestamp` | unix seconds, must be within 5 minutes |
| `X-MCL-Nonce` | 128 random bits, hex, spent exactly once |

### Why this is not an HMAC

Verifying a MAC requires the MAC key, so an HMAC scheme would force the database to
store the token itself — and a database dump would then be a dump of usable
credentials for other people's computers. Storing only the digest means a leaked
dump yields nothing an attacker can present, which is the failure mode worth
engineering against. TLS protects the request in flight, exactly as it protects
every other bearer credential on the internet.

The obvious objection is replay. It is prevented structurally rather than
cryptographically: the nonce is accepted exactly once (`agent_spend_nonce`) and the
timestamp must be inside a five-minute window. Every endpoint is idempotent
regardless — heartbeats and status reports are upserts, command claim and complete
are conditional updates — so even a replay inside the window would be a no-op.

The nonce is spent *before* the credential is checked, so a flood of guesses
cannot be used to enumerate agent ids: an unknown agent id has no row to insert
against and is rejected at the nonce step.

## Routes

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/heartbeat` | agent liveness and host facts; also runs housekeeping |
| `GET` | `/servers` | the servers this agent is responsible for |
| `GET` | `/commands?limit=N` | claim queued commands |
| `POST` | `/commands/:id/result` | report a command outcome |
| `POST` | `/servers/:id/status` | report one server's live state |
| `POST` | `/backups` | record a backup the agent created |
| `POST` | `/events` | short operational log line |
| `POST` | `/maintenance` | expire stale commands and prune |

Bodies over 64 KiB are refused. Rate limits are per agent: 30 heartbeats and 30
mutations per minute, 120 status reports per minute. The rate limiter fails open,
because failing closed would take an agent offline for a reason it cannot see;
every command is ownership-checked regardless.

## Security posture

* `search_path = ''` plus fully-qualified names on every `SECURITY DEFINER`
  function, so a caller cannot shadow a function or table name.
* Agent RPCs are revoked from `anon` and `authenticated` and granted only to
  `service_role`, which is this function's key. A stolen anon key cannot drive an
  agent.
* Every agent RPC re-checks agent identity explicitly, because RLS does not apply
  inside a `SECURITY DEFINER` function.
* `enqueue_command` re-reads ownership through `auth.uid()` and refuses a second
  in-flight command per server, so an offline agent cannot accumulate a backlog
  that all fires at once when a laptop rejoins the network.
* `agent_report_server_status` refuses a server that is not the calling agent's.
* Backups store a bare filename, never a path, and are local-only: the panel learns
  what exists, never how to address a file on the operator's disk.

## What this backend deliberately does not do

* It never stores a world, a jar, or a backup file. Those live on the machine
  running the server.
* It never downloads to or uploads from the operator's filesystem. Everything
  crosses the command queue, which the agent executes locally.
* It does not offer backup download. `server_backups.storage` is constrained to
  `'local'`, so a row is a label, not a file in the cloud.