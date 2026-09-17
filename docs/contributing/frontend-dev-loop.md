# Frontend dev loop

Iterate on the panel from Desktop Mode on the Steam Deck: edit code next to a windowed Big Picture window, with the
backend running beside it. Nothing is deployed anywhere and no plugin loader is involved.

## The loop

```bash
mise run dev         # build the panel, then run the backend in the foreground
mise run dev:bpm     # in another terminal: open windowed Big Picture, optionally on a chosen display
```

`mise run dev` builds the three bundles and starts the backend. The backend serves `dist/` on a loopback port and loads
the panel into Steam's renderer over the CEF debugger — see
[How the panel gets into Steam](../architecture/loading-the-panel.md). Ctrl-C stops it and lets it unload.

Two things have to be true for anything to be loaded: `~/.steam/steam/.cef-enable-remote-debugging` has to exist (create
the empty file and restart Steam if it does not), and Steam has to be running. If Steam is not running the backend waits
and attaches when it comes up, so the order of the two commands does not matter.

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

## Seeing a change

**There is no hot reload.** The injector loads the panel into a JS context once and knows it by a marker on the window;
a rebuilt bundle reaches Steam when that context is rebuilt, which is what wipes the marker.

```bash
mise run build           # rebuild the bundles (the backend can keep running)
mise run dev:bpm-reset   # restart Steam into a fresh renderer, on a chosen display
```

The backend serves whatever is in `dist/` at the moment the panel is imported, so a frontend change needs no backend
restart. A **backend** change does: Ctrl-C and `mise run dev` again.

Keyboard shortcuts in the BPM window: **Ctrl+2** opens the Quick Access Menu, **Ctrl+1** the main menu.

## Why the windowed Big Picture

Desktop Steam's Big Picture window runs the same gamepadui React app as Game Mode — same routes, same game-detail pages
— so what you see there is the panel's actual UI rather than an approximation. It is _not_ WYSIWYG about **vertical
space**: by default the windowed BPM gives the QAM panel far more height than the Deck does. See
[Display scale: the dev loop lies about height](#display-scale-the-dev-loop-lies-about-height).

`mise run dev:bpm [display]` opens and places that window without building or running anything.

### Choosing the display

The optional argument is matched against the **real outputs** of the machine — output naming varies between Decks and
docks (external outputs may be `DP-2`/`DP-3` rather than `DP-1`/`DP-2`), so nothing is hardcoded. List the selectable
targets with `scripts/dev_open_bpm.sh --list`: it prints a lowercase short form per connected **and enabled** output
(e.g. `edp1`, `dp2`, `dp3`), plus the `internal` alias while the built-in panel is enabled. Disabled outputs are neither
listed nor resolvable — KWin can't place a window on them. The raw `kscreen-doctor -o` names (e.g. `DP-2`) are accepted
as well — matching is case- and dash-insensitive, so `dp2`, `DP2` and `DP-2` all mean `DP-2`. The default `internal`
resolves to the built-in panel (`eDP-*`); a target that matches no enabled output is a hard error before anything opens.
On a docked Deck whose internal panel is disabled or disconnected, the default prints a warning and the BPM window
simply opens wherever the window manager puts it.

Placement itself is done by a short-lived KWin script loaded over DBus (`scripts/dev_open_bpm.sh`), which moves the Big
Picture window to the target output and unloads itself again — if KWin scripting is unavailable, the loop still works
and only the placement is skipped. The BPM window stays a normal desktop window: it can always be dragged elsewhere.

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
   [`dev_open_bpm.sh`](#choosing-the-display) uses for placement) un-fullscreens the Big Picture window and sizes it so
   its **client area is exactly 1280x800**, on whatever monitor it already sits on. `frameGeometry` includes the window
   decoration, so the frame is corrected by the measured frame-vs-client delta until the client area lands exactly —
   nothing about the decoration is hardcoded.
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
- **Big Picture reopened without the panel** — the injector loads the panel again by itself when Steam rebuilds its JS
  context. If it did not, `mise run dev:bpm-reset` gives a renderer nobody has loaded anything into yet. It takes the
  same optional display argument, e.g. `mise run dev:bpm-reset dp2`.
- **Big Picture opened on the wrong monitor** — placement matches the window by its title once it appears. Check what
  the window manager actually saw with `journalctl --user -b | grep decky-bpm`: the log lists every window's caption and
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
