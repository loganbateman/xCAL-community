"""xCAL — PySide6 entry point.

Run: python src/main.py
"""
from __future__ import annotations
import os
import sys
from contextlib import ExitStack

# Packaged smoke tests never touch a configured team's settings or credentials.
_smoke_directory = None
if "--smoke-test" in sys.argv:
    from tempfile import TemporaryDirectory
    _smoke_directory = TemporaryDirectory(prefix="xcal-community-packaged-smoke-")
    os.environ["XCAL_DATA_DIR"] = _smoke_directory.name
    os.environ["GOOGLE_DRIVE_FOLDER_ID"] = ""
    os.environ["QT_QPA_PLATFORM"] = "offscreen"

sys.path.insert(0, os.path.dirname(__file__))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
from utils.debug import setup_logging  # noqa: E402
from qt.main_window import MainWindow  # noqa: E402
from qt.widgets import install_first_click_select_all  # noqa: E402


def main():
    setup_logging()
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationName("xCAL")
    app.setOrganizationName("xCAL Community")
    install_first_click_select_all(app)
    with ExitStack() as patches:
        if _smoke_directory is not None:
            from unittest.mock import patch
            from utils.updater import UpdateInfo
            import config
            patches.enter_context(patch("utils.google_drive_client.check_connection", return_value=False))
            patches.enter_context(patch("utils.updater.check_for_update", return_value=UpdateInfo(current_version=config.APP_VERSION)))
        win = MainWindow()
        win.show()
        if _smoke_directory is not None:
            from PySide6.QtCore import QTimer, QThread
            state = {"passed": False}
            def finish():
                state["passed"] = (win._stack.currentWidget() is win._settings
                                   and not config.GOOGLE_DRIVE_FOLDER_ID
                                   and not (config.USER_DATA_DIR / "google_token.json").exists())
                win.close()
                if any(thread.isRunning() for thread in win.findChildren(QThread)):
                    QTimer.singleShot(100, finish)
                else:
                    app.quit()
            QTimer.singleShot(2000, finish)
            QTimer.singleShot(20000, app.quit)
            app.exec()
            sys.exit(0 if state["passed"] else 1)
        sys.exit(app.exec())


if __name__ == "__main__":
    main()
