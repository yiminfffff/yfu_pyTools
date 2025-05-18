import maya.cmds as cmds

def group_selected_objects_and_freeze():
    # Get a list of selected transform objects in the Maya scene
    selected_objects = cmds.ls(selection=True, type="transform")
    if not selected_objects:
        print("No objects selected.")
        return
    
    for obj in selected_objects:
        # Define the group name by appending "_grp" to the object name
        group_name = f"{obj}_grp"
        
        # Check if a group with the same name already exists
        if cmds.objExists(group_name):
            print(f"Group '{group_name}' already exists. Skipping.")
            continue
        
        # Create an empty group with the specified name
        group = cmds.group(empty=True, name=group_name)
        print(f"Created group: {group}")

        # Align the group's position, rotation, and scale to match the object
        cmds.matchTransform(group, obj)
        
        # Parent the object to the newly created group
        cmds.parent(obj, group)
        print(f"Parented '{obj}' to '{group}'.")

        # Apply Freeze Transform to the object to reset its transformations
        cmds.makeIdentity(obj, apply=True, translate=True, rotate=True, scale=True, normal=False)
        print(f"Applied Freeze Transform to '{obj}'.")

# Example usage:
group_selected_objects_and_freeze()
