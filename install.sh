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
# cannot replace it. The panel holds the old backend's token, so it talks to
# nobody, and the new backend finds the injection marker already set and loads
# nothing over it. Only Steam restarting clears that, so only a FIRST install
# into a running Steam can promise the entry appears on its own.
REPLACED_AN_INSTALL="no"

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
# is an escape sequence like any other, so a run that may write none prints each
# row once as it finishes instead: NO_COLOR asks for a plain transcript, not for
# a coloured one with the colour left out.
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
# install into another tree describes that tree.
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
    greeting_key "Needs" "one Steam restart, no sudo"
    greeting_key "Keeps" "your settings, library and shortcuts"
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
# only honest thing a phase of no measurable length can report.
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
}

# A sub-step finished: its name joins the ones before it on the row.
row_add() {
    if [ -z "${ROW_DETAIL[$1]}" ]; then
        ROW_DETAIL[$1]="$2"
    else
        ROW_DETAIL[$1]="${ROW_DETAIL[$1]}$DOT_SEPARATOR$2"
    fi
    flush_rows
}

# The whole detail, for a step that supersedes what it was doing rather than
# adding to it.
row_detail() {
    ROW_DETAIL[$1]="$2"
    flush_rows
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
# installed onto.
row_mark() {
    if ! fancy_marks; then
        case "$1" in
            ok) printf '[ok]' ;;
            fail) printf '[!!]' ;;
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
    [ -z "$WORK_DIR" ] || rm -rf "$WORK_DIR"
    [ -z "$ROW_FILE" ] || rm -f "$ROW_FILE" "$ROW_FILE.tmp" "$ROW_FILE.height" "$ROW_FILE.stop"
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
    greeter greeting_lines
    acknowledge
    rows_begin

    row_start "$CHECKING"
    preflight
    row_end "$CHECKING" ok

    row_start "$INSTALLING"
    WORK_DIR="$(mktemp -d)"
    obtain_tarball "$WORK_DIR"
    [ ! -d "$CODE" ] || REPLACED_AN_INSTALL="yes"
    row_detail "$INSTALLING" "unpacking $(basename "$TARBALL")"
    install_tree "$TARBALL"
    move_covers
    row_detail "$INSTALLING" "$(basename "$TARBALL")$UNVERIFIED $ARROW $(tilde "$CODE")"
    row_end "$INSTALLING" ok

    row_start "$SERVICE"
    ! unit_is_active || REPLACED_AN_INSTALL="yes"
    row_detail "$SERVICE" "writing $UNIT_NAME.service"
    write_unit
    row_detail "$SERVICE" "starting $UNIT_NAME"
    start_unit
    row_detail "$SERVICE" "$(service_state)"
    row_end "$SERVICE" ok

    row_start "$STEAM"
    ensure_marker
    probe_debugger
    if [ "$DEBUGGER_ANSWERED" = "yes" ] && [ "$REPLACED_AN_INSTALL" = "yes" ]; then
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

    closing_block
}

# Whether Steam is up. `pgrep -x` matches the executable's own name exactly, so
# a window title or a launcher script holding the word does not answer for it.
steam_is_running() {
    pgrep -x steam > /dev/null 2>&1
}

# Whether the service was already running before this run touched it. Asked
# BEFORE this run starts the unit, because afterwards every answer is yes.
unit_is_active() {
    systemctl --user is-active --quiet "$UNIT_NAME" 2> /dev/null
}

# What to say the service is doing. The port file is the backend's own note of
# the port it bound, and it is written once the backend is up — which is after
# this run has asked systemd to start it, so it is usually not there yet. Its
# absence is not a fault and is not reported as one.
service_state() {
    local port_file="${XDG_RUNTIME_DIR:-}/romm-tender/port" port=""
    if [ -n "${XDG_RUNTIME_DIR:-}" ] && [ -f "$port_file" ]; then
        port="$(head -n 1 "$port_file" 2> /dev/null || true)"
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
    # Three answers because there are three machines, and telling a user to
    # restart a Steam that is not running sends them looking for a window that
    # is not there. What separates the last two is the process, not the probe:
    # the probe cannot tell a Steam that is not running from one running without
    # the marker Steam only reads at start-up.
    local next
    if [ "$DEBUGGER_ANSWERED" = "yes" ] && [ "$REPLACED_AN_INSTALL" != "yes" ]; then
        next="open the Quick Access menu — Tender's entry appears once the backend has loaded it"
    elif [ "$DEBUGGER_ANSWERED" = "yes" ] || steam_is_running; then
        next="restart Steam, then open the Quick Access menu"
    else
        next="start Steam, then open the Quick Access menu"
    fi
    echo
    # The one line that says the run worked, in the disc's own blue — the two
    # under it are where to look afterwards and stay dim. The bold is on the
    # action, because that is the only part of it a reader has to act on.
    style "$LOGO_DISC_STYLE"
    printf 'Done in %ss.  ' "$SECONDS"
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
