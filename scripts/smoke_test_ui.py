"""Open the full community UI with isolated data and no network; exit cleanly."""
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))


def main():
    with TemporaryDirectory(prefix='xcal-community-smoke-') as temporary:
        os.environ['XCAL_DATA_DIR'] = temporary
        os.environ['GOOGLE_DRIVE_FOLDER_ID'] = ''
        os.environ['QT_QPA_PLATFORM'] = 'offscreen'
        from PySide6.QtCore import QTimer, QThread
        from PySide6.QtWidgets import QApplication
        from qt.main_window import MainWindow
        from utils.updater import UpdateInfo
        import config
        app = QApplication([])
        state = {'passed': False}
        with patch('utils.google_drive_client.check_connection', return_value=False), patch(
                'utils.updater.check_for_update', return_value=UpdateInfo(current_version=config.APP_VERSION)):
            window = MainWindow()
            window.show()

            def finish():
                state['passed'] = (config.GOOGLE_DRIVE_FOLDER_ID == ''
                                   and not (config.USER_DATA_DIR / 'google_token.json').exists()
                                   and window._stack.currentWidget() is window._settings
                                   and window._workspace.detail._tabs.tabText(0) == 'Setup/Notes')
                window.close()
                if any(thread.isRunning() for thread in window.findChildren(QThread)):
                    QTimer.singleShot(100, finish)
                else:
                    print('Community UI smoke test ' + ('passed' if state['passed'] else 'failed') + ': isolated data, no credentials, Settings onboarding, clean shutdown.')
                    app.quit()

            QTimer.singleShot(2000, finish)
            QTimer.singleShot(20000, app.quit)
            app.exec()
            return 0 if state['passed'] else 1


if __name__ == '__main__':
    sys.exit(main())
