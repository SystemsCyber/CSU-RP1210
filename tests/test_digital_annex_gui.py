"""Drives the DigitalAnnexSelect dialog offscreen: create, validate, edit and run vectors."""

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PyQt5.QtWidgets")

import j1939db_tools as tools  # noqa: E402
from DigitalAnnexSelect import DigitalAnnexDialog, VECTOR_COLUMNS  # noqa: E402

RESULT_COL = len(VECTOR_COLUMNS) - 1


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def wait_for(app, condition, timeout=60):
    end = time.time() + timeout
    while not condition():
        app.processEvents()
        if time.time() > end:
            raise TimeoutError("condition not met")
        time.sleep(0.02)


def test_create_validate_and_vectors(app, synthetic_workbook, tmp_path, monkeypatch):
    pytest.importorskip("pretty_j1939")
    monkeypatch.delenv("CSU_UNITS", raising=False)
    dialog = DigitalAnnexDialog(storage_dir=str(tmp_path))
    created = []
    dialog.database_created.connect(created.append)

    dialog.da_list.addItem(synthetic_workbook)
    dialog.out_dir_edit.setText(str(tmp_path))
    dialog.pref_us.setChecked(True)
    dialog.create_databases()
    wait_for(app, lambda: created or dialog.create_button.isEnabled())

    assert created, dialog.log_view.toPlainText()
    for name in tools.OUTPUT_NAMES.values():
        assert (tmp_path / name).exists()
    assert tools.read_unit_preference(str(tmp_path)) == tools.US

    # After creation the dialog validates the preferred (US) file against the metric one.
    assert dialog.tabs.currentIndex() == 1
    assert dialog.db_combo.currentText().endswith(tools.OUTPUT_NAMES[tools.US])
    levels = {dialog.results.topLevelItem(i).text(0): dialog.results.topLevelItem(i).text(1)
              for i in range(dialog.results.topLevelItemCount())}
    assert "FAIL" not in levels.values(), levels
    assert levels["Unit conversion"] == "PASS"
    assert levels["US scaling vs SLOT conversion"] == "PASS", "SLOT cross-check runs with the Create-tab workbook"
    assert "0 failure(s)" in dialog.summary_label.text()

    # Vector results were filled in; synthetic vectors pass.
    results = [dialog.vector_table.item(r, RESULT_COL).text() for r in range(dialog.vector_table.rowCount())]
    assert sum(r.startswith("PASS") for r in results) >= 6
    assert not any(r.startswith("FAIL") for r in results), results


def test_vector_editor_detects_wrong_expectation(app, generated, tmp_path):
    dialog = DigitalAnnexDialog(storage_dir=str(tmp_path))
    dialog.set_combo_path(dialog.db_combo, generated[tools.METRIC])
    dialog.vector_table.setRowCount(0)
    dialog.add_vector_row({"name": "edited", "pgn": "0xFF00", "spn": 520193, "data": "40 1F 3C 82 00 64 00 FF",
                           "expected": {"metric": 21.0, "us": 69.8}, "tolerance": 0.01})
    dialog.run_vectors_clicked()
    assert dialog.vector_table.item(0, RESULT_COL).text().startswith("FAIL")

    dialog.vector_table.item(0, 4).setText("20")
    dialog.run_vectors_clicked()
    assert dialog.vector_table.item(0, RESULT_COL).text().startswith("PASS")

    path = str(tmp_path / "my_vectors.json")
    dialog.vectors_path = path
    dialog.save_vectors()
    saved = tools.load_vectors(path)
    assert saved[0]["pgn"] == 0xFF00 and saved[0]["expected"]["metric"] == 20.0
    dialog.load_vectors(path)
    assert dialog.vector_table.rowCount() == 1


def test_validate_tab_reports_skeleton(app, tmp_path):
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    dialog = DigitalAnnexDialog(storage_dir=str(tmp_path))
    dialog.set_combo_path(dialog.db_combo, os.path.join(root, "J1939db.json"))
    dialog.set_combo_path(dialog.baseline_combo, "")
    checks = dialog.run_validation()
    content = [c for c in checks if c.name == "Content"][0]
    assert content.level == tools.WARN and "Skeleton" in content.message
