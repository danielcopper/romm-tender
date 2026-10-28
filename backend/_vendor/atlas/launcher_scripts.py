"""EmuDeck's launcher scripts — what each runs beside the emulator, pinned file by file (#573).

ES-DE on EmuDeck runs some emulators through a launcher script under
``<toolsPath>/launchers``, and the script runs steps of its own around the
emulator: it updates EmuDeck's backend, syncs saves, stops and starts a
service. What a script does is read at one backend revision and packaged per
script (``atlas/data/emudeck_launchers.json``), and a reading holds only for
exactly the files it was made from: the script, keyed by the git blob hash of
its deployed copy, every backend file it sources (``sources``, which every
script shares through ``functions/all.sh``) and every file it runs or copies
on its launch path, itself or through a function it reaches (``executes``),
each pinned by its own blob hash. The backend revision is
provenance, not the gate: the backend updates itself on every launch, and the
pins say which files the reading is true for, however the rest of it moves.

A step's ``runs`` is stated in the table where the script decides it alone —
``true``, ``false``, or ``null`` where state at launch decides it — and as
``settings`` where EmuDeck's settings and the machine's files decide it — the
cloud sync settings and the rclone binary, ``autoMap`` and the device's
product name for an emulator's own fix, Ryujinx's binary and configuration
for its reinstall — which :func:`step_runs` reads off a :class:`LauncherState`.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from ._data import packaged_text
from .launch_command import (
    EMUDECK_VARIANTS,
    LAUNCHER_STEPS,
    STEP_CLOUD_SYNC_CHECK,
    STEP_CLOUD_SYNC_DOWNLOAD,
    STEP_CLOUD_SYNC_UPLOAD,
    STEP_EMULATOR_INSTALL,
    STEP_LAUNCH_FIXES,
    STEP_SERVICE_START,
    EmuDeckVariant,
    LauncherStepCode,
)

LAUNCHER_SCRIPTS_SCHEMA = 1

RunsBySettings = Literal["settings"]
RUNS_BY_SETTINGS: RunsBySettings = "settings"

# The variables a pinned file's path starts with: the backend checkout, and
# the two folders EmuDeck's settings name.
BACKEND_ROOT = "$emudeckBackend/"
TOOLS_ROOT = "$toolsPath/"
EMULATION_ROOT = "$emulationPath/"
_ROOTS = (BACKEND_ROOT, TOOLS_ROOT, EMULATION_ROOT)

# A cloud sync provider EmuDeck accepts carries this in its name
# (functions/ToolScripts/emuDeckCloudSync.sh:437 at the pinned revision).
_PROVIDER_MARK = "Emudeck"


@dataclass(frozen=True, slots=True)
class PinnedStep:
    """One step of a pinned script: its code, how its running is decided, and the lines that say so."""

    code: LauncherStepCode
    runs: bool | None | RunsBySettings
    citation: str


@dataclass(frozen=True, slots=True)
class LauncherRule:
    """What one pinned script runs: the emulator name it hands EmuDeck, the build it starts, its steps in order."""

    script: str
    blob: str
    emulator: str
    variant: EmuDeckVariant
    variant_citation: str
    executes: Mapping[str, str]
    steps: tuple[PinnedStep, ...]


@dataclass(frozen=True, slots=True)
class LauncherRules:
    """Every pinned script by its blob hash, the files they all source, and the revision they were read at."""

    revision: str
    sources: Mapping[str, str]
    by_blob: Mapping[str, LauncherRule]


@dataclass(frozen=True, slots=True)
class LauncherState:
    """What a ``settings`` step turns on: EmuDeck's settings, and the files on the machine its tests read.

    Settings are as ``settings.sh`` spells them, empty where it sets none.
    ``rclone`` is whether ``$toolsPath/rclone/rclone`` is a file;
    ``product`` the device's product name EmuDeck reads
    (``getProductName``), empty where no such file is there; ``gyro`` whether
    the SteamDeckGyroDSU binary is there; ``ryujinx_installed`` whether
    Ryujinx's binary is, and ``ryujinx_sdl2`` whether its configuration still
    names an SDL2 gamepad backend. Each is ``None`` where it cannot be told.
    """

    rclone: bool | None
    status: str
    provider: str
    net_play: str
    auto_map: str = ""
    product: str | None = ""
    gyro: bool | None = None
    ryujinx_installed: bool | None = None
    ryujinx_sdl2: bool | None = None


def step_runs(
    code: LauncherStepCode, runs: bool | None | RunsBySettings, state: LauncherState, emulator: str = ""
) -> bool | None:
    """Whether a step runs on this launch: the table's own answer, or what the settings decide for a ``settings`` one.

    The tests are EmuDeck's, cited per step in the table. A cloud sync acts
    only where the rclone binary is there and ``cloud_sync_status`` is
    ``true``, and what it then does turns on the network and the save folder's
    markers at launch (``null``), except where the provider is not one
    EmuDeck accepts, which shows a dialog instead. A launch fix is decided by
    the guards of *emulator*'s own fix (:data:`_FIXES`), and an emulator
    install by the condition Ryujinx's fix checks before it reinstalls. What
    cannot be told answers ``null`` wherever it decides.
    """
    if runs != RUNS_BY_SETTINGS:
        return runs
    if code in _BY_EMULATOR:
        return _BY_EMULATOR[code][emulator](state)
    return _BY_SETTINGS[code](state)


def _enabled(state: LauncherState) -> bool:
    return state.status == "true"


def _accepted(state: LauncherState) -> bool:
    return _PROVIDER_MARK in state.provider


def _download_runs(state: LauncherState) -> bool | None:
    if not _enabled(state):
        return False
    if state.rclone is not True:
        return state.rclone
    return None if _accepted(state) else True


def _service_start_runs(state: LauncherState) -> bool | None:
    # The download returns 0 without a binary or after the provider dialog, and the restart follows it.
    if not _enabled(state):
        return False
    if state.rclone is False or (state.rclone and not _accepted(state)):
        return True
    return None


def _check_runs(state: LauncherState) -> bool | None:
    if not _enabled(state) or state.net_play == "true":
        return False
    return state.rclone


def _upload_runs(state: LauncherState) -> bool | None:
    return None if _enabled(state) else False


# The steps whose running EmuDeck's settings decide alone — the only ones the
# table may mark ``settings`` for any script — and how each is decided.
_BY_SETTINGS = {
    STEP_CLOUD_SYNC_DOWNLOAD: _download_runs,
    STEP_SERVICE_START: _service_start_runs,
    STEP_CLOUD_SYNC_CHECK: _check_runs,
    STEP_CLOUD_SYNC_UPLOAD: _upload_runs,
}

# The hardware EmuDeck's controller fixes leave alone: Steam Deck's product
# names (functions/EmuScripts/emuDeckDolphin.sh:202, emuDeckCemu.sh:350,
# emuDeckRyujinx.sh:342 and :439 at the pinned revision).
_STEAM_DECKS = frozenset({"Jupiter", "Galileo"})


def _on_deck(state: LauncherState) -> bool | None:
    return None if state.product is None else state.product in _STEAM_DECKS


def _gamepad_fix_runs(state: LauncherState) -> bool | None:
    """Dolphin's and Cemu's fix: the controller mapping, which returns first on a Steam Deck and then with autoMap off."""
    if state.auto_map == "false":
        return False
    on_deck = _on_deck(state)
    return None if on_deck is None else not on_deck


def _ryujinx_fix_runs(state: LauncherState) -> bool | None:
    """Ryujinx's fix, its reinstall aside: the gyro plugin installed on a Steam Deck that lacks it, and elsewhere the
    controller mapping, unless autoMap is off.
    """
    on_deck = _on_deck(state)
    if on_deck is None:
        return None
    if on_deck:
        return None if state.gyro is None else not state.gyro
    return state.auto_map != "false"


def _ryujinx_install_runs(state: LauncherState) -> bool | None:
    """Ryujinx's reinstall: where Ryujinx's binary is there and its configuration still names an SDL2 gamepad."""
    if state.ryujinx_installed is False or state.ryujinx_sdl2 is False:
        return False
    if state.ryujinx_installed is None or state.ryujinx_sdl2 is None:
        return None
    return True


# The steps an emulator's own fix decides, by the emulator name a script hands
# emulatorInit — the only emulators the table may mark them ``settings`` for.
_FIXES = {"dolphin": _gamepad_fix_runs, "Cemu": _gamepad_fix_runs, "ryujinx": _ryujinx_fix_runs}
_BY_EMULATOR = {STEP_LAUNCH_FIXES: _FIXES, STEP_EMULATOR_INSTALL: {"ryujinx": _ryujinx_install_runs}}

# Ryujinx's fix reinstalls where its configuration's input_config still names
# a gamepad backend starting with this (emuDeckRyujinx.sh:445-446).
_SDL2_BACKEND = "GamepadSDL2"


def ryujinx_names_sdl2(text: str) -> bool | None:
    """Whether a Ryujinx ``Config.json`` names an SDL2 gamepad backend, the way the fix's ``jq`` filter reads it —
    ``None`` where atlas cannot tell what that filter makes of the text.

    The filter is ``[.input_config[]? | .backend? // ""] | any(startswith("GamepadSDL2"))``:
    every entry of ``input_config``, an entry without a ``backend`` read as
    the empty string. Text that does not parse as one JSON document, and a
    ``backend`` that is no string, are where ``jq`` and this reading could
    differ, so they answer ``None``.
    """
    try:
        document = json.loads(text)
    except ValueError:
        return None
    configs = document.get("input_config") if isinstance(document, dict) else None
    entries: list[Any] = []
    if isinstance(configs, list):
        entries = configs
    elif isinstance(configs, dict):
        entries = list(configs.values())
    for entry in entries:
        backend = entry.get("backend") if isinstance(entry, dict) else None
        if backend is None or backend is False:
            backend = ""
        if not isinstance(backend, str):
            return None
        if backend.startswith(_SDL2_BACKEND):
            return True
    return False


def load_launcher_rules(text: str | None = None) -> LauncherRules:
    """Load the packaged launcher-script table (or *text* when supplied, for tests), validated, never coerced."""
    if text is None:
        text = packaged_text("emudeck_launchers.json")
    raw = json.loads(text)
    if not isinstance(raw, dict) or raw.get("schema") != LAUNCHER_SCRIPTS_SCHEMA:
        raise ValueError(
            f"emudeck_launchers: unsupported schema {raw.get('schema') if isinstance(raw, dict) else None!r} "
            f"(this atlas reads schema {LAUNCHER_SCRIPTS_SCHEMA})"
        )
    if set(raw) != {"schema", "spec", "backend", "sources", "scripts"}:
        raise ValueError(f"emudeck_launchers: unexpected top-level keys {sorted(raw)}")
    backend = raw["backend"]
    if not isinstance(backend, dict) or not _is_hash(backend.get("revision")):
        raise ValueError("emudeck_launchers: backend names the revision the scripts were read at, as a full hash")
    sources = _pinned_files("sources", raw["sources"], roots=(BACKEND_ROOT,))
    scripts = raw["scripts"]
    if not isinstance(scripts, list) or not scripts:
        raise ValueError("emudeck_launchers: expected a non-empty 'scripts' list")
    by_blob: dict[str, LauncherRule] = {}
    for item in scripts:
        rule = _rule(item)
        if rule.blob in by_blob:
            raise ValueError(f"emudeck_launchers: two scripts pinned by one blob {rule.blob}")
        by_blob[rule.blob] = rule
    return LauncherRules(backend["revision"], sources, by_blob)


def _is_hash(value: object) -> bool:
    return isinstance(value, str) and len(value) == 40 and all(char in "0123456789abcdef" for char in value)


def _pinned_files(where: str, value: Any, *, roots: tuple[str, ...] = _ROOTS) -> Mapping[str, str]:
    if not isinstance(value, dict):
        raise ValueError(f"emudeck_launchers {where}: expected an object of path → blob hash")
    for path, blob in value.items():
        if not isinstance(path, str) or not path.startswith(roots) or ".." in path.split("/"):
            raise ValueError(f"emudeck_launchers {where}: {path!r} starts with none of {roots}")
        if not _is_hash(blob):
            raise ValueError(f"emudeck_launchers {where}: {path!r} is pinned by a full blob hash, got {blob!r}")
    return dict(value)


_RULE_KEYS = {"script", "blob", "emulator", "variant", "variant_citation", "executes", "steps"}


def _rule(item: Any) -> LauncherRule:
    if not isinstance(item, dict) or set(item) != _RULE_KEYS:
        raise ValueError(f"emudeck_launchers: a script names exactly {sorted(_RULE_KEYS)}, got {item!r}")
    where = f"script {item['script']!r}"
    if not _is_hash(item["blob"]):
        raise ValueError(f"emudeck_launchers {where}: blob is a full blob hash")
    if item["variant"] not in EMUDECK_VARIANTS:
        raise ValueError(f"emudeck_launchers {where}: variant must be one of {EMUDECK_VARIANTS}")
    for key in ("script", "emulator", "variant_citation"):
        if not isinstance(item[key], str) or not item[key].strip():
            raise ValueError(f"emudeck_launchers {where}: {key} is a non-empty string")
    steps = item["steps"]
    if not isinstance(steps, list) or not steps:
        raise ValueError(f"emudeck_launchers {where}: steps is a non-empty list")
    parsed = tuple(_step(where, step, item["emulator"]) for step in steps)
    order = [LAUNCHER_STEPS.index(step.code) for step in parsed]
    if order != sorted(set(order)):
        raise ValueError(f"emudeck_launchers {where}: steps appear once each, in the order LAUNCHER_STEPS runs them")
    return LauncherRule(
        item["script"],
        item["blob"],
        item["emulator"],
        item["variant"],
        item["variant_citation"],
        _pinned_files(f"{where} executes", item["executes"]),
        parsed,
    )


def _step(where: str, step: Any, emulator: str) -> PinnedStep:
    if not isinstance(step, dict) or set(step) != {"code", "runs", "citation"}:
        raise ValueError(f"emudeck_launchers {where}: a step names exactly code, runs and citation, got {step!r}")
    code, runs = step["code"], step["runs"]
    if code not in LAUNCHER_STEPS:
        raise ValueError(f"emudeck_launchers {where}: step code must be one of {LAUNCHER_STEPS}, got {code!r}")
    decided = code in _BY_SETTINGS or emulator in _BY_EMULATOR.get(code, {})
    if not (runs is None or isinstance(runs, bool) or (runs == RUNS_BY_SETTINGS and decided)):
        raise ValueError(
            f"emudeck_launchers {where}: {code} runs true, false or null, or 'settings' where the settings decide it"
        )
    if not isinstance(step["citation"], str) or not step["citation"].strip():
        raise ValueError(f"emudeck_launchers {where}: every step carries a citation")
    return PinnedStep(code, runs, step["citation"])


_PACKAGED: LauncherRules | None = None


def launcher_rules() -> LauncherRules:
    """The packaged table, loaded once."""
    global _PACKAGED
    if _PACKAGED is None:
        _PACKAGED = load_launcher_rules()
    return _PACKAGED
