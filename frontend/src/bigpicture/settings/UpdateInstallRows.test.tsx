import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { act, render, screen } from "@testing-library/react";
import { getUpdateInstallState, type UpdateInstallAttempt, type UpdateInstallState } from "../../api/backend";
import * as backend from "../../api/backend";
import { HostTransportError } from "../../api/host";
import { setStrandedAnswer } from "../../test-utils/stranded-panel";
import { setUpdateInstallAttempt } from "../../utils/updateInstallStore";
import { INSTALLER_OVERDUE_MS, NOT_BACK_LINE, restartWaitLine, runningAgainLine } from "../../utils/updateInstallView";
import { UpdateInstallRows } from "./UpdateInstallRows";
import { UPDATE_INSTALL_READ_DEADLINE_MS, useUpdateInstall } from "./useUpdateInstall";

// The rows over the real hook, reads and all: what a reader sees once the
// backend the installer stopped no longer answers.
const Install = () => <UpdateInstallRows install={useUpdateInstall()} record={null} installed="0.9.0" />;

const INSTALLER_STARTED: UpdateInstallAttempt = {
  version: "1.0.0",
  step: "installer_started",
  bytes_done: 100,
  bytes_total: 100,
  failure: null,
};

const OFFERED: UpdateInstallState = {
  offered: true,
  version: "1.0.0",
  wait_reasons: [],
  paused_downloads: 0,
  attempt: INSTALLER_STARTED,
  try_again: false,
};

describe("UpdateInstallRows over useUpdateInstall", () => {
  let logError: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    vi.useFakeTimers();
    setUpdateInstallAttempt(null);
    vi.mocked(getUpdateInstallState).mockReset().mockResolvedValue(OFFERED);
    logError = vi.spyOn(backend, "logError").mockImplementation(() => undefined);
  });

  afterEach(() => {
    logError.mockRestore();
    vi.useRealTimers();
  });

  it("infers the phase from the reads: checking while they answer, restarting once they stop, not back seven minutes on", async () => {
    render(<Install />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(screen.getByTestId("updates-caption").textContent).toBe("Checking the new version");
    expect(screen.queryByTestId("updates-note")).toBeNull();

    // What a call does once the installer stopped the backend: it waits in the
    // connection's outbox, and never rejects.
    vi.mocked(getUpdateInstallState).mockReturnValue(new Promise(() => {}));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000 + UPDATE_INSTALL_READ_DEADLINE_MS);
    });
    expect(screen.getByTestId("updates-caption").textContent).toBe("Tender is restarting");
    expect(screen.getByTestId("updates-note").textContent).toBe(restartWaitLine("0.9.0"));

    await act(async () => {
      await vi.advanceTimersByTimeAsync(INSTALLER_OVERDUE_MS);
    });

    expect(screen.getByTestId("updates-note").textContent).toBe(NOT_BACK_LINE);
  });

  it.each(["reloads", "restart_steam"] as const)(
    "says Tender is running again once a read fails as stranded (%s), and never that it has not come back",
    async (answer) => {
      render(<Install />);
      await act(async () => {
        await vi.advanceTimersByTimeAsync(0);
      });

      // What a stranded socket does to every call: fails it at once, its
      // answer already set (api/hostSocket.ts).
      act(() => setStrandedAnswer(answer));
      vi.mocked(getUpdateInstallState).mockRejectedValue(
        new HostTransportError("stranded_panel", "Tender was restarted — restart Steam to use it again."),
      );
      await act(async () => {
        await vi.advanceTimersByTimeAsync(3000);
      });
      expect(screen.getByTestId("updates-caption").textContent).toBe("Tender is running again");
      expect(screen.getByTestId("updates-note").textContent).toBe(runningAgainLine(answer));

      await act(async () => {
        await vi.advanceTimersByTimeAsync(INSTALLER_OVERDUE_MS);
      });

      expect(screen.getByTestId("updates-caption").textContent).toBe("Tender is running again");
      expect(screen.queryByText(NOT_BACK_LINE)).toBeNull();
    },
  );

  it("reads any other failed read as the restart it was before, and not back seven minutes on", async () => {
    render(<Install />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });

    vi.mocked(getUpdateInstallState).mockRejectedValue(new HostTransportError("connection_lost", "socket closed"));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });
    expect(screen.getByTestId("updates-caption").textContent).toBe("Tender is restarting");
    expect(screen.getByTestId("updates-note").textContent).toBe(restartWaitLine("0.9.0"));

    await act(async () => {
      await vi.advanceTimersByTimeAsync(INSTALLER_OVERDUE_MS);
    });

    expect(screen.getByTestId("updates-note").textContent).toBe(NOT_BACK_LINE);
  });
});
