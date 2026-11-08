"""Select and mirror APEX Transform Driver setups inside Houdini.

Run from a Python shelf tool with runpy.run_path(..., run_name='__main__').
Tested with Houdini 22.0.466 and MILO_RIG_v033.hipnc.
World means the character's SOP space, before any OBJ-level transform.
"""
from __future__ import annotations

import copy
import json
import re
from contextlib import contextmanager
from pathlib import Path

import hou

BACKUP_KEY = 'apex_setup_mirror_backup_v1'
SIDE_COLOURS = {'L': (0.0, 0.0, 1.0), 'R': (1.0, 0.0, 0.0)}
REQUIRED = {'driver#', 'driverguide#', 'driverparent#', 'driventransforms#',
            'offsettrans#', 'offsetrot#', 'offsetscale#', 'mode#'}


def side_name(text, source='L', target='R'):
    """Replace complete side tokens, including tags like bindjoint__L_arm."""
    return re.sub(r'(?<![A-Za-z0-9])' + re.escape(source) + r'(?![A-Za-z0-9])',
                  lambda _: target, text)


def setups_parm(node):
    if 'autorigcomponent' not in node.type().name().lower():
        raise ValueError('Select an APEX AutoRig Component node.')
    p = node.parm('setups')
    if p is None or not hasattr(p, 'multiParmInstancesAsData'):
        raise ValueError('This node has no setups, or this Houdini version does not support the multiparm data API.')
    return p


def read_setups(node, evaluate=True):
    rows = list(setups_parm(node).multiParmInstancesAsData(evaluate=evaluate))
    if rows and not REQUIRED.issubset(rows[0]):
        raise ValueError('Only Transform Driver setups are supported. This component has a different parameter structure.')
    return rows


def find_components():
    return [n for n in hou.node('/obj').allSubChildren()
            if 'autorigcomponent' in n.type().name().lower() and n.parm('setups')]


def reflection(axis='X'):
    scale = [1.0, 1.0, 1.0]
    scale['XYZ'.index(axis)] = -1.0
    return hou.hmath.buildScale(scale)


def _mirrored_row(row, source, target, axis):
    result = {k: side_name(v, source, target) if isinstance(v, str) else copy.deepcopy(v)
              for k, v in row.items()}
    normal = 'XYZ'.index(axis)
    # S*M*S for a diagonal scale and any Euler rotation order.
    for key in ('offsettrans#', 'translate#'):
        if key in result:
            result[key][normal] *= -1
    for key in ('offsetrot#', 'rotate#'):
        if key in result:
            result[key] = [v if i == normal else -v for i, v in enumerate(result[key])]
    return result


def _dependencies(rows, selected, source, target):
    """Include other setups that create the selected drivers' guide/parent."""
    names = {}
    for i, row in enumerate(rows):
        name = row['driver#']
        if side_name(name, source, target) != name:
            names.setdefault(name, []).append(i)
    chosen = set(selected)
    pending = list(selected)
    while pending:
        i = pending.pop()
        for key in ('driverguide#', 'driverparent#'):
            ref = rows[i].get(key, '')
            for name, matches in names.items():
                if not re.search(r'(?<![A-Za-z0-9_])' + re.escape(name)
                                 + r'(?![A-Za-z0-9_])', ref):
                    continue
                if len(matches) > 1:
                    raise ValueError('Multiple setups define the same dependency driver: ' + name)
                dep = matches[0]
                if dep != i and dep not in chosen:
                    chosen.add(dep)
                    pending.append(dep)
    return chosen


def build_plan(node, indices, direction='L->R', axis='X', conflict='skip',
               include_dependencies=True, position_mode='world', change_colour=True):
    """Read-only plan. indices are the UI's ONE-BASED setup numbers."""
    if direction not in ('L->R', 'R->L') or axis not in 'XYZ' or len(axis) != 1:
        raise ValueError('Invalid mirror direction or axis.')
    if conflict not in ('skip', 'update') or position_mode not in ('world', 'offset'):
        raise ValueError('Invalid conflict policy or position mode.')
    rows = read_setups(node)
    selected = {int(i) - 1 for i in indices}
    if not selected or any(i < 0 or i >= len(rows) for i in selected):
        raise ValueError('Select at least one valid setup.')
    source, target = direction.split('->')
    for i in selected:
        if side_name(rows[i]['driver#'], source, target) == rows[i]['driver#']:
            raise ValueError('The driver in setup %d does not belong to the %s side.' % (i + 1, source))
    chosen = _dependencies(rows, selected, source, target) if include_dependencies else selected
    actions = []
    destinations = set()
    for i in sorted(chosen):
        row = _mirrored_row(rows[i], source, target, axis)
        if change_colour and 'color#' in row:
            row['color#'] = list(SIDE_COLOURS[target])
        name = row['driver#']
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', name):
            raise ValueError('Driver must be a single explicit name: ' + name)
        if name in destinations:
            raise ValueError('The selected setups would create a duplicate driver: ' + name)
        destinations.add(name)
        matches = [j for j, r in enumerate(rows) if r['driver#'] == name]
        if len(matches) > 1:
            raise ValueError('Multiple setups already define the target driver. Resolve duplicates first: ' + name)
        existing = matches[0] if matches else None
        actions.append({'source_index': i + 1, 'source_driver': rows[i]['driver#'],
                        'target_index': existing + 1 if existing is not None else None,
                        'action': ('skip' if conflict == 'skip' else 'update') if matches else 'append',
                        'auto_dependency': i not in selected, 'data': row})
    return {'node': node.path(), 'direction': direction, 'axis': axis,
            'position_mode': position_mode, 'change_colour': bool(change_colour),
            'frame': hou.frame(),
            'snapshot': rows, 'raw_snapshot': read_setups(node, evaluate=False),
            'actions': actions, 'resolved': position_mode == 'offset'}


@contextmanager
def _automatic_updates():
    previous = hou.updateModeSetting()
    hou.setUpdateMode(hou.updateMode.AutoUpdate)
    try:
        yield
    finally:
        hou.setUpdateMode(previous)


def guide_transforms(node, guide_path=None):
    """Read full rest transforms from the component's actual output guides."""
    node.cook(force=True)
    if node.errors():
        raise ValueError('Node cooking failed:\n' + '\n'.join(node.errors()))
    pathparm = node.parm('inputguidename')
    guide_path = guide_path or (pathparm.evalAsString() if pathparm else None)
    if not guide_path:
        raise ValueError('Cannot determine the Guide Skeleton path. Use offset mode or specify guide_path.')
    guide_path = '/' + guide_path.lstrip('/')
    geo = node.geometry().unpackFromFolder(guide_path)
    if geo is None or not geo.findPointAttrib('name') or not geo.findPointAttrib('transform'):
        raise ValueError('Cannot find a guide skeleton with name and transform attributes: ' + guide_path)
    transforms = {}
    for point in geo.points():
        name = point.attribValue('name')
        if name in transforms:
            raise ValueError('The guide skeleton contains a duplicate name: ' + name)
        matrix = hou.Matrix4(hou.Matrix3(point.attribValue('transform')))
        matrix.setAt(3, 0, point.position()[0])
        matrix.setAt(3, 1, point.position()[1])
        matrix.setAt(3, 2, point.position()[2])
        transforms[name] = matrix
    return transforms


def _matrix_close(a, b, tol=2e-5):
    return max(abs(x - y) for x, y in zip(a.asTuple(), b.asTuple())) <= tol


def _result_rows(plan):
    rows = copy.deepcopy(plan['raw_snapshot'])
    for action in plan['actions']:
        if action['action'] == 'skip':
            continue
        if action['action'] == 'append':
            action['target_index'] = len(rows) + 1
            rows.append(copy.deepcopy(action['data']))
        else:
            rows[action['target_index'] - 1] = copy.deepcopy(action['data'])
    return rows


def resolve_world_positions(node, plan, guide_path=None):
    """Solve offsets against real opposite-side bases in an isolated SOP copy.

    The copy runs the original component, including custom constraint code.
    No edits to the source node occur during preview/calculation.
    """
    if plan['position_mode'] == 'offset':
        return plan
    working = copy.deepcopy(plan)
    if guide_path:
        working['guide_path'] = guide_path
    active = [a for a in working['actions'] if a['action'] != 'skip']
    if not active:
        working['resolved'] = True
        return working
    display, render = node.isDisplayFlagSet(), node.isRenderFlagSet()
    temporary = None
    with hou.undos.disabler(), _automatic_updates():
        try:
            source_matrices = guide_transforms(node, guide_path)
            available = set(source_matrices) | {a['data']['driver#'] for a in active}
            for a in active:
                for key in ('driverguide#', 'driverparent#', 'driventransforms#'):
                    ref = a['data'].get(key, '')
                    if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', ref) and ref not in available:
                        raise ValueError('Missing target-side reference: %s = %s. Create the corresponding skeleton/guide or include its setup first.' % (key.rstrip('#'), ref))
            s = reflection(working['axis'])
            desired = {}
            for a in active:
                if not a['data'].get('enable#', True):
                    raise ValueError('Cannot validate positions for a disabled setup. Enable it or use offset mode.')
                if a['source_driver'] not in source_matrices:
                    raise ValueError('Cannot find the source driver in the output guides: ' + a['source_driver'])
                desired[a['data']['driver#']] = s * source_matrices[a['source_driver']] * s
            temporary = hou.copyNodesTo((node,), node.parent())[0]
            temporary.setName('__apex_mirror_preview', unique_name=True)
            temporary.setDisplayFlag(False)
            temporary.setRenderFlag(False)
            rows = _result_rows(working)
            # Apply in destination execution order so parent/guide dependencies
            # already have their final offsets when a later setup is solved.
            for a in sorted(active, key=lambda item: item['target_index']):
                i = a['target_index'] - 1
                row = rows[i]
                row['offsettrans#'] = [0.0, 0.0, 0.0]
                row['offsetrot#'] = [0.0, 0.0, 0.0]
                row['offsetscale#'] = [1.0, 1.0, 1.0]
                temporary.parm('setups').setMultiParmInstancesFromData(rows)
                bases = guide_transforms(temporary, guide_path)
                name = row['driver#']
                if name not in bases:
                    raise ValueError('The target guide was not generated: ' + name)
                base, goal = bases[name], desired[name]
                # Native TransformDriver: pre=O*G, post=G*O,
                # override=O*translation(G). Identity offsets give that base.
                mode = str(a['data']['mode#']).lower()
                if mode in ('1', 'post-multiply'):
                    offset = base.inverted() * goal
                elif mode in ('0', '2', 'pre-multiply', 'override'):
                    offset = goal * base.inverted()
                else:
                    raise ValueError('Unrecognized Offset Mode: ' + mode)
                parts = offset.explode(transform_order='srt', rotate_order='xyz')
                rebuilt = hou.hmath.buildTransform({k: parts[k] for k in ('translate', 'rotate', 'scale')})
                if not _matrix_close(offset, rebuilt):
                    raise ValueError('The mirrored offset contains shear that cannot be represented by TRS: ' + name)
                for key, part in (('offsettrans#', 'translate'), ('offsetrot#', 'rotate'), ('offsetscale#', 'scale')):
                    row[key] = list(parts[part])
                a['data'] = copy.deepcopy(row)
                a['source_position'] = list(source_matrices[a['source_driver']].extractTranslates())
                a['mirrored_position'] = list(goal.extractTranslates())
                a['mirrored_matrix'] = list(goal.asTuple())
            temporary.parm('setups').setMultiParmInstancesFromData(rows)
            actual = guide_transforms(temporary, guide_path)
            for name, goal in desired.items():
                if name not in actual or not _matrix_close(actual[name], goal):
                    raise ValueError('Mirrored position/orientation validation failed for %s. Check guide dependencies or custom component logic.' % name)
            working['resolved'] = True
        finally:
            if temporary is not None:
                temporary.destroy()
            node.setDisplayFlag(display)
            node.setRenderFlag(render)
    return working


def apply_plan(node, plan):
    """Atomic write, one Undo step, plus an embedded backup. Never saves HIP."""
    if plan['node'] != node.path() or hou.frame() != plan['frame']:
        raise ValueError('The node or current frame has changed. Generate a new preview.')
    if read_setups(node) != plan['snapshot'] or read_setups(node, False) != plan['raw_snapshot']:
        raise ValueError('Setups have changed. Generate a new preview.')
    if not plan.get('resolved'):
        raise ValueError('Calculate and validate positions before applying the mirror.')
    active = [a for a in plan['actions'] if a['action'] != 'skip']
    if not active:
        return 0
    p = setups_parm(node)
    backup = copy.deepcopy(plan['raw_snapshot'])
    previous_backup = node.userData(BACKUP_KEY)
    with hou.undos.group('Mirror APEX Setups'):
        try:
            node.setUserData(BACKUP_KEY, json.dumps({'setups': backup}, ensure_ascii=True))
            p.setMultiParmInstancesFromData(_result_rows(plan))
            # Evaluate actual component/constraints; roll back on a cook error.
            with _automatic_updates():
                node.cook(force=True)
                if node.errors():
                    raise ValueError('\n'.join(node.errors()))
                if plan['position_mode'] == 'world':
                    matrices = guide_transforms(node, plan.get('guide_path'))
                    for a in active:
                        name = a['data']['driver#']
                        if name not in matrices or not _matrix_close(matrices[name], hou.Matrix4(a['mirrored_matrix'])):
                            raise ValueError('Mirrored position validation failed after applying changes: ' + name)
        except Exception:
            p.setMultiParmInstancesFromData(backup)
            if previous_backup is None:
                node.destroyUserData(BACKUP_KEY)
            else:
                node.setUserData(BACKUP_KEY, previous_backup)
            raise
    return len(active)


def restore_backup(node):
    backup = node.userData(BACKUP_KEY)
    if not backup:
        raise ValueError('This node has no backup from before mirroring.')
    with hou.undos.group('Restore APEX Setups'):
        setups_parm(node).setMultiParmInstancesFromData(json.loads(backup)['setups'])


def export_plan(plan, filename):
    """Export selected targets and validated SOP-space positions as JSON."""
    public = {k: v for k, v in plan.items() if k not in ('snapshot', 'raw_snapshot')}
    Path(filename).write_text(json.dumps(public, ensure_ascii=False, indent=2), encoding='utf-8')


def show():
    """One modeless dialog: source node, multi-select setups, preview, apply."""
    try:
        from PySide6 import QtCore, QtGui, QtWidgets
    except ImportError:
        from PySide2 import QtCore, QtGui, QtWidgets

    class OptionsComboBox(QtWidgets.QComboBox):
        def paintEvent(self, event):
            super().paintEvent(event)
            # Keep the dropdown indicator visible across Houdini themes.
            painter = QtGui.QPainter(self)
            painter.setRenderHint(QtGui.QPainter.Antialiasing)
            painter.setPen(QtGui.QPen(QtGui.QColor('#edf0f5'), 1.8))
            x, y = self.width() - 13, self.height() // 2
            painter.drawPolyline(QtGui.QPolygon([
                QtCore.QPoint(x - 4, y - 2),
                QtCore.QPoint(x, y + 2),
                QtCore.QPoint(x + 4, y - 2),
            ]))
            painter.end()

    class MirrorDialog(QtWidgets.QDialog):
        def __init__(self):
            super().__init__(hou.qt.mainWindow())
            self.setWindowTitle('APEX Setup Mirror - L / R')
            self.resize(860, 770)
            self.setStyleSheet('''
                QFrame#mirrorOptions {
                    background-color: #2b2e34;
                    border: 1px solid #555e6c;
                    border-radius: 5px;
                }
                QFrame#mirrorOptions QLabel {
                    color: #dce2eb;
                }
                QComboBox {
                    background-color: #202329;
                    color: #edf0f5;
                    border: 1px solid #687587;
                    border-radius: 4px;
                    padding: 5px 10px;
                    min-height: 20px;
                }
                QComboBox:hover {
                    border-color: #a3b9d4;
                }
                QComboBox:focus {
                    border-color: #8ab4f8;
                }
                QComboBox::drop-down {
                    background-color: #454e5c;
                    border-left: 1px solid #687587;
                    width: 24px;
                }
                QComboBox QAbstractItemView {
                    background-color: #202329;
                    color: #edf0f5;
                    selection-background-color: #405e87;
                    selection-color: #ffffff;
                    border: 1px solid #687587;
                }
                QListWidget#setupList {
                    background-color: #24262b;
                    color: #edf0f5;
                    border: 1px solid #687587;
                    border-radius: 4px;
                }
                QListWidget#setupList::item {
                    padding: 5px 10px;
                }
                QListWidget#setupList::item:selected {
                    background-color: #405e87;
                    color: #ffffff;
                }
                QListWidget#setupList::item:disabled {
                    color: #777f8d;
                }
            ''')
            self.plan = None
            layout = QtWidgets.QVBoxLayout(self)
            options = QtWidgets.QFrame()
            options.setObjectName('mirrorOptions')
            form = QtWidgets.QFormLayout(options)
            self.nodes = OptionsComboBox()
            nodes = find_components()
            selected = set(hou.selectedNodes())
            for n in nodes:
                self.nodes.addItem(n.path())
            for i, n in enumerate(nodes):
                if n in selected:
                    self.nodes.setCurrentIndex(i)
                    break
            form.addRow('Component Node', self.nodes)
            self.direction = OptionsComboBox()
            self.direction.addItems(['L->R', 'R->L'])
            form.addRow('Direction', self.direction)
            self.axis = OptionsComboBox()
            self.axis.addItems(['X', 'Y', 'Z'])
            form.addRow('Mirror Plane Normal (through origin)', self.axis)
            self.conflict = OptionsComboBox()
            self.conflict.addItem('Skip existing target setups', 'skip')
            self.conflict.addItem('Update existing target setups', 'update')
            form.addRow('Existing Targets', self.conflict)
            self.position_mode = OptionsComboBox()
            self.position_mode.addItem('Full transform: calculate and validate position/orientation (recommended)', 'world')
            self.position_mode.addItem('Parameter offsets only (final positions are not validated)', 'offset')
            form.addRow('Position Mode', self.position_mode)
            layout.addWidget(options)
            self.deps = QtWidgets.QCheckBox('Mirrored Constraints')
            self.deps.setToolTip('Include source-side parent/guide setup dependencies.')
            self.deps.setChecked(True)
            self.change_colour = QtWidgets.QCheckBox('Change Colour')
            self.change_colour.setToolTip('Set mirrored L controls to blue and R controls to red.')
            self.change_colour.setChecked(True)
            toggles = QtWidgets.QHBoxLayout()
            toggles.addWidget(self.deps)
            toggles.addWidget(self.change_colour)
            toggles.addStretch()
            layout.addLayout(toggles)
            self.entries = QtWidgets.QListWidget()
            self.entries.setObjectName('setupList')
            self.entries.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
            layout.addWidget(self.entries, 2)
            self.preview = QtWidgets.QPlainTextEdit()
            self.preview.setReadOnly(True)
            layout.addWidget(self.preview, 3)
            bar = QtWidgets.QHBoxLayout()
            preview_button = QtWidgets.QPushButton('Preview / Calculate Positions')
            self.apply_button = QtWidgets.QPushButton('Apply Mirror')
            self.apply_button.setEnabled(False)
            export_button = QtWidgets.QPushButton('Export Positions JSON')
            close_button = QtWidgets.QPushButton('Close')
            for button in (preview_button, self.apply_button, export_button, close_button):
                bar.addWidget(button)
            layout.addLayout(bar)
            self.nodes.currentIndexChanged.connect(self.populate)
            self.direction.currentIndexChanged.connect(self.populate)
            self.entries.itemSelectionChanged.connect(self.invalidate)
            for combo in (self.axis, self.conflict, self.position_mode):
                combo.currentIndexChanged.connect(self.invalidate)
            self.deps.toggled.connect(self.invalidate)
            self.change_colour.toggled.connect(self.invalidate)
            preview_button.clicked.connect(self.calculate)
            self.apply_button.clicked.connect(self.apply)
            export_button.clicked.connect(self.export)
            close_button.clicked.connect(self.close)
            self.populate()

        def invalidate(self, *args):
            self.plan = None
            self.apply_button.setEnabled(False)
            self.preview.setPlainText('Select setups, then click "Preview / Calculate Positions".')

        def populate(self, *args):
            self.entries.clear()
            self.invalidate()
            n = hou.node(self.nodes.currentText())
            if n is None:
                self.preview.setPlainText('No APEX AutoRig Component with setups was found in the scene.')
                return
            try:
                source, target = self.direction.currentText().split('->')
                for i, row in enumerate(read_setups(n)):
                    driver = row['driver#']
                    item = QtWidgets.QListWidgetItem(driver)
                    item.setData(QtCore.Qt.UserRole, i + 1)
                    if side_name(driver, source, target) == driver:
                        item.setFlags(item.flags() & ~QtCore.Qt.ItemIsSelectable & ~QtCore.Qt.ItemIsEnabled)
                    self.entries.addItem(item)
            except Exception as error:
                self.preview.setPlainText(str(error))

        def calculate(self):
            self.invalidate()
            try:
                n = hou.node(self.nodes.currentText())
                plan = build_plan(n, [x.data(QtCore.Qt.UserRole) for x in self.entries.selectedItems()],
                                  self.direction.currentText(), self.axis.currentText(),
                                  self.conflict.currentData(), self.deps.isChecked(),
                                  self.position_mode.currentData(),
                                  change_colour=self.change_colour.isChecked())
                plan = resolve_world_positions(n, plan)
                lines = []
                for a in plan['actions']:
                    lines.append('%s  [%d] %s → [%s] %s%s' % (
                        a['action'].upper(), a['source_index'], a['source_driver'],
                        a['target_index'] or 'new', a['data']['driver#'],
                        ' (auto dependency)' if a['auto_dependency'] else ''))
                    for key in ('driverguide#', 'driverparent#', 'driventransforms#',
                                'parentconstraint#', 'ignoreoffset#', 'use_t#', 'use_r#', 'use_s#',
                                'offsettrans#', 'offsetrot#', 'offsetscale#', 'color#'):
                        if key in a['data']:
                            lines.append('    %s: %s' % (key.rstrip('#'), a['data'][key]))
                    if 'mirrored_position' in a:
                        lines.append('    SOP Position: %s → %s' % (a['source_position'], a['mirrored_position']))
                self.plan = plan
                self.preview.setPlainText('\n'.join(lines))
                self.apply_button.setEnabled(any(a['action'] != 'skip' for a in plan['actions']))
            except Exception as error:
                self.preview.setPlainText('Cannot mirror:\n' + str(error))

        def apply(self):
            try:
                count = apply_plan(hou.node(self.nodes.currentText()), self.plan)
                self.apply_button.setEnabled(False)
                self.preview.appendPlainText('\nComplete: applied %d setup(s). Use Ctrl+Z to undo. The HIP file has not been saved.' % count)
            except Exception as error:
                self.preview.appendPlainText('\nNo changes applied / changes rolled back: ' + str(error))
                self.apply_button.setEnabled(False)

        def export(self):
            if self.plan is None:
                self.preview.appendPlainText('\nGenerate a preview first.')
                return
            filename, _ = QtWidgets.QFileDialog.getSaveFileName(self, 'Export Mirrored Positions', 'apex_mirror_positions.json', 'JSON (*.json)')
            if filename:
                export_plan(self.plan, filename)
                self.preview.appendPlainText('\nExported: ' + filename)

    previous = getattr(hou.session, '_apex_setup_mirror_dialog', None)
    if previous is not None:
        previous.close()
    dialog = MirrorDialog()
    hou.session._apex_setup_mirror_dialog = dialog
    dialog.show()
    return dialog


if __name__ == '__main__':
    show()
