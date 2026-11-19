# SPDX-License-Identifier: GPL-3.0-or-later
"""Run from viewport_gui_smoke in its disposable GUI scene only."""
import os
from maya import cmds
import maya.utils
from PySide2 import QtCore
from loop_resample_maya import menus, viewport
from loop_resample_maya.maya_bridge import load_plugin
import maya_smoke


def wait(milliseconds):
    loop = QtCore.QEventLoop()
    QtCore.QTimer.singleShot(milliseconds, loop.quit)
    loop.exec_()


def run(stream):
    load_plugin()
    maya.utils.processIdleEvents()
    menus.disable()
    parent = menus._parent()
    callback = cmds.menu(parent, query=True, postMenuCommand=True)
    stream.write('Native menu=%r; builder=%r\n' % (parent, callback))
    stream.flush()
    space = (cmds.hotkey('Space', query=True, name=True),
             cmds.hotkey('Space', query=True, releaseName=True))
    menu_set = cmds.menuSet(query=True, currentMenuSet=True)
    assert menus.install()
    assert cmds.menu(parent, query=True, numberOfItems=True) > 3
    menus.disable()
    baseline = cmds.menu(parent, query=True, itemArray=True)
    sentinel = cmds.menuItem('loopResampleTestOtherTool', parent=parent, label='Other tool sentinel')
    assert menus.install()
    first = cmds.menu(parent, query=True, itemArray=True)
    assert menus.install()
    assert cmds.menu(parent, query=True, itemArray=True) == first
    assert len(first) == len(baseline)+3
    assert cmds.menuItem(parent+'|'+menus._ITEMS[1], query=True, label=True) == 'Loop Resample'
    assert not cmds.menuItem(parent+'|loopResampleMayaPanel', exists=True)
    cmds.menuItem('loopResampleMayaPanel', parent=parent, label='Legacy panel', docTag=menus._OWNER)
    cmds.menuItem(parent+'|'+menus._ITEMS[1], edit=True, label='Legacy viewport')
    assert menus.install()
    assert cmds.menuItem(parent+'|'+menus._ITEMS[1], query=True, label=True) == 'Loop Resample'
    assert not cmds.menuItem(parent+'|loopResampleMayaPanel', exists=True)
    assert cmds.menu(parent, query=True, itemArray=True) == first
    assert cmds.menu(parent, query=True, postMenuCommand=True) == callback
    assert cmds.menuSet(query=True, currentMenuSet=True) == menu_set
    assert space == (cmds.hotkey('Space', query=True, name=True),
                     cmds.hotkey('Space', query=True, releaseName=True))
    menus.disable()
    assert cmds.menuItem(sentinel, exists=True)
    assert cmds.menu(parent, query=True, itemArray=True) == baseline+[sentinel.rsplit('|', 1)[-1]]
    cmds.deleteUI(sentinel)
    assert menus.install()
    stream.write('Hotbox menu registration, idempotence, native callback and user settings: OK\n')
    stream.flush()

    cmds.file(new=True, force=True)
    obj = cmds.polyCylinder(subdivisionsX=8, subdivisionsY=2, constructionHistory=False)[0]
    cmds.polySoftEdge(obj, angle=180, constructionHistory=False)
    maya_smoke.select_row(obj)
    before = maya_smoke.fingerprint(obj)
    panel = next(p for p in cmds.getPanel(visiblePanels=True) if cmds.getPanel(typeOf=p) == 'modelPanel')
    cmds.setFocus(panel)
    command = cmds.menuItem(parent+'|'+menus._ITEMS[1], query=True, command=True)
    cmds.hotBox()
    eval(compile(command, '<LoopResampleMenu>', 'exec'), {})
    for unused in range(10):
        wait(40)
        maya.utils.processIdleEvents()
        if viewport._SESSION is not None and viewport._SESSION.active:
            break
    assert viewport._SESSION is not None and viewport._SESSION.active, 'Menu did not start viewport tool'
    assert menus._LAUNCH is None
    viewport._SESSION.step(1)
    assert viewport._SESSION.flush()
    viewport.cancel()
    assert maya_smoke.fingerprint(obj) == before
    stream.write('Real hotbox open -> menu command -> preview -> cancel: OK\n')
    stream.flush()

    cmds.file(new=True, force=True)
    cmds.flushUndo()
    plugin = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'plug-ins', 'loopResample.py')
    cmds.pluginInfo(plugin, edit=True, autoload=True)
    cmds.pluginInfo(savePluginPrefs=True)
    assert cmds.pluginInfo(plugin, query=True, autoload=True)
    plugin_name = cmds.pluginInfo(plugin, query=True, name=True)
    cmds.unloadPlugin(plugin_name)
    assert not cmds.pluginInfo(plugin, query=True, loaded=True), 'Plugin did not unload'
    assert not cmds.menuItem(parent+'|'+menus._ITEMS[1], exists=True), 'Owned menu survived unload'
    load_plugin()
    for unused in range(10):
        wait(40)
        maya.utils.processIdleEvents()
        if cmds.menuItem(parent+'|'+menus._ITEMS[1], exists=True):
            break
    assert cmds.pluginInfo(plugin, query=True, loaded=True), 'Plugin did not reload'
    assert menus._ENABLED, 'Plugin did not enable deferred menus'
    assert cmds.menuItem(parent+'|'+menus._ITEMS[1], exists=True), 'Deferred menu did not reinstall'
    stream.write('Plugin unload cleanup / reload menu / auto-load preference: OK\n')
    stream.flush()
    import install_checks
    install_checks.run(stream)
