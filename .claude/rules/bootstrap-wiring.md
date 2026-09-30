---
paths:
  - "backend/bootstrap/*.py"
  - "backend/main.py"
  - "backend/check.py"
---

# Composition root and process boundaries

**Bootstrap (`bootstrap/`)**: `[CP]` The composition root — the only place concrete adapters meet services. `[ours]`
`adapters.py` instantiates every adapter and returns the typed bundles; `services.py` holds `WiringConfig` and turns
those bundles into service instances — protocols in, services out; `application.py` composes the two into the
`Application` (`build_application()`, synchronous, runs nothing) that holds the services and runs the start-up repairs,
the network step and the shutdown when the entry point asks; `check.py` copies the live data a pre-install check builds
on (`copy_live_data`). `__init__.py` is namespace plus re-exports only, so consumers write `from bootstrap import …` and
never deep-import a submodule. Adapter instantiation never happens in `main.py` — a Protocol-wrapped persister is built
in `bootstrap()` and passed through `CallbackBundle`.

**Process boundaries — `main.py` vs `bootstrap/`**: `[ours]` `main.py` owns the process entry point (`run()` and
`build_backend()`) and `Endpoints` (one public method marked `@route` per endpoint), which takes the `Application` and
the host's status record in its constructor and holds nothing else. `bootstrap/` owns adapter instantiation, service
wiring and the `Application`. The split is binding — no endpoints in `bootstrap/`, no service wiring in `main.py`.

`check.py` beside `main.py` is the second entry point, the pre-install check's (`check()`): it builds the `Application`
on copies of the live data and runs nothing of it — no start-up repair, no network step, no host. It is a file of its
own rather than a flag `main.py` reads, because the installer runs it on the NEW version, and a `main.py` from before
the flag would ignore it and start a whole backend; `main.py` reads no arguments. It imports what it needs of `host/` —
the stderr logging, and the event sink and Steam reader `build_application` takes — as `main.py` does. It also imports
`main.py` itself, which the build never reaches, so a `main.py` that does not import is refused too — and so nothing in
`main.py` may run at import: its start stays behind `__name__ == "__main__"`.

`main.py` and `check.py` are also the **only** modules that may import `host/`, which is an `.importlinter` contract in
both directions. Everything the host needs from the application it gets handed: a dispatcher, an event sink, and the
directories the entry point resolved. `bootstrap()` is **told** where those directories are and derives none of them.
What the `Application` needs from the host it takes as a plain callable or a Protocol-typed reader, never the status
record — `run_startup_repairs` gets the failure recorder, and `build_application` the event sink's emit and the host's
reading of Steam (`SteamInterfaceReader`) — and what the host needs from the `Application` it gets on the
`BackendBuild`: the dispatcher, the identity, `open_network` and `shutdown`.

What `bootstrap()` does NOT read is the program's own name and version: they are constants in `domain/identity.py`,
imported directly, and the outgoing User-Agent and the recovery root's name are composed from them. No seam, adapter or
Protocol stands between the two. The road not taken is a reader — a manifest under `directories.code_dir`, parsed at
boot behind a Protocol — and what it costs is a failure mode: a read has to answer something when the file is missing,
and the fallback it answers with travels all the way out to a server's token list. A constant cannot be missing.

**`run()` is synchronous, and that is load-bearing.** Everything it does is path and environment work that belongs
before a loop exists — and the admission token has to be minted before the first log line, since the formatter that
keeps it out of the log file is built with the file handler and takes the token as its argument. `build_backend`, which
it hands the host, is `async` only because the host awaits it; it stays in `main.py`, which Sonar's S7503 exclusion
covers, and an `async def` that never awaits is not put into `bootstrap/`, which has none.

`main.py` grows with the endpoint surface it describes; that is unavoidable density, not god-class, and it is
deliberately out of scope for the module-size gate.

`bootstrap/` is **not** exempt. Every module in it is governed by the ~1000-LOC threshold in
`scripts/check_module_size.py` and none is grandfathered, so each has real headroom and a hard stop. A new adapter goes
into `adapters.py`, new wiring into `services.py`; when either reaches the threshold the answer is the next split along
the same seam (a wiring module per service cluster), never an allowlist entry.
