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
import { STEAM_LOOKUPS, type StartupReport } from "./steamModules";

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

  it("blames Tender's own copy when the bundle carries one", () => {
    render(<StartupFailurePanel report={report(["Tabs"])} copy={OURS} />);
    expect(screen.getByText(/Tender's own copy of @decky\/ui ran them/)).toBeInTheDocument();
    expect(screen.getByText(/A newer Tender is the repair/)).toBeInTheDocument();
  });

  it("blames Decky's copy when the bundle takes the package from it", () => {
    // The same empty search, and a different program to update: in this bundle
    // the predicates are Decky's, so sending the user after Tender would send
    // them after the wrong one — while Decky's own interface is breaking too.
    render(<StartupFailurePanel report={report(["Tabs"])} copy={DECKYS} />);
    expect(screen.getByText(/Decky Loader v3\.2\.8's copy of @decky\/ui ran them/)).toBeInTheDocument();
    expect(screen.getByText(/A newer Decky Loader is the repair/)).toBeInTheDocument();
  });

  it("calls it a disagreement about the package when Decky's copy lacks the name", () => {
    render(<StartupFailurePanel report={report(["Tabs"])} copy={DECKYS_WITHOUT_THE_NAME} />);
    expect(screen.getByText(/does not carry some of the names Tender asks it for/)).toBeInTheDocument();
    expect(screen.getByText(/Bringing both Tender and Decky Loader/)).toBeInTheDocument();
  });

  it("blames neither copy when what missed is not a @decky/ui lookup", () => {
    // Neither name is a search a copy of the package ran — `SP_REACTDOM` is a
    // global a bootstrap installs, the glyph a predicate of ours — so the page
    // must not hand the user Decky's name for either, which would be a program
    // that did nothing here. The glyph arrives beside the global rather than
    // alone because on its own it no longer brings this page up at all.
    const glyph: StartupReport = {
      everySearchAnswered: false,
      panelMayMount: false,
      missing: ["SP_REACTDOM", "ControllerGlyph"],
      missingPackageNames: [],
      checked: STEAM_LOOKUPS.length,
    };
    render(<StartupFailurePanel report={glyph} copy={DECKYS} />);
    expect(screen.getByText(/None of them is a name @decky\/ui exports/)).toBeInTheDocument();
    expect(screen.queryByText(/Decky Loader v3\.2\.8's copy/)).not.toBeInTheDocument();
    expect(screen.queryByText(/is the repair/)).not.toBeInTheDocument();
  });

  it("says something more basic happened when none of them did", () => {
    render(<StartupFailurePanel report={report(["Tabs", "Focusable"], 2)} copy={OURS} />);
    expect(screen.getByText(/not a run of broken lookups/)).toBeInTheDocument();
  });

  it("tells the reader nothing has been changed and where to report it", () => {
    // Both sentences are the page's whole job — an empty panel looks exactly
    // like a backend that is not running, and a user who cannot tell them apart
    // starts by suspecting their library.
    render(<StartupFailurePanel report={report(["Tabs"])} copy={OURS} />);
    expect(screen.getByText(/nothing has been changed/)).toBeInTheDocument();
    expect(screen.getByText(/github\.com\/danielcopper\/romm-tender\/issues/)).toBeInTheDocument();
  });
});
