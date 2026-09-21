#!/usr/bin/env bash
#
# install.sh — install Tender, a RomM library in Steam, for this user.
#
#   curl -fsSL https://raw.githubusercontent.com/danielcopper/romm-tender/main/install.sh | bash
#
# No sudo, nothing outside this user's home, and no daemon but one systemd user
# unit. Run it again to update; `--uninstall` takes it back out.
#
# TEST SEAMS. Each is read once, at the top of this script, and each has a
# working default, so a test — or a hand install into another tree — can move
# the whole thing without touching this file. The six directory names are the
# backend's own ladder rather than this script's: it reads them too, which is
# why the unit below carries them:
#
#   TENDER_PYTHON         the interpreter, checked here AND written into the
#                         unit. One value with two uses, so what was verified is
#                         what will run. Default /usr/bin/python3.
#   TENDER_CODE_DIR       where the program goes
#   TENDER_CONFIG_DIR     settings
#   TENDER_DATA_DIR       the database
#   TENDER_CACHE_DIR      covers and artwork
#   TENDER_STATE_DIR      the log
#   TENDER_BIN_DIR        the launcher every Steam shortcut starts through
#                         — the five above and this one are written into the
#                         unit verbatim.
#   TENDER_ACK_UNTIL      the acknowledgement's expiry date
#   TENDER_RELEASE_API    where the newest release is looked up
#   TENDER_DOWNLOAD_BASE  where a release's assets are downloaded from
#
# The last two default to GitHub's addresses, so a test never reaches the
# network.

set -euo pipefail

# The acknowledgement below is for the 0.33 → 1.0 transition: shortcuts written
# by a Decky-era Tender are not recognised by this one. It stops being shown
# from this date on, and this constant is the only thing to touch when it has
# outlived its reason.
ACK_UNTIL="${TENDER_ACK_UNTIL:-2027-03-31}"

RELEASE_API="${TENDER_RELEASE_API:-https://api.github.com/repos/danielcopper/romm-tender/releases/latest}"
DOWNLOAD_BASE="${TENDER_DOWNLOAD_BASE:-https://github.com/danielcopper/romm-tender/releases/download}"

MIGRATION_NOTES="https://danielcopper.github.io/romm-tender/user-guide/getting-started/"

PYTHON="${TENDER_PYTHON:-/usr/bin/python3}"

# Every path this script touches, resolved once. The unit carries the answers as
# absolute paths because the installer and the service do not share an
# environment: this runs in a login shell, the service runs under the user
# manager, and an XDG_DATA_HOME set in a shell profile never reaches the latter.
CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"
CONFIG="${TENDER_CONFIG_DIR:-$CONFIG_HOME/romm-tender}"
DATA="${TENDER_DATA_DIR:-${XDG_DATA_HOME:-$HOME/.local/share}/romm-tender}"
CACHE="${TENDER_CACHE_DIR:-${XDG_CACHE_HOME:-$HOME/.cache}/romm-tender}"
STATE="${TENDER_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/romm-tender}"
BIN="${TENDER_BIN_DIR:-$HOME/.local/bin}"
CODE="${TENDER_CODE_DIR:-$HOME/.local/lib/romm-tender}"
UNIT_DIR="$CONFIG_HOME/systemd/user"
UNIT="$UNIT_DIR/romm-tender.service"
UNIT_NAME="romm-tender"

# The note recording that Steam's debugger marker is ours. Its FIRST LINE is the
# absolute path of the marker that was created, which is what `--uninstall`
# removes — the note is the whole of the authority, so it has to name the one
# file rather than a name to go looking for. The
# backend writes the same two-line SHAPE under the same filename — the marker's
# path, then which program wrote it and when (DEBUGGER_MARKER_NOTE in
# backend/host/inject/machine.py) — and tests/scripts/test_install_sh.py holds
# the two spellings equal; the uninstaller reads whichever of the two wrote it.
MARKER_NOTE="debugger-marker"
MARKER_FILE=".cef-enable-remote-debugging"

# Steam's CEF debugger. Answering means the panel can be loaded without a
# restart; not answering is the ordinary case on a fresh install.
DEBUGGER_PROBE="http://127.0.0.1:8080/json/version"

# Where Steam is. Answered by the pre-flight, which every path that reads it
# runs first.
STEAM_ROOT=""

# What obtain_tarball answers with, and resolve_tag's two refusals that are not
# "the release named is not one of ours".
TARBALL=""
TAG_UNREACHABLE=2
TAG_ABSENT=3

# Whether Steam's debugger answered the probe, which decides the closing line.
DEBUGGER_ANSWERED="no"

# What the EXIT trap has to clean up: a download directory, a spinner that is
# still drawing, and a step whose line was never finished.
WORK_DIR=""
SPINNER_PID=""
STEP_MESSAGE=""

# What one folder's move did, set by move_folder and read by the sentence after
# it. Three counts rather than a return value: they are three different things
# to say.
MOVED=0
STAYED=0
FAILED=0

MODE="install"
VERSION=""
LOCAL_FILE=""
ASSUME_YES="no"

main() {
    trap cleanup EXIT
    parse_arguments "$@"

    case "$MODE" in
        disable) do_disable ;;
        uninstall) do_uninstall ;;
        install) do_install ;;
    esac
}

usage() {
    cat <<'TEXT'
Usage: install.sh [options]

  (no options)     install or update to the newest release
  --version X      install or update to release X
  --from FILE      install from a tarball already on disk
  --disable        stop Tender and leave it installed
  --uninstall      remove Tender
  --yes            skip the acknowledgement (required when there is no terminal)
  --help           this text
TEXT
}

# --------------------------------------------------------- how this looks

# Whether stdout is a terminal. Every decision about colour, a spinner or a
# progress bar is this question and nothing else, so a run whose output is piped
# or redirected is plain text throughout.
on_a_terminal() {
    [ -t 1 ]
}

# Whether this run may use colour at all. NO_COLOR is the convention
# (https://no-color.org): present and NOT EMPTY means no, whatever its value.
# An empty NO_COLOR says nothing, which is why the test is -z and not -n.
may_colour() {
    on_a_terminal && [ -z "${NO_COLOR:-}" ]
}

# The acknowledgement is the one warning this script prints, and these two are
# the only place escape codes are written. Everything else is plain, which is
# what makes the warning read as one.
warn_style() {
    ! may_colour || printf '\033[1;33m'
}

warn_reset() {
    ! may_colour || printf '\033[0m'
}

# A path with the user's home written as `~`. For printing only — nothing is
# ever opened through the result.
tilde() {
    case "$1" in
        "$HOME"/*) printf '~%s\n' "${1#"$HOME"}" ;;
        *) printf '%s\n' "$1" ;;
    esac
}

# One phase: say what is about to happen, do it, and say how it went.
#
# `step <message> <command> [args...]`, always as a plain command and never as
# `$(step …)`. The commands it runs abort on failure, and an abort inside a
# substitution would end the subshell rather than the run. **The gate does not
# cover this one**: `step` reaches its command through `"$@"`, which
# `scripts/check_shell_answer_functions.py` cannot resolve, so the discipline
# here is the comment and nothing else.
step() {
    local message="$1"
    shift
    STEP_MESSAGE="$message"
    printf '%s …' "$message"
    spinner_start
    local status=0
    "$@" || status=$?
    spinner_stop
    if [ "$status" -eq 0 ]; then
        end_step "done"
    else
        end_step "failed"
    fi
    STEP_MESSAGE=""
    return "$status"
}

# Finish the line a step opened. On a terminal the spinner has been overwriting
# it, so the whole line is rewritten; without one nothing has moved and only the
# ending is needed — which is why a piped run carries no carriage returns at all.
end_step() {
    if on_a_terminal; then
        printf '\r%s … %s\n' "$STEP_MESSAGE" "$1"
    else
        printf ' %s\n' "$1"
    fi
}

# A spinner for the phases that take seconds with nothing to show: unpacking,
# a thousand cover moves, a service starting. A background loop rather than
# progress, because none of those can say how far along they are.
spinner_start() {
    on_a_terminal || return 0
    spin &
    SPINNER_PID=$!
}

spin() {
    local frames="|/-\\" index=0
    while true; do
        index=$(((index + 1) % 4))
        printf '\r%s … %s' "$STEP_MESSAGE" "${frames:index:1}"
        sleep 0.1
    done
}

# Every path out of a step comes through here, including the abort one, so no
# spinner outlives the phase it belongs to.
spinner_stop() {
    [ -n "$SPINNER_PID" ] || return 0
    kill "$SPINNER_PID" 2> /dev/null || true
    wait "$SPINNER_PID" 2> /dev/null || true
    SPINNER_PID=""
}

# Close a step nobody closed. A step is still in flight here only when the
# command it ran ended the script from inside, so the outcome is known.
fail_open_step() {
    spinner_stop
    [ -n "$STEP_MESSAGE" ] || return 0
    end_step "failed"
    STEP_MESSAGE=""
}

# The one EXIT trap. `abort` closes the step itself so its message comes after
# the line it belongs to rather than before; this is the backstop for every
# other way out.
cleanup() {
    fail_open_step
    [ -z "$WORK_DIR" ] || rm -rf "$WORK_DIR"
}

# Ends the run. **A function whose VALUE is taken with `$(...)` never calls this**
# — it answers instead, and its caller aborts. Why, and what the gate can and
# cannot see: scripts/check_shell_answer_functions.py.
abort() {
    fail_open_step
    echo "install.sh: $1" >&2
    [ $# -lt 2 ] || echo "  $2" >&2
    exit 1
}

# The value after an option, or non-zero where the option has none or an empty
# one. Empty is refused rather than carried: `--version ""` would otherwise read
# as no version at all and install the newest release without saying so.
#
# `printf` rather than `echo`, which would swallow a value of `-n` as a flag of
# its own.
value_of() {
    [ $# -ge 2 ] && [ -n "$2" ] || return 1
    printf '%s\n' "$2"
}

parse_arguments() {
    while [ $# -gt 0 ]; do
        case "$1" in
            --version)
                VERSION="$(value_of "$@")" || abort "$1 needs a value" "run install.sh --help to see what it takes"
                shift 2
                ;;
            --from)
                LOCAL_FILE="$(value_of "$@")" || abort "$1 needs a value" "run install.sh --help to see what it takes"
                shift 2
                ;;
            --uninstall)
                MODE="uninstall"
                shift
                ;;
            --disable)
                MODE="disable"
                shift
                ;;
            --yes)
                ASSUME_YES="yes"
                shift
                ;;
            -h | --help)
                usage
                exit 0
                ;;
            *) abort "unknown argument: $1" "run install.sh --help to see what it takes" ;;
        esac
    done
}

# ---------------------------------------------------------------- pre-flight

# Every refusal below is exit 1 and names both the reason and the fix. The order
# is the order in which the answers become useful: a Python to run anything
# with, a manager to run it under, then Steam for it to load a panel into, then
# the plugin that would otherwise share the database with it.
preflight() {
    check_python
    check_user_manager
    refuse_flatpak_steam
    STEAM_ROOT="$(check_native_steam)" ||
        abort "no native Steam installation found" "install Steam and run it once, then run this again"
    refuse_decky_plugin
}

check_python() {
    [ -x "$PYTHON" ] || abort "no Python at $PYTHON" "install python3, or point TENDER_PYTHON at one"
    if ! "$PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'; then
        abort "$PYTHON is older than 3.11" "Tender needs Python 3.11 or newer"
    fi
}

check_user_manager() {
    if ! systemctl --user show-environment > /dev/null 2>&1; then
        abort "no systemd user manager is reachable" "log in to a normal desktop session and run this again"
    fi
}

refuse_flatpak_steam() {
    if [ -d "$HOME/.var/app/com.valvesoftware.Steam" ]; then
        abort "Flatpak Steam is not supported" "Tender needs a native Steam install"
    fi
}

# The one place that answers where Steam is, and it answers by printing the
# root. Same two spellings in the same order as backend/host/inject/machine.py,
# which states why there are two.
check_native_steam() {
    local candidate
    for candidate in "$HOME/.local/share/Steam" "$HOME/.steam/steam"; do
        if [ -d "$candidate" ]; then
            echo "$candidate"
            return 0
        fi
    done
    return 1
}

# Two backends working on one database is the failure this refuses. Parsed with
# grep rather than jq, which is not on every target: a plugin.json is one line
# per field in practice, and what is needed is only whether the name field names
# this plugin.
refuse_decky_plugin() {
    local manifest folder
    for manifest in "$HOME"/homebrew/plugins/*/plugin.json; do
        [ -f "$manifest" ] || continue
        if grep -Eq '"name"[[:space:]]*:[[:space:]]*"(Tender|RomM Sync)"' "$manifest"; then
            folder="$(dirname "$manifest")"
            abort "Tender is still installed as a Decky plugin in $folder" \
                "remove it in Decky first, then run this again"
        fi
    done
}

# --------------------------------------------------------- acknowledgement

# Read from /dev/tty rather than stdin, because under `curl | bash` stdin IS
# this script.
acknowledge() {
    [ "$ASSUME_YES" = "yes" ] && return 0
    [ "$(date -u +%Y-%m-%d)" \< "$ACK_UNTIL" ] || return 0

    echo
    warn_style
    cat <<TEXT
Did you run Tender as a Decky plugin before?
Then read the migration notes first — your Steam shortcuts from that install are not recognised by this
version, and the notes say what to do about it:
  $MIGRATION_NOTES
TEXT
    warn_reset
    # Opened rather than tested for: /dev/tty is readable by its permissions
    # whether or not this process has a controlling terminal, so `[ -r ... ]`
    # answers yes under `curl | bash` in a session that has none, and only the
    # open itself tells the two apart.
    #
    # The braces are load-bearing. `exec` with no command applies its
    # redirections to the SHELL, so an unbraced `2> /dev/null` here would send
    # this script's stderr to /dev/null for the rest of the run — every later
    # abort, and every message from curl, tar and systemctl, lost on exactly the
    # machines that have a terminal to read them.
    if ! { exec 3< /dev/tty; } 2> /dev/null; then
        abort "there is no terminal to ask on" "run with --yes once you have read the migration notes"
    fi
    local answer=""
    printf 'Continue? [y/N] '
    read -r answer <&3 || true
    exec 3<&-
    echo
    # The conventional shape: the capital N is the answer a bare Enter gives,
    # and it is the safe one. Anything that is not a yes refuses.
    case "$(printf '%s' "$answer" | tr '[:upper:]' '[:lower:]')" in
        y | yes) ;;
        *) abort "not confirmed" "run with --yes once you have read the migration notes" ;;
    esac
}

# ------------------------------------------------------------------- fetch

# Puts the tarball's path in TARBALL, verified wherever a checksum exists to
# verify it against — always for a release, and for a local file only where one
# sits beside it. Everything downloaded goes into a directory of its own that
# the caller removes.
#
# It sets a variable rather than printing its answer, so that it runs in THIS
# shell: each refusal below is the end of the run, and every one of them would
# end a subshell instead if the caller took its value with `$(...)`.
obtain_tarball() {
    local work="$1"

    if [ -n "$LOCAL_FILE" ]; then
        [ -f "$LOCAL_FILE" ] || abort "no such file: $LOCAL_FILE"
        verify_local "$LOCAL_FILE"
        TARBALL="$LOCAL_FILE"
        return 0
    fi

    local tag="" status=0
    tag="$(resolve_tag)" || status=$?
    case "$status" in
        0) ;;
        "$TAG_UNREACHABLE") abort "could not ask GitHub for the newest release" "check the network" ;;
        "$TAG_ABSENT") abort "GitHub's answer named no release" "check the network, or name one with --version" ;;
        *) abort "the newest release is not a Tender release ($tag)" "name one with --version instead" ;;
    esac

    local version archive
    version="${tag#tender-v}"
    archive="romm-tender-$version.tar.gz"
    echo "downloading $tag"

    fetch_visibly "$DOWNLOAD_BASE/$tag/$archive" "$work/$archive" ||
        abort "release $tag carries no tarball" "try --version with a release that does, or --from a local build"
    fetch "$DOWNLOAD_BASE/$tag/$archive.sha256" "$work/$archive.sha256" ||
        abort "release $tag carries no checksum for its tarball" "this release cannot be verified, so it is refused"

    (cd "$work" && sha256sum -c "$archive.sha256" > /dev/null) ||
        abort "the downloaded tarball does not match its checksum" "nothing was changed; try again"
    TARBALL="$work/$archive"
}

# Prints the tag the release names and answers what it is: 0 one of ours,
# TAG_UNREACHABLE the release could not be asked for at all, TAG_ABSENT the
# answer carried no tag, anything else a tag that is not ours — and that one is
# on stdout, so the caller's message can quote it.
#
# Absent and not-ours are two answers rather than one because they send the user
# somewhere different: an answer with no tag in it is a server or a network that
# did not say what we asked, and a tag that does not match is a release that
# exists and is not Tender's.
resolve_tag() {
    if [ -n "$VERSION" ]; then
        echo "tender-v${VERSION#v}"
        return 0
    fi
    local body tag
    body="$(curl -fsSL "$RELEASE_API")" || return "$TAG_UNREACHABLE"
    # Two routes to "no tag", both of which answer it: the pipeline finds
    # nothing and fails under pipefail, or it finds a key whose value is empty.
    if ! tag="$(printf '%s' "$body" | grep -o '"tag_name"[[:space:]]*:[[:space:]]*"[^"]*"' | head -n 1 |
        cut -d'"' -f4)"; then
        return "$TAG_ABSENT"
    fi
    [ -n "$tag" ] || return "$TAG_ABSENT"
    echo "$tag"
    case "$tag" in
        tender-v[0-9]*) return 0 ;;
        *) return 1 ;;
    esac
}

fetch() {
    curl -fsSL "$1" -o "$2"
}

# The tarball is the one download worth watching — tens of megabytes over
# whatever the user's connection is — so it draws curl's progress bar where
# there is a terminal to draw it on. The checksum beside it is a hundred bytes
# and stays silent either way.
fetch_visibly() {
    if on_a_terminal; then
        curl -fL --progress-bar "$1" -o "$2"
    else
        curl -fsSL "$1" -o "$2"
    fi
}

# A local build has no release to be checked against, so a sidecar beside it is
# used where there is one and its absence is stated rather than treated as a
# pass.
verify_local() {
    local file="$1"
    if [ -f "$file.sha256" ]; then
        (cd "$(dirname "$file")" && sha256sum -c "$(basename "$file").sha256" > /dev/null) ||
            abort "$file does not match $file.sha256"
        echo "verified $file against its checksum"
    else
        echo "local file, not verified: $file"
    fi
}

# ---------------------------------------------------------- stage and swap

# Unpack beside the install and rename into place. That is TWO renames where an
# install already exists, not one: the window it leaves is a missing directory,
# which the unit's Restart= covers, and the tree already there is not deleted
# until the new one is in place.
install_tree() {
    local tarball="$1"

    rm -rf "$CODE.new"
    mkdir -p "$CODE.new"
    if ! tar -xzf "$tarball" --strip-components=1 -C "$CODE.new"; then
        rm -rf "$CODE.new"
        abort "the tarball could not be unpacked" "nothing was changed"
    fi

    local required
    for required in backend/main.py dist/index.js bin/tender-rom-launcher; do
        if [ ! -f "$CODE.new/$required" ]; then
            rm -rf "$CODE.new"
            abort "the tarball has no $required" "nothing was changed"
        fi
    done

    rm -rf "$CODE.old"
    [ ! -d "$CODE" ] || mv "$CODE" "$CODE.old"
    mv "$CODE.new" "$CODE"
    rm -rf "$CODE.old"
}

# ------------------------------------------------------------ covers once

# An earlier install wrote covers and artwork under the data root; this version
# reads them from the cache root. They are moved once, and never over a file the
# cache side already holds.
#
# The two reasons a file stays are kept apart, because one of them is fine and
# the other is a fault: a name the cache already holds is the rule working, and
# a move that FAILED is a problem the user has to see. Asking `-e` before the
# move rather than reading `mv -n`'s exit status is what separates them — `mv -n`
# answers 0 whether it moved the file or declined to, so a refusal and a write
# error are the same answer to it.
move_covers() {
    local name source target total
    for name in covers artwork; do
        source="$DATA/$name"
        [ -d "$source" ] || continue
        target="$CACHE/$name"
        total="$(count_movable "$source")"
        # Counted before the loop so the phase can say how much work it is, and
        # so a folder with nothing in it costs no line at all.
        if [ "$total" -eq 0 ]; then
            rmdir "$source" 2> /dev/null || true
            continue
        fi
        step "moving $total $(folder_noun "$name" "$total") to the cache" move_folder "$source" "$target" || true
        report_move "$name"
    done
}

# How many files the move will see, counted with the SAME glob the move walks.
# `find` answers for a dotfile too, and `"$source"/*` never yields one, so a
# folder holding nothing else announced a phase that then moved nothing and had
# nothing to report.
count_movable() {
    local file count=0
    for file in "$1"/*; do
        [ -f "$file" ] || continue
        count=$((count + 1))
    done
    printf '%s\n' "$count"
}

# What a folder's files are called in a sentence, for a count: `1 cover` and
# `41 covers`, `1 artwork file` and `111 artwork files`. `covers` is the one
# folder whose name is already the plural noun.
folder_noun() {
    case "$1:$2" in
        covers:1) printf 'cover\n' ;;
        covers:*) printf 'covers\n' ;;
        *:1) printf '%s file\n' "$1" ;;
        *) printf '%s files\n' "$1" ;;
    esac
}

# The verb that goes with a count, used twice in one sentence.
was_or_were() {
    if [ "$1" -eq 1 ]; then
        printf 'was\n'
    else
        printf 'were\n'
    fi
}

# Answers non-zero when anything could not be moved, so the phase says so rather
# than reporting a clean finish over a file still sitting where nothing reads it.
move_folder() {
    local source="$1" target="$2" file
    MOVED=0
    STAYED=0
    FAILED=0
    mkdir -p "$target"
    for file in "$source"/*; do
        [ -f "$file" ] || continue
        if [ -e "$target/$(basename "$file")" ]; then
            STAYED=$((STAYED + 1))
        elif mv "$file" "$target/"; then
            MOVED=$((MOVED + 1))
        else
            FAILED=$((FAILED + 1))
        fi
    done
    rmdir "$source" 2> /dev/null || true
    [ "$FAILED" -eq 0 ]
}

report_move() {
    local name="$1" moved_noun verb
    moved_noun="$(folder_noun "$name" "$MOVED")"
    if [ "$STAYED" -gt 0 ]; then
        verb="$(was_or_were "$STAYED")"
        echo "  $MOVED $moved_noun moved to the cache; $STAYED $verb already there and $verb left in place."
    elif [ "$MOVED" -gt 0 ]; then
        echo "  $MOVED $moved_noun moved to the cache."
    fi
    [ "$FAILED" -eq 0 ] || echo "could not move $FAILED $(folder_noun "$name" "$FAILED"); see above" >&2
}

# ------------------------------------------------------------------- unit

write_unit() {
    mkdir -p "$UNIT_DIR"
    cat > "$UNIT" <<TEXT
[Unit]
Description=Tender — RomM library in Steam

[Service]
ExecStart=$PYTHON $CODE/backend/main.py
Environment=TENDER_CODE_DIR=$CODE
Environment=TENDER_CONFIG_DIR=$CONFIG
Environment=TENDER_DATA_DIR=$DATA
Environment=TENDER_CACHE_DIR=$CACHE
Environment=TENDER_STATE_DIR=$STATE
Environment=TENDER_BIN_DIR=$BIN
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
TEXT
}

start_unit() {
    systemctl --user daemon-reload
    # --quiet: `enable` otherwise announces the symlink it made, which is the
    # one line of third-party noise in an otherwise plain run. Nothing here
    # swallows stderr — a systemctl that fails is exactly what the user needs.
    systemctl --user enable --now --quiet "$UNIT_NAME"
    # An update over a running unit: `enable --now` starts a stopped one and
    # leaves a running one on the tree that has just been replaced under it.
    systemctl --user restart "$UNIT_NAME"
}

# ----------------------------------------------------------------- marker

# Steam opens its CEF debugger only where this file exists, and reads it at
# start-up (docs/architecture/loading-the-panel.md). The backend creates it too;
# doing it here as well is what lets the restart Steam needs happen once, now,
# rather than after a first confusing run.
ensure_marker() {
    local marker="$STEAM_ROOT/$MARKER_FILE"
    if [ ! -e "$marker" ]; then
        : > "$marker"
        mkdir -p "$STATE"
        printf '%s\ncreated by install.sh %s\n' "$marker" "$(date -u +%Y-%m-%d)" > "$STATE/$MARKER_NOTE"
    fi
}

# What was probed is Steam's debugger port, and that is all it can say: whether
# Steam will accept the panel, not whether the backend has put one there yet.
# The answer decides the closing line rather than printing one of its own, so
# the run ends with one summary instead of two statements.
probe_debugger() {
    DEBUGGER_ANSWERED="no"
    if curl -fs --max-time 2 "$DEBUGGER_PROBE" > /dev/null 2>&1; then
        DEBUGGER_ANSWERED="yes"
    fi
}

# ------------------------------------------------------------------ modes

do_install() {
    preflight
    acknowledge

    WORK_DIR="$(mktemp -d)"
    obtain_tarball "$WORK_DIR"

    step "unpacking $(basename "$TARBALL")" install_tree "$TARBALL"
    move_covers
    step "writing the service" write_unit
    step "starting $UNIT_NAME" start_unit
    step "checking Steam's debugger" ensure_and_probe

    echo
    echo "Tender is installed."
    printf '  %-9s %s\n' "Status:" "systemctl --user status $UNIT_NAME"
    printf '  %-9s %s\n' "Log:" "$(tilde "$STATE/backend.log")"
    if [ "$DEBUGGER_ANSWERED" = "yes" ]; then
        echo "  Tender's entry appears in the Quick Access menu once the backend has loaded it."
    else
        echo "  Restart Steam once (or start it) to see Tender's entry in the Quick Access menu."
    fi
}

# One phase, two questions: make sure Steam will open a debugger at all, then
# ask whether it already has.
ensure_and_probe() {
    ensure_marker
    probe_debugger
}

do_disable() {
    systemctl --user disable --now "$UNIT_NAME" || abort "could not stop $UNIT_NAME"
    echo "Tender is stopped and will not start again on login."
    echo "Run install.sh again to enable it."
}

do_uninstall() {
    systemctl --user disable --now "$UNIT_NAME" 2> /dev/null || true
    rm -f "$UNIT"
    systemctl --user daemon-reload 2> /dev/null || true
    rm -rf "$CODE" "$CODE.new" "$CODE.old"

    remove_marker_if_ours
    rm -rf "$STATE"
    [ -z "${XDG_RUNTIME_DIR:-}" ] || rm -rf "$XDG_RUNTIME_DIR/romm-tender"

    echo
    echo "Tender is removed. Left in place on purpose:"
    printf '  %-34s %s\n' "$(tilde "$CONFIG")" "your settings"
    printf '  %-34s %s\n' "$(tilde "$DATA")" "your library database"
    printf '  %-34s %s\n' "$(tilde "$CACHE")" "cached covers and artwork"
    printf '  %-34s %s\n' "$(tilde "$BIN/tender-rom-launcher")" "every Steam shortcut starts through it"
    printf '  %-34s %s\n' "$(tilde "$HOME/romm-tender-recovery")" "recovery bundles, if you made any"
    printf '  %-34s %s\n' "RetroDECK's own folders" "your games, saves and BIOS files"
}

# The marker is removed only where a note says this side created it AND nothing
# else needs it. Why Decky Loader counts as something else, and what it does to
# this file, is docs/architecture/loading-the-panel.md.
#
# Exactly the path the note names, and no search: the note is the authority, so
# removing anything it does not name would be removing a file on a hunch. The
# name check is the one thing asked of that path, because a note that has been
# truncated, half-written or hand-edited must not turn `--uninstall` into an
# unlink of whatever it happens to spell.
remove_marker_if_ours() {
    [ -f "$STATE/$MARKER_NOTE" ] || return 0
    if decky_loader_installed; then
        echo "Steam's remote-debugging marker is left in place: Decky Loader is installed and reads it too."
        return 0
    fi
    local marker
    marker="$(head -n 1 "$STATE/$MARKER_NOTE")"
    case "$marker" in
        /*"/$MARKER_FILE") rm -f "$marker" ;;
        *) echo "Steam's remote-debugging marker is left in place: $STATE/$MARKER_NOTE does not name one." >&2 ;;
    esac
}

# Installed, not running: an uninstaller may not take away a file the program
# next started would need. Decky's unit is a SYSTEM unit, so no --user; reading
# the unit-file list needs no privileges.
#
# Two questions rather than one, because a machine can answer either way: the
# directory is there on a machine that installed Decky by hand, and the unit is
# known to systemd on one whose directory has moved.
decky_loader_installed() {
    [ ! -e "$HOME/homebrew/services/PluginLoader" ] || return 0
    local listed
    listed="$(systemctl list-unit-files plugin_loader.service --no-legend 2> /dev/null || true)"
    [ -n "$listed" ]
}

main "$@"
