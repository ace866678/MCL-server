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

## Layout

```
VERSION              pinned versions + SHA-256 for every jar (single source of truth)
mc                   the only script you need: install, start, stop, backup, update, …
lib/common.sh        shared shell helpers
config/              seed files, copied into server/ on first install
  server.properties    network, world and player settings
  eula.txt             ships with eula=false on purpose
  ops.json whitelist.json banned-*.json
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

## Status web page (Vercel)

A Minecraft server cannot run on Vercel &mdash; functions are request-scoped and capped at 300s,
there is no UDP for Bedrock, and you do not get to choose the runtime image. So the server stays on
the VM and Vercel hosts only a small read-mostly front end:

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

`web/` also holds the Supabase-backed control plane (`NEXT_PUBLIC_SUPABASE_URL`,
`NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY`, schema in `supabase/schema.sql`); see `web/README.md`. The
bridge-backed `/admin` page works without either.

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

## Licence

MIT for the scripts and config in this repository. Minecraft and its server software are
copyrighted by their respective owners; this repository is not affiliated with or endorsed by
Mojang Studios or Microsoft.