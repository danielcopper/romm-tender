/**
 * Builders for the emulator payloads' test fixtures (#1210).
 *
 * The picker payloads (`get_platform_core_info` / `get_system_core_info` /
 * `get_firmware_status`) carry a classified emulator list; these helpers keep
 * the full shapes in one place so tests read as
 * `libretroEmu("mgba_libretro", "mGBA", true)` or `coreInfo({ ... })` instead
 * of repeating every key, and a key the payload gains is one line here.
 *
 * The identity a libretro row carries is its core file's basename, which is the
 * bare `core_so` plus `.so` — the resolver states it in the spelling the launch
 * command uses, so `libretroEmu` derives it. A standalone row's identity has no
 * such relation to the rest of the row (ES-DE labels one `DUCKSTATION` entry
 * "DuckStation (Standalone)"), so the upper-cased label is a stand-in for a
 * plausible one and a test that cares passes `emulator` in the overrides.
 */

import type { CoreInfo, EmulatorOption, EmulatorStart, SystemCoreInfo } from "../types";

/** A bakeable libretro emulator option (the common case). */
export function libretroEmu(core_so: string, label: string, is_default = false): EmulatorOption {
  return {
    label,
    kind: "core",
    core_so,
    emulator: `${core_so}.so`,
    declared_index: null,
    is_default,
    chosen_in_frontend: false,
    bakeable: true,
    reason: null,
  };
}

/** A standalone emulator option; pass overrides to make it un-bakeable. */
export function standaloneEmu(
  label: string,
  is_default = false,
  overrides: Partial<EmulatorOption> = {},
): EmulatorOption {
  return {
    label,
    kind: "program",
    core_so: null,
    emulator: label.toUpperCase(),
    declared_index: null,
    is_default,
    chosen_in_frontend: false,
    bakeable: true,
    reason: null,
    ...overrides,
  };
}

/** A start answer: the game starts with `entry`, unless the overrides say otherwise. */
export function emulatorStart(overrides: Partial<EmulatorStart> = {}): EmulatorStart {
  return { state: "starts", reason: null, entry: null, folder_entry: null, ...overrides };
}

/** A `get_platform_core_info` answer: an empty, readable list with nothing picked, unless the overrides say otherwise. */
export function coreInfo(overrides: Partial<CoreInfo> = {}): CoreInfo {
  return {
    emulators: [],
    emulator_data_available: true,
    emulator_data_reason: null,
    emulator_source: { kind: "retrodeck", starts_games: true },
    platform_system: null,
    active_core: null,
    active_core_label: null,
    platform_core_label: null,
    has_game_override: false,
    start: emulatorStart(),
    vanished_pick: null,
    ...overrides,
  };
}

/** A `get_system_core_info` answer, built the same way as {@link coreInfo}. */
export function systemCoreInfo(overrides: Partial<SystemCoreInfo> = {}): SystemCoreInfo {
  return {
    emulators: [],
    emulator_data_available: true,
    emulator_data_reason: null,
    emulator_source: { kind: "retrodeck", starts_games: true },
    platform_system: null,
    active_core_label: null,
    start: emulatorStart(),
    vanished_pick: null,
    source_defaults: [],
    tender_default: null,
    ...overrides,
  };
}
