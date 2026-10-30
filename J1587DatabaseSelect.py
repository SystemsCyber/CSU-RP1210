"""
A Qt dialog to build the J1587 database from a licensed SAE J1587 document (PDF).

Create:   choose the SAE J1587 PDF (and optionally the SAE J1708 PDF, for the
          names of MIDs 0-127) and write J1587db.licensed.json (metric) and/or
          J1587db.us.licensed.json (US customary). The units preference is the
          one CSU-RP1210 uses for J1939 too.
Validate: structural checks and the decode test vectors for any J1587db*.json.

Run standalone with:  python J1587DatabaseSelect.py
"""

from PyQt5.QtWidgets import (QApplication, QDialog, QDialogButtonBox, QVBoxLayout, QHBoxLayout,
                             QGridLayout, QGroupBox, QLabel, QListWidget, QPushButton, QLineEdit,
                             QCheckBox, QRadioButton, QButtonGroup, QComboBox, QProgressBar,
                             QPlainTextEdit, QTreeWidget, QTreeWidgetItem, QHeaderView, QFileDialog,
                             QMessageBox, QTabWidget, QWidget)
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtGui import QColor, QBrush
import glob
import os
import sys
import traceback
import logging

import j1939db_tools
import j1587db_tools as tools

logger = logging.getLogger(__name__)

LEVEL_COLORS = {tools.PASS: "#087443", tools.WARN: "#b54708", tools.FAIL: "#c4320a", tools.INFO: "#5f6873"}


class GenerateWorker(QThread):
    """Reads the PDF(s) and writes the databases off the GUI thread."""
    log = pyqtSignal(str)
    done = pyqtSignal(dict)
    failed = pyqtSignal(str)

    def __init__(self, pdf_paths, out_dir, systems):
        super(GenerateWorker, self).__init__()
        self.pdf_paths, self.out_dir, self.systems = pdf_paths, out_dir, systems

    def run(self):
        try:
            self.done.emit(tools.generate(self.pdf_paths, self.out_dir, self.systems, log=self.log.emit))
        except Exception as e:
            logger.debug(traceback.format_exc())
            self.failed.emit(str(e))


class J1587DatabaseDialog(QDialog):
    """Select the SAE J1587 PDF, create licensed databases, and check them."""
    database_created = pyqtSignal(dict)

    def __init__(self, parent=None, storage_dir=None):
        super(J1587DatabaseDialog, self).__init__(parent)
        self.storage_dir = storage_dir or os.getcwd()
        self.worker = None
        self.setWindowTitle("J1587 Database")
        self.setWindowModality(Qt.ApplicationModal)
        self.setMinimumWidth(720)
        self.tabs = QTabWidget()
        self.tabs.addTab(self.create_tab(), "Create")
        self.tabs.addTab(self.validate_tab(), "Validate")
        buttons = QDialogButtonBox(QDialogButtonBox.Close, Qt.Horizontal, self)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout()
        layout.addWidget(self.tabs)
        layout.addWidget(buttons)
        self.setLayout(layout)
        self.refresh_database_list()

    # ---- Create ------------------------------------------------------------

    def create_tab(self):
        tab = QWidget()
        notice = QLabel("Select your licensed SAE J1587 document (PDF). Add the SAE J1708 PDF as well to name "
                        "MIDs 0-127. The generated databases contain licensed content: they are git-ignored and "
                        "must not be shared or committed.")
        notice.setWordWrap(True)
        self.pdf_list = QListWidget()
        self.pdf_list.setMaximumHeight(80)
        for name in ("J1587.pdf", "J1708.pdf"):
            for folder in (self.storage_dir, os.path.dirname(os.path.abspath(__file__))):
                path = os.path.join(folder, name)
                if os.path.exists(path) and not self.pdf_list.findItems(path, Qt.MatchExactly):
                    self.pdf_list.addItem(path)
                    break
        add_button = QPushButton("Add PDF...")
        add_button.clicked.connect(self.add_pdf)
        remove_button = QPushButton("Remove")
        remove_button.clicked.connect(lambda: [self.pdf_list.takeItem(self.pdf_list.row(i))
                                               for i in self.pdf_list.selectedItems()])
        side = QVBoxLayout()
        side.addWidget(add_button)
        side.addWidget(remove_button)
        side.addStretch()
        pdf_box = QGroupBox("SAE documents")
        pdf_layout = QHBoxLayout()
        pdf_layout.addWidget(self.pdf_list)
        pdf_layout.addLayout(side)
        pdf_box.setLayout(pdf_layout)

        self.out_dir_edit = QLineEdit(self.storage_dir)
        browse_out = QPushButton("Browse...")
        browse_out.clicked.connect(self.choose_output_dir)
        self.metric_check = QCheckBox("Metric (SI): {}".format(tools.OUTPUT_NAMES[tools.METRIC]))
        self.metric_check.setChecked(True)
        self.us_check = QCheckBox("US customary: {}".format(tools.OUTPUT_NAMES[tools.US]))
        self.us_check.setChecked(True)
        preference = j1939db_tools.read_unit_preference(self.storage_dir)
        self.pref_metric = QRadioButton("Metric")
        self.pref_us = QRadioButton("US customary")
        (self.pref_us if preference == tools.US else self.pref_metric).setChecked(True)
        group = QButtonGroup(self)
        group.addButton(self.pref_metric)
        group.addButton(self.pref_us)
        out_box = QGroupBox("Output")
        grid = QGridLayout()
        grid.addWidget(QLabel("Folder:"), 0, 0)
        grid.addWidget(self.out_dir_edit, 0, 1)
        grid.addWidget(browse_out, 0, 2)
        grid.addWidget(self.metric_check, 1, 0, 1, 3)
        grid.addWidget(self.us_check, 2, 0, 1, 3)
        grid.addWidget(QLabel("Units used by CSU-RP1210 (J1939 and J1587):"), 3, 0, 1, 2)
        pref_row = QHBoxLayout()
        pref_row.addWidget(self.pref_metric)
        pref_row.addWidget(self.pref_us)
        pref_row.addStretch()
        grid.addLayout(pref_row, 4, 0, 1, 3)
        out_box.setLayout(grid)

        self.create_button = QPushButton("Create databases")
        self.create_button.clicked.connect(self.create_databases)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setTextVisible(False)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMinimumHeight(110)
        run_row = QHBoxLayout()
        run_row.addWidget(self.create_button)
        run_row.addWidget(self.progress)

        layout = QVBoxLayout()
        layout.addWidget(notice)
        layout.addWidget(pdf_box)
        layout.addWidget(out_box)
        layout.addLayout(run_row)
        layout.addWidget(self.log_view)
        tab.setLayout(layout)
        return tab

    def add_pdf(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Select SAE J1587 / J1708 PDF", self.storage_dir,
                                                "PDF (*.pdf);;All Files (*.*)")
        for f in files:
            if not self.pdf_list.findItems(f, Qt.MatchExactly):
                self.pdf_list.addItem(f)

    def choose_output_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Output folder", self.out_dir_edit.text())
        if d:
            self.out_dir_edit.setText(d)

    def unit_preference(self):
        return tools.US if self.pref_us.isChecked() else tools.METRIC

    def create_databases(self):
        pdfs = [self.pdf_list.item(i).text() for i in range(self.pdf_list.count())]
        systems = [s for s, box in ((tools.METRIC, self.metric_check), (tools.US, self.us_check)) if box.isChecked()]
        if not pdfs:
            QMessageBox.warning(self, "No document", "Add the SAE J1587 PDF.")
            return
        if not systems:
            QMessageBox.warning(self, "No output", "Select metric, US customary, or both.")
            return
        self.log_view.clear()
        self.create_button.setEnabled(False)
        self.progress.setRange(0, 0)
        self.worker = GenerateWorker(pdfs, self.out_dir_edit.text(), systems)
        self.worker.log.connect(self.log_view.appendPlainText)
        self.worker.done.connect(self.generation_done)
        self.worker.failed.connect(self.generation_failed)
        self.worker.start()

    def generation_done(self, outputs):
        self.progress.setRange(0, 1)
        self.progress.setValue(1)
        self.create_button.setEnabled(True)
        path = j1939db_tools.write_unit_preference(self.unit_preference(), self.storage_dir)
        self.log_view.appendPlainText("Units preference '{}' saved to {}".format(self.unit_preference(), path))
        self.refresh_database_list()
        primary = outputs.get(self.unit_preference()) or next(iter(outputs.values()))
        index = self.db_combo.findText(primary)
        if index >= 0:
            self.db_combo.setCurrentIndex(index)
        self.run_validation()
        self.tabs.setCurrentIndex(1)
        self.database_created.emit(outputs)

    def generation_failed(self, message):
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.create_button.setEnabled(True)
        self.log_view.appendPlainText("Failed: " + message)
        QMessageBox.critical(self, "J1587 conversion failed", message)

    # ---- Validate ------------------------------------------------------------

    def validate_tab(self):
        tab = QWidget()
        self.db_combo = QComboBox()
        self.db_combo.setEditable(True)
        browse = QPushButton("Browse...")
        browse.clicked.connect(self.browse_json)
        run = QPushButton("Validate")
        run.clicked.connect(self.run_validation)
        row = QHBoxLayout()
        row.addWidget(QLabel("Database:"))
        row.addWidget(self.db_combo, 1)
        row.addWidget(browse)
        row.addWidget(run)
        self.results = QTreeWidget()
        self.results.setHeaderLabels(["Result", "Check", "Details"])
        self.results.header().setSectionResizeMode(2, QHeaderView.Stretch)
        layout = QVBoxLayout()
        layout.addLayout(row)
        layout.addWidget(self.results)
        tab.setLayout(layout)
        return tab

    def refresh_database_list(self):
        current = self.db_combo.currentText()
        self.db_combo.clear()
        folders = {self.storage_dir, os.path.dirname(os.path.abspath(__file__))}
        for folder in folders:
            for path in sorted(glob.glob(os.path.join(folder, "J1587db*.json"))):
                if self.db_combo.findText(path) < 0:
                    self.db_combo.addItem(path)
        if current:
            self.db_combo.setEditText(current)

    def browse_json(self):
        f, _ = QFileDialog.getOpenFileName(self, "J1587 database", self.storage_dir, "JSON (*.json)")
        if f:
            self.db_combo.setEditText(f)

    def add_result(self, level, name, message, details=()):
        item = QTreeWidgetItem([level, name, message])
        item.setForeground(0, QBrush(QColor(LEVEL_COLORS.get(level, "#5f6873"))))
        for d in details:
            item.addChild(QTreeWidgetItem(["", "", str(d)]))
        self.results.addTopLevelItem(item)
        return item

    def run_validation(self):
        self.results.clear()
        path = self.db_combo.currentText().strip()
        try:
            db = tools.load(path)
        except (OSError, ValueError) as e:
            self.add_result(tools.FAIL, "load", str(e))
            return
        for check in tools.validate(db):
            self.add_result(check.level, check.name, check.message, check.details)
        if os.path.exists(tools.VECTORS_FILE) and not db.get("_meta", {}).get("skeleton"):
            results = tools.run_vectors(db, tools.load_vectors())
            failed = [(v, m) for v, ok, m in results if not ok]
            self.add_result(tools.PASS if not failed else tools.FAIL, "test vectors",
                            "{}/{} pass ({} units)".format(len(results) - len(failed), len(results),
                                                           db.get("_meta", {}).get("units", "?")),
                            ["MID {} PID {} {}: {}".format(v["mid"], v["pid"], v["data"], m) for v, m in failed])


if __name__ == "__main__":
    app = QApplication.instance() or QApplication(sys.argv)
    dialog = J1587DatabaseDialog()
    dialog.show()
    sys.exit(app.exec_())
