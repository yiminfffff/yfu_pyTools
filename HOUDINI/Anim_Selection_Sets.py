import json
import uuid

import hou

try:
    from PySide6 import QtCore, QtWidgets
except ImportError:
    from PySide2 import QtCore, QtWidgets


DATA_KEY = "animation_selection_sets_v1"
WINDOW_KEY = "_animation_selection_sets_window"
VERSION = "1.0.2"


def read_sets():
    """Read without silently discarding damaged or newer-format data."""
    raw = hou.node("/obj").userData(DATA_KEY)
    if not raw:
        return []
    try:
        payload = json.loads(raw)
        if payload["version"] != 1 or not isinstance(payload["sets"], list):
            raise ValueError("Unsupported selection set format.")
        seen = set()
        for group in payload["sets"]:
            if (not isinstance(group, dict)
                    or not isinstance(group.get("id"), str)
                    or not isinstance(group.get("name"), str)
                    or not group["name"].strip()
                    or group["id"] in seen
                    or not isinstance(group.get("members"), list)):
                raise ValueError("Invalid selection set.")
            seen.add(group["id"])
            for member in group["members"]:
                if (not isinstance(member, dict)
                        or member.get("kind") not in ("node", "apex")
                        or not isinstance(member.get("path"), str)
                        or not member["path"]
                        or (member["kind"] == "apex" and
                            not isinstance(member.get("node"), str))):
                    raise ValueError("Invalid controller reference.")
        return payload["sets"]
    except (ValueError, KeyError, TypeError) as error:
        raise ValueError("Cannot read saved selection sets. Existing data has been preserved.\n" + str(error))


def write_sets(groups):
    hou.node("/obj").setUserData(
        DATA_KEY, json.dumps({"version": 1, "sets": groups}, ensure_ascii=False))


def member_key(member):
    return member["kind"], member.get("node", ""), member["path"]


def unique_members(members):
    result, seen = [], set()
    for member in members:
        key = member_key(member)
        if key not in seen:
            result.append(member)
            seen.add(key)
    return result


def scene_viewers():
    if not hou.isUIAvailable():
        return []
    # hou.Desktop has no sceneViewers() in supported Houdini builds.
    # hou.ui also includes floating viewers not attached to the desktop.
    viewers = [pane for pane in hou.ui.paneTabs()
               if pane.type() == hou.paneTabType.SceneViewer]
    return sorted(viewers, key=lambda v: not v.isCurrentTab())


def is_apex_viewer(viewer):
    from apex.ui import statecommandutils as scu
    return scu.inAnimateState(viewer)


def command(viewer, name, **kwargs):
    """Do not swallow state-command failures and report a false success."""
    args = dict(kwargs)
    viewer.runStateCommand(name, args)
    return args.get("ret")


def apex_node(viewer):
    node = command(viewer, "getSceneNode")
    if node is None:
        raise ValueError("The APEX Animate state has no scene node.")
    return node


def apex_paths(viewer, selected=True):
    from apex.ui.selectionmanager.utils import flattenControlTree
    tree = command(viewer, "retrieveSelectedControls" if selected else "retrieveAllControls")
    if tree is None:
        raise ValueError("This Houdini version did not return APEX controller data.")
    paths = flattenControlTree(tree, "/", prepend=True)
    # Houdini 22 uses persistent refs for controls from temporary tool rigs.
    refs = command(viewer, "buildPathRefs", paths=paths)
    return list(refs) if refs is not None else paths


def capture_selection():
    """APEX selection takes precedence over the selected Scene Animate SOP."""
    for viewer in scene_viewers():
        if is_apex_viewer(viewer):
            node_path = apex_node(viewer).path()
            return unique_members([
                {"kind": "apex", "node": node_path, "path": path}
                for path in apex_paths(viewer)])
    return [{"kind": "node", "path": node.path()} for node in hou.selectedNodes()]


def select_members(members):
    """Resolve first so missing scenes do not wipe the user's selection."""
    nodes, contexts, missing = [], {}, []
    for member in members:
        if member["kind"] == "node":
            node = hou.node(member["path"])
            if node is None:
                missing.append(member["path"])
            else:
                nodes.append(node)
        else:
            contexts.setdefault(member["node"], []).append(member["path"])

    plans = []
    viewers = scene_viewers()
    for node_path, paths in contexts.items():
        node = hou.node(node_path)
        if node is None:
            missing.extend(node_path + " : " + path for path in paths)
            continue
        viewer = next((v for v in viewers
                       if is_apex_viewer(v) and apex_node(v) == node), None)
        if viewer is None:
            # Entering the animation state explicitly also works after reopening
            # a HIP, when its viewport may have returned to the select tool.
            if len(contexts) > 1:
                raise ValueError("Open an APEX Animate viewer for each scene in this set.")
            if not viewers:
                raise ValueError("Open a Scene View to select APEX controllers.")
            viewer = viewers[0]
            node.setCurrent(True, clear_all_selected=True)
            viewer.enterCurrentNodeState()
            if not is_apex_viewer(viewer) or apex_node(viewer) != node:
                raise ValueError("Enter the APEX Animate state on:\n" + node_path)
        available = set(apex_paths(viewer, selected=False))
        valid = [path for path in paths if path in available]
        missing.extend(node_path + " : " + path for path in paths if path not in available)
        if valid:
            plans.append((viewer, valid))

    if not nodes and not plans:
        if not members:
            return 0, []
        raise ValueError("No saved controllers could be found.\n" + "\n".join(missing[:6]))
    if nodes:
        hou.clearAllSelected()
        for node in nodes:
            node.setSelected(True)
        nodes[-1].setCurrent(True)
    for viewer, paths in plans:
        command(viewer, "selectControls", controls=paths,
                controls_in_tree_format=False, pick_modifier=hou.pickModifier.Replace,
                run_control_callbacks=False)
    return len(nodes) + sum(len(paths) for _, paths in plans), missing


def mouse_position(event):
    """Use Qt 6 coordinates, falling back only for Qt 5 events."""
    position = getattr(event, "position", None)
    return position().toPoint() if position is not None else event.pos()


class SetList(QtWidgets.QTreeWidget):
    choose = QtCore.Signal(object)

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.RightButton:
            # Right-click must preserve the VIEWPORT selection that Add/Remove
            # will capture. Only highlight the row in this list.
            item = self.itemAt(mouse_position(event))
            if item:
                self.setCurrentItem(item)
            event.accept()
            return
        super().mousePressEvent(event)
        if event.button() == QtCore.Qt.LeftButton:
            item = self.itemAt(mouse_position(event))
            if item:
                self.choose.emit(item)

    def mouseDoubleClickEvent(self, event):
        pos = mouse_position(event)
        item = self.itemAt(pos)
        if event.button() == QtCore.Qt.LeftButton and item and self.columnAt(pos.x()) == 0:
            self.editItem(item, 0)
        event.accept()

    def keyPressEvent(self, event):
        item = self.currentItem()
        if item and event.key() == QtCore.Qt.Key_F2:
            self.editItem(item, 0)
        elif item and event.key() in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter):
            self.choose.emit(item)
        else:
            super().keyPressEvent(event)


class SelectionSetsWindow(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Animation_Selection_Sets")
        self.setWindowTitle("Animation Selection Sets")
        self.setWindowFlag(QtCore.Qt.WindowContextHelpButtonHint, False)
        self.setAttribute(QtCore.Qt.WA_DeleteOnClose)
        self.resize(370, 450)
        self.setMinimumSize(290, 270)
        self._closed = False
        self._groups = []
        self._loaded = False
        self._raw = None

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        header = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel("SELECTION SETS")
        title.setStyleSheet("font-weight: bold;")
        header.addWidget(title)
        header.addStretch()
        self.add_button = QtWidgets.QPushButton("+")
        self.add_button.setFixedSize(32, 28)
        self.add_button.setToolTip("Create a set from the currently selected controllers")
        self.add_button.clicked.connect(self.create_set)
        header.addWidget(self.add_button)
        layout.addLayout(header)

        self.entries = SetList()
        self.entries.setHeaderLabels(["Name", "Controls"])
        self.entries.setRootIsDecorated(False)
        self.entries.setUniformRowHeights(True)
        self.entries.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.entries.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.entries.header().setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        self.entries.header().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeToContents)
        self.entries.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.entries.customContextMenuRequested.connect(self.context_menu)
        self.entries.choose.connect(self.choose_set)
        self.entries.itemChanged.connect(self.rename_set)
        layout.addWidget(self.entries)

        self.status = QtWidgets.QLabel()
        self.status.setWordWrap(True)
        self.status.hide()
        layout.addWidget(self.status)

        self._hip_callback = self._on_hip_event
        hou.hipFile.addEventCallback(self._hip_callback)
        # Also observe undo or edits made by a second instance of the tool.
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(650)
        self._timer.timeout.connect(self._poll)
        self._timer.start()
        self.reload_sets()

    def message(self, text, error=False):
        self.status.setText(text if error else "")
        self.status.setStyleSheet("color: #eeac75;" if error else "")
        self.status.setVisible(bool(error and text))

    def reload_sets(self):
        was_loaded = self._loaded
        selected = self.entries.currentItem()
        selected_id = selected.data(0, QtCore.Qt.UserRole) if selected else None
        self._raw = hou.node("/obj").userData(DATA_KEY)
        try:
            groups = read_sets()
        except Exception as error:
            self._loaded = False
            self.add_button.setEnabled(False)
            self.entries.setEnabled(False)
            self.message(str(error), True)
            return False
        self._groups = groups
        self._loaded = True
        self.add_button.setEnabled(True)
        self.entries.setEnabled(True)
        blocker = QtCore.QSignalBlocker(self.entries)
        self.entries.clear()
        for group in groups:
            item = QtWidgets.QTreeWidgetItem([group["name"], str(len(group["members"]))])
            item.setData(0, QtCore.Qt.UserRole, group["id"])
            item.setFlags(item.flags() | QtCore.Qt.ItemIsEditable)
            paths = [m.get("node", "") + (" : " if m["kind"] == "apex" else "") + m["path"]
                     for m in group["members"]]
            item.setToolTip(0, "\n".join(paths) or "Empty set")
            self.entries.addTopLevelItem(item)
            if group["id"] == selected_id:
                self.entries.setCurrentItem(item)
        del blocker
        if not was_loaded:
            self.message("Select controllers, then click + to create a set.")
        return True

    def _poll(self):
        if self._closed or hou.hipFile.isLoadingHipFile():
            return
        root = hou.node("/obj")
        if root and root.userData(DATA_KEY) != self._raw:
            self.reload_sets()

    def _on_hip_event(self, event_type):
        if event_type in (hou.hipFileEventType.AfterLoad,
                          hou.hipFileEventType.AfterClear,
                          hou.hipFileEventType.AfterMerge):
            # Queue UI work until Houdini finishes replacing the scene.
            QtCore.QTimer.singleShot(0, self._scene_changed)

    def _scene_changed(self):
        if not self._closed:
            self.reload_sets()
            if self._loaded:
                self.message("Loaded selection sets from the current scene.")

    def _group(self, item):
        if item is None:
            raise ValueError("Choose a selection set first.")
        group_id = item.data(0, QtCore.Qt.UserRole)
        # Always mutate the latest scene data; never write stale window data
        # into a newly loaded HIP or overwrite an external edit.
        for group in read_sets():
            if group["id"] == group_id:
                return group
        raise ValueError("This set is no longer in the current scene. Refresh the list.")

    def _save_group(self, group, delete=False):
        groups = read_sets()
        index = next((i for i, entry in enumerate(groups) if entry["id"] == group["id"]), None)
        if delete:
            if index is not None:
                groups.pop(index)
        elif index is None:
            groups.append(group)
        else:
            groups[index] = group
        write_sets(groups)
        self.reload_sets()
        for i in range(self.entries.topLevelItemCount()):
            item = self.entries.topLevelItem(i)
            if item.data(0, QtCore.Qt.UserRole) == group["id"]:
                self.entries.setCurrentItem(item)
                break

    def create_set(self):
        try:
            # Capture before showing an editor or touching the list selection.
            members = capture_selection()
            if not members:
                raise ValueError("Select one or more controllers first. For APEX, enter Animate mode.")
            existing = {g["name"].casefold() for g in read_sets()}
            number = 1
            while ("Set %d" % number).casefold() in existing:
                number += 1
            group = {"id": uuid.uuid4().hex, "name": "Set %d" % number, "members": members}
            self._save_group(group)
            self.message("Created %s with %d controls. Double-click to rename." % (group["name"], len(members)))
        except Exception as error:
            self.message(str(error), True)

    def choose_set(self, item):
        try:
            group = self._group(item)
            count, missing = select_members(group["members"])
            if not group["members"]:
                self.message("%s is empty. Right-click to add selected controls." % group["name"])
            else:
                text = "Selected %d controls from %s." % (count, group["name"])
                if missing:
                    text += " %d missing controls were skipped." % len(missing)
                self.message(text, bool(missing))
        except Exception as error:
            self.message(str(error), True)

    def rename_set(self, item, column):
        if column != 0:
            return
        try:
            group = self._group(item)
            name = item.text(0).strip()
            if not name:
                raise ValueError("The set name cannot be empty.")
            if any(g["id"] != group["id"] and g["name"].casefold() == name.casefold() for g in read_sets()):
                raise ValueError("A set with that name already exists.")
            group["name"] = name
            self._save_group(group)
            self.message("Renamed to %s." % name)
        except Exception as error:
            self.reload_sets()
            self.message(str(error), True)

    def update_members(self, item, remove=False):
        try:
            selected = capture_selection()
            if not selected:
                raise ValueError("Select the controllers to %s first." % ("remove" if remove else "add"))
            group = self._group(item)
            before = len(group["members"])
            if remove:
                keys = {member_key(m) for m in selected}
                group["members"] = [m for m in group["members"] if member_key(m) not in keys]
            else:
                group["members"] = unique_members(group["members"] + selected)
            changed = abs(len(group["members"]) - before)
            self._save_group(group)
            self.message(("Removed" if remove else "Added") + " %d controls. %d in set." % (changed, len(group["members"])))
        except Exception as error:
            self.message(str(error), True)

    def delete_set(self, item):
        try:
            group = self._group(item)
            self._save_group(group, delete=True)
            self.message("Deleted %s." % group["name"])
        except Exception as error:
            self.message(str(error), True)

    def context_menu(self, pos):
        item = self.entries.itemAt(pos)
        menu = QtWidgets.QMenu(self)
        if item:
            self.entries.setCurrentItem(item)
            add_action = menu.addAction("Add Selected Controls")
            remove_action = menu.addAction("Remove Selected Controls")
            menu.addSeparator()
            rename_action = menu.addAction("Rename")
            delete_action = menu.addAction("Delete Set")
            add_action.triggered.connect(lambda: self.update_members(item))
            remove_action.triggered.connect(lambda: self.update_members(item, remove=True))
            rename_action.triggered.connect(lambda: self.entries.editItem(item, 0))
            delete_action.triggered.connect(lambda: self.delete_set(item))
        else:
            menu.addAction("Create Set from Selection", self.create_set)
        menu.addSeparator()
        menu.addAction("Refresh", self.reload_sets)
        execute = getattr(menu, "exec", None) or menu.exec_
        execute(self.entries.viewport().mapToGlobal(pos))

    def closeEvent(self, event):
        self._closed = True
        self._timer.stop()
        try:
            hou.hipFile.removeEventCallback(self._hip_callback)
        except hou.Error:
            pass
        if getattr(hou.session, WINDOW_KEY, None) is self:
            setattr(hou.session, WINDOW_KEY, None)
        super().closeEvent(event)


def show():
    """A session-owned reference keeps the shelf-created window alive."""
    old = getattr(hou.session, WINDOW_KEY, None)
    if old is not None:
        try:
            old.close()
        except RuntimeError:
            pass
    # Loading a new HIP can replace hou.session while this Qt window survives.
    # Close any surviving instance as well, so rerunning never duplicates it.
    for widget in QtWidgets.QApplication.topLevelWidgets():
        if widget is not old and widget.objectName() == "Animation_Selection_Sets":
            widget.close()
    window = SelectionSetsWindow(hou.qt.mainWindow())
    setattr(hou.session, WINDOW_KEY, window)
    window.show()
    window.raise_()
    window.activateWindow()
    return window


if __name__ == "__main__":
    show()
