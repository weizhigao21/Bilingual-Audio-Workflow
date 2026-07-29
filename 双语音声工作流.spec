# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置文件。"""

import os

# 项目根目录
BASE_DIR = os.path.abspath(SPECPATH)

a = Analysis(
    [os.path.join(BASE_DIR, 'main.py')],
    pathex=[BASE_DIR],
    binaries=[],
    datas=[
        (os.path.join(BASE_DIR, 'resources'), 'resources'),
    ],
    hiddenimports=[
        'edge_tts',
        'edge_tts.communicate',
        'edge_tts.submaker',
        'numpy',
        'numpy.core._methods',
        'numpy.lib.format',
        'pydub',
        'pydub.utils',
        'requests',
        'sqlite3',
        'asyncio',
        'ctypes',
        'json',
        'hashlib',
        'logging',
        'threading',
        'queue',
        'concurrent.futures',
        'dataclasses',
        'PyQt6.QtCore',
        'PyQt6.QtGui',
        'PyQt6.QtWidgets',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
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
    name='双语音声工作流',
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
    icon=os.path.join(BASE_DIR, 'resources', 'configs', 'ui.ico'),
)
