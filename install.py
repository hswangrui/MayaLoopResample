# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Run inside Maya; installs the native menu and module, never a new shelf."""
from __future__ import print_function
import io
import os
import sys


def remove_legacy_shelf():
    from maya import cmds, mel
    shelf = 'LoopResample'
    if not cmds.shelfLayout(shelf, exists=True):
        return
    removed = False
    for child in cmds.shelfLayout(shelf, query=True, childArray=True) or []:
        if not cmds.shelfButton(child, exists=True):
            continue
        if (cmds.shelfButton(child, query=True, annotation=True) == 'Loop Resample Maya' and
                cmds.shelfButton(child, query=True, command=True) ==
                'import loop_resample_maya; loop_resample_maya.show()'):
            cmds.deleteUI(child)
            removed = True
    if not removed:
        return
    cmds.saveShelf(shelf, os.path.join(cmds.internalVar(userShelfDir=True), 'shelf_'+shelf).replace('\\', '/'))
    if not cmds.shelfLayout(shelf, query=True, childArray=True):
        mel.eval('deleteShelfTab "LoopResample";')


def install(root=None):
    from maya import cmds
    root = root or os.path.dirname(__file__)
    if isinstance(root, bytes):
        try:
            root = root.decode('utf-8')
        except UnicodeDecodeError:
            root = root.decode(sys.getfilesystemencoding() or 'mbcs')
    root = os.path.abspath(root).replace('\\', '/')
    if not os.path.isfile(os.path.join(root, 'plug-ins', 'loopResample.py')):
        raise RuntimeError('Choose the extracted Loop Resample folder containing install.py.')
    module_dir = os.path.join(cmds.internalVar(userAppDir=True), 'modules')
    if not os.path.isdir(module_dir):
        os.makedirs(module_dir)
    module_file = os.path.join(module_dir, 'LoopResampleMaya.mod')
    content = u'+ LoopResampleMaya 0.2.2 {0}\nPYTHONPATH +:= .\n'.format(root)
    if os.path.exists(module_file):
        with io.open(module_file, encoding='utf-8') as stream:
            previous = stream.read()
        if previous != content:
            answer = cmds.confirmDialog(title='Loop Resample',
                                        message=u'更新已有 Loop Resample 模块路径？\n'+module_file,
                                        button=[u'更新', u'取消'], cancelButton=u'取消', dismissString=u'取消')
            if answer != u'更新':
                return
    with io.open(module_file, 'w', encoding='utf-8') as stream:
        stream.write(content)
    if root not in sys.path:
        sys.path.insert(0, root)
    from loop_resample_maya.maya_bridge import load_plugin
    from loop_resample_maya import menus
    load_plugin()
    plugin = os.path.join(root, 'plug-ins', 'loopResample.py').replace('\\', '/')
    cmds.pluginInfo(plugin, edit=True, autoload=True)
    cmds.pluginInfo(savePluginPrefs=True)
    try:
        hotbox_installed = menus.install()
    except Exception as error:
        hotbox_installed = False
        cmds.warning('Loop Resample menu: '+str(error))
    if hotbox_installed:
        remove_legacy_shelf()
    menu_hint = (u'空格热盒 → 编辑网格（Edit Mesh）→ Loop Resample\n'
                 if hotbox_installed else u'热盒入口未安装，请查看脚本编辑器警告。\n')
    cmds.confirmDialog(title='Loop Resample',
                       message=u'安装完成（含 Alt 导航修复）。\n'+menu_hint+
                       u'已启用插件自动加载；保留原空格键和标记菜单。\n'
                       u'滚轮调段数，Shift 调平滑，Ctrl 调均匀。\n'
                       u'左键确认，右键取消。\n\n更新旧版后请重启 Maya，避免使用缓存模块。', button=[u'确定'])
    print('Loop Resample module: '+module_file)


def onMayaDroppedPythonFile(*unused):
    install()


if __name__ == '__main__':
    install()
