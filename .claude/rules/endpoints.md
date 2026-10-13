---
paths:
  - "backend/main.py"
  - "frontend/src/api/**"
---

# Endpoint response shapes `[ours]`

Endpoints returning a plain `dict` that can fail use `{success: False, reason: ErrorCode | str, message: str}`. Both
`reason` and `message` are **required**. Reuse `lib.list_result.ErrorCode` for coarse categories; a bespoke guard's
reason is a string outside it — the `reason` of a named refusal class where a caller catches it or the panel branches on
it (`config_error` is `NotConfigured`, `not_installed` is `NotInstalled`), otherwise `Refused`'s first argument
(`sync_disabled`, …). A refusal's details travel beside `reason` and `message`; a payload's empty defaults do not,
because the panel reads a payload only on success — except that every save-sync refusal carries `synced=0`, and the
sweep's also `conflicts=0` (#2205 D2). A partial failure's extras are what was done. Where a refusal is raised, its
reason is a literal, a module constant or a `StrEnum` value — what `scripts/check_reason_parity.py` reads; anything else
fails it. Transport failures collapse onto `SERVER_UNREACHABLE` — a server that could not be reached, never one that
answered: a 4xx answers its own reason or `UNKNOWN`, a 5xx RomM reports itself `server_error`, and a 429 `rate_limited`
(`classify_error` in `lib/errors.py`); 401 and 403 collapse onto `AUTH_FAILED` (same slug, distinct `message`). The
legacy `error_code` key and a second `error` key are **forbidden**. A reason added or dropped lands in the panel's
`WireReason` union (`frontend/src/types/reasons.ts`) in the same change; `scripts/check_reason_parity.py` fails until it
does.

**`main.py` builds the shape** from what a use case raises or returns (below), and no other module builds it from a
literal; `scripts/check_failure_shape.py --check` fails on one that does. The gate reads where the shape is built, not
its keys. The full rule — what the gate reads and what fails it, and the two results that compute a failing `success`
outside `main.py` — is the invariant register's entry in `CLAUDE.md` and `docs/architecture/backend-architecture.md`,
§4.

Two carve-outs, which carry no `success` key, so the gate passes them:

- **Discriminated-status unions** (`status: "ok" | "server_unreachable" | …`, used by the saves version-history
  endpoints) keep the `status` discriminant instead of `success` — more than two outcomes. Failure branches still carry
  `message: str`.
- **Partial-success responses** returning a full payload alongside a failure flag (`get_save_status`'s
  `server_query_failed: bool`, `get_save_setup_info`'s `recommended_action`) keep the additive flag.

Full convention paragraph: the `lib/list_result.py` module docstring.

**`Endpoints` answers a refusal its use case raises.** Every public method is wrapped once in `main.py`, so an endpoint
body never catches `Refused`, `DomainRefused` or `RommApiError` to build the shape above itself, and anything else stays
a transport error (`.claude/rules/host.md`, rule 1). What is translated, and why nothing else is:
`docs/architecture/backend-architecture.md`, "A refusal can be raised".

Two adjacent rules that bite when adding or changing an endpoint:

- **An endpoint is a public method on `Endpoints` marked `@route`**, placed topmost — `def` or `async def` alike. An
  endpoint whose body never awaits is a `def`; nothing mechanical checks that. Its conflict rules are not the
  endpoint's: the use case it calls checks them (GLOSSARY.md → Conflict rules), which is why a use case that checks any
  is `async`. `host.dispatch.route_names` resolves the set off the loaded class, `scripts/check_endpoint_parity.py`
  derives the same set from the source (and fails on a `@route` below another decorator or on an underscored name), and
  `tests/host/test_dispatch.py` asserts the two are equal. A public method must carry `@route`: the refusal translation
  wraps every public method, and `tests/test_endpoints_translation.py` fails on one without it. A method with `@route`
  is reachable whether or not that was intended; the parity check below is what notices that.
- **Frontend↔backend parity** (name + arity) is enforced by `scripts/check_endpoint_parity.py`, which derives the
  frontend surface from every `endpoint<[Args], Return>("name")` in `frontend/src/**/*.ts`. A rename lands on both sides
  or not at all.
