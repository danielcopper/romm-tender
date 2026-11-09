# Invariants: Firmware: files, links and Tender's folder

Tender's folder, where a BIOS link may be placed or removed, and which BIOS files may be deleted. Answers and verdicts
are in [firmware](firmware.md); which RetroDECK folders a download or removal may use is an entry of
[the resolver](resolver.md). How an entry reads and binds, and the other areas: [the register's index](index.md).

- **Tender's folder is unreachable only while a store has recorded something under it; only its owner,
  `services/tender_folder.py`, creates it, writing its `.tender-folder` id file into `Tender/` whenever it creates it**
  — test + prompt-only

  And every write into it is made ready through `TenderFolderService.do_prepare_for_write`, which refuses
  `folder_unreachable` where the drive is gone, the folder cannot be written, or it is missing while something is
  recorded under it

  The folder (`domain/tender_folder.py`, `services/tender_folder.py`) lies on a drive that can be taken out, and the two
  ways of being wrong about that cost differently. Calling a folder with nothing recorded under it unreachable would put
  a notice on Main and pause work for a drive the user has simply not inserted yet, so the condition counts only where a
  store answers that it recorded something there (`TenderFolderRecordReader`); with nothing recorded, a missing folder
  is created again by the next write. Writing into a place without asking would land on the internal drive under an
  empty mount folder, or fail half-way, so every write goes through `do_prepare_for_write`, which creates a missing
  folder only where nothing is recorded under it and refuses `folder_unreachable` where the drive is gone (told by the
  stored mount point) or the folder cannot be written. Creating the folder is the owner's alone because the owner writes
  `.tender-folder` into `Tender/` whenever it creates it, holding the id it keeps in `kv_config`: the recovery bundle
  adapter opens the folder it is handed and never creates it
  (`tests/adapters/test_recovery_bundle.py::test_a_seal_never_creates_tenders_folder`), and the cleanup asks the owner
  before each seal
  (`tests/services/prune/test_service.py::test_a_bundle_on_a_folder_that_cannot_be_reached_fails_its_group_with_the_reason`).
  `tests/services/test_tender_folder.py` pins the rest: `TestTheState` (unreachable only with records, for each of the
  three conditions), `TestCreatingTheFolder` (start, id file, a drive that is gone, a recorded folder),
  `TestPreparingAWrite` and `TestTheFolderRule`, with `tests/domain/test_tender_folder.py::TestReachability`. The
  firmware store is the first store that records something under the folder: `FirmwareStore` answers
  `TenderFolderRecordReader` off its `firmware_store` rows, forgets its records under a folder by removing Tender's
  links to those files — except one under a root the resolver only assumed or whose source's roots could not be asked —
  and deleting no file, and every firmware use case that writes into the store or links into it names the folder rule
  (`tests/services/test_firmware_store.py::TestTendersFolderRecords` and `::TestFolderRule`). That it makes every write
  ready through `do_prepare_for_write` is prompt-only: its tests hand it a stub in that function's place. Prompt-only: a
  new store that writes there must call `do_prepare_for_write` first, answer `TenderFolderRecordReader` and join the
  `records` tuple in `bootstrap/services.py`, and a use case that touches the folder's files names the folder rule
  (`folder=True`); a recovery bundle is never a record — Tender lists bundles from the folder and keeps no record of
  them
- **No link is placed at a place the resolver could not look at (`firmware-path-inaccessible`)** — test + prompt-only —
  nobody could see what lies there, so a link could not be judged against it, and a place the resolver cannot read is
  usually one the emulator cannot read either (#2190). The row's verdict is withheld, grey, and its sentence names the
  place ("Tender could not look into bios/kronos/, so whether saturn_bios.bin is there is not known"). The backend reads
  it as one property, `FirmwarePlacement.place_unreadable`, and four callers ask it:
  `domain/firmware_groups.py::fetched_as_required` (so `Download required` neither counts nor fetches it), and
  `domain/firmware_places.py::place_refusal`, which the link update (`services/firmware/store.py`), the rows
  (`services/firmware/rows.py`) and "Replace…" (`services/firmware/replacement.py`) call and whose `unreadable` refusal
  none of them passes; the frontend's `isFetchable` reads the same caveat code and that refusal, so neither page points
  at the row as something to download. A download itself lands in Tender's firmware store, never at the place, so the
  place's reading decides only the link. Pinned by `tests/domain/test_firmware_places.py`,
  `tests/domain/test_firmware_groups.py`,
  `tests/services/test_firmware.py::TestAPlaceThatCouldNotBeLookedAt::test_a_download_places_no_link_there`,
  `frontend/src/utils/biosFetchable.test.ts`; the resolver's spelling of the code by
  `tests/adapters/test_atlas_firmware.py::TestVocabularyConformance`. Unseen: a new path that places a link without
  `place_refusal`, and the frontend's copy of the code, which nothing holds equal to the backend's. Once the place is
  readable again, the verdict and the link follow by themselves
- **A BIOS file is deleted only where Tender holds a record of it, and only at the path that record holds: a store file
  of the platform with every link of Tender's to it, or a download made into an emulator source's folder before the
  store, at its `downloaded_bios` path** — test + prompt-only

  A link only while it still points at its store file and its place may be touched

  `tests/services/test_firmware_store.py::TestDeleting` pins the store's side end-to-end over a real folder tree — the
  store file and both of its links go, a file the user placed at a place stays, a link under a root the resolver only
  assumed stays with its record and goes at the next link update once the root is read, and an older download stays
  where it is and its Delete removes it there — and `tests/services/test_firmware.py::TestDeletePlatformBios` /
  `::TestDeleteOneBiosFile` the older downloads': an emulator-shipped file survives, a hand-placed file under a server
  file's name survives, a download is removed once RomM no longer holds it, and a per-row delete takes only the record
  it names. What makes the destructive-op rule (the backup-or-confirm rule `CLAUDE.md` keeps) concrete for BIOS files is
  that authority to delete comes from having placed the file, and the records are the only evidence of that: a store
  record is written when a file reaches the store (`StoredFirmware.mark_downloaded`), a link record when Tender places a
  link (`FirmwareLink.mark_placed`), and a `downloaded_bios` record was written by the download path before the store
  existed and is no longer written at all ([#2191](https://github.com/danielcopper/romm-tender/issues/2191) D12). Two
  writers move an existing older record without making a new one: the RetroDECK home migration (`BiosFile.relocate`) and
  the refiling (`services/firmware/refiling.py`). A link is removed only while it still points at the store path its
  record names, so a link someone replaced by a file of their own is left alone; and only where its place may be touched
  — under a root the resolver read, never under one it only assumed or of a source whose roots could not be asked (D11,
  D20) — while an older download is removed only inside the BIOS folder RetroDECK names now: one whose file is gone
  loses its record, one Tender may not reach keeps it while RetroDECK's settings cannot be read, RetroDECK is not
  installed or it names no BIOS folder (D35), and one outside that folder loses it and stays (D29). So the records are
  the delete's whole input: it iterates them, not a status listing. The authorisations a reader reaches for instead are
  wrong in opposite directions. `downloaded` is the resolver's presence at the row's place, equally true of firmware
  RetroDECK ships with its own components, which no RomM library holds and nothing here can fetch back — an older delete
  authorised on presence did exactly that to `<bios>/dolphin-emu/Sys/codehandler.bin` on a real device. `on_server`
  describes what the library holds _now_, not who wrote the file: authorise on it and a file dropped from RomM after we
  downloaded it is stranded on disk. The count the UI offers is bound to the same set: `deletable_count` on the
  `get_platform_firmware_status` payload counts every file of this platform's store that Tender has a record of and that
  is on disk — a copy only another emulator uses and a second copy of the same bytes included — and the older downloads
  still on disk, one a Delete could not reach included (#2191 D29;
  `tests/services/test_firmware.py::TestGetFirmwareStatusDeletableCount`); each row carries the name its Delete is
  addressed by (`delete_name`) — its store file, the older download of the library file that fills it, or the older
  download lying at its own place — and `delete_name` and `deletable_count` alone gate a row's Delete offer. **Both
  buttons reach one removal** (`PlatformBiosDeleter._delete_io` over `FirmwareStore.do_remove`, under a name selection
  per button): a second copy of that loop is the shape this rule is about, because the copies would drift silently.
  **Nothing mechanical stands behind any of this.** A delete path looping a status list on `downloaded` alone would go
  green — which is exactly the shape the older one had when it destroyed that file
- **A BIOS link goes only to a place the resolver states, for an emulator its source does not state not installed on
  every entry it lists it under (`FirmwareCatalogue.linkable`), in every enabled source, a sealed catalogue's places
  included while its rows stay withheld** — test + prompt-only

  The link update removes a link of Tender's that still points at its store file only in a source whose list it read
  whole — never a sealed one — and whose roots it may touch, at a place no emulator that source does not state not
  installed wants any more, for this platform or for any other whose ids are kept — each one RomM listed at the last
  sync, and any kept since — or that Tender holds store files for; it keeps the link where no listing was ever kept or
  such a platform or its source could not be read, and the next update asks again; an installed emulator whose wants the
  resolver could not read wants no place, since the resolver states none; a link whose store file is gone goes wherever
  its place may be touched

  `tests/services/test_firmware_store.py::TestOnlyAnInstalledEmulatorIsLinkedFor` (no link for an emulator stated not
  installed; a link nobody wants removed; a link the user put there, one under a root only assumed, one in a source not
  read and one a sealed list leaves out left alone; its row refusing "Replace…" with the refusal `not_installed`),
  `::TestALinkAnotherPlatformMayStillWant` (a link another platform's emulator still wants kept — a platform RomM listed
  without a store file of Tender's, a sealed list that states the place, a platform whose system is switched off, the
  images a folder another platform's emulator reads recognises — and kept where the listing was never kept, a platform's
  system could not be asked or a source could not be read for its system; removed once no platform wants it, a platform
  with no system wanting nothing; each system read once and the asking stopped once every link is known to be wanted;
  the same through a download and a replacement),
  `::TestBringingLinksUpToDate::test_a_link_of_tenders_with_no_store_copy_left_is_removed` (a link whose store file is
  gone) and `tests/adapters/test_atlas_firmware.py::TestEverySourceItIsPlacedIn` (the launch reading's `not-installed`
  is the only exclusion, and only where every entry of the emulator says it — `unestablished` and a reading that raised
  rule nothing out; a sealed catalogue is read for its places and marked sealed while the answering read of one stays
  withheld; a source whose reading raised or named no root is named unread). Why: a packaged card states what a
  standalone emulator wants whether or not it is installed, so the firmware reading alone cannot keep a link from an
  emulator that is not there; the launch reading (`emulators_for(system)`, each entry's `availability`) is the source's
  own statement. A place the resolver states is a place a link may go even beside a sealed catalogue: what the sealed
  list leaves out is a link not placed, while reading it as complete — for rows and verdicts, or for deciding that a
  link is unwanted — would misstate what is needed. One place can serve two platforms (D19), and a platform need hold no
  file of Tender's to want a place, so a link one platform no longer wants stays while another platform's emulator in
  the same source wants its place, and a platform or a source that could not be read is never read as wanting nothing.
  An installed emulator whose wants the resolver could not read (`unread_emulators`) is not such a case: it wants no
  place, because the resolver states none for it, while an emulator its source does not state not installed wants every
  place the resolver states for it ([#2191](https://github.com/danielcopper/romm-tender/issues/2191) D36, D37, D40, D42,
  D43). Prompt-only: a new placing path reads `linkable()`, never `placements`; `not_installed` comes only from the
  source's own launch reading; a link of Tender's that still points at its store file, in a source switched off or not
  read, stays whatever that source's list would say; a link whose store file is gone goes in any source, not only the
  one the test places it in; an emulator in `unread_emulators` wants no place only because no placement names it —
  nothing pins that no placing or removal path reads that set
