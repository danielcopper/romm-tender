import { describe, it, expect, beforeEach, vi } from "vitest";
import { showModal } from "@decky/ui";
import type { ReactElement } from "react";
import { showUnsyncedSavesModal } from "./UnsyncedSavesSwitchModal";

// The global @decky/ui stub (frontend/src/test-setup.ts) renders ConfirmModal
// as a pass-through <div> and exposes showModal as a vi.fn. We grab the element
// handed to showModal and read its props directly (mirroring
// OfflineDriftModal.test) to assert the rendered copy and invoke onOK /
// onMiddleButton / onCancel.
interface ConfirmModalProps {
  strTitle?: string;
  strDescription?: string;
  strOKButtonText?: string;
  strMiddleButtonText?: string;
  strCancelButtonText?: string;
  onOK?: () => void;
  onMiddleButton?: () => void;
  onCancel?: () => void;
}

function lastConfirmModalProps(): ConfirmModalProps {
  const calls = vi.mocked(showModal).mock.calls;
  const el = calls[calls.length - 1]?.[0] as ReactElement<ConfirmModalProps> | undefined;
  if (!el) throw new Error("showModal was not called");
  return el.props;
}

describe("UnsyncedSavesSwitchModal — reachable (T4)", () => {
  beforeEach(() => {
    vi.mocked(showModal).mockClear();
  });

  it("shows the sync + switch-anyway buttons and the reachable copy", () => {
    void showUnsyncedSavesModal({
      versionName: "Game (USA)",
      stranding: "unsynced",
      serverReachable: true,
      versionGone: false,
    });
    const props = lastConfirmModalProps();
    expect(props.strTitle).toBe("Unsynced saves");
    expect(props.strDescription).toContain("Game (USA)");
    expect(props.strDescription).toContain("haven't been synced to RomM");
    expect(props.strOKButtonText).toBe("Sync now & switch");
    expect(props.strMiddleButtonText).toBe("Switch anyway");
    expect(props.strCancelButtonText).toBe("Cancel");
  });

  it("resolves 'sync_and_switch' on OK", async () => {
    const promise = showUnsyncedSavesModal({
      versionName: "X",
      stranding: "unsynced",
      serverReachable: true,
      versionGone: false,
    });
    lastConfirmModalProps().onOK?.();
    await expect(promise).resolves.toBe("sync_and_switch");
  });

  it("resolves 'switch_anyway' on the middle button", async () => {
    const promise = showUnsyncedSavesModal({
      versionName: "X",
      stranding: "unsynced",
      serverReachable: true,
      versionGone: false,
    });
    lastConfirmModalProps().onMiddleButton?.();
    await expect(promise).resolves.toBe("switch_anyway");
  });

  it("resolves 'cancel' on Cancel", async () => {
    const promise = showUnsyncedSavesModal({
      versionName: "X",
      stranding: "unsynced",
      serverReachable: true,
      versionGone: false,
    });
    lastConfirmModalProps().onCancel?.();
    await expect(promise).resolves.toBe("cancel");
  });
});

describe("UnsyncedSavesSwitchModal — offline (T5)", () => {
  beforeEach(() => {
    vi.mocked(showModal).mockClear();
  });

  it("offers NO sync option — OK is 'Switch anyway' and there is no middle button", () => {
    void showUnsyncedSavesModal({
      versionName: "Game (USA)",
      stranding: "unsynced",
      serverReachable: false,
      versionGone: false,
    });
    const props = lastConfirmModalProps();
    expect(props.strTitle).toBe("Unsynced saves");
    expect(props.strDescription).toContain("not reachable right now");
    expect(props.strOKButtonText).toBe("Switch anyway");
    expect(props.strMiddleButtonText).toBeUndefined();
    expect(props.onMiddleButton).toBeUndefined();
    expect(props.strCancelButtonText).toBe("Cancel");
  });

  it("resolves 'switch_anyway' on OK", async () => {
    const promise = showUnsyncedSavesModal({
      versionName: "X",
      stranding: "unsynced",
      serverReachable: false,
      versionGone: false,
    });
    lastConfirmModalProps().onOK?.();
    await expect(promise).resolves.toBe("switch_anyway");
  });

  it("resolves 'cancel' on Cancel", async () => {
    const promise = showUnsyncedSavesModal({
      versionName: "X",
      stranding: "unsynced",
      serverReachable: false,
      versionGone: false,
    });
    lastConfirmModalProps().onCancel?.();
    await expect(promise).resolves.toBe("cancel");
  });
});

describe("UnsyncedSavesSwitchModal — the version being left is gone from RomM", () => {
  beforeEach(() => {
    vi.mocked(showModal).mockClear();
  });

  it.each([true, false])(
    "offers only Switch anyway and Cancel, and says why (reachable: %s)",
    async (serverReachable) => {
      const promise = showUnsyncedSavesModal({
        versionName: "Game (USA)",
        stranding: "unsynced",
        serverReachable,
        versionGone: true,
      });
      const props = lastConfirmModalProps();
      expect(props.strDescription).toContain("no longer available on RomM, so its saves can't be uploaded");
      expect(props.strDescription).toContain('"Game (USA)" is no longer available on RomM');
      expect(props.strDescription).toContain("They stay on disk.");
      expect(props.strOKButtonText).toBe("Switch anyway");
      expect(props.strMiddleButtonText).toBeUndefined();
      expect(props.onMiddleButton).toBeUndefined();
      expect(props.strCancelButtonText).toBe("Cancel");
      props.onOK?.();
      await expect(promise).resolves.toBe("switch_anyway");
    },
  );

  it("keeps Sync now & switch for a version nothing proved gone", () => {
    void showUnsyncedSavesModal({
      versionName: "Game (USA)",
      stranding: "unsynced",
      serverReachable: true,
      versionGone: false,
    });
    const props = lastConfirmModalProps();
    expect(props.strOKButtonText).toBe("Sync now & switch");
    expect(props.strDescription).not.toContain("no longer available on RomM");
  });
});

describe("UnsyncedSavesSwitchModal — a check that could not read", () => {
  beforeEach(() => {
    vi.mocked(showModal).mockClear();
  });

  it("says Tender couldn't check rather than that the saves were never synced", () => {
    void showUnsyncedSavesModal({
      versionName: "Game (USA)",
      stranding: "unchecked",
      serverReachable: true,
      versionGone: false,
    });
    const props = lastConfirmModalProps();
    expect(props.strDescription).toContain('Tender couldn\'t check whether "Game (USA)" has saves');
    expect(props.strOKButtonText).toBe("Sync now & switch");
    expect(props.strMiddleButtonText).toBe("Switch anyway");
  });
});
