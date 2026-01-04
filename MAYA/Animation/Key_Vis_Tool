import maya.cmds as cmds

def set_visibility_keyframes():
    """
    Set visibility keyframes for selected objects
    Current frame: visibility=1 (visible)
    Previous frame: visibility=0 (invisible)
    """
    
    # Get currently selected objects
    selected_objects = cmds.ls(selection=True)
    
    if not selected_objects:
        cmds.warning("Please select one or more objects first")
        return
    
    # Get current time
    current_time = cmds.currentTime(query=True)
    
    # Calculate previous frame time
    previous_time = current_time - 1
    
    # Loop through all selected objects
    for obj in selected_objects:
        # Check if object has visibility attribute
        if not cmds.attributeQuery('visibility', node=obj, exists=True):
            cmds.warning("Object " + obj + " does not have visibility attribute, skipping")
            continue
        
        # Set visibility=0 at previous frame
        cmds.currentTime(previous_time)
        cmds.setAttr(obj + ".visibility", 0)
        cmds.setKeyframe(obj + ".visibility")
        
        # Set visibility=1 at current frame
        cmds.currentTime(current_time)
        cmds.setAttr(obj + ".visibility", 1)
        cmds.setKeyframe(obj + ".visibility")
        
        print("Visibility keyframes set for " + obj + ": frame " + str(previous_time) + "=0, frame " + str(current_time) + "=1")
    
    # Return to current time
    cmds.currentTime(current_time)

# Execute the function
set_visibility_keyframes()
