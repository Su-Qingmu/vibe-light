# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for vibewin (Windows GUI)

Build:
    pyinstaller --noconfirm vibewin.spec
Output:
    dist/vibewin.exe (onefile, ~6 MB, windowed)

NOTE: be conservative with excludes. Many stdlib modules are imported
transitively by pathlib / zipfile / inspect, and dropping any of them
breaks the bootloader runtime hook (pyi_rth_inspect) with:
  "ModuleNotFoundError: No module named 'urllib'"

Only exclude heavyweight third-party modules that tkinter doesn't need.
"""

block_cipher = None

a = Analysis(
    ['vibewin.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[
        # Force-include stdlib modules that pathlib/zipfile/inspect need
        # transitively. They're in `excludes` above for size, but the
        # bootloader runtime hook (pyi_rth_inspect) crashes at exe startup
        # without them:
        #   ModuleNotFoundError: No module named 'urllib'
        'urllib', 'urllib.parse', 'urllib.request', 'urllib.error',
        'http', 'http.client',
        'ssl', 'email', 'email.message', 'html', 'html.parser',
        'xml', 'xml.etree', 'xml.parsers',
        'sqlite3', 'concurrent', 'concurrent.futures',
        'multiprocessing', 'multiprocessing.sharedctypes',
        'unittest', 'logging', 'logging.handlers',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Only third-party heavyweights — DO NOT exclude stdlib modules here.
    # pathlib/zipfile/inspect transitively need urllib/http/ssl/etc.
    excludes=[
        'numpy', 'pandas', 'scipy', 'matplotlib',
        'PIL', 'cv2', 'sklearn', 'torch',
        'tkinter.test', 'tkinter.tix',
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