"""Guard against the observed regular/Community install-folder collision."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_community_installer_does_not_reuse_legacy_install_destination():
    installer = (ROOT / "installer/xCAL-community.iss").read_text(encoding="utf-8")
    assert 'MyAppExeName "xCAL Community.exe"' in installer
    assert 'MyAppName "xCAL Community"' in installer
    assert "UsePreviousAppDir=no" in installer
    assert "DisableDirPage=yes" in installer
    assert "function PrepareToInstall" in installer
    assert "ExpandConstant('{localappdata}\\Programs\\xCAL Community')" in installer
    assert "AppUserModelID: \"ExcaliburRacing.xCALCommunity\"" in installer
    assert "[InstallDelete]" not in installer


def test_build_and_package_use_the_distinct_executable():
    spec = (ROOT / "xCAL.spec").read_text(encoding="utf-8")
    build = (ROOT / "build.ps1").read_text(encoding="utf-8")
    assert "name='xCAL Community'" in spec
    assert "dist\\xCAL-community\\xCAL Community.exe" in build
    assert "dist\\xCAL-community\\xCAL.exe" not in build
