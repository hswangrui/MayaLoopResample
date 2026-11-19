# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import print_function, division
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import maya.standalone
if os.environ.get('LOOP_RESAMPLE_GUI_TEST') != '1':
    maya.standalone.initialize(name='python')
from maya import cmds
import maya.api.OpenMaya as om
from loop_resample_maya import apply
from loop_resample_maya.maya_bridge import capture_mesh, Snapshot, load_plugin


def dag(name):
    selection = om.MSelectionList()
    selection.add(name)
    path = selection.getDagPath(0)
    path.extendToShape()
    return path


def select_row(name, y=0.0):
    path = dag(name)
    fn = om.MFnMesh(path)
    points = fn.getPoints()
    ids = [i for i in range(fn.numEdges) if all(abs(points[v].y-y) < 1e-6 for v in fn.getEdgeVertices(i))]
    cmds.select([name+'.e[%d]' % i for i in ids], replace=True)
    return ids


def fingerprint(name):
    mesh, shaders = capture_mesh(dag(name))
    shader_names = [om.MFnDependencyNode(s).name() for s in shaders]
    materials = [shader_names[i] if i >= 0 else '' for i in mesh.materials]
    return (mesh.points, mesh.faces, mesh.uv_sets, materials, mesh.hard_edges)


class MayaTests(unittest.TestCase):
    def setUp(self):
        cmds.file(new=True, force=True)
        cmds.undoInfo(state=True)
        load_plugin()

    def cylinder(self):
        obj = cmds.polyCylinder(subdivisionsX=8, subdivisionsY=2, subdivisionsCaps=1, constructionHistory=False)[0]
        cmds.polySoftEdge(obj, angle=180, constructionHistory=False)
        select_row(obj)
        return obj

    def test_apply_undo_redo(self):
        obj = self.cylinder()
        before = fingerprint(obj)
        selection = cmds.ls(selection=True, flatten=True)
        old_vertices = cmds.polyEvaluate(obj, vertex=True)
        result = apply(delta=3)
        self.assertEqual(result, 11)
        self.assertEqual(cmds.polyEvaluate(obj, vertex=True), old_vertices+3)
        after = fingerprint(obj)
        cmds.undo()
        self.assertEqual(fingerprint(obj), before)
        self.assertEqual(cmds.ls(selection=True, flatten=True), selection)
        cmds.redo()
        self.assertEqual(fingerprint(obj), after)
        cmds.undo()
        self.assertEqual(fingerprint(obj), before)

    def test_preview_cancel(self):
        obj = self.cylinder()
        before = fingerprint(obj)
        snapshot = Snapshot()
        snapshot.preview(delta=3, smoothing=0.4, even=0.7)
        snapshot.preview(delta=-2)
        snapshot.restore()
        self.assertEqual(fingerprint(obj), before)

    def test_preview_commit_one_undo(self):
        obj = self.cylinder()
        before = fingerprint(obj)
        snapshot = Snapshot()
        snapshot.preview(delta=2)
        snapshot.preview(delta=3)
        preview = fingerprint(obj)
        snapshot.commit()
        self.assertEqual(fingerprint(obj), preview)
        cmds.undo()
        self.assertEqual(fingerprint(obj), before)

    def test_history_rejected(self):
        obj = cmds.polyCylinder(subdivisionsX=8, subdivisionsY=2, constructionHistory=True)[0]
        select_row(obj)
        before = fingerprint(obj)
        with self.assertRaises(RuntimeError):
            apply(delta=1)
        self.assertEqual(fingerprint(obj), before)

    def test_materials_uv_sets(self):
        obj = self.cylinder()
        material = cmds.shadingNode('lambert', asShader=True)
        shading = cmds.sets(renderable=True, noSurfaceShader=True, empty=True)
        cmds.connectAttr(material+'.outColor', shading+'.surfaceShader')
        cmds.sets(obj+'.f[0:3]', obj+'.f[8:11]', edit=True, forceElement=shading)
        cmds.polyUVSet(obj, copy=True, uvSet='map1', newUVSet='secondUV')
        select_row(obj)
        before = fingerprint(obj)
        apply(delta=4)
        mesh, shaders = capture_mesh(dag(obj))
        self.assertEqual(set(mesh.uv_sets), {'map1', 'secondUV'})
        self.assertEqual(len(set(mesh.materials)), 2)
        cmds.undo()
        self.assertEqual(fingerprint(obj), before)

    def test_vertex_tweaks(self):
        obj = self.cylinder()
        cmds.move(0, 0.15, 0, obj+'.vtx[0]', relative=True)
        select_row(obj)
        before = fingerprint(obj)
        apply(delta=1)
        cmds.undo()
        self.assertEqual(fingerprint(obj), before)

    def test_cancel_preserves_previous_undo(self):
        obj = self.cylinder()
        shape = dag(obj).fullPathName()
        before = fingerprint(obj)
        cmds.setAttr(shape+'.pnts[0].pntx', 0.2)
        edited = fingerprint(obj)
        snapshot = Snapshot()
        snapshot.preview(delta=1)
        snapshot.restore()
        self.assertEqual(fingerprint(obj), edited)
        self.assertAlmostEqual(cmds.getAttr(shape+'.pnts[0].pntx'), 0.2)
        cmds.undo()
        self.assertEqual(fingerprint(obj), before)

    def test_move_undo_after_resample_undo(self):
        obj = self.cylinder()
        before = fingerprint(obj)
        cmds.move(0, 0.15, 0, obj+'.vtx[0]', relative=True)
        moved = fingerprint(obj)
        apply(delta=2)
        cmds.undo()
        self.assertEqual(fingerprint(obj), moved)
        cmds.undo()
        self.assertEqual(fingerprint(obj), before)

    def test_overlapping_uv_cut_preserved(self):
        obj = self.cylinder()
        path = dag(obj)
        fn = om.MFnMesh(path)
        original_shells = fn.getUvShellsIds('map1')[0]
        rungs = [i for i in range(fn.numEdges)
                 if set(fn.getEdgeVertices(i)) in ({2, 10}, {10, 18})]
        cmds.polyMapCut([obj+'.e[%d]' % i for i in rungs], constructionHistory=False)
        select_row(obj)
        fn = om.MFnMesh(path)
        before_shells = fn.getUvShellsIds('map1')[0]
        self.assertGreater(before_shells, original_shells)
        before = fingerprint(obj)
        apply(delta=3)
        self.assertEqual(om.MFnMesh(path).getUvShellsIds('map1')[0], before_shells)
        cmds.undo()
        self.assertEqual(fingerprint(obj), before)

    def test_hard_rungs_reject_reduction(self):
        obj = cmds.polyCylinder(subdivisionsX=8, subdivisionsY=2, constructionHistory=False)[0]
        select_row(obj)
        before = fingerprint(obj)
        with self.assertRaises(RuntimeError):
            apply(delta=-1)
        self.assertEqual(fingerprint(obj), before)

    def test_no_uv(self):
        obj = self.cylinder()
        cmds.polyMapDel(obj, constructionHistory=False)
        select_row(obj)
        apply(delta=1)
        self.assertEqual(len(cmds.ls(selection=True, flatten=True)), 9)

    def test_transforms_preserved(self):
        obj = self.cylinder()
        cmds.setAttr(obj+'.translateX', 15)
        cmds.setAttr(obj+'.scaleY', 2)
        before = cmds.xform(obj, query=True, matrix=True, worldSpace=True)
        apply(delta=-2)
        self.assertEqual(cmds.xform(obj, query=True, matrix=True, worldSpace=True), before)


if __name__ == '__main__':
    print('Testing Maya ' + cmds.about(version=True))
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(MayaTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    maya.standalone.uninitialize()
    sys.exit(0 if result.wasSuccessful() else 1)
