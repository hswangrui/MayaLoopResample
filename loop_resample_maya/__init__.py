# SPDX-License-Identifier: GPL-3.0-or-later
"""Loop Resample for Maya 2019/2020. No third-party Python dependencies."""
__version__ = '0.2.2'


def show():
    from .viewport import start
    return start()


def show_panel():
    from .ui import show as show_window
    return show_window()


def apply(delta=0, segments=None, smoothing=0.0, even=0.0):
    from .maya_bridge import load_plugin
    from maya import cmds
    load_plugin()
    flags = dict(delta=delta, smooth=smoothing, even=even)
    if segments is not None:
        flags['segments'] = segments
    result = cmds.loopResample(**flags)
    return result[0] if isinstance(result, (list, tuple)) else result
