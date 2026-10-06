/**
 * What an emulator source is called and how each of its health findings reads.
 *
 * The resolver gives a kind, never a name, and a finding's stable `code` plus
 * the facts in its `data`, never words a reader is meant to see — so every
 * name and every sentence about a source is worded here, from the code alone
 * and never from the resolver's message text. A kind or a code this module has
 * no wording for still shows, as the kind itself or as
 * "Problem with <source>: <code>", so a finding the resolver adds later is never
 * silently dropped.
 */

import type { SourceHealthFinding } from "../types/emulatorSources";

const SOURCE_NAMES: ReadonlyMap<string, string> = new Map([
  ["retrodeck", "RetroDECK"],
  ["emudeck", "EmuDeck"],
  ["bare_retroarch_flatpak", "RetroArch (Flatpak)"],
  ["bare_retroarch_native", "RetroArch (native)"],
]);

const RETRODECK_REPAIR = " Repair it with RetroDECK's 'Repair RetroDECK Paths'.";

/** The name a source is shown under: Tender's own for a kind it knows, the kind itself otherwise. */
export function sourceName(kind: string): string {
  return SOURCE_NAMES.get(kind) ?? kind;
}

/** Whether a finding shows as a banner. `content-tree-unwired` concerns nothing
 *  Tender does, so it shows only in the source's own row. */
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
