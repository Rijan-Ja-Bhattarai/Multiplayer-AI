"""Package a built desktop app and its per-user installer for GitHub Releases."""
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


def main():
    root = Path(__file__).resolve().parent
    bundle = root / 'dist' / 'MultiplayerAI'
    if not (bundle / 'MultiplayerAI.exe').is_file():
        raise SystemExit('Build the app with PyInstaller first.')
    output = root / 'dist' / 'MultiplayerAI-Windows-x64.zip'
    files = sorted(path for path in bundle.rglob('*') if path.is_file())
    with ZipFile(output, 'w', ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, path.relative_to(bundle.parent))
        for name in ('Install-MultiplayerAI.cmd', 'Install-MultiplayerAI.ps1', 'DESKTOP_GUIDE.md'):
            archive.write(root / name, name)
    with ZipFile(output) as archive:
        failed = archive.testzip()
        if failed:
            raise SystemExit(f'Archive integrity check failed: {failed}')
    print(f'Release ready: {output} ({output.stat().st_size:,} bytes)')


if __name__ == '__main__':
    main()
