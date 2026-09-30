"""Composition root — the only place concrete adapters meet services.

``adapters.py`` owns adapter instantiation and the typed bundles it
hands on; ``services.py`` turns those bundles into the live service
instances; ``application.py`` composes the two into the
:class:`Application` ``main.py`` runs, whose start-up repairs each run
through the wrapper in ``startup.py``. The names re-exported below are
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
from .services import ServicesBundle, WiringConfig, wire_services

__all__ = [
    "AdapterBundle",
    "Application",
    "BootstrapResult",
    "CallbackBundle",
    "RuntimeAdaptersBundle",
    "RuntimeBundle",
    "ServicesBundle",
    "StateBundle",
    "WiringConfig",
    "bootstrap",
    "build_application",
    "wire_services",
]
