"""Create a staged three-control S-curve rig for Maya.

Select the original controls and run this file. Numbered controls are sorted
from the lowest number to the highest. The first pass creates only Root, Mid,
and Tip placement controls. Set Build Now to 1 on any placement control and
confirm the dialog to create the driver groups and constraints.
"""

from __future__ import division

import functools
import math
import re

import maya.cmds as cmds


for _old_job_ids in list(globals().get("_SCRIPT_JOBS", {}).values()):
    for _old_job_id in _old_job_ids:
        try:
            if cmds.scriptJob(exists=_old_job_id):
                cmds.scriptJob(kill=_old_job_id, force=True)
        except Exception:
            pass

_BUILD_GUARD = False
_SCRIPT_JOBS = {}
_PENDING_BUILDS = set()


def _short_name(node):
    return node.rsplit("|", 1)[-1].split(":")[-1]


def _safe_name(value):
    value = re.sub(r"[^A-Za-z0-9_]", "_", value.strip())
    value = re.sub(r"_+", "_", value).strip("_")
    if not value:
        value = "hairCloth"
    if value[0].isdigit():
        value = "rig_" + value
    return value


def _control_number(node):
    numbers = re.findall(r"\d+", _short_name(node))
    if not numbers:
        return None
    return int(numbers[-1])


def _unique_prefix(prefix):
    if not cmds.objExists(prefix + "_RIG_GRP"):
        return prefix
    index = 1
    while cmds.objExists("{}{}_RIG_GRP".format(prefix, index)):
        index += 1
    return "{}{}".format(prefix, index)


def _selected_transforms():
    selection = cmds.ls(orderedSelection=True, long=True) or []
    result = []
    for node in selection:
        if not cmds.objExists(node):
            continue
        if cmds.nodeType(node) != "transform":
            parents = cmds.listRelatives(node, parent=True, fullPath=True) or []
            if not parents:
                continue
            node = parents[0]
        if node not in result:
            result.append(node)

    numbered = [(_control_number(node), index, node)
                for index, node in enumerate(result)]
    if result and all(item[0] is not None for item in numbered):
        numbered.sort(key=lambda item: (item[0], item[1]))
        result = [item[2] for item in numbered]
    return result


def _node_uuid(node):
    values = cmds.ls(node, uuid=True) or []
    if not values:
        cmds.error("Could not read a stable ID for: {}".format(node))
    return values[0]


def _node_from_uuid(node_uuid):
    values = cmds.ls(node_uuid, long=True) or []
    if not values:
        cmds.error("Could not resolve a rig node after hierarchy changes.")
    return values[0]


def _world_matrix(node):
    return cmds.xform(node, query=True, worldSpace=True, matrix=True)


def _world_position(node):
    return cmds.xform(node, query=True, worldSpace=True, translation=True)


def _distance(point_a, point_b):
    return math.sqrt(sum((a - b) ** 2 for a, b in zip(point_a, point_b)))


def _auto_radius(controls):
    positions = [_world_position(control) for control in controls]
    distances = [
        _distance(positions[index], positions[index + 1])
        for index in range(len(positions) - 1)
    ]
    useful = [value for value in distances if value > 0.0001]
    if not useful:
        return 1.0
    return max(sum(useful) / len(useful) * 0.65, 0.01)


def _mid_matrix(controls, matrices):
    count = len(controls)
    if count % 2 == 1:
        return list(matrices[count // 2])

    lower_index = (count // 2) - 1
    upper_index = count // 2
    lower_position = _world_position(controls[lower_index])
    upper_position = _world_position(controls[upper_index])
    midpoint = [
        (lower_position[axis] + upper_position[axis]) * 0.5
        for axis in range(3)
    ]
    matrix = list(matrices[lower_index])
    matrix[12:15] = midpoint
    return matrix


def _rectangle_points(radius, normal_axis):
    long_side = radius * 1.35
    short_side = radius * 0.42
    if normal_axis == "Y":
        return [
            (-long_side, 0.0, -short_side),
            (-long_side, 0.0, short_side),
            (long_side, 0.0, short_side),
            (long_side, 0.0, -short_side),
            (-long_side, 0.0, -short_side),
        ]
    if normal_axis == "Z":
        return [
            (-long_side, -short_side, 0.0),
            (-long_side, short_side, 0.0),
            (long_side, short_side, 0.0),
            (long_side, -short_side, 0.0),
            (-long_side, -short_side, 0.0),
        ]
    return [
        (0.0, -long_side, -short_side),
        (0.0, -long_side, short_side),
        (0.0, long_side, short_side),
        (0.0, long_side, -short_side),
        (0.0, -long_side, -short_side),
    ]


def _set_shape_color(control, color_index):
    for shape in cmds.listRelatives(control, shapes=True, fullPath=True) or []:
        cmds.setAttr(shape + ".overrideEnabled", 1)
        cmds.setAttr(shape + ".overrideColor", color_index)


def _create_rectangle_control(name, matrix, radius, normal_axis, color):
    zero = cmds.createNode("transform", name=name.replace("_CTRL", "_ZERO"))
    cmds.xform(zero, worldSpace=True, matrix=matrix)
    control = cmds.curve(
        name=name,
        degree=1,
        point=_rectangle_points(radius, normal_axis),
    )
    cmds.parent(control, zero, relative=True)
    cmds.makeIdentity(control, apply=True, translate=True, rotate=True, scale=True)
    _set_shape_color(control, color)
    return zero, control


def _add_build_attribute(control):
    cmds.addAttr(
        control,
        longName="__________",
        attributeType="enum",
        enumName="CONTROLS",
        keyable=True,
    )
    cmds.setAttr(control + ".__________", lock=True)
    cmds.addAttr(
        control,
        longName="BuildNow",
        niceName="Build Now",
        attributeType="long",
        minValue=0,
        maxValue=1,
        defaultValue=0,
        keyable=True,
    )


def _add_metadata(root_control, driven_controls, section_controls, prefix):
    cmds.addAttr(root_control, longName="rigType", dataType="string")
    cmds.setAttr(root_control + ".rigType", "HairClothSCurve", type="string", lock=True)
    cmds.addAttr(root_control, longName="rigPrefix", dataType="string")
    cmds.setAttr(root_control + ".rigPrefix", prefix, type="string", lock=True)
    cmds.addAttr(root_control, longName="buildState", attributeType="long",
                 minValue=0, maxValue=1, defaultValue=0)
    cmds.addAttr(root_control, longName="drivenControls", attributeType="message", multi=True)
    for index, control in enumerate(driven_controls):
        cmds.connectAttr(
            control + ".message",
            "{}.drivenControls[{}]".format(root_control, index),
        )
    cmds.addAttr(root_control, longName="sectionControls", attributeType="message", multi=True)
    for index, control in enumerate(section_controls[1:], start=1):
        cmds.connectAttr(
            control + ".message",
            "{}.sectionControls[{}]".format(root_control, index),
        )


def _get_driven_controls(root_control):
    indices = cmds.getAttr(root_control + ".drivenControls", multiIndices=True) or []
    controls = []
    for index in sorted(indices):
        connected = cmds.listConnections(
            "{}.drivenControls[{}]".format(root_control, index),
            source=True,
            destination=False,
        ) or []
        if not connected:
            cmds.error("A stored driven control is missing.")
        controls.append(cmds.ls(connected[0], long=True)[0])
    return controls


def _insert_driver_group(control, prefix, index):
    parent = cmds.listRelatives(control, parent=True, fullPath=True) or []
    matrix = _world_matrix(control)
    group_name = "{}_{:02d}_{}_DRV".format(prefix, index + 1, _short_name(control))
    driver = cmds.createNode("transform", name=group_name)
    if parent:
        cmds.parent(driver, parent[0])
    cmds.xform(driver, worldSpace=True, matrix=matrix)
    cmds.parent(control, driver, absolute=True)
    return driver


def _weights_at_parameter(parameter):
    root_weight = max(1.0 - (2.0 * parameter), 0.0)
    mid_weight = max(1.0 - abs((2.0 * parameter) - 1.0), 0.0)
    tip_weight = max((2.0 * parameter) - 1.0, 0.0)
    return root_weight, mid_weight, tip_weight


def _connect_driver(driver, section_controls, prefix, index, parameter):
    constraint = cmds.parentConstraint(
        section_controls,
        driver,
        maintainOffset=True,
        name="{}_{:02d}_SCURVE_PC".format(prefix, index + 1),
    )[0]
    if cmds.attributeQuery("interpType", node=constraint, exists=True):
        cmds.setAttr(constraint + ".interpType", 2)

    aliases = cmds.parentConstraint(constraint, query=True, weightAliasList=True)
    attributes = ("rootWeight", "midWeight", "tipWeight")
    weights = _weights_at_parameter(parameter)
    for attribute, value, alias in zip(attributes, weights, aliases):
        cmds.addAttr(
            driver,
            longName=attribute,
            attributeType="double",
            minValue=0.0,
            maxValue=1.0,
            defaultValue=value,
        )
        cmds.setAttr(driver + "." + attribute, channelBox=True)
        cmds.connectAttr(driver + "." + attribute, constraint + "." + alias)

    cmds.addAttr(
        driver,
        longName="chainParameter",
        attributeType="double",
        minValue=0.0,
        maxValue=1.0,
        defaultValue=parameter,
    )
    cmds.setAttr(driver + ".chainParameter", lock=True, channelBox=True)
    return constraint


def _section_controls(root_control):
    indices = cmds.getAttr(root_control + ".sectionControls", multiIndices=True) or []
    result = [root_control]
    for index in sorted(indices):
        connected = cmds.listConnections(
            "{}.sectionControls[{}]".format(root_control, index),
            source=True,
            destination=False,
        ) or []
        if not connected:
            cmds.error("A placement control is missing.")
        result.append(cmds.ls(connected[0], long=True)[0])
    if len(result) != 3:
        cmds.error("Could not find all three placement controls.")
    return result


def _kill_build_jobs(root_uuid):
    for job_id in _SCRIPT_JOBS.pop(root_uuid, []):
        if cmds.scriptJob(exists=job_id):
            cmds.scriptJob(kill=job_id, force=True)


def _finalize_build(root_control):
    controls = _get_driven_controls(root_control)
    if len(controls) < 3:
        cmds.error("At least three driven controls are required.")
    prefix = cmds.getAttr(root_control + ".rigPrefix")
    section_controls = _section_controls(root_control)
    control_uuids = [_node_uuid(control) for control in controls]

    cmds.undoInfo(openChunk=True, chunkName="Build Hair Cloth S Curve Constraints")
    try:
        control_count = len(control_uuids)
        for index in reversed(range(control_count)):
            control = _node_from_uuid(control_uuids[index])
            parameter = index / float(control_count - 1)
            driver = _insert_driver_group(control, prefix, index)
            _connect_driver(driver, section_controls, prefix, index, parameter)

        cmds.setAttr(root_control + ".buildState", 1)
        for control in section_controls:
            cmds.setAttr(control + ".BuildNow", 1)
            cmds.setAttr(control + ".BuildNow", lock=True)
        cmds.select(root_control, replace=True)
    except Exception:
        cmds.undoInfo(closeChunk=True)
        cmds.undo()
        raise
    else:
        cmds.undoInfo(closeChunk=True)

    cmds.inViewMessage(
        amg="S-curve constraints built for {} controls.".format(len(controls)),
        position="topCenter",
        fade=True,
    )


def _restore_maya_focus():
    try:
        if cmds.waitCursor(query=True, state=True):
            cmds.waitCursor(state=False)
    except Exception:
        pass
    try:
        cmds.setFocus("MayaWindow")
    except Exception:
        pass
    try:
        cmds.refresh(force=True)
    except Exception:
        pass


def _cancel_build_now(trigger_uuid, root_uuid, *_):
    if _BUILD_GUARD:
        return
    _PENDING_BUILDS.discard(root_uuid)
    try:
        trigger = _node_from_uuid(trigger_uuid)
        if cmds.objExists(trigger + ".BuildNow"):
            cmds.setAttr(trigger + ".BuildNow", 0)
    except Exception:
        pass
    _restore_maya_focus()


def _cancel_confirmation(trigger_uuid, root_uuid, window_name, *_):
    _cancel_build_now(trigger_uuid, root_uuid)
    if cmds.window(window_name, exists=True):
        cmds.deleteUI(window_name, window=True)


def _confirm_build_now(trigger_uuid, root_uuid, window_name, *_):
    global _BUILD_GUARD
    if _BUILD_GUARD:
        return
    _BUILD_GUARD = True
    try:
        trigger = _node_from_uuid(trigger_uuid)
        root_control = _node_from_uuid(root_uuid)
        _kill_build_jobs(root_uuid)
        if cmds.window(window_name, exists=True):
            cmds.deleteUI(window_name, window=True)
        _finalize_build(root_control)
    except Exception as error:
        if "trigger" in locals() and cmds.objExists(trigger + ".BuildNow"):
            cmds.setAttr(trigger + ".BuildNow", 0)
        if "root_control" in locals() and cmds.objExists(root_control):
            try:
                _install_build_jobs(_section_controls(root_control), root_control)
            except Exception:
                pass
        cmds.warning("S-curve build failed: {}".format(error))
    finally:
        _BUILD_GUARD = False
        _PENDING_BUILDS.discard(root_uuid)
        _restore_maya_focus()


def _show_build_confirmation(trigger_uuid, root_uuid):
    if root_uuid not in _PENDING_BUILDS:
        return
    try:
        trigger = _node_from_uuid(trigger_uuid)
        root_control = _node_from_uuid(root_uuid)
        if cmds.getAttr(trigger + ".BuildNow") != 1:
            _PENDING_BUILDS.discard(root_uuid)
            return
        if cmds.getAttr(root_control + ".buildState") == 1:
            _PENDING_BUILDS.discard(root_uuid)
            return
    except Exception:
        _PENDING_BUILDS.discard(root_uuid)
        return

    window_name = "hairClothBuildConfirm_" + re.sub(r"[^A-Za-z0-9_]", "_", root_uuid)
    if cmds.window(window_name, exists=True):
        cmds.deleteUI(window_name, window=True)

    window = cmds.window(
        window_name,
        title="Build S-Curve Rig",
        sizeable=False,
        widthHeight=(360, 135),
        closeCommand=functools.partial(_cancel_build_now, trigger_uuid, root_uuid),
    )
    cmds.columnLayout(adjustableColumn=True, rowSpacing=10)
    cmds.text(
        label="Create driver groups and constraints now?",
        align="center",
        height=35,
    )
    cmds.text(
        label="The current Root, Mid, and Tip positions will be used.",
        align="center",
    )
    cmds.rowLayout(numberOfColumns=2, adjustableColumn=1,
                   columnWidth2=(180, 180))
    cmds.button(
        label="Build",
        height=32,
        command=functools.partial(
            _confirm_build_now, trigger_uuid, root_uuid, window_name
        ),
    )
    cmds.button(
        label="Cancel",
        height=32,
        command=functools.partial(
            _cancel_confirmation, trigger_uuid, root_uuid, window_name
        ),
    )
    cmds.showWindow(window)


def _on_build_now(trigger_uuid, root_uuid):
    if _BUILD_GUARD or root_uuid in _PENDING_BUILDS:
        return
    try:
        trigger = _node_from_uuid(trigger_uuid)
        root_control = _node_from_uuid(root_uuid)
        if cmds.getAttr(trigger + ".BuildNow") != 1:
            return
        if cmds.getAttr(root_control + ".buildState") == 1:
            return
    except Exception:
        return

    _PENDING_BUILDS.add(root_uuid)
    callback = functools.partial(_show_build_confirmation, trigger_uuid, root_uuid)
    cmds.evalDeferred(callback, lowestPriority=True)


def _install_single_build_job(control, root_control):
    root_uuid = _node_uuid(root_control)
    trigger_uuid = _node_uuid(control)
    callback = functools.partial(_on_build_now, trigger_uuid, root_uuid)
    job_id = cmds.scriptJob(
        attributeChange=[control + ".BuildNow", callback],
        killWithScene=True,
    )
    _SCRIPT_JOBS.setdefault(root_uuid, []).append(job_id)
    return job_id


def _install_build_jobs(section_controls, root_control):
    root_uuid = _node_uuid(root_control)
    _SCRIPT_JOBS[root_uuid] = []
    for control in section_controls:
        _install_single_build_job(control, root_control)


def prepare_s_curve_rig(prefix="hairCloth", radius=None, normal_axis="X"):
    """Create three editable placement controls without building constraints."""
    controls = _selected_transforms()
    if len(controls) < 3:
        cmds.error("Select at least three controls.")

    prefix = _unique_prefix(_safe_name(prefix))
    normal_axis = normal_axis.upper()
    if normal_axis not in ("X", "Y", "Z"):
        normal_axis = "X"
    radius = float(radius) if radius is not None else _auto_radius(controls)
    if radius <= 0.0:
        cmds.error("Control radius must be greater than zero.")

    matrices = [_world_matrix(control) for control in controls]
    section_data = (
        ("ROOT", matrices[0], 17),
        ("MID", _mid_matrix(controls, matrices), 18),
        ("TIP", matrices[-1], 13),
    )

    cmds.undoInfo(openChunk=True, chunkName="Prepare Hair Cloth S Curve Rig")
    try:
        rig_group = cmds.createNode("transform", name=prefix + "_RIG_GRP")
        section_controls = []
        previous_control = None
        for label, matrix, color in section_data:
            zero, control = _create_rectangle_control(
                "{}_{}_CTRL".format(prefix, label),
                matrix,
                radius,
                normal_axis,
                color,
            )
            if previous_control:
                cmds.parent(zero, previous_control, absolute=True)
            else:
                cmds.parent(zero, rig_group, absolute=True)
            _add_build_attribute(control)
            section_controls.append(control)
            previous_control = control

        root_control = section_controls[0]
        _add_metadata(root_control, controls, section_controls, prefix)
        _install_build_jobs(section_controls, root_control)
        cmds.select(section_controls, replace=True)
    except Exception:
        cmds.undoInfo(closeChunk=True)
        cmds.undo()
        raise
    else:
        cmds.undoInfo(closeChunk=True)

    cmds.inViewMessage(
        amg="Position Root, Mid, and Tip. Set Build Now to 1 when ready.",
        position="topCenter",
        fade=True,
    )
    return {
        "prefix": prefix,
        "rigGroup": rig_group,
        "sectionControls": section_controls,
    }


if __name__ == "__main__":
    prepare_s_curve_rig()
