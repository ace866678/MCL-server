# Minecraft server

A cross-play Minecraft server: **Paper** for Java clients, **Geyser + Floodgate** so Bedrock
players join the same world. Config, scripts and pinned versions live in git; worlds and player
data do not.

| | |
| --- | --- |
| Minecraft | 26.2 |
| Server core | Paper build 129 (STABLE channel) |
| Java | **25 or newer** — Paper 26.1+ will not boot on anything older |
| Cross-play | Geyser 2.11.3 (b1247) + Floodgate 2.2.5 (b141) |
| Java clients | `25565/tcp` |
| Bedrock clients | `19132/udp` |

26.3 exists but is beta-only, so 26.2 is what is pinned. `mc update` picks up the first STABLE
build and never moves you onto a pre-release.

## Two ways to run it

| | Single VM | Cloud control plane |
| --- | --- | --- |
| Servers | one, on one machine | as many as your agents host |
| Panel | `/admin`, behind `ADMIN_PASSWORD` | `/dashboard`, Supabase accounts and RLS |
| Runs on | a VPS you rent | hardware you already own, Windows or Linux |
| Management path | a tunnel to `bridge/bridge.py` | outbound HTTPS from the agent |
| Router config | yes, for players | no, for management |

Both use the same `mc` script, the same pins and the same backups. Pick the VM
path below if you only ever run one server; pick the control plane if you want
several machines under one panel. Nothing here requires the other.

## Layout

```
VERSION              pinned versions + SHA-256 for every jar (single source of truth)
mc                   the only script you need: install, start, stop, backup, update, …
lib/common.sh        shared shell helpers
lib/mcl_rcon.py      RCON client, shared by the bridge and the agent
config/              seed files, copied into server/ on first install
  server.properties    network, world and player settings
  eula.txt             ships with eula=false on purpose
  ops.json whitelist.json banned-*.json
agent/               the local Server Agent (stdlib Python only)
  agent.py             entry point: `python3 agent/agent.py`
  mcl_agent/           config, backend client, command allowlist, status, tunnel
  tests/               91 unit tests plus a stub of the Edge Function to test against
  agent.json           your credentials, gitignored, created by you
backend/             Supabase: schema, RLS, and the agent-api Edge Function
web/                 the Next.js control panel, deployed to Vercel
bridge/              HTTP bridge for the single-VM mode
systemd/             unit file for a bare cloud VM
docker/              Dockerfile, entrypoint and compose file
server/              runtime state — worlds, plugins, logs, generated configs (gitignored)
backups/             output of `mc backup` (gitignored)
```

Everything under `config/` is a **seed**. `mc install` copies each file into `server/` only if
it is not already there, so once the server has booted, `server/server.properties` is the live
config and editing `config/server.properties` changes nothing.

## Quick start

```bash
# 1. Java 25+
java -version

# 2. Accept the EULA. This is your call to make, so the installer refuses to
#    continue until you flip it.
$EDITOR config/eula.txt        # set eula=true, after reading the EULA

# 3. Download and checksum-verify Paper, Geyser and Floodgate
./mc install

# 4. Run it
./mc start
```

`mc install` refuses to install a jar whose SHA-256 does not match the pin in `VERSION`, and
refuses to seed a config file that already exists.

## Commands

| Command | What it does |
| --- | --- |
| `mc install` | Check Java, seed `server/`, download + verify the three jars |
| `mc start` | Run in the foreground; Ctrl-C sends a clean `stop` |
| `mc stop` | Ask a running server to shut down, waiting up to 120s |
| `mc restart` | `stop`, then `start` |
| `mc status` | Running state, pinned versions, ports. Exit 3 when stopped |
| `mc logs [-f] [n]` | Last `n` lines of `server/logs/latest.log` (default 50); `-f` to follow |
| `mc console <cmd>` | Send a command to the running server |
| `mc backup` | Stop if needed, zip worlds and player data into `backups/` |
| `mc update` | Report newer builds |
| `mc update --apply` | Re-pin `VERSION` and download the new jars |
| `mc deploy` | Set up a bare Ubuntu/Debian VM end to end: packages, Java 25, service user, `/opt/minecraft`, systemd, firewall. Needs root, idempotent, `--dry-run` to preview |
| `mc doctor` | Check Java, RAM, disk, permissions, jar checksums, EULA and ports; exit 1 if anything blocks a deploy |

`--yes` skips confirmation prompts: `./mc backup --yes`.

## Installing Java 25

Ubuntu 22.04's own repos top out below 25, so use Temurin:

```bash
sudo apt-get install -y wget apt-transport-https gpg
wget -qO- https://packages.adoptium.net/artifactory/api/gpg/key/public \
  | gpg --dearmor | sudo tee /usr/share/keyrings/adoptium.gpg >/dev/null
echo "deb [signed-by=/usr/share/keyrings/adoptium.gpg] \
https://packages.adoptium.net/artifactory/deb $(. /etc/os-release && echo "$VERSION_CODENAME") main" \
  | sudo tee /etc/apt/sources.list.d/adoptium.list
sudo apt-get update
sudo apt-get install -y temurin-25-jdk
```

## Heap sizing

`VERSION` sets `MIN_MEMORY` / `MAX_MEMORY` (both `4G`). Override per machine in `server.env`,
which is gitignored:

```bash
cat > server.env <<'EOF'
MIN_MEMORY=8G
MAX_MEMORY=8G
EOF
```

A rough rule: 4G is comfortable for a handful of players. More players and more loaded chunks
want more, and the JVM needs headroom above the heap for the world, so give the machine
roughly 1.5–2× `MAX_MEMORY` in RAM.

## Firewalls

Both ports have to be reachable, and they are different protocols:

```bash
sudo ufw allow 25565/tcp     # Java
sudo ufw allow 19132/udp     # Bedrock
```

Behind a cloud provider you usually need a security group as well — most default to blocking
UDP, which breaks Bedrock only, making it look like Geyser is misconfigured when it is fine.

```
# cloud providers without a security group: a plain NAT still needs this
sudo iptables -A INPUT -p udp --dport 19132 -j ACCEPT
```

## Deploying on a bare VM

One command does the whole thing:

```bash
git clone <this repo> && cd cloud-vm
sudo ./mc deploy --dry-run    # preview; changes nothing
sudo ./mc deploy
```

`mc deploy` installs the missing packages, installs Temurin Java 25 if the box has nothing new
enough, creates the `minecraft` system user, copies the repository to `/opt/minecraft`, downloads
and checksum-verifies the three jars, installs and enables the systemd unit, opens 25565/tcp and
19132/udp in `ufw`, and starts the service. It stops at the EULA on the first run and tells you to
re-run after setting `eula=true`; every step is idempotent, so re-running resumes rather than
repeats. It never touches `server/` or `backups/`, so a world from an earlier deploy survives.

Override the target with `MC_INSTALL_DIR` and `MC_SERVICE_USER` if `/opt/minecraft` is not what you
want.

<details>
<summary>Doing it by hand instead</summary>

```bash
sudo useradd --system --create-home --home-dir /opt/minecraft --shell /usr/sbin/nologin minecraft
sudo cp -r . /opt/minecraft
sudo chown -R minecraft:minecraft /opt/minecraft

sudo -u minecraft /opt/minecraft/mc install   # seeds server/, stops on the EULA
sudoedit /opt/minecraft/server/eula.txt       # set eula=true

sudo cp /opt/minecraft/systemd/minecraft.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now minecraft
journalctl -u minecraft -f
```
</details>

`mc install` has to run as `minecraft`, not as you: it writes `server/` and `backups/`, and the
unit starts the server as the same user. Run later maintenance the same way, e.g.
`sudo -u minecraft /opt/minecraft/mc backup`.

`mc start` traps `SIGTERM` and turns it into a console `stop`, and the unit sets
`KillMode=mixed`, so `systemctl stop` saves the world instead of killing it. `TimeoutStopSec` is
180s to allow for a large save. `systemctl reload minecraft` sends Paper's `reload` (plugins and
config, in place); for a full restart that keeps the world use
`sudo -u minecraft /opt/minecraft/mc restart`.

To run as your own user instead, drop the `User=`/`Group=` lines and install the unit as that
user.

## Deploying with Docker

```bash
cd docker
docker compose build
docker compose run --rm minecraft ./mc install   # seeds server/, stops on the EULA
$EDITOR server/eula.txt                          # eula=true
docker compose up -d
docker compose logs -f
```

`./server` and `./backups` are bind mounts, so worlds survive `docker compose build`.

The container's `mem_limit` is what the JVM sees as system memory, so keep it comfortably above
`MAX_MEMORY`. `stop_grace_period: 2m` matches the systemd timeout.

## Cross-play notes

- Floodgate generates its key pair on first run and Geyser finds it automatically, so there is no
  manual key copying — do not hand-edit `server/plugins/*/key.pem`.
- Bedrock usernames are prefixed with `.` to avoid colliding with Java names. Players appear as
  `.Steve` in chat and `mc console list`.
- A Bedrock player keeps one inventory across Java and Bedrock while
  `online-mode=true` in `server.properties`. Turning that off breaks it.
- Both plugins write their own `config.yml` on first run inside their folder under
  `server/plugins/`. Check `ls server/plugins` for the exact names before editing. Worth
  changing later: Floodgate's `enable-global-linking` (set it to `false` if you would rather not
  talk to Geyser's global link service) and Geyser's Bedrock port if you changed it above.

## Whitelisting

The server is open to anyone who finds the IP until you say otherwise:

```bash
./mc console whitelist on
./mc console whitelist add Steve
./mc console whitelist add .Steve     # Bedrock player, including Floodgate's "."
```

`mc console` writes straight into the server console, so every console command works here.

## Updating

```bash
./mc update             # report only
./mc update --apply     # rewrite VERSION, download, tell you to restart
```

`--apply` edits only the version and checksum lines in `VERSION`, leaving its comments alone, and
it re-downloads and re-verifies. Git history is the audit trail for what changed and when.

Paper updates are safe across builds. Geyser and Floodgate track Minecraft's Bedrock protocol,
so their latest build is always used rather than a pinned old one — if a Bedrock client suddenly
cannot connect after a Paper-only update, that is the first thing to check.

## Backups

```bash
./mc backup            # stops the server first, so the snapshot is consistent
./mc backup --yes
```

Worlds are not in git. `mc backup` writes a timestamped zip to `backups/` covering the overworld,
nether, end, player data, plugin data and `server.properties`; jars are skipped because
`VERSION` can always re-fetch them. Cron it, and copy the zips somewhere the VM cannot delete.

## Cloud control plane (Vercel + Supabase + local agent)

This is the multi-server mode. The Vercel app is a control panel and nothing else:
no Minecraft process, no world data, no credential for the machine running the
game. The game runs on hardware you already own, and a small agent on that
hardware dials out to the control plane.

```
Vercel (Next.js, web/)          Supabase                  your machine
┌──────────────────────┐        ┌──────────────────┐      ┌──────────────────────────┐
│ dashboard, sign-in  │───────▶│ Postgres + RLS   │◀─────│ agent/agent.py           │
│ server control panel│  JWT   │ Edge Function    │ HTTPS│   └─ mc install/start/...│
└──────────────────────┘        └──────────────────┘      │   └─ Paper + Geyser      │
                                       ▲                  │   └─ RCON 127.0.0.1      │
                                       └──────────────────┴──────────────────────────┘
```

The agent only ever makes **outbound** HTTPS requests. It never listens on a
port, so hosting a server on a home network needs no port forwarding and no
inbound firewall rule for *management*. Players still need the game port
reachable — see [Networking](#networking).

### What the agent never does

* No shell, ever. Handlers build a fixed list of argv elements and call
  `subprocess` with `shell=False`. Nothing from the network is concatenated into
  a command line, so metacharacters are inert data.
* Only the ten commands in `server_commands.command`'s `CHECK` constraint. The
  agent's `commands.ALLOWED` mirrors that list and a test asserts they agree; a
  command in the database but not in the agent is refused, not guessed at.
* Every argument validated against its own bounds before use. `server.console`
  is filtered against a real allowlist of read-only and low-impact Minecraft
  commands, because Minecraft's own parser contains operator commands.
* Stopping the agent never stops a server. Game servers are spawned detached and
  outlive an agent restart, so a panel reload cannot take a world offline.

### Deploy the control plane

```bash
cd backend
supabase login
supabase link --project-ref <your-project-ref>
supabase db push                                        # schema first
supabase functions deploy agent-api --no-verify-jwt      # then the function
```

Then set on the Vercel project:

| Variable | Purpose |
| --- | --- |
| `NEXT_PUBLIC_SUPABASE_URL` | your project URL, e.g. `https://abc.supabase.co` |
| `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY` | anon key, used from the browser |
| `SUPABASE_SERVICE_ROLE_KEY` | service-role key, used by the Edge Function. **Server-only** — never prefix this with `NEXT_PUBLIC_` |

Full details, including the header contract and the reasoning behind digest-only
credentials, are in [`backend/README.md`](backend/README.md).

### Register an agent

1. Sign in at `/login`, then **Connect an agent** on the dashboard.
2. Give it a name and pick the platform. The panel shows a three-line block
   **once**; the token is stored only as its SHA-256.

   ```ini
   # agent/agent.json  (gitignored)
   {
     "agent_id": "3f2504e0-4f89-41d3-9a0c-0305e82c3301",
     "token":    "<the token from the dashboard>",
     "api_url":  "https://abc.supabase.co/functions/v1/agent-api",
     "data_dir": "./data",
     "repo_dir": ".."
   }
   ```

3. Install Java and start the agent:

   ```bash
   python3 agent/agent.py check    # verifies config and that the backend accepts you
   python3 agent/agent.py          # runs until Ctrl-C or SIGTERM
   ```

   Environment variables `MCL_AGENT_ID`, `MCL_AGENT_TOKEN`, `MCL_API_URL`,
   `MCL_DATA_DIR`, `MCL_REPO_DIR` and `MCL_HEARTBEAT_SECONDS` all override the
   file, which is what the systemd unit uses.

   To run it as a service instead:

   ```bash
   sudo install -m 644 systemd/mcl-agent.service /etc/systemd/system/
   sudo install -m 600 /dev/null /etc/mcl-agent.env   # then add MCL_AGENT_ID=…, MCL_AGENT_TOKEN=…, MCL_API_URL=…
   sudo systemctl enable --now mcl-agent
   journalctl -u mcl-agent -f
   ```

### Create a server

**New server** picks the agent and a Minecraft version. The panel sends a server
row; the agent notices it on its next poll and reports `offline`. Then open the
server and use **Install** — the agent resolves that version against PaperMC,
downloads the build, and verifies its SHA-256 against the digest Paper publishes
before running it. Java is chosen per Minecraft version (Paper says so; it is not
hardcoded), and the pins are written to that server's own `version.env`.

## Networking

A Minecraft server speaks raw TCP. Nothing in this platform makes that reachable
from the internet on its own, and no built-in provider pretends otherwise:

| Provider | `tunnel.provider` | What it does |
| --- | --- | --- |
| Direct | `direct` (default) | Nothing. Reports the local address and says reachability is *unknown* unless the operator has set up port forwarding. |
| playit.gg | `playit` | Supervises the `playit` binary, which does forward raw TCP. Claim token goes in `agent.json`, never to the backend. |
| Anything else | `command` | Runs your own relay argv — frpc, ngrok, a reverse SSH tunnel. `{port}` is substituted. |

```json
"tunnel": { "provider": "command", "args": ["frpc", "-c", "/path/to/frpc.ini", "--server_port", "{port}"] }
```

`cloudflared`'s quick tunnel is *not* one of these: it terminates HTTP and
WebSocket and will not carry a game client's TCP connection. An operator who
believes their server is reachable, and is not, is the worst outcome here, so
`direct` reports what it can observe and stops there.

## Status web page (single-VM bridge)

The original single-machine mode still works, and is the simpler option if you
only ever run one server on one box:

```
Vercel (Next.js, web/)  ──HTTPS──▶  tunnel  ──▶  bridge (bridge/bridge.py, 127.0.0.1:8787)
                                                          │
                                                          ├─ RCON 127.0.0.1:25575  status, player list
                                                          └─ mc console / mc backup
```

`mc deploy` already enables RCON on loopback, writes the secrets to `/etc/minecraft-bridge.env`
(mode 600) and installs `minecraft-bridge.service`. Expose the bridge with a tunnel, then set the
variables below and point the Vercel project's root directory at `web`:

| Variable | Where it comes from |
| --- | --- |
| `MINECRAFT_BRIDGE_URL` | your tunnel URL, forwarding to `127.0.0.1:8787` |
| `MINECRAFT_BRIDGE_TOKEN` | `BRIDGE_TOKEN` in `/etc/minecraft-bridge.env` |
| `ADMIN_PASSWORD` | anything you choose; gates `/admin` |

`/admin` is this mode's control panel. The Supabase-backed server pages under
`/dashboard` are the other mode; they work without any of the three variables
above.

With `cloudflared` installed, a tunnel is one command and needs no open inbound port:

```bash
cloudflared tunnel --no-autoupdate run --url http://127.0.0.1:8787
```

Two things stay off the internet on purpose: RCON (it can run server commands) is bound to
loopback and `ufw deny 25575`, and the bridge itself binds to `127.0.0.1`. The bridge token never
reaches the browser &mdash; pages fetch server-side &mdash; and `/admin` needs the admin password
before it will touch anything.

The bridge degrades rather than breaks: if RCON is unreachable, `/status` still reports the running
state from the pid file and the page says player counts are approximate.

## When it will not deploy

Start here:

```bash
./mc doctor
```

It is read-only, safe on a fresh clone before `install`, and prints one line per check —
Java version against what Paper needs, free RAM and disk, whether `server/` is writable by the
user you are, whether each jar still matches its pin, the EULA, and whether 25565/19132 are
already taken. It exits 1 if anything would block a deploy, so it is also usable from a deploy
script. Paste its output into a bug report; it contains nothing private.

The checks it cannot make from inside the box:

- **Cloud security group / NAT.** 25565/tcp and 19132/udp must be allowed inbound. Providers
  commonly drop UDP, which leaves Java players working and only Bedrock broken.
- **A JVM that boots and then exits.** Read `server/logs/latest.log` (`./mc logs -f`) for the real
  reason; Paper's own error is at the bottom.

## What is deliberately not here

No plugin set beyond cross-play. EssentialsX, LuckPerms, CoreProtect and friends all work on
Paper, and adding them means committing to keeping them updated — drop them into
`server/plugins/` and Paper loads them on the next boot. `mc backup` already picks up their
data directories.

Minecraft itself, Paper, Geyser and Floodgate are not redistributed here. `mc install` fetches
them at the pinned, checksum-verified versions.

## Tests

```bash
python3 agent/tests/test_agent.py            # 91 tests, stdlib only
python3 agent/tests/stub_agent_api.py --self-test   # agent ↔ Edge Function protocol
python3 bridge/test_bridge.py                 # 23 tests
bash -n mc && ./mc doctor                     # shell syntax and environment
npm run build && npm run typecheck            # the panel
```

`stub_agent_api.py --self-test` runs the real `AgentService` against an in-process stand-in for
the Edge Function: same routes, same headers, same error codes. It is how the agent's protocol
side gets tested without Deno, Docker or a Supabase project. The same file can be run as a
server (`--port 8788`) if you want to point a real agent at it; it binds loopback only and is a
fixture, not a server.

The self-test is also what caught the one bug that mattered most in building this: the SQL
function returns a server row with the column named `server_id`, and the Edge Function renames
it to `id`. Get that wrong and the agent simply finds no servers, with no error anywhere.

## Licence

MIT for the scripts and config in this repository. Minecraft and its server software are
copyrighted by their respective owners; this repository is not affiliated with or endorsed by
Mojang Studios or Microsoft.