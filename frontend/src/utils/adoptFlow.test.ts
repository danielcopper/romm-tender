import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { toaster } from "../api/host";
import * as backend from "../api/backend";
import {
  comparisonForCandidate,
  runDownloadWithAdoption,
  type AdoptionDialogs,
  type AdoptionFlowHooks,
  type DownloadWithAdoption,
} from "./adoptFlow";
import { mountPruneLeaseOwner, releasePruneLeasesByOwner } from "./pruneLease";
import { setLaunchOptionsConfirmed } from "./steamShortcuts";
import type {
  AdoptResult,
  CandidatesFoundResult,
  CandidateVanishedResult,
  RenameCollisionsResult,
  TargetOccupiedResult,
  UnusableNamesakeResult,
} from "../types";

vi.mock("./steamShortcuts", () => ({
  setLaunchOptionsConfirmed: vi.fn().mockResolvedValue(true),
}));

const ROM_ID = 42;
const OWNER = "adopt-flow-test";

const OCCUPIED: TargetOccupiedResult = {
  success: false,
  reason: "target_occupied",
  message: "A file named 'game.z64' is already in place",
  existing: { name: "game.z64", path: "/roms/n64/game.z64", kind: "file", size_bytes: 2048, modified_at: 0 },
  incoming: { name: "game.z64", size_bytes: 1024 },
  sizes_match: false,
  adoptable: true,
};

const CANDIDATE = {
  name: "game (U).z64",
  path: "/roms/n64/game (U).z64",
  is_dir: false,
  size_bytes: 2048,
  modified_at: 0,
  evidence: "size" as const,
  detail: "Exactly the size the server would send",
};
const OTHER = { ...CANDIDATE, name: "game (E).z64", path: "/roms/n64/game (E).z64" };

const FOUND: CandidatesFoundResult = {
  success: false,
  reason: "adoption_candidates",
  message: "'game (U).z64' is already on this device",
  incoming: { name: "game (USA).z64", size_bytes: 2048 },
  candidates: [CANDIDATE],
  truncated: false,
};

const UNUSABLE: UnusableNamesakeResult = {
  success: false,
  reason: "unusable_namesake",
  message: "'game (U)' has this game's name but is a folder",
  incoming: { name: "game (USA).z64", size_bytes: 2048 },
  existing: [{ name: "game (U)", path: "/roms/n64/game (U)", kind: "dir" }],
  served_is_dir: false,
  truncated: false,
};

const VANISHED: CandidateVanishedResult = {
  success: false,
  reason: "candidate_vanished",
  message: "What was found on this device is no longer there",
  incoming: { name: "game (USA).z64", size_bytes: 2048 },
};

const COLLISIONS: RenameCollisionsResult = {
  success: false,
  reason: "rename_collisions",
  message: "'game (USA).srm' already exists",
  collisions: [{ name: "game (USA).srm", path: "/saves/n64/game (USA).srm", kind: "save" }],
};

const STARTED = { success: true, message: "Download started" };

const ADOPTED: AdoptResult = {
  success: true,
  message: "Using the files already on this device",
  file_path: "/roms/n64/game.z64",
  rom_dir: null,
  app_id: 100,
  launch_options: 'flatpak run … "/roms/n64/game.z64"',
  prune_lease_token: "adopt-token",
};

/** Every hook call, in order, so a test can assert what the caller was told and when. */
let log: string[];

function makeFlow(overrides: Partial<DownloadWithAdoption> = {}) {
  const dialogs = {
    showExisting: vi.fn<AdoptionDialogs["showExisting"]>().mockResolvedValue("cancel"),
    showCandidates: vi.fn<AdoptionDialogs["showCandidates"]>().mockResolvedValue({ kind: "cancel" }),
    showCollisions: vi.fn<AdoptionDialogs["showCollisions"]>().mockResolvedValue("cancel"),
    showUnusable: vi.fn<AdoptionDialogs["showUnusable"]>().mockResolvedValue("cancel"),
    showVanished: vi.fn<AdoptionDialogs["showVanished"]>().mockResolvedValue("cancel"),
  };
  const hooks: AdoptionFlowHooks = {
    setBusy: (busy) => log.push(`busy:${busy}`),
    setTargetOccupied: (occupied) => log.push(`occupied:${occupied}`),
    setCandidatePresent: (present) => log.push(`candidate:${present}`),
    onAdopted: () => log.push("adopted"),
  };
  const flow: DownloadWithAdoption = {
    romId: ROM_ID,
    romName: "Test ROM",
    pageSawCandidate: false,
    leaseOwner: OWNER,
    logContext: "test",
    dialogs,
    hooks,
    ...overrides,
  };
  return { flow, dialogs };
}

function toastBodies(): unknown[] {
  return vi.mocked(toaster.toast).mock.calls.map(([data]) => data.body);
}

beforeEach(() => {
  log = [];
  vi.mocked(backend.startDownload).mockReset();
  vi.mocked(backend.adoptExistingRom).mockReset();
  vi.mocked(backend.releasePruneConflictLease).mockClear();
  vi.mocked(setLaunchOptionsConfirmed).mockClear();
  vi.mocked(toaster.toast).mockClear();
  mountPruneLeaseOwner(OWNER);
});

afterEach(async () => {
  await releasePruneLeasesByOwner(OWNER);
});

describe("runDownloadWithAdoption — a plain download", () => {
  it("starts the download, leaves the caller busy and reports it started", async () => {
    vi.mocked(backend.startDownload).mockResolvedValue(STARTED);
    const { flow } = makeFlow();

    await expect(runDownloadWithAdoption(flow)).resolves.toBe("download_started");

    expect(vi.mocked(backend.startDownload).mock.calls).toEqual([[ROM_ID, false, null, null, false]]);
    // The transfer's progress events drive the caller from here.
    expect(log).toEqual(["busy:true"]);
    expect(vi.mocked(toaster.toast)).not.toHaveBeenCalled();
  });

  it("reports what the page saw on the first request", async () => {
    vi.mocked(backend.startDownload).mockResolvedValue(STARTED);
    const { flow } = makeFlow({ pageSawCandidate: true });

    await runDownloadWithAdoption(flow);

    expect(vi.mocked(backend.startDownload).mock.calls[0]).toEqual([ROM_ID, false, null, null, true]);
  });

  it("toasts a refusal's message and clears busy", async () => {
    vi.mocked(backend.startDownload).mockResolvedValue({ success: false, message: "Not enough space" });
    const { flow } = makeFlow();

    await expect(runDownloadWithAdoption(flow)).resolves.toBe("failed");

    expect(toastBodies()).toEqual(["Not enough space"]);
    expect(log).toEqual(["busy:true", "busy:false"]);
  });

  it("falls back to a generic sentence for a refusal with no message", async () => {
    vi.mocked(backend.startDownload).mockResolvedValue({ success: false, message: "" });
    const { flow } = makeFlow();

    await runDownloadWithAdoption(flow);

    expect(toastBodies()).toEqual(["Download failed"]);
  });

  it("toasts a thrown request rather than swallowing it", async () => {
    vi.mocked(backend.startDownload).mockRejectedValue(new Error("bridge down"));
    const { flow } = makeFlow();

    await expect(runDownloadWithAdoption(flow)).resolves.toBe("failed");

    expect(toastBodies()).toEqual(["Download failed — is RomM server running?"]);
    expect(log).toEqual(["busy:true", "busy:false"]);
  });
});

describe("runDownloadWithAdoption — content at the game's own location", () => {
  it("records the occupancy and clears busy before the dialog opens", async () => {
    vi.mocked(backend.startDownload).mockResolvedValue(OCCUPIED);
    const { flow, dialogs } = makeFlow();
    dialogs.showExisting.mockImplementation(async () => {
      log.push("dialog");
      return "cancel";
    });

    await expect(runDownloadWithAdoption(flow)).resolves.toBe("cancelled");

    expect(log).toEqual(["busy:true", "occupied:true", "busy:false", "dialog"]);
    // No candidate path for content at the game's own location — not even an
    // explicit `undefined`.
    expect(dialogs.showExisting.mock.calls).toEqual([[ROM_ID, OCCUPIED]]);
    expect(vi.mocked(backend.startDownload)).toHaveBeenCalledTimes(1);
    expect(vi.mocked(backend.adoptExistingRom)).not.toHaveBeenCalled();
    expect(vi.mocked(toaster.toast)).not.toHaveBeenCalled();
  });

  it("replace re-sends with the replace flag and names nothing", async () => {
    vi.mocked(backend.startDownload).mockResolvedValueOnce(OCCUPIED).mockResolvedValueOnce(STARTED);
    const { flow, dialogs } = makeFlow({ pageSawCandidate: true });
    dialogs.showExisting.mockResolvedValue("replace");

    await expect(runDownloadWithAdoption(flow)).resolves.toBe("download_started");

    // The re-send is an answer, so it never reports what the page saw.
    expect(vi.mocked(backend.startDownload).mock.calls).toEqual([
      [ROM_ID, false, null, null, true],
      [ROM_ID, true, null, null, false],
    ]);
  });

  it("adopt records the install, writes the launch command and tells the caller in order", async () => {
    vi.mocked(backend.startDownload).mockResolvedValue(OCCUPIED);
    vi.mocked(backend.adoptExistingRom).mockResolvedValue(ADOPTED);
    const { flow, dialogs } = makeFlow();
    dialogs.showExisting.mockResolvedValue("adopt");
    const dataChanged = vi.fn((e: Event) => log.push(`event:${JSON.stringify((e as CustomEvent).detail)}`));
    globalThis.addEventListener("romm_data_changed", dataChanged);

    try {
      await expect(runDownloadWithAdoption(flow)).resolves.toBe("adopted");
    } finally {
      globalThis.removeEventListener("romm_data_changed", dataChanged);
    }

    expect(vi.mocked(backend.adoptExistingRom).mock.calls).toEqual([[ROM_ID, null, null]]);
    expect(vi.mocked(setLaunchOptionsConfirmed)).toHaveBeenCalledWith(100, ADOPTED.launch_options);
    expect(vi.mocked(backend.releasePruneConflictLease)).toHaveBeenCalledWith("adopt-token");
    expect(log).toEqual([
      "busy:true",
      "occupied:true",
      "busy:false",
      "busy:true",
      "occupied:false",
      "candidate:false",
      "adopted",
      `event:${JSON.stringify({ type: "rom_adopted", rom_id: ROM_ID })}`,
      "busy:false",
    ]);
    expect(toastBodies()).toEqual(["Test ROM is ready to play"]);
  });

  it("an adoption whose launch-options write rejects is still adopted", async () => {
    vi.mocked(backend.startDownload).mockResolvedValue(OCCUPIED);
    vi.mocked(backend.adoptExistingRom).mockResolvedValue(ADOPTED);
    vi.mocked(setLaunchOptionsConfirmed).mockRejectedValueOnce(new Error("SteamClient threw"));
    const { flow, dialogs } = makeFlow();
    dialogs.showExisting.mockResolvedValue("adopt");

    // The backend has recorded the install by now, so a failed Steam write is
    // not a failed adoption and must not be toasted as one.
    await expect(runDownloadWithAdoption(flow)).resolves.toBe("adopted");

    expect(toastBodies()).toEqual(["Test ROM is ready to play"]);
  });

  it("holds the adopt's Steam write to the owner generation the press was made in", async () => {
    vi.mocked(backend.startDownload).mockResolvedValue(OCCUPIED);
    let answer!: (result: AdoptResult) => void;
    vi.mocked(backend.adoptExistingRom).mockReturnValue(new Promise<AdoptResult>((resolve) => (answer = resolve)));
    const { flow, dialogs } = makeFlow();
    dialogs.showExisting.mockResolvedValue("adopt");

    const run = runDownloadWithAdoption(flow);
    await vi.waitFor(() => expect(vi.mocked(backend.adoptExistingRom)).toHaveBeenCalled());
    // The page is left and opened again while the request is out: a capture
    // taken after it answers would see the new generation and write anyway.
    await releasePruneLeasesByOwner(OWNER);
    mountPruneLeaseOwner(OWNER);
    answer(ADOPTED);
    await run;

    expect(vi.mocked(setLaunchOptionsConfirmed)).not.toHaveBeenCalled();
  });

  it("names the ROM generically when the caller has no name for it", async () => {
    vi.mocked(backend.startDownload).mockResolvedValue(OCCUPIED);
    vi.mocked(backend.adoptExistingRom).mockResolvedValue(ADOPTED);
    const { flow, dialogs } = makeFlow({ romName: "" });
    dialogs.showExisting.mockResolvedValue("adopt");

    await runDownloadWithAdoption(flow);

    expect(toastBodies()).toEqual(["ROM is ready to play"]);
  });

  it("an unbound adoption writes no launch options and releases no lease", async () => {
    vi.mocked(backend.startDownload).mockResolvedValue(OCCUPIED);
    vi.mocked(backend.adoptExistingRom).mockResolvedValue({
      ...ADOPTED,
      app_id: null,
      launch_options: "",
      prune_lease_token: "stray-token",
    });
    const { flow, dialogs } = makeFlow();
    dialogs.showExisting.mockResolvedValue("adopt");

    await expect(runDownloadWithAdoption(flow)).resolves.toBe("adopted");

    expect(vi.mocked(setLaunchOptionsConfirmed)).not.toHaveBeenCalled();
    expect(vi.mocked(backend.releasePruneConflictLease)).not.toHaveBeenCalled();
  });

  it("an adoption with no launch command writes none", async () => {
    vi.mocked(backend.startDownload).mockResolvedValue(OCCUPIED);
    vi.mocked(backend.adoptExistingRom).mockResolvedValue({
      success: true,
      message: "ok",
      file_path: "/roms/n64/game.z64",
      rom_dir: null,
      app_id: 100,
    });
    const { flow, dialogs } = makeFlow();
    dialogs.showExisting.mockResolvedValue("adopt");

    await expect(runDownloadWithAdoption(flow)).resolves.toBe("adopted");

    expect(vi.mocked(setLaunchOptionsConfirmed)).not.toHaveBeenCalled();
  });

  it("a refused adoption toasts its message and leaves the flags alone", async () => {
    vi.mocked(backend.startDownload).mockResolvedValue(OCCUPIED);
    vi.mocked(backend.adoptExistingRom).mockResolvedValue({
      success: false,
      reason: "nothing_to_adopt",
      message: "The files are no longer there — nothing was adopted",
    });
    const { flow, dialogs } = makeFlow();
    dialogs.showExisting.mockResolvedValue("adopt");

    await expect(runDownloadWithAdoption(flow)).resolves.toBe("failed");

    expect(toastBodies()).toEqual(["The files are no longer there — nothing was adopted"]);
    expect(log).toEqual(["busy:true", "occupied:true", "busy:false", "busy:true", "busy:false"]);
  });

  it("a refused adoption with no message still says what failed", async () => {
    vi.mocked(backend.startDownload).mockResolvedValue(OCCUPIED);
    vi.mocked(backend.adoptExistingRom).mockResolvedValue({ success: false, message: "" });
    const { flow, dialogs } = makeFlow();
    dialogs.showExisting.mockResolvedValue("adopt");

    await runDownloadWithAdoption(flow);

    expect(toastBodies()).toEqual(["Couldn't use the existing files"]);
  });

  it("a thrown adoption is toasted and busy cleared", async () => {
    vi.mocked(backend.startDownload).mockResolvedValue(OCCUPIED);
    vi.mocked(backend.adoptExistingRom).mockRejectedValue(new Error("bridge down"));
    const { flow, dialogs } = makeFlow();
    dialogs.showExisting.mockResolvedValue("adopt");

    await expect(runDownloadWithAdoption(flow)).resolves.toBe("failed");

    expect(toastBodies()).toEqual(["Couldn't use the existing files — is RomM server running?"]);
    expect(log[log.length - 1]).toBe("busy:false");
    expect(log).not.toContain("adopted");
  });

  it("an adoption whose lease owner has gone is not counted as adopted", async () => {
    vi.mocked(backend.startDownload).mockResolvedValue(OCCUPIED);
    vi.mocked(backend.adoptExistingRom).mockResolvedValue(ADOPTED);
    const { flow, dialogs } = makeFlow({ leaseOwner: "never-mounted" });
    dialogs.showExisting.mockResolvedValue("adopt");

    await expect(runDownloadWithAdoption(flow)).resolves.toBe("failed");

    expect(vi.mocked(setLaunchOptionsConfirmed)).not.toHaveBeenCalled();
    expect(log).not.toContain("adopted");
  });
});

describe("runDownloadWithAdoption — the same game under another name", () => {
  it("records the candidate and goes straight to the comparison for a single one", async () => {
    vi.mocked(backend.startDownload).mockResolvedValue(FOUND);
    const { flow, dialogs } = makeFlow();

    await expect(runDownloadWithAdoption(flow)).resolves.toBe("cancelled");

    expect(log).toEqual(["busy:true", "candidate:true", "busy:false"]);
    expect(dialogs.showCandidates).not.toHaveBeenCalled();
    expect(dialogs.showExisting).toHaveBeenCalledWith(
      ROM_ID,
      comparisonForCandidate(CANDIDATE, FOUND.incoming),
      CANDIDATE.path,
    );
  });

  it("opens the list for several and compares the one picked", async () => {
    const several = { ...FOUND, candidates: [CANDIDATE, OTHER] };
    vi.mocked(backend.startDownload).mockResolvedValue(several);
    const { flow, dialogs } = makeFlow();
    dialogs.showCandidates.mockResolvedValue({ kind: "candidate", candidate: OTHER });

    await runDownloadWithAdoption(flow);

    expect(dialogs.showCandidates).toHaveBeenCalledWith(several);
    expect(dialogs.showExisting).toHaveBeenCalledWith(
      ROM_ID,
      comparisonForCandidate(OTHER, FOUND.incoming),
      OTHER.path,
    );
  });

  it("cancelling the list starts nothing", async () => {
    vi.mocked(backend.startDownload).mockResolvedValue({ ...FOUND, candidates: [CANDIDATE, OTHER] });
    const { flow, dialogs } = makeFlow();

    await expect(runDownloadWithAdoption(flow)).resolves.toBe("cancelled");

    expect(vi.mocked(backend.startDownload)).toHaveBeenCalledTimes(1);
    expect(dialogs.showExisting).not.toHaveBeenCalled();
    expect(vi.mocked(backend.adoptExistingRom)).not.toHaveBeenCalled();
  });

  it("'None of These' re-sends with replace and names no candidate", async () => {
    vi.mocked(backend.startDownload)
      .mockResolvedValueOnce({ ...FOUND, candidates: [CANDIDATE, OTHER] })
      .mockResolvedValueOnce(STARTED);
    const { flow, dialogs } = makeFlow();
    dialogs.showCandidates.mockResolvedValue({ kind: "download" });

    await expect(runDownloadWithAdoption(flow)).resolves.toBe("download_started");

    expect(vi.mocked(backend.startDownload).mock.calls).toEqual([
      [ROM_ID, false, null, null, false],
      [ROM_ID, true, null, null, false],
    ]);
  });

  it("Download Instead on a chosen candidate names it, so the backend deletes what was promised", async () => {
    vi.mocked(backend.startDownload).mockResolvedValueOnce(FOUND).mockResolvedValueOnce(STARTED);
    const { flow, dialogs } = makeFlow();
    dialogs.showExisting.mockResolvedValue("replace");

    await runDownloadWithAdoption(flow);

    expect(vi.mocked(backend.startDownload).mock.calls).toEqual([
      [ROM_ID, false, null, null, false],
      [ROM_ID, true, CANDIDATE.path, null, false],
    ]);
  });

  it("adopting a candidate sends its path", async () => {
    vi.mocked(backend.startDownload).mockResolvedValue(FOUND);
    vi.mocked(backend.adoptExistingRom).mockResolvedValue(ADOPTED);
    const { flow, dialogs } = makeFlow();
    dialogs.showExisting.mockResolvedValue("adopt");

    await expect(runDownloadWithAdoption(flow)).resolves.toBe("adopted");

    expect(vi.mocked(backend.adoptExistingRom).mock.calls).toEqual([[ROM_ID, CANDIDATE.path, null]]);
  });

  it("an empty candidate list opens nothing", async () => {
    vi.mocked(backend.startDownload).mockResolvedValue({ ...FOUND, candidates: [] });
    const { flow, dialogs } = makeFlow();

    await expect(runDownloadWithAdoption(flow)).resolves.toBe("cancelled");

    expect(dialogs.showCandidates).not.toHaveBeenCalled();
    expect(dialogs.showExisting).not.toHaveBeenCalled();
  });
});

describe("runDownloadWithAdoption — names already taken", () => {
  it("re-asks the adoption with the answer and the same candidate", async () => {
    vi.mocked(backend.startDownload).mockResolvedValue(FOUND);
    vi.mocked(backend.adoptExistingRom).mockResolvedValueOnce(COLLISIONS).mockResolvedValueOnce(ADOPTED);
    const { flow, dialogs } = makeFlow();
    dialogs.showExisting.mockResolvedValue("adopt");
    dialogs.showCollisions.mockResolvedValue("overwrite");

    await expect(runDownloadWithAdoption(flow)).resolves.toBe("adopted");

    expect(dialogs.showCollisions).toHaveBeenCalledWith(COLLISIONS.collisions);
    expect(vi.mocked(backend.adoptExistingRom).mock.calls).toEqual([
      [ROM_ID, CANDIDATE.path, null],
      [ROM_ID, CANDIDATE.path, "overwrite"],
    ]);
  });

  it("cancelling the adoption's collision dialog adopts nothing and toasts nothing", async () => {
    vi.mocked(backend.startDownload).mockResolvedValue(FOUND);
    vi.mocked(backend.adoptExistingRom).mockResolvedValue(COLLISIONS);
    const { flow, dialogs } = makeFlow();
    dialogs.showExisting.mockResolvedValue("adopt");

    await expect(runDownloadWithAdoption(flow)).resolves.toBe("cancelled");

    expect(vi.mocked(backend.adoptExistingRom)).toHaveBeenCalledTimes(1);
    expect(vi.mocked(toaster.toast)).not.toHaveBeenCalled();
    expect(vi.mocked(setLaunchOptionsConfirmed)).not.toHaveBeenCalled();
    expect(log[log.length - 1]).toBe("busy:false");
  });

  it("re-sends the download with the answer, keeping replace and the candidate", async () => {
    vi.mocked(backend.startDownload)
      .mockResolvedValueOnce(FOUND)
      .mockResolvedValueOnce(COLLISIONS)
      .mockResolvedValueOnce(STARTED);
    const { flow, dialogs } = makeFlow();
    dialogs.showExisting.mockResolvedValue("replace");
    dialogs.showCollisions.mockResolvedValue("keep");

    await expect(runDownloadWithAdoption(flow)).resolves.toBe("download_started");

    expect(vi.mocked(backend.startDownload).mock.calls).toEqual([
      [ROM_ID, false, null, null, false],
      [ROM_ID, true, CANDIDATE.path, null, false],
      [ROM_ID, true, CANDIDATE.path, "keep", false],
    ]);
  });

  it("clears busy before the download's collision dialog opens", async () => {
    vi.mocked(backend.startDownload).mockResolvedValue(COLLISIONS);
    const { flow, dialogs } = makeFlow();
    dialogs.showCollisions.mockImplementation(async () => {
      log.push("dialog");
      return "cancel";
    });

    await expect(runDownloadWithAdoption(flow)).resolves.toBe("cancelled");

    expect(log).toEqual(["busy:true", "busy:false", "dialog"]);
    expect(vi.mocked(backend.startDownload)).toHaveBeenCalledTimes(1);
    expect(vi.mocked(toaster.toast)).not.toHaveBeenCalled();
  });
});

describe("runDownloadWithAdoption — a namesake nothing can adopt", () => {
  it("opens its own dialog and moves neither flag", async () => {
    vi.mocked(backend.startDownload).mockResolvedValue(UNUSABLE);
    const { flow, dialogs } = makeFlow();

    await expect(runDownloadWithAdoption(flow)).resolves.toBe("cancelled");

    expect(dialogs.showUnusable).toHaveBeenCalledWith(UNUSABLE);
    expect(dialogs.showExisting).not.toHaveBeenCalled();
    expect(dialogs.showCandidates).not.toHaveBeenCalled();
    expect(log).toEqual(["busy:true", "busy:false"]);
    expect(vi.mocked(toaster.toast)).not.toHaveBeenCalled();
  });

  it("downloading anyway re-sends with replace and names no candidate", async () => {
    vi.mocked(backend.startDownload).mockResolvedValueOnce(UNUSABLE).mockResolvedValueOnce(STARTED);
    const { flow, dialogs } = makeFlow();
    dialogs.showUnusable.mockResolvedValue("download");

    await expect(runDownloadWithAdoption(flow)).resolves.toBe("download_started");

    expect(vi.mocked(backend.startDownload).mock.calls[1]).toEqual([ROM_ID, true, null, null, false]);
  });
});

describe("runDownloadWithAdoption — the backstop", () => {
  it("drops the candidate flag and opens its dialog", async () => {
    vi.mocked(backend.startDownload).mockResolvedValue(VANISHED);
    const { flow, dialogs } = makeFlow({ pageSawCandidate: true });

    await expect(runDownloadWithAdoption(flow)).resolves.toBe("cancelled");

    expect(dialogs.showVanished).toHaveBeenCalledWith(VANISHED);
    expect(log).toEqual(["busy:true", "candidate:false", "busy:false"]);
    expect(vi.mocked(backend.startDownload)).toHaveBeenCalledTimes(1);
    expect(vi.mocked(toaster.toast)).not.toHaveBeenCalled();
  });

  it("downloading from it re-sends with replace and does not report the page again", async () => {
    vi.mocked(backend.startDownload).mockResolvedValueOnce(VANISHED).mockResolvedValueOnce(STARTED);
    const { flow, dialogs } = makeFlow({ pageSawCandidate: true });
    dialogs.showVanished.mockResolvedValue("download");

    await expect(runDownloadWithAdoption(flow)).resolves.toBe("download_started");

    expect(vi.mocked(backend.startDownload).mock.calls).toEqual([
      [ROM_ID, false, null, null, true],
      [ROM_ID, true, null, null, false],
    ]);
  });
});

describe("comparisonForCandidate", () => {
  const base = {
    name: "Game (U).sfc",
    path: "/roms/snes/Game (U).sfc",
    is_dir: false,
    size_bytes: 2048,
    modified_at: 1_700_000_000,
    evidence: "size" as const,
    detail: "Exactly the size the server would send",
  };

  it("carries the candidate's own numbers into the comparison", () => {
    const comparison = comparisonForCandidate(base, { name: "Game (USA).sfc", size_bytes: 2048 });
    expect(comparison.existing).toEqual({
      name: "Game (U).sfc",
      path: "/roms/snes/Game (U).sfc",
      kind: "file",
      size_bytes: 2048,
      modified_at: 1_700_000_000,
    });
    expect(comparison.sizes_match).toBe(true);
  });

  it("reports a size difference rather than hiding it", () => {
    const comparison = comparisonForCandidate(base, { name: "Game (USA).sfc", size_bytes: 4096 });
    expect(comparison.sizes_match).toBe(false);
  });

  it("cannot compare a folder, because the search never sized one", () => {
    const comparison = comparisonForCandidate(
      { ...base, is_dir: true, size_bytes: 0 },
      { name: "Game (USA)", size_bytes: 4096 },
    );
    expect(comparison.existing.kind).toBe("dir");
    expect(comparison.sizes_match).toBeNull();
  });

  it("cannot compare when the server stated no size", () => {
    const comparison = comparisonForCandidate(base, { name: "Game (USA).sfc", size_bytes: 0 });
    expect(comparison.sizes_match).toBeNull();
  });

  it("always offers the candidate, because the search only ever returns a usable shape", () => {
    expect(comparisonForCandidate(base, { name: "Game (USA).sfc", size_bytes: 2048 }).adoptable).toBe(true);
  });
});
