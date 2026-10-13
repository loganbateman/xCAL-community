from pathlib import Path

root = Path(SPECPATH)
analysis = Analysis(
    [str(root / 'src' / 'main.py')],
    pathex=[str(root / 'src')],
    binaries=[],
    datas=[(str(root / 'src' / 'assets'), 'assets'), (str(root / 'resources'), 'resources')],
    hiddenimports=[],
    excludes=['tkinter'],
    noarchive=False,
)
archive = PYZ(analysis.pure)
executable = EXE(archive, analysis.scripts, [], exclude_binaries=True,
                 name='xCAL', console=False)
collection = COLLECT(executable, analysis.binaries, analysis.datas,
                     strip=False, upx=False, name='xCAL-community')
