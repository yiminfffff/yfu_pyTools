"""Better Set Driven Key for Autodesk Maya.

Run in Maya's Script Editor:

    import muscle_definition_tool
    muscle_definition_tool.show()

All persistent data lives in Maya network nodes and is saved with the scene.
"""

from __future__ import annotations

import json
import re
import uuid
from contextlib import contextmanager

import maya.cmds as cmds
import maya.OpenMayaUI as omui

try:
    from PySide6 import QtCore, QtGui, QtWidgets
    from shiboken6 import wrapInstance
    PYSIDE6 = True
except ImportError:
    from PySide2 import QtCore, QtGui, QtWidgets
    from shiboken2 import wrapInstance
    PYSIDE6 = False


if PYSIDE6:
    USER_ROLE = QtCore.Qt.ItemDataRole.UserRole
    SCROLL_AS_NEEDED = QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded
    SCROLL_ALWAYS_OFF = QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    TOOL_BUTTON_TEXT_ONLY = QtCore.Qt.ToolButtonStyle.ToolButtonTextOnly
    MOVE_ACTION = QtCore.Qt.DropAction.MoveAction
    DELETE_ON_CLOSE = QtCore.Qt.WidgetAttribute.WA_DeleteOnClose
    ITEM_EDITABLE = QtCore.Qt.ItemFlag.ItemIsEditable
    ITEM_DRAGGABLE = QtCore.Qt.ItemFlag.ItemIsDragEnabled
    EXTENDED_SELECTION = QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection
    SINGLE_SELECTION = QtWidgets.QAbstractItemView.SelectionMode.SingleSelection
    INTERNAL_MOVE = QtWidgets.QAbstractItemView.DragDropMode.InternalMove
else:
    USER_ROLE = QtCore.Qt.UserRole
    SCROLL_AS_NEEDED = QtCore.Qt.ScrollBarAsNeeded
    SCROLL_ALWAYS_OFF = QtCore.Qt.ScrollBarAlwaysOff
    TOOL_BUTTON_TEXT_ONLY = QtCore.Qt.ToolButtonTextOnly
    MOVE_ACTION = QtCore.Qt.MoveAction
    DELETE_ON_CLOSE = QtCore.Qt.WA_DeleteOnClose
    ITEM_EDITABLE = QtCore.Qt.ItemIsEditable
    ITEM_DRAGGABLE = QtCore.Qt.ItemIsDragEnabled
    EXTENDED_SELECTION = QtWidgets.QAbstractItemView.ExtendedSelection
    SINGLE_SELECTION = QtWidgets.QAbstractItemView.SingleSelection
    INTERNAL_MOVE = QtWidgets.QAbstractItemView.InternalMove


TOOL_TITLE = "Better Set Driven Key"
ROOT_NODE = "MDT_SCENE_DATA"
SCHEMA_VERSION = 1
WINDOW_OBJECT = "muscleDefinitionToolWindow"

ATTR_ROOT_DEFINITIONS = "mdtDefinitions"
ATTR_SCHEMA = "mdtSchemaVersion"
ATTR_MARKER = "mdtMuscleDefinition"
ATTR_NAME = "mdtDisplayName"
ATTR_DRIVERS = "mdtDriverObjects"
ATTR_DRIVEN = "mdtDrivenObjects"
ATTR_CONTROLLERS = "mdtControllerObjects"
ATTR_DRIVER_CHANNELS = "mdtSelectedDriverChannels"
ATTR_DRIVEN_CHANNELS = "mdtSelectedDrivenChannels"
ATTR_EDIT_POINTS = "mdtEditPoints"
ATTR_AUTO_MIRROR = "mdtAutoMirror"
ATTR_HIDE_VISIBILITY = "mdtHideVisibility"
ATTR_HIDE_SCALE = "mdtHideScale"
ATTR_HIDE_RADIUS = "mdtHideRadius"

NUMERIC_TYPES = {
    "bool", "byte", "char", "short", "long", "float", "double",
    "doubleAngle", "doubleLinear", "time", "enum",
}

STYLE = """
QWidget {
    background: #2b2b2b;
    color: #dddddd;
    font-size: 12px;
}
QFrame#panel {
    background: #333333;
    border: 1px solid #4b4b4b;
    border-radius: 5px;
}
QLabel#panelTitle {
    color: #f0f0f0;
    font-weight: 600;
    padding: 2px;
}
QListWidget {
    background: #242424;
    border: 1px solid #484848;
    border-radius: 3px;
    outline: 0;
    padding: 2px;
}
QListWidget::item { padding: 4px; }
QListWidget::item:selected { background: #ffd84a; color: #111111; }
QPushButton, QToolButton {
    background: #444444;
    border: 1px solid #5d5d5d;
    border-radius: 4px;
    padding: 7px;
}
QPushButton:hover, QToolButton:hover { background: #505050; }
QPushButton:pressed, QToolButton:pressed { background: #3a657c; }
QPushButton#createButton {
    background: #a65a32;
    border: 1px solid #ce7748;
    font-size: 14px;
    font-weight: 600;
    padding: 11px;
}
QPushButton#createButton:hover { background: #b8693e; }
QPushButton#keyButton {
    background: #a65a32;
    border: 1px solid #ce7748;
    font-weight: 700;
    font-size: 15px;
}
QPushButton#keyButton:hover { background: #b8693e; }
QPushButton#poseNavButton:pressed,
QPushButton#poseNavButton:focus {
    background: #ffd84a;
    color: #111111;
    border: 1px solid #ffe77c;
}
QToolButton#definitionCard {
    background: #383838;
    border: 1px solid #5a5a5a;
    border-radius: 7px;
    font-size: 13px;
    font-weight: 600;
    padding: 10px;
}
QToolButton#definitionCard:hover {
    background: #a65a32;
    border: 1px solid #ce7748;
}
QScrollArea { border: 0; background: #242424; }
QScrollBar:horizontal { height: 11px; background: #242424; }
QScrollBar::handle:horizontal { background: #555555; border-radius: 5px; min-width: 24px; }
"""


def _maya_main_window():
    pointer = omui.MQtUtil.mainWindow()
    return wrapInstance(int(pointer), QtWidgets.QWidget) if pointer else None


def _short_name(node):
    return node.rsplit("|", 1)[-1]


def _safe_get_string(node, attr, default=""):
    try:
        return cmds.getAttr("{}.{}".format(node, attr)) or default
    except Exception:
        return default


def _set_string(node, attr, value):
    cmds.setAttr("{}.{}".format(node, attr), value or "", type="string")


def _ensure_attr(node, name, attribute_type=None, data_type=None, multi=False):
    if cmds.attributeQuery(name, node=node, exists=True):
        return
    kwargs = {"longName": name}
    if attribute_type:
        kwargs["attributeType"] = attribute_type
    if data_type:
        kwargs["dataType"] = data_type
    if multi:
        kwargs["multi"] = True
    cmds.addAttr(node, **kwargs)


def ensure_root():
    if cmds.objExists(ROOT_NODE) and cmds.nodeType(ROOT_NODE) == "network":
        root = ROOT_NODE
    else:
        root = cmds.createNode("network", name=ROOT_NODE)
    _ensure_attr(root, ATTR_SCHEMA, attribute_type="long")
    _ensure_attr(root, ATTR_ROOT_DEFINITIONS, attribute_type="message", multi=True)
    cmds.setAttr("{}.{}".format(root, ATTR_SCHEMA), SCHEMA_VERSION)
    return root


def _next_free_index(node, attr):
    used = cmds.getAttr("{}.{}".format(node, attr), multiIndices=True) or []
    index = 0
    used = set(used)
    while index in used:
        index += 1
    return index


def _connect_message(source, target_node, target_attr, index=None):
    if index is None:
        index = _next_free_index(target_node, target_attr)
    cmds.connectAttr(
        "{}.message".format(source),
        "{}.{}[{}]".format(target_node, target_attr, index),
        force=True,
    )
    return index


def _connected_at(node, attr):
    result = []
    indices = cmds.getAttr("{}.{}".format(node, attr), multiIndices=True) or []
    for index in sorted(indices):
        plug = "{}.{}[{}]".format(node, attr, index)
        connected = cmds.listConnections(plug, source=True, destination=False) or []
        if not connected:
            continue
        long_names = cmds.ls(connected[0], long=True) or connected
        result.append((index, long_names[0]))
    return result


def definition_nodes():
    if not cmds.objExists(ROOT_NODE):
        return []
    found = []
    for _, node in _connected_at(ROOT_NODE, ATTR_ROOT_DEFINITIONS):
        if cmds.objExists(node) and cmds.attributeQuery(ATTR_MARKER, node=node, exists=True):
            found.append(node)
    return found


def _unique_node_name(display_name):
    stem = re.sub(r"[^A-Za-z0-9_]+", "_", display_name).strip("_") or "Muscle"
    return "MDT_{}_DEF#".format(stem)


def create_definition(display_name, drivers, driven, controllers):
    root = ensure_root()
    node = cmds.createNode("network", name=_unique_node_name(display_name))
    _ensure_attr(node, ATTR_MARKER, attribute_type="bool")
    _ensure_attr(node, ATTR_NAME, data_type="string")
    _ensure_attr(node, ATTR_DRIVERS, attribute_type="message", multi=True)
    _ensure_attr(node, ATTR_DRIVEN, attribute_type="message", multi=True)
    _ensure_attr(node, ATTR_CONTROLLERS, attribute_type="message", multi=True)
    _ensure_attr(node, ATTR_DRIVER_CHANNELS, data_type="string")
    _ensure_attr(node, ATTR_DRIVEN_CHANNELS, data_type="string")
    _ensure_attr(node, ATTR_EDIT_POINTS, data_type="string")
    _ensure_attr(node, ATTR_AUTO_MIRROR, attribute_type="bool")
    _ensure_attr(node, ATTR_HIDE_VISIBILITY, attribute_type="bool")
    _ensure_attr(node, ATTR_HIDE_SCALE, attribute_type="bool")
    _ensure_attr(node, ATTR_HIDE_RADIUS, attribute_type="bool")
    cmds.setAttr("{}.{}".format(node, ATTR_MARKER), True)
    _set_string(node, ATTR_NAME, display_name)
    _set_string(node, ATTR_DRIVER_CHANNELS, "[]")
    _set_string(node, ATTR_DRIVEN_CHANNELS, "[]")
    _set_string(node, ATTR_EDIT_POINTS, "[]")
    cmds.setAttr("{}.{}".format(node, ATTR_AUTO_MIRROR), False)
    cmds.setAttr("{}.{}".format(node, ATTR_HIDE_VISIBILITY), True)
    cmds.setAttr("{}.{}".format(node, ATTR_HIDE_SCALE), True)
    cmds.setAttr("{}.{}".format(node, ATTR_HIDE_RADIUS), True)
    _connect_message(node, root, ATTR_ROOT_DEFINITIONS)
    for index, obj in enumerate(drivers):
        _connect_message(obj, node, ATTR_DRIVERS, index)
    for index, obj in enumerate(driven):
        _connect_message(obj, node, ATTR_DRIVEN, index)
    for index, obj in enumerate(controllers):
        _connect_message(obj, node, ATTR_CONTROLLERS, index)
    return node


def definition_objects(node, attr):
    return [obj for _, obj in _connected_at(node, attr)]


def _json_attr(node, attr, default):
    raw = _safe_get_string(node, attr, "")
    if not raw:
        return default
    try:
        value = json.loads(raw)
        return value
    except (TypeError, ValueError):
        return default


def _set_json_attr(node, attr, value):
    _set_string(node, attr, json.dumps(value, ensure_ascii=False, separators=(",", ":")))


def edit_points(node):
    value = _json_attr(node, ATTR_EDIT_POINTS, [])
    return value if isinstance(value, list) else []


def set_edit_points(node, points):
    _set_json_attr(node, ATTR_EDIT_POINTS, points)


def selected_channels(node, attr):
    value = _json_attr(node, attr, [])
    return value if isinstance(value, list) else []


def set_selected_channels(node, attr, plugs):
    _set_json_attr(node, attr, plugs)


def available_channels(objects):
    channels = []
    seen = set()
    for obj in objects:
        attrs = (cmds.listAttr(obj, keyable=True, scalar=True) or [])
        attrs += (cmds.listAttr(obj, channelBox=True, scalar=True) or [])
        for attr in attrs:
            plug = "{}.{}".format(obj, attr)
            if plug in seen or not cmds.objExists(plug):
                continue
            seen.add(plug)
            try:
                if cmds.getAttr(plug, lock=True):
                    continue
                attr_type = cmds.getAttr(plug, type=True)
                value = cmds.getAttr(plug)
            except Exception:
                continue
            if attr_type not in NUMERIC_TYPES or isinstance(value, (list, tuple)):
                continue
            channels.append(plug)
    return channels


def normalize_selection(selection, controller=False):
    normalized = []
    seen = set()
    for node in selection:
        if not cmds.objExists(node):
            continue
        current = (cmds.ls(node, long=True) or [node])[0]
        if controller and cmds.objectType(current, isAType="shape"):
            parents = cmds.listRelatives(current, parent=True, fullPath=True) or []
            if parents:
                current = parents[0]
        if current not in seen:
            normalized.append(current)
            seen.add(current)
    return normalized


def selection_with_descendants(selection, controller=False):
    """Return current roots and every transform descendant in hierarchy order."""
    roots = normalize_selection(selection, controller=controller)
    result = []
    seen = set()

    def visit(node):
        if node in seen:
            return
        seen.add(node)
        result.append(node)
        children = cmds.listRelatives(node, children=True, fullPath=True) or []
        for child in children:
            try:
                is_transform = cmds.objectType(child, isAType="transform")
            except Exception:
                is_transform = False
            if is_transform:
                visit(child)

    for root in roots:
        visit(root)
    return result


def ensure_definition_options(node):
    defaults = (
        (ATTR_AUTO_MIRROR, False),
        (ATTR_HIDE_VISIBILITY, True),
        (ATTR_HIDE_SCALE, True),
        (ATTR_HIDE_RADIUS, True),
    )
    for attr, default in defaults:
        if not cmds.attributeQuery(attr, node=node, exists=True):
            _ensure_attr(node, attr, attribute_type="bool")
            cmds.setAttr("{}.{}".format(node, attr), default)


def definition_option(node, attr, default=False):
    ensure_definition_options(node)
    try:
        return bool(cmds.getAttr("{}.{}".format(node, attr)))
    except Exception:
        return default


def set_definition_option(node, attr, value):
    ensure_definition_options(node)
    cmds.setAttr("{}.{}".format(node, attr), bool(value))


def _mirror_leaf_name(leaf):
    """Find an opposite-side candidate without replacing letters inside words."""
    replacements = (
        (r"(?i)(^|[_\.\-])left(?=$|[_\.\-0-9])", "right"),
        (r"(?i)(^|[_\.\-])right(?=$|[_\.\-0-9])", "left"),
        (r"(?i)(^|[_\.\-])l(?=$|[_\.\-0-9])", "r"),
        (r"(?i)(^|[_\.\-])r(?=$|[_\.\-0-9])", "l"),
        (r"(?i)(^|[_\.\-])lf(?=$|[_\.\-0-9])", "rt"),
        (r"(?i)(^|[_\.\-])rt(?=$|[_\.\-0-9])", "lf"),
    )
    namespace, separator, bare = leaf.rpartition(":")
    prefix = namespace + separator if separator else ""
    for pattern, replacement in replacements:
        match = re.search(pattern, bare)
        if not match:
            continue
        lead = match.group(1)
        source_token = match.group(0)[len(lead):]
        if source_token.isupper():
            token = replacement.upper()
        elif source_token[:1].isupper():
            token = replacement.capitalize()
        else:
            token = replacement
        mirrored = bare[:match.start()] + lead + token + bare[match.end():]
        return prefix + mirrored
    camel_pairs = (("Left", "Right"), ("Right", "Left"), ("left", "right"), ("right", "left"))
    for source, target in camel_pairs:
        if source in bare:
            return prefix + bare.replace(source, target, 1)
    return ""


def mirror_node(node):
    leaf = _short_name(node)
    candidate = _mirror_leaf_name(leaf)
    if not candidate:
        return ""
    matches = cmds.ls(candidate, long=True) or []
    if len(matches) == 1:
        return matches[0]
    if matches:
        source_parent = node.rsplit("|", 1)[0] if "|" in node else ""
        for match in matches:
            if match.rsplit("|", 1)[0] == source_parent:
                return match
        return matches[0]
    return ""


def mirror_plug(plug):
    if "." not in plug:
        return ""
    node, attr = plug.rsplit(".", 1)
    mirrored_node = mirror_node(node)
    mirrored_plug = "{}.{}".format(mirrored_node, attr) if mirrored_node else ""
    return mirrored_plug if mirrored_plug and cmds.objExists(mirrored_plug) else ""


def _side_token(name):
    low = name.lower().replace(":", "_")
    tokens = [token for token in re.split(r"[^a-z0-9]+", low) if token]
    if low.startswith("left") or "left" in tokens or "lf" in tokens or "lft" in tokens or "l" in tokens:
        return "Left"
    if low.startswith("right") or "right" in tokens or "rt" in tokens or "rgt" in tokens or "r" in tokens:
        return "Right"
    if re.search(r"(^|[_\.\-])l($|[_\.\-0-9])", low):
        return "Left"
    if re.search(r"(^|[_\.\-])r($|[_\.\-0-9])", low):
        return "Right"
    return ""


ANATOMY_TOKENS = [
    ("shoulder", "Shoulder"), ("clavicle", "Shoulder"),
    ("elbow", "Elbow"), ("wrist", "Wrist"), ("hand", "Hand"),
    ("hip", "Hip"), ("thigh", "Thigh"), ("knee", "Knee"),
    ("ankle", "Ankle"), ("foot", "Foot"), ("toe", "Toe"),
    ("spine", "Spine"), ("chest", "Chest"), ("neck", "Neck"),
    ("jaw", "Jaw"), ("bicep", "Bicep"), ("tricep", "Tricep"),
    ("calf", "Calf"), ("glute", "Glute"),
]


def suggest_definition_name(drivers, driven):
    names = [_short_name(node) for node in list(driven) + list(drivers)]
    detected_part = ""
    detected_side = ""
    for name in names:
        low = name.lower()
        if not detected_side:
            detected_side = _side_token(name)
        if not detected_part:
            for token, label in ANATOMY_TOKENS:
                if token in low:
                    detected_part = label
                    break
        if detected_part and detected_side:
            break
    if not detected_part:
        return ""
    base = "{}_{}".format(detected_side, detected_part) if detected_side else detected_part
    existing = {_safe_get_string(node, ATTR_NAME) for node in definition_nodes()}
    if base not in existing:
        return base
    number = 2
    while "{}_{:02d}".format(base, number) in existing:
        number += 1
    return "{}_{:02d}".format(base, number)


def _plug_value(plug):
    try:
        value = cmds.getAttr(plug)
        if isinstance(value, (int, float, bool)):
            return value
    except Exception:
        pass
    return None


def capture_controllers(definition_node):
    snapshots = []
    for logical_index, node in _connected_at(definition_node, ATTR_CONTROLLERS):
        values = {}
        for attr in ("tx", "ty", "tz", "rx", "ry", "rz"):
            plug = "{}.{}".format(node, attr)
            if cmds.objExists(plug):
                value = _plug_value(plug)
                if value is not None:
                    values[attr] = value
        snapshots.append({"index": logical_index, "name": _short_name(node), "values": values})
    return snapshots


@contextmanager
def undo_chunk(name):
    cmds.undoInfo(openChunk=True, chunkName=name)
    try:
        yield
    finally:
        cmds.undoInfo(closeChunk=True)


def _set_if_possible(plug, value):
    if not cmds.objExists(plug):
        return False
    try:
        if cmds.getAttr(plug, lock=True) or not cmds.getAttr(plug, settable=True):
            return False
        cmds.setAttr(plug, value)
        return True
    except Exception:
        return False


class ObjectPanel(QtWidgets.QFrame):
    def __init__(self, title, button_text, load_callback, load_children_callback, parent=None):
        super(ObjectPanel, self).__init__(parent)
        self.setObjectName("panel")
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)
        label = QtWidgets.QLabel(title)
        label.setObjectName("panelTitle")
        self.list_widget = QtWidgets.QListWidget()
        buttons = QtWidgets.QHBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.setSpacing(4)
        self.button = QtWidgets.QPushButton(button_text)
        self.button.clicked.connect(load_callback)
        self.children_button = QtWidgets.QPushButton("Load all child")
        self.children_button.setToolTip("Replace the list with the selection and all transform descendants")
        self.children_button.clicked.connect(load_children_callback)
        buttons.addWidget(self.button, 1)
        buttons.addWidget(self.children_button, 1)
        layout.addWidget(label)
        layout.addWidget(self.list_widget, 1)
        layout.addLayout(buttons)

    def set_objects(self, objects):
        self.list_widget.clear()
        for obj in objects:
            item = QtWidgets.QListWidgetItem(_short_name(obj))
            item.setToolTip(obj)
            self.list_widget.addItem(item)


class ChannelPanel(QtWidgets.QFrame):
    def __init__(self, title, parent=None):
        super(ChannelPanel, self).__init__(parent)
        self.setObjectName("panel")
        self._hidden_selected = set()
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)
        label = QtWidgets.QLabel(title)
        label.setObjectName("panelTitle")
        self.list_widget = QtWidgets.QListWidget()
        self.list_widget.setSelectionMode(EXTENDED_SELECTION)
        layout.addWidget(label)
        layout.addWidget(self.list_widget)

    def set_channels(
        self, channels, selected=None, hide_visibility=False, hide_scale=False,
        hide_radius=False,
    ):
        selected = set(selected or [])
        self._hidden_selected = set()
        self.list_widget.blockSignals(True)
        self.list_widget.clear()
        for plug in channels:
            attr = plug.rsplit(".", 1)[1]
            attr_low = attr.lower()
            hidden = (hide_visibility and attr_low in {"v", "visibility"}) or (
                hide_scale and attr_low in {"sx", "sy", "sz", "scalex", "scaley", "scalez"}
            ) or (hide_radius and attr_low == "radius")
            if hidden:
                if plug in selected:
                    self._hidden_selected.add(plug)
                continue
            display = "{}.{}".format(_short_name(plug.rsplit(".", 1)[0]), attr)
            item = QtWidgets.QListWidgetItem(display)
            item.setData(USER_ROLE, plug)
            item.setToolTip(plug)
            axis = attr[-1:].upper()
            axis_colors = {
                "X": QtGui.QColor("#503232"),
                "Y": QtGui.QColor("#314c38"),
                "Z": QtGui.QColor("#303f58"),
            }
            if axis in axis_colors:
                item.setBackground(axis_colors[axis])
            self.list_widget.addItem(item)
            item.setSelected(plug in selected)
        self.list_widget.blockSignals(False)

    def selected_plugs(self):
        return [item.data(USER_ROLE) for item in self.list_widget.selectedItems()]

    def stored_selected_plugs(self):
        return self.selected_plugs() + sorted(self._hidden_selected)

    def select_plugs(self, plugs):
        plugs = set(plugs or [])
        visible_plugs = {
            self.list_widget.item(row).data(USER_ROLE) for row in range(self.list_widget.count())
        }
        self._hidden_selected = plugs - visible_plugs
        self.list_widget.blockSignals(True)
        for row in range(self.list_widget.count()):
            item = self.list_widget.item(row)
            item.setSelected(item.data(USER_ROLE) in plugs)
        self.list_widget.blockSignals(False)


class HomePage(QtWidgets.QWidget):
    def __init__(self, window):
        super(HomePage, self).__init__()
        self.window = window
        self.drivers = []
        self.driven = []
        self.controllers = []
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        title = QtWidgets.QLabel("Stored Definitions")
        title.setObjectName("panelTitle")
        layout.addWidget(title)

        self.scroll = QtWidgets.QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(SCROLL_AS_NEEDED)
        self.scroll.setVerticalScrollBarPolicy(SCROLL_ALWAYS_OFF)
        self.card_host = QtWidgets.QWidget()
        self.card_layout = QtWidgets.QHBoxLayout(self.card_host)
        self.card_layout.setContentsMargins(8, 8, 8, 8)
        self.card_layout.setSpacing(9)
        self.card_layout.addStretch(1)
        self.scroll.setWidget(self.card_host)
        self.scroll.setMinimumHeight(208)
        layout.addWidget(self.scroll)

        panels = QtWidgets.QHBoxLayout()
        panels.setSpacing(9)
        self.driver_panel = ObjectPanel(
            "Driver", "Load Driver", lambda: self.load_selection("driver"),
            lambda: self.load_selection("driver", include_children=True),
        )
        self.driven_panel = ObjectPanel(
            "Driven", "Load Driven", lambda: self.load_selection("driven"),
            lambda: self.load_selection("driven", include_children=True),
        )
        self.controller_panel = ObjectPanel(
            "Controllers", "Load Controllers", lambda: self.load_selection("controllers"),
            lambda: self.load_selection("controllers", include_children=True),
        )
        panels.addWidget(self.driver_panel)
        panels.addWidget(self.driven_panel)
        panels.addWidget(self.controller_panel)
        layout.addLayout(panels, 1)

        create_button = QtWidgets.QPushButton("Create New Definition")
        create_button.setObjectName("createButton")
        create_button.clicked.connect(self.create_new_definition)
        layout.addWidget(create_button)

    def load_selection(self, target, include_children=False):
        selection = cmds.ls(selection=True, long=True, objectsOnly=True) or []
        if include_children:
            selection = selection_with_descendants(
                selection, controller=(target == "controllers")
            )
        else:
            selection = normalize_selection(selection, controller=(target == "controllers"))
        if target == "driver":
            self.drivers = selection
            self.driver_panel.set_objects(selection)
        elif target == "driven":
            self.driven = selection
            self.driven_panel.set_objects(selection)
        else:
            self.controllers = selection
            self.controller_panel.set_objects(selection)

    def refresh_cards(self):
        while self.card_layout.count():
            item = self.card_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        for node in definition_nodes():
            name = _safe_get_string(node, ATTR_NAME, _short_name(node))
            driver_count = len(definition_objects(node, ATTR_DRIVERS))
            driven_count = len(definition_objects(node, ATTR_DRIVEN))
            point_count = len(edit_points(node))
            button = QtWidgets.QToolButton()
            button.setObjectName("definitionCard")
            button.setFixedSize(132, 176)
            button.setToolButtonStyle(TOOL_BUTTON_TEXT_ONLY)
            button.setText("{}\n\n{} Driver\n{} Driven\n{} Poses".format(
                name, driver_count, driven_count, point_count
            ))
            button.setToolTip("Open {}".format(name))
            button.clicked.connect(lambda checked=False, n=node: self.window.open_definition(n))
            self.card_layout.addWidget(button)
        self.card_layout.addStretch(1)

    def create_new_definition(self):
        if not self.drivers or not self.driven:
            QtWidgets.QMessageBox.warning(
                self, TOOL_TITLE, "Load at least one Driver and one Driven object first."
            )
            return
        name = suggest_definition_name(self.drivers, self.driven)
        if not name:
            name, accepted = QtWidgets.QInputDialog.getText(
                self, TOOL_TITLE, "No anatomical name was detected.\nDefinition name:"
            )
            name = name.strip()
            if not accepted or not name:
                return
        with undo_chunk("Create Muscle Definition"):
            node = create_definition(name, self.drivers, self.driven, self.controllers)
        self.refresh_cards()
        self.window.open_definition(node)


class DefinitionPage(QtWidgets.QWidget):
    def __init__(self, window):
        super(DefinitionPage, self).__init__()
        self.window = window
        self.definition_node = None
        self._refreshing_points = False
        self._loading_options = False
        self._driver_objects = []
        self._driven_objects = []
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        top = QtWidgets.QHBoxLayout()
        back = QtWidgets.QPushButton("?  Back")
        back.setFixedWidth(92)
        back.clicked.connect(window.show_home)
        self.title = QtWidgets.QLabel()
        self.title.setObjectName("panelTitle")
        self.auto_mirror = QtWidgets.QCheckBox("Auto Mirror")
        self.auto_mirror.setToolTip(
            "When Key is pressed, also key matching L/R or Left/Right channels using their current values"
        )
        self.hide_visibility = QtWidgets.QCheckBox("Hide Visibility")
        self.hide_visibility.setToolTip("Hide Visibility channels while preserving their stored selection")
        self.hide_scale = QtWidgets.QCheckBox("Hide Scale")
        self.hide_scale.setToolTip("Hide Scale X/Y/Z channels while preserving their stored selection")
        self.hide_radius = QtWidgets.QCheckBox("Hide Radius")
        self.hide_radius.setToolTip("Hide joint Radius channels while preserving their stored selection")
        top.addWidget(back)
        top.addWidget(self.title)
        top.addStretch(1)
        top.addWidget(self.auto_mirror)
        top.addWidget(self.hide_visibility)
        top.addWidget(self.hide_scale)
        top.addWidget(self.hide_radius)
        layout.addLayout(top)

        channels = QtWidgets.QHBoxLayout()
        channels.setSpacing(9)
        self.driver_channels = ChannelPanel("Driver Channels")
        self.driven_channels = ChannelPanel("Driven Channels")
        channels.addWidget(self.driver_channels)
        channels.addWidget(self.driven_channels)
        layout.addLayout(channels, 3)

        lower = QtWidgets.QHBoxLayout()
        lower.setSpacing(12)
        key_area = QtWidgets.QFrame()
        key_area.setObjectName("panel")
        key_layout = QtWidgets.QHBoxLayout(key_area)
        key_layout.setContentsMargins(18, 18, 18, 18)
        key_layout.setSpacing(8)
        previous_button = QtWidgets.QPushButton("<")
        previous_button.setObjectName("poseNavButton")
        previous_button.setFixedSize(58, 58)
        previous_button.setToolTip("Previous keyed pose")
        previous_button.clicked.connect(lambda: self.navigate(-1))
        key_button = QtWidgets.QPushButton("Key")
        key_button.setObjectName("keyButton")
        key_button.setFixedSize(74, 74)
        key_button.setToolTip("Set Driven Key and store this pose")
        key_button.clicked.connect(self.create_key)
        next_button = QtWidgets.QPushButton(">")
        next_button.setObjectName("poseNavButton")
        next_button.setFixedSize(58, 58)
        next_button.setToolTip("Next keyed pose")
        next_button.clicked.connect(lambda: self.navigate(1))
        key_layout.addStretch(1)
        key_layout.addWidget(previous_button)
        key_layout.addWidget(key_button)
        key_layout.addWidget(next_button)
        key_layout.addStretch(1)
        lower.addWidget(key_area, 2)

        points_panel = QtWidgets.QFrame()
        points_panel.setObjectName("panel")
        points_layout = QtWidgets.QVBoxLayout(points_panel)
        points_layout.setContentsMargins(8, 8, 8, 8)
        points_label = QtWidgets.QLabel("Keyed Poses")
        points_label.setObjectName("panelTitle")
        self.point_list = QtWidgets.QListWidget()
        self.point_list.setDragDropMode(INTERNAL_MOVE)
        self.point_list.setDefaultDropAction(MOVE_ACTION)
        self.point_list.setSelectionMode(SINGLE_SELECTION)
        self.point_list.itemChanged.connect(self.rename_point)
        self.point_list.itemDoubleClicked.connect(lambda item: self.point_list.editItem(item))
        self.point_list.model().rowsMoved.connect(self.reorder_points)
        self.point_list.currentRowChanged.connect(self._point_row_changed)
        points_layout.addWidget(points_label)
        points_layout.addWidget(self.point_list)
        lower.addWidget(points_panel, 3)
        layout.addLayout(lower, 2)

        self.driver_channels.list_widget.itemSelectionChanged.connect(self.save_channel_selection)
        self.driven_channels.list_widget.itemSelectionChanged.connect(self.save_channel_selection)
        self.auto_mirror.toggled.connect(self.options_changed)
        self.hide_visibility.toggled.connect(self.options_changed)
        self.hide_scale.toggled.connect(self.options_changed)
        self.hide_radius.toggled.connect(self.options_changed)

    def load_definition(self, node):
        if not cmds.objExists(node):
            self.window.show_home()
            return
        self.definition_node = node
        ensure_definition_options(node)
        self.title.setText(_safe_get_string(node, ATTR_NAME, _short_name(node)))
        self._driver_objects = definition_objects(node, ATTR_DRIVERS)
        self._driven_objects = definition_objects(node, ATTR_DRIVEN)
        self._loading_options = True
        self.auto_mirror.setChecked(definition_option(node, ATTR_AUTO_MIRROR, False))
        self.hide_visibility.setChecked(definition_option(node, ATTR_HIDE_VISIBILITY, True))
        self.hide_scale.setChecked(definition_option(node, ATTR_HIDE_SCALE, True))
        self.hide_radius.setChecked(definition_option(node, ATTR_HIDE_RADIUS, True))
        self._loading_options = False
        self.refresh_channels()
        self.refresh_points()

    def refresh_channels(self):
        if not self.definition_node:
            return
        driver_selected = selected_channels(self.definition_node, ATTR_DRIVER_CHANNELS)
        driven_selected = selected_channels(self.definition_node, ATTR_DRIVEN_CHANNELS)
        self.driver_channels.set_channels(
            available_channels(self._driver_objects), driver_selected,
            hide_visibility=self.hide_visibility.isChecked(),
            hide_scale=self.hide_scale.isChecked(),
            hide_radius=self.hide_radius.isChecked(),
        )
        self.driven_channels.set_channels(
            available_channels(self._driven_objects), driven_selected,
            hide_visibility=self.hide_visibility.isChecked(),
            hide_scale=self.hide_scale.isChecked(),
            hide_radius=self.hide_radius.isChecked(),
        )

    def options_changed(self, *args):
        if self._loading_options or not self.definition_node:
            return
        # Capture visible selections before rebuilding filtered channel lists.
        self.save_channel_selection()
        set_definition_option(self.definition_node, ATTR_AUTO_MIRROR, self.auto_mirror.isChecked())
        set_definition_option(
            self.definition_node, ATTR_HIDE_VISIBILITY, self.hide_visibility.isChecked()
        )
        set_definition_option(self.definition_node, ATTR_HIDE_SCALE, self.hide_scale.isChecked())
        set_definition_option(self.definition_node, ATTR_HIDE_RADIUS, self.hide_radius.isChecked())
        self.refresh_channels()

    def save_channel_selection(self):
        if not self.definition_node or not cmds.objExists(self.definition_node):
            return
        set_selected_channels(
            self.definition_node, ATTR_DRIVER_CHANNELS, self.driver_channels.stored_selected_plugs()
        )
        set_selected_channels(
            self.definition_node, ATTR_DRIVEN_CHANNELS, self.driven_channels.stored_selected_plugs()
        )

    def refresh_points(self, selected_id=None):
        self._refreshing_points = True
        self.point_list.clear()
        selected_row = -1
        for row, point in enumerate(edit_points(self.definition_node)):
            item = QtWidgets.QListWidgetItem(point.get("name") or "Pose {:02d}".format(row + 1))
            item.setData(USER_ROLE, point.get("id"))
            item.setFlags(item.flags() | ITEM_EDITABLE | ITEM_DRAGGABLE)
            self.point_list.addItem(item)
            if point.get("id") == selected_id:
                selected_row = row
        if selected_row >= 0:
            self.point_list.setCurrentRow(selected_row)
        self._refreshing_points = False

    def create_key(self):
        drivers = self.driver_channels.selected_plugs()
        driven = self.driven_channels.selected_plugs()
        if len(drivers) != 1:
            QtWidgets.QMessageBox.warning(
                self, TOOL_TITLE, "Select exactly one Driver channel, like Maya's Set Driven Key tool."
            )
            return
        if not driven:
            QtWidgets.QMessageBox.warning(self, TOOL_TITLE, "Select one or more Driven channels.")
            return
        driver = drivers[0]
        successful = []
        mirrored_successful = []
        errors = []
        mirror_notes = []
        with undo_chunk("Muscle Definition Key"):
            for plug in driven:
                try:
                    cmds.setDrivenKeyframe(plug, currentDriver=driver)
                    successful.append(plug)
                except Exception as exc:
                    errors.append("{}: {}".format(plug, exc))
            mirrored_driver = ""
            if self.auto_mirror.isChecked():
                mirrored_driver = mirror_plug(driver)
                if not mirrored_driver:
                    mirror_notes.append("No opposite-side Driver match for {}".format(driver))
                else:
                    for plug in driven:
                        mirrored_driven = mirror_plug(plug)
                        if not mirrored_driven:
                            mirror_notes.append("No opposite-side Driven match for {}".format(plug))
                            continue
                        try:
                            cmds.setDrivenKeyframe(
                                mirrored_driven, currentDriver=mirrored_driver
                            )
                            mirrored_successful.append(mirrored_driven)
                        except Exception as exc:
                            errors.append("Mirror {}: {}".format(mirrored_driven, exc))
            if successful:
                points = edit_points(self.definition_node)
                point_id = str(uuid.uuid4())
                points.append({
                    "id": point_id,
                    "name": "Pose {:02d}".format(len(points) + 1),
                    "driverPlugs": [{"plug": driver, "value": _plug_value(driver)}],
                    "drivenPlugs": [
                        {"plug": plug, "value": _plug_value(plug)} for plug in successful
                    ],
                    "mirrorDriverPlug": mirrored_driver,
                    "mirrorDrivenPlugs": mirrored_successful,
                    "controllers": capture_controllers(self.definition_node),
                })
                set_edit_points(self.definition_node, points)
        if not successful:
            QtWidgets.QMessageBox.critical(
                self, TOOL_TITLE, "Set Driven Key failed.\n\n{}".format("\n".join(errors[:4]))
            )
            return
        self.refresh_points(selected_id=point_id)
        self.window.home_page.refresh_cards()
        if errors:
            QtWidgets.QMessageBox.warning(
                self, TOOL_TITLE,
                "Some Driven channels could not be keyed:\n\n{}".format("\n".join(errors[:4]))
            )
        elif mirror_notes:
            QtWidgets.QMessageBox.information(
                self, TOOL_TITLE,
                "The current side was keyed, but Auto Mirror skipped unmatched channels:\n\n{}".format(
                    "\n".join(mirror_notes[:4])
                ),
            )

    def rename_point(self, item):
        if self._refreshing_points or not self.definition_node:
            return
        point_id = item.data(USER_ROLE)
        points = edit_points(self.definition_node)
        for point in points:
            if point.get("id") == point_id:
                clean_name = item.text().strip() or "Pose"
                point["name"] = clean_name
                if item.text() != clean_name:
                    self._refreshing_points = True
                    item.setText(clean_name)
                    self._refreshing_points = False
                set_edit_points(self.definition_node, points)
                break

    def reorder_points(self, *args):
        if self._refreshing_points or not self.definition_node:
            return
        by_id = {point.get("id"): point for point in edit_points(self.definition_node)}
        reordered = []
        for row in range(self.point_list.count()):
            point = by_id.get(self.point_list.item(row).data(USER_ROLE))
            if point:
                reordered.append(point)
        if len(reordered) == len(by_id):
            set_edit_points(self.definition_node, reordered)

    def _point_row_changed(self, row):
        # Selecting a row establishes navigation position; restoration remains explicit via arrows.
        pass

    def navigate(self, offset):
        count = self.point_list.count()
        if not count:
            return
        current = self.point_list.currentRow()
        target = 0 if current < 0 and offset > 0 else count - 1 if current < 0 else current + offset
        target = max(0, min(count - 1, target))
        self.point_list.setCurrentRow(target)
        self.apply_point(target)

    def apply_point(self, row):
        points = edit_points(self.definition_node)
        if row < 0 or row >= len(points):
            return
        point = points[row]
        controller_nodes = dict(_connected_at(self.definition_node, ATTR_CONTROLLERS))
        with undo_chunk("Go To Muscle Edit Point"):
            for snapshot in point.get("controllers", []):
                node = controller_nodes.get(snapshot.get("index"))
                if not node:
                    continue
                for attr, value in snapshot.get("values", {}).items():
                    _set_if_possible("{}.{}".format(node, attr), value)
            for driver_data in point.get("driverPlugs", []):
                value = driver_data.get("value")
                if value is not None:
                    _set_if_possible(driver_data.get("plug", ""), value)
        self.driver_channels.select_plugs([d.get("plug") for d in point.get("driverPlugs", [])])
        self.driven_channels.select_plugs([d.get("plug") for d in point.get("drivenPlugs", [])])
        self.save_channel_selection()


class MuscleDefinitionWindow(QtWidgets.QDialog):
    def __init__(self, parent=_maya_main_window()):
        super(MuscleDefinitionWindow, self).__init__(parent)
        self.setObjectName(WINDOW_OBJECT)
        self.setWindowTitle(TOOL_TITLE)
        self.setMinimumSize(900, 660)
        self.resize(980, 760)
        self.setAttribute(DELETE_ON_CLOSE, True)
        self.setStyleSheet(STYLE)
        self._script_jobs = []
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.pages = QtWidgets.QStackedWidget()
        self.home_page = HomePage(self)
        self.definition_page = DefinitionPage(self)
        self.pages.addWidget(self.home_page)
        self.pages.addWidget(self.definition_page)
        layout.addWidget(self.pages)
        self._install_scene_jobs()
        self.show_home()

    def _install_scene_jobs(self):
        for event_name in ("SceneOpened", "NewSceneOpened"):
            try:
                job = cmds.scriptJob(event=[event_name, self.show_home], protected=True)
                self._script_jobs.append(job)
            except Exception:
                pass

    def closeEvent(self, event):
        for job in self._script_jobs:
            if cmds.scriptJob(exists=job):
                cmds.scriptJob(kill=job, force=True)
        self._script_jobs = []
        super(MuscleDefinitionWindow, self).closeEvent(event)

    def show_home(self, *args):
        self.home_page.refresh_cards()
        self.pages.setCurrentWidget(self.home_page)

    def open_definition(self, node):
        self.definition_page.load_definition(node)
        self.pages.setCurrentWidget(self.definition_page)


_WINDOW = None


def show():
    global _WINDOW
    try:
        if _WINDOW:
            _WINDOW.close()
            _WINDOW.deleteLater()
    except Exception:
        pass
    _WINDOW = MuscleDefinitionWindow()
    _WINDOW.show()
    _WINDOW.raise_()
    _WINDOW.activateWindow()
    return _WINDOW


if __name__ == "__main__":
    show()
