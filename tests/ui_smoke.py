# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import print_function
import os
import sys
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide2 import QtWidgets
APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

import maya_smoke as maya_test
from maya import cmds
from loop_resample_maya.maya_bridge import Snapshot, load_plugin
from loop_resample_maya.ui import ResampleDialog


class DialogTests(unittest.TestCase):
    def setUp(self):
        cmds.file(new=True, force=True)
        load_plugin()
        self.obj = cmds.polyCylinder(subdivisionsX=8, subdivisionsY=2, constructionHistory=False)[0]
        cmds.polySoftEdge(self.obj, angle=180, constructionHistory=False)
        maya_test.select_row(self.obj)
        self.before = maya_test.fingerprint(self.obj)
        self.dialog = ResampleDialog(Snapshot())

    def tearDown(self):
        self.dialog.reject()
        self.dialog.deleteLater()
        APP.processEvents()

    def test_preview_cancel(self):
        self.dialog.count.setValue(3)
        self.assertTrue(self.dialog._preview())
        self.assertNotEqual(maya_test.fingerprint(self.obj), self.before)
        self.dialog.reject()
        self.assertEqual(maya_test.fingerprint(self.obj), self.before)

    def test_accept_undo(self):
        self.dialog.count.setValue(-2)
        self.assertTrue(self.dialog._preview())
        self.dialog.accept()
        cmds.undo()
        self.assertEqual(maya_test.fingerprint(self.obj), self.before)

    def test_reset(self):
        self.dialog.count.setValue(2)
        self.dialog.smooth_spin.setValue(50)
        self.assertTrue(self.dialog._preview())
        self.dialog._reset()
        self.assertEqual(maya_test.fingerprint(self.obj), self.before)
        self.assertIsNone(self.dialog.snapshot.result)

    def test_scene_change_cancels(self):
        self.dialog.count.setValue(1)
        self.assertTrue(self.dialog._preview())
        self.dialog._scene_change()
        self.assertTrue(self.dialog.finished_edit)
        self.assertEqual(maya_test.fingerprint(self.obj), self.before)

    def test_localized_api_error(self):
        def fail(**kwargs):
            raise RuntimeError(b'\xb2\xe2\xca\xd4')
        self.dialog.snapshot.preview = fail
        self.dialog.count.setValue(2)
        self.assertFalse(self.dialog._preview())
        self.assertFalse(self.dialog.confirm.isEnabled())
        self.assertEqual(maya_test.fingerprint(self.obj), self.before)

    def test_absolute_segments(self):
        self.dialog.mode.setCurrentIndex(1)
        self.dialog.count.setValue(12)
        self.assertTrue(self.dialog._preview())
        self.assertEqual(len(cmds.ls(selection=True, flatten=True)), 12)


if __name__ == '__main__':
    print('Testing Qt dialog on Maya '+cmds.about(version=True))
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(DialogTests))
    maya_test.maya.standalone.uninitialize()
    sys.exit(0 if result.wasSuccessful() else 1)
