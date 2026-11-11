"""Slot lifecycle for save sync.

Anything that creates, lists, switches, migrates, or deletes slots
lives here, including the first-sync setup wizard. The matrix
executor lives in ``sync_engine/matrix.py`` and the sync orchestration
in SyncEngine; status reporting in StatusService; persistence is each operation's own
narrow Unit of Work (ADR-0006).
"""

from services.saves.slots.deletion import SlotDeleter
from services.saves.slots.listing import SlotListing
from services.saves.slots.setup import SetupWizard
from services.saves.slots.switching import SlotSwitcher

__all__ = ["SetupWizard", "SlotDeleter", "SlotListing", "SlotSwitcher"]
