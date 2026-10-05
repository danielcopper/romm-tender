---
paths:
  - "backend/main.py"
  - "frontend/src/api/**"
---

# Endpoint response shapes `[ours]`

Endpoints returning a plain `dict` that can fail use `{success: False, reason: ErrorCode | str, message: str}`. Both
`reason` and `message` are **required**. Reuse `lib.list_result.ErrorCode` for coarse categories; bespoke guards
(`config_error`, `sync_disabled`, `not_installed`, …) stay plain-string reasons. Transport failures collapse onto
`SERVER_UNREACHABLE`; 401 and 403 collapse onto `AUTH_FAILED` (same slug, distinct `message`). The legacy `error_code`
key and a second `error` key are **forbidden**. Enforced by `scripts/check_failure_shape.py --check`.

Two carve-outs (pattern-exempt in the gate):

- **Discriminated-status unions** (`status: "ok" | "server_unreachable" | …`, used by the saves version-history
  endpoints) keep the `status` discriminant instead of `success` — more than two outcomes. Failure branches still carry
  `message: str`.
- **Partial-success responses** returning a full payload alongside a failure flag (`get_save_status`'s
  `server_query_failed: bool`, `get_save_setup_info`'s `recommended_action`) keep the additive flag.

Full convention paragraph: the `lib/list_result.py` module docstring.

**A service may raise its refusal instead of answering it** — `Refused` (`lib/errors.py`) from the service layer,
`DomainRefused` (`domain/refusal.py`) from a domain rule, with the reason a literal at the raise site — and return a
`PartialFailure` (`lib/partial_failure.py`) for work that stopped partway. `Endpoints` translates all three into the
shape above, and a raised `RommApiError` into `classify_error`'s reason and message; it catches nothing else, so a bug
stays a transport error (`.claude/rules/host.md`, rule 1). A failure dict stays valid in a module not yet converted. A
converted module is listed in `CONVERTED_MODULES` in `scripts/check_failure_shape.py`, which then fails on any failure
shape built in it; the list only grows.

Two adjacent rules that bite when adding or changing an endpoint:

- **An endpoint is a public method on `Endpoints` marked `@route`**, placed topmost — `def` or `async def` alike. An
  endpoint whose body never awaits is a `def`; nothing mechanical checks that. Its conflict rules are not the
  endpoint's: the use case it calls checks them (GLOSSARY.md → Conflict rules), which is why a use case that checks any
  is `async`. `host.dispatch.route_names` resolves the set off the loaded class, `scripts/check_endpoint_parity.py`
  derives the same set from the source (and fails on a `@route` below another decorator or on an underscored name), and
  `tests/host/test_dispatch.py` asserts the two are equal. A method without `@route` is not reachable at all, and one
  with it is reachable whether or not that was intended; the parity check below is what notices either.
- **Frontend↔backend parity** (name + arity) is enforced by `scripts/check_endpoint_parity.py`, which derives the
  frontend surface from every `endpoint<[Args], Return>("name")` in `frontend/src/**/*.ts`. A rename lands on both sides
  or not at all.
