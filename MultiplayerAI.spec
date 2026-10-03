# Build with: python -m PyInstaller MultiplayerAI.spec
from PyInstaller.utils.hooks import collect_submodules
from pathlib import Path
import sys

# Conda keeps Python's dependent DLLs outside the interpreter's DLLs folder.
library = Path(sys.prefix) / 'Library' / 'bin'
conda_binaries = [(str(path), '.') for path in library.glob('*.dll')
                  if path.name.lower().startswith(('libbz2', 'libmpdec', 'libcrypto', 'libssl', 'ffi', 'zlib', 'sqlite3'))]

analysis = Analysis(
    ['desktop_launcher.py'], pathex=['.'], binaries=conda_binaries, datas=[],
    hiddenimports=collect_submodules('uvicorn') + collect_submodules('websockets') +
                  ['keyring.backends.Windows', 'keyring.backends.macOS', 'keyring.backends.SecretService'],
    hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=['tkinter', 'PyQt5', 'PyQt6', 'PySide2'],
    noarchive=False,
)

if sys.platform == 'win32':
    # This app draws native widgets with QPainter. It has no QML, PDF viewer,
    # virtual keyboard, SVG assets, or OpenGL surface. Qt's broad default hooks
    # otherwise collect these features and their transitive DLLs.
    unused_dlls = {
        'opengl32sw.dll', 'Qt6Quick.dll', 'Qt6Qml.dll', 'Qt6QmlModels.dll',
        'Qt6QmlMeta.dll', 'Qt6QmlWorkerScript.dll', 'Qt6VirtualKeyboard.dll',
        'Qt6Pdf.dll', 'Qt6Svg.dll', 'Qt6OpenGL.dll',
    }
    plugin_allowlist = {
        'platforms/qwindows.dll', 'platforms/qoffscreen.dll',
        'styles/qmodernwindowsstyle.dll', 'imageformats/qico.dll',
        'networkinformation/qnetworklistmanager.dll',
        'tls/qcertonlybackend.dll', 'tls/qopensslbackend.dll',
        'tls/qschannelbackend.dll',
    }

    def needed(entry):
        destination = entry[0].replace('\\', '/')
        if destination.rsplit('/', 1)[-1] in unused_dlls:
            return False
        if '/translations/' in destination and destination.startswith('PySide6/'):
            return False  # The current application is English only.
        if destination.startswith('PySide6/plugins/'):
            return destination.removeprefix('PySide6/plugins/') in plugin_allowlist
        return True

    analysis.binaries = [entry for entry in analysis.binaries if needed(entry)]
    analysis.datas = [entry for entry in analysis.datas if needed(entry)]

pyz = PYZ(analysis.pure)
exe = EXE(pyz, analysis.scripts, [], exclude_binaries=True, name='MultiplayerAI', debug=False,
          bootloader_ignore_signals=False, strip=False, upx=False, console=False)
bundle = COLLECT(exe, analysis.binaries, analysis.datas, strip=False, upx=False, name='MultiplayerAI')
