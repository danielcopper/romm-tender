"""A recording ``ShortcutIconJob`` for the services that start and stop the shortcut icon job."""

from __future__ import annotations


class FakeShortcutIconJob:
    """Records every request for a run, every stop and every resume; answers a fixed placeholder path.

    ``placeholder`` is what :meth:`placeholder_path` answers; set it to ``None``
    for a machine where the placeholder cannot be written.
    """

    def __init__(self, *, placeholder: str | None = "/grid/tender-icon-placeholder.png") -> None:
        self.placeholder = placeholder
        self.requests: list[bool] = []
        self.stops = 0
        self.resumes = 0
        self.removal_stops = 0

    def request_run(self, *, settle: bool = False) -> None:
        self.requests.append(settle)

    async def stop_for_cleanup(self) -> None:
        self.stops += 1

    def resume_after_cleanup(self) -> None:
        self.resumes += 1

    async def stop_for_removal(self) -> None:
        self.removal_stops += 1

    async def placeholder_path(self) -> str | None:
        return self.placeholder
