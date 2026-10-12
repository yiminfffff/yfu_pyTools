# Maya Python: create a locator, place it, then set locator.build to 1.
# The locator will be parented to the originally selected controller while
# preserving its current world-space position.

import maya.cmds as cmds


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

    def finish_locator_setup():
        if not cmds.objExists(controller) or not cmds.objExists(locator):
            cmds.warning("The controller or locator was deleted; setup could not be completed.")
            return

        # Absolute parenting preserves the locator's current world position.
        cmds.parent(locator, controller, absolute=True)
        cmds.select(locator, replace=True)
        cmds.inViewMessage(amg="Locator parented under the controller.",
                           position="topCenter", fade=True)

    cmds.scriptJob(attributeChange=[locator + ".build", finish_locator_setup],
                   runOnce=True, protected=True)
    cmds.select(locator, replace=True)
    cmds.inViewMessage(
        amg="Position the locator, then set <hl>build</hl> to 1 to finish setup.",
        position="topCenter", fade=True)
    return locator


create_motion_probe()
