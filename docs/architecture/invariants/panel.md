# Invariants: The panel: Quick Access and the game page

Tender's Quick Access entry, its section on Steam's game page, the stranded panel, per-ROM detail state, focusable rows,
list-and-detail entry focus and focus-group values. How an entry reads and binds, and the other areas:
[the register's index](index.md).

- **Tender's Quick Access entry composes with Decky's rather than going through it, and everything it binds to the Quick
  Access window is bound from inside that window's React tree** — test + prompt-only —
  `frontend/src/qam/quickAccessEntry.test.ts` pins what a render pass does to a tab array (added once, added again to
  the replacement array a remount builds, put back — before the first `decky`-marked entry under `"above_decky"`, at the
  end under `"below_decky"` or where no entry carries that mark — whenever a pass finds it elsewhere, and recognised by
  its marker alone in an array this module has never seen), each case mutation-checked. **Everything above that line is
  device-only**: whether the two renderers are found, patched and hand back a tree the resolver can walk was measured in
  #1897 and nothing here re-measures it — a suite that faked it would assert against a tree it wrote itself. The rule
  spans the frontend entry and the backend injector, and nothing joins them. **Three halves fail green.** (1) The entry
  carries `tender` as its marker and its key and never `decky`, and nothing writes `window.__TABS_HOOK_INSTANCE` or
  calls `__TABS_HOOK_INSTANCE.add()`: Decky's own render counts its `decky`-marked entries against its list length, so a
  foreign entry there desynchronises that guard into re-pushing every tab with no convergence, and its constructor calls
  `deinit()` on whatever it finds in that global. **Both are read off Decky's source rather than measured** — the
  `add()` route was deliberately never taken, so its runaway was never observed, and observing the `deinit()` would mean
  breaking Decky's boot on purpose. Neither is a presence check and neither may become one; which bundle is loaded was
  already decided from the machine (`backend/host/inject/machine.py`). Neither `window.__TABS_HOOK_INSTANCE` nor a
  `decky`-marked entry is read as a sign Decky is present; a `decky`-marked entry is read only to place Tender's entry:
  finding none puts the entry last and decides nothing else. (2) **No tab array is held anywhere**, which is available
  only because there is no unpatch: the injector refuses to load the panel into a context already carrying
  `window.__tender_panel__` (`backend/host/inject/bootstrap.py`), and what clears that marker is a JS-context rebuild,
  which takes the module, its patches and every array with it. Re-adding an unpatch is therefore also re-adding a reason
  to retain arrays, and the spike's shape — a `Set` of every array ever pushed into — leaks one dead array per Quick
  Access remount, with the strip's entries and their React elements, for the life of the JS context. (3) On every render
  pass the handler places the entry by the stored `qam_entry_position` (`"above_decky"` the default); the placement is
  re-asserted on EVERY pass rather than set at creation, because `afterPatch` runs the previous handler first: whoever
  patches last lands lowest, measured both ways, and install order is a property of which program starts first. A
  handler that placed the entry once and trusted the order goes green here and is wrong in exactly one case: where
  TENDER started first under `"below_decky"`. Its pass over a fresh array runs before Decky has pushed, finds no
  `decky`-marked entry, puts the entry last, and Decky pushes under it. Where Decky started first, Decky's entries are
  already in the array when Tender's pass runs, so a single placement lands right under either setting. That is the case
  re-assertion does not have to fix, and the one a developer is most likely to test. In the Tender-first order even the
  re-asserting handler leaves the entry above Decky for the first pass over a fresh array, until the next pass moves it;
  that is worked out from the code, not measured. The third rule's own half — **nothing binds to the Quick Access window
  at module scope** — is unmechanized and unpinned: that window is replaced by every remount, so a listener, observer or
  stylesheet held across one is bound to a document nothing renders. What holds today was measured rather than assumed —
  an unfiltered grep over `frontend/src` (tests aside) for `addEventListener(`, `ResizeObserver`, `MutationObserver`,
  `ownerDocument`, `defaultView` and `createElement(`, with the enclosing function of every hit read. **Neither observer
  term is prefixed with `new`**, and that is what makes it find anything: this repo's realm rule takes the constructor
  off the node's own view, so every observer here is spelled `new view.ResizeObserver` or
  `new panelView.MutationObserver`, and a pattern anchored on `new ResizeObserver` matches nothing in `frontend/src` at
  all. The sweep's own boundary is worth stating, because a reader re-deriving it meets the other kind first: a
  `globalThis` listener binds SharedJSContext's window, which the menu's remount does not touch, so those are out of
  scope however many of them there are. Every binding into the MENU's window sits inside an effect or an event handler
  of a component the menu mounts: `utils/qamExpansion.ts`'s stylesheet and `MutationObserver`, `utils/entryFocus.ts`'s
  focus listeners, `bigpicture/layout/WidePage.tsx`'s `ResizeObserver`, and `bigpicture/layout/ScrollRegion.tsx`, which
  reads the view per event and retains nothing. The glyph binds nothing there: its update dot reads stores that are
  module state of the window the panel's code runs in, and subscribes to them from the tree through
  `useSyncExternalStore`. (`utils/styleInjector.ts` writes into `findSP()`'s document, which is the game page's and not
  the menu's.) One added at module scope would work perfectly until the first Gaming-Mode-to-Desktop switch and then do
  nothing, silently. Detail: `docs/architecture/qam-panel.md` → The entry. Prompt-only: marker and key are `tender`,
  never `decky`; nothing writes `window.__TABS_HOOK_INSTANCE` or calls its `add()`, and neither it nor a `decky`-marked
  entry is read as a sign Decky is present — a `decky`-marked entry is read only to place Tender's entry; no tab array
  held, no unpatch added; on every render pass the handler places the entry by the stored `qam_entry_position` (before
  the first `decky`-marked entry under `"above_decky"`, the default; last under `"below_decky"` or with no such entry);
  nothing binds to the Quick Access window at module scope.
- **Tender's section reaches Steam's game page through the ROUTE component's `renderFunc`, and never through the page
  component's own `type`** — test + prompt-only — `frontend/src/bigpicture/patches/gamePageSeam.test.ts` pins the half
  that is decidable without Steam: the factory predicate in both directions, that two matching factories answer as no
  match, the shape predicate that decides whose sources are read at all, the memo selection (including an export whose
  getter throws), and the per-render decision — wrapped once per PROPS object, never per component and never per
  `renderFunc`. Each case mutation-checked. **Everything the install does with those answers is device-only**: obtaining
  Steam's webpack `require`, reading the factory sources, patching the memo and adopting a mounted page are
  `installGamePagePatch.ts`, which is coverage-exempt because a suite that faked any of it would assert against a
  registry and a fiber tree it wrote itself. The rule spans that file, the seam module, the patch it installs
  (`gameDetailPatch.tsx`) and the start-up check, and nothing joins them. **Why the page component is the wrong seam is
  the half a reader will re-derive wrongly**: `@decky/ui`'s tree patcher caches the wrapped component per ORIGINAL type
  (`dist/utils/react/treepatcher.js`, `handleStep`), so once any plugin has wrapped the page component every later
  render goes through that cached copy and a patch installed on the original is never entered again. That is the
  ordinary case rather than a corner: this backend starts after Steam has been running, so Decky's plugins have already
  wrapped the page. The failure is silent and machine-dependent — green suite, green gate, and the section simply never
  appears on a machine that runs Decky Loader while appearing on one that does not, which is the difference this program
  exists not to depend on. Two further halves nothing checks: the install patches EVERY memo export of that module whose
  `type` is a function rather than picking the route out (nothing on an export says which one it is, and on any other
  export the handler finds no `renderFunc` to wrap), and the start-up check's `AppDetailsRoute` entry costs a `feature`
  beside `appDetailsClasses`, which costs one for the same reason — every read of it is in that same patch. Moving
  either to `panel` takes the whole interface off the air for a section outside it; moving a panel name to `feature`
  beside them renders a hole. Detail: `docs/architecture/frontend-bundles.md` → Tender's section on Steam's game page.
  Prompt-only: the install patches every memo export whose `type` is a function, and the start-up check's
  `AppDetailsRoute` and `appDetailsClasses` cost `feature`, never `panel`, and no panel name moves to `feature` beside
  them (that renders a hole).
- **While the panel is stranded it offers no press that cannot work: the stranded card stands in place of Tender's whole
  Quick Access panel and of a game page's section, the play row keeps only Resume for its game while that game runs,
  Steam's own Play on a RomM shortcut that is not running is refused** — test + prompt-only

  And the fallback page drops every button but Restart Steam

  `frontend/src/index.test.tsx`, `frontend/src/bigpicture/RomMGameInfoPanel.test.tsx`,
  `frontend/src/bigpicture/RomMPlaySection.test.tsx`, `frontend/src/bigpicture/CustomPlayButton.test.tsx`,
  `frontend/src/utils/launchInterceptor.test.ts`, `frontend/src/boot/FallbackActions.test.tsx` and, in the desktop dev
  build, `frontend/src/desktop/gameview/PlayButton.test.tsx`, each in its stranded-panel `describe` block. They pin the
  six shells a shipped bundle has that gate today: the Quick Access root (`QAMPanel` in `frontend/src/index.tsx`)
  renders the card above its page switch, so no page component runs; the game page's section (`RomMGameInfoPanel.tsx`)
  renders it ahead of every other card and of Loading; the play row (`RomMPlaySection.tsx`) drops its gears, its version
  and disc pickers and the two badges whose tabs the card replaced, and stays hidden under a version error or a pending
  migration as it is otherwise; the play button (`CustomPlayButton.tsx`) renders nothing unless its game runs, and then
  Resume without the chevron whose only entry was Stop Game; the launch interceptor
  (`frontend/src/utils/launchInterceptor.ts`) refuses a start after its cancel and before the launch gate asks anything;
  the fallback page's actions (`frontend/src/boot/FallbackActions.tsx`) give way to the answer — every button but
  Restart Steam, which asks Steam rather than the backend — with the line that an update's result shows after the reload
  or restart that answer names. In the desktop dev build the desktop client's Play button
  (`frontend/src/desktop/gameview/PlayButton.tsx`) renders nothing, since it has no Resume and PLAY, STOP and RESOLVE
  CONFLICT each need the backend, pinned by the stranded-panel `describe` block of
  `frontend/src/desktop/gameview/PlayButton.test.tsx`. **Why**: a stranded panel is never admitted again — it holds the
  token of a backend process that is gone — so every press that reaches the backend could only fail. Before #2275 each
  such press failed in a way of its own: some with the answer, some with a bare failure, some with a wrong reason, some
  with nothing at all, leaving a switch at a value that was never saved; Play on a page opened before the restart
  started the game without its pre-launch save sync. A gate at the shells covers a press added inside them later without
  a change of its own, where a message per press would be a rule every new failure path has to remember. **What fails
  green**: a new surface outside the shells — another element injected into Steam's game page, another hook on Steam's
  launch — offers presses that can only fail, and nothing notices: `api/host` is stubbed suite-wide, so a test of that
  surface sees a stranded panel only if it arranges one (`frontend/src/test-utils/stranded-panel.ts`). That half is
  prompt-only: such a surface gates itself on `useStrandedAnswer()` in a component or `strandedAnswer()` in a handler.
  **The exceptions are deliberate**: a Tender dialog or menu already open when the panel is stranded stays open and its
  buttons fail as before (#2275 D6), since closing every one would need one opener in place of `showModal` and
  `showContextMenu` everywhere one is opened, and Stop Game in a menu opened before the strand says at once that it
  cannot stop the game; a Play press on a game already running passes the interceptor, whose running-game guard comes
  before the cancel. Detail: `docs/architecture/loading-the-panel.md` → What the stranded panel is told
- **Every write into per-rom detail state that crosses an `await` is bound to a rom identity** — test + prompt-only

  The store (`frontend/src/utils/gameDetailStore.ts`) via `writerForRom`, or the answer's own `rom_id` in
  `applySaveStatus`; the panel's modules `frontend/src/bigpicture/panelState.ts`,
  `frontend/src/bigpicture/panelEvents.ts` and `frontend/src/bigpicture/panelTabContent.tsx` via `RomBinding`
  (`bindRom`, `bindRomInState`); `useAchievements` (`frontend/src/utils/achievements.ts`) by tagging each answer with
  the rom it was read for (`forRom`) and showing it only while that rom shows, and `useRomMetadata`
  (`frontend/src/utils/useRomMetadata.ts`) the same way. Two answers for the SAME rom are ordered by a sequence taken at
  issue (`loadSeq`, `saveStatusSeq`, `takeReadTicket`). Ordered but unbound: the two identity writes (the store's
  `loadDetail`, the panel's `loadData`) and the panel's lazy SAVES-tab slot load
  (`frontend/src/bigpicture/panelSlotsLoad.ts`, raw setter, `slots` ticket); neither bound nor ordered: the store's
  `cached.bios_status` fold. The panel's `rereadBiosAnswer`, which `handleBiosChange` and a press on the BIOS tab both
  read through, is bound and ordered (`bios` ticket). The play button is NOT covered

  The panel component itself holds none of these writes. `RomBinding` is built by `bindRom` for a read a run of the
  `[appId]` effect issued and by `bindRomInState` for the active tab's panes, whose writer is built during render; the
  achievements load's tagging means its state cannot outlive the identity it was read for; `useRomMetadata` is the
  desktop game view's metadata read. A version switch changes the rom without closing, so neither the store's generation
  counter nor the panel's `[appId]` effect sees this class. Binding answers the wrong-rom question only; the ordering of
  two answers for the SAME rom is #1717. Four writes are unbound. Three are ordered: the two identity writes install
  what a binding would compare against, so ordering is all they can have. The fourth, the store's `cached.bios_status`
  fold, has neither — it runs in the same synchronous run as its guard. The event lane's `rereadBiosAnswer` re-reads
  `get_bios_status` for the rom it shows, whose emulator is resolved per ROM — a per-game pin over the platform's pick —
  and takes the `bios` ticket the panel's other BIOS re-reads take (#1718). The play button's exclusion is #1714. The
  panel's fourteen bound sites each carry a version-switch test (`frontend/src/bigpicture/RomMGameInfoPanel.test.tsx`),
  and `useRomMetadata`'s binding is pinned by `frontend/src/utils/useRomMetadata.test.ts`; the store side and every new
  write site on either are prompt-only, because a checker scoped to the store's own function bodies would be green on
  the case this rule was written for. The reasons behind the two writer mechanisms live at `writerForRom` and
  `RomBinding` — do not restate them here
- **Every row a reader must be able to reach on a QAM page is a row Steam can focus — a toggle, a button, or a
  `Focusable` declaring a stop of its own, including a table row with no action of its own** — check + prompt-only

  Text between two stops of the same card is reached through them and is no stop of its own (an emulator source card's
  folder and health lines, between its arrows and its switch), and so is a line above the first stop of a
  `ScrollRegion`, which the region brings into view when focus reaches that stop (the Tender folder section's status
  line)

  A table row with no action of its own is a stop so the reader can walk the table. `tender/qam-focusable-row` checks
  the narrow syntactic slice where an `@decky/ui` `Focusable` in the QAM module map has no nav-stop prop, static
  focusable descendant, opaque child, or unknown spread — it passes a row with an unknown spread, any opaque child, or a
  browser-focusable descendant (`tabIndex`, `button`), so those rows are prompt-only too; focus order, runtime
  reachability, edge revelation, scrolling geometry, and controller behaviour remain prompt-only. The frontend suite
  cannot see those runtime properties: happy-dom has no nav tree, so a page whose rows are unreachable renders exactly
  like one whose rows are not, and a mouse-driven dev loop never meets the problem either. A region scrolls only by
  moving focus — Steam's plain `ScrollPanel` binds no gamepad direction — so an unreachable row is also an unscrollable
  one, and everything below the fold is simply out of reach with a controller. The trap is that a bare `Focusable` is a
  container rather than a focus stop: Steam's navigation asks the nav node the panel renders (`GetFocusable()`), which
  finds no stop on a row declaring none of the four options it reads — nor on one whose `onActivate`/`onOKButton` would
  have promoted it, since that promotion is skipped where an option was supplied — all stated in full at
  `docs/architecture/qam-panel.md`, which owns that mechanism. **What the check cannot see it says nothing about**, and
  three shapes matter. One opaque child anywhere among a row's children passes the whole row, which is how the
  removed-games cleanup's details region stood unreachable in the one release that has shipped with this gate green — a
  `Focusable` of plain text, fixed by declaring a nav option on it and not by the rule. A descendant the BROWSER can
  focus — a `tabIndex`, a `button` — is taken as an escape, so a row reachable with a mouse and not with a stick passes.
  And a row whose own `tabIndex` is its only affordance is reported rather than accepted, because that attribute reaches
  the rendered element and not the node. The rule spans every page the wide frame will host, and the frame cannot carry
  it: it holds a page's content as opaque nodes, never as rows it could check. **What the frame DOES carry is the
  content outside a region's focusable rows, at both ends** — a heading, a counts line or a column header above the
  first, a legend or a total below the last, each unreachable for the same reason and with no neighbour to ride along
  with — so `ScrollRegion` scrolls itself to the top when focus reaches the first stop in it and to its end when focus
  reaches the last (`revealEdge`, over `revealTop` and `revealBottom`). Both halves are pinned by
  `frontend/src/bigpicture/layout/ScrollRegion.test.tsx` over mocked geometry, so what is tested is the DECISION and not
  the scroll: whether the panel and the reader agree about which element is topmost or last stays device-only, like the
  rest of this entry. **Reachable is not near, and the same mechanism decides where a page puts its controls**: focus
  moves one row at a time, so a button under a list of N focusable rows is N presses from the top of the column —
  sixteen, measured on the device for the Cancel that stops a sixteen-unit run. That is why the Sync page's two button
  rows sit ABOVE their tables, which is the reading the layout wants anyway: what you can do, then why. Nothing checks
  that half either, and the suite is blind to it for the same reason — a page whose only control is a library's length
  below the point it opens at renders exactly like one whose control is a press away. Detail:
  `docs/architecture/qam-panel.md`, "Building blocks"
- **A list-and-detail page opens on the row it was opened WITH, not on its first row** — test + prompt-only — On that
  layout focus selects, so entry focus landing anywhere selects what it lands on. `ListDetail.test.tsx` pins the mark
  and that it moves with the selection, `WidePage.test.tsx` pins that the frame asks for the declared stop, and both use
  a **non-first** row deliberately, as any test of it must; between them they pin the `ENTRY_STOP_ATTR` mark a swap
  places focus by, and the `preferredFocus` on the selected row and on the body that entry from the Quick Access strip
  is meant to land by, and that both marks go once focus first enters the panel. **The rule spans four modules and
  nothing joins them**: `utils/entryFocus.ts` owns `ENTRY_STOP_ATTR` and `pageEntryStop`,
  `bigpicture/layout/WidePage.tsx` places entry focus through it rather than through `firstBodyStop`,
  `bigpicture/layout/ListDetail.tsx` marks its selected row, and `index.tsx` tells the frame whether a swap opened the
  page (`EntryFocusOnMountContext`) — plus whatever page passes a starting selection at all, `SettingsPage` today. A
  fourth wide page that opens on a non-first row and forgets the mark selects its first row instead, and every test
  still passes; a wide page that opens on a non-first row without `ListDetail` places the mark itself, the same way, and
  gives that row `preferredFocus` too, only while `EntryPreferenceContext` holds. **The end to end is unreachable
  here**: happy-dom performs no layout and does not reproduce Steam's focus resolution, so what the suite pins is the
  declaration and the finder, never the press. Only a controller confirms it. **What hid this for a whole review round
  is the shape of the failure, not its size**: Main's notices name sections, and the one naming the FIRST section keeps
  working, so a reader checking Open Connections sees the feature working while a jump into any later section lands on
  Connections. A check that exercises the first row proves nothing about the rule. The mark sits on a
  `display: contents` wrapper AROUND each row rather than on the row, and that is load-bearing rather than stylistic:
  `pageEntryStop` calls `firstBodyStop(declared)`, which searches DESCENDANTS — a mark on the row itself finds no
  candidate inside it, falls back to the first row, and ships the defect under a comment saying it does not. **The page
  the panel appears with is opened by neither placer** — not the router and not the frame, because focus placed as the
  panel mounts would take the reader off the Quick Access strip — so there the same row is meant to be reached through
  Steam's entry preference: `ListDetail` gives the selected row's own `Focusable` `preferredFocus`, and the frame's body
  searches for it. That holds on an untabbed page only: on a tabbed one Steam marks its own active tab the same way, at
  a shallower depth, and entry lands there. The mark holds only until focus first enters the panel, because Steam's
  search for it runs ahead of the child it remembers and would otherwise pull every later entry onto the row. Nothing
  joins that mark to `ENTRY_STOP_ATTR` either, and whether Steam honours either is device-only. Detail:
  `docs/architecture/qam-panel.md`, "Pages". Prompt-only: `bigpicture/layout/WidePage.tsx` places entry focus through
  `pageEntryStop` (`utils/entryFocus.ts`), never `firstBodyStop`; `bigpicture/layout/ListDetail.tsx` puts the
  `ENTRY_STOP_ATTR` mark on the `display: contents` wrapper around the selected row, never on the row; and a wide page
  that opens on a non-first row without `ListDetail` places the mark itself, the same way, and gives that row
  `preferredFocus` too, only while `EntryPreferenceContext` holds.
- **Every `flow-children` in a `.ts` or `.tsx` file under `frontend/src/` is one of the values Steam's parser knows
  (`docs/architecture/qam-panel.md`, "Focus groups"), written as a string literal** — check — Steam's parser drops any
  other value, and the group then takes its direction from its CSS each time it is asked, so a value such as
  `horizontal` steers the way it reads only while the group's CSS points the same way, and turns with the CSS with no
  sign of it but a failed assertion in Steam's console. The frontend suite does not navigate: happy-dom has no nav tree,
  so a test sees a group's value only where it reads it off the props or the element, as the tests of the play row, the
  BIOS tab and the settings dialogs' footer do, and never the way focus moves. `tender/flow-children-value` in
  `frontend/eslint.config.js` (`frontend/eslint-rules/flow-children-value.js`, over `src/**/*.{ts,tsx}`) fails a value
  outside the values Steam knows and a value it cannot read as a string literal, in every `.ts` and `.tsx` file under
  `frontend/src/`, tests included; `frontend/src/eslintFlowChildren.test.ts` keeps it reporting. Unseen by it: a value
  handed over through a spread or `createElement`'s props, a misspelt prop name (`flowChildren`, `flow_children`), and
  whether a known value is the way its group's stops are laid out. Detail: `docs/architecture/qam-panel.md`, "Focus
  groups"
