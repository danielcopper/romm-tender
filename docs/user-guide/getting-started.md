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

   This one always points at the newest release, so it needs no version number — and pasting it again later is how you
   update:

   ```text
   https://github.com/danielcopper/romm-tender/releases/latest/download/Tender.zip
   ```

   To pin a specific version instead, name its tag:

   ```text
   https://github.com/danielcopper/romm-tender/releases/download/tender-v{VERSION}/Tender.zip
   ```

   Every release from `tender-v0.31.0` on carries this asset — `tender-v0.31.0` itself published it as `tender.zip`, and
   GitHub matches release-asset names case-insensitively, so the URL above still finds it. Most of the
   `decky-romm-sync-v{VERSION}` releases before it published `decky-romm-sync.zip`; name that tag and that file for
   those. Four have no asset at all: the three earliest and `v0.2.0`.

6. Decky downloads and installs the plugin automatically — no restart needed

**Tip:** You can also open the [releases page](https://github.com/danielcopper/romm-tender/releases) in Steam's built-in
browser (Gaming Mode → long-press the Steam button → Web Browser), long-press the zip download link, and copy the URL
from there.

Any direct URL to the zip file works (GitHub releases, a self-hosted mirror, etc.) as long as it points to a valid
`.zip` containing the plugin.

### Updating from a release before 0.31.0

Releases up to 0.30.1 install into a folder named `decky-romm-sync`; from 0.31.0 on the folder is named after the
renamed repository. Decky treats a differently-named folder as a different plugin, so updating across that boundary
leaves you with **two** entries: the older one still shown as **RomM Sync**, and the new one as **Tender**.

**Wait for Tender to say the older plugin can go.** Every Steam shortcut this plugin created used to launch through a
file inside that older plugin's folder. Tender now keeps its own copy of that file outside any plugin folder and points
your shortcuts at it, which it does shortly after Steam starts — but until it has, removing "RomM Sync" stops all of
your games from starting, and nothing in Tender can put the file back.

While both are installed, Tender's QAM panel carries a card that tells you which of those it is — including when the
panel is showing a server-version error or a pending RetroDECK migration instead of its usual contents, the two states
in which the older plugin looks most like something to clear away. The card says one of two things:

- **"RomM Sync" is still installed, and your games need it** — the move has not happened yet on this device. Leave the
  older plugin alone. Tender tries again every time it starts up, which is when your device does — restarting Steam
  itself is not enough, because that reloads the panel without starting Tender's own half again.
- **"RomM Sync" can be removed now** — nothing in Tender depends on it any more. Remove it wherever Decky lists your
  installed plugins, or keep it: it costs disk space and nothing else. This one has a **Dismiss**, and dismissing it is
  remembered — the card does not come back at the next start.

The new install also starts with its own empty settings and library, because Decky gave each plugin folder its own data
location — your existing settings and synced library are in the older install until Tender brings them across.

### Manual installation (alternative)

1. Download the plugin's `.zip` asset from the [releases page](https://github.com/danielcopper/romm-tender/releases)
2. Extract the zip to `~/homebrew/plugins/` on your device (via SSH, file manager, or USB)
3. Restart Decky Loader — either reboot, or run `sudo systemctl restart plugin_loader` via SSH
4. The plugin appears in your QAM under the Decky tab

## First-Time Setup

After installation, you need to connect the plugin to your RomM server:

1. Open the QAM and find **Tender**
2. Tap **Connection Settings**
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
