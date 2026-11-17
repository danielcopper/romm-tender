"""Composition root — the only place concrete adapters meet services.

``adapters.py`` owns adapter instantiation and the typed bundles it
hands on; ``services.py`` turns those bundles into the live service
instances, and ``library.py`` and ``saves.py`` build the library and save
services' parts for it; ``application.py`` composes the adapters and the
services into the :class:`Application` ``main.py`` runs, whose start-up
repairs each run through the wrapper in ``startup.py``; ``check.py`` copies
the live data a pre-install check builds on. The names re-exported below are
the composition root's whole public surface — consumers import them
from ``bootstrap``, never from a submodule.
"""

from .adapters import (
    AdapterBundle,
    BootstrapResult,
    CallbackBundle,
    RuntimeAdaptersBundle,
    RuntimeBundle,
    StateBundle,
    bootstrap,
)
from .application import Application, build_application
from .check import copy_live_data
from .library import LibraryParts, LibraryWiring, wire_library
from .saves import SaveParts, SaveWiring, wire_saves
from .services import ServicesBundle, WiringConfig, wire_services

__all__ = [
    "AdapterBundle",
    "Application",
    "BootstrapResult",
    "CallbackBundle",
    "LibraryParts",
    "LibraryWiring",
    "RuntimeAdaptersBundle",
    "RuntimeBundle",
    "SaveParts",
    "SaveWiring",
    "ServicesBundle",
    "StateBundle",
    "WiringConfig",
    "bootstrap",
    "build_application",
    "copy_live_data",
    "wire_library",
    "wire_saves",
    "wire_services",
]
