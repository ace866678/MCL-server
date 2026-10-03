#!/usr/bin/env bash
#
# mc — control script for the Minecraft server in this repository.
#
# Paper (Java edition) with Geyser + Floodgate, so Java and Bedrock players
# share one world. Everything the server needs is pinned in VERSION and every
# jar is checksum-verified before it is trusted.
#
set -Eeuo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "$REPO_ROOT/lib/common.sh"

# ---- helpers ----------------------------------------------------------------

usage() {
  cat <<'USAGE'
mc — Paper + Geyser/Floodgate Minecraft server

Usage: mc <command> [options]

Commands
  install          Verify Java, seed config, download + checksum the jars
  start            Run the server in the foreground (Ctrl-C stops it cleanly)
  stop             Ask a running server to shut down
  restart          stop, then start
  status           Report whether the server is up, plus memory settings
  logs [-f] [n]    Print the last n lines of the server log (default 50; -f to follow)
  console <cmd>    Send a command to the running server, e.g. `mc console op Steve`
  backup           Stop-and-zip a consistent snapshot into backups/
  update [--apply] Show newer Paper/Geyser/Floodgate builds; --apply re-pins
  deploy [--dry-run]  Set up a bare Ubuntu VM end to end (root, idempotent)
  doctor           Check everything a deploy needs and report what is wrong
  help             This text

Options
  --yes            Skip the confirmation prompt on `backup` and `update --apply`

Examples
  mc install
  mc start
  mc console op Steve
  mc update --apply
  mc doctor
USAGE
}

confirm() {
  local prompt="$1"
  if [[ "${MC_ASSUME_YES:-0}" == "1" ]]; then
    return 0
  fi
  local reply
  read -r -p "$prompt [y/N] " reply
  [[ "$reply" == "y" || "$reply" == "Y" ]]
}

# Downloads a jar to dest, skipping the transfer when the existing copy already
# matches the pinned checksum. Refuses to leave a mismatched file in place.
fetch_jar() {
  local url="$1" dest="$2" expected="$3" label="$4"

  if [[ -f "$dest" ]]; then
    local actual
    actual="$(sha256_of "$dest")"
    if [[ "$actual" == "$expected" ]]; then
      ok "$label already present and verified"
      return 0
    fi
    warn "$label checksum mismatch, re-downloading"
    rm -f "$dest"
  fi

  mkdir -p "$(dirname "$dest")"
  log "downloading $label"
  dim "$url"

  local tmp="$dest.part"
  # curl's progress meter is one line of carriage returns per update, which is
  # unreadable in a systemd journal or `docker logs`. Only show it to a terminal.
  local meter=(-sS)
  [[ -t 1 ]] && meter=(--progress-bar)
  if ! curl -fSL --retry 3 --retry-delay 2 "${meter[@]}" -o "$tmp" "$url"; then
    rm -f "$tmp"
    die "download failed for $label"
  fi

  local actual
  actual="$(sha256_of "$tmp")"
  if [[ "$actual" != "$expected" ]]; then
    rm -f "$tmp"
    err "checksum mismatch for $label"
    dim "expected $expected"
    dim "actual   $actual"
    die "refusing to install $label"
  fi

  mv -f "$tmp" "$dest"
  ok "$label installed and verified"
}

# ---- commands ---------------------------------------------------------------

cmd_install() {
  load_versions
  load_local_env
  require_cmd curl

  log "Minecraft ${MINECRAFT_VERSION} — Paper build ${PAPER_BUILD}, Java ${JAVA_MAJOR}+"
  check_java

  mkdir -p "$SERVER_DIR" "$PLUGINS_DIR"
  seed_config
  check_eula

  fetch_jar "$(paper_url)" "$PAPER_JAR" "$PAPER_SHA256" \
    "paper ${MINECRAFT_VERSION} build ${PAPER_BUILD}"
  fetch_jar "$(geyser_url)" "$GEYSER_JAR" "$GEYSER_SHA256" \
    "geyser ${GEYSER_VERSION} build ${GEYSER_BUILD}"
  fetch_jar "$(floodgate_url)" "$FLOODGATE_JAR" "$FLOODGATE_SHA256" \
    "floodgate ${FLOODGATE_VERSION} build ${FLOODGATE_BUILD}"

  echo
  ok "install complete"
  dim "Java clients:      <your-host>:25565"
  dim "Bedrock clients:   <your-host>:19132   (needs UDP 19132 open too)"
  dim "start with:        mc start"
}

cmd_start() {
  load_versions
  load_local_env
  check_java
  check_eula

  [[ -f "$PAPER_JAR" ]] || die "paper.jar missing. Run 'mc install' first."
  if [[ -f "$PLUGINS_DIR/geyser.jar" && ! -f "$PLUGINS_DIR/floodgate.jar" ]]; then
    warn "geyser is installed but floodgate is not — Bedrock players cannot join"
  fi

  local pid
  if pid="$(server_pid)"; then
    die "already running as pid $pid. Use 'mc stop' first."
  fi
  rm -f "$PID_FILE"

  local opts
  mapfile -t opts < <(jvm_flags)

  log "starting Minecraft ${MINECRAFT_VERSION} (${MIN_MEMORY}–${MAX_MEMORY})"

  # The console FIFO: opened read-write so this shell is simultaneously a reader
  # and a writer. Java takes fd 3 as stdin, so the server never sees EOF on its
  # console and `mc console` can inject commands with no pty in between.
  rm -f "$FIFO"
  mkfifo -m 600 "$FIFO"
  exec 3<>"$FIFO"

  java \
    "-Xms${MIN_MEMORY}" \
    "-Xmx${MAX_MEMORY}" \
    "${opts[@]}" \
    -jar "$PAPER_JAR" \
    --nogui <&3 &
  local java_pid=$!
  printf '%s' "$java_pid" > "$PID_FILE"

  # Ctrl-C and `systemctl stop` must save the world, not kill it. Forward a
  # console `stop` and keep waiting so the JVM shutdown hook can finish.
  forward_stop() {
    warn "shutting down (console stop)"
    printf 'stop\n' > "$FIFO" 2>/dev/null || true
  }
  trap forward_stop INT TERM

  local rc=0
  # `wait` returns 128+signo when a trapped signal interrupts it, and the child
  # is still alive — so loop until it is really gone.
  while kill -0 "$java_pid" 2>/dev/null; do
    wait "$java_pid" && rc=0 || rc=$?
    (( rc < 128 )) && break
  done
  trap - INT TERM

  rm -f "$PID_FILE" "$FIFO"
  exec 3>&- 3<&-

  if (( rc == 0 )); then
    log "server stopped"
  else
    err "server exited with status $rc — see server/logs/latest.log"
  fi
  return "$rc"
}

cmd_stop() {
  local pid
  if ! pid="$(server_pid)"; then
    warn "server is not running"
    return 0
  fi
  log "stopping server (pid $pid)"
  printf 'stop\n' > "$FIFO" 2>/dev/null || die "console fifo is gone; try 'kill $pid'"

  local waited=0
  while kill -0 "$pid" 2>/dev/null; do
    sleep 1
    waited=$((waited + 1))
    if (( waited >= 120 )); then
      err "server did not stop within 120s"
      return 1
    fi
  done
  ok "server stopped cleanly"
}

cmd_restart() {
  cmd_stop
  cmd_start
}

cmd_status() {
  load_versions
  # server.env can move the heap off the VERSION default, so read it before
  # reporting what the server would actually run with.
  load_local_env
  local pid major rc=0
  if pid="$(server_pid)"; then
    ok "running (pid $pid)"
  else
    warn "not running"
    rc=3
  fi
  dim "minecraft  ${MINECRAFT_VERSION}  paper build ${PAPER_BUILD}"
  dim "heap      ${MIN_MEMORY} – ${MAX_MEMORY}"
  dim "geyser    ${GEYSER_VERSION} build ${GEYSER_BUILD}"
  dim "floodgate ${FLOODGATE_VERSION} build ${FLOODGATE_BUILD}"
  dim "ports     25565/tcp (java), 19132/udp (bedrock)"
  if command -v java >/dev/null 2>&1 && major="$(java_major)"; then
    dim "java      ${major}"
  else
    dim "java      not found"
  fi
  return "$rc"
}

cmd_logs() {
  local follow=0 lines=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      -f|--follow) follow=1; shift ;;
      -*)           die "unknown option for mc logs: $1" ;;
      *)
        [[ -z "$lines" ]] || die "usage: mc logs [-f] [lines]"
        lines="$1"
        shift
        ;;
    esac
  done

  local log_file="$SERVER_DIR/logs/latest.log"
  [[ -f "$log_file" ]] || die "no log yet at $log_file. Has the server ever started?"

  # Snapshot by default so `mc logs` can be used in a script; -f to stream.
  if (( follow )); then
    dim "following $log_file — Ctrl-C to stop"
    tail -n "${lines:-50}" -f "$log_file"
  else
    tail -n "${lines:-50}" "$log_file"
  fi
}

cmd_console() {
  [[ $# -gt 0 ]] || die "usage: mc console <command>"
  server_pid >/dev/null || die "server is not running"
  printf '%s\n' "$*" > "$FIFO"
  ok "sent: $*"
}

cmd_backup() {
  load_versions
  load_local_env
  require_cmd zip
  local pid
  if pid="$(server_pid)"; then
    confirm "Server is running (pid $pid). Stop it for a consistent snapshot?" \
      || die "cancelled"
    cmd_stop
  fi

  [[ -d "$SERVER_DIR" ]] || die "server/ does not exist yet. Run 'mc install'."

  # Build an explicit list: `zip` exits non-zero on a pattern that matches
  # nothing, and worlds/ directories only exist once they have been visited.
  local -a items=()
  local item
  for item in world world_nether world_the_end playerdata; do
    [[ -e "$SERVER_DIR/$item" ]] && items+=("$item")
  done
  for item in whitelist.json ops.json banned-players.json banned-ips.json server.properties; do
    [[ -f "$SERVER_DIR/$item" ]] && items+=("$item")
  done
  while IFS= read -r -d '' dir; do
    items+=("${dir#"$SERVER_DIR/"}")
  done < <(find "$SERVER_DIR/plugins" -mindepth 1 -maxdepth 1 -type d -print0 2>/dev/null)

  (( ${#items[@]} > 0 )) || die "nothing worth backing up yet in server/"

  mkdir -p "$BACKUP_DIR"
  local stamp archive
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  archive="$BACKUP_DIR/server-${MINECRAFT_VERSION}-${stamp}.zip"

  log "writing $archive"
  # Selections first, -x last: zip stops parsing options at the first bare
  # argument, so an exclusion placed earlier consumes the whole file list.
  # Jars are excluded because they are re-fetchable from VERSION any time.
  ( cd "$SERVER_DIR" && zip -qr "$archive" "${items[@]}" -x '*.jar' )
  [[ -s "$archive" ]] || die "zip produced nothing at $archive"
  ok "$(du -h "$archive" | cut -f1) at $archive"
  dim "back up regularly — worlds are not in git"
}

# Resolves the newest published build for each pinned component, preferring the
# Paper STABLE channel over alpha/beta so an update never lands a server on a
# pre-release. Sets NEW_* globals.
resolve_updates() {
  local builds project meta
  builds="$(curl -fsSL --max-time 30 \
    "https://fill.papermc.io/v3/projects/paper/versions/${MINECRAFT_VERSION}/builds")"

  # sort_by/last rather than max(): jq only grew max() in 1.7, and Ubuntu 22.04
  # still ships 1.6.
  NEW_PAPER_BUILD="$(printf '%s' "$builds" | json_eval \
    '[.[] | select(.channel == "STABLE")] | sort_by(.id) | last | .id' \
    'max(b["id"] for b in d if b["channel"] == "STABLE")')"

  NEW_PAPER_SHA="$(printf '%s' "$builds" | json_eval \
    '.[] | select(.id == '"$NEW_PAPER_BUILD"') | .downloads."server:default".checksums.sha256' \
    'next(b["downloads"]["server:default"]["checksums"]["sha256"] for b in d if b["id"] == '"$NEW_PAPER_BUILD"')')"

  [[ -n "$NEW_PAPER_BUILD" && "$NEW_PAPER_BUILD" != "null" ]] \
    || die "no STABLE paper build published for ${MINECRAFT_VERSION}"
  [[ -n "$NEW_PAPER_SHA" ]] || die "could not read the checksum for paper build ${NEW_PAPER_BUILD}"

  local project meta prefix version build sha
  for project in geyser floodgate; do    [[ "$project" == "geyser" ]] && prefix="NEW_GEYSER_" || prefix="NEW_FLOODGATE_"
    meta="$(curl -fsSL --max-time 30 \
      "https://download.geysermc.org/v2/projects/${project}/versions/latest/builds/latest")"
    version="$(printf '%s' "$meta" | json_eval '.version' 'd["version"]')"
    build="$(printf '%s' "$meta" | json_eval '.build' 'd["build"]')"
    sha="$(printf '%s' "$meta" | json_eval '.downloads.spigot.sha256' \
      'd["downloads"]["spigot"]["sha256"]')"
    [[ -n "$version" && -n "$build" && -n "$sha" ]] \
      || die "could not read the latest ${project} build metadata"
    printf -v "${prefix}VERSION" '%s' "$version"
    printf -v "${prefix}BUILD" '%s' "$build"
    printf -v "${prefix}SHA256" '%s' "$sha"
  done
}

cmd_update() {
  load_versions
  load_local_env
  require_cmd curl
  local apply=0
  [[ "${1:-}" == "--apply" ]] && apply=1

  log "checking for newer builds"
  resolve_updates

  local changes=0 label
  # Compare a pinned value against what the API reports right now.
  compare() {
    local old="$1" new="$2"
    if [[ "$old" == "$new" ]]; then
      ok "$label: $old (current)"
    else
      warn "$label: $old -> $new"
      changes=$((changes + 1))
    fi
  }

  label="paper ${MINECRAFT_VERSION} build"
  compare "$PAPER_BUILD" "$NEW_PAPER_BUILD"
  label="geyser"
  compare "$GEYSER_VERSION build $GEYSER_BUILD" "$NEW_GEYSER_VERSION build $NEW_GEYSER_BUILD"
  label="floodgate"
  compare "$FLOODGATE_VERSION build $FLOODGATE_BUILD" \
    "$NEW_FLOODGATE_VERSION build $NEW_FLOODGATE_BUILD"

  (( changes == 0 )) && { ok "nothing to do"; return 0; }

  (( apply == 0 )) && {
    echo
    dim "re-pin and download with: mc update --apply"
    return 0
  }

  confirm "Apply ${changes} update(s) to VERSION and re-download?" || die "cancelled"

  set_pin() {
    local key="$1" value="$2" tmp
    tmp="$(mktemp)"
    awk -v key="$key" -v value="$value" '
      index($0, key "=") == 1 { print key "=" value; next }
      { print }
    ' "$VERSION_FILE" > "$tmp"
    mv -f "$tmp" "$VERSION_FILE"
  }
  set_pin PAPER_BUILD "$NEW_PAPER_BUILD"
  set_pin PAPER_SHA256 "$NEW_PAPER_SHA"
  set_pin GEYSER_VERSION "$NEW_GEYSER_VERSION"
  set_pin GEYSER_BUILD "$NEW_GEYSER_BUILD"
  set_pin GEYSER_SHA256 "$NEW_GEYSER_SHA256"
  set_pin FLOODGATE_VERSION "$NEW_FLOODGATE_VERSION"
  set_pin FLOODGATE_BUILD "$NEW_FLOODGATE_BUILD"
  set_pin FLOODGATE_SHA256 "$NEW_FLOODGATE_SHA256"
  ok "VERSION updated"

  # Re-read the pins so the fetches below use the new values, then let a local
  # server.env override heap sizing again.
  unset PAPER_BUILD PAPER_SHA256 GEYSER_VERSION GEYSER_BUILD GEYSER_SHA256
  unset FLOODGATE_VERSION FLOODGATE_BUILD FLOODGATE_SHA256
  load_versions
  load_local_env

  fetch_jar "$(paper_url)" "$PAPER_JAR" "$PAPER_SHA256" \
    "paper ${MINECRAFT_VERSION} build ${PAPER_BUILD}"
  fetch_jar "$(geyser_url)" "$GEYSER_JAR" "$GEYSER_SHA256" \
    "geyser ${GEYSER_VERSION} build ${GEYSER_BUILD}"
  fetch_jar "$(floodgate_url)" "$FLOODGATE_JAR" "$FLOODGATE_SHA256" \
    "floodgate ${FLOODGATE_VERSION} build ${FLOODGATE_BUILD}"
  ok "update installed — restart with 'mc restart'"
}

# ---- deploy -----------------------------------------------------------------

# One command for a bare Ubuntu/Debian VM: prerequisites, Java 25, the service
# user, /opt/minecraft, the systemd unit and the firewall rules. Idempotent, so
# re-running after fixing one problem picks up where it left off.
cmd_deploy() {
  local dry_run=0
  local -a deploy_args=()
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --dry-run|-n) dry_run=1; deploy_args+=("$1"); shift ;;
      -h|--help)    printf 'usage: mc deploy [--dry-run]\n'; return 0 ;;
      *)            die "unknown option for mc deploy: $1" ;;
    esac
  done

  load_versions
  load_local_env

  local install_dir="${MC_INSTALL_DIR:-/opt/minecraft}"
  local svc_user="${MC_SERVICE_USER:-minecraft}"
  local unit="minecraft.service"

  local step_no=0
  step() { printf '\n%s[%d/%d] %s%s\n' "$C_BLUE" "$((++step_no))" 10 "$*" "$C_RESET"; }
  # Prints instead of executing under --dry-run, so the plan is reviewable.
  run() {
    if (( dry_run )); then
      printf '     would run: %s\n' "$*"
      return 0
    fi
    "$@"
  }
  # Tolerant secret lookup: on a first deploy the file does not exist yet, and
  # under `set -e` + pipefail a plain sed on a missing file would abort the run.
  read_secret() {
    [[ -r "$1" ]] || return 0
    local value
    value="$(sed -n "s/^$2=//p" "$1" | head -1)" || return 0
    printf '%s' "$value"
  }

  # Random alphanumeric string. dd reads an exact count so nothing downstream is
  # left holding a broken pipe -- `head -c` inside a pipeline can SIGPIPE the
  # producer and fail the whole command under pipefail.
  random_secret() {
    local raw
    raw="$(dd if=/dev/urandom bs="$1" count=1 2>/dev/null | base64 | tr -dc 'A-Za-z0-9')" || return 1
    printf '%s' "${raw:0:$1}"
  }

  # Run as the service user. Already root by now, and sudo is not installed on
  # every minimal image, so prefer runuser from util-linux.
  as_svc() {
    if (( EUID == 0 )); then
      runuser -u "$svc_user" -- "$@"
    else
      sudo -u "$svc_user" "$@"
    fi
  }

  if (( dry_run )); then
    log "would deploy to $install_dir as $svc_user (dry run, nothing is changed)"
  else
    log "deploying to $install_dir as $svc_user"
  fi

  if (( ! dry_run )); then
    if (( EUID != 0 )); then
      if command -v sudo >/dev/null 2>&1; then
        warn "needs root; re-running under sudo"
        # Carry the original flags across, or `mc deploy --dry-run` silently
        # becomes a real deploy the moment it re-executes itself.
        exec sudo -- "$0" deploy ${deploy_args[@]+"${deploy_args[@]}"}
      fi
      die "needs root. Re-run with sudo."
    fi
    [[ -r /etc/os-release ]] || die "cannot identify this OS"
    # shellcheck disable=SC1091
    . /etc/os-release
    [[ "$ID" == "ubuntu" || "$ID" == "debian" ]] \
      || die "mc deploy only supports Debian/Ubuntu (found $PRETTY_NAME). Install Java $JAVA_MAJOR yourself, then run 'mc install'."
  fi

  step "host packages"
  local missing=()
  local pkg
  for pkg in curl zip unzip ca-certificates; do
    case "$pkg" in
      curl|zip|unzip) command -v "$pkg" >/dev/null 2>&1 || missing+=("$pkg") ;;
    esac
  done
  local apt_pkgs=(ca-certificates)
  (( ${#missing[@]} )) && apt_pkgs+=("${missing[@]}")
  if (( ${#apt_pkgs[@]} == 1 )) && command -v curl >/dev/null 2>&1 && [[ -f /etc/ssl/certs/ca-certificates.crt ]]; then
    ok "curl, zip, unzip and CA certificates already present"
  else
    run env DEBIAN_FRONTEND=noninteractive apt-get update -qq
    run env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "${apt_pkgs[@]}"
  fi

  step "java $JAVA_MAJOR+"
  local jmajor=""
  if command -v java >/dev/null 2>&1; then
    jmajor="$(java_major || true)"
  fi
  if [[ -n "$jmajor" ]] && (( jmajor >= JAVA_MAJOR )); then
    ok "java $jmajor already installed"
  else
    [[ -n "$jmajor" ]] && warn "java $jmajor is too old for Paper $MINECRAFT_VERSION"
    # Adoptium's signed apt repo, per README. Verified to carry temurin-$JAVA_MAJOR-jdk.
    run apt-get install -y -qq wget gnupg
    run bash -c 'wget -qO- https://packages.adoptium.net/artifactory/api/gpg/key/public | gpg --dearmor --yes -o /usr/share/keyrings/adoptium.gpg'
    run bash -c 'echo "deb [signed-by=/usr/share/keyrings/adoptium.gpg] https://packages.adoptium.net/artifactory/deb $(. /etc/os-release && echo "$VERSION_CODENAME") main" > /etc/apt/sources.list.d/adoptium.list'
    run env DEBIAN_FRONTEND=noninteractive apt-get update -qq
    run env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "temurin-${JAVA_MAJOR}-jdk"
  fi

  step "service account"
  if id "$svc_user" >/dev/null 2>&1; then
    ok "$svc_user exists"
  else
    run useradd --system --create-home --home-dir "$install_dir" \
                --shell /usr/sbin/nologin "$svc_user"
    (( dry_run )) || ok "$svc_user created"
  fi

  step "install files"
  if (( dry_run )); then
    printf '     would copy this repo to %s (excluding .git, server/, backups/)\n' "$install_dir"
  else
    # Guard the one destructive thing below: rm -rf is only ever reached with a
    # path built from install_dir plus a name we did not copy.
    case "$install_dir" in
      /*) ;;
      *)  die "MC_INSTALL_DIR must be an absolute path (got '$install_dir')" ;;
    esac
    [[ "$install_dir" != "/" && "$install_dir" != "/opt" && "$install_dir" != "/usr" ]] \
      || die "refusing to use '$install_dir' as the install directory"

    mkdir -p "$install_dir"
    # Copy rather than symlink: the unit points at a real path, and a git pull
    # in the source checkout must not be able to swap the code out from under a
    # running server.
    local item rel dest
    while IFS= read -r -d '' item; do
      rel="${item#"$REPO_ROOT"/}"
      case "$rel" in
        # server/backups hold live world data and are never copied or removed;
        # web/ builds on Vercel, so shipping node_modules to the VM is waste.
        server|backups|.git|web) continue ;;
      esac
      dest="$install_dir/$rel"
      # Replace first, so re-running does not nest directories.
      rm -rf "$dest"
      mkdir -p "$(dirname "$dest")"
      cp -rp "$item" "$dest"
    done < <(find "$REPO_ROOT" -mindepth 1 -maxdepth 1 \
               ! -name '.git' ! -name 'server' ! -name 'backups' ! -name 'web' -print0)
    ok "copied repository to $install_dir"
    run chown -R "$svc_user":"$svc_user" "$install_dir"
  fi

  step "download and verify the jars"
  if (( dry_run )); then
    printf '     would run: %s %s/mc install\n' "$( (( EUID == 0 )) && echo "runuser -u $svc_user --" || echo "sudo -u $svc_user" )" "$install_dir"
  else
    if run as_svc "$install_dir/mc" install; then
      ok "jars installed and checksum-verified"
    else
      err "'mc install' did not complete — the usual cause is the EULA"
      printf '     1. read https://aka.ms/MinecraftEULA\n'
      printf '     2. set eula=true in %s/server/eula.txt\n' "$install_dir"
      printf '     3. re-run the same command; it resumes from here\n'
      return 1
    fi
  fi

  step "systemd"
  if ! command -v systemctl >/dev/null 2>&1; then
    warn "systemctl not found — skipping the unit; start with 'mc start' yourself"
  else
    run cp "$install_dir/systemd/$unit" "/etc/systemd/system/$unit"
    run systemctl daemon-reload
    run systemctl enable "$unit"
    (( dry_run )) || ok "unit installed and enabled"
  fi

  step "RCON for player counts"
  # The status page needs to ask the server who is online, and RCON is the only
  # interface that answers that. Enable it with a generated password, and keep
  # the port on loopback: `ufw deny` still allows 127.0.0.1 but drops the public
  # path, which is the one thing that must not leak.
  local rcon_pw secrets_file="/etc/minecraft-bridge.env"
  if (( dry_run )); then
    dim "would enable RCON on 127.0.0.1:25575 with a generated password"
  else
    rcon_pw="$(read_secret "$secrets_file" RCON_PASSWORD)"
    if [[ -z "$rcon_pw" ]]; then
      rcon_pw="$(random_secret 24)"
      local token
      token="$(random_secret 40)"
      ( umask 077
        {
          printf '# Written by mc deploy: the RCON password and the token the\n'
          printf '# web app authenticates with. Keep this file private.\n'
          printf 'RCON_PASSWORD=%s\n' "$rcon_pw"
          printf 'BRIDGE_TOKEN=%s\n' "$token"
        } > "$secrets_file" )
      ok "wrote $secrets_file (mode 600)"
    else
      ok "reusing the existing secrets in $secrets_file"
    fi

    set_server_property enable-rcon true "$install_dir/server/server.properties"
    set_server_property rcon.port 25575 "$install_dir/server/server.properties"
    set_server_property rcon.password "$rcon_pw" "$install_dir/server/server.properties"
    chown "$svc_user":"$svc_user" "$install_dir/server/server.properties"
    ok "RCON enabled on 127.0.0.1:25575 (a server restart applies it)"
  fi

  step "bridge for the web app"
  local bridge_unit="minecraft-bridge.service"
  if ! command -v systemctl >/dev/null 2>&1; then
    dim "no systemd: run bridge/bridge.py yourself with RCON_PASSWORD and BRIDGE_TOKEN set"
  else
    run cp "$install_dir/systemd/$bridge_unit" "/etc/systemd/system/$bridge_unit"
    run systemctl daemon-reload
    run systemctl enable --now "$bridge_unit"
    (( dry_run )) || ok "bridge installed on 127.0.0.1:8787"
  fi

  step "firewall"
  if command -v ufw >/dev/null 2>&1; then
    run ufw allow 25565/tcp
    run ufw allow 19132/udp
    # RCON can run server commands. Loopback still works; the internet does not.
    run ufw deny 25575/tcp
  else
    dim "ufw not installed — allow 25565/tcp and 19132/udp in your cloud security group"
  fi

  if (( dry_run )); then
    printf '\n%s dry run: nothing was changed%s\n' "$C_YELLOW" "$C_RESET"
    return 0
  fi

  step "start"
  if command -v systemctl >/dev/null 2>&1; then
    run systemctl restart "$unit"
    dim "tail the log with: journalctl -u $unit -f"
  else
    dim "not started (no systemd); run: sudo -u $svc_user $install_dir/mc start"
  fi

  printf '\n'
  ok "deploy finished"
  dim "status    sudo -u $svc_user $install_dir/mc status"
  dim "console   sudo -u $svc_user $install_dir/mc console op <player>"
  dim "problems? sudo -u $svc_user $install_dir/mc doctor"
  dim "remember to allow 25565/tcp and 19132/udp in the cloud security group too"
  echo
  dim "the web app in web/ needs these Vercel environment variables:"
  if (( ! dry_run )) && [[ -r "$secrets_file" ]]; then
    dim "  MINECRAFT_BRIDGE_TOKEN=$(read_secret "$secrets_file" BRIDGE_TOKEN)"
  else
    dim "  MINECRAFT_BRIDGE_TOKEN=<from $secrets_file>"
  fi
  dim "  ADMIN_PASSWORD=<something you choose>"
  dim "  MINECRAFT_BRIDGE_URL=https://<tunnel host>  (forwards to 127.0.0.1:8787 here)"
}

# ---- doctor -----------------------------------------------------------------

# Everything here is checked read-only, so `mc doctor` is safe to run on a fresh
# clone before `install`, and safe to paste the output of into an issue.
cmd_doctor() {
  # Nearly every check below needs the pins. A missing or incomplete VERSION is
  # itself a deploy blocker, and common.sh names the offending key.
  load_versions
  load_local_env

  local fails=0 warns=0

  # All of this goes to stdout, including the failures: the report is meant to be
  # read top to bottom and pasted into an issue, and stderr would interleave.
  pass()  { printf '%s  ok%s %s\n'   "$C_GREEN"  "$C_RESET" "$*"; }
  fail()  { printf '%s err%s %s\n'   "$C_RED"    "$C_RESET" "$*"; fails=$((fails + 1)); }
  warn_() { printf '%swarn%s %s\n'   "$C_YELLOW" "$C_RESET" "$*"; warns=$((warns + 1)); }
  info()  { printf '%s     %s%s\n'   "$C_DIM"    "$*"      "$C_RESET"; }

  log "host"
  info "os        $(uname -s) $(uname -r) ($(uname -m))"
  if [[ -f /etc/os-release ]]; then
    # shellcheck disable=SC1091
    info "distro    $(. /etc/os-release && echo "$PRETTY_NAME")"
  fi
  if [[ -f /.dockerenv ]]; then
    info "container yes (docker)"
  fi
  info "user      $(id -un) (uid $(id -u))"
  info "path      $REPO_ROOT"

  log "tools"
  local tool
  for tool in curl zip; do
    if command -v "$tool" >/dev/null 2>&1; then
      pass "$tool -> $(command -v "$tool")"
    else
      fail "$tool not found"
    fi
  done
  if command -v jq >/dev/null 2>&1; then
    info "jq        $(jq --version 2>/dev/null)"
  elif command -v python3 >/dev/null 2>&1; then
    info "jq        missing (python3 fallback works for 'mc update')"
  else
    fail "neither jq nor python3 — 'mc update' cannot query the update APIs"
  fi
  if command -v unzip >/dev/null 2>&1; then
    pass "unzip -> $(command -v unzip)"
  else
    warn_ "unzip not found (only needed to inspect a backup)"
  fi

  log "java"
  # Paper 26.1+ refuses to start on Java 24, so this is the single most common
  # reason a deploy fails.
  if ! command -v java >/dev/null 2>&1; then
    fail "java not found — install a JDK ${JAVA_MAJOR}+ (README: Installing Java 25)"
  else
    local jmajor jline
    jline="$(java -version 2>&1)"
    jmajor="$(java_major || true)"
    if [[ -z "$jmajor" ]]; then
      fail "could not parse java version from: ${jline%%$'\n'*}"
    elif (( jmajor < JAVA_MAJOR )); then
      fail "Java ${jmajor} found, but Paper needs ${JAVA_MAJOR}+ — the server will refuse to boot"
    else
      pass "java ${jmajor} (need ${JAVA_MAJOR}+)"
    fi
    info "$(command -v java)"
  fi

  log "resources"
  if command -v free >/dev/null 2>&1; then
    local avail_mb
    avail_mb="$(free -m | awk '/^Mem:/ {print $7}')"
    # MAX_MEMORY is in megabytes (4G, 8192M); `free -m` reports megabytes too.
    # Without the conversion below this compared a megabyte count against a
    # gigabyte count and could never fire.
    local want_mb=""
    case "$MAX_MEMORY" in
      *G|*g) want_mb="$((${MAX_MEMORY%[Gg]} * 1024))" ;;
      *M|*m) want_mb="$((${MAX_MEMORY%[Mm]}))" ;;
      *K|*k) want_mb="$(( ${MAX_MEMORY%[Kk]} / 1024 ))" ;;
    esac
    if [[ -n "$want_mb" ]] && [[ -n "$avail_mb" ]] && (( avail_mb < want_mb )); then
      warn_ "only ${avail_mb}MB RAM available, MAX_MEMORY is ${MAX_MEMORY} — the JVM may fail to reserve its heap"
    else
      info "ram       ${avail_mb}MB available (MAX_MEMORY ${MAX_MEMORY})"
    fi
  fi
  if command -v df >/dev/null 2>&1; then
    local src avail_kb
    src="$(df -P "$REPO_ROOT" 2>/dev/null | awk 'NR==2 {print $4}')"
    avail_kb="${src:-0}"
    # Paper alone is ~65MB; a world grows fast, so want a few GB free.
    if [[ -n "$src" ]] && (( avail_kb < 2097152 )); then
      warn_ "only $((avail_kb / 1024))MB free on $(df -P "$REPO_ROOT" | awk 'NR==2 {print $6}') — worlds need GBs"
    else
      info "disk      $((avail_kb / 1024))MB free"
    fi
  fi

  log "layout"
  pass "VERSION readable and complete"
  info "pins      paper ${MINECRAFT_VERSION} b${PAPER_BUILD}, geyser ${GEYSER_VERSION} b${GEYSER_BUILD}, floodgate ${FLOODGATE_VERSION} b${FLOODGATE_BUILD}"

  local dir
  for dir in "$SERVER_DIR" "$BACKUP_DIR"; do
    if [[ ! -d "$dir" ]]; then
      info "$(basename "$dir")/     not created yet"
    elif [[ -w "$dir" ]]; then
      pass "$(basename "$dir")/ writable"
    else
      fail "$(basename "$dir")/ not writable by $(id -un) — in Docker this means the entrypoint's chown did not run"
    fi
  done

  log "install state"
  if [[ ! -d "$SERVER_DIR" ]]; then
    info "server/   missing — run 'mc install'"
  else
    local spec dest name
    for spec in "paper:$PAPER_JAR:$PAPER_SHA256" \
                "geyser:$GEYSER_JAR:$GEYSER_SHA256" \
                "floodgate:$FLOODGATE_JAR:$FLOODGATE_SHA256"; do
      name="${spec%%:*}"; spec="${spec#*:}"
      dest="${spec%:*}"; spec="${spec#*:}"
      if [[ ! -f "$dest" ]]; then
        warn_ "$name jar missing — run 'mc install'"
      elif [[ -n "$spec" ]]; then
        local actual
        actual="$(sha256_of "$dest")"
        if [[ "$actual" == "$spec" ]]; then
          pass "$name jar matches the pin"
        else
          fail "$name jar checksum mismatch — delete it and run 'mc install'"
        fi
      fi
    done

    local eula="$SERVER_DIR/eula.txt"
    if [[ ! -f "$eula" ]]; then
      fail "eula.txt missing — run 'mc install'"
    elif grep -qiE '^[[:space:]]*eula[[:space:]]*=[[:space:]]*true' "$eula"; then
      pass "EULA accepted in server/eula.txt"
    else
      fail "EULA not accepted — set eula=true in $eula after reading https://aka.ms/MinecraftEULA"
    fi
  fi

  log "network"
  # A port already in use is the usual cause of a server that starts and dies.
  # /proc/net needs no packages (`ss` only exists if iproute2 happens to be
  # installed), and covers UDP as well as TCP.
  local tcp_ports udp_ports
  tcp_ports="$(awk 'NR>1 && $4=="0A" {split($2,a,":"); print toupper(a[2])}' \
                 /proc/net/tcp /proc/net/tcp6 2>/dev/null || true)"
  udp_ports="$(awk 'NR>1 {split($2,a,":"); print toupper(a[2])}' \
                 /proc/net/udp /proc/net/udp6 2>/dev/null || true)"

  local spec port proto proto_label hex listening
  for spec in "25565:tcp:java" "19132:udp:bedrock"; do
    port="${spec%%:*}"; spec="${spec#*:}"
    proto="${spec%%:*}"; proto_label="${spec#*:}"
    hex="$(printf '%04X' "$port")"

    if [[ "$proto" == "tcp" ]]; then
      listening="$tcp_ports"
    else
      listening="$udp_ports"
    fi

    # An empty list means nothing is bound, which is the good case; only an
    # unreadable /proc/net means we could not tell.
    if [[ ! -r "/proc/net/$proto" ]]; then
      info "$port/$proto ($proto_label) could not be checked"
    elif grep -qxF "$hex" <<<"$listening"; then
      if server_pid >/dev/null; then
        info "$port/$proto ($proto_label) in use by this server — fine"
      else
        fail "$port/$proto ($proto_label) is already in use by another process — mc start will fail"
      fi
    else
      info "$port/$proto ($proto_label) free"
    fi
  done
  info "remember: a cloud security group must allow 25565/tcp and 19132/udp inbound"

  log "state"
  if server_pid >/dev/null 2>&1; then
    pass "server running (pid $(server_pid))"
  else
    info "not running"
  fi

  echo
  if (( fails > 0 )); then
    err "$fails problem(s) found, $warns warning(s)"
    return 1
  fi
  ok "no problems found ($warns warning(s))"
  return 0
}

# ---- dispatch ---------------------------------------------------------------

main() {
  local command="${1:-help}"
  if (( $# > 0 )); then
    shift
  fi

  case "$command" in
    install) cmd_install "$@" ;;
    start)   cmd_start "$@" ;;
    stop)    cmd_stop "$@" ;;
    restart) cmd_restart "$@" ;;
    status)  cmd_status "$@" ;;   # exit 3 when stopped, so it works as a health check
    logs)    cmd_logs "$@" ;;
    console) cmd_console "$@" ;;
    backup)  cmd_backup "$@" ;;
    update)  cmd_update "$@" ;;
    deploy)  cmd_deploy "$@" ;;
    doctor)  cmd_doctor "$@" ;;
    help|-h|--help) usage ;;
    *) err "unknown command: $command"; echo; usage; exit 1 ;;
  esac
}

# Pull global flags out of the argument list before dispatch, so
# `mc update --apply --yes` and `mc --yes backup` both work.
args=()
for arg in "$@"; do
  case "$arg" in
    --yes|-y) assume_yes=1 ;;
    *) args+=("$arg") ;;
  esac
done
export MC_ASSUME_YES="${assume_yes:-0}"

if (( ${#args[@]} > 0 )); then
  # Called bare, not as `main ... || status=$?`: putting a function in a `||`
  # list suppresses errexit for everything inside it, which would let a failed
  # download or zip be reported as success.
  main "${args[@]}"
  exit $?
fi

usage
