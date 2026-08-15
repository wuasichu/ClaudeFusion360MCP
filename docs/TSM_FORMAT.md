# The TSM Format — Creating T-Splines Programmatically

Autodesk never opened a public API for authoring T-Splines. The official
answer on the forums, still unchanged as of a 2024 follow-up, is that it
cannot be done.

It can. This document is how.

---

## 1. The doorway

`TSplineBody` objects cannot be constructed directly, but the `TSplineBodies`
collection exposes `addByTSMDescription()` and `addByTSMFile()`. Feed it TSM
data and Fusion builds a real, editable Form body.

```python
forms  = rootComp.features.formFeatures
feat   = forms.add()
feat.startEdit()
tb     = feat.tSplineBodies.addByTSMFile(path)   # or addByTSMDescription(text)
tb.name = "MyBody"
feat.finishEdit()
```

Requires a **parametric** design. In direct-modelling mode `formFeatures` is
not available — check `design.designType` first.

`TSplineBody.getTSMDescription()` and `saveAsTSMFile()` go the other way, which
is how the grammar below was verified.

Implemented here as `create_tspline`, `export_tspline_tsm`,
`list_tspline_bodies`.

---

## 2. Grammar

Text format. Header, then index-ordered records, then a footer. The **Nth line
of each type defines entity N** — faces, edges, vertices and links are each
implicitly numbered by order of appearance.

```
#TS0200

degree 3
cap-type G1CAPS
star-smoothness 0
units 1 meters
end-conditions SUBD_CREASES
star-knot-rule NURCCS

f <link> <flag>                                        one incident half-edge
e <link> <knot_interval>                               one incident half-edge
v <link> <direction>                                   NORTH|SOUTH|EAST|WEST
l <prev> <next> <opp> <vertex> <face> <edge> <flag>    half-edge
0g <x> <y> <z> <weight>                                control point

tol 1.00000000000000008e-05
geom-tol 1.00000000000000008e-05
ver 992
behavior-version 14.2.0
compat-version 13.3.0
```

Field names come from `rhparser.cpp` in GrapeTec/T-SPLINE:

```cpp
s >> face->link   >> face->flag;
s >> edge->link   >> edge->interval;
s >> vertex->link >> vertex->direction;
s >> link->previous_link >> link->next_link >> link->opp_link
  >> link->vertex >> link->face >> link->edge >> link->flag;
```

### The one ambiguity that matters

Is `link.vertex` the half-edge's **origin** or its **destination**? The parser
does not say. Verified empirically against Fusion's own export:

```
vertex(opp) == vertex(next)   holds for all 96 links
```

That identity is only true if `link.vertex` is the **origin**. (The twin's
origin is this edge's destination, which is also where the next half-edge in
the face loop starts.)

Get this backwards and Fusion accepts the file but builds inside-out geometry.

### Invariants Fusion's own files satisfy

Any generator must reproduce all of these:

- `prev`/`next` form closed cycles of 4 within each face (quads only)
- `L[L[i].next].face == L[i].face`
- `L[L[i].opp].opp == i` — twins are reciprocal
- `L[L[i].opp].edge == L[i].edge` — twins share an edge id
- exactly **2 links per edge**
- `L[V[i].link].vertex == i` — the `v` record points at a link that starts there
- Euler holds: `V - E + F = 2` for a closed genus-0 surface (0 for a torus)

`tsm_gen.py` checks all of these and refuses to emit a broken file.

### Values observed

| Field | Fusion | Rhino samples |
|---|---|---|
| header | `#TS0200` | `#TS0200` |
| cap-type | `G1CAPS` | `1` |
| end conditions | `end-conditions SUBD_CREASES` | `force-bezier-end-conditions 1` |
| edge interval | `1` | `0.5` |
| vertex direction | all `NORTH` | mixed compass values |

All-`NORTH` vertices work fine. The `0m` compound-grip records in the Rhino
files are not required — Fusion accepts files without them.

---

## 3. Units and the shrink problem

**1 TSM unit = 1 cm.** The `units 1 meters` line is decorative; Fusion reads
coordinates in its internal units. Calibrated against a torus of R=20, r=6
which measured 49.94 cm across.

**The limit surface sits inside the control cage and never touches it.** For a
closed ring of *n* cubic B-spline control points:

```
shrink = (2 + cos(2*pi/n)) / 3
```

| n | factor |
|---|---|
| 8 | 0.9024 |
| 12 | 0.9553 |
| 16 | 0.9746 |
| 20 | 0.9837 |

Verified: torus minor ring, n=10, r=6 → predicted height 2 × 6 × 0.9363 =
11.24 cm, measured 11.231 cm. 0.08% error.

**But do not trust the formula for a whole model.** It describes a uniform
closed ring. On a real cage the widest points often sit at corners or
valence-3 vertices where the surface passes much closer, and the formula
over-compensates. Two builds of the seat came out at 530.0 mm and 515.8 mm
against a 520.0 mm target.

The reliable method:

1. build once,
2. measure in Fusion,
3. scale by `target / measured`,
4. measure again to confirm.

Better still, **normalise the profile against its own widest station** before
applying scale — then changing the silhouette does not invalidate the
calibration. See `WIDTH_NORM` in `asiento_v3.py`.

Recalibrate whenever the cage topology changes. The factor is a property of
the topology, not of the model.

---

## 4. Building a cage

A T-Spline control cage is a subdivision cage: an all-quad, closed, manifold
mesh with consistent winding.

- **All quads.** No triangles, no n-gons.
- **Closed.** Every half-edge needs a twin. An open cage fails.
- **Consistent winding.** If two adjacent faces both traverse an edge in the
  same direction, the surface is inside-out there. `tsm_gen.py` catches this
  as a duplicate directed edge.
- **Valence 3 and 5 are fine** — those become star points, hence the
  `star-smoothness` and `star-knot-rule` settings. Valence 4 everywhere gives
  the cleanest surfaces.
- **Coarse beats dense.** Subdivision does the smoothing. Add control points
  only where a feature needs definition — a tight waist or a thin edge.

The box-surface topology (a lattice with only the boundary shell kept, swept
along a spine) covers most product forms and is what the seat uses.

---

## 5. Sources

- `TSplineBodies` — Fusion API reference
- GrapeTec/T-SPLINE — reader, and `.tsm` samples that seeded the grammar
- kantoku-code/Fusion360-TSplineBodyDoorway — the working `formFeatures.add()`
  → `startEdit()` → `addByTSMFile()` → `finishEdit()` sequence
- Autodesk forum thread "T spline API in Fusion 360" — the official "not
  possible", and the 2024 follow-up that went unanswered

---

## 6. Standing risk

TSM is undocumented by Autodesk. Nothing obliges them to keep accepting it,
and the version fields (`ver 992`, `behavior-version 14.2.0`) suggest it is
tied to internal releases. Run the smoke test after every Fusion update.
