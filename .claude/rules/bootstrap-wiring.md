---
paths:
  - "backend/bootstrap/*.py"
  - "backend/main.py"
---

# Composition root and process boundaries

**Bootstrap (`bootstrap/`)**: `[CP]` The composition root — the only place concrete adapters meet services. `[ours]`
`adapters.py` instantiates every adapter and returns the typed bundles; `services.py` holds `WiringConfig` and turns
those bundles into service instances — protocols in, services out; `application.py` composes the two into the
`Application` (`build_application()`, synchronous, runs nothing) that holds the services and runs the start-up repairs,
the network step and the shutdown when the entry point asks. `__init__.py` is namespace plus re-exports only, so
consumers write `from bootstrap import …` and never deep-import a submodule. Adapter instantiation never happens in
`main.py` — a Protocol-wrapped persister is built in `bootstrap()` and passed through `CallbackBundle`.

**Process boundaries — `main.py` vs `bootstrap/`**: `[ours]` `main.py` owns the process entry point (`run()`) and
`Endpoints` (one public method marked `@route` per endpoint), which takes the `Application` and the host's status record
in its constructor and holds nothing else. `bootstrap/` owns adapter instantiation, service wiring and the
`Application`. The split is binding — no endpoints in `bootstrap/`, no service wiring in `main.py`.

`main.py` is also the **only** module that may import `host/`, which is an `.importlinter` contract in both directions.
Everything the host needs from the application it gets handed: a dispatcher, an event sink, and the directories the
entry point resolved. `bootstrap()` is **told** where those directories are and derives none of them. What the
`Application` needs from the host it takes as a plain callable — `run_startup_repairs` gets the failure recorder, never
the status record — and what the host needs from the `Application` it gets on the `BackendBuild`: the dispatcher, the
identity, `open_network` and `shutdown`.

What `bootstrap()` does NOT read is the program's own name and version: they are constants in `domain/identity.py`,
imported directly, and the outgoing User-Agent and the recovery root's name are composed from them. No seam, adapter or
Protocol stands between the two. The road not taken is a reader — a manifest under `directories.code_dir`, parsed at
boot behind a Protocol — and what it costs is a failure mode: a read has to answer something when the file is missing,
and the fallback it answers with travels all the way out to a server's token list. A constant cannot be missing.

**`run()` is synchronous, and that is load-bearing.** Everything it does is path and environment work that belongs
before a loop exists — and the admission token has to be minted before the first log line, since the formatter that
keeps it out of the log file is built with the file handler and takes the token as its argument. The `build` closure it
hands the host is `async` only because the host awaits it; it stays in `main.py`, which Sonar's S7503 exclusion covers,
and an `async def` that never awaits is not put into `bootstrap/`, which has none.

`main.py` grows with the callable surface it describes; that is unavoidable density, not god-class, and it is
deliberately out of scope for the module-size gate.

`bootstrap/` is **not** exempt. Every module in it is governed by the ~1000-LOC threshold in
`scripts/check_module_size.py` and none is grandfathered, so each has real headroom and a hard stop. A new adapter goes
into `adapters.py`, new wiring into `services.py`; when either reaches the threshold the answer is the next split along
the same seam (a wiring module per service cluster), never an allowlist entry.
