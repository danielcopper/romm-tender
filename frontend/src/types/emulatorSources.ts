import type { RommErrorCode } from "./api";

/**
 * Emulator sources as the backend answers them: what the resolver detected,
 * keyed by its installation kind, and what each one's health states.
 */

/** One health finding of a source, as the resolver states it: a stable `code`
 *  and the facts it established (`path`, `status`, `key`, `problem`, `hub`, …).
 *  The resolver's message text is never carried; the wording is Tender's. */
export interface SourceHealthFinding {
  code: string;
  data: Record<string, unknown>;
}

/** What a source's emulator catalogue is: read, sealed (EmuDeck's, which the
 *  resolver cannot read yet), or unavailable for any other reason. */
export type SourceCatalogueState = "read" | "sealed" | "unavailable";

/** One detected emulator source as Settings → Emulator sources lists it. */
export interface EmulatorSource {
  /** The resolver's installation kind; the name shown is Tender's wording of it. */
  kind: string;
  /** The switch "Use this source". */
  enabled: boolean;
  /** Whether Tender can start games through this source today. */
  starts_games: boolean;
  /** The source's root, shown under its name and never used; `null` where it
   *  is a default because the source's settings file is missing or broken. */
  root: string | null;
  findings: SourceHealthFinding[];
  catalogue: SourceCatalogueState;
}

/** The answer of `get_emulator_sources` and of the two writes beside it. */
export interface EmulatorSourcesListing {
  sources: EmulatorSource[];
  /** The kind of the source a game's questions go to; `null` where none answers. */
  answering: string | null;
}

/** The answer of `check_start_source`: the kind of the detected source that
 *  would start a game while it is switched off, which refuses the start; `null` otherwise. */
export interface StartSourceCheck {
  switched_off: string | null;
}

export type EmulatorSourceDirection = "up" | "down";

/** Why an emulator list could not be given: no source answers (`no_source`,
 *  `switched_off`), the answering source's catalogue was refused, the
 *  platform has no switched-on system there (`no_platform_system`,
 *  `platform_system_off`), or RomM could not be read for the platform's ids
 *  (one of {@link RommErrorCode}, `server_unreachable` while it is offline). */
export type EmulatorDataReason =
  | RommErrorCode
  | "no_source"
  | "switched_off"
  | "sealed"
  | "catalogue_invalid"
  | "not_set_up"
  | "unavailable"
  | "no_platform_system"
  | "platform_system_off";

/** The system a RomM platform is in the source asked: the one taken, the
 *  switched-off one named, or none. `platform` is RomM's display name. */
export interface PlatformSystemAnswer {
  state: "found" | "switched_off" | "no_system";
  source: string;
  system: string | null;
  platform: string;
}

/** The source an emulator answer came from, and whether Tender starts games through it. */
export interface AnsweringSource {
  kind: string;
  starts_games: boolean;
}
