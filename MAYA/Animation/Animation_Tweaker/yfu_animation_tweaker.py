from PySide6.QtCore import Qt, QFile
from PySide6.QtWidgets import QMainWindow, QWidget
from PySide6.QtUiTools import QUiLoader
from functools import partial

from maya import OpenMayaUI as omui
from shiboken6 import wrapInstance

import maya.cmds as cmds


def paSlider_update():
    paSlider_value = ui.paSlider.value() 
    ui.paSpinBox.setValue(paSlider_value) # sync spinbox value to slider
    if ui.autoUpdateButton.isChecked():
        feed_value_back()
    

def paSpinBox_update():
    paSpinbox_value = ui.paSpinBox.value()
    ui.paSlider.setValue(paSpinbox_value) # sync slider value to spinbox


def manual_update():
    feed_value_back()
    

def feed_value_back():

    #get info
    currentTime = cmds.currentTime(query=True)
    previousKey = cmds.findKeyframe(timeSlider=True, which="previous")
    nextKey = cmds.findKeyframe(timeSlider=True, which="next")
    paSlider_value = ui.paSlider.value()

    # check if object has key needed
    if previousKey is None or nextKey is None:
        print("Skipping: Missing previous or next keyframe.")
        return

    # get selected objects
    selected = cmds.ls(sl=True)
    if not selected:
        print("No object selected.")
        return

    for obj in selected: # list all selected objects
       
        keyable_attributes = cmds.listAttr(obj, k=True) or [] # pic up all attribute that has animation
        attributesHasKey = [
            attr for attr in keyable_attributes
            if cmds.keyframe(obj, attribute=attr, query=True, kc=True) > 0
        ]

        if not attributesHasKey:
            print(f"No keyable attributes found for {obj}.")
            continue

        for attr in attributesHasKey: # lise all attribute
            attr_full = f"{obj}.{attr}"  # resume full name

            try:
                # get info
                previousValue = cmds.getAttr(attr_full, time=previousKey)
                nextValue = cmds.getAttr(attr_full, time=nextKey)
                diffence = previousValue + (nextValue - previousValue) * (paSlider_value / 100)

                # feed value back
                cmds.setAttr(attr_full, diffence)
                cmds.setKeyframe(attr_full, time=currentTime)
                print(f"Updated {attr_full} to {diffence} at frame {currentTime}")
            except Exception as e:
                print(f"Error processing {attr_full}: {e}")


# get maya window
mw_ptr = omui.MQtUtil.mainWindow()
mayaMainWindow = wrapInstance(int(mw_ptr), QMainWindow)


root_widget = QWidget()
loader = QUiLoader()
file = QFile(r"C:\Docs\yfu_animation_tweaker.ui") # UI fil CHANGE HERE
file.open(QFile.ReadOnly)
ui = loader.load(file)
file.close()

# set ui slider value range
ui.paSlider.setMinimum(-25)
ui.paSlider.setMaximum(125)
ui.paSpinBox.setMinimum(-25)
ui.paSpinBox.setMaximum(125)

# default checkbox checked
ui.autoUpdateButton.setChecked(True)

# ui.name.signal.runCODE
# live update, usualy a button in the end runs all the changes
ui.paSlider.valueChanged.connect(paSlider_update)
ui.paSpinBox.valueChanged.connect(paSpinBox_update)
#ui.paSlider.valueChanged.connect(feed_value_back)
ui.updateButton.clicked.connect(manual_update)

# set pop up window
ui.setWindowTitle('AnimationTweaker') # name window
ui.setWindowFlags(Qt.Window|Qt.WindowStaysOnTopHint) # Make this widget a parented standalone window
ui.show()
