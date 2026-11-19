# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""A modal preview dialog keeps unrelated Maya edits out of the transaction."""
from __future__ import division

from PySide2 import QtCore, QtWidgets
from shiboken2 import wrapInstance
from maya import cmds
from maya import OpenMayaUI
import maya.api.OpenMaya as om

from .geometry import paths_from_edges
from .maya_bridge import Snapshot, load_plugin

_DIALOG = None
try:
    _pointer = long
    _text = unicode
except NameError:
    _pointer = int
    _text = str


def _error_text(error):
    value = error.args[0] if error.args else repr(error)
    if isinstance(value, bytes):
        for encoding in ('utf-8', 'mbcs'):
            try:
                return value.decode(encoding)
            except (UnicodeError, LookupError):
                pass
        return value.decode('utf-8', 'replace')
    return _text(value)


class ResampleDialog(QtWidgets.QDialog):
    def __init__(self, snapshot, parent=None):
        super(ResampleDialog, self).__init__(parent)
        self.snapshot = snapshot
        self.finished_edit = False
        self.callbacks = []
        self.setObjectName('LoopResampleMayaDialog')
        self.setWindowTitle(u'Loop Resample | Maya')
        self.setWindowModality(QtCore.Qt.ApplicationModal)
        self.setMinimumWidth(430)
        self.setWindowFlags(self.windowFlags() & ~QtCore.Qt.WindowContextHelpButtonHint)
        self.paths = paths_from_edges(snapshot.selected_edges)
        self.base_counts = [len(v)-(not closed) for v, closed in self.paths]
        self.minimum_delta = max((3 if closed else 1)-n for (v, closed), n in zip(self.paths, self.base_counts))
        layout = QtWidgets.QVBoxLayout(self)
        title = QtWidgets.QLabel(u'边环重采样')
        title.setStyleSheet('font-size: 20px; font-weight: bold; margin-bottom: 6px;')
        layout.addWidget(title)
        self.summary = QtWidgets.QLabel(u'%d 条路径  |  原段数：%s' % (len(self.paths), ', '.join(str(n) for n in self.base_counts)))
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        form = QtWidgets.QFormLayout()
        self.mode = QtWidgets.QComboBox()
        self.mode.addItems([u'共同增减段数', u'统一目标段数'])
        form.addRow(u'段数模式', self.mode)
        self.count = QtWidgets.QSpinBox()
        self.count.setRange(self.minimum_delta, 2048-max(self.base_counts))
        self.count.setValue(0)
        form.addRow(u'段数 / 增量', self.count)
        self.smooth_slider, self.smooth_spin = self._axis(form, u'平滑')
        self.even_slider, self.even_spin = self._axis(form, u'均匀分布')
        layout.addLayout(form)
        hint = QtWidgets.QLabel(u'在下方区域滚轮：段数  ·  Shift：平滑  ·  Ctrl：均匀\n'
                               u'参数始终基于原网格；确定后可一次撤销。\n'
                               u'预览期间锁定 Maya 其他操作，关闭或 Esc 恢复原网格。')
        hint.setWordWrap(True)
        hint.setStyleSheet('color: #bdbdbd; padding: 10px; background: #303030; border-radius: 4px;')
        hint.installEventFilter(self)
        layout.addWidget(hint)
        self.status = QtWidgets.QLabel(u'调整参数以开始预览。')
        self.status.setWordWrap(True)
        self.status.setMinimumHeight(50)
        layout.addWidget(self.status)
        buttons = QtWidgets.QHBoxLayout()
        reset = QtWidgets.QPushButton(u'重置')
        cancel = QtWidgets.QPushButton(u'取消')
        self.confirm = QtWidgets.QPushButton(u'确定')
        self.confirm.setDefault(True)
        self.confirm.setStyleSheet('background-color: #337eaa; padding: 6px 20px;')
        buttons.addWidget(reset)
        buttons.addStretch()
        buttons.addWidget(cancel)
        buttons.addWidget(self.confirm)
        layout.addLayout(buttons)
        self.timer = QtCore.QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(120)
        self.timer.timeout.connect(self._preview)
        self.mode.currentIndexChanged.connect(self._mode_changed)
        self.count.valueChanged.connect(self._schedule)
        self.smooth_spin.valueChanged.connect(self._schedule)
        self.even_spin.valueChanged.connect(self._schedule)
        reset.clicked.connect(self._reset)
        cancel.clicked.connect(self.reject)
        self.confirm.clicked.connect(self.accept)
        for message in (om.MSceneMessage.kBeforeNew, om.MSceneMessage.kBeforeOpen):
            self.callbacks.append(om.MSceneMessage.addCallback(message, self._scene_change))

    def _axis(self, form, label):
        widget = QtWidgets.QWidget()
        row = QtWidgets.QHBoxLayout(widget)
        row.setContentsMargins(0, 0, 0, 0)
        slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        slider.setRange(0, 100)
        spin = QtWidgets.QSpinBox()
        spin.setRange(0, 100)
        spin.setSingleStep(5)
        spin.setSuffix('%')
        slider.valueChanged.connect(spin.setValue)
        spin.valueChanged.connect(slider.setValue)
        row.addWidget(slider)
        row.addWidget(spin)
        form.addRow(label, widget)
        return slider, spin

    def _mode_changed(self, index):
        self.count.blockSignals(True)
        if index:
            self.count.setRange(3 if any(c for v, c in self.paths) else 1, 2048)
            self.count.setValue(self.base_counts[0])
        else:
            self.count.setRange(self.minimum_delta, 2048-max(self.base_counts))
            self.count.setValue(0)
        self.count.blockSignals(False)
        self._schedule()

    def _schedule(self, *unused):
        self.confirm.setEnabled(False)
        self.timer.start()

    def _flags(self):
        flags = dict(smoothing=self.smooth_spin.value()/100.0, even=self.even_spin.value()/100.0)
        flags['segments' if self.mode.currentIndex() else 'delta'] = self.count.value()
        return flags

    def _preview(self):
        self.timer.stop()
        flags = self._flags()
        try:
            identity = flags.get('delta') == 0 or (flags.get('segments') is not None and
                       all(n == flags['segments'] for n in self.base_counts))
            if identity and not flags['smoothing'] and not flags['even']:
                self.snapshot.restore()
                self.snapshot.result = None
            else:
                self.snapshot.preview(**flags)
            counts = ([flags['segments']]*len(self.paths) if 'segments' in flags else
                      [n+flags['delta'] for n in self.base_counts])
            self.status.setText(u'预览成功  |  段数：%s' % ', '.join(str(n) for n in counts))
            self.status.setStyleSheet('color: #a7d2e8;')
            self.confirm.setEnabled(True)
            cmds.refresh(force=True)
            return True
        except Exception as error:
            self.status.setText(u'未应用此参数，保留上次成功预览：\n%s' % _error_text(error))
            self.status.setStyleSheet('color: #edb27c;')
            self.confirm.setEnabled(False)
            return False

    def _reset(self):
        self.mode.setCurrentIndex(0)
        self.count.setValue(0)
        self.smooth_spin.setValue(0)
        self.even_spin.setValue(0)
        self._preview()

    def _scene_change(self, *unused):
        self.reject()

    def _cleanup(self):
        self.timer.stop()
        for callback in self.callbacks:
            om.MMessage.removeCallback(callback)
        self.callbacks = []

    def accept(self):
        if self.finished_edit:
            return
        if self.timer.isActive() and not self._preview():
            return
        if not self.confirm.isEnabled():
            return
        try:
            self.snapshot.commit()
        except Exception as error:
            self.status.setText(u'提交失败：%s' % _error_text(error))
            self.confirm.setEnabled(False)
            return
        self.finished_edit = True
        self._cleanup()
        super(ResampleDialog, self).accept()

    def reject(self):
        if not self.finished_edit:
            try:
                self.snapshot.restore()
            finally:
                self.finished_edit = True
                self._cleanup()
        super(ResampleDialog, self).reject()

    def closeEvent(self, event):
        self.reject()
        event.accept()

    def eventFilter(self, watched, event):
        if event.type() == QtCore.QEvent.Wheel:
            step = 1 if event.angleDelta().y() > 0 else -1
            modifiers = event.modifiers()
            spin = self.smooth_spin if modifiers & QtCore.Qt.ShiftModifier else (
                self.even_spin if modifiers & QtCore.Qt.ControlModifier else self.count)
            spin.setValue(spin.value()+step*(1 if spin is self.count else 5))
            return True
        return super(ResampleDialog, self).eventFilter(watched, event)


def show():
    global _DIALOG
    from .viewport import cancel
    cancel()
    if _DIALOG is not None and _DIALOG.isVisible():
        _DIALOG.raise_()
        _DIALOG.activateWindow()
        return _DIALOG
    try:
        load_plugin()
        snapshot = Snapshot()
    except Exception as error:
        cmds.confirmDialog(title='Loop Resample', message=u'无法开始：\n%s\n\n请先选择连续边链；带历史的模型请在副本上删除历史。' % _error_text(error), button=[u'关闭'])
        return None
    parent = wrapInstance(_pointer(OpenMayaUI.MQtUtil.mainWindow()), QtWidgets.QWidget)
    _DIALOG = ResampleDialog(snapshot, parent)
    _DIALOG.show()
    return _DIALOG
