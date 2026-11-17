/**
 * How the panel words a call to Tender's backend that failed in transport (#2776 D9).
 */

import { HostTransportError, isConnectionLost, isStrandedPanelFailure } from "../api/host";

/**
 * The sentence for *e* where it is a transport failure, *lead* saying what failed; `null` for any other error. A lost
 * connection is named as the cause; a stranded panel answers with its own sentence; any other transport failure (an
 * endpoint that raised, an answer too large, an unknown method, a broken message) is *lead* alone, since naming a cause
 * there would name the wrong one.
 */
export function transportFailureSentence(lead: string, e: unknown): string | null {
  if (!(e instanceof HostTransportError)) return null;
  if (isStrandedPanelFailure(e)) return e.message;
  if (isConnectionLost(e)) return `${lead}: the connection to Tender's backend was lost.`;
  return lead;
}
