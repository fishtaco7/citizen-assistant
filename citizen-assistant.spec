# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller recipe for the Windows build.

Data layout matters: the app resolves ``app/data`` and ``app.ico`` relative to
``__file__``, so both must sit at the same paths inside the bundle.

rapidocr_onnxruntime is imported lazily inside SignatureOCR, which static
analysis cannot see, hence the explicit collect_all calls.
"""
from PyInstaller.utils.hooks import collect_all, collect_submodules

datas = [
    ("app/data", "app/data"),
    ("app.ico", "."),
]
binaries = []
hiddenimports = collect_submodules("app")

for package in ("rapidocr_onnxruntime", "onnxruntime"):
    package_datas, package_binaries, package_hiddenimports = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hiddenimports

a = Analysis(
    ["launcher.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="CitizenAssistant",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=["app.ico"],
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="CitizenAssistant",
)
