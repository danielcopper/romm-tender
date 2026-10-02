import { describe, it, expect } from "vitest";
import {
  COLLISION_KIND_LABEL,
  adoptButtonLabel,
  adoptedToast,
  candidateDetail,
  candidatesTruncatedNote,
  describesTheGame,
  existingIntro,
  existingSize,
  formatModifiedAt,
  incomingSize,
  lastChangedLine,
  noneOfTheseLabel,
  nounFor,
  renameNotice,
  replaceWarning,
  sizeVerdict,
  unusableDownloadLabel,
  unusableIntro,
  unusableTruncatedNote,
  vanishedDownloadLabel,
  verifyProgressLabel,
} from "./adoptWording";
import type {
  AdoptionCandidate,
  CandidatesFoundResult,
  CandidateVanishedResult,
  TargetOccupiedResult,
  UnusableNamesakeResult,
} from "../types";

function occupied(overrides: Partial<TargetOccupiedResult> = {}): TargetOccupiedResult {
  return {
    success: false,
    reason: "target_occupied",
    message: "A file named 'Game.sfc' is already in place",
    existing: {
      name: "Game.sfc",
      path: "/roms/snes/Game.sfc",
      kind: "file",
      size_bytes: 2048,
      modified_at: 1_700_000_000,
    },
    incoming: { name: "Game.sfc", size_bytes: 1024 },
    sizes_match: false,
    adoptable: true,
    ...overrides,
  };
}

function ofKind(kind: TargetOccupiedResult["existing"]["kind"], size_bytes = 2048): TargetOccupiedResult {
  return occupied({ existing: { ...occupied().existing, kind, size_bytes }, sizes_match: null });
}

const CANDIDATE: AdoptionCandidate = {
  name: "Game (U).sfc",
  path: "/roms/snes/Game (U).sfc",
  is_dir: false,
  size_bytes: 2048,
  modified_at: 0,
  evidence: "size",
  detail: "Exactly the size the server would send",
};

describe("nounFor / describesTheGame", () => {
  it("names each kind, and a kindless entry only as a thing", () => {
    expect(nounFor(ofKind("file"))).toBe("file");
    expect(nounFor(ofKind("dir"))).toBe("folder");
    expect(nounFor(ofKind("link"))).toBe("shortcut to somewhere else");
    expect(nounFor(ofKind(null))).toBe("thing");
  });

  it("treats only a file or a folder as the game's own content", () => {
    expect(describesTheGame(ofKind("file"))).toBe(true);
    expect(describesTheGame(ofKind("dir"))).toBe(true);
    expect(describesTheGame(ofKind("link"))).toBe(false);
    expect(describesTheGame(ofKind(null))).toBe(false);
  });
});

describe("the modified line", () => {
  it("formats a stamp and has nothing for a zero one", () => {
    expect(formatModifiedAt(0)).toBe("");
    expect(formatModifiedAt(1_700_000_000)).toBe(new Date(1_700_000_000_000).toLocaleString());
  });

  it("is withheld for a zero stamp and for anything whose mtime is not the game's", () => {
    expect(lastChangedLine(occupied())).toBe(`Last changed ${formatModifiedAt(1_700_000_000)}`);
    expect(lastChangedLine(occupied({ existing: { ...occupied().existing, modified_at: 0 } }))).toBeNull();
    expect(lastChangedLine(ofKind("link"))).toBeNull();
    expect(lastChangedLine(ofKind(null))).toBeNull();
  });
});

describe("existingIntro", () => {
  it("places content at the game's own location", () => {
    expect(existingIntro(ofKind("dir"), false)).toBe(
      "A folder is already where this game would be downloaded. Tender did not put it there, so it will not be " +
        "touched until you decide.",
    );
  });

  it("does not claim a candidate is where the download would land", () => {
    expect(existingIntro(occupied(), true)).toBe(
      "This file carries this game's name. Tender did not put it there, so it will not be touched until you decide.",
    );
  });
});

describe("sizes", () => {
  it("prints a file's own size, and none for a shortcut or a kindless entry", () => {
    expect(existingSize(occupied(), false)).toBe("2.0 KB");
    expect(existingSize(ofKind("link", 19), false)).toBe("Shortcut — no size of its own");
    expect(existingSize(ofKind(null, 0), false)).toBe("No size to show");
  });

  it("never prints 0 B for a candidate folder the search did not measure", () => {
    expect(existingSize(ofKind("dir", 0), true)).toBe("Folder — not measured");
    // At the game's own location a folder was stat'ed, so its number stands.
    expect(existingSize(ofKind("dir", 4096), false)).toBe("4.0 KB");
  });

  it("states a server size of zero as unknown", () => {
    expect(incomingSize(occupied())).toBe("1.0 KB");
    expect(incomingSize(occupied({ incoming: { name: "Game.sfc", size_bytes: 0 } }))).toBe("Size unknown");
  });
});

describe("sizeVerdict", () => {
  it("states the difference in either direction", () => {
    expect(sizeVerdict(occupied(), false)).toBe("What is here is 1.0 KB larger than what the server would send.");
    expect(sizeVerdict(occupied({ existing: { ...occupied().existing, size_bytes: 512 } }), false)).toBe(
      "What is here is 512 B smaller than what the server would send.",
    );
  });

  it("says when they match", () => {
    expect(sizeVerdict(occupied({ sizes_match: true }), false)).toBe("Both are the same size.");
  });

  it("blames the server only when the server stated no size", () => {
    expect(sizeVerdict(occupied({ sizes_match: null }), true)).toBe(
      "The server did not state a size, so the two cannot be compared.",
    );
    expect(sizeVerdict(ofKind("dir", 0), true)).toBe(
      "Folders are not measured before you open this, so the two sizes are not compared.",
    );
  });

  it("compares nothing for a shortcut or a kindless entry", () => {
    expect(sizeVerdict(ofKind("link"), false)).toBe(
      "A shortcut is not the game's bytes, so there is nothing here to compare.",
    );
    expect(sizeVerdict(ofKind(null), false)).toBe(
      "This is not a file or a folder, so there is nothing here to compare.",
    );
  });
});

describe("replaceWarning", () => {
  it("names the file and its size, and that it is gone for good", () => {
    expect(replaceWarning(occupied(), false)).toBe(
      "Downloading deletes the file that is here now — Game.sfc, 2.0 KB. If it is your own dump, patch or romhack, " +
        "it is gone. Continue?",
    );
  });

  it("promises a shortcut's target survives", () => {
    expect(replaceWarning(ofKind("link"), false)).toBe(
      "Downloading deletes the shortcut that is here now — Game.sfc. Whatever it points at is left alone. Continue?",
    );
  });

  it("claims no more about a kindless entry than that it goes", () => {
    expect(replaceWarning(ofKind(null), false)).toBe(
      "Downloading removes what is here now — Game.sfc. Tender cannot tell what it is, only that it goes. Continue?",
    );
  });

  it("does not claim an unmeasured candidate folder is 0 B", () => {
    expect(replaceWarning(ofKind("dir", 0), true)).toContain("Downloading deletes the folder that is here now");
    expect(replaceWarning(ofKind("dir", 0), true)).not.toContain("0 B");
  });
});

describe("the comparison's other lines", () => {
  it("states the rename by the server's name", () => {
    expect(renameNotice(occupied({ incoming: { name: "Game (USA).sfc", size_bytes: 2048 } }))).toBe(
      "Using it renames it to Game (USA).sfc, and moves any saves and savestates named after it with it, so this " +
        "game works the same as one Tender downloaded.",
    );
  });

  it("labels adopt as an offer, or says why it is not one", () => {
    expect(adoptButtonLabel(occupied())).toBe("Use These Files");
    expect(adoptButtonLabel({ ...ofKind("link"), adoptable: false })).toBe(
      "Can't use this shortcut to somewhere else for this game",
    );
  });

  it("shows a percentage only once a frame has arrived", () => {
    expect(verifyProgressLabel(null)).toBe("Checking the files…");
    expect(verifyProgressLabel(0)).toBe("Checking the files…");
    expect(verifyProgressLabel(0.25)).toBe("Checking the files… 25%");
  });
});

describe("the candidate list", () => {
  const found: CandidatesFoundResult = {
    success: false,
    reason: "adoption_candidates",
    message: "",
    incoming: { name: "Game (USA).sfc", size_bytes: 2048 },
    candidates: [CANDIDATE, { ...CANDIDATE, path: "/x" }],
    truncated: true,
  };

  it("states each row's evidence and its size, or that it is a folder", () => {
    expect(candidateDetail(CANDIDATE)).toBe("Exactly the size the server would send — 2.0 KB");
    expect(candidateDetail({ ...CANDIDATE, is_dir: true })).toBe("Exactly the size the server would send — folder");
  });

  it("counts what is shown and names the server's file on the way out", () => {
    expect(candidatesTruncatedNote(found)).toBe(
      "Only the 2 strongest matches are shown — there are more in this folder.",
    );
    expect(noneOfTheseLabel(found)).toBe("None of These — Download Game (USA).sfc");
  });
});

describe("the collision, unusable and backstop dialogs", () => {
  it("names each colliding kind", () => {
    expect(COLLISION_KIND_LABEL).toEqual({ rom: "game file", save: "save", savestate: "savestate" });
  });

  it("says what shape the server sends, and what downloading leaves behind", () => {
    const unusable: UnusableNamesakeResult = {
      success: false,
      reason: "unusable_namesake",
      message: "",
      incoming: { name: "Game (USA).sfc", size_bytes: 2048 },
      existing: [{ name: "Game (U)", path: "/roms/snes/Game (U)", kind: "dir" }],
      served_is_dir: false,
      truncated: false,
    };
    expect(unusableIntro(unusable)).toContain("Your server sends this game as a single file,");
    expect(unusableIntro({ ...unusable, served_is_dir: true })).toContain("as a folder of several files,");
    expect(unusableIntro(unusable)).toContain("Downloading leaves you with two copies");
    expect(unusableTruncatedNote(unusable)).toBe("Only the first 1 are shown — there are more in this folder.");
    expect(unusableDownloadLabel(unusable)).toBe("Download Game (USA).sfc Anyway");
  });

  it("names the server's file on the backstop's download", () => {
    const vanished: CandidateVanishedResult = {
      success: false,
      reason: "candidate_vanished",
      message: "",
      incoming: { name: "Game (USA).sfc", size_bytes: 2048 },
    };
    expect(vanishedDownloadLabel(vanished)).toBe("Download Game (USA).sfc");
  });
});

describe("toasts", () => {
  it("names the adopted ROM, and calls one with no name a ROM", () => {
    expect(adoptedToast("Super Metroid")).toBe("Super Metroid is ready to play");
    expect(adoptedToast("")).toBe("ROM is ready to play");
  });
});
