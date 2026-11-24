r"""Animation Motion Trails 1.1.5 -- Houdini 22 APEX Scene Animate shelf tool.

Shelf tool (Python):
    import runpy
    runpy.run_path(r"D:\UH\L6SA\Showreel\APEX_Milo\Animation_Motion_Trails.py", run_name="__main__")

Select ONE APEX transform control, click Locator, drag its viewport axes or center,
then click Build (or press Enter in the viewport). Delete removes list selections.
The playback range, sampling, cache updates, and guide size are automatic.
The locator is a local offset, not a new rig control or constraint. Nothing is
inserted into the rig, its channels, or its SOP connections. Definitions are
stored as versioned JSON user data on the Animate SOP and travel with the HIP.

Trails are viewport guides, not render geometry. The window must remain open.
Closing releases the observers/drawables/timer; reopen to restore saved trails.
Sampling uses the installed SideFX control-output API, without hou.setFrame or
mesh evaluation. All HOM/APEX work stays on the UI thread. A read-only native
animation observer invalidates caches; small idle batches rebuild after edits.
Playback, scrubbing and control dragging suspend off-frame sampling.

Support target: Houdini 22.0 (APEX APIs checked against 22.0.466). This file uses
SideFX's internal APEX Python APIs, which can change in later Houdini releases.
"""

import json
import math
import os
import time
import traceback
import uuid
import weakref
from collections import deque

import hou
from apex.control_2 import ControlManager, TransformControl, controlRigPath
from apex.sceneevaluators import AnimationEvaluatorBase
from apex.ui import statecommandutils as scu
from apex.ui.selectionmanager.utils import flattenControlTree

try:
    from PySide6 import QtCore, QtWidgets
except ImportError:
    from PySide2 import QtCore, QtWidgets


VERSION = "1.1.5"
DATA_KEY = "animation_motion_trails_v1"
WINDOW_KEY = "_animation_motion_trails_window"
OBSERVER_KEY = "animation_motion_trails_v1"
PLACE_STATE = "animation_motion_trails_place_v5"
MAX_SAMPLES = 10000
SETTLE_SECONDS = 0.22
BATCH_SECONDS = 0.005
BATCH_FRAMES = 3
COLORS = ((0.1, 0.85, 1.0), (1.0, 0.55, 0.15), (0.55, 1.0, 0.35),
          (1.0, 0.35, 0.7), (0.7, 0.55, 1.0))


def state_command(viewer, name, **kwargs):
    args = dict(kwargs)
    viewer.runStateCommand(name, args)
    return args


def active_context(node=None):
    viewers = [p for p in hou.ui.paneTabs()
               if p.type() == hou.paneTabType.SceneViewer]
    viewers.sort(key=lambda p: not p.isCurrentTab())
    for viewer in viewers:
        if scu.inAnimateState(viewer):
            state = state_command(viewer, "getState").get("state")
            if state is not None and (node is None or state.scene_node == node):
                return viewer, state
    return None, None


def selected_path(viewer):
    tree = state_command(viewer, "retrieveSelectedControls").get("ret")
    if tree is None:
        raise ValueError("This APEX state did not return its selected controls.")
    paths = flattenControlTree(tree, "/", prepend=True)
    if len(paths) != 1:
        raise ValueError("Select exactly one APEX transform control in the viewport.")
    return paths[0]


def path_refs(viewer, paths, resolve=False):
    name = "resolvePathRefs" if resolve else "buildPathRefs"
    result = state_command(viewer, name, paths=list(paths)).get("ret")
    return list(result) if result is not None else list(paths)


def read_control_matrix(manager, path):
    """Copy evaluated control values without a sequence/copy constructor.

    A numeric scalar creates the HOM matrix, and setAt writes its components.
    Errors from APEX value retrieval are included in the control-path context,
    since those may occur before any Python-side conversion takes place.
    """
    value = None
    try:
        value = manager.getControlXform(path)
        values = value.asTuple() if hasattr(value, "asTuple") else value
        values = tuple(values)
        if len(values) == 4:
            rows = tuple(tuple(row) for row in values)
            if any(len(row) != 4 for row in rows):
                raise ValueError("expected four rows of four values")
            values = tuple(component for row in rows for component in row)
        if len(values) != 16:
            raise ValueError("expected 16 values")
        values = tuple(float(component) for component in values)
        if not all(math.isfinite(component) for component in values):
            raise ValueError("matrix contains non-finite values")
        result = hou.Matrix4(1.0)
        for index, component in enumerate(values):
            result.setAt(index // 4, index % 4, component)
        return result
    except Exception as error:
        raise ValueError("Cannot read the transform for {} ({}): {}".format(
            path, type(value).__name__, error)) from error


def default_data():
    start, end = hou.playbar.playbackRange()
    return {"version": 1, "start": int(start), "end": int(end), "step": 1,
            "auto": True, "markers": True, "locator_size": 0.05, "trails": []}


def parse_data(raw):
    if not raw:
        return default_data()
    try:
        data = json.loads(raw)
        if data["version"] != 1:
            raise ValueError("Unsupported data version")
        sample_frames(data["start"], data["end"], data["step"])
        if (not isinstance(data["auto"], bool) or not isinstance(data["markers"], bool)
                or not 0 < data["locator_size"] < 1e6):
            raise ValueError("Invalid settings")
        seen = set()
        for item in data["trails"]:
            if (not isinstance(item["id"], str) or item["id"] in seen
                    or not isinstance(item["name"], str) or not item["name"].strip()
                    or not isinstance(item["parent"], str) or not item["parent"]
                    or not isinstance(item["built"], bool)
                    or not isinstance(item["visible"], bool)
                    or len(item["offset"]) != 3 or len(item["color"]) != 3
                    or not all(math.isfinite(float(v)) for v in item["offset"])
                    or not all(0 <= float(v) <= 1 for v in item["color"])):
                raise ValueError("Invalid locator definition")
            seen.add(item["id"])
        return data
    except (KeyError, TypeError, ValueError, OverflowError) as error:
        raise ValueError("Saved motion-trail data cannot be read. It has been preserved.\n" + str(error))


def sample_frames(start, end, step):
    if (any(isinstance(v, bool) or not isinstance(v, int) for v in (start, end, step))
            or step < 1 or end < start):
        raise ValueError("Use an end frame >= start frame and a positive integer step.")
    count = (end - start) // step + 1 + bool((end - start) % step)
    if count > MAX_SAMPLES:
        raise ValueError("Too many samples. Shorten the range or increase Step (maximum 10,000 frames).")
    frames = list(range(start, end + 1, step))
    if frames[-1] != end:
        frames.append(end)
    return frames


def sample_order(frames, current):
    return deque(sorted(frames, key=lambda f: (abs(f - current), f)))


def object_node(node):
    parent = node.parent()
    while parent is not None:
        if isinstance(parent, hou.ObjNode):
            return parent
        parent = parent.parent()
    raise ValueError("This tool requires a SOP APEX Scene Animate inside an OBJ geometry container.")


def _drawable(viewer, name):
    geo = hou.Geometry()
    geo.addAttrib(hou.attribType.Point, "Cd", (1.0, 1.0, 1.0))
    drawable = hou.SimpleDrawable(viewer, geo, name)
    drawable.setDisplayMode(hou.drawableDisplayMode.WireframeMode)
    drawable.setUseWireframeColor(False)
    drawable.enable(True)
    return geo, drawable


def _lines(geo, segments, color):
    geo.clear()
    geo.addAttrib(hou.attribType.Point, "Cd", tuple(color))
    for segment in segments:
        if len(segment) < 2:
            continue
        points = geo.createPoints(segment)
        poly = geo.createPolygon(is_closed=False)
        for point in points:
            poly.addVertex(point)


class TrailGuide:
    def __init__(self, viewer, item):
        uid = uuid.uuid4().hex
        self.line_geo, self.line = _drawable(viewer, "mt_line_" + uid)
        self.locator_geo, self.locator = _drawable(viewer, "mt_locator_" + uid)
        self.item = item
        self.has_curve = False
        self.has_locator = False
        self.last_locator = None
        self.last_transform = None
        self.last_visibility = None

    def show(self, visible, selected=False):
        flags = (visible and self.item["visible"] and self.has_curve,
                 visible and self.item["visible"] and self.has_locator and selected)
        if flags != self.last_visibility:
            self.line.show(flags[0])
            self.locator.show(flags[1])
            self.last_visibility = flags

    def transform(self, world_to_model):
        signature = world_to_model.asTuple()
        if signature != self.last_transform:
            self.line.setTransform(world_to_model)
            self.locator.setTransform(world_to_model)
            self.last_transform = signature

    def curve(self, frames, matrices, markers, size):
        positions = [hou.Vector3(self.item["offset"]) * matrices[f] for f in frames]
        segments = [positions]
        if markers:
            # Three short axes give per-frame dots without creating SOP nodes.
            # Keep markers usable on very long timelines by decimating to 500.
            stride = max(1, (len(positions) + 499) // 500)
            radius = size * 0.07
            for pos in positions[::stride]:
                for axis in ((radius, 0, 0), (0, radius, 0), (0, 0, radius)):
                    delta = hou.Vector3(axis)
                    segments.append([pos - delta, pos + delta])
        _lines(self.line_geo, segments, self.item["color"])
        self.has_curve = len(positions) >= 2

    def current(self, matrix, size):
        pos = hou.Vector3(self.item["offset"]) * matrix
        signature = tuple(pos) + (size,)
        if signature == self.last_locator:
            return False
        self.last_locator = signature
        segments = []
        for axis in ((size, 0, 0), (0, size, 0), (0, 0, size)):
            delta = hou.Vector3(axis)
            segments.append([pos - delta, pos + delta])
        _lines(self.locator_geo, segments, (1.0, 0.95, 0.2))
        self.has_locator = True
        return True

    def dispose(self):
        for drawable in (self.line, self.locator):
            drawable.show(False)
            drawable.enable(False)


class TrailObserver(AnimationEvaluatorBase):
    """Observe native changes, but NEVER request its synchronous range sampler."""
    def __init__(self, owner, state):
        super().__init__(state.scene)
        self._owner = weakref.ref(owner)
        self._state = weakref.ref(state)
        self.controls = ControlManager()
        self.paths = {}
        self.missing = []
        refs = [item["parent"] for item in owner.data["trails"] if item["visible"]]
        resolved = path_refs(owner.viewer, refs, resolve=True)
        for ref, path in zip(refs, resolved):
            # ControlMap is a pybind map, not a Python dict: default is required.
            ctrl = state.scene.control_manager.controls.get(path, None)
            if not isinstance(ctrl, TransformControl):
                self.missing.append(ref)
                continue
            self.paths[ref] = path
            if path not in self.controls.controls:
                self.controls.addControl(state.scene, path)
                self.trackRig(controlRigPath(path))

    @property
    def state(self):
        return self._state()

    def needsUpdate(self, frame):
        owner = self._owner()
        if owner is not None and not owner.sampling:
            owner.observe(self)
        return False

    def draw(self):
        # Native AnimationEvaluatorCollection.draw() can run before the scene
        # has returned from an off-frame sample. Read the current pose in the
        # UI timer AFTER the complete native scene-callback cycle instead.
        pass

    def update(self, frame):
        # Native control drags call updateAtFrame separately from needsUpdate.
        owner = self._owner()
        if owner is not None and not owner.sampling:
            owner.invalidate()

    def onActiveLayerChanged(self):
        self.setDirty(True)

    def onResume(self):
        self.setDirty(True)


def plane_position(viewport, x, y, point, normal):
    """Unproject a viewport pixel onto a plane in world space."""
    direction, origin = (tuple(v) for v in viewport.mapToWorld(x, y))
    divisor = sum(d * n for d, n in zip(direction, normal))
    if abs(divisor) < 1e-10:
        return None
    distance = sum((p - o) * n for p, o, n in zip(point, origin, normal)) / divisor
    return tuple(o + d * distance for o, d in zip(origin, direction))


def axis_parameter(viewport, x, y, point, axis):
    """Closest point on a world axis to the pointer ray; no depth snapping."""
    direction, origin = (tuple(v) for v in viewport.mapToWorld(x, y))
    delta = tuple(o - p for o, p in zip(origin, point))
    dd = sum(d * d for d in direction)
    ad = sum(a * d for a, d in zip(axis, direction))
    divisor = dd - ad * ad  # axis is a unit vector
    if dd < 1e-20 or divisor / dd < 1e-8:
        return None
    return (dd * sum(a * w for a, w in zip(axis, delta))
            - ad * sum(d * w for d, w in zip(direction, delta))) / divisor


class LocatorPlacementState:
    """Nodeless shelf state with a drawn, pickable world-space translate gizmo.

    Template handles are not instantiated for a nodeless state. Draw the axes
    explicitly instead, and convert mouse rays to the locator's parent offset.
    Neither placement nor dragging modifies any rig/animation parameter.
    """
    AXES = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
    COLORS = ((1.0, 0.25, 0.2, 1.0), (0.25, 1.0, 0.3, 1.0),
              (0.3, 0.65, 1.0, 1.0), (1.0, 0.95, 0.2, 1.0))

    def __init__(self, state_name, scene_viewer):
        self.viewer = scene_viewer
        self.drag = None
        self.hover_axis = None
        self.layout = None
        self.signature = None
        self.geometries, self.drawables = [], []
        for color in self.COLORS:
            geo = hou.Geometry()
            drawable = hou.GeometryDrawable(scene_viewer, hou.drawableGeometryType.Line,
                "mt_place_" + uuid.uuid4().hex, geometry=geo,
                params={"color1": color, "line_width": 3.0, "fade_factor": 1.0})
            drawable.show(True)
            self.geometries.append(geo)
            self.drawables.append(drawable)

    def owner(self):
        window = getattr(hou.session, WINDOW_KEY, None)
        if window is not None and window.placement:
            return window
        return None

    def onEnter(self, kwargs):
        owner = self.owner()
        if owner is None:
            return
        flags = kwargs.get("state_flags")
        if flags is not None:
            flags["mouse_drag"] = True
            flags["redraw"] = True
            flags["indirect_handle_drag"] = False
        self.viewer.setPromptMessage("Drag axes / yellow center. Build / Enter: create trail. Esc: cancel.")
        owner.preview_offset(owner.placement["item"]["offset"])
        self.update_gizmo(self.viewer.curViewport())

    def onGenerate(self, kwargs):
        # setCurrentState() from a shelf enters here, rather than onEnter().
        self.onEnter(kwargs)

    def update_gizmo(self, viewport):
        owner = self.owner()
        if owner is None:
            return None
        placement = owner.placement
        pos = tuple(hou.Vector3(placement["item"]["offset"]) * placement["parent_world"])
        center = tuple(viewport.mapToScreen(hou.Vector3(pos)))
        normal = tuple(viewport.mapToWorld(*center)[0])
        across = plane_position(viewport, center[0] + 64, center[1], pos, normal)
        if across is None:
            return None
        length = math.sqrt(sum((a - p) ** 2 for a, p in zip(across, pos)))
        if not math.isfinite(length) or length <= 1e-12:
            return None
        segments, tips = [], []
        for axis in self.AXES:
            end = tuple(p + a * length for p, a in zip(pos, axis))
            tip = tuple(viewport.mapToScreen(hou.Vector3(end)))
            dx, dy = tip[0] - center[0], tip[1] - center[1]
            pixels = math.hypot(dx, dy)
            lines = []
            # An axis pointing straight into the view has no usable pick area.
            tips.append(tip if pixels >= 8 else None)
            if pixels >= 8:
                wings = [plane_position(viewport,
                    tip[0] - 9 * dx / pixels + sign * 4 * dy / pixels,
                    tip[1] - 9 * dy / pixels - sign * 4 * dx / pixels, end, normal)
                    for sign in (-1, 1)]
                lines = [[pos, end]]
                if all(wing is not None for wing in wings):
                    lines.append([wings[0], end, wings[1]])
            segments.append(lines)
        ring = [plane_position(viewport, center[0] + x, center[1] + y, pos, normal)
                for x, y in ((0, 7), (7, 0), (0, -7), (-7, 0), (0, 7))]
        segments.append([ring] if all(p is not None for p in ring) else [])
        world_to_model = (object_node(owner.node).worldTransform().inverted()
                         * viewport.modelToGeometryTransform().inverted())
        signature = (tuple(tuple(tuple(p) for p in line) for lines in segments for line in lines),
                     world_to_model.asTuple())
        if signature != self.signature:
            for geo, drawable, lines in zip(self.geometries, self.drawables, segments):
                _lines(geo, lines, (1.0, 1.0, 1.0))
                drawable.setGeometry(geo)
                drawable.setTransform(world_to_model)
            self.signature = signature
        self.layout = {"position": pos, "center": center, "normal": normal, "tips": tips}
        return self.layout

    def onDraw(self, kwargs):
        owner = self.owner()
        if owner is None:
            return
        try:
            event = kwargs.get("ui_event")
            viewport = event.curViewport() if event is not None else self.viewer.curViewport()
            if self.update_gizmo(viewport) is not None:
                selected = self.drag["axis"] if self.drag else self.hover_axis
                for index, drawable in enumerate(self.drawables):
                    params = ({"color1": (1.0, 1.0, 0.3, 1.0), "line_width": 5.0}
                              if index == selected else {})
                    drawable.draw(kwargs["draw_handle"], params)
        except hou.ObjectWasDeleted:
            pass
        except Exception as error:
            owner.report(error)

    def onDrawInterrupt(self, kwargs):
        # Keep the locator visible when the pointer is over the tool's buttons.
        self.onDraw(kwargs)

    def pick_axis(self, layout, x, y):
        cx, cy = layout["center"]
        if math.hypot(x - cx, y - cy) <= 10:
            return 3  # The yellow diamond moves in the view plane.
        nearest, best = None, 8.0
        for index, tip in enumerate(layout["tips"]):
            if tip is None:
                continue
            dx, dy = tip[0] - cx, tip[1] - cy
            t = max(0.0, min(1.0, ((x - cx) * dx + (y - cy) * dy) / (dx * dx + dy * dy)))
            distance = math.hypot(x - cx - t * dx, y - cy - t * dy)
            if distance < best:
                nearest, best = index, distance
        return nearest

    def onMouseEvent(self, kwargs):
        owner = self.owner()
        if owner is None:
            return False
        event = kwargs["ui_event"]
        device, reason = event.device(), event.reason()
        if device.isAltKey():
            self.drag = None
            self.hover_axis = None
            return False
        viewport = event.curViewport()
        # Derive the pointer position with the same projection used for the
        # gizmo. UI mouse pixels may use a different DPI scale/view origin.
        origin, direction = (tuple(value) for value in event.ray())
        ray_point = hou.Vector3(tuple(o + d for o, d in zip(origin, direction)))
        x, y = viewport.mapToScreen(ray_point)
        if self.drag is None:
            layout = self.update_gizmo(viewport)
            axis = self.pick_axis(layout, x, y) if layout else None
            self.hover_axis = axis
            # The event reason defines the LMB lifecycle. Do not additionally
            # gate Start/Active on a separate raw button-state snapshot.
            if reason != hou.uiEventReason.Start:
                return False
            if axis is None:
                return False
            point, normal = layout["position"], layout["normal"]
            start = (plane_position(viewport, x, y, point, normal) if axis == 3 else
                     axis_parameter(viewport, x, y, point, self.AXES[axis]))
            if start is None:
                return False
            self.drag = {"axis": axis, "point": point, "normal": normal,
                         "start": start, "viewport_name": viewport.name()}
            return True
        drag = self.drag
        # GeometryViewport has no value equality. Each HOM call can return a
        # fresh Python wrapper for the SAME viewport; compare its stable name.
        if viewport.name() != drag["viewport_name"]:
            self.drag = None
            self.hover_axis = None
            return False
        axis, point = drag["axis"], drag["point"]
        current = (plane_position(viewport, x, y, point, drag["normal"]) if axis == 3 else
                   axis_parameter(viewport, x, y, point, self.AXES[axis]))
        if current is not None:
            delta = (tuple(a - b for a, b in zip(current, drag["start"])) if axis == 3 else
                     tuple(a * (current - drag["start"]) for a in self.AXES[axis]))
            world_pos = hou.Vector3(tuple(p + d for p, d in zip(point, delta)))
            owner.preview_offset(world_pos * owner.placement["parent_world"].inverted())
        if reason == hou.uiEventReason.Changed:
            self.drag = None
        return True

    def onResume(self, kwargs):
        self.onEnter(kwargs)

    def onInterrupt(self, kwargs):
        self.drag = None
        self.hover_axis = None

    def onKeyEvent(self, kwargs):
        owner = self.owner()
        if owner is None:
            return False
        key = kwargs["ui_event"].device().keyString()
        if key in ("Enter", "Return", "\r", "Esc", "Escape"):
            accept = key not in ("Esc", "Escape")
            owner.finish_placement(accept, build=accept)
            return True
        return False

    def onExit(self, kwargs):
        self.drag = None
        self.hover_axis = None
        for drawable in self.drawables:
            drawable.show(False)
        try:
            self.viewer.clearPromptMessage()
        except (hou.ObjectWasDeleted, hou.NotAvailable):
            pass
        owner = self.owner()
        if owner is not None:
            owner.finish_placement(False, return_to_animate=False)


def register_placement_state():
    # A fresh name avoids old factories retained across shelf-script reloads.
    flag = "_animation_motion_trails_placement_registered_v5"
    if getattr(hou.session, flag, False):
        return
    template = hou.ViewerStateTemplate(PLACE_STATE, "Motion Trail Locator", hou.sopNodeTypeCategory())
    template.bindFactory(LocatorPlacementState)
    hou.ui.registerViewerState(template)
    setattr(hou.session, flag, True)


class MotionTrailsWindow(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Animation Motion Trails " + VERSION)
        self.setObjectName("Animation_Motion_Trails")
        self.setWindowFlag(QtCore.Qt.WindowContextHelpButtonHint, False)
        self.setAttribute(QtCore.Qt.WA_DeleteOnClose)
        self.resize(300, 240)
        self.setMinimumSize(240, 150)
        self.node = self.viewer = self.observer = None
        self.data = default_data()
        self.raw = None
        self.guides = {}
        self.cache = {}
        self.current_matrices = {}
        self.queue = deque()
        self.generation = 0
        self.dirty_at = time.monotonic()
        self.last_maintenance = 0.0
        self.last_frame = None
        self.last_signature = None
        self.sampling = self.closed = self.loading = False
        self.failed = False
        self.refresh_requested = False
        self.redraw_needed = False
        self.placement = None
        self.build_after_placement = False
        self.last_error = ""
        self.node_callbacks = []
        self._ui()
        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(35)
        self.timer.timeout.connect(self.tick)
        self.timer.start()
        hou.hipFile.addEventCallback(self.hip_event)
        self.use_active_scene()

    def _ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        self.list = QtWidgets.QTreeWidget()
        self.list.setColumnCount(1)
        self.list.setHeaderHidden(True)
        self.list.setRootIsDecorated(False)
        self.list.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.list.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.list.itemSelectionChanged.connect(self.selection_changed)
        layout.addWidget(self.list, 1)
        row = QtWidgets.QHBoxLayout()
        for label, callback in (("Locator", self.add_locator),
                                ("Build", self.build), ("Delete", self.delete_selected)):
            button = QtWidgets.QPushButton(label)
            button.setAutoDefault(False)
            button.clicked.connect(callback)
            row.addWidget(button)
        layout.addLayout(row)
        self.status = QtWidgets.QLabel()
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        self.status.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.status.customContextMenuRequested.connect(self.error_menu)
        layout.addWidget(self.status)
        self.load_ui()

    def message(self, message, error=False):
        self.status.setText(message)
        self.status.setStyleSheet("color: #f59c70" if error else "")
        self.status.setVisible(bool(message))
        if not error:
            self.status.setToolTip("")

    def report(self, error):
        frames = traceback.extract_tb(error.__traceback__)
        own_frames = [frame for frame in frames
                      if os.path.normcase(os.path.abspath(frame.filename)) ==
                      os.path.normcase(os.path.abspath(__file__))]
        origin = own_frames[-1] if own_frames else (frames[-1] if frames else None)
        location = "{}:{}".format(origin.name, origin.lineno) if origin else "unknown"
        self.last_error = "Animation Motion Trails {}\nSource: {}\nLocation: {}\n\n{}".format(
            VERSION, os.path.abspath(__file__), location,
            "".join(traceback.format_exception(type(error), error, error.__traceback__)))
        setattr(hou.session, "_animation_motion_trails_last_error", self.last_error)
        self.failed = True
        self.refresh_requested = False
        self.queue.clear()
        summary = str(error).splitlines()[0] if str(error) else type(error).__name__
        self.message("v{} {}: {}".format(VERSION, location, summary), True)
        self.status.setToolTip(self.last_error + "\nRight-click to copy error details.")

    def error_menu(self, point):
        if not self.last_error:
            return
        menu = QtWidgets.QMenu(self.status)
        action = menu.addAction("Copy Error Details")
        action.triggered.connect(lambda: QtWidgets.QApplication.clipboard().setText(self.last_error))
        if hasattr(menu, "exec"):
            menu.exec(self.status.mapToGlobal(point))
        else:
            menu.exec_(self.status.mapToGlobal(point))

    def current_item(self):
        row = self.list.currentItem()
        if row is None:
            return None
        uid = row.data(0, QtCore.Qt.UserRole)
        return next((i for i in self.data["trails"] if i["id"] == uid), None)

    def load_ui(self, selected=None):
        self.loading = True
        try:
            self.list.clear()
            for item in self.data["trails"]:
                row = QtWidgets.QTreeWidgetItem([item["name"]])
                row.setData(0, QtCore.Qt.UserRole, item["id"])
                row.setToolTip(0, item["parent"])
                self.list.addTopLevelItem(row)
                if item["id"] == selected:
                    self.list.setCurrentItem(row)
            if selected is None and self.list.topLevelItemCount():
                self.list.setCurrentItem(self.list.topLevelItem(self.list.topLevelItemCount() - 1))
        finally:
            self.loading = False
        self.selection_changed()

    def selection_changed(self):
        self.list.setEnabled(self.placement is None)
        self.update_visibility()

    def save(self, label):
        if self.node is None:
            raise ValueError("Enter the APEX Animate viewport state first.")
        raw = json.dumps(self.data, separators=(",", ":"), ensure_ascii=False)
        if raw != self.raw:
            with hou.undos.group(label):
                self.node.setUserData(DATA_KEY, raw)
            self.raw = raw

    def use_active_scene(self):
        if self.placement:
            self.finish_placement(False)
            return
        try:
            viewer, state = active_context()
            if state is None:
                self.message("Enter APEX Animate, select a control, then click Locator.")
                return
            data = parse_data(state.scene_node.userData(DATA_KEY))
            object_node(state.scene_node)
            self.disconnect(clear_guides=True)
            self.node, self.viewer = state.scene_node, viewer
            self.list.setToolTip(self.node.path())
            self.data, self.raw = data, self.node.userData(DATA_KEY)
            self.sync_settings()
            self.load_ui()
            self.rebuild_guides()
            self.connect(state)
            self.watch_nodes()
            self.invalidate(force=True)
            self.message("")
        except Exception as error:
            self.report(error)

    def watch_nodes(self):
        nodes = [self.node]
        parent = object_node(self.node)
        while isinstance(parent, hou.ObjNode):
            nodes.append(parent)
            parent = parent.parent()
        for node in nodes:
            node.addEventCallback((hou.nodeEventType.ParmTupleChanged, hou.nodeEventType.InputRewired), self.node_changed)
            self.node_callbacks.append(node)

    def node_changed(self, **kwargs):
        self.invalidate()

    def connect(self, state):
        self.detach_observer()
        observer = TrailObserver(self, state)
        collection = state.scene_callbacks.get("anim")
        if collection is None:
            raise ValueError("This state has no APEX animation evaluator collection.")
        collection[OBSERVER_KEY] = observer
        self.observer = observer
        self.current_matrices.clear()
        self.last_signature = None
        self.invalidate(force=True)
        self.observe(observer)
        self.capture_current(observer)

    def detach_observer(self):
        if self.observer is not None:
            state = self.observer.state
            if state is not None:
                collection = state.scene_callbacks.get("anim")
                if collection is not None and collection.get(OBSERVER_KEY) is self.observer:
                    collection.pop(OBSERVER_KEY)
            self.observer.active = False
            self.observer = None

    def disconnect(self, clear_guides=False):
        self.detach_observer()
        for node in self.node_callbacks:
            try:
                node.removeEventCallback((hou.nodeEventType.ParmTupleChanged, hou.nodeEventType.InputRewired), self.node_changed)
            except hou.Error:
                pass
        self.node_callbacks.clear()
        self.queue.clear()
        self.cache.clear()
        self.current_matrices.clear()
        for guide in self.guides.values():
            guide.show(False)
            if clear_guides:
                guide.dispose()
        if clear_guides:
            self.guides.clear()

    def rebuild_guides(self):
        for guide in self.guides.values():
            guide.dispose()
        self.guides = {item["id"]: TrailGuide(self.viewer, item) for item in self.data["trails"]}

    def require_context(self, follow_active=False):
        if follow_active or self.node is None:
            viewer, state = active_context()
            if state is None:
                raise ValueError("Enter APEX Animate and select a control first.")
            if self.node != state.scene_node:
                self.use_active_scene()
        if self.node is None:
            raise ValueError("Enter APEX Animate and select a control first.")
        viewer, state = active_context(self.node)
        if state is None:
            raise ValueError("Return to this node's APEX Animate viewport state first.")
        if viewer != self.viewer or self.observer is None or self.observer.state is not state:
            self.viewer = viewer
            self.rebuild_guides()
            self.connect(state)
        return state

    def add_locator(self):
        if self.placement:
            self.message("Drag axes or yellow center, then Build. Esc cancels placement.")
            return
        try:
            state = self.require_context(follow_active=True)
            path = selected_path(self.viewer)
            if not isinstance(state.scene.control_manager.controls.get(path, None), TransformControl):
                raise ValueError("Select a transform control, not an abstract slider or animation channel.")
            # Validate before saving a locator, so an unreadable control does
            # not leave a new definition in the HIP on each failed click.
            probe = ControlManager()
            probe.addControl(state.scene, path)
            probe.update(state.scene)
            read_control_matrix(probe, path)
            ref = path_refs(self.viewer, [path])[0]
            base_name = path.rsplit("/", 1)[-1] + "_trail"
            used = {item["name"] for item in self.data["trails"]}
            name, suffix = base_name, 2
            while name in used:
                name = base_name + "_" + str(suffix)
                suffix += 1
            item = {"id": uuid.uuid4().hex, "name": name,
                    "parent": ref, "offset": [0.0, 0.0, 0.0], "built": False,
                    "visible": True, "color": list(COLORS[len(self.data["trails"]) % len(COLORS)])}
            self.data["trails"].append(item)
            self.save("Add Motion Trail Locator")
            self.rebuild_guides()
            self.connect(state)
            self.load_ui(item["id"])
            self.place_locator()
        except Exception as error:
            self.loading = False
            self.report(error)

    def delete_ids(self, ids):
        if not ids:
            return
        if self.placement:
            self.finish_placement(False)
        try:
            self.data["trails"] = [i for i in self.data["trails"] if i["id"] not in ids]
            self.save("Delete Motion Trails")
            for uid in ids:
                guide = self.guides.pop(uid, None)
                if guide:
                    guide.dispose()
            self.load_ui()
            if self.observer is not None and self.observer.state is not None:
                self.connect(self.observer.state)
            if not self.data["trails"]:
                self.detach_observer()
                self.cache.clear()
                self.queue.clear()
            self.message("")
            self.redraw_view()
        except Exception as error:
            self.report(error)

    def delete_selected(self):
        self.delete_ids({row.data(0, QtCore.Qt.UserRole) for row in self.list.selectedItems()})

    def delete_all(self):
        self.delete_ids({i["id"] for i in self.data["trails"]})

    def place_locator(self):
        try:
            self.require_context()
            item = self.current_item()
            if item is None:
                raise ValueError("Select a locator in the list first.")
            self.capture_current(self.observer)
            parent_world = self.current_matrices.get(item["parent"])
            if parent_world is None:
                raise ValueError("The parent control could not be found.")
            obj_world = object_node(self.node).worldTransform()
            self.placement = {"item": item, "original": list(item["offset"]),
                              "parent_world": parent_world,
                              "parent_sop": parent_world * obj_world.inverted()}
            register_placement_state()
            self.selection_changed()
            self.viewer.setCurrentState(PLACE_STATE)
            self.message("Move the locator, then click Build. Esc cancels placement.")
        except Exception as error:
            if self.placement:
                self.finish_placement(False, return_to_animate=False)
            self.report(error)

    def preview_offset(self, offset):
        if self.placement is None:
            return
        self.placement["item"]["offset"] = list(offset)
        item = self.placement["item"]
        self.guides[item["id"]].current(self.placement["parent_world"], self.data["locator_size"])
        self.redraw_needed = True
        self.update_visibility()
        self.redraw_view()

    def finish_placement(self, accept, return_to_animate=True, build=False):
        if self.placement is None:
            return
        placement, self.placement = self.placement, None
        self.build_after_placement = bool(accept and build)
        try:
            if accept:
                self.save("Place Motion Trail Locator")
            else:
                placement["item"]["offset"] = placement["original"]
            self.redraw_needed = True
            self.selection_changed()
            if return_to_animate:
                # State changes must happen outside the current handle/key event.
                hou.ui.postEventCallback(self.return_to_animate)
        except Exception as error:
            self.report(error)

    def return_to_animate(self):
        if self.closed or self.node is None:
            return
        try:
            self.node.setCurrent(True, clear_all_selected=True)
            self.viewer.enterCurrentNodeState()
            build, self.build_after_placement = self.build_after_placement, False
            if build:
                self.build()
            else:
                self.message("")
        except hou.Error as error:
            self.report(error)

    def sync_settings(self):
        """One automatic configuration, also migrating the old adjustable UI."""
        start, end = (int(value) for value in hou.playbar.playbackRange())
        if end < start:
            raise ValueError("The playback range is invalid.")
        parents = max(1, len({item["parent"] for item in self.data["trails"]}))
        # Preserve every frame for ordinary shots; bound exceptionally long ones.
        budget = max(2, MAX_SAMPLES // parents)
        step = max(1, math.ceil((end - start) / (budget - 1)))
        previous = (self.data["start"], self.data["end"], self.data["step"])
        self.data.update(start=start, end=end, step=step, auto=True,
                         markers=True, locator_size=0.05)
        for item in self.data["trails"]:
            item["visible"] = True
        return previous != (start, end, step)

    def build(self):
        if self.placement:
            self.finish_placement(True, build=True)
            return
        try:
            self.require_context()
            if not self.data["trails"]:
                self.message("Select a control, then click Locator first.")
                return
            self.sync_settings()
            selected = {row.data(0, QtCore.Qt.UserRole) for row in self.list.selectedItems()}
            for item in self.data["trails"]:
                if item["id"] in selected or not selected:
                    item["built"] = True
            self.save("Build Motion Trails")
            self.invalidate(force=True)
            self.message("Building...")
        except Exception as error:
            self.report(error)

    def invalidate(self, force=False):
        self.generation += 1
        self.cache.clear()
        self.queue.clear()
        self.dirty_at = time.monotonic()
        self.failed = False
        if force or self.data["auto"]:
            self.refresh_requested = True
        elif any(item["built"] for item in self.data["trails"]):
            self.message("Animation changed. Click Build to refresh.")

    def observe(self, observer):
        try:
            if observer.updateChannelDataIds():
                self.invalidate()
        except Exception as error:
            self.failed = True
            self.report(error)

    def capture_current(self, observer):
        if observer.state is None or not observer.paths or self.placement:
            return
        try:
            observer.controls.update(observer.scene)
            obj_world = object_node(self.node).worldTransform()
            matrices = {ref: read_control_matrix(observer.controls, path) * obj_world
                        for ref, path in observer.paths.items()}
            signature = tuple((ref, matrix.asTuple()) for ref, matrix in sorted(matrices.items()))
            frame = hou.frame()
            if self.last_frame == frame and self.last_signature is not None and signature != self.last_signature:
                self.invalidate()
            self.last_frame, self.last_signature = frame, signature
            self.current_matrices = matrices
            world_to_model = obj_world.inverted() * self.viewer.curViewport().modelToGeometryTransform().inverted()
            changed = False
            for guide in self.guides.values():
                guide.transform(world_to_model)
                matrix = matrices.get(guide.item["parent"])
                if matrix is not None:
                    changed = guide.current(matrix, self.data["locator_size"]) or changed
            self.update_visibility()
            if changed:
                self.redraw_view()
        except Exception as error:
            self.failed = True
            self.report(error)

    def update_visibility(self):
        visible = bool(self.placement or (self.observer is not None and self.observer.state is not None))
        item = self.current_item()
        selected = item["id"] if item else None
        for uid, guide in self.guides.items():
            parent_found = self.placement or (self.observer and guide.item["parent"] in self.observer.paths)
            guide.show(bool(visible and parent_found), uid == selected)

    def redraw_view(self):
        if self.viewer:
            try:
                self.viewer.curViewport().draw()
            except hou.Error:
                pass

    def requested(self):
        if not self.observer:
            return set()
        return {i["parent"] for i in self.data["trails"]
                if i["built"] and i["visible"] and i["parent"] in self.observer.paths}

    def publish_curves(self):
        frames = sample_frames(self.data["start"], self.data["end"], self.data["step"])
        for guide in self.guides.values():
            if not guide.item["built"]:
                continue
            matrices = self.cache.get(guide.item["parent"], {})
            if all(frame in matrices for frame in frames):
                guide.curve(frames, matrices, self.data["markers"], self.data["locator_size"])
        self.redraw_needed = False
        self.update_visibility()
        self.redraw_view()

    def sample_batch(self):
        observer = self.observer
        state, scene = observer.state, observer.scene
        obj = object_node(self.node)
        refs = self.requested()
        generation = self.generation
        # Preserve current unkeyed/pending parameter values as well as time.
        saved = {}
        for rig_path in observer.anim_tracker.rig_trackers:
            path = rig_path + "/graph_parms"
            parms = scene.getData(path)
            if parms is not None:
                saved[path] = parms.freeze()
        self.sampling = True
        started = time.monotonic()
        try:
            for _ in range(BATCH_FRAMES):
                if not self.queue or generation != self.generation:
                    break
                frame = self.queue.popleft()
                scene.updateEvaluationParms(frame)
                observer.controls.update(scene)
                obj_world = obj.worldTransformAtTime(hou.frameToTime(frame))
                for ref in refs:
                    matrix = read_control_matrix(observer.controls, observer.paths[ref])
                    self.cache.setdefault(ref, {})[frame] = matrix * obj_world
                if time.monotonic() - started >= BATCH_SECONDS:
                    break
        finally:
            try:
                scene.updateEvaluationParms(hou.frame())
                for path, parms in saved.items():
                    scene.getData(path).update(parms)
                # Only refresh control outputs, never invoke the mesh drawables.
                observer.controls.update(scene)
                state.control_manager.update(scene)
            finally:
                self.sampling = False
        if not self.queue:
            self.refresh_requested = False
            self.publish_curves()
            missing = len(observer.missing)
            self.message("Missing parent control(s): " + "; ".join(observer.missing[:3]) if missing else "",
                         error=bool(missing))
        else:
            self.message("Building... {}".format(len(self.queue)))

    def maintenance(self):
        if self.node is None or self.placement:
            return
        raw = self.node.userData(DATA_KEY)
        if raw != self.raw:
            # Undo/redo and externally edited metadata share the same restore path.
            data = parse_data(raw)
            selected = self.current_item()
            self.data, self.raw = data, raw
            self.sync_settings()
            self.load_ui(selected["id"] if selected else None)
            self.rebuild_guides()
            self.detach_observer()
            self.invalidate(force=True)
        if self.sync_settings():
            self.invalidate(force=True)
        viewer, state = active_context(self.node)
        if state is None:
            if self.observer:
                self.detach_observer()
                self.update_visibility()
            return
        collection = state.scene_callbacks.get("anim")
        if (self.observer is None or self.observer.state is not state or self.observer.scene is not state.scene
                or viewer != self.viewer
                or collection is None or collection.get(OBSERVER_KEY) is not self.observer):
            if not self.data["trails"]:
                return
            if viewer != self.viewer:
                self.viewer = viewer
                self.rebuild_guides()
            self.connect(state)
        self.observe(self.observer)
        if not self.failed:
            self.capture_current(self.observer)

    def tick(self):
        if self.closed or self.sampling:
            return
        try:
            if hou.hipFile.isLoadingHipFile() or hou.hipFile.isShuttingDown():
                return
            now = time.monotonic()
            if now - self.last_maintenance >= 0.18:
                self.last_maintenance = now
                self.maintenance()
            if self.redraw_needed:
                self.publish_curves()
            observer = self.observer
            if observer and observer.state is not None and not self.placement and not self.failed:
                if not getattr(observer.state, "_running_scene_callbacks", False):
                    self.capture_current(observer)
            if not observer or self.failed or self.placement or not self.refresh_requested:
                return
            state = observer.state
            # A passive APEX state still owns a valid scene. Do not steal its
            # channel list, and do not require viewport focus to refresh trails.
            if (state is None or getattr(state, "_running_scene_callbacks", False)
                    or getattr(state, "is_dragging_control", False)
                    or getattr(state, "is_playing_back", False)
                    or getattr(state, "is_scrubbing", False)
                    or QtWidgets.QApplication.mouseButtons() != QtCore.Qt.NoButton
                    or now - self.dirty_at < SETTLE_SECONDS):
                return
            refs = self.requested()
            if not refs:
                self.refresh_requested = False
                if observer.missing:
                    self.message("Parent control(s) missing: " + "; ".join(observer.missing[:3]), True)
                return
            if not self.queue:
                frames = sample_frames(self.data["start"], self.data["end"], self.data["step"])
                if len(frames) * len(refs) > MAX_SAMPLES:
                    raise ValueError("Too many locators for the current playback range.")
                pending = [f for f in frames if any(f not in self.cache.get(ref, {}) for ref in refs)]
                self.queue = sample_order(pending, hou.frame())
                if not self.queue:
                    self.refresh_requested = False
                    self.publish_curves()
                    return
            self.sample_batch()
        except hou.ObjectWasDeleted:
            self.disconnect(clear_guides=True)
            self.node = None
            self.message("Source node deleted. Enter APEX Animate and click Locator.")
        except Exception as error:
            self.failed = True
            self.queue.clear()
            self.refresh_requested = False
            self.report(error)

    def hip_event(self, event_type):
        if event_type in (hou.hipFileEventType.BeforeClear, hou.hipFileEventType.BeforeLoad):
            if self.placement:
                self.finish_placement(False, return_to_animate=False)
            self.disconnect(clear_guides=True)
            self.node = None
            self.data, self.raw = default_data(), None
            self.load_ui()
            self.message("")

    def closeEvent(self, event):
        if self.closed:
            super().closeEvent(event)
            return
        if self.placement:
            self.finish_placement(False)
            # The deferred callback would intentionally ignore a closed window.
            self.return_to_animate()
        self.closed = True
        self.timer.stop()
        self.disconnect(clear_guides=True)
        hou.hipFile.removeEventCallback(self.hip_event)
        if getattr(hou.session, WINDOW_KEY, None) is self:
            setattr(hou.session, WINDOW_KEY, None)
        super().closeEvent(event)


def show():
    previous = getattr(hou.session, WINDOW_KEY, None)
    if previous is not None:
        previous.close()
    if not hou.isUIAvailable():
        raise RuntimeError("Animation Motion Trails requires the Houdini UI.")
    window = MotionTrailsWindow(hou.qt.mainWindow())
    setattr(hou.session, WINDOW_KEY, window)
    window.show()
    return window


if __name__ == "__main__":
    show()
