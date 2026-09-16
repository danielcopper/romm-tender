import { toaster } from "@decky/api";
import type { ToastData, ToastNotification } from "@decky/api";
import type { ReactNode } from "react";

// Must match the backend's `DISPLAY_NAME` (`backend/domain/identity.py`) —
// this value, handed back by `definePlugin`, is what the QAM header shows.
// Nothing checks that the two agree.
export const PLUGIN_NAME = "Tender";

/**
 * Raise a toast under the plugin's name.
 *
 * *options* passes through to `toaster.toast` and may override the title, which
 * no call site currently needs: every notice comes from the same plugin, so a
 * second sender would only make the user work out that they are the same thing.
 */
export function showToast(body: ReactNode, options?: Partial<Omit<ToastData, "body">>): ToastNotification {
  return toaster.toast({ title: PLUGIN_NAME, body, ...options });
}
