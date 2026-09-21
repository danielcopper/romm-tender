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
# file rather than a name to go looking for. The backend writes the same two
# lines under the same filename (DEBUGGER_MARKER_NOTE in
# backend/host/inject/machine.py), and tests/scripts/test_install_sh.py holds the
# two spellings equal; the uninstaller reads whichever of the two wrote it.
MARKER_NOTE="debugger-marker"
MARKER_FILE=".cef-enable-remote-debugging"

# Steam's CEF debugger. Answering means the panel can be loaded without a
# restart; not answering is the ordinary case on a fresh install.
DEBUGGER_PROBE="http://127.0.0.1:8080/json/version"

# Where Steam is. Answered by the pre-flight, which every path that reads it
# runs first.
STEAM_ROOT=""

MODE="install"
VERSION=""
LOCAL_FILE=""
ASSUME_YES="no"

main() {
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

abort() {
    echo "install.sh: $1" >&2
    [ $# -lt 2 ] || echo "  $2" >&2
    exit 1
}

value_of() {
    [ $# -ge 2 ] || abort "$1 needs a value"
    echo "$2"
}

parse_arguments() {
    while [ $# -gt 0 ]; do
        case "$1" in
            --version)
                VERSION="$(value_of "$@")"
                shift 2
                ;;
            --from)
                LOCAL_FILE="$(value_of "$@")"
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

# Each of the five below aborts with exit 1 and names both the reason and the
# fix. They run cheapest first, and because refusing over a missing Steam before
# knowing there is a Python to run anything with would be the wrong answer
# first.
preflight() {
    check_python
    check_user_manager
    refuse_flatpak_steam
    STEAM_ROOT="$(check_native_steam)"
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
    abort "no native Steam installation found" "install Steam and run it once, then run this again"
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

    cat <<TEXT

Did you run Tender as a Decky plugin before?
Then read the migration notes first — your Steam shortcuts from that install are not recognised by this
version, and the notes say what to do about it:
  $MIGRATION_NOTES
TEXT
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
    printf 'Type "yes" to continue: '
    read -r answer <&3 || true
    exec 3<&-
    [ "$answer" = "yes" ] || abort "not confirmed" "run with --yes once you have read the migration notes"
}

# ------------------------------------------------------------------- fetch

# Answers with the path of a tarball, verified wherever a checksum exists to
# verify it against — always for a release, and for a local file only where one
# sits beside it. Everything downloaded goes into a directory of its own that
# the caller removes.
obtain_tarball() {
    local work="$1"

    if [ -n "$LOCAL_FILE" ]; then
        [ -f "$LOCAL_FILE" ] || abort "no such file: $LOCAL_FILE"
        verify_local "$LOCAL_FILE" >&2
        echo "$LOCAL_FILE"
        return 0
    fi

    local tag version archive
    tag="$(resolve_tag)"
    version="${tag#tender-v}"
    archive="romm-tender-$version.tar.gz"
    echo "downloading $tag" >&2

    fetch "$DOWNLOAD_BASE/$tag/$archive" "$work/$archive" ||
        abort "release $tag carries no tarball" "try --version with a release that does, or --from a local build"
    fetch "$DOWNLOAD_BASE/$tag/$archive.sha256" "$work/$archive.sha256" ||
        abort "release $tag carries no checksum for its tarball" "this release cannot be verified, so it is refused"

    (cd "$work" && sha256sum -c "$archive.sha256" > /dev/null) ||
        abort "the downloaded tarball does not match its checksum" "nothing was changed; try again"
    echo "$work/$archive"
}

resolve_tag() {
    if [ -n "$VERSION" ]; then
        echo "tender-v${VERSION#v}"
        return 0
    fi
    local body tag
    body="$(curl -fsSL "$RELEASE_API")" || abort "could not ask GitHub for the newest release" "check the network"
    # `|| true` so the empty answer is a value this function decides about
    # rather than a pipeline status something else might act on. Measured on
    # bash 5.3.3: errexit does NOT fire on this assignment as the script calls
    # it — the function runs inside a command substitution, where a failing
    # assignment does not end anything — so without the guard the refusal below
    # is reached by a rule that is not written down at either end.
    tag="$(printf '%s' "$body" | grep -o '"tag_name"[[:space:]]*:[[:space:]]*"[^"]*"' | head -n 1 |
        cut -d'"' -f4 || true)"
    case "$tag" in
        tender-v[0-9]*) echo "$tag" ;;
        *) abort "the newest release is not a Tender release ($tag)" "name one with --version instead" ;;
    esac
}

fetch() {
    curl -fsSL "$1" -o "$2"
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
    local name source target moved stayed failed file
    for name in covers artwork; do
        source="$DATA/$name"
        [ -d "$source" ] || continue
        target="$CACHE/$name"
        mkdir -p "$target"
        moved=0
        stayed=0
        failed=0
        for file in "$source"/*; do
            [ -f "$file" ] || continue
            if [ -e "$target/$(basename "$file")" ]; then
                stayed=$((stayed + 1))
            elif mv "$file" "$target/"; then
                moved=$((moved + 1))
            else
                failed=$((failed + 1))
            fi
        done
        [ "$moved" -eq 0 ] || echo "moved $moved $name file(s) to $target"
        [ "$stayed" -eq 0 ] || echo "left $stayed $name file(s) in $source: the cache already holds a file of that name"
        [ "$failed" -eq 0 ] || echo "could not move $failed $name file(s); see above" >&2
        rmdir "$source" 2> /dev/null || true
    done
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
    systemctl --user enable --now "$UNIT_NAME"
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
    # What was probed is Steam's debugger port, and that is all this can say:
    # whether Steam will accept the panel, not whether the backend has put one
    # there yet.
    if curl -fs --max-time 2 "$DEBUGGER_PROBE" > /dev/null 2>&1; then
        echo "Steam's debugger is answering; Tender's entry appears in the Quick Access menu once the backend has loaded it."
    else
        echo "Restart Steam once (or start it); Tender's entry appears in the Quick Access menu after that."
    fi
}

# ------------------------------------------------------------------ modes

do_install() {
    preflight
    acknowledge

    local work tarball
    work="$(mktemp -d)"
    # shellcheck disable=SC2064 # expand $work now: it is what this trap exists for
    trap "rm -rf '$work'" EXIT
    tarball="$(obtain_tarball "$work")"

    install_tree "$tarball"
    move_covers
    write_unit
    start_unit
    ensure_marker

    echo "Tender is installed at $CODE."
    echo "  systemctl --user status $UNIT_NAME    what it is doing"
    echo "  $STATE/backend.log                    its log"
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

    echo "Tender is removed. Left in place on purpose:"
    echo "  $CONFIG            your settings"
    echo "  $DATA              your library database"
    echo "  $CACHE             cached covers and artwork"
    echo "  $BIN/tender-rom-launcher   every Steam shortcut starts through it"
    echo "  $HOME/romm-tender-recovery  recovery bundles, if you made any"
    echo "  RetroDECK's own folders    your games, saves and BIOS files"
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
