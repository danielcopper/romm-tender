---
paths:
  - "backend/services/**"
---

# Services — Cosmic Python rules

Cosmic Python ("Architecture Patterns with Python", Percival & Gregory) is our north star, adapted for Tender, a
single-user RomM client. `[CP]` marks a canonical Cosmic Python rule — a hard rule, breaking it is an architectural
regression. `[ours]` marks a project convention layered on top — flag deviations in review, but the rule itself can be
debated.

Backend layout: `services/` (orchestration) / `adapters/` (I/O) / `domain/` (pure compute) / `lib/` (cross-cutting
utilities) / `models/` (data shapes). `import-linter` enforces direction. `[CP]`

## Rules

- `[CP]` Depend on Protocols (defined in `services/protocols/`, imported as `from services.protocols import X`), never
  on concrete adapter classes. Carve-out: sub-services within one bounded context (e.g. all of `services/saves/`) may
  hold concrete peer-service refs in their `*ServiceConfig` when they share an aggregate. `[ours]` A method a peer calls
  is part of that peer's **public** surface — no leading underscore.
- `[CP]` No raw I/O. `[ours]` Forbidden in `services/`: `os.*` (except pure path algebra: `relpath`, `join`, `splitext`,
  `basename`, `dirname`), `open(...)`, `pathlib.Path(...).read_*` / `write_*`, `fcntl.*`, `urllib.*`, `shutil.*`,
  `subprocess.*`, `hashlib.<x>(open(...))`.
- `[CP]` No clocks or randomness — inject `Clock` / `UuidGen` / `Sleeper`. `time.time()` / `time.monotonic()` /
  `datetime.now()` / `uuid.uuid4()` / `asyncio.sleep()` / `random.*` are banned at the call site.
- `[CP]` No service-to-service concrete imports — services are independent; cross-service deps are Protocol-typed.
- `[ours]` A service creates a task only through a `BackgroundTasks` (`lib/background_tasks.py`) it builds with its
  logger — `spawn` for a task nobody awaits, `start_awaited` for one it awaits itself; a task `gather` or `shield` makes
  from a coroutine awaited on the spot is the other kind. A service with a place in `Application.shutdown` cancels and
  awaits its tasks there, through the helper's `shutdown()` unless it stops them itself, as the cleanup does.
  `scripts/check_background_tasks.py` fails on any other `create_task` / `ensure_future` / `asyncio.Task(...)`.
- `[ours]` Module functions from `domain/` are still a coupling — if tests need `patch("services.X.module_name.fn")`,
  wrap the module behind a Protocol and inject it.
- `[ours]` **Constructor shape: every service takes a single `config: XxxServiceConfig` keyword argument.** Frozen
  dataclass. Outer services keep the `Service` token in both names (`SteamGridService` + `SteamGridServiceConfig`);
  sub-services may use role-based names (`SyncEngine` + `SyncEngineConfig`). All deps live in the config: Protocol-typed
  adapters, infrastructure (loop, logger, clock, uuid_gen, sleeper), persistence callbacks, settings-derived values. No
  bare-param ctors, no mixed ctors.
- `[ours]` **Debug logging: inject the `DebugLogger` Protocol.** No per-service `_log_debug` that re-reads settings at
  call time, and no reaching for a module-level logger to bypass log-level filtering — a service is handed its logger.
- `[ours]` **A service refuses by raising, never by returning the failure shape** — `Refused` (`lib/errors.py`), with
  the reason spelled as `.claude/rules/endpoints.md` says, or a `NamedRefused` subclass that declares only
  `reason = "…"`; a domain rule raises `DomainRefused` or a `NamedDomainRefused` subclass (`domain/refusal.py`). Work
  that stopped partway returns a `PartialFailure` (`lib/partial_failure.py`) subclass that carries what was done. No
  module but `main.py` builds the shape from a literal; `scripts/check_failure_shape.py --check` fails on one built
  anywhere else (`.claude/rules/endpoints.md`), not on a missing raise.
- `[ours]` God-class signal: services > ~1000 LOC — decompose into sub-services with constructor injection
  (`services/saves/` is the reference). Enforced by `scripts/check_module_size.py`: no module may cross the threshold.
  None is grandfathered any more, and nothing is ever added to its `ALLOWLIST`.

## Reference shape for new service-level work

A Protocol (in `services/protocols/`) + an adapter implementing it + a `FakeXxxAdapter` in `conftest` + `*ServiceConfig`
ctor decomposition. `services/saves/` and `services/library/` are the reference decompositions for shared-state
sub-services. Sequencing for a new vertical: cross-cutting Protocols first, domain extraction next, the biggest service
last.

If a refactor breaks a `[CP]` rule, that's an architectural regression — call it out and fix it in the same PR or open a
follow-up. `[ours]` deviations should be flagged in review but can be debated.
