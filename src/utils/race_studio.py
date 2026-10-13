"""Queue XRKs through the Windows association used by the individual button."""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ImportResult:
    imported: int
    skipped: int
    errors: int
    opened: int = 0


class _RaceStudioWindows:
    """Track completion of individual Windows opens in Race Studio."""

    def __init__(self):
        import ctypes
        from ctypes import wintypes
        self.c = ctypes
        self.w = wintypes
        self.u = ctypes.WinDLL("user32", use_last_error=True)
        self.callback_type = ctypes.WINFUNCTYPE(
            wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        self.u.EnumWindows.argtypes = [self.callback_type, wintypes.LPARAM]
        self.u.EnumChildWindows.argtypes = [
            wintypes.HWND, self.callback_type, wintypes.LPARAM]
        self.u.GetDlgCtrlID.argtypes = [wintypes.HWND]
        self.u.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        self.u.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        self.u.IsWindowVisible.argtypes = [wintypes.HWND]
        self.u.IsWindowEnabled.argtypes = [wintypes.HWND]
        self.u.GetWindowThreadProcessId.argtypes = [
            wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        self.u.PostMessageW.argtypes = [
            wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        self.u.SendMessageTimeoutW.argtypes = [
            wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
            wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t)]

    def text(self, handle, class_name=False):
        buffer = self.c.create_unicode_buffer(512)
        function = self.u.GetClassNameW if class_name else self.u.GetWindowTextW
        function(handle, buffer, len(buffer))
        return buffer.value

    def windows(self, parent=None):
        handles = []

        def visit(handle, _parameter):
            handles.append(handle)
            return True

        callback = self.callback_type(visit)
        if parent:
            self.u.EnumChildWindows(parent, callback, 0)
        else:
            self.u.EnumWindows(callback, 0)
        return handles

    def pid(self, handle):
        pid = self.w.DWORD()
        self.u.GetWindowThreadProcessId(handle, self.c.byref(pid))
        return pid.value

    def control(self, parent, identifier, caption=None, class_name=None):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            for handle in self.windows(parent):
                if identifier is not None and self.u.GetDlgCtrlID(handle) != identifier:
                    continue
                if caption is not None and self.text(handle) != caption:
                    continue
                if class_name and self.text(handle, True) != class_name:
                    continue
                return handle
            time.sleep(0.1)
        raise RuntimeError("Race Studio's import controls differ from the tested version.")

    def message(self, handle, message, parameter=0, value=0):
        result = self.c.c_size_t()
        if not self.u.SendMessageTimeoutW(
                handle, message, parameter, value, 2, 3000,
                self.c.byref(result)):
            raise RuntimeError("Race Studio is not responding.")
        return result.value

    def click(self, handle):
        if not self.u.IsWindowEnabled(handle):
            raise RuntimeError("Race Studio's import action is currently unavailable.")
        if not self.u.PostMessageW(handle, 0xF5, 0, 0):  # BM_CLICK
            raise RuntimeError("Could not activate Race Studio's import action.")

    def wait_dialog(self, pid, title, timeout=20):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            from qt.workers import check_interruption
            check_interruption()
            for handle in self.windows():
                if (self.pid(handle) == pid and self.u.IsWindowVisible(handle)
                        and self.text(handle) == title
                        and self.text(handle, True) == "#32770"):
                    return handle
            time.sleep(0.1)
        raise RuntimeError(f"Race Studio did not open its {title} dialog.")

    def main_windows(self):
        return [handle for handle in self.windows()
                if self.u.IsWindowVisible(handle)
                and self.text(handle).startswith("RaceStudio3 (64 bit) ")
                and self.text(handle, True) == "#32770"]

    def wait_main(self):
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            from qt.workers import check_interruption
            check_interruption()
            candidates = self.main_windows()
            if len(candidates) == 1:
                return candidates[0]
            if len(candidates) > 1:
                raise RuntimeError("More than one Race Studio instance is open.")
            time.sleep(0.2)
        raise RuntimeError("Windows could not launch Race Studio for XRK files.")

    def result_lists(self, dialog):
        return [handle for handle in self.windows(dialog)
                if self.text(handle, True) == "ListBox"]

    def import_dialogs(self, pid):
        return [handle for handle in self.windows()
                if self.pid(handle) == pid and self.u.IsWindowVisible(handle)
                and self.text(handle) == "Import Data"]

    def settle_existing_import(self, main):
        """Dismiss completed import summaries and wait for active imports."""
        deadline = time.monotonic() + 120
        while not self.u.IsWindowEnabled(main):
            from qt.workers import check_interruption
            check_interruption()
            dialogs = self.import_dialogs(self.pid(main))
            if not dialogs:
                raise RuntimeError("Race Studio has another dialog open. Finish it before importing.")
            for dialog in dialogs:
                lists = self.result_lists(dialog)
                if len(lists) != 4:
                    continue
                if self.message(lists[3], 0x18B):
                    from utils.debug import log
                    log.warning("Dismissing previous Race Studio import errors before new batch")
                exit_button = self.control(dialog, None, "Exit", "Button")
                if self.u.IsWindowEnabled(exit_button):
                    self.click(exit_button)
            if time.monotonic() >= deadline:
                raise RuntimeError("Race Studio's previous import is still running.")
            time.sleep(0.2)

    def wait_result(self, pid, expected):
        # Race Studio's three results lists: skipped, imported, errors. Read
        # counts rather than owner-drawn text, which contains process pointers.
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            from qt.workers import check_interruption
            check_interruption()
            for result_dialog in self.import_dialogs(pid):
                lists = self.result_lists(result_dialog)
                if len(lists) != 4:
                    continue
                exit_button = self.control(result_dialog, None, "Exit", "Button")
                if not self.u.IsWindowEnabled(exit_button):
                    continue
                counts = [self.message(lists[index], 0x18B)
                          for index in (2, 1, 3)]
                if sum(counts) >= expected:
                    result = ImportResult(*counts)
                    if not result.errors:
                        self.click(exit_button)
                        deadline_close = time.monotonic() + 5
                        while (self.u.IsWindowVisible(result_dialog)
                               and time.monotonic() < deadline_close):
                            time.sleep(0.1)
                    return result
            time.sleep(0.2)
        raise RuntimeError("Race Studio is still importing; check its progress window.")

    def wait_file(self, main, allow_silent=False):
        """Wait for this single-file import, or a silent startup, to settle."""
        pid = self.pid(main)
        deadline = time.monotonic() + 120
        idle_since = None
        report = ImportResult(0, 0, 0)
        while time.monotonic() < deadline:
            from qt.workers import check_interruption
            check_interruption()
            dialogs = self.import_dialogs(pid)
            for dialog in dialogs:
                lists = self.result_lists(dialog)
                if len(lists) != 4:
                    continue
                exit_buttons = [handle for handle in self.windows(dialog)
                                if self.text(handle, True) == "Button"
                                and self.text(handle) == "Exit"
                                and self.u.IsWindowEnabled(handle)]
                if not exit_buttons:
                    continue
                counts = [self.message(lists[index], 0x18B) for index in (2, 1, 3)]
                if not sum(counts):
                    continue
                report = ImportResult(report.imported + counts[0],
                                      report.skipped + counts[1],
                                      report.errors + counts[2])
                self.click(exit_buttons[0])
                # Await destruction so this result cannot be counted twice.
                close_deadline = time.monotonic() + 5
                while self.u.IsWindowVisible(dialog) and time.monotonic() < close_deadline:
                    time.sleep(0.1)
            if not self.import_dialogs(pid) and self.u.IsWindowEnabled(main):
                idle_since = idle_since or time.monotonic()
                acknowledged = report.imported + report.skipped + report.errors
                if (acknowledged or allow_silent) and time.monotonic() - idle_since >= 1.5:
                    return report
            else:
                idle_since = None
            time.sleep(0.1)
        raise RuntimeError("Race Studio is still importing. The remaining XRKs stay saved in xCAL.")

    def import_files(self, paths, progress=None):
        imported = skipped = errors = 0
        for index, path in enumerate(paths):
            from qt.workers import check_interruption
            check_interruption()
            existing = self.main_windows()
            if len(existing) > 1:
                raise RuntimeError("More than one Race Studio instance is open.")
            if existing:
                self.settle_existing_import(existing[0])
            # The exact Windows file association used by the individual button.
            # One file is handed off only after the preceding import settles.
            os.startfile(str(path))
            main = self.wait_main()
            report = self.wait_file(main, allow_silent=not existing)
            imported += report.imported
            skipped += report.skipped
            errors += report.errors
            if progress:
                progress(round((index + 1) / len(paths) * 100))
        return ImportResult(imported, skipped, errors, len(paths))


def import_xrk_files(paths, progress=None):
    """Open each XRK through Windows, waiting between imports."""
    if os.name != "nt":
        raise RuntimeError("Race Studio automatic import requires Windows.")
    files = list(dict.fromkeys(Path(path).resolve() for path in paths))
    if not files:
        raise ValueError("There are no XRK files to import.")
    missing = [path for path in files if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"XRK is missing: {missing[0]}")

    # Serialize requests across local/installed xCAL instances too. Two queues
    # delivering file opens simultaneously can create nested import dialogs.
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.CreateMutexW.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.ReleaseMutex.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    mutex = kernel.CreateMutexW(None, False, r"Local\xCAL_RaceStudio_Imports")
    if not mutex:
        raise RuntimeError("Could not create the Race Studio import queue.")
    acquired = False
    try:
        from qt.workers import check_interruption
        while not acquired:
            check_interruption()
            status = kernel.WaitForSingleObject(mutex, 100)
            if status in (0, 0x80):
                acquired = True
            elif status != 0x102:
                raise RuntimeError("Could not access the Race Studio import queue.")
        backend = _RaceStudioWindows()
        if progress is None:
            return backend.import_files(files)
        return backend.import_files(files, progress)
    finally:
        if acquired:
            kernel.ReleaseMutex(mutex)
        kernel.CloseHandle(mutex)
