#!/usr/bin/env python3
"""Renders the mark's shipped files: the static asset, the banner lockup and the animated loop.

Wraps the two external tools the drawing code deliberately knows nothing about
— `rsvg-convert` to rasterise an SVG and `ffmpeg` to pack frames into a GIF.
Both must be on PATH.

    build.py                      everything, into scripts/logo/out/
    build.py --install            everything, into the paths the repo ships from
    build.py --out <dir>          somewhere else
    build.py --palette <name>     a palette other than the chosen one
    build.py --static             only the static SVG + PNGs and the lockup
    build.py --gif                only the animated GIF
    build.py --tab-icon           only the Quick Access strip glyph
    build.py --terminal           only the installer's terminal mark
    build.py --size <px>          master raster size (default 512)

`--install` is the one to run after changing the mark: it writes every shipped
copy from the same render, so `assets/` and `docs/assets/` cannot drift apart.

The GIF is quantised against a palette generated from the whole sequence rather
than one per frame, so the flat colours stay flat and the loop does not shimmer.
That single shared palette, and no dithering, is most of why the file stays well
under a per-frame-palette encode of the same footage.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess
import sys

import anim
import gen
import lockup
import tabicon
import terminal

HERE = pathlib.Path(__file__).parent
PNG_SIZES = (1024, 512, 256, 128, 64, 32)
# The README renders the banner around 300px wide; three times that covers the
# densest display anyone reads it on.
LOCKUP_PNG_WIDTH = 900
# The animated banner stops at twice the rendered width instead of three times:
# a GIF pays for every pixel in every frame, and the third multiple buys sharpness
# nobody sees at the cost of roughly half the file again.
LOCKUP_GIF_WIDTH = 600


# One palette for the whole sequence, then mapped against it. `stats_mode=full`
# looks at every frame, so a colour that only appears mid-morph still gets a slot.
#
# Dithering is off on purpose. The mark is flat colour over flat colour, so the
# only thing a dither adds is per-pixel noise — which reads as grain *and* costs a
# fifth of the file, because LZW cannot compress it.
#
# The mark itself only has about ten colours; the rest of the palette goes on the
# antialiased edges between them, and at 512px there are enough edge pixels that a
# small palette starts to show. Against the rendered frames, 24 slots leave a
# worst-case channel error of 49 on 1.2% of pixels and 64 halve that to 26 on 0.3%;
# past 64 the size grows faster than the fidelity. Hence 64.
def _gif_filter(colors: int) -> str:
    return (
        "split[a][b];"
        f"[a]palettegen=max_colors={colors}:stats_mode=full:reserve_transparent=1[p];"
        "[b][p]paletteuse=dither=none:diff_mode=rectangle"
    )


def _require(*tools: str) -> None:
    missing = [t for t in tools if shutil.which(t) is None]
    if missing:
        sys.exit(f"missing on PATH: {', '.join(missing)}")


def _run(cmd: list[str]) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        sys.exit(f"{cmd[0]} failed:\n{proc.stderr.strip()}")


def build_static(out: pathlib.Path, pal: gen.Palette, size: int) -> None:
    _require("rsvg-convert")
    out.mkdir(parents=True, exist_ok=True)
    svg = out / "logo.svg"
    svg.write_text(gen.standalone(pal, size=size))
    print(f"  {svg.name}  ({svg.stat().st_size:,}b)")
    for px in PNG_SIZES:
        png = out / (f"logo-{px}.png" if px != size else "logo.png")
        _run(["rsvg-convert", "-w", str(px), "-h", str(px), str(svg), "-o", str(png)])
        print(f"  {png.name}  ({png.stat().st_size:,}b)")

    sheet = out / "contact-sheet.svg"
    sheet.write_text(gen.sheet())
    _run(["rsvg-convert", "-w", "1400", str(sheet), "-o", str(out / "contact-sheet.png")])
    print("  contact-sheet.png")


def build_lockup(out: pathlib.Path, pal: gen.Palette) -> None:
    """The banner: mark over wordmark, in the two variants the grounds need."""
    _require("rsvg-convert")
    out.mkdir(parents=True, exist_ok=True)
    for stem, dark in (("lockup", False), ("lockup-dark", True)):
        svg = out / f"{stem}.svg"
        svg.write_text(lockup.lockup(pal, dark))
        png = out / f"{stem}.png"
        _run(["rsvg-convert", "-w", str(LOCKUP_PNG_WIDTH), str(svg), "-o", str(png)])
        print(f"  {svg.name}  ({svg.stat().st_size:,}b)")
        print(f"  {png.name}  ({png.stat().st_size:,}b)")


def build_lockup_gif(out: pathlib.Path, pal: gen.Palette, a: anim.Animation) -> None:
    """The banner, animated — the same fold and spin the bare mark runs."""
    _require("rsvg-convert", "ffmpeg")
    out.mkdir(parents=True, exist_ok=True)
    for stem, dark in (("lockup-animated", False), ("lockup-animated-dark", True)):
        work = out / f"frames-{stem}"
        if work.exists():
            shutil.rmtree(work)
        svgs = lockup.write_frames(work, pal, a, dark, LOCKUP_GIF_WIDTH)
        for i, svg in enumerate(svgs):
            _run(["rsvg-convert", "-w", str(LOCKUP_GIF_WIDTH), str(svg), "-o", str(work / f"f{i:03d}.png")])
        gif = out / f"{stem}.gif"
        _gif(work / "f%03d.png", gif, a.fps)
        print(f"  {gif.name}  ({gif.stat().st_size:,}b, {LOCKUP_GIF_WIDTH}px wide)")


def _gif(
    frames_glob: pathlib.Path,
    dest: pathlib.Path,
    fps: int,
    scale: int | None = None,
    colors: int = 64,
) -> None:
    filt = _gif_filter(colors)
    vf = filt if scale is None else f"scale={scale}:{scale}:flags=lanczos,{filt}"
    _run(
        [
            *("ffmpeg", "-v", "error", "-y"),
            *("-framerate", str(fps)),
            *("-i", str(frames_glob)),
            *("-vf", vf),
            *("-loop", "0", str(dest)),
        ]
    )


def build_gif(out: pathlib.Path, pal: gen.Palette, size: int, a: anim.Animation) -> None:
    _require("rsvg-convert", "ffmpeg")
    out.mkdir(parents=True, exist_ok=True)
    work = out / "frames"
    if work.exists():
        shutil.rmtree(work)
    svgs = anim.write_frames(work, pal, a, size=size)
    for i, svg in enumerate(svgs):
        _run(["rsvg-convert", "-w", str(size), "-h", str(size), str(svg), "-o", str(work / f"f{i:03d}.png")])

    pattern = work / "f%03d.png"
    gif = out / "logo-animated.gif"
    _gif(pattern, gif, a.fps)
    print(f"  {gif.name}  ({gif.stat().st_size:,}b, {size}px, {a.frames} frames @ {a.fps}fps)  <- shipped")

    # A half-size one for slots that never render bigger than this. Quarter the
    # pixels means quarter the antialiased edge, so it needs far less palette to
    # stay clean — 24 measures as well here as 64 does at full size.
    small = out / "logo-animated-256.gif"
    _gif(pattern, small, a.fps, scale=256, colors=24)
    print(f"  {small.name}  ({small.stat().st_size:,}b, 256px)")


def _prettier() -> pathlib.Path:
    """The frontend package's prettier, which the generated TypeScript needs."""
    binary = REPO / "frontend" / "node_modules" / ".bin" / "prettier"
    if not binary.exists():
        sys.exit(f"not found: {binary} — run `mise run setup` first")
    return binary


def build_tab_icon(out: pathlib.Path) -> None:
    """The Quick Access strip glyph: a TypeScript module, and an SVG to look at.

    The only build step here that rasterises nothing, so it needs neither
    `rsvg-convert` nor `ffmpeg`. The panel imports the module; the SVG is
    installed nowhere and is written beside it so a change to the form can be
    seen without opening Steam.

    **The module is handed to prettier**, because the repository's commit hook and
    CI both format TypeScript and would otherwise reformat this file after the
    build wrote it — at which point re-running the build produces a diff nobody
    made and the generated file stops being checkable against its generator.
    This copy is formatted for reading; the installed one is formatted again at
    its destination, where the repository's own prettier configuration applies
    ({@link install}).
    """
    out.mkdir(parents=True, exist_ok=True)
    ts = out / "tab-icon-art.ts"
    ts.write_text(tabicon.ts_module())
    _run([str(_prettier()), "--log-level", "warn", "--write", str(ts)])
    print(f"  {ts.name}  ({ts.stat().st_size:,}b)")
    svg = out / "tab-icon.svg"
    svg.write_text(tabicon.glyph())
    print(f"  {svg.name}  ({svg.stat().st_size:,}b, not installed — for looking at)")


REPO = HERE.parent.parent

# Where each shipped file goes. The mark lands twice because MkDocs only serves
# what lives under docs/, and the README needs a path that resolves on GitHub.
INSTALL = {
    "logo.svg": ("assets/logo.svg", "docs/assets/logo.svg"),
    "logo.png": ("assets/logo.png", "docs/assets/logo.png"),
    "logo-animated.gif": ("assets/logo-animated.gif", "docs/assets/logo-animated.gif"),
    # The lockup lands once: it is the README's banner, and the docs site draws
    # its own header from the bare mark instead.
    "lockup.svg": ("assets/lockup.svg",),
    "lockup.png": ("assets/lockup.png",),
    "lockup-dark.svg": ("assets/lockup-dark.svg",),
    "lockup-dark.png": ("assets/lockup-dark.png",),
    "lockup-animated.gif": ("assets/lockup-animated.gif",),
    "lockup-animated-dark.gif": ("assets/lockup-animated-dark.gif",),
    # Not an image: the strip glyph lands in the panel's own source tree, because
    # the tab icon is a React node rather than a file the panel points at — it
    # inherits `currentColor`, which does not survive being an <img>. Whether the
    # strip's colour is what it ends up inheriting is a device question.
    "tab-icon-art.ts": ("frontend/src/qam/tabIconArt.ts",),
}
# A 1024px square of the bare mark. Nothing renders it — no page, no manifest —
# it is the copy to hand out wherever a link preview or a listing wants one
# square image.
STORE_IMAGE = ("logo-1024.png", "assets/store_image.png")


def build_terminal(out: pathlib.Path) -> None:
    """The mark as terminal text, which `install.sh` prints before it does anything.

    Draws the mark itself — `gen.standalone` in the terminal's own pose, through
    `rsvg-convert` — rather than reading a shipped raster, so it needs no other
    build step to have run first and a change to the mark reaches it directly.
    Written here as well as installed so the drawings can be looked at without
    opening the script that carries them.
    """
    out.mkdir(parents=True, exist_ok=True)
    block = out / "installer-logo.sh"
    block.write_text(terminal.bash_block())
    print(f"  {block.name}  ({block.stat().st_size:,}b)")
    for destination, render in terminal.WORDMARK_FILES:
        copy = out / destination.name
        copy.write_text(render())
        print(f"  {copy.name}  ({copy.stat().st_size:,}b)")


def install_terminal() -> None:
    """Splice today's terminal mark into `install.sh`, and write the wordmark beside it.

    The mark is the one installed thing that is not a copy: the art lives inside
    a script that is otherwise hand-written, so it replaces a block rather than
    a file. The wordmark is two ordinary files, and nothing reads them yet.
    """
    script = REPO / "install.sh"
    script.write_text(terminal.replace_in(script.read_text()))
    print(f"  {script.relative_to(REPO)}  (the mark's block)")
    for destination, render in terminal.WORDMARK_FILES:
        destination.write_text(render())
        print(f"  {destination.relative_to(REPO)}  ({destination.stat().st_size:,}b)")


def install(out: pathlib.Path, names: set[str]) -> None:
    """Copy the freshly built files over the ones the repo ships.

    `names` is what this run actually built, so a narrowed build installs its own
    outputs and nothing else. Anything named that is missing is an error rather
    than a skip: a silent one would leave a shipped copy standing while the run
    that was supposed to replace it reported success.
    """
    pairs = [(out / src, REPO / dest) for src, dests in INSTALL.items() if src in names for dest in dests]
    if STORE_IMAGE[0] in names:
        pairs.append((out / STORE_IMAGE[0], REPO / STORE_IMAGE[1]))
    for src, dest in pairs:
        if not src.exists():
            sys.exit(f"not built: {src}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
        # Formatted HERE and not in `out/`, because prettier resolves its
        # configuration from the path of the file it is formatting and there is
        # exactly one config in this repository, under `frontend/`. A run over
        # the copy in `out/` therefore formats to prettier's defaults, and the
        # result does not satisfy `pnpm -C frontend format:check` — measured: 73
        # lines there against the 141 the frontend's own rules produce.
        if dest.suffix == ".ts":
            _run([str(_prettier()), "--log-level", "warn", "--write", str(dest)])
        print(f"  {dest.relative_to(REPO)}  ({dest.stat().st_size:,}b)")


if __name__ == "__main__":
    argv = sys.argv[1:]
    out = pathlib.Path(argv[argv.index("--out") + 1]) if "--out" in argv else HERE / "out"
    name = argv[argv.index("--palette") + 1] if "--palette" in argv else gen.CHOSEN
    size = int(argv[argv.index("--size") + 1]) if "--size" in argv else 512
    pal = gen.BY_NAME[name]
    only_static, only_gif, only_tab = "--static" in argv, "--gif" in argv, "--tab-icon" in argv
    only_terminal = "--terminal" in argv
    # Each --only flag narrows to itself; none of them means everything.
    everything = not (only_static or only_gif or only_tab or only_terminal)

    print(f"palette: {pal.name}   out: {out}")
    built: set[str] = set()
    if everything or only_static:
        build_static(out, pal, size)
        build_lockup(out, pal)
        built |= {"logo.svg", "logo.png", STORE_IMAGE[0]}
        built |= {n for n in INSTALL if n.startswith("lockup") and not n.endswith(".gif")}
    if everything or only_gif:
        build_gif(out, pal, size, anim.DEFAULT_ANIMATION)
        build_lockup_gif(out, pal, anim.DEFAULT_ANIMATION)
        built |= {n for n in INSTALL if n.endswith(".gif")}
    if everything or only_tab:
        build_tab_icon(out)
        built.add("tab-icon-art.ts")
    if everything or only_terminal:
        build_terminal(out)
    if "--install" in argv:
        print("installing:")
        install(out, built)
        if everything or only_terminal:
            install_terminal()
