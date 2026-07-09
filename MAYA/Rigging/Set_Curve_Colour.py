import maya.cmds as cmds

def set_curve_color(color_index=None):
    """
    Sets the color index for the selected curves.
    If color_index is None, it clears the color.
    """
    selection = cmds.ls(selection=True, type='transform')
    if not selection:
        cmds.warning("Please select at least one object!")
        return
    
    enabled = 1 if color_index is not None else 0
    
    for obj in selection:
        shapes = cmds.listRelatives(obj, shapes=True, type='nurbsCurve')
        if shapes:
            for shape in shapes:
                cmds.setAttr(f"{shape}.overrideEnabled", enabled)
                if color_index is not None:
                    cmds.setAttr(f"{shape}.overrideColor", color_index)

def get_color_rgb(index):
    """Helper to get RGB from Maya index"""
    temp_attr = cmds.colorSliderGrp('tempColorAttr', rgb=(1,1,1))
    cmds.colorSliderGrp(temp_attr, edit=True, rgb=cmds.colorIndex(index, query=True))
    rgb_val = cmds.colorSliderGrp(temp_attr, query=True, rgb=True)
    cmds.deleteUI(temp_attr)
    return rgb_val

def create_ui():
    window_name = "curveColorTool"
    
    if cmds.window(window_name, exists=True):
        cmds.deleteUI(window_name)
    
    # Window settings
    window = cmds.window(window_name, title="Rigging Color Tool", widthHeight=(250, 380))
    
    # Main layout centered
    main_col = cmds.columnLayout(adjustableColumn=True, columnOffset=["both", 10])
    
    # --- Header ---
    cmds.text(label="Rigging Color Setter", align="center", font="boldLabelFont", height=30)
    
    # --- Section 1: Primary Colors ---
    cmds.text(label="Primary Controls", align="center", font="plainLabelFont")
    
    # Row for Primary Buttons
    primary_layout = cmds.rowLayout(numberOfColumns=3, columnWidth3=(80, 80, 80), columnAlign=[(1, "center"), (2, "center"), (3, "center")], adjustableColumn=3)
    
    # 13: Red, 17: Yellow, 6: Blue
    # Height set to 50
    cmds.button(label="", height=50, bgc=get_color_rgb(13), command=lambda _: set_curve_color(13))
    cmds.button(label="", height=50, bgc=get_color_rgb(17), command=lambda _: set_curve_color(17))
    cmds.button(label="", height=50, bgc=get_color_rgb(6), command=lambda _: set_curve_color(6))
    
    cmds.setParent(main_col)
    
    # Separator
    cmds.separator(height=15, style='in')
    
    # --- Section 2: Secondary Colors ---
    cmds.text(label="Secondary / FK", align="center", font="plainLabelFont")
    
    # Row for Secondary Buttons
    # Reordered: 9 (Green), 21 (Orange), 18 (Light Blue)
    secondary_layout = cmds.rowLayout(numberOfColumns=3, columnWidth3=(80, 80, 80), columnAlign=[(1, "center"), (2, "center"), (3, "center")], adjustableColumn=3)
    
    # 9: Green (Left)
    cmds.button(label="", height=50, bgc=get_color_rgb(9), command=lambda _: set_curve_color(9))
    # 21: Orange (Middle)
    cmds.button(label="", height=50, bgc=get_color_rgb(21), command=lambda _: set_curve_color(21))
    # 18: Light Blue (Right)
    cmds.button(label="", height=50, bgc=get_color_rgb(18), command=lambda _: set_curve_color(18))

    cmds.setParent(main_col)
    
    # Separator
    cmds.separator(height=15, style='in')
    
    # --- Section 3: Full Palette (The Rest) ---
    cmds.text(label="Full Palette (8-31)", align="center", font="plainLabelFont")
    
    # Grid for the rest of the colors
    # Exclude used indices: 6, 9, 13, 17, 18, 21
    used_indices = [6, 9, 13, 17, 18, 21]
    remaining_indices = [i for i in range(8, 32) if i not in used_indices]
    
    grid_layout = cmds.gridLayout(numberOfColumns=8, cellWidthHeight=(24, 24))
    
    for index in remaining_indices:
        rgb_val = get_color_rgb(index)
        btn = cmds.button(label="", bgc=rgb_val, command=lambda x, i=index: set_curve_color(i))
        # Tooltip to show index number
        cmds.popupMenu()
        cmds.menuItem(label=f"Index {index}")

    cmds.setParent(main_col)
    
    # --- Footer ---
    cmds.separator(height=10, style='none')
    cmds.button(label="Clear Color", height=30, bgc=(0.6, 0.6, 0.6), command=lambda _: set_curve_color(None))
    
    cmds.showWindow(window)

if __name__ == "__main__":
    create_ui()
