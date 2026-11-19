# SPDX-License-Identifier: GPL-3.0-or-later
"""Installer regression checks for a disposable Maya GUI profile only."""
import imp
import os
from maya import cmds, mel
from PySide2 import QtCore, QtWidgets, QtTest


def run(stream):
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    installer = imp.load_source('loop_resample_test_installer', os.path.join(root, 'install.py'))
    top = mel.eval('$tmp = $gShelfTopLevel')
    assert not cmds.shelfLayout('LoopResample', exists=True), 'Test requires a fresh isolated profile'
    shelves_before = cmds.tabLayout(top, query=True, childArray=True)
    selection_before = cmds.tabLayout(top, query=True, selectTab=True)
    confirm = cmds.confirmDialog
    cmds.confirmDialog = lambda **kwargs: kwargs['button'][0]
    try:
        installer.install(root)
        assert not cmds.shelfLayout('LoopResample', exists=True)
        assert cmds.tabLayout(top, query=True, childArray=True) == shelves_before
        assert cmds.tabLayout(top, query=True, selectTab=True) == selection_before
        shelf = cmds.shelfLayout('LoopResample', parent=top)
        owned = cmds.shelfButton(parent=shelf, annotation='Loop Resample Maya',
                                 command='import loop_resample_maya; loop_resample_maya.show()',
                                 sourceType='python')
        other = cmds.shelfButton(parent=shelf, label='Other tool',
                                 annotation='Loop Resample Maya', command='print(123)', sourceType='python')
        installer.install(root)
        assert not cmds.shelfButton(owned, exists=True)
        assert cmds.shelfButton(other, exists=True)
        assert cmds.shelfButton(other, query=True, command=True) == 'print(123)'
        assert cmds.shelfLayout(shelf, exists=True)
        installer.install(root)
        assert cmds.shelfLayout(shelf, query=True, childArray=True) == [other.rsplit('|', 1)[-1]]
    finally:
        cmds.confirmDialog = confirm
    stream.write('Fresh install creates no shelf; migration preserves other buttons: OK\n')
    stream.flush()

    cmds.deleteUI(other)
    cmds.shelfButton(parent=shelf, annotation='Loop Resample Maya',
                     command='import loop_resample_maya; loop_resample_maya.show()', sourceType='python')
    timer = QtCore.QTimer()
    timer.setInterval(50)
    def accept_native_prompt():
        dialog = QtWidgets.QApplication.activeModalWidget()
        if dialog is not None:
            QtTest.QTest.keyClick(dialog, QtCore.Qt.Key_Return)
    timer.timeout.connect(accept_native_prompt)
    timer.start()
    try:
        installer.remove_legacy_shelf()
    finally:
        timer.stop()
    assert not cmds.shelfLayout('LoopResample', exists=True)
    assert cmds.tabLayout(top, query=True, childArray=True) == shelves_before
    shelf_file = os.path.join(cmds.internalVar(userShelfDir=True), 'shelf_LoopResample.mel')
    assert not os.path.isfile(shelf_file)
    assert os.path.isfile(shelf_file+'.deleted')
    stream.write('Empty legacy shelf removal uses native confirmation and file backup: OK\n')
    stream.flush()
