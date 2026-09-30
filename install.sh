#!/usr/bin/env bash
#
# install.sh — install Tender, a RomM library in Steam, for this user.
#
#   curl -fsSL https://raw.githubusercontent.com/danielcopper/romm-tender/main/install.sh | bash
#
# No sudo, nothing outside this user's home, and no daemon but one systemd user
# unit. Run it again to update — an update whose new version does not answer is
# rolled back, and `--rollback` goes back by hand; `--uninstall` takes it back
# out.
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
#   TENDER_UPDATE_WAIT    how many seconds the installer waits for a version to
#                         answer after an update or a rollback
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

UPDATE_WAIT="${TENDER_UPDATE_WAIT:-60}"

RELEASE_API="${TENDER_RELEASE_API:-https://api.github.com/repos/danielcopper/romm-tender/releases/latest}"
DOWNLOAD_BASE="${TENDER_DOWNLOAD_BASE:-https://github.com/danielcopper/romm-tender/releases/download}"

MIGRATION_NOTES="https://danielcopper.github.io/romm-tender/user-guide/getting-started/#coming-from-the-decky-plugin"

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

# Where the backend notes the port it bound: its runtime directory, which is the
# state directory where the session names none (resolve_directories in
# backend/domain/app_directories.py, PORT_FILENAME in
# backend/host/single_instance.py).
PORT_NOTE="port"
if [ -n "${XDG_RUNTIME_DIR:-}" ]; then
    PORT_FILE="$XDG_RUNTIME_DIR/romm-tender/$PORT_NOTE"
else
    PORT_FILE="$STATE/$PORT_NOTE"
fi

# What an update backs up before it swaps the tree, and what a rollback puts
# back: the database and its two WAL sidecars — WAL mode is recorded in the
# database file itself (backend/adapters/sqlite_migrations.py) — the settings,
# and the unit. The names are the backend's (DB_FILENAME in
# backend/bootstrap/adapters.py, SETTINGS_FILENAME in
# backend/adapters/persistence.py), and tests/scripts/test_install_sh.py holds
# the spellings equal.
DATABASE="romm_sync.db"
DATABASE_FILES=("$DATABASE" "$DATABASE-wal" "$DATABASE-shm")
SETTINGS="settings.json"
BACKUP="$DATA/update-backup"

# When the backup was made, one line of ISO-8601 UTC inside it, which is the
# date a rollback by hand names.
BACKUP_STAMP="backed-up-at"

# Which version's data the backup holds: the version of the tree installed when
# it was made, one line. A rollback by hand goes ahead only where the kept tree
# says the same, because an update that ended between replacing the backup and
# swapping the tree leaves an older kept tree beside the data of a newer one.
BACKUP_VERSION="data-of-version"

# What a rollback by hand replaces, kept before it does: the database files and
# the settings as the version being left wrote them. A directory of its own
# rather than one inside $BACKUP, which every update replaces whole: this copy
# lasts until the next rollback by hand.
ROLLBACK_BACKUP="$DATA/rollback-backup"

# The note an update that did not go through leaves in the state directory: the
# version it tried, the version it left running, and when. A rolled-back update
# writes it with no `kind`; one the pre-install check refused writes the kind
# below (UpdateFailureKind in backend/domain/update_outcome.py, which
# tests/scripts/test_install_sh.py holds equal).
UPDATE_FAILURE="update-failure.json"
CHECK_REFUSED="check"

# The new version's pre-install check, relative to its tree, and what its exit
# status means (backend/check.py): 1 is a version that could not be built, and
# 2 a check that was not tried, which says nothing about the version. A tree
# without the file predates the check and is installed without one.
CHECK_ENTRY="backend/check.py"
CHECK_NOT_BUILT=1
CHECK_NOT_TRIED=2

# The signals a check dies of when the new version's own code crashed — its
# native library among that code — rather than something outside stopping it. A
# shell answers 128 plus the signal's number for a command a signal ended, and
# `timeout` answers the same for its command (coreutils' timeout(1)). SIGBUS can
# also come from an I/O error on a mapped file, such as one on storage that
# stopped answering; that is filed as the version's too, accepted as rare.
CHECK_CRASH_SIGNALS="SEGV ABRT BUS ILL FPE"

# How long the pre-install check may run before it is stopped, and how long it
# then has before it is killed, in seconds. Building the application takes a few
# seconds; a check still running at the limit is waiting on something — a
# database another process keeps locked, a storage device that stopped
# answering — rather than working. `timeout` answers 124 for a check it
# stopped, and 137 for one that was killed: by `timeout` itself once the grace
# period ran out, or by anything else, the kernel's out-of-memory killer among
# them (coreutils' timeout(1)).
CHECK_SECONDS=120
CHECK_KILL_AFTER=10

# The name every answer from the backend's server carries in its `Server` field,
# before the version: `<PACKAGE_NAME>/<VERSION>` (backend/domain/identity.py,
# composed in backend/bootstrap/adapters.py). tests/scripts/test_install_sh.py
# holds the spelling equal.
SERVER_NAME="romm-tender"

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

# The lock a running backend holds, under the data root (LOCK_FILENAME in
# backend/host/single_instance.py). tests/scripts/test_install_sh.py holds the
# two spellings equal: a rename on one side only would leave this run asking
# about a file no backend takes, and every backend let through.
BACKEND_LOCK="backend.lock"

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

# What `curl -f` exits with when the server answered 400 or above (curl(1), EXIT
# CODES) — which answer it was is in HTTP_STATUS. Every other failure is the
# transfer itself.
CURL_HTTP_ERROR=22

# The status the server answered the last download with, as curl's
# `-w '%{http_code}'` prints it (curl(1), --write-out): the final response's,
# after any redirect, and 000 where no response arrived at all.
HTTP_STATUS=""

# check_python's two refusals, which are two different things to tell a user.
PYTHON_MISSING=2
PYTHON_TOO_OLD=3

# Whether Steam's debugger answered the probe, which decides the closing line.
DEBUGGER_ANSWERED="no"

# Said on the Installing row where a local tarball had no checksum beside it.
UNVERIFIED=""

# Whether this run replaced something that was already here — a tree at $CODE,
# or a unit that was running. What it changes is the answer about Steam: a
# backend that starts while an EARLIER one's panel is still loaded into Steam
# cannot load over it. The panel holds the old backend's token, so it talks to
# nobody, and the new backend finds the injection marker already set; the new
# backend replaces it only by having Steam reload its JS context, and only once
# no app is running (docs/architecture/loading-the-panel.md).
REPLACED_AN_INSTALL="no"

# Whether this run is an update: a tree was already at $CODE. An update runs
# the pre-install check first, then stops the unit before it touches anything,
# keeps the tree it replaces and a backup of the data, and rolls back where the
# new version does not answer.
UPDATING="no"

# Whether the backend now running was seen to answer as the version at $CODE,
# which only an update or a rollback waits for. Only then does the run know
# which backend is up to replace a panel an earlier one left in Steam.
ANSWERED="no"

# Whether an update was rolled back, which ends the run non-zero once the rest
# of it has been said.
ROLLED_BACK="no"

# How far a run got between stopping the unit and starting it again: for an
# update, `stopped` before the new tree is in place and `swapped` after; for a
# rollback by hand, `rolling-back`. The EXIT trap reads it, so a run that ends
# in that window says the service is stopped.
UPDATE_STAGE=""

# What the EXIT trap has to clean up: a download directory, a spinner that is
# still drawing, and a row whose outcome was never written.
WORK_DIR=""

# What one folder's move did, set by move_folder and read by the sentence after
# it. Three counts rather than a return value: they are three different things
# to say.
MOVED=0
STAYED=0
FAILED=0
MOVED_TOTAL=0
MOVED_SUMMARY=""

MODE="install"
VERSION=""
LOCAL_FILE=""
ASSUME_YES="no"

main() {
    trap cleanup EXIT
    parse_arguments "$@"
    resolve_look

    case "$MODE" in
        disable) do_disable ;;
        uninstall) do_uninstall ;;
        rollback) do_rollback ;;
        install) do_install ;;
    esac
}

usage() {
    cat <<'TEXT'
Usage: install.sh [options]

  (no options)     install or update to the newest release
  --version X      install or update to release X
  --from FILE      install from a tarball already on disk
  --rollback       go back to the version the last update replaced
  --disable        stop Tender and leave it installed
  --uninstall      remove Tender
  --yes            skip the question a first install asks (required there when
                   there is no terminal)
  --help           this text
TEXT
}

# --------------------------------------------------------- how this looks

# The mark, as two pictures of the same drawing: Braille cells where the
# terminal can show them, and an ASCII weight drawing where it cannot. Each row
# is `class:text` runs joined by the separator below, so drawing one is a split
# and never a parse, and every row is emitted at its full width — which is what
# lets a text block sit beside it without measuring a string that has escape
# sequences in it.
# >>> generated by scripts/logo/terminal.py — do not edit
LOGO_RUN_SEPARATOR='|'
LOGO_ICON_WIDTH=28
LOGO_ASCII_WIDTH=28
LOGO_RING_RGB='146;183;227'
LOGO_BUTTON_RGB='221;152;128'
LOGO_DISC_RGB='146;183;227'
LOGO_RING_256=110
LOGO_BUTTON_256=174
LOGO_DISC_256=110
LOGO_ICON_TRUECOLOR=(
    $'\033[39;49m        \033[38;2;98;120;152;49m▄\033[38;2;137;171;212;49m▄\033[38;2;146;183;227;49m▄▄\033[38;2;91;111;141;48;2;146;183;227m▀\033[38;2;98;121;153;48;2;146;183;227m▀▀\033[38;2;91;111;141;48;2;146;183;227m▀\033[38;2;146;183;227;49m▄▄\033[38;2;137;171;213;49m▄\033[38;2;98;120;152;49m▄\033[39;49m        \033[0m'
    $'\033[39;49m     \033[38;2;136;170;212;49m▄\033[38;2;104;128;161;48;2;146;183;227m▀\033[38;2;145;182;226;48;2;144;181;225m▀\033[38;2;146;183;227;48;2;104;136;171m▀\033[38;2;146;183;227;48;2;55;83;107m▀\033[38;2;132;168;209;48;2;31;56;75m▀\033[38;2;105;138;173;48;2;31;56;75m▀\033[38;2;87;118;150;48;2;31;56;75m▀\033[38;2;79;109;138;48;2;31;56;75m▀▀\033[38;2;87;118;150;48;2;31;56;75m▀\033[38;2;105;138;173;48;2;31;56;75m▀\033[38;2;132;168;209;48;2;31;56;75m▀\033[38;2;146;183;227;48;2;55;83;107m▀\033[38;2;146;183;227;48;2;104;136;171m▀\033[38;2;145;182;226;48;2;144;181;225m▀\033[38;2;104;128;161;48;2;146;183;227m▀\033[38;2;136;170;212;49m▄\033[39;49m     \033[0m'
    $'\033[39;49m   \033[38;2;144;180;224;49m▄\033[38;2;144;180;224;48;2;146;183;227m▀\033[38;2;146;183;227;48;2;107;140;175m▀\033[38;2;124;158;197;48;2;34;60;80m▀\033[38;2;52;79;103;48;2;31;56;75m▀\033[38;2;31;56;75;48;2;31;56;75m▀\033[38;2;31;56;75;48;2;36;62;82m▀\033[38;2;31;56;75;48;2;79;109;138m▀\033[38;2;31;56;75;48;2;116;149;187m▀\033[38;2;31;56;75;48;2;139;176;218m▀\033[38;2;36;61;82;48;2;146;183;227m▀▀\033[38;2;31;56;75;48;2;139;176;218m▀\033[38;2;31;56;75;48;2;116;149;187m▀\033[38;2;31;56;75;48;2;79;109;138m▀\033[38;2;31;56;75;48;2;36;62;82m▀\033[38;2;31;56;75;48;2;31;56;75m▀\033[38;2;52;79;102;48;2;31;56;75m▀\033[38;2;124;158;197;48;2;34;60;80m▀\033[38;2;146;183;227;48;2;107;140;175m▀\033[38;2;144;180;224;48;2;146;183;227m▀\033[38;2;131;167;209;49m▄\033[39;49m   \033[0m'
    $'\033[39;49m \033[38;2;99;121;153;49m▄\033[38;2;136;169;211;48;2;146;183;227m▀\033[38;2;146;183;227;48;2;132;168;209m▀\033[38;2;111;144;180;48;2;38;63;84m▀\033[38;2;32;58;77;48;2;31;56;75m▀\033[38;2;31;56;75;48;2;31;56;75m▀\033[38;2;31;56;75;48;2;92;124;156m▀\033[38;2;72;101;129;48;2;146;183;227m▀\033[38;2;136;172;214;48;2;146;183;227m▀\033[38;2;146;183;227;48;2;146;183;227m▀▀\033[38;2;146;183;227;48;2;145;182;226m▀\033[38;2;146;183;227;48;2;123;155;191m▀\033[38;2;146;183;227;48;2;121;151;184m▀\033[38;2;146;183;227;48;2;141;178;221m▀\033[38;2;146;183;227;48;2;146;183;227m▀▀\033[38;2;136;172;214;48;2;146;183;227m▀\033[38;2;72;101;129;48;2;146;183;227m▀\033[38;2;31;56;75;48;2;92;124;156m▀\033[38;2;31;56;75;48;2;29;47;62m▀\033[38;2;32;55;72;48;2;26;33;39m▀\033[38;2;96;124;155;48;2;31;38;46m▀\033[38;2;119;156;197;48;2;108;141;178m▀\033[38;2;111;145;184;48;2;119;156;197m▀\033[38;2;83;106;137;49m▄\033[39;49m \033[0m'
    $'\033[39;49m \033[38;2;143;180;223;48;2;146;183;227m▀\033[38;2;146;183;227;48;2;136;172;214m▀\033[38;2;71;100;127;48;2;32;57;77m▀\033[38;2;31;56;75;48;2;31;56;75m▀\033[38;2;31;56;75;48;2;43;69;90m▀\033[38;2;79;109;139;48;2;141;178;221m▀\033[38;2;146;183;227;48;2;146;183;227m▀▀▀\033[38;2;146;183;227;48;2;109;142;178m▀\033[38;2;134;170;211;48;2;40;65;86m▀\033[38;2;122;132;140;48;2;182;161;136m▀\033[38;2;222;189;152;48;2;232;196;156m▀\033[38;2;230;194;155;48;2;232;196;156m▀\033[38;2;152;147;140;48;2;229;194;155m▀\033[38;2;141;177;220;48;2;115;148;184m▀\033[38;2;146;183;227;48;2;146;183;227m▀\033[38;2;146;183;227;48;2;140;177;220m▀\033[38;2;145;182;225;48;2;123;160;201m▀\033[38;2;129;166;208;48;2;119;155;196m▀\033[38;2;65;83;104;48;2;89;116;146m▀\033[38;2;26;31;37;48;2;26;32;38m▀\033[38;2;26;31;37;48;2;26;31;37m▀\033[38;2;46;58;71;48;2;26;31;37m▀\033[38;2;44;55;68;48;2;26;31;37m▀\033[38;2;102;133;168;48;2;113;148;187m▀\033[39;49m \033[0m'
    $'\033[38;2;114;140;176;48;2;134;167;208m▀\033[38;2;146;183;227;48;2;146;183;227m▀\033[38;2;142;179;222;48;2;146;183;227m▀\033[38;2;51;79;102;48;2;145;182;226m▀\033[38;2;33;58;77;48;2;141;177;220m▀\033[38;2;102;134;168;48;2;146;183;227m▀\033[38;2;146;183;227;48;2;146;183;227m▀\033[38;2;146;183;227;48;2;131;164;202m▀\033[38;2;141;177;220;48;2;140;139;135m▀\033[38;2;76;106;135;48;2;147;137;122m▀\033[38;2;31;56;75;48;2;69;83;90m▀\033[38;2;31;56;75;48;2;31;56;75m▀\033[38;2;125;122;113;48;2;31;56;75m▀\033[38;2;232;196;156;48;2;75;87;93m▀\033[38;2;232;196;156;48;2;107;116;123m▀\033[38;2;181;163;141;48;2;125;159;197m▀\033[38;2;133;169;210;48;2;116;150;189m▀\033[38;2;133;170;212;48;2;122;111;117m▀\033[38;2;114;150;189;48;2;179;126;109m▀\033[38;2;117;154;194;48;2;162;120;108m▀\033[38;2;113;147;186;48;2;107;127;155m▀\033[38;2;43;55;67;48;2;117;154;194m▀\033[38;2;26;31;37;48;2;76;98;123m▀\033[38;2;26;31;37;48;2;29;36;43m▀\033[38;2;26;31;37;48;2;26;31;37m▀\033[38;2;35;43;53;48;2;50;64;79m▀\033[38;2;119;156;197;48;2;119;156;197m▀\033[38;2;95;122;156;48;2;110;144;182m▀\033[0m'
    $'\033[38;2;144;181;224;48;2;144;181;224m▀\033[38;2;146;183;227;48;2;146;183;227m▀\033[38;2;144;181;224;48;2;81;112;142m▀\033[38;2;146;183;227;48;2;62;90;116m▀\033[38;2;146;183;227;48;2;130;166;206m▀\033[38;2;146;183;227;48;2;146;183;227m▀▀\033[38;2;155;150;143;48;2;162;153;139m▀\033[38;2;232;196;156;48;2;232;196;156m▀▀\033[38;2;220;188;151;48;2;229;194;155m▀\033[38;2;41;63;79;48;2;56;77;94m▀\033[38;2;31;56;75;48;2;110;143;180m▀\033[38;2;79;108;138;48;2;125;161;202m▀\033[38;2;132;169;211;48;2;64;82;103m▀\033[38;2;92;120;150;48;2;26;31;37m▀\033[38;2;49;51;57;48;2;35;37;41m▀\033[38;2;218;150;127;48;2;209;145;122m▀\033[38;2;221;152;128;48;2;221;152;128m▀▀\033[38;2;151;116;108;48;2;142;114;111m▀\033[38;2;119;156;197;48;2;119;156;197m▀▀\033[38;2;106;139;175;48;2;119;156;197m▀\033[38;2;51;64;79;48;2;119;156;197m▀\033[38;2;66;85;106;48;2;117;154;194m▀\033[38;2;119;156;197;48;2;119;156;197m▀\033[38;2;118;155;195;48;2;118;155;195m▀\033[0m'
    $'\033[38;2;134;167;208;48;2;114;140;176m▀\033[38;2;146;183;227;48;2;146;183;227m▀\033[38;2;62;90;115;48;2;43;69;91m▀\033[38;2;31;56;75;48;2;31;56;75m▀\033[38;2;35;61;81;48;2;31;56;75m▀\033[38;2;93;124;157;48;2;31;56;75m▀\033[38;2;144;181;225;48;2;53;80;104m▀\033[38;2;128;156;187;48;2;138;175;217m▀\033[38;2;172;158;139;48;2;144;181;225m▀\033[38;2;189;166;139;48;2;140;176;219m▀\033[38;2;137;144;150;48;2;132;169;212m▀\033[38;2;129;165;206;48;2;109;143;180m▀\033[38;2;109;138;172;48;2;170;124;110m▀\033[38;2;96;85;89;48;2;221;152;128m▀\033[38;2;69;58;57;48;2;221;152;128m▀\033[38;2;26;31;37;48;2;118;88;80m▀\033[38;2;26;31;37;48;2;26;31;37m▀\033[38;2;63;54;54;48;2;26;31;37m▀\033[38;2;138;101;89;48;2;62;80;99m▀\033[38;2;128;105;102;48;2;115;150;190m▀\033[38;2;107;137;171;48;2;119;156;197m▀\033[38;2;119;156;197;48;2;119;156;197m▀\033[38;2;119;156;197;48;2;83;107;135m▀\033[38;2;115;150;190;48;2;27;33;39m▀\033[38;2;118;155;196;48;2;42;53;65m▀\033[38;2;119;156;197;48;2;116;152;192m▀\033[38;2;119;156;197;48;2;119;156;197m▀\033[38;2;110;144;182;48;2;95;122;155m▀\033[0m'
    $'\033[39;49m \033[38;2;139;175;218;48;2;125;159;199m▀\033[38;2;31;56;75;48;2;53;81;105m▀\033[38;2;31;56;75;48;2;56;83;108m▀\033[38;2;31;56;75;48;2;31;56;75m▀\033[38;2;32;57;76;48;2;31;56;75m▀\033[38;2;109;143;179;48;2;79;109;139m▀\033[38;2;146;183;226;48;2;136;173;216m▀\033[38;2;142;179;223;48;2;120;157;199m▀\033[38;2;125;162;204;48;2;119;156;197m▀\033[38;2;119;156;197;48;2;119;156;197m▀\033[38;2;94;121;151;48;2;114;150;189m▀\033[38;2;218;150;127;48;2;140;112;108m▀\033[38;2;221;152;128;48;2;219;151;127m▀\033[38;2;221;152;128;48;2;211;146;123m▀\033[38;2;173;122;105;48;2;109;100;106m▀\033[38;2;33;40;49;48;2;109;143;180m▀\033[38;2;89;115;145;48;2;119;156;197m▀\033[38;2;119;156;197;48;2;119;156;197m▀▀▀\033[38;2;115;151;190;48;2;65;83;104m▀\033[38;2;35;43;53;48;2;26;31;37m▀\033[38;2;26;31;37;48;2;26;31;37m▀\033[38;2;27;32;39;48;2;58;74;91m▀\033[38;2;111;145;183;48;2;119;156;197m▀\033[38;2;119;156;197;48;2;117;153;194m▀\033[39;49m \033[0m'
    $'\033[39;49m \033[38;2;99;121;153;49m▀\033[38;2;146;183;227;48;2;136;169;211m▀\033[38;2;132;168;209;48;2;146;183;227m▀\033[38;2;38;63;84;48;2;105;138;173m▀\033[38;2;31;54;73;48;2;28;36;43m▀\033[38;2;28;40;50;48;2;26;31;37m▀\033[38;2;75;97;121;48;2;26;31;37m▀\033[38;2;119;156;197;48;2;58;75;93m▀\033[38;2;119;156;197;48;2;111;145;183m▀\033[38;2;119;156;197;48;2;119;156;197m▀▀\033[38;2;115;151;190;48;2;119;156;197m▀\033[38;2;100;123;152;48;2;119;156;197m▀\033[38;2;101;127;158;48;2;119;156;197m▀\033[38;2;118;155;196;48;2;119;156;197m▀\033[38;2;119;156;197;48;2;119;156;197m▀▀\033[38;2;119;156;197;48;2;111;145;183m▀\033[38;2;119;156;197;48;2;58;75;93m▀\033[38;2;75;97;121;48;2;26;31;37m▀\033[38;2;26;31;37;48;2;26;31;37m▀\033[38;2;26;31;37;48;2;27;33;39m▀\033[38;2;31;38;46;48;2;90;117;147m▀\033[38;2;108;141;178;48;2;119;156;197m▀\033[38;2;119;156;197;48;2;111;145;184m▀\033[38;2;83;106;136;49m▀\033[39;49m \033[0m'
    $'\033[39;49m   \033[38;2;130;167;209;49m▀\033[38;2;119;156;197;48;2;117;154;194m▀\033[38;2;87;113;142;48;2;119;156;197m▀\033[38;2;29;35;42;48;2;101;131;165m▀\033[38;2;26;31;37;48;2;43;53;66m▀\033[38;2;26;31;37;48;2;26;31;37m▀\033[38;2;30;37;44;48;2;26;31;37m▀\033[38;2;64;82;103;48;2;26;31;37m▀\033[38;2;94;122;154;48;2;26;31;37m▀\033[38;2;113;148;187;48;2;26;31;37m▀\033[38;2;119;156;197;48;2;30;36;43m▀▀\033[38;2;113;148;187;48;2;26;31;37m▀\033[38;2;94;123;154;48;2;26;31;37m▀\033[38;2;64;82;103;48;2;26;31;37m▀\033[38;2;30;37;44;48;2;26;31;37m▀\033[38;2;26;31;37;48;2;26;31;37m▀\033[38;2;26;31;37;48;2;42;53;65m▀\033[38;2;29;35;42;48;2;101;131;165m▀\033[38;2;87;113;142;48;2;119;156;197m▀\033[38;2;119;156;197;48;2;117;154;194m▀\033[38;2;117;154;194;49m▀\033[39;49m   \033[0m'
    $'\033[39;49m     \033[38;2;112;146;185;49m▀\033[38;2;119;156;197;48;2;87;111;143m▀\033[38;2;118;154;195;48;2;118;155;196m▀\033[38;2;85;110;138;48;2;119;156;197m▀\033[38;2;45;57;70;48;2;119;156;197m▀\033[38;2;26;31;37;48;2;108;141;178m▀\033[38;2;26;31;37;48;2;86;111;140m▀\033[38;2;26;31;37;48;2;71;92;115m▀\033[38;2;26;31;37;48;2;64;82;103m▀▀\033[38;2;26;31;37;48;2;71;92;115m▀\033[38;2;26;31;37;48;2;86;111;140m▀\033[38;2;26;31;37;48;2;108;141;178m▀\033[38;2;45;57;70;48;2;119;156;197m▀\033[38;2;84;110;138;48;2;119;156;197m▀\033[38;2;118;154;195;48;2;118;155;196m▀\033[38;2;119;156;197;48;2;87;111;143m▀\033[38;2;112;146;185;49m▀\033[39;49m     \033[0m'
    $'\033[39;49m        \033[38;2;83;105;135;49m▀\033[38;2;112;146;185;49m▀\033[38;2;119;156;197;49m▀▀\033[38;2;119;156;197;48;2;77;97;126m▀\033[38;2;119;156;197;48;2;83;105;136m▀▀\033[38;2;119;156;197;48;2;77;97;126m▀\033[38;2;119;156;197;49m▀▀\033[38;2;112;146;185;49m▀\033[38;2;83;105;135;49m▀\033[39;49m        \033[0m'
)
LOGO_ICON_256=(
    $'\033[39;49m        \033[38;5;66;49m▄\033[38;5;110;49m▄\033[38;5;110;49m▄▄\033[38;5;60;48;5;110m▀\033[38;5;66;48;5;110m▀▀\033[38;5;60;48;5;110m▀\033[38;5;110;49m▄▄\033[38;5;110;49m▄\033[38;5;66;49m▄\033[39;49m        \033[0m'
    $'\033[39;49m     \033[38;5;110;49m▄\033[38;5;67;48;5;110m▀\033[38;5;110;48;5;110m▀\033[38;5;110;48;5;67m▀\033[38;5;110;48;5;239m▀\033[38;5;110;48;5;237m▀\033[38;5;67;48;5;237m▀\033[38;5;66;48;5;237m▀\033[38;5;60;48;5;237m▀▀\033[38;5;66;48;5;237m▀\033[38;5;67;48;5;237m▀\033[38;5;110;48;5;237m▀\033[38;5;110;48;5;239m▀\033[38;5;110;48;5;67m▀\033[38;5;110;48;5;110m▀\033[38;5;67;48;5;110m▀\033[38;5;110;49m▄\033[39;49m     \033[0m'
    $'\033[39;49m   \033[38;5;110;49m▄\033[38;5;110;48;5;110m▀\033[38;5;110;48;5;67m▀\033[38;5;110;48;5;237m▀\033[38;5;239;48;5;237m▀\033[38;5;237;48;5;237m▀\033[38;5;237;48;5;237m▀\033[38;5;237;48;5;60m▀\033[38;5;237;48;5;103m▀\033[38;5;237;48;5;110m▀\033[38;5;237;48;5;110m▀▀\033[38;5;237;48;5;110m▀\033[38;5;237;48;5;103m▀\033[38;5;237;48;5;60m▀\033[38;5;237;48;5;237m▀\033[38;5;237;48;5;237m▀\033[38;5;239;48;5;237m▀\033[38;5;110;48;5;237m▀\033[38;5;110;48;5;67m▀\033[38;5;110;48;5;110m▀\033[38;5;110;49m▄\033[39;49m   \033[0m'
    $'\033[39;49m \033[38;5;66;49m▄\033[38;5;110;48;5;110m▀\033[38;5;110;48;5;110m▀\033[38;5;67;48;5;237m▀\033[38;5;237;48;5;237m▀\033[38;5;237;48;5;237m▀\033[38;5;237;48;5;67m▀\033[38;5;60;48;5;110m▀\033[38;5;110;48;5;110m▀\033[38;5;110;48;5;110m▀▀\033[38;5;110;48;5;110m▀\033[38;5;110;48;5;103m▀\033[38;5;110;48;5;103m▀\033[38;5;110;48;5;110m▀\033[38;5;110;48;5;110m▀▀\033[38;5;110;48;5;110m▀\033[38;5;60;48;5;110m▀\033[38;5;237;48;5;67m▀\033[38;5;237;48;5;236m▀\033[38;5;236;48;5;234m▀\033[38;5;66;48;5;235m▀\033[38;5;110;48;5;67m▀\033[38;5;67;48;5;110m▀\033[38;5;60;49m▄\033[39;49m \033[0m'
    $'\033[39;49m \033[38;5;110;48;5;110m▀\033[38;5;110;48;5;110m▀\033[38;5;60;48;5;237m▀\033[38;5;237;48;5;237m▀\033[38;5;237;48;5;238m▀\033[38;5;60;48;5;110m▀\033[38;5;110;48;5;110m▀▀▀\033[38;5;110;48;5;67m▀\033[38;5;110;48;5;238m▀\033[38;5;244;48;5;144m▀\033[38;5;180;48;5;187m▀\033[38;5;180;48;5;187m▀\033[38;5;246;48;5;180m▀\033[38;5;110;48;5;67m▀\033[38;5;110;48;5;110m▀\033[38;5;110;48;5;110m▀\033[38;5;110;48;5;110m▀\033[38;5;110;48;5;104m▀\033[38;5;240;48;5;66m▀\033[38;5;234;48;5;234m▀\033[38;5;234;48;5;234m▀\033[38;5;237;48;5;234m▀\033[38;5;237;48;5;234m▀\033[38;5;67;48;5;67m▀\033[39;49m \033[0m'
    $'\033[38;5;67;48;5;110m▀\033[38;5;110;48;5;110m▀\033[38;5;110;48;5;110m▀\033[38;5;239;48;5;110m▀\033[38;5;237;48;5;110m▀\033[38;5;67;48;5;110m▀\033[38;5;110;48;5;110m▀\033[38;5;110;48;5;110m▀\033[38;5;110;48;5;245m▀\033[38;5;60;48;5;102m▀\033[38;5;237;48;5;239m▀\033[38;5;237;48;5;237m▀\033[38;5;243;48;5;237m▀\033[38;5;187;48;5;240m▀\033[38;5;187;48;5;243m▀\033[38;5;144;48;5;110m▀\033[38;5;110;48;5;103m▀\033[38;5;110;48;5;243m▀\033[38;5;67;48;5;137m▀\033[38;5;103;48;5;137m▀\033[38;5;67;48;5;66m▀\033[38;5;237;48;5;103m▀\033[38;5;234;48;5;60m▀\033[38;5;234;48;5;235m▀\033[38;5;234;48;5;234m▀\033[38;5;236;48;5;238m▀\033[38;5;110;48;5;110m▀\033[38;5;67;48;5;67m▀\033[0m'
    $'\033[38;5;110;48;5;110m▀\033[38;5;110;48;5;110m▀\033[38;5;110;48;5;60m▀\033[38;5;110;48;5;240m▀\033[38;5;110;48;5;110m▀\033[38;5;110;48;5;110m▀▀\033[38;5;246;48;5;246m▀\033[38;5;187;48;5;187m▀▀\033[38;5;180;48;5;180m▀\033[38;5;237;48;5;239m▀\033[38;5;237;48;5;67m▀\033[38;5;60;48;5;110m▀\033[38;5;110;48;5;239m▀\033[38;5;66;48;5;234m▀\033[38;5;236;48;5;235m▀\033[38;5;174;48;5;174m▀\033[38;5;174;48;5;174m▀▀\033[38;5;101;48;5;243m▀\033[38;5;110;48;5;110m▀▀\033[38;5;67;48;5;110m▀\033[38;5;238;48;5;110m▀\033[38;5;240;48;5;103m▀\033[38;5;110;48;5;110m▀\033[38;5;103;48;5;103m▀\033[0m'
    $'\033[38;5;110;48;5;67m▀\033[38;5;110;48;5;110m▀\033[38;5;240;48;5;238m▀\033[38;5;237;48;5;237m▀\033[38;5;237;48;5;237m▀\033[38;5;67;48;5;237m▀\033[38;5;110;48;5;239m▀\033[38;5;109;48;5;110m▀\033[38;5;144;48;5;110m▀\033[38;5;144;48;5;110m▀\033[38;5;246;48;5;110m▀\033[38;5;110;48;5;67m▀\033[38;5;67;48;5;137m▀\033[38;5;240;48;5;174m▀\033[38;5;237;48;5;174m▀\033[38;5;234;48;5;95m▀\033[38;5;234;48;5;234m▀\033[38;5;237;48;5;234m▀\033[38;5;95;48;5;239m▀\033[38;5;95;48;5;67m▀\033[38;5;67;48;5;110m▀\033[38;5;110;48;5;110m▀\033[38;5;110;48;5;60m▀\033[38;5;67;48;5;234m▀\033[38;5;104;48;5;237m▀\033[38;5;110;48;5;103m▀\033[38;5;110;48;5;110m▀\033[38;5;67;48;5;66m▀\033[0m'
    $'\033[39;49m \033[38;5;110;48;5;110m▀\033[38;5;237;48;5;239m▀\033[38;5;237;48;5;239m▀\033[38;5;237;48;5;237m▀\033[38;5;237;48;5;237m▀\033[38;5;67;48;5;60m▀\033[38;5;110;48;5;110m▀\033[38;5;110;48;5;110m▀\033[38;5;110;48;5;110m▀\033[38;5;110;48;5;110m▀\033[38;5;66;48;5;67m▀\033[38;5;174;48;5;95m▀\033[38;5;174;48;5;174m▀\033[38;5;174;48;5;174m▀\033[38;5;137;48;5;242m▀\033[38;5;235;48;5;67m▀\033[38;5;60;48;5;110m▀\033[38;5;110;48;5;110m▀▀▀\033[38;5;67;48;5;240m▀\033[38;5;236;48;5;234m▀\033[38;5;234;48;5;234m▀\033[38;5;234;48;5;239m▀\033[38;5;67;48;5;110m▀\033[38;5;110;48;5;103m▀\033[39;49m \033[0m'
    $'\033[39;49m \033[38;5;66;49m▀\033[38;5;110;48;5;110m▀\033[38;5;110;48;5;110m▀\033[38;5;237;48;5;67m▀\033[38;5;236;48;5;235m▀\033[38;5;235;48;5;234m▀\033[38;5;60;48;5;234m▀\033[38;5;110;48;5;239m▀\033[38;5;110;48;5;67m▀\033[38;5;110;48;5;110m▀▀\033[38;5;67;48;5;110m▀\033[38;5;66;48;5;110m▀\033[38;5;67;48;5;110m▀\033[38;5;104;48;5;110m▀\033[38;5;110;48;5;110m▀▀\033[38;5;110;48;5;67m▀\033[38;5;110;48;5;239m▀\033[38;5;60;48;5;234m▀\033[38;5;234;48;5;234m▀\033[38;5;234;48;5;234m▀\033[38;5;235;48;5;66m▀\033[38;5;67;48;5;110m▀\033[38;5;110;48;5;67m▀\033[38;5;60;49m▀\033[39;49m \033[0m'
    $'\033[39;49m   \033[38;5;110;49m▀\033[38;5;110;48;5;103m▀\033[38;5;60;48;5;110m▀\033[38;5;235;48;5;67m▀\033[38;5;234;48;5;237m▀\033[38;5;234;48;5;234m▀\033[38;5;235;48;5;234m▀\033[38;5;239;48;5;234m▀\033[38;5;66;48;5;234m▀\033[38;5;67;48;5;234m▀\033[38;5;110;48;5;235m▀▀\033[38;5;67;48;5;234m▀\033[38;5;66;48;5;234m▀\033[38;5;239;48;5;234m▀\033[38;5;235;48;5;234m▀\033[38;5;234;48;5;234m▀\033[38;5;234;48;5;237m▀\033[38;5;235;48;5;67m▀\033[38;5;60;48;5;110m▀\033[38;5;110;48;5;103m▀\033[38;5;103;49m▀\033[39;49m   \033[0m'
    $'\033[39;49m     \033[38;5;67;49m▀\033[38;5;110;48;5;60m▀\033[38;5;103;48;5;104m▀\033[38;5;60;48;5;110m▀\033[38;5;237;48;5;110m▀\033[38;5;234;48;5;67m▀\033[38;5;234;48;5;60m▀\033[38;5;234;48;5;59m▀\033[38;5;234;48;5;239m▀▀\033[38;5;234;48;5;59m▀\033[38;5;234;48;5;60m▀\033[38;5;234;48;5;67m▀\033[38;5;237;48;5;110m▀\033[38;5;60;48;5;110m▀\033[38;5;103;48;5;104m▀\033[38;5;110;48;5;60m▀\033[38;5;67;49m▀\033[39;49m     \033[0m'
    $'\033[39;49m        \033[38;5;60;49m▀\033[38;5;67;49m▀\033[38;5;110;49m▀▀\033[38;5;110;48;5;60m▀\033[38;5;110;48;5;60m▀▀\033[38;5;110;48;5;60m▀\033[38;5;110;49m▀▀\033[38;5;67;49m▀\033[38;5;60;49m▀\033[39;49m        \033[0m'
)
LOGO_ASCII=(
    'r:        ++########++        '
    'r:     +################+     '
    'r:   +#####+        +#####+   '
    'r:  ####+      ...     +#### .'
    'r: ####     .::|b:OOO|r::     .#####'
    'r: +##    :::::|b:OO|r::. ... +#####'
    'r:      :|b:OOO|r:::::. ::|b:OOO|r::  .###'
    'r:###.  :|b:OOO|r::: .::::|b:OOO|r::      '
    'r:#####+ ... .:|b:OO|r::::::    ##+ '
    'r:#####.     :|b:OOO|r:::.     #### '
    'r:. ####+     ...      +####  '
    'r:   +#####+        +#####+   '
    'r:     +################+     '
    'r:        ++########++        '
)
# <<< generated

# What kind of output this run writes, answered ONCE. Every one of these is a
# question about the process — is stdout a terminal, does the locale say UTF-8 —
# and a question about the process cannot be asked inside a command
# substitution, where stdout is a pipe by construction. Asking them there gave a
# run on a real terminal the plain answers meant for a log file.
IS_A_TERMINAL="no"
USE_COLOUR="no"
USE_TRUECOLOR="no"
USE_UTF8="no"
USE_FANCY_MARKS="no"
ANIMATE="no"

on_a_terminal() {
    [ "$IS_A_TERMINAL" = "yes" ]
}

# NO_COLOR is the convention (https://no-color.org): present and NOT EMPTY
# means no, whatever its value. An empty NO_COLOR says nothing, which is why
# the test is -z and not -n.
may_colour() {
    [ "$USE_COLOUR" = "yes" ]
}

# Whether the Braille mark and the Braille spinner can be drawn. The locale is
# the only thing that says so, and a terminal that is not in a UTF-8 one draws
# every cell as a replacement character — which is worse than the ASCII drawing
# made for exactly this case.
utf8_terminal() {
    [ "$USE_UTF8" = "yes" ]
}

# Whether the terminal takes 24-bit colour. COLORTERM is the only thing that
# says so — there is no query for it that does not need a reply — and a terminal
# that does not set it gets the nearest of the 256 indices instead.
truecolor_terminal() {
    [ "$USE_TRUECOLOR" = "yes" ]
}

# A row's mark is text rather than a glyph whenever colour is off, because ✓ and
# ✗ are told apart BY their colour — without it they are two small marks that
# look alike at a glance.
fancy_marks() {
    [ "$USE_FANCY_MARKS" = "yes" ]
}

# Whether the run may draw over what it has already written. Moving the cursor
# is an escape sequence like any other, so a run that may write none writes each
# row as a new line instead — as it starts, as its detail changes, and as it
# finishes: NO_COLOR asks for a plain transcript, not for a coloured one with
# the colour left out.
may_animate() {
    [ "$ANIMATE" = "yes" ]
}

# How wide the terminal is. COLUMNS is not set for a non-interactive shell, so
# `tput` answers for a real run and the variable is what a test sets.
#
# The question goes to /dev/tty rather than to this process's own streams:
# `tput` takes the size from whichever of its three streams is a terminal.
# Inside a command substitution stdout is a pipe, `2> /dev/null` below
# disqualifies stderr, and under `curl | bash` stdin is the script — so none is
# a terminal and it answers terminfo's default of 80 instead of the width of the
# terminal the run is being watched on. Where there is no /dev/tty the open
# fails and the default stands, which is the answer that machine would have
# given anyway.
#
# The order of the two redirections is load-bearing: `2> /dev/null` first means
# a failed open loses bash's complaint about it, and it is the COMMAND's stderr
# that goes there rather than this shell's, which `acknowledge` says more about.
terminal_width() {
    local width="${COLUMNS:-}"
    [ -n "$width" ] || width="$(tput cols 2> /dev/null < /dev/tty || echo 80)"
    printf '%s\n' "$width"
}

LOGO_GAP="    "

# How many columns a line takes up, with the escape sequences left out. The text
# block is styled, so its strings are longer than they look — measured raw, a
# bold marker and its reset read as eight columns of text that is not there.
visible_width() {
    local rest="$1" seen=""
    while :; do
        seen="$seen${rest%%$'\033'*}"
        case "$rest" in
            *$'\033'*) ;;
            *) break ;;
        esac
        rest="${rest#*$'\033'}"
        rest="${rest#*m}"
    done
    printf '%s\n' "${#seen}"
}

# How wide a terminal has to be for the text to sit BESIDE the art rather than
# under it: the art, the gap, and the longest line the block actually came out
# at. Measured rather than written down, because one of those lines carries
# $CODE and a hand install into a deep tree makes it longer than any constant
# would have guessed.
wide_enough_for() {
    local longest=0 line width
    for line in "$@"; do
        width="$(visible_width "$line")"
        [ "$width" -le "$longest" ] || longest="$width"
    done
    printf '%s\n' "$(($(art_width) + ${#LOGO_GAP} + longest))"
}

# Every escape this script writes goes through these two, so a run that may not
# use colour writes none at all rather than writing them where nobody looks.
style() {
    if may_colour; then
        printf '\033[%sm' "$1"
    fi
}

reset_style() {
    if may_colour; then
        printf '\033[0m'
    fi
}

# The two tones the ASCII drawing is written in, and the disc's tone the success
# line is set in, resolved once. All three are generated from the palette
# build.py ships, so neither the drawing nor that line can drift away from the
# mark they sit beside.
LOGO_RING_STYLE=""
LOGO_BUTTON_STYLE=""
LOGO_DISC_STYLE=""

resolve_logo_colours() {
    if truecolor_terminal; then
        LOGO_RING_STYLE="38;2;$LOGO_RING_RGB"
        LOGO_BUTTON_STYLE="38;2;$LOGO_BUTTON_RGB"
        LOGO_DISC_STYLE="38;2;$LOGO_DISC_RGB"
    else
        LOGO_RING_STYLE="38;5;$LOGO_RING_256"
        LOGO_BUTTON_STYLE="38;5;$LOGO_BUTTON_256"
        LOGO_DISC_STYLE="38;5;$LOGO_DISC_256"
    fi
}

# One row of the ASCII drawing. The runs arrive grouped by tone, so the number
# of escapes written is the number of colour changes rather than the number of
# cells.
print_ascii_row() {
    local runs run class text
    IFS="$LOGO_RUN_SEPARATOR" read -r -a runs <<< "$1"
    for run in "${runs[@]}"; do
        class="${run%%:*}"
        text="${run#*:}"
        case "$class" in
            b) style "$LOGO_BUTTON_STYLE" ;;
            *) style "$LOGO_RING_STYLE" ;;
        esac
        printf '%s' "$text"
        reset_style
    done
}

# What the greeter says beside the icon: what this run will do, in the order a
# reader asks it. The paths are this run's own rather than literals, so a hand
# install into another tree describes that tree. A rollback by hand says the
# same, except that what it keeps is the copy it makes of the data it replaces.
#
# The name and the four keys are the bold things here, so the bold means
# something. Written here rather than by the caller because in the first line it
# is one WORD, not the line.
greeting_lines() {
    style 1
    printf 'TENDER'
    reset_style
    printf '%sRomM library in Steam\n' "$TITLE_SEPARATOR"
    printf '\n'
    greeting_key "Install to" "$(tilde "$CODE")"
    greeting_key "Runs as" "a systemd user service, starts with your session"
    # Steam reads the debugger marker only at its own start, so a first install
    # needs that restart once; an update's backend replaces the panel the
    # earlier one left, and the closing line asks for the restart where it
    # cannot.
    if [ -d "$CODE" ]; then
        greeting_key "Needs" "no sudo"
    else
        greeting_key "Needs" "one Steam restart, no sudo"
    fi
    if [ "$MODE" = "rollback" ]; then
        greeting_key "Keeps" "a copy of your data as it is now"
    else
        greeting_key "Keeps" "your settings, library and shortcuts"
    fi
}

greeting_key() {
    style 1
    printf '%-12s' "$1"
    reset_style
    printf ' %s\n' "$2"
}

# What the other two modes say instead. Neither installs anything, so neither
# lists what an install would put where.
mode_lines() {
    style 1
    printf 'TENDER'
    reset_style
    printf '\n'
    printf '%s\n' "$1"
}

# The rows of art the greeter draws, or nothing at all.
#
# **The icon is half-blocks, and its colours ARE the picture** — every cell is
# one block character whose two halves are two colours — so a run that may write
# no colour gets no icon rather than a slab of one character. The ASCII drawing
# is the other way round: it carries the mark in glyph WEIGHT, so it survives
# having its colour taken away, and it is what a terminal outside a UTF-8 locale
# gets in place of block characters it would draw as replacement marks.
art_rows() {
    # No terminal, no art: a drawing in a log file is something somebody has to
    # scroll past.
    on_a_terminal || return 0
    if utf8_terminal; then
        # Half-blocks carry the mark in COLOUR and in nothing else, so a run
        # that may write none is better off with no icon than with a slab of one
        # character in one tone.
        may_colour || return 0
        if truecolor_terminal; then
            printf '%s\n' "${LOGO_ICON_TRUECOLOR[@]}"
        else
            printf '%s\n' "${LOGO_ICON_256[@]}"
        fi
        return 0
    fi
    printf '%s\n' "${LOGO_ASCII[@]}"
}

# Whether a row of art is already an escape string or still has to be coloured.
# The icon is emitted ready to print; the ASCII drawing is emitted as runs.
art_is_ready_made() {
    may_colour && utf8_terminal
}

art_width() {
    if art_is_ready_made; then
        printf '%s\n' "$LOGO_ICON_WIDTH"
    else
        printf '%s\n' "$LOGO_ASCII_WIDTH"
    fi
}

print_art_row() {
    if art_is_ready_made; then
        printf '%s' "$1"
    else
        print_ascii_row "$1"
    fi
}

# The greeter: the art beside the text where there is room for both, above it
# where there is not, and not at all where there is no art to draw. The text is
# centred against the art rather than hung from its top — the block is six lines
# against thirteen, and top-aligned it sits in the icon's upper half and reads
# as having fallen off it.
greeter() {
    local -a text=() art=()
    local line
    while IFS= read -r line; do
        text+=("$line")
    done < <("$@")
    while IFS= read -r line; do
        art+=("$line")
    done < <(art_rows)

    echo
    if [ "${#art[@]}" -eq 0 ]; then
        printf '%s\n' "${text[@]}"
        echo
        return 0
    fi

    local width
    width="$(art_width)"
    if [ "$(terminal_width)" -lt "$(wide_enough_for "${text[@]}")" ]; then
        for line in "${art[@]}"; do
            print_art_row "$line"
            echo
        done
        echo
        printf '%s\n' "${text[@]}"
        echo
        return 0
    fi

    local offset=$(((${#art[@]} - ${#text[@]}) / 2))
    [ "$offset" -ge 0 ] || offset=0
    local index=0 at
    while [ "$index" -lt "${#art[@]}" ] || [ "$index" -lt $((offset + ${#text[@]})) ]; do
        if [ "$index" -lt "${#art[@]}" ]; then
            print_art_row "${art[index]}"
        else
            printf '%*s' "$width" ""
        fi
        at=$((index - offset))
        if [ "$at" -ge 0 ] && [ "$at" -lt "${#text[@]}" ]; then
            printf '%s%s' "$LOGO_GAP" "${text[at]}"
        fi
        echo
        index=$((index + 1))
    done
    echo
}

# --------------------------------------------------------------- the run

# The run is four rows that stand for the whole of it, drawn once and then
# rewritten in place: the plan and the progress report are the same four lines.
# What a row says while it runs is its sub-steps as they finish, which is the
# only honest thing a phase of no measurable length can report. Where nothing
# may be redrawn, the same progress is a line of its own each time a row starts
# or its detail changes, ahead of the line it ends on: a run nobody watches —
# one whose output goes to the journal — would otherwise hold only how each row
# ended, and a wait of a minute would be a gap with no line saying what it was.
#
# **A spinner is a process of its own.** It cannot see a variable this shell
# sets after it was forked, so the rows go through a file: while a row is
# running, everything here writes state and the next frame shows it. This shell
# draws in only two places — when a row ends, and when a sub-line changes the
# block's height — and it stops the spinner first both times, so there is never
# more than one drawer.
ROW_LABELS=("Checking" "Installing" "Service" "Steam")
ROW_STATE=(pending pending pending pending)
ROW_DETAIL=("" "" "" "")
ROW_SUB=("" "" "" "")
ROW_SUB_STATE=("" "" "" "")
ROW_FILE=""
ROW_ACTIVE=-1
SPINNER_PID=""

# The four rows, by name. Every caller names the row it is reporting on rather
# than counting to it.
CHECKING=0
INSTALLING=1
SERVICE=2
STEAM=3

# What the drawing is made of, resolved once from the two questions above —
# whether the terminal can draw the glyphs, and whether it may use colour.
DOT_SEPARATOR=" - "
TITLE_SEPARATOR="  -  "
ARROW="->"
ROW_INDENT="    "
SPINNER_FRAMES=("|" "/" "-" "\\")
ELLIPSIS="..."
# How many columns a row's mark takes: `[ok]` without the glyphs, one cell with.
ROW_MARK_WIDTH=4
# The terminal's width while the rows are drawn, 0 where they are not redrawn.
ROW_COLUMNS=0

resolve_look() {
    if [ -t 1 ]; then
        IS_A_TERMINAL="yes"
    fi
    if [ "$IS_A_TERMINAL" = "yes" ] && [ -z "${NO_COLOR:-}" ]; then
        USE_COLOUR="yes"
        ANIMATE="yes"
        case "${COLORTERM:-}" in
            *truecolor* | *24bit*) USE_TRUECOLOR="yes" ;;
            *) ;;
        esac
    fi
    case "${LC_ALL:-${LC_CTYPE:-${LANG:-}}}" in
        *UTF-8* | *UTF8* | *utf-8* | *utf8*) USE_UTF8="$IS_A_TERMINAL" ;;
        *) ;;
    esac
    if [ "$USE_COLOUR" = "yes" ] && [ "$USE_UTF8" = "yes" ]; then
        USE_FANCY_MARKS="yes"
        ROW_MARK_WIDTH=1
    fi
    resolve_logo_colours
    if [ "$USE_UTF8" = "yes" ]; then
        DOT_SEPARATOR=" · "
        TITLE_SEPARATOR="  ·  "
        ARROW="→"
        ELLIPSIS="…"
        SPINNER_FRAMES=("⠋" "⠙" "⠹" "⠸" "⠼" "⠴" "⠦" "⠧" "⠇" "⠏")
    fi
}

rows_begin() {
    ROW_FILE="$(mktemp)"
    set_block_height 0
    flush_rows
    if may_animate; then
        # Read once, here, because the spinner is forked after this and draws
        # with what it inherited.
        ROW_COLUMNS="$(terminal_width)"
        draw_block ""
    fi
}

# Keeps one line of a row inside the terminal, into FITTED. A line that wrapped
# would stand on two lines of the screen and on one in the block's height, and
# every later redraw would aim one line short of the block — the row standing
# there again, once per frame. The last column is left free: a terminal that
# wraps as soon as it is written to would otherwise wrap there too.
fit_row() {
    local line="$1" room="$2" keep
    FITTED="$line"
    [ "$ROW_COLUMNS" -gt 0 ] || return 0
    room=$((room - 1))
    [ "${#line}" -gt "$room" ] || return 0
    keep=$((room - ${#ELLIPSIS}))
    [ "$keep" -gt 0 ] || keep=0
    FITTED="${line:0:keep}$ELLIPSIS"
}

# Written whole and renamed into place, so the spinner never reads half a block.
flush_rows() {
    local index
    for index in "${!ROW_LABELS[@]}"; do
        printf '%s\t%s\t%s\t%s\n' \
            "${ROW_STATE[index]}" "${ROW_DETAIL[index]}" "${ROW_SUB[index]}" "${ROW_SUB_STATE[index]}"
    done > "$ROW_FILE.tmp"
    mv "$ROW_FILE.tmp" "$ROW_FILE"
}

row_start() {
    ROW_ACTIVE="$1"
    ROW_STATE[$1]="running"
    flush_rows
    spinner_start
    say_progress "$1"
}

# A sub-step finished: its name joins the ones before it on the row.
row_add() {
    if [ -z "${ROW_DETAIL[$1]}" ]; then
        ROW_DETAIL[$1]="$2"
    else
        ROW_DETAIL[$1]="${ROW_DETAIL[$1]}$DOT_SEPARATOR$2"
    fi
    flush_rows
    say_progress "$1"
}

# The whole detail, for a step that supersedes what it was doing rather than
# adding to it. Saying the same detail again is not progress, so it prints
# nothing new.
row_detail() {
    [ "${ROW_DETAIL[$1]}" != "$2" ] || return 0
    ROW_DETAIL[$1]="$2"
    flush_rows
    say_progress "$1"
}

# A running row as one plain line, where the rows are not redrawn. Trailing
# blanks are dropped: a row that has only just started has no detail yet, and
# its label is padded for one.
say_progress() {
    if may_animate; then
        return 0
    fi
    local line
    line="$(print_row "$1" progress "${ROW_DETAIL[$1]}" "" "" "")"
    printf '%s\n' "${line%"${line##*[![:space:]]}"}"
}

# One line under a row, for the thing that happened once and has to be said.
row_sub() {
    ROW_SUB[$1]="$2"
    ROW_SUB_STATE[$1]="${3:-dim}"
    flush_rows
    # A sub-line makes the block one line taller, and this shell is the only one
    # that may change its height: the spinner's frames have to leave it alone,
    # or stopping one becomes able to aim the next draw at the wrong line.
    if may_animate; then
        spinner_stop
        draw_block ""
        spinner_start
    fi
}

row_end() {
    local index="$1"
    ROW_STATE[index]="$2"
    ROW_ACTIVE=-1
    flush_rows
    if may_animate; then
        spinner_stop
        draw_block ""
    else
        print_row "$index" "${ROW_STATE[$index]}" "${ROW_DETAIL[$index]}" "${ROW_SUB[$index]}" "${ROW_SUB_STATE[$index]}" ""
    fi
}

# Redraw the whole block over itself, in ONE write, so the cursor move and the
# rows it was aimed at reach the terminal together rather than line by line.
#
# How many lines are on screen is recorded beside the rows rather than
# remembered, because the two things that draw — this shell and the spinner it
# forked — are different processes and neither can be told what the other left
# behind. Only this shell ever CHANGES that number (see row_sub); the spinner's
# frames leave the block exactly as tall as they found it.
draw_block() {
    local rendered
    rendered="$(render_block "$1")"
    printf '%s\n' "$rendered"
}

render_block() {
    local frame="$1" index=0 drawn=0 state detail sub sub_state on_screen
    on_screen="$(block_height)"
    if [ "$on_screen" -gt 0 ]; then
        printf '\033[%dA' "$on_screen"
    fi
    while IFS=$'\t' read -r state detail sub sub_state; do
        print_row "$index" "$state" "$detail" "$sub" "$sub_state" "$frame"
        drawn=$((drawn + 1))
        if [ -n "$sub" ]; then
            drawn=$((drawn + 1))
        fi
        index=$((index + 1))
    done < "$ROW_FILE"
    set_block_height "$drawn"
}

# How far above the cursor the block begins, and how far the next redraw has to
# move up to reach it. Zero says the block is not a known distance above the
# cursor at all, which is what a fresh run and an interrupted download both
# leave behind: the next draw then writes a new block where the cursor is
# instead of aiming at a line it cannot find.
block_height() {
    if [ -s "$ROW_FILE.height" ]; then
        cat "$ROW_FILE.height"
    else
        printf '0\n'
    fi
}

set_block_height() {
    printf '%s\n' "$1" > "$ROW_FILE.height"
}

print_row() {
    local index="$1" state="$2" detail="$3" sub="$4" sub_state="$5" frame="$6"
    clear_line
    if [ "$state" = "running" ] && [ -n "$frame" ]; then
        style 36
        printf '%s' "$frame"
        reset_style
    else
        row_mark "$state"
    fi
    printf ' %-12s ' "${ROW_LABELS[index]}"
    if [ "$state" = "pending" ]; then
        style 2
    fi
    fit_row "$detail" "$((ROW_COLUMNS - ROW_MARK_WIDTH - 14))"
    printf '%s' "$FITTED"
    reset_style
    printf '\n'
    if [ -n "$sub" ]; then
        clear_line
        if [ "$sub_state" = "fail" ]; then
            style 31
        else
            style 2
        fi
        fit_row "$sub" "$((ROW_COLUMNS - ${#ROW_INDENT}))"
        printf '%s%s' "$ROW_INDENT" "$FITTED"
        reset_style
        printf '\n'
    fi
}

clear_line() {
    if may_animate; then
        printf '\r\033[K'
    fi
}

# The four states a row can be in. `warn` is not an error and is not red: Steam
# not having answered yet is the ordinary case on a machine that has just been
# installed onto. `progress` is not a state but the mark on a line
# say_progress writes, which only a run with no fancy marks ever does.
row_mark() {
    if ! fancy_marks; then
        case "$1" in
            ok) printf '[ok]' ;;
            fail) printf '[!!]' ;;
            progress) printf '[..]' ;;
            *) printf '[--]' ;;
        esac
        return 0
    fi
    case "$1" in
        ok)
            style 32
            printf '✓'
            ;;
        fail)
            style 31
            printf '✗'
            ;;
        warn)
            style 33
            printf '✗'
            ;;
        *)
            style 2
            printf '·'
            ;;
    esac
    reset_style
}

spinner_start() {
    if ! may_animate; then
        return 0
    fi
    rm -f "$ROW_FILE.stop"
    spin &
    SPINNER_PID=$!
}

# The frame rate is the outer wait; the inner one is how soon it can be asked to
# stop. Split because a spinner asleep for a whole frame is a spinner the run
# has to wait for at the end of every row.
spin() {
    local index=0 slices
    while [ ! -e "$ROW_FILE.stop" ]; do
        draw_block "${SPINNER_FRAMES[index % ${#SPINNER_FRAMES[@]}]}"
        index=$((index + 1))
        slices=0
        while [ "$slices" -lt 8 ] && [ ! -e "$ROW_FILE.stop" ]; do
            sleep 0.01
            slices=$((slices + 1))
        done
    done
}

# Every path out of a row comes through here, including the abort one, so no
# spinner outlives the row it belongs to.
#
# **Asked to stop rather than killed.** A signal lands wherever the spinner
# happens to be, and where that is inside the command substitution a frame is
# built in, the substitution's own child outlives the shell that started it and
# writes its answer after the run has moved on. A flag it looks at between
# frames means a stopped spinner has always finished the frame it was drawing.
spinner_stop() {
    [ -n "$SPINNER_PID" ] || return 0
    : > "$ROW_FILE.stop"
    wait "$SPINNER_PID" 2> /dev/null || true
    rm -f "$ROW_FILE.stop"
    SPINNER_PID=""
}

# Close a row nobody closed. A row is still running here only when what it ran
# ended the script from inside, so the outcome is known.
fail_open_row() {
    spinner_stop
    [ "$ROW_ACTIVE" -ge 0 ] || return 0
    row_end "$ROW_ACTIVE" fail
}

# The one EXIT trap. `abort` closes the row itself so its reason lands ON the
# row rather than above it; this is the backstop for every other way out.
cleanup() {
    fail_open_row
    say_the_service_is_stopped
    [ -z "$WORK_DIR" ] || rm -rf "$WORK_DIR"
    [ -z "$ROW_FILE" ] || rm -f "$ROW_FILE" "$ROW_FILE.tmp" "$ROW_FILE.height" "$ROW_FILE.stop"
}

# Not a refusal — the run's own has already been said, or there was none — so
# it carries no `install.sh:` prefix. Where the trees are is read off the disk
# rather than off the stage, because a stage spans several renames. `--rollback`
# is named only once the new tree is in place: before that the kept tree is an
# earlier update's, and the backup may already hold the data of the version
# still installed.
say_the_service_is_stopped() {
    case "$UPDATE_STAGE" in
        stopped)
            if [ -d "$CODE" ]; then
                echo "$UNIT_NAME is stopped: the update ended before the new version was in place." >&2
                echo "  the installed version is still at $(tilde "$CODE"); start it again with systemctl --user start $UNIT_NAME" >&2
            else
                echo "$UNIT_NAME is stopped: the update ended with no version at $(tilde "$CODE")." >&2
                echo "  the one it was replacing is at $(tilde "$CODE.old"): move it back to $(tilde "$CODE"), then start it with systemctl --user start $UNIT_NAME" >&2
            fi
            ;;
        rolling-back)
            if [ -d "$CODE.old" ] && [ -d "$CODE" ]; then
                echo "$UNIT_NAME is stopped: the rollback ended before it moved either version." >&2
                echo "  start it again with systemctl --user start $UNIT_NAME" >&2
            elif [ -d "$CODE.old" ]; then
                echo "$UNIT_NAME is stopped: the rollback ended with no version at $(tilde "$CODE")." >&2
                echo "  the one it was leaving is at $(tilde "$CODE.new"): move it back to $(tilde "$CODE"), then start it with systemctl --user start $UNIT_NAME" >&2
            else
                echo "$UNIT_NAME is stopped: the rollback put the earlier version in place and ended before its data was back." >&2
                echo "  copy the files in $(tilde "$BACKUP") back before starting it with systemctl --user start $UNIT_NAME" >&2
            fi
            ;;
        swapped)
            echo "$UNIT_NAME is stopped: the update ended after putting the new version in place and before starting it." >&2
            echo "  start it with systemctl --user start $UNIT_NAME, or go back with $(tilde "$CODE")/install.sh --rollback" >&2
            ;;
        *) ;;
    esac
}

# Ends the run. **A function whose VALUE is taken with `$(...)` never calls this**
# — it answers instead, and its caller aborts. Why, and what the gate can and
# cannot see: scripts/check_shell_answer_functions.py.
#
# The reason becomes the failing row's detail, so the row says what went wrong
# rather than only that something did. It is repeated on stderr because that is
# the run's record: a piped install keeps its refusal where a reader of the log
# looks for it, and a second refusal in one run — the shape a `$(...)`-swallowed
# exit leaves behind — is countable there.
abort() {
    if [ "$ROW_ACTIVE" -ge 0 ]; then
        ROW_DETAIL[ROW_ACTIVE]="$1"
    fi
    fail_open_row
    echo "install.sh: $1" >&2
    [ $# -lt 2 ] || echo "  $2" >&2
    exit 1
}

# A path with the user's home written as `~`. For printing only — nothing is
# ever opened through the result.
tilde() {
    case "$1" in
        "$HOME"/*) printf '~%s\n' "${1#"$HOME"}" ;;
        *) printf '%s\n' "$1" ;;
    esac
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
            --rollback)
                MODE="rollback"
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
# the plugin that would otherwise share the database with it, and last the
# backend that would already be holding what the service's own needs.
preflight() {
    local found status=0
    found="$(check_python)" || status=$?
    case "$status" in
        0) ;;
        "$PYTHON_MISSING") abort "no Python at $PYTHON" "install python3, or point TENDER_PYTHON at one" ;;
        *) abort "$PYTHON is older than 3.13" "Tender needs Python 3.13 or newer" ;;
    esac
    row_add "$CHECKING" "$found"
    check_user_manager
    row_add "$CHECKING" "systemd"
    refuse_flatpak_steam
    STEAM_ROOT="$(check_native_steam)" ||
        abort "no native Steam installation found" "install Steam and run it once, then run this again"
    row_add "$CHECKING" "Steam"
    refuse_decky_plugin
    row_add "$CHECKING" "no Tender plugin in Decky"
    refuse_foreign_backend
}

# Prints the version it found, and ANSWERS rather than aborting: its value is
# taken with `$(...)`, so an exit here would end that subshell and let the run
# carry on with an empty answer. The two refusals are two statuses because they
# send the user somewhere different — no interpreter at all, or one too old.
#
# One invocation for both questions. A second one could answer for a different
# interpreter than the one just approved, and the unit runs exactly this path.
check_python() {
    [ -x "$PYTHON" ] || return "$PYTHON_MISSING"
    "$PYTHON" -c 'import sys; print("python %d.%d" % sys.version_info[:2]); sys.exit(0 if sys.version_info >= (3, 13) else 1)' ||
        return "$PYTHON_TOO_OLD"
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

# A backend outside the unit holds the exclusive lock the unit's own takes
# (BACKEND_LOCK above), so the unit cannot come up while one is running — and
# this run would still report it up. `systemctl restart` of a unit with no
# `Type=` returns as soon as its process has been forked (systemd.service(5),
# Type=simple), before the backend has asked for the lock, and service_state()
# then reads a port file that is either missing ("enabled and started") or the
# hand-started backend's own. Refused rather than reported.
#
# The LOCK is the question rather than a process name, because only a Tender
# backend takes it: a backend started by hand runs as `python backend/main.py`
# from wherever it was started, and any other program may run a file of that
# name. The one holder that is not refused is the unit's own MainPID, which is
# an update over a running service.
#
# Only the modes that start the service ask: `--uninstall` and `--disable` start
# nothing, and both stop the unit whatever else is running.
refuse_foreign_backend() {
    local lock="$DATA/$BACKEND_LOCK" own opener holder="" own_opens="no"
    # No file, no holder — and asked first because the read-only open below fails
    # on a missing file, which the `if` would take for a held lock. A descriptor
    # rather than a path because `flock` handed a path creates the file.
    [ -e "$lock" ] || return 0
    if flock -n 9 2> /dev/null 9< "$lock"; then
        return 0
    fi
    own="$(service_main_pid)"
    while IFS= read -r opener; do
        if [ "$opener" = "$own" ]; then
            own_opens="yes"
        else
            holder="$opener"
        fi
    done < <(lock_openers "$lock")
    if [ -z "$holder" ] && [ "$own_opens" = "yes" ]; then
        return 0
    fi
    if [ -z "$holder" ]; then
        abort "another process holds $(tilde "$lock"), so Tender's service cannot start" \
            "stop the Tender backend you started by hand, then run this again"
    fi
    local directory
    directory="$(readlink "/proc/$holder/cwd" 2> /dev/null || true)"
    abort "another Tender backend is running (pid $holder)" \
        "it holds $(tilde "$lock") and runs $(command_line "$holder") in $(tilde "$directory") — stop it with Ctrl-C where it was started, or kill $holder, then run this again"
}

# Every process that has *1* open through a descriptor, one pid to a line. The
# lock is held by one of them, which is all a caller can learn: the kernel says
# who has a file OPEN, and a second backend waiting out its retry window has it
# open too. Read off /proc rather than asked of `fuser` or `lsof`, which are not
# on every target — this needs no tool beyond bash.
#
# `-ef` stats both sides — the descriptor through its /proc link, which resolves
# to the open file — and compares device and inode, so a holder that opened the
# lock under another name is found as well.
# Only this user's processes can answer: another user's fd directory cannot be
# listed, so the glob yields nothing from it and no filter is needed. Best
# effort: that process, one that ends mid-scan, and a lock held with no
# descriptor open on it at all are simply not named.
lock_openers() {
    local fd pid last=""
    for fd in /proc/[0-9]*/fd/*; do
        [ "$fd" -ef "$1" ] 2> /dev/null || continue
        pid="${fd#/proc/}"
        pid="${pid%%/*}"
        [ "$pid" != "$last" ] || continue
        printf '%s\n' "$pid"
        last="$pid"
    done
}

# A process's command line, its arguments joined by spaces. The kernel keeps
# them NUL-separated, and a command substitution cannot carry a NUL.
command_line() {
    local -a argv=()
    mapfile -d '' argv 2> /dev/null < "/proc/$1/cmdline" || true
    printf '%s\n' "${argv[*]}"
}

# The backend the service is running, or 0 — which is what systemd answers for a
# unit that is running none.
service_main_pid() {
    systemctl --user show -p MainPID --value "$UNIT_NAME" 2> /dev/null || true
}

# --------------------------------------------------------- acknowledgement

# Read from /dev/tty rather than stdin, because under `curl | bash` stdin IS
# this script.
acknowledge() {
    [ "$ASSUME_YES" = "yes" ] && return 0
    [ "$(date -u +%Y-%m-%d)" \< "$ACK_UNTIL" ] || return 0
    # A tree already at the code root is this installer's own, so whoever runs
    # it over one is not coming from the Decky plugin.
    [ ! -d "$CODE" ] || return 0

    local sign="!"
    if utf8_terminal; then
        sign="⚠"
    fi
    style "1;33"
    printf '%s  Coming from the Decky plugin?' "$sign"
    reset_style
    printf '\n'
    printf '   The Steam shortcuts it created are not recognised by this version.\n'
    style 2
    printf '   Read first: %s' "$MIGRATION_NOTES"
    reset_style
    printf '\n'

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
    printf '   Continue? [y/N] '
    read -r answer <&3 || true
    exec 3<&-
    echo
    # The conventional shape: the capital N is the answer a bare Enter gives,
    # and it is the safe one. Anything that is not a yes refuses.
    case "$(printf '%s' "$answer" | tr '[:upper:]' '[:lower:]')" in
        y | yes) ;;
        # No `--yes` hint here: the answer was no, and a hint would read as a
        # way around the question rather than an answer to it. The hint belongs
        # to the refusal above, where there was no way to ask at all.
        *) abort "stopped — nothing was changed." ;;
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
    row_detail "$INSTALLING" "downloading $tag"

    local fetched=0
    fetch_visibly "$DOWNLOAD_BASE/$tag/$archive" "$work/$archive" || fetched=$?
    [ "$fetched" -eq 0 ] ||
        refuse_download "$fetched" "$archive" "release $tag carries no tarball" \
            "try --version with a release that does, or --from a local build"
    fetch "$DOWNLOAD_BASE/$tag/$archive.sha256" "$work/$archive.sha256" || fetched=$?
    [ "$fetched" -eq 0 ] ||
        refuse_download "$fetched" "$archive.sha256" "release $tag carries no checksum for its tarball" \
            "this release cannot be verified, so it is refused"

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

# Ends the run over a release asset that did not arrive, with *missing* and its
# hint only where the server said the file is not there. Any other error answer
# — a rate limit, an outage — says nothing about the release, so it is reported
# as the server's answer rather than as the release lacking the file.
refuse_download() {
    local status="$1" name="$2" missing="$3" missing_hint="$4"
    if [ "$status" -ne "$CURL_HTTP_ERROR" ]; then
        abort "the download was cut short" "check the network and run this again"
    fi
    if [ "$HTTP_STATUS" = "404" ]; then
        abort "$missing" "$missing_hint"
    fi
    abort "the server answered $HTTP_STATUS for $name" "try again later"
}

fetch() {
    HTTP_STATUS="$(curl -fsSL -w '%{http_code}' "$1" -o "$2")"
}

# The tarball is the one download worth watching — tens of megabytes over
# whatever the user's connection is — so it draws curl's progress bar where
# there is a terminal to draw it on. The checksum beside it is a hundred bytes
# and stays silent either way.
fetch_visibly() {
    if ! on_a_terminal; then
        fetch "$1" "$2"
        return
    fi
    # The bar draws on the line under the block, and so does the spinner's idea
    # of where the block ends — both write there and one of them moves the
    # cursor relative to it, so the spinner stops for the length of the
    # download. That line is the block's own while the bar is on it: curl ends a
    # bar that reached the end with a newline, which puts the cursor one line
    # lower than it was, and a redraw that did not count the bar's line would
    # aim the whole block one line short of where it is.
    local status=0 height=0
    spinner_stop
    if may_animate; then
        height="$(block_height)"
        set_block_height "$((height + 1))"
    fi
    # curl takes the bar's width from COLUMNS where that is exported, and
    # otherwise from its stdin's window size (get_terminal_columns in curl's
    # src/terminal.c; 8.22 then falls back to stdout and stderr, 8.21 does not).
    # Under `curl | bash` stdin is the script, so an 8.21 draws the bar at its
    # default of 79 columns whatever the terminal is, and a narrower one wraps it.
    # /dev/tty is handed to it where it can be opened; the subshell asks first,
    # silently, because a redirection that fails on the command itself would
    # skip the download and complain on stderr.
    if (: < /dev/tty) 2> /dev/null; then
        HTTP_STATUS="$(curl -fL --progress-bar -w '%{http_code}' "$1" -o "$2" < /dev/tty)" || status=$?
    else
        HTTP_STATUS="$(curl -fL --progress-bar -w '%{http_code}' "$1" -o "$2")" || status=$?
    fi
    if ! may_animate; then
        return "$status"
    fi
    if [ "$status" -eq 0 ]; then
        printf '\033[1A\r\033[K'
        set_block_height "$height"
        spinner_start
    else
        # curl wrote its reason where the bar was, over however many lines that
        # took — so the block is no longer a known distance above the cursor,
        # and the refusal on its way draws a fresh one under that reason rather
        # than over it. The spinner stays stopped: at an unknown height each
        # frame would be a new block rather than the same one again.
        set_block_height 0
    fi
    return "$status"
}

# A local build has no release to be checked against, so a sidecar beside it is
# used where there is one and its absence is stated rather than treated as a
# pass.
verify_local() {
    local file="$1"
    if [ -f "$file.sha256" ]; then
        (cd "$(dirname "$file")" && sha256sum -c "$(basename "$file").sha256" > /dev/null) ||
            abort "$file does not match $file.sha256"
        row_detail "$INSTALLING" "verified $(basename "$file")"
    else
        # Carried to the end of the row rather than said once: the row's detail
        # is rewritten by every step after this one, and "not verified" is the
        # kind of thing a reader has to still be able to see when the run is
        # over.
        UNVERIFIED=" (not verified)"
        row_detail "$INSTALLING" "local file, not verified"
    fi
}

# ---------------------------------------------------------- stage and swap

# Unpack beside the install and look at what came out, before anything that is
# running is touched. `version.txt` is required because an update is judged by
# it: the backend has to answer as the version the tree says it is.
stage_tree() {
    local tarball="$1"

    rm -rf "$CODE.new"
    mkdir -p "$CODE.new"
    if ! tar -xzf "$tarball" --strip-components=1 -C "$CODE.new"; then
        rm -rf "$CODE.new"
        abort "the tarball could not be unpacked" "nothing was changed"
    fi

    local required
    for required in backend/main.py dist/index.js bin/tender-rom-launcher version.txt; do
        if [ ! -f "$CODE.new/$required" ]; then
            rm -rf "$CODE.new"
            abort "the tarball has no $required" "nothing was changed"
        fi
    done
}

# Rename the staged tree into place, keeping the one already there as $CODE.old
# — exactly one, so the tree an earlier update kept goes now. That is TWO
# renames, not one: the window between them is a missing directory, which only
# a running unit could see, and an update has stopped it.
swap_tree() {
    rm -rf "$CODE.old"
    [ ! -d "$CODE" ] || mv "$CODE" "$CODE.old"
    mv "$CODE.new" "$CODE"
}

# ---------------------------------------------------- update and rollback

# The version a tree says it is, or non-zero where it says none. Whitespace and
# control characters are dropped, which record_update_failure relies on.
tree_version() {
    local file="$1/version.txt"
    [ -f "$file" ] || return 1
    tr -d '[:space:][:cntrl:]' < "$file"
}

# The version the backend behind the port note says it is, or nothing where
# nothing answered.
#
# It knocks without the token. The backend refuses that, and every answer it
# gives — a refusal included — carries `Server: <name>/<version>` (_refuse in
# backend/host/server.py), which is the whole question; the price is one
# WARNING line in its log per knock. curl's default Host is the one the
# backend's access check admits (allowed_hosts in backend/host/access.py).
answering_version() {
    local port="" response
    [ -f "$PORT_FILE" ] || return 0
    port="$(head -n 1 "$PORT_FILE" 2> /dev/null || true)"
    case "$port" in
        '' | *[!0-9]*) return 0 ;;
    esac
    response="$(curl -s --max-time 2 -o /dev/null -D - "http://127.0.0.1:$port/")" || return 0
    printf '%s\n' "$response" | tr -d '\r' | sed -n "s|^[Ss]erver: $SERVER_NAME/\\([^[:space:]]*\\)\$|\\1|p" | head -n 1
}

# Knocks until the backend answers as *1*, or until UPDATE_WAIT seconds have
# passed. A port note that is missing or stale and a knock nobody answers are
# one answer — not yet: a backend writes its note only once it has migrated the
# database and bound its port (run_backend in backend/host/runtime.py).
wait_for_version() {
    local want="$1" deadline=$((SECONDS + UPDATE_WAIT)) got
    [ -n "$want" ] || return 1
    while :; do
        got="$(answering_version)" || got=""
        [ "$got" != "$want" ] || return 0
        [ "$SECONDS" -lt "$deadline" ] || return 1
        sleep 1
    done
}

# Answers non-zero where the unit is still up once asked to stop.
stop_unit() {
    systemctl --user stop "$UNIT_NAME" || true
    ! unit_is_active
}

# Plain copies, whole only because the unit has been stopped first. Exactly what
# exists is copied, because a rollback reproduces exactly this set.
back_up() {
    local staged="$BACKUP.new"
    stage_directory "$staged" || return 1
    copy_data_into "$staged" || return 1
    [ ! -e "$UNIT" ] || cp -p "$UNIT" "$staged/$UNIT_NAME.service" || return 1
    date -u +%Y-%m-%dT%H:%M:%SZ > "$staged/$BACKUP_STAMP" || return 1
    local version=""
    version="$(tree_version "$CODE")" || version=""
    printf '%s\n' "$version" > "$staged/$BACKUP_VERSION" || return 1
    put_in_place "$staged" "$BACKUP"
}

# The same copy for a rollback by hand, of the data it is about to replace.
back_up_before_rollback() {
    local staged="$ROLLBACK_BACKUP.new"
    stage_directory "$staged" || return 1
    copy_data_into "$staged" || return 1
    put_in_place "$staged" "$ROLLBACK_BACKUP"
}

# An empty directory, or non-zero: a stale one left by an interrupted run would
# otherwise carry files this copy did not find into it.
stage_directory() {
    rm -rf "$1" || return 1
    mkdir -p "$1"
}

copy_data_into() {
    local name
    for name in "${DATABASE_FILES[@]}"; do
        [ ! -e "$DATA/$name" ] || cp -p "$DATA/$name" "$1/$name" || return 1
    done
    [ ! -e "$CONFIG/$SETTINGS" ] || cp -p "$CONFIG/$SETTINGS" "$1/$SETTINGS" || return 1
}

# A `.prev` with nothing at the name is the copy itself, from a run that ended
# between put_in_place's two renames, so it is moved back under the name.
recover_aside() {
    local target="$1" aside="$1.prev"
    if [ -e "$aside" ] && [ ! -e "$target" ]; then
        mv -T "$aside" "$target" || return 1
    fi
}

# The earlier copy is renamed aside rather than removed until the staged one
# holds its name: a removal that failed partway would leave a gutted copy under
# that name, which a rollback then restores from. The aside copy is removed
# last, and one that will not go stays as `.prev` rather than failing a copy
# that is already in place. A `.prev` still there once recover_aside has run is
# an earlier run's removal that failed. `-T`, because `mv` onto a directory that
# is still there moves INTO it.
put_in_place() {
    local staged="$1" target="$2" aside="$2.prev"
    recover_aside "$target" || return 1
    rm -rf "$aside" || return 1
    [ ! -e "$target" ] || mv -T "$target" "$aside" || return 1
    if ! mv -T "$staged" "$target"; then
        [ ! -e "$aside" ] || mv -T "$aside" "$target" || true
        return 1
    fi
    rm -rf "$aside" || true
}

# When the backup was made, as its stamp says.
backup_date() {
    head -n 1 "$BACKUP/$BACKUP_STAMP" 2> /dev/null
}

# The version whose data the backup holds, or non-zero where it records none.
backup_version() {
    local file="$BACKUP/$BACKUP_VERSION"
    [ -f "$file" ] || return 1
    tr -d '[:space:][:cntrl:]' < "$file"
}

# Every file the backup holds is put back, and every one of the database's and
# the settings' files it does NOT hold is taken away: a WAL left beside a
# database it does not belong to corrupts it ("Overwriting a database file with
# another without also deleting any hot journal associated with the original
# database", https://www.sqlite.org/howtocorrupt.html, section 1.4). The unit is
# put back where the backup has one and otherwise left as this run wrote it — it
# names paths, not a version, so it starts either tree.
restore_backup() {
    local name status=0
    for name in "${DATABASE_FILES[@]}"; do
        restore_file "$BACKUP/$name" "$DATA/$name" || status=1
    done
    restore_file "$BACKUP/$SETTINGS" "$CONFIG/$SETTINGS" || status=1
    if [ -e "$BACKUP/$UNIT_NAME.service" ]; then
        cp -p "$BACKUP/$UNIT_NAME.service" "$UNIT" || status=1
    fi
    return "$status"
}

restore_file() {
    if [ -e "$1" ]; then
        mkdir -p "$(dirname "$2")" && cp -p "$1" "$2"
    else
        rm -f "$2"
    fi
}

# The failed tree goes and the one it replaced comes back, with the data it had.
# The caller has stopped the unit, and starting it again is the caller's too.
revert_to_previous() {
    rm -rf "$CODE.new"
    [ ! -d "$CODE" ] || mv "$CODE" "$CODE.new"
    mv "$CODE.old" "$CODE"
    rm -rf "$CODE.new"
    if ! restore_backup; then
        abort "could not put your data back from $(tilde "$BACKUP")" \
            "the earlier version is at $(tilde "$CODE") and the backup is untouched; copy its files back before starting $UNIT_NAME"
    fi
}

# Starts the unit on the tree revert_to_previous put back, with the unit file it
# put back read in again. Waiting for it to answer is the caller's.
start_the_reverted_unit() {
    systemctl --user daemon-reload || true
    systemctl --user start "$UNIT_NAME" || true
}

# Written through a temporary file and renamed, so a reader never sees half of
# it. The versions come off tree_version, so the only characters JSON needs
# escaped in them are the quote and the backslash. A third argument is the
# record's `kind`, which a rollback leaves out. Non-zero where the record
# could not be written. The steps are chained by hand because `set -e` does not
# reach into a function whose status its caller tests. Their own errors are
# dropped, since the caller says once that the record was not written; `2>`
# stands before `>` so the error of a redirect that fails is dropped as well.
record_update_failure() {
    local record="$STATE/$UPDATE_FAILURE" kind=""
    [ $# -lt 3 ] || kind=", \"kind\": \"$3\""
    mkdir -p "$STATE" 2> /dev/null &&
        printf '{"attempted_version": "%s", "restored_version": "%s", "rolled_back_at": "%s"%s}\n' \
            "$(json_text "$1")" "$(json_text "$2")" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$kind" 2> /dev/null > "$record.tmp" &&
        mv "$record.tmp" "$record" 2> /dev/null &&
        return 0
    rm -f "$record.tmp"
    return 1
}

json_text() {
    local escaped="${1//\\/\\\\}"
    printf '%s\n' "${escaped//\"/\\\"}"
}

# Whether the tree at $CODE replaces a panel an earlier backend left in Steam
# (backend/host/inject/recovery.py). A release from before that has no such
# file, and after going back to one only a Steam restart brings the panel back.
replaces_a_stranded_panel() {
    [ -f "$CODE/backend/host/inject/recovery.py" ]
}

# Whether a panel an earlier backend left in Steam goes without a restart: it
# is replaced by the backend now running where that one was seen to answer and
# knows how. The Steam row and the closing line both say what follows from it.
panel_comes_back_by_itself() {
    [ "$DEBUGGER_ANSWERED" = "yes" ] && [ "$REPLACED_AN_INSTALL" = "yes" ] &&
        [ "$ANSWERED" = "yes" ] && replaces_a_stranded_panel
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
    MOVED_TOTAL=0
    for name in covers artwork; do
        source="$DATA/$name"
        [ -d "$source" ] || continue
        target="$CACHE/$name"
        total="$(count_movable "$source")"
        # Counted before the loop so a folder with nothing in it costs nothing
        # at all: the sub-line is an event, and no files moved is not one.
        if [ "$total" -eq 0 ]; then
            rmdir "$source" 2> /dev/null || true
            continue
        fi
        move_folder "$source" "$target" || true
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

# Answers non-zero when anything could not be moved, so the caller can say so
# rather than reporting a clean finish over a file still sitting where nothing
# reads it.
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

# The move gets a line only where something HAPPENED. A file the cache already
# holds is the rule working rather than news, and after the first run it is what
# every file is — so saying it would put a line under every future update that
# reports nothing being done.
#
# A failure is the other case and always speaks: those are the user's covers,
# still sitting where nothing reads them.
report_move() {
    local name="$1"
    if [ "$MOVED" -gt 0 ]; then
        MOVED_TOTAL=$((MOVED_TOTAL + MOVED))
        if [ -z "$MOVED_SUMMARY" ]; then
            MOVED_SUMMARY="$MOVED $(folder_noun "$name" "$MOVED")"
        else
            MOVED_SUMMARY="$MOVED_SUMMARY and $MOVED $(folder_noun "$name" "$MOVED")"
        fi
        row_sub "$INSTALLING" "$MOVED_SUMMARY moved to the cache"
    fi
    if [ "$FAILED" -gt 0 ]; then
        row_sub "$INSTALLING" "could not move $FAILED $(folder_noun "$name" "$FAILED"); see above" fail
        echo "could not move $FAILED $(folder_noun "$name" "$FAILED"); see above" >&2
    fi
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
    # `enable --now` starts a stopped unit and leaves a running one on the tree
    # it started from. An update has stopped it first; a unit that is up with
    # no tree at $CODE behind it has not been.
    [ "$UPDATING" = "yes" ] || systemctl --user restart "$UNIT_NAME"
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
    greeter greeting_lines
    acknowledge
    rows_begin

    row_start "$CHECKING"
    preflight
    row_end "$CHECKING" ok

    row_start "$INSTALLING"
    WORK_DIR="$(mktemp -d)"
    obtain_tarball "$WORK_DIR"
    if [ -d "$CODE" ]; then
        UPDATING="yes"
        REPLACED_AN_INSTALL="yes"
    fi
    row_detail "$INSTALLING" "unpacking $(basename "$TARBALL")"
    stage_tree "$TARBALL"
    local previous="" new=""
    if [ "$UPDATING" = "yes" ]; then
        previous="$(tree_version "$CODE")" || previous=""
    fi
    new="$(tree_version "$CODE.new")" || new=""
    if [ -f "$CODE.new/$CHECK_ENTRY" ]; then
        row_detail "$INSTALLING" "trying ${new:-the new version}"
        local checked=0
        check_the_new_version || checked=$?
        [ "$checked" -eq 0 ] || refuse_the_new_version "$checked" "$new" "$previous"
    else
        row_sub "$INSTALLING" "this version has no pre-install check"
        echo "this version has no pre-install check; it is installed without one" >&2
    fi
    if [ "$UPDATING" = "yes" ]; then
        set_the_install_aside
    fi
    swap_tree
    [ "$UPDATE_STAGE" != "stopped" ] || UPDATE_STAGE="swapped"
    move_covers
    row_detail "$INSTALLING" "$(basename "$TARBALL")$UNVERIFIED $ARROW $(tilde "$CODE")"
    row_end "$INSTALLING" ok

    row_start "$SERVICE"
    ! unit_is_active || REPLACED_AN_INSTALL="yes"
    row_detail "$SERVICE" "writing $UNIT_NAME.service"
    write_unit
    row_detail "$SERVICE" "starting $UNIT_NAME"
    if [ "$UPDATING" = "no" ]; then
        start_unit
    else
        UPDATE_STAGE=""
        start_unit || true
        row_detail "$SERVICE" "waiting for $new to answer"
        if wait_for_version "$new"; then
            ANSWERED="yes"
            rm -f "$STATE/$UPDATE_FAILURE"
        else
            roll_back_the_update "$new" "$previous"
        fi
    fi
    if [ "$ROLLED_BACK" = "no" ]; then
        row_detail "$SERVICE" "$(service_state)"
        row_end "$SERVICE" ok
    fi

    report_steam
    closing_block

    if [ "$ROLLED_BACK" = "yes" ]; then
        echo "install.sh: update to $new failed; back on $previous" >&2
        echo "  $new did not answer within ${UPDATE_WAIT}s; what it logged is in $(tilde "$STATE/backend.log"), and a start that failed early only in journalctl --user -u $UNIT_NAME" >&2
        exit 1
    fi
}

# The pre-install check: builds the staged version's backend without starting it
# (backend/check.py) — main.py imported with everything it imports, the native
# library loaded, and the database and settings migrated, on copies of the live
# ones taken while the installed version may still be writing them. Every root
# it could write under is named here, the runtime directory among them, because
# a root left out falls back to one the running version uses, and a panel
# install hands this run the live ones in its environment. The code root is the
# staged tree; the others lie under WORK_DIR, which the EXIT trap removes. `-B`
# because the staged tree is the one put in place, and bytecode this run wrote
# into it would be files the tarball did not bring.
check_the_new_version() {
    local roots="$WORK_DIR/check"
    TENDER_CODE_DIR="$CODE.new" \
        TENDER_CONFIG_DIR="$roots/config" \
        TENDER_DATA_DIR="$roots/data" \
        TENDER_CACHE_DIR="$roots/cache" \
        TENDER_STATE_DIR="$roots/state" \
        TENDER_BIN_DIR="$roots/bin" \
        XDG_RUNTIME_DIR="$roots/run" \
        timeout --kill-after="$CHECK_KILL_AFTER" "$CHECK_SECONDS" \
        "$PYTHON" -B "$CODE.new/$CHECK_ENTRY" --data-from "$DATA" --config-from "$CONFIG" \
        > "$WORK_DIR/check.log" 2>&1
}

# Nothing has been stopped or replaced yet, so the staged tree is all there is
# to take away, whatever the check answered (*status*). Only a version the check
# could not build, or one that crashed it, is the version's: an update records
# that refusal for the panel, naming the version still installed, and a first
# install has no panel to tell. A check that did not finish, was not tried, or
# ended with a status this script does not know says nothing about the version,
# and is recorded nowhere. What the check said is printed above the abort's two
# lines — the journal's, for an install started from the panel.
refuse_the_new_version() {
    local status="$1" new="$2" previous="$3" reason
    rm -rf "$CODE.new" || echo "could not remove the new version from $(tilde "$CODE.new")" >&2
    if [ "$status" -eq 124 ]; then
        reason="the check did not finish"
        echo "the pre-install check was stopped after ${CHECK_SECONDS}s" >&2
    elif [ "$status" -eq 137 ]; then
        reason="the check did not finish"
        echo "the pre-install check was killed" >&2
    elif [ "$status" -eq "$CHECK_NOT_TRIED" ]; then
        reason="could not try the new version: your data could not be copied"
    elif ! the_version_ended_the_check "$status"; then
        reason="the check did not finish"
        echo "the pre-install check ended with status $status" >&2
    else
        reason="the new version does not start"
        [ "$status" -eq "$CHECK_NOT_BUILT" ] || echo "the pre-install check crashed (SIG$(kill -l "$status"))" >&2
        if [ "$UPDATING" = "yes" ] && ! record_update_failure "$new" "$previous" "$CHECK_REFUSED"; then
            row_sub "$INSTALLING" "could not record the refused update; Tender will not show it" fail
            echo "install.sh: could not record the refused update in $(tilde "$STATE/$UPDATE_FAILURE"); Tender will not show it" >&2
        fi
    fi
    row_detail "$INSTALLING" "$reason"
    fail_open_row
    say_what_the_check_said
    abort "$reason" "nothing was changed"
}

# Whether the check's exit *status* is the new version's own doing: a build that
# failed, or a crash.
the_version_ended_the_check() {
    local status="$1" signal
    [ "$status" -ne "$CHECK_NOT_BUILT" ] || return 0
    for signal in $CHECK_CRASH_SIGNALS; do
        [ "$status" -ne $((128 + $(kill -l "$signal"))) ] || return 0
    done
    return 1
}

# The check's last error line and the last line of its last traceback first, then
# the end of what it printed: a tail alone can begin below the reason where the
# log carries two tracebacks. Reading the log never ends the run, which still has
# its reason to give.
say_what_the_check_said() {
    local log="$WORK_DIR/check.log" reason="" raised=""
    [ -s "$log" ] || return 0
    reason="$(grep -F '[ERROR]: check:' "$log" 2> /dev/null | tail -n 1)" || reason=""
    reason="${reason#*\]: check: }"
    raised="$(grep -E '^[A-Za-z_][A-Za-z0-9_.]*(: |$)' "$log" 2> /dev/null | tail -n 1)" || raised=""
    if [ -n "$reason" ]; then
        echo "the pre-install check said: $reason" >&2
        [ -z "$raised" ] || echo "  $raised" >&2
    elif [ -n "$raised" ]; then
        echo "the pre-install check said: $raised" >&2
    fi
    echo "the last lines it printed:" >&2
    tail -n 20 "$log" 2> /dev/null | sed 's/^/  /' >&2 || true
}

# Stops the unit and backs the data up, before the tree is swapped. Either one
# failing leaves the install as it was, the staged tree gone and a unit that
# was running running again.
set_the_install_aside() {
    local was_running="no"
    ! unit_is_active || was_running="yes"
    row_detail "$INSTALLING" "stopping $UNIT_NAME"
    if ! stop_unit; then
        rm -rf "$CODE.new"
        abort "$UNIT_NAME would not stop" "nothing was changed; stop it with systemctl --user stop $UNIT_NAME and run this again"
    fi
    UPDATE_STAGE="stopped"
    row_detail "$INSTALLING" "backing up your data"
    if ! back_up; then
        rm -rf "$CODE.new" "$BACKUP.new"
        start_again_if "$was_running"
        abort "could not back up your data to $(tilde "$BACKUP")" "nothing was changed"
    fi
}

# Puts the unit back the way it was found, before a refusal that changed
# nothing: running again where it had been running.
start_again_if() {
    UPDATE_STAGE=""
    [ "$1" = "no" ] || systemctl --user start "$UNIT_NAME" || true
}

# Never retried: an update that did not start once is not one to start again
# without the user deciding to.
roll_back_the_update() {
    local new="$1" previous="$2"
    row_detail "$SERVICE" "$new did not answer, going back to $previous"
    if ! stop_unit; then
        abort "$UNIT_NAME would not stop, so the earlier version was not put back" \
            "stop it with systemctl --user stop $UNIT_NAME, then run $(tilde "$CODE")/install.sh --rollback"
    fi
    revert_to_previous
    # Before the start rather than after it: the restored version logs the
    # rollback only as it starts, from the record it finds then. A record that
    # cannot be written costs the notice and never the service.
    if ! record_update_failure "$new" "$previous"; then
        row_sub "$SERVICE" "could not record the rolled-back update; Tender will not show it" fail
        echo "install.sh: could not record the rolled-back update in $(tilde "$STATE/$UPDATE_FAILURE"); Tender will not show it" >&2
    fi
    start_the_reverted_unit
    if ! wait_for_version "$previous"; then
        abort "update to $new failed, and $previous has not answered since the rollback either" \
            "what both logged is in $(tilde "$STATE/backend.log"), and a start that failed early only in journalctl --user -u $UNIT_NAME"
    fi
    ANSWERED="yes"
    ROLLED_BACK="yes"
    row_detail "$SERVICE" "update to $new failed; back on $previous"
    row_end "$SERVICE" fail
}

# Puts back the tree and the data the last update replaced, by hand. Nothing is
# asked or changed before both are known to be there and to belong together,
# except that a backup an interrupted update left as `.prev` is moved back under
# its name so it can be found. The data it replaces is copied aside first, and
# without a prompt, because the copy is what makes the question unnecessary; a
# copy that cannot be made refuses the rollback.
do_rollback() {
    [ -d "$CODE.old" ] ||
        abort "there is no earlier version to go back to" "an update keeps the one it replaced at $(tilde "$CODE.old"), and there is none"
    recover_aside "$BACKUP" ||
        abort "could not move the backup at $(tilde "$BACKUP.prev") back to $(tilde "$BACKUP")" "nothing was changed"
    [ -d "$BACKUP" ] ||
        abort "there is no backup to go back to" "an update backs your data up to $(tilde "$BACKUP") first, and there is none"
    local previous="" belongs="" made
    previous="$(tree_version "$CODE.old")" || previous=""
    belongs="$(backup_version)" || belongs=""
    if [ -z "$previous" ] || [ "$previous" != "$belongs" ]; then
        abort "the kept version and the backup do not belong together" \
            "$(tilde "$CODE.old") is ${previous:-an unrecorded version} and the backup holds the data of ${belongs:-an unrecorded version}. Your installed version and your data are as they were; start the service if it is not running: systemctl --user start $UNIT_NAME"
    fi
    made="$(backup_date)" || made=""
    # Under the block rather than in it: beside the icon, a line this long
    # needs a terminal about 130 columns wide, and on a narrower one the whole
    # block goes under the icon.
    greeter greeting_lines
    printf '%s\n\n' "Putting back the version the last update replaced, and your data as it was on ${made:-an unrecorded date}."
    rows_begin

    row_start "$CHECKING"
    preflight
    row_end "$CHECKING" ok

    local was_running="no"
    REPLACED_AN_INSTALL="yes"
    row_start "$INSTALLING"
    ! unit_is_active || was_running="yes"
    row_detail "$INSTALLING" "stopping $UNIT_NAME"
    stop_unit ||
        abort "$UNIT_NAME would not stop" "nothing was changed; stop it with systemctl --user stop $UNIT_NAME and run this again"
    UPDATE_STAGE="rolling-back"
    row_detail "$INSTALLING" "keeping a copy of your data"
    if ! back_up_before_rollback; then
        rm -rf "$ROLLBACK_BACKUP.new"
        start_again_if "$was_running"
        abort "could not copy your data to $(tilde "$ROLLBACK_BACKUP")" "nothing was changed"
    fi
    revert_to_previous
    start_the_reverted_unit
    UPDATE_STAGE=""
    row_detail "$INSTALLING" "$previous $ARROW $(tilde "$CODE")"
    row_sub "$INSTALLING" "your data from before the rollback is in $(tilde "$ROLLBACK_BACKUP")"
    row_end "$INSTALLING" ok

    row_start "$SERVICE"
    row_detail "$SERVICE" "waiting for $previous to answer"
    wait_for_version "$previous" ||
        abort "$previous has not answered since the rollback" "what it logged is in $(tilde "$STATE/backend.log"), and a start that failed early only in journalctl --user -u $UNIT_NAME"
    ANSWERED="yes"
    row_detail "$SERVICE" "$(service_state)"
    row_end "$SERVICE" ok

    report_steam
    closing_block
}

report_steam() {
    row_start "$STEAM"
    ensure_marker
    probe_debugger
    if panel_comes_back_by_itself; then
        row_detail "$STEAM" "running"
        row_sub "$STEAM" "the backend now running replaces the earlier panel once no game is running"
        row_end "$STEAM" ok
    elif [ "$DEBUGGER_ANSWERED" = "yes" ] && [ "$REPLACED_AN_INSTALL" = "yes" ]; then
        row_detail "$STEAM" "running, an earlier Tender's panel is still loaded"
        row_end "$STEAM" warn
    elif [ "$DEBUGGER_ANSWERED" = "yes" ]; then
        row_detail "$STEAM" "debugger answering"
        row_end "$STEAM" ok
    elif steam_is_running; then
        row_detail "$STEAM" "running, debugger not answering"
        row_end "$STEAM" warn
    else
        row_detail "$STEAM" "not running"
        row_end "$STEAM" warn
    fi
}

# Whether Steam is up. `pgrep -x` matches the executable's own name exactly, so
# a window title or a launcher script holding the word does not answer for it.
steam_is_running() {
    pgrep -x steam > /dev/null 2>&1
}

# Whether the unit is up. The Service row asks it BEFORE this run starts the
# unit, because afterwards every answer is yes.
unit_is_active() {
    systemctl --user is-active --quiet "$UNIT_NAME" 2> /dev/null
}

# What to say the service is doing. The port file is the backend's own note of
# the port it bound, and it is written once the backend is up — which is after
# this run has asked systemd to start it, so it is usually not there yet. Its
# absence is not a fault and is not reported as one.
service_state() {
    local port=""
    if [ -f "$PORT_FILE" ]; then
        port="$(head -n 1 "$PORT_FILE" 2> /dev/null || true)"
    fi
    case "$port" in
        [0-9]*) printf '%s.service running on 127.0.0.1:%s\n' "$UNIT_NAME" "$port" ;;
        *) printf '%s.service enabled and started\n' "$UNIT_NAME" ;;
    esac
}

# The run's last word: how long it took, the one thing to do next, and where to
# look afterwards. The bold is on the action, because that is the only line here
# the reader has to act on.
closing_block() {
    # Telling a user to restart a Steam that is not running sends them looking
    # for a window that is not there, so what separates the last two answers is
    # the process, not the probe: the probe cannot tell a Steam that is not
    # running from one running without the marker Steam only reads at start-up.
    #
    # Where a panel an earlier backend left does not come back by itself, only
    # a Steam restart takes it out.
    local next
    if [ "$DEBUGGER_ANSWERED" = "yes" ] && [ "$REPLACED_AN_INSTALL" != "yes" ]; then
        next="open the Quick Access menu — Tender's entry appears once the backend has loaded it"
    elif panel_comes_back_by_itself; then
        next="Tender's panel comes back by itself once no game is running — if it hasn't after a few minutes, restart Steam"
    elif [ "$DEBUGGER_ANSWERED" = "yes" ] || steam_is_running; then
        next="restart Steam, then open the Quick Access menu"
    else
        next="start Steam, then open the Quick Access menu"
    fi
    echo
    # The one line that says how the run ended, in the disc's own blue — the
    # two under it are where to look afterwards and stay dim. The bold is on the
    # action, because that is the only part of it a reader has to act on.
    style "$LOGO_DISC_STYLE"
    if [ "$ROLLED_BACK" = "yes" ]; then
        printf 'Rolled back in %ss.  ' "$SECONDS"
    else
        printf 'Done in %ss.  ' "$SECONDS"
    fi
    style 1
    printf 'Next: %s.' "$next"
    reset_style
    printf '\n'
    style 2
    printf '  %-7s %s\n' "Status" "systemctl --user status $UNIT_NAME"
    printf '  %-7s %s' "Log" "$(tilde "$STATE/backend.log")"
    reset_style
    printf '\n'
}

do_disable() {
    greeter mode_lines "Stopping the service and leaving everything installed."
    systemctl --user disable --now "$UNIT_NAME" || abort "could not stop $UNIT_NAME"
    echo "Tender is stopped and will not start again on login."
    echo "Run install.sh again to enable it."
}

do_uninstall() {
    greeter mode_lines "Removing the service and the program, and keeping your data."
    systemctl --user disable --now "$UNIT_NAME" 2> /dev/null || true
    rm -f "$UNIT"
    systemctl --user daemon-reload 2> /dev/null || true
    rm -rf "$CODE" "$CODE.new" "$CODE.old"

    remove_marker_if_ours
    rm -rf "$STATE"
    [ -z "${XDG_RUNTIME_DIR:-}" ] || rm -rf "$XDG_RUNTIME_DIR/romm-tender"

    echo "Tender is removed. Left in place on purpose:"
    style 2
    printf '  %-34s %s\n' "$(tilde "$CONFIG")" "your settings"
    printf '  %-34s %s\n' "$(tilde "$DATA")" "your library database"
    printf '  %-34s %s\n' "$(tilde "$CACHE")" "cached covers and artwork"
    printf '  %-34s %s\n' "$(tilde "$BIN/tender-rom-launcher")" "every Steam shortcut starts through it"
    printf '  %-34s %s\n' "$(tilde "$HOME/romm-tender-recovery")" "recovery bundles, if you made any"
    printf '  %-34s %s' "RetroDECK's own folders" "your games, saves and BIOS files"
    reset_style
    printf '\n'
    # The entry is code a backend loaded into Steam, and removing the backend
    # does not take it back out again — only Steam restarting does.
    if steam_is_running; then
        echo
        echo "Steam still shows Tender's entry until it restarts."
    fi
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
        # The blank line belongs to the message rather than to what follows it:
        # the greeter already leaves one, so a second printed unconditionally
        # opens a gap on every run that has nothing to say here.
        echo "Steam's remote-debugging marker is left in place: Decky Loader is installed and reads it too."
        echo
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
