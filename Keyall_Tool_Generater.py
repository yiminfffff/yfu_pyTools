import maya.cmds as cmds
import maya.mel as mel

def generate_keyframe_tool():
    """
    Main function to generate the tool and add it to the shelf
    """
    selected_objects = cmds.ls(selection=True)
    if not selected_objects:
        cmds.warning("Please select at least one object!")
        return

    selected_objects_str = str(selected_objects)

    def confirm_selection(*_):
        selected_attrs = []
        select_only_mode = cmds.checkBox(select_only_cb, query=True, value=True)

        if not select_only_mode:
            if cmds.checkBox(move_cb, query=True, value=True):
                selected_attrs.extend(["translateX", "translateY", "translateZ"])
            if cmds.checkBox(rotate_cb, query=True, value=True):
                selected_attrs.extend(["rotateX", "rotateY", "rotateZ"])

        create_shelf_tool(selected_attrs, select_only=select_only_mode)

        if cmds.window("KeyframeToolWin", exists=True):
            cmds.deleteUI("KeyframeToolWin")

    def create_shelf_tool(selected_attrs, select_only=False):
        if not select_only and not selected_attrs:
            cmds.warning("No channels selected!")
            return

        if select_only:
            script_content = f"""
import maya.cmds as cmds

def select_stored_objects():
    stored_objects = {selected_objects_str}
    if not stored_objects:
        cmds.warning("No objects stored in the tool!")
        return
    cmds.select(stored_objects, replace=True)
    print("Objects selected.")

select_stored_objects()
"""
            tool_label = "Set (Select Only)"
            tool_annotation = "Select only the stored objects"
        else:
            script_content = f"""
import maya.cmds as cmds

def add_keyframes_to_objects():
    selected_objects = {selected_objects_str}
    attrs_to_key = {selected_attrs}
    if not selected_objects:
        cmds.warning("No objects stored in the tool!")
        return

    for obj in selected_objects:
        for attr in attrs_to_key:
            cmds.setKeyframe(obj, attribute=attr)

    cmds.select(selected_objects, replace=True)
    print("Keyframes added and objects reselected!")

add_keyframes_to_objects()
"""
            tool_label = "Keyframe Tool"
            tool_annotation = "Tool to add keyframes to specific objects"

        current_shelf = mel.eval('tabLayout -q -selectTab $gShelfTopLevel')
        cmds.shelfButton(
            label=tool_label,
            command=script_content,
            annotation=tool_annotation,
            image="pythonFamily.png",
            sourceType="Python",
            parent=current_shelf
        )
        cmds.confirmDialog(title="Success", message=f"{tool_label} added to the shelf!", button=["OK"])

    # UI
    if cmds.window("KeyframeToolWin", exists=True):
        cmds.deleteUI("KeyframeToolWin")

    win = cmds.window("KeyframeToolWin", title="Keyframe Tool Setup", sizeable=True, width=300, height=180)
    layout = cmds.columnLayout(adjustableColumn=True, columnAlign="center", width=300)
    cmds.text(label="Select channels to keyframe:", align="center", height=30)
    move_cb = cmds.checkBox(label="Translate (move)", align="left", width=200)
    rotate_cb = cmds.checkBox(label="Rotate (rotate)", align="left", width=200)
    select_only_cb = cmds.checkBox(label="Set (Select Only)", align="left", width=200)

    cmds.separator(height=10)
    cmds.button(label="Confirm", height=40, command=confirm_selection)
    cmds.setParent(layout)
    cmds.showWindow(win)

# Run the main function
generate_keyframe_tool()
