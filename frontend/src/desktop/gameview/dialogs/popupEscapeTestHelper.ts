/**
 * Local test helper verifying that a desktop dialog listens for `Escape` on
 * its rendered `Window` (`ownerDocument.defaultView`) rather than `globalThis.window`.
 */

import type { ReactElement } from "react";
import { fireEvent, render } from "@testing-library/react";
import { Window as HappyWindow } from "happy-dom";
import { expect, vi } from "vitest";

export function assertDialogEscapeScopedToOwnWindow(renderElement: (onDismiss: () => void) => ReactElement): void {
  const onDismiss = vi.fn();
  const popupWin = new HappyWindow();
  const container = popupWin.document.createElement("div");
  popupWin.document.body.appendChild(container);
  const { unmount } = render(renderElement(onDismiss), { container: container as unknown as HTMLElement });
  try {
    fireEvent.keyDown(globalThis.window, { key: "Escape" });
    expect(onDismiss).not.toHaveBeenCalled();

    fireEvent.keyDown(popupWin as unknown as Window, { key: "Escape" });
    expect(onDismiss).toHaveBeenCalledTimes(1);
  } finally {
    unmount();
    void popupWin.close();
  }
}
