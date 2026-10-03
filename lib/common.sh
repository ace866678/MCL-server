#!/usr/bin/env bash
# Shared helpers for the `mc` script. Sourced, not executed.

# ---- paths ------------------------------------------------------------------

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG_DIR="${MC_CONFIG_DIR:-$REPO_ROOT/config}"

# One pin file per server. The repository default keeps `mc` working standalone;
# the agent points MC_VERSION_FILE at a generated file inside each server
# directory so several servers on one machine can run different Minecraft
# versions without sharing pins.
VERSION_FILE="${MC_VERSION_FILE:-$REPO_ROOT/VERSION}"

SERVER_DIR="${MC_SERVER_DIR:-$REPO_ROOT/server}"
PLUGINS_DIR="$SERVER_DIR/plugins"
BACKUP_DIR="${MC_BACKUP_DIR:-$REPO_ROOT/backups}"
LOCAL_ENV="${MC_LOCAL_ENV:-$REPO_ROOT/server.env}"

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

# Resolves the newest STABLE Paper build for an arbitrary Minecraft version
# instead of reading a pin, writes the pin file, and then falls through to the
# normal checksum-verified download.
#
# The checksum and the required Java version both come from Paper's own API, so a
# version this repository has never seen is still verified and still gets the
# right JVM: the Java floor moves with Minecraft (17 for 1.20.x, 21 for 1.21.x,
# 25 for 26.x), and hardcoding one number would break every server but the last.
resolve_paper_catalog() {
  local want="$1"
  require_cmd curl
  [[ -n "$want" ]] || die "usage: mc install --catalog <minecraft-version>"

  log "resolving Minecraft $want from PaperMC"
  local meta
  meta="$(curl -fsSL --max-time 45 \
    "https://fill.papermc.io/v3/projects/paper/versions/${want}")" \
    || die "Paper does not publish Minecraft $want"

  JAVA_MAJOR="$(printf '%s' "$meta" | json_eval '.version.java.version.minimum' \
    'd["version"]["java"]["version"]["minimum"]')"
  [[ "$JAVA_MAJOR" =~ ^[0-9]+$ ]] \
    || die "Paper's API reported no Java version for Minecraft $want"

  # Paper ships its own recommended GC flags per version. Prefer them; the
  # hand-tuned set in jvm_flags() remains the fallback for a repository install
  # that was never catalog-resolved.
  PAPER_JAVA_FLAGS="$(printf '%s' "$meta" | json_eval \
    '.version.java.flags.recommended // [] | join(" ")' \
    '" ".join(d["version"]["java"]["flags"].get("recommended", []))')"
  (( ${#PAPER_JAVA_FLAGS} > 0 )) || PAPER_JAVA_FLAGS=""

  local builds candidate
  builds="$(curl -fsSL --max-time 45 \
    "https://fill.papermc.io/v3/projects/paper/versions/${want}/builds")" \
    || die "Paper has no builds published for Minecraft $want"

  # STABLE only: an alpha or beta must never land on someone's server because a
  # catalog install ran. Sort numerically, then take the last entry.
  candidate="$(printf '%s' "$builds" | json_eval \
    '[.[] | select(.channel == "STABLE")] | sort_by(.id) | last | .id // empty' \
    'max([b["id"] for b in d if b["channel"] == "STABLE"], default=None)')"
  [[ -n "$candidate" && "$candidate" != "None" ]] \
    || die "no STABLE Paper build for Minecraft $want (only pre-releases exist)"

  MINECRAFT_VERSION="$want"
  PAPER_BUILD="$candidate"
  PAPER_SHA256="$(printf '%s' "$builds" | json_eval \
    '.[] | select(.id == '"$candidate"') | .downloads."server:default".checksums.sha256' \
    'next((b["downloads"]["server:default"]["checksums"]["sha256"] for b in d if b["id"] == '"$candidate"'), "")')"
  [[ -n "$PAPER_SHA256" ]] || die "Paper's API returned no checksum for build $candidate"

  # Geyser and Floodgate follow the Bedrock protocol, so they deliberately track
  # the latest build rather than being pinned per Minecraft version.
  resolve_geyser_catalog

  write_version_file
  ok "pinned paper $MINECRAFT_VERSION build $PAPER_BUILD (sha256 ${PAPER_SHA256:0:16}...)"
  ok "pinned geyser $GEYSER_VERSION build $GEYSER_BUILD, floodgate $FLOODGATE_VERSION build $FLOODGATE_BUILD"
  ok "this version needs Java ${JAVA_MAJOR}+"
}

resolve_geyser_catalog() {
  local project version build sha
  for project in geyser floodgate; do
    local meta
    meta="$(curl -fsSL --max-time 45 \
      "https://download.geysermc.org/v2/projects/${project}/versions/latest/builds/latest")" \
      || die "could not reach the ${project} download API"
    version="$(printf '%s' "$meta" | json_eval '.version' 'd["version"]')"
    build="$(printf '%s' "$meta" | json_eval '.build' 'd["build"]')"
    sha="$(printf '%s' "$meta" | json_eval '.downloads.spigot.sha256' 'd["downloads"]["spigot"]["sha256"]')"
    [[ -n "$version" && -n "$build" && -n "$sha" ]] \
      || die "could not read the latest ${project} build metadata"
    if [[ "$project" == "geyser" ]]; then
      GEYSER_VERSION="$version"; GEYSER_BUILD="$build"; GEYSER_SHA256="$sha"
    else
      FLOODGATE_VERSION="$version"; FLOODGATE_BUILD="$build"; FLOODGATE_SHA256="$sha"
    fi
  done
}

# Writes the pins back to VERSION_FILE so the download below, and every later
# `mc` invocation, verifies against exactly what was resolved.
write_version_file() {
  local tmp
  tmp="$(mktemp "${VERSION_FILE}.XXXXXX")" || die "could not write $VERSION_FILE"
  {
    printf '# Generated by `mc install --catalog %s`. Minecraft world data is not in\n' "$MINECRAFT_VERSION"
    printf '# git; this pin file is. Re-run `mc install --catalog` to change version.\n\n'
    printf 'MINECRAFT_VERSION=%s\n' "$MINECRAFT_VERSION"
    printf 'PAPER_BUILD=%s\n' "$PAPER_BUILD"
    printf 'PAPER_SHA256=%s\n' "$PAPER_SHA256"
    printf 'PAPER_JAVA_FLAGS=%s\n\n' "$PAPER_JAVA_FLAGS"
    printf 'GEYSER_VERSION=%s\n' "$GEYSER_VERSION"
    printf 'GEYSER_BUILD=%s\n' "$GEYSER_BUILD"
    printf 'GEYSER_SHA256=%s\n' "$GEYSER_SHA256"
    printf 'FLOODGATE_VERSION=%s\n' "$FLOODGATE_VERSION"
    printf 'FLOODGATE_BUILD=%s\n' "$FLOODGATE_BUILD"
    printf 'FLOODGATE_SHA256=%s\n\n' "$FLOODGATE_SHA256"
    printf '# Resolved from PaperMC for this Minecraft version, not hardcoded.\n'
    printf 'JAVA_MAJOR=%s\n\n' "$JAVA_MAJOR"
    printf '# Written by the agent from the dashboard memory settings.\n'
    printf 'MIN_MEMORY=%s\n' "$MIN_MEMORY"
    printf 'MAX_MEMORY=%s\n' "$MAX_MEMORY"
  } > "$tmp"
  mv -f "$tmp" "$VERSION_FILE"
}

# Per-machine overrides, applied after the pins so a VM can differ from CI.
# For an agent-managed server this is the file the agent writes from the
# dashboard's memory settings, which is why it is overridable: several servers
# on one machine each need their own.
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

# Aikar-style G1GC flags, the set Paper's own performance guide points at. One
# flag per line so it stays readable and greppable.
#
# When the server was installed with `mc install --catalog`, Paper published a
# recommended flag set for that exact Minecraft version and it is in the pin file
# as PAPER_JAVA_FLAGS. Those win, because they are what the server's own authors
# tested against that release; this hand-tuned set is the fallback for a
# repository install that was never catalog-resolved.
jvm_flags() {
  if [[ -n "${PAPER_JAVA_FLAGS:-}" ]]; then
    printf '%s\n' "$PAPER_JAVA_FLAGS" | tr ' ' '\n' | sed '/^$/d'
    printf '%s\n' "-Dfile.encoding=UTF-8"
    return 0
  fi

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
