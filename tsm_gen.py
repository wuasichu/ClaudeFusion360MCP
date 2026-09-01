"""Generate Autodesk Fusion TSM (T-Spline Mesh) from a quad control cage.

Grammar (from GrapeTec/T-SPLINE rhparser.cpp, verified against a TSM exported
by Fusion itself):

    f <link> <flag>
    e <link> <knot_interval>
    v <link> <direction>
    l <prev> <next> <opp> <vertex> <face> <edge> <flag>
    0g <x> <y> <z> <weight>

link.vertex is the ORIGIN vertex of the half-edge - confirmed because
vertex(opp) == vertex(next) holds for every link in Fusion's own output.
"""

HEADER = """#TS0200

degree 3
cap-type G1CAPS
star-smoothness 0
units 1 meters
end-conditions SUBD_CREASES
star-knot-rule NURCCS
"""

FOOTER = """
tol 1.00000000000000008e-05
geom-tol 1.00000000000000008e-05
ver 992
behavior-version 14.2.0
compat-version 13.3.0
"""


def build_tsm(verts, quads, knot_interval=1):
    """verts: [(x,y,z), ...]   quads: [(a,b,c,d), ...] consistently wound."""
    nf = len(quads)
    links = []                      # [prev, next, opp, vertex, face, edge, flag]
    directed = {}                   # (origin, dest) -> link id
    edge_ids = {}                   # frozenset({a,b}) -> edge id
    edge_link = {}                  # edge id -> a link on it
    vert_link = {}                  # vertex -> a link starting there

    for f, quad in enumerate(quads):
        if len(quad) != 4:
            raise ValueError("face %d is not a quad: %r" % (f, quad))
        base = 4 * f
        for k in range(4):
            origin = quad[k]
            dest = quad[(k + 1) % 4]
            lid = base + k
            key = frozenset((origin, dest))
            if key not in edge_ids:
                edge_ids[key] = len(edge_ids)
                edge_link[edge_ids[key]] = lid
            links.append([base + (k + 3) % 4,   # prev
                          base + (k + 1) % 4,   # next
                          -1,                   # opp, filled below
                          origin, f, edge_ids[key], 0])
            if (origin, dest) in directed:
                raise ValueError("duplicate directed edge %d->%d - winding is "
                                 "inconsistent between adjacent faces" % (origin, dest))
            directed[(origin, dest)] = lid
            vert_link.setdefault(origin, lid)

    # Twins
    for (origin, dest), lid in directed.items():
        twin = directed.get((dest, origin))
        if twin is None:
            raise ValueError("edge %d-%d has no opposite half-edge - the cage is "
                             "not closed (T-Splines need a watertight cage)"
                             % (origin, dest))
        links[lid][2] = twin

    missing = [v for v in range(len(verts)) if v not in vert_link]
    if missing:
        raise ValueError("vertices not used by any face: %r" % missing[:10])

    # Topology can be perfectly valid while the geometry is degenerate, and
    # Fusion rejects that with an opaque ASM_TSP_IG_SURFACE_MULTIPLE_ISSUES.
    # Catch it here, where the message can actually say what is wrong.
    def _dist2(a, b):
        return sum((x - y) ** 2 for x, y in zip(verts[a], verts[b]))

    tol2 = 1e-12
    coincident = []
    for f, quad in enumerate(quads):
        for k in range(4):
            a, b = quad[k], quad[(k + 1) % 4]
            if _dist2(a, b) < tol2:
                coincident.append((f, a, b))
    if coincident:
        f, a, b = coincident[0]
        raise ValueError(
            "%d quad edge(s) have coincident endpoints - zero-area faces. "
            "First: face %d, vertices %d and %d both at %r. Fusion rejects "
            "these as ASM_TSP_IG_SURFACE_MULTIPLE_ISSUES."
            % (len(coincident), f, a, b, verts[a]))

    dupes = {}
    for i, p in enumerate(verts):
        key = tuple(round(c, 9) for c in p)
        dupes.setdefault(key, []).append(i)
    stacked = {k: v for k, v in dupes.items() if len(v) > 1}
    if stacked:
        k, v = next(iter(stacked.items()))
        raise ValueError(
            "%d position(s) have more than one vertex on them - the cage is "
            "pinched. First: %r shared by vertices %r"
            % (len(stacked), k, v[:6]))

    out = [HEADER]
    for f in range(nf):
        out.append("f %d 0" % (4 * f))
    for e in range(len(edge_ids)):
        out.append("e %d %s" % (edge_link[e], knot_interval))
    for v in range(len(verts)):
        out.append("v %d NORTH" % vert_link[v])
    for l in links:
        out.append("l %d %d %d %d %d %d %d" % tuple(l))
    for (x, y, z) in verts:
        out.append("0g %.10g %.10g %.10g 1" % (x, y, z))
    out.append(FOOTER)

    stats = {"vertices": len(verts), "faces": nf,
             "edges": len(edge_ids), "links": len(links),
             "euler": len(verts) - len(edge_ids) + nf}
    return "\n".join(out), stats


def torus(R=20.0, r=6.0, nu=16, nv=10):
    """Regular all-quad torus - every vertex valence 4, no poles. The cleanest
    possible sanity check for the generator."""
    import math
    verts, quads = [], []
    for i in range(nu):
        a = 2 * math.pi * i / nu
        for j in range(nv):
            b = 2 * math.pi * j / nv
            verts.append(((R + r * math.cos(b)) * math.cos(a),
                          (R + r * math.cos(b)) * math.sin(a),
                          r * math.sin(b)))
    idx = lambda i, j: (i % nu) * nv + (j % nv)
    for i in range(nu):
        for j in range(nv):
            quads.append((idx(i, j), idx(i + 1, j), idx(i + 1, j + 1), idx(i, j + 1)))
    return verts, quads


if __name__ == "__main__":
    import sys
    v, q = torus()
    tsm, stats = build_tsm(v, q)
    print(stats)
    open(sys.argv[1] if len(sys.argv) > 1 else "toro.tsm", "w").write(tsm)
    print("escrito")
