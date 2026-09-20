#!/usr/bin/env python3
"""The mark reduced for Steam's Quick Access tab strip, and the module the panel draws it from.

Read off a screenshot of the strip rather than measured on the device: the strip
carries nothing but single-tone, free-standing glyphs — a bell, friends, a cog, a
bolt, a note, a question mark, and Decky's plug. Every one of them is a bare
silhouette, so a disc behind ours would be the only backing plate in the row: the
disc goes and the two tones collapse into one. What is left is the pair of sync
arrows around the button bars — the mark's own body at rest, drawn as solid
capsules. Nothing marks the four button positions: at the size the glyph asks for
a bar is too narrow to hold a second shape. `docs/architecture/qam-panel.md`
holds that arithmetic.

**The glyph does not move.** It shipped animated, and the motion cost roughly 29%
of one core for as long as the menu was open — measured on the device over CDP.
`docs/architecture/qam-panel.md` holds the reading in full.

Every departure from `gen.DEFAULT_GEOMETRY` is in {@link STRIP_GEOMETRY} with its
reason. Nothing else is redrawn here: the arcs come from `gen._arc_d` and
`gen._arrowhead`, the bars from `gen.arm_paths` at rest — so a change to the mark
reaches the strip glyph rather than leaving the two to drift, which is the whole
reason this lives in the generator instead of beside the panel as hand-written
SVG.

Outputs:

    tabicon.py --svg          the glyph as a standalone SVG, for looking
    tabicon.py --ts           the module the panel imports
"""

from __future__ import annotations

from dataclasses import replace

import gen

# One decimal instead of the mark's two, for the only numbers this module formats
# itself: the two bars. It does not reach the arc — `TAB_ICON_ARC.d` and its
# arrowhead arrive already formatted from `gen._arc_d` and `gen._arrowhead`, at
# three decimals and two. The glyph asks for 28 px across a 200-unit square, so
# the second decimal is worth about 1/700 of a pixel.
PLACES = 1

# --------------------------------------------------------------------------- #
# Geometry                                                                    #
# --------------------------------------------------------------------------- #
# Two departures from `gen.DEFAULT_GEOMETRY`; the rest of that object stays the
# reference the mark is drawn from.
#
# * `arc_rot` goes to 0. The 6.34 in the mark pushes both arcs clockwise, which
#   sits the gaps off the horizontal; in a disc that reads as motion, and in a row
#   of upright glyphs it reads as a tilt. At 0 the gaps lie on the horizontal.
# * The arc stroke goes up by 1.3, and the three arrowhead numbers move with it.
#   `Geometry`'s own comment asks for two of them — the head is wider than the
#   stroke it caps, so `arrow_half` and `arrow_round` track `arc_w` — and
#   `arrow_len` is scaled here beside them because a head that widens without
#   lengthening reads as a paddle rather than an arrow. This is a legibility
#   departure at strip size, not a redrawing of the mark: at the
#   28 px the glyph asks for, the mark's own 15.5 stroke lands at 2.17 px — the
#   thinnest thing in a row of solid silhouettes.
#   `docs/architecture/qam-panel.md` holds the arithmetic and the argument.
STRIP_GEOMETRY = replace(
    gen.DEFAULT_GEOMETRY,
    arc_rot=0.0,
    arc_w=gen.DEFAULT_GEOMETRY.arc_w * 1.3,
    arrow_len=gen.DEFAULT_GEOMETRY.arrow_len * 1.3,
    arrow_half=gen.DEFAULT_GEOMETRY.arrow_half * 1.3,
    arrow_round=gen.DEFAULT_GEOMETRY.arrow_round * 1.3,
)


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


# --------------------------------------------------------------------------- #
# The glyph                                                                   #
# --------------------------------------------------------------------------- #
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
    turn = f'transform="rotate(180 {gen.CX:.0f} {gen.CY:.0f})"'
    # The bars declare no fill, so an ancestor is what keeps them off SVG's
    # initial `fill`, which is black. The group asks for it here as well as the
    # root, because the panel's copy of this structure declares none on its root
    # and the group is all it has — `frontend/src/qam/TabIcon.tsx`.
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" '
        f'viewBox="0 0 {gen.VIEW:.0f} {gen.VIEW:.0f}" fill="currentColor" color="#dcdedf">'
        f"<g>{ring}</g><g {turn}>{ring}</g>"
        f'<g fill="currentColor"><g>{arms}</g><g {turn}>{arms}</g></g>'
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
 * TAB_ICON_BARS} two of the body's four bars.
 */

'''


def ts_module(g: gen.Geometry = STRIP_GEOMETRY) -> str:
    arc_d, head = ring_arc(g)
    bar_a, bar_b = body_bars(g)
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
        f"}} as const;\n"
    )


if __name__ == "__main__":
    import sys

    argv = sys.argv[1:]
    if "--ts" in argv:
        print(ts_module(), end="")
    else:
        print(glyph())
