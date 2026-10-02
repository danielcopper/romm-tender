# Configuration

All settings are accessible from the plugin's QAM panel. Open the Quick Access Menu (**...** button), select **Tender**,
and pick **Settings** from the menu at the bottom of the panel.

## The Settings page

Settings is a wide page split in two: a list of six sections on the left, and the focused section's controls on the
right. Move onto a section in the list and the right-hand side changes at once — there is nothing to confirm.

| Section           | What is in it                                                                                                                                                 |
| ----------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Connections**   | the services Tender talks to: **RomM** (server URL, account, Sign out, Allow Insecure SSL) and **SteamGridDB** (the API key)                                  |
| **Save Sync**     | the save-sync switch and its settings (device, before launch, after exit, default slot, history limit, Sync All Saves Now) and the list of registered devices |
| **Controller**    | Steam Input Mode, Apply to All Shortcuts, and the RetroArch `input_driver` fix                                                                                |
| **Steam Library** | preferred region, collection games in platform groups, collection types in Steam names                                                                        |
| **Updates**       | the version you have and the release the last successful check found, an update that was rolled back or refused, the daily update check, and **Check now**    |
| **Advanced**      | log level                                                                                                                                                     |

If you used an earlier version, everything is still here — the eight blocks the panel used to stack are grouped into
five of those six; **Updates** is new. Registered Devices is now inside **Save Sync**, the SteamGridDB key is inside
**Connections**, and the section that used to be called **Library** is now **Steam Library**: the Library _page_ is
about what gets synced out of RomM, this section is about how it looks once it is in Steam.

**Signing in to RetroAchievements is not here yet.** When it arrives it will live under Connections, with the other
accounts.

### Getting there from a notice

Four of the notices on the plugin's main panel are doors into a section, and the action they are about lives only behind
that door:

| The notice says               | Its button           | Where it takes you     |
| ----------------------------- | -------------------- | ---------------------- |
| RetroArch: input_driver issue | **Open Controller**  | Settings › Controller  |
| Cross-device playtime         | **Open Connections** | Settings › Connections |
| Update to X failed            | **Open Updates**     | Settings › Updates     |
| Tender X is available         | **Open Updates**     | Settings › Updates     |

The main panel only names the condition — it no longer carries a Fix button, so there is one place to do each of these
and no chance of two of them disagreeing. **B** takes you back to the main panel from anywhere on the page.

Where the sections below say "in Connection Settings", read it as **Settings › Connections**.

## Connection Settings

The **Connections** section manages your RomM server connection.

<!-- Screenshot: Connection Settings page -->

- **RomM URL** — the full URL of your RomM server, including port if needed (e.g. `http://192.168.1.100:8080`). Tap
  **Edit** to change it; the URL saves automatically.
- **RomM Account** — shows **Signed in** once a token is stored, or **Not signed in** otherwise. Tap **Sign in** to open
  a one-time prompt. The prompt offers three sign-in methods, chosen from the **Sign-in method** dropdown:
  - **Username & password** (default) — enter your RomM username and password once. The plugin exchanges them for a RomM
    Client API Token and stores only the token; your password is discarded after the token is minted and never saved. If
    your account cannot create API tokens, the status reports that.
  - **API token** — paste a Client API Token you created in RomM's web UI. This is one of the two paths for accounts
    that have no password to mint from, such as **OIDC / SSO logins**. See
    [Sign in with an API token (OIDC)](#sign-in-with-an-api-token-oidc) below.
  - **Pairing code** — the other, recommended OIDC path: instead of copying the token, enter the short-lived 8-character
    code RomM shows when you **Pair** a token. The plugin fetches the token itself, so nothing is copied or pasted. See
    [Sign in with an API token (OIDC)](#sign-in-with-an-api-token-oidc) below.

  Both the credentials and the pasted token are write-only — they are never pre-filled or shown back to you.

  Once you are signed in, this button reads **Sign in again** and a **Sign out** button appears below it (see
  [Sign out](#sign-out)).

- **Custom headers** — extra HTTP headers sent with every request to your RomM server. Shows how many are configured, or
  **(none)**. Only needed when your server sits behind a proxy that authenticates requests itself — see
  [Custom headers for an authenticating proxy](#custom-headers-for-an-authenticating-proxy) below.
- **Allow Insecure SSL** — shown only for `https://` URLs; skips certificate checks for a self-signed server. Anyone who
  can intercept the connection can then read what the plugin sends — your RomM token, your password when you sign in
  with it, and any custom headers — and use your account, so turn it on only on a network you trust. While it is on,
  every start of the backend writes a warning to its log saying certificate verification is off.

The plugin checks the connection for you — there is no manual "Test Connection" button. The **Connection** row on the
plugin's main QAM panel shows the live status whenever you open it, and names the problem when it can't connect (for
example _Sign-in rejected_, _Server unreachable_, or _No server URL_).

### Custom headers for an authenticating proxy

If your RomM server sits behind a proxy that authenticates requests before they reach RomM — Pangolin, Cloudflare
Access, Authelia, Authentik forward-auth — the proxy rejects the plugin's requests and you cannot connect at all. A
browser gets past it because you logged in to the proxy there; the plugin has no such session.

The way through is a header the proxy accepts. Tap **Edit** on the **Custom headers** row, add a row per header, enter
its name and value, and save. From then on every request the plugin sends to your RomM server carries them — including
the sign-in itself, since the proxy sits in front of that too.

**Where they go.** The plugin puts them on the requests it sends to the RomM server you configured, and on no other
request it sends — they are credentials for your front door. The plugin does reach other hosts: SteamGridDB for artwork,
and a metadata provider's CDN for a cover image RomM has no local copy of. Neither carries them.

One case is outside the plugin's hands: if your server answers with a redirect to a **different** host, the HTTP library
carries the headers along to it, the same way it already carries your RomM API token. That is worth knowing if your RomM
URL is plain `http://`, where anyone on the network between you and the server could insert such a redirect. Over
`https://` to a server you control it is not a practical concern.

**What they cannot be.** These names are refused when you save, so nothing you enter can quietly replace a header the
plugin or its HTTP library already sets: `Authorization`, `User-Agent`, `Content-Type`, `Content-Length`, `Host`,
`Accept-Encoding`, `Range`, `If-None-Match` and `If-Modified-Since`.

`Authorization` is the one worth explaining. Proxy documentation often suggests it — Pangolin documents a Basic-auth
`Authorization` header — but that is exactly the header your RomM API token travels in. One request cannot carry both,
so the plugin refuses it rather than silently sending one and dropping the other. A proxy built for non-browser clients
usually offers a header of its own as well; use that one.

**Worked example — Pangolin.** Issue a resource access token in Pangolin, then enter its two parts as rows:

| Name                | Value                        |
| ------------------- | ---------------------------- |
| `P-Access-Token`    | the token Pangolin generated |
| `P-Access-Token-Id` | the token's id               |

Save, then check the **Connection** row on the main QAM panel — it should stop reporting a rejection.

**Values are write-only.** A saved value is never sent back to the plugin's UI: reopening the editor shows each header's
name with an empty value field marked `•••• stored`. Leave it empty to keep the stored value, or type to replace it.
Removing a row and saving deletes that header. Values are never written to the plugin's log.

### Sign out

The **Sign out** button (shown only while signed in) forgets the stored token **on this device**: it clears the token,
its server-side id, its origin, and its provenance from the plugin's settings, but keeps the **server URL** and the SSL
setting so you do not have to re-enter them. It asks for confirmation first.

Signing out **never deletes or revokes the token in RomM** — the token stays valid on the server. A token the plugin
minted from your username and password can only be deleted during a same-server re-sign-in (the stored token
deliberately lacks the permission to delete itself), and a token you supplied (pasted or paired) is yours to manage. To
revoke a token for good, delete it in RomM's web UI under **Settings → API Tokens**.

If you just want to switch accounts or re-authenticate, prefer **Sign in again** over signing out and back in. For
username/password accounts, re-signing in on the **same** server revokes the token the plugin minted before — a path
that a sign-out then sign-in cannot take, since sign-out has already forgotten the old token's id. RomM caps the number
of Client API Tokens per user, so avoiding stranded minted tokens matters.

### Sign in with an API token (OIDC)

If you log in to RomM through an identity provider (OIDC / SSO), your RomM account has no password, so the plugin cannot
mint a token for you. Instead you create a Client API Token yourself in RomM's web UI and hand it to the plugin. There
are two ways to do that — **pairing code** (recommended) and **pasting the token** — and both grant the same scopes and
carry the same warnings (see [Required scopes](#required-scopes) below).

Start the same way for either method:

1. In RomM's web UI, open **Settings → API Tokens** (also called Client API Tokens) and create a new token.
2. Grant the scopes listed below — make sure the **write** scopes are included. Without them, downloads work but save
   upload, device sync, and playtime tracking fail with a permissions error.

#### Pairing code (recommended)

Pairing hands the device the token over a short-lived one-time code, so you never copy or type the token itself.

1. Create the token with the scopes above (steps 1–2).
2. On that token in RomM's web UI, click **Pair**. RomM shows an 8-character pairing code, valid for **60 seconds**.
3. In the plugin's Connection Settings, tap **Sign in**, switch the **Sign-in method** dropdown to **Pairing code**,
   enter the code, and confirm — within the 60-second window. The plugin exchanges the code for the token itself.

> **Pairing rotates the token's secret.** Exchanging a pairing code hands the device a **freshly rotated** secret for
> that token — any raw token value you copied earlier stops working. Use **one token per device** so pairing a new
> device never invalidates another.

#### Paste the token

1. Create the token with the scopes above (steps 1–2).
2. Copy the token value (RomM shows it only once).
3. In the plugin's Connection Settings, tap **Sign in**, switch the **Sign-in method** dropdown to **API token**, paste
   the token, and confirm.

Both methods validate the token at sign-in with an authenticated probe against your RomM profile: a wrong, revoked, or
expired credential is rejected there with an actionable message. Sign-in only confirms that the token **authenticates**,
though — the plugin cannot verify the token's granted scopes, so double-check that you granted the write scopes
(`assets.write`, `devices.write`, `roms.user.write`) when creating it. A missing write scope is not caught at sign-in;
it surfaces later as a permissions error on the affected action (save upload, device sync, or playtime).

#### Required scopes

| Scope              | Access    | What it is used for                                                                          |
| ------------------ | --------- | -------------------------------------------------------------------------------------------- |
| `me.read`          | read      | Your RomM profile — validates the token at sign-in and reads your RetroAchievements username |
| `platforms.read`   | read      | Listing your platforms                                                                       |
| `roms.read`        | read      | Listing and reading ROM metadata (the library)                                               |
| `roms.user.read`   | read      | Your per-user ROM data — native play-session history                                         |
| `collections.read` | read      | Reading your collections (user, smart, virtual)                                              |
| `firmware.read`    | read      | Listing and downloading BIOS / firmware                                                      |
| `assets.read`      | read      | Downloading save files                                                                       |
| `devices.read`     | read      | Reading your registered devices (device sync)                                                |
| `assets.write`     | **write** | Uploading save files                                                                         |
| `devices.write`    | **write** | Registering this device and opening device-sync sessions                                     |
| `roms.user.write`  | **write** | Writing your per-user ROM data — playtime ingest                                             |

All eleven scopes are within RomM's **Viewer** role, so a token created by any account (including OIDC accounts) can
carry them. `me.write` is deliberately **not** requested — a pasted token cannot mint or delete tokens.

> **The plugin never deletes a pasted token.** Signing out of or back into the plugin, or switching servers, leaves your
> token untouched on the RomM server — you manage its lifecycle in RomM's web UI. (This differs from the
> username/password method, where the plugin revokes the token it minted when you re-sign-in on the same server.)
>
> Note the reverse case too: if you previously signed in with your username and password and then switch to a pasted
> token on the **same** server, the token the plugin minted earlier is left behind on RomM — it can only revoke that
> during a same-server password re-sign-in (it has no password once you switch to a token). Revoke the old token
> manually in RomM's web UI if you no longer want it.

## SteamGridDB API Key

Under **Settings › Connections**, below the RomM group — SteamGridDB is one of the two services Tender talks to.

The plugin uses [SteamGridDB](https://www.steamgriddb.com/) to fetch additional artwork for your games — hero banners,
logos, and wide grid images. RomM provides cover art, but SteamGridDB fills in the rest so your games look like
first-class Steam titles.

To set this up:

1. Create a free account at [steamgriddb.com](https://www.steamgriddb.com/)
2. Go to your [API preferences](https://www.steamgriddb.com/profile/preferences/api) and copy your API key
3. In **Settings › Connections**, tap **Edit** next to **API Key** under "SteamGridDB" and paste your key
4. Tap **Save** — the key is checked against SteamGridDB first, so an invalid key is rejected inline and only a working
   key is stored

<!-- Screenshot: SteamGridDB API Key section with the Edit button -->

Without an API key, games will still have cover art from RomM but the hero banner, logo overlay, and wide grid image
will be missing.

## Steam Input Mode

Controls how Steam handles controller input for ROM shortcuts. Found in **Settings › Controller**.

| Mode                      | Description                                                                                                                |
| ------------------------- | -------------------------------------------------------------------------------------------------------------------------- |
| **Default** (Recommended) | Uses your global Steam Input settings. Works well with RetroDECK's default configuration.                                  |
| **Force On**              | Explicitly enables Steam Input wrapping. Normalizes the controller as standard XInput, which RetroArch autoconfig expects. |
| **Force Off**             | Raw HID passthrough. Only for advanced users — may break RetroArch menu navigation.                                        |

After changing the mode, tap **Apply to All Shortcuts** to update all existing ROM shortcuts. The button reads
**Applying to all shortcuts…** and stops responding while the run is going — a second tap is refused rather than
starting a second pass over the same shortcuts.

<!-- Screenshot: Steam Input Mode dropdown with the three options -->

## Preferred region

A dropdown in **Settings › Steam Library**. When a game exists in your RomM library as several regional dumps
(versions), this decides which region the plugin prefers when it picks the version to bind and the name it gives the
Steam shortcut.

| Option                                   | Effect                                                                                              |
| ---------------------------------------- | --------------------------------------------------------------------------------------------------- |
| **Default (World > USA > Europe)**       | Prefer World, then USA, then Europe, then Japan, then any other region alphabetically (fixed order) |
| a specific region (World, USA, Japan, …) | Put that region at the top of the order; everything else keeps the default order behind it          |

**"Default" is a fixed order, not auto-detection.** It always prefers `World > USA > Europe > Japan` (then other regions
alphabetically, then dumps with no region); the plugin never looks at your language or system region.

**The dropdown's options** are the fixed anchors — Default, World, USA, Europe, Japan — followed by every other region
actually present in the games you have already synced (read from the local database, sorted alphabetically; no server
request). If your synced library has no other regions, only the anchors are shown.

**When you change it, a short modal explains the effect** and asks you to confirm. The choice is saved immediately, but
it takes effect on the **next sync** and only affects games synced from then on. It **never** switches the version or
renames an existing shortcut — shortcut names are fixed when the shortcut is first created, to protect its artwork,
collections and playtime. Already-synced games keep their bound version and name; run a sync to apply the new preference
to new games. See [Multiple versions of a game](syncing-your-library.md#multiple-versions-of-a-game) for the full
picture.

## Log Level

A dropdown in **Settings › Advanced**. Controls how much detail the plugin logs.

| Level              | Description                        |
| ------------------ | ---------------------------------- |
| **Error**          | Only errors — minimal output       |
| **Warn** (default) | Errors and warnings                |
| **Info**           | General operational messages       |
| **Debug**          | Verbose output for troubleshooting |

Leave this at **Warn** unless you're investigating an issue. Switch to **Debug** when reporting bugs or diagnosing
problems.

## Updates

Tender asks GitHub whether a newer release is out — at most once a day, when Steam loads it and again while it runs,
unless you switch the daily check off. When there is one, a notice on the main panel says **Tender X is available** and
names the version you have — a check made while the panel is open brings it up there and then. **Open Updates** takes
you to **Settings › Updates**, where you can install it, and **Dismiss** puts the notice away for that version only —
the next release brings it back.

Tender also says so in a message that goes by itself — once Steam has finished starting, or as soon as a check while
Tender runs finds the release — so you hear of it without opening the panel: **Tender X is available. Settings › Updates
to install it.** Tapping it does nothing. It says it once for each release: reopening the panel, restarting Steam or
restarting Tender does not bring it back, and a release you found yourself with **Check now** does not say it at all. It
says nothing while the daily check is switched off, for a release whose notice you dismissed, or while the main panel
does not call that release available because an update to it failed (see below) — after the installer went back or
refused the new version, that holds even once you dismissed the failure's notice. From the moment you press **Install**
until the install has ended it waits; if the update did not go through, it comes then, unless one of the above holds it
back.

While the notice is on the main panel, small blue dots show you the way to the release — on Tender's icon in the Quick
Access menu, beside **Settings** on the main panel, and beside **Updates** in the Settings list — with the daily check
switched off too. Once **Settings › Updates** has been open for about a second, whether you went there from the list or
with **Open Updates**, the release counts as seen: the dots grow and fade out, and they stay away for that release, also
after a restart. Moving through the Settings list past **Updates** does not count, and neither does closing the menu
within that second. Seeing it there also means no message comes for it. The notice on the main panel stays until you
dismiss it or install the update; dismissing it takes the dots away too, and so does installing the update. A newer
release brings them back.

After an update, once Steam has finished starting, it says **Tender updated to X** in a message that goes by itself;
after installing an earlier version, it says **Tender is back on X** instead. It says it once: reopening the panel, or
restarting Steam, does not bring it back. The main panel also shows a notice, **Tender was updated to X.** or **Tender
is back on X.**, which stays — above the other update notices — until you press **Dismiss**, or until the Deck restarts
(which restarts Tender).

If the new version did not start after an update, and the installer went back to the version you had, a notice on the
main panel says **Update to X failed — you are still on Y.**, and under it where the reason is.
[Troubleshooting](troubleshooting.md#an-update-was-rolled-back) says what to look at there. **Open Updates** takes you
to **Settings › Updates**, and **Dismiss** puts the notice away for that failed update only; another one brings it back.
It also goes away by itself once a later update goes through. While it is there, and after you dismiss it until a later
update goes through, the main panel does not also call X available — you have just seen it fail — although a release
newer than X brings back an **is available** notice for that release.

A failed update also says so in a message that goes by itself, once Steam has finished starting, so you hear of it
without opening the panel. After the installer went back to the version you had or refused the new version, and after an
installer that stopped Tender and then stopped without updating, it says **Update to X failed. You are still on Y.
Settings › Updates shows why.** An install you started from **Settings › Updates** that fails while Tender runs says
what stopped it — **Update to X failed. The download failed.** or **Update to X failed. The installer stopped without
updating.**, for example — and one a game stopped says **Update to X was cancelled. A game was started. Nothing was
changed.** Where the panel was not loaded when such an install failed, the message comes when it next loads, unless
Tender was restarted in between. Each failed update says it once: reopening the panel, restarting Steam or restarting
Tender does not bring it back, and one whose notice you already dismissed on the main panel does not say it at all.

**Settings › Updates** shows:

- **Installed** — the version you are running.
- **Available** — the release the last successful check found, in green, **None newer** when you already have it, or
  **Not known yet** before a check has found anything — whether or not the daily check is switched on.
- **Install update** — installs the release **Available** names; see [Installing an update](#installing-an-update).
  **Try again** instead, for a version whose install already failed, was rolled back or was refused. A copy of Tender
  run from a source checkout says **Development build — install updates with the installer.** in its place.
- Below the versions and the button, one block for what is happening: an install under way, or an update that did not go
  through. While the installer's note of a rolled-back update, or of one its pre-install check refused, is there and you
  are still on the version it names, the block says so — **Update to X failed — Tender went back to Y.** or **Update to
  X failed — nothing was changed.** — with the step it failed at and where the reason is, whether or not you dismissed
  the notice on the main panel. Where the installer ran, **Show what the installer said** stands under the block; see
  [What the installer said](#what-the-installer-said).
- **Check for updates daily** — on by default. Switch it off and Tender no longer asks GitHub by itself; what the last
  check found stays in **Available** and can still be installed.
- **Check now** — asks straight away rather than waiting for the day to pass, whether or not the daily check is switched
  on, and brings back a notice you dismissed. What it found shows in **Available**; a line under the button appears only
  when there is more to say: that GitHub gave no usable answer, or that the check failed.

A release counts as out only once its download is attached together with GitHub's checksum for it and the checksum file
the installer verifies it against, which happens a few minutes after the release is published; until then Tender says
nothing about it. A release whose download comes without a valid checksum, or without that file, does not count as out
while either is missing, because it could not be verified.

**What the check sends where.** When a day has passed since the last check — while the daily check is on, Tender looks
when Steam loads it and once an hour while it runs — and whenever you press **Check now**, Tender asks GitHub's public
API for the newest release of `danielcopper/romm-tender`. The request names the program and its version (for example
`romm-tender/1.0.0`), and GitHub sees your IP address, as it does for any request. Nothing about your library, your RomM
server or your accounts is sent. If the check gets no usable answer — you are offline, GitHub is down, or it refuses the
request — nothing changes: whatever the last successful check found stays as it was, and Tender tries again once a day
has passed.

### Installing an update

**Install update** downloads the release, checks it against GitHub's checksum and starts the installer, which replaces
Tender and restarts it. It is there for the release **Available** names, whether or not the daily check is switched on.

The button waits while an update would cut something short, and says what under **Waiting for:** — a game to close
(named), library sync, game downloads, save sync (a slot switch or a save deletion counts too), BIOS downloads, a save
directory move, a removed-game cleanup, a RetroDECK migration, or **Other Tender work** — anything else Tender is in the
middle of, such as uninstalling games, removing shortcuts or switching a game's version. **Could not check whether a
game is running** means Tender could not ask Steam, and it waits then too rather than assume nothing is running. An
update reloads Steam's interface, and Tender reloads it at most twice in ten minutes; after two reloads the button waits
until the time it names. **Could not check when Steam's interface may be reloaded** means Tender could not check that
limit, and it waits then too. The list updates by itself every few seconds, and the button comes back on its own once
nothing is left on it. A RetroDECK migration that is only waiting for your answer does not hold the button back: the
question is still there after the update. Paused game downloads do not hold it back either — a line under the button
says how many there are, because the restart cancels them.

Once you press it, the button says **Installing…** and a block under it shows how far the install got: what is
happening, with the percent of the download and the time since you pressed, a bar, and the four steps **Download**,
**Verify**, **Check the new version** and **Install**, each marked `✓` done, `●` under way, `○` still to come or `✕`
failed. While it downloads and verifies, the block says **Starting a game now cancels the update.** — Tender checks once
more right before the installer starts, and stops there, with nothing changed, if a game is running.

When the installer has started, it first runs the new version's pre-install check, without stopping Tender: the block
says **Checking the new version**. Once the check has passed, the installer stops Tender and the block says **Tender is
restarting**, with **Steam's interface reloads when it is done — usually within a minute, and up to about 5 minutes if
Tender has to go back to Y.** Tender reports nothing once the installer has started, so the panel tells the two apart by
whether Tender still answers. From then on the panel loses touch with the old Tender, which is expected; after the
reload Tender says it was updated, or, if the new version did not answer once started, that the installer
[went back to the version you had](troubleshooting.md#an-update-was-rolled-back). The check may take up to two minutes;
the installer then waits up to a minute for the new version to answer, and up to a minute more for the one you had if it
goes back; stopping Tender, saving your data and reloading Steam's interface come on top. Where the new version cannot
even be put together, the check stops the installer there: nothing is replaced, Steam's interface does not reload, and
the block says **Update to X failed — nothing was changed.** with **Check the new version** marked failed and **The new
version does not start.**, and the main panel says the update failed
([The New Version Does Not Start](troubleshooting.md#the-new-version-does-not-start)). While the install runs, Tender
refuses to start a library sync, a game download or a save sync.

If Tender is not back seven minutes after the installer started, the line in the block changes: **Tender has not come
back.** when it no longer answers — the line names the journal to read and the command that starts it again — or **The
installer is taking unusually long.** when it still answers and the installer has not stopped it yet.

If the install fails, the block turns amber, says **Update to X failed** — or **Update to X was cancelled**, where a
game was started or Tender could not check whether one runs — marks the step that failed with `✕` where it was one of
the four, and says why, and the button comes back as **Try again**. Nothing tries again by itself. Where the installer
ran — it stopped without updating, or refused the new version — **Show what the installer said** stands under the block.
[An update from Settings did not go through](troubleshooting.md#an-update-from-settings-did-not-go-through) says what
each failure means and where to read more.

### What the installer said

**Show what the installer said** is under the failure block in **Settings › Updates** wherever the installer ran: an
update it went back from, one its pre-install check refused, and one where it stopped without updating. It is not there
for a download that failed, a checksum that did not match, an installer that could not be started, or an update a game
cancelled — the installer did not run for those, and Tender's log, `backend.log`, says why.

It opens a window titled **What the installer said**, which says **Reading what the installer said…** until Tender has
the answer; pressing the button again meanwhile opens no second one. Where there is output, the title carries the time
the installer ran, and **The installer** shows that installer's output — the run that belongs to this failure, not the
latest one if the installer has run again since. After the installer went back to the version you had, a second part,
**X, when it tried to start**, shows what the new version printed between the installer starting and going back — where
the reason usually is, since the installer itself only says that the new version did not answer. The installer prints a
line each time one of its steps — Checking, Installing, Service, Steam — gets further, and the window shows each step
the installer reached once, in its last state: how the step ended, or, for a step the installer was cut off in, how far
it got. Each part shows its last 300 lines and says how many earlier ones it leaves out, and a line longer than 500
characters is cut. Tender's own address, which the new version prints with the key that lets the panel in, shows that
key as `[hidden]`. A step that failed and the installer's own failure lines are amber, a step done green, and a step
under way grey. Move down through the lines with the D-pad, and press **Close** to go back.

Tender reads this from the Deck's system journal. Sometimes there is nothing to show, and the window says why instead,
with no time in its title:

- **This output is no longer in the system journal — it keeps only the last hours of logs.** The journal keeps a fixed
  amount for everything on the Deck, so older output is gone.
- **This update was run in a terminal, so its output is there, not in the journal.** The installer ran by hand, so its
  output went to that terminal.
- **The installer left nothing in the journal for this update.** Tender started the installer, and the journal, which
  still reaches back that far, holds nothing from it — it may never have started.
- **This failed update is no longer on record.** The failure went away between the block showing it and the press — a
  later update went through, or a new install started.
- **Tender could not read what the installer said.** The journal could not be read, or Tender did not answer.

## RetroArch Input Driver Fix

If the plugin detects that RetroArch is using the `x` input driver (which causes controller issues in menus on Wayland
systems), a notice appears on the main panel with an **Open Controller** button. The fix itself is in **Settings ›
Controller**: **Fix input_driver to sdl2** modifies your RetroArch config to use `sdl2` instead, which fixes controller
navigation in RetroArch menus. The result is reported under the button, and the warning goes away once the config has
been changed.

The button **asks before it acts** — it opens a confirmation naming the change, and only **Apply Fix** writes anything;
**Cancel** leaves your config exactly as it was. The confirmation is there because the change is written straight into
your config and the plugin keeps no copy of the file it replaces, so if you have hand-edited your `retroarch.cfg` and
want a copy, take one before confirming.

<!-- Screenshot: RetroArch input_driver warning with fix button -->

---

**Previous:** [Getting Started](getting-started.md) | **Next:** [Syncing Your Library](syncing-your-library.md)
