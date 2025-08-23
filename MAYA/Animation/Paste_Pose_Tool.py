import maya.cmds as cmds
import json
import os


def copy_selected_keyframes(*_):
    """Copy only selected keyframes and save them into memory"""
    selected_keys = cmds.keyframe(query=True, selected=True, name=True)
    if not selected_keys:
        cmds.warning("No keyframes selected.")
        return None

    data = {}
    for key in selected_keys:
        curve = key.split(".")[0]
        times = cmds.keyframe(key, query=True, selected=True, timeChange=True)
        values = cmds.keyframe(key, query=True, selected=True, valueChange=True)

        if curve not in data:
            data[curve] = []
        for t, v in zip(times, values):
            data[curve].append({"time": t, "value": v})

    # Save data into memory
    cmds.optionVar(stringValue=("lastCopiedKeys", json.dumps(data)))
    print("Copied selected keyframes.")
    return data


def paste_keyframes_at_current_time(data=None, *_):
    """Paste copied keyframes at the current time, keeping relative spacing"""
    # If called from button, data may be a bool, ignore it
    if not isinstance(data, dict):
        if not cmds.optionVar(exists="lastCopiedKeys"):
            cmds.warning("No copied keyframe data found.")
            return
        data = json.loads(cmds.optionVar(query="lastCopiedKeys"))

    current_time = cmds.currentTime(query=True)
    earliest_time = min(t["time"] for keys in data.values() for t in keys)

    for curve, keys in data.items():
        for entry in keys:
            offset = entry["time"] - earliest_time
            new_time = current_time + offset
            cmds.setKeyframe(curve, time=new_time, value=entry["value"])

    print("Pasted keyframes at current time.")


def write_pose_file(*_):
    """Copy currently selected keyframes and write them to a JSON file"""
    data = copy_selected_keyframes()
    if not data:
        return

    filepath = cmds.fileDialog2(fileMode=0, caption="Save Pose File")  # 0 = save file
    if filepath:
        with open(filepath[0], "w") as f:
            json.dump(data, f, indent=2)
        print("Pose file written:", filepath[0])


def read_pose_file(*_):
    """Open a file dialog to choose file and read/paste keyframes"""
    filepath = cmds.fileDialog2(fileMode=1, caption="Open Pose File")  # 1 = open file
    if not filepath:
        return

    with open(filepath[0], "r") as f:
        data = json.load(f)
    paste_keyframes_at_current_time(data)


def show_ui():
    """Create UI with two button groups (Copy/Paste and Write/Read) with spacing"""
    if cmds.window("keyframeCopyPasteUI", exists=True):
        cmds.deleteUI("keyframeCopyPasteUI")

    window = cmds.window("keyframeCopyPasteUI", title="Paste Pose Tool", widthHeight=(200, 150))
    layout = cmds.columnLayout(adjustableColumn=True, columnAlign="center")

    # Copy Paste
    cmds.button(label="Copy", command=copy_selected_keyframes)
    cmds.button(label="Paste", command=paste_keyframes_at_current_time)

    cmds.text(label="", height=10)

    # Read Write 
    cmds.button(label="Write", command=write_pose_file)
    cmds.button(label="Read", command=read_pose_file)

    cmds.showWindow(window)


show_ui()
