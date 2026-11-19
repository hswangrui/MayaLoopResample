# SPDX-License-Identifier: GPL-3.0-or-later
"""Maya API 2.0 adapter; all undoable mutations belong to the MPxCommand."""
from __future__ import division

import os
import uuid
from maya import cmds
import maya.api.OpenMaya as om

from .geometry import ResampleError, edge
from .topology import Mesh, resample_mesh

_PAYLOADS = {}


def load_plugin():
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'plug-ins', 'loopResample.py')
    if not cmds.pluginInfo(path, query=True, loaded=True):
        cmds.loadPlugin(path, quiet=True)


def selected_mesh():
    selection = om.MGlobal.getActiveSelectionList()
    target, ids = None, set()
    for i in range(selection.length()):
        path, component = selection.getComponent(i)
        if component.isNull() or component.apiType() not in (om.MFn.kMeshEdgeComponent, om.MFn.kMeshVertComponent):
            raise ResampleError('Select mesh edges (or a continuous row of vertices), not objects or faces.')
        path.extendToShape()
        if target is not None and path.fullPathName() != target.fullPathName():
            raise ResampleError('Operate on one mesh at a time.')
        target = path
        fn = om.MFnMesh(path)
        elements = set(om.MFnSingleIndexedComponent(component).getElements())
        if component.apiType() == om.MFn.kMeshEdgeComponent:
            ids.update(elements)
        else:
            ids.update(e for e in range(fn.numEdges) if set(fn.getEdgeVertices(e)).issubset(elements))
    if target is None or not ids:
        raise ResampleError('Select a continuous mesh edge path first.')
    fn = om.MFnMesh(target)
    return target, [edge(*fn.getEdgeVertices(i)) for i in sorted(ids)]


def validate_target(path):
    name = path.fullPathName()
    fn = om.MFnMesh(path)
    if path.isInstanced():
        raise ResampleError('Instanced meshes are not supported. Use a unique duplicate.')
    if cmds.referenceQuery(name, isNodeReferenced=True) or cmds.lockNode(name, query=True, lock=True)[0]:
        raise ResampleError('Referenced or locked meshes cannot be resampled.')
    if cmds.listConnections(name+'.inMesh', source=True, destination=False):
        raise ResampleError('Mesh has construction history/deformers. Work on a copy and delete its history first.')
    for attr in ('outMesh', 'worldMesh'):
        if cmds.listConnections(name+'.'+attr, source=False, destination=True):
            raise ResampleError('Mesh drives downstream geometry. Work on an independent copy.')
    if fn.getColorSetNames():
        raise ResampleError('Color sets are not supported in this version; use a copy without color sets.')
    if any(fn.isNormalLocked(i) for i in range(fn.numNormals)):
        raise ResampleError('Locked custom normals are not supported. Unlock normals on a copy first.')
    for query in (fn.getCreaseEdges, fn.getCreaseVertices):
        try:
            unused, values = query()
        except RuntimeError:
            values = []
        if any(v != 0 for v in values):
            raise ResampleError('Crease weights are not supported; use a copy without creases.')
    sets, components = fn.getConnectedSetsAndMembers(path.instanceNumber(), False)
    for obj, component in zip(sets, components):
        if not obj.hasFn(om.MFn.kShadingEngine) and not component.isNull():
            raise ResampleError('Mesh belongs to a component set. Remove that membership on a copy first.')
    for component_type in (om.MFn.kMeshVertComponent, om.MFn.kMeshEdgeComponent, om.MFn.kMeshPolygonComponent):
        if fn.hasBlindData(component_type):
            raise ResampleError('Mesh blind data cannot be remapped safely in this version.')
    it = om.MItMeshPolygon(path)
    for index in range(fn.numPolygons):
        it.setIndex(index)
        if it.isHoled():
            raise ResampleError('Polygons with holes are not supported.')


def capture_mesh(path):
    om.MFnDependencyNode(path.node()).findPlug('outMesh', False).asMObject()
    fn = om.MFnMesh(path)
    points = [(p.x, p.y, p.z) for p in fn.getPoints(om.MSpace.kObject)]
    counts, connects = fn.getVertices()
    faces, offset = [], 0
    for count in counts:
        faces.append(list(connects[offset:offset+count]))
        offset += count
    uv_sets, identities = {}, {}
    for name in fn.getUVSetNames():
        us, vs = fn.getUVs(name)
        uv_counts, uv_ids = fn.getAssignedUVs(name)
        values, corner_ids, offset = [], [], 0
        for count in uv_counts:
            values.append([(us[i], vs[i]) for i in uv_ids[offset:offset+count]] if count else None)
            corner_ids.append(list(uv_ids[offset:offset+count]) if count else None)
            offset += count
        uv_sets[name], identities[name] = values, corner_ids
    shaders, assignments = fn.getConnectedShaders(path.instanceNumber())
    hard = [edge(*fn.getEdgeVertices(i)) for i in range(fn.numEdges) if not fn.isEdgeSmooth(i)]
    return Mesh(points, faces, uv_sets, list(assignments), hard, identities), list(shaders)


def mesh_data(mesh, current_uv='map1'):
    data = om.MFnMeshData().create()
    fn = om.MFnMesh()
    fn.create([om.MPoint(*p) for p in mesh.points], [len(f) for f in mesh.faces],
              [v for f in mesh.faces for v in f], parent=data)
    for i in range(fn.numEdges):
        fn.setEdgeSmoothing(i, edge(*fn.getEdgeVertices(i)) not in mesh.hard_edges)
    fn.cleanupEdgeSmoothing()
    return data


def _set_uvs(fn, mesh, current_uv, raw=None):
    for name, faces in sorted(mesh.uv_sets.items()):
        if name not in fn.getUVSetNames():
            fn.createUVSet(name)
        fn.clearUVs(name)
        u, v, counts, ids, lookup = [], [], [], [], {}
        for fid, (face, corners) in enumerate(zip(mesh.faces, faces)):
            counts.append(0 if corners is None else len(corners))
            if corners is None:
                continue
            for corner, (vertex, uv) in enumerate(zip(face, corners)):
                key = mesh.uv_ids[name][fid][corner] if name in mesh.uv_ids else (vertex, uv[0], uv[1])
                if key not in lookup:
                    lookup[key] = len(u)
                    u.append(uv[0])
                    v.append(uv[1])
                ids.append(lookup[key])
        if raw is not None:
            u, v, counts, ids = raw[name]
        fn.setUVs(u, v, name)
        fn.assignUVs(counts, ids, name)
    if current_uv in fn.getUVSetNames():
        fn.setCurrentUVSetName(current_uv)
    for name in fn.getUVSetNames():
        if name not in mesh.uv_sets and len(fn.getUVSetNames()) > 1:
            fn.deleteUVSet(name)


def _copy_data(node):
    data = om.MFnMeshData().create()
    om.MFnMesh().copy(node, data)
    return data


def _component(kind, ids):
    fn = om.MFnSingleIndexedComponent()
    obj = fn.create(kind)
    fn.addElements(ids)
    return obj


class Snapshot(object):
    def __init__(self):
        self.path, self.selected_edges = selected_mesh()
        validate_target(self.path)
        self.handle = om.MObjectHandle(self.path.node())
        self.selection = om.MGlobal.getActiveSelectionList()
        self.mesh, self.shaders = capture_mesh(self.path)
        mesh_fn = om.MFnMesh(self.path)
        self.current_uv = mesh_fn.currentUVSetName()
        self.raw_uvs = dict((name, tuple(list(a) for a in mesh_fn.getUVs(name)+mesh_fn.getAssignedUVs(name)))
                           for name in mesh_fn.getUVSetNames())
        fn = om.MFnDependencyNode(self.path.node())
        try:
            cached = fn.findPlug('cachedInMesh', False).asMObject()
        except RuntimeError:
            cached = om.MObject.kNullObj
        self.data = _copy_data(cached if not cached.isNull() else self.path.node())
        self.tweaks = []
        plug = fn.findPlug('pnts', False)
        for index in plug.getExistingArrayAttributeIndices():
            item = plug.elementByLogicalIndex(index)
            if item.isConnected or any(item.child(i).isConnected for i in range(3)):
                raise ResampleError('Animated/connected vertex tweaks are not supported.')
            value = tuple(item.child(i).asDouble() for i in range(3))
            self.tweaks.append((index, value))
        self.result = None

    def alive(self):
        return self.handle.isValid() and self.handle.isAlive()

    def solve(self, delta=0, segments=None, smoothing=0.0, even=0.0):
        mesh, selected = resample_mesh(self.mesh, self.selected_edges, delta, segments, smoothing, even)
        return mesh_data(mesh, self.current_uv), mesh, selected

    def _clear_tweaks(self):
        plug = om.MFnDependencyNode(self.path.node()).findPlug('pnts', False)
        for index in plug.getExistingArrayAttributeIndices():
            item = plug.elementByLogicalIndex(index)
            for i in range(3):
                item.child(i).setDouble(0.0)

    def _materials(self, assignments):
        path = self.path
        for shader in self.shaders:
            fn_set = om.MFnSet(shader)
            members = fn_set.getMembers(False)
            remove = om.MSelectionList()
            for index in range(members.length()):
                try:
                    member, comp = members.getComponent(index)
                    member.extendToShape()
                except (RuntimeError, TypeError):
                    continue
                if member.node() == path.node():
                    remove.add((member, comp)) if not comp.isNull() else remove.add(member)
            if remove.length():
                fn_set.removeMembers(remove)
        by_shader = {}
        for fid, index in enumerate(assignments):
            if index >= 0:
                by_shader.setdefault(index, []).append(fid)
        for index, faces in by_shader.items():
            members = om.MSelectionList()
            members.add((path, _component(om.MFn.kMeshPolygonComponent, faces)))
            om.MFnSet(self.shaders[index]).addMembers(members)

    def write(self, data, mesh, edges=None, restore_selection=False):
        undo_enabled = cmds.undoInfo(query=True, state=True)
        cmds.undoInfo(stateWithoutFlush=False)
        try:
            self._write(data, mesh, edges, restore_selection)
        finally:
            cmds.undoInfo(stateWithoutFlush=undo_enabled)

    def _write(self, data, mesh, edges=None, restore_selection=False):
        if not self.alive():
            raise ResampleError('The source mesh no longer exists.')
        self._clear_tweaks()
        om.MFnMesh(self.path).copyInPlace(data)
        if restore_selection:
            plug = om.MFnDependencyNode(self.path.node()).findPlug('pnts', False)
            for index, value in self.tweaks:
                item = plug.elementByLogicalIndex(index)
                for i in range(3):
                    item.child(i).setDouble(value[i])
        fn = om.MFnMesh(self.path)
        _set_uvs(fn, mesh, self.current_uv, self.raw_uvs if restore_selection else None)
        fn.updateSurface()
        self._materials(mesh.materials)
        if restore_selection:
            om.MGlobal.setActiveSelectionList(self.selection)
        elif edges is not None:
            wanted = set(edges)
            ids = [i for i in range(fn.numEdges) if edge(*fn.getEdgeVertices(i)) in wanted]
            selected = om.MSelectionList()
            selected.add((self.path, _component(om.MFn.kMeshEdgeComponent, ids)))
            om.MGlobal.setActiveSelectionList(selected)

    def restore(self):
        if self.alive():
            self.write(self.data, self.mesh, restore_selection=True)

    def preview(self, **kwargs):
        result = self.solve(**kwargs)
        try:
            self.write(*result)
        except Exception:
            if self.result is not None:
                self.write(*self.result)
            else:
                self.restore()
            raise
        self.result = result
        return result

    def commit(self):
        if self.result is None:
            return
        load_plugin()
        self.restore()
        token = uuid.uuid4().hex
        _PAYLOADS[token] = (self, self.result)
        try:
            cmds.loopResample(payload=token)
        finally:
            _PAYLOADS.pop(token, None)
