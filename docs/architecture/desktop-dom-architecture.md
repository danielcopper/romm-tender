# How the desktop surface adapts Steam's game page

How the desktop surface — the surface Tender draws into Steam's desktop client — adapts Steam's game page for a RomM
shortcut, and gives every change back when the page leaves it.

**Dev build only.** Nothing on this page is in a shipped bundle. The surface reaches Steam only through
`pnpm -C frontend build:desktop` ([the desktop dev build](frontend-bundles.md#the-desktop-dev-build-dev-build-only)).

The navigation watcher (`frontend/src/desktop/navigationWatcher.ts`) does the work on this page. It draws what it is
handed — a `DesktopGamePage` with an optional `gameView` and `playButton` — and `startDesktopSurface`
(`frontend/src/desktop/index.ts`) is the one place that hands it anything. A part it is handed replaces Steam's own:
`gameView` the page's content sections, `playButton` Steam's Play button and the badges beside it. A part left out
leaves Steam's own in place, and the play bar, the hero and the controls beside them are adapted either way.

It hands both. `GameView` (`frontend/src/desktop/gameview/GameView.tsx`) draws the page's cards in two columns. Its one
card so far is About — the cover Steam shows (`steamsCover`, `frontend/src/utils/artworkThumbs.ts`), the title, the
summary and the rows `metadataRows` answers, under the labels and in the order Big Picture's GAME INFO tab shows them.
While it is drawn it holds two things `RomMPlaySection` holds on Big Picture's game page: the connection heartbeat, and
the automatic artwork, whose apply in flight it cancels on leaving.

While a RetroDECK migration is pending it draws the migration card
(`frontend/src/desktop/gameview/MigrationBlockedCard.tsx`) in place of the columns, as the game page's section does on
Big Picture (`RomMGameInfoPanel`), and goes on holding the heartbeat and the automatic artwork behind it. The card's
sentence names Big Picture as where the Tender menu is, because the desktop client has no Quick Access menu. It asks the
backend for the migration state again for each game page it is drawn for (`useMigrationRefresh`,
`frontend/src/utils/migrationRefresh.ts`).

`PlayButton` (`frontend/src/desktop/gameview/PlayButton.tsx`) is Tender's Play button in the desktop client. Its presses
(`frontend/src/desktop/gameview/usePlayLaunch.ts`) are those of Big Picture's `CustomPlayButton`
(`frontend/src/bigpicture/CustomPlayButton.tsx`): PLAY starts the game through the launch gate
([Pre-launch sync](save-file-sync-architecture.md#pre-launch-sync)), and Stop and RESOLVE CONFLICT do what they do
there. It has no RESUME: while the game runs, whatever started it, the button is STOP, as Steam's own Play button in the
desktop client is, and bringing the game's window to the front is the window manager's. It asks its questions in the
desktop dialogs, which `useDialogHost` (`frontend/src/desktop/gameview/dialogs/useDialogHost.tsx`) draws into the body
of the document the button is drawn in. Its save-tracking step is the one the
[Pre-launch sync](save-file-sync-architecture.md#pre-launch-sync) table names for it. Where the game cannot be played
from the page — not downloaded, downloading, or its downloaded file gone — it draws a disabled button that says which,
and where the game cannot start — its emulator cannot, or its file does not start with it — PLAY is disabled with the
sentence `emulatorStartRefusal` (`frontend/src/utils/emulatorStart.ts`) or `launchRefusal`
(`frontend/src/utils/gameLaunch.ts`) answers. While the page checks whether the game can start, and where that check
went wrong, PLAY does what Big Picture's does
([Launch-target validation](backend-architecture.md#launch-target-validation)), and reads CHECKING... where Big
Picture's shows its throbber. On a stranded panel it gates as the [invariant register](invariants.md)'s stranded-panel
entry says. Steam's Play button and the badges beside it are hidden as soon as the button's root has rendered, also
while it draws nothing — before the page's detail names the ROM, and on a stranded panel — and the button draws no
badges in their place.

## Two windows, two realms

The panel runs in Steam's **SharedJSContext**, where the backend loads it
([loading the panel](loading-the-panel.md#which-bundles-and-the-rule-that-cannot-bend)), and the navigation watcher runs
there with it. The page it adapts is in another window: the popup that `g_PopupManager.GetPopups()` lists under a name
starting with `SP Desktop` (`findDesktopWindow`, `frontend/src/desktop/desktopWindow.ts`). The watcher looks for that
window when it starts, on a poll, and whenever Steam reports a popup created or destroyed; it detaches from a window
that closed or was replaced, and attaches to the new one. A window it could not attach to is not tried again.

That window's document is a realm of its own, as the Quick Access view's is. What that means for `instanceof` and for a
constructor such as `MutationObserver` or `ResizeObserver` is the trap in `CLAUDE.md`, "`instanceof` against a DOM
global is false in QAM and desktop-client code".

### Which window is Steam's main UI

Measured in Steam's `SharedJSContext` on a Steam Deck in Desktop Mode. The Big Picture reading (4, no `SP Desktop`
popup) was taken again on a Steam Deck running Steam client build 1792618285.

- `SteamUIStore.MainInstanceUIMode` reads **7** while the desktop client is the main window (`SP Desktop_uid0`) and
  **4** while Big Picture is (`SP BPM_uid0`), and reads 7 again after leaving Big Picture.
- Steam **replaces** the main window rather than layering one over the other: while Big Picture was open, no
  `SP Desktop` popup was in `g_PopupManager.GetPopups()`, and after returning a **new** `SP Desktop_uid0` window
  existed. A reference to the desktop window does not outlive a trip through Big Picture, which is why the navigation
  watcher re-finds it.
- With the desktop client closed to the tray, the mode stays 7, the `SP Desktop_uid0` popup still exists,
  `MainWindowVisible` is true and the window's `document.hidden` is false. From JavaScript, a window hidden in the tray
  cannot be told apart from an open one.
- Game Mode was not measured. Tender reads every mode other than 7, and an unreadable `SteamUIStore`, as the desktop
  client not being the main UI.

## Dialogs for a start the launch watcher catches

A start Tender's Play button did not make — Steam's own Play, a `steam://rungameid` link — is caught by the launch
watcher (`frontend/src/utils/launchInterceptor.ts`), which may have questions to ask before the game starts. Which
surface draws them is decided by `launchPromptsForThisStart` (`frontend/src/utils/launchPromptRouter.ts`): the desktop
dialogs while `MainInstanceUIMode` is 7 and the desktop surface has offered them and has an open window, and Steam's
gamepad modals otherwise. The desktop surface offers them from `startDesktopSurface` and withdraws them from
`stopDesktopSurface` (`frontend/src/desktop/index.ts`), so a bundle without the desktop surface — every shipped one —
always asks through the gamepad modals.

Such a start comes with no Tender component to ask from, so `askInDesktopWindow`
(`frontend/src/desktop/launchPromptHost.tsx`) draws each question into the body of the desktop window that exists at the
moment it is asked: a container created with that window's own `document.createElement`, and a React root from
`findReactClient`. Each question keeps a `DomRestorationLedger` of its own, which records the root, its container and
the window's `pagehide` and `unload` listeners, and `restoreAll()` takes all of it away when the question settles. A
question settles with the button pressed, or with its dismissed answer: unlogged when the dialog is left
(`DesktopDialog`'s `onDismiss`), and logged as a `desktop:` warning or error naming why when its window goes or unloads,
when the surface is withdrawn, when there is no open desktop window or `createRoot` to draw with, or when looking for
them or drawing it throws. A dismissal the host makes itself does not wait for a request the dialog has in flight: when
the save-conflict dialog's window goes, or the surface is withdrawn, while its resolution is running, the start is
cancelled, the resolution still lands and shows its toast, and its answer, arriving after the question settled, is
logged as a `desktop:` warning and dropped, so the announcement a start makes once its conflicts are resolved
(`announceSaveSync`) is not made. Two starts caught together each draw a dialog of their own, one over the other, and
Escape then dismisses both.

**Known limitation:** because a tray-hidden desktop window looks open, a question for a start caught while the desktop
client sits in the tray is drawn into a window nobody sees until Steam is opened again, and the start waits for it.

## Fiber, read-only (`frontend/src/desktop/watcher/fiberInspector.ts`)

Where the DOM does not say which element is which, the watcher reads React's Fiber: the `__reactFiber$` (or
`__reactInternalInstance$`) property React attaches to a DOM element. From it, it reads a component's name (its
`displayName` or `type.name`, walking up past HTML tags to the nearest component), and an appId from the nearest
ancestor's props — `appId`, `overview.appid` or `details.appid` — when the route (`/library/app/<appId>` in
`MainWindowBrowserManager.m_lastLocation.pathname`) yields none.

It reads Fiber and never writes it, because the desktop client offers no seam to write through: a patch on a context
menu's `menuContent.type` fires zero times, since each open builds a fresh element, and the desktop route passes no
`renderFunc` to patch (#1866). The types it reads Fiber through are `readonly`, so the compiler refuses a write.

## The restoration ledger (`frontend/src/desktop/watcher/restorationLedger.ts`)

Every change the navigation watcher makes to Steam's page goes through one `DomRestorationLedger`, so that leaving the
page gives all of it back:

- **Styles:** `ledger.style(el, property, value)` records the element's own inline value of that property, named in its
  CSS spelling, before the first change; later changes keep that first record.
- **Hiding:** `ledger.hide(el)` records the element's inline `display` before the first hide and sets it to `none`; a
  later hide keeps the first record.
- **React roots:** `ledger.recordRoot(root, host)` records a mounted root with the host element Tender created for it,
  and `ledger.releaseRoot(host)` unmounts one root and removes its host.
- **Listeners:** `ledger.addListener(...)` attaches a listener and records it.

`ledger.restoreAll()` detaches the listeners, unmounts the roots and removes their hosts, then restores the recorded
displays and styles, in that order; a step that throws is logged and the rest still run. It runs when the page leaves
RomM shortcuts or moves to another RomM game — whenever the ledger holds anything, not only once a page was adapted, so
a pass that threw partway is given back too — when a pass tears down a stale `GameView` (below), when a view fails to
mount or fails after it was drawn, and when the watcher detaches from the window. A callback registered with Steam can
still fire after that, and the ledger cannot cancel what it never recorded: the watcher's popup callbacks, a view's
render callback and the sticky play bar controller's updates each do nothing once the teardown before them has run. The
controller's own set-up is no exception: when its first update throws, it disconnects its `ResizeObserver` before the
throw reaches the pass.

A launch question keeps a ledger of its own, whose changes are given back when the question settles ("Dialogs for a
start the launch watcher catches").

A test holds the whole rule for the navigation watcher: it records the `style` attribute of every element on a page,
runs passes from Steam's mutations and from a change to the set of RomM shortcuts, re-pins the play bar on a scroll and
a resize, leaves the page, and requires every attribute to be what it was
(`frontend/src/desktop/navigationWatcher.test.ts`, "every change it makes is given back"). That a pass which threw
partway is given back is held by its own test, "gives back what a pass changed before it threw".

## The selector ladder (`frontend/src/desktop/watcher/elementSelectors.ts`)

A class name in Steam's DOM is a webpack hash that a Steam update may change, so no selector names one, and ESLint fails
a string or regex literal under `frontend/src/desktop/` that holds a token shaped like one
(`frontend/eslint.config.js`). The finders resolve through these rungs instead:

| Rung         | What it matches                                                                                          | Example                                                                   |
| :----------- | :------------------------------------------------------------------------------------------------------- | :------------------------------------------------------------------------ |
| module class | The exact class `@decky/ui` resolved from Steam's own modules (`frontend/src/utils/deckyUiInternals.ts`) | `appDetailsClasses.PlayBar`, `appActionButtonClasses.PlayButtonContainer` |
| class name   | The readable, PascalCase part of a module class name, where the class carries one                        | `[class*="PlayBar"]`, `[class*="AppDetailsOverviewPanel"]`                |
| Fiber name   | The nearest component name on the element's Fiber                                                        | the nearest name is `PlayButton` or `AppActionButton`                     |
| structure    | Known parent and child relationships                                                                     | The nearest common ancestor of the play bar and the content sections      |

What is fixed is the two ends: the module class is asked first, and the structural walk last. Between them a finder asks
the rungs it has, in its own order, and not every finder has all four. Three do not stop at the first answer:
`findSteamPlaySection` asks the Play button's ladder before its own and climbs from the button it finds, and
`findSteamContentSections` and `findSteamPlayBarBadges` collect from every rung. The name the watcher logs for a rung is
the one in the table (`Rung`).

The module class is not one rung among equals. The class-name rung needs a readable part, and not every class has one:
on a German-language desktop client, as the maintainer measured it (#2389), the Play button's and the play bar's classes
are two hashes and `Panel`, and the module class is the only rung that finds either. Text and ARIA labels are no rung at
all, because they read differently in every language.
`frontend/src/desktop/watcher/elementSelectors.moduleClasses.test.ts` holds the first rung against made-up hashed class
maps, since the rest of the suite runs with those maps undefined.

## Drawing a view

A part is drawn in a React root of its own, in a host element Tender creates beside Steam's: the `playButton` before
Steam's Play button, the `gameView` right after the play bar. `createRoot` comes from Steam's module registry
(`findReactClient`, `frontend/src/desktop/desktopWindow.ts`). The watcher looks it up the first time it has a view to
mount and keeps the answer, a miss included, for as long as it is attached to that window; with nothing handed to it, it
never asks.

Steam's own part is hidden only once React has committed the view into its host, so a view that fails to draw leaves
Steam's in place. A mount that fails — `createRoot` or `render` throwing, or the view throwing as it renders — is logged
and remembered for that appId, and not tried again, and the next pass gives Steam's page back first: an earlier mount of
the same part, before Steam moved its Play button or the view broke, may have hidden Steam's, and it returns. Steam's
part stays from then on.

## The pass order

Steam renders the game page in more than one go: the hero's blurred background canvases, the play bar's badges and
Steam's duplicate sticky header arrive after the page's first render. An adaptation made only once, when the page is
first seen, misses all of them. Until the canvases arrive, `findInflatedHeroWrapper` — which recognises the hero wrapper
by the canvases inflating its `scrollHeight` — finds nothing, and the watcher keeps `overflow: visible` on what
`findHeroWrapperFallback` answers instead: the panel's first element with `Hero` in its class name, else the panel's
first child unless that child holds the play bar.

So each pass (`pass()` in `frontend/src/desktop/navigationWatcher.ts`) runs in this order:

1. **Another game's page is a page left.** A pass on a different appId than the one before it gives Steam's page back
   first, as leaving RomM shortcuts would. Where Steam keeps the same Play button and badges across the switch, what was
   hidden for the last game would otherwise stay hidden when nothing is drawn for this one.
2. **A stale `GameView` is torn down.** One carrying another appId, or no longer in the insertion container, is
   unmounted with everything the ledger holds. Both of these come before the adaptations because they restore everything
   the ledger holds: after them, they would undo them all.
3. **The adaptations, on every pass**, in this order: hide the page's content sections (once a `gameView` is drawn);
   mount `playButton` beside Steam's Play button, and hide Steam's once it is drawn — remounted only when it is missing,
   detached, for another appId or moved; set up or refresh the sticky play bar controller, which also keeps the hero
   wrapper at `overflow: visible` (below); hide Steam's duplicate sticky header; hide the play bar's badges (once a
   `playButton` is drawn); push the right-hand controls to the right edge.
4. **The mount guard.** If a `GameView` for this appId is already in place, or failed for it before, the pass ends here.
   Otherwise it mounts one right after the play bar.

The adaptations run on every pass, before the mount guard, because Steam adds the canvases after `GameView` has mounted
— a pass that returned at the guard would skip them.

A pass runs in the desktop window's next animation frame, and every trigger that arrives before that frame shares it: at
most one pass a frame. Passes are triggered by:

- **A `MutationObserver`** on the desktop window's `body`, for elements added or removed anywhere in its subtree.
- **A change to the set of RomM shortcuts** (`onRomMAppIdsChanged`, `frontend/src/utils/rommAppIds.ts`), so a page
  opened before its shortcut was registered is adapted once it is.
- **A view's first render**, which is what hides the Steam part it replaces, and **a view's failure**, which gives that
  part back (below, "Drawing a view").
- **Attaching to a window**, for the page already open in it.

The sticky play bar controller (`frontend/src/desktop/watcher/stickyPlayBarController.ts`) also updates on scroll, on
the window's resize, and on a `ResizeObserver` over the play bar, the scroller, the content container and the parallax
layer — the sizes `boundHeroOverflow`'s margin is computed from (below).

Neither sets a timer. A timer catches only a change that adds or removes no element. On a Steam Deck's desktop client
(2026-10-14, #2389), the watcher's interval and settle timeouts ran some 800 passes of their own and changed nothing;
the sticky play bar controller's settle timeouts changed one thing, the parallax layer's clip margin, which follows
sizes rather than elements. So the `ResizeObserver` watches those sizes in their place, and with it watching them,
checks one and three seconds after each play bar was set up found nothing left to change.

## What it logs

The watcher writes to the backend's log, each line starting `desktop:`: its failures as errors and warnings, which the
default log level keeps, and its transitions as info, which only Tender's log level at Info or Debug keeps. It logs each
transition once: attaching to and detaching from the desktop client's window, adapting a RomM game page — with the rung
that found the panel, the play bar and the Play button — and giving the page back. It logs every failure with what it
was doing: a window it could not attach to, a pass that threw (once for each path and error), a view that could not be
drawn, a restore step that threw, and `createRoot` missing from Steam's modules.

A RomM game page the finders never recognised is a warning too, logged once when the page is left and naming what was
not found, the overview panel or the play section, and how long the page was open. It is the line to look for after a
Steam update. It is not logged when a pass misses the page, because a pass that runs before Steam has drawn the page
misses it too, and that miss is no failure. A page left before Steam drew it, as when clicking quickly through the
library, was never recognised either, so it is reported too; the time it was open tells the two apart, since a page the
finders cannot read goes unrecognised for as long as it is open.

## The hero's parallax

Steam's game page scrolls its hero artwork more slowly than the page, behind the play bar and the cards, with CSS 3D
transforms:

- The scroller sets a `perspective`.
- The hero wrapper and the elements between it and the artwork keep `transform-style: preserve-3d` and
  `overflow: visible`.
- The artwork's own layer — the **parallax layer** — is pushed back along the z axis and scaled up to its visual size
  again (a `matrix3d(...)` transform), which slows its scroll. It sits on a negative `z-index`, which paints it beneath
  the page's other content.

The sticky play bar controller keeps the play bar see-through while it scrolls with the page, so the artwork shows
through it, and solid once it is pinned to the top of the scroller (`isPlayBarPinned`); its colours are in
`frontend/src/desktop/gameview/styles.ts`.

**The hero wrapper stays `overflow: visible`.** Clipping it, or any element in the `preserve-3d` chain, flattens the
chain: the artwork stops scrolling more slowly than the page, and is cut off at the wrapper's height instead of running
on behind the play bar and the cards. The sticky play bar controller sets `overflow: visible` on the wrapper on every
pinning update, which every pass runs, through the ledger.

### Bounding the artwork (`boundHeroOverflow`)

The parallax layer is much shorter than what it holds: the image, then a canvas Steam flips vertically to continue the
blurred background below it. The scroller counts that overflow towards its scrollable height, so with nothing else done
the page scrolls on past its last card into empty space.

`boundHeroOverflow` (`frontend/src/desktop/watcher/stickyPlayBarController.ts`) closes that gap lower in the chain, on
the parallax layer itself. The layer is the end of the 3D chain and already `transform-style: flat`, so clipping it
flattens nothing above it:

- **`overflow: clip`** on the layer, with an **`overflow-clip-margin`** that lets the artwork run on exactly as far as
  the page does: to the content's bottom, or to the scroller's own height on a page shorter than the window. How the
  margin is computed is the function's docstring.
- **A fade** over the flipped canvas's last `HERO_FADE_PX` before the clip edge, so the artwork ends in a gradient
  rather than on a straight line. It is a mask on the canvas itself, where the overflow is.

Both are recomputed on every pinning update, because the cards change height, and both go through the ledger.
