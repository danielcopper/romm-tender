import "@testing-library/jest-dom/vitest";
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { DesktopAdoptCandidatesDialog } from "./DesktopAdoptCandidatesDialog";
import { DesktopAdoptCollisionsDialog } from "./DesktopAdoptCollisionsDialog";
import { DesktopAdoptUnusableDialog } from "./DesktopAdoptUnusableDialog";
import { DesktopAdoptVanishedDialog } from "./DesktopAdoptVanishedDialog";
import {
  CANDIDATES_INTRO,
  COLLISIONS_CONSEQUENCES,
  UNUSABLE_DOWNLOAD_NOTE,
  VANISHED_DOWNLOAD_NOTE,
  VANISHED_INTRO,
} from "../../../utils/adoptWording";
import type {
  AdoptionCandidate,
  CandidatesFoundResult,
  CandidateVanishedResult,
  UnusableNamesakeResult,
} from "../../../types";

const CANDIDATE: AdoptionCandidate = {
  name: "Game (USA).sfc",
  path: "/roms/snes/Game (USA).sfc",
  is_dir: false,
  size_bytes: 2048,
  modified_at: 0,
  evidence: "crc32",
  detail: "Same checksum",
};

const FOUND: CandidatesFoundResult = {
  success: false,
  reason: "adoption_candidates",
  message: "",
  incoming: { name: "Game.sfc", size_bytes: 2048 },
  candidates: [CANDIDATE, { ...CANDIDATE, name: "Game [h].sfc", path: "/roms/snes/Game [h].sfc", is_dir: true }],
  truncated: true,
};

describe("DesktopAdoptCandidatesDialog", () => {
  it("lists each candidate with what its offer rests on, and notes a cut-short list", () => {
    render(<DesktopAdoptCandidatesDialog found={FOUND} onChoice={vi.fn()} />);
    const dialog = screen.getByRole("dialog", { name: "This Game May Already Be on Your Device" });
    expect(dialog).toHaveTextContent(CANDIDATES_INTRO);
    expect(dialog).toHaveTextContent("Same checksum — 2.0 KB");
    expect(dialog).toHaveTextContent("Same checksum — folder");
    expect(dialog).toHaveTextContent("Only the 2 strongest matches are shown");
  });

  it("resolves the candidate picked, None of These, and cancel from every dismissal", () => {
    const onChoice = vi.fn();
    render(<DesktopAdoptCandidatesDialog found={FOUND} onChoice={onChoice} />);
    fireEvent.click(screen.getByRole("button", { name: /Game \[h\]\.sfc/ }));
    fireEvent.click(screen.getByRole("button", { name: "None of These — Download Game.sfc" }));
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    fireEvent.keyDown(window, { key: "Escape" });
    fireEvent.click(screen.getByRole("button", { name: "Close dialog" }));
    expect(onChoice.mock.calls).toEqual([
      [{ kind: "candidate", candidate: FOUND.candidates[1] }],
      [{ kind: "download" }],
      [{ kind: "cancel" }],
      [{ kind: "cancel" }],
      [{ kind: "cancel" }],
    ]);
  });
});

describe("DesktopAdoptCollisionsDialog", () => {
  it("names every taken file with its kind and states both consequences", () => {
    render(
      <DesktopAdoptCollisionsDialog
        collisions={[
          { name: "Game.srm", path: "/saves/Game.srm", kind: "save" },
          { name: "Game.state1", path: "/states/Game.state1", kind: "savestate" },
        ]}
        onChoice={vi.fn()}
      />,
    );
    const dialog = screen.getByRole("dialog", { name: "Some of These Names Are Taken" });
    expect(dialog).toHaveTextContent("Game.srm (save)");
    expect(dialog).toHaveTextContent("Game.state1 (savestate)");
    expect(dialog).toHaveTextContent(COLLISIONS_CONSEQUENCES);
  });

  it("resolves overwrite, keep and cancel", () => {
    const onChoice = vi.fn();
    render(<DesktopAdoptCollisionsDialog collisions={[]} onChoice={onChoice} />);
    fireEvent.click(screen.getByRole("button", { name: "Replace Them" }));
    fireEvent.click(screen.getByRole("button", { name: "Keep Them" }));
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onChoice.mock.calls).toEqual([["overwrite"], ["keep"], ["cancel"], ["cancel"]]);
  });
});

describe("DesktopAdoptUnusableDialog", () => {
  const UNUSABLE: UnusableNamesakeResult = {
    success: false,
    reason: "unusable_namesake",
    message: "",
    incoming: { name: "Game.sfc", size_bytes: 2048 },
    existing: [{ name: "Game", path: "/roms/snes/Game", kind: "dir" }],
    served_is_dir: false,
    truncated: false,
  };

  it("says a download makes a second copy, and lists what is there", () => {
    render(<DesktopAdoptUnusableDialog namesake={UNUSABLE} onChoice={vi.fn()} />);
    const dialog = screen.getByRole("dialog", { name: "Something With This Name Is Already Here" });
    expect(dialog).toHaveTextContent("Your server sends this game as a single file");
    expect(dialog).toHaveTextContent("Game (folder)");
    expect(dialog).toHaveTextContent(UNUSABLE_DOWNLOAD_NOTE);
    expect(dialog).not.toHaveTextContent("Only the first");
  });

  it("resolves download and cancel", () => {
    const onChoice = vi.fn();
    render(<DesktopAdoptUnusableDialog namesake={UNUSABLE} onChoice={onChoice} />);
    fireEvent.click(screen.getByRole("button", { name: "Download Game.sfc Anyway" }));
    fireEvent.click(screen.getByRole("button", { name: "Close dialog" }));
    expect(onChoice.mock.calls).toEqual([["download"], ["cancel"]]);
  });
});

describe("DesktopAdoptVanishedDialog", () => {
  const VANISHED: CandidateVanishedResult = {
    success: false,
    reason: "candidate_vanished",
    message: "",
    incoming: { name: "Game.sfc", size_bytes: 2048 },
  };

  it("claims no cause and offers the download or a look first", () => {
    const onChoice = vi.fn();
    render(<DesktopAdoptVanishedDialog vanished={VANISHED} onChoice={onChoice} />);
    const dialog = screen.getByRole("dialog", { name: "The Copy on This Device Cannot Be Found" });
    expect(dialog).toHaveTextContent(VANISHED_INTRO);
    expect(dialog).toHaveTextContent(VANISHED_DOWNLOAD_NOTE);
    fireEvent.click(screen.getByRole("button", { name: "Download Game.sfc" }));
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onChoice.mock.calls).toEqual([["download"], ["cancel"]]);
  });
});
