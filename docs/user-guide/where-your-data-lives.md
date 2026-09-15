# Where Your Data Lives

Tender keeps everything it knows about your library in folders under your own home directory:

| Folder                        | What is in it                                                                                                                  |
| ----------------------------- | ------------------------------------------------------------------------------------------------------------------------------ |
| `~/.config/romm-tender/`      | Your settings — server address, sign-in, which platforms and collections you sync                                              |
| `~/.local/share/romm-tender/` | The library database, playtime and save-sync state — and the small `bin/rom-launcher` file every Steam shortcut starts through |
| `~/.cache/romm-tender/`       | Cached cover art and artwork                                                                                                   |
| `~/.local/state/romm-tender/` | Tender's log file                                                                                                              |

Your **games** are not in any of them. Downloaded ROMs, BIOS files and save files live in RetroDECK's own folders,
exactly as before, and nothing on this page moves them.

## Why the cache is separate

The cache folder holds only things Tender can fetch again — cover art and artwork come back from your RomM server on the
next sync. The `~/.local/share` folder holds the things it cannot: your library database is the only record of what you
have installed, synced and chosen.

That split is the whole point of having two folders. If you are short of space, or something looks wrong with a cover,
you can delete `~/.cache/romm-tender/` and lose nothing but a re-download. Deleting `~/.local/share/romm-tender/` is a
different matter entirely.

## Why It Moved

Earlier versions kept this data inside Decky's plugin folders. Decky names those folders after the plugin's own folder
name, and that name is not something Tender chooses — so when the project was renamed at version 0.31.0, every user's
data quietly moved with it, and a freshly updated Tender opened on an empty library while everything was still sitting
in the old folder.

Tender now runs as its own program rather than as a plugin inside Decky, and it is **told** where these folders are when
it is installed. Nothing derives them from a folder name any more, so no rename can move them again.

## Settings and the database are separate

They are separate files in separate folders, and they are read independently. Your settings surviving a problem with the
database, or the other way round, is normal rather than a sign that something went half-finished.

## If Decky still lists an older plugin

If Decky lists **"RomM Sync"** or an older **Tender** plugin, those are separate installs with their own copies of your
data. Removing one before your shortcuts have been repointed can stop your games from starting — see
[Updating from a release before 0.31.0](getting-started.md#updating-from-a-release-before-0310).
