import sys
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from utils import race_studio


def test_file_import_passes_existing_paths_without_staging(tmp_path, monkeypatch):
    files = [tmp_path / "one.xrk", tmp_path / "two with spaces.xrk"]
    for index, path in enumerate(files):
        path.write_bytes(b"XRK" + bytes([index]))
    calls = []
    monkeypatch.setattr(race_studio.os, "name", "nt")
    monkeypatch.setattr(race_studio, "_RaceStudioWindows", lambda: type(
        "Importer", (), {"import_files": lambda self, paths: calls.append(paths)})())
    race_studio.import_xrk_files(files)
    assert calls == [[path.resolve() for path in files]]
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "one.xrk", "two with spaces.xrk"]
def test_file_import_rejects_missing_file(tmp_path):
    try:
        race_studio.import_xrk_files([tmp_path / "missing.xrk"])
    except FileNotFoundError as exc:
        assert "XRK is missing" in str(exc)
    else:
        raise AssertionError("missing XRK should fail before staging")


def test_result_reader_ignores_progress_dialog_with_same_title():
    backend = object.__new__(race_studio._RaceStudioWindows)
    backend.import_dialogs = lambda pid: ["progress", "results"]
    backend.result_lists = lambda dialog: (
        ["categories", "skipped", "imported", "errors"]
        if dialog == "results" else [])
    backend.message = lambda handle, message: {
        "skipped": 3, "imported": 9, "errors": 0}[handle]
    requests = []

    def control(dialog, identifier, caption, class_name):
        assert dialog == "results", "Progress dialog has no result controls"
        return "exit"

    backend.control = control
    backend.click = requests.append
    backend.u = SimpleNamespace(IsWindowEnabled=lambda handle: True,
                                IsWindowVisible=lambda handle: False)
    assert backend.wait_result(123, 12) == race_studio.ImportResult(9, 3, 0)
    assert requests == ["exit"]


def test_existing_completed_import_is_cleared_before_next_batch():
    backend = object.__new__(race_studio._RaceStudioWindows)
    enabled = [False]
    backend.u = SimpleNamespace(IsWindowEnabled=lambda handle: (
        enabled[0] if handle == "main" else True))
    backend.pid = lambda main: 123
    backend.import_dialogs = lambda pid: ["progress", "completed"]
    backend.result_lists = lambda dialog: list(range(4)) if dialog == "completed" else []
    backend.message = lambda handle, message: 0
    backend.control = lambda *args: "exit"
    backend.click = lambda handle: enabled.__setitem__(0, True)
    backend.settle_existing_import("main")
    assert enabled[0]


def test_queue_waits_for_every_single_open_before_next_file(tmp_path, monkeypatch):
    files = [tmp_path / "one.xrk", tmp_path / "two.xrk"]
    events = []
    backend = object.__new__(race_studio._RaceStudioWindows)
    backend.main_windows = lambda: ["main"]
    backend.settle_existing_import = lambda main: events.append("settle")
    backend.wait_main = lambda: "main"

    def finished(main, allow_silent=False):
        assert not allow_silent
        events.append("finished")
        return race_studio.ImportResult(0, 1, 0)

    backend.wait_file = finished
    monkeypatch.setattr(race_studio.os, "startfile",
                        lambda path: events.append(Path(path).name))
    progress = []
    result = backend.import_files(files, progress.append)
    assert events == ["settle", "one.xrk", "finished",
                      "settle", "two.xrk", "finished"]
    assert progress == [50, 100]
    assert result == race_studio.ImportResult(0, 2, 0, 2)
