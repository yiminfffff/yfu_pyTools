r"""Reset selected APEX controls immediately, without opening a window.

Python shelf tool:
    import runpy
    runpy.run_path(r"D:\UH\L6SA\Showreel\APEX_Milo\Animation_Reset_Controls.py", run_name="__main__")

Uses each control's rig defaults (normally translate/rotate = 0, scale = 1).
Other control parameters return to their rig defaults as well. Existing
animation is handled by Houdini's native reset operation, without clearing
the animation curves. The operation is one undo step.
"""

import hou


def reset_selected_controls():
    from apex.ui import statecommandutils
    from apex import stateparmutils

    viewers = [pane for pane in hou.ui.paneTabs()
               if pane.type() == hou.paneTabType.SceneViewer]
    viewers.sort(key=lambda pane: not pane.isCurrentTab())

    for viewer in viewers:
        if not statecommandutils.inAnimateState(viewer):
            continue

        args = {}
        viewer.runStateCommand("getState", args)
        state = args.get("state")
        if state is None:
            raise hou.Error("Cannot access the APEX Animate state.")

        # Only use the viewport's selected controls, never the Animate SOP.
        controls = state.control_manager.controlPaths()
        if not controls:
            hou.ui.setStatusMessage("Select APEX controls in the viewport first.")
            return

        with hou.undos.group("Reset Selected Controls"):
            stateparmutils.resetSelectedControlParmValues(state)
            if state.hud_channel_panel is not None:
                state.hud_channel_panel.onParmChangedEvent({})

        viewer.curViewport().draw()
        hou.ui.setStatusMessage("Reset {} selected control(s).".format(len(controls)))
        return

    hou.ui.setStatusMessage("Enter APEX Animate and select controls first.")


if __name__ == "__main__":
    reset_selected_controls()
