"""PyInstaller onedir specification for the three AWS Connect entry points."""

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules


repository_root = Path(SPECPATH).parent
source_root = repository_root / "src"
common_hidden_imports = collect_submodules("boto3") + collect_submodules("botocore")
common_datas = collect_data_files("botocore") + [
    (
        str(source_root / "aws_connect" / "presentation" / "gui" / "assets"),
        "aws_connect/presentation/gui/assets",
    )
]


def analysis(entry_point: str):
    return Analysis(
        [str(source_root / "aws_connect" / entry_point)],
        pathex=[str(source_root)],
        binaries=[],
        datas=common_datas,
        hiddenimports=common_hidden_imports,
        hookspath=[],
        hooksconfig={},
        runtime_hooks=[],
        excludes=[],
        noarchive=False,
        optimize=0,
    )


gui_analysis = analysis("gui_main.py")
cli_analysis = analysis("cli_main.py")
host_analysis = analysis("session_host.py")

gui_pyz = PYZ(gui_analysis.pure)
cli_pyz = PYZ(cli_analysis.pure)
host_pyz = PYZ(host_analysis.pure)

gui_exe = EXE(
    gui_pyz,
    gui_analysis.scripts,
    [],
    exclude_binaries=True,
    name="aws_connect",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
)
cli_exe = EXE(
    cli_pyz,
    cli_analysis.scripts,
    [],
    exclude_binaries=True,
    name="aws_connect_cli",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
)
host_exe = EXE(
    host_pyz,
    host_analysis.scripts,
    [],
    exclude_binaries=True,
    name="aws_connect_session_host",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
)

bundle = COLLECT(
    gui_exe,
    gui_analysis.binaries,
    gui_analysis.datas,
    cli_exe,
    cli_analysis.binaries,
    cli_analysis.datas,
    host_exe,
    host_analysis.binaries,
    host_analysis.datas,
    strip=False,
    upx=False,
    name="aws-connect",
)
