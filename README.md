<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/lockup-animated-dark.gif">
  <img src="assets/lockup-animated.gif" alt="Tender" width="300">
</picture>

<h3>Your RomM library, running native in Steam</h3>

[Getting Started](https://danielcopper.github.io/romm-tender/user-guide/getting-started/) ·
[Configuration](https://danielcopper.github.io/romm-tender/user-guide/configuration/) ·
[Syncing](https://danielcopper.github.io/romm-tender/user-guide/syncing-your-library/) ·
[Managing Games](https://danielcopper.github.io/romm-tender/user-guide/managing-games/)

[BIOS &amp; Cores](https://danielcopper.github.io/romm-tender/user-guide/bios-management/) ·
[Save Sync](https://danielcopper.github.io/romm-tender/user-guide/save-sync/) ·
[Troubleshooting](https://danielcopper.github.io/romm-tender/user-guide/troubleshooting/)

<a href="https://danielcopper.github.io/romm-tender/"><img alt="Documentation" src="https://img.shields.io/badge/user%20guide-read-4795c9?style=for-the-badge&labelColor=16202c"></a>
<a href="https://github.com/danielcopper/romm-tender/releases/latest"><img alt="Release" src="https://img.shields.io/github/package-json/v/danielcopper/romm-tender?style=for-the-badge&label=release&color=4795c9&labelColor=16202c"></a>
<a href="https://github.com/danielcopper/romm-tender/stargazers"><img alt="Stars" src="https://img.shields.io/github/stars/danielcopper/romm-tender?style=for-the-badge&color=4795c9&labelColor=16202c"></a>
<a href="https://github.com/danielcopper/romm-tender/releases"><img alt="Downloads" src="https://img.shields.io/github/downloads/danielcopper/romm-tender/total?style=for-the-badge&color=4795c9&labelColor=16202c"></a>
<a href="https://github.com/rommapp/romm/releases"><img alt="Requires RomM 4.9.0 or newer" src="https://img.shields.io/badge/RomM-%E2%89%A5%204.9.0-4795c9?style=for-the-badge&labelColor=16202c"></a>

</div>

A [Decky Loader](https://decky.xyz/) plugin that syncs your self-hosted [RomM](https://github.com/rommapp/romm) library
into Steam as non-steam shortcuts. Games appear directly in your Steam library, launch through
[RetroDECK](https://retrodeck.net/), and can keep their saves in sync across devices through your RomM server.

_Named after the railway car behind a steam locomotive, or the boat that shuttles out to a ship at anchor._

> **Pre-1.0 (v0.x).** The feature set isn't complete yet. Save sync covers standard cartridge saves; the memory-card
> systems — PlayStation, PS2, Dreamcast, GameCube, PSP, 3DS — don't sync their saves yet, and the
> [support matrix](https://danielcopper.github.io/romm-tender/user-guide/save-sync-support-matrix/) has the per-system
> detail.

---

> [!NOTE]
> **How this is built.** The code is written by an AI coding agent working under my direction. The architecture, the
> design decisions and the review are mine, and changes are smoke-tested on real hardware before they ship.

## Features

- **Library sync** — Pulls the platforms and collections you pick from your RomM server into Steam as non-steam
  shortcuts with RomM cover art; later runs are incremental and preview what will change before touching anything
- **SteamGridDB artwork** — Add a free [SteamGridDB](https://www.steamgriddb.com/) key to get hero banners, logos, wide
  capsules and custom icons, with a manual picker for games that don't match automatically
- **Save sync** — Opt-in save syncing across devices through your RomM server, automatically before launch and after you
  quit; identical saves resolve silently, and if both sides genuinely changed you decide which one wins
  ([not every system syncs yet](https://danielcopper.github.io/romm-tender/user-guide/save-sync-support-matrix/))
- **Save slots & version history** — Multiple named save profiles per game, plus per-file version history with restore
- **ROM downloads** — Download on demand with progress, pause/resume/cancel, and a managed queue
- **BIOS management** — Per-platform BIOS status, download all or only what your active core requires, hash-verified
  against a bundled registry, and delete them again when you're done
- **Game detail page** — Replaces Steam's page for synced games: RomM metadata, RetroAchievements progress, playtime,
  install and BIOS status, save management, and per-game actions
- **Multi-disc & multi-version** — Pick the disc for multi-disc games and switch between regions or revisions of the
  same game, right from its Steam page
- **Emulator cores** — Set the core per system, or override it for a single game
- **Steam Input** — Pick a Steam Input mode (Default / Force On / Force Off) and apply it to every shortcut the plugin
  created
- **RetroArch input fix** — Spots the `input_driver` value that breaks controller navigation in RetroArch's menus and
  repairs it in one tap
- **Follows RetroDECK moves** — Moved RetroDECK to another drive? The ROMs, BIOS files and saves the plugin manages are
  relocated and your shortcuts repointed — nothing needs re-downloading
- **Cleanup tools** — Remove shortcuts per platform or all at once, uninstall ROMs, and clear orphaned grid images

## Screenshots

|                Quick Access panel                |                    Game detail page                    |
| :----------------------------------------------: | :----------------------------------------------------: |
| ![Quick Access panel](assets/screenshot-qam.jpg) | ![Game detail page](assets/screenshot-game-detail.jpg) |
|               **BIOS management**                |                  **Per-game actions**                  |
|    ![BIOS status](assets/screenshot-bios.jpg)    |     ![Actions menu](assets/screenshot-actions.jpg)     |

## Requirements

- [Decky Loader](https://decky.xyz/) on your Steam Deck or Linux HTPC — the plugin lives in Steam's gamepad UI, so it
  works in the Deck's Game Mode **or** in Big Picture Mode on any Linux PC
- A running [RomM](https://github.com/rommapp/romm) server, **version 4.9.0 or newer** (the plugin stays inert against
  older servers)
- [RetroDECK](https://retrodeck.net/) for launching games

## Installation

**Not on the Decky store.** The store doesn't accept plugins whose code is written with AI assistance, and Tender's is —
see _How this is built_ above. Install it from the URL below.

```text
https://github.com/danielcopper/romm-tender/releases/latest/download/Tender.zip
```

<details open>
<summary><b>Install from URL</b> — the recommended way</summary>

Needs **Developer mode** in Decky Loader (Decky tab → gear icon → **General → Other** → toggle **Developer mode**).

1. Decky settings → **Developer** tab → **Install Plugin from URL**
2. Paste the URL above and install

That URL always resolves to the newest release.

Tender is not in Decky's plugin store, so Decky's own update detection never fires for it — Tender checks GitHub once a
day itself and shows a card in its QAM panel when a newer release is out, with a button that hands the update to Decky.
Pasting the URL again still works and is the fallback if that button ever cannot reach Decky's installer. Close any
running game before updating: the update restarts the plugin, and that session's play time is lost.

</details>

<details>
<summary><b>Install from ZIP</b></summary>

Also needs **Developer mode**.

1. Download the plugin's `.zip` asset from the [releases page](https://github.com/danielcopper/romm-tender/releases)
2. Decky settings → **Developer** tab → **Install Plugin from ZIP File**

</details>

> Full step-by-step instructions, including first-time setup, are in
> [Getting Started](https://danielcopper.github.io/romm-tender/user-guide/getting-started/).

## Quick start

1. Open the Quick Access Menu and select **Tender**
2. In **Settings**, enter your RomM server URL and credentials, then hit **Test Connection**
3. In **Platforms**, enable the platforms you want to sync
4. Open **Sync** and hit **Sync Library** — look over the changes it works out, then **Apply Sync**, and your ROMs
   appear as non-steam shortcuts

See the [User Guide](https://danielcopper.github.io/romm-tender/user-guide/syncing-your-library/) for syncing details,
[save sync](https://danielcopper.github.io/romm-tender/user-guide/save-sync/), and
[BIOS management](https://danielcopper.github.io/romm-tender/user-guide/bios-management/).

## Contributing

Build from source, run the tests, and read the architecture reference on the documentation site:

- [Development setup](https://danielcopper.github.io/romm-tender/contributing/development/)
- [Frontend dev loop](https://danielcopper.github.io/romm-tender/contributing/frontend-dev-loop/) — live-reload the UI
  into a windowed Big Picture on the Deck, no Game Mode switching
- [Backend architecture](https://danielcopper.github.io/romm-tender/architecture/backend-architecture/)

[![CI](https://github.com/danielcopper/romm-tender/actions/workflows/ci.yml/badge.svg)](https://github.com/danielcopper/romm-tender/actions/workflows/ci.yml)
[![Quality Gate](https://sonarcloud.io/api/project_badges/measure?project=danielcopper_romm-tender&metric=alert_status)](https://sonarcloud.io/summary/new_code?id=danielcopper_romm-tender)
[![Coverage](https://img.shields.io/sonar/coverage/danielcopper_romm-tender?server=https%3A%2F%2Fsonarcloud.io)](https://sonarcloud.io/summary/new_code?id=danielcopper_romm-tender)
[![Maintainability](https://sonarcloud.io/api/project_badges/measure?project=danielcopper_romm-tender&metric=sqale_rating)](https://sonarcloud.io/summary/new_code?id=danielcopper_romm-tender)
[![Reliability](https://sonarcloud.io/api/project_badges/measure?project=danielcopper_romm-tender&metric=reliability_rating)](https://sonarcloud.io/summary/new_code?id=danielcopper_romm-tender)
[![Security](https://sonarcloud.io/api/project_badges/measure?project=danielcopper_romm-tender&metric=security_rating)](https://sonarcloud.io/summary/new_code?id=danielcopper_romm-tender)

## Acknowledgments

This plugin stands on the shoulders of some great projects:

- [RomM](https://github.com/rommapp/romm) — the self-hosted ROM manager at the heart of this plugin. RomM provides the
  library, metadata, cover art, and save file storage that makes the entire sync experience possible
- [RetroDECK](https://retrodeck.net/) — the all-in-one emulation solution for Steam Deck that bundles ES-DE, RetroArch,
  and standalone emulators into a single flatpak. Our entire launch chain runs through RetroDECK
- [Decky Loader](https://decky.xyz/) — the plugin framework that makes all of this possible
- [Valve](https://www.valvesoftware.com/) — for the Steam Deck, SteamOS, and an open enough platform to build on
- [Unifideck](https://github.com/ma3ke/unifideck) — inspiration for game detail page injection techniques and gamepad
  navigation patterns
- [MetaDeck](https://github.com/EmuDeck/MetaDeck) — inspiration for store patching patterns used in metadata display on
  non-Steam shortcuts
- [Argosy](https://github.com/rommapp/argosy-launcher) — RomM's Android device-sync client. Its baseline-anchored
  save-conflict handling — client-side detection layered over RomM's negotiate transport, with a keep-local/keep-remote
  prompt on genuine divergence — validated the posture this plugin's save sync takes
- [Grout](https://github.com/rommapp/grout) — RomM's Linux handheld client. Its 409-driven upload reconciliation (POST
  with overwrite=false, downgrading to a download when the local save is unchanged and surfacing a conflict when it
  diverged) informed this plugin's negotiate upload and conflict path

## License

GPL-3.0. This is an independent project and is not affiliated with, endorsed by, or sponsored by the RomM project or
Valve Corporation.
