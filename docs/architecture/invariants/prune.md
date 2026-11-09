# Invariants: Removed-game cleanup

The cleanup's claims and leases, its frames, the installed-content mutations it authorizes, and the RomM proofs it rests
on. Its recovery bundles live in Tender's folder, whose rule is in [firmware-files](firmware-files.md); the
backup-or-confirm rule stands in `CLAUDE.md`. How an entry reads and binds, and the other areas:
[the register's index](index.md).

- **A prune run's claim reservation and its refusal of every conflicting endpoint happen in one atomic hold of the prune
  conflicts' lock (the preview rebuild does not), and frontend-owned Steam work holds a heartbeated lease through every
  continuation's final write** — test + prompt-only

  And a continuation with an owner is tombstoned by its owner's teardown

  Prune service and prune conflicts race tests + contract endpoint-entry matrix
  (`tests/contract/test_conflict_refusals.py`), and `frontend/src/utils/pruneLease.test.ts` for the heartbeat and the
  owner's tombstone; new conflicting entry points are prompt-only
- **A removed-game cleanup's run claim is registered on the prune conflicts before the start's reservation is given
  back, so the two windows overlap and no conflicting endpoint runs in a gap between them** — test + prompt-only —
  `tests/services/prune/test_service.py::test_a_started_run_holds_its_run_claim_until_it_ends` and
  `tests/contract/test_prune.py::test_a_cleanup_refuses_conflicting_endpoints_from_its_start_to_its_end`. A gap would
  let a conflicting endpoint change local state after the start revalidated its preview and before the run acts on it.
  The reservation is taken by `hold_start` in `PruneService.start_prune`, which gives it back once the block around the
  whole start ends; the run claim is registered in the second hold of the prune service's own lock, where it sets
  `_run_id`. The order holds only because the second happens inside the first's block. Prompt-only: the start's body
  (`_start_prune`) is reached only through that `hold_start` — a caller that reached it another way would skip the
  refusal on held operations and leases, and would run the start's validation with no reservation in front of it, open
  to every conflicting endpoint that could change the local state the refreshed preview is checked against. The service
  test checks that the run is registered by the time `start_prune` returns, which is what keeps the order; the contract
  test holds a real start inside its preview rebuild and checks the refusal during validation and after the start
  returns, and its lifting once the run ends — it does not see a registration moved into the run task's first step,
  because that step runs before the start's caller resumes. Neither sees a second caller
- **A prune frontend action mutates Steam only after atomically claiming its exact run/token/discriminant/binding;
  repeats are idempotent and an outcome lost in transit is ambiguous, never success** — test + prompt-only — prune
  service claim tests + `frontend/src/utils/pruneActions.test.ts`; new action kinds are prompt-only
- **Every installed-content mutation is authorized by a descriptor-relative no-follow claim (root identity, descendant
  identities, and — where a bundle exists — regular-file hashes) revalidated immediately before it, never by a path
  re-lookup** — test + prompt-only

  Refusal, partial mutation and ambiguity are reported, never rewritten into success. The hashes follow the **bundle**,
  not the caller: a source a sealed bundle holds consumes the bundle's digest-bound claim, a source it did not capture
  seals a fresh content-bound one, and a removal with no bundle seals identity-only (`claim_source(..., digest=False)`)
  — the only claim that may adopt interrupted `.{basename}.romm-prune-*` staging, and only where a surviving install row
  proves the path. Content-bound and identity-only claims alike keep staging rename, mount checks, no-follow traversal
  and exact-identity revalidation under writer exclusion across each unlink; a content-bound removal leases the whole
  tree up front, an identity-only directory per unlink, so a writer mid-loop yields a reported partial removal

  The hashes bind a deletion to bytes held somewhere else, so they follow the bundle. A removal with no bundle anywhere
  — recovery off, or a user-initiated uninstall — seals identity-only. The one guarantee that differs is all-or-nothing:
  a content-bound removal leases the whole tree up front, an identity-only directory leases per unlink (a whole-tree
  hold hits `EMFILE` and makes large dumps un-uninstallable), so a writer arriving mid-loop yields a reported partial
  removal instead of a clean refusal. Descriptor-path, recovery-adapter, real RomRemovalService, and prune contract
  tests; new mutation adapters are prompt-only
- **Every prune frame carries its originating preview ID; only a matching pending preview may adopt a run, and an
  accepted contiguous terminal result seals it against every later frame** — test + prompt-only — prune service frame
  tests + `frontend/src/utils/pruneStore.test.ts`; new prune frame types are prompt-only
- **Every destructive RomM proof is bound to one canonical server-origin/token-origin/user namespace from preview
  through every exact-ID request; a namespace change is uncertainty, never a 404 deletion authority** — test +
  prompt-only — prune service namespace-race tests; new destructive RomM proof paths are prompt-only
