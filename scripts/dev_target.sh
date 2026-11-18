#!/bin/sh
# The target's half of a dev task run with --target. The dispatcher runs it from
# the incoming copy (incoming/scripts/) with the words scripts/dev_target.py
# sent; what each task does here: docs/contributing/frontend-dev-loop.md,
# "The dev tasks on a target".
#
#   dev_target.sh start <name> [restart [bpm|desktop [display]]]
#       refuse while a backend holds the lock; otherwise put the incoming copy
#       in place of current/, restart Steam through dev_steam.sh when asked to,
#       start the backend as a user unit and follow its backend.log until the
#       backend stops
#   dev_target.sh frontend <name> [display]
#       refuse unless the dev backend holds the lock; otherwise put the copy in
#       place and restart Steam into the remembered window, starting no backend
#   dev_target.sh reset <name> bpm|desktop [display]
#   dev_target.sh ui-scale <name> [mode]
#       start dev_ui_scale.py as a user unit and follow what it prints; `auto`
#       runs once in the foreground instead, as it does on the build host
#   dev_target.sh ui-scale-stop <name>
#   dev_target.sh stop <name>
#       stop whichever of the dev backend and the installed service holds the
#       lock, and leave Steam and the service's enablement alone
#   dev_target.sh status <name>
#
# <name> is the target's name on the build host, for the messages. Everything
# here needs nothing beyond /usr/bin.
set -eu

here=$(cd "${0%/*}" && pwd)
root="${here%/incoming/scripts}"

# Printed alone on stdout once what follows is the output being followed, so
# the build host knows Ctrl-C from then on ends only the following.
readonly FOLLOWING="tender-dev-target: following"

# The installed service's unit (install.sh UNIT_NAME) and the two this script
# starts.
readonly SERVICE_UNIT="romm-tender"
readonly DEV_UNIT="romm-tender-dev"
readonly UI_SCALE_UNIT="romm-tender-dev-ui-scale"

# The backend's log (host/logging_setup.py LOG_FILENAME, domain/user_data_location.py
# APP_DIR_NAME) on install.sh's ladder, as the dispatcher reads it.
APP_DIR="romm-tender"
LOG_NAME="backend.log"
LOG_FILE="${TENDER_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/$APP_DIR}/$LOG_NAME"

# install.sh's interpreter rule, so the dev backend runs on what the installed
# service runs on.
PYTHON="${TENDER_PYTHON:-/usr/bin/python3}"

say() {
    printf 'tender-dev: %s\n' "$*" >&2
}

need_python() {
    if [ ! -x "$PYTHON" ]; then
        say "no Python at $PYTHON on $name — install python3, or point TENDER_PYTHON at one."
        exit 1
    fi
    if ! "$PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)'; then
        say "$PYTHON on $name is older than 3.12, which Tender needs."
        exit 1
    fi
}

# sshd hands this script none of the desktop session: the display and the
# session bus that window placement and dev_ui_scale.py talk to are taken from
# the user manager, whose units (the backend, Steam) run in them already. Only
# those names are taken, and only a plain value: show-environment writes one
# with special characters in $'...' quoting, which no session address needs.
import_session() {
    if [ -z "${XDG_RUNTIME_DIR:-}" ] && [ -d "/run/user/$(id -u)" ]; then
        XDG_RUNTIME_DIR="/run/user/$(id -u)"
        export XDG_RUNTIME_DIR
    fi
    environment=$(systemctl --user show-environment 2>/dev/null) || return 0
    while IFS= read -r line; do
        case "$line" in
            DISPLAY=* | WAYLAND_DISPLAY=* | XAUTHORITY=* | DBUS_SESSION_BUS_ADDRESS=*)
                case "${line#*=}" in
                    "\$'"*) ;;
                    *) export "${line?}" ;;
                esac
                ;;
        esac
    done <<EOF
$environment
EOF
}

is_active() {
    systemctl --user is-active --quiet "$1"
}

main_pid() {
    systemctl --user show -p MainPID --value "$1" 2>/dev/null || true
}

# Sets holder (none, dev, service, other or unknown), pid and lock. The
# kernel names the holder by pid, which is matched against each unit's main
# process; where it names none, the unit that runs stands in for it.
lock_holder() {
    report=$("$PYTHON" -B "$here/dev_backend_lock.py" --holder) || report=unknown
    state=""
    pid=""
    lock=""
    {
        read -r state || true
        read -r pid || true
        read -r lock || true
    } <<EOF
$report
EOF
    case "$state" in
        free) holder=none ;;
        held)
            if [ -n "$pid" ] && [ "$pid" = "$(main_pid "$DEV_UNIT")" ]; then
                holder=dev
            elif [ -n "$pid" ] && [ "$pid" = "$(main_pid "$SERVICE_UNIT")" ]; then
                holder=service
            elif [ -z "$pid" ] && is_active "$DEV_UNIT"; then
                holder=dev
            elif [ -z "$pid" ] && is_active "$SERVICE_UNIT"; then
                holder=service
            else
                holder=other
            fi
            ;;
        *) holder=unknown ;;
    esac
}

holder_text() {
    case "$holder" in
        dev) printf 'the dev backend (%s.service%s)' "$DEV_UNIT" "${pid:+, pid $pid}" ;;
        service) printf 'the installed service (%s.service%s)' "$SERVICE_UNIT" "${pid:+, pid $pid}" ;;
        *)
            if [ -n "$pid" ]; then
                printf 'pid %s (%s)' "$pid" "$(cat "/proc/$pid/comm" 2>/dev/null || printf 'gone')"
            else
                printf 'a process this machine does not name'
            fi
            ;;
    esac
}

# Before anything is replaced or restarted: a start that the lock refuses
# leaves the running tree and Steam as they were.
refuse_if_held() {
    case "$holder" in
        none) ;;
        unknown) say "could not read the single-instance lock on $name at ${lock:-its place} — carrying on anyway." ;;
        other)
            say "a backend is already running on $name — $(holder_text) holds the single-instance lock, and it is neither the dev backend nor the installed service."
            printf '%s\n' "  lock: $lock" \
                "Stop it on $name by hand; mise run dev:stop stops only the dev backend and the installed service." \
                "Steam has not been restarted, and the running tree is as it was." >&2
            exit 1
            ;;
        *)
            say "a backend is already running on $name — $(holder_text) holds the single-instance lock."
            printf '%s\n' "  lock: $lock" \
                "Stop it with \`mise run dev:stop --target $name\`, or use a -reset task, which starts no backend." \
                "Steam has not been restarted, and the running tree is as it was." >&2
            exit 1
            ;;
    esac
}

# The installed service loads the panel it was installed with, not the copy, so
# only the dev backend makes a restart worth it.
refuse_unless_dev() {
    case "$holder" in
        none)
            say "no backend is running on $name — nothing would load the panel into the fresh Steam."
            printf '%s\n' "  lock: $lock" \
                "Start one first: \`mise run dev:backend --target $name\` runs one against the Steam that is up," \
                "and \`mise run dev --target $name\` restarts Steam and runs one itself." \
                "Steam has not been restarted, and the running tree is as it was." >&2
            exit 1
            ;;
        service | other)
            say "$(holder_text) holds the single-instance lock on $name, and it loads its own panel, not this build."
            printf '%s\n' "  lock: $lock" >&2
            if [ "$holder" = service ]; then
                printf '%s\n' "Stop it with \`mise run dev:stop --target $name\`, then \`mise run dev:backend --target $name\`." >&2
            else
                printf '%s\n' "Stop it on $name by hand, then \`mise run dev:backend --target $name\`." >&2
            fi
            printf '%s\n' "Steam has not been restarted, and the running tree is as it was." >&2
            exit 1
            ;;
        unknown) say "could not read the single-instance lock on $name at ${lock:-its place} — carrying on anyway." ;;
        dev) ;;
    esac
}

# The copy is made whole beside current/ and swapped in by two renames, so a
# step that fails leaves current/ as it was.
install_tree() {
    rm -rf "$root/.current.new" "$root/.current.old"
    cp -a "$root/incoming" "$root/.current.new"
    if [ -e "$root/current" ]; then
        mv "$root/current" "$root/.current.old"
    fi
    mv "$root/.current.new" "$root/current"
    rm -rf "$root/.current.old"
}

# Follows backend.log from where it ended before the start until the backend's
# process ends; what the process wrote to stderr — an import that failed
# before the log was opened among it — is in the unit's journal.
start_backend() {
    offset=0
    if [ -f "$LOG_FILE" ]; then
        offset=$(wc -c < "$LOG_FILE")
    fi
    since=$(date +%s)
    systemd-run --user --unit="$DEV_UNIT" --collect --quiet -- "$PYTHON" "$root/current/backend/main.py"
    printf 'tender-dev: the backend runs on %s as %s.service; its log follows. Ctrl-C ends only the following.\n' \
        "$name" "$DEV_UNIT" >&2
    backend=$(main_pid "$DEV_UNIT")
    printf '%s\n' "$FOLLOWING"
    case "$backend" in
        '' | 0)
            if [ -f "$LOG_FILE" ]; then
                tail -c "+$((offset + 1))" "$LOG_FILE" || true
            fi
            ;;
        *) tail -c "+$((offset + 1))" -F --pid="$backend" "$LOG_FILE" || true ;;
    esac
    say "the backend on $name has stopped. What it wrote to stderr since it started:"
    journalctl --user -u "$DEV_UNIT" --since "@$since" -o cat --no-pager >&2 || true
    exit 1
}

start() {
    need_python
    import_session
    lock_holder
    refuse_if_held
    install_tree
    if [ "${1:-}" = restart ]; then
        shift
        bash "$here/dev_steam.sh" restart "$@"
    fi
    start_backend
}

frontend() {
    need_python
    import_session
    lock_holder
    refuse_unless_dev
    install_tree
    bash "$here/dev_steam.sh" restart-remembered "$@"
}

ui_scale() {
    need_python
    import_session
    mode="${1:-deck}"
    case "$mode" in
        auto | off) exec "$PYTHON" "$here/dev_ui_scale.py" "$mode" ;;
    esac
    if is_active "$UI_SCALE_UNIT"; then
        say "dev:ui-scale already runs on $name. Stop it first: mise run dev:ui-scale --stop --target $name"
        exit 1
    fi
    since=$(date +%s)
    systemd-run --user --unit="$UI_SCALE_UNIT" --collect --quiet -- "$PYTHON" "$here/dev_ui_scale.py" "$mode"
    printf '%s\n' "$FOLLOWING"
    exec journalctl --user -u "$UI_SCALE_UNIT" --since "@$since" -o cat --no-pager -f
}

# Stopping the unit is what restores: dev_ui_scale.py puts the scale and the
# window back on SIGTERM, and what it says then is in the journal.
ui_scale_stop() {
    if ! is_active "$UI_SCALE_UNIT"; then
        printf 'tender-dev: no dev:ui-scale runs on %s; nothing was stopped.\n' "$name"
        return 0
    fi
    since=$(date +%s)
    systemctl --user stop "$UI_SCALE_UNIT"
    journalctl --user -u "$UI_SCALE_UNIT" --since "@$since" -o cat --no-pager || true
    printf 'tender-dev: stopped dev:ui-scale on %s.\n' "$name"
}

stop() {
    need_python
    lock_holder
    case "$holder" in
        none) printf 'tender-dev: nothing holds the single-instance lock on %s; nothing was stopped.\n' "$name" ;;
        dev)
            systemctl --user stop "$DEV_UNIT"
            printf 'tender-dev: stopped the dev backend on %s (%s.service). Steam was left as it is.\n' \
                "$name" "$DEV_UNIT"
            ;;
        service)
            systemctl --user stop "$SERVICE_UNIT"
            printf '%s\n' "tender-dev: stopped the installed service on $name ($SERVICE_UNIT.service). Steam was left as it is." \
                "  The service stays enabled and starts again at the next login; to start it now, run on $name:" \
                "    systemctl --user start $SERVICE_UNIT"
            ;;
        other)
            say "$(holder_text) holds the single-instance lock on $name, and it is neither the dev backend nor the installed service; nothing was stopped."
            exit 1
            ;;
        *)
            say "could not read the single-instance lock on $name at ${lock:-its place}; nothing was stopped."
            exit 1
            ;;
    esac
}

status() {
    need_python
    lock_holder
    case "$holder" in
        none) printf 'tender-dev: no backend runs on %s — nothing holds the single-instance lock (%s).\n' "$name" "$lock" ;;
        unknown) printf 'tender-dev: the single-instance lock on %s could not be read (%s).\n' "$name" "${lock:-its place}" ;;
        *) printf 'tender-dev: %s holds the single-instance lock on %s (%s).\n' "$(holder_text)" "$name" "$lock" ;;
    esac
    if is_active "$UI_SCALE_UNIT"; then
        printf 'tender-dev: dev:ui-scale runs on %s (%s.service).\n' "$name" "$UI_SCALE_UNIT"
    else
        printf 'tender-dev: no dev:ui-scale runs on %s.\n' "$name"
    fi
}

usage() {
    say "usage: dev_target.sh start|frontend|reset|ui-scale|ui-scale-stop|stop|status <name> [word ...]"
    exit 2
}

[ $# -ge 2 ] || usage
verb="$1"
name="$2"
shift 2
case "$verb" in
    start) start "$@" ;;
    frontend) frontend "$@" ;;
    reset)
        import_session
        bash "$here/dev_steam.sh" restart "$@"
        ;;
    ui-scale) ui_scale "$@" ;;
    ui-scale-stop) ui_scale_stop ;;
    stop) stop ;;
    status) status ;;
    *) usage ;;
esac
