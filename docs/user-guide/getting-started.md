# Getting Started

## What is Tender?

Tender is a [Decky Loader](https://decky.xyz/) plugin that connects your self-hosted
[RomM](https://github.com/rommapp/romm) ROM library to Steam. Every game in your RomM library appears as a Non-Steam
shortcut in the Steam Library, complete with cover art, metadata, and collections. Games launch through
[RetroDECK](https://retrodeck.net/).

## Prerequisites

Before installing the plugin, you need:

1. **A RomM server** — a running RomM instance with your ROM library. You'll need the server URL plus a username and
   password to connect the first time. The plugin exchanges those credentials for a RomM Client API Token and stores
   only the token — your password is never saved. Each user should have their own RomM account (see
   [Save Sync](save-sync.md) for why this matters).

2. **RetroDECK** — installed on your Steam Deck or Linux PC. RetroDECK handles the actual emulation. The plugin creates
   shortcuts that launch games through RetroDECK.

3. **Decky Loader** — the plugin framework. Install it from [decky.xyz](https://decky.xyz/) if you haven't already.
   Decky renders inside Steam's gamepad UI, and the plugin works wherever that UI runs: Gaming Mode on the Steam Deck,
   or Big Picture Mode on a Linux PC/HTPC — a dedicated Game Mode session is not required.

4. **A personal RomM account** — save sync ties saves to the authenticated user. Use your own account, not a shared one.

## Installation

**Not on the Decky store.** The store doesn't accept plugins whose code is written with AI assistance, and Tender's is —
see [How this is built](../index.md). Install it from the URL below.

### From Decky's "Install Plugin from URL"

1. Open the Quick Access Menu (QAM) in Gaming Mode by pressing the **...** button
2. Go to the Decky Loader tab (the plug icon) and open settings (gear icon)
3. Under **General → Other**, enable **Developer mode** — a new **Developer** tab appears in the sidebar
4. Open the **Developer** tab and select **Install Plugin from URL**
5. Enter the direct URL to the release zip

   This names one fixed release, `tender-v0.33.0`:

   ```text
   https://github.com/danielcopper/romm-tender/releases/download/tender-v0.33.0/Tender.zip
   ```

   Any other version is named the same way, by its tag:

   ```text
   https://github.com/danielcopper/romm-tender/releases/download/tender-v{VERSION}/Tender.zip
   ```

   `tender-v0.33.0` is the newest release carrying this asset, and releases from `tender-v0.31.0` up to it all carry it
   — `tender-v0.31.0` itself published it as `tender.zip`, and GitHub matches release-asset names case-insensitively, so
   the URL still finds it. Most of the `decky-romm-sync-v{VERSION}` releases before it published `decky-romm-sync.zip`;
   name that tag and that file for those. Four have no asset at all: the three earliest and `v0.2.0`.

6. Decky downloads and installs the plugin automatically — no restart needed

**Tip:** You can also open the [releases page](https://github.com/danielcopper/romm-tender/releases) in Steam's built-in
browser (Gaming Mode → long-press the Steam button → Web Browser), long-press the zip download link, and copy the URL
from there.

Any direct URL to the zip file works (GitHub releases, a self-hosted mirror, etc.) as long as it points to a valid
`.zip` containing the plugin.

### Updating from a release before 0.31.0

Releases up to 0.30.1 install into a folder named `decky-romm-sync`; from 0.31.0 on the folder is named `romm-tender`.
Decky treats a differently-named folder as a different plugin, so updating across that boundary leaves you with **two**
entries: the older one still shown as **RomM Sync**, and the new one as **Tender**.

**Check one of your games before you remove the older plugin.** Every Steam shortcut the older plugin created starts
through a small file inside that plugin's own folder. Tender keeps its copy of that file under your home directory
instead — `~/.local/share/romm-tender/bin/rom-launcher` — and repoints your existing shortcuts at it the next time
Tender loads. Until that has happened, removing "RomM Sync" stops all of your games from starting, and nothing can put
the file back.

The shortcut itself tells you which state you are in, and looking costs nothing:

1. Open any game Tender created in your Steam library and show its **Properties** — the gear icon on the game's page in
   Gaming Mode.
2. Read the **Target** path under **Shortcut**.

- **The path is inside `~/.local/share/romm-tender/bin/`** — your shortcuts no longer depend on the older plugin. Remove
  it wherever Decky lists your installed plugins, or keep it: it costs disk space and nothing else.
- **The path is still inside `homebrew/plugins/`** — leave the older plugin where it is. Tender repoints shortcuts when
  it loads, not when you open its panel, so reload Tender or restart Steam, then look again before you delete anything.

The new install also starts with its own settings and library: each install keeps its data in its own place, and nothing
copies the older one's settings or synced library across. Set Tender up as if it were new, and remove the older plugin
only once the check above passes.

### Manual installation (alternative)

1. Download the plugin's `.zip` asset from the [releases page](https://github.com/danielcopper/romm-tender/releases)
2. Extract the zip to `~/homebrew/plugins/` on your device (via SSH, file manager, or USB)
3. Restart Decky Loader — either reboot, or run `sudo systemctl restart plugin_loader` via SSH
4. The plugin appears in your QAM under the Decky tab

## First-Time Setup

After installation, you need to connect the plugin to your RomM server:

1. Open the QAM and find **Tender**
2. Tap **Settings** in the menu, then **Connections** in the section list on the left
3. Enter your RomM server URL (e.g. `http://192.168.1.100:8080`) — this saves automatically
4. Tap **Sign in**, enter your RomM username and password once, and confirm
5. The **RomM Account** row shows **Signed in** on success. The plugin's main QAM panel has a **Connection** row that
   shows the live connection status whenever you open it — there is no separate test button

On a controller you can confirm a text field with the on-screen keyboard's **Enter** key (or the **R2** shortcut)
instead of navigating to the button — for example save-slot names and the artwork search. In the sign-in dialog, Enter
moves from the username to the password field and confirms only once every required field is filled; an incomplete form
can't be submitted, and **Cancel** leaves your existing sign-in untouched.

The plugin mints a RomM Client API Token from the credentials you enter and discards the password — it is never stored.
The same applies if the plugin auto-migrates an older install that still had a saved password: the password is discarded
as soon as a token is minted. If your RomM account is not allowed to create API tokens, the sign-in step reports that
and you'll need an account with token permissions.

<!-- Screenshot: Connection Settings page with URL field, Sign in button, and token status -->

Once connected, you're ready to sync your library. See [Configuration](configuration.md) for additional settings, or
jump straight to [Syncing Your Library](syncing-your-library.md).

---

**Next:** [Configuration](configuration.md)
