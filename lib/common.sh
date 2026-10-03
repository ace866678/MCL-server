#!/usr/bin/env bash
# Shared helpers for the `mc` script. Sourced, not executed.

# ---- paths ------------------------------------------------------------------

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG_DIR="$REPO_ROOT/config"
VERSION_FILE="$REPO_ROOT/VERSION"
SERVER_DIR="${MC_SERVER_DIR:-$REPO_ROOT/server}"
PLUGINS_DIR="$SERVER_DIR/plugins"
BACKUP_DIR="${MC_BACKUP_DIR:-$REPO_ROOT/backups}"
LOCAL_ENV="$REPO_ROOT/server.env"

PAPER_JAR="$SERVER_DIR/paper.jar"
GEYSER_JAR="$PLUGINS_DIR/geyser.jar"
FLOODGATE_JAR="$PLUGINS_DIR/floodgate.jar"
FIFO="$SERVER_DIR/console.fifo"
PID_FILE="$SERVER_DIR/server.pid"

# ---- output -----------------------------------------------------------------

if [[ -t 1 ]]; then
  C_RESET=$'\033[0m'; C_RED=$'\033[31m'; C_GREEN=$'\033[32m'
  C_YELLOW=$'\033[33m'; C_BLUE=$'\033[34m'; C_DIM=$'\033[2m'
else
  C_RESET=''; C_RED=''; C_GREEN=''; C_YELLOW=''; C_BLUE=''; C_DIM=''
fi

log()  { printf '%s==>%s %s\n' "$C_BLUE" "$C_RESET" "$*"; }
ok()   { printf '%s  ok%s %s\n' "$C_GREEN" "$C_RESET" "$*"; }
warn() { printf '%swarn%s %s\n' "$C_YELLOW" "$C_RESET" "$*" >&2; }
err()  { printf '%s err%s %s\n' "$C_RED" "$C_RESET" "$*" >&2; }
dim()  { printf '%s     %s%s\n' "$C_DIM" "$*" "$C_RESET"; }
die()  { err "$*"; exit 1; }

# ---- version pins -----------------------------------------------------------

load_versions() {
  [[ -f "$VERSION_FILE" ]] || die "VERSION file missing at $VERSION_FILE"

  local line key value
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%$'\r'}"
    [[ -z "$line" || "$line" == \#* || "$line" != *=* ]] && continue
    key="${line%%=*}"
    value="${line#*=}"
    key="${key//[[:space:]]/}"
    [[ "$key" =~ ^[A-Z_][A-Z0-9_]*$ ]] || continue
    printf -v "$key" '%s' "$value"
  done < "$VERSION_FILE"

  local required=(
    MINECRAFT_VERSION PAPER_BUILD PAPER_SHA256
    GEYSER_VERSION GEYSER_BUILD GEYSER_SHA256
    FLOODGATE_VERSION FLOODGATE_BUILD FLOODGATE_SHA256
    JAVA_MAJOR MIN_MEMORY MAX_MEMORY
  )
  local name
  for name in "${required[@]}"; do
    [[ -n "${!name:-}" ]] || die "VERSION is missing $name"
  done
}

# Per-machine overrides, applied after the pins so a VM can differ from CI.
load_local_env() {
  [[ -f "$LOCAL_ENV" ]] || return 0
  set -a
  # shellcheck disable=SC1090
  source "$LOCAL_ENV"
  set +a
}

# ---- derived download URLs --------------------------------------------------

paper_url() {
  printf 'https://fill-data.papermc.io/v1/objects/%s/paper-%s-%s.jar' \
    "$PAPER_SHA256" "$MINECRAFT_VERSION" "$PAPER_BUILD"
}

geyser_url() {
  printf 'https://download.geysermc.org/v2/projects/geyser/versions/%s/builds/%s/downloads/spigot' \
    "$GEYSER_VERSION" "$GEYSER_BUILD"
}

floodgate_url() {
  printf 'https://download.geysermc.org/v2/projects/floodgate/versions/%s/builds/%s/downloads/spigot' \
    "$FLOODGATE_VERSION" "$FLOODGATE_BUILD"
}

# ---- prerequisites ----------------------------------------------------------

require_cmd() {
  local cmd
  for cmd in "$@"; do
    command -v "$cmd" >/dev/null 2>&1 || die "required command not found: $cmd"
  done
}

sha256_of() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | cut -d' ' -f1
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" | cut -d' ' -f1
  else
    die "need sha256sum or shasum to verify downloads"
  fi
}

# A tiny JSON reader so `mc update` does not need jq installed everywhere.
# Reads JSON on stdin. $1 is a jq filter, $2 the equivalent python expression
# (the Python side receives the document as `d`). Both are always passed, so
# neither tool is a hard dependency.
json_eval() {
  local jq_filter="$1" py_expr="$2"
  if command -v jq >/dev/null 2>&1; then
    jq -r "$jq_filter"
  elif command -v python3 >/dev/null 2>&1; then
    python3 -c "import sys,json;d=json.load(sys.stdin);print($py_expr)"
  else
    die "need jq or python3 to query the update APIs"
  fi
}

java_version_string() {
  # Captured whole rather than piped to head: under pipefail, an early-exiting
  # reader can SIGPIPE the JVM and turn a working `java -version` into a failure.
  java -version 2>&1
}

java_major() {
  local raw
  raw="$(java_version_string)" || return 1
  raw="${raw%%$'\n'*}"
  if [[ "$raw" =~ version[[:space:]]+\"([0-9]+) ]]; then
    printf '%s' "${BASH_REMATCH[1]}"
  else
    return 1
  fi
}

# Paper 26.1+ will not boot on Java 24, so this is a hard requirement and the
# single most common reason a fresh install does not start.
check_java() {
  command -v java >/dev/null 2>&1 || die "java not found. Install a JDK ${JAVA_MAJOR}+ (see README)."
  local major raw
  raw="$(java_version_string)" || die "'java -version' failed"
  if ! major="$(java_major)"; then
    die "could not parse java version from: ${raw%%$'\n'*}"
  fi
  (( major >= JAVA_MAJOR )) || die "Java ${JAVA_MAJOR}+ required for Minecraft ${MINECRAFT_VERSION}; found ${major}. See README for the Temurin install."
  ok "java ${major} (need ${JAVA_MAJOR}+)"
}

# ---- config seeding ---------------------------------------------------------

# Copies anything from config/ into server/ that is not already there. Existing
# files are never overwritten: the live server.properties is the real config once
# the server has booted once.
seed_config() {
  [[ -d "$CONFIG_DIR" ]] || return 0
  local rel src dst copied=0
  while IFS= read -r -d '' src; do
    rel="${src#"$CONFIG_DIR"/}"
    dst="$SERVER_DIR/$rel"
    if [[ -e "$dst" ]]; then
      continue
    fi
    mkdir -p "$(dirname "$dst")"
    cp -p "$src" "$dst"
    dim "seeded server/$rel"
    copied=$((copied + 1))
  done < <(find "$CONFIG_DIR" -type f -print0)
  (( copied > 0 )) && ok "seeded $copied config file(s) into server/"
  return 0
}

# Rewrite one key in the live server.properties, preserving comments and order.
# Used by `mc deploy` to turn RCON on without shipping a hand-edited file.
set_server_property() {
  local key="$1" value="$2" props="${3:-$SERVER_DIR/server.properties}"
  local escaped
  escaped="$(printf '%s' "$value" | sed 's/[\\&|]/\\&/g')"
  if grep -qE "^[[:space:]]*${key}=" "$props"; then
    sed -i -E "s|^[[:space:]]*${key}=.*|${key}=${escaped}|" "$props"
  else
    printf '%s=%s\n' "$key" "$value" >> "$props"
  fi
}

check_eula() {
  local eula="$SERVER_DIR/eula.txt"
  [[ -f "$eula" ]] || die "eula.txt missing from server/. Run 'mc install' first."
  if ! grep -qiE '^[[:space:]]*eula[[:space:]]*=[[:space:]]*true' "$eula"; then
    err "You have not accepted the Minecraft EULA."
    dim "Read https://aka.ms/MinecraftEULA"
    dim "If you accept it, set eula=true in $eula"
    exit 1
  fi
}

# ---- process handling -------------------------------------------------------

server_pid() {
  [[ -f "$PID_FILE" ]] || return 1
  local pid
  pid="$(cat "$PID_FILE" 2>/dev/null)" || return 1
  [[ -n "$pid" ]] || return 1
  kill -0 "$pid" 2>/dev/null || return 1
  printf '%s' "$pid"
}

# Aikar-style G1GC flags, the set Paper's own performance guide points at.
# One flag per line so it stays readable and greppable.
jvm_flags() {
  cat <<'FLAGS'
-XX:+UseG1GC
-XX:+ParallelRefProcEnabled
-XX:MaxGCPauseMillis=200
-XX:+UnlockExperimentalVMOptions
-XX:+DisableExplicitGC
-XX:+AlwaysPreTouch
-XX:+UseStringDeduplication
-XX:G1NewSizePercent=30
-XX:G1MaxNewSizePercent=40
-XX:G1HeapRegionSize=8M
-XX:G1ReservePercent=20
-XX:G1HeapWastePercent=5
-XX:G1MixedGCCountTarget=4
-XX:InitiatingHeapOccupancyPercent=15
-XX:G1MixedGCLiveThresholdPercent=90
-XX:G1RSetUpdatingPauseTimePercent=5
-XX:SurvivorRatio=32
-XX:MaxTenuringThreshold=1
-XX:+PerfDisableSharedMem
-Dfile.encoding=UTF-8
FLAGS
}
