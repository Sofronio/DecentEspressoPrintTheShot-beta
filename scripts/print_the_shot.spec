# -*- mode: python ; coding: utf-8 -*-
# PrintTheShot Beta PyInstaller spec
# 相比 v1.6:无 matplotlib/numpy,spec 简单得多;内置字体/模板/插件

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(SPEC)))

a = Analysis(
    [os.path.join(ROOT, 'print_the_shot_server.py')],
    pathex=[ROOT],
    binaries=[],
    datas=[
        (os.path.join(ROOT, 'fonts'), 'fonts'),          # 内置中文字体
        (os.path.join(ROOT, 'web'), 'web'),              # 管理界面模板
        (os.path.join(ROOT, 'plugin'), 'plugin'),        # DE1插件
    ],
    hiddenimports=[
        'PIL._imaging',
        'PIL._imagingft',
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        'tkinter', 'test', 'pydoc', 'pdb',
        'matplotlib', 'numpy', 'scipy', 'pandas', 'cv2',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=None,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=None)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    name='PrintTheShot',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,               # 避免杀软误报,关闭UPX
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False if sys.platform != 'darwin' else True,
    target_arch=None,
    codesign_identity=None,
    entitle_file=None,
    icon=None,
)
