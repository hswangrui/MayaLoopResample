# SPDX-License-Identifier: GPL-3.0-or-later
"""Host-independent sampling, based on Loop Resample by YLIANG (1.0.3)."""
from __future__ import division

from bisect import bisect_right
from math import sqrt


class ResampleError(ValueError):
    pass


def add(a, b):
    return tuple(x + y for x, y in zip(a, b))


def sub(a, b):
    return tuple(x - y for x, y in zip(a, b))


def mul(a, s):
    return tuple(x * s for x in a)


def dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def length(a):
    return sqrt(dot(a, a))


def lerp(a, b, t):
    return add(mul(a, 1.0 - t), mul(b, t))


def cross(a, b):
    return (a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0])


def normal(points):
    result = (0.0, 0.0, 0.0)
    origin = points[0]
    for i in range(1, len(points)-1):
        result = add(result, cross(sub(points[i], origin), sub(points[i+1], origin)))
    return result


def smooth(points, closed, strength):
    source = list(points)
    current = list(source)
    active = range(len(points)) if closed else range(1, len(points)-1)
    for unused in range(4):
        for factor in (0.5, -0.53):
            previous = current[:]
            for i in active:
                average = mul(add(previous[i-1], previous[(i+1) % len(points)]), 0.5)
                current[i] = lerp(previous[i], average, factor)
    for i in active:
        displacement = mul(sub(current[i], source[i]), strength)
        cap = 0.25 * min(length(sub(source[i], source[i-1])),
                         length(sub(source[i], source[(i+1) % len(points)])))
        size = length(displacement)
        current[i] = add(source[i], mul(displacement, min(1.0, cap/size))) if size else source[i]
    return current


def catmull(points, index, factor, closed):
    count = len(points)
    p1, p2 = points[index], points[(index+1) % count]
    p0 = points[(index-1) % count] if closed or index else sub(mul(p1, 2.0), p2)
    p3 = points[(index+2) % count] if closed or index+2 < count else sub(mul(p2, 2.0), p1)
    t0 = 0.0
    t1 = max(length(sub(p1, p0)), 1e-12)**0.5
    t2 = t1 + max(length(sub(p2, p1)), 1e-12)**0.5
    t3 = t2 + max(length(sub(p3, p2)), 1e-12)**0.5
    t = t1 + factor*(t2-t1)
    a = lerp(p0, p1, (t-t0)/(t1-t0))
    b = lerp(p1, p2, (t-t1)/(t2-t1))
    c = lerp(p2, p3, (t-t2)/(t3-t2))
    d = lerp(a, b, (t-t0)/(t2-t0))
    e = lerp(b, c, (t-t1)/(t3-t1))
    return lerp(d, e, (t-t1)/(t2-t1))


def _allocate(total, spans):
    if total < len(spans):
        raise ResampleError('Too few segments to retain UV/material/hard-edge boundaries.')
    remaining = total - len(spans)
    size = sum(spans)
    quotas = [remaining*s/size for s in spans]
    result = [1 + int(q) for q in quotas]
    order = sorted(range(len(spans)), key=lambda i: (-(quotas[i]-int(quotas[i])), -spans[i], i))
    for i in order[:total-sum(result)]:
        result[i] += 1
    return result


def sample(points, segments, closed=False, smoothing=0.0, even=0.0, anchors=()):
    """Return positions and source-edge parameters; open endpoints stay fixed."""
    minimum = 3 if closed else 1
    if segments < minimum or segments > 2048:
        raise ResampleError('Segment count must be between %d and 2048.' % minimum)
    if not 0.0 <= smoothing <= 1.0 or not 0.0 <= even <= 1.0:
        raise ResampleError('Smooth and Even must be between 0 and 1.')
    source_count = len(points) if closed else len(points)-1
    if source_count < minimum:
        raise ResampleError('Not enough source vertices.')
    if any(length(sub(points[(i+1) % len(points)], points[i])) <= 1e-12 for i in range(source_count)):
        raise ResampleError('The source contains a zero-length edge.')
    controls = smooth(points, closed, smoothing)
    table, distances, control_distances = [controls[0]], [0.0], [0.0]
    for i in range(source_count):
        for step in range(1, 9):
            p = controls[(i+1) % len(controls)] if step == 8 else catmull(controls, i, step/8.0, closed)
            distance = length(sub(p, table[-1]))
            if distance > 1e-14:
                table.append(p)
                distances.append(distances[-1] + distance)
        control_distances.append(distances[-1])
    measure = [0.0]
    for i in range(source_count):
        span = control_distances[i+1]-control_distances[i]
        if span <= 1e-12:
            raise ResampleError('A smoothed segment has collapsed.')
        measure.append(measure[-1] + span**even)
    pins = sorted(set([0, source_count] + [int(a) for a in anchors]))
    if pins[0] < 0 or pins[-1] > source_count:
        raise ResampleError('Invalid curve anchor.')
    allocation = _allocate(segments, [measure[b]-measure[a] for a, b in zip(pins, pins[1:])])
    samples = []
    for a, b, count in zip(pins, pins[1:], allocation):
        for j in range(count):
            samples.append(measure[a] + (measure[b]-measure[a])*j/count)
    if not closed:
        samples.append(measure[-1])
    positions, parameters = [], []
    for value in samples:
        segment = min(source_count-1, max(0, bisect_right(measure, value)-1))
        t = (value-measure[segment])/(measure[segment+1]-measure[segment])
        distance = control_distances[segment] + t*(control_distances[segment+1]-control_distances[segment])
        k = min(len(distances)-1, max(1, bisect_right(distances, distance)))
        factor = (distance-distances[k-1])/(distances[k]-distances[k-1])
        positions.append(lerp(table[k-1], table[k], factor))
        parameters.append(segment+t)
    if not closed:
        positions[0], positions[-1] = points[0], points[-1]
    return positions, parameters


def edge(a, b):
    return (min(a, b), max(a, b))


def face_edges(face):
    return [edge(a, b) for a, b in zip(face, face[1:]+face[:1])]


def paths_from_edges(edges):
    adjacency = {}
    for a, b in set(edge(*e) for e in edges):
        if a == b:
            raise ResampleError('Degenerate selected edge.')
        adjacency.setdefault(a, set()).add(b)
        adjacency.setdefault(b, set()).add(a)
    if not adjacency:
        raise ResampleError('Select one or more continuous mesh edge paths.')
    if any(len(neighbors) > 2 for neighbors in adjacency.values()):
        raise ResampleError('Selected paths branch or intersect at a vertex.')
    remaining, result = set(adjacency), []
    while remaining:
        seed = min(remaining)
        component, pending = set(), [seed]
        while pending:
            v = pending.pop()
            if v in component:
                continue
            component.add(v)
            pending.extend(adjacency[v]-component)
        endpoints = sorted(v for v in component if len(adjacency[v]) == 1)
        if len(endpoints) not in (0, 2):
            raise ResampleError('Selection is not a simple path.')
        start = endpoints[0] if endpoints else min(component)
        ordered, previous, current = [], None, start
        while current not in ordered:
            ordered.append(current)
            candidates = sorted(adjacency[current] - ({previous} if previous is not None else set()))
            if not candidates:
                break
            previous, current = current, candidates[0]
        if len(ordered) != len(component):
            raise ResampleError('Cannot order selected path.')
        result.append((ordered, not endpoints))
        remaining -= component
    return result


def _face_cost(ids, points):
    poly = [points[v] for v in ids]
    lengths = [length(sub(a, b)) for a, b in zip(poly, poly[1:]+poly[:1])]
    mean = sum(lengths)/len(lengths)
    area = length(normal(poly))*0.5
    if min(lengths) <= 1e-12 or area <= mean*mean*1e-8:
        return None
    if len(ids) == 4:
        n1 = cross(sub(poly[1], poly[0]), sub(poly[2], poly[0]))
        n2 = cross(sub(poly[2], poly[0]), sub(poly[3], poly[0]))
        if dot(n1, n2) <= 0:
            return None
    quality = 0.7*(max(lengths)/min(lengths)-1.0)**2 + 0.15*mean*mean/area
    return (0.75 if len(ids) == 4 else 1.35)*min(quality, 50.0)


def bridge(inner, outer, closed, points, waypoints=()):
    """Monotone strip DP: quads plus triangles, constrained by protected rungs."""
    ni, no = len(inner)-(not closed), len(outer)-(not closed)
    if ni*no > 1000000:
        raise ResampleError('Strip is too large for the Python solver (1,000,000 states).')
    a = inner + inner[:1] if closed else inner
    b = outer + outer[:1] if closed else outer
    bounds = sorted(set([(0, 0), (ni, no)] + list(waypoints)))
    if any(x[0] > y[0] or x[1] > y[1] for x, y in zip(bounds, bounds[1:])):
        raise ResampleError('UV/material boundaries require crossing strip connections.')
    scale = max(length(sub(points[x], points[y])) for x, y in ((a[0], b[0]), (a[0], a[-1]), (b[0], b[-1])))
    scale = max(scale, 1e-9)
    result = []
    for (si, sj), (ei, ej) in zip(bounds, bounds[1:]):
        di, dj = ei-si, ej-sj
        moves = [(1, 1)]
        if di > dj:
            moves.append((1, 0))
        if di < dj:
            moves.append((0, 1))
        costs, parents = {(si, sj): 0.0}, {}
        for total in range(si+sj, ei+ej+1):
            for i in range(max(si, total-ej), min(ei, total-sj)+1):
                j = total-i
                current = costs.get((i, j))
                if current is None:
                    continue
                for dx, dy in moves:
                    x, y = i+dx, j+dy
                    if x > ei or y > ej:
                        continue
                    ids = [a[i], a[x], b[y], b[j]] if dx and dy else ([a[i], a[x], b[j]] if dx else [a[i], b[y], b[j]])
                    cost = _face_cost(ids, points)
                    if cost is None:
                        continue
                    rung = length(sub(points[a[x]], points[b[y]]))/scale
                    score = current + cost + 1.2*rung*rung + 0.8*abs(x/ni-y/no)
                    if score < costs.get((x, y), float('inf')):
                        costs[x, y] = score
                        parents[x, y] = ((i, j), ids)
        cursor, faces = (ei, ej), []
        while cursor != (si, sj):
            if cursor not in parents:
                raise ResampleError('Cannot build a non-degenerate strip; try less smoothing or a different selection.')
            cursor, ids = parents[cursor]
            faces.append(ids)
        result.extend(reversed(faces))
    return result
