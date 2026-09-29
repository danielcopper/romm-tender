import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, fireEvent } from "@testing-library/react";
import { UpdatesSection, NOT_INSTALLED_PROGRAM } from "./UpdatesSection";
import type { UpdateInstall } from "./useUpdateInstall";
import type { UpdateInstallAttempt } from "../../api/backend";
import { RESTARTING_LINE } from "../../utils/updateInstallView";
import { UpdateFailureNotice } from "../UpdateFailureNotice";
import type { UpdateNoticeState } from "../../utils/updateNoticeStore";
import {
  UPDATE_FAILURE_REASON,
  resetUpdateOutcomeStoreForTests,
  setUpdateOutcomeState,
  type UpdateOutcomeState,
} from "../../utils/updateOutcomeStore";

// The install's reads and press are `useUpdateInstall.test.ts`'s; here the
// section is handed what the hook answers, so what it renders is all there is.
const installView = vi.hoisted(() => ({ current: undefined as unknown as UpdateInstall }));
vi.mock("./useUpdateInstall", () => ({ useUpdateInstall: () => installView.current }));

const NOTHING_OFFERED: UpdateInstall = {
  offered: false,
  version: null,
  waitReasons: [],
  pausedDownloads: 0,
  attempt: null,
  tryAgain: false,
  pressing: false,
  refusal: "",
  underWay: false,
  restarting: false,
  overdue: false,
  readFailed: false,
  install: () => undefined,
};

const OFFERED: UpdateInstall = { ...NOTHING_OFFERED, offered: true, version: "1.0.0" };

const DOWNLOADING: UpdateInstallAttempt = {
  version: "1.0.0",
  step: "downloading",
  bytes_done: 25,
  bytes_total: 100,
  failure: null,
};

const STATE: UpdateNoticeState = {
  available: true,
  newer: true,
  latestVersion: "0.34.0",
  currentVersion: "0.33.0",
  enabled: true,
  installedProgram: true,
};

const NO_OUTCOME: UpdateOutcomeState = { announcement: null, failure: null, failureDismissed: false };

const ROLLED_BACK: UpdateOutcomeState = {
  announcement: null,
  failure: { attemptedVersion: "0.34.0", restoredVersion: "0.33.0", rolledBackAt: "2026-09-25T10:15:00Z" },
  failureDismissed: false,
};

const renderSection = (
  over: Partial<UpdateNoticeState> = {},
  props: { checking?: boolean; result?: string; outcome?: UpdateOutcomeState } = {},
) => {
  const onEnabledChange = vi.fn();
  const onCheckNow = vi.fn();
  const utils = render(
    <UpdatesSection
      update={{ ...STATE, ...over }}
      outcome={props.outcome ?? NO_OUTCOME}
      checking={props.checking ?? false}
      result={props.result ?? ""}
      onEnabledChange={onEnabledChange}
      onCheckNow={onCheckNow}
    />,
  );
  return { ...utils, onEnabledChange, onCheckNow };
};

describe("UpdatesSection", () => {
  beforeEach(() => {
    resetUpdateOutcomeStoreForTests();
    installView.current = NOTHING_OFFERED;
  });

  it("states the installed and the available version", () => {
    const { getByTestId } = renderSection();
    expect(getByTestId("updates-installed").textContent).toBe("0.33.0");
    expect(getByTestId("updates-available").textContent).toBe("0.34.0");
  });

  it("still names a newer release the card was dismissed for", () => {
    const { getByTestId } = renderSection({ available: false });
    expect(getByTestId("updates-available").textContent).toBe("0.34.0");
  });

  it("says when nothing newer is out", () => {
    const { getByTestId } = renderSection({ available: false, newer: false, latestVersion: "0.33.0" });
    expect(getByTestId("updates-available").textContent).toBe("None newer");
  });

  it("says when no check has established anything yet", () => {
    const { getByTestId } = renderSection({ available: false, newer: false, latestVersion: null });
    expect(getByTestId("updates-available").textContent).toBe("Not known yet");
  });

  it("says the daily check is off rather than claiming anything about releases", () => {
    const { getByTestId } = renderSection({ enabled: false, available: false, newer: false, latestVersion: null });
    expect(getByTestId("updates-available").textContent).toContain("the daily check is off");
  });

  it("a run from a checkout says it is a development build and points at the installer", () => {
    const { getByTestId } = renderSection({ installedProgram: false });
    expect(NOT_INSTALLED_PROGRAM).toBe("Development build — install updates with the installer.");
    expect(getByTestId("updates-not-installed").textContent).toBe(NOT_INSTALLED_PROGRAM);
  });

  it("the installed program carries no such line", () => {
    const { queryByTestId } = renderSection();
    expect(queryByTestId("updates-not-installed")).toBeNull();
  });

  it("the switch shows its state and reports a flip", () => {
    const { getByTestId, onEnabledChange } = renderSection({ enabled: true });
    const toggle = getByTestId("toggle-input") as HTMLInputElement;
    expect(toggle.checked).toBe(true);

    fireEvent.click(toggle);

    expect(onEnabledChange).toHaveBeenCalledWith(false);
  });

  it("Check now presses through, and is dead while a check is in flight", () => {
    const idle = renderSection();
    fireEvent.click(idle.getByText("Check now"));
    expect(idle.onCheckNow).toHaveBeenCalledTimes(1);
    idle.unmount();

    const busy = renderSection({}, { checking: true });
    expect((busy.getByText("Checking…") as HTMLButtonElement).disabled).toBe(true);
  });

  it("shows the last check's result, and nothing where there is none", () => {
    expect(renderSection().queryByTestId("updates-result")).toBeNull();
    const { getByTestId } = renderSection({}, { result: "You have the newest release." });
    expect(getByTestId("updates-result").textContent).toBe("You have the newest release.");
  });

  it("states a rolled-back update and where its reason is", () => {
    const { getByTestId, getByText } = renderSection({}, { outcome: ROLLED_BACK });
    expect(getByTestId("updates-last-update").textContent).toBe("Update to 0.34.0 failed — you are still on 0.33.0.");
    expect(getByText(UPDATE_FAILURE_REASON)).toBeTruthy();
  });

  it("words a rolled-back update in the warning colour its card on Main uses", () => {
    const row = renderSection({}, { outcome: ROLLED_BACK }).getByTestId("updates-last-update");
    setUpdateOutcomeState(ROLLED_BACK);
    const card = render(<UpdateFailureNotice onOpenUpdates={vi.fn()} />).getByTestId("update-failure-notice")
      .firstElementChild as HTMLElement;

    expect(row.style.color).toBe("#d4a72c");
    expect(card.style.color).toBe(row.style.color);
  });

  it("states it still once its notice on Main was dismissed", () => {
    const { getByTestId } = renderSection({}, { outcome: { ...ROLLED_BACK, failureDismissed: true } });
    expect(getByTestId("updates-last-update").textContent).toBe("Update to 0.34.0 failed — you are still on 0.33.0.");
  });

  it("says nothing about a last update where no record stands", () => {
    const { queryByTestId } = renderSection();
    expect(queryByTestId("updates-last-update")).toBeNull();
  });

  it("offers no install button while nothing is offered", () => {
    const { container } = renderSection();
    const buttons = [...container.querySelectorAll("button")].map((b) => b.textContent);
    expect(buttons).toEqual(["Check now"]);
  });

  describe("the install", () => {
    const withInstall = (over: Partial<UpdateInstall>) => {
      installView.current = { ...OFFERED, ...over };
      return renderSection();
    };
    const stepStatus = (utils: ReturnType<typeof renderSection>, id: string) =>
      utils.getByTestId(`updates-step-${id}`).dataset.status;

    it("offers the stored release by its version", () => {
      const install = vi.fn();
      const { getByText } = withInstall({ install });
      const button = getByText("Install update 1.0.0") as HTMLButtonElement;
      expect(button.disabled).toBe(false);

      fireEvent.click(button);

      expect(install).toHaveBeenCalledTimes(1);
    });

    it("offers a version that already failed as Try again", () => {
      const { getByText, queryByText } = withInstall({ tryAgain: true });
      expect(getByText("Try again")).toBeTruthy();
      expect(queryByText("Install update 1.0.0")).toBeNull();
    });

    it("holds the button back and names every reason it waits for", () => {
      const { getByText, getByTestId, getAllByTestId } = withInstall({
        waitReasons: [{ reason: "app_running", apps: ["Celeste"] }, { reason: "library_sync" }],
      });

      expect((getByText("Install update 1.0.0") as HTMLButtonElement).disabled).toBe(true);
      expect(getByText("Waiting for:")).toBeTruthy();
      expect(getByTestId("updates-waiting")).toBeTruthy();
      expect(getAllByTestId("updates-wait-reason").map((row) => row.textContent)).toEqual([
        "A game to close (Celeste)",
        "Library sync",
      ]);
    });

    it("names no wait while nothing holds the button back", () => {
      expect(withInstall({}).queryByTestId("updates-waiting")).toBeNull();
    });

    it("holds the button back while a press waits for its answer", () => {
      const { getByText } = withInstall({ pressing: true });
      expect((getByText("Install update 1.0.0") as HTMLButtonElement).disabled).toBe(true);
    });

    it("says which paused downloads the restart cancels, and still lets the button run", () => {
      const { getByTestId, getByText } = withInstall({ pausedDownloads: 2 });
      expect(getByTestId("updates-paused-hint").textContent).toBe("2 paused downloads will be cancelled.");
      expect((getByText("Install update 1.0.0") as HTMLButtonElement).disabled).toBe(false);
    });

    it("says nothing about paused downloads where there are none", () => {
      expect(withInstall({}).queryByTestId("updates-paused-hint")).toBeNull();
    });

    it("shows what refused a press", () => {
      const { getByTestId } = withInstall({ refusal: "The release offered is now 1.0.1" });
      expect(getByTestId("updates-install-refusal").textContent).toBe("The release offered is now 1.0.1");
    });

    it("shows no step before a press", () => {
      expect(withInstall({}).queryByTestId("updates-step-download")).toBeNull();
    });

    it("keeps the button, disabled and saying Installing…, while the download runs, its bar at the byte percentage", () => {
      const utils = withInstall({ attempt: DOWNLOADING, underWay: true });

      expect(utils.queryByText("Install update 1.0.0")).toBeNull();
      expect((utils.getByText("Installing…") as HTMLButtonElement).disabled).toBe(true);
      expect(stepStatus(utils, "download")).toBe("current");
      expect(utils.getByTestId("updates-step-download").textContent).toBe("25%");
      expect(stepStatus(utils, "verify")).toBe("pending");
      expect(stepStatus(utils, "installer")).toBe("pending");
      expect(utils.getByTestId("progress-progress").textContent).toBe("25");
      expect(utils.getByTestId("progress-indeterminate").textContent).toBe("false");
    });

    it("runs the bar indeterminate and counts bytes where the download announced no size", () => {
      const utils = withInstall({ attempt: { ...DOWNLOADING, bytes_done: 2048, bytes_total: null } });
      expect(utils.getByTestId("progress-indeterminate").textContent).toBe("true");
      expect(utils.getByTestId("updates-step-download").textContent).toBe("2.0 KB");
    });

    it("drops the bar once the download is done", () => {
      const utils = withInstall({ attempt: { ...DOWNLOADING, step: "verifying" } });
      expect(stepStatus(utils, "download")).toBe("done");
      expect(stepStatus(utils, "verify")).toBe("current");
      expect(utils.queryByTestId("progress")).toBeNull();
    });

    it("says Tender is restarting once the installer started, and asks for no check meanwhile", () => {
      const utils = withInstall({
        attempt: { ...DOWNLOADING, step: "installer_started" },
        underWay: true,
        restarting: true,
      });

      expect(stepStatus(utils, "installer")).toBe("done");
      expect(utils.getByTestId("updates-restarting").textContent).toBe(RESTARTING_LINE);
      expect(RESTARTING_LINE).toBe("Tender is restarting — Steam's interface will reload in a moment.");
      expect((utils.getByText("Check now") as HTMLButtonElement).disabled).toBe(true);
      expect(utils.queryByTestId("updates-install-failure")).toBeNull();
    });

    it("marks the step a failed attempt stopped at, says why, and offers Try again", () => {
      const utils = withInstall({
        attempt: { ...DOWNLOADING, step: "failed", failure: "checksum_mismatch" },
        tryAgain: true,
      });

      expect(stepStatus(utils, "download")).toBe("done");
      expect(stepStatus(utils, "verify")).toBe("failed");
      expect(utils.getByTestId("updates-step-verify").textContent).toBe("Failed");
      expect(utils.getByTestId("updates-install-failure").textContent).toBe(
        "The download did not match its checksum — nothing was changed.",
      );
      expect(utils.getByText("Try again")).toBeTruthy();
    });

    it("keeps the button's row when what it waited for clears, so focus on it stays put", () => {
      installView.current = { ...OFFERED, waitReasons: [{ reason: "library_sync" }] };
      const utils = renderSection();
      const button = utils.getByText("Install update 1.0.0");

      installView.current = { ...OFFERED };
      utils.rerender(
        <UpdatesSection
          update={STATE}
          outcome={NO_OUTCOME}
          checking={false}
          result=""
          onEnabledChange={vi.fn()}
          onCheckNow={vi.fn()}
        />,
      );

      expect(utils.getByText("Install update 1.0.0")).toBe(button);
      expect(utils.queryByTestId("updates-waiting")).toBeNull();
    });

    it("says under the steps that starting a game cancels the update while it downloads and verifies", () => {
      for (const step of ["downloading", "verifying"] as const) {
        const utils = withInstall({ attempt: { ...DOWNLOADING, step }, underWay: true });
        expect(utils.getByTestId("updates-game-hint").textContent).toBe("Starting a game now cancels the update.");
        utils.unmount();
      }
    });

    it("changes the words of the line under the steps rather than its row, from download to restart", () => {
      installView.current = { ...OFFERED, attempt: DOWNLOADING, underWay: true };
      const utils = renderSection();
      const row = utils.getByTestId("updates-game-hint").closest('[data-testid="field"]');

      installView.current = {
        ...OFFERED,
        attempt: { ...DOWNLOADING, step: "installer_started" },
        underWay: true,
        restarting: true,
      };
      utils.rerender(
        <UpdatesSection
          update={STATE}
          outcome={NO_OUTCOME}
          checking={false}
          result=""
          onEnabledChange={vi.fn()}
          onCheckNow={vi.fn()}
        />,
      );

      expect(utils.getByTestId("updates-restarting").closest('[data-testid="field"]')).toBe(row);
    });

    it("says the installer is taking unusually long five minutes on, while the backend still answers", () => {
      const utils = withInstall({
        attempt: { ...DOWNLOADING, step: "installer_started" },
        underWay: true,
        restarting: true,
        overdue: true,
      });

      expect(utils.getByTestId("updates-restarting").textContent).toBe(
        "The installer is taking unusually long. Details: journalctl --user -u romm-tender-update",
      );
    });

    it("says Tender has not come back five minutes on, once the backend no longer answers", () => {
      const utils = withInstall({
        attempt: { ...DOWNLOADING, step: "installer_started" },
        underWay: true,
        restarting: true,
        overdue: true,
        readFailed: true,
      });

      expect(utils.getByTestId("updates-restarting").textContent).toBe(
        "Tender has not come back. Details: journalctl --user -u romm-tender-update — start it again with: systemctl --user start romm-tender",
      );
      expect(utils.queryByTestId("updates-install-unread")).toBeNull();
    });

    it("shows no steps of a failed attempt for a version no longer offered", () => {
      const utils = withInstall({
        version: "1.1.0",
        attempt: { ...DOWNLOADING, step: "failed", failure: "download_failed" },
      });

      expect(utils.getByText("Install update 1.1.0")).toBeTruthy();
      expect(utils.queryByTestId("updates-step-download")).toBeNull();
      expect(utils.queryByTestId("updates-install-failure")).toBeNull();
    });

    it("shows an attempt under way even where nothing is offered any more", () => {
      const utils = withInstall({ offered: false, version: null, attempt: DOWNLOADING, underWay: true });

      expect(utils.getByText("Installing…")).toBeTruthy();
      expect(utils.getByTestId("updates-step-download")).toBeTruthy();
    });

    it("asks for no check while an attempt downloads", () => {
      const utils = withInstall({ attempt: DOWNLOADING, underWay: true });
      expect((utils.getByText("Check now") as HTMLButtonElement).disabled).toBe(true);
    });

    it("says a read of the state did not answer, on the button where there is one", () => {
      const utils = withInstall({ readFailed: true });

      expect(utils.getByTestId("updates-install-unread").textContent).toBe("Could not read the update state.");
      expect(utils.getByTestId("updates-install-unread").closest('[data-testid="button-desc"]')).not.toBeNull();
    });

    it("says it in a row of its own where no read ever answered", () => {
      installView.current = { ...NOTHING_OFFERED, readFailed: true };
      const utils = renderSection();

      const line = utils.getByTestId("updates-install-unread");
      expect(line.textContent).toBe("Could not read the update state.");
      expect(line.closest('[data-testid="field"]')?.getAttribute("tabindex")).toBe("0");
    });

    it("makes every row it adds a focus stop, and hangs what the button waits for on the button itself", () => {
      const utils = withInstall({
        attempt: { ...DOWNLOADING, step: "failed", failure: "installer_stopped" },
        tryAgain: true,
        pausedDownloads: 1,
        waitReasons: [{ reason: "save_sync" }],
        refusal: "An update is already being installed.",
      });
      const rows = [
        "updates-step-download",
        "updates-step-verify",
        "updates-step-installer",
        "updates-install-failure",
      ].map((id) => utils.getByTestId(id).closest('[data-testid="field"]'));
      const onTheButton = ["updates-paused-hint", "updates-waiting", "updates-install-refusal"].map((id) =>
        utils.getByTestId(id).closest('[data-testid="button-desc"]'),
      );

      expect(rows.every((row) => row?.getAttribute("tabindex") === "0")).toBe(true);
      expect(onTheButton.every((desc) => desc !== null)).toBe(true);
    });
  });
});
