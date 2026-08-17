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
    from PySide6 import QtWidgets
    from shiboken6 import wrapInstance
except ImportError:
    from PySide2 import QtWidgets
    from shiboken2 import wrapInstance


# ------------------------------------------------------------
# Configuration
# ------------------------------------------------------------

TOOL_KEY = "_maya_skeleton_naming_assistant"

NAME_MIDDLE = "_jnt_rig_"

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


# Only suggest anatomical names automatically for names that look generic.
# This prevents the tool from constantly questioning custom names.
GENERIC_NAME_PATTERNS = [
    r"^joint\d*$",
    r"^joint_\d+$",
    r"^jnt\d*$",
    r"^bone\d*$",
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

    clean_name = short_name(name)

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
        return "charName"

    name = short_name(root)

    if name.upper().endswith("_ROOT"):
        return name[:-5]

    return name


# ------------------------------------------------------------
# Name generation
# ------------------------------------------------------------

def build_rig_name(character, part, side=None):
    """
    Build a rig joint name.

    Examples:
        character_jnt_rig_clavicle_left
        character_jnt_rig_ball_left_end
        character_jnt_rig_head_end
    """

    if part.endswith("_end"):

        base_part = part[:-4]

        if side:
            return "{}{}{}_{}_end".format(
                character,
                NAME_MIDDLE,
                base_part,
                side
            )

        return "{}{}{}_end".format(
            character,
            NAME_MIDDLE,
            base_part
        )

    if side:
        return "{}{}{}_{}".format(
            character,
            NAME_MIDDLE,
            part,
            side
        )

    return "{}{}{}".format(
        character,
        NAME_MIDDLE,
        part
    )


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


def ask_yes_no(text):
    """Show a Y/N confirmation dialog."""

    dialog = YesNoDialog(
        text,
        maya_main_window()
    )

    return dialog.exec() == QtWidgets.QMessageBox.Yes


# ------------------------------------------------------------
# Skeleton analysis
# ------------------------------------------------------------

class SkeletonAnalyzer(object):

    def __init__(self, cog):

        self.cog = cog

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
        """Score a child that should continue upward near the center."""

        parent_pos = get_world_position(parent)
        child_pos = get_world_position(child)

        delta = child_pos - parent_pos

        length = max(delta.length(), 0.0001)

        up = delta.y / length
        side_motion = abs(delta.x) / length

        score = 0.0

        score += up * 4.0
        score -= side_motion * 3.0

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

        if side == "left":
            return x_direction * 4.0

        return -x_direction * 4.0

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

        cog_children = get_joint_children(self.cog)

        spine = self.choose_candidate(
            self.cog,
            cog_children,
            expected_depth=4,
            direction_mode="up",
            minimum_score=1.0
        )

        if not spine:
            return None, None

        self.assign(
            spine,
            "spine"
        )

        spine_children = get_joint_children(spine)

        chest = self.choose_candidate(
            spine,
            spine_children,
            expected_depth=3,
            direction_mode="up",
            minimum_score=-1.0
        )

        if not chest:
            return spine, None

        self.assign(
            chest,
            "chest"
        )

        chest_children = get_joint_children(chest)

        neck = self.choose_candidate(
            chest,
            chest_children,
            expected_depth=2,
            direction_mode="up",
            minimum_score=0.5
        )

        if neck:

            self.assign(
                neck,
                "neck"
            )

            neck_children = get_joint_children(neck)

            head = self.choose_candidate(
                neck,
                neck_children,
                expected_depth=1,
                direction_mode="up",
                minimum_score=-1.0
            )

            if head:

                self.assign(
                    head,
                    "head"
                )

                head_children = get_joint_children(head)

                head_end = self.choose_candidate(
                    head,
                    head_children,
                    expected_depth=0,
                    direction_mode="up",
                    minimum_score=-2.0
                )

                if head_end:

                    self.assign(
                        head_end,
                        "head_end"
                    )

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

            if position.x > cog_pos.x:
                left_candidates.append(joint)

            elif position.x < cog_pos.x:
                right_candidates.append(joint)

        left_hip = self.choose_candidate(
            self.cog,
            left_candidates,
            expected_depth=4,
            direction_mode="down",
            side="left",
            minimum_score=0.0
        )

        right_hip = self.choose_candidate(
            self.cog,
            right_candidates,
            expected_depth=4,
            direction_mode="down",
            side="right",
            minimum_score=0.0
        )

        if left_hip:

            self.trace_chain(
                left_hip,
                LEG_CHAIN,
                side="left",
                direction_mode="down"
            )

        if right_hip:

            self.trace_chain(
                right_hip,
                LEG_CHAIN,
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
                if data["part"] == "neck":
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

            if position.x > chest_pos.x:
                left_candidates.append(joint)

            elif position.x < chest_pos.x:
                right_candidates.append(joint)

        left_clavicle = self.choose_candidate(
            chest,
            left_candidates,
            expected_depth=3,
            direction_mode="lateral",
            side="left",
            minimum_score=0.0
        )

        right_clavicle = self.choose_candidate(
            chest,
            right_candidates,
            expected_depth=3,
            direction_mode="lateral",
            side="right",
            minimum_score=0.0
        )

        if left_clavicle:

            self.trace_chain(
                left_clavicle,
                ARM_CHAIN,
                side="left",
                direction_mode="lateral"
            )

        if right_clavicle:

            self.trace_chain(
                right_clavicle,
                ARM_CHAIN,
                side="right",
                direction_mode="lateral"
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
# Main listener
# ------------------------------------------------------------

class SkeletonNamingAssistant(object):

    def __init__(self):

        self.selection_job = None
        self.rename_job = None

        self.processing = False

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

        question = (
            'Change joint name "{}" to "{}" ?'
        ).format(
            old_name,
            new_name
        )

        if not ask_yes_no(question):
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

        if not ask_yes_no(question):
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

def toggle_skeleton_naming_assistant():

    existing = getattr(
        builtins,
        TOOL_KEY,
        None
    )

    if existing is not None:

        existing.stop()

        delattr(
            builtins,
            TOOL_KEY
        )

        return

    tool = SkeletonNamingAssistant()

    setattr(
        builtins,
        TOOL_KEY,
        tool
    )

    tool.start()


toggle_skeleton_naming_assistant()