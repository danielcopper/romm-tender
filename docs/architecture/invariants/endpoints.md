# Invariants: Endpoints and the wire

An endpoint's failure answer, the parity of endpoint, event and reason names between backend and panel, and what may
travel on the wire. How an entry reads and binds, and the other areas: [the register's index](index.md).

- **Endpoint failures use `{success, reason, message}` (never `error` / `error_code`), and no module but `main.py`
  builds that shape — a service raises its refusal, which `Endpoints` answers** — check + test + prompt-only

  With two known exceptions that compute a failing `success` outside it: the save-sync results (a completed sync with
  per-file errors, `"success": len(errors) == 0`, #2205 D10, and a Sync All that passed a game over for a missing saves
  folder, #2151 D4) and the removed-game cleanup's `prune_complete` event; anything an endpoint raises but a refusal or
  a `RommApiError` stays a transport error

  `scripts/check_failure_shape.py --check` (every module under `backend/` but `main.py`, `_vendor/` and `native/`: a
  dict literal with a literal `False`, `0` or `None` `success`, an `error_response` call or a refusal-helper spread,
  event payloads included; unseen: a computed `success`, the two exceptions among it, a failure without a `success` key,
  a failure dict built without a literal), `tests/test_endpoints_translation.py` (on every route, a raised `Refused` /
  `DomainRefused` answers that shape and any other exception stays `backend_exception`; on one `def` and one `async def`
  route, a `RommApiError` answers `classify_error`'s reason and a returned `PartialFailure` is serialized into it; every
  route is the translating wrapper). Prompt-only: no failure beyond those two computes its `success`; the keys `main.py`
  builds the shape with — no refusal's details carry `error` or `error_code` — and a further type joins
  `main._TRANSLATED` only by decision
- **Frontend↔backend endpoint parity (names + arity)** — check — `scripts/check_endpoint_parity.py`
- **Every backend `emit` event name has a frontend listener, and vice versa** — check — `scripts/check_event_parity.py`
- **The panel's `WireReason` union (`frontend/src/types/reasons.ts`) holds exactly the reasons an endpoint's failure
  answer can carry — refusals, partial failures and `classify_error`'s table — and its `RommErrorReason` sub-union
  exactly that table** — check + prompt-only — `scripts/check_reason_parity.py`. The backend spells a reason where it
  refuses and the panel compares it with values of its own, so without the gate a reason renamed or added on one side
  reaches the other as a value no comparison matches, and nothing fails. The gate reads the backend's refusal
  constructions, named refusal classes, partial failures and the classifier by AST, resolving module constants,
  `StrEnum` values and a refusal class reached through an import alias or its module itself; a reason passed on from
  another refusal is accepted unread only where its `PASS_THROUGHS` list names it, and any other reason it cannot read
  fails. Its docstring is the one list of what it does not see and of what stays out of the union; unseen by it: a
  refusal class reached through an assignment alias (`R = Refused`), `getattr` or as a value, another site in a
  `PASS_THROUGHS` file with a listed site's source text, a module constant shadowed by a local, whether a counted reason
  can reach the wire, and whether a type that narrows the union names its endpoint's reasons truly. Prompt-only: a
  literal-typed endpoint-failure reason in the panel goes through `WireReasonSubset`, so a reason outside the union does
  not compile — a convention, unchecked
- **A transport failure and an endpoint's own failure never arrive in the same shape, on either end** — test +
  prompt-only — `backend/host/protocol.py` states the vocabulary and `.claude/rules/host.md` holds the backend half; the
  frontend half is `frontend/src/api/hostSocket.ts`, which THROWS `HostTransportError` for an `error` message and
  resolves only a `reply`, so a transport reason cannot reach a reader of `{success, reason, message}`.
  `hostSocket.test.ts` pins both directions. **Nothing joins the two ends**: `connection_lost` and `stranded_panel` are
  the two reasons no backend ever sends — the caller's own socket answers with them — and each is spelled once in Python
  and once in TypeScript with no check that the two agree; so are the close codes 4001 and 4002 a stranded panel's
  upgrade is answered with. A frontend that spelled one differently would go green, and the divergence would surface
  only to whoever eventually matched on it — for a close code, on a device, as a stranded panel that keeps reconnecting.
  Prompt-only: the Python and TypeScript spellings of `connection_lost` and `stranded_panel`, and of the stranded-panel
  close codes 4001 and 4002, must agree — nothing holds them equal
- **No sentinel objects on the wire — explicit JSON-representable tagged values only** — prompt-only — no sentinel
  survives on the wire today (`NO_MIGRATION` retired with #1004, legacy `slot:null` confirmation with #1276), so the
  rule now guards reintroduction; nothing mechanical detects a new one
