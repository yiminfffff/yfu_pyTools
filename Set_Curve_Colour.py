import maya.cmds as cmds

def set_curve_color(color_index=None):
    selection = cmds.ls(selection=True, type='transform')
    if not selection:
        cmds.warning("Please select at least one curve.")
        return
    
    for obj in selection:
        shapes = cmds.listRelatives(obj, shapes=True, type='nurbsCurve')
        if shapes:
            for shape in shapes:
                cmds.setAttr(f"{shape}.overrideEnabled", 1 if color_index is not None else 0)
                if color_index is not None:
                    cmds.setAttr(f"{shape}.overrideColor", color_index)

def create_ui():
    if cmds.window("curveColorUI", exists=True):
        cmds.deleteUI("curveColorUI")
    
    window = cmds.window("curveColorUI", title="Curve Color Setter", widthHeight=(200, 180))
    cmds.columnLayout(adjustableColumn=True)
    
    cmds.button(label="Red", bgc=(1, 0, 0), command=lambda _: set_curve_color(13))
    cmds.button(label="Yellow", bgc=(1, 1, 0), command=lambda _: set_curve_color(17))
    cmds.button(label="Blue", bgc=(0, 0, 1), command=lambda _: set_curve_color(6))
    cmds.button(label="Light Blue", bgc=(0, 1, 1), command=lambda _: set_curve_color(18))
    cmds.button(label="Orange", bgc=(1, 0.5, 0), command=lambda _: set_curve_color(21))
    cmds.button(label="Clear Color", bgc=(0.5, 0.5, 0.5), command=lambda _: set_curve_color())
    
    cmds.showWindow(window)

create_ui()
