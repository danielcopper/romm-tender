/**
 * Whether the next sync CONTINUES an incomplete run or starts one over — the
 * name a press that starts a run carries, the line that says how much is already
 * done, and, with the preview question folded in, the name on the button itself.
 *
 * The Sync page names the button that starts a run from it and hands the
 * session-budget card the same name, which is the whole point of that card
 * taking the name rather than deriving it (#1789). Main asks nothing of it: it
 * starts no run, so it has no button to name. A second spelling of the reasoning
 * below is what would drift.
 */

import type { SyncStats } from "../types";
import { pluralize } from "./pluralize";

/** What a press that starts a run says, whether that press continues one, and
 *  the line under the button — `null` where there is no number worth stating. */
export interface SyncResumeState {
  canResume: boolean;
  /** The name of a press that starts a RUN. It is on the button only where the
   *  button starts one — {@link startButtonLabel} is what the page renders. */
  label: string;
  scopeText: string | null;
}

/** What a press that only works out a preview says. It deliberately matches the
 *  words Main's conditional slot shows while that run is going ("Checking for
 *  changes"), so the button and the state it produces read as one thing. */
const PREVIEW_LABEL = "Check for changes";

/**
 * The name on the Sync page's start button: what the press DOES.
 *
 * Skip preview decides which of the two things a press is. With it off the press
 * works out a preview and adds nothing to Steam, however much of the library is
 * already synced — so the resume question, which is about a RUN, has nothing to
 * say about the name; it is answered under the button instead, by
 * {@link formatResumeScope}. With it on the press starts the run itself, and
 * then the name is the resume question's answer.
 */
export function startButtonLabel(resume: SyncResumeState, skipPreview: boolean): string {
  return skipPreview ? resume.label : PREVIEW_LABEL;
}

/**
 * The line under the start button — how much of a resume there is, counted in
 * the unit the user recognises: games whose shortcut the next run can pass over.
 *
 * It is where the resume survives a button that does not name it: with Skip
 * preview off the press works out a preview, so the button says so and this line
 * is the only thing that says a resume is waiting.
 *
 * It states what is already done, never what is left, and carries no total: the
 * remainder needs the server's library, while this line is drawn from stats the
 * panel already holds, every time the Sync page offers a resume. "already
 * synced" is a claim about these games only — the clause that follows is what
 * keeps it from reading as a claim that the library is complete.
 *
 * Omitted entirely when the count is zero, which a resume on a surviving
 * completion stamp alone can be. Honest silence beats "0 games".
 */
export function formatResumeScope(resumableGames: number): string {
  return `${pluralize(resumableGames, "game")} already synced — a resume continues from there.`;
}

/**
 * Read the resume question off the stats.
 *
 * ``last_attempt`` is non-null exactly when the newest terminal run did NOT
 * complete. "errored" is not a resume: an errored run often fails before
 * applying anything (e.g. a config error), so "resume" isn't the right mental
 * model. A completed sync clears ``last_attempt`` on the stats refresh, flipping
 * the label back.
 *
 * An incomplete attempt alone is not enough, and the half that used to stand in
 * for "progress survives" was measuring the wrong thing: it asked whether
 * SHORTCUTS exist, and Force Full Sync does not delete shortcuts — it deletes
 * the completion stamps and the recorded launch commands. So the offer survived
 * a clear that had just discarded everything it offered to continue (#1789).
 *
 * What a resume actually rests on is skip authority, of which this plugin keeps
 * two kinds and clears both together in that one place: a completion stamp
 * (whole platform or collection skipped at fetch time) or a recorded launch
 * command (one game skipped at apply time). Either is a real resume — a run
 * cancelled inside its first platform has written shortcuts and recorded their
 * commands without reaching a stamp, and the next run genuinely does less work.
 *
 * ``roms > 0`` is LOAD-BEARING, not a belt-and-braces restatement of the two
 * branches. ``has_completion_stamp`` is a global "any stamp anywhere", while the
 * removal path is surgical: it deletes only the platform slugs its removed rows
 * name, and only the collection stamps whose member set intersects those rows. A
 * stamp naming nothing the ``roms`` table still holds therefore outlives a
 * remove-all. Prune is the reachable path — it deletes ``roms`` rows and never
 * touches ``platform_sync_state`` (services/prune/registry.py ``delete_rows``),
 * so a platform whose games RomM dropped keeps its stamp with no rows left to
 * name it; the next remove-all cannot see that slug to invalidate it. Without
 * this conjunct that state offers "Resume Sync" over zero shortcuts. It is also
 * the rule in its own right: a run that stopped before a single shortcut was
 * written starts from the beginning and must read "Sync Library".
 */
export function syncResumeState(stats: SyncStats | null): SyncResumeState {
  const status = stats?.last_attempt?.status;
  const incompleteAttempt = status === "interrupted" || status === "cancelled" || status === "paused";
  const resumableGames = stats?.resumable_games ?? 0;
  const skipAuthoritySurvives = resumableGames > 0 || (stats?.has_completion_stamp ?? false);
  const canResume = incompleteAttempt && (stats?.roms ?? 0) > 0 && skipAuthoritySurvives;
  return {
    canResume,
    label: canResume ? "Resume Sync" : "Sync Library",
    scopeText: canResume && resumableGames > 0 ? formatResumeScope(resumableGames) : null,
  };
}
