/**
 * Logging a ROM call the backend refused or that did not answer.
 */

import { logError, logWarn, type BackendResult } from "../api/backend";

export function logRefusal(tag: string, what: string, romId: number, result: BackendResult): void {
  logWarn(`${tag}: the backend refused to ${what} romId=${romId}: ${result.reason ?? "no reason"}: ${result.message}`);
}

export function logFailure(tag: string, what: string, romId: number, e: unknown): void {
  logError(`${tag}: could not ${what} romId=${romId}: ${String(e)}`);
}
