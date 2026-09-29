import { useCallback, useEffect, useRef, useState } from "react";
import {
  getUpdateInstallState,
  installUpdate,
  logError,
  type UpdateInstallAttempt,
  type UpdateInstallState,
  type UpdateWaitReason,
} from "../../api/backend";
import { detach } from "../../utils/detach";
import {
  getUpdateInstallAttempt,
  setUpdateInstallAttempt,
  useUpdateInstallAttempt,
} from "../../utils/updateInstallStore";
import { furtherAttempt, INSTALL_REQUEST_FAILED } from "../../utils/updateInstallView";

export const UPDATE_INSTALL_POLL_MS = 3000;

export interface UpdateInstall {
  offered: boolean;
  version: string | null;
  waitReasons: UpdateWaitReason[];
  pausedDownloads: number;
  attempt: UpdateInstallAttempt | null;
  tryAgain: boolean;
  /** A press is waiting for its answer. */
  pressing: boolean;
  /** What refused the last press, where it was not a wait; `""` otherwise. */
  refusal: string;
  /** The installer is running, so this backend is going away and its connection with it. */
  restarting: boolean;
  install: () => void;
}

/** The state an answer of `{success: true}` stands for: the attempt exists and is downloading. */
const started = (version: string): UpdateInstallAttempt => ({
  version,
  step: "downloading",
  bytes_done: 0,
  bytes_total: null,
  failure: null,
});

/**
 * Settings › Updates' install: the state read every
 * {@link UPDATE_INSTALL_POLL_MS} while the caller is mounted, the frames the
 * backend pushes, and the press. One read is in flight at a time; why, and how
 * the reads and the frames are told apart, is `docs/architecture/qam-panel.md`,
 * Settings.
 */
export function useUpdateInstall(): UpdateInstall {
  const [reading, setReading] = useState<UpdateInstallState | null>(null);
  const [pressing, setPressing] = useState(false);
  const [refusal, setRefusal] = useState("");
  const pushed = useUpdateInstallAttempt();
  // Bumped by a press and by its answer: a read issued before either is older
  // than what the press established, and writes nothing when it lands.
  const generation = useRef(0);
  const lastReading = useRef<UpdateInstallState | null>(null);
  const mounted = useRef(true);
  const pressInFlight = useRef(false);

  const take = useCallback((next: UpdateInstallState) => {
    lastReading.current = next;
    setReading(next);
  }, []);

  useEffect(() => {
    mounted.current = true;
    let inFlight = false;
    const read = async () => {
      if (inFlight) return;
      inFlight = true;
      const issuedIn = generation.current;
      try {
        const answer = await getUpdateInstallState();
        if (mounted.current && issuedIn === generation.current) take(answer);
      } catch (e) {
        // Once the installer started, the connection going is what success
        // looks like, so a read it takes down is no failure to report.
        const attempt = furtherAttempt(getUpdateInstallAttempt(), lastReading.current?.attempt ?? null);
        if (attempt?.step !== "installer_started") logError(`Failed to read the update install state: ${e}`);
      } finally {
        inFlight = false;
      }
    };
    detach(read());
    const id = setInterval(() => detach(read()), UPDATE_INSTALL_POLL_MS);
    return () => {
      mounted.current = false;
      clearInterval(id);
    };
  }, [take]);

  const attempt = furtherAttempt(pushed, reading?.attempt ?? null);
  const version = reading?.version ?? null;

  const install = async () => {
    // A disabled control still reports a press on the device.
    if (pressInFlight.current || version === null) return;
    pressInFlight.current = true;
    generation.current += 1;
    setUpdateInstallAttempt(null);
    if (lastReading.current !== null) take({ ...lastReading.current, attempt: null });
    setPressing(true);
    setRefusal("");
    try {
      const answer = await installUpdate(version);
      generation.current += 1;
      if (!mounted.current) return;
      const last = lastReading.current;
      if (answer.success) {
        if (last !== null) take({ ...last, attempt: started(version), wait_reasons: [] });
      } else if (answer.reason === "update_waiting") {
        if (last !== null) take({ ...last, wait_reasons: answer.wait_reasons });
      } else {
        setRefusal(answer.message);
      }
    } catch (e) {
      logError(`Failed to request the update install: ${e}`);
      if (mounted.current) setRefusal(INSTALL_REQUEST_FAILED);
    } finally {
      pressInFlight.current = false;
      if (mounted.current) setPressing(false);
    }
  };

  return {
    offered: reading?.offered ?? false,
    version,
    waitReasons: reading?.wait_reasons ?? [],
    pausedDownloads: reading?.paused_downloads ?? 0,
    attempt,
    tryAgain: (reading?.try_again ?? false) || (attempt?.step === "failed" && attempt.version === version),
    pressing,
    refusal,
    restarting: attempt?.step === "installer_started",
    install: () => detach(install()),
  };
}
