# Invariant register

The cross-cutting safety rules — the ones that span files, so no diff-scoped review (human or agent) sees the whole rule
— each with its enforcement tier. It is a **map of the enforcement surface, not the enforcement itself**. What the tiers
mean, who reads which file and when, and which rules stand in `CLAUDE.md` instead of here is the register section of the
repository's `CLAUDE.md`, which also holds the map from area to file.

Every other entry stands once, in the file of the area it belongs to; an entry that concerns two areas stands in one of
them, and the other points to it.

## How an entry reads

Format: **headline** — tier — enforced by.

An entry binds as a whole. The bold headline names the rule. Where the rule is longer than its headline, the paragraph
directly under the headline holds the rest of it and binds the same way. What follows — what enforces the rule, what
that does not see, why the rule exists, the tests that pin it — stays what it is.

What holds the register to its size and every headline to one place is its own entry in
[Cross-cutting](cross-cutting.md); the limits are in the [development guide](../../contributing/development.md).

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
