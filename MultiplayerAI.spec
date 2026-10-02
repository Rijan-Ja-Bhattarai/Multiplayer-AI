# Build with: python -m PyInstaller MultiplayerAI.spec
from PyInstaller.utils.hooks import collect_submodules
from pathlib import Path
import sys

# Conda keeps Python's dependent DLLs outside the interpreter's DLLs folder.
library = Path(sys.prefix) / 'Library' / 'bin'
conda_binaries = [(str(path), '.') for path in library.glob('*.dll')
                  if path.name.lower().startswith(('libbz2', 'libmpdec', 'libcrypto', 'libssl', 'ffi', 'zlib'))]

analysis = Analysis(
    ['desktop_launcher.py'], pathex=['.'], binaries=conda_binaries, datas=[],
    hiddenimports=collect_submodules('uvicorn') + collect_submodules('websockets') +
                  ['keyring.backends.Windows', 'keyring.backends.macOS', 'keyring.backends.SecretService'],
    hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=['tkinter', 'PyQt5', 'PyQt6', 'PySide2'],
    noarchive=False,
)
pyz = PYZ(analysis.pure)
exe = EXE(pyz, analysis.scripts, [], exclude_binaries=True, name='MultiplayerAI', debug=False,
          bootloader_ignore_signals=False, strip=False, upx=False, console=False)
bundle = COLLECT(exe, analysis.binaries, analysis.datas, strip=False, upx=False, name='MultiplayerAI')
