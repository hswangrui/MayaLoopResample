# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import print_function
import os
import sys
import unittest
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide2 import QtCore, QtGui, QtWidgets
APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

import maya_smoke as maya_test
from maya import cmds
from loop_resample_maya.maya_bridge import Snapshot, load_plugin
from loop_resample_maya.viewport import ViewportSession


class ViewportTests(unittest.TestCase):
    def setUp(self):
        cmds.file(new=True, force=True)
        load_plugin()
        self.obj = cmds.polyCylinder(subdivisionsX=8, subdivisionsY=2, constructionHistory=False)[0]
        cmds.polySoftEdge(self.obj, angle=180, constructionHistory=False)
        maya_test.select_row(self.obj)
        self.before = maya_test.fingerprint(self.obj)
        self.widget = QtWidgets.QWidget()
        self.widget.resize(900, 650)
        self.widget.show()
        APP.processEvents()
        self.session = ViewportSession(Snapshot(), self.widget, manage_context=False).start()

    def tearDown(self):
        self.session.finish(False)
        self.widget.close()
        self.widget.deleteLater()
        APP.processEvents()

    def wheel(self, amount, modifiers=QtCore.Qt.NoModifier):
        return self.session.wheel_delta(amount, modifiers)

    def mouse(self, kind, button, modifiers=QtCore.Qt.NoModifier, buttons=None):
        if buttons is None:
            buttons = QtCore.Qt.NoButton if kind == QtCore.QEvent.MouseButtonRelease else button
        event = QtGui.QMouseEvent(kind, QtCore.QPointF(100, 100), button, buttons, modifiers)
        return self.session.eventFilter(self.widget, event)

    def key(self, key, modifiers=QtCore.Qt.NoModifier):
        for kind in (QtCore.QEvent.KeyPress, QtCore.QEvent.KeyRelease):
            self.session.eventFilter(self.widget, QtGui.QKeyEvent(kind, key, modifiers))
        APP.processEvents()

    def test_wheel_axes_and_cancel(self):
        self.assertTrue(self.wheel(240))
        self.wheel(120, QtCore.Qt.ShiftModifier)
        self.wheel(240, QtCore.Qt.ControlModifier)
        self.assertTrue(self.session.flush())
        self.assertEqual(self.session.applied, (2, 5, 10))
        self.assertEqual(len(cmds.ls(selection=True, flatten=True)), 10)
        self.key(QtCore.Qt.Key_Escape)
        self.assertFalse(self.session.active)
        self.assertEqual(maya_test.fingerprint(self.obj), self.before)

    def test_fractional_and_zero_wheel(self):
        self.wheel(0)
        self.wheel(60)
        self.assertEqual(self.session.parameters, (0, 0, 0))
        self.wheel(60)
        self.assertEqual(self.session.parameters, (1, 0, 0))
        self.wheel(-240)
        self.assertEqual(self.session.parameters, (-1, 0, 0))

    def test_mouse_commit_single_undo(self):
        self.wheel(360)
        self.assertTrue(self.mouse(QtCore.QEvent.MouseButtonPress, QtCore.Qt.LeftButton))
        self.assertTrue(self.session.active)
        self.assertTrue(self.mouse(QtCore.QEvent.MouseButtonRelease, QtCore.Qt.LeftButton))
        APP.processEvents()
        self.assertFalse(self.session.active)
        self.assertEqual(len(cmds.ls(selection=True, flatten=True)), 11)
        cmds.undo()
        self.assertEqual(maya_test.fingerprint(self.obj), self.before)
        cmds.redo()
        self.assertEqual(len(cmds.ls(selection=True, flatten=True)), 11)

    def test_right_click_cancel(self):
        self.wheel(120)
        self.session.flush()
        self.mouse(QtCore.QEvent.MouseButtonPress, QtCore.Qt.RightButton)
        self.mouse(QtCore.QEvent.MouseButtonRelease, QtCore.Qt.RightButton)
        APP.processEvents()
        self.assertFalse(self.session.active)
        self.assertEqual(maya_test.fingerprint(self.obj), self.before)

    def test_alt_navigation_not_confirm(self):
        for button in (QtCore.Qt.LeftButton, QtCore.Qt.MiddleButton, QtCore.Qt.RightButton):
            self.assertFalse(self.mouse(QtCore.QEvent.MouseButtonPress, button, QtCore.Qt.AltModifier))
            self.assertFalse(self.mouse(QtCore.QEvent.MouseButtonRelease, button))
        self.assertFalse(self.wheel(120, QtCore.Qt.AltModifier))
        self.assertTrue(self.session.active)
        self.assertEqual(self.session.parameters, (0, 0, 0))

    def test_native_window_transport_does_not_cancel(self):
        self.wheel(120)
        self.session.flush()
        window = self.widget.windowHandle()
        self.assertIsNotNone(window)
        for kind in (QtCore.QEvent.MouseButtonPress, QtCore.QEvent.MouseButtonRelease):
            buttons = QtCore.Qt.LeftButton if kind == QtCore.QEvent.MouseButtonPress else QtCore.Qt.NoButton
            event = QtGui.QMouseEvent(kind, QtCore.QPointF(100, 100), QtCore.Qt.LeftButton,
                                     buttons, QtCore.Qt.AltModifier)
            self.assertFalse(self.session.eventFilter(window, event))
            self.assertTrue(self.session.active)
            self.assertFalse(self.session.eventFilter(self.widget, event))
            self.assertTrue(self.session.active)
        self.assertIsNone(self.session.pending_finish)
        self.assertEqual(self.session.applied, (1, 0, 0))

    def test_enter_flushes_pending(self):
        self.wheel(-240)
        self.key(QtCore.Qt.Key_Return)
        self.assertFalse(self.session.active)
        self.assertEqual(len(cmds.ls(selection=True, flatten=True)), 6)
        cmds.undo()
        self.assertEqual(maya_test.fingerprint(self.obj), self.before)

    def test_reset(self):
        self.wheel(360)
        self.session.flush()
        self.key(QtCore.Qt.Key_R)
        self.assertEqual(self.session.applied, (0, 0, 0))
        self.assertEqual(maya_test.fingerprint(self.obj), self.before)

    def test_failure_restores_applied_parameters(self):
        self.wheel(120)
        self.session.flush()
        good = maya_test.fingerprint(self.obj)
        def fail(**kwargs):
            raise RuntimeError('Unsupported topology')
        self.session.snapshot.preview = fail
        self.wheel(120)
        self.assertFalse(self.session.flush())
        self.assertEqual(self.session.parameters, (1, 0, 0))
        self.assertTrue(self.session.message)
        self.assertEqual(maya_test.fingerprint(self.obj), good)

    def test_external_selection_cancels(self):
        self.wheel(120)
        self.session.flush()
        cmds.select(clear=True)
        self.assertFalse(self.session.active)
        self.assertEqual(maya_test.fingerprint(self.obj), self.before)

    def test_scene_new_cancels_and_cleans_callbacks(self):
        self.wheel(120)
        self.session.flush()
        cmds.file(new=True, force=True)
        self.assertFalse(self.session.active)
        self.assertEqual(self.session.callbacks, [])
        self.assertIsNone(self.session.hud)

    def test_outside_click_restores_before_other_ui(self):
        self.wheel(120)
        self.session.flush()
        other = QtWidgets.QWidget()
        event = QtGui.QMouseEvent(QtCore.QEvent.MouseButtonPress, QtCore.QPointF(1, 1),
                                 QtCore.Qt.LeftButton, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier)
        self.assertFalse(self.session.eventFilter(other, event))
        self.assertFalse(self.session.active)
        self.assertEqual(maya_test.fingerprint(self.obj), self.before)
        other.deleteLater()

    def test_ctrl_z_cancels_only_preview(self):
        self.wheel(120)
        self.session.flush()
        self.key(QtCore.Qt.Key_Z, QtCore.Qt.ControlModifier)
        self.assertFalse(self.session.active)
        self.assertTrue(cmds.objExists(self.obj))
        self.assertEqual(maya_test.fingerprint(self.obj), self.before)

    def test_shortcuts_blocked(self):
        self.assertTrue(self.session.eventFilter(self.widget,
                        QtGui.QKeyEvent(QtCore.QEvent.ShortcutOverride, QtCore.Qt.Key_Delete, QtCore.Qt.NoModifier)))
        self.key(QtCore.Qt.Key_Delete)
        self.assertTrue(cmds.objExists(self.obj))
        self.assertTrue(self.session.active)

    def test_hud_resize_and_mouse_transparency(self):
        self.assertTrue(self.session.hud.testAttribute(QtCore.Qt.WA_TransparentForMouseEvents))
        self.widget.resize(500, 400)
        APP.processEvents()
        self.assertLessEqual(self.session.hud.geometry().right(), 500)
        self.assertLessEqual(self.session.hud.geometry().bottom(), 400)

    def test_auto_repeat_release_does_not_confirm(self):
        self.wheel(120)
        self.session.eventFilter(self.widget, QtGui.QKeyEvent(QtCore.QEvent.KeyPress,
                                 QtCore.Qt.Key_Return, QtCore.Qt.NoModifier))
        self.session.eventFilter(self.widget, QtGui.QKeyEvent(QtCore.QEvent.KeyRelease,
                                 QtCore.Qt.Key_Return, QtCore.Qt.NoModifier, '', True, 1))
        APP.processEvents()
        self.assertTrue(self.session.active)
        self.session.eventFilter(self.widget, QtGui.QKeyEvent(QtCore.QEvent.KeyRelease,
                                 QtCore.Qt.Key_Return, QtCore.Qt.NoModifier))
        APP.processEvents()
        self.assertFalse(self.session.active)

    def test_commit_failure_reapplies_preview(self):
        self.wheel(120)
        self.session.flush()
        preview = maya_test.fingerprint(self.obj)
        def fail():
            self.session.snapshot.restore()
            raise RuntimeError('Commit failed')
        self.session.snapshot.commit = fail
        self.assertFalse(self.session.finish(True))
        self.assertTrue(self.session.active)
        self.assertEqual(maya_test.fingerprint(self.obj), preview)
        self.assertFalse(self.session.recovery_required)

    def test_restore_failure_retains_snapshot_and_vetoes_scene_change(self):
        self.wheel(120)
        self.session.flush()
        restore = self.session.snapshot.restore
        def fail():
            raise RuntimeError('Restore failed')
        self.session.snapshot.restore = fail
        try:
            self.assertFalse(self.session._scene_change())
            self.assertTrue(self.session.active)
            self.assertTrue(self.session.callbacks)
            self.assertTrue(self.session.recovery_required)
        finally:
            self.session.snapshot.restore = restore
        self.assertTrue(self.session.finish(False))
        self.assertEqual(maya_test.fingerprint(self.obj), self.before)

    def test_application_deactivate_cancels(self):
        self.wheel(120)
        self.session.flush()
        self.session.eventFilter(APP, QtCore.QEvent(QtCore.QEvent.ApplicationDeactivate))
        self.assertFalse(self.session.active)
        self.assertEqual(maya_test.fingerprint(self.obj), self.before)


if __name__ == '__main__':
    print('Testing viewport events on Maya '+cmds.about(version=True))
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(ViewportTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    maya_test.maya.standalone.uninitialize()
    sys.exit(0 if result.wasSuccessful() else 1)
