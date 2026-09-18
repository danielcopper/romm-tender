#!/usr/bin/env python3
"""The mark reduced for Steam's Quick Access tab strip, and the module the panel draws it from.

Read off a screenshot of the strip rather than measured on the device: the strip
carries nothing but single-tone, free-standing glyphs — a bell, friends, a cog, a
bolt, a note, a question mark, and Decky's plug. A filled disc would be the only
solid body in the row, so the disc goes and the two tones collapse into one. What is left is the pair of sync arrows around the D-pad cross, which is the
mark's own body at full morph.

Every departure from `gen.DEFAULT_GEOMETRY` is in {@link STRIP_GEOMETRY} with its
reason. Nothing else is redrawn here: the arcs come from `gen._arc_d` and
`gen._arrowhead`, the bars from `gen.arm_paths`, and the fold's timing from
`anim.morph_at` — so a change to the mark reaches the strip glyph rather than
leaving the two to drift, which is the whole reason this lives in the generator
instead of beside the panel as hand-written SVG.

**The fold is inverted.** The mark rests as the button diamond and folds to the
cross; the glyph rests as the CROSS and folds to the buttons, because the strip's
resting pose is the one the reader sees all the time. The schedule and easing are
`anim.DEFAULT_ANIMATION`'s, untouched — only the value is taken as `1 - morph`.

Outputs:

    tabicon.py --svg          the resting glyph as a standalone SVG, for looking
    tabicon.py --svg --morph <0..1>   part-way through the fold
    tabicon.py --ts           the module the panel imports
    tabicon.py --stops        the fold's keyframe stops as a table
"""

from __future__ import annotations

from dataclasses import replace

import anim
import gen

# The fold's poses are emitted as SMIL animation values, so the whole loop ships as path
# data and every stop costs bytes twice (two bars; the other two are one
# `rotate(180)`). 36 over the loop puts a stop every 80 ms at the animation's own
# 2.88 s, and the browser interpolates linearly between them — far finer than the
# easing's own curvature at the size this is drawn.
FOLD_SAMPLES = 36

# One decimal instead of the mark's two. The glyph is drawn around 24 px across a
# 200-unit square, so the second decimal is worth about 1/800 of a pixel.
PLACES = 1

# --------------------------------------------------------------------------- #
# Geometry                                                                    #
# --------------------------------------------------------------------------- #
# Everything below is a departure from `gen.DEFAULT_GEOMETRY`; the rest of that
# object stays the reference the mark is drawn from.
#
# * The stroke goes up by 1.3, because a hairline that reads at 512 px disappears
#   at 24. `arrow_half` and `arrow_round` move with it because `Geometry`'s own
#   comment says they want to track `arc_w`; `arrow_len` is a reach along the
#   tangent rather than a width against the stroke, and it is scaled here so the
#   head keeps its proportion — scaling one without the others turns it into
#   either a dart or a paddle.
# * `arc_rot` goes to 0. The 6.34 in the mark pushes both arcs clockwise, which
#   sits the gaps off the horizontal; in a disc that reads as motion, and in a row
#   of upright glyphs it reads as a tilt. At 0 the gaps lie on the horizontal.
# * `dpad_scale` comes down from 0.93. The cross does NOT grow with the stroke —
#   `dpad_scale` is measured against the dot reach, not against `cap_w` — so at a
#   1.3 stroke the arms reach into the arcs. 0.74 is where the gap comes back.
STRIP_GEOMETRY = replace(
    gen.DEFAULT_GEOMETRY,
    arc_w=gen.DEFAULT_GEOMETRY.arc_w * 1.3,
    arrow_len=gen.DEFAULT_GEOMETRY.arrow_len * 1.3,
    arrow_half=gen.DEFAULT_GEOMETRY.arrow_half * 1.3,
    arrow_round=gen.DEFAULT_GEOMETRY.arrow_round * 1.3,
    arc_rot=0.0,
    dpad_scale=0.74,
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


def fold_stops(
    samples: int = FOLD_SAMPLES,
    a: anim.Animation = anim.DEFAULT_ANIMATION,
    g: gen.Geometry = STRIP_GEOMETRY,
) -> list[tuple[float, str, str]]:
    """The fold as (loop fraction, bar A, bar B), with the holds collapsed.

    Sampled uniformly and then thinned: consecutive samples that draw the same
    pair carry no information for an interpolating renderer, and the schedule
    spends just over half of it parked at one pose or the other (0.52 by the
    schedule; 61% of the loop lies between stops this thins to one, because a
    ramp's tails round to the same one-decimal path data). The final stop is
    always emitted at 1.0 so the loop closes on the pose it opened with.
    """
    stops: list[tuple[float, str, str]] = []
    for i in range(samples + 1):
        t = i / samples
        # Inverted: the strip glyph rests as the cross, which is the mark's
        # morph 1. The schedule and easing are untouched.
        arms = gen.arm_paths(g, 1.0 - anim.morph_at(t, a), PLACES)
        pose = (arms[0], arms[1])
        last = t >= 1.0
        if stops and stops[-1][1:] == pose and not last:
            continue
        stops.append((t, *pose))
    # An interpolating renderer needs every value to carry the same commands in
    # the same order; what a renderer does with a pose that does not is its own
    # business — an error or a discrete fallback, either way not the fold.
    # `gen._arm_d` has a second branch for a vanishing outer radius, so this is a
    # geometry change away rather than impossible — assert it instead of saying it.
    shapes = {_commands(d) for _, a, b in stops for d in (a, b)}
    if len(shapes) != 1:
        raise AssertionError(f"fold stops disagree on their command sequence: {sorted(shapes)}")
    return stops


def _commands(path: str) -> str:
    """A path's command letters, in order — its shape for interpolation."""
    return "".join(c for c in path if c.isalpha())


# --------------------------------------------------------------------------- #
# The glyph                                                                   #
# --------------------------------------------------------------------------- #
def glyph(g: gen.Geometry = STRIP_GEOMETRY, morph: float = 1.0, size: int = 200) -> str:
    """The glyph as a standalone one-tone SVG, at a fixed pose. For looking at.

    What the panel renders is the module {@link ts_module} emits, not this —
    the panel's copy carries the whole fold and asks for `currentColor`, which a
    file on disk cannot do. This one exists so a change to the form can be seen
    without a build.
    """
    arc_d, head = ring_arc(g)
    arms = "".join(f'<path d="{d}"/>' for d in gen.arm_paths(g, morph, PLACES)[:2] if d)
    ring = (
        f'<path d="{arc_d}" fill="none" stroke="currentColor" stroke-width="{g.arc_w:.2f}" stroke-linecap="round"/>'
        f'<polygon points="{head}" fill="currentColor" stroke="currentColor" '
        f'stroke-width="{g.arrow_round:.2f}" stroke-linejoin="round"/>'
    )
    turn = f'transform="rotate(180 {gen.CX:.0f} {gen.CY:.0f})"'
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" '
        f'viewBox="0 0 {gen.VIEW:.0f} {gen.VIEW:.0f}" fill="currentColor" color="#dcdedf">'
        f"<g>{ring}</g><g {turn}>{ring}</g>"
        f"<g>{arms}</g><g {turn}>{arms}</g>"
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
 * Everything here is point-symmetric about the centre of the {@link
 * TAB_ICON_VIEW_BOX} square, so each half is emitted once and drawn twice under
 * one `rotate(180)`: {@link TAB_ICON_ARC} is the upper sync arc and {@link
 * TAB_ICON_FOLD} carries two of the body's four bars.
 */

'''


def ts_module(
    samples: int = FOLD_SAMPLES,
    a: anim.Animation = anim.DEFAULT_ANIMATION,
    g: gen.Geometry = STRIP_GEOMETRY,
) -> str:
    arc_d, head = ring_arc(g)
    stops = fold_stops(samples, a, g)
    rest = stops[0]
    body: list[str] = ["  TAB_ICON_REST"]
    for t, p, q in stops[1:-1]:
        body.append(f'  {{ at: {t:.4f}, a: "{p:s}", b: "{q:s}" }}')
    # The closing stop repeats the resting pose at t=1, which is what makes the
    # loop seamless. Written as a reference so the two cannot be edited apart.
    body.append(f"  {{ at: {stops[-1][0]:.4f}, a: TAB_ICON_REST.a, b: TAB_ICON_REST.b }}")
    rows = ",\n".join(body)
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
        f"/** One pose of the body: two of its four bars, at a fraction of the loop. */\n"
        f"export interface TabIconFoldStop {{\n"
        f"  readonly at: number;\n"
        f"  readonly a: string;\n"
        f"  readonly b: string;\n"
        f"}}\n\n"
        f"/**\n"
        f" * The pose the glyph sits in: the D-pad cross, ring level.\n"
        f" *\n"
        f" * This is what a renderer puts in each path's own `d`, so an engine that\n"
        f" * animates nothing draws exactly this. It is the fold's first stop and its\n"
        f" * last, by reference rather than by a second copy of the data.\n"
        f" */\n"
        f"export const TAB_ICON_REST: TabIconFoldStop = "
        f'{{ at: {rest[0]:.4f}, a: "{rest[1]:s}", b: "{rest[2]:s}" }};\n\n'
        f"/**\n"
        f" * The fold, first stop first, opening and closing on {{@link TAB_ICON_REST}}.\n"
        f" *\n"
        f" * Every stop's path data carries the same command sequence, so one\n"
        f" * interpolates into the next. Stops where the pose does not move are not\n"
        f" * emitted — three spans, because the rest hold is split by the loop boundary.\n"
        f" */\n"
        f"export const TAB_ICON_FOLD: readonly TabIconFoldStop[] = [\n{rows},\n];\n\n"
        f"/** Seconds one loop takes — the mark's own {a.frames} frames at {a.fps} fps. */\n"
        f"export const TAB_ICON_LOOP_SECONDS = {a.frames / a.fps:.2f};\n"
    )


if __name__ == "__main__":
    import sys

    argv = sys.argv[1:]
    morph = float(argv[argv.index("--morph") + 1]) if "--morph" in argv else 1.0
    if "--ts" in argv:
        print(ts_module(), end="")
    elif "--stops" in argv:
        print(f"{len(fold_stops())} stops")
        for t, p, _ in fold_stops():
            print(f"  {t:6.4f}  {p[:48]}…")
    else:
        print(glyph(morph=morph))
