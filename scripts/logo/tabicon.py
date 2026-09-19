#!/usr/bin/env python3
"""The mark reduced for Steam's Quick Access tab strip, and the module the panel draws it from.

Read off a screenshot of the strip rather than measured on the device: the strip
carries nothing but single-tone, free-standing glyphs — a bell, friends, a cog, a
bolt, a note, a question mark, and Decky's plug. A filled disc would be the only
solid body in the row, so the disc goes and the two tones collapse into one. What
is left is the pair of sync arrows around the button bars — the mark's own body
at rest — with the four button positions punched OUT of the bars, because in one
tone a filled dot has nothing to be filled with that the bar is not already.

**The glyph does not move.** It shipped animated and the cost was measured on the
device over CDP, on the QuickAccess target, in 6-second windows: with the fold
running the task took 1.726 s against 0.0077 s with the animation off — the menu
was open and the glyph drawn in both readings — and layout and style recalc each
ran 720 times, twice a frame at 60 Hz, because animating a path's `d` forces
layout every frame. That is roughly 29% of one core for as long as the
menu is open, for a glyph that was rendering at 24 px across.
`docs/architecture/qam-panel.md` holds the reading in full.

Every departure from `gen.DEFAULT_GEOMETRY` is in {@link STRIP_GEOMETRY} with its
reason. Nothing else is redrawn here: the arcs come from `gen._arc_d` and
`gen._arrowhead`, the bars and the button positions from `gen.arm_paths` and
`gen._diamond` at rest — so a change to the mark reaches the strip glyph rather
than leaving the two to drift, which is the whole reason this lives in the
generator instead of beside the panel as hand-written SVG.

Outputs:

    tabicon.py --svg          the glyph as a standalone SVG, for looking
    tabicon.py --ts           the module the panel imports
"""

from __future__ import annotations

from dataclasses import replace

import gen

# One decimal instead of the mark's two, for the numbers this module formats
# itself: the bars, the hole centres and `TAB_ICON_HOLE_R`. It does not reach the
# arc — `TAB_ICON_ARC.d` and its arrowhead arrive already formatted from
# `gen._arc_d` and `gen._arrowhead`, at three decimals and two. The glyph asks for
# 28 px across a 200-unit square, so the second decimal is worth about 1/700 of a
# pixel.
PLACES = 1

# The button positions are holes rather than dots: the glyph has one tone, so a
# filled dot disappears into the bar it sits on. The hole is narrower than the
# mark's own dot: at the mark's 13.63 a bar 31.38 wide keeps 2.06 units either
# side of each hole, and at 12 it keeps 3.69. Which of the two to cut was the
# owner's pick from renderings, and 12 is what was picked.
HOLE_R = 12.0

# --------------------------------------------------------------------------- #
# Geometry                                                                    #
# --------------------------------------------------------------------------- #
# One departure from `gen.DEFAULT_GEOMETRY`; the rest of that object stays the
# reference the mark is drawn from.
#
# * `arc_rot` goes to 0. The 6.34 in the mark pushes both arcs clockwise, which
#   sits the gaps off the horizontal; in a disc that reads as motion, and in a row
#   of upright glyphs it reads as a tilt. At 0 the gaps lie on the horizontal.
#
# A second departure was DROPPED, and the reason is a choice rather than a
# consequence. The arc stroke and its arrowhead used to be scaled by 1.3 (`arc_w`,
# `arrow_len`, `arrow_half`, `arrow_round`), on the grounds that a hairline that
# reads at 512 px disappears at 24. Nothing else here made that bump unnecessary —
# the owner looked at both renderings at strip size and chose the mark's own
# stroke. So the legibility argument the bump was answering is open again, and now
# at the 28 px the glyph asks for rather than the 24 the 1.3 was picked for. That
# is a device question and is on this cut's list (#1946).
STRIP_GEOMETRY = replace(gen.DEFAULT_GEOMETRY, arc_rot=0.0)


def ring_arc(g: gen.Geometry = STRIP_GEOMETRY) -> tuple[str, str]:
    """The upper arc's path data and its arrowhead's points.

    One arc, not two: the pair is point-symmetric about the centre — the lower arc
    sweeps the same span 180° away — so the glyph draws this one twice under one
    `rotate(180)`. That is the same symmetry {@link gen.arm_paths} rests on.
    """
    half = g.arc_span / 2.0
    a0, a1 = 270.0 - half + g.arc_rot, 270.0 + half + g.arc_rot
    return gen._arc_d(gen.CX, gen.CY, g.arc_r, a0, a1), gen._arrowhead(gen.CX, gen.CY, g.arc_r, a1, g)


def body_bars(g: gen.Geometry = STRIP_GEOMETRY) -> tuple[str, str]:
    """The two bars either side of one hub, at rest, where they merge into a capsule.

    The other two are these reflected through the centre, which is why only the
    first pair is drawn — see {@link gen.arm_paths}.
    """
    a, b = gen.arm_paths(g, 0.0, PLACES)[:2]
    return a, b


def hole_centres(g: gen.Geometry = STRIP_GEOMETRY) -> tuple[tuple[float, float], ...]:
    """All four button positions, in the mark's own draw order.

    Four rather than the two the rest of the glyph emits: a hole is punched by a
    mask, and a mask is not carried by the `rotate(180)` that draws the second
    half of the bars — it covers the finished body, so it has to name every
    position itself.
    """
    _, _, corners = gen._diamond(g, 0.0)
    return tuple((x, y) for _, x, y, _ in corners)


# --------------------------------------------------------------------------- #
# The glyph                                                                   #
# --------------------------------------------------------------------------- #
_MASK_ID = "tab-icon-holes"


def glyph(g: gen.Geometry = STRIP_GEOMETRY, size: int = 200) -> str:
    """The glyph as a standalone one-tone SVG. For looking at.

    What the panel renders is the module {@link ts_module} emits, not this. Both
    ask for `currentColor`; what this one adds is a `color` on the root, pinning
    what that resolves to, where the panel's copy inherits it from the strip
    around it. This one exists so a change to the form can be seen without a
    build.
    """
    arc_d, head = ring_arc(g)
    arms = "".join(f'<path d="{d}"/>' for d in body_bars(g) if d)
    ring = (
        f'<path d="{arc_d}" fill="none" stroke="currentColor" stroke-width="{g.arc_w:.2f}" stroke-linecap="round"/>'
        f'<polygon points="{head}" fill="currentColor" stroke="currentColor" '
        f'stroke-width="{g.arrow_round:.2f}" stroke-linejoin="round"/>'
    )
    # Luminance masking: white keeps the bar, black punches it through. The rect's
    # percentages resolve against the view box, so it covers the whole square
    # whatever the glyph is drawn at.
    holes = "".join(
        f'<circle cx="{x:.{PLACES}f}" cy="{y:.{PLACES}f}" r="{HOLE_R:.{PLACES}f}" fill="#000"/>'
        for x, y in hole_centres(g)
    )
    turn = f'transform="rotate(180 {gen.CX:.0f} {gen.CY:.0f})"'
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" '
        f'viewBox="0 0 {gen.VIEW:.0f} {gen.VIEW:.0f}" fill="currentColor" color="#dcdedf">'
        f"<g>{ring}</g><g {turn}>{ring}</g>"
        f'<mask id="{_MASK_ID}"><rect x="0" y="0" width="100%" height="100%" fill="#fff"/>{holes}</mask>'
        f'<g mask="url(#{_MASK_ID})"><g>{arms}</g><g {turn}>{arms}</g></g>'
        f"</svg>"
    )


# --------------------------------------------------------------------------- #
# The generated module                                                        #
# --------------------------------------------------------------------------- #
_TS_HEADER = '''/**
 * The Quick Access tab glyph's geometry — GENERATED, do not edit.
 *
 * Written by `scripts/logo/build.py --tab-icon` from `scripts/logo/tabicon.py`,
 * which draws it with the mark's own routines. Editing this file by hand is the
 * one way the strip glyph and the mark can drift apart, which is the whole
 * reason it is generated; change `tabicon.STRIP_GEOMETRY` and re-run the build.
 *
 * The arc and the bars are point-symmetric about the centre of the {@link
 * TAB_ICON_VIEW_BOX} square, so each is emitted once and drawn twice under one
 * `rotate(180)`: {@link TAB_ICON_ARC} is the upper sync arc and {@link
 * TAB_ICON_BARS} two of the body's four bars. {@link TAB_ICON_HOLES} names all
 * four button positions instead, because what punches them covers the finished
 * body rather than riding along with one half of it.
 */

'''


def ts_module(g: gen.Geometry = STRIP_GEOMETRY) -> str:
    arc_d, head = ring_arc(g)
    bar_a, bar_b = body_bars(g)
    holes = ",\n".join(f"  {{ cx: {x:.{PLACES}f}, cy: {y:.{PLACES}f} }}" for x, y in hole_centres(g))
    return (
        f"{_TS_HEADER}"
        f'export const TAB_ICON_VIEW_BOX = "0 0 {gen.VIEW:.0f} {gen.VIEW:.0f}";\n\n'
        f"/** The centre both halves are reflected through, as `rotate()` takes it. */\n"
        f'export const TAB_ICON_CENTRE = "{gen.CX:.0f} {gen.CY:.0f}";\n\n'
        f"/** The upper sync arc: a stroked path, capped by a filled arrowhead. */\n"
        f"export const TAB_ICON_ARC = {{\n"
        f'  d: "{arc_d}",\n'
        f"  width: {g.arc_w:.2f},\n"
        f'  head: "{head}",\n'
        f"  headRound: {g.arrow_round:.2f},\n"
        f"}} as const;\n\n"
        f"/** The body: two of its four bars, meeting at one hub as a capsule. */\n"
        f"export const TAB_ICON_BARS = {{\n"
        f'  a: "{bar_a}",\n'
        f'  b: "{bar_b}",\n'
        f"}} as const;\n\n"
        f"/** One button position, punched out of the bar it sits on. */\n"
        f"export interface TabIconHole {{\n"
        f"  readonly cx: number;\n"
        f"  readonly cy: number;\n"
        f"}}\n\n"
        f"/** All four of them, in the mark's own draw order. */\n"
        f"export const TAB_ICON_HOLES: readonly TabIconHole[] = [\n{holes},\n];\n\n"
        f"/** The radius each one is cut at, narrower than the mark's own dot. */\n"
        f"export const TAB_ICON_HOLE_R = {HOLE_R:.{PLACES}f};\n"
    )


if __name__ == "__main__":
    import sys

    argv = sys.argv[1:]
    if "--ts" in argv:
        print(ts_module(), end="")
    else:
        print(glyph())
