# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置文件 (onedir 模式，启动秒开)。"""

import os

# 项目根目录
BASE_DIR = os.path.abspath(SPECPATH)

a = Analysis(
    [os.path.join(BASE_DIR, 'main.py')],
    pathex=[BASE_DIR],
    binaries=[],
    datas=[
        # 只打包必要的配置文件，排除运行时生成的缓存/日志
        (os.path.join(BASE_DIR, 'resources', 'configs', 'ui.ico'), os.path.join('resources', 'configs')),
        (os.path.join(BASE_DIR, 'resources', 'configs', 'workflow_config.json'), os.path.join('resources', 'configs')),
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
    excludes=[
        # 以下包未被项目使用，PyInstaller 误收集，排除以减小打包体积
        'pythonwin', 'win32com', 'win32ui', 'win32api', 'win32con', 'pywin32',
        'pythonnet', 'clr', 'PIL', 'Pillow',
        'cryptography', 'pydantic', 'pydantic_core',
        'PyQt6.QtPdf', 'PyQt6.QtPdfWidgets',
        'PyQt6.QtWebEngineCore', 'PyQt6.QtWebEngineWidgets', 'PyQt6.QtWebEngine',
        'PyQt6.QtQml', 'PyQt6.QtQuick', 'PyQt6.QtQuickWidgets',
        'PyQt6.QtMultimedia', 'PyQt6.QtMultimediaWidgets',
        'PyQt6.QtCharts', 'PyQt6.QtDataVisualization',
    ],
    noarchive=False,
    optimize=0,
)

# 排除未使用的 Qt 动态库（PyQt6 hook 会收集全部 Qt DLL，实际只用 Core/Gui/Widgets）
_EXCLUDE_QT_DLLS = ('Qt6Pdf', 'Qt6Network', 'Qt6Svg')
a.binaries = [
    toc for toc in a.binaries
    if not any(k in toc[0] for k in _EXCLUDE_QT_DLLS)
]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    exclude_binaries=True,   # onedir: 二进制文件放到目录中，不嵌入 exe
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

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='双语音声工作流',
)
