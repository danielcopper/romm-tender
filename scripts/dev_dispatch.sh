#!/bin/sh
# The forced command of the build host's key on a dev target. setup:remote-target
# installs it as dispatch.sh beside the running tree (current/) and the incoming
# copy (incoming/), in neither, so no copy can replace it. sshd runs it in place
# of whatever the key's holder asked for and hands that request over in
# SSH_ORIGINAL_COMMAND; the request alone decides. What each kind is for, and
# why the setup request costs nothing: docs/contributing/frontend-dev-loop.md,
# "What the key may do on the target". The exact requests that pass:
#
#   sh -s
#       the setup's install script on stdin. Spelled as a shell command because
#       the setup sends the same command line to a login of the user's own,
#       which reaches a shell.
#   rsync --server <options> . <incoming>[/]
#       rsync receiving into the incoming copy, named relative to the home
#       directory sshd starts a forced command in
#   scripts/dev_target.sh [word ...]
#       the incoming copy's scripts/dev_target.sh, with those words
#   read log
#   read database
#       the backend's log, and its database dumped as SQL through Python's
#       sqlite3, opened read-only
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

# Where setup:remote-target put this script: its own directory. The install
# writes the key's command= with this script's absolute path, so $0 names it.
ROOT="${0%/*}"
INCOMING_ARGUMENT="${ROOT#"$HOME"/}/incoming"
DEV_TARGET="$ROOT/incoming/scripts/dev_target.sh"

# The backend's names (host/logging_setup.py LOG_FILENAME, bootstrap/adapters.py
# DB_FILENAME, domain/user_data_location.py APP_DIR_NAME), install.sh's ladder
# for the directories they lie in, and install.sh's default interpreter. sshd
# hands a forced command none of the installer's environment (the service unit's
# Environment= lines among it), so the TENDER_* rungs below hold only for a
# caller that sets them itself; an install into directories of its own is not
# found here.
APP_DIR="romm-tender"
LOG_NAME="backend.log"
DATABASE_NAME="romm-tender.db"
LOG_FILE="${TENDER_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/$APP_DIR}/$LOG_NAME"
DATABASE="${TENDER_DATA_DIR:-${XDG_DATA_HOME:-$HOME/.local/share}/$APP_DIR}/$DATABASE_NAME"
PYTHON="${TENDER_PYTHON:-/usr/bin/python3}"

refuse() {
    printf 'tender-dev: refused: %s\n' "${SSH_ORIGINAL_COMMAND:-(a login)}" >&2
    printf '%s\n' "  This key may only run the setup, copy into the incoming tree, run that tree's" \
        "  scripts/dev_target.sh, and read the backend's log and database." >&2
    exit "$REFUSED"
}

# rsync's server half, receiving. The letters an option cluster may carry before
# its `e`: rsync sends the client's short options there, and what follows the
# `e` is the protocol's capability string, not options. Among the letters left
# out are `s`, which sends the arguments inside the protocol where this script
# cannot see the destination, and `K` (--keep-dirlinks), which has the receiver
# follow a symlinked directory out of the incoming copy.
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
