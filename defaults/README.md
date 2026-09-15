# Packaged defaults

Reference data that ships inside the plugin. The runtime looks for each file at the plugin root first and under
`defaults/` second — Decky-packaged installs carried these flattened into the root — so they keep their bare names; do
not move or rename them.

## `config.json` — in-tree default

The platform-slug map and other default configuration. It is maintained in this repo (not vendored) and carries no
checksum gate.

## What used to live here

`bios_registry.json` — a frozen snapshot of which firmware files each platform and libretro core wanted — was vendored
here from an [emu-atlas](https://github.com/danielcopper/emu-atlas) release and read at runtime by `FirmwareService`. It
is gone: the file no longer exists upstream, so the snapshot could never be refreshed again and drifted a little further
with every RetroDECK update. Firmware requirements are now read live off the installed cores through the vendored
resolver (`backend/_vendor/atlas/`, provenance in [`_vendor/README.md`](../backend/_vendor/README.md)), which is data no
snapshot has to keep in step.
