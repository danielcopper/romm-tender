/**
 * Steam reports a start by its 64-bit game ID (Steam's `CGameID`; a non-Steam
 * shortcut is type `k_EGameIDTypeShortcut`), not by its appId. How the two
 * relate is `docs/architecture/save-file-sync-architecture.md`, "Pre-launch
 * sync".
 */

const LOW_32_BITS = 0xffffffffn;
const MAX_64_BITS = 0xffffffffffffffffn;
const SHORTCUT_TYPE_MARK = 0x02000000n;

/**
 * The appId `gameId` names, or `null` when it names nothing Tender can own.
 *
 * A value that fits in 32 bits is taken as the appId itself: Steam has not been
 * seen reporting a shortcut's start that way, and accepting it keeps a Steam
 * that does recognised. The arithmetic is `BigInt` because a shortcut's game ID
 * lies above 2^53, where a `number` no longer holds every integer.
 */
export function appIdFromGameId(gameId: string): number | null {
  if (!/^\d+$/.test(gameId)) return null;
  const value = BigInt(gameId);
  if (value <= LOW_32_BITS) return Number(value);
  if (value > MAX_64_BITS) return null;
  if ((value & LOW_32_BITS) !== SHORTCUT_TYPE_MARK) return null;
  return Number(value >> 32n);
}
