"""PyInstaller entry point — launches the native Fiskeretta Diagnostics window."""

import os
import sys

# Python.Runtime.dll (loaded by pythonnet via the .NET runtime) needs to find
# python3XX.dll. In a PyInstaller one-dir bundle, that DLL lives in _internal/
# (_MEIPASS), which isn't on the Windows DLL search path by default.
if hasattr(sys, '_MEIPASS'):
    os.environ['PATH'] = sys._MEIPASS + os.pathsep + os.environ.get('PATH', '')

from fiskeretta.app import main

if __name__ == "__main__":
    main()
