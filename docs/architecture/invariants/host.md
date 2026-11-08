# Invariants: The host and loading the panel

Which bundles are loaded beside Decky Loader, the globals installer, the crash watchdog and the reload limit, whether a
listed app runs, and the focus watcher in Desktop Mode. How an entry reads and binds, and the other areas:
[the register's index](index.md).

- **`dist/globals.js` is never evaluated into Steam where Decky Loader is serving, and what decides that is read from
  the MACHINE rather than from the window** — test + prompt-only — `tests/host/inject/test_bundles.py` pins both choices
  in both directions (the globals bundle absent beside a serving loader, and the standalone panel absent too, since it
  carries the same sweep), and `tests/host/inject/test_injector.py::TestBesideDeckyLoader` pins it end to end over a
  real socket with something answering on the loader's port. It is the only crash cause ever observed here: that bundle
  carries `@decky/ui`'s module sweep at import scope, and re-running it under an interface already rendering from those
  modules takes the Big Picture window down. **The rule spans three modules and nothing joins them.**
  `host/inject/machine.py` asks whether Decky Loader's server answers, `host/inject/bundles.py` turns that into a file
  list, and `host/inject/injector.py` evaluates it. Prompt-only: the file list comes only from `host/inject/bundles.py`
  over `host/inject/machine.py`'s answer, never from another caller or a window probe. A fourth caller building its own
  file list, or one reading the window instead, goes green: at the moment the question has to be answered every Decky
  marker on the window — `DFL`, `DeckyPluginLoader`, `DeckyBackend`, `deckyAuthToken`, `deckyHasLoaded` — is still
  `undefined`, so a window probe answers "no Decky" on a machine that has one, picks the globals, and crashes the
  interface. Prompt-only too: the gap between "the loader's server answers" and "Decky is rendering" is deliberately
  left on the safe side, and the ordering measurement (Tender loading first is safe) may not be leant on to close it the
  other way: a reconnect puts the same question at a moment when Steam has been up for an hour. Detail:
  `docs/architecture/loading-the-panel.md`
- **Where the globals bundle is loaded, its installer is CALLED between the import that defines it and the panel import,
  and the panel is not imported unless the report says every global it names is installed** — test + prompt-only —
  `tests/host/inject/test_bootstrap.py::TestItRunsUnderNode` runs the bootstrap under node against a stub page, with the
  bundles as `data:` modules that record having run and that leave the installer on the window where the real one does,
  and pins the order, the refusal and each refusal's sentence; `test_bundles.py` pins that the globals bundle comes
  first; `TestTheInstallersName` holds the property's spelling in Python to `frontend/src/boot/steamGlobals.ts`. The
  rule spans three modules in two languages: `host/inject/bundles.py` says WHICH file installs (`globals_at`),
  `host/inject/bootstrap.py` turns that into import, call, import and into the gate, and `steamGlobals.ts` leaves the
  installer under the shared name and decides the report's keys. **What nothing checks is the seam the node tier
  stubs**: the stub report is written from the TypeScript interface rather than derived from it, so a `GlobalsReport`
  that renamed the `installed` field passes every test named here and fails on a device the same silent way — the
  bootstrap reads no keys, refuses, and leaves no panel, a card, and a green suite. Renaming the keys INSIDE `installed`
  costs nothing, because the gate is generic over whatever the report names; renaming `steamReady` costs only the
  readiness sentence, and nothing on either side holds one spelling to the other. Prompt-only: the stub report's keys
  (`installed`, `steamReady`) agree with `GlobalsReport` in `frontend/src/boot/steamGlobals.ts`
- **An injection that could not be observed is never counted as a crash, and this process answers for its own record
  before it reads one** — test + prompt-only — `tests/host/inject/test_watchdog.py` pins the state machine in every
  direction and which of the three answers settles an armed record;
  `tests/host/inject/test_injector.py::TestDidTheInterfaceSurviveIt` pins every way a record is answered for over the
  real loop — the survival, the collapse that leaves it open, and each way an attempt is closed without being counted.
  Count those by their call: a test in that class that ends with the record `open: false` and `failures: 0` is one of
  them. One more way lives in its own class, `::TestWhatThisBackendDoesToSteamIsNeverACrash`, because it is reached only
  through replacing a panel an earlier backend left behind. That test drives the reload; the fallback's SIGTERM to
  `steamwebhelper` bumps the same counter and has no test of its own, because every path to it passes the reload's bump
  first. The crash cannot be counted inside a session — it leaves `SharedJSContext` alive with our marker on it, so the
  injector sees "already injected" and never tries again — so what is counted is a record left open at the NEXT attempt,
  and two in a row stop the injection. **Three things have to hold together and nothing checks that they do.** `judge`
  writes its resolution back before it answers, or one open record counts once per attempt for ever. **The ORDER is the
  second**: an injection answers for the record this process already holds before `judge` reads one, because a
  JS-context rebuild inside the alive window starts the next injection while the last record is still open and
  unanswered — ordinary during start-up settle, and what a Steam restart produces — and read the other way round it is a
  crash that never happened, twice over on a machine where nothing was wrong. A real crash is unaffected: the check that
  saw it marks its reading taken (`stays_open`) and the record stays open for the next `judge` to find. The third is
  that the alive check closes the record without counting whenever nothing was established — the debugger stopped
  answering, the backend is shutting down, nothing but the renderer was open when the panel was loaded, a second
  injection began before the check that answers for the first could run, or this process took the interface down itself
  after the panel was loaded (the reload that replaces a stranded panel, or its fallback's SIGTERM to `steamwebhelper`,
  each counted in `PanelInjector` before it happens and compared by the check against the count at arming) — because the
  signature is specific (every other page target goes at once while the debugger keeps answering), and a run in which
  that could not be observed says nothing. A close that counted any of those would stop the panel loading over a user
  closing Steam, and the failure is silent in both directions: too lenient and a crash loop is never stopped, too strict
  and the panel disappears with only a log line to say why. The way back is not inside Steam (the interface is what is
  gone): the fingerprint — Tender's version, the bundle bytes, Steam's client build — drops the count on its own, and
  `TENDER_INJECT=force` is the switch the refusal line names
- **This machine takes Steam's interface down to replace a stranded panel at most twice in ten minutes, across backend
  starts; a reload Steam does not refuse and the fallback's SIGTERM both count** — test + prompt-only —
  `tests/host/inject/test_reload_limit.py` pins the record (under the limit, at it, the window passing, a clock set
  back, a record it cannot read or write, an entry no float can hold), and
  `tests/host/inject/test_injector.py::TestAcrossBackendStarts` pins it over the real loop from a record written before
  the backend starts: a reload under the limit, nothing at it and nothing recorded for the refusal, the fallback refused
  because the reload before it counted, a reload Steam refused left uncounted and one that got no answer counted, and
  takedowns the window has passed forgotten. Why the record outlives the process, why two, and why one it cannot read or
  write blocks nothing:
  [a panel an earlier backend left behind](../loading-the-panel.md#a-panel-an-earlier-backend-left-behind). **The join
  is prompt-only**: `recovery.py` goes through `_may_take_the_interface_down` in front of both takedowns it performs and
  calls `ReloadLimit.record` once each is under way — the SIGTERM before it is sent, the reload once Steam has not
  refused it — and nothing checks that a third takedown does the same; it would pass every test above and reload in a
  loop again
- **Whether a listed app runs is read only through `frontend/src/utils/runningApps.ts` (panel) or
  `RUNNING_APPS_EXPRESSION` (`backend/host/inject/recovery.py`), by display status** — test + prompt-only

  Running alone makes a game run, Launching or Running one that runs or is starting, Launching, Running and Terminating
  hold a restart, a wait or a game's saves (the backend's reading, matched to a ROM by each entry's `appid` in
  `services/running_games.py`); an unreadable status counts as running or holding

  Being listed in `SteamUIStore.RunningApps` is not being running: Steam's own Play lists a game it is only starting,
  before the start is reported, and Steam keeps a game listed after its exit while it still knows a window for it — read
  without the status, the first skipped the pre-launch sync and the second held the stranded panel's reload and every
  update until Steam was restarted ([Is the game running](../save-file-sync-architecture.md#is-the-game-running)). The
  panel reads the list three ways: `readRunningApps` for whether a game runs (Running alone, for `readGameRunning`),
  `readRunningOrStartingApps` for whether one runs or is starting (Launching or Running, for the stranded card and
  reload adoption, because a game Steam has reported started can still read Launching), and `isAnyAppHolding` for the
  session-budget banner's restart, which closes a game on its way in or out as well. The backend's expression is the
  third reading, `isAnyAppHolding`'s, and both waits — the reload and an update's install — read it. An unreadable
  status counts as running or holding on both sides, because a field Steam renamed hits every entry at once, and read as
  "not running" it would gate a link on a running game, let a restart close one, and lose every running session at a
  JS-context rebuild. `tests/host/inject/test_recovery.py::TestTheStatusHomesAgree` reads `runningApps.ts` and holds its
  `DISPLAY_STATUSES_THAT_HOLD` equal to the backend's; `TestWhichEntriesHold` runs the expression under node over
  Launching, Running, ReadyToLaunch and Terminating and four unreadable shapes; `frontend/src/utils/runningApps.test.ts`
  pins the three panel readings. **Prompt-only**: no module reads `SteamUIStore.RunningApps` but these two — a third
  reader would pass every test above with its own rule — and the stranded card's sentence follows what the backend waits
  on: it names an app whose status could not be read whatever stop Tender saw for it, because the backend's reload has
  no stop rule; the card's tests pin that, and nothing joins them to the backend's expression. The expression's entries
  also name their `appid`, and `RunningGames.running_rom_ids` (`services/running_games.py`) matches them to the ROMs
  whose shortcut `roms.shortcut_app_id` records, so the save sync counts a game as running as the backend's waits hold:
  Launching, Running or Terminating, or a status that could not be read. `TestEachEntryNamesItsApp` runs the expression
  under node for the id and pins the reading's parse; `tests/services/test_running_games.py` pins the match, and that no
  reading — or a reader that raised — is nothing running
- **The focus watcher sends `steam://forceinputappid` only while Steam runs the 64-bit client outside gamescope** —
  test + prompt-only

  And pins only a window Steam cannot attribute itself — one under a `reaper SteamLaunch AppId=<id>` that carries no
  `STEAM_GAME` — with its id, on every focus change onto it, an unchanged one included; focus leaving a standing pin
  gets once the id Steam's own choice would be and right after it `0`, and so does the first answer in a Steam run for a
  window not to pin, once per run; otherwise, with no pin standing, it sends nothing; `0` at stop goes out once, through
  the host's step before it detaches from Steam's debugger, and only where a pin stands and its Steam still runs;
  switched off (`TENDER_FOCUS_WATCHER=off`, or `TENDER_INJECT=off`) it opens nothing and sends nothing; and no module
  but `domain/controller_focus.py` spells the URL

  `tests/domain/test_controller_focus.py` (`TestWhenTheWatcherApplies`, `TestWhichWindowIsPinned`,
  `TestWhatSteamWouldChooseItself`, `TestWhatAFocusChangeSends`, `TestTheSwitch`, `TestOnlyTheRulesSpellTheUrl`),
  `tests/services/test_controller_focus.py` (`TestWhereSteamAttributesWindowsItself`, `TestWatching`,
  `TestSteamRestarts`, `TestReleasingThePin`, `TestSwitchedOff`),
  `tests/host/test_runtime.py::TestTheStepBeforeTheDetach` for the order at stop, `tests/test_build_backend.py`
  (`TestWhetherTheFocusWatcherRuns`, `TestTheFocusWatcher::test_the_step_before_the_detach_releases_its_pin`),
  `tests/adapters/test_running_steam.py` (`TestGamingMode`, `TestTheDisplayItWasStartedFrom`) for what the 64-bit client
  and Gaming Mode are read from, and the X tier, `tests/adapters/test_x11_focus.py::TestAgainstARealServer`, for what a
  focused window is read as. Unseen by them: what Steam does with a URL (device only), a URL built at run time or named
  only in a comment or docstring, and the X tier wherever Xvfb is missing outside CI, where it skips. Prompt-only:
  nothing but the watcher's `release_pin` runs in `BackendBuild.before_detach`. The pin is Steam's own and it outlives
  its game: while one stands, Steam forces its layout onto every window, Big Picture and other games included, until
  another id replaces it, and `0` takes effect only at the next focus change. A pin on a native game's window also kept
  that game's in-game overlay, drawn inside the same window, from taking the controller. So only a window Steam cannot
  attribute is pinned; leaving it sends Steam's own choice, which sets the window focused now at once, and then the `0`,
  which leaves every later change to Steam; and the `0` at stop has to go out while the attachment it travels over still
  exists — the host lets go of Steam's debugger before it shuts the application down, so a `0` from
  `Application.shutdown` would find no connection and leave the pin forcing its layout onto every window. A backend that
  did not stop cleanly leaves its pin, which the next backend cannot know about, so the first answer in a Steam run
  treats one as standing. Under the 32-bit client and in Gaming Mode Steam attributes the window itself, and a pin there
  would override what works. Each URL is built by `force_input_url` from an integer, so nothing but a number reaches
  Steam. The rules are `domain/controller_focus.py`'s, the watcher is `services/controller_focus.py`, the step before
  the detach is `BackendBuild.before_detach` in `backend/host/runtime.py`, and the design is
  [Controller focus in Desktop Mode](../controller-focus.md)
