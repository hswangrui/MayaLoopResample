# SPDX-License-Identifier: GPL-3.0-or-later
"""Undoable Maya Python API 2.0 command (Maya 2019/2020, Python 2.7)."""
import inspect
import os
import sys
import maya.api.OpenMaya as om

_SOURCE = globals().get('__file__') or inspect.currentframe().f_code.co_filename
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(_SOURCE)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from loop_resample_maya.maya_bridge import Snapshot, _PAYLOADS


def maya_useNewAPI():
    pass


class LoopResampleCommand(om.MPxCommand):
    def __init__(self):
        super(LoopResampleCommand, self).__init__()
        self.snapshot = None
        self.result = None

    @staticmethod
    def creator():
        return LoopResampleCommand()

    @staticmethod
    def syntax_creator():
        syntax = om.MSyntax()
        syntax.addFlag('-d', '-delta', om.MSyntax.kLong)
        syntax.addFlag('-s', '-segments', om.MSyntax.kLong)
        syntax.addFlag('-sm', '-smooth', om.MSyntax.kDouble)
        syntax.addFlag('-ev', '-even', om.MSyntax.kDouble)
        syntax.addFlag('-pl', '-payload', om.MSyntax.kString)
        return syntax

    def doIt(self, arguments):
        args = om.MArgDatabase(self.syntax(), arguments)
        if args.isFlagSet('-pl'):
            self.snapshot, self.result = _PAYLOADS.pop(args.flagArgumentString('-pl', 0))
        else:
            self.snapshot = Snapshot()
            flags = {}
            for flag, name, method in (('-d', 'delta', args.flagArgumentInt),
                                       ('-s', 'segments', args.flagArgumentInt),
                                       ('-sm', 'smoothing', args.flagArgumentDouble),
                                       ('-ev', 'even', args.flagArgumentDouble)):
                if args.isFlagSet(flag):
                    flags[name] = method(flag, 0)
            self.result = self.snapshot.solve(**flags)
        self.redoIt()
        self.setResult(len(self.result[2]))

    def redoIt(self):
        try:
            self.snapshot.write(*self.result)
        except Exception:
            self.snapshot.restore()
            raise

    def undoIt(self):
        self.snapshot.restore()

    def isUndoable(self):
        return self.snapshot is not None and self.result is not None


def initializePlugin(obj):
    om.MFnPlugin(obj, 'Loop Resample Maya contributors / YLIANG original', '0.2.2', 'Any').registerCommand(
        'loopResample', LoopResampleCommand.creator, LoopResampleCommand.syntax_creator)
    from loop_resample_maya import menus
    menus.enable()


def uninitializePlugin(obj):
    from loop_resample_maya import menus
    menus.disable()
    om.MFnPlugin(obj).deregisterCommand('loopResample')
