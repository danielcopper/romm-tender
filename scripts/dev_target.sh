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
#   dev_target.sh ui-scale <name> <mode>
#       start dev_ui_scale.py as a user unit and follow what it prints until
#       the unit stops; `auto` runs once in the foreground instead, as it does
#       on the build host
#   dev_target.sh ui-scale-stop <name>
#   dev_target.sh stop <name>
#       stop whichever of the dev backend and the installed service holds the
#       lock, and leave Steam and the service's enablement alone
#   dev_target.sh status <name>
#   dev_target.sh stop-dev <name>
#       stop the dev backend's unit alone, never the installed service: what a
#       Ctrl-C on the build host sends while a task follows the backend
#   dev_target.sh install <name> <tarball> <sha256> [--yes]
#       refuse while the dev backend runs; otherwise write the tarball sent on
#       stdin as <tarball> in a temporary directory under the root, its
#       checksum beside it, run the copy's install.sh --from on it (--yes only
#       when given) on the installed service's interpreter, remove both, and
#       end with install.sh's status
#
# <name> is the target's name on the build host, for the messages. Everything
# here needs nothing beyond /usr/bin.
set -eu

here=$(cd "${0%/*}" && pwd)
root="${here%/incoming/scripts}"

# Printed alone on stdout where the following starts: the build host tells a
# task that follows what it started from one cut short before it by this line.
readonly FOLLOWING="tender-dev-target: following"

# The installed service's unit (install.sh UNIT_NAME) and the two this script
# starts.
readonly SERVICE_UNIT="romm-tender"
readonly DEV_UNIT="romm-tender-dev"
readonly UI_SCALE_UNIT="romm-tender-dev-ui-scale"

# Without an installed service, the interpreter install.sh would give one.
readonly DEFAULT_PYTHON="/usr/bin/python3"

say() {
    printf 'tender-dev: %s\n' "$*" >&2
}

# Before anything is started or the running tree replaced.
# $1: what systemctl said.
manager_unreachable() {
    say "could not ask the user manager on $name (systemctl --user); is a session of this user running there? Nothing was started, and the running tree is as it was."
    systemctl_said "${1:-}"
    exit 1
}

# After $1 was started: it may run on without the task here knowing. $3: what
# systemctl said.
manager_lost() {
    say "could not ask the user manager on $name after starting $1 (systemctl --user)."
    systemctl_said "${3:-}"
    printf '%s\n' "  \`mise run dev:status --target $name\` says what runs there; $2" >&2
    exit 1
}

systemctl_said() {
    if [ -n "$1" ]; then
        printf '  systemctl said: %s\n' "$1" >&2
    else
        printf '  systemctl said nothing.\n' >&2
    fi
}

# Sets PYTHON to the interpreter the installed service runs on, so the dev
# backend runs on the same one: the path= field of its ExecStart, which install.sh
# wrote from TENDER_PYTHON or its default. SERVICE_PYTHON holds the same, and is
# empty where no service is installed. Why TENDER_PYTHON is not read here:
# docs/contributing/frontend-dev-loop.md, "The dev tasks on a target".
need_python() {
    exec_start=$(systemctl --user show -p ExecStart --value "$SERVICE_UNIT" 2>&1) || manager_unreachable "$exec_start"
    case "$exec_start" in
        *"path="*)
            PYTHON="${exec_start#*path=}"
            PYTHON="${PYTHON%% ;*}"
            SERVICE_PYTHON="$PYTHON"
            source="the installed service's interpreter"
            ;;
        *)
            PYTHON="$DEFAULT_PYTHON"
            SERVICE_PYTHON=""
            source="the interpreter without an installed service"
            ;;
    esac
    if [ ! -x "$PYTHON" ]; then
        say "no Python at $PYTHON on $name ($source) — install python3 there."
        exit 1
    fi
    # Exit 3 is this check's own answer; anything else is the interpreter failing.
    status=0
    version=$("$PYTHON" -c 'import sys; print("%d.%d" % sys.version_info[:2]); sys.exit(0 if sys.version_info >= (3, 12) else 3)' 2>&1) || status=$?
    case "$status" in
        0) ;;
        3)
            say "$PYTHON on $name ($source) is Python $version; Tender needs 3.12 or newer."
            exit 1
            ;;
        *)
            say "$PYTHON on $name ($source) does not run (exit $status): $version"
            exit 1
            ;;
    esac
}

# The display and the session bus are taken from the user manager (why:
# docs/contributing/frontend-dev-loop.md, "The dev tasks on a target"). Only
# those names are taken, and only a plain value: show-environment writes one
# with special characters in $'...' quoting, which no session address needs.
import_session() {
    environment=$(systemctl --user show-environment 2>&1) || manager_unreachable "$environment"
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
    if [ -z "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]; then
        say "warning: the user manager on $name names no display (DISPLAY, WAYLAND_DISPLAY) — is a desktop session running there? Placing Steam's window and dev:ui-scale need one."
    fi
}

# Status 0 while unit $1 runs, 1 when it does not, 2 when the user manager
# could not be asked; state_said then holds what systemctl said. Read from the
# unit's ActiveState, which systemctl prints with exit 0 whatever the state:
# is-active's exit code is no answer to that, as systemd 261 ends it with 4 for
# a unit that is not loaded. A unit starting or stopping runs: its process is
# there.
unit_state() {
    if ! state_said=$(systemctl --user show -p ActiveState --value "$1" 2>&1); then
        return 2
    fi
    case "$state_said" in
        inactive | failed | '') return 1 ;;
        *) return 0 ;;
    esac
}

# Whether unit $1 runs; a user manager that cannot be asked ends the task.
unit_runs() {
    runs_status=0
    unit_state "$1" || runs_status=$?
    case "$runs_status" in
        0) return 0 ;;
        1) return 1 ;;
        *) manager_unreachable "$state_said" ;;
    esac
}

# The unit's main pid, 0 when it runs none; status 1 when the user manager
# gave no answer — an empty one among them, which is not a 0 — with what
# systemctl said in its place.
main_pid() {
    if ! answer=$(systemctl --user show -p MainPID --value "$1" 2>&1); then
        printf '%s\n' "$answer"
        return 1
    fi
    [ -n "$answer" ] || return 1
    printf '%s\n' "$answer"
}

# Sets holder (none, dev, service, other or unknown), pid, lock, described
# (dev_backend_lock.py's words for the holder) and dev_running. The kernel
# names the holder by pid, which is matched against each unit's main process;
# where it names none, the unit that runs stands in for it (unit_runs).
lock_holder() {
    report=$("$PYTHON" -B "$here/dev_backend_lock.py" --holder) || report=unknown
    state=""
    pid=""
    lock=""
    described=""
    {
        read -r state || true
        read -r pid || true
        read -r lock || true
        read -r described || true
    } <<EOF
$report
EOF
    dev_pid=$(main_pid "$DEV_UNIT") || manager_unreachable "$dev_pid"
    service_pid=$(main_pid "$SERVICE_UNIT") || manager_unreachable "$service_pid"
    dev_running=no
    if unit_runs "$DEV_UNIT"; then
        dev_running=yes
    fi
    case "$state" in
        free) holder=none ;;
        held)
            if [ -n "$pid" ] && [ "$pid" = "$dev_pid" ]; then
                holder=dev
            elif [ -n "$pid" ] && [ "$pid" = "$service_pid" ]; then
                holder=service
            elif [ -z "$pid" ] && [ "$dev_running" = yes ]; then
                holder=dev
            elif [ -z "$pid" ] && unit_runs "$SERVICE_UNIT"; then
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
        *) printf '%s' "${described:-a process this machine does not name}" ;;
    esac
}

refuse_unreadable() {
    say "could not read the single-instance lock on $name at ${lock:-its place}, so whether a backend runs is not known."
    printf '%s\n' "Steam has not been restarted, and the running tree is as it was." >&2
    exit 1
}

# Before anything is replaced or restarted: a start that the lock refuses
# leaves the running tree and Steam as they were.
refuse_if_held() {
    case "$holder" in
        none)
            if [ "$dev_running" = yes ]; then
                say "$DEV_UNIT.service runs on $name but holds no lock — a backend that did not start, or one stopping."
                printf '%s\n' "Stop it with \`mise run dev:stop --target $name\`." \
                    "Steam has not been restarted, and the running tree is as it was." >&2
                exit 1
            fi
            ;;
        unknown) refuse_unreadable ;;
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
        unknown) refuse_unreadable ;;
        dev) ;;
    esac
}

# The copy is made whole beside current/ and put in its place by two renames.
# Between them current/ is missing and .current.old holds the only tree, so a
# run that stopped there is repaired first: .current.old is never removed
# while it is the only tree.
install_tree() {
    if [ ! -e "$root/current" ] && [ -e "$root/.current.old" ]; then
        mv "$root/.current.old" "$root/current"
    fi
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
    log_file=$("$PYTHON" -B "$here/dev_backend_lock.py" --log-path)
    offset=0
    if [ -f "$log_file" ]; then
        offset=$(wc -c < "$log_file")
    fi
    since=$(date +%s)
    systemd-run --user --unit="$DEV_UNIT" --collect --quiet -- "$PYTHON" "$root/current/backend/main.py"
    printf 'tender-dev: the backend runs on %s as %s.service; its log follows; Ctrl-C stops it.\n' \
        "$name" "$DEV_UNIT" >&2
    backend=$(main_pid "$DEV_UNIT") || manager_lost "the backend ($DEV_UNIT.service)" \
        "\`mise run dev:stop --target $name\` stops it." "$backend"
    printf '%s\n' "$FOLLOWING"
    case "$backend" in
        '' | 0)
            if [ -f "$log_file" ]; then
                tail -c "+$((offset + 1))" "$log_file" || true
            fi
            ;;
        *) tail -c "+$((offset + 1))" -F --pid="$backend" "$log_file" || true ;;
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
    [ $# -eq 1 ] || usage
    mode="$1"
    case "$mode" in
        # dev_ui_scale.py ONE_SHOT_MODES: they act once and exit, as on the build host.
        auto | off) exec "$PYTHON" "$here/dev_ui_scale.py" "$mode" ;;
    esac
    if unit_runs "$UI_SCALE_UNIT"; then
        say "dev:ui-scale already runs on $name. Stop it first: mise run dev:ui-scale --stop --target $name"
        exit 1
    fi
    since=$(date +%s)
    systemd-run --user --unit="$UI_SCALE_UNIT" --collect --quiet --setenv=TENDER_DEV_TARGET="$name" -- \
        "$PYTHON" "$here/dev_ui_scale.py" "$mode"
    scale=$(main_pid "$UI_SCALE_UNIT") || manager_lost "dev:ui-scale ($UI_SCALE_UNIT.service)" \
        "\`mise run dev:ui-scale --stop --target $name\` stops it and restores the scale." "$scale"
    printf '%s\n' "$FOLLOWING"
    journalctl --user -u "$UI_SCALE_UNIT" --since "@$since" -o cat --no-pager -f &
    follower=$!
    # journalctl -f never ends by itself, so the unit's main process is waited
    # on beside it, and the following ends with it.
    case "$scale" in
        '' | 0) ;;
        *) tail -f --pid="$scale" /dev/null || true ;;
    esac
    # A moment for journalctl to show what the unit wrote last.
    sleep 1
    kill "$follower" 2>/dev/null || true
    wait "$follower" 2>/dev/null || true
    say "dev:ui-scale on $name has stopped; what it wrote last is above."
    exit 1
}

# Stopping the unit is what restores: dev_ui_scale.py puts the scale and the
# window back on SIGTERM and ends with one of its RESTORE_DONE or
# RESTORE_INCOMPLETE lines, which journald may hand on a moment after the stop.
ui_scale_stop() {
    scale_state=0
    unit_state "$UI_SCALE_UNIT" || scale_state=$?
    case "$scale_state" in
        0) ;;
        1)
            printf 'tender-dev: no dev:ui-scale runs on %s; nothing was stopped.\n' "$name"
            return 0
            ;;
        *)
            say "could not ask the user manager on $name (systemctl --user); nothing was stopped."
            systemctl_said "$state_said"
            exit 4
            ;;
    esac
    since=$(date +%s)
    stop_status=0
    systemctl --user stop "$UI_SCALE_UNIT" || stop_status=$?
    said=""
    for _ in 1 2 3 4 5 6 7 8 9 10; do
        said=$(journalctl --user -u "$UI_SCALE_UNIT" --since "@$since" -o cat --no-pager 2>&1) || true
        case "$said" in
            *"Restore done:"* | *"Restore incomplete:"*) break ;;
        esac
        sleep 1
    done
    printf '%s\n' "$said"
    landed=no
    case "$said" in
        *"Restore done:"*) landed=yes ;;
        *"Restore incomplete:"*)
            say "the restore on $name did not land — see what dev:ui-scale said above."
            ;;
        *)
            say "the restore on $name is not confirmed: dev:ui-scale said neither that it was done nor that it was not. Check the scale and the window there."
            ;;
    esac
    if [ "$stop_status" != 0 ]; then
        say "stopping $UI_SCALE_UNIT.service failed (systemctl ended with $stop_status)."
        exit 1
    fi
    if [ "$landed" = no ]; then
        exit 1
    fi
    printf 'tender-dev: stopped dev:ui-scale on %s.\n' "$name"
}

stop() {
    need_python
    lock_holder
    case "$holder" in
        none)
            if [ "$dev_running" = yes ]; then
                systemctl --user stop "$DEV_UNIT"
                printf 'tender-dev: stopped %s.service on %s, which held no lock. Steam was left as it is.\n' \
                    "$DEV_UNIT" "$name"
            else
                printf 'tender-dev: nothing holds the single-instance lock on %s; nothing was stopped.\n' "$name"
            fi
            ;;
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

# A Ctrl-C on the build host ends what the task started, as it ends the backend
# locally.
stop_dev() {
    dev_state=0
    unit_state "$DEV_UNIT" || dev_state=$?
    case "$dev_state" in
        0) ;;
        1)
            printf 'tender-dev: no dev backend runs on %s; nothing was stopped.\n' "$name"
            return 0
            ;;
        *)
            say "could not ask the user manager on $name (systemctl --user)."
            systemctl_said "$state_said"
            exit 4
            ;;
    esac
    stop_status=0
    stop_said=$(systemctl --user stop "$DEV_UNIT" 2>&1) || stop_status=$?
    if [ "$stop_status" != 0 ]; then
        say "stopping $DEV_UNIT.service on $name failed (systemctl ended with $stop_status)."
        systemctl_said "$stop_said"
        exit 1
    fi
    printf 'tender-dev: stopped the dev backend on %s (%s.service). Steam was left as it is.\n' "$name" "$DEV_UNIT"
}

# The installer is the copy's rather than the one inside the tarball: this
# checkout's, as `curl | bash` hands a user main's installer for whichever
# release it installs. Beside the dev backend the service could not take the
# lock, and install.sh's own refusal there names a Ctrl-C in a terminal this
# target has none of; every other holder is install.sh's to judge. The
# checksum goes where install.sh --from looks for one (verify_local: the
# tarball's name with .sha256, in sha256sum's own format), and TENDER_PYTHON
# keeps an update on the interpreter the unit was written with.
install_release() {
    [ $# -ge 2 ] && [ $# -le 3 ] || usage
    tarball="$1"
    checksum="$2"
    case "$tarball" in
        romm-tender-*.tar.gz) ;;
        *) usage ;;
    esac
    case "$checksum" in
        '' | *[!0-9a-f]*) usage ;;
    esac
    [ "${#checksum}" -eq 64 ] || usage
    assume_yes=""
    case "${3:-}" in
        '') ;;
        --yes) assume_yes="--yes" ;;
        *) usage ;;
    esac
    installer="$root/incoming/install.sh"
    if [ ! -f "$installer" ]; then
        say "the copy on $name holds no install.sh ($installer); nothing was installed."
        exit 1
    fi
    need_python
    lock_holder
    if [ "$holder" = dev ] || { [ "$holder" = none ] && [ "$dev_running" = yes ]; }; then
        say "the dev backend runs on $name ($DEV_UNIT.service${pid:+, pid $pid}), and the installed service could not start beside it."
        printf '%s\n' "Stop it with \`mise run dev:stop --target $name\`, then run the install again." \
            "Nothing was installed." >&2
        exit 1
    fi
    work=$(mktemp -d "$root/.install.XXXXXX")
    trap 'rm -rf "$work"' EXIT
    trap 'exit 129' HUP
    trap 'exit 130' INT
    trap 'exit 143' TERM
    cat > "$work/$tarball"
    if [ ! -s "$work/$tarball" ]; then
        say "no tarball arrived on $name; nothing was installed."
        exit 1
    fi
    printf '%s  %s\n' "$checksum" "$tarball" > "$work/$tarball.sha256"
    if [ -n "$SERVICE_PYTHON" ]; then
        TENDER_PYTHON="$SERVICE_PYTHON"
        export TENDER_PYTHON
    else
        unset TENDER_PYTHON
    fi
    install_status=0
    bash "$installer" --from "$work/$tarball" ${assume_yes:+"$assume_yes"} || install_status=$?
    exit "$install_status"
}

status() {
    need_python
    lock_holder
    case "$holder" in
        none)
            if [ "$dev_running" = yes ]; then
                printf 'tender-dev: nothing holds the single-instance lock on %s (%s).\n' "$name" "$lock"
            else
                printf 'tender-dev: no backend runs on %s — nothing holds the single-instance lock (%s).\n' \
                    "$name" "$lock"
            fi
            ;;
        unknown) printf 'tender-dev: the single-instance lock on %s could not be read (%s).\n' "$name" "${lock:-its place}" ;;
        *) printf 'tender-dev: %s holds the single-instance lock on %s (%s).\n' "$(holder_text)" "$name" "$lock" ;;
    esac
    if [ "$dev_running" = yes ] && [ "$holder" = unknown ]; then
        printf 'tender-dev: %s.service runs on %s.\n' "$DEV_UNIT" "$name"
    elif [ "$dev_running" = yes ] && [ "$holder" != dev ]; then
        printf 'tender-dev: %s.service runs on %s but holds no lock.\n' "$DEV_UNIT" "$name"
    fi
    if unit_runs "$UI_SCALE_UNIT"; then
        printf 'tender-dev: dev:ui-scale runs on %s (%s.service).\n' "$name" "$UI_SCALE_UNIT"
    else
        printf 'tender-dev: no dev:ui-scale runs on %s.\n' "$name"
    fi
}

usage() {
    say "usage: dev_target.sh start|frontend|reset|ui-scale|ui-scale-stop|stop|stop-dev|status|install <name> [word ...]"
    exit 2
}

# systemctl --user finds the user manager through this; a login without
# pam_systemd sets none.
if [ -z "${XDG_RUNTIME_DIR:-}" ] && [ -d "/run/user/$(id -u)" ]; then
    XDG_RUNTIME_DIR="/run/user/$(id -u)"
    export XDG_RUNTIME_DIR
fi

[ $# -ge 2 ] || usage
verb="$1"
name="$2"
shift 2
# dev_steam.sh and dev_ui_scale.py name the target in the tasks their hints give.
TENDER_DEV_TARGET="$name"
export TENDER_DEV_TARGET
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
    stop-dev) stop_dev ;;
    status) status ;;
    install) install_release "$@" ;;
    *) usage ;;
esac
