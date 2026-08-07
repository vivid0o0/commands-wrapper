#!/usr/bin/env bash
set -euo pipefail

GREEN="\033[38;5;108m"
RED="\033[38;5;131m"
BLUE="\033[38;5;67m"
GRAY="\033[38;5;244m"
YELLOW="\033[38;5;214m"
RESET="\033[0m"

COLOR_MODE="${COMMANDS_WRAPPER_COLOR:-auto}"
if [ "$COLOR_MODE" != "always" ] && {
    [ "$COLOR_MODE" = "never" ] || [ -n "${NO_COLOR+x}" ] || [ "${TERM:-}" = "dumb" ] || [ ! -t 1 ];
}; then
    GREEN=""
    RED=""
    BLUE=""
    GRAY=""
    YELLOW=""
    RESET=""
fi

PRIMARY_WRAPPER="commands-wrapper"
SHORT_ALIAS="cw"

PATH_BLOCK_START='# >>> commands-wrapper path >>>'
PATH_BLOCK_END='# <<< commands-wrapper path <<<'
FISH_PATH_BLOCK_START='# >>> commands-wrapper fish path >>>'
FISH_PATH_BLOCK_END='# <<< commands-wrapper fish path <<<'

TOTAL_STEPS=9
CURRENT_STEP=0
UI_RUNTIME_READY=1

SOURCE_URL="${COMMANDS_WRAPPER_SOURCE_URL:-}"
SOURCE_SHA256="${COMMANDS_WRAPPER_SOURCE_SHA256:-}"
MAX_SOURCE_ARCHIVE_BYTES=67108864
MAX_SOURCE_EXTRACTED_BYTES=134217728
MAX_SOURCE_MEMBERS=10000

ensure_ui_runtime() {
    return 0
}

ui_emit() {
    local mode="$1"
    shift || true

    case "$mode" in
        logo)
            printf "${BLUE}commands-wrapper${RESET}\n"
            ;;
        step)
            printf "${BLUE}[%s/%s]${RESET} %s\n" "${1:-0}" "${2:-0}" "${3:-}"
            ;;
        ok)
            printf "${GREEN}OK${RESET}\n"
            ;;
        warn)
            printf "${YELLOW}WARN:${RESET} %s\n" "${1:-}"
            ;;
        error)
            printf "${RED}ERROR:${RESET} %s\n" "${1:-}" >&2
            ;;
        info)
            printf "${BLUE}%s${RESET}\n" "${1:-}"
            ;;
        detail)
            printf "${GRAY}%s${RESET}\n" "${1:-}"
            ;;
        success-panel)
            if [ -n "${1:-}" ]; then
                printf "${GREEN}commands-wrapper %s is installed and self-healed.${RESET}\n" "$1"
            else
                printf "${GREEN}commands-wrapper is installed and self-healed.${RESET}\n"
            fi
            printf "${GRAY}Use 'cw' or 'commands-wrapper' from any directory.${RESET}\n"
            ;;
        *)
            return 1
            ;;
    esac
}

print_logo() {
    if ! ui_emit logo; then
        printf "${BLUE}commands-wrapper${RESET}\n"
    fi
}

draw_progress() {
    local label="$1"
    if ! ui_emit step "$CURRENT_STEP" "$TOTAL_STEPS" "$label"; then
        printf "${BLUE}[%d/%d]${RESET} %s\n" "$CURRENT_STEP" "$TOTAL_STEPS" "$label"
    fi
}

start_step() {
    CURRENT_STEP=$((CURRENT_STEP + 1))
    draw_progress "$1"
}

step_ok() {
    if ! ui_emit ok; then
        printf "${GREEN}OK${RESET}\n"
    fi
}

step_warn() {
    if ! ui_emit warn "$1"; then
        printf "${YELLOW}WARN:${RESET} %s\n" "$1"
    fi
}

die() {
    if [ "$UI_RUNTIME_READY" = "1" ] && ui_emit error "$1" >&2; then
        :
    else
        printf "${RED}ERROR:${RESET} %s\n" "$1" >&2
    fi
    exit 1
}

env_flag_enabled() {
    local value
    value="$(printenv "$1" 2>/dev/null || true)"
    case "$value" in
        1|true|TRUE|yes|YES|on|ON)
            return 0
            ;;
        *)
            return 1
            ;;
    esac
}

run_pip() {
    local status
    if python3 -m pip "$@"; then
        return 0
    else
        status=$?
    fi

    if ! env_flag_enabled COMMANDS_WRAPPER_ALLOW_BREAK_SYSTEM_PACKAGES; then
        return "$status"
    fi

    step_warn "Standard pip command failed; retrying with --break-system-packages because explicit opt-in is enabled."
    python3 -m pip "$@" --break-system-packages
}

scripts_dir_from_python() {
    python3 -c "import os, site, sys, sysconfig; in_venv = getattr(sys, 'base_prefix', sys.prefix) != sys.prefix; scripts = None
if in_venv:
    scripts = sysconfig.get_path('scripts')
else:
    scheme = f'{os.name}_user'
    if scheme in sysconfig.get_scheme_names():
        scripts = sysconfig.get_path('scripts', scheme=scheme)
scripts = scripts or os.path.join(site.USER_BASE or os.path.expanduser('~'), 'bin')
print(os.path.abspath(scripts))"
}

user_config_dir_from_python() {
    python3 - <<'PY'
import os

if os.name == 'nt':
    base = os.environ.get('APPDATA') or os.path.expanduser('~')
    print(os.path.join(base, 'commands-wrapper'))
else:
    xdg = os.environ.get('XDG_CONFIG_HOME')
    if xdg:
        print(os.path.join(os.path.expanduser(xdg), 'commands-wrapper'))
    else:
        print(os.path.join(os.path.expanduser('~'), '.config', 'commands-wrapper'))
PY
}

file_sha256() {
    python3 - "$1" <<'PY'
import hashlib
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
digest = hashlib.sha256()
with path.open('rb') as handle:
    for chunk in iter(lambda: handle.read(1024 * 1024), b''):
        digest.update(chunk)
print(digest.hexdigest())
PY
}

validate_source_url() {
    python3 - "$SOURCE_URL" <<'PY'
import os
import sys
import urllib.parse

url = sys.argv[1]
parsed = urllib.parse.urlparse(url)
allow_insecure = os.environ.get("COMMANDS_WRAPPER_ALLOW_INSECURE_SOURCE", "").strip().lower() in {
    "1", "true", "yes", "on"
}
if parsed.scheme == "https" and parsed.netloc:
    raise SystemExit(0)
if allow_insecure and (
    (parsed.scheme == "http" and parsed.netloc)
    or (parsed.scheme == "file" and parsed.path)
):
    raise SystemExit(0)
raise SystemExit(
    "source URL must use HTTPS; set COMMANDS_WRAPPER_ALLOW_INSECURE_SOURCE=1 "
    "only for a trusted local development source"
)
PY
}

file_size_bytes() {
    python3 - "$1" <<'PY'
import os
import sys
print(os.path.getsize(sys.argv[1]))
PY
}

secure_extract_source_archive() {
    local archive_path="$1"
    local destination="$2"

    python3 - "$archive_path" "$destination" \
        "$MAX_SOURCE_EXTRACTED_BYTES" "$MAX_SOURCE_MEMBERS" <<'PY'
import os
import pathlib
import shutil
import stat
import sys
import tarfile

archive_path = pathlib.Path(sys.argv[1])
destination = pathlib.Path(sys.argv[2])
max_bytes = int(sys.argv[3])
max_members = int(sys.argv[4])
destination.mkdir(parents=True, exist_ok=True)
destination_real = destination.resolve()

with tarfile.open(archive_path, mode="r:gz") as archive:
    members = archive.getmembers()
    if not members:
        raise SystemExit("source archive is empty")
    if len(members) > max_members:
        raise SystemExit("source archive contains too many entries")

    top_levels = set()
    total_size = 0
    validated = []
    for member in members:
        member_path = pathlib.PurePosixPath(member.name)
        parts = member_path.parts
        if member_path.is_absolute() or ".." in parts or not parts:
            raise SystemExit(f"unsafe source archive path: {member.name}")
        top_levels.add(parts[0])
        if len(top_levels) != 1:
            raise SystemExit("source archive must contain exactly one top-level directory")
        if member.issym() or member.islnk() or member.isdev() or member.isfifo():
            raise SystemExit(f"unsupported source archive entry: {member.name}")
        if not (member.isdir() or member.isfile()):
            raise SystemExit(f"unsupported source archive entry: {member.name}")

        relative_parts = parts[1:]
        if not relative_parts:
            continue
        target = destination.joinpath(*relative_parts)
        target_real = target.resolve(strict=False)
        if os.path.commonpath((str(destination_real), str(target_real))) != str(destination_real):
            raise SystemExit(f"unsafe source archive path: {member.name}")

        if member.isfile():
            total_size += member.size
            if total_size > max_bytes:
                raise SystemExit("source archive exceeds the extracted-size safety limit")
        validated.append((member, target))

    for member, target in validated:
        if member.isdir():
            target.mkdir(parents=True, exist_ok=True)
            continue

        target.parent.mkdir(parents=True, exist_ok=True)
        source = archive.extractfile(member)
        if source is None:
            raise SystemExit(f"failed to read source archive entry: {member.name}")
        with source, target.open("wb") as output:
            shutil.copyfileobj(source, output, length=1024 * 1024)
        safe_mode = 0o755 if member.mode & stat.S_IXUSR else 0o644
        target.chmod(safe_mode)
PY
}

is_commands_wrapper_source_root() {
    local root="$1"
    [ -f "$root/pyproject.toml" ] || return 1
    [ -f "$root/.commands-wrapper/commands-wrapper" ] || return 1
    return 0
}

path_has_dir() {
    local target="$1"
    case ":$PATH:" in
        *":$target:"*)
            return 0
            ;;
        *)
            return 1
            ;;
    esac
}

resolved_command_path() {
    local command_name="$1"
    type -P "$command_name" 2>/dev/null || true
}

assert_global_command_path() {
    local command_name="$1"
    local expected_dir="$2"
    local resolved
    resolved="$(resolved_command_path "$command_name")"
    if [ -z "$resolved" ]; then
        die "global access check failed: '$command_name' is not discoverable in PATH."
    fi

    local resolved_dir
    resolved_dir="$(dirname "$resolved")"
    if [ "$resolved_dir" != "$expected_dir" ]; then
        die "global access check failed: '$command_name' resolves to '$resolved' instead of '$expected_dir'."
    fi
}

path_block_for_bin_dir() {
    local bin_dir="$1"
    printf '%s\n' \
        "$PATH_BLOCK_START" \
        "if [ -d \"$bin_dir\" ]; then" \
        "    case \":\$PATH:\" in" \
        "        *\":$bin_dir:\"*) ;;" \
        "        *) export PATH=\"$bin_dir:\$PATH\" ;;" \
        "    esac" \
        "fi" \
        "$PATH_BLOCK_END"
}

build_updated_path_block_content() {
    local block="$1"
    build_updated_block_content "$PATH_BLOCK_START" "$PATH_BLOCK_END" "$block"
}

build_updated_fish_path_block_content() {
    local block="$1"
    build_updated_block_content "$FISH_PATH_BLOCK_START" "$FISH_PATH_BLOCK_END" "$block"
}

build_updated_block_content() {
    local start_marker="$1"
    local end_marker="$2"
    local block="$3"
    python3 - "$start_marker" "$end_marker" "$block" <<'PY'
import re
import sys

start, end, block = sys.argv[1], sys.argv[2], sys.argv[3]
existing = sys.stdin.read()

pattern = re.compile(re.escape(start) + r".*?" + re.escape(end), re.S)
if pattern.search(existing):
    updated = pattern.sub(block, existing, count=1)
else:
    if existing and not existing.endswith("\n"):
        updated = existing + "\n\n" + block + "\n"
    elif existing:
        updated = existing + "\n" + block + "\n"
    else:
        updated = block + "\n"

sys.stdout.write(updated)
PY
}

fish_path_block_for_bin_dir() {
    local bin_dir="$1"
    printf '%s\n' \
        "$FISH_PATH_BLOCK_START" \
        "if test -d \"$bin_dir\"" \
        "    if not contains \"$bin_dir\" \$PATH" \
        "        set -gx PATH \"$bin_dir\" \$PATH" \
        "    end" \
        "end" \
        "$FISH_PATH_BLOCK_END"
}

append_fish_path_block() {
    local fish_conf_path="$1"
    local block="$2"
    local existing=""

    if [ -f "$fish_conf_path" ]; then
        if ! existing="$(<"$fish_conf_path")"; then
            existing=""
        fi
    fi

    local parent
    parent="$(dirname "$fish_conf_path")"
    if [ -n "$parent" ]; then
        mkdir -p "$parent" || return 1
    fi

    local updated
    if ! updated="$(printf '%s' "$existing" | build_updated_fish_path_block_content "$block")"; then
        return 1
    fi

    if [ "$updated" = "$existing" ]; then
        return 0
    fi

    printf '%s' "$updated" > "$fish_conf_path" || return 1
    return 0
}

ensure_fish_path_persistence() {
    local bin_dir="$1"
    local fish_conf_path="$HOME/.config/fish/conf.d/commands-wrapper.fish"
    local fish_block
    fish_block="$(fish_path_block_for_bin_dir "$bin_dir")"

    if append_fish_path_block "$fish_conf_path" "$fish_block"; then
        printf '%s\n' "$fish_conf_path"
        return 0
    fi

    return 1
}

installed_package_version() {
    python3 -m pip show commands-wrapper 2>/dev/null |
        while IFS=: read -r key value; do
            if [ "$key" = "Version" ]; then
                value="${value# }"
                printf '%s\n' "$value"
                break
            fi
        done
}

project_version_from_pyproject() {
    local pyproject_path="$1"
    python3 - "$pyproject_path" <<'PY'
import pathlib
import re
import sys

pyproject_path = pathlib.Path(sys.argv[1])
if not pyproject_path.is_file():
    sys.exit(0)

text = pyproject_path.read_text(encoding='utf-8', errors='replace')

try:
    import tomllib  # type: ignore[attr-defined]
except Exception:
    tomllib = None

version = None
if tomllib is not None:
    try:
        data = tomllib.loads(text)
    except Exception:
        data = {}
    project = data.get('project') if isinstance(data, dict) else None
    if isinstance(project, dict):
        value = project.get('version')
        if isinstance(value, str) and value.strip():
            version = value.strip()

if version is None:
    in_project = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith('['):
            in_project = stripped == '[project]'
            continue
        if not in_project:
            continue
        match = re.match(r'^version\s*=\s*["\']([^"\']+)["\']\s*$', stripped)
        if match:
            version = match.group(1).strip()
            break

if version:
    print(version)
PY
}

compare_versions() {
    local left="$1"
    local right="$2"
    python3 - "$left" "$right" <<'PY'
import sys

left = sys.argv[1]
right = sys.argv[2]

try:
    from packaging.version import Version

    left_v = Version(left)
    right_v = Version(right)
except Exception:
    if left == right:
        print('eq')
        raise SystemExit(0)
    print('unknown')
    raise SystemExit(0)

if left_v == right_v:
    print('eq')
elif left_v > right_v:
    print('gt')
else:
    print('lt')
PY
}

append_path_block() {
    local rc_path="$1"
    local block="$2"
    local existing=""

    if [ -f "$rc_path" ]; then
        if ! existing="$(<"$rc_path")"; then
            existing=""
        fi
    fi

    local parent
    parent="$(dirname "$rc_path")"
    if [ -n "$parent" ]; then
        mkdir -p "$parent" || return 1
    fi

    local updated
    if ! updated="$(printf '%s' "$existing" | build_updated_path_block_content "$block")"; then
        return 1
    fi

    if [ "$updated" = "$existing" ]; then
        return 0
    fi

    printf '%s' "$updated" > "$rc_path" || return 1
    return 0
}

ensure_path_persistence() {
    local bin_dir="$1"
    local shell_name
    shell_name="$(basename "${SHELL:-}")"
    local candidates=()

    case "$shell_name" in
        zsh)
            candidates=("$HOME/.zshrc" "$HOME/.profile")
            ;;
        bash|sh)
            candidates=("$HOME/.bashrc" "$HOME/.profile")
            ;;
        *)
            candidates=("$HOME/.bashrc" "$HOME/.zshrc" "$HOME/.profile")
            ;;
    esac

    local block
    block="$(path_block_for_bin_dir "$bin_dir")"
    local rc_path

    for rc_path in "${candidates[@]}"; do
        if append_path_block "$rc_path" "$block"; then
            printf '%s\n' "$rc_path"
            return 0
        fi
    done

    return 1
}

secure_temp_file() {
    mktemp "${TMPDIR:-/tmp}/$1XXXXXX"
}

run_sync_with_retry() {
    local wrapper_path="$1"
    if ! command -v mktemp >/dev/null 2>&1; then
        step_warn "mktemp is required for safe synchronization diagnostics."
        return 1
    fi
    local sync_log_first
    local sync_log_second
    sync_log_first="$(secure_temp_file 'commands-wrapper-sync-')"
    sync_log_second="$(secure_temp_file 'commands-wrapper-sync-retry-')"

    if COMMANDS_WRAPPER_BIN_DIR="$(dirname "$wrapper_path")" \
        "$wrapper_path" sync --bin-dir "$(dirname "$wrapper_path")" \
        >"$sync_log_first" 2>&1; then
        rm -f "$sync_log_first" "$sync_log_second"
        return 0
    fi

    step_warn "Initial wrapper sync failed; retrying with diagnostics."
    if COMMANDS_WRAPPER_BIN_DIR="$(dirname "$wrapper_path")" \
        "$wrapper_path" sync --bin-dir "$(dirname "$wrapper_path")" \
        >"$sync_log_second" 2>&1; then
        rm -f "$sync_log_first" "$sync_log_second"
        return 0
    fi

    if [ -f "$sync_log_first" ]; then
        ui_emit warn "Sync attempt output:" || printf '%s\n' "Sync attempt output:"
        while IFS= read -r log_line; do
            ui_emit detail "$log_line" || printf '%s\n' "$log_line"
        done < "$sync_log_first"
    fi

    if [ -f "$sync_log_second" ]; then
        ui_emit warn "Retry sync output:" || printf '%s\n' "Retry sync output:"
        while IFS= read -r log_line; do
            ui_emit detail "$log_line" || printf '%s\n' "$log_line"
        done < "$sync_log_second"
    fi

    rm -f "$sync_log_first" "$sync_log_second"
    return 1
}

if ! command -v python3 >/dev/null 2>&1; then
    printf "${RED}ERROR:${RESET} python3 was not found in PATH.\n" >&2
    exit 1
fi
if ! python3 -m pip --version >/dev/null 2>&1; then
    printf "${RED}ERROR:${RESET} python3 is available but pip is missing or broken.\n" >&2
    exit 1
fi
ensure_ui_runtime

print_logo
ui_emit info "Installing commands-wrapper" || printf "${BLUE}Installing commands-wrapper${RESET}\n"

INSTALL_CWD="$(pwd)"
SCRIPT_PATH="${BASH_SOURCE[0]}"
case "$SCRIPT_PATH" in
    */*)
        SCRIPT_DIR="$(cd -- "${SCRIPT_PATH%/*}" && pwd)"
        ;;
    *)
        SCRIPT_DIR="$INSTALL_CWD"
        ;;
esac
SCRIPT_REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"

TMP_DIR=""
cleanup() {
    if [ -n "$TMP_DIR" ] && [ -d "$TMP_DIR" ]; then
        rm -rf "$TMP_DIR"
    fi
}
trap cleanup EXIT

start_step "Checking Python and pip availability"
if ! command -v python3 >/dev/null 2>&1; then
    die "python3 was not found in PATH."
fi
if ! python3 -m pip --version >/dev/null 2>&1; then
    die "python3 is available but pip is missing or broken."
fi
step_ok

start_step "Preparing installation source"
LOCAL_SOURCE_ROOT=""
INSTALL_TARGET="$PRIMARY_WRAPPER"
TARGET_VERSION=""
if is_commands_wrapper_source_root "$SCRIPT_REPO_ROOT"; then
    LOCAL_SOURCE_ROOT="$SCRIPT_REPO_ROOT"
elif is_commands_wrapper_source_root "$INSTALL_CWD"; then
    LOCAL_SOURCE_ROOT="$INSTALL_CWD"
fi

if [ -n "$LOCAL_SOURCE_ROOT" ]; then
    cd "$LOCAL_SOURCE_ROOT"
    INSTALL_TARGET="."
    TARGET_VERSION="$(project_version_from_pyproject "$(pwd)/pyproject.toml" || true)"
elif [ -n "$SOURCE_URL" ]; then
    if ! command -v curl >/dev/null 2>&1; then
        die "curl was not found and a custom archive source requires it."
    fi
    if ! command -v mktemp >/dev/null 2>&1; then
        die "mktemp not found (required for custom archive install)."
    fi
    if ! validate_source_url; then
        die "invalid or insecure installation source URL."
    fi

    TMP_DIR="$(mktemp -d)"
    ARCHIVE_PATH="$TMP_DIR/commands-wrapper.tar.gz"
    EXTRACT_DIR="$TMP_DIR/source"

    CURL_PROTOCOL_ARGS=(--proto '=https' --proto-redir '=https')
    if [ "${COMMANDS_WRAPPER_ALLOW_INSECURE_SOURCE:-}" = "1" ]; then
        CURL_PROTOCOL_ARGS=()
    fi
    if [ -t 1 ]; then
        curl -fSL --max-filesize "$MAX_SOURCE_ARCHIVE_BYTES" \
            "${CURL_PROTOCOL_ARGS[@]}" --progress-bar "$SOURCE_URL" -o "$ARCHIVE_PATH"
    else
        curl -fsSL --max-filesize "$MAX_SOURCE_ARCHIVE_BYTES" \
            "${CURL_PROTOCOL_ARGS[@]}" "$SOURCE_URL" -o "$ARCHIVE_PATH"
    fi

    ARCHIVE_SIZE="$(file_size_bytes "$ARCHIVE_PATH")"
    if [ "$ARCHIVE_SIZE" -gt "$MAX_SOURCE_ARCHIVE_BYTES" ]; then
        die "source archive exceeds the 64 MiB safety limit."
    fi

    if [ -n "$SOURCE_SHA256" ]; then
        EXPECTED_SHA256="$(printf '%s' "$SOURCE_SHA256" | tr '[:upper:]' '[:lower:]')"
        if [[ ! "$EXPECTED_SHA256" =~ ^[0-9a-f]{64}$ ]]; then
            die "invalid COMMANDS_WRAPPER_SOURCE_SHA256 value."
        fi

        ACTUAL_SHA256="$(file_sha256 "$ARCHIVE_PATH")"
        if [ "$ACTUAL_SHA256" != "$EXPECTED_SHA256" ]; then
            die "source archive checksum mismatch."
        fi
    fi

    if ! secure_extract_source_archive "$ARCHIVE_PATH" "$EXTRACT_DIR"; then
        die "source archive failed security validation or extraction."
    fi
    cd "$EXTRACT_DIR"
    if ! is_commands_wrapper_source_root "$EXTRACT_DIR"; then
        die "downloaded archive does not contain a valid commands-wrapper source tree."
    fi
    INSTALL_TARGET="."
    TARGET_VERSION="$(project_version_from_pyproject "$(pwd)/pyproject.toml" || true)"
elif [ -n "$SOURCE_SHA256" ]; then
    die "COMMANDS_WRAPPER_SOURCE_SHA256 requires COMMANDS_WRAPPER_SOURCE_URL."
fi
step_ok

INSTALLED_VERSION="$(installed_package_version || true)"

PIP_INSTALL_SCOPE_ARG=""
if ! python3 - <<'PY_SCOPE'
import sys
raise SystemExit(0 if getattr(sys, 'base_prefix', sys.prefix) != sys.prefix else 1)
PY_SCOPE
then
    PIP_INSTALL_SCOPE_ARG="--user"
fi

install_package_target() {
    if [ -n "$PIP_INSTALL_SCOPE_ARG" ]; then
        run_pip install --upgrade "$PIP_INSTALL_SCOPE_ARG" "$@"
    else
        run_pip install --upgrade "$@"
    fi
}

start_step "Installing/updating package"
if [ -n "$INSTALLED_VERSION" ] && [ -n "$TARGET_VERSION" ]; then
    VERSION_RELATION="$(compare_versions "$INSTALLED_VERSION" "$TARGET_VERSION")"
    if [ "$VERSION_RELATION" = "eq" ]; then
        step_warn "commands-wrapper $INSTALLED_VERSION is already installed; skipping package reinstall."
    elif [ "$VERSION_RELATION" = "gt" ]; then
        step_warn "installed version $INSTALLED_VERSION is newer than source $TARGET_VERSION; skipping downgrade."
    else
        if ! install_package_target "$INSTALL_TARGET"; then
            die "pip install failed while installing commands-wrapper."
        fi
    fi
else
    if ! install_package_target "$INSTALL_TARGET"; then
        die "pip install failed while installing commands-wrapper."
    fi
fi
step_ok

start_step "Resolving command locations"
BIN_DIR="$(scripts_dir_from_python)"
if [ -z "$BIN_DIR" ]; then
    die "failed to determine python scripts directory."
fi
BIN_PATH="$BIN_DIR/$PRIMARY_WRAPPER"
CW_PATH="$BIN_DIR/$SHORT_ALIAS"

if [ ! -f "$BIN_PATH" ]; then
    die "installed binary was not found at '$BIN_PATH'."
fi
chmod +x "$BIN_PATH" >/dev/null 2>&1 || true
step_ok

start_step "Synchronizing wrapper commands"
if ! run_sync_with_retry "$BIN_PATH"; then
    die "automatic wrapper sync failed after retry."
fi
if [ ! -f "$CW_PATH" ]; then
    die "wrapper sync completed but '$CW_PATH' was not generated."
fi
chmod +x "$CW_PATH" >/dev/null 2>&1 || true
step_ok

start_step "Self-healing PATH for global command access"
PATH_RC_FILE=""
if PATH_RC_FILE="$(ensure_path_persistence "$BIN_DIR")"; then
    step_warn "PATH self-heal persisted to $PATH_RC_FILE"
fi
FISH_PATH_FILE=""
if FISH_PATH_FILE="$(ensure_fish_path_persistence "$BIN_DIR")"; then
    step_warn "Fish PATH self-heal persisted to $FISH_PATH_FILE"
fi
if ! path_has_dir "$BIN_DIR"; then
    export PATH="$BIN_DIR:$PATH"
fi
assert_global_command_path "$PRIMARY_WRAPPER" "$BIN_DIR"
assert_global_command_path "$SHORT_ALIAS" "$BIN_DIR"
step_ok

start_step "Ensuring global command config exists"
USER_CONFIG_DIR="$(user_config_dir_from_python)"
if [ -z "$USER_CONFIG_DIR" ]; then
    die "failed to determine user config directory."
fi
mkdir -p "$USER_CONFIG_DIR"
if [ ! -f "$USER_CONFIG_DIR/commands.yaml" ] && [ ! -f "$USER_CONFIG_DIR/commands.yml" ]; then
    (
        umask 077
        printf '%s\n' \
            '# command-name:' \
            '#   description: "What this command does"' \
            '#   steps 60:' \
            '#     - command: "shell command here"' \
            '#     - send: "text to type into process"' \
            '#     - press_key: "enter"' \
            '#     - wait: "2"' \
            > "$USER_CONFIG_DIR/commands.yaml"
    )
fi
step_ok

start_step "Running post-install launch preview"
if [ -z "${CI:-}" ] && [ -t 0 ] && [ -t 1 ]; then
    "$BIN_PATH"
else
    if ! "$BIN_PATH" list >/dev/null 2>&1; then
        die "post-install verification failed: '$PRIMARY_WRAPPER list' returned a non-zero exit code."
    fi
fi
step_ok

start_step "Final health check"
if ! "$BIN_PATH" --help >/dev/null 2>&1; then
    die "final health check failed: '$PRIMARY_WRAPPER --help' exited non-zero."
fi
step_ok

INSTALLED_AFTER="$(installed_package_version || true)"
if ! ui_emit success-panel "$INSTALLED_AFTER"; then
    printf "${GREEN}commands-wrapper is installed and self-healed.${RESET}\n"
fi
