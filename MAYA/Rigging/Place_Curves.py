import maya.cmds as cmds


# ------------------------------------------------------------
# Controller settings
# ------------------------------------------------------------

COLOR_BLUE = 6
COLOR_RED = 13
COLOR_YELLOW = 17


RADIUS_10 = {
    "hip",
    "cog",
    "spine",
    "chest",
    "head"
}

RADIUS_5 = {
    "neck",
    "clavicle",
    "shoulder",
    "elbow",
    "wrist",
    "knee",
    "ankle",
    "ball"
}

RADIUS_1 = {
    "thumb",
    "index",
    "middle",
    "ring",
    "pinky"
}


# ------------------------------------------------------------
# Name utilities
# ------------------------------------------------------------

def get_short_name(node):
    """Return the short DAG name."""

    return node.split("|")[-1]


def get_ctrl_name(joint_name):
    """Generate controller name from joint name."""

    name = get_short_name(joint_name)

    # Example:
    # charName_jnt_rig_clavicle_left
    # ->
    # charName_ctrl_clavicle_left

    name = name.replace("_jnt_rig_", "_ctrl_")

    # Fallback for other naming cases
    name = name.replace("_jnt_", "_ctrl_")
    name = name.replace("_rig_", "_")

    return name


def get_joint_type(joint_name):
    """
    Detect the anatomical joint type from the joint name.

    Example:
        charName_jnt_rig_clavicle_left
        -> clavicle

        charName_jnt_rig_index_left_01
        -> index
    """

    name = get_short_name(joint_name).lower()

    all_types = (
        RADIUS_10 |
        RADIUS_5 |
        RADIUS_1
    )

    # Split the name into individual tokens
    tokens = name.split("_")

    for token in tokens:
        if token in all_types:
            return token

    return None


# ------------------------------------------------------------
# Radius
# ------------------------------------------------------------

def get_ctrl_radius(joint_name):
    """Return controller radius based on anatomical joint type."""

    joint_type = get_joint_type(joint_name)

    if joint_type in RADIUS_10:
        return 10.0

    if joint_type in RADIUS_5:
        return 5.0

    if joint_type in RADIUS_1:
        return 1.0

    # Default radius
    return 1.0


# ------------------------------------------------------------
# Color
# ------------------------------------------------------------

def get_ctrl_color(joint_name):
    """Return controller color based on joint side."""

    name = get_short_name(joint_name).lower()

    tokens = name.split("_")

    if "left" in tokens:
        return COLOR_BLUE

    if "right" in tokens:
        return COLOR_RED

    return COLOR_YELLOW


def set_curve_color(ctrl, color_index):
    """Set viewport override color on all curve shapes."""

    shapes = cmds.listRelatives(
        ctrl,
        shapes=True,
        fullPath=True
    ) or []

    for shape in shapes:

        cmds.setAttr(
            shape + ".overrideEnabled",
            1
        )

        cmds.setAttr(
            shape + ".overrideColor",
            color_index
        )


# ------------------------------------------------------------
# Controller creation
# ------------------------------------------------------------

def create_ctrl_for_joint(joint):
    """Create one circle controller for a joint."""

    ctrl_name = get_ctrl_name(joint)

    joint_type = get_joint_type(joint)
    radius = get_ctrl_radius(joint)
    color = get_ctrl_color(joint)

    # Debug information
    print(
        "Joint: {} | Type: {} | Radius: {}".format(
            get_short_name(joint),
            joint_type,
            radius
        )
    )

    # Create circle with the correct radius
    ctrl = cmds.circle(
        name=ctrl_name,
        normal=(1, 0, 0),
        radius=radius,
        constructionHistory=False
    )[0]

    # Match both position and rotation to the joint
    cmds.matchTransform(
        ctrl,
        joint,
        position=True,
        rotation=True
    )

    set_curve_color(
        ctrl,
        color
    )

    return ctrl


# ------------------------------------------------------------
# Main
# ------------------------------------------------------------

def create_controls_from_selection():
    """Create controllers for all selected joints."""

    selection = cmds.ls(
        selection=True,
        long=True
    ) or []

    joints = []

    for node in selection:

        if cmds.nodeType(node) == "joint":
            joints.append(node)

    if not joints:

        cmds.warning(
            "Please select one or more joints."
        )

        return

    created_controls = []

    for joint in joints:

        ctrl = create_ctrl_for_joint(
            joint
        )

        created_controls.append(
            ctrl
        )

    # Select all created controllers
    cmds.select(
        created_controls,
        replace=True
    )

    cmds.inViewMessage(
        amg='<hl>Created {} controllers</hl>'.format(
            len(created_controls)
        ),
        pos='topCenter',
        fade=True
    )


create_controls_from_selection()
