# Packaged defaults

Reference data that ships with the program. The backend reads `config.json` and `tender-icon.png` from this directory by
those names, so do not move or rename them.

## `config.json` — in-tree default

The platform-slug map. It is maintained in this repo (not vendored) and carries no checksum gate.

## `tender-icon.png` — the icon of a game SteamGridDB has none for

Tender's logo at 64×64, the size every shortcut icon is written at. The shortcut icon job copies it into Steam's grid
directory and gives it to every shortcut SteamGridDB has no icon for
([Shortcut icons](../docs/architecture/steam-non-steam-shortcuts.md#shortcut-icons)). It is `assets/logo.png` passed
through the same downscale as every other icon; after a change to the logo, regenerate it from `backend/`:

```sh
python -c "from adapters.icon_image import downscale_icon; open('../defaults/tender-icon.png', 'wb').write(downscale_icon(open('../assets/logo.png', 'rb').read()))"
```
