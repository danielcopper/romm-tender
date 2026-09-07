/**
 * Whether the next sync CONTINUES an incomplete run or starts one over — the
 * name the sync button carries, and the line that says how much is already done.
 *
 * The Sync page names the button that starts a run from it and hands the
 * session-budget card the same name, which is the whole point of that card
 * taking the name rather than deriving it (#1789). Main asks nothing of it: it
 * starts no run, so it has no button to name. A second spelling of the reasoning
 * below is what would drift.
 */

import type { SyncStats } from "../types";
import { pluralize } from "./pluralize";

/** What the sync button says, whether pressing it continues a run, and the line
 *  under it — `null` where there is no number worth stating. */
export interface SyncResumeState {
  canResume: boolean;
  label: string;
  scopeText: string | null;
}

/**
 * The line under "Resume Sync" — how much of a resume there is, counted in the
 * unit the user recognises: games whose shortcut the next run can pass over.
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
