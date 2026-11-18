#!/bin/sh
# The forced command of the build host's key on a dev target
# (docs/contributing/frontend-dev-loop.md, "A remote target"). setup:remote-target
# installs it as ~/.local/lib/romm-tender-dev/dispatch.sh, beside the running tree
# and the incoming copy rather than in either, so no copy can replace it. sshd
# runs it in place of whatever the key's holder asked for and hands that request
# over in SSH_ORIGINAL_COMMAND, and the request alone decides. Four kinds pass:
#
#   sh -s
#       the setup: setup:remote-target run again sends its install script on
#       stdin, which repairs the target through the key (#2844 D11). It is
#       spelled as a shell command because the same login reaches a shell
#       instead when the user's own access gets in first. It puts this script
#       within the key's reach, which grants nothing the key lacks:
#       dev_target.sh runs whatever tree the key copied in.
#   rsync --server <options> . .local/lib/romm-tender-dev/incoming[/]
#       the copy: rsync receiving into the incoming copy, and nowhere else
#   scripts/dev_target.sh [word ...]
#       the incoming copy's scripts/dev_target.sh, with those words
#   read log
#   read database
#       the backend's backend.log, and its database dumped as SQL through
#       Python's sqlite3, opened read-only
#
# Everything else is refused with REFUSED, an empty request (a login) among it.
#
# The request is split on blanks with globbing off and never handed to a shell
# as text: `sh -c "$SSH_ORIGINAL_COMMAND"` would run whatever a `;` or a `$(...)`
# in it says. A word with a character outside the few each request needs is
# refused rather than quoted.
set -eu
set -f

readonly REFUSED=3

ROOT="$HOME/.local/lib/romm-tender-dev"
# The rsync destination as the client names it: relative to the home directory
# sshd starts a forced command in.
INCOMING_ARGUMENT=".local/lib/romm-tender-dev/incoming"
DEV_TARGET="$ROOT/incoming/scripts/dev_target.sh"
# The defaults install.sh resolves the installed service's directories to.
LOG_FILE="${XDG_STATE_HOME:-$HOME/.local/state}/romm-tender/backend.log"
DATABASE="${XDG_DATA_HOME:-$HOME/.local/share}/romm-tender/romm-tender.db"
# The interpreter install.sh picks.
PYTHON="${TENDER_PYTHON:-/usr/bin/python3}"

refuse() {
    printf 'tender-dev: refused: %s\n' "${SSH_ORIGINAL_COMMAND:-(a login)}" >&2
    printf '%s\n' "  This key may only run the setup, copy into the incoming tree, run that tree's" \
        "  scripts/dev_target.sh, and read the backend's log and database." >&2
    exit "$REFUSED"
}

# rsync's server half, receiving. The letters an option cluster may carry before
# its `e`: rsync sends the client's short options there, and what follows the
# `e` is the protocol's capability string, not options. `s` (arguments sent
# inside the protocol, where this script cannot see the destination), `R`, `b`
# and `k` (paths outside the plain destination) are among the letters left out.
check_rsync() {
    [ "${1:-}" = --server ] || refuse
    shift
    destination=""
    after_dot=no
    for word in "$@"; do
        if [ "$after_dot" = yes ]; then
            [ -z "$destination" ] || refuse
            destination="$word"
            continue
        fi
        case "$word" in
            .) after_dot=yes ;;
            --delete | --delete-before | --delete-during | --delete-delay | --delete-after | --delete-excluded) ;;
            --force | --numeric-ids | --safe-links | --partial) ;;
            --*) refuse ;;
            -?*)
                letters="${word#-}"
                options="${letters%%e*}"
                capabilities="${letters#"$options"}"
                case "$options" in
                    *[!rlptgoDczHxuvnWIOJLE]*) refuse ;;
                esac
                case "$capabilities" in
                    *[!.A-Za-z]*) refuse ;;
                esac
                ;;
            *) refuse ;;
        esac
    done
    case "$destination" in
        "$INCOMING_ARGUMENT" | "$INCOMING_ARGUMENT/") ;;
        *) refuse ;;
    esac
}

check_words() {
    for word in "$@"; do
        case "$word" in
            '' | *[!A-Za-z0-9._,:=+-]*) refuse ;;
        esac
    done
}

read_log() {
    [ -f "$LOG_FILE" ] || {
        printf 'tender-dev: no backend log at %s\n' "$LOG_FILE" >&2
        exit 1
    }
    exec cat "$LOG_FILE"
}

read_database() {
    [ -f "$DATABASE" ] || {
        printf 'tender-dev: no database at %s\n' "$DATABASE" >&2
        exit 1
    }
    exec "$PYTHON" -c '
import pathlib, sqlite3, sys
connection = sqlite3.connect(pathlib.Path(sys.argv[1]).as_uri() + "?mode=ro", uri=True)
for statement in connection.iterdump():
    sys.stdout.write(statement + "\n")
' "$DATABASE"
}

# shellcheck disable=SC2086 # split on blanks, with globbing off above, on purpose
set -- ${SSH_ORIGINAL_COMMAND:-}
[ $# -gt 0 ] || refuse
cd "$HOME"

case "$1" in
    sh)
        [ "$*" = "sh -s" ] || refuse
        exec sh -s
        ;;
    rsync)
        shift
        check_rsync "$@"
        exec rsync "$@"
        ;;
    scripts/dev_target.sh)
        shift
        check_words "$@"
        [ -f "$DEV_TARGET" ] || {
            printf 'tender-dev: no copy has arrived yet — %s is missing.\n' "$DEV_TARGET" >&2
            exit 1
        }
        exec sh "$DEV_TARGET" "$@"
        ;;
    read)
        [ $# -eq 2 ] || refuse
        case "$2" in
            log) read_log ;;
            database) read_database ;;
            *) refuse ;;
        esac
        ;;
    *) refuse ;;
esac
