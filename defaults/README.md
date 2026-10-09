# Packaged defaults

Reference data that ships with the program. The backend reads `tender-icon.png` from this directory by that name, so do
not move or rename it.

## `tender-icon.png` — the icon of a game SteamGridDB has none for

Tender's logo at 64×64, the bound every SteamGridDB icon is downscaled to fit. The shortcut icon job copies it into
Steam's grid directory and gives it to every shortcut SteamGridDB has no icon for
([Shortcut icons](../docs/architecture/steam-non-steam-shortcuts.md#shortcut-icons)). It is `assets/logo.png` passed
through the same downscale as every other icon; after a change to the logo, regenerate it from `backend/`:

```sh
python -c "import logging; from adapters.icon_image import IconImages; open('../defaults/tender-icon.png', 'wb').write(IconImages(code_dir='..', logger=logging.getLogger()).downscale(open('../assets/logo.png', 'rb').read()))"
```
