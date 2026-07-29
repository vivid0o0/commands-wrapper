#!/usr/bin/env bash
set -euo pipefail

GREEN="\033[38;5;108m"
RED="\033[38;5;131m"
YELLOW="\033[38;5;214m"
GRAY="\033[38;5;244m"
BLUE="\033[38;5;67m"
RESET="\033[0m"

PRIMARY_WRAPPER="commands-wrapper"

PATH_BLOCK_START='# >>> commands-wrapper path >>>'
PATH_BLOCK_END='# <<< commands-wrapper path <<<'
HOOK_BLOCK_START='# >>> commands-wrapper hook >>>'
HOOK_BLOCK_END='# <<< commands-wrapper hook <<<'
FISH_PATH_BLOCK_START='# >>> commands-wrapper fish path >>>'
FISH_PATH_BLOCK_END='# <<< commands-wrapper fish path <<<'

printf "${BLUE}Uninstalling commands-wrapper${RESET}\n"

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

confirm_action() {
    local prompt="$1"
    local force_flag="${COMMANDS_WRAPPER_UNINSTALL_FORCE:-0}"
    if [ "$force_flag" = "1" ]; then
        return 0
    fi

    if [ ! -t 0 ] || [ ! -t 1 ]; then
        return 1
    fi

    local reply=""
    read -r -p "$prompt " reply || return 1
    case "${reply,,}" in
        y|yes)
            return 0
            ;;
        *)
            return 1
            ;;
    esac
}

build_removed_block_content() {
    local start_marker="$1"
    local end_marker="$2"
    python3 - "$start_marker" "$end_marker" <<'PY'
import re
import sys

start = sys.argv[1]
end = sys.argv[2]
existing = sys.stdin.read()

pattern = re.compile(r'\n?' + re.escape(start) + r'.*?' + re.escape(end) + r'\n?', re.S)
updated = pattern.sub('\n', existing)
updated = re.sub(r'\n{3,}', '\n\n', updated)
updated = updated.lstrip('\n')
sys.stdout.write(updated)
PY
}

remove_managed_block() {
    local file_path="$1"
    local start_marker="$2"
    local end_marker="$3"

    if [ ! -f "$file_path" ]; then
        return 0
    fi

    local existing
    if ! existing="$(<"$file_path")"; then
        return 1
    fi

    local updated
    if ! updated="$(printf '%s' "$existing" | build_removed_block_content "$start_marker" "$end_marker")"; then
        return 1
    fi

    if [ "$updated" = "$existing" ]; then
        return 0
    fi

    if [ -n "$updated" ]; then
        printf '%s' "$updated" > "$file_path" || return 1
    else
        : > "$file_path" || return 1
    fi

    return 0
}

remove_path_persistence() {
    local rc_files=("$HOME/.bashrc" "$HOME/.zshrc" "$HOME/.profile")
    local rc_file
    for rc_file in "${rc_files[@]}"; do
        remove_managed_block "$rc_file" "$PATH_BLOCK_START" "$PATH_BLOCK_END" || true
        remove_managed_block "$rc_file" "$HOOK_BLOCK_START" "$HOOK_BLOCK_END" || true
    done

    local fish_conf="$HOME/.config/fish/conf.d/commands-wrapper.fish"
    remove_managed_block "$fish_conf" "$FISH_PATH_BLOCK_START" "$FISH_PATH_BLOCK_END" || true
}

if ! confirm_action "Continue and uninstall commands-wrapper? [y/N]"; then
    printf "${YELLOW}Uninstall cancelled.${RESET}\n"
    printf "${GRAY}Set COMMANDS_WRAPPER_UNINSTALL_FORCE=1 for non-interactive uninstall.${RESET}\n"
    exit 0
fi

if ! command -v python3 >/dev/null 2>&1; then
    printf "${GRAY}python3 not found; removing shell PATH/hook entries only.${RESET}\n"
    remove_path_persistence
    exit 0
fi

if ! python3 -m pip --version >/dev/null 2>&1; then
    printf "${GRAY}pip not found; removing shell PATH/hook entries only.${RESET}\n"
    remove_path_persistence
    exit 0
fi

BIN_DIR="$(scripts_dir_from_python)"
BIN_PATH="$BIN_DIR/$PRIMARY_WRAPPER"
if [ -f "$BIN_PATH" ]; then
    if ! "$BIN_PATH" sync --uninstall >/dev/null 2>&1; then
        printf "${YELLOW}Wrapper cleanup failed; continuing package uninstall.${RESET}\n"
    fi
fi

if run_pip show commands-wrapper >/dev/null 2>&1; then
    if ! run_pip uninstall commands-wrapper -y >/dev/null 2>&1; then
        printf "${RED}failed to uninstall commands-wrapper.${RESET}\n"
        exit 1
    fi
else
    printf "${GRAY}commands-wrapper is not installed.${RESET}\n"
fi

remove_path_persistence

USER_CONFIG_DIR="$(user_config_dir_from_python || true)"
if [ -n "$USER_CONFIG_DIR" ] && [ -d "$USER_CONFIG_DIR" ]; then
    remove_config="${COMMANDS_WRAPPER_REMOVE_CONFIG:-0}"
    if [ "$remove_config" = "1" ] || confirm_action "Remove user config at '$USER_CONFIG_DIR'? [y/N]"; then
        rm -rf "$USER_CONFIG_DIR"
        printf "${GREEN}Removed user config directory.${RESET}\n"
    else
        printf "${GRAY}Kept user config at '$USER_CONFIG_DIR'.${RESET}\n"
    fi
fi

printf "${GREEN}commands-wrapper uninstalled.${RESET}\n"
printf "${GRAY}If your shell is open, run 'exec \$SHELL' to reload PATH changes.${RESET}\n"
