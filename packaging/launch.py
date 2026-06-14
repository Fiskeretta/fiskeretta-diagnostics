"""PyInstaller entry point — launches the native Fiskeretta Diagnostics window."""

import os
import sys

# Python.Runtime.dll (a .NET assembly loaded by pythonnet) P/Invokes back into
# python3XX.dll. In a PyInstaller one-dir bundle that DLL lives in _internal/
# (_MEIPASS). os.environ['PATH'] is not enough — the .NET CLR's LoadLibrary
# doesn't reliably honour PATH changes made after process start. We need
# AddDllDirectory (via os.add_dll_directory) which patches the search list at
# the Windows API level and is always respected by subsequent LoadLibrary calls.
if hasattr(sys, '_MEIPASS') and sys.platform == 'win32':
    os.add_dll_directory(sys._MEIPASS)

from fiskeretta.app import main

if __name__ == "__main__":
    main()
