/**
 * What an emulator source is called, how each of its health findings reads, and
 * what the pages say where the answering source gives no emulator list.
 *
 * The resolver gives a kind, never a name, and a finding's stable `code` plus
 * the facts in its `data`, never words a reader is meant to see — so every
 * name and every sentence about a source is worded here, from the code alone
 * and never from the resolver's message text. A kind or a code this module has
 * no wording for still shows, as the kind itself or as
 * "Problem with <source>: <code>", so a finding the resolver adds later is never
 * silently dropped.
 */

import type {
  AnsweringSource,
  EmulatorDataReason,
  EmulatorSource,
  EmulatorSourcesListing,
  PlatformSystemAnswer,
  SourceHealthFinding,
} from "../types/emulatorSources";
import type { RommErrorReason } from "../types/reasons";
import { joined } from "./formatters";

const SOURCE_NAMES: ReadonlyMap<string, string> = new Map([
  ["retrodeck", "RetroDECK"],
  ["emudeck", "EmuDeck"],
  ["bare_retroarch_flatpak", "RetroArch (Flatpak)"],
  ["bare_retroarch_native", "RetroArch (native)"],
]);

// A RetroArch without a frontend has no emulator list, and its "cannot start
// games" line stands in place of the "not established" one.
const NO_CATALOGUE_KINDS: ReadonlySet<string> = new Set(["bare_retroarch_flatpak", "bare_retroarch_native"]);

// The findings whose sentence says why a source's emulator list is missing. Any
// other finding leaves the "not established" line standing beside it.
const LIST_EXPLAINING_CODES: ReadonlySet<string> = new Set(["catalogue-invalid", "not-set-up"]);

const RETRODECK_REPAIR = " Repair it with RetroDECK's 'Repair RetroDECK Paths'.";

/** The name a source is shown under: Tender's own for a kind it knows, the kind itself otherwise. */
export function sourceName(kind: string): string {
  return SOURCE_NAMES.get(kind) ?? kind;
}

/** Whether a finding shows as a notice on Main. Two show only on the source's own card:
 *  `content-tree-unwired`, which concerns nothing Tender does, and a settings file whose
 *  version alone cannot be read, from which every folder is still read. */
export function findingIsBanner(finding: SourceHealthFinding): boolean {
  return finding.code !== "content-tree-unwired" && !isVersionOnly(finding);
}

function isVersionOnly({ code, data }: SourceHealthFinding): boolean {
  return code === "marker-invalid" && text(data, "key") === "version";
}

/** The sentence for one health finding of the source of kind `kind`. */
export function findingSentence(kind: string, finding: SourceHealthFinding): string {
  return wordedFinding(kind, finding) ?? `Problem with ${sourceName(kind)}: ${finding.code}`;
}

function text(data: Record<string, unknown>, key: string): string | null {
  const value = data[key];
  return typeof value === "string" && value !== "" ? value : null;
}

const CATALOGUE_PROBLEMS: ReadonlyMap<string, string> = new Map([
  ["parse-error", "does not parse as XML"],
  ["missing-systemlist", "has no <systemList>"],
]);

const UNWIRED_PROBLEMS: ReadonlyMap<string, string> = new Map([
  ["missing", "is missing"],
  ["not-a-link", "is not a link"],
  ["diverted", "points elsewhere"],
]);

// Tender's own finding for the roots the resolver only assumed; its shape is
// `backend/domain/source_roots.py`'s.
const ROOTS_ASSUMED = "roots-assumed";

// What each root is called in a sentence, after "its".
const ROOT_WORDS: ReadonlyMap<string, string> = new Map([
  ["installation", "folder"],
  ["roms", "ROM folder"],
  ["bios", "BIOS folder"],
  ["saves", "saves folder"],
]);

/** The sentence for the roots a source's settings file leaves out, or `null` for a root this module cannot name. */
function assumedRootsSentence(name: string, file: string, roots: string | null): string | null {
  const words = (roots ?? "").split(",").map((root) => ROOT_WORDS.get(root));
  if (words.length === 0 || words.includes(undefined)) return null;
  const folders = joined(words as string[]);
  const use = words.length === 1 ? "does not use it" : "uses none of them";
  return `${name}: its settings file ${file} does not name its ${folders}, so Tender ${use}.`;
}

// The key a finding names its folder, file or setting under, where it is not `path`.
const SUBJECT_KEYS: ReadonlyMap<string, string> = new Map([
  ["roms-root-not-absolute", "value"],
  ["emulator-config-missing", "config"],
]);

/** The worded sentence, or `null` where the code, or a fact it needs, is not one this module knows. */
function wordedFinding(kind: string, { code, data }: SourceHealthFinding): string | null {
  const name = sourceName(kind);
  const subject = text(data, SUBJECT_KEYS.get(code) ?? "path");
  if (subject === null) return null;
  const repair = kind === "retrodeck" ? RETRODECK_REPAIR : "";
  switch (code) {
    case "not-set-up":
      return `${name} is installed but has not been set up yet. Start ${name} once and finish its first-run setup.`;
    case "marker-missing":
      return `${name}: its settings file ${subject} is missing.`;
    case "marker-unreadable":
    case "config-unreadable":
      return `${name}: its settings file ${subject} cannot be read.`;
    case "marker-invalid":
      if (isVersionOnly({ code, data })) {
        return `${name}: its settings file ${subject} holds no version Tender can read; its folders are still read from it.`;
      }
      return `${name}: its settings file ${subject} is damaged, so Tender cannot tell where its folder, BIOS folder and saves folder are.${repair}`;
    case ROOTS_ASSUMED:
      return assumedRootsSentence(name, subject, text(data, "roots"));
    case "root-missing":
      return `${name}: its folder ${subject} does not exist. If it is on an SD card or another drive, insert it.`;
    case "saves-root-missing":
      return `${name}: its saves folder ${subject} does not exist, so saves of its emulators cannot be synced.${repair}`;
    case "bios-root-missing":
      return `${name}: its BIOS folder ${subject} does not exist. If it is on an SD card or another drive, insert it.`;
    case "roms-root-missing":
      return `${name}: its ROM folder ${subject} does not exist. If it is on an SD card or another drive, insert it.`;
    case "roms-root-not-absolute":
      return `${name}: ES-DE's ROM folder setting "${subject}" is not a full path, so Tender cannot tell where its games are.`;
    case "emulator-config-missing":
      return `${name}: RetroArch's settings file ${subject} is missing, so RetroArch uses its built-in default folders.`;
    case "companion-config-missing":
      return `${name}: RetroArch's settings file ${subject} cannot be read; ${name}'s RetroArch may be missing or broken.`;
    case "catalogue-invalid": {
      const problem = CATALOGUE_PROBLEMS.get(text(data, "problem") ?? "");
      if (problem === undefined) return null;
      return (
        `${name}: ES-DE's systems file ${subject} ${problem}. ` +
        `ES-DE shows no systems until it is fixed, and Tender cannot tell which emulators ${name} offers.`
      );
    }
    case "content-tree-unwired": {
      const hub = text(data, "hub");
      const problem = UNWIRED_PROBLEMS.get(text(data, "problem") ?? "");
      if (hub === null || problem === undefined) return null;
      return (
        `${name}: texture packs or mods in ${hub} do not reach the emulator, because ${subject} ${problem}. ` +
        `Resetting that emulator in ${name} fixes it.`
      );
    }
    default:
      return null;
  }
}

/** The reason a press is refused with while a finding of RetroDECK's stands in the way of its folders. */
export const RETRODECK_FINDING_REASON = "retrodeck_finding";

/**
 * The two reasons a refused save action carries a source's finding under:
 * `save_shape_unsupported`, which a sync and every save write refuse with, and
 * `saves_root_missing`, a sync's own failure for a missing saves folder. Either
 * carries the finding, and the source's kind beside it, wherever the save was
 * refused for a place not read from the emulator's settings or for a missing
 * saves folder.
 */
const SAVE_FINDING_REASONS: ReadonlySet<unknown> = new Set(["save_shape_unsupported", "saves_root_missing"]);

function isFinding(value: unknown): value is SourceHealthFinding {
  if (typeof value !== "object" || value === null) return false;
  const { code, data } = value as { code?: unknown; data?: unknown };
  return typeof code === "string" && typeof data === "object" && data !== null;
}

/**
 * The sentence for a save refused because the emulator's own settings file was
 * not read, where Main has no notice for that finding: an emulator's file rides
 * only on the save answer, and RetroArch's own `retroarch.cfg` is the one such
 * file that is a health finding — the one carrying no `token` and no `core`.
 */
function settingsFileSentence(finding: SourceHealthFinding, emulator: string): string | null {
  const config = text(finding.data, "config");
  if (config === null) return null;
  const ownFile = text(finding.data, "token") !== null || text(finding.data, "core") !== null;
  if (finding.code === "emulator-config-missing" && ownFile) {
    return `${emulator}'s settings file ${config} is missing, so Tender cannot tell where it keeps this game's saves.`;
  }
  if (finding.code === "emulator-config-unreadable") {
    return `${emulator}'s settings file ${config} cannot be read, so Tender cannot tell where it keeps this game's saves.`;
  }
  return null;
}

/** The sentence a refusal carrying `finding` is worded with, or `null` where its message stands. */
function refusalSentence(answer: Record<string, unknown>, finding: SourceHealthFinding): string | null {
  const { reason, source, emulator } = answer;
  if (reason === RETRODECK_FINDING_REASON) {
    return findingSentence(typeof source === "string" ? source : "retrodeck", finding);
  }
  if (!SAVE_FINDING_REASONS.has(reason) || typeof source !== "string") return null;
  const settingsFile = typeof emulator === "string" ? settingsFileSentence(finding, emulator) : null;
  return settingsFile ?? findingSentence(source, finding);
}

/**
 * An endpoint's answer, with the message of a refusal for a source's finding
 * replaced by the sentence Main's notice shows for that finding, so a refused
 * press and the notice say the same thing. Two refusals carry one: a folder
 * press refused for one of RetroDECK's findings, and a save action refused for
 * a place not read or a missing saves folder, which carries its source's kind
 * beside the finding. A save refused for an emulator's own settings file, for
 * which Main shows no notice, says so in a sentence of its own. Every other
 * answer comes back as it is.
 */
export function withFindingSentence<T>(answer: T): T {
  if (typeof answer !== "object" || answer === null) return answer;
  const { finding } = answer as { finding?: unknown };
  if (!isFinding(finding)) return answer;
  const sentence = refusalSentence(answer as Record<string, unknown>, finding);
  return sentence === null ? answer : { ...answer, message: sentence };
}

/** The two reasons a press is refused with for a platform that has no switched-on system in its source. */
const PLATFORM_SYSTEM_REASONS: ReadonlySet<string> = new Set(["no_platform_system", "platform_system_off"]);

function isPlatformSystemRefusal(answer: object): answer is { reason: EmulatorDataReason } & PlatformSystemAnswer {
  const { reason, source, platform, system } = answer as Record<string, unknown>;
  return (
    typeof reason === "string" &&
    PLATFORM_SYSTEM_REASONS.has(reason) &&
    typeof source === "string" &&
    typeof platform === "string" &&
    (system === null || typeof system === "string")
  );
}

/**
 * An endpoint's answer, with the message of a refusal for a platform that has
 * no switched-on system replaced by the sentence the platform's pages show for
 * it, so a refused download and the page say the same thing. Every other
 * answer comes back as it is.
 */
export function withPlatformSystemSentence<T>(answer: T): T {
  if (typeof answer !== "object" || answer === null || !isPlatformSystemRefusal(answer)) return answer;
  const sentence = platformSystemSentence(answer.reason, { ...answer, state: "no_system" });
  return sentence === null ? answer : { ...answer, message: sentence };
}

/** Main's notice, and the settings section's line, while no emulator source is detected. */
export const NO_SOURCE_BANNER = "No emulator source was found.";

/** The settings section's line while its listing has not answered yet. */
export const SOURCES_READING = "Reading the emulator sources…";

/** The settings section's line where its listing could not be read. */
export const SOURCES_UNREAD = "Could not read the emulator sources. Reopen the page to try again.";

/** The card's line for a source Tender cannot start games through, switched on or off. */
export function cannotStartSentence(kind: string): string {
  return `Tender cannot start games through ${sourceName(kind)} yet.`;
}

/** Main's notice for a switched-on source Tender cannot start games through. */
export function cannotStartNotice(kind: string): string {
  return `${sourceName(kind)} is switched on in Settings › Emulator sources, but Tender cannot start games through it yet.`;
}

// Why a start is refused while the source that would start the game is
// switched off, by that source's kind. RetroDECK is the one source Tender
// starts games through yet, so it is the one sentence there is.
const STARTING_SOURCE_OFF_SENTENCES: ReadonlyMap<string, string> = new Map([
  [
    "retrodeck",
    "RetroDECK is switched off in Settings › Emulator sources, and Tender can only start games through RetroDECK yet.",
  ],
]);

/**
 * What the game page's Play and a start from Steam's own library say, as a
 * notification, when they refuse a start because the source of kind `kind`
 * that would start the game is switched off. A kind with no sentence of its
 * own says only that it is switched off, and nothing about what Tender can
 * start through.
 */
export function startingSourceOffSentence(kind: string): string {
  return (
    STARTING_SOURCE_OFF_SENTENCES.get(kind) ?? `${sourceName(kind)} is switched off in Settings › Emulator sources.`
  );
}

/** The title of the dialog both launch paths show where they could not tell whether the source that would start the game is switched off. */
export const UNCHECKED_START_SOURCE_TITLE = "Emulator Source Unchecked";

/** The question that dialog asks. */
export const UNCHECKED_START_SOURCE_QUESTION =
  "Tender could not check whether the emulator source is switched on. Start anyway?";

/** A source whose emulator list the resolver cannot read yet (EmuDeck's sealed catalogue). */
export function sealedCatalogueSentence(kind: string): string {
  return `${sourceName(kind)}'s emulator list cannot be read yet.`;
}

/** Why a platform has no system yet: its ids are not kept, and RomM, which gives them, cannot be reached. */
const ROMM_UNREACHABLE_SENTENCE = "RomM cannot be reached, so Tender does not know this platform's system yet.";

/** Why a platform has no system yet: its ids are not kept, and RomM refused the read that gives them. */
const ROMM_REFUSED_SENTENCE = "RomM did not give this platform's ids, so Tender does not know its system yet.";

/** Every reason a RomM read can fail with: a `Record` so that a reason added to {@link RommErrorReason} must be added here. */
const ROMM_READ_FAILURES: Record<RommErrorReason, true> = {
  server_unreachable: true,
  auth_failed: true,
  not_found: true,
  unsupported: true,
  unknown: true,
  config_error: true,
  device_sync_disabled: true,
  rate_limited: true,
  server_error: true,
};
const ROMM_READ_FAILURE_CODES: ReadonlySet<string> = new Set(Object.keys(ROMM_READ_FAILURES));

/** Why a platform's games cannot be downloaded: the source asked has no system for it. */
export function noPlatformSystemSentence(kind: string, platform: string): string {
  return `${sourceName(kind)} has no system for ${platform}, so Tender cannot download its games.`;
}

/** Why a platform's games cannot be downloaded: every system it has in the source asked is switched off there. */
export function platformSystemOffSentence(kind: string, system: string): string {
  return `System ${system} is switched off in ${sourceName(kind)}.`;
}

/** The platform page's clause naming the system a platform is in its source. */
export function platformSystemClause(answer: PlatformSystemAnswer): string | null {
  return answer.state === "found" && answer.system !== null
    ? `${sourceName(answer.source)} system: ${answer.system}`
    : null;
}

/** The sentence for a platform with no switched-on system, or `null` for any other answer. */
function platformSystemSentence(reason: EmulatorDataReason | null, answer: PlatformSystemAnswer | null): string | null {
  if (answer === null) return null;
  if (reason === "no_platform_system") return noPlatformSystemSentence(answer.source, answer.platform);
  if (reason === "platform_system_off" && answer.system !== null) {
    return platformSystemOffSentence(answer.source, answer.system);
  }
  return null;
}

/**
 * Why a platform page or an emulator menu has no emulator list to offer, from
 * the answer's `reason`, its answering `source` and the platform's system
 * there. Never "no emulator": every one of these is a list that could not be
 * established. An answer with no source is one no source answered, whatever
 * its reason says, so it reads as no source found — except a failed read of
 * RomM for the platform's ids, which stands whether or not a source answers.
 */
export function emulatorDataReasonSentence(
  reason: EmulatorDataReason | null,
  source: AnsweringSource | null,
  platformSystem: PlatformSystemAnswer | null = null,
): string {
  if (reason === "switched_off") return "Every emulator source is switched off in Settings › Emulator sources.";
  if (reason === "server_unreachable") return ROMM_UNREACHABLE_SENTENCE;
  if (reason !== null && ROMM_READ_FAILURE_CODES.has(reason)) return ROMM_REFUSED_SENTENCE;
  if (reason === "no_source" || source === null) {
    return "No emulator source was found, so Tender cannot tell which emulators this platform offers.";
  }
  const noSystem = platformSystemSentence(reason, platformSystem);
  if (noSystem !== null) return noSystem;
  if (reason === "catalogue_invalid") {
    return `${sourceName(source.kind)}: ES-DE's systems file is broken, so its emulators are not established.`;
  }
  if (reason === "not_set_up") {
    return `${sourceName(source.kind)} has not been set up yet, so its emulators are not established.`;
  }
  if (reason === "sealed") return sealedCatalogueSentence(source.kind);
  return `${sourceName(source.kind)}'s emulator list is not established.`;
}

/** How a card line reads at a glance: nothing wrong, a fact about what Tender
 *  does, or something the reader may have to look into. */
export type SourceRowTone = "ok" | "info" | "warning";

/** One line of a source's card, with the tone its leading icon is drawn in. */
export interface SourceRowLine {
  tone: SourceRowTone;
  text: string;
}

const warning = (text: string): SourceRowLine => ({ tone: "warning", text });

/** The lines a source's card under Settings → Emulator sources says about it, below its name and root. */
export function sourceRowLines(source: EmulatorSource): SourceRowLine[] {
  const health = source.findings.map((finding) => warning(findingSentence(source.kind, finding)));
  // A sealed catalogue, and a source with no catalogue at all, each have a
  // sentence of their own below that stands in for the "not established" line.
  const unexplained =
    source.catalogue === "unavailable" &&
    !NO_CATALOGUE_KINDS.has(source.kind) &&
    !source.findings.some((finding) => LIST_EXPLAINING_CODES.has(finding.code));
  const quiet = source.catalogue === "read" ? [{ tone: "ok" as const, text: "No problems found." }] : [];
  return [
    ...(health.length > 0 ? health : quiet),
    ...(unexplained ? [warning(`${sourceName(source.kind)}'s emulator list is not established.`)] : []),
    ...(source.catalogue === "sealed" ? [warning(sealedCatalogueSentence(source.kind))] : []),
    ...(source.starts_games ? [] : [{ tone: "info" as const, text: cannotStartSentence(source.kind) }]),
  ];
}

/** One notice on Main: its sentence, a key no other notice of the same listing
 *  has, and its tone — an "info" notice is drawn without the warning sign. */
export interface SourceBanner {
  key: string;
  text: string;
  tone: "warning" | "info";
}

/**
 * Main's notices about the emulator sources, in the sources' order: one where
 * none is detected, one per finding of a switched-on source that shows on Main, and one per
 * switched-on source Tender cannot start games through, whether or not it is
 * the one that answers. A switched-off source says either only on its card.
 */
export function mainSourceBanners(listing: EmulatorSourcesListing): SourceBanner[] {
  if (listing.sources.length === 0) return [{ key: "no-source", text: NO_SOURCE_BANNER, tone: "warning" }];
  const switchedOn = listing.sources.filter((source) => source.enabled);
  const findings = switchedOn.flatMap((source) =>
    source.findings
      .map((finding, index) => ({ finding, key: `${source.kind}:${index}` }))
      .filter(({ finding }) => findingIsBanner(finding))
      .map(({ finding, key }) => ({ key, text: findingSentence(source.kind, finding), tone: "warning" as const })),
  );
  const cannotStart = switchedOn
    .filter((source) => !source.starts_games)
    .map((source) => ({
      key: `cannot-start:${source.kind}`,
      text: cannotStartNotice(source.kind),
      tone: "info" as const,
    }));
  return [...findings, ...cannotStart];
}
