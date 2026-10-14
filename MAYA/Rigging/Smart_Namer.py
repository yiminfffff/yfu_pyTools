import re
import math
import builtins

import maya.cmds as cmds
import maya.api.OpenMaya as om
import maya.OpenMayaUI as omui


# ------------------------------------------------------------
# Qt compatibility
# ------------------------------------------------------------

try:
    from PySide6 import QtCore, QtWidgets
    from shiboken6 import wrapInstance
except ImportError:
    from PySide2 import QtCore, QtWidgets
    from shiboken2 import wrapInstance


# ------------------------------------------------------------
# Configuration
# ------------------------------------------------------------

TOOL_KEY = "_maya_skeleton_naming_assistant"
TOOL_WINDOW_KEY = "_maya_skeleton_naming_assistant_window"
POSITIVE_X_LEFT_OPTION = "skeletonNamingAssistant_positiveXIsLeft"
SILENT_RENAME_OPTION = "skeletonNamingAssistant_silentRename"
LISTENER_ENABLED_OPTION = "skeletonNamingAssistant_listenerEnabled"
ANATOMY_MODE_OPTION = "skeletonNamingAssistant_anatomyMode"
POSITIVE_Z_FORWARD_OPTION = "skeletonNamingAssistant_positiveZForward"


def positive_x_is_left():
    """Return the saved convention for which side positive world X means."""

    try:
        if cmds.optionVar(exists=POSITIVE_X_LEFT_OPTION):
            return bool(cmds.optionVar(query=POSITIVE_X_LEFT_OPTION))
    except Exception:
        pass

    return True


def set_positive_x_is_left(value):
    """Save the positive-X side convention in Maya preferences."""

    try:
        cmds.optionVar(
            intValue=(POSITIVE_X_LEFT_OPTION, 1 if value else 0)
        )
    except Exception:
        pass


def current_anatomy_mode():
    """Return the saved naming/anatomy mode."""

    try:
        if cmds.optionVar(exists=ANATOMY_MODE_OPTION):
            value = cmds.optionVar(query=ANATOMY_MODE_OPTION)
            if value in ("biped", "quadruped"):
                return value
    except Exception:
        pass

    return "biped"


def set_anatomy_mode(value):
    """Save the selected naming/anatomy mode in Maya preferences."""

    if value not in ("biped", "quadruped"):
        return

    try:
        cmds.optionVar(stringValue=(ANATOMY_MODE_OPTION, value))
    except Exception:
        pass


def positive_z_is_forward():
    """Return whether positive world Z points toward the animal's head."""

    try:
        if cmds.optionVar(exists=POSITIVE_Z_FORWARD_OPTION):
            return bool(cmds.optionVar(query=POSITIVE_Z_FORWARD_OPTION))
    except Exception:
        pass

    return True


def set_positive_z_forward(value):
    """Save the world-Z forward convention in Maya preferences."""

    try:
        cmds.optionVar(
            intValue=(POSITIVE_Z_FORWARD_OPTION, 1 if value else 0)
        )
    except Exception:
        pass


def silent_rename_enabled():
    """Return whether listener suggestions should be applied without prompts."""

    try:
        if cmds.optionVar(exists=SILENT_RENAME_OPTION):
            return bool(cmds.optionVar(query=SILENT_RENAME_OPTION))
    except Exception:
        pass

    return False


def set_silent_rename_enabled(value):
    """Save the silent listener preference in Maya preferences."""

    try:
        cmds.optionVar(
            intValue=(SILENT_RENAME_OPTION, 1 if value else 0)
        )
    except Exception:
        pass


def listener_enabled_by_default():
    """Return the saved listener preference, enabled on first use."""

    try:
        if cmds.optionVar(exists=LISTENER_ENABLED_OPTION):
            return bool(cmds.optionVar(query=LISTENER_ENABLED_OPTION))
    except Exception:
        pass

    return True


def set_listener_preference(value):
    """Save whether the background listener should start with the tool."""

    try:
        cmds.optionVar(
            intValue=(LISTENER_ENABLED_OPTION, 1 if value else 0)
        )
    except Exception:
        pass

CENTER_CHAIN = [
    "cog",
    "spine",
    "chest",
    "neck",
    "head",
    "head_end",
]

ARM_CHAIN = [
    "clavicle",
    "shoulder",
    "elbow",
    "wrist",
]

LEG_CHAIN = [
    "hip",
    "knee",
    "ankle",
    "ball",
    "ball_end",
]

QUADRUPED_CENTER_CHAIN = [
    "cog",
    "lumbar",
    "thorax",
    "cervical",
    "cranium",
    "cranium_end",
]

QUADRUPED_FORELIMB_CHAIN = [
    "scapula",
    "humerus",
    "elbow",
    "radius",
    "carpus",
    "metacarpus",
    "phalanges",
]

QUADRUPED_HINDLIMB_CHAIN = [
    "femur",
    "stifle",
    "tibia",
    "tarsus",
    "metatarsus",
    "phalanges",
]


# Only suggest anatomical names automatically for names that look generic.
# This prevents the tool from constantly questioning custom names.
GENERIC_NAME_PATTERNS = [
    r"^(?:joint|jnt|bone)(?:[_\-.]?\d+)?$",
]


# ------------------------------------------------------------
# Basic utilities
# ------------------------------------------------------------

def maya_main_window():
    """Return Maya's main window."""

    ptr = omui.MQtUtil.mainWindow()

    if ptr:
        return wrapInstance(int(ptr), QtWidgets.QWidget)

    return None


def qt_enum(owner, enum_name, value_name):
    """Resolve Qt5 flat enums and Qt6 scoped enums consistently."""

    value = getattr(owner, value_name, None)
    if value is not None:
        return value

    return getattr(getattr(owner, enum_name), value_name)


def short_name(node):
    """Return the short DAG name."""

    return node.split("|")[-1]


def get_joint_children(joint):
    """Return direct joint children."""

    return cmds.listRelatives(
        joint,
        children=True,
        type="joint",
        fullPath=True
    ) or []


def get_world_position(node):
    """Return world-space translation."""

    value = cmds.xform(
        node,
        query=True,
        worldSpace=True,
        translation=True
    )

    return om.MVector(
        value[0],
        value[1],
        value[2]
    )


def distance(a, b):
    """Return distance between two DAG nodes."""

    return (get_world_position(a) - get_world_position(b)).length()


def is_generic_name(name):
    """Return True if a node name looks like a default temporary name."""

    clean_name = short_name(name).rsplit(":", 1)[-1]

    for pattern in GENERIC_NAME_PATTERNS:
        if re.match(pattern, clean_name, re.IGNORECASE):
            return True

    return False


def descendant_depth(joint, max_depth=20):
    """
    Return the maximum joint depth below a joint.

    Depth is intentionally capped so extremely long accessory chains
    do not automatically receive a higher score.
    """

    children = get_joint_children(joint)

    if not children or max_depth <= 0:
        return 0

    return 1 + max(
        descendant_depth(child, max_depth - 1)
        for child in children
    )


def get_object_handle(node):
    """Create an MObjectHandle for a DAG node."""

    selection = om.MSelectionList()
    selection.add(node)

    obj = selection.getDependNode(0)

    return om.MObjectHandle(obj)


def path_from_handle(handle):
    """Resolve an MObjectHandle back to its current DAG path."""

    if not handle:
        return None

    if not handle.isValid():
        return None

    if not handle.isAlive():
        return None

    try:
        obj = handle.object()
        path = om.MDagPath.getAPathTo(obj)

        return path.fullPathName()

    except Exception:
        return None


# ------------------------------------------------------------
# Character root detection
# ------------------------------------------------------------

def find_character_root(joint=None):
    """
    Try to find the top-level character transform ending with _ROOT.

    The ancestor hierarchy is checked first.
    """

    if joint and cmds.objExists(joint):

        current = joint

        while True:

            parent = cmds.listRelatives(
                current,
                parent=True,
                fullPath=True
            ) or []

            if not parent:
                break

            current = parent[0]

            if short_name(current).upper().endswith("_ROOT"):
                return current

    # Fallback: search top-level transforms
    assemblies = cmds.ls(
        assemblies=True,
        long=True
    ) or []

    root_candidates = []

    for node in assemblies:

        if short_name(node).upper().endswith("_ROOT"):
            root_candidates.append(node)

    if len(root_candidates) == 1:
        return root_candidates[0]

    return None


def get_character_name(joint=None):
    """Extract character name from CHARNAME_ROOT."""

    root = find_character_root(joint)

    if not root:
        if joint:
            namespace = short_name(joint).rpartition(":")[0]
            if namespace:
                return namespace + ":charName"
        return "charName"

    name = short_name(root)

    if name.upper().endswith("_ROOT"):
        return name[:-5]

    return name


# ------------------------------------------------------------
# Name generation
# ------------------------------------------------------------

def clean_name_fragment(value):
    """Normalize user-entered name fragments for use in Maya node names."""

    return re.sub(r"\s+", "_", str(value).strip()).strip("_")


def build_rig_name(
    character,
    part,
    side=None,
    bone_type="RIG",
    prefix="",
    suffix=""
):
    """
    Build a rig joint name.

    Examples:
        character_jnt_rig_clavicle_left
        character_jnt_skin_ball_left_end
        prefix_character_jnt_fk_head_end_suffix
    """

    character = clean_name_fragment(character) or "charName"
    bone_type = clean_name_fragment(bone_type).lower() or "rig"
    prefix = clean_name_fragment(prefix)
    suffix = clean_name_fragment(suffix)

    if prefix:
        if ":" in character:
            namespace, unused_separator, leaf = character.rpartition(":")
            character = namespace + ":" + prefix + "_" + leaf
        else:
            character = prefix + "_" + character

    is_end = part.endswith("_end")
    base_part = part[:-4] if is_end else part

    name_parts = [character, "jnt", bone_type, base_part]

    if side:
        name_parts.append(side)

    if is_end:
        name_parts.append("end")

    if suffix:
        name_parts.append(suffix)

    return "_".join(name_parts)


# ------------------------------------------------------------
# Confirmation dialogs
# ------------------------------------------------------------

class YesNoDialog(QtWidgets.QMessageBox):

    def __init__(self, text, parent=None):

        super(YesNoDialog, self).__init__(parent)

        self.setWindowTitle("Skeleton Naming Assistant")
        self.setText(text)

        self.setIcon(QtWidgets.QMessageBox.Question)

        self.setStandardButtons(
            QtWidgets.QMessageBox.Yes |
            QtWidgets.QMessageBox.No
        )

        self.setDefaultButton(
            QtWidgets.QMessageBox.Yes
        )

    def keyPressEvent(self, event):
        """Support direct Y/N confirmation."""

        text = event.text().lower()

        if text == "y":
            self.done(QtWidgets.QMessageBox.Yes)
            return

        if text == "n":
            self.done(QtWidgets.QMessageBox.No)
            return

        super(YesNoDialog, self).keyPressEvent(event)


def ask_yes_no(text, parent=None):
    """Show a Y/N confirmation dialog."""

    dialog = YesNoDialog(
        text,
        parent or maya_main_window()
    )

    return dialog.exec() == QtWidgets.QMessageBox.Yes


# ------------------------------------------------------------
# Skeleton analysis
# ------------------------------------------------------------

class SkeletonAnalyzer(object):

    def __init__(self, cog, mode=None):

        self.cog = cog
        self.mode = mode or current_anatomy_mode()
        self.quadruped = self.mode == "quadruped"

        if self.quadruped:
            self.center_chain = QUADRUPED_CENTER_CHAIN
            self.arm_chain = QUADRUPED_FORELIMB_CHAIN
            self.leg_chain = QUADRUPED_HINDLIMB_CHAIN
            self.ikfk_arm_root = "scapula"
            self.ikfk_leg_root = "femur"
        else:
            self.center_chain = CENTER_CHAIN
            self.arm_chain = ARM_CHAIN
            self.leg_chain = LEG_CHAIN
            self.ikfk_arm_root = "shoulder"
            self.ikfk_leg_root = "hip"

        self.mapping = {}

    # --------------------------------------------------------
    # Mapping
    # --------------------------------------------------------

    def assign(self, joint, part, side=None):
        """Assign an anatomical prediction to a joint."""

        if not joint:
            return

        if not cmds.objExists(joint):
            return

        self.mapping[joint] = {
            "part": part,
            "side": side,
        }

    # --------------------------------------------------------
    # Candidate scoring
    # --------------------------------------------------------

    def depth_score(self, joint, expected_depth):
        """
        Score a candidate based on expected remaining chain depth.

        A chain that is much longer than expected is not automatically
        considered better.
        """

        depth = descendant_depth(joint)

        difference = abs(depth - expected_depth)

        if difference == 0:
            return 5.0

        if difference == 1:
            return 3.5

        if difference == 2:
            return 1.5

        return max(
            -2.0,
            1.0 - difference
        )

    def branch_penalty(self, joint):
        """
        Penalize excessive immediate branching.

        Small branches are still allowed because rig components may
        contain helper or accessory joints.
        """

        count = len(get_joint_children(joint))

        if count <= 2:
            return 0.0

        if count == 3:
            return -0.5

        return -(count - 2) * 1.5

    def central_up_score(self, parent, child):
        """Score a child that continues along the body's center axis."""

        parent_pos = get_world_position(parent)
        child_pos = get_world_position(child)

        delta = child_pos - parent_pos

        length = max(delta.length(), 0.0001)

        side_motion = abs(delta.x) / length

        if self.quadruped:
            forward = delta.z / length
            if not positive_z_is_forward():
                forward *= -1.0
            score = forward * 4.0 - side_motion * 3.0
            score -= abs(delta.y) / length
        else:
            up = delta.y / length
            score = up * 4.0 - side_motion * 3.0

        return score

    def downward_score(self, parent, child):
        """Score a child expected to move downward."""

        parent_pos = get_world_position(parent)
        child_pos = get_world_position(child)

        delta = child_pos - parent_pos

        length = max(delta.length(), 0.0001)

        down = -delta.y / length

        return down * 4.0

    def lateral_score(self, parent, child, side):
        """Score a child expected to move outward from the body."""

        parent_pos = get_world_position(parent)
        child_pos = get_world_position(child)

        delta = child_pos - parent_pos

        length = max(delta.length(), 0.0001)

        x_direction = delta.x / length

        positive_x_left = positive_x_is_left()

        if side == "left":
            return x_direction * 4.0 if positive_x_left else -x_direction * 4.0

        return -x_direction * 4.0 if positive_x_left else x_direction * 4.0

    # --------------------------------------------------------
    # Candidate selection
    # --------------------------------------------------------

    def choose_candidate(
        self,
        parent,
        candidates,
        expected_depth,
        direction_mode=None,
        side=None,
        minimum_score=1.0
    ):
        """Choose the most structurally plausible child candidate."""

        if not candidates:
            return None

        scored = []

        for child in candidates:

            score = 0.0

            score += self.depth_score(
                child,
                expected_depth
            )

            score += self.branch_penalty(child)

            if direction_mode == "up":
                score += self.central_up_score(
                    parent,
                    child
                )

            elif direction_mode == "down":
                score += self.downward_score(
                    parent,
                    child
                )

            elif direction_mode == "lateral":
                score += self.lateral_score(
                    parent,
                    child,
                    side
                )

            scored.append(
                (score, child)
            )

        scored.sort(
            key=lambda item: item[0],
            reverse=True
        )

        best_score, best_joint = scored[0]

        if best_score < minimum_score:
            return None

        # Avoid uncertain guesses when two candidates score almost equally.
        if len(scored) > 1:

            second_score = scored[1][0]

            if abs(best_score - second_score) < 0.75:
                return None

        return best_joint

    # --------------------------------------------------------
    # Chain tracing
    # --------------------------------------------------------

    def trace_chain(
        self,
        start_joint,
        labels,
        side=None,
        direction_mode=None
    ):
        """
        Trace a known anatomical chain.

        The expected remaining depth becomes shorter at every step.
        """

        if not start_joint:
            return

        current = start_joint

        for index, label in enumerate(labels):

            if not current:
                break

            self.assign(
                current,
                label,
                side
            )

            if index == len(labels) - 1:
                break

            children = get_joint_children(current)

            if not children:
                break

            expected_depth = len(labels) - index - 2

            next_joint = self.choose_candidate(
                current,
                children,
                expected_depth,
                direction_mode=direction_mode,
                side=side,
                minimum_score=-1.0
            )

            if not next_joint:
                break

            current = next_joint

    # --------------------------------------------------------
    # Center skeleton
    # --------------------------------------------------------

    def build_center_chain(self):

        self.assign(
            self.cog,
            "cog"
        )

        current = self.cog
        mapped_center = [self.cog]
        minimum_scores = [1.0, -1.0, 0.5, -1.0, -2.0]

        for target_index, label in enumerate(self.center_chain[1:], start=1):
            candidate = self.choose_candidate(
                current,
                get_joint_children(current),
                expected_depth=len(self.center_chain) - target_index - 1,
                direction_mode="up",
                minimum_score=minimum_scores[target_index - 1]
            )
            if not candidate:
                break

            self.assign(candidate, label)
            mapped_center.append(candidate)
            current = candidate

        spine = mapped_center[1] if len(mapped_center) > 1 else None
        chest = mapped_center[2] if len(mapped_center) > 2 else None
        return spine, chest

    # --------------------------------------------------------
    # Legs
    # --------------------------------------------------------

    def build_legs(self, spine):

        candidates = get_joint_children(self.cog)

        candidates = [
            joint
            for joint in candidates
            if joint != spine
        ]

        cog_pos = get_world_position(self.cog)

        left_candidates = []
        right_candidates = []

        for joint in candidates:

            position = get_world_position(joint)
            side_offset = position.x - cog_pos.x

            if abs(side_offset) < 0.0001:
                continue

            positive_side = side_offset > 0.0

            if positive_side == positive_x_is_left():
                left_candidates.append(joint)

            else:
                right_candidates.append(joint)

        left_hip = self.choose_candidate(
            self.cog,
            left_candidates,
            expected_depth=len(self.leg_chain) - 1,
            direction_mode="down",
            side="left",
            minimum_score=0.0
        )

        right_hip = self.choose_candidate(
            self.cog,
            right_candidates,
            expected_depth=len(self.leg_chain) - 1,
            direction_mode="down",
            side="right",
            minimum_score=0.0
        )

        if left_hip:

            self.trace_chain(
                left_hip,
                self.leg_chain,
                side="left",
                direction_mode="down"
            )

        if right_hip:

            self.trace_chain(
                right_hip,
                self.leg_chain,
                side="right",
                direction_mode="down"
            )

    # --------------------------------------------------------
    # Arms
    # --------------------------------------------------------

    def build_arms(self, chest):

        if not chest:
            return

        children = get_joint_children(chest)

        mapped_center_children = []

        for joint in children:

            data = self.mapping.get(joint)

            if data:
                if data["part"] == self.center_chain[3]:
                    mapped_center_children.append(joint)

        arm_candidates = [
            joint
            for joint in children
            if joint not in mapped_center_children
        ]

        chest_pos = get_world_position(chest)

        left_candidates = []
        right_candidates = []

        for joint in arm_candidates:

            position = get_world_position(joint)
            side_offset = position.x - chest_pos.x

            if abs(side_offset) < 0.0001:
                continue

            positive_side = side_offset > 0.0

            if positive_side == positive_x_is_left():
                left_candidates.append(joint)

            else:
                right_candidates.append(joint)

        left_clavicle = self.choose_candidate(
            chest,
            left_candidates,
            expected_depth=len(self.arm_chain) - 1,
            direction_mode="lateral",
            side="left",
            minimum_score=0.0
        )

        right_clavicle = self.choose_candidate(
            chest,
            right_candidates,
            expected_depth=len(self.arm_chain) - 1,
            direction_mode="lateral",
            side="right",
            minimum_score=0.0
        )

        if left_clavicle:

            self.trace_chain(
                left_clavicle,
                self.arm_chain,
                side="left",
                direction_mode="down" if self.quadruped else "lateral"
            )

        if right_clavicle:

            self.trace_chain(
                right_clavicle,
                self.arm_chain,
                side="right",
                direction_mode="down" if self.quadruped else "lateral"
            )

    # --------------------------------------------------------
    # Full analysis
    # --------------------------------------------------------

    def analyze(self):

        if not self.cog:
            return {}

        if not cmds.objExists(self.cog):
            return {}

        spine, chest = self.build_center_chain()

        self.build_legs(spine)
        self.build_arms(chest)

        return self.mapping


# ------------------------------------------------------------
# Preview and controls
# ------------------------------------------------------------

def scene_name_index():
    """Index exact short names once for fast, unambiguous preflight checks."""

    index = {}

    for node in (cmds.ls(long=True) or []):
        index.setdefault(short_name(node), []).append(node)

    return index


def exact_scene_name_matches(name, index=None):
    """Return scene nodes whose short name exactly matches ``name``."""

    if index is not None:
        return index.get(name, [])

    return scene_name_index().get(name, [])


class RenamePreviewDialog(QtWidgets.QDialog):
    """Review predicted names and apply a safe, undoable batch rename."""

    def __init__(self, cog, mapping, parent=None):

        super(RenamePreviewDialog, self).__init__(parent)

        self.cog = cog
        self.mapping = mapping
        self.rows = []

        self.setWindowTitle("Skeleton Naming Assistant - Preview")
        self.resize(820, 560)

        layout = QtWidgets.QVBoxLayout(self)

        root_label = QtWidgets.QLabel(
            "COG: {}\nSelect a row to highlight its joint in Maya. Custom names start unchecked."
            .format(short_name(cog))
        )
        layout.addWidget(root_label)

        naming_options = QtWidgets.QFormLayout()

        self.character_edit = QtWidgets.QLineEdit(
            get_character_name(cog),
            self
        )
        self.bone_type_combo = QtWidgets.QComboBox(self)
        self.bone_type_combo.addItems(["RIG", "SKIN", "IK", "FK"])
        self.prefix_edit = QtWidgets.QLineEdit(self)
        self.suffix_edit = QtWidgets.QLineEdit(self)

        naming_options.addRow("Character name", self.character_edit)
        naming_options.addRow("Bone type", self.bone_type_combo)
        naming_options.addRow("Extra prefix", self.prefix_edit)
        naming_options.addRow("Extra suffix", self.suffix_edit)
        layout.addLayout(naming_options)

        self.table = QtWidgets.QTableWidget(0, 4, self)
        self.table.setHorizontalHeaderLabels([
            "Rename",
            "Current name",
            "Proposed name",
            "Part / status",
        ])
        self.table.setSelectionBehavior(
            qt_enum(
                QtWidgets.QAbstractItemView,
                "SelectionBehavior",
                "SelectRows"
            )
        )
        self.table.setSelectionMode(
            qt_enum(
                QtWidgets.QAbstractItemView,
                "SelectionMode",
                "SingleSelection"
            )
        )
        self.table.setEditTriggers(
            qt_enum(
                QtWidgets.QAbstractItemView,
                "EditTrigger",
                "NoEditTriggers"
            )
        )
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.horizontalHeader().setSectionResizeMode(
            0,
            qt_enum(
                QtWidgets.QHeaderView,
                "ResizeMode",
                "ResizeToContents"
            )
        )
        layout.addWidget(self.table)

        buttons = QtWidgets.QHBoxLayout()
        buttons.addStretch(1)
        self.apply_button = QtWidgets.QPushButton("Apply checked names")
        close_button = QtWidgets.QPushButton("Close")
        buttons.addWidget(self.apply_button)
        buttons.addWidget(close_button)
        layout.addLayout(buttons)

        self.apply_button.clicked.connect(self.apply_checked)
        close_button.clicked.connect(self.reject)

        self.populate()
        self.character_edit.textChanged.connect(self.refresh_preview)
        self.bone_type_combo.currentTextChanged.connect(self.refresh_preview)
        self.prefix_edit.textChanged.connect(self.refresh_preview)
        self.suffix_edit.textChanged.connect(self.refresh_preview)
        self.table.itemSelectionChanged.connect(self.select_joint_for_row)

    def populate(self):

        for joint, data in self.mapping.items():

            if not cmds.objExists(joint):
                continue

            self.rows.append({
                "handle": get_object_handle(joint),
                "path": joint,
                "current": short_name(joint),
                "part": data["part"] + ("_" + data["side"] if data["side"] else ""),
                "part_name": data["part"],
                "side": data["side"],
                "checked": is_generic_name(short_name(joint)),
            })

        self.refresh_preview()

    def refresh_preview(self, unused_value=None):

        proposed_counts = {}

        for row in self.rows:
            old_checkbox = row.get("checkbox")
            if old_checkbox and old_checkbox.isEnabled():
                row["checked"] = old_checkbox.isChecked()

            row["proposed"] = build_rig_name(
                self.character_edit.text(),
                row["part_name"],
                row["side"],
                bone_type=self.bone_type_combo.currentText(),
                prefix=self.prefix_edit.text(),
                suffix=self.suffix_edit.text()
            )
            proposed_counts[row["proposed"]] = (
                proposed_counts.get(row["proposed"], 0) + 1
            )

        scene_names = scene_name_index()
        self.table.setRowCount(len(self.rows))

        for row_index, row in enumerate(self.rows):

            current = row["current"]
            proposed = row["proposed"]
            matches = exact_scene_name_matches(proposed, scene_names)

            conflict = None

            if proposed_counts.get(proposed, 0) > 1:
                conflict = "Duplicate proposed name"
            elif proposed != current and any(
                match != row["path"] for match in matches
            ):
                conflict = "Name already exists in scene"

            checkbox = QtWidgets.QCheckBox(self.table)
            checkbox.setChecked(
                row["checked"] and current != proposed and conflict is None
            )
            if current == proposed or conflict:
                checkbox.setEnabled(False)
                checkbox.setChecked(False)
            self.table.setCellWidget(row_index, 0, checkbox)
            row["checkbox"] = checkbox
            row["conflict"] = conflict

            current_item = QtWidgets.QTableWidgetItem(current)
            current_item.setToolTip(row["path"])
            self.table.setItem(row_index, 1, current_item)
            self.table.setItem(row_index, 2, QtWidgets.QTableWidgetItem(proposed))

            if conflict:
                status = conflict
            elif current == proposed:
                status = "Already named"
            elif is_generic_name(current):
                status = row["part"]
            else:
                status = row["part"] + " - custom name"

            self.table.setItem(row_index, 3, QtWidgets.QTableWidgetItem(status))

    def select_joint_for_row(self):

        row_index = self.table.currentRow()
        if row_index < 0 or row_index >= len(self.rows):
            return

        joint = path_from_handle(self.rows[row_index]["handle"])
        if not joint or not cmds.objExists(joint):
            return

        tool = getattr(builtins, TOOL_KEY, None)
        was_processing = tool.processing if tool else False

        try:
            if tool:
                tool.processing = True
            cmds.select(joint, replace=True)
            if tool:
                tool.remember_selection(joint)
        finally:
            if tool:
                tool.processing = was_processing

    def apply_checked(self):

        selected = [
            row
            for row in self.rows
            if row["checkbox"].isEnabled() and row["checkbox"].isChecked()
        ]

        if not selected:
            QtWidgets.QMessageBox.information(
                self,
                "Nothing selected",
                "Check at least one conflict-free name to apply."
            )
            return

        # Recheck against the live scene; it may have changed since preview.
        live_names = scene_name_index()

        for row in selected:
            joint = path_from_handle(row["handle"])
            if not joint or not cmds.objExists(joint):
                QtWidgets.QMessageBox.warning(
                    self,
                    "Joint no longer exists",
                    "A joint in this preview was deleted. Reopen the preview."
                )
                return

            conflicts = [
                match
                for match in exact_scene_name_matches(row["proposed"], live_names)
                if match != joint
            ]
            if conflicts:
                QtWidgets.QMessageBox.warning(
                    self,
                    "Name conflict",
                    'The name "{}" is now in use. Reopen the preview to refresh.'
                    .format(row["proposed"])
                )
                return

        opened = False

        try:
            cmds.undoInfo(openChunk=True, chunkName="Skeleton Naming Assistant")
            opened = True

            for row in selected:
                joint = path_from_handle(row["handle"])
                if not joint:
                    raise RuntimeError("A joint was removed during the rename.")

                renamed = cmds.rename(joint, row["proposed"])
                if short_name(renamed) != row["proposed"]:
                    raise RuntimeError(
                        'Maya could not apply the exact name "{}".'
                        .format(row["proposed"])
                    )

        except Exception as error:
            if opened:
                cmds.undoInfo(closeChunk=True)
                opened = False
                try:
                    cmds.undo()
                except Exception:
                    pass

            QtWidgets.QMessageBox.warning(
                self,
                "Rename not applied",
                "The batch was rolled back. {}".format(error)
            )
            return

        finally:
            if opened:
                cmds.undoInfo(closeChunk=True)

        cmds.inViewMessage(
            amg="<hl>Skeleton names updated</hl>",
            pos="topCenter",
            fade=True
        )
        self.accept()


def show_rename_preview(cog=None, parent=None):
    """Open a batch preview for a COG joint or the assistant's saved COG."""

    if not cog:
        tool = getattr(builtins, TOOL_KEY, None)
        if tool:
            cog = path_from_handle(tool.cog_handle)

    if not cog or not cmds.objExists(cog):
        QtWidgets.QMessageBox.warning(
            parent or maya_main_window(),
            "COG not set",
            "Select the COG joint and choose 'Use selected joint as COG' first."
        )
        return

    mapping = SkeletonAnalyzer(cog).analyze()

    if not mapping:
        QtWidgets.QMessageBox.information(
            parent or maya_main_window(),
            "No predictions",
            "No anatomical names could be predicted from this COG."
        )
        return

    dialog = RenamePreviewDialog(
        cog,
        mapping,
        parent or maya_main_window()
    )

    if hasattr(dialog, "exec"):
        dialog.exec()
    else:
        dialog.exec_()


def collect_joint_subtree(root):
    """Return a joint root and all joint descendants in parent-first order."""

    result = []
    pending = [root]

    while pending:
        joint = pending.pop()
        result.append(joint)
        children = get_joint_children(joint)
        pending.extend(reversed(children))

    return result


def convert_rig_name_to_type(name, target_type):
    """Replace a known type token, or suffix untagged helper joint names."""

    pattern = re.compile(
        r"(_jnt_)(?:rig|skin|ik|fk)(?=_(?:cog|spine|chest|neck|head|clavicle|shoulder|"
        r"elbow|wrist|hip|knee|ankle|ball|lumbar|thorax|cervical|cranium|scapula|"
        r"humerus|radius|carpus|metacarpus|phalanges|femur|stifle|tibia|tarsus|"
        r"metatarsus)(?:_|$))",
        re.IGNORECASE
    )
    short = short_name(name)

    if pattern.search(short):
        return pattern.sub(
            lambda match: match.group(1) + target_type.lower(),
            short,
            count=1
        )

    return short + "_" + target_type.lower()


def duplicate_joint_roots_as_types(type_roots, parent=None):
    """Duplicate joint branches and change their rig type tokens safely."""

    groups = []
    all_targets = []

    for target_type, roots in type_roots:
        for root in roots:
            if not root or not cmds.objExists(root):
                QtWidgets.QMessageBox.warning(
                    parent or maya_main_window(),
                    "Joint no longer exists",
                    "A source joint for this operation no longer exists."
                )
                return False

            source_nodes = collect_joint_subtree(root)
            target_by_source = {}

            for source in source_nodes:
                target_name = convert_rig_name_to_type(
                    short_name(source),
                    target_type
                )

                target_by_source[source] = target_name
                all_targets.append(target_name)

            groups.append({
                "type": target_type.lower(),
                "root": root,
                "sources": source_nodes,
                "targets": target_by_source,
            })

    target_counts = {}
    for target_name in all_targets:
        target_counts[target_name] = target_counts.get(target_name, 0) + 1

    live_names = scene_name_index()
    raw_targets = set(all_targets)
    used_targets = set(
        name for name, count in target_counts.items()
        if count == 1
    )
    disambiguated_count = 0

    for group in groups:
        for source in group["sources"]:
            target_name = group["targets"][source]
            if target_counts[target_name] <= 1:
                continue

            disambiguated_count += 1
            number = 1
            unique_name = "{}_{}".format(target_name, str(number).zfill(2))
            while (
                unique_name in raw_targets or
                unique_name in used_targets or
                exact_scene_name_matches(unique_name, live_names)
            ):
                number += 1
                unique_name = "{}_{}".format(target_name, str(number).zfill(2))

            group["targets"][source] = unique_name
            used_targets.add(unique_name)

    all_targets = [
        group["targets"][source]
        for group in groups
        for source in group["sources"]
    ]

    conflicts = [
        name for name in all_targets
        if exact_scene_name_matches(name, live_names)
    ]
    if conflicts:
        QtWidgets.QMessageBox.warning(
            parent or maya_main_window(),
            "Names already exist",
            "Clear or rename these existing nodes before copying: {}"
            .format(", ".join(conflicts[:5]))
        )
        return False

    joint_count = len(all_targets)
    type_counts = {}
    ordered_types = []
    for group in groups:
        if group["type"] not in type_counts:
            ordered_types.append(group["type"])
        type_counts[group["type"]] = (
            type_counts.get(group["type"], 0) + len(group["sources"])
        )

    type_summary = ", ".join(
        "{}: {} joints".format(target_type.upper(), type_counts[target_type])
        for target_type in ordered_types
    )
    helper_name_count = sum(
        1
        for group in groups
        for source in group["sources"]
        if not re.search(r"(?i)_jnt_(?:rig|skin|ik|fk)_", short_name(source))
    )
    question = "Create {} joints?\n{}".format(joint_count, type_summary)
    if helper_name_count:
        question += (
            "\n{} untagged joints will keep their names and gain "
            "a type suffix."
        ).format(helper_name_count)
    if disambiguated_count:
        question += "\n{} repeated names will receive numeric suffixes.".format(
            disambiguated_count
        )

    if not ask_yes_no(
        question,
        parent
    ):
        return False

    opened = False
    created_root_handles = []

    try:
        cmds.undoInfo(openChunk=True, chunkName="Create Skeleton Variants")
        opened = True

        for group in groups:
            duplicate_handles = {}

            for source in group["sources"]:
                duplicate_result = cmds.duplicate(
                    source,
                    parentOnly=True,
                    name=group["targets"][source]
                ) or []

                if not duplicate_result:
                    raise RuntimeError(
                        'Maya could not duplicate "{}".'
                        .format(short_name(source))
                    )

                duplicate = duplicate_result[0]
                duplicate_handle = get_object_handle(duplicate)

                if source != group["root"]:
                    source_parent = cmds.listRelatives(
                        source,
                        parent=True,
                        fullPath=True
                    ) or []

                    if not source_parent or source_parent[0] not in duplicate_handles:
                        raise RuntimeError(
                            'Could not rebuild the joint hierarchy at "{}".'
                            .format(short_name(source))
                        )

                    duplicate_parent = path_from_handle(
                        duplicate_handles[source_parent[0]]
                    )
                    duplicate_path = path_from_handle(duplicate_handle)

                    cmds.parent(
                        duplicate_path,
                        duplicate_parent,
                        relative=True
                    )

                duplicate_path = path_from_handle(duplicate_handle)
                if not duplicate_path or short_name(duplicate_path) != group["targets"][source]:
                    raise RuntimeError(
                        'Maya could not apply the exact name "{}".'
                        .format(group["targets"][source])
                    )

                duplicate_handles[source] = duplicate_handle

                if source == group["root"]:
                    created_root_handles.append(duplicate_handle)

    except Exception as error:
        if opened:
            cmds.undoInfo(closeChunk=True)
            opened = False
            try:
                cmds.undo()
            except Exception:
                pass

        QtWidgets.QMessageBox.warning(
            parent or maya_main_window(),
            "Copy failed",
            "The operation was rolled back. {}".format(error)
        )
        return False

    finally:
        if opened:
            cmds.undoInfo(closeChunk=True)

    created_roots = [
        path_from_handle(handle)
        for handle in created_root_handles
        if path_from_handle(handle)
    ]

    tool = getattr(builtins, TOOL_KEY, None)
    was_processing = tool.processing if tool else False
    try:
        if tool:
            tool.processing = True
        if created_roots:
            cmds.select(created_roots, replace=True)
        if tool:
            selected = tool.get_selected_joint()
            if selected:
                tool.remember_selection(selected)
            else:
                tool.selected_handle = None
                tool.selected_name = None
    finally:
        if tool:
            tool.processing = was_processing

    cmds.inViewMessage(
        amg="<hl>Created {} joints</hl>".format(joint_count),
        pos="topCenter",
        fade=True
    )
    return True


def make_skin_skeleton(cog, parent=None):
    """Make a full SKIN copy of the COG joint hierarchy."""

    return duplicate_joint_roots_as_types(
        [("skin", [cog])],
        parent
    )


def make_ik_fk_limbs(cog, parent=None):
    """Make IK and FK copies of detected forelimb and hindlimb branches."""

    analyzer = SkeletonAnalyzer(cog)
    mapping = analyzer.analyze()
    forelimbs = []
    hindlimbs = []

    for joint, data in mapping.items():
        if data["part"] == analyzer.ikfk_arm_root:
            forelimbs.append(joint)
        elif data["part"] == analyzer.ikfk_leg_root:
            hindlimbs.append(joint)

    if not forelimbs or not hindlimbs:
        QtWidgets.QMessageBox.warning(
            parent or maya_main_window(),
            "Limbs not detected",
            "The selected mode must detect at least one forelimb and one hindlimb."
        )
        return False

    roots = forelimbs + hindlimbs
    return duplicate_joint_roots_as_types(
        [("ik", roots), ("fk", roots)],
        parent
    )


class SkeletonNamingAssistantWindow(QtWidgets.QDialog):
    """Small control panel for the listener and whole-skeleton preview."""

    def __init__(self, parent=None):

        super(SkeletonNamingAssistantWindow, self).__init__(parent)

        self.manual_cog_handle = None

        self.setWindowTitle("Skeleton Naming Assistant")
        self.setMinimumWidth(360)

        layout = QtWidgets.QVBoxLayout(self)
        self.status_label = QtWidgets.QLabel(self)
        layout.addWidget(self.status_label)

        self.listener_checkbox = QtWidgets.QCheckBox(
            "Enable background listener",
            self
        )
        self.listener_checkbox.setChecked(
            getattr(builtins, TOOL_KEY, None) is not None
        )
        self.listener_checkbox.toggled.connect(self.set_listener_enabled)
        layout.addWidget(self.listener_checkbox)

        self.silent_checkbox = QtWidgets.QCheckBox(
            "Silent listener (apply names without prompts)",
            self
        )
        self.silent_checkbox.setChecked(silent_rename_enabled())
        self.silent_checkbox.toggled.connect(self.set_silent_mode)
        layout.addWidget(self.silent_checkbox)

        anatomy_options = QtWidgets.QFormLayout()
        self.anatomy_combo = QtWidgets.QComboBox(self)
        self.anatomy_combo.addItem("Biped", "biped")
        self.anatomy_combo.addItem("Quadruped", "quadruped")
        self.anatomy_combo.setCurrentIndex(
            self.anatomy_combo.findData(current_anatomy_mode())
        )
        self.anatomy_combo.currentIndexChanged.connect(
            self.set_anatomy_mode_from_ui
        )
        anatomy_options.addRow("Naming mode", self.anatomy_combo)
        layout.addLayout(anatomy_options)

        self.side_checkbox = QtWidgets.QCheckBox(
            "Positive world X is the character's left side",
            self
        )
        self.side_checkbox.setChecked(positive_x_is_left())
        self.side_checkbox.toggled.connect(set_positive_x_is_left)
        layout.addWidget(self.side_checkbox)

        self.forward_checkbox = QtWidgets.QCheckBox(
            "Positive world Z points forward (quadruped)",
            self
        )
        self.forward_checkbox.setChecked(positive_z_is_forward())
        self.forward_checkbox.toggled.connect(set_positive_z_forward)
        layout.addWidget(self.forward_checkbox)

        self.cog_label = QtWidgets.QLabel("COG: not set", self)
        layout.addWidget(self.cog_label)

        self.set_cog_button = QtWidgets.QPushButton(
            "Use selected joint as COG",
            self
        )
        self.preview_button = QtWidgets.QPushButton(
            "Preview skeleton names...",
            self
        )
        self.make_skin_button = QtWidgets.QPushButton("Make SKIN", self)
        self.make_ik_fk_button = QtWidgets.QPushButton("Make IK/FK", self)

        layout.addWidget(self.set_cog_button)
        layout.addWidget(self.preview_button)
        layout.addWidget(self.make_skin_button)
        layout.addWidget(self.make_ik_fk_button)

        self.set_cog_button.clicked.connect(self.set_cog_from_selection)
        self.preview_button.clicked.connect(self.preview)
        self.make_skin_button.clicked.connect(self.make_skin)
        self.make_ik_fk_button.clicked.connect(self.make_ik_fk)

        self.refresh()

    def current_cog(self):

        tool = getattr(builtins, TOOL_KEY, None)
        if tool:
            cog = path_from_handle(tool.cog_handle)
            if cog:
                return cog

        return path_from_handle(self.manual_cog_handle)

    def refresh(self):

        tool = getattr(builtins, TOOL_KEY, None)
        self.status_label.setText(
            "Background listener: {}. UI tools work in either mode."
            .format("on" if tool else "off")
        )

        self.listener_checkbox.blockSignals(True)
        self.listener_checkbox.setChecked(tool is not None)
        self.listener_checkbox.blockSignals(False)

        self.silent_checkbox.blockSignals(True)
        self.silent_checkbox.setChecked(silent_rename_enabled())
        self.silent_checkbox.blockSignals(False)

        self.anatomy_combo.blockSignals(True)
        self.anatomy_combo.setCurrentIndex(
            self.anatomy_combo.findData(current_anatomy_mode())
        )
        self.anatomy_combo.blockSignals(False)

        self.forward_checkbox.blockSignals(True)
        self.forward_checkbox.setChecked(positive_z_is_forward())
        self.forward_checkbox.blockSignals(False)

        cog = self.current_cog()
        self.cog_label.setText(
            "COG: {}".format(short_name(cog)) if cog else "COG: not set"
        )

    def set_cog_from_selection(self):

        selection = cmds.ls(selection=True, long=True, type="joint") or []
        if len(selection) != 1:
            QtWidgets.QMessageBox.information(
                self,
                "Select one joint",
                "Select exactly one joint to use as the COG."
            )
            return

        self.manual_cog_handle = get_object_handle(selection[0])
        tool = getattr(builtins, TOOL_KEY, None)
        if tool:
            tool.set_cog(selection[0])

        self.refresh()

    def preview(self):

        show_rename_preview(self.current_cog(), self)
        self.refresh()

    def set_listener_enabled(self, enabled):

        set_skeleton_naming_assistant_enabled(
            enabled,
            self.manual_cog_handle
        )
        self.refresh()

    def set_silent_mode(self, enabled):

        set_silent_rename_enabled(enabled)
        tool = getattr(builtins, TOOL_KEY, None)
        if tool:
            tool.silent_rename = bool(enabled)

        self.refresh()

    def set_anatomy_mode_from_ui(self, index):

        mode = self.anatomy_combo.itemData(index)
        if mode:
            set_anatomy_mode(mode)
        self.refresh()

    def make_skin(self):

        cog = self.current_cog()
        if not cog:
            QtWidgets.QMessageBox.information(
                self,
                "COG not set",
                "Set the COG joint before making a SKIN copy."
            )
            return

        make_skin_skeleton(cog, self)
        self.refresh()

    def make_ik_fk(self):

        cog = self.current_cog()
        if not cog:
            QtWidgets.QMessageBox.information(
                self,
                "COG not set",
                "Set the COG joint before making IK/FK copies."
            )
            return

        make_ik_fk_limbs(cog, self)
        self.refresh()


def center_skeleton_naming_window(window):
    """Center the control panel over Maya when it is first opened."""

    parent = window.parentWidget() or maya_main_window()
    if not parent:
        return

    window.adjustSize()
    frame = window.frameGeometry()
    frame.moveCenter(parent.frameGeometry().center())
    window.move(frame.topLeft())


def show_skeleton_naming_assistant_ui():
    """Show or raise the naming assistant control panel."""

    window = getattr(builtins, TOOL_WINDOW_KEY, None)
    manual_cog = None

    try:
        if window and isinstance(window, SkeletonNamingAssistantWindow):
            window.refresh()
            window.show()
            center_skeleton_naming_window(window)
            window.raise_()
            window.activateWindow()
            return window
        elif window:
            manual_cog = path_from_handle(
                getattr(window, "manual_cog_handle", None)
            )
            window.close()
    except RuntimeError:
        pass

    window = SkeletonNamingAssistantWindow(maya_main_window())
    if manual_cog:
        window.manual_cog_handle = get_object_handle(manual_cog)
    setattr(builtins, TOOL_WINDOW_KEY, window)
    window.show()
    center_skeleton_naming_window(window)
    return window


# ------------------------------------------------------------
# Main listener
# ------------------------------------------------------------

class SkeletonNamingAssistant(object):

    def __init__(self):

        self.selection_job = None
        self.rename_job = None

        self.processing = False
        self.silent_rename = silent_rename_enabled()

        self.selected_handle = None
        self.selected_name = None

        self.cog_handle = None

        self.first_cog_pending = True

        self.last_prompted_joint = None
        self.last_prompted_name = None

    # --------------------------------------------------------
    # Lifecycle
    # --------------------------------------------------------

    def start(self):

        self.selection_job = cmds.scriptJob(
            event=[
                "SelectionChanged",
                self.on_selection_changed
            ],
            protected=True
        )

        self.rename_job = cmds.scriptJob(
            event=[
                "NameChanged",
                self.on_name_changed
            ],
            protected=True
        )

        cmds.inViewMessage(
            amg='<hl>Skeleton Naming Assistant: ON</hl>',
            pos='topCenter',
            fade=True
        )

        print("Skeleton Naming Assistant: ON")

    def stop(self):

        for job in [
            self.selection_job,
            self.rename_job
        ]:

            if job:
                if cmds.scriptJob(exists=job):
                    cmds.scriptJob(
                        kill=job,
                        force=True
                    )

        self.selection_job = None
        self.rename_job = None

        cmds.inViewMessage(
            amg='<hl>Skeleton Naming Assistant: OFF</hl>',
            pos='topCenter',
            fade=True
        )

        print("Skeleton Naming Assistant: OFF")

    # --------------------------------------------------------
    # Selection tracking
    # --------------------------------------------------------

    def get_selected_joint(self):

        selection = cmds.ls(
            selection=True,
            long=True,
            type="joint"
        ) or []

        if len(selection) != 1:
            return None

        return selection[0]

    def remember_selection(self, joint):

        try:

            self.selected_handle = get_object_handle(
                joint
            )

            self.selected_name = short_name(joint)

        except Exception:

            self.selected_handle = None
            self.selected_name = None

    # --------------------------------------------------------
    # COG detection
    # --------------------------------------------------------

    def is_plausible_cog(self, joint):
        """
        Determine whether the first selected joint is a plausible COG.

        Exactly three joint children is considered the strongest case.
        """

        children = get_joint_children(joint)

        if len(children) == 3:
            return True

        if len(children) < 2:
            return False

        if len(children) > 5:
            return False

        parent_pos = get_world_position(joint)

        above = 0
        below = 0

        positive_x = 0
        negative_x = 0

        for child in children:

            position = get_world_position(child)

            if position.y > parent_pos.y:
                above += 1
            else:
                below += 1

            if position.x > parent_pos.x:
                positive_x += 1
            elif position.x < parent_pos.x:
                negative_x += 1

        return (
            above >= 1 and
            below >= 1 and
            positive_x >= 1 and
            negative_x >= 1
        )

    def set_cog(self, joint):

        self.cog_handle = get_object_handle(
            joint
        )

        self.first_cog_pending = False

        cmds.inViewMessage(
            amg='<hl>COG detected</hl>',
            pos='topCenter',
            fade=True
        )

        window = getattr(builtins, TOOL_WINDOW_KEY, None)
        if window:
            try:
                window.refresh()
            except RuntimeError:
                pass

    # --------------------------------------------------------
    # Anatomical prediction
    # --------------------------------------------------------

    def get_prediction(self, joint):

        cog = path_from_handle(
            self.cog_handle
        )

        if not cog:
            return None

        analyzer = SkeletonAnalyzer(cog)

        mapping = analyzer.analyze()

        return mapping.get(joint)

    def suggest_anatomical_name(self, joint):

        prediction = self.get_prediction(
            joint
        )

        if not prediction:
            return

        current_name = short_name(joint)

        # Avoid repeatedly questioning already meaningful custom names.
        if not is_generic_name(current_name):
            return

        character = get_character_name(
            joint
        )

        new_name = build_rig_name(
            character,
            prediction["part"],
            prediction["side"]
        )

        if current_name == new_name:
            return

        prompt_key = (
            current_name,
            new_name
        )

        if self.last_prompted_joint == joint:
            if self.last_prompted_name == prompt_key:
                return

        self.last_prompted_joint = joint
        self.last_prompted_name = prompt_key

        self.rename_with_confirmation(
            joint,
            new_name
        )

    # --------------------------------------------------------
    # Rename
    # --------------------------------------------------------

    def rename_with_confirmation(
        self,
        joint,
        new_name
    ):

        if not cmds.objExists(joint):
            return False

        old_name = short_name(joint)

        conflicts = [
            node
            for node in exact_scene_name_matches(new_name)
            if node != joint
        ]

        if new_name != old_name and conflicts:
            cmds.warning(
                'Skeleton Naming Assistant skipped "{}": "{}" already exists.'
                .format(old_name, new_name)
            )
            return False

        question = (
            'Change joint name "{}" to "{}" ?'
        ).format(
            old_name,
            new_name
        )

        if not self.silent_rename and not ask_yes_no(question):
            return False

        self.processing = True

        try:

            renamed = cmds.rename(
                joint,
                new_name
            )

            self.remember_selection(
                renamed
            )

            return True

        finally:

            self.processing = False

    # --------------------------------------------------------
    # Original end-joint logic
    # --------------------------------------------------------

    def check_end_joint(self, joint):

        if not cmds.objExists(joint):
            return

        children = get_joint_children(
            joint
        )

        if len(children) != 1:
            return

        child = children[0]

        grandchildren = get_joint_children(
            child
        )

        if grandchildren:
            return

        parent_name = short_name(joint)
        child_name = short_name(child)

        new_name = parent_name + "_end"

        if child_name == new_name:
            return

        self.rename_with_confirmation(
            child,
            new_name
        )

    # --------------------------------------------------------
    # Sequential numbering
    # --------------------------------------------------------

    def get_numbered_name_data(self, name):

        match = re.match(
            r"^(.*)_(\d+)$",
            name
        )

        if not match:
            return None

        return (
            match.group(1),
            int(match.group(2)),
            len(match.group(2))
        )

    def choose_numbering_child(self, joint):

        children = get_joint_children(
            joint
        )

        if not children:
            return None

        if len(children) == 1:
            return children[0]

        # Prefer children that continue a moderate chain rather than
        # simply selecting the branch with the most descendants.
        candidates = []

        for child in children:

            depth = descendant_depth(child)

            branch_count = len(
                get_joint_children(child)
            )

            score = 0.0

            if depth <= 4:
                score += 3.0
            elif depth <= 7:
                score += 1.0
            else:
                score -= 1.0

            if branch_count <= 1:
                score += 2.0
            elif branch_count == 2:
                score += 0.5
            else:
                score -= 1.0

            candidates.append(
                (score, child)
            )

        candidates.sort(
            key=lambda item: item[0],
            reverse=True
        )

        if len(candidates) > 1:

            if abs(
                candidates[0][0] -
                candidates[1][0]
            ) < 0.75:

                return None

        return candidates[0][1]

    def continue_numbering(self, joint):

        current_name = short_name(joint)

        data = self.get_numbered_name_data(
            current_name
        )

        if not data:
            return

        base_name, number, digits = data

        first_child = self.choose_numbering_child(
            joint
        )

        if not first_child:
            return

        question = (
            'Continue numbering child joints from "{}" ?'
        ).format(
            current_name
        )

        if not self.silent_rename and not ask_yes_no(question):
            return

        self.processing = True

        try:

            current = joint
            current_number = number

            while True:

                child = self.choose_numbering_child(
                    current
                )

                if not child:
                    break

                current_number += 1

                number_text = str(
                    current_number
                ).zfill(digits)

                new_name = "{}_{}".format(
                    base_name,
                    number_text
                )

                if exact_scene_name_matches(new_name):
                    cmds.warning(
                        'Skeleton Naming Assistant stopped numbering: "{}" already exists.'
                        .format(new_name)
                    )
                    break

                current = cmds.rename(
                    child,
                    new_name
                )

        finally:

            self.processing = False

            selected = self.get_selected_joint()

            if selected:
                self.remember_selection(
                    selected
                )

    # --------------------------------------------------------
    # Events
    # --------------------------------------------------------

    def on_selection_changed(self):

        if self.processing:
            return

        joint = self.get_selected_joint()

        if not joint:
            self.selected_handle = None
            self.selected_name = None
            return

        self.remember_selection(
            joint
        )

        # The first plausible joint selected after activation becomes COG.
        if self.first_cog_pending:

            if self.is_plausible_cog(joint):

                self.set_cog(
                    joint
                )

                character = get_character_name(
                    joint
                )

                expected_name = build_rig_name(
                    character,
                    "cog"
                )

                if short_name(joint) != expected_name:

                    self.rename_with_confirmation(
                        joint,
                        expected_name
                    )

                return

        # Once COG is known, selected joints can receive predictions.
        if self.cog_handle:

            cmds.evalDeferred(
                lambda: self.process_prediction()
            )

    def process_prediction(self):

        if self.processing:
            return

        joint = self.get_selected_joint()

        if not joint:
            return

        self.suggest_anatomical_name(
            joint
        )

    def on_name_changed(self):

        if self.processing:
            return

        if not self.selected_handle:
            return

        joint = path_from_handle(
            self.selected_handle
        )

        if not joint:
            return

        current_name = short_name(
            joint
        )

        # Ignore rename events caused by other scene objects.
        if current_name == self.selected_name:
            return

        self.selected_name = current_name

        # Detect manually created numbered chains.
        if self.get_numbered_name_data(
            current_name
        ):

            self.continue_numbering(
                joint
            )

        # Preserve the original automatic end-joint behavior.
        self.check_end_joint(
            joint
        )


# ------------------------------------------------------------
# Toggle
# ------------------------------------------------------------

def set_skeleton_naming_assistant_enabled(enabled, manual_cog_handle=None):

    set_listener_preference(enabled)

    existing = getattr(
        builtins,
        TOOL_KEY,
        None
    )

    if enabled and existing is None:
        existing = SkeletonNamingAssistant()
        setattr(builtins, TOOL_KEY, existing)
        existing.start()

    elif not enabled and existing is not None:
        window = getattr(builtins, TOOL_WINDOW_KEY, None)
        if window and not manual_cog_handle:
            cog = path_from_handle(existing.cog_handle)
            if cog:
                window.manual_cog_handle = get_object_handle(cog)

        existing.stop()
        delattr(builtins, TOOL_KEY)
        existing = None

    if existing and manual_cog_handle:
        cog = path_from_handle(manual_cog_handle)
        if cog:
            existing.set_cog(cog)

    window = getattr(builtins, TOOL_WINDOW_KEY, None)
    if window:
        try:
            window.refresh()
            window.show()
            window.raise_()
            window.activateWindow()
        except RuntimeError:
            pass

    return existing


def toggle_skeleton_naming_assistant():

    enabled = getattr(builtins, TOOL_KEY, None) is None
    return set_skeleton_naming_assistant_enabled(enabled)


if getattr(builtins, TOOL_KEY, None) is None and listener_enabled_by_default():
    set_skeleton_naming_assistant_enabled(True)

show_skeleton_naming_assistant_ui()
