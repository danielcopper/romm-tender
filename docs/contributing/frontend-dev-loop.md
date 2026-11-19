# Frontend dev loop

Iterate on the panel from Desktop Mode on the Steam Deck: edit code next to one of Steam's windows — windowed Big
Picture or the desktop client — with the backend running beside it. The tasks below run the panel and the backend
straight from your checkout, with no install and no plugin loader involved.

## The loop

```bash
mise run dev [--surface]                      # build, restart Steam into the remembered window, run the backend
mise run dev:bpm [display] [--surface]        # the same into windowed Big Picture on <display>, and remember that
mise run dev:desktop [display] [--surface]    # the same into the desktop client on <display>, and remember that
mise run dev:backend [--surface]              # build and run the backend against the Steam already running
mise run dev:frontend [display] [--surface]   # build and restart Steam; the running backend loads the panel
mise run dev:bpm-reset [display]              # second terminal, wrong display: only the restart, into Big Picture
mise run dev:desktop-reset [display]          # ...or into the desktop client
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

`--surface`, on any of the five tasks that build, builds the desktop surface into the panel —
[Desktop surface dev loop](#desktop-surface-dev-loop-dev-build-only).

`dev:backend` is the wrong task beside a panel that is already loaded: the injector refuses a context that carries its
marker, and the panel already in that context cannot reach the new backend, because every backend process mints its own
admission token. The new backend replaces that panel itself by having Steam reload its JS context
([a panel an earlier backend left behind](../architecture/loading-the-panel.md#a-panel-an-earlier-backend-left-behind)),
but only once no app is running, and the reload closes whatever is open in Steam just as a restart would. Those are the
same two facts that make the other tasks restart Steam — and what makes `dev:frontend` worth having, because the restart
is exactly what they call for.

The display argument is optional, and leaving it out means one thing on a task that names a window and another on
`dev:frontend`, which repeats one — see [Choosing the display](#choosing-the-display). Every one of these tasks but
`dev:backend` shuts the running Steam down first, so anything open in it closes; a Steam that is not running is simply
started. `dev:backend` restarts nothing itself, but beside a panel an earlier backend loaded, the backend it starts has
Steam reload its JS context once no app is running, which closes what is open too. A build that fails, or a display that
matches nothing, stops the task before Steam is touched.

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
`~/.steam/steam/.cef-enable-remote-debugging` to exist, and creates the empty file itself when it is missing — a start
by hand does too. Steam reads that file only when it starts, so the restart that follows is what picks it up; the
backend says so at WARNING when it has just created one.

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
  the marker. The tasks ask for that by restarting Steam. `TENDER_INJECT=force` does not get round it: that switch
  belongs to the crash watchdog, and the marker check does not read it.
- **A new backend strands the panel the old one loaded.** Every backend process makes its own admission token
  (`new_token` in `backend/host/access.py`), and the panel reads its address and token off the URL it was loaded from
  ([the token](../architecture/loading-the-panel.md#the-token)), so a restarted backend leaves the running panel holding
  a token nobody accepts. The backend replaces such a panel itself by having Steam reload its JS context — only when no
  app is running, one time per stranded panel, and no more than twice in ten minutes on the machine
  ([a panel an earlier backend left behind](../architecture/loading-the-panel.md#a-panel-an-earlier-backend-left-behind)).
  Every task but `dev:backend` restarts Steam rather than wait for that; `dev:backend` restarts nothing and leaves it to
  the backend it starts.
- **Both windows are one load.** The desktop client and Big Picture render from one shared JS context, and the backend
  loads the panel into it once. There is no loading into one window and not the other; the tasks differ only in which
  window you end up looking at.

A frontend change and a backend change are therefore the same step: Ctrl-C, and `mise run dev` again.

## Why the window is remembered rather than read

Steam does not come back in the window it was shut down from: a plain start after a shutdown opens the desktop client,
even when windowed Big Picture was open before. So the restart has to be told which window to open, and the running
Steam cannot answer all of it: which display a window sits on is KWin's to know, not Steam's. What Steam's main-UI mode
reads in each of its windows is recorded in
[Which window is Steam's main UI](../architecture/desktop-dom-architecture.md#which-window-is-steams-main-ui).
Remembering the last explicit choice needs no detection and comes out the same every time.

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

The windowed Big Picture renders Tender's real UI, but **not at the Deck's real size**. Measured on-device:

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
physical size and pushes it into each CEF browser view; it is the same value the user sees under **Settings › Display ›
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
user and ever have to use it, re-set your scale in Steam › Settings › Display afterwards.

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

## Desktop surface dev loop (dev build only)

The desktop surface (`frontend/src/desktop/`) adapts Steam's game page for a RomM shortcut in the desktop client's
library window. No shipped bundle carries it, and `build:dev`, which the tasks above build by default, leaves it out. It
reaches Steam only through its own build
([the desktop dev build](../architecture/frontend-bundles.md#the-desktop-dev-build-dev-build-only)), which every task
above that builds runs in place of `build:dev` when given `--surface`. So the loop is one command:

```bash
mise run dev:desktop [display] --surface
```

It builds the desktop dev bundle, restarts Steam into the desktop client and runs the backend, which loads it. The flag
is not remembered: **a task that builds, run without `--surface`, puts `build:dev`'s bundle back**, and with it Steam's
own game page. The two resets build nothing, so they leave whichever bundle is in `dist/`.

To inspect it, open a RomM shortcut in the desktop client's library, and select the desktop client window's target in
DevTools (below). What the watcher found and what failed is in the backend's log, on the lines starting `desktop:`: its
failures at any log level, the rest only with Tender's log level at Info or Debug.

## A remote target

`setup:remote-target` prepares another machine than the one you write code on — a Steam Deck beside a build host, say —
as a **target** with a name, for the dev tasks' `--target` ([below](#the-dev-tasks-on-a-target)). The target needs no
checkout and no toolchain; it is prepared once, from the build host:

```bash
mise run setup:remote-target <name> <destination>           # log in, install, and record the target here
mise run setup:remote-target <name> <destination> --print   # print a script to run on the target instead
```

`<destination>` is anything your own `ssh` reaches: `deck@192.168.1.20`, or an alias from your `~/.ssh/config`, which
the setup reads with `ssh -G` and never writes. `<name>` is yours to choose — letters, digits, `.`, `_` and `-`.

### What the setup does

1. **It checks the login before it writes anything**, here or on the target. Where the build host already holds the dev
   loop's key, it logs in with that key alone first, asking for no password and reading none of your SSH config. It logs
   in through your own `ssh` instead — with whatever access that already has: a password, or a key from your agent or
   your SSH config — when there is no key yet, when the target refuses the key, when the target takes the key but its
   dispatcher runs no install (gone, or older than the setup request), and when a jump host on the way refuses the key's
   login, since your own config may pass it.
2. **It creates the build host's key for targets** if there is none: `~/.config/romm-tender-dev/id_ed25519`. One key
   serves every target.
3. **It records the target's host key** in the dev loop's own known-hosts file. At first contact `ssh` shows the
   fingerprint and asks, as it always does — even for a machine your own `~/.ssh/known_hosts` already knows, because
   that file is not the one the dev loop's entries name. The record is filed under the target's name
   (`romm-tender-dev.<name>`) rather than its address, so a target at a new address keeps it. Your own login sees the
   target by its address, and so do its prompts — `deck@192.168.1.20's password` — and a key already filed under the
   name holds for it too.
4. **It installs on the target**, under `~/.local/lib/romm-tender-dev/`: `current/` (the running tree), `incoming/` (the
   copy the build host sends), and `dispatch.sh` beside them, where no copy reaches it. It rebuilds
   `~/.ssh/authorized_keys` with one line for the key, a comment above it naming the build host and the time, and every
   other line as it was; a rebuilt file that came out short, or without the new line last, is not put in place. A
   symlinked `authorized_keys` is written through: the file it names is replaced, and the link stays; a link into a
   place that cannot be written stops the setup with the line to add there through whatever manages it. The install is
   the script `scripts/dev_target_install.sh`, sent with its values filled in.
5. **It writes the target into the dev loop's own SSH config**, `~/.config/romm-tender-dev/ssh_config`: the address,
   port, user and jump host or proxy command the destination resolved to, the dev loop's key and nothing else, and its
   known-hosts file. Your `~/.ssh/config` serves only the setup's `ssh -G` and its login through your own `ssh`; the
   entry is written to be read with this file alone, and so with the restricted key alone. A jump host is written out as
   `user@address:port`, since an alias from your config means nothing in this one; a proxy command goes in as `ssh -G`
   prints it.
6. **It checks that the dev loop can reach the target** with that file alone: one more login, through the entry just
   written and the dev loop's key, which sends an empty setup request. Its leg to the target runs in batch mode and asks
   for nothing, the target's host key being recorded by then; a jump host's leg reads that file too, but no entry the
   setup writes names it, and the batch mode given on the command line reaches only the target's leg, so it may still
   ask, about its host key among others. Only when the login gets in does the setup end as done. Otherwise the target is
   set up all the same, and so is its entry, but the setup ends saying the dev loop cannot reach it with its own config,
   with what `ssh` said and that the dev loop's config carries none of yours — a jump host your own config lets past,
   with settings of its own there, is the likely case.

Run again, it changes what changed: one key, one `authorized_keys` line and one config entry, however often it runs.
That is also how a target is repaired. While the build host's address is the one the line names, the repair needs no
password: the dev loop's key logs in, and the dispatcher's setup request runs the same install. After the build host's
address changed, the target refuses the key, and the repair needs a login of your own — or, on a target without one,
`--print`.

### What the key may do on the target

The `authorized_keys` line restricts the key three ways:

- **`from=`** — to the build host's address **as the target sees it**: the one `$SSH_CLIENT` shows in the setup login,
  which behind a jump host is the jump host's. This is the bound that holds: the key may copy a tree in and run a script
  out of it, so a copy of the key used from anywhere else would run code on the target. When the build host's address
  changes, the target refuses the key — run the setup again.
- **`command=`** — to the dispatcher, which runs instead of whatever the key asks for and decides from that request
  alone. It lets four kinds through: the setup's install script; `rsync` receiving into `incoming/` and nowhere else;
  the incoming copy's `scripts/dev_target.sh`; and reads of the backend's log — once, or followed — and of its database.
  Everything else, a login among it, is refused; the exact requests are in the header of `scripts/dev_dispatch.sh`. The
  setup request puts the dispatcher itself within the key's reach, which grants nothing the key lacks: `dev_target.sh`
  already runs whatever tree the key copied in, so `from=` is the bound either way.
- **`restrict`** — no terminal, no forwarding of any kind.

### A target you cannot log into yet: `--print`

A target with no password login and no key of yours on it cannot be logged into, so `--print` contacts nothing and
prints a script instead:

```bash
mise run setup:remote-target deck deck@192.168.1.20 --print > setup-deck.sh
```

Copy it to the target by any means and run it there with `sh setup-deck.sh`. It does the same install, and shows the
target's host key fingerprints. By default it restricts the key to the address the build host sends from towards the
target (`ip route get`), written into the script, and the script says which before it writes anything; if the target
sees the build host at another address — through NAT, say — give that one instead: `sh setup-deck.sh 10.8.0.6`. Then run
the setup on the build host again, without `--print`: it logs in with the key, `ssh` shows the fingerprint for you to
compare with the script's, and the setup installs through the key once more, records the target and finishes. If the key
is refused at that point, the message names the script's default address as the likely cause.

`--print` refuses a destination behind a jump host or a proxy command: the target would see that hop's address, which
the build host cannot know.

### When the setup refuses

Four ways the login can fail end the setup with what to do next, and **nothing changed, here or on the target**:

| `ssh` reports                   | What to do                                                                                   |
| ------------------------------- | -------------------------------------------------------------------------------------------- |
| a name it cannot resolve        | give the target's address, or a name the network resolves                                    |
| no SSH server answering         | check the address, and that the target runs an SSH server                                    |
| no login accepted               | `--print`, as above — behind a jump host or proxy command, a login of yours on the target    |
| a host key not the recorded one | another machine may answer at that address; if the target was reinstalled, remove its record |

When `ssh` names a jump host on the way as the one that failed — a name this machine cannot resolve, a host key your own
files record differently, a login it refused, no server answering — the message names the hop, and the setup leaves your
files alone; where `ssh` names no host, the message names the target or the hop. A host key you decline, or one `ssh`
cannot ask about without a terminal, ends the setup the same way. A login that runs no install at all ends it too, and
there the message gives what `ssh` said; when the target took the dev loop's key and its forced command failed, the
message says so as well.

A failure once the install has started is the target's, not the login's. Where the install refuses to go on, the setup
gives its reason and this machine is unchanged; running it again as it stands changes nothing, so the reason says what
to do. The install refuses on three things:

- no address it can restrict the key to;
- a rebuilt `authorized_keys` that did not come out whole;
- a symlinked `authorized_keys` that cannot be resolved, or that resolves into a place that cannot be written — the
  message gives the line to add there through whatever manages it.

Anything else that stops the install is no fault of its own: it builds its files first and moves them into place last,
so the setup says it stopped part-way, this machine is unchanged, and running it again finishes it. A failure writing
this machine's side after the target is set up is named with its file, and running the setup again finishes that too.

A target whose address changed is set up again with the new one, and needs no password for it: the key still holds,
because `from=` bounds the build host's address, not the target's.

### The dev tasks on a target

Every dev task but `dev:fake-github` takes `--target <name>`, a name `setup:remote-target` recorded. What is built is
built here; Steam and the backend are the target's:

```bash
mise run dev --target <name> [--surface]                      # build here, restart Steam there, run the backend there
mise run dev:bpm [display] --target <name> [--surface]        # ...into windowed Big Picture on the target's <display>
mise run dev:desktop [display] --target <name> [--surface]    # ...into the desktop client
mise run dev:backend --target <name> [--surface]              # build here, run the backend there, restart nothing
mise run dev:frontend [display] --target <name> [--surface]   # build here, restart Steam there; its backend loads it
mise run dev:bpm-reset [display] --target <name>              # only the restart, there
mise run dev:desktop-reset [display] --target <name>
mise run dev:ui-scale [mode] --target <name>                  # emulate Game Mode's metrics there
mise run dev:ui-scale --stop --target <name>                  # stop one whose terminal is gone
mise run dev:stop --target <name>                             # stop the backend there, or the installed service
mise run dev:log --target <name> [--follow]                   # its backend.log, once or followed
mise run dev:status --target <name>                           # who holds the lock there, and whether dev:ui-scale runs
```

Without `--target`, every task does what the sections above describe and nothing else. With it, the task hands over to
`scripts/dev_target.py`, which works in this order:

1. **It finds the target** in `~/.config/romm-tender-dev/ssh_config`, and refuses before anything else when there is no
   such target.
2. **It builds here**, for the five tasks that build: `build:dev`, or `build:desktop` with `--surface`, source maps
   kept.
3. **It copies the tree to the target's `incoming/`**, for every task but `dev:log`: the paths a release ships —
   `SHIPPED` in `scripts/package.sh`, read from that list — plus `scripts/`, without this machine's compiled modules.
   The copy is a mirror that `rsync` keeps up to date, so only what changed travels. It lands beside the running tree,
   `current/`, not over it, and a task that builds nothing leaves `current/` alone. A task that builds copies the tree
   whole: a path that list names and the checkout lacks stops it before the copy, as it stops `scripts/package.sh`. The
   others copy what there is, so a checkout never built can still stop or ask a target. The lock is asked only after the
   build and the copy, by the copy's own scripts, so a refusal costs both; asking it earlier would have to go through
   the previous copy's scripts, and a first run has none.
4. **It runs the task on the target**, through `scripts/dev_target.sh` from that copy. The tasks that start a backend
   ask the single-instance lock first and refuse as they do locally — `dev:frontend` the other way round, and stricter
   than locally (below) — and only once the answer lets them on does the copy replace `current/`. A lock that cannot be
   read refuses too, where locally the task carries on, and so does a `romm-tender-dev` unit that runs without holding
   the lock: `dev:stop` stops it. Then Steam restarts through `scripts/dev_steam.sh`, as it does locally, and the
   backend starts as the user unit `romm-tender-dev`, so it outlives the connection. It runs on the Python the installed
   service runs on, read from that unit's `ExecStart` — `/usr/bin/python3` where no service is installed — and is
   refused below 3.12. `TENDER_PYTHON` is not read there: a login over `ssh` carries none of the installer's
   environment. The display a task names is one of the target's, and the
   [remembered window](#why-the-window-is-remembered-rather-than-read) is the target's memory file.
5. **It follows what it started** — the backend's log, or what `dev:ui-scale` prints — until Ctrl-C, which stops it
   there, as Ctrl-C stops it locally (below).

On the target everything runs in the user manager's environment: the units do by being units, and the steps between take
the display and the session bus from it, since a login over `ssh` carries neither. It needs nothing beyond `/usr/bin`. A
user manager that cannot be asked — no session of the user running there — stops the task before it starts anything or
replaces the running tree; lost after a start, the message names what was started and how to stop it. One that names no
display gets a warning: placing Steam's window and `dev:ui-scale` need one. The hints the scripts there give name the
task to run here, with `--target <name>`.

**The tasks that start a backend follow its `backend.log` until Ctrl-C.** The log shows from where it stood before the
start, so a start that fails is seen: when the backend stops, the following ends and shows what the backend wrote to
stderr since it started — an import that failed before the log was opened among it. **Ctrl-C ends the following and
stops the dev backend there**, as it ends the backend locally: the task catches it here, since a Ctrl-C does not reach
the target, and stops the `romm-tender-dev` unit — never the installed service — and says so. A stop that cannot be
sent, the connection gone, says the backend may still run and names `dev:stop`; a second Ctrl-C abandons the stop and
says the same. A following that ends without a Ctrl-C here — a connection lost, a terminal closed — leaves the backend
running, and the task says so and names `dev:stop`.

**`dev:stop` stops whichever holds the lock on the target**: the dev backend, or the installed service. It is for a dev
backend whose following ended without a Ctrl-C, and for the installed service. It leaves Steam as it is — the next task
that starts a backend restarts it, or has it reload its JS context, anyway — and leaves the installed service enabled,
so it starts again at the next login; the message names `systemctl --user start romm-tender` to start it now. A lock
held by any other process is named and left alone. When the installed service holds the lock, a task that starts a
backend refuses with `mise run dev:stop --target <name>` in its message.

**`dev:frontend --target` needs the dev backend to hold the lock**, not just any backend: on a target the installed
service is the likely holder, and it loads the panel it was installed with, never the copy. So beside the installed
service it refuses and names `mise run dev:stop --target <name>`, then `mise run dev:backend --target <name>`; beside
any other process it names that process, to be stopped by hand. Without `--target` it refuses only when nothing holds
the lock, as above.

**`dev:ui-scale` runs on the target as the user unit `romm-tender-dev-ui-scale`**, and the task follows what it prints
until the unit stops, which the task then says, or until Ctrl-C, which stops the unit there as Ctrl-C does locally: it
restores the scale and the window, and the task shows what it said doing so. `dev:ui-scale --stop --target <name>` does
the same for one whose terminal is gone. Either way the task waits for the restore's last line, which says whether the
scale and the window were put back and read back as they were before; a restore that did not land, could not be read
back, or ends without saying — or a stop that failed — ends the task with 1 and says so. A stop that cannot be sent
after a Ctrl-C names `dev:ui-scale --stop`. `dev:stop` leaves it running. `auto`, the rescue, runs once and ends, as it
does locally.

**`dev:log`** shows the target's `backend.log` once; with `--follow` it shows its end and follows it, by name, so a log
rotated away or not written yet is picked up. **`dev:status`** says what holds the lock — the dev backend, the installed
service, another process, or nothing — whether `romm-tender-dev` runs without holding it, and whether a `dev:ui-scale`
runs.

The calls run `ssh` and `rsync` with the dev loop's config alone. The target's leg asks nothing; a jump host on the way
may ask as your own `ssh` would, about its host key or a key's passphrase, and the task waits for the answer. A target
whose dispatcher is older than the checkout — one that refuses `dev:log --follow`, say — is brought up to date by
running `setup:remote-target` again.

**A lost connection ends the call within a minute.** Every call gives up on a target it cannot reach within 10 seconds,
and on one that stops answering for 45 — a Deck gone to sleep, a network gone. Before the copy the task says
`copying to <name> …`, so a wait there is not a silent one. A connection lost while a task follows ends the following
with `the connection to <name> ended while following`; what the task started there may still run, and `dev:status` says
whether it does.

#### When a target cannot be used

A task that cannot reach the target says why and what to do, and nothing is done on the target:

| What happened                   | What to do                                                                                     |
| ------------------------------- | ---------------------------------------------------------------------------------------------- |
| no such target                  | `mise run setup:remote-target <name> <destination>`                                            |
| no answer at its address        | it may have a new one: run the setup with it                                                   |
| a host key not the recorded one | another machine answers there: no connection; reinstalled, remove its record and run the setup |
| the build host's key refused    | this machine's address may have changed: run the setup again                                   |

No such target is found before the build; the other three at the first connection — the copy, or for `dev:log` the read.

A Ctrl-C before a task follows anything, or a connection lost then, ends this side, and with it the far side's output:
`ssh` gives it no terminal, so what the task was doing there stops at its next write, possibly part-way — a Steam
restart among it. For a task that changes the target the message says so: `dev:status` says what holds the lock, and
Steam there is to be checked by eye; `dev:status` and `dev:log` change nothing, and say only that they were interrupted
or lost the connection. A Ctrl-C during the build or the copy says only `interrupted`: nothing on the target but
`incoming/` was touched by then. A connection lost after the task there has printed output or one of its `tender-dev:`
messages, or one `ssh` reports in words only a lost login gives, is not read as a target that did not answer.

## DevTools

With `~/.steam/steam/.cef-enable-remote-debugging` present, Steam exposes the CEF DevTools protocol on
<http://localhost:8080>:

- The **SharedJSContext** target is where all the panel's code runs — console output and JS debugging live here.
- The **Steam Big Picture Mode** target is the rendered UI — element inspection and live CSS editing. For the desktop
  surface (dev build only), select the desktop client window's target instead.

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
  mode. It is unrelated to this tooling. **Turn Steam's Game Recording off** (Steam › Settings › Game Recording) — you
  don't want Steam capturing your desktop mid-development anyway, and this removes the dialog for good. Ticking the
  portal's _"enable restore"_ box instead only hides the dialog: it pins whichever source you picked, so if you pick a
  single monitor the capture stays on it even after `dev:bpm <other-display>` opens Big Picture elsewhere.
- **The QAM Performance tab is non-functional in desktop BPM** (it needs gamescope). Irrelevant for Tender's UI.
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
