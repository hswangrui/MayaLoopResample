# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Viewport-local input with a non-interactive HUD and one undoable commit."""
from __future__ import division

from PySide2 import QtCore, QtWidgets
from shiboken2 import isValid, wrapInstance
from maya import cmds
import maya.api.OpenMaya as om
import maya.api.OpenMayaUI as omui

from .geometry import paths_from_edges
from .maya_bridge import Snapshot, load_plugin
from .ui import _error_text, _pointer

_SESSION = None
_CONTEXT = 'loopResampleViewportContext'


def _viewport_widget():
    for panel in (cmds.getPanel(underPointer=True), cmds.getPanel(withFocus=True)):
        if panel and cmds.getPanel(typeOf=panel) == 'modelPanel':
            view = omui.M3dView.getM3dViewFromModelPanel(panel)
            if view.widget():
                widget = wrapInstance(_pointer(view.widget()), QtWidgets.QWidget)
                if widget.isVisible():
                    return widget
    view = omui.M3dView.active3dView()
    if not view.widget():
        raise RuntimeError('Activate a model viewport before starting Loop Resample.')
    return wrapInstance(_pointer(view.widget()), QtWidgets.QWidget)


class ViewportHud(QtWidgets.QFrame):
    def __init__(self, viewport):
        super(ViewportHud, self).__init__(viewport)
        self.setObjectName('LoopResampleViewportHUD')
        self.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents)
        self.setFocusPolicy(QtCore.Qt.NoFocus)
        self.setStyleSheet('QFrame#LoopResampleViewportHUD {background: rgba(25,29,34,210);'
                           'border: 1px solid #59646d; border-radius: 7px;}'
                           'QLabel {background: transparent; border: none; color: #d5dce1;}')
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(5)
        self.title = QtWidgets.QLabel(u'LOOP RESAMPLE  /  视口交互')
        self.title.setStyleSheet('color: #74c8ee; font-size: 12px; font-weight: bold;')
        self.values = QtWidgets.QLabel()
        self.values.setStyleSheet('font-size: 16px; font-weight: bold; color: #f0d38a;')
        self.values.setWordWrap(True)
        self.hint = QtWidgets.QLabel(u'滚轮 段数   Shift 平滑   Ctrl 均匀\n'
                                    u'左键 / Enter 确认   右键 / Esc 取消\n'
                                    u'Alt + 鼠标 导航   R 重置')
        self.notice = QtWidgets.QLabel()
        self.notice.setWordWrap(True)
        self.notice.setStyleSheet('color: #f0b17a;')
        for widget in (self.title, self.values, self.hint, self.notice):
            layout.addWidget(widget)
        self.notice.hide()
        self.reposition()

    def reposition(self):
        parent = self.parentWidget()
        if parent is None:
            return
        self.setFixedWidth(max(160, min(390, parent.width()-32)))
        self.adjustSize()
        self.move(16, max(12, parent.height()-self.height()-28))
        self.raise_()

    def update_values(self, counts, smoothing, even, axis='segments', message='', pending=False):
        count_text = str(counts[0]) if len(set(counts)) == 1 else ', '.join(str(n) for n in counts[:8])
        if len(counts) > 8 and len(set(counts)) != 1:
            count_text += ' ...'
        self.values.setText(u'%s 段  ·  平滑 %d%%  ·  均匀 %d%%' % (count_text, smoothing, even))
        names = {'segments': u'段数', 'smoothing': u'平滑', 'even': u'均匀'}
        self.title.setText(u'LOOP RESAMPLE  /  %d 条路径  /  %s%s' %
                           (len(counts), names[axis], u' · 计算中' if pending else ''))
        self.notice.setText(message)
        self.notice.setVisible(bool(message))
        self.reposition()


class ViewportSession(QtCore.QObject):
    def __init__(self, snapshot, viewport, manage_context=True):
        super(ViewportSession, self).__init__()
        self.snapshot = snapshot
        self.viewport = viewport
        self.manage_context = manage_context
        paths = paths_from_edges(snapshot.selected_edges)
        self.base_counts = [len(v)-(not closed) for v, closed in paths]
        self.minimum = max((3 if closed else 1)-n for (v, closed), n in zip(paths, self.base_counts))
        self.maximum = 2048-max(self.base_counts)
        self.parameters = (0, 0, 0)
        self.applied = self.parameters
        self.axis = 'segments'
        self.message = ''
        self.active = False
        self.writing = False
        self.callbacks = []
        self.previous_context = None
        self.recovery_required = False
        self.mouse_action = None
        self.navigation = False
        self.alt_down = False
        self.key_action = None
        self.wheel_remainders = {}
        self.hud = None
        self.timer = QtCore.QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(25)
        self.timer.timeout.connect(self.flush)
        self.watchdog = QtCore.QTimer(self)
        self.watchdog.setInterval(200)
        self.watchdog.timeout.connect(self._check_lifetime)
        self.finish_timer = QtCore.QTimer(self)
        self.finish_timer.setSingleShot(True)
        self.finish_timer.timeout.connect(self._finish_requested)
        self.pending_finish = None

    def start(self):
        self.active = True
        self.writing = True
        try:
            if self.manage_context:
                self.previous_context = cmds.currentCtx()
                if cmds.draggerContext(_CONTEXT, exists=True):
                    cmds.deleteUI(_CONTEXT)
                cmds.draggerContext(_CONTEXT, cursor='crossHair', undoMode='all',
                                   finalize=self._context_finalized)
                cmds.setToolTo(_CONTEXT)
            self.hud = ViewportHud(self.viewport)
            QtWidgets.QApplication.instance().installEventFilter(self)
            self.viewport.destroyed.connect(self._viewport_destroyed)
            for event in ('SelectionChanged', 'Undo', 'Redo'):
                self.callbacks.append(om.MEventMessage.addEventCallback(event, self._external_change))
            if self.manage_context:
                self.callbacks.append(om.MEventMessage.addEventCallback('ToolChanged', self._tool_changed))
            for message in (om.MSceneMessage.kBeforeNewCheck, om.MSceneMessage.kBeforeOpenCheck,
                            om.MSceneMessage.kBeforeSaveCheck, om.MSceneMessage.kBeforeExportCheck,
                            om.MSceneMessage.kBeforeImportCheck):
                self.callbacks.append(om.MSceneMessage.addCheckCallback(message, self._scene_change))
            self.viewport.setFocus(QtCore.Qt.OtherFocusReason)
            self.hud.show()
            self._update_hud()
            self.watchdog.start()
        except Exception:
            self.writing = False
            self.finish(False)
            raise
        finally:
            self.writing = False
        return self

    def _inside(self, widget):
        return (isValid(self.viewport) and isinstance(widget, QtWidgets.QWidget) and
                (widget is self.viewport or self.viewport.isAncestorOf(widget)))

    def _update_hud(self):
        if self.hud is not None and isValid(self.hud):
            delta, smoothing, even = self.parameters
            self.hud.update_values([n+delta for n in self.base_counts], smoothing, even,
                                   self.axis, self.message, self.parameters != self.applied)

    def wheel_delta(self, amount, modifiers=QtCore.Qt.NoModifier):
        if not self.active or modifiers & QtCore.Qt.AltModifier or self.navigation:
            return False
        if amount:
            axis = ('smoothing' if modifiers & QtCore.Qt.ShiftModifier else
                    'even' if modifiers & QtCore.Qt.ControlModifier else 'segments')
            remainder = self.wheel_remainders.get(axis, 0)+amount
            steps = int(remainder/120)
            self.wheel_remainders[axis] = remainder-steps*120
            self.step(steps, modifiers)
        return True

    def step(self, steps, modifiers=QtCore.Qt.NoModifier):
        if not self.active or not steps:
            return
        delta, smoothing, even = self.parameters
        if modifiers & QtCore.Qt.ShiftModifier:
            self.axis = 'smoothing'
            smoothing = max(0, min(100, smoothing+steps*5))
        elif modifiers & QtCore.Qt.ControlModifier:
            self.axis = 'even'
            even = max(0, min(100, even+steps*5))
        else:
            self.axis = 'segments'
            delta = max(self.minimum, min(self.maximum, delta+steps))
        self.parameters = (delta, smoothing, even)
        self.message = ''
        self._update_hud()
        if self.parameters != self.applied:
            self.timer.start()

    def flush(self):
        self.timer.stop()
        if not self.active or self.writing:
            return False
        if self.parameters == self.applied and not self.recovery_required:
            return True
        self.writing = True
        try:
            delta, smoothing, even = self.parameters
            if self.parameters == (0, 0, 0):
                self.snapshot.restore()
                self.snapshot.result = None
            else:
                self.snapshot.preview(delta=delta, smoothing=smoothing/100.0, even=even/100.0)
            self.applied = self.parameters
            self.recovery_required = False
            self.message = ''
            cmds.refresh(force=True)
            return True
        except Exception as error:
            self.parameters = self.applied
            self.message = u'未应用，保留上次结果：\n'+_error_text(error)
            return False
        finally:
            self.writing = False
            self._update_hud()

    def reset(self):
        self.parameters = (0, 0, 0)
        self.message = ''
        self.wheel_remainders.clear()
        self.flush()
        self._update_hud()

    def _request_finish(self, commit):
        self.pending_finish = commit
        self.finish_timer.start(0)

    def _finish_requested(self):
        if self.pending_finish is not None:
            commit, self.pending_finish = self.pending_finish, None
            self.finish(commit)

    def _context_finalized(self, *unused):
        if self.active and not self.writing and not self.alt_down and not self.navigation:
            self.finish(False, restore_tool=False)

    def _tool_changed(self, *unused):
        if (self.active and not self.writing and not self.alt_down and not self.navigation and
                cmds.currentCtx() != _CONTEXT):
            self.finish(False, restore_tool=False)

    def _external_change(self, *unused):
        if self.active and not self.writing:
            self.finish(False)

    def _scene_change(self, *unused):
        return self.finish(False) if self.active else True

    def _viewport_destroyed(self, *unused):
        if self.active:
            self.finish(False)

    def _check_lifetime(self):
        if self.active and (not isValid(self.viewport) or not self.viewport.isVisible() or
                            not self.snapshot.alive()):
            self.finish(False)
        elif (self.active and self.manage_context and not self.alt_down and not self.navigation and
              cmds.currentCtx() != _CONTEXT):
            self.finish(False, restore_tool=False)

    def finish(self, commit=False, restore_tool=True):
        global _SESSION
        if not self.active:
            return True
        self.timer.stop()
        self.finish_timer.stop()
        if commit and not self.flush():
            return False
        self.writing = True
        try:
            if commit:
                self.snapshot.commit()
            elif self.snapshot.result is not None or self.recovery_required:
                self.snapshot.restore()
        except Exception as error:
            self.recovery_required = True
            if commit and self.snapshot.result is not None:
                try:
                    self.snapshot.write(*self.snapshot.result)
                    self.recovery_required = False
                except Exception:
                    pass
            self.message = u'结束失败，请重试或按 Esc 恢复：\n'+_error_text(error)
            self._update_hud()
            return False
        finally:
            self.writing = False
        self.active = False
        self.watchdog.stop()
        QtWidgets.QApplication.instance().removeEventFilter(self)
        for callback in self.callbacks:
            om.MMessage.removeCallback(callback)
        self.callbacks = []
        if isValid(self.viewport):
            try:
                self.viewport.destroyed.disconnect(self._viewport_destroyed)
            except (RuntimeError, TypeError):
                pass
        if self.hud is not None and isValid(self.hud):
            self.hud.hide()
            self.hud.deleteLater()
        self.hud = None
        if restore_tool and self.manage_context and cmds.currentCtx() == _CONTEXT:
            cmds.setToolTo(self.previous_context or 'selectSuperContext')
        if _SESSION is self:
            _SESSION = None
        return True

    def eventFilter(self, watched, event):
        if not self.active or self.writing:
            return False
        kind = event.type()
        if watched is self.viewport and kind == QtCore.QEvent.Resize:
            self.hud.reposition()
            return False
        if kind == QtCore.QEvent.ApplicationDeactivate:
            self.finish(False)
            return False
        if not isinstance(watched, QtWidgets.QWidget):
            return False
        inside = self._inside(watched)
        if not inside:
            if kind in (QtCore.QEvent.MouseButtonPress, QtCore.QEvent.MouseButtonDblClick):
                if (isValid(self.viewport) and isinstance(watched, QtWidgets.QWidget) and
                        watched.isAncestorOf(self.viewport) and
                        self.viewport.rect().contains(self.viewport.mapFromGlobal(event.globalPos()))):
                    return False
                return not self.finish(False)
            if kind == QtCore.QEvent.KeyPress and not self._inside(QtWidgets.QApplication.focusWidget()):
                return not self.finish(False)
            return False
        if kind in (QtCore.QEvent.ShortcutOverride, QtCore.QEvent.KeyPress, QtCore.QEvent.KeyRelease):
            key, modifiers = event.key(), event.modifiers()
            if key == QtCore.Qt.Key_Alt and not event.isAutoRepeat():
                self.alt_down = kind != QtCore.QEvent.KeyRelease
            passthrough = (QtCore.Qt.Key_Alt, QtCore.Qt.Key_Shift, QtCore.Qt.Key_Control,
                           QtCore.Qt.Key_Meta)
            if key in passthrough or (key == QtCore.Qt.Key_F and not modifiers):
                return False
            if kind == QtCore.QEvent.ShortcutOverride:
                event.accept()
                return True
            if event.isAutoRepeat() and key in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter,
                                                QtCore.Qt.Key_Escape, QtCore.Qt.Key_Z):
                return True
            if kind == QtCore.QEvent.KeyRelease:
                if self.key_action is not None and key == self.key_action[0]:
                    self._request_finish(self.key_action[1])
                    self.key_action = None
                return True
            if key in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter, QtCore.Qt.Key_Escape):
                self.key_action = (key, key != QtCore.Qt.Key_Escape)
            elif key == QtCore.Qt.Key_Z and modifiers & QtCore.Qt.ControlModifier:
                self.key_action = (key, False)
            elif key in (QtCore.Qt.Key_Plus, QtCore.Qt.Key_Equal, QtCore.Qt.Key_Minus):
                self.step(-1 if key == QtCore.Qt.Key_Minus else 1)
            elif key in (QtCore.Qt.Key_Up, QtCore.Qt.Key_Right, QtCore.Qt.Key_Down, QtCore.Qt.Key_Left):
                self.step(1 if key in (QtCore.Qt.Key_Up, QtCore.Qt.Key_Right) else -1, modifiers)
            elif key == QtCore.Qt.Key_R and not modifiers:
                self.reset()
            return True
        if kind == QtCore.QEvent.Wheel:
            consumed = self.wheel_delta(event.angleDelta().y(), event.modifiers())
            if consumed:
                event.accept()
            return consumed
        mouse_events = (QtCore.QEvent.MouseButtonPress, QtCore.QEvent.MouseButtonDblClick,
                        QtCore.QEvent.MouseButtonRelease, QtCore.QEvent.MouseMove)
        if kind in mouse_events:
            if self.navigation:
                if kind == QtCore.QEvent.MouseButtonRelease and not event.buttons():
                    self.navigation = False
                return False
            if kind in (QtCore.QEvent.MouseButtonPress, QtCore.QEvent.MouseButtonDblClick):
                if event.modifiers() & QtCore.Qt.AltModifier or event.button() == QtCore.Qt.MiddleButton:
                    self.navigation = True
                    return False
                self.mouse_action = (event.button(), event.button() == QtCore.Qt.LeftButton)
                return True
            if kind == QtCore.QEvent.MouseButtonRelease:
                if self.mouse_action is not None and event.button() == self.mouse_action[0]:
                    self._request_finish(self.mouse_action[1])
                    self.mouse_action = None
                return True
            return self.mouse_action is not None
        if kind == QtCore.QEvent.ContextMenu:
            return True
        return False


def cancel():
    if _SESSION is not None:
        _SESSION.finish(False)


def start():
    global _SESSION
    if _SESSION is not None and _SESSION.active:
        return _SESSION
    from . import ui
    if ui._DIALOG is not None and ui._DIALOG.isVisible():
        ui._DIALOG.reject()
    try:
        load_plugin()
        widget = _viewport_widget()
        snapshot = Snapshot()
        session = ViewportSession(snapshot, widget)
        _SESSION = session
        return session.start()
    except Exception as error:
        if _SESSION is not None and _SESSION.active:
            _SESSION.finish(False)
        _SESSION = None
        cmds.warning(u'Loop Resample: '+_error_text(error))
        return None
