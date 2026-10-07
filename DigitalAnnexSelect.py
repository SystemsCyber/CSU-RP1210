"""
A Qt dialog to build the J1939 database from a licensed SAE J1939 Digital Annex.

Create tab:       choose the Digital Annex workbook(s) and write
                  J1939db.licensed.json (metric) and/or J1939db.us.licensed.json
                  (US customary), and set which units CSU-RP1210 uses.
Validate tab:     check any J1939db*.json version, optionally against a baseline
                  (another Digital Annex release, or the other unit system).
Test vectors tab: edit and run decode test vectors against a database.

Run standalone with:  python DigitalAnnexSelect.py
"""

from PyQt5.QtWidgets import (QApplication,
                             QDialog,
                             QDialogButtonBox,
                             QTabWidget,
                             QWidget,
                             QVBoxLayout,
                             QHBoxLayout,
                             QGridLayout,
                             QGroupBox,
                             QLabel,
                             QListWidget,
                             QPushButton,
                             QLineEdit,
                             QCheckBox,
                             QRadioButton,
                             QButtonGroup,
                             QComboBox,
                             QProgressBar,
                             QPlainTextEdit,
                             QTreeWidget,
                             QTreeWidgetItem,
                             QTableWidget,
                             QTableWidgetItem,
                             QHeaderView,
                             QFileDialog,
                             QMessageBox)
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtGui import QColor, QBrush
import glob
import os
import sys
import traceback
import logging

import j1939db_tools as tools

logger = logging.getLogger(__name__)

LEVEL_COLORS = {tools.PASS: "#087443", tools.WARN: "#b54708", tools.FAIL: "#c4320a",
                tools.INFO: "#5f6873", "SKIP": "#5f6873"}
VECTOR_COLUMNS = ["Name", "PGN", "SPN", "Data (hex)", "Metric value", "US value", "Tolerance", "Result"]


class GenerateWorker(QThread):
    """Runs the Digital Annex conversion off the GUI thread."""
    log = pyqtSignal(str)
    done = pyqtSignal(dict)
    failed = pyqtSignal(str)

    def __init__(self, da_paths, out_dir, systems):
        super(GenerateWorker, self).__init__()
        self.da_paths = da_paths
        self.out_dir = out_dir
        self.systems = systems

    def run(self):
        try:
            outputs = tools.generate(self.da_paths, self.out_dir, self.systems, log=self.log.emit)
            self.done.emit(outputs)
        except Exception as e:
            logger.debug(traceback.format_exc())
            self.failed.emit(str(e))


class DigitalAnnexDialog(QDialog):
    """Select a Digital Annex, create licensed databases, and check them."""
    database_created = pyqtSignal(dict)

    def __init__(self, parent=None, storage_dir=None):
        super(DigitalAnnexDialog, self).__init__(parent)
        self.storage_dir = storage_dir or os.getcwd()
        self.vectors_path = tools.VECTORS_FILE
        self.worker = None
        self.setWindowTitle("J1939 Digital Annex")
        self.setWindowModality(Qt.ApplicationModal)
        self.setMinimumWidth(760)

        self.tabs = QTabWidget()
        self.tabs.addTab(self.create_tab(), "Create")
        self.tabs.addTab(self.validate_tab(), "Validate")
        self.tabs.addTab(self.vectors_tab(), "Test vectors")

        buttons = QDialogButtonBox(QDialogButtonBox.Close, Qt.Horizontal, self)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout()
        layout.addWidget(self.tabs)
        layout.addWidget(buttons)
        self.setLayout(layout)
        self.refresh_database_lists()

    # ---- Create ------------------------------------------------------------

    def create_tab(self):
        tab = QWidget()
        notice = QLabel("Select your licensed SAE J1939 Digital Annex workbook (.xlsx or .xls). "
                        "The generated databases contain licensed content: they are git-ignored and "
                        "must not be shared or committed.")
        notice.setWordWrap(True)

        self.da_list = QListWidget()
        self.da_list.setMaximumHeight(90)
        add_button = QPushButton("Add Digital Annex...")
        add_button.clicked.connect(self.add_digital_annex)
        remove_button = QPushButton("Remove")
        remove_button.clicked.connect(lambda: [self.da_list.takeItem(self.da_list.row(i))
                                               for i in self.da_list.selectedItems()])
        da_buttons = QVBoxLayout()
        da_buttons.addWidget(add_button)
        da_buttons.addWidget(remove_button)
        da_buttons.addStretch()
        da_box = QGroupBox("Digital Annex files")
        da_layout = QHBoxLayout()
        da_layout.addWidget(self.da_list)
        da_layout.addLayout(da_buttons)
        da_box.setLayout(da_layout)

        self.out_dir_edit = QLineEdit(self.storage_dir)
        browse_out = QPushButton("Browse...")
        browse_out.clicked.connect(self.choose_output_dir)

        self.metric_check = QCheckBox("Metric (SI): {}".format(tools.OUTPUT_NAMES[tools.METRIC]))
        self.metric_check.setChecked(True)
        self.us_check = QCheckBox("US customary: {}".format(tools.OUTPUT_NAMES[tools.US]))
        self.us_check.setChecked(True)

        preference = tools.read_unit_preference(self.storage_dir)
        self.pref_metric = QRadioButton("Metric")
        self.pref_us = QRadioButton("US customary")
        (self.pref_us if preference == tools.US else self.pref_metric).setChecked(True)
        self.pref_group = QButtonGroup(self)
        self.pref_group.addButton(self.pref_metric)
        self.pref_group.addButton(self.pref_us)

        out_box = QGroupBox("Output")
        out_layout = QGridLayout()
        out_layout.addWidget(QLabel("Folder:"), 0, 0)
        out_layout.addWidget(self.out_dir_edit, 0, 1)
        out_layout.addWidget(browse_out, 0, 2)
        out_layout.addWidget(self.metric_check, 1, 0, 1, 3)
        out_layout.addWidget(self.us_check, 2, 0, 1, 3)
        out_layout.addWidget(QLabel("Units used by CSU-RP1210:"), 3, 0)
        pref_row = QHBoxLayout()
        pref_row.addWidget(self.pref_metric)
        pref_row.addWidget(self.pref_us)
        pref_row.addStretch()
        out_layout.addLayout(pref_row, 3, 1, 1, 2)
        out_box.setLayout(out_layout)

        self.create_button = QPushButton("Create databases")
        self.create_button.clicked.connect(self.create_databases)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setTextVisible(False)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMinimumHeight(120)

        run_row = QHBoxLayout()
        run_row.addWidget(self.create_button)
        run_row.addWidget(self.progress)

        layout = QVBoxLayout()
        layout.addWidget(notice)
        layout.addWidget(da_box)
        layout.addWidget(out_box)
        layout.addLayout(run_row)
        layout.addWidget(self.log_view)
        tab.setLayout(layout)
        return tab

    def add_digital_annex(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Select J1939 Digital Annex", self.storage_dir,
                                                "Digital Annex (*.xlsx *.xls);;All Files (*.*)")
        for f in files:
            if not self.da_list.findItems(f, Qt.MatchExactly):
                self.da_list.addItem(f)

    def choose_output_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Output folder", self.out_dir_edit.text())
        if d:
            self.out_dir_edit.setText(d)

    def selected_systems(self):
        systems = []
        if self.metric_check.isChecked():
            systems.append(tools.METRIC)
        if self.us_check.isChecked():
            systems.append(tools.US)
        return systems

    def unit_preference(self):
        return tools.US if self.pref_us.isChecked() else tools.METRIC

    def append_log(self, text):
        self.log_view.appendPlainText(text)

    def create_databases(self):
        da_paths = [self.da_list.item(i).text() for i in range(self.da_list.count())]
        systems = self.selected_systems()
        if not da_paths:
            QMessageBox.warning(self, "No Digital Annex", "Add at least one Digital Annex workbook.")
            return
        if not systems:
            QMessageBox.warning(self, "No output", "Select metric, US customary, or both.")
            return
        self.log_view.clear()
        self.create_button.setEnabled(False)
        self.progress.setRange(0, 0)  # busy
        self.worker = GenerateWorker(da_paths, self.out_dir_edit.text(), systems)
        self.worker.log.connect(self.append_log)
        self.worker.done.connect(self.generation_done)
        self.worker.failed.connect(self.generation_failed)
        self.worker.start()

    def generation_done(self, outputs):
        self.progress.setRange(0, 1)
        self.progress.setValue(1)
        self.create_button.setEnabled(True)
        path = tools.write_unit_preference(self.unit_preference(), self.storage_dir)
        self.append_log("Units preference '{}' saved to {}".format(self.unit_preference(), path))
        self.refresh_database_lists()
        # Validate what was just written; with both systems, cross-check the conversion.
        primary = outputs.get(self.unit_preference()) or next(iter(outputs.values()))
        other = [p for p in outputs.values() if p != primary]
        self.set_combo_path(self.db_combo, primary)
        self.set_combo_path(self.baseline_combo, other[0] if other else "")
        self.run_validation()
        self.tabs.setCurrentIndex(1)
        self.database_created.emit(outputs)

    def generation_failed(self, message):
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.create_button.setEnabled(True)
        self.append_log("Failed: " + message)
        QMessageBox.critical(self, "Digital Annex conversion failed", message)

    # ---- Validate ------------------------------------------------------------

    def validate_tab(self):
        tab = QWidget()
        self.db_combo = QComboBox()
        self.db_combo.setEditable(True)
        self.baseline_combo = QComboBox()
        self.baseline_combo.setEditable(True)
        db_browse = QPushButton("Browse...")
        db_browse.clicked.connect(lambda: self.browse_json(self.db_combo))
        base_browse = QPushButton("Browse...")
        base_browse.clicked.connect(lambda: self.browse_json(self.baseline_combo))
        self.da_check_edit = QLineEdit()
        self.da_check_edit.setPlaceholderText("Digital Annex workbook for the SLOT cross-check (optional)")
        da_browse = QPushButton("Browse...")
        da_browse.clicked.connect(self.browse_da_for_check)
        run_button = QPushButton("Run checks")
        run_button.clicked.connect(self.run_validation)

        form = QGridLayout()
        form.addWidget(QLabel("Database:"), 0, 0)
        form.addWidget(self.db_combo, 0, 1)
        form.addWidget(db_browse, 0, 2)
        form.addWidget(QLabel("Compare with (optional):"), 1, 0)
        form.addWidget(self.baseline_combo, 1, 1)
        form.addWidget(base_browse, 1, 2)
        form.addWidget(QLabel("SLOT cross-check:"), 2, 0)
        form.addWidget(self.da_check_edit, 2, 1)
        form.addWidget(da_browse, 2, 2)
        form.setColumnStretch(1, 1)

        self.results = QTreeWidget()
        self.results.setHeaderLabels(["Check", "Result", "Details"])
        self.results.header().setSectionResizeMode(2, QHeaderView.Stretch)
        self.summary_label = QLabel("")

        layout = QVBoxLayout()
        layout.addLayout(form)
        layout.addWidget(run_button)
        layout.addWidget(self.results)
        layout.addWidget(self.summary_label)
        tab.setLayout(layout)
        return tab

    def browse_da_for_check(self):
        f, _ = QFileDialog.getOpenFileName(self, "Select J1939 Digital Annex", self.storage_dir,
                                           "Digital Annex (*.xlsx *.xls);;All Files (*.*)")
        if f:
            self.da_check_edit.setText(f)

    def default_digital_annex(self):
        listed = [self.da_list.item(i).text() for i in range(self.da_list.count())]
        if listed:
            return listed[0]
        found = sorted(glob.glob(os.path.join(self.storage_dir, "J1939DA*.xls*")))
        return found[-1] if found else ""

    def refresh_database_lists(self):
        found = sorted(set(glob.glob(os.path.join(self.storage_dir, "J1939db*.json"))
                           + glob.glob(os.path.join(self.out_dir_edit.text(), "J1939db*.json"))))
        for combo, blank in ((self.db_combo, False), (self.baseline_combo, True)):
            current = combo.currentText()
            combo.clear()
            if blank:
                combo.addItem("")
            combo.addItems(found)
            if current:
                self.set_combo_path(combo, current)
        if not self.db_combo.currentText():
            preferred = [p for p in tools.database_candidates(self.storage_dir, self.unit_preference())
                         if os.path.exists(p)]
            if preferred:
                self.set_combo_path(self.db_combo, preferred[0])
        if not self.da_check_edit.text():
            self.da_check_edit.setText(self.default_digital_annex())

    @staticmethod
    def set_combo_path(combo, path):
        i = combo.findText(path)
        if i < 0 and path:
            combo.addItem(path)
            i = combo.findText(path)
        combo.setCurrentIndex(max(i, 0))
        if not path:
            combo.setEditText("")

    def browse_json(self, combo):
        f, _ = QFileDialog.getOpenFileName(self, "Select J1939 database", self.storage_dir,
                                           "J1939 database (*.json);;All Files (*.*)")
        if f:
            self.set_combo_path(combo, f)

    def add_result(self, check):
        item = QTreeWidgetItem([check.name, check.level, check.message])
        color = QBrush(QColor(LEVEL_COLORS.get(check.level, "#000000")))
        item.setForeground(1, color)
        for d in check.details:
            QTreeWidgetItem(item, ["", "", str(d)])
        self.results.addTopLevelItem(item)
        return item

    def run_validation(self):
        path = self.db_combo.currentText().strip()
        self.results.clear()
        if not path or not os.path.exists(path):
            self.summary_label.setText("Select a database file.")
            return []
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            table = tools.UnitTable()
            db = tools.load(path)
            checks = tools.validate(db, table)
            baseline = self.baseline_combo.currentText().strip()
            baseline_db = None
            if baseline and os.path.exists(baseline) and os.path.abspath(baseline) != os.path.abspath(path):
                baseline_db = tools.load(baseline)
                checks += [tools.Check("-- compared with " + os.path.basename(baseline), tools.INFO, "")]
                checks += tools.compare(db, baseline_db, table)
            da = self.da_check_edit.text().strip()
            if da and os.path.exists(da):
                by_system = {tools.unit_system(db, table): db}
                if baseline_db is not None:
                    by_system.setdefault(tools.unit_system(baseline_db, table), baseline_db)
                metric_db = by_system.get(tools.METRIC)
                checks += [tools.Check("-- cross-checked with " + os.path.basename(da) + " SLOTs", tools.INFO, "")]
                if metric_db is None:
                    checks += [tools.Check("SLOT cross-check", tools.WARN,
                                           "Select the metric database (or use it as the comparison) to cross-check SLOTs")]
                else:
                    checks += tools.slot_crosscheck([da], metric_db, by_system.get(tools.US), table)
            for c in checks:
                self.add_result(c)
            vector_results = self.run_vectors(db, table)
            failures = sum(c.level == tools.FAIL for c in checks) + sum(r == "FAIL" for r in vector_results)
            warnings = sum(c.level == tools.WARN for c in checks)
            self.summary_label.setText("{}: {} failure(s), {} warning(s); test vectors {} pass / {} fail / {} skipped. "
                                       "Units: {}.".format(os.path.basename(path), failures, warnings,
                                                           vector_results.count("PASS"), vector_results.count("FAIL"),
                                                           vector_results.count("SKIP"),
                                                           tools.unit_system(db, table) or "unknown"))
            for i in range(3):
                self.results.resizeColumnToContents(i)
            return checks
        except Exception as e:
            logger.debug(traceback.format_exc())
            self.summary_label.setText("Could not read {}: {}".format(path, e))
            return []
        finally:
            QApplication.restoreOverrideCursor()

    # ---- Test vectors --------------------------------------------------------

    def vectors_tab(self):
        tab = QWidget()
        self.vectors_label = QLabel("")
        self.vector_table = QTableWidget(0, len(VECTOR_COLUMNS))
        self.vector_table.setHorizontalHeaderLabels(VECTOR_COLUMNS)
        header = self.vector_table.horizontalHeader()
        for col in range(len(VECTOR_COLUMNS) - 1):
            header.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(len(VECTOR_COLUMNS) - 1, QHeaderView.Stretch)

        buttons = QHBoxLayout()
        for text, slot in (("Add", self.add_vector_row), ("Remove", self.remove_vector_rows),
                           ("Load...", self.load_vectors_dialog), ("Save", self.save_vectors),
                           ("Save as...", self.save_vectors_as), ("Run against database", self.run_vectors_clicked)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            buttons.addWidget(b)

        layout = QVBoxLayout()
        intro = QLabel("Each vector decodes one SPN from a PGN payload and compares it with the expected value "
                       "for the database's unit system. Runs against the database selected on the Validate tab.")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        layout.addWidget(self.vectors_label)
        layout.addWidget(self.vector_table)
        layout.addLayout(buttons)
        tab.setLayout(layout)
        self.load_vectors(self.vectors_path)
        return tab

    def load_vectors(self, path):
        try:
            vectors = tools.load_vectors(path)
        except (OSError, ValueError, KeyError):
            vectors = []
        self.vectors_path = path
        self.vectors_label.setText("Vectors file: " + path)
        self.vector_table.setRowCount(0)
        for v in vectors:
            self.add_vector_row(v)

    def add_vector_row(self, vector=None):
        v = vector if isinstance(vector, dict) else {"name": "New vector", "pgn": "", "spn": "", "data": "",
                                                       "expected": {}, "tolerance": 0.01}
        row = self.vector_table.rowCount()
        self.vector_table.insertRow(row)
        exp = v.get("expected", {})
        metric = exp.get(tools.METRIC, v.get("expected_status") or v.get("expected_text") or "")
        us = exp.get(tools.US, v.get("expected_status") or v.get("expected_text") or "")
        values = [v.get("name", ""), v.get("pgn", ""), v.get("spn", ""), v.get("data", ""),
                  metric, us, v.get("tolerance", ""), ""]
        for col, val in enumerate(values):
            item = QTableWidgetItem(str(val))
            if col == 0 and v.get("source"):
                item.setData(Qt.UserRole, v["source"])
                item.setToolTip(v["source"])
            if col == len(VECTOR_COLUMNS) - 1:
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            self.vector_table.setItem(row, col, item)

    def remove_vector_rows(self):
        for row in sorted({i.row() for i in self.vector_table.selectedItems()}, reverse=True):
            self.vector_table.removeRow(row)

    def table_vectors(self):
        vectors = []
        for row in range(self.vector_table.rowCount()):
            cell = lambda c: (self.vector_table.item(row, c).text().strip() if self.vector_table.item(row, c) else "")
            def number(text, kind=float):
                try:
                    return kind(text, 0) if kind is int else kind(text)
                except ValueError:
                    return None
            vector = {"name": cell(0), "pgn": number(cell(1), int), "spn": number(cell(2), int),
                      "data": cell(3).replace(" ", "").upper()}
            expected = {}
            for system, col in ((tools.METRIC, 4), (tools.US, 5)):
                if number(cell(col)) is not None:
                    expected[system] = number(cell(col))
            if expected:
                vector["expected"] = expected
                vector["tolerance"] = number(cell(6)) or 0.01
            elif cell(4) in tools.SPN_STATUSES:
                vector["expected_status"] = cell(4)
            elif cell(4):
                vector["expected_text"] = cell(4)
            source = self.vector_table.item(row, 0).data(Qt.UserRole) if self.vector_table.item(row, 0) else None
            if source:
                vector["source"] = source
            vectors.append(vector)
        return vectors

    def load_vectors_dialog(self):
        f, _ = QFileDialog.getOpenFileName(self, "Load test vectors", os.path.dirname(self.vectors_path),
                                           "Test vectors (*.json)")
        if f:
            self.load_vectors(f)

    def save_vectors(self):
        tools.save_vectors(self.table_vectors(), self.vectors_path)
        self.vectors_label.setText("Vectors file: {} (saved)".format(self.vectors_path))

    def save_vectors_as(self):
        f, _ = QFileDialog.getSaveFileName(self, "Save test vectors", self.vectors_path, "Test vectors (*.json)")
        if f:
            self.vectors_path = f
            self.save_vectors()

    def run_vectors(self, db, table):
        statuses = []
        for row, vector in enumerate(self.table_vectors()):
            if vector["pgn"] is None or vector["spn"] is None or not vector["data"]:
                status, message = "SKIP", "incomplete vector"
            else:
                try:
                    status, message = tools.run_vector(db, vector, table)
                except ValueError as e:
                    status, message = "FAIL", str(e)
            item = QTableWidgetItem("{}: {}".format(status, message))
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            item.setToolTip(message)
            item.setForeground(QBrush(QColor(LEVEL_COLORS.get(status, "#000000"))))
            self.vector_table.setItem(row, len(VECTOR_COLUMNS) - 1, item)
            statuses.append(status)
        return statuses

    def run_vectors_clicked(self):
        path = self.db_combo.currentText().strip()
        if not path or not os.path.exists(path):
            QMessageBox.warning(self, "No database", "Select a database on the Validate tab first.")
            return
        statuses = self.run_vectors(tools.load(path), tools.UnitTable())
        self.vectors_label.setText("Vectors file: {} | {}: {} pass, {} fail, {} skipped".format(
            self.vectors_path, os.path.basename(path), statuses.count("PASS"), statuses.count("FAIL"),
            statuses.count("SKIP")))


if __name__ == '__main__':
    app = QApplication(sys.argv)
    dialog = DigitalAnnexDialog()
    dialog.show()
    sys.exit(app.exec_())
