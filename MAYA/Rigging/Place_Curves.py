import maya.cmds as cmds

def create_circle_match_transform():
    # 1. Get the currently selected objects
    selection = cmds.ls(selection=True, type='transform')
    
    # If nothing is selected, warn the user and exit the function
    if not selection:
        cmds.warning("Please select at least one bone or object!")
        return
        
    # Loop through each selected target
    for target in selection:
        # 2. Generate controller name by replacing '_jnt_' with '_ctrl_'
        # Fallback to appending '_ctrl' if '_jnt_' is not found in the name
        if "_jnt_" in target:
            circle_name = target.replace("_jnt_", "_ctrl_")
        else:
            circle_name = "{}_ctrl".format(target)
            
        # 3. Create a NURBS Circle with Normal Axes set to X (normal=(1,0,0))
        circle = cmds.circle(
            name=circle_name,
            normal=(1, 0, 0),  # Set the normal axis to X
            center=(0, 0, 0),
            radius=10,  # UPDATED: Changed radius from 1 to 10 for larger controller size
            degree=3,
            sections=8,
            constructionHistory=False  # Disable history to prevent rigging issues later
        )[0]
        
        # 4. Match Transform (Align Translation, Rotation, and Scale)
        # Select the controller first, then the target bone, and execute the match
        cmds.select(circle, target)
        cmds.matchTransform(pos=True, rot=True, scl=True)
        
        # 5. Freeze Transformations (Standard rigging practice)
        # Resets the transform attributes to zero after matching
        cmds.makeIdentity(circle, apply=True, translate=True, rotate=True, scale=True)
        
        # 6. Keep the controller at world root level (NOT parented under the joint)
        # This makes it easier to find and manage in the Outliner
        # If you need to drive the joint later, use a Parent Constraint instead:
        # cmds.parentConstraint(circle, target, maintainOffset=True)
        
        # Print a success message to the Script Editor
        print("Successfully created and matched controller: {}".format(circle))

# Execute the function
create_circle_match_transform()