# Controller Focus in Desktop Mode

In Desktop Mode, Steam's **64-bit client** loads the wrong controller layout for a game a Flatpak draws: the Desktop
layout, under which the Deck's controls act as mouse and keyboard. The **focus watcher** in Tender's backend works
around this. On every change of the focused window it tells Steam which layout to load. This page is how it does that
and why. It is a stopgap: the watcher goes once Steam attributes such windows itself, and this page goes with it. What a
user sees, and how to switch the watcher off, is in
[Troubleshooting](../user-guide/troubleshooting.md#the-controller-acts-as-mouse-and-keyboard-in-a-game-in-desktop-mode).

## Why Steam gets it wrong

Steam picks a controller layout by the focused window. It finds the window's process through the X server's X-Resource
extension, which answers with the pid in the host's process table, and then matches that process to a game. It matches
either a process it tracks itself, or a `SteamGameId=` variable in the process's `/proc/<pid>/environ`. A Flatpak's
processes are never tracked: the sandbox drops `LD_PRELOAD`, so Steam's overlay library, which is what reports a
process, is never loaded there. For a Flatpak that leaves the environment variable.

The variable is there. The **32-bit client** runs on the host and reads it. The **64-bit client** runs inside its own
pressure-vessel container, in a user namespace of its own, and from there another process's `environ` is unreadable
(`EPERM`). The match fails without a log line, and the window gets the Desktop layout.

Gamescope does not ask for the environment at all. It walks the window's process up its parents to the `reaper` Steam
started the game under, which is why Gaming Mode is not affected.

Valve tracks the client side in its steam-for-linux tracker as issue `13053`.

## What Steam is told: three ids, and the release

Each URL below was measured on a Deck under the 64-bit client. `steam://forceinputappid/<id>` makes Steam load the
layout of `<id>` and keep it until it is replaced. This is the **pin**.

| id       | Layout                                | Takes effect             |
| -------- | ------------------------------------- | ------------------------ |
| a game's | that game's                           | at once                  |
| `769`    | Big Picture's (`basicui_neptune.vdf`) | at once                  |
| `413080` | the desktop's                         | at once                  |
| `0`      | none: Steam chooses by itself again   | at the next focus change |

A pin outlives its game. While one stands, Steam forces its layout onto every window, Big Picture and other games
included, until another id replaces it. So the watcher never leaves a pin to stand and never relies on `0` while it
runs. On every focus change it sends an explicit id, an unchanged one included, which also puts the pin back if anything
else changed it. It sends `0` only once, when the backend stops. A controller that disconnects and reconnects keeps the
pin, so nothing is sent for that.

Which id a window gets, in this order (`domain/controller_focus.py::input_id_for`):

1. The window's process, or one of its parents, is a `reaper` of this user started by `SteamLaunch` with `AppId=<id>`
   among its own options, before the `--`. The watcher sends `<id>`, for any game and not only a shortcut of Tender's.
   The process comes from X-Resource, the host's pid. `_NET_WM_PID` is never used, because for a Flatpak it is the
   sandbox's own pid.
2. The window carries `STEAM_GAME` = `769` **and** a `STEAM_INPUT_FOCUS` property. That is Big Picture, and it gets
   `769`. The desktop client's windows (library, friends list, a properties dialog) carry `STEAM_GAME` = `769` too, but
   not `STEAM_INPUT_FOCUS`. The title is never used, because Steam translates it.
3. Everything else gets `413080`: no focused window, a compositor's placeholder for a native Wayland window (mutter ≥
   48.4, KWin ≥ 6.5), a window gone before it could be asked about, and a window whose pid the server cannot give.

## When it acts

Only in Desktop Mode under the 64-bit client (`watcher_applies`). Both are read off the running Steam:

- **Which Steam**: the pid in `~/.steam/steam.pid`. It counts only where `/proc/<pid>/comm` is `steam` and
  `/proc/<pid>/exe` can be read. A stale file, a pid that is some other program now, and a Steam installed as a Flatpak,
  whose pid file lies elsewhere and names a sandbox pid, all mean no Steam.
- **Which client**: the directory the executable resolves into is `steamrt64`. The opt-in marker file is not read,
  because Steam no longer needs it to switch.
- **Gaming Mode**: Steam's own environment carries `GAMESCOPE_WAYLAND_DISPLAY`. SteamOS's `gamescope-session` writes it
  into `gamescope-environment`, and `steam-launcher.service` loads that file. The environment file and gamescope's
  sockets stay on disk in Desktop Mode, so their presence says nothing, and neither does `-gamepadui` on the command
  line.

A Steam run is identified by its pid together with the start time in `/proc/<pid>/stat`, so a reused pid counts as a new
run. Steam is read every three seconds. A new run, a switch of client, Steam gone and a lost X connection are each
noticed within that time. A run where the watcher applies gets a fresh connection, and the focused window's id is sent
at once. Under the 32-bit client and in Gaming Mode nothing is opened and nothing is sent, so the attribution that works
there is never overridden.

## Talking to the X server

Steam learns about focus only through X, so the watcher asks X too. On a Wayland session it asks Xwayland. The root
window's `_NET_ACTIVE_WINDOW` names the focused window, and a `PropertyNotify` on it marks the change. The backend's
socket to the X server is watched with `loop.add_reader`, with no thread. Each question, and the walk up `/proc`, runs
off the loop.

- **Through libxcb, over `ctypes`.** It uses the system's `libxcb.so.1` and `libxcb-res.so.0`: no dependency added,
  nothing vendored. Xlib was not chosen, because its error handlers end the process and its authority lookup is global
  to the process. `xprop` and `xdotool` cannot ask X-Resource. The libraries are loaded on the first connection and
  never at import, so a missing one costs the watcher and not the backend. On Debian, Ubuntu, Mint and openSUSE,
  `libxcb-res.so.0` comes in a package of its own, `libxcb-res0`, which an Xfce, MATE or LXDE install may lack.
- **The display and its cookie come from Steam's session, never from the backend's environment.** The backend's systemd
  unit starts before the desktop session, and its environment outlives a switch between modes. `DISPLAY` and
  `XAUTHORITY` are read from the first process at or above Steam that shares the backend's user namespace. Inside the
  64-bit client's container, `XAUTHORITY` names a file only that container can see. The cookie, `MIT-MAGIC-COOKIE-1`, is
  read out of that authority file and handed to `xcb_connect_to_display_with_auth_info`. The backend's own environment
  is never changed, and the cookie is never logged.
- **A reply can bring events with it.** An event read off the socket together with a reply never makes the socket
  readable again, so every round of questions ends by draining xcb's queue of events already read.

Where the libraries cannot be loaded, or the display cannot be reached, the watcher says so once for that Steam run and
tries again at the next reading of Steam.

## Handing Steam the URL

The URL goes over the backend's existing attachment to Steam's debugger, the one the
[panel is loaded through](loading-the-panel.md). It is evaluated as `SteamClient.URL.ExecuteSteamURL("<url>")` in
`SharedJSContext`. The URL is built only from an integer and reaches the expression only as a JSON string. Measured on a
Deck under the 64-bit client, `413080` and `769` sent this way take effect at once, the same as from the command line.

Two other ways in were rejected:

- **The `steam` command.** On SteamOS it is a wrapper script that checks runtimes, may rewrite the beta branch on every
  call, and starts Steam when Steam is not running.
- **`~/.steam/steam.pipe`.** Its format is undocumented.

A URL that cannot go out answers `False`: nothing is attached yet, or the connection was lost. The watcher then tries
the id of the window focused at that point on its next reading of Steam. A focus change that arrives while a send is in
flight is not queued, because the next pass reads the window focused by then. With `TENDER_INJECT=off` there is no
attachment, so the entry point does not start the watcher at all.

## At stop: the one step before the detach

The host lets go of Steam's debugger before it shuts the application down. A `0` sent from the services' shutdown would
find no connection, and a pin left standing keeps forcing its layout until Steam restarts. So the host gives the
application one narrow step before it detaches (`BackendBuild.before_detach`). In that step the watcher, and nothing
else, sends `0`, and only where this run sent a pin and the Steam it pinned is still running. Every send after it is
dropped. The rest of the stop keeps its old order.

Moving the whole detach behind the services' shutdown was rejected: the connection would stay open while every service
stops, and nothing has examined that. A backend that is killed outright never runs this step, and its pin stands until
the next backend start sends a fresh id, or until Steam restarts.

## Switching it off

`TENDER_FOCUS_WATCHER=off` (any case, surrounding space ignored) is read once at start-up, and so is
`TENDER_INJECT=off`. With either one, the watcher opens nothing, reads nothing and sends nothing, not even a `0`. Any
other value leaves it on. The switch exists to check whether a game starts cleanly without the watcher, before the
watcher is removed. It has no setting in the panel.

## What it does not cover

- **Steam's game menu and overlay.** In Desktop Mode, for a game in a Flatpak, the Steam button opens only Steam's main
  menu, under either client. The game menu follows the in-game overlay, and the overlay cannot work inside a Flatpak: it
  reports the sandbox's pid, which Steam checks against the host's processes. A pin fixes the controller layout and
  nothing about which game Steam thinks has focus.
- **The on-screen keyboard.** Steam+X opens none over such a game in Desktop Mode, whether the game is pinned or not.
- **A native Wayland window.** It is invisible to X, so it gets the desktop's id. Tender starts RetroDECK without a
  Wayland socket so that its windows are X windows
  ([Troubleshooting](../user-guide/troubleshooting.md#retrodeck-runs-through-xwayland-in-desktop-mode)).
- **A Steam installed as a Flatpak.** The watcher stays idle there.
