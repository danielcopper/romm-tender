#!/usr/bin/env bash
# Restart Steam for the frontend dev loop (docs/contributing/frontend-dev-loop.md);
# called by the `dev*` mise tasks.
#
# Usage:
#   dev_steam.sh restart [bpm|desktop [display]]
#       Restart Steam into windowed Big Picture or the desktop client, its
#       window placed on <display> (default "internal"), and remember both.
#       With no window, restart into the remembered one and remember nothing.
#       A Steam that is not running is started; one still running ~30 s after
#       the shutdown request fails the restart (exit 1).
#
# Both the shutdown and the start launch steam through the user's systemd
# manager, NOT this shell: `mise run` and the project venv prepend their own
# PATH and set VIRTUAL_ENV,
# and steam-jupiter's 32-bit runtime check fails in that environment on a cold
# start ("You are missing the following 32-bit libraries: libc.so.6").
# systemd-run --user runs steam in the pristine session environment (clean
# PATH, with DISPLAY / WAYLAND_DISPLAY / DBUS intact) that Game Mode itself
# uses. Without a systemd user manager, scrubbing VIRTUAL_ENV is the
# best-effort fallback.
set -euo pipefail

PLACE_WINDOW="$(dirname "$0")/dev_place_window.sh"
MEMORY_FILE="${XDG_STATE_HOME:-$HOME/.local/state}/tender-dev/steam-window"

stop_steam() {
  if ! pgrep -x steam >/dev/null; then
    return 0
  fi
  if command -v systemd-run >/dev/null 2>&1; then
    systemd-run --user --collect --quiet --wait -- steam -shutdown >/dev/null 2>&1 || true
  else
    env -u VIRTUAL_ENV steam -shutdown >/dev/null 2>&1 || true
  fi
  # `steam -shutdown` returns before the running instance has exited, and a
  # start issued in that window talks to the dying process instead of starting
  # a new one.
  for _ in $(seq 1 30); do
    if ! pgrep -x steam >/dev/null; then
      return 0
    fi
    sleep 1
  done
  echo "Steam is still running after 30 s — close it manually, then re-run this task." >&2
  return 1
}

start_steam() {
  if command -v systemd-run >/dev/null 2>&1; then
    systemd-run --user --collect --quiet -- steam "$@" >/dev/null 2>&1 &
  else
    nohup env -u VIRTUAL_ENV steam "$@" >/dev/null 2>&1 &
  fi
}

remember() {
  mkdir -p "$(dirname "$MEMORY_FILE")"
  printf 'window=%s\ndisplay=%s\n' "$1" "$2" > "$MEMORY_FILE"
}

# Sets WINDOW and TARGET from the memory file; the desktop client on the
# internal panel when nothing is remembered.
recall() {
  local key value
  WINDOW="desktop"
  TARGET="internal"
  [ -f "$MEMORY_FILE" ] || return 0
  while IFS='=' read -r key value || [ -n "$key" ]; do
    case "$key" in
      window) WINDOW="$value" ;;
      display) TARGET="$value" ;;
    esac
  done < "$MEMORY_FILE"
}

restart_steam() {
  local explicit output
  if [ $# -gt 0 ]; then
    explicit=1
    WINDOW="$1"
    TARGET="${2:-internal}"
  else
    explicit=0
    recall
    echo "Remembered: $WINDOW on $TARGET ($MEMORY_FILE)"
  fi
  case "$WINDOW" in
    bpm | desktop) ;;
    *)
      echo "error: unknown window '$WINDOW' — expected bpm or desktop" >&2
      [ "$explicit" = 1 ] || echo "  (read from $MEMORY_FILE)" >&2
      return 1
      ;;
  esac
  # Resolved BEFORE Steam is shut down, so a display that matches nothing costs
  # an error message rather than a restart.
  if ! output=$(bash "$PLACE_WINDOW" --resolve "$TARGET"); then
    if [ "$explicit" = 0 ]; then
      echo "  The remembered display is not connected. Choose one with" \
        "\`mise run dev:bpm <display>\` or \`mise run dev:desktop <display>\`." >&2
    fi
    return 1
  fi

  echo "Shutting down Steam..."
  stop_steam
  # Armed after the old window is gone and before the new one can appear, so
  # the watcher neither latches onto a closing window nor misses the new one.
  if [ -n "$output" ]; then
    if bash "$PLACE_WINDOW" --arm "$WINDOW" "$output"; then
      echo "The $WINDOW window will be placed on $output."
    else
      echo "warning: could not reach KWin scripting over DBus — starting Steam without window placement" >&2
    fi
  fi
  case "$WINDOW" in
    bpm)
      echo "Starting Steam into windowed Big Picture..."
      start_steam steam://open/bigpicture
      ;;
    desktop)
      echo "Starting Steam into the desktop client..."
      start_steam
      ;;
  esac
  if [ "$explicit" = 1 ]; then
    remember "$WINDOW" "$TARGET"
  fi
}

case "${1:-}" in
  restart)
    shift
    restart_steam "$@"
    ;;
  *)
    echo "usage: dev_steam.sh restart [bpm|desktop [display]]" >&2
    exit 2
    ;;
esac
