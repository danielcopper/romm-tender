---
paths:
  - "backend/bootstrap/*.py"
  - "backend/main.py"
---

# Composition root and process boundaries

**Bootstrap (`bootstrap/`)**: `[CP]` The composition root — the only place concrete adapters meet services. `[ours]`
`adapters.py` instantiates every adapter and returns the typed bundles; `services.py` holds `WiringConfig` and turns
those bundles into service instances — protocols in, services out. `__init__.py` is namespace plus re-exports only, so
consumers write `from bootstrap import …` and never deep-import a submodule. Adapter instantiation never happens in
`main.py` — a Protocol-wrapped persister is built in `bootstrap()` and passed through `CallbackBundle`.

**Process boundaries — `main.py` vs `bootstrap/`**: `[ours]` `main.py` owns the lifecycle (`_main`, `_open_network`,
`_unload`), the process entry point (`Plugin.run`) and the callable surface (one public `async def` per callable).
`bootstrap/` owns adapter instantiation and service wiring. The split is binding — no callables in `bootstrap/`, no
service wiring in `main.py`.

`main.py` is also the **only** module that may import `host/`, which is an `.importlinter` contract in both directions.
Everything the host needs from the application it gets handed: a dispatcher, an event sink, and the directories the
entry point resolved. `bootstrap()` is **told** where those directories are and derives none of them.

What `bootstrap()` does NOT read is the program's own name and version: they are constants in `domain/identity.py`,
imported directly, and the outgoing User-Agent and the recovery root's name are composed from them. No seam, adapter or
Protocol stands between the two. The road not taken is a reader — a manifest under `directories.code_dir`, parsed at
boot behind a Protocol — and what it costs is a failure mode: a read has to answer something when the file is missing,
and the fallback it answers with travels all the way out to a server's token list. A constant cannot be missing.

**`Plugin.run` is a `classmethod` and synchronous, and both halves are load-bearing.** Synchronous because everything it
does is path and environment work that belongs before a loop exists — and because the admission token has to be minted
before the first log line, since the formatter that keeps it out of the log file is built with the file handler and
takes the token as its argument. A method on the class rather than a module function because the lifecycle it drives is
private, and a module-level caller would be reaching across the class boundary to use it. It must stay **non-async**:
every public `async def` on `Plugin` is a callable, both to the manifest gate and to the dispatcher.

`main.py` grows with the callable surface it describes; that is unavoidable density, not god-class, and it is
deliberately out of scope for the module-size gate.

`bootstrap/` is **not** exempt. Both modules are governed by the ~1000-LOC threshold in `scripts/check_module_size.py`
and neither is grandfathered, so each has real headroom and a hard stop. A new adapter goes into `adapters.py`, new
wiring into `services.py`; when either reaches the threshold the answer is the next split along the same seam (a wiring
module per service cluster), never an allowlist entry.
