import maya.cmds as cmds


def unlock_and_show_transform_attrs():
    """
    Unlock and make transform attributes visible and keyable
    for selected objects.
    """

    selection = cmds.ls(selection=True)

    if not selection:
        cmds.warning("Please select at least one object.")
        return

    transform_attrs = [
        "translateX", "translateY", "translateZ",
        "rotateX", "rotateY", "rotateZ",
        "scaleX", "scaleY", "scaleZ"
    ]

    for obj in selection:
        for attr in transform_attrs:
            full_attr = "{}.{}".format(obj, attr)

            if cmds.objExists(full_attr):

                # Unlock attribute
                try:
                    cmds.setAttr(full_attr, lock=False)
                except:
                    pass

                # Make attribute visible in channel box
                try:
                    cmds.setAttr(full_attr, keyable=True)
                except:
                    pass

        print("Updated: {}".format(obj))


unlock_and_show_transform_attrs()
