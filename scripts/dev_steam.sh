#!/usr/bin/env bash
# Stop and start Steam for the frontend dev loop (docs/contributing/frontend-dev-loop.md);
# called by the `dev:restart` / `dev:bpm-reset` mise tasks and by dev_open_bpm.sh.
#
# Usage:
#   dev_steam.sh stop               shut Steam down and wait for it to exit
#                                   (exit 1 if it is still running after ~30 s)
#   dev_steam.sh start [arg...]     start Steam detached, passing <arg...> on
#
# Both halves launch steam through the user's systemd manager, NOT this shell:
# `mise run` and the project venv prepend their own PATH and set VIRTUAL_ENV,
# and steam-jupiter's 32-bit runtime check fails in that environment on a cold
# start ("You are missing the following 32-bit libraries: libc.so.6").
# systemd-run --user runs steam in the pristine session environment (clean
# PATH, with DISPLAY / WAYLAND_DISPLAY / DBUS intact) that Game Mode itself
# uses. Without a systemd user manager, scrubbing VIRTUAL_ENV is the
# best-effort fallback.
set -euo pipefail

stop_steam() {
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

case "${1:-}" in
  stop)
    stop_steam
    ;;
  start)
    shift
    start_steam "$@"
    ;;
  *)
    echo "usage: dev_steam.sh stop | start [arg...]" >&2
    exit 2
    ;;
esac
