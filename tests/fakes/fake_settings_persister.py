"""In-memory ``SettingsPersister`` implementation for service tests."""

from __future__ import annotations


class FakeSettingsPersister:
    """In-memory ``SettingsPersister`` for tests.

    Counts how many times ``save_settings()`` was invoked. Tests use
    ``save_count`` to assert the persister was triggered without
    standing up a real on-disk write.
    """

    def __init__(self) -> None:
        self.save_count = 0

    def save_settings(self) -> None:
        self.save_count += 1


class FailingSettingsPersister(FakeSettingsPersister):
    """A ``SettingsPersister`` whose every save raises *error* — ``OSError("disk full")`` where none is given."""

    def __init__(self, error: BaseException | None = None) -> None:
        super().__init__()
        self.error = error if error is not None else OSError("disk full")

    def save_settings(self) -> None:
        raise self.error
