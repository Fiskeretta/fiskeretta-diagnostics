"""PyInstaller entry point — launches the native Fiskeretta Diagnostics window."""

import os
import sys

# Python.Runtime.dll (a .NET assembly) P/Invokes back into python3XX.dll.
# In a PyInstaller one-dir bundle that DLL lives in _internal/ (_MEIPASS),
# which the .NET CLR's DllImport resolver doesn't search. Neither PATH nor
# AddDllDirectory reliably fixes this — .NET P/Invoke uses its own search.
# The guaranteed fix: pre-load python3XX.dll via ctypes so it's already in
# the process module table. Any subsequent LoadLibrary("python3XX.dll") call,
# including from .NET, gets the cached handle without a directory search.
if hasattr(sys, '_MEIPASS') and sys.platform == 'win32':
    import ctypes
    _pydll = os.path.join(
        sys._MEIPASS,
        f'python{sys.version_info.major}{sys.version_info.minor}.dll',
    )
    if os.path.exists(_pydll):
        ctypes.WinDLL(_pydll)

from fiskeretta.app import main

if __name__ == "__main__":
    main()
