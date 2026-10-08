/**
 * What a stranded panel tells the reader: one headline, and per answer the
 * backend's close carried, the detail beside it and the sentence that joins the
 * two. A stranded panel was loaded by a backend process that is gone, and the
 * one running refuses it; the answer is whether that backend reloads Steam's
 * interface once no game is running — which replaces the panel — or Steam has to
 * be restarted.
 */

/** The two answers a stranded panel can be given (`backend/host/protocol.py`'s two close codes). */
export type StrandedAnswer = "reloads" | "restart_steam";

export const STRANDED_PANEL_HEADLINE = "Tender was restarted";

const DETAIL: Record<StrandedAnswer, string> = {
  reloads: "It reloads Steam's interface once no game is running.",
  restart_steam: "Restart Steam to use it again.",
};

const SENTENCE: Record<StrandedAnswer, string> = {
  reloads: "Tender was restarted — it reloads Steam's interface once no game is running.",
  restart_steam: "Tender was restarted — restart Steam to use it again.",
};

/** The detail beside {@link STRANDED_PANEL_HEADLINE}. */
export function strandedPanelDetail(answer: StrandedAnswer): string {
  return DETAIL[answer];
}

/** The headline and the detail as one sentence. */
export function strandedPanelSentence(answer: StrandedAnswer): string {
  return SENTENCE[answer];
}
