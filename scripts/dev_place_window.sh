#!/usr/bin/env bash
# Resolve a display target and place one of Steam's windows on it.
#
# Part of the frontend dev loop (docs/contributing/frontend-dev-loop.md);
# called by dev_steam.sh and by the mise tasks' display completion.
#
# Usage:
#   dev_place_window.sh --list      print selectable targets, one per line:
#                                   the normalized (lowercase, dash-stripped)
#                                   form of each connected + enabled output,
#                                   plus the "internal" alias while the
#                                   built-in panel is enabled (drives the mise
#                                   tab completion — its prefix filter is
#                                   case-sensitive, so the lowercase forms are
#                                   what complete)
#   dev_place_window.sh --resolve [t]
#                                   print the output name <t> resolves to, and
#                                   nothing (with a warning) when the default
#                                   "internal" resolves to no output; exit 1
#                                   when an explicit <t> matches nothing
#   dev_place_window.sh --arm <window> <output>
#                                   arm a KWin script that places <window> —
#                                   "bpm" (Big Picture) or "desktop" (the
#                                   desktop client's main window) — on
#                                   <output> once it appears; exit 1, with
#                                   nothing armed, if KWin scripting cannot be
#                                   reached
#
# Target resolution — always against the REAL outputs of this machine (no
# hardcoded alias tables: on some Decks the external outputs are DP-2/DP-3,
# not DP-1/DP-2):
#   - "internal" (the default) resolves to the enabled output whose name
#     starts with eDP (the built-in panel).
#   - anything else matches an enabled output name case- and
#     dash-insensitively, so `dp2`, `DP2` and `DP-2` all resolve to `DP-2`.
#   - only connected AND enabled outputs count — KWin can't place a window on
#     a disabled output, so those are neither listed nor resolvable.
#   - an explicit target that matches nothing is a hard error; an
#     unresolvable DEFAULT (internal panel disabled or disconnected while
#     docked) only warns, and the window opens wherever the window manager
#     puts it.
#
# Window placement — KWin scripting over DBus (works on X11 and Wayland, no
# extra packages): a short-lived KWin script sweeps the existing windows for
# the chosen one and watches windowAdded/captionChanged for it to appear, and
# moves it to the target output (Plasma 6 API: workspace.windowList /
# workspace.sendClientToScreen / workspace.screens[].name). It stays live for
# the newest such window rather than for the first one only, because Steam
# can replace the window it opens (see track()). The script is unloaded again
# after ~120 s so re-runs never accumulate.
set -euo pipefail

SCRIPT_NAME="tender-dev-window-place"

enabled_outputs() {
  # connected AND enabled: KWin only exposes enabled outputs as screens, so a
  # connected-but-disabled panel (e.g. internal display switched off in KDE
  # while docked) can never receive a window — don't offer it as a target.
  command -v kscreen-doctor >/dev/null 2>&1 || return 0
  kscreen-doctor --json 2>/dev/null | python3 -c '
import json
import sys

try:
    data = json.load(sys.stdin)
except ValueError:
    sys.exit(0)
for output in data.get("outputs", []):
    if output.get("connected") and output.get("enabled") and output.get("name"):
        print(output["name"])
'
}

normalize() {
  printf '%s' "$1" | tr '[:upper:]' '[:lower:]' | tr -d '-'
}

list_targets() {
  # Selectable targets: the normalized form of each enabled output — exactly
  # the spellings the resolver accepts — plus the "internal" alias, but only
  # while the built-in panel is actually enabled (offering the alias for a
  # panel KWin can't place a window on would be a lie). Raw output names
  # (e.g. DP-2) resolve too; they're just not listed.
  local name outputs
  outputs=$(enabled_outputs)
  while IFS= read -r name; do
    [ -n "$name" ] || continue
    case "$(normalize "$name")" in
      edp*)
        echo "internal"
        break
        ;;
    esac
  done <<EOF
$outputs
EOF
  while IFS= read -r name; do
    [ -n "$name" ] || continue
    printf '%s\n' "$(normalize "$name")"
  done <<EOF
$outputs
EOF
}

resolve_target() {
  # Prints the enabled output name $1 resolves to; status 1 if none matches.
  local requested normalized name candidate
  requested="$1"
  normalized=$(normalize "$requested")
  while IFS= read -r name; do
    [ -n "$name" ] || continue
    candidate=$(normalize "$name")
    if [ "$normalized" = "internal" ]; then
      case "$candidate" in edp*) printf '%s\n' "$name"; return 0 ;; esac
    elif [ "$candidate" = "$normalized" ]; then
      printf '%s\n' "$name"
      return 0
    fi
  done <<EOF
$(enabled_outputs)
EOF
  return 1
}

arm_kwin_placement() {
  # Loads + runs the KWin placement script for window $1 on output $2.
  # Status 1 (with nothing armed) if any step of the DBus route fails.
  local window="$1" output="$2" qdbus js_file script_id
  qdbus=$(command -v qdbus6 || command -v qdbus || true)
  [ -n "$qdbus" ] || return 1
  js_file=$(mktemp --suffix=.js) || return 1
  cat > "$js_file" <<'EOF' || { rm -f "$js_file"; return 1; }
var TARGET_OUTPUT = "@TARGET@";
var WINDOW = "@WINDOW@";
// The window we currently manage, and a bounded re-assert budget. Both are per
// window, not per run: Steam does not always keep the window it maps first. A
// window carrying the same caption has been seen to replace it 8-12 s later,
// with only one steam-class window alive afterwards; what makes Steam do it is
// not established. See track() for what a replacement costs and why the budget
// exists.
var tracked = null;
var reasserts = 0;
var MAX_REASSERTS = 8;

// All log lines land in the user journal (journalctl --user | grep tender-dev-window),
// so a placement that misses is diagnosable without re-instrumenting.
function log(msg) {
  print("[tender-dev-window] " + msg);
}

function targetOutput() {
  var screens = workspace.screens;
  for (var i = 0; i < screens.length; i++) {
    if (screens[i].name === TARGET_OUTPUT) {
      return screens[i];
    }
  }
  return null;
}

function outputName(win) {
  return win && win.output ? win.output.name : "?";
}

function onTarget(win) {
  return win && win.output && win.output.name === TARGET_OUTPUT;
}

// Class "steam" covers the desktop client window, Big Picture, and Steam's own
// popups (a store offer, the friends list), so the caption decides which is
// which — and a caption has to be matched on something no locale translates.
function isManaged(win) {
  if (!win || !win.resourceClass ||
      win.resourceClass.toLowerCase() !== "steam") {
    return false;
  }
  var caption = win.caption || "";
  if (WINDOW === "desktop") {
    // The desktop client's main window is captioned with the bare brand,
    // "Steam", which a German client leaves untranslated while its popups carry
    // translated titles. A fullscreen steam window is Big Picture.
    return caption === "Steam" && win.fullScreen !== true;
  }
  // BPM keeps the "Big Picture" brand in its title across locales (German is
  // "Big-Picture-Modus"), so match on "picture" — the English "big picture"
  // with a space misses the hyphenated forms. A fullscreen steam window is BPM
  // as well (the desktop client window isn't fullscreen), which covers a locale
  // that fully translates the title.
  return caption.toLowerCase().indexOf("picture") !== -1 ||
    win.fullScreen === true;
}

function place(win) {
  var output = targetOutput();
  if (!output) {
    log("target output " + TARGET_OUTPUT + " not present; leaving window put");
    return;
  }
  // sendClientToScreen is the idiomatic move; wrap it because the accepted
  // second-arg type has varied across KWin versions (Output vs screen index).
  try {
    workspace.sendClientToScreen(win, output);
  } catch (e) {
    log("sendClientToScreen threw: " + e);
  }
  // Reinforce/fallback for a windowed (non-fullscreen) window: put the frame
  // on the target output. Skipped for fullscreen, which KWin re-lays out per
  // output on its own.
  if (!win.fullScreen) {
    try {
      var geo = output.geometry;
      var fg = win.frameGeometry;
      win.frameGeometry = {
        x: geo.x + Math.max(0, Math.floor((geo.width - fg.width) / 2)),
        y: geo.y + Math.max(0, Math.floor((geo.height - fg.height) / 2)),
        width: fg.width,
        height: fg.height,
      };
    } catch (e) {
      log("geometry move threw: " + e);
    }
  }
}

function track(win) {
  if (win === tracked) {
    return;
  }
  // The newest matching window wins. Latching onto the first one for the whole
  // run placed the window nobody ends up looking at: the visible symptom was
  // BPM showing up on the target for a beat and then "jumping back" — the
  // replacement, which the latch skipped, mapping wherever Steam put it.
  tracked = win;
  reasserts = 0;
  log("tracking " + WINDOW + " window [" + win.caption + "] on " +
    outputName(win) + " -> placing on " + TARGET_OUTPUT);
  place(win);
  // A fullscreen window leaves place() with sendClientToScreen alone (the
  // geometry fallback in it is skipped), so log where the window actually ended
  // up: a move that does nothing is otherwise indistinguishable in the journal
  // from one that worked.
  log("after place: [" + win.caption + "] on " + outputName(win) +
    " (fullScreen=" + win.fullScreen + ")");
  // A cold-started Steam moves Big Picture onto the monitor it remembers a beat
  // AFTER the window first appears, overriding the initial move. React to the
  // real event instead of guessing a delay: outputChanged fires whenever the
  // window changes monitor. Our own move fires it too, but lands on target, so
  // onTarget() short-circuits and there is no ping-pong. The budget bounds how
  // many times we fight Steam's startup shuffle, so a later MANUAL drag to
  // another display is left alone.
  if (win.outputChanged) {
    win.outputChanged.connect(function () {
      // Bound to the window this handler was connected for, not to whatever is
      // tracked now: a replaced window keeps its connection and would otherwise
      // spend the current window's budget on moves nobody sees.
      if (win !== tracked || onTarget(win)) {
        return;
      }
      if (reasserts >= MAX_REASSERTS) {
        log("stopped re-asserting after " + reasserts +
          "; window left on " + outputName(win));
        return;
      }
      reasserts++;
      log("Steam moved the " + WINDOW + " window to " + outputName(win) +
        "; re-assert #" + reasserts + " -> " + TARGET_OUTPUT);
      place(win);
    });
  }
}

log("armed for the " + WINDOW + " window on " + TARGET_OUTPUT + "; screens=" +
  workspace.screens.map(function (s) { return s.name; }).join(","));

// Cover a window that is already open (Steam was running) — the sweep.
var existing = workspace.windowList();
for (var i = 0; i < existing.length; i++) {
  var w = existing[i];
  log("existing win caption=[" + w.caption + "] class=[" +
    w.resourceClass + "] output=[" + outputName(w) + "]");
  if (isManaged(w)) {
    track(w);
  }
}

// Cover a window that opens after we arm (Steam cold-started).
workspace.windowAdded.connect(function (win) {
  log("added win caption=[" + win.caption + "] class=[" +
    win.resourceClass + "] output=[" + outputName(win) + "]");
  if (isManaged(win)) {
    track(win);
  } else if (win.captionChanged) {
    // resourceClass is set at creation, but the title can arrive a beat later.
    win.captionChanged.connect(function () {
      log("retitled -> caption=[" + win.caption + "]");
      if (isManaged(win)) {
        track(win);
      }
    });
  }
});
EOF
  sed -i -e "s/@TARGET@/$output/" -e "s/@WINDOW@/$window/" "$js_file" || { rm -f "$js_file"; return 1; }
  # Guarded unload first so a re-run never collides with a lingering script.
  "$qdbus" org.kde.KWin /Scripting org.kde.kwin.Scripting.unloadScript "$SCRIPT_NAME" >/dev/null 2>&1 || true
  script_id=$("$qdbus" org.kde.KWin /Scripting org.kde.kwin.Scripting.loadScript "$js_file" "$SCRIPT_NAME" 2>/dev/null) || {
    rm -f "$js_file"
    return 1
  }
  case "$script_id" in
    '' | *[!0-9]*)
      rm -f "$js_file"
      return 1
      ;;
  esac
  "$qdbus" org.kde.KWin "/Scripting/Script$script_id" org.kde.kwin.Script.run >/dev/null 2>&1 || {
    "$qdbus" org.kde.KWin /Scripting org.kde.kwin.Scripting.unloadScript "$SCRIPT_NAME" >/dev/null 2>&1 || true
    rm -f "$js_file"
    return 1
  }
  # Keep the watcher armed for the window to appear, then self-clean.
  (
    sleep 120
    "$qdbus" org.kde.KWin /Scripting org.kde.kwin.Scripting.unloadScript "$SCRIPT_NAME" >/dev/null 2>&1 || true
    rm -f "$js_file"
  ) &
  return 0
}

usage() {
  echo "usage: dev_place_window.sh --list | --resolve [target] | --arm bpm|desktop <output>" >&2
  exit 2
}

case "${1:-}" in
  --list)
    list_targets
    ;;
  --resolve)
    TARGET="${2:-internal}"
    if RESOLVED=$(resolve_target "$TARGET"); then
      echo "$RESOLVED"
    elif [ "$(normalize "$TARGET")" = "internal" ]; then
      echo "warning: no enabled internal (eDP*) panel found — window placement will be skipped" >&2
    else
      {
        echo "error: display target '$TARGET' matches no enabled output. Available targets:"
        list_targets
      } >&2
      exit 1
    fi
    ;;
  --arm)
    [ $# -eq 3 ] || usage
    case "$2" in
      bpm | desktop) ;;
      *) usage ;;
    esac
    arm_kwin_placement "$2" "$3"
    ;;
  *)
    usage
    ;;
esac
