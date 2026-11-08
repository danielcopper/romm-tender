"""Every Steam internal the backend reaches in Steam, with what is lost without it.

Contract: the backend's half of the one list of Steam internals — every name of
Steam's client the strings the backend evaluates in Steam spell, the ``steam://``
URL it has Steam execute, the debugger target it attaches to and the origin
Steam's interface sends from. The panel's half is
``frontend/src/boot/steamInternals.ts``; what a Steam internal is, how a path is
spelled and what belongs on neither half is
``docs/architecture/frontend-bundles.md``, "Every Steam internal Tender
reaches".
"""

from __future__ import annotations

from dataclasses import dataclass

from domain.controller_focus import force_input_url


@dataclass(frozen=True)
class SteamInternal:
    """One Steam internal, and the feature that needs it.

    ``spelled_in`` names each constant that spells it, as
    ``<module>.<constant>`` under ``backend/``. ``not_spelled_by_steam`` is why
    Steam's own JavaScript never spells it, so the daily watch over Steam's
    published client (``scripts/watch_steam_internals.py``) passes it over.
    """

    path: str
    feature: str
    spelled_in: tuple[str, ...]
    not_spelled_by_steam: str | None = None


_RUNNING_APPS = "host.inject.recovery.RUNNING_APPS_EXPRESSION"
_RUNNING = "whether a game runs, which holds back replacing a stranded panel"

# The URL has one spelling, in ``domain/controller_focus.py``
# (``TestOnlyTheRulesSpellTheUrl``), so its path is taken from there.
_FORCE_INPUT = force_input_url(0).removesuffix("/0")

STEAM_INTERNALS: tuple[SteamInternal, ...] = (
    SteamInternal("SteamUIStore.RunningApps", _RUNNING, (_RUNNING_APPS,)),
    SteamInternal("SteamUIStore.RunningApps[].local_per_client_data.display_status", _RUNNING, (_RUNNING_APPS,)),
    SteamInternal("SteamUIStore.RunningApps[].display_name", _RUNNING, (_RUNNING_APPS,)),
    SteamInternal("SteamUIStore.RunningApps[].strDisplayName", _RUNNING, (_RUNNING_APPS,)),
    SteamInternal("SteamUIStore.RunningApps[].appid", _RUNNING, (_RUNNING_APPS,)),
    SteamInternal(
        "SteamClient.Browser.RestartJSContext",
        "replacing a stranded panel without restarting Steam",
        ("host.inject.recovery.RELOAD_EXPRESSION",),
        not_spelled_by_steam="a binding of Steam's native side its JavaScript never calls",
    ),
    SteamInternal(
        "SteamClient.URL.ExecuteSteamURL",
        "the focus watcher's controller layout pin",
        ("host.inject.injector._EXECUTE_STEAM_URL",),
    ),
    SteamInternal(
        _FORCE_INPUT,
        "the focus watcher's controller layout pin",
        ("domain.controller_focus._FORCE_INPUT_URL",),
        not_spelled_by_steam="a steam:// URL Steam's JavaScript never spells",
    ),
    SteamInternal(
        "webpackChunksteamui",
        "waiting for Steam's module registry before the panel is loaded",
        ("host.inject.bundles._STEAM_REGISTRY_READY",),
    ),
    SteamInternal(
        "SharedJSContext",
        "finding Steam's interface to load the panel into, and to free its memory",
        ("host.inject.cdp.SHARED_JS_CONTEXT", "adapters.renderer_gc._TARGET_TITLE"),
    ),
    SteamInternal(
        "https://steamloopback.host",
        "admitting the panel's connection, which arrives from Steam's interface",
        ("host.access.STEAM_UI_ORIGIN",),
    ),
)
