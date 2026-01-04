import re
import maya.cmds as cmds

EPS = 1e-5

SIDE_PATTERNS = [
    (r"(^L_)", "R_"), (r"(^R_)", "L_"),
    (r"(_L$)", "_R"), (r"(_R$)", "_L"),
    (r"(_L_)", "_R_"), (r"(_R_)", "_L_"),
    (r"(\.L$)", ".R"), (r"(\.R$)", ".L"),
    (r"(_left_)", "_right_"), (r"(_right_)", "_left_"),
    (r"(Left)", "Right"), (r"(Right)", "Left"),
    (r"(left)", "right"), (r"(right)", "left"),
]


def swap_side_in_name(shortname, original_tx):
    """Swap L/R tokens. If none found, append side suffix based on X."""
    new = shortname

    for pat, repl in SIDE_PATTERNS:
        new_candidate = re.sub(pat, repl, new)
        if new_candidate != new:
            new = new_candidate
            break

    # No replacement happened
    if new == shortname:
        if original_tx >= 0:
            new = shortname + "_R"
        else:
            new = shortname + "_L"

    return new


def mirror_transform(node, freeze_after=False):
    """Duplicate node and mirror across YZ plane (X -> -X)."""
    try:
        tx, ty, tz = cmds.xform(node, q=True, ws=True, t=True)
    except Exception:
        cmds.warning("Failed to query transform for %s" % node)
        return None

    dup_list = cmds.duplicate(node, rc=True)
    if not dup_list:
        cmds.warning("Duplicate failed for %s" % node)
        return None

    dup = dup_list[0]

    try:
        cmds.parent(dup, world=True)
    except Exception:
        pass

	# rename according to side
    original_short = cmds.ls(node, sn=True)[0]
    new_name = swap_side_in_name(original_short, tx)

    try:
        dup = cmds.rename(dup, new_name)
    except Exception:
        pass

    # mirror translate
    cmds.setAttr(dup + ".translateX", -tx)
    cmds.setAttr(dup + ".translateY", ty)
    cmds.setAttr(dup + ".translateZ", tz)

    # mirror rotation
    try:
        rx, ry, rz = cmds.xform(node, q=True, ws=True, ro=True)
        cmds.setAttr(dup + ".rotateX", rx)
        cmds.setAttr(dup + ".rotateY", -ry)
        cmds.setAttr(dup + ".rotateZ", -rz)
    except Exception:
        pass

    # mirror scale
    try:
        sx, sy, sz = cmds.getAttr(node + ".scale")[0]
        cmds.setAttr(dup + ".scale", -sx, sy, sz)
    except Exception:
        pass

    if freeze_after:
        try:
            cmds.makeIdentity(dup, apply=True, t=1, r=1, s=1, n=0)
        except Exception:
            pass

    return dup


def mirror_selected(freeze_after=False):
    sel = cmds.ls(sl=True, long=True) or []
    if not sel:
        cmds.warning("No objects selected.")
        return []

    results = []
    for node in sel:
        if cmds.nodeType(node) != 'transform':
            parents = cmds.listRelatives(node, parent=True, fullPath=True) or []
            if parents:
                node = parents[0]

        try:
            cmds.xform(node, q=True, ws=True, t=True)
        except Exception:
            cmds.warning("Could not read world translation for %s" % node)
            continue

        new = mirror_transform(node, freeze_after=freeze_after)
        if new:
            results.append(new)

    if results:
        cmds.select(results, replace=True)
    return results


if __name__ == '__main__':
    mirror_selected()
