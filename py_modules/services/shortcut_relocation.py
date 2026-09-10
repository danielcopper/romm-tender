"""ShortcutRelocationService — pointing the existing shortcuts at the launcher's home.

Owns one question the frontend asks at start-up: which Steam shortcuts still
name a launcher inside a plugin folder, and may they be repointed. Nothing here
writes to Steam — the frontend owns every shortcut mutation — and nothing here
decides where the launcher lives; the composition root settles that and hands
the answer in.

A one-time transition task with a recorded completion, in the shape of a schema
migration: once a run has repointed everything a reading found, the completion
is stamped and no later start reads Steam's shortcut file again.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from domain.shortcut_data import select_shortcuts_to_relocate

if TYPE_CHECKING:
    import asyncio
    import logging

    from services.protocols import SteamConfigStore, UnitOfWorkFactory

# The completion stamp. A derived marker about work THIS install finished — not
# a user intent — so it lives in ``kv_config`` beside the other cross-run
# markers rather than in ``settings.json`` (CONTEXT.md, persistence boundary).
KV_RELOCATION_DONE = "shortcut_launcher_relocated"


@dataclass(frozen=True)
class ShortcutRelocationServiceConfig:
    """Frozen wiring bundle handed to ``ShortcutRelocationService.__init__``.

    ``launcher_exe`` is where the launcher belongs and ``launcher_at_home``
    whether this start actually got it there — two answers the composition root
    settles, threaded in rather than re-derived, because the second one also
    carries the data-migration ordering: a start whose data half has not landed
    installs no launcher at all.
    """

    launcher_exe: str
    launcher_at_home: bool
    steam_config: SteamConfigStore
    uow_factory: UnitOfWorkFactory
    loop: asyncio.AbstractEventLoop
    logger: logging.Logger


class ShortcutRelocationService:
    """Answers which shortcuts still have to be repointed, and records when none do."""

    def __init__(self, *, config: ShortcutRelocationServiceConfig) -> None:
        self._launcher_exe = config.launcher_exe
        self._launcher_at_home = config.launcher_at_home
        self._steam_config = config.steam_config
        self._uow_factory = config.uow_factory
        self._loop = config.loop
        self._logger = config.logger

    async def get_shortcut_relocation(self) -> dict[str, Any]:
        """Report what the frontend still has to repoint, if anything.

        Returns a discriminated status union (``.claude/rules/callables.md``):

        - ``{"status": "done"}`` — no shortcut of ours names a plugin folder any
          more. Either the completion is already stamped, in which case nothing
          at all is read, or this call read the file and found nothing to do and
          stamped it.
        - ``{"status": "outstanding", "exe", "start_dir", "app_ids"}`` — those
          app IDs carry a launcher path that is not ``exe``. The frontend writes
          both fields on each and then calls
          :meth:`complete_shortcut_relocation`.
        - ``{"status": "blocked", "message"}`` — nothing may be repointed yet:
          the launcher is not at its home (the data migration is still
          outstanding, or the install failed), or Steam's shortcut file could
          not be read. Never stamped, so the next start asks again.

        The blocked answer is the one that matters. Repointing a shortcut at a
        launcher that is not there stops its game from starting and nothing in
        this plugin could put the file back, so every uncertainty resolves to
        it — and the panel keeps warning against removing the pre-rename
        install, which is still load-bearing exactly while this is the answer.
        """
        if await self._already_done():
            return {"status": "done"}
        if not self._launcher_at_home:
            return {
                "status": "blocked",
                "message": "The launcher is not at its home yet, so no shortcut may be pointed at it.",
            }
        exes = await self._loop.run_in_executor(None, self._steam_config.read_shortcut_exes)
        if exes is None:
            return {"status": "blocked", "message": "Steam's shortcut file could not be read."}
        app_ids = select_shortcuts_to_relocate(exes, self._launcher_exe)
        if not app_ids:
            await self._stamp_done()
            return {"status": "done"}
        self._logger.info(f"{len(app_ids)} shortcut(s) still point at a launcher other than {self._launcher_exe}")
        return {
            "status": "outstanding",
            "exe": self._launcher_exe,
            "start_dir": os.path.dirname(self._launcher_exe),
            "app_ids": app_ids,
        }

    async def complete_shortcut_relocation(self) -> dict[str, Any]:
        """Record that a run repointed everything the reading found.

        Called by the frontend once its writes are done AND one of them has been
        read back from Steam carrying the launcher's home
        (``src/utils/launcherRelocation.ts``). That read-back is what this stamp
        rests on: ``SetShortcutExe`` returns nothing, and the run was planned off
        ``shortcuts.vdf`` — which Steam rewrites from its own memory, and which
        ``find_steam_user_dir`` picks by modification time where a machine has
        more than one Steam account — so a run that wrote to the wrong place, or
        to nothing, would otherwise report success. Idempotent.

        **The gap this leaves, deliberately.** The stamp is permanent and
        nothing clears it: a shortcut that turns up later carrying the old path
        — restored from a backup, written by a downgraded build — stays on it,
        and no start will look again. It keeps launching, because the package
        still ships ``bin/rom-launcher`` at that path; what it does NOT keep is
        the panel's agreement, since the card reads this stamp as "nothing points
        into the pre-rename install any more" and offers its removal. Clearing
        the stamp on Force Full Sync was considered and rejected: it would only
        ever reach a user who had already diagnosed the shortcut, and that button
        carries enough meanings already.
        """
        await self._stamp_done()
        return {"success": True}

    async def _already_done(self) -> bool:
        return await self._loop.run_in_executor(None, self._read_done_io)

    def _read_done_io(self) -> bool:
        with self._uow_factory() as uow:
            return uow.kv_config.get(KV_RELOCATION_DONE) == "1"

    async def _stamp_done(self) -> None:
        await self._loop.run_in_executor(None, self._stamp_done_io)

    def _stamp_done_io(self) -> None:
        with self._uow_factory() as uow:
            uow.kv_config.set(KV_RELOCATION_DONE, "1")
