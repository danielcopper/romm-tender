---
paths:
  - "frontend/src/**/*.test.ts"
  - "frontend/src/**/*.test.tsx"
  - "frontend/src/test-utils/**"
---

# Frontend component tests — the backend-event harness

Run with `mise run test:frontend` (Vitest + happy-dom); `mise run test:frontend:coverage` for coverage.

`frontend/src/test-utils/host-event-bus.ts` exposes an in-memory event bus that `addEventListener` /
`removeEventListener` route through. Tests dispatch backend events via `emitHostEvent` instead of mocking
`frontend/src/api/host.ts` per file; `frontend/src/bigpicture/CustomPlayButton.test.tsx` is the reference shape. The bus
resets between tests; use `hostEventListenerCount(name)` to assert `useEffect` cleanup ran. DOM-level
`globalThis.dispatchEvent` flows bypass the harness — happy-dom handles them natively. Prefer the harness over
extracting listener bodies into `frontend/src/utils/*.ts` purely for testability.

**Catch coverage assertions must be non-vacuous.** A test claiming `.catch` coverage MUST assert the post-catch state —
the fallback return value, the toast body, the `debugLog` message, the surfaced status. Asserting only that the
rejecting call was invoked is vacuous: it passes with or without the `.catch`.

**`api/host` is stubbed for the whole suite, so no socket is ever opened in it.** `test-setup.ts` replaces the module
wholesale: `callable` is a `vi.fn()` resolving to `undefined`, and the event pair routes through the harness. **No line
of `frontend/src/api/hostSocket.ts` runs through any of that** — framing, call numbering, the outbox, the reconnection
and the reply-versus-error discrimination are covered in `hostSocket.test.ts`, against a socket that file supplies, and
are invisible everywhere else. A test that wants the real module takes the stub off itself (`vi.unmock("./host")`, as
`host.test.ts` does).

**`utils/quickAccessVisible` is the third module stubbed for the whole suite, and its stub answers that the QAM is
OPEN.** Keep it in `test-setup.ts` rather than per file, for two reasons: a file that re-mocks `@decky/ui` for its own
components does not disturb a mock of a different module, and the real hook reads Steam's navigation trees through
`utils/deckyUiInternals`, so every wide-page test would otherwise have to stub that getter as well. A test that wants
the real hook takes the stub off itself, as `quickAccessVisible.test.ts` does.

**Losing that stub is loud today, and what makes it loud is an omission nobody stated.** The real hook reads
`getGamepadNavigationTrees` off `utils/deckyUiInternals`, and no factory a wide-page file reaches defines that name — so
the read hits a mock without it, Vitest throws, and every wide-page file fails outright instead of quietly taking the
nothing-established branch. **Adding the export puts the net back to silence**, because that branch proceeds exactly as
the menu-is-open one does, and the two routes do not reach equally far:

- A wide-page test's **own `deckyUiInternals` factory** silences that file. It is the shorter reach and the likelier
  edit — most wide-page tests shadow that module already.
- **`test-setup.ts`'s `@decky/ui` factory** silences only a file that does NOT shadow `deckyUiInternals`, because the
  hook's read then reaches the real re-export module, whose own import of the package hits the suite-wide mock. That is
  not a hypothetical: `bigpicture/library/PlatformsTab.test.tsx` is written that way today, and so is every future
  wide-page test with no reason to touch that module.

Either way the price is the same and is worth paying knowingly: add the export for a test that needs the trees, and give
the wide-page tests the stub they will then need.
