# PyInstaller spec for the LocalDeploy desktop tray launcher (Release R7,
# Phase A packaging). Bundles the existing FastAPI backend + web UI behind a
# system tray icon — no separate installer logic, no Python/venv required by
# the end user.
#
# Build (Windows):
#   pip install -e ".[packaging]"
#   pyinstaller packaging/localdeploy.spec --distpath dist --workpath build
#
# Output: dist/LocalDeploy/LocalDeploy.exe (onedir build — faster startup and
# easier antivirus allow-listing than a single-file --onefile build; switch
# to onefile later if a single downloadable .exe is preferred over a folder).
#
# NOT done here (see docs/ROADMAP.md P0.1 "Phase B"): code signing,
# notarization, an actual NSIS/MSI installer wrapper, and macOS/Linux
# equivalents. Those need a signing certificate, an Apple developer account,
# and platform-specific build machines this environment doesn't have.
from __future__ import annotations

from pathlib import Path

block_cipher = None
# PyInstaller execs spec files with no __file__; SPECPATH (this spec's own
# directory) is injected into the exec namespace by PyInstaller itself.
REPO_ROOT = Path(SPECPATH).resolve().parent  # noqa: F821

a = Analysis(
    [str(REPO_ROOT / "packaging" / "tray_app.py")],
    pathex=[str(REPO_ROOT)],
    binaries=[],
    datas=[
        (str(REPO_ROOT / "localdeploy" / "web"), "localdeploy/web"),
    ],
    hiddenimports=[
        # Root companion modules (declared as py-modules in pyproject.toml,
        # imported lazily throughout localdeploy/ — PyInstaller's static
        # analysis does not always see through `import X` inside a function).
        "api_server",
        "benchmark",
        "chat_cli",
        "compare_models",
        # FastAPI/uvicorn/pydantic optional extras used at runtime.
        "uvicorn.logging",
        "uvicorn.loops.auto",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.lifespan.on",
        "yaml",
        "multipart",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="LocalDeploy",
    debug=False,
    strip=False,
    upx=False,
    console=False,  # tray app: no console window
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name="LocalDeploy",
)
