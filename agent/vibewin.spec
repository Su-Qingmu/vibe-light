# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for vibewin (Windows GUI)

Build:
    pyinstaller --noconfirm vibewin.spec
Output:
    dist/vibewin.exe (onefile, ~6 MB, windowed)
"""

block_cipher = None

a = Analysis(
    ['vibewin.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Strip heavyweight stdlib modules that tkinter pulls in transitively.
    # tkinter itself + the GUI classes we use are NOT in this list.
    excludes=[
        'numpy', 'pandas', 'scipy', 'matplotlib',
        'PIL', 'cv2', 'sklearn', 'torch',
        'tkinter.test', 'tkinter.tix',
        'unittest', 'test',
        'email', 'html', 'http', 'urllib',
        'xml', 'xmlrpc', 'pydoc', 'doctest',
        'sqlite3', 'ssl',
        'logging.handlers', 'multiprocessing',
        'concurrent', 'wsgiref',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='vibewin',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,                # GUI: no console window flash
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # icon='vibewin.ico',        # uncomment when an icon asset exists
)