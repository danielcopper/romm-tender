import { describe, it, expect, beforeEach, vi } from "vitest";
import { act, render, fireEvent } from "@testing-library/react";
import { UpdatesSection, NOT_INSTALLED_PROGRAM } from "./UpdatesSection";
import type { UpdateInstall } from "./useUpdateInstall";
import type { UpdateInstallAttempt } from "../../api/backend";
import { setUpdateInstallAttempt } from "../../utils/updateInstallStore";
import { NOT_BACK_LINE, TAKING_LONG_LINE } from "../../utils/updateInstallView";
import { GREEN } from "../layout/pane";
import { UpdateFailureNotice } from "../UpdateFailureNotice";
import type { UpdateNoticeState } from "../../utils/updateNoticeStore";
import {
  UPDATE_CHECK_FAILURE_NOTE,
  UPDATE_FAILURE_REASON,
  UPDATE_UNKNOWN_FAILURE_REASON,
  resetUpdateOutcomeStoreForTests,
  setUpdateOutcomeState,
  type UpdateOutcomeState,
} from "../../utils/updateOutcomeStore";

// The install's reads and press are `useUpdateInstall.test.ts`'s; here the
// section is handed what the hook answers, so what it renders is all there is.
const installView = vi.hoisted(() => ({ current: undefined as unknown as UpdateInstall }));
vi.mock("./useUpdateInstall", () => ({ useUpdateInstall: () => installView.current }));

const NOTHING_OFFERED: UpdateInstall = {
  answered: true,
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
  failure: {
    attemptedVersion: "0.34.0",
    restoredVersion: "0.33.0",
    rolledBackAt: "2026-09-25T10:15:00Z",
    kind: "rollback",
  },
  failureDismissed: false,
};

/**
 * Call a button's own press handler past its `disabled`, as the device does:
 * a disabled control there still reports the press, while the DOM drops the
 * click before React sees it.
 */
function pressDespiteDisabled(element: HTMLElement): void {
  const key = Object.keys(element).find((name) => name.startsWith("__reactProps$"));
  const props = (key === undefined ? undefined : (element as unknown as Record<string, unknown>)[key]) as
    { onClick?: () => void } | undefined;
  if (props?.onClick === undefined) throw new Error("the element carries no press handler");
  props.onClick();
}

const statuses = (utils: { getByTestId: (id: string) => HTMLElement }) =>
  ["download", "verify", "check", "install"].map((id) => utils.getByTestId(`updates-step-${id}`).dataset.status);

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
    setUpdateInstallAttempt(null);
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

  it("shows a newer release in green, and what is not one in no colour of its own", () => {
    const newer = renderSection();
    expect(newer.getByTestId("updates-available").style.color).toBe(GREEN);
    newer.unmount();

    const none = renderSection({ available: false, newer: false, latestVersion: "0.33.0" });
    expect(none.getByTestId("updates-available").style.color).toBe("");
    none.unmount();

    const unknown = renderSection({ available: false, newer: false, latestVersion: null });
    expect(unknown.getByTestId("updates-available").style.color).toBe("");
  });

  it("names what the last check found whatever the switch says", () => {
    const found = renderSection({ enabled: false });
    expect(found.getByTestId("updates-available").textContent).toBe("0.34.0");
    found.unmount();

    const unknown = renderSection({ enabled: false, available: false, newer: false, latestVersion: null });
    expect(unknown.getByTestId("updates-available").textContent).toBe("Not known yet");
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
    const { getByTestId } = renderSection({}, { result: "The check failed." });
    expect(getByTestId("updates-result").textContent).toBe("The check failed.");
  });

  describe("the installer's record of an update that did not go through", () => {
    const withRecord = (kind: "rollback" | "check" | "unknown") =>
      renderSection({}, { outcome: { ...ROLLED_BACK, failure: { ...ROLLED_BACK.failure!, kind } } });

    it("states a rolled-back update as gone back, failed at Install, with where its reason is", () => {
      const utils = withRecord("rollback");

      expect(utils.getByTestId("updates-caption").textContent).toBe(
        "Update to 0.34.0 failed — Tender went back to 0.33.0.",
      );
      expect(statuses(utils)).toEqual(["done", "done", "done", "failed"]);
      expect(utils.getByTestId("updates-note").textContent).toBe(UPDATE_FAILURE_REASON);
    });

    it("states an update the pre-install check refused as nothing changed, failed at the check, saying so once", () => {
      const utils = withRecord("check");

      expect(utils.getByTestId("updates-caption").textContent).toBe("Update to 0.34.0 failed — nothing was changed.");
      expect(statuses(utils)).toEqual(["done", "done", "failed", "pending"]);
      expect(utils.getByTestId("updates-note").textContent).toBe(UPDATE_CHECK_FAILURE_NOTE);
      expect(UPDATE_CHECK_FAILURE_NOTE).toBe(
        "The new version did not start. The installer's output says why: journalctl --user -u romm-tender-update, or the terminal it was run in.",
      );
    });

    it("states a record of a kind this version does not know with no cause and no step", () => {
      const utils = withRecord("unknown");

      expect(utils.getByTestId("updates-caption").textContent).toBe(
        "Update to 0.34.0 failed — you are still on 0.33.0.",
      );
      expect(utils.queryByTestId("updates-step-install")).toBeNull();
      expect(utils.getByTestId("updates-note").textContent).toBe(UPDATE_UNKNOWN_FAILURE_REASON);
    });

    it("words it in the warning colour its card on Main uses", () => {
      const title = withRecord("rollback").getByTestId("updates-caption");
      setUpdateOutcomeState(ROLLED_BACK);
      const card = render(<UpdateFailureNotice onOpenUpdates={vi.fn()} />).getByTestId("update-failure-notice")
        .firstElementChild as HTMLElement;

      expect(title.style.color).toBe("#d4a72c");
      expect(card.style.color).toBe(title.style.color);
    });

    it("states it still once its notice on Main was dismissed", () => {
      const { getByTestId } = renderSection({}, { outcome: { ...ROLLED_BACK, failureDismissed: true } });
      expect(getByTestId("updates-caption").textContent).toBe("Update to 0.34.0 failed — Tender went back to 0.33.0.");
    });

    it("says nothing about a last update where no record stands", () => {
      expect(renderSection().queryByTestId("updates-caption")).toBeNull();
    });

    it("gives way to this backend's failed attempt for the same update, so the failure is said once", () => {
      installView.current = {
        ...OFFERED,
        version: "0.34.0",
        attempt: { ...DOWNLOADING, version: "0.34.0", step: "failed", failure: "new_version_does_not_start" },
        tryAgain: true,
      };
      const utils = withRecord("check");

      expect(utils.getAllByTestId("updates-caption")).toHaveLength(1);
      expect(utils.getByTestId("updates-note").textContent).toBe(
        "The new version does not start. The installer's output says why: journalctl --user -u romm-tender-update",
      );
    });
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
    const rerender = (utils: ReturnType<typeof renderSection>, over: Partial<UpdateInstall>) => {
      installView.current = { ...OFFERED, ...over };
      utils.rerender(
        <UpdatesSection
          update={STATE}
          outcome={NO_OUTCOME}
          checking={false}
          result=""
          onEnabledChange={utils.onEnabledChange}
          onCheckNow={utils.onCheckNow}
        />,
      );
    };
    const text = (utils: ReturnType<typeof renderSection>, id: string) => utils.getByTestId(id).textContent;
    const INSTALLER_STARTED = {
      attempt: { ...DOWNLOADING, step: "installer_started" as const },
      underWay: true,
      restarting: true,
    };
    const failedWith = (failure: NonNullable<UpdateInstallAttempt["failure"]>): Partial<UpdateInstall> => ({
      attempt: { ...DOWNLOADING, step: "failed", failure },
      tryAgain: true,
    });

    it("offers the stored release, leaving its version to the Available row", () => {
      const install = vi.fn();
      const { getByText, queryByText } = withInstall({ install });
      const button = getByText("Install update") as HTMLButtonElement;
      expect(button.disabled).toBe(false);
      expect(queryByText("Install update 1.0.0")).toBeNull();

      fireEvent.click(button);

      expect(install).toHaveBeenCalledTimes(1);
    });

    it("offers a version that already failed as Try again", () => {
      const { getByText, queryByText } = withInstall({ tryAgain: true });
      expect(getByText("Try again")).toBeTruthy();
      expect(queryByText("Install update")).toBeNull();
    });

    it("holds the button back and names every reason it waits for, a line each under the header", () => {
      const { getByText, getByTestId, getAllByTestId } = withInstall({
        waitReasons: [{ reason: "app_running", apps: ["Celeste"] }, { reason: "library_sync" }],
      });

      expect((getByText("Install update") as HTMLButtonElement).disabled).toBe(true);
      expect(getByTestId("updates-waiting").firstElementChild?.textContent).toBe("Waiting for:");
      expect(getAllByTestId("updates-wait-reason").map((row) => row.textContent)).toEqual([
        "A game to close (Celeste)",
        "Library sync",
      ]);
    });

    it("names a single reason on the header's own line", () => {
      const waiting = withInstall({ waitReasons: [{ reason: "library_sync" }] }).getByTestId("updates-waiting");

      expect(waiting.textContent).toBe("Waiting for: Library sync");
      expect(waiting.querySelector("div")).toBeNull();
    });

    it("names no wait while nothing holds the button back", () => {
      expect(withInstall({}).queryByTestId("updates-waiting")).toBeNull();
    });

    it("holds the button back while a press waits for its answer", () => {
      const { getByText } = withInstall({ pressing: true });
      expect((getByText("Install update") as HTMLButtonElement).disabled).toBe(true);
    });

    it("says which paused downloads the restart cancels, and still lets the button run", () => {
      const { getByTestId, getByText } = withInstall({ pausedDownloads: 2 });
      expect(getByTestId("updates-paused-hint").textContent).toBe("2 paused downloads will be cancelled.");
      expect((getByText("Install update") as HTMLButtonElement).disabled).toBe(false);
    });

    it("says nothing about paused downloads where there are none", () => {
      expect(withInstall({}).queryByTestId("updates-paused-hint")).toBeNull();
    });

    it("shows what refused a press", () => {
      const { getByTestId } = withInstall({ refusal: "The release offered is now 1.0.1" });
      expect(getByTestId("updates-install-refusal").textContent).toBe("The release offered is now 1.0.1");
    });

    it("shows no block before a press", () => {
      const utils = withInstall({});
      expect(utils.queryByTestId("updates-caption")).toBeNull();
    });

    it("keeps the button, disabled and saying Installing…, while the download runs, its bar at the byte percentage", () => {
      const utils = withInstall({ attempt: DOWNLOADING, underWay: true });

      expect(utils.queryByText("Install update")).toBeNull();
      expect((utils.getByText("Installing…") as HTMLButtonElement).disabled).toBe(true);
      expect(text(utils, "updates-caption")).toBe("Downloading 1.0.0");
      expect(text(utils, "updates-elapsed")).toBe("25% · 0:00");
      expect(statuses(utils)).toEqual(["current", "pending", "pending", "pending"]);
      expect(text(utils, "progress-progress")).toBe("25");
      expect(text(utils, "progress-indeterminate")).toBe("false");
    });

    it("runs the bar indeterminate, and states no percent, where the download announced no size", () => {
      const utils = withInstall({ attempt: { ...DOWNLOADING, bytes_done: 2048, bytes_total: null }, underWay: true });
      expect(text(utils, "progress-indeterminate")).toBe("true");
      expect(text(utils, "updates-elapsed")).toBe("0:00");
    });

    it("runs the bar indeterminate once the download is done", () => {
      const utils = withInstall({ attempt: { ...DOWNLOADING, step: "verifying" }, underWay: true });
      expect(text(utils, "updates-caption")).toBe("Verifying 1.0.0");
      expect(statuses(utils)).toEqual(["done", "current", "pending", "pending"]);
      expect(text(utils, "progress-indeterminate")).toBe("true");
      expect(text(utils, "updates-elapsed")).toBe("0:00");
    });

    it("says under the steps that starting a game cancels the update while it downloads and verifies", () => {
      for (const step of ["downloading", "verifying"] as const) {
        const utils = withInstall({ attempt: { ...DOWNLOADING, step }, underWay: true });
        expect(text(utils, "updates-note")).toBe("Starting a game now cancels the update.");
        utils.unmount();
      }
    });

    it("says the new version is being checked once the installer started and reads still answer, and asks for no check meanwhile", () => {
      const utils = withInstall(INSTALLER_STARTED);

      expect(text(utils, "updates-caption")).toBe("Checking the new version");
      expect(statuses(utils)).toEqual(["done", "done", "current", "pending"]);
      expect(text(utils, "progress-indeterminate")).toBe("true");
      expect(utils.queryByTestId("updates-note")).toBeNull();
      expect((utils.getByText("Check now") as HTMLButtonElement).disabled).toBe(true);
    });

    it("says Tender is restarting once reads fail, and how long that takes at most", () => {
      const utils = withInstall({ ...INSTALLER_STARTED, readFailed: true });

      expect(text(utils, "updates-caption")).toBe("Tender is restarting");
      expect(statuses(utils)).toEqual(["done", "done", "done", "current"]);
      expect(text(utils, "updates-note")).toBe(
        "Steam's interface reloads when it is done — usually within a minute, and up to about 5 minutes if Tender has to go back to 0.33.0.",
      );
      expect(utils.queryByTestId("updates-install-unread")).toBeNull();
    });

    it("counts the time since the attempt was first seen, as m:ss beside the percent", () => {
      vi.useFakeTimers();
      try {
        vi.setSystemTime(100_000);
        setUpdateInstallAttempt(DOWNLOADING);
        vi.setSystemTime(103_000);
        const utils = withInstall({ attempt: DOWNLOADING, underWay: true });
        expect(text(utils, "updates-elapsed")).toBe("25% · 0:03");

        act(() => {
          vi.advanceTimersByTime(62_000);
        });

        expect(text(utils, "updates-elapsed")).toBe("25% · 1:05");
      } finally {
        vi.useRealTimers();
      }
    });

    it("stops the clock once the attempt is no longer under way", () => {
      vi.useFakeTimers();
      try {
        const utils = withInstall({ attempt: DOWNLOADING, underWay: true });
        expect(vi.getTimerCount()).toBe(1);

        rerender(utils, failedWith("download_failed"));

        expect(vi.getTimerCount()).toBe(0);
      } finally {
        vi.useRealTimers();
      }
    });

    it("says the installer is taking unusually long seven minutes on, while the backend still answers", () => {
      const utils = withInstall({ ...INSTALLER_STARTED, overdue: true });
      expect(text(utils, "updates-note")).toBe(TAKING_LONG_LINE);
      expect(TAKING_LONG_LINE).toBe(
        "The installer is taking unusually long. Details: journalctl --user -u romm-tender-update",
      );
    });

    it("says Tender has not come back seven minutes on, once the backend no longer answers", () => {
      const utils = withInstall({ ...INSTALLER_STARTED, overdue: true, readFailed: true });

      expect(text(utils, "updates-note")).toBe(NOT_BACK_LINE);
      expect(NOT_BACK_LINE).toBe(
        "Tender has not come back. Details: journalctl --user -u romm-tender-update — start it again with: systemctl --user start romm-tender",
      );
      expect(utils.queryByTestId("updates-install-unread")).toBeNull();
    });

    it("states a failed attempt in one block: nothing changed, the step it stopped at, why, and Try again above it", () => {
      const utils = withInstall(failedWith("checksum_mismatch"));
      const caption = utils.getByTestId("updates-caption");

      expect(caption.textContent).toBe("Update to 1.0.0 failed — nothing was changed.");
      expect(caption.style.color).toBe("#d4a72c");
      expect(statuses(utils)).toEqual(["done", "failed", "pending", "pending"]);
      expect(["download", "verify", "check", "install"].map((id) => text(utils, `updates-step-${id}`))).toEqual([
        "✓ Download",
        "✕ Verify",
        "○ Check the new version",
        "○ Install",
      ]);
      expect(text(utils, "updates-note")).toBe("The download did not match its checksum.");
      expect(utils.getByText("Try again")).toBeTruthy();
      expect(utils.queryByTestId("progress")).toBeNull();
    });

    it("colours a step's mark — done green, current blue, to do muted, failed amber — and leaves its label in the text colour", () => {
      const colours = (utils: ReturnType<typeof renderSection>, id: string) => {
        const step = utils.getByTestId(`updates-step-${id}`);
        return [step.style.color, (step.firstElementChild as HTMLElement).style.color];
      };
      const underWay = withInstall(INSTALLER_STARTED);
      expect(["download", "check", "install"].map((id) => colours(underWay, id))).toEqual([
        ["", "#5ba32b"],
        ["", "#1a9fff"],
        ["", "#8f98a0"],
      ]);
      underWay.unmount();

      expect(colours(withInstall(failedWith("download_failed")), "download")).toEqual(["", "#d4a72c"]);
    });

    it("marks a download that failed at Download", () => {
      expect(statuses(withInstall(failedWith("download_failed")))).toEqual(["failed", "pending", "pending", "pending"]);
    });

    it("marks a new version that does not start at the check", () => {
      expect(statuses(withInstall(failedWith("new_version_does_not_start")))).toEqual([
        "done",
        "done",
        "failed",
        "pending",
      ]);
    });

    it.each<[NonNullable<UpdateInstallAttempt["failure"]>, string, string]>([
      [
        "game_started",
        "Update to 1.0.0 was cancelled — nothing was changed.",
        "A game was started. Try again once it has closed.",
      ],
      [
        "running_apps_unknown",
        "Update to 1.0.0 was cancelled — nothing was changed.",
        "Could not check whether a game is running.",
      ],
      ["installer_not_started", "Update to 1.0.0 failed — nothing was changed.", "The installer could not be started."],
    ])("states %s as %s, with no step line, and its reason", (failure, title, reason) => {
      const utils = withInstall(failedWith(failure));

      expect(text(utils, "updates-caption")).toBe(title);
      expect(utils.queryByTestId("updates-step-check")).toBeNull();
      expect(text(utils, "updates-note")).toBe(reason);
    });

    it("states an installer that stopped where this panel saw it start as nothing changed, marked at the check", () => {
      setUpdateInstallAttempt({ ...DOWNLOADING, step: "installer_started" });
      const utils = withInstall(failedWith("installer_stopped"));

      expect(statuses(utils)).toEqual(["done", "done", "failed", "pending"]);
      expect(text(utils, "updates-caption")).toBe("Update to 1.0.0 failed — nothing was changed.");
    });

    it("states an installer that stopped where this panel never saw it start as still on the installed version, marked at Install", () => {
      const utils = withInstall(failedWith("installer_stopped"));

      expect(statuses(utils)).toEqual(["done", "done", "done", "failed"]);
      expect(text(utils, "updates-caption")).toBe("Update to 1.0.0 failed — you are still on 0.33.0.");
    });

    it("keeps the button's row when what it waited for clears, so focus on it stays put", () => {
      const utils = withInstall({ waitReasons: [{ reason: "library_sync" }] });
      const button = utils.getByText("Install update");

      rerender(utils, {});

      expect(utils.getByText("Install update")).toBe(button);
      expect(utils.queryByTestId("updates-waiting")).toBeNull();
    });

    it("keeps one focus stop for the block while its content goes from download through restart to failure", () => {
      const utils = withInstall({ attempt: DOWNLOADING, underWay: true });
      const field = () => utils.getByTestId("updates-step-download").closest('[data-testid="field"]');
      const stop = field();

      rerender(utils, { ...INSTALLER_STARTED, readFailed: true });
      expect(field()).toBe(stop);

      rerender(utils, failedWith("new_version_does_not_start"));
      expect(field()).toBe(stop);
      expect(stop?.getAttribute("tabindex")).toBe("0");
    });

    it("shows no block of a failed attempt for a version no longer offered", () => {
      const utils = withInstall({ version: "1.1.0", ...failedWith("download_failed"), tryAgain: false });

      expect(utils.getByText("Install update")).toBeTruthy();
      expect(utils.queryByTestId("updates-caption")).toBeNull();
    });

    it("keeps the block and the button's row where an attempt fails before the first read answers", () => {
      const unread = { answered: false, offered: false, version: null };
      const utils = withInstall({ ...unread, attempt: DOWNLOADING, underWay: true });
      const stop = utils.getByTestId("updates-caption").closest('[data-testid="field"]');
      const button = utils.getByText("Installing…");

      rerender(utils, { ...unread, ...failedWith("download_failed"), tryAgain: true });

      expect(text(utils, "updates-caption")).toBe("Update to 1.0.0 failed — nothing was changed.");
      expect(utils.getByTestId("updates-caption").closest('[data-testid="field"]')).toBe(stop);
      expect(utils.getByText("Try again")).toBe(button);
      expect((button as HTMLButtonElement).disabled).toBe(true);
    });

    it("drops a failed attempt's block and its button once a read answers offering nothing", () => {
      const utils = withInstall({ offered: false, version: null, ...failedWith("download_failed"), tryAgain: false });

      expect(utils.queryByTestId("updates-caption")).toBeNull();
      expect(utils.queryByText("Install update")).toBeNull();
    });

    it("shows an attempt under way even where nothing is offered any more", () => {
      const utils = withInstall({ offered: false, version: null, attempt: DOWNLOADING, underWay: true });

      expect(utils.getByText("Installing…")).toBeTruthy();
      expect(text(utils, "updates-caption")).toBe("Downloading 1.0.0");
    });

    it("asks for no check while an attempt downloads", () => {
      const utils = withInstall({ attempt: DOWNLOADING, underWay: true });
      expect((utils.getByText("Check now") as HTMLButtonElement).disabled).toBe(true);
    });

    it("refuses a press of Check now in its handler while an attempt is under way", () => {
      const utils = withInstall({ attempt: DOWNLOADING, underWay: true });

      pressDespiteDisabled(utils.getByText("Check now"));

      expect(utils.onCheckNow).not.toHaveBeenCalled();
    });

    it("says a read of the state did not answer, on the button where there is one", () => {
      const utils = withInstall({ readFailed: true });

      expect(text(utils, "updates-install-unread")).toBe("Could not read the update state.");
      expect(utils.getByTestId("updates-install-unread").closest('[data-testid="button-desc"]')).not.toBeNull();
    });

    it("says it on the Available row where there is no button, so no row comes and goes with the reads", () => {
      installView.current = { ...NOTHING_OFFERED, readFailed: true };
      const utils = renderSection();
      const rowsBefore = utils.getAllByTestId("field").length;

      const line = utils.getByTestId("updates-install-unread");
      expect(line.textContent).toBe("Could not read the update state.");
      expect(line.closest('[data-testid="field"]')?.querySelector('[data-testid="updates-available"]')).not.toBeNull();

      installView.current = { ...NOTHING_OFFERED };
      utils.rerender(
        <UpdatesSection
          update={STATE}
          outcome={NO_OUTCOME}
          checking={false}
          result=""
          onEnabledChange={utils.onEnabledChange}
          onCheckNow={utils.onCheckNow}
        />,
      );
      expect(utils.queryByTestId("updates-install-unread")).toBeNull();
      expect(utils.getAllByTestId("field")).toHaveLength(rowsBefore);
    });

    it("makes the block a focus stop, and hangs what the button waits for on the button itself", () => {
      const utils = withInstall({
        ...failedWith("installer_stopped"),
        pausedDownloads: 1,
        waitReasons: [{ reason: "save_sync" }],
        refusal: "An update is already being installed.",
      });
      const block = utils.getByTestId("updates-caption").closest('[data-testid="field"]');
      const onTheButton = ["updates-paused-hint", "updates-waiting", "updates-install-refusal"].map((id) =>
        utils.getByTestId(id).closest('[data-testid="button-desc"]'),
      );

      expect(block?.getAttribute("tabindex")).toBe("0");
      expect(onTheButton.every((desc) => desc !== null)).toBe(true);
    });
  });
});
