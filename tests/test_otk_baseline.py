from unittest.mock import Mock

from PySide6.QtWidgets import QApplication, QLabel, QMessageBox

import otk_baseline
from qt.views.parts_qt import PartsView, _BaselineCard
from qt.views.setup_builder_qt import SetupBuilderView


def _app():
    return QApplication.instance() or QApplication([])


def test_otk_dry_template_maps_only_documented_values():
    draft = otk_baseline.template()
    assert draft.id == "builtin:otk-tony-kart-dry-v1"
    assert draft.is_baseline
    assert draft.data["_universal_baseline"] is True
    assert draft.data["upper_castor_camber"] == "FWD (Arrow forward)"
    assert draft.data["lower_castor_camber"] == "FWD (Arrow forward)"
    assert draft.data["front_left_camber"] == "0 ticks"
    assert draft.data["front_right_camber"] == "0 ticks"
    assert draft.data["toe"] == "half box toe-out (1mm)"
    assert draft.data["torsion_bar"] == "Flat OTK"
    assert draft.data["front_ride_height"] == "MID (middle height)"
    assert draft.data["ackerman"] == "BOTTOM HOLE"
    assert draft.data["axle"] == "N 1030 mm"
    assert draft.data["cage_spring_preload"] == "1-2"
    assert draft.data["front_track_spacers"] == "1 large spacer (15mm)"
    assert draft.data["rear_track"] == "54 3/4 - 55 1/8"
    assert draft.data["rim_type"] == "OTK MXJ"
    assert draft.data["rear_rim_type"] == "OTK MXJ"
    assert draft.data["clutch_slip"] == "9500-10400"
    assert draft.data["carburetor"] == "Pop (8-12 psi) Fulc (.045-.055 in)"
    assert draft.data["needle_settings"] == "L 2 1/4 - 2 3/4 H 1/8 - 1/2"
    assert draft.data["flex_length"] == "7 3/4- 8 1/2"
    assert draft.data["exhaust_pipe"] == "L2"
    assert draft.data["oil_mix"] == "8 oz per gallon"
    assert draft.data["fuel"] == "110 Race Fuel"
    assert not any(key.endswith("_psi") for key in draft.data)
    assert "Seat position depends on driver height" in draft.notes


def test_each_team_gets_draft_until_its_copy_exists():
    rows = otk_baseline.add_when_team_has_no_copy([])
    assert len(rows) == 1
    assert otk_baseline.is_builtin(rows[0])
    owned = otk_baseline.editable_copy(rows[0])
    owned.id = "team-baseline"
    assert otk_baseline.add_when_team_has_no_copy([owned]) == [owned]


def test_new_preload_default_preserves_existing_team_values():
    import setup_fields as sf
    rear = [field.key for field in sf.REAR_FIELDS]
    assert rear.index("cage_spring_preload") == rear.index("cage") + 1
    owned = otk_baseline.editable_copy()
    owned.id = "team-baseline"
    del owned.data["cage_spring_preload"]
    presented = otk_baseline.add_when_team_has_no_copy([owned])[0]
    assert presented.data["cage_spring_preload"] == "1-2"
    assert "cage_spring_preload" not in owned.data
    for value in ("", "3"):
        owned.data["cage_spring_preload"] = value
        assert otk_baseline.add_when_team_has_no_copy([owned])[0].data["cage_spring_preload"] == value


def test_parts_opens_builtin_as_editable_team_copy():
    _app()
    parts = PartsView()
    built_in = otk_baseline.template()
    parts._items = [built_in]
    parts._render()
    card = parts._parts_list.itemWidget(parts._parts_list.item(0))
    labels = [label.text() for label in card.findChildren(QLabel)]
    assert isinstance(card, _BaselineCard)
    assert "Guide Draft" in labels
    opened = []
    parts.open_baseline.connect(opened.append)
    parts._open_metadata()
    assert len(opened) == 1
    assert opened[0].id is None
    assert opened[0].data["_template_id"] == otk_baseline.TEMPLATE_ID
    assert built_in.id == otk_baseline.BUILTIN_ID
    parts.deleteLater()


def test_otk_draft_is_visible_and_editable_in_setup_builder(monkeypatch):
    app = _app()
    monkeypatch.setattr(
        SetupBuilderView, "refresh_maintenance", lambda self, **kwargs: None)
    builder = SetupBuilderView()
    draft = otk_baseline.editable_copy()
    builder._setup = draft
    builder._is_baseline = True
    builder._populate(draft)
    builder._universal_baseline = True
    builder._set_identity_controls_enabled(True)
    assert builder._field_text(builder._fields["toe"]) == "half box toe-out (1mm)"
    assert builder._field_text(builder._fields["rear_track"]) == "54 3/4 - 55 1/8"
    assert not builder._driver.isEnabled()
    assert not builder._chassis_cb.isEnabled()
    assert not builder._track.isEnabled()
    builder._set_field_text(builder._fields["toe"], "2 mm toe-out")
    assert builder._field_text(builder._fields["toe"]) == "2 mm toe-out"
    assert "no rear torsion bar" in builder._notes.toPlainText()
    builder.deleteLater()
    app.processEvents()


def test_universal_otk_draft_saves_without_identity_scope(monkeypatch):
    app = _app()
    monkeypatch.setattr(
        SetupBuilderView, "refresh_maintenance", lambda self, **kwargs: None)
    monkeypatch.setattr(
        "qt.views.setup_builder_qt.run_async",
        lambda parent, work, on_done=None, on_error=None:
        on_done(work()) if on_done else work())
    monkeypatch.setattr(
        "qt.views.setup_builder_qt.db.record_setup_change",
        lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "qt.views.setup_builder_qt.db.setup_identity_pending",
        lambda setup: False)
    monkeypatch.setattr(
        "qt.views.setup_builder_qt.db.fetch_conflicting_baseline",
        lambda *args, **kwargs: None)
    saved = []
    monkeypatch.setattr(
        "qt.views.setup_builder_qt.db.create_setup",
        lambda setup: saved.append(setup) or setup)
    information = Mock()
    monkeypatch.setattr(QMessageBox, "information", information)
    builder = SetupBuilderView()
    draft = otk_baseline.editable_copy()
    builder._setup = draft
    builder._is_baseline = True
    builder._universal_baseline = True
    builder._populate(draft)
    builder._save()
    assert len(saved) == 1
    assert saved[0].driver_id is None
    assert saved[0].chassis_id is None
    assert saved[0].track is None
    assert saved[0].data["_universal_baseline"] is True
    information.assert_not_called()
    builder.deleteLater()
    app.processEvents()
