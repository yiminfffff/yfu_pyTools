# -*- coding: utf-8 -*-
"""OptiTrack motion preparation + HumanIK definition for Maya 2027.

Run this file in Maya's Python Script Editor, or import it and call show_ui().
Keep MOCAP_def.xml beside this file. No source FBX or XML files are modified.
"""
from pathlib import Path
import json
import math
import re
import traceback
import xml.etree.ElementTree as ET

import maya.cmds as cmds
import maya.mel as mel

VERSION = '2.0.1'
META_ATTR = 'mocapPrepInfo'
REQUIRED = {'Hips', 'Spine', 'Head', 'LeftUpLeg', 'LeftLeg', 'LeftFoot',
            'RightUpLeg', 'RightLeg', 'RightFoot', 'LeftArm', 'LeftForeArm',
            'LeftHand', 'RightArm', 'RightForeArm', 'RightHand'}
ROTATIONS = ('rotateX', 'rotateY', 'rotateZ')
TRS = ROTATIONS + ('translateX', 'translateY', 'translateZ', 'scaleX', 'scaleY', 'scaleZ')
_DIALOG = None


class PrepError(RuntimeError):
    pass


def _mel_string(value):
    return json.dumps(str(value), ensure_ascii=False)


def default_template():
    filename = globals().get('__file__')
    candidate = Path(filename).resolve().with_name('MOCAP_def.xml') if filename else None
    if candidate and candidate.is_file():
        return str(candidate)
    return ''


def read_mapping(path):
    try:
        tree = ET.parse(path)
    except (OSError, ET.ParseError) as exc:
        raise PrepError('Cannot read definition file: {}'.format(exc)) from exc
    mapping = {}
    for item in tree.findall('./match_list/item'):
        slot, bone = item.get('key', '').strip(), item.get('value', '').strip()
        if not bone:
            continue  # Empty optional slots must stay unassigned.
        if not slot or slot in mapping:
            raise PrepError('Empty or duplicate XML slot: ' + slot)
        mapping[slot] = bone
    if not mapping.get('Reference'):
        raise PrepError('Definition must include Reference → Root.')
    # Maya's exported template normally already omits the character prefix.
    ref = mapping['Reference']
    if ref != 'Root' and ref.endswith('_Root'):
        old_prefix = ref[:-len('Root')]
        if not all(bone.startswith(old_prefix) for bone in mapping.values()):
            raise PrepError('Inconsistent XML prefixes. Export the template without character prefixes.')
        mapping = {slot: bone[len(old_prefix):] for slot, bone in mapping.items()}
    missing = REQUIRED.difference(mapping)
    if missing:
        raise PrepError('Required HumanIK slots missing from XML: ' + ', '.join(sorted(missing)))
    return mapping


def _leaf(node):
    return node.rsplit('|', 1)[-1]


def inspect_selection(template_path, root=None):
    mapping = read_mapping(template_path)
    selected = cmds.ls(root, long=True) if root else cmds.ls(selection=True, long=True)
    if len(selected or []) != 1 or not cmds.objectType(selected[0], isAType='transform'):
        raise PrepError('Select one mocap Root joint or transform.')
    root = selected[0]
    leaf = _leaf(root)
    namespace, sep, bare = leaf.rpartition(':')
    namespace = namespace + ':' if sep else ''
    bare = bare if sep else leaf
    suffix = mapping['Reference']
    if bare != suffix and not bare.endswith('_' + suffix):
        raise PrepError('Select the top mocap Root ending in {}.'.format(suffix))
    prefix = bare[:-len(suffix)]
    joints = cmds.listRelatives(root, allDescendents=True, type='joint', fullPath=True) or []
    nodes = [root] + sorted(set(joints).difference([root]))
    if len(nodes) < 2:
        raise PrepError('No joints found under the selected Root.')
    by_leaf = {}
    for node in nodes:
        by_leaf.setdefault(_leaf(node), []).append(node)
    resolved, errors = {}, []
    for slot, bone in mapping.items():
        expected = namespace + prefix + bone
        found = by_leaf.get(expected, [])
        if len(found) != 1:
            errors.append('{} → {} ({})'.format(slot, expected, 'missing' if not found else 'ambiguous name'))
        else:
            resolved[slot] = found[0]
    if errors:
        raise PrepError('Mapping check failed:\n' + '\n'.join(errors))
    if len(set(resolved.values())) != len(resolved):
        raise PrepError('XML assigns the same joint to multiple HumanIK slots.')
    times = cmds.keyframe(nodes, q=True, timeChange=True) or []
    label = namespace + prefix.rstrip('_')
    char_name = re.sub(r'[^A-Za-z0-9_]', '_', label).strip('_') or 'Mocap'
    if char_name[0].isdigit():
        char_name = 'Mocap_' + char_name
    return {'root': root, 'nodes': nodes, 'namespace': namespace, 'prefix': prefix,
            'character_name': char_name + '_HIK', 'mapping': resolved,
            'key_range': (min(times), max(times)) if times else None}


def _metadata(root):
    if not cmds.attributeQuery(META_ATTR, node=root, exists=True):
        return None
    try:
        return json.loads(cmds.getAttr(root + '.' + META_ATTR) or '{}')
    except (ValueError, TypeError) as exc:
        raise PrepError('Cannot read the Root prep record. Check its mocapPrepInfo attribute.') from exc


def _existing_characters(info):
    found = set()
    for node in info['nodes']:
        found.update(cmds.listConnections(node, type='HIKCharacterNode') or [])
    return sorted(found)


def _check_editable(info):
    for node in info['nodes']:
        if cmds.referenceQuery(node, isNodeReferenced=True):
            raise PrepError('Import the reference before processing: ' + node)
        if cmds.lockNode(node, q=True, lock=True)[0]:
            raise PrepError('Locked node: ' + node)
        for attr in TRS:
            plug = node + '.' + attr
            if cmds.getAttr(plug, lock=True):
                raise PrepError('Locked attribute: ' + plug)
            inputs = cmds.listConnections(plug, source=True, destination=False, plugs=True) or []
            for source in inputs:
                source_node = source.split('.')[0]
                if cmds.nodeType(source_node) not in ('animCurveTA', 'animCurveTL', 'animCurveTT', 'animCurveTU'):
                    raise PrepError('Bake animation layers, constraints or drivers first: ' + plug)
    curves = sorted(set(cmds.keyframe(info['nodes'], q=True, name=True) or []))
    allowed = set(info['nodes'])
    for curve in curves:
        if cmds.nodeType(curve) not in ('animCurveTA', 'animCurveTL', 'animCurveTT', 'animCurveTU'):
            raise PrepError('Bake non-time animation curves first: ' + curve)
        source = cmds.listConnections(curve + '.input', source=True, destination=False) or []
        if any(cmds.nodeType(n) != 'time' for n in source):
            raise PrepError('Bake time remapping first: ' + curve)
        if cmds.referenceQuery(curve, isNodeReferenced=True) or cmds.lockNode(curve, q=True, lock=True)[0]:
            raise PrepError('Referenced or locked animation curve: ' + curve)
        for destination in cmds.listConnections(curve + '.output', source=False, destination=True) or []:
            paths = cmds.ls(destination, long=True) or []
            if not paths or any(path not in allowed for path in paths):
                raise PrepError('Curve also drives nodes outside this character: ' + curve)
    return curves


def _prepare(info, curves, target, source, pre_roll):
    offset, pose_time = target - source, target - pre_roll
    # Insert the trim boundary before cutting, keeping the selected frame's pose.
    for curve in curves:
        keys = cmds.keyframe(curve, q=True, timeChange=True) or []
        if keys and source not in keys:
            if min(keys) < source < max(keys):
                cmds.setKeyframe(curve, time=source, insert=True)
            else:
                value = cmds.keyframe(curve, q=True, eval=True, time=(source, source))[0]
                cmds.setKeyframe(curve, time=source, value=value)
        if keys:
            # Removing an earlier key recalculates spline/auto tangents at an
            # existing trim boundary. Freeze that boundary before editing so
            # subframe motion after the new start remains identical.
            time_range = (source, source)
            tangents = {flag: cmds.keyTangent(curve, q=True, time=time_range, **{flag: True})[0]
                        for flag in ('inAngle', 'outAngle', 'inWeight', 'outWeight',
                                     'inTangentType', 'outTangentType')}
            weighted = bool(cmds.keyTangent(curve, q=True, weightedTangents=True)[0])
            cmds.keyTangent(curve, e=True, time=time_range, lock=False)
            if weighted:
                cmds.keyTangent(curve, e=True, time=time_range, weightLock=False)
            in_weight = {'inWeight': tangents['inWeight']} if weighted else {}
            out_weight = {'outWeight': tangents['outWeight']} if weighted else {}
            cmds.keyTangent(curve, e=True, time=time_range, inTangentType='fixed',
                            inAngle=tangents['inAngle'], **in_weight)
            if tangents['outTangentType'] not in ('step', 'stepnext'):
                cmds.keyTangent(curve, e=True, time=time_range, outTangentType='fixed',
                                outAngle=tangents['outAngle'], **out_weight)
    if curves:
        cmds.keyframe(curves, edit=True, relative=True, timeChange=offset, animation='objects')
        # Explicit earliest key includes negative and fractional pre-start keys.
        lower = info['key_range'][0] + offset
        if lower < target:
            cmds.cutKey(curves, time=(lower, target), includeUpperBound=False,
                        option='keys', clear=True, animation='objects')
    # Protect the first motion value on every rotation channel, including unkeyed bones.
    cmds.currentTime(target, update=True)
    for node in info['nodes']:
        for attr in ROTATIONS:
            plug = node + '.' + attr
            value = cmds.getAttr(plug)
            cmds.setKeyframe(node, attribute=attr, time=target, value=value,
                             insert=bool(cmds.keyframe(plug, q=True, keyframeCount=True)))
            cmds.setKeyframe(node, attribute=attr, time=pose_time, value=0.0)
    end = max(target, info['key_range'][1] + offset)
    cmds.playbackOptions(animationStartTime=pose_time, animationEndTime=end, minTime=target, maxTime=end)
    cmds.currentTime(pose_time, update=True)
    metadata = {'version': VERSION, 'source_start': source, 'start': target,
                'pose': pose_time, 'end': end, 'time_unit': cmds.currentUnit(q=True, time=True)}
    if not cmds.attributeQuery(META_ATTR, node=info['root'], exists=True):
        cmds.addAttr(info['root'], longName=META_ATTR, dataType='string')
    cmds.setAttr(info['root'] + '.' + META_ATTR, json.dumps(metadata), type='string')
    return metadata


def _load_hik():
    for plugin in ('mayaHIK', 'mayaCharacterization', 'retargeterNodes'):
        if not cmds.pluginInfo(plugin, q=True, loaded=True):
            cmds.loadPlugin(plugin, quiet=True)
    for script in ('hikGlobalUtils.mel', 'hikCharacterControlsUI.mel',
                   'hikDefinitionOperations.mel'):
        mel.eval('source {};'.format(_mel_string(script)))


def _create_hik(info):
    name = info['character_name']
    if cmds.objExists(name):
        index = 2
        while cmds.objExists(name + '_' + str(index)):
            index += 1
        name += '_' + str(index)
    character = mel.eval('hikCreateCharacter({});'.format(_mel_string(name)))
    for slot, node in info['mapping'].items():
        node_id = mel.eval('hikGetNodeIdFromName({});'.format(_mel_string(slot)))
        if node_id < 0:
            raise PrepError('Unknown HumanIK slot: ' + slot)
        mel.eval('setCharacterObject({}, {}, {}, 0);'.format(
            _mel_string(node), _mel_string(character), node_id))
    mel.eval('hikSetCurrentCharacter({});'.format(_mel_string(character)))
    if not mel.eval('hikValidateSkeleton({});'.format(_mel_string(character))):
        raise PrepError('Required HumanIK bones failed validation.')
    # The pose validator lives inside Maya's characterization widget. In batch
    # mode its status can misleadingly be 0 even for an empty definition.
    if cmds.about(batch=True):
        raise PrepError('HumanIK pose validation requires the Maya GUI. Use Prepare Animation in batch mode.')
    mel.eval('hikUpdateDefinitionUI();')
    active = mel.eval('characterizationToolUICmd -query -currentcharname;')
    if active != character:
        raise PrepError('Pose validator unavailable. Open HumanIK and try again.')
    status = int(mel.eval('characterizationToolUICmd -query -curcharstatus;'))
    if status not in (0, 2, 4):
        raise PrepError('HumanIK pose validation failed (status {}). Check the zero pose and mapping.'.format(status))
    mel.eval('hikCharacterLock({}, 1, 1);'.format(_mel_string(character)))
    if not cmds.getAttr(character + '.InputCharacterizationLock'):
        raise PrepError('Cannot lock HumanIK. Check that the zero frame is a valid T-pose.')
    if status in (2, 4):
        cmds.warning('HumanIK locked with pose warnings. Check the yellow markers in Definition.')
    return character, status


def run_setup(template_path, target=1001, pre_roll=11, source_mode='current',
              prepare=True, characterize=True, root=None):
    """Process one selected raw skeleton. source_mode: 'current' or 'first'.

    All edits form one undo operation. An error rolls the operation back.
    Existing character definitions and previously prepared motion are rejected.
    """
    if not prepare and not characterize:
        raise PrepError('Select at least one operation.')
    if not cmds.undoInfo(q=True, state=True):
        raise PrepError('Enable Undo in Maya preferences before processing.')
    info = inspect_selection(template_path, root)
    previous = _metadata(info['root'])
    existing = _existing_characters(info)
    if existing:
        raise PrepError('Skeleton already assigned to HumanIK: {}. No changes made.'.format(', '.join(existing)))
    curves = _check_editable(info)
    if prepare:
        target, pre_roll = float(target), float(pre_roll)
        if not math.isfinite(target) or not math.isfinite(pre_roll) or pre_roll <= 0:
            raise PrepError('Start frame must be finite and pre-roll must be greater than 0.')
        if previous:
            raise PrepError('Root already prepared. Undo or reimport the FBX, or use Define HumanIK.')
        if not info['key_range'] or not curves:
            raise PrepError('No animation keys found.')
        if source_mode not in ('current', 'first'):
            raise PrepError('source_mode must be current or first.')
        source = float(cmds.currentTime(q=True)) if source_mode == 'current' else info['key_range'][0]
        if source < info['key_range'][0] - 1e-6 or source > info['key_range'][1] + 1e-6:
            raise PrepError('Current frame is outside the animation range. Move to a valid frame or select First Keyframe.')
        for curve in curves:
            first = min(cmds.keyframe(curve, q=True, timeChange=True))
            if first > source + 1e-6:
                raise PrepError('A channel starts after the source frame. Bake a uniform range or choose a later source frame: ' + curve)
    if characterize:
        if cmds.about(batch=True):
            raise PrepError('HumanIK pose validation requires the Maya GUI. Use Prepare Animation in batch mode.')
        _load_hik()
        mel.eval('hikCreateCharacterControlsDockableWindow();')
        for slot in info['mapping']:
            if mel.eval('hikGetNodeIdFromName({});'.format(_mel_string(slot))) < 0:
                raise PrepError('Unknown HumanIK slot: ' + slot)
    selection = cmds.ls(selection=True, long=True) or []
    old_time = cmds.currentTime(q=True)
    old_range = {key: cmds.playbackOptions(q=True, **{key: True}) for key in
                 ('animationStartTime', 'animationEndTime', 'minTime', 'maxTime')}
    auto_key = cmds.autoKeyframe(q=True, state=True)
    result = {'info': info, 'character': None, 'motion': previous, 'hik_status': None}
    cmds.undoInfo(openChunk=True, chunkName='OptiTrack Motion Prep')
    failed = False
    try:
        cmds.autoKeyframe(state=False)
        if prepare:
            result['motion'] = _prepare(info, curves, target, source, pre_roll)
        elif characterize and previous:
            if previous.get('time_unit') != cmds.currentUnit(q=True, time=True):
                raise PrepError('Scene frame rate changed after prep. Restore the original frame rate before defining HumanIK.')
            cmds.currentTime(previous['pose'], update=True)
        if characterize:
            result['character'], result['hik_status'] = _create_hik(info)
        cmds.select(info['root'], replace=True)
    except Exception:
        failed = True
        raise
    finally:
        cmds.autoKeyframe(state=auto_key)
        cmds.undoInfo(closeChunk=True)
        if failed:
            cmds.undo()
            cmds.playbackOptions(**old_range)
            cmds.currentTime(old_time, update=True)
            if selection:
                cmds.select(selection, replace=True)
            else:
                cmds.select(clear=True)
    return result


def show_ui():
    """Open the Maya 2027 (PySide6) tool window."""
    global _DIALOG
    from PySide6 import QtCore, QtWidgets
    from shiboken6 import wrapInstance
    from maya import OpenMayaUI

    class MocapDialog(QtWidgets.QDialog):
        def __init__(self, parent=None):
            super().__init__(parent)
            self.setObjectName('OptiTrackMotionPrepUI')
            self.setWindowTitle('OptiTrack · Mocap Setup')
            self.setMinimumWidth(520)
            self.setWindowFlag(QtCore.Qt.WindowType.WindowContextHelpButtonHint, False)
            layout = QtWidgets.QVBoxLayout(self)
            layout.setSpacing(12)
            title = QtWidgets.QLabel('Mocap Setup')
            title.setStyleSheet('font-size: 20px; font-weight: 600;')
            layout.addWidget(title)
            file_row = QtWidgets.QHBoxLayout()
            self.xml = QtWidgets.QLineEdit(default_template())
            self.xml.setPlaceholderText('MOCAP_def.xml')
            browse = QtWidgets.QPushButton('Browse XML…')
            browse.clicked.connect(self.browse)
            file_row.addWidget(self.xml, 1)
            file_row.addWidget(browse)
            layout.addLayout(file_row)
            detect = QtWidgets.QPushButton('Check Selected Root')
            detect.clicked.connect(self.inspect)
            layout.addWidget(detect)
            self.identity = QtWidgets.QLabel('No character loaded')
            self.identity.setWordWrap(True)
            self.identity.setTextFormat(QtCore.Qt.TextFormat.PlainText)
            layout.addWidget(self.identity)
            group = QtWidgets.QGroupBox('Timing')
            form = QtWidgets.QFormLayout(group)
            self.source = QtWidgets.QComboBox()
            self.source.addItems(['Current Frame', 'First Keyframe'])
            form.addRow('Source Start', self.source)
            target_row = QtWidgets.QHBoxLayout()
            self.preset = QtWidgets.QComboBox()
            self.preset.addItems(['0', '1', '1001', 'Custom'])
            self.preset.setCurrentIndex(2)
            self.target = QtWidgets.QDoubleSpinBox()
            self.target.setRange(-10000000, 10000000)
            self.target.setDecimals(3)
            self.target.setValue(1001)
            self.target.setEnabled(False)
            target_row.addWidget(self.preset)
            target_row.addWidget(self.target)
            form.addRow('Start Frame', target_row)
            self.pre_roll = QtWidgets.QDoubleSpinBox()
            self.pre_roll.setRange(0.001, 1000000)
            self.pre_roll.setDecimals(3)
            self.pre_roll.setValue(11)
            form.addRow('Pre-roll Frames', self.pre_roll)
            self.preview = QtWidgets.QLabel()
            form.addRow('Zero Pose Frame', self.preview)
            layout.addWidget(group)
            self.combined = QtWidgets.QPushButton('Prepare + Define HumanIK')
            self.combined.setMinimumHeight(38)
            self.combined.setStyleSheet('background: #356b87; color: white; font-weight: 600;')
            self.combined.clicked.connect(lambda: self.execute(True, True))
            layout.addWidget(self.combined)
            buttons = QtWidgets.QHBoxLayout()
            prep = QtWidgets.QPushButton('Prepare Animation')
            hik = QtWidgets.QPushButton('Define HumanIK')
            prep.clicked.connect(lambda: self.execute(True, False))
            hik.clicked.connect(lambda: self.execute(False, True))
            buttons.addWidget(prep)
            buttons.addWidget(hik)
            layout.addLayout(buttons)
            self.log = QtWidgets.QPlainTextEdit()
            self.log.setReadOnly(True)
            self.log.setMinimumHeight(120)
            layout.addWidget(self.log)
            open_hik = QtWidgets.QPushButton('Open HumanIK')
            open_hik.clicked.connect(self.open_hik)
            layout.addWidget(open_hik)
            self.preset.currentIndexChanged.connect(self.set_preset)
            self.target.valueChanged.connect(self.update_preview)
            self.pre_roll.valueChanged.connect(self.update_preview)
            self.update_preview()

        def browse(self):
            path, _ = QtWidgets.QFileDialog.getOpenFileName(self, 'Select Definition', self.xml.text(), 'HumanIK XML (*.xml)')
            if path:
                self.xml.setText(path)

        def set_preset(self, index):
            self.target.setEnabled(index == 3)
            if index < 3:
                self.target.setValue([0, 1, 1001][index])
            self.update_preview()

        def update_preview(self, *_):
            start = self.target.value()
            self.preview.setText('{:g}'.format(start - self.pre_roll.value()))

        def inspect(self):
            try:
                info = inspect_selection(self.xml.text().strip())
                self.identity.setText('Prefix: {}{}\nRoot: {}'.format(info['namespace'], info['prefix'] or '(none)', info['root']))
                rows = ['Ready: {} nodes, {} HumanIK slots.'.format(len(info['nodes']), len(info['mapping'])),
                        'Range: {} | Rate: {}'.format(info['key_range'], cmds.currentUnit(q=True, time=True)),
                        'Character: ' + info['character_name']]
                rows.extend('{} → {}'.format(slot, _leaf(node)) for slot, node in info['mapping'].items())
                self.log.setPlainText('\n'.join(rows))
            except Exception as exc:
                self.log.setPlainText(str(exc))

        def execute(self, prepare, characterize):
            try:
                result = run_setup(self.xml.text().strip(), self.target.value(), self.pre_roll.value(),
                                   'current' if self.source.currentIndex() == 0 else 'first', prepare, characterize)
                self.identity.setText('Root: ' + result['info']['root'])
                rows = ['Complete.']
                if result['motion']:
                    m = result['motion']
                    rows.append('Range: {start:g}–{end:g} | Zero pose: {pose:g}'.format(**m))
                if result['character']:
                    rows.append('HumanIK: {} (locked)'.format(result['character']))
                    if result['hik_status'] in (2, 4):
                        rows.append('Pose warnings: check yellow markers in HumanIK Definition.')
                self.log.setPlainText('\n'.join(rows))
            except Exception as exc:
                self.log.setPlainText('Failed: ' + str(exc))
                cmds.warning(str(exc))
                if not isinstance(exc, PrepError):
                    traceback.print_exc()

        def open_hik(self):
            try:
                _load_hik()
                mel.eval('HIKCharacterControlsTool;')
            except Exception as exc:
                self.log.setPlainText(str(exc))

    if _DIALOG is not None:
        try:
            _DIALOG.close()
            _DIALOG.deleteLater()
        except RuntimeError:
            pass
    pointer = OpenMayaUI.MQtUtil.mainWindow()
    parent = wrapInstance(int(pointer), QtWidgets.QWidget) if pointer else None
    _DIALOG = MocapDialog(parent)
    _DIALOG.show()
    return _DIALOG


def onMayaDroppedPythonFile(*_):
    show_ui()


if __name__ == '__main__':
    show_ui()
