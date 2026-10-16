---
paths:
  - "frontend/src/desktop/**"
---

# Changing Steam's page in the desktop client

**Dev build only.** This surface reaches Steam only through `pnpm -C frontend build:desktop`; no shipped bundle carries
it (`docs/architecture/frontend-bundles.md`, "The desktop dev build"). Why each rule below holds is
`docs/architecture/desktop-dom-architecture.md`'s, in the section named beside it. Rules 1 and 2 are in `CLAUDE.md`'s
invariant register with what checks them; the others have no mechanical check.

1. **No hashed class names.** A selector never names a webpack-hashed class; it resolves through
   `watcher/elementSelectors.ts` ("The selector ladder"). ESLint fails a string or regex literal under
   `frontend/src/desktop/` that holds a token shaped like one; what passes it is listed at the rule's register entry.
2. **Every change to Steam's DOM goes through the `DomRestorationLedger`** — inline styles, hidden elements, listeners
   and mounted roots alike ("The restoration ledger"). A bare `el.style.x = …` or `addEventListener` on one of Steam's
   elements is never restored. A React component's own listener, removed by its effect's cleanup, and styles on a host
   Tender creates itself are outside the rule. A callback that can fire after the teardown does nothing once it has run
   (the `stopped` / `disposed` flags in `navigationWatcher.ts` and `stickyPlayBarController.ts`). The test that holds it
   compares `style` attributes only; a write to anything else is on you.
3. **Fiber is read, never written** ("Fiber, read-only").
4. **A constructor used on the desktop window's nodes comes from that window**, and from the global scope only where
   that window has none ("Two windows, two realms"); why is `CLAUDE.md`'s trap on `instanceof` and realms.
5. **The adaptations in `pass()` run before the mount guard**, and another game's page is given back and a stale
   `GameView` torn down before them ("The pass order"). The guard gates only the `GameView` mount; a pass runs on a RomM
   page even when everything is mounted. Passes run on what Steam reports, never on a timer; a change that follows sizes
   is a `ResizeObserver`'s to catch.
6. **Steam's own part is hidden only once Tender's view is drawn**, and a mount that failed is not tried again for that
   appId ("Drawing a view").
7. **A failure is logged with what it was doing**, never swallowed ("What it logs").
8. **The hero wrapper stays `overflow: visible`.** Nothing in its `preserve-3d` chain is clipped; the page's scroll gap
   is closed on the parallax layer by `boundHeroOverflow` ("The hero's parallax").
