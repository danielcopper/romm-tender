# Invariant register

The audit's clearest pattern: every rule with a mechanical check held; every rule that lived in prose or in a reviewer's
head drifted. This register is the single inventory of the cross-cutting safety rules — the ones that span files, so no
diff-scoped review (human or agent) sees the whole rule — plus the current enforcement tier of each. It is a **map of
the enforcement surface, not the enforcement itself**: a `check`/`test` rule is enforced by the named artifact; a
`prompt-only` rule is not yet mechanized and is carried by review until a check exists. The moment a `prompt-only` rule
gets a mechanical check it moves to the `check` tier — a rule is never weakened to stay green, and a real drift is a
finding to triage, never an exemption.

Each entry stands once, in the file of the area it belongs to; an entry that concerns two areas stands in one of them,
and the other points to it. `CLAUDE.md` keeps the map from area to file and the rules whose breach loses user data for
good, any area can break, and no check holds fully. Whoever implements or reviews in an area reads that area's file
first.

## How an entry reads

Format: **headline** — tier — enforced by.

An entry binds as a whole. The bold headline names the rule. Where the rule is longer than its headline, the paragraph
directly under the headline holds the rest of it and binds the same way. What follows — what enforces the rule, what
that does not see, why the rule exists, the tests that pin it — stays what it is.

`scripts/check_invariant_register.py` holds the register to its size: a headline at most 300 characters, an area file at
most 40,000 (an area that grows past it is split), the register section of `CLAUDE.md` at most 6,000, and every headline
in one place only.

## The areas

- [Endpoints and the wire](endpoints.md)
- [RomM HTTP](romm-http.md)
- [Install and update](install-update.md)
- [The resolver](resolver.md)
- [Library sync: the run](library-sync.md)
- [Library sync: skips and stamps](library-sync-skips.md)
- [Library sync: representatives and removals](library-sync-removals.md)
- [Firmware: answers and verdicts](firmware.md)
- [Firmware: files, links and Tender's folder](firmware-files.md)
- [Save-file sync](saves.md)
- [Removed-game cleanup](prune.md)
- [The host and loading the panel](host.md)
- [Bundles and the start-up check](startup.md)
- [The panel: Quick Access and the game page](panel.md)
- [The desktop surface](desktop.md)
- [Code structure](code-structure.md)
- [Cross-cutting](cross-cutting.md)
