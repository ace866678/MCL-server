# Reuse map — what was kept, what changed, and why

Every component of the original single-VM project was read before any code was written. This
records what carries over, so the transformation is a migration and not a rewrite.

## Kept as-is

| Component | Why it stays |
| --- | --- |
| `mc` (944 lines) | The whole server control surface, and it is the only part of this project with real operational history: FIFO-based console injection with no pty, `SIGTERM` → console `stop` so the world is saved, `set -Eeuo pipefail` with `errexit` deliberately not suppressed inside functions, checksum-verified downloads, `mc doctor`, `mc deploy --dry-run`. Reimplemented, this would be a regression. |
| `lib/common.sh` | `load_versions`, `sha256_of`, `check_java`, `seed_config`, `set_server_property`, `check_eula`, `jvm_flags`. Aikar-style G1 flags and the "capture `java -version` whole, never pipe it" pipefail fix are load-bearing. |
| `VERSION` | Verified against the live APIs during this work: Paper `26.2` build `129` is still the newest STABLE and its SHA-256 still matches; Geyser `2.11.3` build `1247` and Floodgate `2.2.5` build `141` are still latest. The pins are correct, and the legacy `fill-data.papermc.io` URL the script builds is what Paper's own v3 API returns. Kept as the shipped default and as the reference for how a pin looks. |
| `config/*` | `server.properties`, `eula.txt`, and the four JSON lists are exactly the seed a managed server needs. Copied into the agent as per-server templates instead of living at a fixed path. |
| `bridge/bridge.py` RCON | `Rcon._pack` / `_read_packet` / `command` (request-id matching that discards the post-auth empty `RESPONSE_VALUE` packet) and the two parsers are covered by 23 passing tests. Copied into the agent so players-online works without a second protocol implementation. |
| `bridge/bridge.py` HTTP + `/admin` | Still a genuine single-VM status page. Kept and still documented as the "one box, one server" option. |
| `web/lib/auth.ts`, `web/app/actions.ts`, `/admin` | HMAC session cookie with `timingSafeEqual`, server actions that re-check the session rather than trusting the UI. |
| `docker/`, `systemd/` | Valid ways to run the original single-server setup on a box you already own. |

## Deliberately replaced

| Component | Why |
| --- | --- |
| `agent/agent.py` | 94 lines with no installer, no version resolution, no status collection, no backup or log handling, and a command loop that would `exec` whatever `mc` subcommand arrived. It could not be extended into a managed server host without becoming a different program. The useful part — outbound-only polling with a stop event — is preserved. |
| `supabase/schema.sql` | Two tables and two policies, no command queue, no backups, no agent auth. Superseded by numbered migrations that `ALTER` the existing tables rather than replacing them, so a project that already ran the old file keeps its rows. |
| `web/app/page.tsx`, `/dashboard/*` | Rendered `<b>—</b>` for player/CPU/RAM and a hardcoded "Awaiting agent" pill, and linked to routes that did not exist. Every value is now sourced from `servers.status` + `agents.status`, which only the agent writes. |

## Changed in place, minimally

`lib/common.sh` and `mc` gain exactly what multi-tenancy needs:

- `MC_VERSION_FILE` — so each managed server has its own pin file instead of sharing the repo's
  `VERSION`. One environment variable; `load_versions` is otherwise untouched.
- `mc install --catalog` — resolve the newest STABLE build for an arbitrary Minecraft version from
  PaperMC's v3 API, write a pin file, then reuse the existing checksum-verified download path.
  The pin is still verified on every install, which is the property that matters.

Nothing else in either file was rewritten. The agent drives `mc` rather than reimplementing JVM
flags, FIFO consoles, backups or `server.properties` rewriting.

## Verified externally during the audit

- `GET https://fill.papermc.io/v3/projects/paper` → `{"versions": {"26.3": [...], "26.2": [...]}}`,
  a grouped map, so a version list has to be flattened and pre-releases filtered.
- `GET .../v3/projects/paper/versions/{mc}/builds` → `[{channel, id, downloads["server:default"].checksums.sha256}]`.
  This is the source for configurable versions; the SHA-256 comes from the API, so a version the
  project has never heard of is still checksum-verified.
- `GET https://download.geysermc.org/v2/projects/{project}/versions/latest/builds/latest` →
  `{version, build, downloads.spigot.sha256}`. Geyser and Floodgate deliberately track the latest
  build, because they follow Minecraft's Bedrock protocol and a pinned old build breaks Bedrock.
- There is no Geyser `/versions` listing endpoint (404), which is why they cannot be pinned by
  Minecraft version the way Paper can.

## Not verifiable here, and how that is handled

No Docker, no Supabase CLI and no Postgres in this environment, so a live Supabase project and a
live Deno Edge Function could not be run. Rather than claim otherwise:

- The SQL is written to run under `supabase db push`; every function sets `search_path` and is
  `SECURITY DEFINER` with a revoked public grant.
- The Edge Function's HMAC contract is implemented identically on both sides, and the agent half is
  tested against a local stub that enforces the same checks (`agent/tests/`), so the crypto, the
  canonical signing string, the replay window and the request shapes are proven. What remains
  unproven is only Deno/Postgres runtime behaviour.
- `README.md` lists the exact steps to bring the project up against a real Supabase instance.