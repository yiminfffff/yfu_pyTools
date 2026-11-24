r"""Animation Tweaker 1.0.1 -- Houdini Python shelf tool (PySide6 / PySide2).

Shelf tool, Script language: Python:
    import runpy
    runpy.run_path(r"D:\UH\L6SA\Showreel\APEX_Milo\Animation_Tweaker.py", run_name="__main__")

Selected APEX controls (in the Animate state) take precedence over nodes.
Each channel uses its own nearest keys STRICTLY before and after the playhead.
The current key is excluded from the endpoints. value = A + (B-A)*percent/100.
Only animated floating-point node parameters / APEX control channels are used.
APEX integration uses the installed SideFX Animate-state APIs (Houdini 22).
"""

import math
import traceback
import weakref

import hou

try:
    from PySide6 import QtCore, QtGui, QtWidgets
except ImportError:
    from PySide2 import QtCore, QtGui, QtWidgets


VERSION = "1.0.1"
WINDOW_KEY = "_animation_tweaker_window"
EPS = 1e-5  # Frames, including subframe keys.
UNDO_LABEL = "Animation Tweaker"


def state_command(viewer, name, **kwargs):
    args = dict(kwargs)
    viewer.runStateCommand(name, args)
    return args


def selection_context():
    """Return explicit viewport controls, never the Animate SOP's parameters."""
    # Enumerate actual pane tabs; hou.Desktop has no sceneViewers() method.
    # hou.ui.paneTabs() also includes floating Scene Views.
    viewers = [pane for pane in hou.ui.paneTabs()
               if pane.type() == hou.paneTabType.SceneViewer]
    viewers.sort(key=lambda v: not v.isCurrentTab())
    try:
        from apex.ui import statecommandutils as scu
    except ImportError:
        scu = None
    for viewer in viewers:
        if scu is None or not scu.inAnimateState(viewer):
            continue
        from apex.ui.selectionmanager.utils import flattenControlTree
        tree = state_command(viewer, "retrieveSelectedControls").get("ret")
        if tree is None:
            raise ValueError("This APEX state did not return its controller selection.")
        paths = tuple(sorted(set(flattenControlTree(tree, "/", prepend=True))))
        state = state_command(viewer, "getState").get("state")
        if state is None:
            raise ValueError("This Houdini version does not expose the APEX Animate state.")
        return {
            "kind": "apex", "viewer": viewer, "state": state, "paths": paths,
            "token": ("apex", state.scene_node.sessionId(), paths,
                      state.scene.getActiveClipPath(), state.scene.activeLayer().name),
        }
    nodes = tuple(hou.selectedNodes())
    return {"kind": "node", "nodes": nodes,
            "token": ("node", tuple(n.sessionId() for n in nodes))}


def neighbors(frames, frame):
    """Exclude the key at the playhead; do not round subframe times."""
    before = [f for f in frames if f < frame - EPS]
    after = [f for f in frames if f > frame + EPS]
    if not before or not after:
        return None
    return max(before), min(after)


def percentage(value, left, right):
    if abs(right - left) < 1e-10:
        return None
    return 100.0 * (value - left) / (right - left)


class ParmChannel:
    def __init__(self, parm, frame):
        self.parm = parm
        self.frame = frame
        self.label = parm.path()
        self.original = tuple(hou.Keyframe(k) for k in parm.keyframes())
        pair = neighbors([k.frame() for k in self.original], frame)
        if pair is None:
            raise ValueError("needs a key on each side")
        self.previous, self.following = pair
        # Expressions such as ch(), sin($T), etc. must not be overwritten.
        if any(not k.isValueSet() for k in self.original):
            raise ValueError("expression-driven channel")
        left_key = next(k for k in self.original if k.frame() == self.previous)
        right_key = next(k for k in self.original if k.frame() == self.following)
        self.left = left_key.value()
        self.right = right_key.value() if right_key.isValueTied() else right_key.inValue()
        self.current = next((k for k in self.original if abs(k.frame() - frame) <= EPS), None)
        self.initial = float(parm.evalAtFrame(frame))
        self.ratio = percentage(self.initial, self.left, self.right)
        self.template = left_key
        if not all(math.isfinite(v) for v in (self.left, self.right, self.initial)):
            raise ValueError("non-finite channel value")

    def set_percent(self, percent):
        value = self.left + (self.right - self.left) * percent / 100.0
        if self.current is not None:
            key = hou.Keyframe(self.current)
            key.setValue(value)
            if not key.isValueTied():
                key.setInValue(value)
        else:
            key = hou.Keyframe()
            key.setFrame(self.frame)
            key.setValue(value)
            expression = self.template.expression() if self.template.isExpressionSet() else "bezier()"
            key.setExpression(expression, hou.exprLanguage.Hscript)
            key.setSlopeAuto(True)
            key.setInSlopeAuto(True)
        self.parm.setKeyframe(key)

    def restore(self):
        self.parm.deleteAllKeyframes()
        self.parm.setKeyframes(tuple(hou.Keyframe(k) for k in self.original))


class ApexChannel:
    def __init__(self, prim, frame):
        self.frame = frame
        self.name = prim.stringAttribValue("name")
        self.label = self.name
        pair = neighbors(prim.keyFrames(), frame)
        if pair is None:
            raise ValueError("needs a key on each side")
        self.previous, self.following = pair
        self.left = prim.keyValue(self.previous, hou.keyHalf.Out)
        self.right = prim.keyValue(self.following, hou.keyHalf.In)
        self.initial = prim.eval(frame)
        self.ratio = percentage(self.initial, self.left, self.right)
        if not all(math.isfinite(v) for v in (self.left, self.right, self.initial)):
            raise ValueError("non-finite channel value")

    def set_percent(self, prim, percent):
        if not prim.hasKeyAtFrame(self.frame):
            prim.insertKey(self.frame)
        prim.setKeyValue(self.frame, self.left + (self.right - self.left) * percent / 100.0)
        # This respects manual tangents and the segment type (including constant).
        prim.smoothAutoSlopes()


class ApexEdit:
    """One binding on the active animation layer, including exact geo snapshots."""
    def __init__(self, state, rig_path, names, frame, skipped):
        self.state_ref = weakref.ref(state)
        self.binding_path = rig_path + "/animbinding"
        self.binding = state.scene.getData(self.binding_path)
        self.layer = state.scene.activeLayer().name
        self.path = self.binding.activeLayerGeoPath(state.scene)
        geo = state.scene.getData(self.path)
        self.original = hou.Geometry(geo, True)
        self.working = hou.Geometry(geo, True)
        self.channels = []
        self.prims = {}
        wanted = set(names)
        for prim in self.working.prims():
            if not isinstance(prim, hou.ChannelPrim):
                continue
            name = prim.stringAttribValue("name")
            if name not in wanted:
                continue
            try:
                # Pending poses are deliberately preserved. Do not silently
                # discard an animator's uncommitted pose to edit its keys.
                if self.binding.channelIsPending(state.scene, name):
                    raise ValueError("commit or discard the pending pose first")
                channel = ApexChannel(prim, frame)
                self.channels.append(channel)
                self.prims[name] = prim
            except (ValueError, hou.Error) as error:
                skipped.append(name + ": " + str(error))

    @property
    def state(self):
        state = self.state_ref()
        if state is None:
            raise ValueError("The APEX Animate state has closed.")
        return state

    def install(self, geo):
        self.state.scene.setData(self.path, geo)
        self.binding.setNeedsUpdate()

    def set_percent(self, percent):
        for channel in self.channels:
            channel.set_percent(self.prims[channel.name], percent)
        self.install(self.working)

    def restore(self):
        self.install(hou.Geometry(self.original, True))

    def register_undo(self):
        from apex.stateutils_2 import SetChannelsUndo
        from apex.utils import addAnimateStateUndo
        addAnimateStateUndo(SetChannelsUndo(
            self.state, self.binding_path, hou.Geometry(self.original, True),
            hou.Geometry(self.working, True), layer=self.layer), UNDO_LABEL)


def refresh_apex(state):
    state.pushChannels(include_pinned_channels=True)
    state.scene.updateEvaluationParms(hou.frame())
    state.runSceneCallbacks()
    state.scene_viewer.curViewport().draw()
    if state.hud_channel_panel is not None:
        state.hud_channel_panel.onParmChangedEvent({})


class EditBatch:
    def __init__(self, context, frame):
        self.context = context
        self.frame = frame
        self.fps = hou.fps()
        self.channels = []
        self.apex_edits = []
        self.skipped = []
        self.percent = None
        self.changed = False
        if not hou.undos.areEnabled():
            raise ValueError("Enable Houdini undo before using Animation Tweaker.")
        if context["kind"] == "apex":
            self._gather_apex()
        else:
            for node in context["nodes"]:
                for parm in node.parms():
                    if not isinstance(parm.parmTemplate(), hou.FloatParmTemplate):
                        continue
                    if not parm.keyframes():
                        continue
                    try:
                        if parm.isLocked() or parm.isDisabled():
                            raise ValueError("locked or disabled")
                        self.channels.append(ParmChannel(parm, frame))
                    except (ValueError, hou.Error) as error:
                        self.skipped.append(parm.path() + ": " + str(error))

    def _gather_apex(self):
        from apex.control_2 import getParmsForControls
        state = self.context["state"]
        if not state._nodeIsWritable():
            raise ValueError("The APEX Animate node is not writable.")
        if not state.scene.isLayerEditable(state.scene.activeLayer().name):
            raise ValueError("The active APEX animation layer is locked.")
        if state.scene_node.parm("animation") is None:
            raise ValueError("This APEX state has no writable animation stash.")
        mapping = getParmsForControls(state.scene, self.context["paths"],
                                      channels=True, include_controlgroups=True,
                                      include_non_animatable=False)
        for rig_path, names in mapping.items():
            binding = state.scene.getData(rig_path + "/animbinding")
            if binding is None or state.scene.getData(binding.activeLayerGeoPath(state.scene)) is None:
                self.skipped.append(rig_path + ": no animation on the active layer")
                continue
            edit = ApexEdit(state, rig_path, names, self.frame, self.skipped)
            if edit.channels:
                self.apex_edits.append(edit)
                self.channels.extend(edit.channels)

    def preview(self, percent):
        if not self.channels:
            return
        # Mark before the first write so even a partially failed multi-channel
        # preview is rolled back by the caller.
        self.changed = True
        with hou.undos.disabler():
            if self.apex_edits:
                for edit in self.apex_edits:
                    edit.set_percent(percent)
                refresh_apex(self.apex_edits[0].state)
            else:
                for channel in self.channels:
                    channel.set_percent(percent)
        self.percent = percent

    def restore(self):
        errors = []
        with hou.undos.disabler():
            for item in self.apex_edits or self.channels:
                try:
                    item.restore()
                except Exception as error:
                    errors.append(str(error))
            if self.apex_edits:
                refresh_apex(self.apex_edits[0].state)
        if errors:
            raise ValueError("Some channels could not be restored:\n" + "\n".join(errors[:5]))

    def commit(self):
        if not self.changed:
            return
        # Never leave an undo group open across Qt events. Only the final edit
        # is recorded, so previews cannot swallow other actions into one undo.
        if self.apex_edits:
            with hou.undos.group(UNDO_LABEL):
                for edit in self.apex_edits:
                    edit.register_undo()
                state = self.apex_edits[0].state
                # Both the live binding and the persistent HIP stash participate
                # in this same undo group. Saving/reopening retains the result.
                state._writeAnimationToGeometryParm(
                    state.scene_node.parm("animation"),
                    pattern=state.scene.getActiveClipPath() + "**")
        else:
            self.restore()
            with hou.undos.group(UNDO_LABEL):
                for channel in self.channels:
                    channel.set_percent(self.percent)
        self.changed = False


class TweenSlider(QtWidgets.QSlider):
    begin = QtCore.Signal()
    finish = QtCore.Signal()
    cancel = QtCore.Signal()

    def __init__(self, parent=None):
        super().__init__(QtCore.Qt.Horizontal, parent)
        self.setRange(-500, 1500)  # Tenths of a percent.
        self.setValue(500)
        self.setSingleStep(1)
        self.setPageStep(100)
        self.setMinimumHeight(62)
        self.setFocusPolicy(QtCore.Qt.StrongFocus)
        self._active = False
        self._keyboard = False

    def option(self):
        opt = QtWidgets.QStyleOptionSlider()
        self.initStyleOption(opt)
        opt.rect.adjust(12, 0, -12, -26)
        opt.subControls = (QtWidgets.QStyle.SC_SliderGroove
                           | QtWidgets.QStyle.SC_SliderHandle)
        if self._active:
            opt.activeSubControls = QtWidgets.QStyle.SC_SliderHandle
            opt.state |= QtWidgets.QStyle.State_Sunken
        return opt

    def span(self):
        opt = self.option()
        handle = self.style().subControlRect(QtWidgets.QStyle.CC_Slider, opt,
                                            QtWidgets.QStyle.SC_SliderHandle, self)
        return opt.rect.left() + handle.width() / 2.0, max(1, opt.rect.width() - handle.width())

    def value_x(self, value):
        start, length = self.span()
        return start + length * (value - self.minimum()) / (self.maximum() - self.minimum())

    def set_from_mouse(self, event):
        x = event.position().x() if hasattr(event, "position") else event.pos().x()
        start, length = self.span()
        value = self.minimum() + (x - start) / length * (self.maximum() - self.minimum())
        if not event.modifiers() & QtCore.Qt.ShiftModifier:
            for detent in (0, 1000):
                if abs(x - self.value_x(detent)) <= 6:
                    value = detent
                    break
        self.setValue(max(self.minimum(), min(self.maximum(), round(value))))

    def paintEvent(self, event):
        painter = QtWidgets.QStylePainter(self)
        opt = self.option()
        painter.drawComplexControl(QtWidgets.QStyle.CC_Slider, opt)
        for value in (-500, 0, 500, 1000, 1500):
            x = round(self.value_x(value))
            painter.setPen(self.palette().color(QtGui.QPalette.Mid))
            painter.drawLine(x, self.height() - 27, x, self.height() - 22)
            painter.setPen(self.palette().color(QtGui.QPalette.Text))
            painter.drawText(QtCore.QRect(x - 23, self.height() - 21, 46, 20),
                             QtCore.Qt.AlignCenter, str(value // 10))

    def mousePressEvent(self, event):
        if event.button() != QtCore.Qt.LeftButton:
            event.ignore()
            return
        self.setFocus()
        self._active = True
        self.setSliderDown(True)
        self.begin.emit()
        self.set_from_mouse(event)
        event.accept()

    def mouseMoveEvent(self, event):
        if self._active:
            self.set_from_mouse(event)
        event.accept()

    def mouseReleaseEvent(self, event):
        if self._active and event.button() == QtCore.Qt.LeftButton:
            self.set_from_mouse(event)
            self.end_gesture()
        event.accept()

    def end_gesture(self):
        was_active = self._active or self._keyboard
        self._active = self._keyboard = False
        self.setSliderDown(False)
        if was_active:
            self.finish.emit()

    def abort_gesture(self):
        self._active = self._keyboard = False
        self.setSliderDown(False)

    def keyPressEvent(self, event):
        if event.key() == QtCore.Qt.Key_Escape:
            self.abort_gesture()
            self.cancel.emit()
            return
        keys = (QtCore.Qt.Key_Left, QtCore.Qt.Key_Right, QtCore.Qt.Key_Up,
                QtCore.Qt.Key_Down, QtCore.Qt.Key_Home, QtCore.Qt.Key_End,
                QtCore.Qt.Key_PageUp, QtCore.Qt.Key_PageDown)
        if event.key() in keys:
            if not self._keyboard:
                self._keyboard = True
                self.begin.emit()
            super().keyPressEvent(event)
        else:
            super().keyPressEvent(event)

    def keyReleaseEvent(self, event):
        if self._keyboard and not event.isAutoRepeat():
            self.end_gesture()
        super().keyReleaseEvent(event)

    def focusOutEvent(self, event):
        self.end_gesture()
        super().focusOutEvent(event)

    def wheelEvent(self, event):
        event.ignore()


class TweakerWindow(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Animation Tweaker")
        self.setObjectName("Animation_Tweaker")
        self.setWindowFlag(QtCore.Qt.WindowContextHelpButtonHint, False)
        self.setAttribute(QtCore.Qt.WA_DeleteOnClose)
        self.resize(540, 170)
        self.setMinimumWidth(410)
        self._batch = None
        self._signature = None
        self._busy = False
        self._closed = False
        self._shortcuts = []

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 12)
        row = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel("ANIMATION TWEAKER")
        title.setStyleSheet("font-weight: bold; font-size: 14px;")
        row.addWidget(title)
        row.addStretch()
        self.amount = QtWidgets.QDoubleSpinBox()
        self.amount.setRange(-50, 150)
        self.amount.setDecimals(1)
        self.amount.setSuffix(" %")
        self.amount.setValue(50)
        self.amount.setKeyboardTracking(False)
        self.amount.setFixedWidth(105)
        row.addWidget(self.amount)
        layout.addLayout(row)
        self.slider = TweenSlider()
        layout.addWidget(self.slider)
        buttons = QtWidgets.QHBoxLayout()
        self.preset_buttons = []
        for value in (0, 50, 100):
            button = QtWidgets.QPushButton(str(value) + " %")
            button.clicked.connect(lambda checked=False, p=value: self.apply_once(p))
            self.preset_buttons.append(button)
            buttons.addWidget(button)
        buttons.addStretch()
        refresh_button = QtWidgets.QPushButton("Refresh")
        refresh_button.clicked.connect(lambda: self.refresh(force=True))
        buttons.addWidget(refresh_button)
        layout.addLayout(buttons)
        self.status = QtWidgets.QLabel()
        self.status.setWordWrap(True)
        self.status.hide()
        layout.addWidget(self.status)

        self.slider.begin.connect(self.begin)
        self.slider.finish.connect(self.finish)
        self.slider.cancel.connect(self.cancel)
        self.slider.valueChanged.connect(self.slide)
        self.amount.valueChanged.connect(self.apply_once)
        shortcut_type = getattr(QtGui, "QShortcut", None) or QtWidgets.QShortcut
        for sequence, redo in (("Ctrl+Z", False), ("Ctrl+Shift+Z", True), ("Ctrl+Y", True)):
            shortcut = shortcut_type(QtGui.QKeySequence(sequence), self)
            shortcut.setContext(QtCore.Qt.WindowShortcut)
            shortcut.activated.connect(lambda r=redo: self.history(r))
            self._shortcuts.append(shortcut)

        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(300)
        self.timer.timeout.connect(self.poll)
        self.timer.start()
        hou.hipFile.addEventCallback(self.hip_event)
        self.refresh(force=True)

    def display_percent(self, percent):
        self.slider.blockSignals(True)
        self.amount.blockSignals(True)
        self.slider.setValue(round(max(-50, min(150, percent)) * 10))
        self.amount.setValue(percent)
        self.slider.blockSignals(False)
        self.amount.blockSignals(False)

    def report_error(self, error):
        self.status.setText(str(error))
        self.status.setToolTip(traceback.format_exc())
        self.status.show()

    def signature(self, context):
        return (hou.frame(), hou.fps(), context["token"],
                tuple(hou.undos.undoLabels()), tuple(hou.undos.redoLabels()))

    def refresh(self, force=False):
        if self._busy or self._batch is not None or self._closed:
            return
        try:
            context = selection_context()
            signature = self.signature(context)
            if not force and signature == self._signature:
                return
            self._signature = signature
            batch = EditBatch(context, hou.frame())
            count = len(batch.channels)
            self.slider.setEnabled(bool(count))
            self.amount.setEnabled(bool(count))
            for button in self.preset_buttons:
                button.setEnabled(bool(count))
            mode = "APEX" if context["kind"] == "apex" else "Node controls"
            context_text = "Frame {:g} | {} | {} channel{}".format(
                hou.frame(), mode, count, "" if count == 1 else "s")
            ratios = [c.ratio for c in batch.channels if c.ratio is not None]
            mixed = ratios and max(ratios) - min(ratios) > 0.15
            # Mixed poses have no single percentage. Show a neutral handle;
            # do not modify the scene until the user actually changes it.
            self.display_percent(sum(ratios) / len(ratios) if ratios and not mixed else 50)
            text = "Mixed channel percentages; dragging applies one percentage to all." if mixed else "Ready. Each release is one undo step."
            if count == 0:
                text = "Select animated controls with a key strictly before AND after the current frame."
            if batch.skipped:
                text += "  Skipped {} channel(s); hover for details.".format(len(batch.skipped))
            # Keep normal usage free of explanatory labels. Context and skipped
            # channel details remain available on hover; only errors get a row.
            details = context_text + "\n" + text
            if batch.skipped:
                details += "\n" + "\n".join(batch.skipped)
            self.setToolTip(details)
            self.status.clear()
            self.status.hide()
        except Exception as error:
            self.slider.setEnabled(False)
            self.amount.setEnabled(False)
            for button in self.preset_buttons:
                button.setEnabled(False)
            self.report_error(error)

    def begin(self):
        if self._busy or self._batch is not None:
            return
        try:
            context = selection_context()
            batch = EditBatch(context, hou.frame())
            if not batch.channels:
                raise ValueError("No editable channels have a key on both sides of the current frame.")
            self._batch = batch
        except Exception as error:
            self.report_error(error)

    def slide(self, raw):
        self.amount.blockSignals(True)
        self.amount.setValue(raw / 10.0)
        self.amount.blockSignals(False)
        if self._batch is None or self._busy:
            return
        try:
            self._busy = True
            if (abs(hou.frame() - self._batch.frame) > EPS
                    or hou.fps() != self._batch.fps):
                raise ValueError("The playhead changed; the drag was cancelled.")
            self._batch.preview(raw / 10.0)
        except Exception as error:
            self._busy = False
            self.cancel()
            self.report_error(error)
        finally:
            self._busy = False

    def finish(self):
        batch, self._batch = self._batch, None
        if batch is None:
            return
        try:
            if abs(hou.frame() - batch.frame) > EPS or hou.fps() != batch.fps:
                batch.restore()
            else:
                batch.commit()
            self.refresh(force=True)
        except Exception as error:
            try:
                batch.restore()
            except Exception as restore_error:
                error = ValueError(str(error) + "\nRestore: " + str(restore_error))
            self.report_error(error)

    def cancel(self):
        self.slider.abort_gesture()
        batch, self._batch = self._batch, None
        try:
            if batch is not None and batch.changed:
                batch.restore()
            self.refresh(force=True)
        except Exception as error:
            self.report_error(error)

    def apply_once(self, percent):
        if self._busy:
            return
        self.finish()
        self.begin()
        if self._batch is not None:
            self.display_percent(percent)
            self.slide(round(percent * 10))
            self.finish()

    def history(self, redo=False):
        was_editing = self._batch is not None
        self.cancel()
        if was_editing:
            return
        try:
            (hou.undos.performRedo if redo else hou.undos.performUndo)()
            self.refresh(force=True)
        except Exception as error:
            self.report_error(error)

    def poll(self):
        if self._busy or self._closed or hou.hipFile.isLoadingHipFile():
            return
        if self._batch is not None:
            try:
                context = selection_context()
                if (abs(hou.frame() - self._batch.frame) > EPS
                        or hou.fps() != self._batch.fps
                        or context["token"] != self._batch.context["token"]):
                    self.cancel()
                elif (self.slider._active and not QtWidgets.QApplication.mouseButtons() & QtCore.Qt.LeftButton):
                    self.slider.end_gesture()
            except Exception as error:
                self.cancel()
                self.report_error(error)
        else:
            self.refresh()

    def hip_event(self, event):
        if event in tuple(getattr(hou.hipFileEventType, n, None)
                          for n in ("BeforeLoad", "BeforeClear", "BeforeMerge", "BeforeSave")):
            self.cancel()
        self._signature = None

    def keyPressEvent(self, event):
        if event.key() == QtCore.Qt.Key_Escape:
            if self._batch is not None:
                self.cancel()
            else:
                self.close()
            event.accept()
        else:
            super().keyPressEvent(event)

    def closeEvent(self, event):
        self.slider.end_gesture()
        self.finish()
        self.timer.stop()
        self._closed = True
        try:
            hou.hipFile.removeEventCallback(self.hip_event)
        except hou.Error:
            pass
        if getattr(hou.session, WINDOW_KEY, None) is self:
            setattr(hou.session, WINDOW_KEY, None)
        super().closeEvent(event)


def show():
    old = getattr(hou.session, WINDOW_KEY, None)
    if old is not None:
        try:
            old.close()
        except RuntimeError:
            pass
    window = TweakerWindow(hou.qt.mainWindow())
    setattr(hou.session, WINDOW_KEY, window)
    window.show()
    window.raise_()
    window.activateWindow()
    return window


if __name__ == "__main__":
    show()
