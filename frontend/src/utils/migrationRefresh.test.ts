import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { useMigrationRefresh } from "./migrationRefresh";
import { clearMigration, getMigrationState } from "./migrationStore";
import * as backend from "../api/backend";

describe("useMigrationRefresh", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    clearMigration();
    vi.mocked(backend.refreshMigrationState).mockReset();
    vi.spyOn(backend, "logError").mockImplementation(() => {});
  });

  it("puts the backend's answer into the migration store", async () => {
    vi.mocked(backend.refreshMigrationState).mockResolvedValue({ retrodeck: { pending: true, old_path: "/old" } });

    renderHook(() => useMigrationRefresh(1));

    await waitFor(() => expect(getMigrationState()).toEqual({ pending: true, old_path: "/old" }));
  });

  it("asks again for another app, and not on a render for the same one", () => {
    vi.mocked(backend.refreshMigrationState).mockResolvedValue({ retrodeck: { pending: false } });

    const { rerender } = renderHook(({ appId }) => useMigrationRefresh(appId), { initialProps: { appId: 1 } });
    rerender({ appId: 1 });
    expect(backend.refreshMigrationState).toHaveBeenCalledTimes(1);

    rerender({ appId: 2 });
    expect(backend.refreshMigrationState).toHaveBeenCalledTimes(2);
  });

  it("logs a failed ask and leaves the store as it was", async () => {
    vi.mocked(backend.refreshMigrationState).mockRejectedValue(new Error("boom"));
    const before = getMigrationState();

    renderHook(() => useMigrationRefresh(1));

    await waitFor(() =>
      expect(backend.logError).toHaveBeenCalledExactlyOnceWith("Failed to refresh migration state: Error: boom"),
    );
    expect(getMigrationState()).toBe(before);
  });
});
