# Maya Python: select a controller and run this script.
# Set the new locator's build attribute to 1 to create an editable motion curve.

import math
import maya.cmds as cmds


def _set_rgb_override(node, color):
    nodes = [node] + (cmds.listRelatives(node, shapes=True, fullPath=True) or [])
    for item in nodes:
        try:
            cmds.setAttr(item + ".overrideEnabled", 1)
            cmds.setAttr(item + ".overrideRGBColors", 1)
            cmds.setAttr(item + ".overrideColorRGB", color[0], color[1], color[2],
                         type="double3")
        except Exception:
            pass


def create_motion_probe():
    selection = cmds.ls(selection=True, long=True, objectsOnly=True) or []
    if not selection:
        cmds.warning("Please select a controller first.")
        return

    controller = selection[0]
    if cmds.nodeType(controller) in ("mesh", "nurbsCurve", "nurbsSurface"):
        parents = cmds.listRelatives(controller, parent=True, fullPath=True) or []
        if not parents:
            cmds.warning("The selected object has no usable parent controller.")
            return
        controller = parents[0]

    if not cmds.attributeQuery("translate", node=controller, exists=True):
        cmds.warning("Please select a transform controller.")
        return

    short_name = controller.rsplit("|", 1)[-1].replace(":", "_")
    locator = cmds.spaceLocator(name=short_name + "_motionProbe")[0]
    cmds.matchTransform(locator, controller, position=True, rotation=True)
    cmds.addAttr(locator, longName="build", attributeType="double",
                 minValue=0.0, maxValue=1.0, defaultValue=0.0, keyable=True)

    # Keep only the custom build attribute visible in the Channel Box.
    for channel in ("translateX", "translateY", "translateZ",
                    "rotateX", "rotateY", "rotateZ",
                    "scaleX", "scaleY", "scaleZ", "visibility"):
        cmds.setAttr(locator + "." + channel, keyable=False, channelBox=False)

    state = {"built": False}

    def build_editable_curve():
        if state["built"]:
            return
        if not cmds.objExists(controller) or not cmds.objExists(locator):
            cmds.warning("The controller or locator was deleted; setup could not be completed.")
            return

        state["built"] = True
        # Preserve the placed world-space position while parenting the locator.
        cmds.parent(locator, controller, absolute=True)
        old_time = cmds.currentTime(query=True)
        start = cmds.playbackOptions(query=True, minTime=True)
        end = cmds.playbackOptions(query=True, maxTime=True)
        frames = list(range(int(math.ceil(start)), int(math.floor(end)) + 1))
        if len(frames) < 2:
            cmds.warning("The playback range must contain at least two whole frames.")
            state["built"] = False
            return

        group = cmds.group(empty=True, name=short_name + "_motionCurve_GRP")
        key_times = cmds.keyframe(controller, query=True, timeChange=True) or []

        def get_key_frames():
            return set(int(round(value)) for value in key_times)

        def sample_locator_positions():
            points = []
            for frame in frames:
                cmds.currentTime(frame, edit=True)
                points.append(cmds.xform(locator, query=True, worldSpace=True,
                                         translation=True))
            return points

        try:
            positions = sample_locator_positions()
            curve = cmds.curve(name=short_name + "_motionCurve", degree=1,
                               point=positions)
            cmds.parent(curve, group, absolute=True)
            cmds.setAttr(curve + ".overrideEnabled", 1)
            cmds.setAttr(curve + ".overrideRGBColors", 1)
            cmds.setAttr(curve + ".overrideColorRGB", 0.95, 0.78, 0.2,
                         type="double3")
            cmds.setAttr(curve + ".overrideDisplayType", 2)

            handles = []
            key_frames = get_key_frames()
            for index, frame in enumerate(frames):
                handle = cmds.spaceLocator(
                    name="{}_motionPoint_{:04d}".format(short_name, frame))[0]
                cmds.xform(handle, worldSpace=True, translation=positions[index])
                cmds.parent(handle, group, absolute=True)
                handles.append(handle)
                shape = (cmds.listRelatives(handle, shapes=True, fullPath=True) or [None])[0]
                if shape:
                    for axis in "XYZ":
                        try:
                            cmds.setAttr(shape + ".localScale" + axis, 0.12)
                        except Exception:
                            pass
                for channel in ("translateX", "translateY", "translateZ",
                                "rotateX", "rotateY", "rotateZ",
                                "scaleX", "scaleY", "scaleZ", "visibility"):
                    try:
                        cmds.setAttr(handle + "." + channel,
                                     keyable=False, channelBox=False)
                    except Exception:
                        pass
                color = (1.0, 0.35, 0.08) if frame in key_frames else (0.1, 0.8, 1.0)
                _set_rgb_override(handle, color)
                try:
                    cmds.setAttr(handle + ".hiddenInOutliner", True)
                except Exception:
                    pass

            state.update({"group": group, "curve": curve, "frames": frames,
                          "handles": handles, "busy": False, "pending": None})

            def refresh_curve():
                if not cmds.objExists(group):
                    return
                saved_time = cmds.currentTime(query=True)
                state["busy"] = True
                try:
                    points = sample_locator_positions()
                    current_keys = set(int(round(value)) for value in
                                       (cmds.keyframe(controller, query=True,
                                                      timeChange=True) or []))
                    for index, (frame, handle, point) in enumerate(
                            zip(frames, handles, points)):
                        cmds.xform(handle, worldSpace=True, translation=point)
                        cmds.xform(curve + ".cv[{}]".format(index),
                                   worldSpace=True, translation=point)
                        color = ((1.0, 0.35, 0.08) if frame in current_keys
                                 else (0.1, 0.8, 1.0))
                        _set_rgb_override(handle, color)
                finally:
                    cmds.currentTime(saved_time, edit=True)
                    state["busy"] = False

            def apply_point_edit(frame):
                if state["busy"] or not cmds.objExists(controller):
                    return
                handle = handles[frames.index(frame)]
                desired = cmds.xform(handle, query=True, worldSpace=True,
                                     translation=True)
                saved_time = cmds.currentTime(query=True)
                state["busy"] = True
                try:
                    cmds.currentTime(frame, edit=True)
                    probe_pos = cmds.xform(locator, query=True, worldSpace=True,
                                           translation=True)
                    controller_pos = cmds.xform(controller, query=True,
                                                worldSpace=True, translation=True)
                    delta = [desired[i] - probe_pos[i] for i in range(3)]
                    new_pos = [controller_pos[i] + delta[i] for i in range(3)]
                    cmds.xform(controller, worldSpace=True, translation=new_pos)
                    cmds.setKeyframe(controller,
                                     attribute=["translateX", "translateY", "translateZ"],
                                     time=(frame, frame))
                except Exception as error:
                    cmds.warning("Could not key the controller at frame {}: {}".format(
                        frame, error))
                finally:
                    cmds.currentTime(saved_time, edit=True)
                    state["busy"] = False
                refresh_curve()

            # Debounce handle changes so keying and resampling happen after dragging pauses.
            try:
                try:
                    from PySide6 import QtCore
                except ImportError:
                    from PySide2 import QtCore
                timer = QtCore.QTimer()
                timer.setSingleShot(True)
                timer.setInterval(120)

                def commit_pending():
                    frame = state.get("pending")
                    state["pending"] = None
                    if frame is not None:
                        apply_point_edit(frame)

                timer.timeout.connect(commit_pending)
                state["timer"] = timer
            except ImportError:
                state["timer"] = None

            for frame, handle in zip(frames, handles):
                def queue_edit(frame=frame):
                    if state["busy"]:
                        return
                    state["pending"] = frame
                    timer = state.get("timer")
                    if timer:
                        timer.start()
                    else:
                        apply_point_edit(frame)

                cmds.scriptJob(attributeChange=[handle + ".translate", queue_edit],
                               allChildren=True, protected=True)

            cmds.currentTime(old_time, edit=True)
            cmds.select(clear=True)
            cmds.setToolTo("moveSuperContext")
            cmds.inViewMessage(
                amg="Editable motion curve ready. Select and move a colored point.",
                position="topCenter", fade=True)
        except Exception:
            if cmds.objExists(group):
                cmds.delete(group)
            state["built"] = False
            raise
        finally:
            cmds.currentTime(old_time, edit=True)

    cmds.scriptJob(attributeChange=[locator + ".build", build_editable_curve],
                   runOnce=True, protected=True)
    cmds.select(locator, replace=True)
    cmds.inViewMessage(
        amg="Position the locator, then set <hl>build</hl> to 1 to show the editable curve.",
        position="topCenter", fade=True)
    return locator


create_motion_probe()
