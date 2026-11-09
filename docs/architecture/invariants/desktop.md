# Invariants: The desktop surface

The desktop client's surface in the dev build: its restoration ledgers and its selectors. How an entry reads and binds,
and the other areas: [the register's index](index.md).

- **Every change the desktop surface makes to Steam's desktop window goes through a restoration ledger and is given
  back: the navigation watcher's on leaving the page whenever the ledger holds anything — a pass that threw partway
  included — and each launch question's when the question settles** (dev build only) — test + prompt-only — the watcher
  runs a pass on every change Steam makes to a RomM game page, so a change that bypasses the ledger, or a ledger that
  records the wrong original, is not an occasional leak but the page left changed after every visit: a second pass that
  recorded `display: none` as the original would leave Steam's Play button, badges and sections hidden once the page is
  left, and a style written straight onto the play bar would stay on it. Neither shows in any one adaptation's test,
  because each of those checks its own element, so the rule is held by one test over the whole page:
  `frontend/src/desktop/navigationWatcher.test.ts`, "every change it makes is given back", records the `style` attribute
  of every element before the watcher starts, runs passes from Steam's mutations and from a change to the set of RomM
  shortcuts, has a scroll and a resize re-pin the play bar — each asserted to have reached its listener — leaves the
  page, and requires every attribute to match — once with both views handed and once with none. Leaving restores
  whenever the ledger holds anything rather than once a page was entered, which is what gives back a pass that threw
  after the sticky play bar was set up; "gives back what a pass changed before it threw" holds that. What the test
  cannot see is a write to anything but `style` — a class, another attribute, a node moved — and a change to an element
  its page does not hold; those are prompt-only. The launch prompt host keeps a ledger of its own for each question and
  gives it back when the question settles (`docs/architecture/desktop-dom-architecture.md`, "Dialogs for a start the
  launch watcher catches"); a container left behind would stay drawn over Steam's page after its start was settled.
  `frontend/src/desktop/launchPromptHost.test.tsx` holds it: the container is gone from the window's body however the
  question settles, a second window's included, and once it is answered the window's `pagehide` and `unload` listeners
  are removed, and with the root's unmount the dialog's own `keydown` listener, which a component's effect cleanup gives
  back outside the ledger. Beside a launch question, any change but its container and its window's listeners is
  prompt-only. The rule's home is `.claude/rules/desktop-dom.md`, and the mechanism
  `docs/architecture/desktop-dom-architecture.md`, "The restoration ledger". Prompt-only: beside the navigation watcher,
  a write to anything but `style` — a class, another attribute, a node moved — and a change to an element its test's
  page does not hold; beside a launch question, any change but its container and its window's listeners.
- **No code under `frontend/src/desktop/` names one of Steam's minified class hashes** (dev build only) — check — a
  class hash is webpack's, and a Steam update may change it, so a selector that names one finds nothing after that
  update and the surface goes inert without an error. The finders resolve through the selector ladder instead
  (`docs/architecture/desktop-dom-architecture.md`, "The selector ladder"), whose first rung takes the exact class
  `@decky/ui` resolved from Steam's own modules at run time — the one place a hash may appear, because it was never
  written down. `frontend/eslint.config.js` (`no-restricted-syntax`, `NO_CLASS_HASHES`) fails a string literal, regex
  literal or template part under `frontend/src/desktop/` that holds a token of twelve or more letters, digits, `_` and
  `-` with a digit, a capital and a small letter in it; test files are left out, because a test builds Steam's page out
  of such classes. `frontend/src/eslintNoClassHashes.test.ts` keeps the rule reporting, and holds that the block, which
  replaces the package-wide `no-restricted-syntax` options for those files, still carries the rule that no name calls
  Tender a plugin. Unseen by it: a hash with no digit, no capital or no small letter in it, one shorter than twelve
  characters, and one built at run time. This list is the one home of what the check misses; the config and the rules
  file point here
