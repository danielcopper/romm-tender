"""Loading this backend's panel into Steam, over the CEF debugger.

Contract: everything about reaching Steam's renderer from outside it — the
protocol client, the decision about what to load, the source that is evaluated
there, and the record that stops the whole thing if loading the panel is taking
the interface down.

It is a sub-package rather than four modules beside the server because they share
one internal type and one job: ``cdp.CdpConnection`` is the transport
``injector.PanelInjector`` drives, and ``bundles``, ``bootstrap`` and ``watchdog``
each answer one question that injector asks and nobody else does.

The two names below are its whole public surface; ``host/runtime.py`` starts the
injector once the port is bound, and nothing else in this backend knows it exists.
"""

from host.inject.injector import InjectionSetup, PanelInjector

__all__ = ["InjectionSetup", "PanelInjector"]
