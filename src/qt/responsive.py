"""State-preserving layouts for narrow desktop and Windows snap layouts."""
from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtWidgets import QGridLayout, QLayout, QSizePolicy, QSpacerItem, QWidget


class FlowLayout(QLayout):
    """Wrap existing controls onto another line instead of clipping them."""

    def __init__(self, parent=None, spacing=6):
        super().__init__(parent)
        self._items = []
        self.setContentsMargins(0, 0, 0, 0)
        self.setSpacing(spacing)

    def addItem(self, item):
        self._items.append(item)

    def addWidget(self, widget, stretch=0, alignment=None):
        if stretch:
            widget.setSizePolicy(QSizePolicy.Expanding, widget.sizePolicy().verticalPolicy())
        super().addWidget(widget)

    def insertWidget(self, index, widget):
        self.addWidget(widget)
        self._items.insert(max(0, index), self._items.pop())
        self.invalidate()

    def addStretch(self, _stretch=1):
        self.addItem(QSpacerItem(0, 0, QSizePolicy.Expanding, QSizePolicy.Minimum))

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index):
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._arrange(QRect(0, 0, width, 0), False)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._arrange(rect, True)

    def minimumSize(self):
        size = QSize()
        for item in self._items:
            if not item.isEmpty():
                size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        return size + QSize(margins.left() + margins.right(), margins.top() + margins.bottom())

    def sizeHint(self):
        return self.minimumSize()

    def _arrange(self, rect, apply):
        margins = self.contentsMargins()
        area = rect.adjusted(margins.left(), margins.top(), -margins.right(), -margins.bottom())
        width = max(1, area.width())
        rows, row, used = [], [], 0
        for item in self._items:
            if item.widget() is not None and item.isEmpty():
                continue
            hint = item.sizeHint()
            item_width = min(width, max(0, hint.width()))
            if row and used + self.spacing() + item_width > width:
                rows.append(row)
                row, used = [], 0
            row.append((item, item_width, max(0, hint.height())))
            used += item_width + (self.spacing() if len(row) > 1 else 0)
        if row:
            rows.append(row)
        y = area.y()
        for row in rows:
            used = sum(entry[1] for entry in row) + self.spacing() * (len(row) - 1)
            expanding = [entry[0] for entry in row if entry[0].expandingDirections() & Qt.Horizontal]
            extra = max(0, width - used) // max(1, len(expanding))
            allocations = [(item, item_width + (extra if item in expanding else 0), hint_height)
                           for item, item_width, hint_height in row]
            height = max(max(hint_height, item.heightForWidth(allocated)) if item.hasHeightForWidth()
                         else hint_height for item, allocated, hint_height in allocations)
            x = area.x()
            for item, allocated, _height in allocations:
                if apply:
                    item.setGeometry(QRect(QPoint(x, y), QSize(allocated, height)))
                x += allocated + self.spacing()
            y += height + self.spacing()
        return max(0, y - rect.y() - (self.spacing() if rows else 0)) + margins.bottom()


class RunNavigationLayout(FlowLayout):
    """Keep run navigation grouped and centered when a single row won't fit."""

    def _arrange(self, rect, apply):
        if len(self._items) != 7:
            return super()._arrange(rect, apply)
        margins = self.contentsMargins()
        area = rect.adjusted(margins.left(), margins.top(), -margins.right(), -margins.bottom())
        width, gap = max(1, area.width()), self.spacing()
        hints = [item.sizeHint() for item in self._items]
        if sum(hint.width() for hint in hints) + gap * 6 <= width:
            return super()._arrange(rect, apply)

        # Previous and Next get equal-width edge columns, leaving the run title
        # centered over the whole bar rather than over leftover flow space.
        edge = max(hints[0].width(), hints[2].width())
        height = max(hint.height() for hint in hints[:3])
        widths = (edge, max(0, width - 2 * edge - 2 * gap), edge)
        x, y = area.x(), area.y()
        for item, allocated in zip(self._items[:3], widths):
            if apply:
                item.setGeometry(QRect(x, y, allocated, height))
            x += allocated + gap
        y += height + gap

        # Add / Remove / Notes / key share a balanced second row. At extreme
        # font scaling, use two pairs rather than overlapping or clipping them.
        actions = self._items[3:]
        minimum = max(item.minimumSize().width() for item in actions)
        columns = 4 if width >= minimum * 4 + gap * 3 else 2
        for start in range(0, len(actions), columns):
            row = actions[start:start + columns]
            height = max(item.sizeHint().height() for item in row)
            allocated = (width - gap * (columns - 1)) // columns
            for column, item in enumerate(row):
                if apply:
                    item.setGeometry(QRect(area.x() + column * (allocated + gap), y,
                                           allocated, height))
            y += height + gap
        return y - gap - rect.y() + margins.bottom()


class AdaptiveGrid(QWidget):
    """Reflow the same live input widgets into fewer columns on narrow screens."""

    def __init__(self, columns=4, cell_width=210, parent=None):
        super().__init__(parent)
        self.setObjectName("AdaptiveGrid")
        self.setStyleSheet("QWidget#AdaptiveGrid, QWidget#AdaptiveCell { background:transparent; }")
        self._maximum_columns = columns
        self._cell_width = cell_width
        self._columns = 0
        self._cells = []
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setSpacing(8)
        self._grid.setSizeConstraint(QLayout.SetNoConstraint)

    def addWidget(self, widget, _stretch=0):
        self._cells.append(widget)
        self._reflow(force=True)

    def add_cell(self, layout):
        cell = QWidget()
        cell.setObjectName("AdaptiveCell")
        layout.setContentsMargins(0, 0, 0, 0)
        cell.setLayout(layout)
        self.addWidget(cell)

    def _reflow(self, force=False):
        columns = max(1, min(self._maximum_columns, self.width() // self._cell_width))
        if not force and columns == self._columns:
            return
        while self._grid.count():
            self._grid.takeAt(0)
        for column in range(self._maximum_columns):
            self._grid.setColumnStretch(column, 1 if column < columns else 0)
        for index, widget in enumerate(self._cells):
            self._grid.addWidget(widget, index // columns, index % columns)
        self._columns = columns
        self.updateGeometry()

    def resizeEvent(self, event):
        self._reflow()
        super().resizeEvent(event)

    def minimumSizeHint(self):
        return QSize(0, self._grid.minimumSize().height())
