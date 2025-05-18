import maya.cmds as cmds

def constrain_skin_to_rig():
    # Get all joints containing '_rig_' and '_skin_'
    rig_joints = sorted(cmds.ls("*_rig_*", type="joint"))
    skin_joints = sorted(cmds.ls("*_skin_*", type="joint"))
    
    # Ensure the number of joints match
    if len(rig_joints) != len(skin_joints):
        cmds.warning("Mismatch in number of rig and skin joints.")
        return
    
    # Use zip to create constraints
    for rig, skin in zip(rig_joints, skin_joints):
        cmds.parentConstraint(rig, skin, mo=True)
        print(f"Constraint created: {rig} -> {skin}")

# Run the script
constrain_skin_to_rig()
