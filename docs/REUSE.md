# Reuse map — what was kept, what changed, and why

Every component of the original single-VM project was read before any code was written. This
records what carries over, so the transformation is a migration and not a rewrite.

## Kept as-is

| Component | Why it stays |
| --- | --- |
| `mc` | The whole server control surface, and it is the only part of this project with real operational history: FIFO-based console injection with no pty, `SIGTERM` → console `stop` so the world is saved, `set -Eeuo pipefail` with `errexit` deliberately not suppressed inside functions, checksum-verified downloads, `mc doctor`, `mc deploy --dry-run`. Reimplemented, this would be a regression. |
| `lib/common.sh` | `load_versions`, `sha256_of`, `check_java`, `seed_config`, `set_server_property`, `check_eula`, `jvm_flags`. Aikar-style G1 flags and the "capture `java -version` whole, never pipe it" pipefail fix are load-bearing. |
| `VERSION` | Verified against the live APIs during this work: Paper `26.2` build `129` is still the newest STABLE and its SHA-256 still matches; Geyser `2.11.3` build `1247` and Floodgate `2.2.5` build `141` are still latest. Kept as the shipped default and as the reference for how a pin looks. |
| `config/*` | `server.properties`, `eula.txt`, and the four JSON lists are exactly the seed a managed server needs. `mc install` copies them per server, so the same files seed every managed server. |
| `bridge/bridge.py` HTTP + `/admin` | Still a genuine single-VM status page. Kept, still documented, and now labelled as the single-machine option so it is not confused with `/dashboard`. |
| `web/lib/auth.ts`, `web/app/actions.ts` | HMAC session cookie with `timingSafeEqual`, server actions that re-check the session rather than trusting the UI. |
| `docker/`, `systemd/minecraft*.service` | Valid ways to run the original single-server setup on a box you already own. |

## Moved rather than copied

| Component | Why |
| --- | --- |
| `bridge/bridge.py` RCON | `Rcon._pack` / `_read_packet` / `command` (request-id matching that discards the post-auth empty `RESPONSE_VALUE` packet) and the two parsers, covered by the existing 23 tests. Moved to `lib/mcl_rcon.py` so the bridge and the agent import **one** implementation. Two copies of a protocol implementation drift, and then the status page and the dashboard disagree about who is online. |

`bridge/bridge.py` re-exports the names it always exported, so its tests and its callers are
unchanged — the refactor is invisible above the import.

## Deliberately replaced

| Component | Why |
| --- | --- |
| `agent/agent.py` | The original was ~94 lines: no installer, no version resolution, no status collection, no backup or log handling, and it authenticated with `Authorization: Bearer` against `/api/agents/...` routes that do not exist in the Edge Function. It could not have worked against the deployed backend at all. Replaced by `agent/mcl_agent/` — 12 modules behind the same `python3 agent/agent.py` entry point. |
| `supabase/schema.sql` | Two tables and two policies, no command queue, no backups, no agent auth. Superseded by numbered migrations that `ALTER` the existing tables rather than replacing them, so a project that already ran the old file keeps its rows. |
| `web/app/page.tsx`, `/dashboard/*` | Rendered `<b>—</b>` for player counts and a hardcoded "Awaiting agent" pill, and linked to routes that did not exist. Every value is now sourced from `servers.status` and `servers.runtime`, which only the agent writes. |

## Changed in place, minimally

`lib/common.sh` and `mc` gain exactly what multi-tenancy needs:

- `MC_VERSION_FILE` — so each managed server has its own pin file instead of sharing the repo's
  `VERSION`. One environment variable; `load_versions` is otherwise untouched.
- `mc install --catalog` — resolve the newest STABLE build for an arbitrary Minecraft version from
  PaperMC's v3 API, write a pin file, then reuse the existing checksum-verified download path.
  The pin is still verified on every install, which is the property that matters.
- `PAPER_JAVA_FLAGS` / `JAVA_MAJOR` became per-pin values, because Paper's required Java version
  is a function of the Minecraft version. Hardcoding 25 would have made `install --catalog 1.20.4`
  produce a server that cannot boot.

Nothing else in either file was rewritten. The agent drives `mc` rather than reimplementing JVM
flags, FIFO consoles, backups or `server.properties` rewriting.

## Verified externally during the audit

- `GET https://fill.papermc.io/v3/projects/paper` → `{"versions": {"26.3": [...], "26.2": [...]}}`,
  a grouped map, so a version list has to be flattened and pre-releases filtered.
- `GET .../v3/projects/paper/versions/{mc}/builds` → `[{channel, id, downloads["server:default"].checksums.sha256}]`.
  This is the source for configurable versions; the SHA-256 comes from the API, so a version the
  project has never heard of is still checksum-verified.
- `GET .../v3/projects/paper/versions/{mc}` → `version.java.version.minimum` and
  `version.java.flags.recommended`. Confirmed live: 26.2 → 25, 1.21.11 → 21, 1.20.4 → 17.
- `GET https://download.geysermc.org/v2/projects/{project}/versions/latest/builds/latest` →
  `{version, build, downloads.spigot.sha256}`. Geyser and Floodgate deliberately track the latest
  build, because they follow Minecraft's Bedrock protocol and a pinned old build breaks Bedrock.
- There is no Geyser `/versions` listing endpoint (404), which is why they cannot be pinned by
  Minecraft version the way Paper can.

`mc install --catalog <version>` was then run end to end against these APIs and produced a
correct pin file for 1.21.4 — build 232, the API's own SHA-256, Java 21, and the memory settings
already on that server — before stopping cleanly at the Java check.

## Bugs found by the tests, not by reading

Recorded because each one was silent, which is exactly the class of bug this work is most
susceptible to.

- **`log.read` skipped lines.** Trimming a page off the *front* also advanced the offset past the
  rest of the file, so following a log from the top returned the last page and then nothing.
  Paging now tiles the file: the returned offset is always the byte after the last line handed out.
- **The timestamp regex never matched.** `[12:00:00] [Server thread/INFO]: ` has a colon after the
  logger name, and the pattern required whitespace there. Every log line kept its timestamp.
- **`agent_list_servers` shaped as `server_id`.** The SQL function returns the column under its own
  name; the Edge Function renames it to `id`. The agent looks for `id`. A mismatch means the agent
  finds zero servers and reports nothing anywhere. Found by `stub_agent_api.py --self-test`, which
  exists precisely because this failure has no other symptom.
- **`commands.execute` returned `None`.** Every handler's result was discarded, so every successful
  command reported to the panel with no output.
- **`_memory` used the wrong unit multipliers**, so a valid `2G` was rejected as 2 petabytes.
- **The agent had no way to learn its assigned servers.** It only discovered them from a queued
  command, so a server that was merely *stopped* never reported `offline` and the panel showed
  nothing at all. Fixed by `0006_agent_server_discovery.sql` plus `GET /servers`.

## Not verifiable here, and how that is handled

No Docker, no Supabase CLI, no Postgres and no Deno in this environment, so a live Supabase project
and a live Edge Function could not be run. Rather than claim otherwise:

- The Edge Function typechecks under the repo's TypeScript, with a `deno-shim.d.ts` for the Deno
  globals, so it is not unverified source.
- The agent's half of the protocol is tested against `agent/tests/stub_agent_api.py`, which
  implements the same routes, headers and error codes. Discovery, command claim and completion,
  status reporting, replay refusal and bad-credential refusal are all proven against it — in
  process and as two separate OS processes talking over a socket.
- The SQL is written to run under `supabase db push`; every function sets `search_path` and is
  `SECURITY DEFINER` with a revoked public grant. It has not been executed by Postgres.
- `README.md` and `backend/README.md` list the exact steps to bring the project up against a real
  Supabase instance.