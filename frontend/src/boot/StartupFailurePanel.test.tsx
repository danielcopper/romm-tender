/**
 * What the fallback page puts on screen, and the one thing it must never reach
 * for.
 *
 * The import assertion is the load-bearing half. Every component in `@decky/ui`
 * is a search into Steam's own bundle, and this page renders precisely when such
 * a search has missed — so a page built out of them could be the next thing to
 * render nothing, and the user would be back at an empty panel with no idea
 * which of the two faults they have. Nothing in the toolchain says so: adding
 * `import { Field } from "@decky/ui"` here passes the type check, passes lint,
 * and renders perfectly in this suite, because the global stub answers with a
 * `<div>` where Steam would answer with `undefined`.
 */

import { readFileSync } from "node:fs";

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { SearchingCopy } from "./searchingCopy";
import { StartupFailurePanel } from "./StartupFailurePanel";
import { SEARCH_OWNERS, STEAM_LOOKUPS, type SearchOwner, type StartupReport, searchOwner } from "./steamModules";

// `checked` defaults to what a real run would put there rather than to a
// literal: it is `STEAM_LOOKUPS.length` in production, and a fixture no run
// could produce reads as a state of the program. A case that deliberately
// describes a shorter run passes its own count.
const report = (missing: string[], checked = STEAM_LOOKUPS.length): StartupReport => ({
  everySearchAnswered: false,
  panelMayMount: false,
  missing,
  missingPackageNames: missing,
  checked,
});

const ISSUES = "github.com/danielcopper/romm-tender/issues";

const OURS: SearchingCopy = { owner: "tender" };
const DECKYS: SearchingCopy = { owner: "decky", carriesEveryName: true, version: "v3.2.8" };
const DECKYS_WITHOUT_THE_NAME: SearchingCopy = { owner: "decky", carriesEveryName: false, version: "v3.2.8" };

describe("the fallback page", () => {
  it("imports nothing from @decky/ui", () => {
    const source = readFileSync(`${process.cwd()}/src/boot/StartupFailurePanel.tsx`, "utf8");
    // Import statements, not the file's text: the page's own docstring has to
    // be able to NAME the package it refuses, which is where its reason lives.
    const specifiers = [...source.matchAll(/^\s*(?:import|export)[^;]*?from\s+"([^"]+)"/gm)].map((match) => match[1]);
    expect(specifiers).not.toContain("@decky/ui");
    expect(specifiers.filter((name) => name?.startsWith("@decky/"))).toEqual([]);
  });

  it("names every search that found nothing", () => {
    render(<StartupFailurePanel report={report(["Focusable", "Tabs"])} copy={OURS} />);
    // The names ARE the bug report, so the assertion is on the names rather than
    // on a count beside them.
    expect(screen.getByText(/Focusable, Tabs/)).toBeInTheDocument();
  });

  it("heads the page with what the reader is looking at", () => {
    render(<StartupFailurePanel report={report(["Tabs"])} copy={OURS} />);
    expect(screen.getByText("Tender can't start right now")).toBeInTheDocument();
  });

  it("sends the user after Tender when the bundle carries its own copy", () => {
    render(<StartupFailurePanel report={report(["Tabs"])} copy={OURS} />);
    expect(
      screen.getByText(
        "Steam has changed, and this version of Tender doesn't know its way around the new one yet. " +
          "Updating Tender should fix it.",
      ),
    ).toBeInTheDocument();
  });

  it("sends the user after Decky when the bundle takes the package from it", () => {
    // The same empty search, and a different program to update: in this bundle
    // the predicates are Decky's, so sending the user after Tender would send
    // them after the wrong one — while Decky's own interface is breaking too.
    render(<StartupFailurePanel report={report(["Tabs"])} copy={DECKYS} />);
    expect(
      screen.getByText(
        "Steam has changed, and Decky Loader v3.2.8 doesn't know its way around the new one yet. " +
          "Tender shares that part with Decky, so it's affected too — and so are Decky's own menu and " +
          "its other plugins. Updating Decky Loader should fix all of it.",
      ),
    ).toBeInTheDocument();
  });

  it("calls it the two programs out of step when Decky's copy lacks the name", () => {
    render(<StartupFailurePanel report={report(["Tabs"])} copy={DECKYS_WITHOUT_THE_NAME} />);
    expect(
      screen.getByText(
        "Tender and Decky Loader v3.2.8 are out of step with each other: Tender asks Decky's shared part " +
          "for things it doesn't have. Updating both to their current versions should fix it.",
      ),
    ).toBeInTheDocument();
  });

  it("sends the user after Decky for its share and asks for a report about the rest", () => {
    const mixed: StartupReport = { ...report(["SP_REACTDOM", "Tabs"]), missingPackageNames: ["Tabs"] };
    render(<StartupFailurePanel report={mixed} copy={DECKYS} />);
    expect(
      screen.getByText(
        "Steam has changed, and Decky Loader v3.2.8 doesn't know its way around the new one yet. " +
          "Updating Decky Loader fixes that part. If this page still appears afterwards, please report it.",
      ),
    ).toBeInTheDocument();
  });

  it("names no program to update when what missed is not a @decky/ui lookup", () => {
    // Neither name is a search a copy of the package ran — `SP_REACTDOM` is a
    // global a bootstrap installs, the glyph a predicate of ours — so the page
    // must not hand the user Decky's name for either, which would be a program
    // that did nothing here. The glyph arrives beside the global rather than
    // alone because on its own it no longer brings this page up at all.
    const glyph: StartupReport = { ...report(["SP_REACTDOM", "ControllerGlyph"]), missingPackageNames: [] };
    render(<StartupFailurePanel report={glyph} copy={DECKYS} />);
    expect(
      screen.getByText(
        "Tender couldn't find the parts it needs from Steam. Please report this — the names below are what helps.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Decky Loader/)).not.toBeInTheDocument();
  });

  it("says Tender started too early when none of them answered", () => {
    render(<StartupFailurePanel report={report(["Tabs", "Focusable"], 2)} copy={OURS} />);
    expect(
      screen.getByText(
        "Tender started before Steam was ready. Restarting Steam is the first thing to try; if this page " +
          "comes back every time, please report it.",
      ),
    ).toBeInTheDocument();
  });

  // One report per verdict, so a verdict added to `SEARCH_OWNERS` without a
  // case here fails to compile rather than shipping a report line nobody read.
  const verdictCases: Record<SearchOwner, { report: StartupReport; copy: SearchingCopy }> = {
    none: { report: { ...report(["SP_REACTDOM"]), missingPackageNames: [] }, copy: DECKYS },
    tender: { report: report(["Tabs"]), copy: OURS },
    disagreement: { report: report(["Tabs"]), copy: DECKYS_WITHOUT_THE_NAME },
    mixed: { report: { ...report(["SP_REACTDOM", "Tabs"]), missingPackageNames: ["Tabs"] }, copy: DECKYS },
    decky: { report: report(["Tabs"]), copy: DECKYS },
  };
  const ASKS_IN_FULL = `If this keeps happening after the update, please report it at ${ISSUES} and include these names:`;
  const SAYS_ONLY_WHERE = `You can do that at ${ISSUES} — please include these names:`;

  it.each(SEARCH_OWNERS)("puts the report line in the form the %s sentence calls for", (owner) => {
    const { report: missed, copy } = verdictCases[owner];
    expect(searchOwner(missed, copy)).toBe(owner);
    render(<StartupFailurePanel report={missed} copy={copy} />);
    // Where the sentence above has already asked for a report, the line only
    // says where to send it — the page asking twice in a row is the whole of
    // what these two forms are for. `mixed` takes the short one although its
    // sentence names an update, because that sentence carries the clause.
    const asked = owner === "none" || owner === "mixed";
    expect(screen.getByText(asked ? SAYS_ONLY_WHERE : ASKS_IN_FULL)).toBeInTheDocument();
  });

  it.each([
    ["Tender's", OURS],
    ["Decky's", DECKYS],
  ] as const)("puts the short report line under the too-early sentence, under %s copy", (_, copy) => {
    render(<StartupFailurePanel report={report(["Tabs", "Focusable"], 2)} copy={copy} />);
    expect(screen.getByText(SAYS_ONLY_WHERE)).toBeInTheDocument();
  });
});
