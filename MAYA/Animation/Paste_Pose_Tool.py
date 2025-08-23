import maya.cmds as cmds
import json
import os

# Path to store copied keyframes
KEYFRAME_FILE = os.path.join(cmds.internalVar(userAppDir=True), "copied_keys.json")

def copy_selected_keyframes():
    """Copy only selected keyframes and save them into a json file"""
    # Get selected keyframes
    selected_keys = cmds.keyframe(query=True, selected=True, name=True)
    if not selected_keys:
        cmds.warning("No keyframes selected.")
        return

    data = {}
    for key in selected_keys:
        # Get curve and frame info
        curve = key.split(".")[0]
        times = cmds.keyframe(key, query=True, selected=True, timeChange=True)
        values = cmds.keyframe(key, query=True, selected=True, valueChange=True)

        if curve not in data:
            data[curve] = []
        for t, v in zip(times, values):
            data[curve].append({"time": t, "value": v})

    with open(KEYFRAME_FILE, "w") as f:
        json.dump(data, f, indent=2)

    print("Copied selected keyframes.")


def paste_keyframes_at_current_time():
    """Paste copied keyframes at the current time, keeping relative spacing"""
    if not os.path.exists(KEYFRAME_FILE):
        cmds.warning("No copied keyframe data found.")
        return

    with open(KEYFRAME_FILE, "r") as f:
        data = json.load(f)

    current_time = cmds.currentTime(query=True)

    # Find earliest keyframe time in copied data
    earliest_time = min(
        t["time"] for keys in data.values() for t in keys
    )

    for curve, keys in data.items():
        for entry in keys:
            offset = entry["time"] - earliest_time
            new_time = current_time + offset
            cmds.setKeyframe(curve, time=new_time, value=entry["value"])

    print("Pasted keyframes at current time.")


def show_ui():
    """Create a small UI with Copy and Paste buttons"""
    if cmds.window("keyframeCopyPasteUI", exists=True):
        cmds.deleteUI("keyframeCopyPasteUI")

    window = cmds.window("keyframeCopyPasteUI", title="Paste Pose Tool", widthHeight=(200, 80))
    layout = cmds.columnLayout(adjustableColumn=True, columnAlign="center")

    cmds.button(label="Copy", command=lambda x: copy_selected_keyframes())
    cmds.button(label="Paste", command=lambda x: paste_keyframes_at_current_time())

    cmds.showWindow(window)

show_ui()
