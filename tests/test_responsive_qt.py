from copy import deepcopy

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QLineEdit, QPushButton, QWidget

from qt.responsive import AdaptiveGrid, FlowLayout, RunNavigationLayout
from qt.sidebar import Sidebar, WIDTH_COLLAPSED, WIDTH_EXPANDED
from qt.views.setup_builder_qt import SetupBuilderView


@pytest.fixture
def app():
    application = QApplication.instance() or QApplication([])
    yield application
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    application.processEvents()


def test_sidebar_auto_collapse_restores_manual_preference(app):
    sidebar = Sidebar()
    sidebar.fit_space(960, 820)
    assert sidebar.width() == WIDTH_COLLAPSED
    sidebar.toggle()
    assert sidebar.width() == WIDTH_COLLAPSED
    sidebar.fit_space(1280, 820)
    assert sidebar.width() == WIDTH_EXPANDED
    sidebar.toggle()
    sidebar.fit_space(960, 820)
    sidebar.fit_space(1280, 820)
    assert sidebar.width() == WIDTH_COLLAPSED
    sidebar.deleteLater()


def test_flow_wraps_controls_without_overlap(app):
    host = QWidget()
    layout = FlowLayout(host)
    buttons = [QPushButton("Action " + str(index)) for index in range(6)]
    for button in buttons:
        layout.addWidget(button)
    host.resize(260, 250)
    host.show()
    app.processEvents()
    assert len({button.y() for button in buttons}) > 1
    for index, button in enumerate(buttons):
        assert button.geometry().right() < host.width()
        for other in buttons[index + 1:]:
            assert not button.geometry().intersects(other.geometry())
    host.close()
    host.deleteLater()


def test_grid_reflows_same_editors_without_losing_values(app):
    grid = AdaptiveGrid(4, 200)
    fields = [QLineEdit(str(index)) for index in range(8)]
    for field in fields:
        grid.addWidget(field)
    grid.resize(900, 300)
    grid.show()
    app.processEvents()
    assert grid._columns == 4
    fields[0].setFocus()
    grid.resize(450, 300)
    app.processEvents()
    assert grid._columns == 2
    assert [field.text() for field in fields] == [str(index) for index in range(8)]
    assert fields[0].hasFocus()
    assert all(field.width() <= 225 for field in fields)
    grid.close()
    grid.deleteLater()


def test_compact_run_bar_groups_navigation_and_balances_actions(app):
    from PySide6.QtCore import QSize

    class Control(QPushButton):
        def sizeHint(self):
            return QSize(100, 32)

        def minimumSizeHint(self):
            return QSize(70, 32)

    host = QWidget()
    layout = RunNavigationLayout(host)
    controls = [Control(str(index)) for index in range(7)]
    for index, control in enumerate(controls):
        layout.addWidget(control, 1 if index == 1 else 0)
    host.resize(560, 100)
    host.show()
    app.processEvents()
    previous, position, next_button = controls[:3]
    assert previous.y() == position.y() == next_button.y()
    assert abs(position.geometry().center().x() - host.rect().center().x()) <= 1
    assert len({control.y() for control in controls[3:]}) == 1
    assert controls[3].y() > previous.y()
    assert max(control.width() for control in controls[3:]) - min(control.width() for control in controls[3:]) <= 1
    host.resize(1000, 100)
    app.processEvents()
    assert len({control.y() for control in controls}) == 1
    host.close()
    host.deleteLater()


def test_split_screen_builder_retains_run_state_and_accessible_controls(app, monkeypatch):
    monkeypatch.setattr(SetupBuilderView, "refresh_maintenance", lambda self: None)
    builder = SetupBuilderView()
    builder._content_stack.setCurrentWidget(builder._scroll)
    builder._add_run()
    builder._feedback.setPlainText("Unsaved run notes")
    expected = deepcopy(builder._serialized_runs())
    builder.resize(580, 700)
    builder.show()
    app.processEvents()
    assert builder.width() <= 580
    assert builder._scroll.horizontalScrollBar().maximum() == 0
    assert builder._serialized_runs() == expected
    assert all(grid._columns <= 2 for grid in builder.findChildren(AdaptiveGrid))
    toolbar_controls = [builder._previous_run_btn, builder._next_run_btn, builder._add_run_btn,
                        builder._remove_run_btn, builder._run_log_btn, builder._run_comparison_key]
    assert all(widget.geometry().right() < builder._run_toolbar.width() for widget in toolbar_controls)
    builder.resize(1280, 850)
    app.processEvents()
    assert builder._serialized_runs() == expected
    builder.close()
    builder.deleteLater()


def test_kart_panels_stack_without_overlap_when_narrow(app, monkeypatch):
    from qt.views.setup_builder_qt import _KartOverlay
    monkeypatch.setattr(SetupBuilderView, "refresh_maintenance", lambda self: None)
    builder = SetupBuilderView()
    builder._content_stack.setCurrentWidget(builder._scroll)
    builder.resize(580, 750)
    builder.show()
    app.processEvents()
    overlay = builder.findChild(_KartOverlay)
    cards = list(overlay._cards.values())
    assert overlay._image.isHidden()
    for index, card in enumerate(cards):
        assert card.geometry().right() < overlay.width()
        for other in cards[index + 1:]:
            assert not card.geometry().intersects(other.geometry())
    builder.resize(1280, 850)
    app.processEvents()
    assert not overlay._image.isHidden()
    for index, card in enumerate(cards):
        for other in cards[index + 1:]:
            assert not card.geometry().intersects(other.geometry())
    builder.close()
    builder.deleteLater()


def test_compare_resize_keeps_both_selected_setups(app):
    from qt.views.compare_qt import CompareView
    from database.models import Setup
    page = CompareView()
    page._entries = [("A setup", Setup(id="a", name="A")), ("B setup", Setup(id="b", name="B"))]
    page._build_slots()
    page._a.select_setup_id("a")
    page._b.select_setup_id("b")
    page.resize(570, 700)
    page.show()
    app.processEvents()
    assert page._selection_guide.isHidden()
    assert not page._a.frame.geometry().intersects(page._b.frame.geometry())
    assert (page._a.setup.id, page._b.setup.id) == ("a", "b")
    page.resize(1200, 800)
    app.processEvents()
    assert not page._selection_guide.isHidden()
    assert (page._a.setup.id, page._b.setup.id) == ("a", "b")
    page.close()
    page.deleteLater()


def test_main_window_accepts_windows_half_screen_width(app, monkeypatch):
    from qt.main_window import MainWindow
    from qt.views.settings_qt import SettingsView
    monkeypatch.setattr(SettingsView, "on_show", lambda self: None)
    monkeypatch.setattr("utils.team_setup_fields.load", lambda: {"schema": 1, "fields": []})
    monkeypatch.setattr("database.client.fetch_app_doc", lambda _key: None)
    monkeypatch.setattr(SetupBuilderView, "refresh_maintenance", lambda self: None)
    for name in ("_connect", "_refresh_offline_catalogs", "_load_team_naming", "_check_for_updates_on_startup", "_navigate"):
        monkeypatch.setattr(MainWindow, name, lambda *_args, **_kwargs: None)
    window = MainWindow()
    window.resize(680, 700)
    window.show()
    app.processEvents()
    assert window.width() == 680
    assert window._sidebar.width() == WIDTH_COLLAPSED
    window._stack.setCurrentWidget(window._setup_builder)
    window._setup_builder._content_stack.setCurrentWidget(window._setup_builder._scroll)
    app.processEvents()
    assert window._setup_builder.width() <= window._stack.width()
    assert window._setup_builder._scroll.horizontalScrollBar().maximum() == 0
    window._stack.setCurrentWidget(window._workspace)
    app.processEvents()
    assert window._workspace._session_rail.isHidden()
    window.resize(1500, 850)
    app.processEvents()
    assert window._sidebar.width() == WIDTH_EXPANDED
    assert not window._workspace._session_rail.isHidden()
    window._connection_timer.stop()
    window._stack.setCurrentWidget(window._sessions)
    window.close()
    window.deleteLater()
