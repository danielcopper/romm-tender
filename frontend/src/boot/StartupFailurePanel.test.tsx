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

import { StartupFailurePanel } from "./StartupFailurePanel";

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
    render(<StartupFailurePanel report={{ ok: false, missing: ["Focusable", "Tabs"], checked: 27 }} />);
    // The names ARE the bug report, so the assertion is on the names rather than
    // on a count beside them.
    expect(screen.getByText(/Focusable, Tabs/)).toBeInTheDocument();
  });

  it("says a Steam update is the cause when some searches still resolved", () => {
    render(<StartupFailurePanel report={{ ok: false, missing: ["Tabs"], checked: 27 }} />);
    expect(screen.getByText(/Steam client update has moved/)).toBeInTheDocument();
  });

  it("says something more basic happened when none of them did", () => {
    render(<StartupFailurePanel report={{ ok: false, missing: ["Tabs", "Focusable"], checked: 2 }} />);
    expect(screen.getByText(/not a run of broken lookups/)).toBeInTheDocument();
  });

  it("tells the reader nothing has been changed and where to report it", () => {
    // Both sentences are the page's whole job — an empty panel looks exactly
    // like a backend that is not running, and a user who cannot tell them apart
    // starts by suspecting their library.
    render(<StartupFailurePanel report={{ ok: false, missing: ["Tabs"], checked: 27 }} />);
    expect(screen.getByText(/nothing has been changed/)).toBeInTheDocument();
    expect(screen.getByText(/github\.com\/danielcopper\/romm-tender\/issues/)).toBeInTheDocument();
  });
});
