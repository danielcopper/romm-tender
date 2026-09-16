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
