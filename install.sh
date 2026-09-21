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

# check_python's two refusals, which are two different things to tell a user.
PYTHON_MISSING=2
PYTHON_TOO_OLD=3

# Whether Steam's debugger answered the probe, which decides the closing line.
DEBUGGER_ANSWERED="no"

# Said on the Installing row where a local tarball had no checksum beside it.
UNVERIFIED=""

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
LOGO_RING_RGB='146;183;227'
LOGO_BUTTON_RGB='221;152;128'
LOGO_RING_256=110
LOGO_BUTTON_256=174
LOGO_BRAILLE_WIDTH=24
LOGO_ASCII_WIDTH=26
LOGO_BRAILLE=(
    '_:     |r:⢀⣠⣴⣶⣾⣿⣿⣿⣿⣷⣶⣤⣄⡀|_:     '
    '_:   |r:⣠⣶⣿⠿⠛⠉⠁|_:    |r:⠈⠉⠛⠿⣿⣶⣄|_:   '
    '_:  |r:⣼⣿⠟⠁|_:    |r:⣀|b:⣤⣤|r:⣄|_:    |r:⠈⠻⣿⣧⡀|_: '
    '_:  |r:⠛⠋|_:   |r:⣀⣴⣾|b:⣿⣿⣿⣿|r:⠂|_:     |r:⣹⣿⣷⣴'
    '_:     |r:⣰|b:⣾⣿⣿|r:⣿⣿⡿⠋⢁⣤|b:⣾⣿⣿|r:⣦|_: |r:⠙⠻⣿⠇'
    'r:⢰⣿⣦⣀|_: |r:⠻|b:⣿⣿⡿|r:⠛⢁⣠⣾⣿⣿|b:⣿⣿⡿|r:⠏|_:   |r:⠈|_: '
    'r:⠿⢿⣿⣏|_:     |r:⠠|b:⣿⣿⣿⣿|r:⣿⠟⠉|_:   |r:⣠⣤⡀|_: '
    '_: |r:⠈⢻⣿⣦⡀|_:    |r:⠙|b:⠛⠛|r:⠋|_:    |r:⢀⣴⣿⡟⠁|_: '
    '_:   |r:⠙⠿⣿⣶⣤⣀|_:      |r:⣀⣤⣶⣿⠿⠋|_:   '
    '_:     |r:⠈⠙⠻⠿⣿⣿⣿⣿⣿⣿⠿⠟⠋⠁|_:     '
)
LOGO_ASCII=(
    '_:      |r:....|_:      |r:....|_:      '
    '_:   |r:...|_:  |r:++######++|_:  |r:...|_:   '
    '_:  |r:.|_:  |r:-####++--++####+|_:  |r:.|_:  '
    '_: |r:.|_:  |r:###-|_:    |r:--|_:    |r:-###|_:  |r:.|_: '
    'r:.|_:  |r:+#+|_:    |r:+|b:O@@O|_:     |r:+##-|_: |r:.'
    'r:.|_:      |r:-+###|b:OO|r:---|b:O|r:-|_: |r:+###|_:  '
    '_:  |r:++|_:  |r:-|b:@@@|r:##++##|b:@@@|r:+|_:  |r:++|_:  '
    'r:.|_: |r:###+|_: |r:-|b:O|r:---|b:OO|r:###+-|_:       '
    'r:.|_: |r:-##+|_:     |b:O@@O|r:#-|_:   |r:+#+|_:  |r:.'
    '_: |r:.|_:  |r:###-|_:    |r:--|_:    |r:-###|_:  |r:..'
    '_:  |r:.|_:  |r:+####++---+####+|_:  |r:.|_:  '
    '_:   |r:...|_:  |r:+########+|_:  |r:...|_:   '
    '_:      |r:....|_:      |r:....|_:      '
)
# <<< generated

# What kind of output this run writes, answered ONCE. Every one of these is a
# question about the process — is stdout a terminal, does the locale say UTF-8 —
# and a question about the process cannot be asked inside a command
# substitution, where stdout is a pipe by construction. Asking them there gave a
# run on a real terminal the plain answers meant for a log file.
IS_A_TERMINAL="no"
USE_COLOUR="no"
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
terminal_width() {
    local width="${COLUMNS:-}"
    [ -n "$width" ] || width="$(tput cols 2> /dev/null || echo 80)"
    printf '%s\n' "$width"
}

# What the mark beside the text needs: the mark, the gap, and the longest line
# of the block. Under this the mark goes above the text instead.
WIDE_ENOUGH=90
LOGO_GAP="    "

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

# The mark's two tones, resolved once per run. 24-bit where the terminal says it
# can take it, the nearest 256-colour index otherwise; both values are generated
# from the palette build.py ships, so the drawing here cannot drift away from
# the mark it is a picture of.
LOGO_RING_STYLE=""
LOGO_BUTTON_STYLE=""

resolve_logo_colours() {
    case "${COLORTERM:-}" in
        *truecolor* | *24bit*)
            LOGO_RING_STYLE="38;2;$LOGO_RING_RGB"
            LOGO_BUTTON_STYLE="38;2;$LOGO_BUTTON_RGB"
            ;;
        *)
            LOGO_RING_STYLE="38;5;$LOGO_RING_256"
            LOGO_BUTTON_STYLE="38;5;$LOGO_BUTTON_256"
            ;;
    esac
}

# One row of the mark. The runs arrive grouped by tone, so the number of escapes
# written is the number of colour changes rather than the number of cells.
print_logo_row() {
    local runs run class text
    IFS="$LOGO_RUN_SEPARATOR" read -r -a runs <<< "$1"
    for run in "${runs[@]}"; do
        class="${run%%:*}"
        text="${run#*:}"
        case "$class" in
            b) style "$LOGO_BUTTON_STYLE" ;;
            r) style "$LOGO_RING_STYLE" ;;
            *) ;;
        esac
        printf '%s' "$text"
        if [ "$class" != "_" ]; then
            reset_style
        fi
    done
}

# What the greeter says beside the mark. The paths are this run's own rather
# than literals, so a hand install into another tree describes that tree.
greeting_lines() {
    printf '%s\n' "TENDER"
    printf '%s\n' "RomM library in Steam"
    printf '%s\n' ""
    printf '%s\n' "Installs for your user, no sudo:"
    printf '  %-13s %s\n' "the program" "$(tilde "$CODE")"
    printf '  %-13s %s\n' "a service" "systemd --user, starts with your session"
    printf '  %-13s %s\n' "Steam" "its debug marker, one restart"
    printf '%s\n' ""
    printf '%s\n' "Settings, library and shortcuts stay where they are."
}

# What the other two modes say instead. Neither installs anything, so neither
# lists what an install would put where.
mode_lines() {
    printf '%s\n' "TENDER"
    printf '%s\n' "$1"
}

# The greeter: the mark beside the text where there is room for both, above it
# where there is not, and not at all where there is no terminal — art in a log
# file is something somebody has to scroll past.
greeter() {
    local -a text=()
    local line
    while IFS= read -r line; do
        text+=("$line")
    done < <("$@")

    if ! on_a_terminal; then
        printf '%s\n' "${text[@]}"
        echo
        return 0
    fi

    resolve_logo_colours
    local -a art=("${LOGO_ASCII[@]}")
    local width="$LOGO_ASCII_WIDTH"
    if utf8_terminal; then
        art=("${LOGO_BRAILLE[@]}")
        width="$LOGO_BRAILLE_WIDTH"
    fi

    echo
    if [ "$(terminal_width)" -lt "$WIDE_ENOUGH" ]; then
        for line in "${art[@]}"; do
            print_logo_row "$line"
            echo
        done
        echo
        print_greeting_text "${text[@]}"
    else
        local index=0
        while [ "$index" -lt "${#art[@]}" ] || [ "$index" -lt "${#text[@]}" ]; do
            if [ "$index" -lt "${#art[@]}" ]; then
                print_logo_row "${art[index]}"
            else
                printf '%*s' "$width" ""
            fi
            printf '%s' "$LOGO_GAP"
            if [ "$index" -lt "${#text[@]}" ]; then
                print_greeting_line "$index" "${text[index]}"
            fi
            echo
            index=$((index + 1))
        done
    fi
    echo
}

print_greeting_text() {
    local index=0 line
    for line in "$@"; do
        print_greeting_line "$index" "$line"
        echo
        index=$((index + 1))
    done
}

# The name is the one bold thing in the greeter; everything under it is the
# ordinary weight, so the bold means something.
print_greeting_line() {
    if [ "$1" -eq 0 ]; then
        style 1
        printf '%s' "$2"
        reset_style
    else
        printf '%s' "$2"
    fi
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
ARROW="->"
ROW_INDENT="     "
SPINNER_FRAMES=("|" "/" "-" "\\")

resolve_look() {
    if [ -t 1 ]; then
        IS_A_TERMINAL="yes"
    fi
    if [ "$IS_A_TERMINAL" = "yes" ] && [ -z "${NO_COLOR:-}" ]; then
        USE_COLOUR="yes"
        ANIMATE="yes"
    fi
    case "${LC_ALL:-${LC_CTYPE:-${LANG:-}}}" in
        *UTF-8* | *UTF8* | *utf-8* | *utf8*) USE_UTF8="$IS_A_TERMINAL" ;;
        *) ;;
    esac
    if [ "$USE_COLOUR" = "yes" ] && [ "$USE_UTF8" = "yes" ]; then
        USE_FANCY_MARKS="yes"
        ROW_INDENT="  "
    fi
    if [ "$USE_UTF8" = "yes" ]; then
        DOT_SEPARATOR=" · "
        ARROW="→"
        SPINNER_FRAMES=("⠋" "⠙" "⠹" "⠸" "⠼" "⠴" "⠦" "⠧" "⠇" "⠏")
    fi
}

rows_begin() {
    ROW_FILE="$(mktemp)"
    : > "$ROW_FILE.height"
    flush_rows
    if may_animate; then
        draw_block ""
    fi
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
    local frame="$1" index=0 drawn=0 state detail sub sub_state on_screen=0
    if [ -s "$ROW_FILE.height" ]; then
        on_screen="$(cat "$ROW_FILE.height")"
    fi
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
    printf '%s\n' "$drawn" > "$ROW_FILE.height"
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
    printf ' %-11s ' "${ROW_LABELS[index]}"
    if [ "$state" = "pending" ]; then
        style 2
    fi
    printf '%s' "$detail"
    reset_style
    printf '\n'
    if [ -n "$sub" ]; then
        clear_line
        if [ "$sub_state" = "fail" ]; then
            style 31
        else
            style 2
        fi
        printf '%s%s' "$ROW_INDENT" "$sub"
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
# the plugin that would otherwise share the database with it.
preflight() {
    local found status=0
    found="$(check_python)" || status=$?
    case "$status" in
        0) ;;
        "$PYTHON_MISSING") abort "no Python at $PYTHON" "install python3, or point TENDER_PYTHON at one" ;;
        *) abort "$PYTHON is older than 3.11" "Tender needs Python 3.11 or newer" ;;
    esac
    row_add "$CHECKING" "$found"
    check_user_manager
    row_add "$CHECKING" "systemd"
    refuse_flatpak_steam
    STEAM_ROOT="$(check_native_steam)" ||
        abort "no native Steam installation found" "install Steam and run it once, then run this again"
    row_add "$CHECKING" "Steam"
    refuse_decky_plugin
    row_add "$CHECKING" "no Decky plugin"
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
    "$PYTHON" -c 'import sys; print("python %d.%d" % sys.version_info[:2]); sys.exit(0 if sys.version_info >= (3, 11) else 1)' ||
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
    printf '   Your old Steam shortcuts will not be recognised.\n'
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
    row_detail "$INSTALLING" "downloading $tag"

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
    if ! on_a_terminal; then
        curl -fsSL "$1" -o "$2"
        return
    fi
    # The bar draws on the line under the block, and so does the spinner's idea
    # of where the block ends — both write there and one of them moves the
    # cursor relative to it, so the spinner stops for the length of the download
    # and the bar's line is cleared before it starts again.
    local status=0
    spinner_stop
    curl -fL --progress-bar "$1" -o "$2" || status=$?
    if may_animate; then
        printf '\r\033[K'
        spinner_start
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
            MOVED_SUMMARY="$MOVED_SUMMARY + $MOVED $(folder_noun "$name" "$MOVED")"
        fi
        row_sub "$INSTALLING" "moved to the cache: $MOVED_SUMMARY"
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
    row_detail "$INSTALLING" "unpacking $(basename "$TARBALL")"
    install_tree "$TARBALL"
    move_covers
    row_detail "$INSTALLING" "$(basename "$TARBALL")$UNVERIFIED $ARROW $(tilde "$CODE")"
    row_end "$INSTALLING" ok

    row_start "$SERVICE"
    row_detail "$SERVICE" "writing $UNIT_NAME.service"
    write_unit
    row_detail "$SERVICE" "starting $UNIT_NAME"
    start_unit
    row_detail "$SERVICE" "$(service_state)"
    row_end "$SERVICE" ok

    row_start "$STEAM"
    ensure_marker
    probe_debugger
    if [ "$DEBUGGER_ANSWERED" = "yes" ]; then
        row_detail "$STEAM" "debugger answering"
        row_end "$STEAM" ok
    else
        row_detail "$STEAM" "debugger not answering yet"
        row_end "$STEAM" warn
    fi

    closing_block
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
    local next="restart Steam, then open the Quick Access menu"
    if [ "$DEBUGGER_ANSWERED" = "yes" ]; then
        next="open the Quick Access menu"
    fi
    echo
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

    echo
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
