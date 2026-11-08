/**
 * The comparison dialog's check of the files on this device against the
 * server's, for every surface that draws that dialog.
 */

import { useEffect, useState } from "react";
import { addEventListener, removeEventListener } from "../api/host";
import { logWarn, verifyExistingContent } from "../api/backend";
import { VERIFY_UNREACHABLE_MESSAGE } from "./adoptWording";
import type { VerifyContentResult, VerifyProgressEvent } from "../types";

interface ContentCheck {
  verifying: boolean;
  /** How far the running check has got, from 0 to 1; `null` while none runs. */
  progress: number | null;
  /** The last check's answer; `null` before the first and while one runs. */
  verdict: VerifyContentResult | null;
  /** Start a check, unless one runs. */
  verify: () => Promise<void>;
}

/**
 * Check the files at `candidatePath`, or at the ROM's own location without one,
 * against the server's. A check that throws answers that the server could not be
 * reached, and is logged as a warning under `tag`.
 */
export function useContentCheck(romId: number, candidatePath: string | undefined, tag: string): ContentCheck {
  const [verifying, setVerifying] = useState(false);
  const [progress, setProgress] = useState<number | null>(null);
  const [verdict, setVerdict] = useState<VerifyContentResult | null>(null);

  useEffect(() => {
    const listener = addEventListener<VerifyProgressEvent>("verify_progress", (payload) => {
      if (payload.rom_id !== romId || !payload.bytes_total) return;
      setProgress(payload.bytes_done / payload.bytes_total);
    });
    return () => removeEventListener("verify_progress", listener);
  }, [romId]);

  const verify = async () => {
    if (verifying) return;
    setVerifying(true);
    setVerdict(null);
    setProgress(0);
    try {
      setVerdict(await verifyExistingContent(romId, candidatePath ?? null));
    } catch (e) {
      logWarn(`${tag}: the content check of romId=${romId} failed: ${e}`);
      setVerdict({ status: "error", message: VERIFY_UNREACHABLE_MESSAGE, differences: [] });
    } finally {
      setVerifying(false);
      setProgress(null);
    }
  };

  return { verifying, progress, verdict, verify };
}
