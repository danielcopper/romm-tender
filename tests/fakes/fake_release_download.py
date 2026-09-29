"""In-memory ``ReleaseAssetDownloadFn`` for service and contract tests — reaches no network."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable


class FakeReleaseDownload:
    """Writes the body it was given for a URL to the destination, reporting progress per chunk.

    ``bodies`` maps a URL to its bytes; a URL it does not hold raises, as does
    one named in ``failing``. ``chunk`` is how many bytes each progress call
    adds. ``calls`` records ``(url, dest)`` in order.
    """

    def __init__(
        self, *, bodies: dict[str, bytes] | None = None, failing: set[str] | None = None, chunk: int = 4
    ) -> None:
        self.bodies = dict(bodies or {})
        self.failing = set(failing or ())
        self.chunk = chunk
        self.calls: list[tuple[str, str]] = []

    def __call__(self, url: str, dest: str, progress: Callable[[int, int | None], None] | None) -> None:
        self.calls.append((url, dest))
        if url in self.failing or url not in self.bodies:
            raise OSError(f"no answer for {url}")
        body = self.bodies[url]
        if progress is not None:
            for done in range(self.chunk, len(body) + self.chunk, self.chunk):
                progress(min(done, len(body)), len(body))
        with open(dest, "wb") as out:
            out.write(body)
