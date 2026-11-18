import type { RommErrorReason } from "./reasons";

/**
 * Emulator sources as the backend answers them: what the resolver detected,
 * keyed by its installation kind, and what each one's health states.
 */

/** One health finding of a source, as the resolver states it: a stable `code`
 *  and the facts it established (`path`, `status`, `key`, `problem`, `hub`, …).
 *  The resolver's message text is never carried; the wording is Tender's. One
 *  code is Tender's own, `roots-assumed`, for the roots the resolver states it
 *  only assumed. */
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
  /** The source's root, shown under its name and never used; `null` where the
   *  resolver did not read it from the source's settings. */
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
 *  platform has no system to use there — none matched (`platform_unmatched`),
 *  none the source's frontend has (`no_platform_system`), one this machine has
 *  not set up (`platform_system_not_here`), a switched-off one
 *  (`platform_system_off`), or several the resolver names none of
 *  (`platform_systems_several`) — or RomM could not be read for the platform
 *  (one of {@link RommErrorReason}, `server_unreachable` while it is offline). */
export type EmulatorDataReason =
  | RommErrorReason
  | "no_source"
  | "switched_off"
  | "sealed"
  | "catalogue_invalid"
  | "not_set_up"
  | "unavailable"
  | "no_platform_system"
  | "platform_system_off"
  | "platform_system_not_here"
  | "platform_unmatched"
  | "platform_systems_several";

/** The system a RomM platform is in the source asked: the one taken, several
 *  none of which is taken, switched-off ones, ones this machine has not set up,
 *  none the source's frontend has, or a platform the resolver cannot match
 *  (#2751 D2). `system` is the one system named, `systems` every one the state
 *  names, each by its key; `platform` is RomM's display name. */
export interface PlatformSystemAnswer {
  state: "found" | "several" | "switched_off" | "not_here" | "no_system" | "unmatched";
  source: string;
  system: string | null;
  systems: string[];
  platform: string;
}

/** The source an emulator answer came from, and whether Tender starts games through it. */
export interface AnsweringSource {
  kind: string;
  starts_games: boolean;
}
