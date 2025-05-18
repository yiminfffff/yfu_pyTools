import maya.cmds as cmds

def mirror_curve(axis='x', copy=True):
    # Get the list of selected transform objects (assumed to be curves)
    selected = cmds.ls(selection=True, type="transform")
    if not selected:
        print("No curves selected.")
        return
    
    # Apply freeze transformations to all selected curves
    for obj in selected:
        cmds.makeIdentity(obj, apply=True, translate=True, rotate=True, scale=True, normal=False)
        print(f"Applied freeze transformations to: {obj}")

    # Determine the axis index (x, y, or z) for mirroring based on the given axis
    axis_index = {'x': 0, 'y': 1, 'z': 2}.get(axis.lower(), 0)
    scale_values = [1, 1, 1]  # Initialize scale factors for each axis
    scale_values[axis_index] = -1  # Set the specified axis to -1 for mirroring

    # Process each selected curve
    for obj in selected:
        if copy:
            # Duplicate the selected curve
            duplicated_curve = cmds.duplicate(obj)[0]
            # Scale the duplicated curve for mirroring
            cmds.scale(*scale_values, duplicated_curve, pivot=(0, 0, 0))
            
            # Rename the duplicated curve, replacing "_left" with "_right" if applicable
            new_name = obj.replace('_left', '_right')
            if new_name == obj:  # If "_left" is not in the name, append "_mirrored"
                new_name += "_mirrored"
            duplicated_curve = cmds.rename(duplicated_curve, new_name)

            # Center the pivot and freeze transformations for the mirrored curve
            cmds.xform(duplicated_curve, centerPivots=True)
            cmds.makeIdentity(duplicated_curve, apply=True, translate=True, rotate=True, scale=True, normal=False)

            print(f"Created mirrored curve: {duplicated_curve}")
        else:
            # Modify the original curve by applying mirroring
            cmds.scale(*scale_values, obj, pivot=(0, 0, 0))
            print(f"Modified original curve: {obj}")

# Example usage:
# mirror_curve(axis='x', copy=True)  # Mirror curves along the X-axis and create duplicates
mirror_curve(axis='x', copy=True)  # Mirror curves along the X-axis with duplicates
