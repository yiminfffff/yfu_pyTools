import maya.cmds as cmds

def add_attributes(obj_name):
    # Enum attribute
    cmds.addAttr(obj_name, longName="__________", attributeType="enum", enumName="CONTROLS", keyable=True)
    
    # Float attributes
    float_attrs = [
        {"longName": "IK_FK_Switch", "min": 0, "max": 1, "default": 0},
        {"longName": "Foot_Roll", "min": -10, "max": 10, "default": 0},
        {"longName": "Heel_Pivot", "min": -10, "max": 10, "default": 0},
        {"longName": "Ball_Pivot", "min": -10, "max": 10, "default": 0},
        {"longName": "Toe_Pivot", "min": -10, "max": 10, "default": 0},
        {"longName": "Bank_In", "min": 0, "max": 10, "default": 0},
        {"longName": "Bank_Out", "min": 0, "max": 10, "default": 0},
    ]
    
    for attr in float_attrs:
        cmds.addAttr(obj_name, 
                     longName=attr["longName"], 
                     attributeType="double", 
                     minValue=attr["min"], 
                     maxValue=attr["max"], 
                     defaultValue=attr["default"], 
                     keyable=True)

# Example usage: Replace 'your_object' with the name of the object to which you want to add attributes
selected_objects = cmds.ls(selection=True)
if selected_objects:
    for obj in selected_objects:
        add_attributes(obj)
else:
    print("No object selected. Please select an object.")
