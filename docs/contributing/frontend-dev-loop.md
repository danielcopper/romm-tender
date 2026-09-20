# Frontend dev loop

Iterate on the panel from Desktop Mode on the Steam Deck: edit code next to one of Steam's windows — windowed Big
Picture or the desktop client — with the backend running beside it. Nothing is deployed anywhere and no plugin loader is
involved.

## The loop

```bash
mise run dev                           # build, restart Steam into the remembered window, run the backend
mise run dev:bpm [display]             # the same into windowed Big Picture on <display>, and remember that
mise run dev:desktop [display]         # the same into the desktop client on <display>, and remember that
mise run dev:backend                   # build and run the backend against the Steam already running
mise run dev:frontend [display]        # build and restart Steam; the running backend loads the panel
mise run dev:bpm-reset [display]       # second terminal, wrong display: only the restart, into Big Picture
mise run dev:desktop-reset [display]   # ...or into the desktop client
```

`mise run dev` is the everyday command. It builds the three bundles, restarts Steam into the window and display you last
chose, and runs the backend in the foreground. `dev:bpm` and `dev:desktop` do the same with the window named on the
command line — that is how you switch between Big Picture and the desktop client, or move to another display — and `dev`
keeps that choice from then on. Each of the three ends in the same known state: a Steam started afresh, and the panel
loaded into it by a backend running the current build.

The two resets are for a window on the wrong display. They restart Steam into the window and display you name, and do
nothing else — no build, and no backend of their own: a backend still running from `dev` in the first terminal loads the
panel into the fresh Steam by itself. They remember their choice too, so the next `dev` does not put it back wrong.

`dev:backend` and `dev:frontend` are `dev` split in two, for when only one of its halves is what you want. `dev:backend`
builds and runs the backend against the Steam that is already there and restarts nothing — the task to reach for after a
reset, or beside a Steam that is up and carrying no panel. `dev:frontend` is the mirror: it builds and restarts Steam,
and leaves the backend you already have running alone, so the rebuilt panel reaches a fresh context without a sync in
flight being killed to get it there.

`dev:backend` is the wrong task beside a panel that is already loaded: the injector refuses a context that carries its
marker, so nothing is loaded, and the panel already in that context cannot reach the new backend either, because every
backend process mints its own admission token. Those are the same two facts that make the other tasks restart Steam —
and what makes `dev:frontend` worth having, because the restart is exactly what they call for.

The display argument is optional, and leaving it out means one thing on a task that names a window and another on
`dev:frontend`, which repeats one — see [Choosing the display](#choosing-the-display). Every one of these tasks but
`dev:backend` shuts the running Steam down first, so anything open in it closes; a Steam that is not running is simply
started. A build that fails, or a display that matches nothing, stops the task before Steam is touched.

**The five tasks that build ask who holds the single-instance lock before anything else they do** — `backend.lock`,
beside the database — and refuse on the wrong answer, before the build as well as before Steam: no refusal here is one
the task could go on from, so a build ahead of it is a build thrown away. Which answer is the wrong one depends on what
the task is for, so the question has two directions:

- `dev`, `dev:bpm`, `dev:desktop` and `dev:backend` start a backend, so they refuse when one **already holds** the lock,
  naming the lock file and the process that has it. Stop that backend, or use a `-reset` task, which starts none.
  Without the question the restart goes ahead regardless and the second backend exits a line later, leaving a freshly
  started Steam with no panel in it and one line of stderr to say why.
- `dev:frontend` starts none, so it refuses when **nobody holds** it: a restart with no backend behind it gives a fresh
  Steam that nothing loads a panel into. The message names `dev:backend` and `dev`, the two that start one.

The two resets ask nothing at all, deliberately: they build nothing and start nothing, so they remain the way out of
either refusal. And the reading is a moment's answer rather than a guarantee — a backend started in another terminal a
second later is not stopped by it, and a lock that cannot be read at all is reported as such and the task carries on.

The backend serves `dist/` on a loopback port and loads the panel into Steam's renderer over the CEF debugger — see
[How the panel gets into Steam](../architecture/loading-the-panel.md). Ctrl-C stops it and lets it unload. It needs
`~/.steam/steam/.cef-enable-remote-debugging` to exist: create the empty file once, and the next task's restart makes
Steam read it.

Keyboard shortcuts in the Big Picture window: **Ctrl+2** opens the Quick Access Menu, **Ctrl+1** the main menu.

One start-up line on stderr prints an address with the port and the token in it — the deliberate exception to the token
never being printed, and what makes the served root reachable by hand. **It is not the injector's choice**: the line is
built from a constant (`BUNDLE_FILENAME`, `index.js`) and says where the served root answers, so beside a serving Decky
Loader it names a file the injector did not load. Read it for the port and the token.

!!! danger "Do not hand-import `index.js` beside a running Decky Loader"

    Pasting that address into the DevTools console imports the standalone panel, which carries `@decky/ui` — and
    re-running that sweep under a Decky that is already rendering is the crash that takes the Big Picture window down
    ([frontend bundles](../architecture/frontend-bundles.md#why-two-copies-of-the-panel)). On a fresh context it throws
    anyway, because `dist/globals.js` has not run. Let the backend load the panel; it picks the pair that is safe for
    the machine it is on.

## Why the loop restarts Steam

**There is no hot reload, and no deploy smaller than the whole.** Three facts make it so:

- **A rebuilt bundle needs a fresh JS context.** The injector refuses a context that already carries the panel — it
  knows one by [its marker](../architecture/loading-the-panel.md#the-marker) — and only a rebuild of the context wipes
  the marker. A Steam restart is the one way to ask for that. `TENDER_INJECT=force` does not get round it: that switch
  belongs to the crash watchdog, and the marker check does not read it.
- **A new backend strands the panel the old one loaded.** Every backend process makes its own admission token
  (`new_token` in `backend/host/access.py`), and the panel reads its address and token off the URL it was loaded from
  ([the token](../architecture/loading-the-panel.md#the-token)), so a restarted backend leaves the running panel holding
  a token nobody accepts.
- **Both windows are one load.** The desktop client and Big Picture render from one shared JS context, and the backend
  loads the panel into it once. There is no loading into one window and not the other; the tasks differ only in which
  window you end up looking at.

A frontend change and a backend change are therefore the same step: Ctrl-C, and `mise run dev` again.

## Why the window is remembered rather than read

Steam does not come back in the window it was shut down from: a plain start after a shutdown opens the desktop client,
even when windowed Big Picture was open before. So the restart has to be told which window to open, and asking the
running Steam does not answer it — `SteamUIStore.m_mainInstanceUIMode` describes Steam's main instance, not the window
on screen, and reads as the desktop client while a windowed Big Picture is open; which display a window sits on is
KWin's to know, not Steam's. Remembering the last explicit choice needs no detection and comes out the same every time.

The memory is one plain-text file per machine, `${XDG_STATE_HOME:-~/.local/state}/tender-dev/steam-window`:

```text
window=bpm
display=dp2
```

`window` is `bpm` or `desktop`; `display` is any [display target](#choosing-the-display), or empty when the task that
wrote it named no display — which means the window is placed nowhere. Every `dev:bpm*` and `dev:desktop*` task writes
it, and `dev:frontend` writes the display when it is given one. `dev` only reads it, and with no file it opens the
desktop client and places it nowhere. It lives outside the repository because the display you dock to belongs to the
machine, and a file in the tree would start every new worktree with an empty memory. Edit or delete it by hand as you
like. A remembered display that is no longer connected stops `dev` before Steam is touched, and the message names the
tasks that choose another.

## Why the windowed Big Picture

Desktop Steam's Big Picture window runs the same gamepadui React app as Game Mode — same routes, same game-detail pages
— so what you see there is the panel's actual UI rather than an approximation. It is _not_ WYSIWYG about **vertical
space**: by default the windowed BPM gives the QAM panel far more height than the Deck does. See
[Display scale: the dev loop lies about height](#display-scale-the-dev-loop-lies-about-height).

`mise run dev:bpm [display]` opens it, and `dev` keeps opening it from then on.

### Choosing the display

The optional display argument of the four `dev:bpm*` and `dev:desktop*` tasks and of `dev:frontend` is matched against
the **real outputs** of the machine — output naming varies between Decks and docks (external outputs may be
`DP-2`/`DP-3` rather than `DP-1`/`DP-2`), so nothing is hardcoded. List the selectable targets with
`scripts/dev_place_window.sh --list`: it prints a lowercase short form per connected **and enabled** output (e.g.
`edp1`, `dp2`, `dp3`), plus the `internal` alias while the built-in panel is enabled. Disabled outputs are neither
listed nor resolvable — KWin can't place a window on them. The raw `kscreen-doctor -o` names (e.g. `DP-2`) are accepted
as well — matching is case- and dash-insensitive, so `dp2`, `DP2` and `DP-2` all mean `DP-2`. `internal` resolves to the
built-in panel (`eDP-*`); a target that matches no enabled output is a hard error before Steam is shut down. Asking for
`internal` explicitly on a docked Deck whose internal panel is disabled or disconnected prints a warning and leaves the
window where the window manager puts it.

**What omitting the argument means differs between the two kinds of task, and the difference is deliberate.** On
`dev:bpm` and `dev:desktop` you are STATING an intent, so leaving the display out is part of it: **nothing is placed** —
no target is resolved, no placement is armed, the window opens wherever Steam and the window manager put it, and that is
what the task remembers, so the next `dev` places nothing either. `dev:frontend` REPEATS the last intent instead, so
leaving the display out means "as before": the remembered display stands and nothing is written. A task whose whole job
is to get a rebuilt panel in front of you would otherwise throw away the display you had chosen.

Placement itself is done by a short-lived KWin script loaded over DBus (`scripts/dev_place_window.sh`), which moves the
chosen window to the target output and unloads itself again — if KWin scripting is unavailable, the loop still works and
only the placement is skipped. It sweeps the windows already open first and then watches for one to appear, so a Steam
that was already running is covered as well as a cold-started one. Which window is which is decided by the caption with
a fullscreen test beside it, because the class `steam` covers the desktop client, Big Picture and Steam's own popups
alike: Big Picture is a caption carrying "picture" — which survives the German "Big-Picture-Modus" — **or** any
fullscreen `steam` window, which covers a locale that translates the title outright; the desktop client's main window is
the bare caption "Steam" and not fullscreen, which is a German client's spelling too but is not claimed for every
locale. Either window stays a normal desktop window: it can always be dragged elsewhere.

With [mise shell completions](https://mise.jdx.dev/installing-mise.html#shells) enabled (requires the `usage` CLI, e.g.
`eval "$(mise completion bash)"` in your shell rc), the display argument tab-completes with those targets.

## Display scale: the dev loop lies about height

The windowed Big Picture renders the plugin's real UI, but **not at the Deck's real size**. Measured on-device:

| Configuration                                     | Big Picture window               | QuickAccess (QAM) panel |
| ------------------------------------------------- | -------------------------------- | ----------------------- |
| Game Mode (Deck internal panel, scale 1.5)        | 1280x800 physical, CSS 853x533   | CSS **854x454**         |
| Desktop BPM, 1280x800 window, unscaled (dpr 1)    | 1280x800 physical, CSS 1280x800  | CSS **854x720**         |
| Desktop BPM fullscreen on 1440p, Steam's auto 1.9 | 2560x1440 physical, CSS 1348x758 | CSS **855x679**         |
| Desktop BPM fullscreen on 1440p, **forced 1.5**   | 2560x1440 physical, CSS 1707x960 | CSS **854x880**         |

The dev loop hands the QAM panel far more vertical room than the device. CSS **width is ~854 everywhere**, so layout,
wrapping and truncation are faithful — only **height** lies. A panel that fits comfortably in the windowed BPM can
overflow in Game Mode.

The last row is the trap, and it is why this tool does two things rather than one. Every number above obeys:

```text
QAM CSS height = (Big Picture window PHYSICAL height / scale) - 80
```

- Game Mode: 800 / 1.5 − 80 = 453 → **measured 454**
- Fullscreen 1440p at Steam's automatic 1.9: 1440 / 1.9 − 80 = 678 → **measured 679**
- Fullscreen 1440p forced to 1.5: 1440 / 1.5 − 80 = **880** — nearly double the Deck's 454

(Steam lays a view out in `ceil(physical / scale)` CSS px, hence the ±1.) So **forcing the scale alone does not emulate
the Deck**: on a maximized or fullscreen Big Picture it hands you the Deck's `devicePixelRatio` with the wrong layout —
a lie that looks like a measurement. The window has to be the Deck panel's **1280x800 physical pixels** as well.

The 1.5 is **Steam's own per-display "GamepadUI display scale"** — not gamescope, and not a Chromium flag (Chromium is
pinned to `--force-device-scale-factor=1` by `steamclient.so`). Steam derives it from the display's resolution and
physical size and pushes it into each CEF browser view; it is the same value the user sees under **Settings → Display →
UI Scale**. Steam's default is **automatic**, which on a 1280x800 window comes out as 1.

### `mise run dev:ui-scale`

```bash
mise run dev:ui-scale          # deck (default): force 1.5 — exact Game Mode metrics
mise run dev:ui-scale 2.4      # a user on "Larger text" — worst case, least vertical room
mise run dev:ui-scale steam    # adopt whatever UI Scale you have set in Steam
mise run dev:ui-scale auto     # rescue: force automatic scaling back on, and exit
```

The task emulates the Deck with **both halves**:

1. **The window.** KWin scripting over DBus (the same `loadScript`/`run`/`unloadScript` route
   [`dev_place_window.sh`](#choosing-the-display) uses for placement) un-fullscreens the Big Picture window and sizes it
   so its **client area is exactly 1280x800**, on whatever monitor it already sits on. `frameGeometry` includes the
   window decoration, so the frame is corrected by the measured frame-vs-client delta until the client area lands
   exactly — nothing about the decoration is hardcoded.
2. **The scale.** The two undocumented calls Steam's own settings UI uses
   (`SteamClient.Window.SetGamepadUIAutoDisplayScale` / `SetGamepadUIManualDisplayScaleFactor`), driven through the CEF
   debugger on `localhost:8080`, so the views are **really re-laid out and repainted** — unlike CDP's
   `Emulation.setDeviceMetricsOverride`, which only fakes `devicePixelRatio` and leaves the window unpainted.

It then **verifies that the emulation actually happened** rather than claiming it, comparing the measured QAM against
what a Deck renders at that scale:

```text
$ mise run dev:ui-scale deck
Captured prior state: AUTOMATIC scaling at dpr 1.899999976158142 (source: Steam's live settings store)
Captured prior window: fullscreen 2560x1440 on DP-2
Sizing the Big Picture window to a 1280x800 client area (was fullscreen 2560x1440 on DP-2)...
  window now: windowed 1280x800 on DP-2 (frame 1282x829)
Forcing GamepadUI display scale 1.5 (auto scaling OFF)...
  scaling display: External: DP-2 27"|||Windowed

Measured (real, repainted — not a CDP emulation override):
  Big Picture  dpr 1.5  CSS 854x534
  QuickAccess  dpr 1.5  CSS 854x454
  => QAM is 854x454 CSS at dpr 1.5, the 854x454 a Deck renders at scale 1.5 — this is what the Deck renders.
```

If the QAM does **not** come out at the expected size (KWin unreachable, or Steam re-asserted the window size), the run
prints a loud `EMULATION FAILED` block with the measured-vs-expected numbers and the window's real pixel size, and says
that any layout judgement made on those numbers is invalid. It never reports Deck metrics it did not achieve.

#### Open the QAM once

Steam creates the QuickAccess view together with the Big Picture window but **lays it out only on the first QAM open of
that session**. Until then it measures **1x1 CSS**, and there is simply nothing to verify — so a run started against a
fresh Big Picture says so instead of crying wolf:

```text
Measured (real, repainted — not a CDP emulation override):
  Big Picture  dpr 1.5  CSS 854x534
  QuickAccess  dpr 1.5  CSS 1x1 — created, never rendered

QAM NOT VERIFIED YET — the emulation is applied, the QAM has simply never been opened.
  Big Picture: 1280x800 physical, scale forced to 1.5 (rendering at dpr 1.5).
  ...
  => OPEN THE QAM ONCE. This tool re-applies the scale to it the moment it renders, and
     then verifies it against the 854x454 a Deck shows at scale 1.5.
```

**Open the QAM once** and the run verifies it and prints the same verdict it would have printed up front — the success
line, or the loud `EMULATION FAILED` block if the window is not the Deck's size after all. A never-opened QAM is a
not-yet, not a failure; a QAM that renders at the wrong size still fails loudly.

That laziness has a sharper edge, and it is why the tool **holds in a polling loop rather than idling**: Steam's scale
push reaches only the views that are **rendered when it lands**. A QuickAccess view that renders _after_ the force can
therefore come up **unscaled — measured at dpr 1, CSS 854x720**, which is exactly the dev-loop lie this whole tool
exists to kill. So while it holds, the tool watches that view and re-issues the same two calls the moment it finds it
rendering at the wrong `devicePixelRatio`:

```text
QuickAccess view is at dpr 1.899999976158142  CSS 855x343 — not the forced 1.5.
Re-applying the scale (Steam's push reaches only the views that are rendered when it lands)...

Measured (real, repainted — not a CDP emulation override):
  Big Picture  dpr 1.5  CSS 854x534
  QuickAccess  dpr 1.5  CSS 854x454
  => QAM is 854x454 CSS at dpr 1.5, the 854x454 a Deck renders at scale 1.5 — this is what the Deck renders.
```

This also covers Steam re-materializing the popup later in the session. It is quiet by construction: it re-applies only
when a **rendered** view sits at the wrong dpr, and only once per distinct measurement, so a re-apply that does not take
is reported once rather than every couple of seconds.

The forcing modes **hold until Ctrl-C**. Useful factors:

| Factor  | What it reproduces              | Measured QAM   |
| ------- | ------------------------------- | -------------- |
| 1.5     | Deck internal panel (Game Mode) | 854x454        |
| 2.0–2.4 | A user who picked "Larger text" | 855x255 @ 2.4  |
| ~1.28   | Docked 1080p                    | 855x546 @ 1.28 |
| ~1.71   | Docked 1440p                    | —              |

#### What it restores on exit

The tool **captures both halves before it touches anything** — the scale you were on, and the Big Picture window's
geometry and fullscreen state — and puts back exactly that, on Ctrl-C, on SIGTERM, and on any error.

- **Scale.** If you were on automatic scaling, you get automatic scaling back. If you had deliberately set a **manual**
  UI Scale (an accessibility "Larger text" value, say), you get **that factor** back — it is never silently flipped to
  auto, which would destroy the setting. The restore is verified against Steam's live state and the rendered
  `devicePixelRatio`, and a restore that didn't land is reported as a warning rather than assumed.
- **Window.** It goes back to the geometry and fullscreen state it had — a fullscreen BPM is put back to fullscreen on
  the same monitor.

The scale is restored **before** the window, and the exit path **ignores further Ctrl-C/SIGTERM while it runs** so an
impatient second interrupt cannot truncate it half-way (the window half is the slow half — subprocesses and a journal
read). A stranded forced scale is the damaging outcome; a window left at 1280x800 is merely annoying.

`mise run dev:ui-scale auto` is different on purpose: it **unconditionally** forces automatic scaling back on. It is the
rescue path for when a previous run was hard-killed and the state it captured died with it — it has nothing to restore,
so it cannot honour a manual scale, and it does not touch the window (resize it by hand). If you are a manual-UI-Scale
user and ever have to use it, re-set your scale in Steam → Settings → Display afterwards.

!!! warning "A hard kill leaves the forced scale behind"

    Steam persists the factor to `~/.local/share/Steam/config/config.vdf` (`UI → display → Current → ScaleFactor`,
    flushed within seconds). Ctrl-C, SIGTERM and errors all restore — but a **hard kill (SIGKILL) skips the restore**,
    and the forced scale is what Steam keeps. If that happens, or if the UI ever comes up at the wrong size, run
    `mise run dev:ui-scale auto`.

    This is also why the prior state is read from Steam's **live settings store**
    (`window.settingsStore.settings`) rather than from `config.vdf`: the file's `AutoScaleFactor` is **not** re-flushed
    in-session (measured: it stayed `1` while a manual factor was applied and rendering), so trusting it would report a
    manual-scale user as "automatic" — and restore them to the wrong thing. `config.vdf` is only the fallback source.

!!! info "The UI Scale is per display — and the window mode is part of the display's identity"

    Steam files the scale under a display identity (`strDisplayName`), and `config.vdf` carries **one entry per
    identity**. The identity includes the window mode, which the tool prints on every run:

    ```text
    "External: eDP-1 7"|||Fullscreen-1280x800"    <- Game Mode / the internal panel
    "External: DP-2 27"|||Fullscreen-2560x1440"   <- a fullscreen BPM on an external 1440p monitor
    "External: DP-2 27"|||Windowed"               <- any windowed BPM on that monitor
    ```

    So the blast radius of a forced factor is narrower than "it bleeds into Game Mode": it lands in **the entry the
    window is currently under**. Because this tool always makes the window *windowed*, it writes a `…|||Windowed`
    entry — **not** Game Mode's `…|||Fullscreen-1280x800` one, even when the window sits on the Deck's internal panel.
    A leftover forced factor therefore mis-sizes a future *windowed desktop BPM*, and `dev:ui-scale auto` clears it.

The QAM is a popup view that Steam creates lazily and can re-materialize at will, and Steam's scale reaches only the
views that are **rendered when it is pushed** — a view that renders later comes up unscaled. That is why the tool holds
in a polling loop and re-applies the scale to a QuickAccess view it finds at the wrong `devicePixelRatio` (see
[Open the QAM once](#open-the-qam-once)).

### What this means for our panels

**The QAM's usable height is a runtime variable, not a constant.** The user can pick any UI Scale, and docking changes
it automatically — measured on this Deck the same QAM panel ranged from **255 CSS px** (scale 2.4) to **720 CSS px**
(unscaled desktop window), with Game Mode's 454 in between. So:

- **Never assume a pixel budget.** No fixed heights, no `maxHeight` tuned against one screenshot, nothing that only fits
  at 454 px.
- **Every row must be focusable**, so gamepad navigation can reach content that is scrolled out of view.
- **Let Steam's scroll container do its job** — the panel scrolls; content below the fold is reachable, not lost.

Validate a panel at 1.5 (the device) and at 2.4 (the worst case) before calling a layout done.

## DevTools

With `~/.steam/steam/.cef-enable-remote-debugging` present, Steam exposes the CEF DevTools protocol on
<http://localhost:8080>:

- The **SharedJSContext** target is where all plugin JS runs — console output and JS debugging live here.
- The **Steam Big Picture Mode** target is the rendered UI — element inspection and live CSS editing.

The backend drives the same protocol on the same port, so a DevTools window open on `SharedJSContext` and the injector
are two clients of one debugger; both work at once. What the backend did and why is in its own log — `backend.log` under
the state directory, and on stderr in the terminal `mise run dev` is running in.

## Troubleshooting and caveats

- **The panel is not there** — read the backend's own log first; it says which bundles it loaded, or why it loaded none.
  The states worth knowing: no CEF debugger (the marker file is missing, or Steam has not been restarted since it was
  created), the renderer never named (Steam is still coming up), and the crash watchdog having stopped the injection
  after two dead Steam starts, which names itself in the log and is lifted with `TENDER_INJECT=force`.
- **A card in the corner says Tender could not load its panel** — the bundle was served and did not mount. It names the
  log path; the reason it prints is the import's own. Its one button stops the injection for the life of this backend
  process and takes the card away — nothing is loaded again until `mise run dev` is started afresh.
- **Steam came back without the panel** — the injector loads the panel again by itself when Steam rebuilds its JS
  context. If it did not, `mise run dev:bpm-reset` or `mise run dev:desktop-reset` gives a context nobody has loaded
  anything into yet, and the backend still running from `dev` loads it. With no backend left running,
  `mise run dev:backend` puts one against that Steam without restarting it a second time.
- **A window opened on the wrong monitor** — run the reset for that window with the right display, e.g.
  `mise run dev:bpm-reset dp2`; it remembers the display, so the next `dev` uses it too. Placement picks the window out
  by caption and fullscreen state, among those already open and those that appear afterwards; check what the window
  manager actually saw with `journalctl --user -b | grep tender-dev-window`: the log lists every window's caption and
  output, and whether the move fired. The window stays a normal desktop window, so you can always drag it over yourself.
- **A "screen sharing" portal dialog pops up when Big Picture opens** — that's Steam's own desktop capture (Game
  Recording / Remote Play) asking through the xdg-desktop-portal, because there is no gamescope to capture in desktop
  mode. It is unrelated to this tooling. **Turn Steam's Game Recording off** (Steam → Settings → Game Recording) — you
  don't want Steam capturing your desktop mid-development anyway, and this removes the dialog for good. Ticking the
  portal's _"enable restore"_ box instead only hides the dialog: it pins whichever source you picked, so if you pick a
  single monitor the capture stays on it even after `dev:bpm <other-display>` opens Big Picture elsewhere.
- **The QAM Performance tab is non-functional in desktop BPM** (it needs gamescope). Irrelevant for this plugin's UI.
- **Re-verify the loop after a Steam update.** Nothing in this repo's toolchain runs against a real Steam, so a change
  to how Steam names its targets or builds its renderer is only ever found on a device.
- **Big Picture comes up at the wrong size** — a `dev:ui-scale` run was hard-killed and left Steam on a forced scale (it
  persists in `config.vdf`, filed under the display identity that run printed — a `…|||Windowed` one). Run
  `mise run dev:ui-scale auto`.
- **Big Picture is left windowed at 1280x800** — same cause: a hard-killed `dev:ui-scale` run never restored the window.
  Re-fullscreen it by hand; nothing else is broken.
- **Do a final Game Mode pass before a release.** `dev:ui-scale deck` reproduces Game Mode's CSS metrics exactly (and
  says so out loud when it fails to), so layout and overflow can be judged from the desktop — but controller focus
  behavior and gamescope rendering are still only real in Game Mode.
