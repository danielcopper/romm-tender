import { getShortcutLaunchOptions, type ShortcutLaunchOptions } from "../api/backend";

/**
 * What Steam's `shortcuts.vdf` showed when it was read: the launch options of
 * every shortcut asked about that the file holds, or why there was no reading.
 */
export type ShortcutFileReading =
  { status: "read"; launchOptions: ReadonlyMap<number, string> } | { status: "unreadable"; why: string };

/** Answers the reading of Steam's shortcut file a confirm falls back on. */
export type ShortcutFileReader = () => Promise<ShortcutFileReading>;

/**
 * Ask the backend what Steam's shortcut file holds for `appIds`, once. A
 * refusal, or a backend that answered nothing, is a file that could not be
 * read; one that threw rejects, which the confirm counts the same way.
 */
export async function readShortcutFile(appIds: number[]): Promise<ShortcutFileReading> {
  const answer = (await getShortcutLaunchOptions(appIds)) as ShortcutLaunchOptions | undefined;
  if (answer?.success) {
    return {
      status: "read",
      launchOptions: new Map(answer.launch_options.map((entry) => [entry.app_id, entry.launch_options])),
    };
  }
  return { status: "unreadable", why: answer?.message ?? "no answer" };
}

/**
 * One reading of Steam's shortcut file for all of `appIds`, started by the
 * first caller and answered to every caller after it; a reader nobody calls
 * reads nothing. For a batch of confirms, so a batch whose confirms fail
 * together reads the file once rather than once per shortcut.
 */
export function sharedShortcutFileReader(appIds: number[]): ShortcutFileReader {
  let reading: Promise<ShortcutFileReading> | undefined;
  return () => (reading ??= readShortcutFile(appIds));
}
