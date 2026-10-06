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
  SourceHealthFinding,
} from "../types/emulatorSources";

const SOURCE_NAMES: ReadonlyMap<string, string> = new Map([
  ["retrodeck", "RetroDECK"],
  ["emudeck", "EmuDeck"],
  ["bare_retroarch_flatpak", "RetroArch (Flatpak)"],
  ["bare_retroarch_native", "RetroArch (native)"],
]);

// A RetroArch without a frontend has no emulator list, and its "cannot start
// games" line stands in place of the "not established" one (#2188 D33).
const NO_CATALOGUE_KINDS: ReadonlySet<string> = new Set(["bare_retroarch_flatpak", "bare_retroarch_native"]);

const RETRODECK_REPAIR = " Repair it with RetroDECK's 'Repair RetroDECK Paths'.";

/** The name a source is shown under: Tender's own for a kind it knows, the kind itself otherwise. */
export function sourceName(kind: string): string {
  return SOURCE_NAMES.get(kind) ?? kind;
}

/** Whether a finding shows as a banner. `content-tree-unwired` concerns nothing
 *  Tender does, so it shows only on the source's own card. */
export function findingIsBanner(finding: SourceHealthFinding): boolean {
  return finding.code !== "content-tree-unwired";
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

/** The worded sentence, or `null` where the code, or a fact it needs, is not one this module knows. */
function wordedFinding(kind: string, { code, data }: SourceHealthFinding): string | null {
  const name = sourceName(kind);
  const path = text(data, "path");
  if (path === null) return null;
  const repair = kind === "retrodeck" ? RETRODECK_REPAIR : "";
  switch (code) {
    case "not-set-up":
      return `${name} is installed but has not been set up yet. Start ${name} once and finish its first-run setup.`;
    case "marker-missing":
      return `${name}: its settings file ${path} is missing.`;
    case "marker-unreadable":
    case "config-unreadable":
      return `${name}: its settings file ${path} cannot be read.`;
    case "marker-invalid":
      return `${name}: its settings file ${path} is damaged, so Tender cannot tell where its folders are.${repair}`;
    case "root-missing":
      return `${name}: its folder ${path} does not exist. If it is on an SD card or another drive, insert it.`;
    case "saves-root-missing":
      return `${name}: its saves folder ${path} does not exist, so saves of its emulators cannot be synced.${repair}`;
    case "companion-config-missing":
      return `${name}: RetroArch's settings file ${path} cannot be read; ${name}'s RetroArch may be missing or broken.`;
    case "catalogue-invalid": {
      const problem = CATALOGUE_PROBLEMS.get(text(data, "problem") ?? "");
      if (problem === undefined) return null;
      return (
        `${name}: ES-DE's systems file ${path} ${problem}. ` +
        `ES-DE shows no systems until it is fixed, and Tender cannot tell which emulators ${name} offers.`
      );
    }
    case "content-tree-unwired": {
      const hub = text(data, "hub");
      const problem = UNWIRED_PROBLEMS.get(text(data, "problem") ?? "");
      if (hub === null || problem === undefined) return null;
      return (
        `${name}: texture packs or mods in ${hub} do not reach the emulator, because ${path} ${problem}. ` +
        `Resetting that emulator in ${name} fixes it.`
      );
    }
    default:
      return null;
  }
}

/** Main's banner, and the settings section's line, while no emulator source is detected. */
export const NO_SOURCE_BANNER = "No emulator source was found.";

/** The settings section's line while its listing has not answered yet. */
export const SOURCES_READING = "Reading the emulator sources…";

/** The settings section's line where its listing could not be read. */
export const SOURCES_UNREAD = "Could not read the emulator sources. Reopen the page to try again.";

/** The card's line, and Main's banner, for a source Tender cannot start games through. */
export function cannotStartSentence(kind: string): string {
  return `Tender cannot start games through ${sourceName(kind)} yet.`;
}

/** A source whose emulator list the resolver cannot read yet (EmuDeck's sealed catalogue). */
export function sealedCatalogueSentence(kind: string): string {
  return `${sourceName(kind)}'s emulator list cannot be read yet.`;
}

/**
 * Why a platform page or an emulator menu has no emulator list to offer, from
 * the answer's `reason` and its answering `source`. Never "no emulator": every
 * one of these is a list that could not be established. An answer with no
 * source is one no source answered, whatever its reason says, so it reads as
 * no source found.
 */
export function emulatorDataReasonSentence(reason: EmulatorDataReason | null, source: AnsweringSource | null): string {
  if (reason === "switched_off") return "Every emulator source is switched off in Settings → Emulator sources.";
  if (reason === "no_source" || source === null) {
    return "No emulator source was found, so Tender cannot tell which emulators this platform offers.";
  }
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
  const quiet: Record<EmulatorSource["catalogue"], SourceRowLine[]> = {
    read: [{ tone: "ok", text: "No problems found." }],
    sealed: [],
    unavailable: NO_CATALOGUE_KINDS.has(source.kind)
      ? []
      : [warning(`${sourceName(source.kind)}'s emulator list is not established.`)],
  };
  return [
    ...(health.length > 0 ? health : quiet[source.catalogue]),
    ...(source.catalogue === "sealed" ? [warning(sealedCatalogueSentence(source.kind))] : []),
    ...(source.starts_games ? [] : [{ tone: "info" as const, text: cannotStartSentence(source.kind) }]),
  ];
}

/** One banner on Main: its sentence, and a key no other banner of the same listing has. */
export interface SourceBanner {
  key: string;
  text: string;
}

/**
 * Main's banners about the emulator sources, in the sources' order: one where
 * none is detected, one per banner finding of a switched-on source (a
 * switched-off source's findings stay on its card), and one where the source
 * that answers is one Tender cannot start games through.
 */
export function mainSourceBanners(listing: EmulatorSourcesListing): SourceBanner[] {
  if (listing.sources.length === 0) return [{ key: "no-source", text: NO_SOURCE_BANNER }];
  const findings = listing.sources
    .filter((source) => source.enabled)
    .flatMap((source) =>
      source.findings
        .map((finding, index) => ({ finding, key: `${source.kind}:${index}` }))
        .filter(({ finding }) => findingIsBanner(finding))
        .map(({ finding, key }) => ({ key, text: findingSentence(source.kind, finding) })),
    );
  const answering = listing.sources.find((source) => source.kind === listing.answering);
  return answering && !answering.starts_games
    ? [...findings, { key: `cannot-start:${answering.kind}`, text: cannotStartSentence(answering.kind) }]
    : findings;
}
