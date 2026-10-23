"""Native drag controls for the setup layout editor."""
from PySide6.QtCore import Qt, QMimeData
from PySide6.QtGui import QDrag
from PySide6.QtWidgets import QApplication, QPushButton


class ParameterDragButton(QPushButton):
    MIME = "application/x-xcal-parameter"

    def __init__(self, text, key, scope, on_move, parent=None):
        super().__init__(text, parent)
        self.field_key = key
        self.scope = scope
        self.on_move = on_move
        self._press_position = None
        self._dragging = False
        self._drop_style = None
        self._pending_move = None
        self.setAcceptDrops(True)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._press_position = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if (event.buttons() & Qt.LeftButton and self._press_position is not None
                and (event.position().toPoint() - self._press_position).manhattanLength()
                >= QApplication.startDragDistance()):
            self._dragging = True
            self._pending_move = None
            self._press_position = None
            self.setDown(False)
            drag = QDrag(self)
            mime = QMimeData()
            mime.setData(self.MIME, self.field_key.encode("utf-8"))
            drag.setMimeData(mime)
            drag.setPixmap(self.grab())
            drag.setHotSpot(event.position().toPoint())
            drag.exec(Qt.MoveAction)
            self._dragging = False
            if self._pending_move is not None:
                move = self._pending_move
                self._pending_move = None
                self.on_move(*move)
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._press_position = None
        if not self._dragging:
            super().mouseReleaseEvent(event)

    def _valid(self, event):
        source = event.source()
        return (isinstance(source, ParameterDragButton) and source is not self
                and source.scope == self.scope and event.mimeData().hasFormat(self.MIME))

    def _show_drop(self, event):
        if self._drop_style is None:
            self._drop_style = self.styleSheet()
        after = event.position().x() >= self.width() / 2
        self.setStyleSheet(self._drop_style + (
            "border-right: 4px solid #f3cf65;" if after else "border-left: 4px solid #f3cf65;"))
        event.setDropAction(Qt.MoveAction)
        event.accept()

    def _clear_drop(self):
        if self._drop_style is not None:
            self.setStyleSheet(self._drop_style)
            self._drop_style = None

    def dragEnterEvent(self, event):
        if self._valid(event):
            self._show_drop(event)
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if self._valid(event):
            self._show_drop(event)
        else:
            event.ignore()

    def dragLeaveEvent(self, event):
        self._clear_drop()
        event.accept()

    def dropEvent(self, event):
        self._clear_drop()
        if self._valid(event):
            source = event.source()
            source._pending_move = (source.field_key, self.field_key, event.position().x() >= self.width() / 2)
            event.setDropAction(Qt.MoveAction)
            event.accept()
        else:
            event.ignore()
