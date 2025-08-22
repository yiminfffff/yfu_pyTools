import maya.cmds as cmds
import maya.mel as mel

# Get the global variable for the top-level Shelf layout
gShelfTopLevel = mel.eval('$tmp=$gShelfTopLevel')

# Query the currently active Shelf tab
current_shelf = cmds.tabLayout(gShelfTopLevel, query=True, selectTab=True)

# Check if there is an active Shelf
if current_shelf:
    # Set the parent to the current Shelf
    cmds.setParent(current_shelf)
    # Add a separator to the active Shelf
    cmds.separator(style='shelf', horizontal=False)
    print(f"Separator added to Shelf: {current_shelf}")
else:
    # If no active Shelf is found, output a warning message
    print("No active Shelf found!")
