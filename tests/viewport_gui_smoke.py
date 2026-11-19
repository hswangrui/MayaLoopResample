# SPDX-License-Identifier: GPL-3.0-or-later
"""Run only in a NEW disposable Maya GUI process; creates and discards test scenes."""
from __future__ import print_function
import os
import sys
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'tests'))
os.environ['LOOP_RESAMPLE_GUI_TEST'] = '1'
from PySide2 import QtCore, QtGui, QtWidgets
from maya import cmds
import maya.utils


def run():
    log_path = os.path.join(ROOT, '.maya_test', 'gui'+cmds.about(version=True)+'.log')
    success = False
    with open(log_path, 'w') as stream:
        try:
            import ctypes
            from ctypes import wintypes
            import maya_smoke
            import loop_resample_maya.viewport as viewport
            from PySide2 import QtTest
            import hotbox_checks
            hotbox_checks.run(stream)
            cmds.file(new=True, force=True)
            obj = cmds.polyCylinder(subdivisionsX=8, subdivisionsY=2, constructionHistory=False)[0]
            cmds.polySoftEdge(obj, angle=180, constructionHistory=False)
            maya_smoke.select_row(obj)
            before = maya_smoke.fingerprint(obj)
            panel = next(p for p in cmds.getPanel(visiblePanels=True)
                         if cmds.getPanel(typeOf=p) == 'modelPanel')
            cmds.setFocus(panel)
            stream.write('Starting real modelPanel context: '+panel+'\n')
            stream.flush()
            session = viewport.start()
            assert session is not None and session.active
            widget = session.viewport
            app = QtWidgets.QApplication.instance()
            app.processEvents()
            stream.write('Context: '+cmds.currentCtx()+'; active=%s; visible=%s; widget=%s\n' %
                         (session.active, widget.isVisible(), widget.objectName()))
            stream.flush()
            send = ctypes.windll.user32.SendMessageW
            send.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
            send.restype = ctypes.c_ssize_t
            point = widget.mapToGlobal(QtCore.QPoint(widget.width()//2, widget.height()//2))
            coord = (point.x() & 65535) | ((point.y() & 65535) << 16)
            hwnd = int(widget.winId())
            stream.write('Sending native WM_MOUSEWHEEL to test viewport\n')
            stream.flush()
            send(hwnd, 0x020A, 120 << 16, coord)
            app.processEvents()
            assert session.parameters[0] == 1, repr(session.parameters)
            assert session.flush()
            assert len(cmds.ls(selection=True, flatten=True)) == 9
            stream.write('Native wheel and mesh update: OK\n')
            for virtual_key, flag, axis in ((0x10, 0x0004, 1), (0x11, 0x0008, 2)):
                saved = (ctypes.c_ubyte*256)()
                assert ctypes.windll.user32.GetKeyboardState(saved)
                held = (ctypes.c_ubyte*256)(*saved)
                held[virtual_key] |= 0x80
                assert ctypes.windll.user32.SetKeyboardState(held)
                try:
                    send(hwnd, 0x020A, (120 << 16) | flag, coord)
                    app.processEvents()
                    assert session.parameters[axis] == 5, repr(session.parameters)
                finally:
                    ctypes.windll.user32.SetKeyboardState(saved)
            stream.write('Native Shift/Ctrl wheel: OK\n')
            stream.flush()
            session.finish(False)
            assert maya_smoke.fingerprint(obj) == before
            session = viewport.start()
            session.step(2)
            session.flush()
            cmds.setToolTo('moveSuperContext')
            app.processEvents()
            assert not session.active
            assert maya_smoke.fingerprint(obj) == before
            assert cmds.currentCtx() == 'moveSuperContext'
            stream.write('Tool switch cancellation: OK\n')
            stream.flush()
            session = viewport.start()
            session.step(1)
            session.flush()
            widget = session.viewport
            hwnd = int(widget.winId())
            pos = QtCore.QPoint(widget.width()//2, widget.height()//2)
            origin = (pos.x() & 65535) | ((pos.y() & 65535) << 16)
            destination = ((pos.x()+40) & 65535) | (((pos.y()+20) & 65535) << 16)
            camera = cmds.modelPanel(panel, query=True, camera=True)
            camera_before = cmds.xform(camera, query=True, matrix=True, worldSpace=True)
            saved = (ctypes.c_ubyte*256)()
            assert ctypes.windll.user32.GetKeyboardState(saved)
            held = (ctypes.c_ubyte*256)(*saved)
            held[0x12] |= 0x80
            assert ctypes.windll.user32.SetKeyboardState(held)
            try:
                send(hwnd, 0x0104, 0x12, 1 | (1 << 29))
                send(hwnd, 0x0201, 0x0001, origin)
                app.processEvents()
                assert session.active, 'Native Alt+left press cancelled the session'
                send(hwnd, 0x0200, 0x0001, destination)
                app.processEvents()
                send(hwnd, 0x0202, 0, destination)
                send(hwnd, 0x0105, 0x12, 1 | (1 << 29) | (1 << 30) | (1 << 31))
            finally:
                ctypes.windll.user32.SetKeyboardState(saved)
            app.processEvents()
            assert session.active, 'Native Alt+left navigation cancelled the session'
            assert cmds.xform(camera, query=True, matrix=True, worldSpace=True) != camera_before, 'Native camera did not move'
            stream.write('Native Alt+left navigation: OK\n')
            send(hwnd, 0x0204, 0x0002, destination)
            send(hwnd, 0x0205, 0, destination)
            hotbox_checks.wait(60)
            app.processEvents()
            assert not session.active, 'Native right click did not cancel'
            assert maya_smoke.fingerprint(obj) == before
            stream.write('Native right-click cancellation after navigation: OK\n')
            session = viewport.start()
            session.step(1)
            session.flush()
            preview = maya_smoke.fingerprint(obj)
            hwnd = int(session.viewport.winId())
            send(hwnd, 0x0201, 0x0001, origin)
            send(hwnd, 0x0202, 0, origin)
            hotbox_checks.wait(60)
            app.processEvents()
            assert not session.active, 'Native left click did not confirm'
            assert maya_smoke.fingerprint(obj) == preview
            cmds.undo()
            assert maya_smoke.fingerprint(obj) == before
            stream.write('Native left-click confirmation and undo: OK\n')
            stream.flush()
            session = viewport.start()
            session.step(1)
            session.flush()
            widget = session.viewport
            pos = QtCore.QPoint(widget.width()//2, widget.height()//2)
            camera = cmds.modelPanel(panel, query=True, camera=True)
            camera_before = cmds.xform(camera, query=True, matrix=True, worldSpace=True)
            QtTest.QTest.keyPress(widget, QtCore.Qt.Key_Alt)
            QtTest.QTest.mousePress(widget, QtCore.Qt.LeftButton, QtCore.Qt.AltModifier, pos)
            QtTest.QTest.mouseMove(widget, pos+QtCore.QPoint(30, 20), 20)
            QtTest.QTest.mouseRelease(widget, QtCore.Qt.LeftButton, QtCore.Qt.AltModifier, pos+QtCore.QPoint(30, 20))
            QtTest.QTest.keyRelease(widget, QtCore.Qt.Key_Alt)
            app.processEvents()
            assert session.active, 'Alt navigation cancelled the session'
            assert cmds.xform(camera, query=True, matrix=True, worldSpace=True) != camera_before, 'Camera did not move'
            stream.write('Alt navigation moves camera and leaves tool active: OK\n')
            stream.flush()
            QtTest.QTest.keyClick(widget, QtCore.Qt.Key_Return)
            app.processEvents()
            assert not session.active
            cmds.undo()
            assert maya_smoke.fingerprint(obj) == before
            success = True
            stream.write('GUI_TEST_SUCCESS=True\n')
        except Exception:
            traceback.print_exc(file=stream)
        stream.flush()
    cmds.quit(force=True, exitCode=0 if success else 1)


maya.utils.executeDeferred(run)
