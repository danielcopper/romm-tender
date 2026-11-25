import { useEffect, useState } from "react";
import { logWarn } from "../../api/backend";
import { detach } from "../../utils/detach";
import { changesSaveAnswers } from "../../utils/saveHelpers";

interface RomAnswerOptions {
  /** Begins each log line, naming the component that asked. */
  owner: string;
  /** What the read is, for its log line. */
  what: string;
  /** Asked again whenever this changes, beside the changes `changesSaveAnswers` names. */
  rereadOn?: string | number | boolean | null;
}

/**
 * One read about the ROM shown: asked while it is wanted and again on a change
 * that may move it, ordered by a sequence, and tagged with its ROM so another
 * version's answer reads as none. A read that fails holds no answer rather than
 * the one from before, which may be about another emulator. `ask` is module-level.
 */
export function useRomAnswer<T>(
  romId: number | null,
  wanted: boolean,
  ask: (romId: number) => Promise<T>,
  { owner, what, rereadOn = null }: RomAnswerOptions,
): T | null {
  const [answer, setAnswer] = useState<{ romId: number; value: T } | null>(null);
  if (!wanted && answer !== null) setAnswer(null);

  useEffect(() => {
    if (romId === null || !wanted) return;
    let cancelled = false;
    let latestRead = 0;
    const read = async () => {
      latestRead += 1;
      const thisRead = latestRead;
      try {
        const value = await ask(romId);
        if (!cancelled && thisRead === latestRead) setAnswer({ romId, value });
      } catch (e) {
        logWarn(`${owner}: ${what} read for romId=${romId} failed: ${e}`);
        if (!cancelled && thisRead === latestRead) setAnswer(null);
      }
    };
    detach(read());
    const onDataChanged = ({ detail }: WindowEventMap["romm_data_changed"]) => {
      if (changesSaveAnswers(detail, romId)) detach(read());
    };
    globalThis.addEventListener("romm_data_changed", onDataChanged);
    return () => {
      cancelled = true;
      globalThis.removeEventListener("romm_data_changed", onDataChanged);
    };
  }, [romId, wanted, owner, what, ask, rereadOn]);

  return answer !== null && answer.romId === romId ? answer.value : null;
}
