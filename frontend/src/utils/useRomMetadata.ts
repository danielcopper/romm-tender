/**
 * A ROM's metadata record, read through the page's shared read.
 *
 * The hook binds what it holds to the ROM it was read for, and shows it only while
 * that ROM is the one asked about (`docs/architecture/invariants.md`, the entry on
 * per-rom detail state).
 */

import { useEffect, useState } from "react";
import { getRomMetadataShared } from "../api/sharedReads";
import { logWarn } from "../api/backend";
import type { RomMetadata } from "../types";

/** One ROM's record, tagged with the ROM it was read for. */
interface LoadedMetadata {
  romId: number;
  metadata: RomMetadata | null;
}

/** `romId`'s metadata record, read anew whenever `romId` changes; `null` until a
 *  read of it has landed, after a read of it threw, and for no ROM. A record
 *  read for another ROM is never answered. */
export function useRomMetadata(romId: number | null): RomMetadata | null {
  const [loaded, setLoaded] = useState<LoadedMetadata | null>(null);

  useEffect(() => {
    if (romId === null) return;
    let cancelled = false;
    getRomMetadataShared(romId).then(
      (metadata) => {
        if (!cancelled) setLoaded({ romId, metadata });
      },
      (e: unknown) => {
        logWarn(`useRomMetadata: the metadata read for rom ${romId} failed: ${String(e)}`);
        if (!cancelled) setLoaded({ romId, metadata: null });
      },
    );
    return () => {
      cancelled = true;
    };
  }, [romId]);

  return loaded !== null && loaded.romId === romId ? loaded.metadata : null;
}
