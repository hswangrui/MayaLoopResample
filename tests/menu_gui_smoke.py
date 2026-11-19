# SPDX-License-Identifier: GPL-3.0-or-later
"""Menu/installer tests only; run in a NEW disposable Maya GUI process."""
from __future__ import print_function
import os
import sys
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'tests'))
os.environ['LOOP_RESAMPLE_GUI_TEST'] = '1'
from maya import cmds
import maya.utils


def run():
    success = False
    path = os.path.join(ROOT, '.maya_test', 'menu'+cmds.about(version=True)+'.log')
    with open(path, 'w') as stream:
        try:
            import hotbox_checks
            hotbox_checks.run(stream)
            success = True
            stream.write('MENU_TEST_SUCCESS=True\n')
        except Exception:
            traceback.print_exc(file=stream)
    cmds.quit(force=True, exitCode=0 if success else 1)


maya.utils.executeDeferred(run)
