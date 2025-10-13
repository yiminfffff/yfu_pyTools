import maya.cmds as cmds

def create_ui():
    """Create the baking tool UI with improved spacing"""
    if cmds.window("bakeWin", exists=True):
        cmds.deleteUI("bakeWin")
    
    # Create main window with margins
    window = cmds.window("bakeWin", title="Force Baking Tool", width=420, height=320)
    main_layout = cmds.columnLayout(
        adjustableColumn=True, 
        rowSpacing=5,
        columnAttach=('both', 10)  # Add 10px padding on both sides
    )
    
    # Add vertical space at top
    cmds.separator(height=10, style="none")
    
    # Parent/Child selection section with left padding
    cmds.text(label="Select Objects", align="left", font="boldLabelFont")
    cmds.separator(height=5, style="in")
    
    # Parent selection row with left padding
    parent_layout = cmds.rowLayout(
        numberOfColumns=3,
        columnWidth3=(100, 200, 100),
        columnAlign=(1, "left"),
        columnAttach=[(1, "left", 5), (2, "both", 0), (3, "right", 5)]  # Add left padding
    )
    cmds.text(label="Parent")
    parent_field = cmds.textField("parentField", text="", editable=False)
    cmds.button(label="Select", command=lambda _: set_object_field("parentField"))
    cmds.setParent("..")
    
    # Add vertical space between rows
    cmds.separator(height=5, style="none")
    
    # Child selection row with left padding
    child_layout = cmds.rowLayout(
        numberOfColumns=3,
        columnWidth3=(100, 200, 100),
        columnAlign=(1, "left"),
        columnAttach=[(1, "left", 5), (2, "both", 0), (3, "right", 5)]  # Add left padding
    )
    cmds.text(label="Child")
    child_field = cmds.textField("childField", text="", editable=False)
    cmds.button(label="Select", command=lambda _: set_object_field("childField"))
    cmds.setParent("..")
    
    # Add vertical space between sections
    cmds.separator(height=15, style="none")
    
    # Frame range section with left padding
    cmds.text(label="Frame Range", align="left", font="boldLabelFont")
    cmds.separator(height=5, style="in")
    
    frame_layout = cmds.rowLayout(
        numberOfColumns=5,
        columnWidth5=(80, 70, 80, 70, 100),
        columnAlign=(1, "left"),
        columnAttach=[(1, "left", 5), (2, "both", 0), (3, "both", 0), (4, "both", 0), (5, "right", 5)]
    )
    cmds.text(label="Start Frame:")
    start_field = cmds.intField("startField", value=cmds.playbackOptions(q=True, min=True))
    cmds.text(label="End Frame:")
    end_field = cmds.intField("endField", value=cmds.playbackOptions(q=True, max=True))
    cmds.button(label="Get Range", command=lambda _: set_time_range(start_field, end_field))
    cmds.setParent("..")
    
    # Add vertical space before bake button
    cmds.separator(height=15, style="none")
    
	# Bake button
    bake_button = cmds.button(label="Bake", height=40, command=lambda _: bake_animation())

    # Add vertical space before status
    cmds.separator(height=10, style="none")
    
    
    status_field = cmds.textField("statusField", text="Ready", editable=False, bgc=(0.2, 0.2, 0.2))
    cmds.setParent("..")
    
    cmds.showWindow(window)

def set_object_field(field_name):
    """Set the object field with the currently selected object"""
    selection = cmds.ls(selection=True)
    if selection:
        cmds.textField(field_name, edit=True, text=selection[0])
        update_status("Selected: " + selection[0])
    else:
        update_status("Error: No object selected")

def set_time_range(start_field, end_field):
    """Set time range fields to current playback range"""
    start = cmds.playbackOptions(q=True, min=True)
    end = cmds.playbackOptions(q=True, max=True)
    cmds.intField(start_field, edit=True, value=start)
    cmds.intField(end_field, edit=True, value=end)
    update_status("Set frame range to {0}-{1}".format(int(start), int(end)))

def update_status(message):
    """Update the status field with a message"""
    cmds.textField("statusField", edit=True, text=message)

def bake_animation():
    """Bake animation from parent to child"""
    # Get values from UI
    parent_name = cmds.textField("parentField", query=True, text=True)
    child_name = cmds.textField("childField", query=True, text=True)
    start_frame = cmds.intField("startField", query=True, value=True)
    end_frame = cmds.intField("endField", query=True, value=True)
    
    # Validate inputs
    if not parent_name or not child_name:
        update_status("Error: Please select parent and child objects")
        return
    
    if not cmds.objExists(child_name):
        update_status("Error: Child object does not exist")
        return
    
    if not cmds.objExists(parent_name):
        update_status("Error: Parent object does not exist")
        return
    
    if start_frame >= end_frame:
        update_status("Error: Invalid frame range")
        return
    
    # Store current time
    current_time = cmds.currentTime(query=True)
    
    try:
        # Bake animation frame by frame
        for frame in range(int(start_frame), int(end_frame) + 1):
            cmds.currentTime(frame)
            
            # Get parent's world matrix
            parent_matrix = cmds.xform(parent_name, query=True, matrix=True, worldSpace=True)
            
            # Set child's world matrix to match parent
            cmds.xform(child_name, matrix=parent_matrix, worldSpace=True)
            
            # Set keyframes for all transform attributes
            cmds.setKeyframe(child_name, attribute=["translateX", "translateY", "translateZ"])
            cmds.setKeyframe(child_name, attribute=["rotateX", "rotateY", "rotateZ"])
            cmds.setKeyframe(child_name, attribute=["scaleX", "scaleY", "scaleZ"])
            
            # Update progress
            progress = int((frame - start_frame) / (end_frame - start_frame) * 100)
            update_status("Baking... {0}% (Frame {1})".format(progress, frame))
            cmds.refresh()
        
        update_status("Success! Baked {0} frames".format(end_frame - start_frame + 1))
        
    except Exception as e:
        update_status("Error: " + str(e))
        
    finally:
        # Restore original time
        cmds.currentTime(current_time)

# Create the UI
create_ui()