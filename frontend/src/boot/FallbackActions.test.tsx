/**
 * What the fallback page offers: the Tender update that repairs it, Check for
 * updates and Restart Steam — through endpoint calls alone, worded with
 * Settings › Updates' sentences, and pressed with the buttons the start-up
 * report allows.
 */

import { readFileSync } from "node:fs";

import "@testing-library/jest-dom/vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  checkForUpdateNow,
  getUpdateInstallState,
  getUpdateNotice,
  getUpdateOutcome,
  installUpdate,
  type UpdateFailure,
  type UpdateInstallAttempt,
  type UpdateInstallState,
  type UpdateNotice,
  type UpdateOutcome,
} from "../api/backend";
import { toaster } from "../api/host";
import { setStrandedAnswer } from "../test-utils/stranded-panel";
import { resetUpdateInstallStoreForTests, setUpdateInstallAttempt } from "../utils/updateInstallStore";
import { getUpdateNoticeState, resetUpdateNoticeStoreForTests } from "../utils/updateNoticeStore";
import {
  getUpdateOutcomeState,
  resetUpdateOutcomeStoreForTests,
  updateFailureReason,
} from "../utils/updateOutcomeStore";
import { FallbackActions, TOUCH_SCREEN_LINE } from "./FallbackActions";
import { PLAIN_BUTTONS, STEAM_BUTTONS, fallbackButtons } from "./fallbackButtons";
import type { SearchingCopy } from "./searchingCopy";
import { StartupFailurePanel } from "./StartupFailurePanel";
import {
  SEARCH_OWNERS,
  STEAM_LOOKUPS,
  type SearchOwner,
  type StartupReport,
  searchOwner,
  sentenceNamesATenderUpdate,
} from "./steamModules";

// The page logs what it could not read, and that line is the only trace a
// failed read leaves, so it is what a catch is asserted by.
const logError = vi.hoisted(() => vi.fn());
vi.mock("../api/backend", async (importActual) => ({
  ...(await importActual<typeof import("../api/backend")>()),
  logError,
}));

const NOTICE: UpdateNotice = {
  available: true,
  newer: true,
  latest_version: "1.2.0",
  current_version: "1.1.0",
  enabled: true,
  installed_program: true,
  toast_owed: false,
  seen: false,
};

const OFFERED: UpdateInstallState = {
  offered: true,
  version: "1.2.0",
  wait_reasons: [],
  paused_downloads: 0,
  attempt: null,
  try_again: false,
};

const NOTHING_OFFERED: UpdateInstallState = { ...OFFERED, offered: false, version: null };

const NO_OUTCOME: UpdateOutcome = {
  announce_version: null,
  announce_direction: null,
  toast_owed: false,
  failure: null,
  failure_dismissed: false,
  failure_toast_owed: false,
};

const ROLLED_BACK: UpdateFailure = {
  attempted_version: "1.2.0",
  restored_version: "1.1.0",
  rolled_back_at: "2026-10-16T10:00:00Z",
  kind: "rollback",
};

const DOWNLOADING: UpdateInstallAttempt = {
  version: "1.2.0",
  step: "downloading",
  bytes_done: 50,
  bytes_total: 100,
  failure: null,
};

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

/** One page per verdict, as `StartupFailurePanel.test.tsx` arranges them. */
const verdictCases: Record<SearchOwner, { report: StartupReport; copy: SearchingCopy }> = {
  none: { report: { ...report(["SP_REACTDOM"]), missingPackageNames: [] }, copy: DECKYS },
  tender: { report: report(["Tabs"]), copy: OURS },
  disagreement: { report: report(["Tabs"]), copy: DECKYS_WITHOUT_THE_NAME },
  mixed: { report: { ...report(["SP_REACTDOM", "Tabs"]), missingPackageNames: ["Tabs"] }, copy: DECKYS },
  decky: { report: report(["Tabs"]), copy: DECKYS },
};

/** The page as `index.tsx` composes it. */
function renderPage(missed: StartupReport, copy: SearchingCopy) {
  return render(
    <StartupFailurePanel report={missed} copy={copy}>
      <FallbackActions offerUpdate={sentenceNamesATenderUpdate(missed, copy)} buttons={fallbackButtons(missed)} />
    </StartupFailurePanel>,
  );
}

function renderOffer(buttons = PLAIN_BUTTONS) {
  return render(<FallbackActions offerUpdate={true} buttons={buttons} />);
}

/** Let every read the page started answer, and the page take each answer in. */
const settle = () =>
  act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });

const button = (name: string) => screen.getByRole("button", { name });

function stubSteam(running: Array<{ appid: number; display_name: string; local_per_client_data?: object }> = []) {
  vi.stubGlobal("SteamClient", { User: { StartRestart: vi.fn() } });
  vi.stubGlobal("SteamUIStore", { RunningApps: running });
}

const freshNoticeState = (() => {
  resetUpdateNoticeStoreForTests();
  return getUpdateNoticeState();
})();

const freshOutcomeState = (() => {
  resetUpdateOutcomeStoreForTests();
  return getUpdateOutcomeState();
})();

beforeEach(() => {
  logError.mockClear();
  resetUpdateNoticeStoreForTests();
  resetUpdateOutcomeStoreForTests();
  vi.mocked(toaster.toast).mockClear();
  resetUpdateInstallStoreForTests();
  vi.mocked(getUpdateNotice).mockReset().mockResolvedValue(NOTICE);
  vi.mocked(checkForUpdateNow)
    .mockReset()
    .mockResolvedValue({ ...NOTICE, reached: true });
  vi.mocked(getUpdateInstallState).mockReset().mockResolvedValue(OFFERED);
  vi.mocked(installUpdate).mockReset().mockResolvedValue({ success: true });
  vi.mocked(getUpdateOutcome).mockReset().mockResolvedValue(NO_OUTCOME);
  stubSteam();
});

describe("the fallback page's actions", () => {
  it("imports nothing from @decky/ui directly", () => {
    // The buttons are handed in for exactly this reason: the module that may
    // hand Steam's is `fallbackButtons.tsx`, never this one. What this module
    // reaches through its own imports may import the package — those modules
    // were evaluated with the bundle, and nothing rendered here comes from it.
    const source = readFileSync(`${process.cwd()}/src/boot/FallbackActions.tsx`, "utf8");
    const specifiers = [...source.matchAll(/^\s*(?:import|export)[^;]*?from\s+"([^"]+)"/gm)].map((match) => match[1]);
    expect(specifiers.filter((name) => name?.startsWith("@decky/"))).toEqual([]);
  });

  it.each(SEARCH_OWNERS)("offers a Tender update under the %s sentence only where it names one", async (owner) => {
    const { report: missed, copy } = verdictCases[owner];
    expect(searchOwner(missed, copy)).toBe(owner);
    renderPage(missed, copy);
    await settle();
    const offers = owner === "tender" || owner === "disagreement";
    expect(screen.queryByRole("button", { name: "Install update" }) !== null).toBe(offers);
    expect(screen.queryByRole("button", { name: "Check for updates" }) !== null).toBe(offers);
    // Not even the due check is asked where no Tender update is offered.
    expect(vi.mocked(getUpdateNotice).mock.calls.length > 0).toBe(offers);
    expect(button("Restart Steam")).toBeInTheDocument();
  });

  it("offers no Tender update when nothing at all answered", async () => {
    renderPage(report(["Tabs", "Focusable"], 2), OURS);
    await settle();
    expect(screen.queryByRole("button", { name: "Install update" })).not.toBeInTheDocument();
    expect(getUpdateNotice).not.toHaveBeenCalled();
    expect(button("Restart Steam")).toBeInTheDocument();
  });

  it("installs the offered version when Install is pressed", async () => {
    renderOffer();
    await settle();
    expect(screen.getByTestId("fallback-available")).toHaveTextContent("Available: 1.2.0");
    fireEvent.click(button("Install update"));
    await settle();
    expect(installUpdate).toHaveBeenCalledExactlyOnceWith("1.2.0");
  });

  it("asks for the due check when it appears, and asks GitHub now on Check for updates", async () => {
    renderOffer();
    await settle();
    expect(getUpdateNotice).toHaveBeenCalledOnce();
    expect(checkForUpdateNow).not.toHaveBeenCalled();
    fireEvent.click(button("Check for updates"));
    await settle();
    expect(checkForUpdateNow).toHaveBeenCalledOnce();
  });

  it("holds the install while it waits, and says for what", async () => {
    vi.mocked(getUpdateInstallState).mockResolvedValue({ ...OFFERED, wait_reasons: [{ reason: "library_sync" }] });
    renderOffer();
    await settle();
    expect(screen.getByTestId("fallback-waiting")).toHaveTextContent("Waiting for:");
    expect(screen.getByTestId("fallback-wait-reason")).toHaveTextContent("Library sync");
    expect(button("Install update")).toBeDisabled();
  });

  it("shows an attempt's steps and percent", async () => {
    vi.mocked(getUpdateInstallState).mockResolvedValue({ ...OFFERED, attempt: DOWNLOADING });
    renderOffer();
    await settle();
    expect(screen.getByTestId("fallback-caption")).toHaveTextContent("Downloading 1.2.0");
    expect(screen.getByTestId("fallback-progress")).toHaveAttribute("data-percent", "50");
    expect(screen.getByTestId("fallback-step-download")).toHaveAttribute("data-status", "current");
    expect(screen.getByTestId("fallback-step-install")).toHaveAttribute("data-status", "pending");
    expect(screen.getByTestId("fallback-note")).toHaveTextContent("Starting a game now cancels the update.");
    expect(button("Installing…")).toBeDisabled();
  });

  it("states a failed attempt and offers to try again", async () => {
    vi.mocked(getUpdateInstallState).mockResolvedValue({
      ...OFFERED,
      attempt: { ...DOWNLOADING, step: "failed", failure: "download_failed" },
      try_again: true,
    });
    renderOffer();
    await settle();
    expect(screen.getByTestId("fallback-caption")).toHaveTextContent("Update to 1.2.0 failed — nothing was changed.");
    expect(screen.getByTestId("fallback-note")).toHaveTextContent("The download failed.");
    fireEvent.click(button("Try again"));
    await settle();
    expect(installUpdate).toHaveBeenCalledExactlyOnceWith("1.2.0");
  });

  it("says no newer release is out where the check found none", async () => {
    vi.mocked(getUpdateNotice).mockResolvedValue({
      ...NOTICE,
      available: false,
      newer: false,
      latest_version: "1.1.0",
    });
    vi.mocked(getUpdateInstallState).mockResolvedValue(NOTHING_OFFERED);
    renderOffer();
    await settle();
    // The sentence names the last check, because that check may be a day
    // old; Check for updates beside it asks again.
    expect(screen.getByTestId("fallback-none-newer")).toHaveTextContent(
      "No newer release of Tender was out at the last check.",
    );
    expect(button("Check for updates")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Install update" })).not.toBeInTheDocument();
  });

  it("claims nothing about a newer release before a check has established one", async () => {
    vi.mocked(getUpdateNotice).mockResolvedValue({ ...NOTICE, available: false, newer: false, latest_version: null });
    vi.mocked(getUpdateInstallState).mockResolvedValue(NOTHING_OFFERED);
    renderOffer();
    await settle();
    expect(screen.queryByTestId("fallback-none-newer")).not.toBeInTheDocument();
  });

  it("says when Check for updates could not reach GitHub", async () => {
    vi.mocked(getUpdateInstallState).mockResolvedValue(NOTHING_OFFERED);
    vi.mocked(checkForUpdateNow).mockResolvedValue({
      ...NOTICE,
      available: false,
      newer: false,
      latest_version: null,
      reached: false,
    });
    renderOffer();
    await settle();
    fireEvent.click(button("Check for updates"));
    await settle();
    expect(screen.getByTestId("fallback-check-result")).toHaveTextContent(
      "GitHub gave no usable answer. Try again later.",
    );
  });

  it("offers no install to a program not installed with the installer", async () => {
    vi.mocked(getUpdateNotice).mockResolvedValue({ ...NOTICE, installed_program: false });
    vi.mocked(getUpdateInstallState).mockResolvedValue(NOTHING_OFFERED);
    renderOffer();
    await settle();
    expect(screen.getByTestId("fallback-not-installed")).toHaveTextContent(
      "Development build — install updates with the installer.",
    );
    expect(screen.queryByRole("button", { name: "Install update" })).not.toBeInTheDocument();
  });

  it("states an update the installer did not let through, beside Try again", async () => {
    vi.mocked(getUpdateOutcome).mockResolvedValue({ ...NO_OUTCOME, failure: ROLLED_BACK, failure_toast_owed: true });
    vi.mocked(getUpdateInstallState).mockResolvedValue({ ...OFFERED, try_again: true });
    renderOffer();
    await settle();
    expect(screen.getByTestId("fallback-caption")).toHaveTextContent(
      "Update to 1.2.0 failed — Tender went back to 1.1.0.",
    );
    expect(screen.getByTestId("fallback-note")).toHaveTextContent(
      updateFailureReason({
        attemptedVersion: "1.2.0",
        restoredVersion: "1.1.0",
        rolledBackAt: ROLLED_BACK.rolled_back_at,
        kind: "rollback",
      }),
    );
    expect(button("Try again")).toBeInTheDocument();
    // Read, never kept where the card on Main or its toast would find it.
    expect(toaster.toast).not.toHaveBeenCalled();
    expect(getUpdateOutcomeState()).toEqual(freshOutcomeState);
  });

  it("stays usable when the installer's record cannot be read, and logs why", async () => {
    vi.mocked(getUpdateOutcome).mockRejectedValue(new Error("connection reset"));
    renderOffer();
    await settle();
    expect(logError).toHaveBeenCalledWith("Failed to read what the last update did: Error: connection reset");
    expect(screen.getByTestId("fallback-available")).toHaveTextContent("Available: 1.2.0");
    expect(button("Install update")).toBeEnabled();
    expect(button("Restart Steam")).toBeInTheDocument();
  });

  describe("on a stranded panel", () => {
    it.each([
      ["with the update offered", true],
      ["with Restart Steam alone", false],
    ])("offers only Restart Steam, %s", async (_, offerUpdate) => {
      render(<FallbackActions offerUpdate={offerUpdate} buttons={PLAIN_BUTTONS} />);
      await settle();
      act(() => setStrandedAnswer("reloads"));
      expect(screen.queryAllByRole("button").map((element) => element.textContent)).toEqual(["Restart Steam"]);
      expect(screen.getByTestId("fallback-stranded")).toHaveTextContent(
        "Tender was restarted — it reloads Steam's interface once no game is running.",
      );
    });

    it("says that the install's result shows after the reload, from what the poll read", async () => {
      vi.mocked(getUpdateInstallState).mockResolvedValue({
        ...OFFERED,
        attempt: { ...DOWNLOADING, step: "installer_started" },
      });
      renderOffer();
      await settle();
      act(() => setStrandedAnswer("reloads"));
      expect(screen.getByTestId("fallback-stranded")).toHaveTextContent(
        "Tender was restarted — it reloads Steam's interface once no game is running. " +
          "The update's result shows after that.",
      );
    });

    it("says that the install's result shows after the reload", async () => {
      renderOffer();
      await settle();
      act(() => setUpdateInstallAttempt({ ...DOWNLOADING, step: "installer_started" }));
      act(() => setStrandedAnswer("restart_steam"));
      expect(screen.getByTestId("fallback-stranded")).toHaveTextContent(
        "Tender was restarted — restart Steam to use it again. The update's result shows after that.",
      );
    });
  });

  it("restarts Steam on Restart Steam", async () => {
    render(<FallbackActions offerUpdate={false} buttons={PLAIN_BUTTONS} />);
    fireEvent.click(button("Restart Steam"));
    await settle();
    expect(SteamClient.User.StartRestart).toHaveBeenCalledExactlyOnceWith(false);
  });

  it("refuses Restart Steam while a game runs, and says why on the page", async () => {
    stubSteam([{ appid: 42, display_name: "Some Game", local_per_client_data: { display_status: 4 } }]);
    render(<FallbackActions offerUpdate={false} buttons={PLAIN_BUTTONS} />);
    fireEvent.click(button("Restart Steam"));
    await settle();
    expect(SteamClient.User.StartRestart).not.toHaveBeenCalled();
    expect(screen.getByTestId("fallback-restart-refusal")).toHaveTextContent(
      "Close your running game before restarting Steam.",
    );
  });

  it("leaves the store the tab icon's update dot reads as a fresh load has it", async () => {
    // The fallback path shows no dot, and the dot is drawn off this store.
    renderOffer();
    await settle();
    fireEvent.click(button("Check for updates"));
    await settle();
    expect(screen.getByTestId("fallback-available")).toBeInTheDocument();
    expect(getUpdateNoticeState()).toEqual(freshNoticeState);
  });

  it("keeps what Check for updates found when the due check answers after it", async () => {
    let answerDueCheck!: (notice: UpdateNotice) => void;
    vi.mocked(getUpdateNotice).mockReturnValue(new Promise((resolve) => (answerDueCheck = resolve)));
    vi.mocked(getUpdateInstallState).mockResolvedValue(NOTHING_OFFERED);
    vi.mocked(checkForUpdateNow).mockResolvedValue({
      ...NOTICE,
      available: false,
      newer: false,
      latest_version: "1.1.0",
      reached: true,
    });
    renderOffer();
    fireEvent.click(button("Check for updates"));
    await settle();
    expect(screen.getByTestId("fallback-none-newer")).toBeInTheDocument();
    // An older answer, from before the check: had it been taken it would
    // claim a release the check just found none of.
    await act(async () => answerDueCheck({ ...NOTICE, installed_program: false }));
    expect(screen.getByTestId("fallback-none-newer")).toBeInTheDocument();
    expect(screen.queryByTestId("fallback-not-installed")).not.toBeInTheDocument();
  });
});

describe("the fallback page's buttons", () => {
  it("are Steam's where both of their searches answered", () => {
    expect(fallbackButtons(report(["Tabs"]))).toBe(STEAM_BUTTONS);
  });

  it.each(["DialogButton", "Focusable"])("are plain where %s missed", (name) => {
    expect(fallbackButtons(report(["Tabs", name]))).toBe(PLAIN_BUTTONS);
  });

  it("put plain buttons under the line asking for the touch screen", async () => {
    renderOffer(PLAIN_BUTTONS);
    await settle();
    expect(screen.getByText(TOUCH_SCREEN_LINE)).toBeInTheDocument();
    expect(screen.queryByTestId("focusable")).not.toBeInTheDocument();
  });

  it("put Steam's buttons in a Steam column, with no touch-screen line", async () => {
    renderOffer(STEAM_BUTTONS);
    await settle();
    expect(screen.queryByText(TOUCH_SCREEN_LINE)).not.toBeInTheDocument();
    expect(screen.getByTestId("focusable")).toContainElement(button("Restart Steam"));
  });
});
