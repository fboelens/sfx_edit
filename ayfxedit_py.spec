# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['ayfxedit_py.py'],
    pathex=[],
    binaries=[('C:\\Users\\fboel\\AppData\\Local\\Programs\\Python\\Python312\\DLLs\\_tkinter.pyd', '.'), ('C:\\Users\\fboel\\AppData\\Local\\Programs\\Python\\Python312\\DLLs\\tcl86t.dll', '.'), ('C:\\Users\\fboel\\AppData\\Local\\Programs\\Python\\Python312\\DLLs\\tk86t.dll', '.')],
    datas=[('C:\\Users\\fboel\\AppData\\Local\\Programs\\Python\\Python312\\tcl\\tcl8.6', '_tcl_data'), ('C:\\Users\\fboel\\AppData\\Local\\Programs\\Python\\Python312\\tcl\\tk8.6', '_tk_data')],
    hiddenimports=['tkinter'],
    hookspath=['pyinstaller_hooks'],
    hooksconfig={},
    runtime_hooks=['pyinstaller_tk_runtime.py'],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='ayfxedit_py',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
