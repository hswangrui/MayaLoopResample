# SPDX-License-Identifier: GPL-3.0-or-later
"""Conservative polygon-strip rebuilding and face-corner attribute transfer."""
from __future__ import division

from .geometry import (ResampleError, add, sub, mul, dot, length, normal, lerp,
                       edge, face_edges, paths_from_edges, sample, bridge, _face_cost)


class Mesh(object):
    def __init__(self, points, faces, uv_sets=None, materials=None, hard_edges=(), uv_ids=None):
        self.points = [tuple(p) for p in points]
        self.faces = [list(f) for f in faces]
        self.uv_sets = uv_sets or {}
        self.uv_ids = uv_ids or {}
        self.materials = list(materials) if materials is not None else [0]*len(faces)
        self.hard_edges = set(edge(*e) for e in hard_edges)


def _center(face, points):
    value = (0.0, 0.0, 0.0)
    for v in face:
        value = add(value, points[v])
    return mul(value, 1.0/len(face))


def _components(items, neighbors):
    remaining, result = set(items), []
    while remaining:
        pending, group = [min(remaining)], set()
        while pending:
            item = pending.pop()
            if item in group:
                continue
            group.add(item)
            pending.extend(neighbors.get(item, set()) - group)
        remaining -= group
        result.append(sorted(group))
    return result


def _align(inner, outer, closed, edge_faces, points):
    candidates = []
    for reverse in (False, True):
        oriented = list(reversed(outer)) if reverse else list(outer)
        for shift in range(len(outer)) if closed else (0,):
            order = oriented[shift:] + oriented[:shift]
            links = int(edge(inner[0], order[0]) in edge_faces)
            if not closed:
                links += int(edge(inner[-1], order[-1]) in edge_faces)
            distance = length(sub(points[inner[0]], points[order[0]]))
            if not closed:
                distance += length(sub(points[inner[-1]], points[order[-1]]))
            candidates.append((-links, distance, order))
    return min(candidates)[2]


def _uv_at(mesh, name, fid, vertex):
    corners = mesh.uv_sets[name][fid]
    if corners is None:
        return None
    return corners[mesh.faces[fid].index(vertex)]


def _uv_id(mesh, name, fid, vertex):
    if mesh.uv_sets[name][fid] is None:
        return None
    corner = mesh.faces[fid].index(vertex)
    if name in mesh.uv_ids:
        return mesh.uv_ids[name][fid][corner]
    return (vertex, tuple(mesh.uv_sets[name][fid][corner]))


def _protected(mesh, edge_faces):
    result = set(mesh.hard_edges)
    for e, fids in edge_faces.items():
        if len(fids) != 2:
            continue
        a, b = fids
        if mesh.materials[a] != mesh.materials[b]:
            result.add(e)
        for name in mesh.uv_sets:
            for v in e:
                x, y = _uv_at(mesh, name, a, v), _uv_at(mesh, name, b, v)
                if x is None or y is None:
                    if x != y:
                        result.add(e)
                elif (_uv_id(mesh, name, a, v) != _uv_id(mesh, name, b, v) or
                      any(abs(s-t) > 1e-6 for s, t in zip(x, y))):
                    result.add(e)
    return result


def resample_mesh(mesh, selected_edges, delta=0, segments=None, smoothing=0.0, even=0.0):
    paths = paths_from_edges(selected_edges)
    selected = set(edge(*e) for e in selected_edges)
    edge_faces, vertex_faces = {}, {}
    for fid, face in enumerate(mesh.faces):
        for e in face_edges(face):
            edge_faces.setdefault(e, []).append(fid)
        for v in face:
            vertex_faces.setdefault(v, set()).add(fid)
    if not selected.issubset(edge_faces):
        raise ResampleError('Selected edges are not part of the mesh.')
    affected = set(fid for e in selected for fid in edge_faces[e])
    local_edges = set(e for fid in affected for e in face_edges(mesh.faces[fid]))
    if any(len(edge_faces[e]) > 2 for e in local_edges):
        raise ResampleError('Non-manifold edges in the affected region are not supported.')
    protected = _protected(mesh, edge_faces)
    neighbors = dict((f, set()) for f in affected)
    for e in local_edges-selected:
        fids = set(edge_faces[e]) & affected
        for f in fids:
            neighbors[f].update(fids-{f})
    groups = _components(affected, neighbors)
    anchors = [set([0]) for unused in paths]
    for pid, (vertices, closed) in enumerate(paths):
        rail_edges = set(face_edges(vertices) if closed else [edge(a, b) for a, b in zip(vertices, vertices[1:])])
        for i, v in enumerate(vertices):
            if any(v in e for e in protected-rail_edges):
                anchors[pid].add(i)
            adjacent = [e for e in rail_edges if v in e]
            if len(adjacent) == 2 and (adjacent[0] in mesh.hard_edges) != (adjacent[1] in mesh.hard_edges):
                anchors[pid].add(i)
        removable = vertices if closed else vertices[1:-1]
        if any(vertex_faces.get(v, set())-affected for v in removable):
            raise ResampleError('A selected interior vertex touches faces outside the supported one-row strips.')
    plans = []
    path_edge_sets = [set(face_edges(v) if c else [edge(a, b) for a, b in zip(v, v[1:])]) for v, c in paths]
    for group in groups:
        all_edges = set(e for fid in group for e in face_edges(mesh.faces[fid]))
        pids = [p for p, es in enumerate(path_edge_sets) if es & all_edges]
        if len(pids) > 2 or not pids:
            raise ResampleError('Only simple strips between one or two selected paths are supported.')
        for p in pids:
            if not path_edge_sets[p].issubset(all_edges):
                raise ResampleError('A selected path crosses a pole or changes strip structure; split the selection.')
        inner_id = pids[0]
        inner, closed = paths[inner_id]
        boundary = set(e for e in all_edges if sum(f in group for f in edge_faces[e]) == 1)
        if len(group) == 1 and len(pids) == 1:
            face = mesh.faces[group[0]]
            if not set(inner).issubset(face):
                raise ResampleError('Unsupported cap boundary.')
            plans.append(dict(kind='cap', faces=group, inner=inner_id, outer=None, closed=closed))
            continue
        if len(pids) == 2:
            outer_id = pids[1]
            outer, other_closed = paths[outer_id]
            if closed != other_closed:
                raise ResampleError('Cannot bridge an open path to a closed loop.')
            outer = _align(inner, outer, closed, edge_faces, mesh.points)
            anchors[outer_id].add(paths[outer_id][0].index(outer[0]))
        else:
            outer_id = None
            other_vertices = set(v for fid in group for v in mesh.faces[fid])-set(inner)
            if len(other_vertices) == 1 and all(len(mesh.faces[f]) == 3 for f in group):
                plans.append(dict(kind='fan', faces=group, inner=inner_id,
                                  pole=min(other_vertices), outer=None, closed=closed))
                continue
            leftover = boundary-path_edge_sets[inner_id]
            outside = paths_from_edges(leftover)
            if len(outside) != 1:
                raise ResampleError('Region has holes or multiple outer boundaries.')
            outer, outer_closed = outside[0]
            if closed:
                if not outer_closed or set(outer) & set(inner):
                    raise ResampleError('Unsupported closed strip boundary.')
                outer = _align(inner, outer, True, edge_faces, mesh.points)
            else:
                if outer_closed or set((outer[0], outer[-1])) != set((inner[0], inner[-1])):
                    raise ResampleError('Open strip does not have two end rungs.')
                if outer[0] != inner[0]:
                    outer.reverse()
                outer = outer[1:-1]
                if len(outer) < 2:
                    raise ResampleError('Outer strip rail is too short.')
        if set(v for fid in group for v in mesh.faces[fid]) != set(inner) | set(outer):
            raise ResampleError('Region contains interior vertices; only single-row strips are supported.')
        for v in (inner[0], outer[0]):
            for p in pids:
                if v in paths[p][0]:
                    anchors[p].add(paths[p][0].index(v))
        plans.append(dict(kind='strip', faces=group, inner=inner_id, outer=outer_id,
                          outer_vertices=outer, closed=closed))
    points = list(mesh.points)
    provenance = dict((i, (i, i, 0.0)) for i in range(len(points)))
    generated, parameters, old_to_new = [], [], {}
    for pid, (vertices, closed) in enumerate(paths):
        count = len(vertices)-(not closed)
        target = int(segments) if segments is not None else count+int(delta)
        coords, params = sample([mesh.points[v] for v in vertices], target, closed, smoothing, even, anchors[pid])
        ids = []
        for index, (coord, u) in enumerate(zip(coords, params)):
            base = min(int(u), len(vertices)-1)
            factor = u-base
            if abs(factor) < 1e-8:
                a = b = vertices[base]
            elif abs(1.0-factor) < 1e-8:
                a = b = vertices[(base+1) % len(vertices)]
                factor = 0.0
            else:
                a, b = vertices[base], vertices[(base+1) % len(vertices)]
            if not closed and index in (0, len(coords)-1):
                vid = vertices[0] if index == 0 else vertices[-1]
            else:
                vid = len(points)
                points.append(coord)
            provenance[vid] = (a, b, factor)
            ids.append(vid)
            if a == b:
                old_to_new[a] = vid
        generated.append(ids)
        parameters.append(params)
    removed = set(v for vertices, closed in paths for v in (vertices if closed else vertices[1:-1]))
    output_faces, output_sources, output_uvs, output_materials = [], [], dict((n, []) for n in mesh.uv_sets), []
    output_uv_ids = dict((n, []) for n in mesh.uv_sets)
    source_centers = [_center(f, mesh.points) for f in mesh.faces]
    hard_edges = set(e for e in mesh.hard_edges if not (set(e) & removed))
    for e in mesh.hard_edges:
        if e[0] in old_to_new or e[1] in old_to_new:
            hard_edges.add(edge(old_to_new.get(e[0], e[0]), old_to_new.get(e[1], e[1])))
    for pid, (vertices, closed) in enumerate(paths):
        ids, us = generated[pid], parameters[pid]
        count = len(ids)-(not closed)
        original_count = len(vertices)-(not closed)
        for i in range(count):
            start = us[i]
            end = us[i+1] if i+1 < len(us) else float(original_count)
            if any(edge(vertices[j], vertices[(j+1) % len(vertices)]) in mesh.hard_edges
                   for j in range(int(start+1e-8), min(original_count, int(end-1e-8)+1))):
                hard_edges.add(edge(ids[i], ids[(i+1) % len(ids)]))
    def append_face(face, source_id, group, island):
        center = _center(face, points)
        reference = normal([mesh.points[v] for v in mesh.faces[source_id]])
        if dot(normal([points[v] for v in face]), reference) < 0:
            face = list(reversed(face))
        if len(set(face)) != len(face) or length(normal([points[v] for v in face])) <= 1e-12:
            raise ResampleError('Rebuild produced a degenerate face.')
        if len(face) <= 4 and _face_cost(face, points) is None:
            raise ResampleError('Rebuild produced a folded or degenerate face.')
        output_faces.append(face)
        output_sources.append(source_id)
        output_materials.append(mesh.materials[source_id])
        for name in mesh.uv_sets:
            values, uv_tokens = [], []
            for vid in face:
                a, b, t = provenance[vid]
                candidates = [f for f in island if a in mesh.faces[f] and b in mesh.faces[f]]
                if not candidates:
                    raise ResampleError('Cannot preserve a UV boundary at this segment count.')
                fid = min(candidates, key=lambda f: (f != source_id, length(sub(source_centers[f], center)), f))
                uv_a, uv_b = _uv_at(mesh, name, fid, a), _uv_at(mesh, name, fid, b)
                values.append(None if uv_a is None or uv_b is None else lerp(uv_a, uv_b, t))
                ia, ib = _uv_id(mesh, name, fid, a), _uv_id(mesh, name, fid, b)
                if a == b:
                    uv_tokens.append(ia)
                elif repr(ia) <= repr(ib):
                    uv_tokens.append(('lerp', ia, ib, round(t, 12)))
                else:
                    uv_tokens.append(('lerp', ib, ia, round(1.0-t, 12)))
            if all(v is None for v in values):
                output_uvs[name].append(None)
                output_uv_ids[name].append(None)
            elif any(v is None for v in values):
                raise ResampleError('Partially mapped UV faces are not supported.')
            else:
                output_uvs[name].append(values)
                output_uv_ids[name].append(uv_tokens)
    for fid, face in enumerate(mesh.faces):
        if fid not in affected:
            output_faces.append(list(face))
            output_sources.append(fid)
            output_materials.append(mesh.materials[fid])
            for name in mesh.uv_sets:
                output_uvs[name].append(mesh.uv_sets[name][fid])
                output_uv_ids[name].append([_uv_id(mesh, name, fid, v) for v in face]
                                           if mesh.uv_sets[name][fid] is not None else None)
    for plan in plans:
        pid, group, closed = plan['inner'], plan['faces'], plan['closed']
        inner = generated[pid]
        source_inner = paths[pid][0]
        if plan['kind'] == 'cap':
            original = mesh.faces[group[0]]
            if closed:
                faces = [inner[:]]
            else:
                start = original.index(source_inner[0])
                forward = original[(start+1) % len(original)] == source_inner[1]
                ordered = original if forward else list(reversed(original))
                start = ordered.index(source_inner[0])
                ordered = ordered[start:]+ordered[:start]
                if ordered[:len(source_inner)] != source_inner:
                    raise ResampleError('Selected cap edges are not contiguous in the polygon.')
                faces = [inner + ordered[len(source_inner):]]
        elif plan['kind'] == 'fan':
            faces = [[inner[i], inner[(i+1) % len(inner)], plan['pole']]
                     for i in range(len(inner)-(not closed))]
        else:
            source_outer = plan['outer_vertices']
            if plan['outer'] is None:
                outer = source_outer[:]
            else:
                other = plan['outer']
                original = paths[other][0]
                ids = generated[other][:]
                direction = 1 if original[(original.index(source_outer[0])+1) % len(original)] == source_outer[1] else -1
                if direction < 0:
                    ids.reverse()
                first = old_to_new[source_outer[0]]
                cut = ids.index(first)
                outer = ids[cut:]+ids[:cut]
            rungs = []
            for a, b in protected:
                if a in source_outer and b in source_inner:
                    a, b = b, a
                if a not in source_inner or b not in source_outer:
                    continue
                ai, bi = old_to_new.get(a, a), old_to_new.get(b, b)
                if ai not in inner or bi not in outer:
                    raise ResampleError('Protected strip boundary lost its anchor.')
                rungs.append((inner.index(ai), outer.index(bi)))
            faces = bridge(inner, outer, closed, points, rungs)
        island_neighbors = dict((f, set()) for f in group)
        for e in set(e for f in group for e in face_edges(mesh.faces[f]))-protected:
            adjacent = set(edge_faces[e]) & set(group)
            for f in adjacent:
                island_neighbors[f].update(adjacent-{f})
        islands = _components(group, island_neighbors)
        for face in faces:
            center = _center(face, points)
            support = set(v for vid in face for v in provenance[vid][:2])
            compatible = [island for island in islands
                          if support.issubset(set(v for f in island for v in mesh.faces[f]))]
            if not compatible:
                raise ResampleError('A new face would cross a UV/material/hard-edge boundary.')
            island = min(compatible, key=lambda fs: min(length(sub(source_centers[f], center)) for f in fs))
            source = min(island, key=lambda f: length(sub(source_centers[f], center)))
            append_face(face, source, group, island)
    used = set(v for f in output_faces for v in f)
    keep = [i for i in range(len(points)) if i not in removed and (i < len(mesh.points) or i in used)]
    remap = dict((v, i) for i, v in enumerate(keep))
    if not used.issubset(remap):
        raise ResampleError('A rebuilt face still refers to a removed source vertex.')
    result_faces = [[remap[v] for v in f] for f in output_faces]
    result_edges = set(e for f in result_faces for e in face_edges(f))
    result_hard = set(edge(remap[a], remap[b]) for a, b in hard_edges if a in remap and b in remap)
    result = Mesh([points[v] for v in keep], result_faces, output_uvs, output_materials,
                  result_hard & result_edges, output_uv_ids)
    selected_result = []
    for ids, (original, closed) in zip(generated, paths):
        ids = [remap[v] for v in ids]
        selected_result.extend(face_edges(ids) if closed else [edge(a, b) for a, b in zip(ids, ids[1:])])
    counts = {}
    seen_faces = set()
    for f in result.faces:
        key = tuple(sorted(f))
        if key in seen_faces:
            raise ResampleError('Rebuild would produce duplicate faces.')
        seen_faces.add(key)
        for e in face_edges(f):
            counts[e] = counts.get(e, 0)+1
    if any(counts.get(e, 0) > 2 for e in selected_result):
        raise ResampleError('Rebuild would create non-manifold selected edges.')
    return result, selected_result
