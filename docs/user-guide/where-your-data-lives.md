# Where Your Data Lives

Tender keeps what it knows about your library in folders under your own home directory, each named after Tender itself:

| Folder                        | What is in it                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| ----------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `~/.config/romm-tender/`      | Your settings — server address, sign-in, which platforms and collections you sync                                                                                                                                                                                                                                                                                                                                                                                                    |
| `~/.local/share/romm-tender/` | The library database, and playtime and save-sync state; the copy an update made of the database and your settings, in `update-backup/`, and the one going back by hand made, in `rollback-backup/`                                                                                                                                                                                                                                                                                   |
| `~/.cache/romm-tender/`       | Cached cover art and artwork                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| `~/.local/state/romm-tender/` | Tender's log file, `backend.log`; `update-failure.json` after an update that was rolled back, or that the installer's pre-install check refused; `update-attempt.json` from the moment an update from Settings starts its installer until Tender knows how it went — right away when the running Tender sees the installer fail, otherwise once Tender has started again — and after one whose installer stopped Tender and then gave up, until you dismiss that notice or try again |

Four more places sit outside those folders, because none of them holds anything of yours:

| Where                              | What it is                                                                                                                                                                                                                                    |
| ---------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `~/.local/bin/tender-rom-launcher` | The small program every one of your Steam shortcuts starts through. It sits in the folder your own programs go in, shared with anything else you installed for yourself, and uninstalling Tender leaves it there so your games keep launching |
| `~/.local/lib/romm-tender/`        | Tender itself, with the installer that updates it and puts the previous version back (`install.sh`). Replaced whole when you update, removed when you uninstall                                                                               |
| `~/.local/lib/romm-tender.old/`    | The version your last update replaced, kept so that it can be put back. Each update replaces it; going back puts it in place of the current version, after which there is none. Uninstalling removes it                                       |
| `/run/user/<id>/romm-tender/`      | One note saying which port Tender is answering on while it runs. Your session clears it when you log out                                                                                                                                      |

Your **games** are not in any of them. Downloaded ROMs, BIOS files and save files live in RetroDECK's own folders,
exactly as before, and nothing on this page moves them. The one folder outside RetroDECK's that can hold copies of them
is Tender's own folder below.

## Tender's folder

`Tender/` is the folder Tender keeps its own files in, and it holds things of yours. Today that is `recovery/`: the
recovery bundles **Clean Up Removed RomM Games** seals before it removes anything, so that what the bundle recorded can
be put back by hand. What a bundle holds and how to restore from it is under
[Cleaning up versions removed from RomM](managing-games.md#cleaning-up-versions-removed-from-romm). Later versions keep
the game library, the firmware store, saves and save states beside it.

It lies in `~/Tender` unless you choose another place under **Settings › Tender folder**: the internal storage, the root
of any writable drive mounted under `/run/media/<user>/` (an SD card, a USB drive), or a folder you type in. Tender
always puts its folder `Tender/` inside the place you choose. A place is refused where `Tender/` would lie inside an
emulator source's ROM folder, or inside the folder a source keeps a system's games in — even where that folder is a link
to another drive — or would hold a ROM folder; and while Tender cannot read the emulator sources, or a source cannot
name its folders, a place you choose is refused for that reason. Choosing a place switches where Tender keeps its files
from then on; it does not move what is already in the old folder, and leaves that folder where it is unless it holds
nothing but Tender's own `.tender-folder` file.

Tender creates the folder when it starts, when it is missing and its drive is there, and whenever you choose a place. It
writes a `.tender-folder` file into `Tender/` whenever it creates it. It never removes a bundle, and uninstalling Tender
leaves the folder where it is.

### When the folder cannot be reached

Once Tender has recorded something it keeps there, the folder must be reachable: its drive connected, the folder present
and writable. While it is not, Main says so — naming the folder and the reason — and removes nothing. Its two ways out
are **Choose another place**, which opens the setting, and **Forget what is recorded there**, which, after asking,
forgets what Tender recorded under that folder and deletes no file. With nothing recorded there, no notice is shown: a
missing folder is simply created again the next time Tender writes into it, and a write to a drive that is gone is
refused with the reason.

If your settings are reset while Tender's files are recorded in another folder than the one they name, Main says that
too, removes nothing, and offers **Use that folder** — which points the setting back at it — or **Forget what is
recorded there**.

### Older recovery folders

Bundles that version 0.33 sealed sit in `~/romm-tender-recovery/`, and those of versions 0.30 to 0.32 in
`~/decky-romm-sync-recovery/`. Both stay where they are, are not counted on the Data Management page, and are yours to
keep or delete — [Recovery bundles](troubleshooting.md#recovery-bundles) has the details.

## What an update keeps

Before an update replaces Tender, it stops it and copies your library database and your settings to
`~/.local/share/romm-tender/update-backup/`, replacing the copy the previous update made. Uninstalling leaves it there,
with the rest of your data. A previous copy that cannot be removed is left beside the new one as `update-backup.prev/`;
the next update removes it first, and does not go ahead until it can. An update interrupted while replacing the copy can
leave it only as `update-backup.prev/`; the next update, and going back by hand, move it back first.

If the new version has not started within about a minute, the installer puts the previous version and that copy back and
starts it again — so anything the new version recorded in that minute is gone, and nothing from before it is. It also
leaves `~/.local/state/romm-tender/update-failure.json` saying which version it tried, which one it went back to, and
when; the next update whose new version answers removes it. Tender reads that note to tell you on its main panel, and
never changes or removes it itself. [Troubleshooting](troubleshooting.md#an-update-was-rolled-back) has what to do next.

Before it stops anything, the installer runs the new version's pre-install check, which tries it on copies of the
database and settings made in a temporary directory the installer removes again. Where the new version cannot be built,
or crashes the check, nothing is stopped or replaced, and the same note is left marked as refused rather than rolled
back (`"kind": "check"`), naming the version it tried and the one still installed.
[Troubleshooting](troubleshooting.md#the-new-version-does-not-start) has what to do next.

An update started from **Settings › Updates** leaves a note of its own,
`~/.local/state/romm-tender/update-attempt.json`, written by Tender right before it starts the installer: which version
it tried, which one it was running, and when. The installer never touches it. An installer that fails while Tender is
still running is reported there and then, and Tender removes the note itself. Otherwise, when Tender next starts, the
note tells it how that update ended. Running the new version, or after the installer rolled that update back or refused
it, the note is removed. Running the same version as before, with no such note from the installer, the installer stopped
without updating — Tender says so on its main panel and removes the note once you dismiss that, press **Try again**, or
run another version.

Going back to the previous version by hand (`install.sh --rollback`) puts back that same copy, but first copies the
database and settings it is about to replace to `~/.local/share/romm-tender/rollback-backup/`, replacing the copy the
previous time you went back made, with a previous copy that cannot be removed left as `rollback-backup.prev/` the same
way. Updates leave that folder alone, and so does uninstalling.

## The split that matters

The database folder and the cache folder are separate on purpose. Everything in `~/.cache/romm-tender/` can be fetched
again from your RomM server — delete it and Tender re-downloads the covers as it needs them. Nothing in
`~/.local/share/romm-tender/` can: that folder holds the only record of what you have installed, synced and chosen.

So a cleanup tool that empties caches is safe to point at the first folder and never the second.

## Why they are named after Tender

Earlier versions kept this data inside Decky's plugin folders. Decky names those after the folder it installed Tender
into, and that name is not something Tender chooses — so when the project was renamed at version 0.31.0, every user's
data moved with it, and a freshly updated Tender opened on an empty library while everything was still sitting in the
old folder.

Naming the folders after Tender ends that. They no longer depend on how Tender happens to be packaged, so a rename
cannot move them again.

## If your folders are somewhere else

The folders above are the defaults. Tender asks its environment first, so an installer that sets `TENDER_CONFIG_DIR`,
`TENDER_DATA_DIR`, `TENDER_CACHE_DIR`, `TENDER_STATE_DIR`, `TENDER_CODE_DIR` or `TENDER_BIN_DIR` decides where they go;
failing that it follows the standard `XDG_*` variables — there is none for the launcher's folder and none for Tender's
own, so those two are either their `TENDER_*` variable or the default — and only then falls back to the paths in the
tables above.

The installer settles all of them once and writes them into Tender's service file, so

```bash
systemctl --user cat romm-tender
```

prints the answers this install is running on, one `Environment=` line each. Tender also resolves them at every start
and logs the program, database and cache folders, so the **last** `host: code …` line in `backend.log` says the same
thing. Look for the last one rather than the first: the log is appended to across runs, so the top of the file belongs
to an older start.

Tender's folder follows none of the `TENDER_*` or `XDG_*` variables: it lies where **Settings › Tender folder** says,
`~/Tender` unless you chose another place, wherever the folders above have gone.

## Coming from an older version

Nothing copies your data forward. If you used a version that stored its library inside Decky's plugin folders, that
library stays there and Tender starts with an empty one — set it up as if it were new.

Settings in `~/.config/romm-tender/` that 0.30 or later wrote are used as they are. A settings file there from an older
release — copied in by hand from `~/homebrew/settings/<folder>` — is not: Tender starts on its default settings, notes
in `backend.log` what it found, and writes the defaults over that file. Sign in and choose your platforms and
collections again.

The old folders are yours to keep or delete:

- `~/homebrew/settings/<folder>` — the settings that install used
- `~/homebrew/data/<folder>` — its library, covers and artwork

`<folder>` is `decky-romm-sync` or `romm-tender`, depending on which version wrote them. Tender reads neither, so
deleting them frees the space and changes nothing; leaving them alone is equally fine.

!!! warning "The older plugin itself is a separate question"

    If Decky still lists an older **"RomM Sync"** plugin, that is not the same thing as the folders above. Your Steam
    shortcuts may still start through a file inside it, and removing it before they have been repointed stops your games
    from launching. Check one of your games first —
    [Updating from a release before 0.31.0](getting-started.md#updating-from-a-release-before-0310) has the check.
