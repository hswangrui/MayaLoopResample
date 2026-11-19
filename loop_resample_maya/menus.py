# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Append to Maya's Edit Mesh menu, also used by the native space hotbox."""
from maya import cmds, mel
import maya.utils

_OWNER = 'loop_resample_maya/native_menu'
_ITEMS = ('loopResampleMayaDivider', 'loopResampleMayaViewport')
_LEGACY_ITEMS = ('loopResampleMayaPanel',)
_ENABLED = False
_LAUNCH = None


def _parent():
    return mel.eval('global string $gMainEditMeshMenu; $loopResampleMenuParent = $gMainEditMeshMenu;')


def _owned(name):
    return cmds.menuItem(name, exists=True) and cmds.menuItem(name, query=True, docTag=True) == _OWNER


def install():
    if cmds.about(batch=True):
        return False
    parent = _parent()
    if not parent or not cmds.menu(parent, exists=True):
        raise RuntimeError('Maya Edit Mesh menu is not available yet.')
    names = [parent+'|'+item for item in _ITEMS]
    for name in names:
        if cmds.menuItem(name, exists=True) and not _owned(name):
            raise RuntimeError('Loop Resample menu name is already owned by another tool: '+name)
    if not cmds.menu(parent, query=True, numberOfItems=True):
        callback = cmds.menu(parent, query=True, postMenuCommand=True) or ''
        tokens = callback.strip().rstrip(';').replace('"', '').split()
        if (len(tokens) != 2 or tokens[0] != 'PolygonsBuildMenu' or
                tokens[1].rsplit('|', 1)[-1] != parent.rsplit('|', 1)[-1]):
            raise RuntimeError('Edit Mesh has a custom lazy builder; left unchanged. Open Edit Mesh once and reinstall.')
        mel.eval('PolygonsBuildMenu "'+parent+'";')
        if not cmds.menu(parent, query=True, numberOfItems=True):
            raise RuntimeError('Native Edit Mesh menu did not finish building.')
    for item in _LEGACY_ITEMS:
        name = parent+'|'+item
        if _owned(name):
            cmds.deleteUI(name)
    if not cmds.menuItem(names[0], exists=True):
        cmds.menuItem(_ITEMS[0], parent=parent, divider=True, docTag=_OWNER)
    flags = dict(label='Loop Resample', docTag=_OWNER,
                 annotation=u'边环重采样；Alt + 鼠标保留 Maya 相机导航',
                 sourceType='python',
                 command='from loop_resample_maya import menus; menus.launch()')
    if cmds.menuItem(names[1], exists=True):
        cmds.menuItem(names[1], edit=True, **flags)
    else:
        cmds.menuItem(_ITEMS[1], parent=parent, **flags)
    cmds.hotBox(updateMenus=True)
    return True


def _install_deferred():
    if not _ENABLED:
        return
    try:
        install()
    except Exception as error:
        cmds.warning('Loop Resample menu: '+str(error))


def enable():
    global _ENABLED
    if not cmds.about(batch=True):
        _ENABLED = True
        maya.utils.executeDeferred(_install_deferred)


def disable():
    global _ENABLED, _LAUNCH
    _ENABLED = False
    if _LAUNCH is not None:
        _LAUNCH.stop()
        _LAUNCH.deleteLater()
        _LAUNCH = None
    if cmds.about(batch=True):
        return
    parent = _parent()
    for item in reversed(_ITEMS+_LEGACY_ITEMS):
        name = parent+'|'+item
        if _owned(name):
            cmds.deleteUI(name)
    cmds.hotBox(updateMenus=True)


def launch():
    """Start only after the hotbox and the menu's mouse release have finished."""
    global _LAUNCH
    from PySide2 import QtCore, QtWidgets
    if _LAUNCH is not None:
        _LAUNCH.stop()
        _LAUNCH.deleteLater()
        _LAUNCH = None
    timer = QtCore.QTimer(QtWidgets.QApplication.instance())
    timer.setInterval(30)
    timer.setProperty('attempts', 0)
    _LAUNCH = timer

    def begin():
        global _LAUNCH
        attempts = int(timer.property('attempts'))+1
        timer.setProperty('attempts', attempts)
        if attempts > 166:
            timer.stop()
            timer.deleteLater()
            if _LAUNCH is timer:
                _LAUNCH = None
            cmds.warning('Loop Resample: menu remained open; click the entry again to start.')
            return
        if QtWidgets.QApplication.mouseButtons() or QtWidgets.QApplication.activePopupWidget() is not None:
            return
        timer.stop()
        timer.deleteLater()
        if _LAUNCH is timer:
            _LAUNCH = None
        cmds.hotBox(release=True)
        from . import show
        maya.utils.executeDeferred(show)

    timer.timeout.connect(begin)
    timer.start()
