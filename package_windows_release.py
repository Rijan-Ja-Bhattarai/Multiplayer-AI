"""Package a built desktop app and its per-user installer for GitHub Releases."""
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile
import argparse


def main():
    """Build the Windows release archive from the executable bundle and project guides."""
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', type=Path, default=root / 'dist' / 'MultiplayerAI')
    args = parser.parse_args()
    bundle = args.bundle.resolve()
    if not (bundle / 'MultiplayerAI.exe').is_file():
        raise SystemExit('Build the app with PyInstaller first.')
    output = root / 'dist' / 'MultiplayerAI-Windows-x64.zip'
    files = sorted(path for path in bundle.rglob('*') if path.is_file())
    with ZipFile(output, 'w', ZIP_DEFLATED, compresslevel=9) as archive:
        for path in files:
            archive.write(path, path.relative_to(bundle.parent))
        for name in ('Install-MultiplayerAI.cmd', 'Install-MultiplayerAI.ps1', 'DESKTOP_GUIDE.md', 'docs.html'):
            archive.write(root / name, name)
    with ZipFile(output) as archive:
        failed = archive.testzip()
        if failed:
            raise SystemExit(f'Archive integrity check failed: {failed}')
    print(f'Release ready: {output} ({output.stat().st_size:,} bytes)')


if __name__ == '__main__':
    main()
