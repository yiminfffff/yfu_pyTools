import maya.cmds as cmds

def toggle_local_rotation_axis():
    # Get the list of selected transform objects in the Maya scene
    selected_objects = cmds.ls(selection=True, type="transform")
    if not selected_objects:
        print("No objects selected.")
        return

    all_objects = []  # Initialize a list to store all transform objects
    for obj in selected_objects:
        # Get all descendants of the current object that are of type "transform"
        descendants = cmds.listRelatives(obj, allDescendents=True, type="transform") or []
        all_objects.append(obj)  # Add the current object to the list
        all_objects.extend(descendants)  # Add the descendants to the list

    # Remove duplicates and sort the list of objects
    all_objects = list(set(all_objects))
    all_objects.sort()

    for obj in all_objects:
        # Get the full name of the object in the scene
        obj_name = cmds.ls(obj)[0]

        # Check if the object has the "displayLocalAxis" attribute
        if cmds.attributeQuery("displayLocalAxis", node=obj_name, exists=True):
            # Toggle the current state of the "displayLocalAxis" attribute
            current_state = cmds.getAttr(f"{obj_name}.displayLocalAxis")
            cmds.setAttr(f"{obj_name}.displayLocalAxis", not current_state)
            print(f"Toggled Local Rotation Axis for: {obj_name}")
        else:
            # Skip objects without the "displayLocalAxis" attribute
            print(f"Skipped: {obj_name} (no 'displayLocalAxis' attribute)")

toggle_local_rotation_axis()
