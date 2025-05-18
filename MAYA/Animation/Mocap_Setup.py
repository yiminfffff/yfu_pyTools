import maya.cmds as cmds

def get_all_descendants(joint):
    descendants = cmds.listRelatives(joint, allDescendents=True, type="joint", fullPath=True) or []
    descendants.append(joint)
    return list(set(descendants))  # Prevent duplication

def move_animation_to_1001(joints):
    current_frame = int(cmds.currentTime(q=True))
    offset = 1001 - current_frame
    cmds.select(joints, replace=True)
    
    # Get keyframe time range
    min_time = cmds.findKeyframe(joints, which="first")
    max_time = cmds.findKeyframe(joints, which="last")

    if min_time is not None and max_time is not None:
        cmds.keyframe(joints, edit=True, time=(min_time, max_time), relative=True, timeChange=offset)

def delete_before_1001(joints):
    cmds.cutKey(joints, time=(1, 1000), option="keys")

def insert_zero_rotation_key_at_990(joints):
    for jnt in joints:
        for attr in ["rotateX", "rotateY", "rotateZ"]:
            cmds.setKeyframe(jnt, time=990, attribute=attr, value=0)

def run_motion_prep():
    selection = cmds.ls(selection=True, type="joint")
    if not selection:
        cmds.warning("Please select the root joint.")
        return
    
    root_joint = selection[0]
    all_joints = get_all_descendants(root_joint)

    move_animation_to_1001(all_joints)
    delete_before_1001(all_joints)
    insert_zero_rotation_key_at_990(all_joints)

    cmds.select(all_joints)
    cmds.currentTime(990)  # Move the playback head to frame 990
    cmds.playbackOptions(min=1001)  # Set playback range start to 1001
    print("Mocap prep complete: animation moved, early keys deleted, 990f zero rotation added.")

run_motion_prep()
