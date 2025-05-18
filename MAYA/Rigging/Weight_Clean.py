import maya.cmds as cmds

def update_constraint_weights():
    selection = cmds.ls(selection=True, long=True)  # Get the selected objects with full path names
    if not selection:
        cmds.warning("No objects selected.")
        return
    
    all_constraints = []
    for obj in selection:
        children = cmds.listRelatives(obj, children=True, fullPath=True) or []  # Get all child nodes
        for child in children:
            if "parentConstraint" in child:  # Check if the child is a parentConstraint node
                all_constraints.append(child)

    if not all_constraints:
        print("No parentConstraint nodes found.")
        return

    for constraint in all_constraints:
        attrs = cmds.listAttr(constraint, string="*W0") or []  # Find attributes matching "*W0"
        for attr in attrs:
            full_attr = f"{constraint}.{attr}"
            try:
                cmds.setAttr(full_attr, 0)  # Set the weight attribute to 0
                print(f"Set {full_attr} to 0")
            except Exception as e:
                print(f"Failed to set {full_attr}: {e}")

update_constraint_weights()
