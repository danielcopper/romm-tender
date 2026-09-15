/**
 * Builders for `EmulatorOption` test fixtures (#1210).
 *
 * The picker payloads (`get_platform_core_info` / `get_firmware_status`) carry
 * a classified emulator list; these helpers keep the full seven-field shape in
 * one place so tests read as `libretroEmu("mgba_libretro", "mGBA", true)`
 * instead of repeating `{ label, kind, core_so, emulator, is_default, bakeable,
 * reason }`.
 *
 * The identity a libretro row carries is its core file's basename, which is the
 * bare `core_so` plus `.so` — the resolver states it in the spelling the launch
 * command uses, so `libretroEmu` derives it. A standalone row's identity has no
 * such relation to the rest of the row (ES-DE labels one `DUCKSTATION` entry
 * "DuckStation (Standalone)"), so the upper-cased label is a stand-in for a
 * plausible one and a test that cares passes `emulator` in the overrides.
 */

import type { EmulatorOption } from "../types";

/** A bakeable libretro emulator option (the common case). */
export function libretroEmu(core_so: string, label: string, is_default = false): EmulatorOption {
  return { label, kind: "libretro", core_so, emulator: `${core_so}.so`, is_default, bakeable: true, reason: null };
}

/** A standalone emulator option; pass overrides to make it un-bakeable. */
export function standaloneEmu(
  label: string,
  is_default = false,
  overrides: Partial<EmulatorOption> = {},
): EmulatorOption {
  return {
    label,
    kind: "standalone",
    core_so: null,
    emulator: label.toUpperCase(),
    is_default,
    bakeable: true,
    reason: null,
    ...overrides,
  };
}
