---
paths:
  - "backend/main.py"
  - "frontend/src/api/**"
---

# Callable response shapes `[ours]`

Callables returning a plain `dict` that can fail use `{success: False, reason: ErrorCode | str, message: str}`. Both
`reason` and `message` are **required**. Reuse `lib.list_result.ErrorCode` for coarse categories; bespoke guards
(`config_error`, `sync_disabled`, `not_installed`, …) stay plain-string reasons. Transport failures collapse onto
`SERVER_UNREACHABLE`; 401 and 403 collapse onto `AUTH_FAILED` (same slug, distinct `message`). The legacy `error_code`
key and a second `error` key are **forbidden**. Enforced by `scripts/check_failure_shape.py --check`.

Two carve-outs (pattern-exempt in the gate):

- **Discriminated-status unions** (`status: "ok" | "server_unreachable" | …`, used by the saves version-history
  callables) keep the `status` discriminant instead of `success` — more than two outcomes. Failure branches still carry
  `message: str`.
- **Partial-success responses** returning a full payload alongside a failure flag (`get_save_status`'s
  `server_query_failed: bool`, `get_save_setup_info`'s `recommended_action`) keep the additive flag.

Full convention paragraph: the `lib/list_result.py` module docstring.

Two adjacent rules that bite when adding or changing a callable:

- **A callable must be `async def`** — even where the body is synchronous. The set a caller can reach is exactly the
  public `async def` on `Plugin`: `host.dispatch.reachable_methods` resolves it off the loaded class and
  `scripts/check_callable_manifest.py` derives the same set from the source, and `tests/host/test_dispatch.py` asserts
  the two are equal. A synchronous method is not reachable at all, and a public async one is reachable whether or not
  that was intended.
- **Frontend↔backend parity** (name + arity) is enforced by `scripts/check_callable_manifest.py`, which derives the
  frontend surface from every `callable<[Args], Return>("name")` in `frontend/src/**/*.ts`. A rename lands on both sides
  or not at all.
