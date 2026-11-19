# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import division
import math
import unittest

from loop_resample_maya.geometry import ResampleError, sample, paths_from_edges, face_edges
from loop_resample_maya.topology import Mesh, resample_mesh


def strip(count=4):
    points = [(i, j, 0) for j in range(3) for i in range(count+1)]
    faces = [[j*(count+1)+i, j*(count+1)+i+1, (j+1)*(count+1)+i+1, (j+1)*(count+1)+i]
             for j in range(2) for i in range(count)]
    uvs = [[(points[v][0]/count, points[v][1]/2) for v in f] for f in faces]
    selected = [(count+1+i, count+2+i) for i in range(count)]
    return Mesh(points, faces, {'map1': uvs}), selected


def cylinder(count=8, rows=3, cap=False):
    points = [(math.cos(2*math.pi*i/count), y, math.sin(2*math.pi*i/count))
              for y in range(rows) for i in range(count)]
    faces, uvs = [], []
    for y in range(rows-1):
        for i in range(count):
            faces.append([y*count+i, y*count+(i+1)%count, (y+1)*count+(i+1)%count, (y+1)*count+i])
            uvs.append([(i/count, y), ((i+1)/count, y), ((i+1)/count, y+1), (i/count, y+1)])
    if cap:
        faces.append(list(reversed(range(count))))
        uvs.append([(points[v][0], points[v][2]) for v in faces[-1]])
    return Mesh(points, faces, {'map1': uvs}), [(count+i, count+(i+1)%count) for i in range(count)]


class SamplingTests(unittest.TestCase):
    def test_identity(self):
        points = [(0, 0, 0), (1, 2, 0), (4, 1, 0), (7, 0, 0)]
        self.assertEqual(sample(points, 3)[0], points)

    def test_even(self):
        result, unused = sample([(0, 0, 0), (1, 0, 0), (4, 0, 0)], 4, even=1)
        for i, p in enumerate(result):
            self.assertAlmostEqual(p[0], i)

    def test_endpoints_and_anchors(self):
        points = [(0, 0, 0), (1, 3, 0), (4, 1, 0), (7, 0, 0)]
        result, params = sample(points, 6, smoothing=1, even=1, anchors=[1])
        self.assertEqual(result[0], points[0])
        self.assertEqual(result[-1], points[-1])
        self.assertIn(1, params)

    def test_invalid(self):
        for args in [([(0, 0, 0)]*2, 3), ([(0, 0, 0), (1, 0, 0)], 0)]:
            with self.assertRaises(ResampleError):
                sample(*args)
        with self.assertRaises(ResampleError):
            paths_from_edges([(0, 1), (1, 2), (1, 3)])

    def test_closed(self):
        points = [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)]
        result, params = sample(points, 7, True)
        self.assertEqual(len(result), 7)
        self.assertEqual(result[0], points[0])
        self.assertLess(params[-1], 4)


class TopologyTests(unittest.TestCase):
    def check_mesh(self, mesh):
        for face in mesh.faces:
            self.assertEqual(len(face), len(set(face)))
            self.assertTrue(all(0 <= v < len(mesh.points) for v in face))
        for name, faces in mesh.uv_sets.items():
            self.assertEqual(len(faces), len(mesh.faces))
            for face, uv in zip(mesh.faces, faces):
                self.assertTrue(uv is None or len(face) == len(uv))
        counts = {}
        for f in mesh.faces:
            for e in face_edges(f):
                counts[e] = counts.get(e, 0)+1
        self.assertTrue(all(c <= 2 for c in counts.values()))

    def test_open_add_remove(self):
        for delta in (-3, -1, 0, 2):
            mesh, selected = strip()
            result, edges = resample_mesh(mesh, selected, delta=delta)
            self.assertEqual(len(edges), 4+delta)
            self.assertEqual(len(result.points), len(mesh.points)+delta)
            self.check_mesh(result)

    def test_closed_add_remove_uv_seam(self):
        for delta in (-4, -1, 0, 3):
            mesh, selected = cylinder()
            result, edges = resample_mesh(mesh, selected, delta=delta)
            self.assertEqual(len(edges), 8+delta)
            self.assertEqual(len(result.points), len(mesh.points)+delta)
            self.check_mesh(result)
            values = [u[0] for f in result.uv_sets['map1'] for u in f]
            self.assertEqual(min(values), 0)
            self.assertEqual(max(values), 1)

    def test_adjacent_loops(self):
        mesh, selected = cylinder(rows=4)
        selected += [(16+i, 16+(i+1)%8) for i in range(8)]
        result, edges = resample_mesh(mesh, selected, delta=2)
        self.assertEqual(len(edges), 20)
        self.check_mesh(result)

    def test_cap(self):
        mesh, unused = cylinder(cap=True)
        result, edges = resample_mesh(mesh, [(i, (i+1)%8) for i in range(8)], delta=2)
        self.assertEqual(len(edges), 10)
        self.assertTrue(any(len(f) == 10 for f in result.faces))
        self.check_mesh(result)

    def test_fan(self):
        mesh, unused = cylinder(rows=1)
        mesh.points.append((0, 0, 0))
        mesh.faces = [[i, (i+1)%8, 8] for i in range(8)]
        mesh.materials = [0]*8
        mesh.uv_sets = {}
        result, edges = resample_mesh(mesh, [(i, (i+1)%8) for i in range(8)], delta=-3)
        self.assertEqual(len(result.faces), 5)
        self.check_mesh(result)

    def test_material_boundary(self):
        mesh, selected = strip()
        mesh.materials = [0, 0, 1, 1]*2
        result, unused = resample_mesh(mesh, selected, delta=2, even=1)
        self.assertEqual(set(result.materials), {0, 1})
        self.check_mesh(result)
        with self.assertRaises(ResampleError):
            resample_mesh(mesh, selected, segments=1)

    def test_partial_open_path(self):
        mesh, selected = cylinder()
        result, edges = resample_mesh(mesh, selected[:3], delta=1)
        self.assertEqual(len(edges), 4)
        self.assertEqual(len(result.points), len(mesh.points)+1)
        self.assertEqual(result.points[8], mesh.points[8])
        self.check_mesh(result)

    def test_inputs_unchanged(self):
        mesh, selected = strip()
        points, faces = list(mesh.points), [list(f) for f in mesh.faces]
        resample_mesh(mesh, selected, delta=3)
        self.assertEqual(points, mesh.points)
        self.assertEqual(faces, mesh.faces)


if __name__ == '__main__':
    unittest.main()
