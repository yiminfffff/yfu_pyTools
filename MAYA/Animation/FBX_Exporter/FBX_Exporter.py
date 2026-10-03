# -*- coding: utf-8 -*-
"""Standalone batch skeleton-animation FBX exporter for Maya.

Run this file with Maya's mayapy.exe. The UI opens Maya scenes in standalone
mode, bakes each selected joint hierarchy to temporary joints, and exports FBX
without displaying or modifying the source scenes.
"""

from __future__ import print_function

import json
import os
import sys
import traceback
import uuid

from PySide6 import QtCore, QtWidgets


APP_TITLE = "FBX_Exporter"
PROJECT_CONFIG_NAME = ".fbx_exporter.json"
SESSION_CONFIG_NAME = "FBX_Exporter.user.json"
SCENE_EXTENSIONS = (".ma", ".mb")


def _script_directory():
    """Return the absolute directory that contains this script."""
    return os.path.dirname(os.path.abspath(__file__))


def _normalized_path(path):
    """Return an absolute normalized path without surrounding whitespace."""
    return os.path.normpath(os.path.abspath(os.path.expanduser(path.strip())))


def _read_json(path, default=None):
    """Read a JSON dictionary, returning a default value if it is unavailable."""
    if not path or not os.path.isfile(path):
        return {} if default is None else default
    try:
        with open(path, "r", encoding="utf-8") as stream:
            value = json.load(stream)
        return value if isinstance(value, dict) else ({} if default is None else default)
    except (OSError, ValueError, TypeError):
        return {} if default is None else default


def _write_json(path, value):
    """Write JSON atomically so interrupted saves do not corrupt settings."""
    directory = os.path.dirname(path)
    if directory and not os.path.isdir(directory):
        os.makedirs(directory)
    temporary_path = path + ".tmp." + uuid.uuid4().hex
    try:
        with open(temporary_path, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True)
        os.replace(temporary_path, path)
    finally:
        if os.path.exists(temporary_path):
            os.remove(temporary_path)


def _collect_scenes(paths, recursive=True):
    """Expand files and directories into a sorted list of Maya scene paths."""
    scenes = []
    seen = set()
    for raw_path in paths:
        if not raw_path:
            continue
        path = _normalized_path(raw_path)
        candidates = []
        if os.path.isfile(path) and os.path.splitext(path)[1].lower() in SCENE_EXTENSIONS:
            candidates = [path]
        elif os.path.isdir(path):
            if recursive:
                for directory, _, names in os.walk(path):
                    for name in names:
                        if os.path.splitext(name)[1].lower() in SCENE_EXTENSIONS:
                            candidates.append(os.path.join(directory, name))
            else:
                candidates = [
                    os.path.join(path, name)
                    for name in os.listdir(path)
                    if os.path.splitext(name)[1].lower() in SCENE_EXTENSIONS
                ]
        for candidate in candidates:
            key = os.path.normcase(os.path.normpath(candidate))
            if key not in seen:
                seen.add(key)
                scenes.append(os.path.normpath(candidate))
    return sorted(scenes, key=lambda value: value.lower())


def _initialize_maya():
    """Initialize Maya standalone and return maya.cmds and maya.mel."""
    import maya.standalone

    try:
        maya.standalone.initialize(name="python")
    except RuntimeError as exc:
        if "initialized" not in str(exc).lower():
            raise

    import maya.cmds as cmds
    import maya.mel as mel

    return cmds, mel


def _hide_console_window():
    """Hide only the Windows console without hiding later Qt windows."""
    if os.name != "nt" or os.environ.get("FBX_EXPORTER_SHOW_CONSOLE") == "1":
        return
    try:
        import ctypes

        console_window = ctypes.windll.kernel32.GetConsoleWindow()
        if console_window:
            ctypes.windll.user32.ShowWindow(console_window, 0)
    except (AttributeError, OSError):
        pass


def _find_root_joint(cmds, stored_name):
    """Resolve one root joint by exact name or namespace-independent short name."""
    requested = stored_name.strip()
    if not requested:
        raise RuntimeError("No root joint name has been saved for this project.")

    exact_matches = cmds.ls(requested, long=True, type="joint") or []
    if len(exact_matches) == 1:
        return exact_matches[0]
    if len(exact_matches) > 1:
        raise RuntimeError("Multiple root joints use the same name: " + requested)

    clean_requested = requested.rsplit("|", 1)[-1].rsplit(":", 1)[-1]
    suffix_matches = []
    for joint in cmds.ls(type="joint", long=True) or []:
        clean_name = joint.rsplit("|", 1)[-1].rsplit(":", 1)[-1]
        if clean_name == clean_requested:
            suffix_matches.append(joint)
    if len(suffix_matches) == 1:
        return suffix_matches[0]
    if not suffix_matches:
        raise RuntimeError("Root joint not found: " + requested)
    raise RuntimeError(
        "Multiple root joints match {} after removing namespaces:\n{}".format(
            clean_requested, "\n".join(suffix_matches)
        )
    )


def _joint_hierarchy(cmds, root):
    """Return a parent-first joint hierarchy and namespace-free bone names."""
    joints = [root] + sorted(
        cmds.listRelatives(
            root, allDescendents=True, type="joint", fullPath=True
        ) or [],
        key=lambda value: value.count("|"),
    )
    names = [joint.rsplit("|", 1)[-1].rsplit(":", 1)[-1] for joint in joints]
    if len(names) != len(set(names)):
        raise RuntimeError("The skeleton contains duplicate bone names after removing namespaces.")
    return joints, names


def _set_fbx_option(mel, saved_settings, command, value):
    """Save one FBX option before setting its temporary export value."""
    saved_settings[command] = mel.eval(command + " -q;")
    mel.eval("{} -v {};".format(command, value))


def _restore_fbx_options(cmds, mel, saved_settings):
    """Restore FBX options changed by the exporter."""
    for command, value in saved_settings.items():
        try:
            if isinstance(value, str):
                restored = '"{}"'.format(value.replace('"', '\\"'))
            elif isinstance(value, bool):
                restored = str(value).lower()
            else:
                restored = str(value)
            mel.eval("{} -v {};".format(command, restored))
        except (RuntimeError, ValueError, TypeError):
            cmds.warning("Could not restore FBX setting: " + command)


def export_open_scene(cmds, mel, root_name, output_path, status_callback=None):
    """Bake and export one already-open Maya scene to a controller-free FBX."""
    root = _find_root_joint(cmds, root_name)
    joints, names = _joint_hierarchy(cmds, root)
    start = cmds.playbackOptions(query=True, minTime=True)
    end = cmds.playbackOptions(query=True, maxTime=True)
    if start > end or start != int(start) or end != int(end):
        raise RuntimeError("The playback start and end values must be valid whole frames.")
    start, end = int(start), int(end)

    output_path = _normalized_path(output_path)
    output_directory = os.path.dirname(output_path)
    if not os.path.isdir(output_directory):
        os.makedirs(output_directory)
    if not output_path.lower().endswith(".fbx"):
        output_path += ".fbx"

    if not cmds.pluginInfo("fbxmaya", query=True, loaded=True):
        cmds.loadPlugin("fbxmaya", quiet=True)

    old_time = cmds.currentTime(query=True)
    old_namespace = cmds.namespaceInfo(currentNamespace=True, absoluteName=True)
    namespace = "FBX_Export_" + uuid.uuid4().hex[:8]
    constraints = []
    copies = []
    mapping = {}
    saved_settings = {}
    temporary_file = os.path.join(
        output_directory, ".fbx_export_" + uuid.uuid4().hex + ".fbx"
    )

    try:
        cmds.namespace(setNamespace=":")
        cmds.namespace(add=namespace)
        cmds.currentTime(start, edit=True)

        for source, name in zip(joints, names):
            parent = cmds.listRelatives(source, parent=True, fullPath=True) or []
            ancestor = parent[0] if parent else None
            while ancestor and ancestor not in mapping:
                parents = cmds.listRelatives(ancestor, parent=True, fullPath=True) or []
                ancestor = parents[0] if parents else None

            arguments = {"name": namespace + ":" + name}
            if ancestor:
                arguments["parent"] = mapping[ancestor]
            target = cmds.createNode("joint", **arguments)
            target = cmds.ls(target, long=True)[0]
            mapping[source] = target
            copies.append(target)

            for attribute in ("rotateOrder", "segmentScaleCompensate", "radius"):
                cmds.setAttr(
                    target + "." + attribute,
                    cmds.getAttr(source + "." + attribute),
                )
            for attribute in ("jointOrient", "rotateAxis"):
                cmds.setAttr(
                    target + "." + attribute,
                    *cmds.getAttr(source + "." + attribute)[0]
                )
            constraints.extend(
                cmds.parentConstraint(source, target, maintainOffset=False)
            )
            constraints.extend(
                cmds.scaleConstraint(source, target, maintainOffset=False)
            )

        if status_callback:
            status_callback("Baking frames {}-{}...".format(start, end))
        cmds.bakeResults(
            copies,
            time=(start, end),
            sampleBy=1,
            simulation=True,
            attribute=["tx", "ty", "tz", "rx", "ry", "rz", "sx", "sy", "sz"],
            preserveOutsideKeys=False,
            sparseAnimCurveBake=False,
            minimizeRotation=True,
            shape=False,
        )
        cmds.delete(constraints)
        constraints = []

        if status_callback:
            status_callback("Validating baked poses...")
        for frame in range(start, end + 1):
            cmds.currentTime(frame, edit=True)
            for source, target in mapping.items():
                original = cmds.xform(
                    source, query=True, worldSpace=True, matrix=True
                )
                baked = cmds.xform(
                    target, query=True, worldSpace=True, matrix=True
                )
                if any(
                    abs(a - b) > 0.001 + abs(a) * 0.00001
                    for a, b in zip(original, baked)
                ):
                    raise RuntimeError(
                        "The baked pose does not match joint {} at frame {}. "
                        "Check for non-uniform scale, shear, or dynamic simulation.".format(
                            source, frame
                        )
                    )

        settings = {
            "FBXExportAnimationOnly": "false",
            "FBXExportBakeComplexAnimation": "true",
            "FBXExportBakeComplexStart": str(start),
            "FBXExportBakeComplexEnd": str(end),
            "FBXExportBakeComplexStep": "1",
            "FBXExportBakeResampleAnimation": "true",
            "FBXExportInputConnections": "false",
            "FBXExportConstraints": "false",
            "FBXExportCameras": "false",
            "FBXExportLights": "false",
            "FBXExportSkins": "false",
            "FBXExportShapes": "false",
            "FBXExportEmbeddedTextures": "false",
            "FBXExportIncludeChildren": "true",
            "FBXExportApplyConstantKeyReducer": "false",
            "FBXExportInAscii": "true",
        }
        for command, value in settings.items():
            _set_fbx_option(mel, saved_settings, command, value)

        if status_callback:
            status_callback("Writing FBX...")
        cmds.select(copies, replace=True)
        escaped = temporary_file.replace("\\", "/").replace('"', '\\"')
        mel.eval('FBXExport -f "{}" -s;'.format(escaped))
        if not os.path.isfile(temporary_file) or os.path.getsize(temporary_file) == 0:
            raise RuntimeError("The FBX plug-in did not create a valid file.")

        with open(temporary_file, "rb") as stream:
            data = stream.read()
        if not data.startswith(b"; FBX"):
            raise RuntimeError(
                "The FBX plug-in did not produce ASCII FBX. Export stopped to protect bone names."
            )
        namespace_prefix = (namespace + ":").encode("ascii")
        with open(temporary_file, "wb") as stream:
            stream.write(data.replace(namespace_prefix, b""))
        os.replace(temporary_file, output_path)
    finally:
        _restore_fbx_options(cmds, mel, saved_settings)
        if constraints:
            existing = [node for node in constraints if cmds.objExists(node)]
            if existing:
                cmds.delete(existing)
        if copies and cmds.objExists(copies[0]):
            cmds.delete(copies[0])
        cmds.namespace(setNamespace=old_namespace)
        if cmds.namespace(exists=namespace):
            cmds.namespace(removeNamespace=namespace, deleteNamespaceContent=True)
        cmds.currentTime(old_time, edit=True)
        if os.path.exists(temporary_file):
            os.remove(temporary_file)

    return {
        "output": output_path,
        "start": start,
        "end": end,
        "joint_count": len(joints),
    }


class ExporterWindow(QtWidgets.QWidget):
    """Project-aware standalone batch exporter window."""

    def __init__(self, cmds, mel, initial_paths=None):
        super(ExporterWindow, self).__init__()
        self.cmds = cmds
        self.mel = mel
        self.setWindowTitle(APP_TITLE)
        self.resize(760, 650)
        self._build_ui()
        self._load_last_project()
        if initial_paths:
            self._add_paths(initial_paths)

    def _build_ui(self):
        """Create the exporter controls."""
        main_layout = QtWidgets.QVBoxLayout(self)
        form_layout = QtWidgets.QFormLayout()

        self.project_edit = QtWidgets.QLineEdit()
        project_row = QtWidgets.QHBoxLayout()
        project_row.addWidget(self.project_edit)
        project_button = QtWidgets.QPushButton("Browse Project")
        project_button.clicked.connect(self._choose_project)
        project_row.addWidget(project_button)
        form_layout.addRow("Project Directory", project_row)

        self.root_edit = QtWidgets.QLineEdit()
        self.root_edit.setPlaceholderText("Example: charName_jnt_root")
        form_layout.addRow("Root Joint Name", self.root_edit)

        self.exclude_name_edit = QtWidgets.QLineEdit()
        self.exclude_name_edit.setPlaceholderText("Example: ANIM_yf_")
        form_layout.addRow("Remove from Output Name", self.exclude_name_edit)

        self.output_edit = QtWidgets.QLineEdit()
        output_row = QtWidgets.QHBoxLayout()
        output_row.addWidget(self.output_edit)
        output_button = QtWidgets.QPushButton("Browse Output")
        output_button.clicked.connect(self._choose_output)
        output_row.addWidget(output_button)
        form_layout.addRow("FBX Output Directory", output_row)
        main_layout.addLayout(form_layout)

        source_header = QtWidgets.QHBoxLayout()
        source_header.addWidget(QtWidgets.QLabel("Maya Scenes to Export"))
        source_header.addStretch()
        self.recursive_check = QtWidgets.QCheckBox("Include Subfolders")
        self.recursive_check.setChecked(True)
        source_header.addWidget(self.recursive_check)
        main_layout.addLayout(source_header)

        self.file_list = QtWidgets.QListWidget()
        self.file_list.setSelectionMode(
            QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection
        )
        main_layout.addWidget(self.file_list, 2)

        file_buttons = QtWidgets.QHBoxLayout()
        add_files_button = QtWidgets.QPushButton("Add Files")
        add_files_button.clicked.connect(self._choose_files)
        file_buttons.addWidget(add_files_button)
        add_folder_button = QtWidgets.QPushButton("Add Folder")
        add_folder_button.clicked.connect(self._choose_source_folder)
        file_buttons.addWidget(add_folder_button)
        remove_button = QtWidgets.QPushButton("Remove Selected")
        remove_button.clicked.connect(self._remove_selected)
        file_buttons.addWidget(remove_button)
        clear_button = QtWidgets.QPushButton("Clear")
        clear_button.clicked.connect(self.file_list.clear)
        file_buttons.addWidget(clear_button)
        file_buttons.addStretch()
        main_layout.addLayout(file_buttons)

        settings_row = QtWidgets.QHBoxLayout()
        save_button = QtWidgets.QPushButton("Save Project Settings")
        save_button.clicked.connect(self._save_project_settings)
        settings_row.addWidget(save_button)
        settings_row.addStretch()
        self.export_button = QtWidgets.QPushButton("Export FBX")
        self.export_button.setMinimumHeight(38)
        self.export_button.clicked.connect(self._export)
        settings_row.addWidget(self.export_button)
        main_layout.addLayout(settings_row)

        self.log = QtWidgets.QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(1000)
        main_layout.addWidget(self.log, 1)

        self.project_edit.editingFinished.connect(self._project_changed)

    def _session_config_path(self):
        """Return the small per-tool file used to remember the last project."""
        return os.path.join(_script_directory(), SESSION_CONFIG_NAME)

    def _project_config_path(self):
        """Return the active project's exporter configuration path."""
        project = self.project_edit.text().strip()
        return os.path.join(_normalized_path(project), PROJECT_CONFIG_NAME) if project else ""

    def _load_last_project(self):
        """Restore the most recently used project and its settings."""
        session = _read_json(self._session_config_path())
        project = session.get("last_project") or _script_directory()
        self.project_edit.setText(os.path.normpath(project))
        self._load_project_settings()

    def _load_project_settings(self):
        """Load the root bone and output directory stored in the project."""
        project = self.project_edit.text().strip()
        if not project:
            return
        project = _normalized_path(project)
        config = _read_json(os.path.join(project, PROJECT_CONFIG_NAME))
        self.root_edit.setText(config.get("root_joint", ""))
        self.exclude_name_edit.setText(config.get("remove_from_output_name", ""))
        output = config.get("output_directory") or os.path.join(project, "FBX")
        self.output_edit.setText(os.path.normpath(output))
        self.recursive_check.setChecked(config.get("recursive", True))

    def _save_project_settings(self, show_message=True):
        """Persist settings inside the active project."""
        project = self.project_edit.text().strip()
        root = self.root_edit.text().strip()
        remove_from_name = self.exclude_name_edit.text()
        output = self.output_edit.text().strip()
        if not project or not os.path.isdir(_normalized_path(project)):
            raise RuntimeError("Select a valid project directory.")
        if not root:
            raise RuntimeError("Enter a root joint name.")
        if not output:
            raise RuntimeError("Select an FBX output directory.")
        project = _normalized_path(project)
        output = _normalized_path(output)
        _write_json(
            os.path.join(project, PROJECT_CONFIG_NAME),
            {
                "root_joint": root,
                "remove_from_output_name": remove_from_name,
                "output_directory": output,
                "recursive": self.recursive_check.isChecked(),
            },
        )
        _write_json(self._session_config_path(), {"last_project": project})
        if show_message:
            self._append_log("Project settings saved: " + os.path.join(project, PROJECT_CONFIG_NAME))

    def _project_changed(self):
        """Load settings when the project field changes."""
        project = self.project_edit.text().strip()
        if project and os.path.isdir(_normalized_path(project)):
            self._load_project_settings()

    def _choose_project(self):
        """Choose a project directory and load its saved settings."""
        start = self.project_edit.text().strip() or _script_directory()
        path = QtWidgets.QFileDialog.getExistingDirectory(self, "Select Project Directory", start)
        if path:
            self.project_edit.setText(os.path.normpath(path))
            self._load_project_settings()

    def _choose_output(self):
        """Choose the destination folder for generated FBX files."""
        start = self.output_edit.text().strip() or self.project_edit.text().strip()
        path = QtWidgets.QFileDialog.getExistingDirectory(self, "Select FBX Output Directory", start)
        if path:
            self.output_edit.setText(os.path.normpath(path))

    def _choose_files(self):
        """Add Maya scene files through a file picker."""
        start = self.project_edit.text().strip() or _script_directory()
        paths, _ = QtWidgets.QFileDialog.getOpenFileNames(
            self, "Select Maya Animation Scenes", start, "Maya Scenes (*.ma *.mb)"
        )
        self._add_paths(paths)

    def _choose_source_folder(self):
        """Add Maya scenes contained in one directory."""
        start = self.project_edit.text().strip() or _script_directory()
        path = QtWidgets.QFileDialog.getExistingDirectory(self, "Select Animation Scene Directory", start)
        if path:
            self._add_paths([path])

    def _add_paths(self, paths):
        """Add unique scene paths to the file list."""
        existing = {
            os.path.normcase(self.file_list.item(index).text())
            for index in range(self.file_list.count())
        }
        for path in _collect_scenes(paths, self.recursive_check.isChecked()):
            if os.path.normcase(path) not in existing:
                self.file_list.addItem(path)
                existing.add(os.path.normcase(path))

    def _remove_selected(self):
        """Remove selected scene paths from the queue."""
        for item in self.file_list.selectedItems():
            self.file_list.takeItem(self.file_list.row(item))

    def _append_log(self, message):
        """Append one line and keep the window responsive."""
        self.log.appendPlainText(message)
        QtWidgets.QApplication.processEvents()

    def _scene_paths(self):
        """Return every queued Maya scene path."""
        return [self.file_list.item(index).text() for index in range(self.file_list.count())]

    def _output_paths(self, scenes, output_directory, remove_from_name):
        """Clean output names, build flat paths, and reject invalid results."""
        outputs = {}
        collisions = []
        cleaned_names = {}
        for scene in scenes:
            stem = os.path.splitext(os.path.basename(scene))[0]
            cleaned_stem = stem.replace(remove_from_name, "") if remove_from_name else stem
            if not cleaned_stem:
                raise RuntimeError(
                    "Removing '{}' leaves an empty output name for: {}".format(
                        remove_from_name, scene
                    )
                )
            key = cleaned_stem.lower()
            if key in outputs:
                collisions.append(cleaned_stem)
            outputs[key] = scene
            cleaned_names[scene] = cleaned_stem
        if collisions:
            raise RuntimeError(
                "These scenes produce the same output name after cleanup: "
                + ", ".join(sorted(set(collisions)))
            )
        return {
            scene: os.path.join(output_directory, cleaned_names[scene] + ".fbx")
            for scene in scenes
        }

    @QtCore.Slot()
    def _export(self):
        """Open and export every queued scene without displaying Maya Editor."""
        try:
            self._save_project_settings(show_message=False)
            scenes = self._scene_paths()
            if not scenes:
                raise RuntimeError("Add at least one .ma or .mb file.")
            output_directory = _normalized_path(self.output_edit.text())
            if not os.path.isdir(output_directory):
                os.makedirs(output_directory)
            outputs = self._output_paths(
                scenes, output_directory, self.exclude_name_edit.text()
            )
            root_name = self.root_edit.text().strip()
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, APP_TITLE, str(exc))
            return

        self.export_button.setEnabled(False)
        succeeded = []
        failed = []
        self._append_log("Starting export for {} scene(s).".format(len(scenes)))

        try:
            for index, scene in enumerate(scenes, 1):
                self._append_log("\n[{}/{}] {}".format(index, len(scenes), scene))
                try:
                    self.cmds.file(
                        scene,
                        open=True,
                        force=True,
                        prompt=False,
                        ignoreVersion=True,
                    )
                    result = export_open_scene(
                        self.cmds,
                        self.mel,
                        root_name,
                        outputs[scene],
                        status_callback=self._append_log,
                    )
                    succeeded.append(result["output"])
                    self._append_log(
                        "Completed: {} | Frames {}-{} | {} joints".format(
                            result["output"],
                            result["start"],
                            result["end"],
                            result["joint_count"],
                        )
                    )
                except Exception as exc:
                    failed.append((scene, str(exc)))
                    self._append_log("Failed: " + str(exc))
                    self._append_log(traceback.format_exc())
                finally:
                    try:
                        self.cmds.file(new=True, force=True)
                    except RuntimeError:
                        pass
        finally:
            self.export_button.setEnabled(True)

        summary = "Export finished: {} succeeded, {} failed.".format(
            len(succeeded), len(failed)
        )
        self._append_log("\n" + summary)
        if failed:
            details = "\n".join("{}\n  {}".format(scene, error) for scene, error in failed)
            QtWidgets.QMessageBox.warning(self, APP_TITLE, summary + "\n\n" + details)
        else:
            QtWidgets.QMessageBox.information(self, APP_TITLE, summary)


def main():
    """Create Qt first, then start Maya standalone and display the window."""
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    _hide_console_window()
    maya_initialized = False
    try:
        cmds, mel = _initialize_maya()
        maya_initialized = True
        initial_paths = [value for value in sys.argv[1:] if os.path.exists(value)]
        window = ExporterWindow(cmds, mel, initial_paths=initial_paths)
        window.show()
        return application.exec()
    finally:
        if maya_initialized:
            try:
                import maya.standalone

                maya.standalone.uninitialize()
            except RuntimeError:
                pass


if __name__ == "__main__":
    sys.exit(main())
