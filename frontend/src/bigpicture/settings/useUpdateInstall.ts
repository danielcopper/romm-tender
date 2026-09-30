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
import { endStoppedAttempt } from "../../utils/stoppedUpdateStore";
import {
  getUpdateInstallAttempt,
  installerSeenAt,
  noteAttempt,
  setUpdateInstallAttempt,
  useUpdateInstallAttempt,
} from "../../utils/updateInstallStore";
import {
  furtherAttempt,
  INSTALL_REFUSAL_SENTENCES,
  INSTALLER_OVERDUE_MS,
  refusalStands,
  type InstallRefusal,
} from "../../utils/updateInstallView";

export const UPDATE_INSTALL_POLL_MS = 3000;

/**
 * How long a read may go unanswered before it counts as failed. A call made
 * while the connection is down does not fail, it waits (`api/hostSocket.ts`),
 * so without this a backend the installer stopped would never read as gone.
 */
export const UPDATE_INSTALL_READ_DEADLINE_MS = 5000;

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
  /** An attempt is downloading, verifying, or its installer has started. */
  underWay: boolean;
  /** The installer is running, so this backend is going away and its connection with it. */
  restarting: boolean;
  /** Five minutes have passed since this panel first saw the installer started, and it is still restarting. */
  overdue: boolean;
  /** The last read failed, or has not answered within {@link UPDATE_INSTALL_READ_DEADLINE_MS}. */
  readFailed: boolean;
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
  const [refusal, setRefusal] = useState<InstallRefusal | null>(null);
  const [readFailed, setReadFailed] = useState(false);
  // The last moment the deadline below was looked at; moved only by its timer.
  const [lookedAt, setLookedAt] = useState(() => Date.now());
  const pushed = useUpdateInstallAttempt();
  // Bumped by a press and by its answer: a read issued before either is older
  // than what the press established, and writes nothing when it lands.
  const generation = useRef(0);
  const lastReading = useRef<UpdateInstallState | null>(null);
  const mounted = useRef(true);
  const pressInFlight = useRef(false);

  const take = useCallback((next: UpdateInstallState) => {
    lastReading.current = next;
    noteAttempt(next.attempt);
    setReading(next);
    setRefusal((held) => (held !== null && !refusalStands(held, next) ? null : held));
  }, []);

  useEffect(() => {
    mounted.current = true;
    let inFlight = false;
    const read = async () => {
      if (inFlight) return;
      inFlight = true;
      const issuedIn = generation.current;
      // Past its deadline the read counts as failed but stays the one in
      // flight: abandoning it would leave it queued behind the connection
      // while the next tick queued another.
      const deadline = setTimeout(() => {
        if (mounted.current) setReadFailed(true);
      }, UPDATE_INSTALL_READ_DEADLINE_MS);
      try {
        const answer = await getUpdateInstallState();
        if (!mounted.current) return;
        setReadFailed(false);
        // A read that lands while a press waits for its answer may still carry
        // the attempt the press replaces; the answer decides instead.
        if (issuedIn === generation.current && !pressInFlight.current) take(answer);
      } catch (e) {
        if (mounted.current) setReadFailed(true);
        // Not logged once the installer started: qam-panel.md, Settings.
        const attempt = furtherAttempt(getUpdateInstallAttempt(), lastReading.current?.attempt ?? null);
        if (attempt?.step !== "installer_started") logError(`Failed to read the update install state: ${e}`);
      } finally {
        clearTimeout(deadline);
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
  const restarting = attempt?.step === "installer_started";
  const underWay = attempt !== null && attempt.step !== "failed";

  // The line under the steps gives way once the installer has had five minutes.
  // Timed from the first time this panel saw it started, which the store
  // keeps across the section's unmounts.
  useEffect(() => {
    const seenAt = installerSeenAt();
    if (!restarting || seenAt === null) return;
    const id = setTimeout(() => setLookedAt(Date.now()), Math.max(0, seenAt + INSTALLER_OVERDUE_MS - Date.now()));
    return () => clearTimeout(id);
  }, [restarting]);
  const seenAt = installerSeenAt();
  const overdue = restarting && seenAt !== null && lookedAt >= seenAt + INSTALLER_OVERDUE_MS;

  const install = async () => {
    // A disabled control still reports a press on the device.
    const last = lastReading.current;
    if (pressInFlight.current || version === null || underWay || (last?.wait_reasons.length ?? 0) > 0) return;
    pressInFlight.current = true;
    generation.current += 1;
    setUpdateInstallAttempt(null);
    if (last !== null) take({ ...last, attempt: null });
    setPressing(true);
    setRefusal(null);
    try {
      const answer = await installUpdate(version);
      generation.current += 1;
      // The new attempt ended the stopped one's record, and the card on Main
      // with it, whether or not this section is still on screen.
      if (answer.success) endStoppedAttempt();
      if (!mounted.current) return;
      const now = lastReading.current;
      if (answer.success) {
        if (now !== null) take({ ...now, attempt: started(version), wait_reasons: [] });
      } else if (answer.reason === "update_waiting") {
        if (now !== null) take({ ...now, wait_reasons: answer.wait_reasons });
      } else {
        setRefusal({ reason: answer.reason, version });
      }
    } catch (e) {
      logError(`Failed to request the update install: ${e}`);
      if (mounted.current) setRefusal({ reason: "request_failed", version });
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
    refusal: refusal === null ? "" : INSTALL_REFUSAL_SENTENCES[refusal.reason],
    underWay,
    restarting,
    overdue,
    readFailed,
    install: () => detach(install()),
  };
}
