import { toaster } from "../api/host";
import type { ToastData, ToastNotification } from "../api/host";
import type { ReactNode } from "react";

// Must match the backend's `DISPLAY_NAME` (`backend/domain/identity.py`) —
// this value, handed back by `definePanel`, is what the QAM header shows.
// Nothing checks that the two agree.
export const DISPLAY_NAME = "Tender";

/**
 * Raise a toast under Tender's name.
 *
 * *options* passes through to `toaster.toast` and may override the title, which
 * no call site currently needs: every notice comes from Tender, so a
 * second sender would only make the user work out that they are the same thing.
 */
export function showToast(body: ReactNode, options?: Partial<Omit<ToastData, "body">>): ToastNotification {
  return toaster.toast({ title: DISPLAY_NAME, body, ...options });
}
