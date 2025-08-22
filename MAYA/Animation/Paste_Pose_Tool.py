import maya.cmds as cmds
import json
import os

# Temp files
TEMP_FILE = os.path.join(cmds.internalVar(userAppDir=True), "keyframe_clipboard.json")
MAPPING_FILE = os.path.join(cmds.internalVar(userAppDir=True), "keyframe_mapping.json")

# Global toggle for auto namespace match
AUTO_MATCH = False

# Utility Functions

def strip_namespace(name):
    """Return object name without namespace/prefix"""
    if ":" in name:
        return name.split(":")[-1]
    return name

def load_mapping():
    """Load user-defined mapping table from JSON"""
    if os.path.exists(MAPPING_FILE):
        with open(MAPPING_FILE, "r") as f:
            return json.load(f)
    return {}

def save_mapping(mapping):
    """Save user-defined mapping table to JSON"""
    with open(MAPPING_FILE, "w") as f:
        json.dump(mapping, f, indent=2)

# Copy Paste

def copy_keyframes():
    """Copy selected objects' keyframes to clipboard JSON"""
    sel = cmds.ls(sl=True)
    if not sel:
        cmds.warning("No objects selected.")
        return

    data = {}
    for obj in sel:
        attrs = cmds.listAnimatable(obj) or []
        obj_data = {}
        for attr in attrs:
            times = cmds.keyframe(attr, query=True, timeChange=True)
            values = cmds.keyframe(attr, query=True, valueChange=True)
            in_tan = cmds.keyTangent(attr, query=True, inAngle=True)
            out_tan = cmds.keyTangent(attr, query=True, outAngle=True)

            if times:
                obj_data[attr.split('.')[-1]] = {
                    "times": times,
                    "values": values,
                    "inTan": in_tan,
                    "outTan": out_tan
                }
        if obj_data:
            data[obj] = obj_data

    with open(TEMP_FILE, "w") as f:
        json.dump(data, f, indent=2)

    cmds.inViewMessage(amg="Keyframes copied to clipboard", pos="midCenter", fade=True)
    print("Copied keyframes:", list(data.keys()))

def paste_keyframes():
    """Paste keyframes from clipboard JSON to current scene objects"""
    if not os.path.exists(TEMP_FILE):
        cmds.warning("No clipboard file found.")
        return

    with open(TEMP_FILE, "r") as f:
        data = json.load(f)

    scene_objs = cmds.ls(long=True)
    mapping = load_mapping()
    current_time = cmds.currentTime(query=True)

    for src_obj, attrs in data.items():
        target_obj = None

        # Manual mapping
        if src_obj in mapping and cmds.objExists(mapping[src_obj]):
            target_obj = mapping[src_obj]

        # Strict match
        if not target_obj and cmds.objExists(src_obj):
            target_obj = src_obj

        # Auto namespace match
        if not target_obj and AUTO_MATCH:
            src_short = strip_namespace(src_obj)
            matches = [o for o in scene_objs if strip_namespace(o) == src_short]
            if matches:
                target_obj = matches[0]

        if not target_obj:
            cmds.warning(f"Object {src_obj} not found, skipping...")
            continue

        for attr, info in attrs.items():
            full_attr = f"{target_obj}.{attr}"
            if not cmds.objExists(full_attr):
                cmds.warning(f"Attr {full_attr} missing, skipping...")
                continue

            # Paste at current time
            source_times = info["times"]
            if source_times:
                offset = current_time - source_times[0]  # Earliest frame aligns with current time
            else:
                offset = 0

            for t, v in zip(source_times, info["values"]):
                cmds.setKeyframe(full_attr, time=t + offset, value=v)

            # Apply tangents (rough implementation)
            try:
                cmds.keyTangent(full_attr, edit=True,
                                inAngle=info["inTan"], outAngle=info["outTan"])
            except:
                pass

    cmds.inViewMessage(amg="Keyframes pasted at current time", pos="midCenter", fade=True)
    print("Pasted keyframes for objects:", list(data.keys()))

# UI Functions

def set_auto_match(state):
    """Set global auto namespace match toggle"""
    global AUTO_MATCH
    AUTO_MATCH = state
    cmds.inViewMessage(amg=f"Auto Match set to {state}", pos="midCenter", fade=True)
    print("Auto Match:", state)

def save_mapping_ui(text_field):
    """Save mapping from text editor"""
    text = cmds.scrollField(text_field, query=True, text=True)
    try:
        mapping = json.loads(text)
        save_mapping(mapping)
        cmds.inViewMessage(amg="Mapping saved", pos="midCenter", fade=True)
        print("Mapping saved:", mapping)
    except Exception as e:
        cmds.warning(f"Invalid JSON: {e}")

def edit_mapping():
    """Open a simple text editor window to edit mapping dict"""
    if not os.path.exists(MAPPING_FILE):
        save_mapping({})

    with open(MAPPING_FILE, "r") as f:
        content = f.read()

    if cmds.window("MappingEditorWin", exists=True):
        cmds.deleteUI("MappingEditorWin")

    win = cmds.window("MappingEditorWin", title="Edit Mapping", widthHeight=(400, 300))
    form = cmds.formLayout()
    text_field = cmds.scrollField(editable=True, wordWrap=False, text=content)
    save_btn = cmds.button(label="Save", command=lambda x: save_mapping_ui(text_field))

    cmds.formLayout(form, edit=True,
                    attachForm=[(text_field, 'top', 5), (text_field, 'left', 5),
                                (text_field, 'right', 5), (save_btn, 'left', 5),
                                (save_btn, 'right', 5), (save_btn, 'bottom', 5)],
                    attachControl=[(text_field, 'bottom', 5, save_btn)])

    cmds.showWindow(win)
    cmds.refresh()

def show_ui():
    """Display main UI window"""
    global AUTO_MATCH
    if cmds.window("CopyPasteAnimWin", exists=True):
        cmds.deleteUI("CopyPasteAnimWin")

    win = cmds.window("CopyPasteAnimWin", title="Cross-Project Keyframes", widthHeight=(260, 140))
    cmds.columnLayout(adjustableColumn=True)
    cmds.button(label="Copy Keyframes", command=lambda x: copy_keyframes())
    cmds.button(label="Paste Keyframes", command=lambda x: paste_keyframes())
    cmds.checkBox("autoMatchCB", label="Auto Match Namespaces",
                  value=AUTO_MATCH,
                  onc=lambda x: set_auto_match(True),
                  ofc=lambda x: set_auto_match(False))
    cmds.button(label="Edit Mapping Table", command=lambda x: edit_mapping())
    cmds.showWindow(win)
    cmds.refresh()

show_ui()
