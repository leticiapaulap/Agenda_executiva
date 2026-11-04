# -*- mode: python ; coding: utf-8 -*-
"""Build Windows: python -m PyInstaller --noconfirm AgendaExecutiva.spec."""
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

project_dir = Path(SPECPATH)
ctk_datas, ctk_binaries, ctk_hiddenimports = collect_all("customtkinter")

a = Analysis(
    [str(project_dir / "agenda_executiva.py")],
    pathex=[str(project_dir)],
    binaries=ctk_binaries,
    datas=ctk_datas + [(str(project_dir / "assets"), "assets")],
    hiddenimports=ctk_hiddenimports,
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
    name="AgendaExecutiva",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    icon=str(project_dir / "assets" / "app.ico"),
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="AgendaExecutiva",
)
