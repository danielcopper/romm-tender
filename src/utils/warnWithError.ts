import { logWarn } from "../api/backend";
import { thrownText } from "./thrownText";

/** Write *line* to the console, with the error itself where one is handed, so its stack is kept. */
export function warnInConsole(line: string, ...error: [] | [unknown]): void {
  console.warn(`[Tender] ${line}`, ...error);
}

/**
 * Write *line* to the console as `warnInConsole` does, and to the plugin log
 * with the error as text. It does not throw, so it can be called from a catch
 * whose job is not to: a plugin log that throws is passed over, the console
 * line having gone out already.
 */
export function warnWithError(line: string, ...error: [] | [unknown]): void {
  warnInConsole(line, ...error);
  try {
    logWarn(error.length === 0 ? line : `${line}: ${thrownText(error[0])}`);
  } catch {
    // The console line above carries it.
  }
}
