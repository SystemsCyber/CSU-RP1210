"""
TU RP1210 is a 32-bit Python 3 program that uses the RP1210 API from the 
American Trucking Association's Technology and Maintenance Council (TMC). This 
framework provides an introduction sample source code with RP1210 capabilities.
To get the full utility from this program, the user should have an RP1210 compliant
device installed. To make use of the device, you should also have access to a vehicle
network with either J1939 or J1708.

The program is release under one of two licenses.  See LICENSE.TXT for details. The 
default license is as follows:

    Copyright (C) 2018  Jeremy Daily, The University of Tulsa
                  2020  Jeremy Daily, Colorado State University

    This program is free software: you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation, either version 3 of the License, or
    (at your option) any later version.

    This program is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU General Public License for more details.

    You should have received a copy of the GNU General Public License
    along with this program.  If not, see <http://www.gnu.org/licenses/>.
"""

from PyQt5.QtWidgets import (QMainWindow,
                             QWidget,
                             QTreeView,
                             QMessageBox,
                             QFileDialog,
                             QLabel,
                             QSlider,
                             QCheckBox,
                             QLineEdit,
                             QVBoxLayout,
                             QApplication,
                             QPushButton,
                             QTableWidget,
                             QTableView,
                             QTableWidgetItem,
                             QScrollArea,
                             QAbstractScrollArea,
                             QAbstractItemView,
                             QSizePolicy,
                             QGridLayout,
                             QGroupBox,
                             QComboBox,
                             QAction,
                             QDockWidget,
                             QDialog,
                             QFrame,
                             QDialogButtonBox,
                             QInputDialog,
                             QProgressDialog,
                             QTabWidget)
from PyQt5.QtCore import Qt, QTimer, QAbstractTableModel, QCoreApplication, QSize
from PyQt5.QtGui import QIcon, QKeySequence
from PyQt5.QtWidgets import QShortcut

import humanize

import queue
import time
import base64
import sys
import struct
import json
import os
import threading
import binascii

from RP1210 import *
from RP1210Functions import *
from RP1210Select import *
from J1939Tab import *
from J1587Tab import *
from ComponentInfoTab import *
from DigitalAnnexSelect import DigitalAnnexDialog
import app_icons
from J1587DatabaseSelect import J1587DatabaseDialog
import j1939db_tools
import j1587db_tools
import j1939_dbc
import j1939_name
import vehicle_spy
from ISO15765 import *

import logging
logger.addHandler(logging.StreamHandler(sys.stdout))
logger.setLevel(logging.DEBUG)

# Bundled resources (icons, version.json, skeleton J1939db.json) live next to
# this file, or in the PyInstaller bundle when running as CSU_RP1210.exe.
# User files (licensed databases, settings, logs) live in get_storage_path().
module_directory = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))


def status_html(icon_name, text):
    """Network status label: an icon above a caption."""
    return "<html><img src='{}' width='40' height='40'><br>{}</html>".format(app_icons.image_path(icon_name), text)


def database_directories():
    """Folders searched for the licensed J1939/J1587 databases, in order: next to the
    program (the portable folder), the current folder and, for a build at
    <repository>\\dist\\CSU_RP1210.exe, the repository where they were generated."""
    folders = [get_storage_path(), os.getcwd()]
    if getattr(sys, "frozen", False):
        exe_folder = os.path.dirname(os.path.abspath(sys.executable))
        if os.path.basename(exe_folder).lower() == "dist":
            folders.append(os.path.dirname(exe_folder))
    unique = []
    for folder in folders:
        if os.path.normcase(os.path.abspath(folder)) not in [os.path.normcase(os.path.abspath(u)) for u in unique]:
            unique.append(folder)
    return unique

if getattr(sys, "frozen", False):
    log_file = logging.FileHandler(os.path.join(get_storage_path(), "CSU_RP1210.log"), mode="w")
    log_file.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.getLogger().addHandler(log_file)
    logging.getLogger().setLevel(logging.DEBUG)

# RP1210 DLLs must match the process bitness: a 64-bit build loads 64-bit vendor
# DLLs (e.g. PEAKRP32 from System32); 32-bit-only DLLs need a 32-bit build.
logger.info("Running {}-bit Python {}".format(64 if sys.maxsize > 2**32 else 32, sys.version.split()[0]))

try:
    with open(os.path.join(module_directory, 'version.json')) as f:
        CSU_RP1210_version = json.load(f)
except OSError:
    CSU_RP1210_version = {"major": 0, "minor": 0, "patch": 0}
    logger.warning("version.json not found in {}".format(module_directory))

class CSU_RP1210(QMainWindow):
    def __init__(self, splash=None):
        super(CSU_RP1210,self).__init__()

        self.setWindowTitle("CSU RP1210")
        self.setWindowIcon(app_icons.icon("app"))

        # Start-up feedback: the splash screen when the program starts it, else a progress dialog.
        if splash is None:
            progress = QProgressDialog(self)
            progress.setMinimumWidth(600)
            progress.setWindowTitle("Starting Application")
            progress.setMinimumDuration(0)
            progress.setWindowModality(Qt.WindowModal)
            progress.setMaximum(10)
            progress_label = QLabel()
            progress.setLabel(progress_label)

        def step(text, value):
            if splash is not None:
                splash.message(text)
            else:
                progress_label.setText(text)
                progress.setValue(value)
            QCoreApplication.processEvents()

        step("Loading the J1939 database", 0)
        # The repository ships a skeleton J1939db.json without SAE content.
        # Licensed databases come from Tools > J1939 Database (DigitalAnnexSelect).
        self.j1939db = {}
        self.load_j1939db()
        logger.info("Done Loading J1939db")

        self.rx_queues = {}
        self.tx_queues = {}
        step("Loading the J1587 database", 1)
        # Like J1939: a skeleton J1587db.json ships with the program and the
        # licensed database comes from Tools > J1587 Database (the SAE J1587 PDF).
        self.j1587db = {}
        self.load_j1587db()
        logger.info("Done Loading J1587db")

        step("Initializing system variables", 2)
        if sys.platform == "win32":
            os.system("TASKKILL /F /IM DGServer2.exe >nul 2>&1")
            os.system("TASKKILL /F /IM DGServer1.exe >nul 2>&1")
        
        self.update_rate = 100

        self.module_directory = module_directory
        
        self.isodriver = None

        self.source_addresses=[]
        self.long_pgn_timeouts = [65227, ]
        self.long_pgn_timeout_value = 2
        self.short_pgn_timeout_value = .1

        self.setGeometry(0,50,1600,850)
        self.RP1210 = None
        self.network_connected = {"J1939": False, "J1708": False}
        self.RP1210_toolbar = None

        step("Setting up the user interface", 3)
        self.init_ui()
        logger.debug("Done Setting Up User Interface.")
        if splash is not None:
            # The window is up: the RP1210 connection that follows has its own dialogs.
            splash.finish(self)
            splash = None
            step = lambda text, value: QCoreApplication.processEvents()
        else:
            step("Setting up the RP1210 interface", 4)

        self.selectRP1210(automatic=True)
        logger.debug("Done selecting RP1210.")

        step("Initializing a new document", 6)
        self.create_new(False)

        step("Starting loop timers", 8)
        connections_timer = QTimer(self)
        connections_timer.timeout.connect(self.check_connections)
        connections_timer.start(1003) #milliseconds

        read_timer = QTimer(self)
        read_timer.timeout.connect(self.read_rp1210)
        read_timer.start(self.update_rate) #milliseconds

        step("Ready", 10)

    def init_ui(self):
        # Builds GUI
        # Start with a status bar
        self.statusBar().showMessage("Welcome!")

        self.grid_layout = QGridLayout()
        
        # Build common menu options
        menubar = self.menuBar()

        # File Menu Items
        file_menu = menubar.addMenu('&File')
        open_logger2 = self.make_action("import_logger", '&Import CAN Logger 2...', 'Ctrl+I',
            'Open a file from the NMFTA/TU CAN Logger 2', self.open_open_logger2)
        open_vehicle_spy = self.make_action("import_vehicle_spy", 'Import &Vehicle Spy Log...', 'Ctrl+Shift+I',
            'Play a neoVI / Vehicle Spy 3 bus traffic file (.csv) through the J1939 and J1587 tabs', self.open_vehicle_spy)
        exit_action = self.make_action("quit", '&Quit', 'Ctrl+Q', 'Exit the program.', self.confirm_quit)
        for action in (open_logger2, open_vehicle_spy):
            file_menu.addAction(action)
        file_menu.addSeparator()
        file_menu.addAction(exit_action)

        # Tools Menu: databases from licensed SAE documents, and conversions.
        tools_menu = menubar.addMenu('&Tools')
        j1939_database = self.make_action("j1939_database", 'J1939 &Database...', 'Ctrl+D',
            'Create the J1939 database from a licensed Digital Annex and choose metric or US units.',
            self.open_digital_annex_dialog)
        j1587_database = self.make_action("j1587_database", 'J1587 Data&base...', 'Ctrl+J',
            'Create the J1587 database from the licensed SAE J1587 document (PDF).', self.open_j1587_database_dialog)
        export_dbc = self.make_action("export_dbc", 'Export J1939 D&BC...', 'Ctrl+E',
            'Write the loaded J1939 database as a CAN database (.dbc) for SavvyCAN, cantools or CANalyzer.',
            self.export_j1939_dbc)
        convert_spy = self.make_action("convert_candump", 'Convert Vehicle Spy Log to &candump...', 'Ctrl+K',
            'Write the CAN traffic of a Vehicle Spy log as a candump log (csu command line, CSUCAN replay).',
            self.convert_vehicle_spy)
        tools_menu.addAction(j1939_database)
        tools_menu.addAction(j1587_database)
        tools_menu.addSeparator()
        tools_menu.addAction(export_dbc)
        tools_menu.addAction(convert_spy)

        #build the entries in the dockable tool bars
        file_toolbar = self.addToolBar("File")
        for action in (open_logger2, open_vehicle_spy, exit_action):
            file_toolbar.addAction(action)
        tools_toolbar = self.addToolBar("Tools")
        for action in (j1939_database, j1587_database, export_dbc, convert_spy):
            tools_toolbar.addAction(action)

        # RP1210 Menu Items
        self.rp1210_menu = menubar.addMenu('&RP1210')

        help_menu = menubar.addMenu('&Help')
        shortcuts = self.make_action("shortcuts", '&Keyboard Shortcuts', 'Ctrl+/',
            'List the keyboard shortcuts of every menu command and tab.', self.show_shortcuts_dialog)
        about = self.make_action("about", 'A&bout', 'F1',
            'Display a dialog box with information about the program.', self.show_about_dialog)
        help_menu.addAction(shortcuts)
        help_menu.addAction(about)

        help_toolbar = self.addToolBar("Help")
        help_toolbar.addAction(shortcuts)
        help_toolbar.addAction(about)

        # Setup the network status windows for logging
        info_box = {}
        info_box_area = {}
        info_layout = {}
        info_box_area_layout = {}
        self.previous_count = {}
        self.status_icon = {}
        self.previous_count = {}
        self.message_count_label = {}
        self.message_rate_label = {}
        self.message_duration_label = {}
        for key in ["J1939","J1708"]:
            # Create the container widget
            info_box_area[key] = QScrollArea()
            info_box_area[key].setWidgetResizable(True)
            info_box_area[key].setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        
            bar_size = QSize(150,300)
            info_box_area[key].sizeHint()
            info_box[key] = QFrame(info_box_area[key])
            
            info_box_area[key].setWidget(info_box[key])
            info_box_area[key].setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        
        
            # create a layout strategy for the container 
            info_layout[key] = QVBoxLayout()
            #set the layout so labels are at the top
            info_layout[key].setAlignment(Qt.AlignTop)
            #assign the layout strategy to the container
            info_box[key].setLayout(info_layout[key])

            info_box_area_layout[key] = QVBoxLayout()
            info_box_area[key].setLayout(info_box_area_layout[key])
            info_box_area_layout[key].addWidget(info_box[key])
            
            #Add some labels and content
            self.status_icon[key] = QLabel(status_html("network_unavailable", "Network<br>Unavailable"))
            self.status_icon[key].setAlignment(Qt.AlignCenter)
            
            self.previous_count[key] = 0
            self.message_count_label[key] = QLabel("Count: 0")
            self.message_count_label[key].setAlignment(Qt.AlignCenter)
            
            #self.message_duration = 0
            self.message_duration_label[key] = QLabel("Duration: 0 sec.")
            self.message_duration_label[key].setAlignment(Qt.AlignCenter)
            
            #self.message_rate = 0
            self.message_rate_label[key] = QLabel("Rate: 0 msg/sec")
            self.message_rate_label[key].setAlignment(Qt.AlignCenter)
            
            csv_save_button = QPushButton("Save as CSV")
            csv_save_button.setToolTip("Save all the {} Network messages to a comma separated values file.".format(key))
            if key == ["J1939"]:
                csv_save_button.clicked.connect(self.save_j1939_csv)
            if key == ["J1708"]:
                csv_save_button.clicked.connect(self.save_j1708_csv)
            
            info_layout[key].addWidget(QLabel("<html><h3>{} Status</h3></html>".format(key)))
            info_layout[key].addWidget(self.status_icon[key])
            info_layout[key].addWidget(self.message_count_label[key])
            info_layout[key].addWidget(self.message_rate_label[key])
            info_layout[key].addWidget(self.message_duration_label[key])
    
        
        # Initialize tab screen
        self.tabs = QTabWidget()
        self.tabs.setTabShape(QTabWidget.Triangular)
        self.J1939 = J1939Tab(self, self.tabs)
        self.J1587 = J1587Tab(self, self.tabs)
        self.Components = ComponentInfoTab(self, self.tabs)

        
        # Ctrl+1 ... Ctrl+9 switch tabs (listed in Help > Keyboard Shortcuts).
        for i in range(min(9, self.tabs.count())):
            QShortcut(QKeySequence("Ctrl+{}".format(i + 1)), self, activated=lambda i=i: self.tabs.setCurrentIndex(i))

        self.grid_layout.addWidget(info_box_area["J1939"],0,0,1,1)
        self.grid_layout.addWidget(info_box_area["J1708"],1,0,1,1)
        self.grid_layout.addWidget(self.tabs,0,1,4,1)

        main_widget = QWidget()
        main_widget.setLayout(self.grid_layout)
        self.setCentralWidget(main_widget)
        
        self.show()
    
    def get_plot_bytes(self, fig):
        img = BytesIO()
        fig.figsize=(7.5, 10)
        fig.savefig(img, format='PDF',)
        return img

    def make_action(self, icon_name, text, shortcut, tip, slot):
        """A menu/toolbar command with its icon, keyboard shortcut and tip (the tooltip shows the shortcut)."""
        action = QAction(app_icons.icon(icon_name), text, self)
        action.setShortcut(QKeySequence(shortcut))
        action.setStatusTip(tip)
        action.setToolTip("{} ({})".format(text.replace("&", "").rstrip("."), QKeySequence(shortcut).toString(QKeySequence.NativeText)))
        action.triggered.connect(slot)
        return action

    def setup_RP1210_menus(self):
        actions = [
            self.make_action("connect", '&Client Connect...', 'Ctrl+Shift+C',
                             'Connect Vehicle Diagnostic Adapter', self.selectRP1210),
            self.make_action("driver_version", '&Driver Version', 'Ctrl+Shift+V',
                             'Show Vehicle Diagnostic Adapter Driver Version Information', self.display_version),
            self.make_action("detailed_version", 'De&tailed Version', 'Ctrl+Shift+T',
                             'Show Vehicle Diagnostic Adapter Detailed Version Information', self.display_detailed_version),
            self.make_action("hardware_status", 'Get &Hardware Status', 'Ctrl+Shift+H',
                             'Determine details regarding the hardware interface status and its connections.',
                             self.get_hardware_status),
            self.make_action("hardware_status_ex", 'Get &Extended Hardware Status', 'Ctrl+Shift+E',
                             'Determine the hardware interface status and whether the VDA device is physically connected.',
                             self.get_hardware_status_ex),
            self.make_action("disconnect", 'Client Dis&connect', 'Ctrl+Shift+X',
                             'Disconnect all RP1210 Clients', self.disconnectRP1210),
        ]
        self.RP1210_toolbar = self.addToolBar("RP1210")
        for action in actions:
            self.rp1210_menu.addAction(action)
            self.RP1210_toolbar.addAction(action)

    def show_shortcuts_dialog(self):
        """Help > Keyboard Shortcuts: every menu command, tab and tab button with its keys."""
        rows = []
        for menu_action in self.menuBar().actions():
            menu = menu_action.menu()
            for action in menu.actions() if menu else []:
                if not action.shortcut().isEmpty():
                    rows.append((menu_action.text().replace("&", ""), action.text().replace("&", "").rstrip("."),
                                 action.shortcut().toString(QKeySequence.NativeText)))
        for i in range(min(9, self.tabs.count())):
            rows.append(("Tabs", self.tabs.tabText(i), "Ctrl+{}".format(i + 1)))
        rows += [("J1939 PGNs tab", "Dynamically update table", "Alt+U"),
                 ("J1939 PGNs tab", "Expand multiplexed PGNs", "Alt+X"),
                 ("J1939 PGNs tab", "Industry group", "Alt+G"),
                 ("J1939 PGNs tab", "Stop J1939 broadcast", "Alt+B"),
                 ("J1939 PGNs tab", "Clear J1939 PGN table", "Alt+L"),
                 ("J1939 Diagnostic Codes tab", "Request previously active DTCs (DM2)", "Alt+2"),
                 ("J1939 Freeze Frames tab", "Request freeze frame parameters (DM4)", "Alt+4"),
                 ("J1587 Data tab", "Dynamically update table", "Alt+U"),
                 ("J1587 Data tab", "Clear J1587 table", "Alt+L"),
                 ("Component Information tab", "Request VIN", "Alt+V"),
                 ("Component Information tab", "Request component ID", "Alt+C"),
                 ("Component Information tab", "Request software ID", "Alt+S"),
                 ("Component Information tab", "Request ECU distances", "Alt+D"),
                 ("Component Information tab", "Request ECU hours", "Alt+O"),
                 ("Component Information tab", "Request address claims", "Alt+A"),
                 ("Component Information tab", "Refresh data", "F5 or Alt+E")]
        dialog = QDialog(self)
        dialog.setWindowTitle("Keyboard Shortcuts")
        dialog.setWindowIcon(app_icons.icon("shortcuts"))
        table = QTableWidget(len(rows), 3)
        table.setHorizontalHeaderLabels(["Where", "Command", "Keys"])
        for r, row in enumerate(rows):
            for c, value in enumerate(row):
                table.setItem(r, c, QTableWidgetItem(value))
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(22)
        table.resizeColumnsToContents()
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(dialog.reject)
        layout = QVBoxLayout()
        layout.addWidget(table)
        layout.addWidget(buttons)
        dialog.setLayout(layout)
        dialog.resize(table.horizontalHeader().length() + 60, 560)
        dialog.exec_()

    def create_new(self, new_file=True):
        self.data_package = {"File Format":{"major":CSU_RP1210_version["major"],
                                            "minor":CSU_RP1210_version["minor"],
                                            "patch":CSU_RP1210_version["minor"]}}
        self.data_package["Time Records"] = {"Personal Computer":{
                                                 "PC Start Time": time.time(),
                                                 "Last PC Time": None,
                                                 "Last GPS Time": None,
                                                 "Permission Time": None,
                                                 "PC Time at Last GPS Reading": None,
                                                 "PC Time minus GPS Time": []
                                                 }
                                            }
        self.data_package["Warnings"] = []
        self.data_package["J1587 Message and Parameter IDs"] = {}
        self.data_package["J1939 Parameter Group Numbers"] = {}
        self.data_package["J1939 Suspect Parameter Numbers"] = {}
        self.data_package["UDS Messages"] = {}
        self.data_package["Component Information"] = {}
        self.data_package["Distance Information"] = {}
        self.data_package["ECU Time Information"] = {}
        self.data_package["Event Data"] = {}
        self.data_package["Address Claims"] = {}
        self.data_package["GPS Data"] = {
            "Altitude": 0.0,
            "GPS Time": None,
            "Latitude": None,
            "Longitude": None,
            "System Time": time.time(),
            "Address": "Not Available"}
        self.data_package["Diagnostic Codes"] = {"DM01":{},
                                                 "DM02":{},
                                                 "DM04":{}
                                                 }
        self.request_timeout = 1

        self.J1939.address_claims = {}
        self.J1939.reset_data()
        self.J1939.clear_j1939_table()
        self.J1587.clear_J1587_table()
        self.Components.rebuild_trees()

    # def setup_logger(self, logger_name, log_file, level=logging.INFO):
    #     l = logging.getLogger(logger_name)
    #     l.propagate = False
    #     l.setLevel(level)
    #     l.removeHandler(logging.StreamHandler)
        
    #     formatter = logging.Formatter('%(message)s')
    #     fileHandler = logging.FileHandler(log_file, mode='w')
    #     fileHandler.setFormatter(formatter)
    #     fileHandler.setLevel(logging.DEBUG)
    #     l.addHandler(fileHandler)
    #     streamHandler = logging.StreamHandler()
    #     streamHandler.setLevel(logging.CRITICAL)
    #     l.addHandler(streamHandler)    
    #
    def load_j1939db(self):
        """
        Load the J1939 database in place (the tabs hold a reference to this dict).
        Search order: $CSU_J1939DB, then the licensed database for the preferred
        units (J1939db.us.licensed.json or J1939db.licensed.json), then the other
        unit system, then the skeleton J1939db.json.
        """
        db = {"J1939BitDecodings":{},
              "J1939FMITabledb": {},
              "J1939LampFlashTabledb": {},
              "J1939OBDTabledb": {},
              "J1939PGNdb": {},
              "J1939SAHWTabledb": {},
              "J1939SATabledb": {},
              "J1939SPNdb": {} }
        units = j1939db_tools.read_unit_preference(get_storage_path())
        candidates = [os.environ.get("CSU_J1939DB")]
        for directory in database_directories():
            candidates += j1939db_tools.database_candidates(directory, units)[:2]   # licensed files first
        candidates += j1939db_tools.database_candidates(module_directory, units)
        for candidate in filter(None, candidates):
            try:
                with open(candidate,'r') as j1939_file:
                    db.update(json.load(j1939_file))
                logger.info("Loaded J1939 database from {} (preferred units: {})".format(candidate, units))
                break
            except FileNotFoundError:
                continue
        else:
            logger.debug("No J1939 database file was found.")
        if db.get("_meta", {}).get("skeleton"):
            logger.warning("Only the skeleton J1939db.json is loaded. Use Tools > J1939 Database to create "
                           "a licensed database from the Digital Annex.")
        self.j1939db.clear()
        self.j1939db.update(db)
        self.update_database_status()

    def open_digital_annex_dialog(self):
        dialog = DigitalAnnexDialog(self, storage_dir=get_storage_path())
        dialog.database_created.connect(lambda outputs: self.load_j1939db())
        dialog.exec_()
        # The units preference (shared with J1587) may have changed even without regenerating.
        self.load_j1939db()
        self.load_j1587db()
        self.statusBar().showMessage("J1939 database reloaded ({} PGNs).".format(len(self.j1939db.get("J1939PGNdb", {}))))

    def load_j1587db(self):
        """
        Load the J1587 database in place (the J1587 tab holds a reference to this dict).
        Search order: $CSU_J1587DB, then the licensed database for the preferred
        units (J1587db.us.licensed.json or J1587db.licensed.json), then the other
        unit system, then the skeleton J1587db.json.
        """
        db = {"FMI": {}, "MID": {}, "MIDAlias": {}, "PID": {}, "PIDNames": {}, "SID": {}}
        units = j1939db_tools.read_unit_preference(get_storage_path())
        candidates = [os.environ.get("CSU_J1587DB")]
        for directory in database_directories():
            candidates += j1587db_tools.database_candidates(directory, units)[:2]
        candidates += j1587db_tools.database_candidates(module_directory, units)
        for candidate in filter(None, candidates):
            try:
                with open(candidate, 'r', encoding='utf-8') as j1587_file:
                    db.update(json.load(j1587_file))
                logger.info("Loaded J1587 database from {} (preferred units: {})".format(candidate, units))
                break
            except FileNotFoundError:
                continue
        if db.get("_meta", {}).get("skeleton"):
            logger.warning("Only the skeleton J1587db.json is loaded. Use Tools > J1587 Database to create "
                           "a licensed database from the SAE J1587 document.")
        self.j1587db.clear()
        self.j1587db.update(db)
        self.update_database_status()

    def update_database_status(self):
        """Permanent status bar note of which J1939/J1587 databases are loaded (red for a skeleton)."""
        if not hasattr(self, "database_status"):
            self.database_status = QLabel()
            self.statusBar().addPermanentWidget(self.database_status)
        parts, skeleton = [], False
        for label, db, table in (("J1939", getattr(self, "j1939db", {}), "J1939PGNdb"),
                                 ("J1587", getattr(self, "j1587db", {}), "PID")):
            meta = db.get("_meta", {})
            if not db:
                continue
            if meta.get("skeleton") or not db.get(table):
                skeleton = True
                parts.append("{} database: skeleton (Tools > {} Database)".format(label, label))
            else:
                parts.append("{} database: licensed, {}".format(label, "US" if meta.get("units") == "us" else "metric"))
        self.database_status.setText("  |  ".join(parts))
        self.database_status.setStyleSheet("color: #c4320a; font-weight: bold;" if skeleton else "")

    def open_j1587_database_dialog(self):
        dialog = J1587DatabaseDialog(self, storage_dir=get_storage_path())
        dialog.database_created.connect(lambda outputs: self.load_j1587db())
        dialog.exec_()
        self.load_j1587db()
        self.load_j1939db()
        self.statusBar().showMessage("J1587 database reloaded ({} PIDs).".format(len(self.j1587db.get("PID", {}))))

    def export_j1939_dbc(self):
        meta = self.j1939db.get("_meta", {})
        if meta.get("skeleton") or not self.j1939db.get("J1939PGNdb"):
            QMessageBox.information(self, "Export J1939 DBC",
                "Only the skeleton J1939 database is loaded. Create the licensed database with "
                "Tools > J1939 Database first.")
            return
        units = meta.get("units", "metric")
        default = os.path.join(get_storage_path(), "J1939.{}.licensed.dbc".format(units))
        fname, _ = QFileDialog.getSaveFileName(self, "Export J1939 DBC", default, "CAN database (*.dbc)")
        if not fname:
            return
        text, stats = j1939_dbc.build(self.j1939db)
        with open(fname, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        QMessageBox.information(self, "Export J1939 DBC",
            "Wrote {} ({} units): {} messages, {} signals, {} value tables.\n\n{} SPNs that a DBC cannot "
            "represent (text, variable length or split fields) are listed in the message comments.\n\n"
            "The file contains licensed J1939 content: do not share or commit it.".format(
                fname, units, stats["messages"], stats["signals"], stats["value_tables"], stats["skipped_spns"]))

    def convert_vehicle_spy(self):
        fname, _ = QFileDialog.getOpenFileName(self, "Vehicle Spy Log", self.export_path,
                                               "Vehicle Spy logs (*.csv);;All Files (*.*)")
        if not fname:
            return
        out, _ = QFileDialog.getSaveFileName(self, "Save candump log", os.path.splitext(fname)[0] + ".candump",
                                             "candump log (*.candump *.log);;All Files (*.*)")
        if not out:
            return
        n = vehicle_spy.to_candump(fname, out)
        self.statusBar().showMessage("Wrote {} CAN frames to {}".format(n, out))

    def open_vehicle_spy(self):
        """Play a Vehicle Spy 3 log through the J1939 and J1587 tabs, as if read from an adapter."""
        fname, _ = QFileDialog.getOpenFileName(self, "Import Vehicle Spy Log", self.export_path,
                                               "Vehicle Spy logs (*.csv);;All Files (*.*)")
        if fname:
            self.import_vehicle_spy(fname)

    def import_vehicle_spy(self, fname):
        with open(fname, "rb") as f:
            total = sum(1 for _ in f)
        progress = QProgressDialog(self)
        progress.setMinimumWidth(600)
        progress.setWindowTitle("Importing Vehicle Spy Log")
        progress.setMinimumDuration(0)
        progress.setWindowModality(Qt.WindowModal)
        progress.setMaximum(total)
        counts = {"J1939": 0, "J1587": 0, "skipped": 0}
        reassembler = vehicle_spy.J1939Reassembler()
        logger.info("Importing Vehicle Spy log {}".format(fname))
        for i, record in enumerate(vehicle_spy.read(fname)):
            timestamp = int(record.time * 1e6)
            if isinstance(record, vehicle_spy.CANRecord):
                if record.error or not record.extended:
                    counts["skipped"] += 1
                    continue
                for priority, pgn, sa, da, data in reassembler.feed(record):
                    buffer = vehicle_spy.rp1210_j1939(timestamp, priority, pgn, sa, da, data, echo=record.tx)
                    try:
                        self.J1939.fill_j1939_table({'current_time': record.time, 'data': buffer})
                        counts["J1939"] += 1
                    except Exception:
                        logger.debug(traceback.format_exc())
            elif record.checksum_ok and not record.error:
                try:
                    self.J1587.fill_j1587_table((record.time, vehicle_spy.rp1210_j1708(timestamp, record, echo=record.tx)))
                    counts["J1587"] += 1
                except Exception:
                    logger.debug(traceback.format_exc())
            else:
                counts["skipped"] += 1
            if i % 500 == 0:
                progress.setValue(min(record.line, total))
                progress.setLabelText("{} J1939 and {} J1708 messages".format(counts["J1939"], counts["J1587"]))
                QCoreApplication.processEvents()
                if progress.wasCanceled():
                    break
        progress.close()
        message = "Imported {}: {} J1939 messages, {} J1708 messages, {} skipped.".format(
            os.path.basename(fname), counts["J1939"], counts["J1587"], counts["skipped"])
        logger.info(message)
        self.statusBar().showMessage(message)
        return counts

    def open_open_logger2(self):
        filters = "{} Data Files (*.bin);;All Files (*.*)".format(self.title)
        selected_filter = "CAN Logger 2 Data Files (*.bin)"
        fname,_ = QFileDialog.getOpenFileName(self, 
                                            'Open CAN Logger 2 File',
                                            self.export_path,
                                            filters,
                                            selected_filter)
        if fname:
            file_size = os.path.getsize(fname)
            bytes_processed = 0
            #update the data package
            progress = QProgressDialog(self)
            progress.setMinimumWidth(600)
            progress.setWindowTitle("Processing CAN Logger 2 Data File")
            progress.setMinimumDuration(0)
            progress.setWindowModality(Qt.WindowModal)
            progress.setModal(False) #Will lead to instability when trying to click around.
            progress.setMaximum(file_size)
            progress_label = QLabel("Processed {:0.3f} of {:0.3f} Mbytes.".format(bytes_processed/1000000,file_size/1000000))
            progress.setLabel(progress_label)
        
            logger.debug("Importing file {}".format(fname))
            with open(fname,'rb') as f:
                while True:
                    line = f.read(512)
                    if len(line) < 512:
                        logger.debug("Reached end of file {}".format(fname))
                        break
                    #check integrity
                    bytes_to_check = line[0:508]
                    crc_value = struct.unpack('<L',line[508:512])[0]
                    
                    if binascii.crc32(bytes_to_check) != crc_value:
                        logger.warning("CRC Failed")
                        break
                    #print('CRC Passed')
                    #print(line)
                    #print(" ".join(["{:02X}".format(c) for c in line]))
                    prefix = line[0:4]
                    RXCount0 = struct.unpack('<L',line[479:483])[0]
                    RXCount1 = struct.unpack('<L',line[483:487])[0]   
                    RXCount2 = struct.unpack('<L',line[487:491])[0]
                    # CAN Controller Receive Error Counters.
                    Can0_REC = line[491]
                    Can1_REC = line[492]
                    Can2_REC = line[493]
                    # CAN Controller Transmit Error Counters
                    Can0_TEC = line[494]
                    Can1_TEC = line[495]
                    Can2_TEC = line[496]
                    # A constant ASCII Text file to preserve the original filename (and take up space)
                    block_filename = line[497:505]
                    #micro seconds to write the previous 512 bytes to the SD card (only 3 bytes so mask off the MSB)
                    buffer_write_time = struct.unpack('<L',line[505:509])[0] & 0x00FFFFFF
                    for i in range(4,487,25):
                        # parse data from records
                        channel = line[i]
                        timestamp = struct.unpack('<L',line[i+1:i+5])[0]
                        system_micros = struct.unpack('<L',line[i+5:i+9])[0]
                        can_id = struct.unpack('<L',line[i+9:i+13])[0]
                        dlc = line[i+13]
                        if dlc == 0xFF:
                            break
                        micros_per_second = struct.unpack('<L',line[i+13:i+17])[0] & 0x00FFFFFF
                        timestamp += micros_per_second/1000000
                        data_bytes = line[i+17:i+25]
                        data = struct.unpack('8B',data_bytes)[0]

                        #create an RP1210 data structure
                        sa =  can_id & 0xFF
                        priority = (can_id & 0x1C000000) >> 26
                        edp = (can_id & 0x02000000) >> 25
                        dp =  (can_id & 0x01000000) >> 24
                        pf =  (can_id & 0x00FF0000) >> 16
                        if pf >= 0xF0:
                            ps = (can_id & 0x0000FF00) >> 8
                            da = 0xFF
                        else:
                            ps = 0
                            da = (can_id & 0x0000FF00) >> 8
                        ps = struct.pack('B', ps)
                        pf = struct.pack('B', pf)
                        pgn = ps + pf + struct.pack('B', edp + dp)
                        rp1210_message = struct.pack('<L',system_micros) 
                        rp1210_message += b'\x00' 
                        rp1210_message += pgn  
                        rp1210_message += struct.pack('B', priority) 
                        rp1210_message += struct.pack('B', sa) 
                        rp1210_message += struct.pack('B', da) 
                        rp1210_message += data_bytes
                        self.rx_queues["Logger"].put({'current_time': timestamp, 'data': rp1210_message})
                    bytes_processed += 512
                    progress.setValue(bytes_processed)
                    progress_label.setText("Processed {:0.3f} of {:0.3f} Mbytes.".format(bytes_processed/1000000,file_size/1000000))
                    if progress.wasCanceled():
                        break
                    QCoreApplication.processEvents()

                    
            progress.deleteLater()
    def reload_data(self):
        """
        Reload and refresh the data tables.
        """
        self.J1939.pgn_data_model.aboutToUpdate()
        self.J1939.j1939_unique_ids = self.data_package["J1939 Parameter Group Numbers"]
        self.J1939.pgn_data_model.setDataDict(self.J1939.j1939_unique_ids)
        self.J1939.pgn_data_model.signalUpdate()
        #TODO: Add the row and column resizers like the one for UDS.

        self.J1939.pgn_rows = list(self.J1939.j1939_unique_ids.keys())
        
        self.J1939.spn_data_model.aboutToUpdate()
        self.J1939.unique_spns = self.data_package["J1939 Suspect Parameter Numbers"]
        self.J1939.spn_data_model.setDataDict(self.J1939.unique_spns)
        self.J1939.spn_data_model.signalUpdate()

        self.J1939.dm01_data_model.aboutToUpdate()
        self.J1939.active_trouble_codes = self.data_package["Diagnostic Codes"]["DM01"]
        self.J1939.dm01_data_model.setDataDict(self.J1939.active_trouble_codes)
        self.J1939.dm01_data_model.signalUpdate()

        self.J1939.dm02_data_model.aboutToUpdate()
        self.J1939.previous_trouble_codes = self.data_package["Diagnostic Codes"]["DM02"]
        self.J1939.dm02_data_model.setDataDict(self.J1939.previous_trouble_codes)
        self.J1939.dm02_data_model.signalUpdate()

        self.J1939.dm04_data_model.aboutToUpdate()
        self.J1939.freeze_frame = self.data_package["Diagnostic Codes"]["DM04"]
        self.J1939.dm04_data_model.setDataDict(self.J1939.freeze_frame)
        self.J1939.dm04_data_model.signalUpdate()

        self.J1939.uds_data_model.aboutToUpdate()
        self.J1939.iso_recorder.uds_messages = self.data_package["UDS Messages"]
        self.J1939.uds_data_model.setDataDict(self.J1939.iso_recorder.uds_messages)
        self.J1939.uds_data_model.signalUpdate()
        self.J1939.uds_table.resizeRowsToContents()
        for c in self.J1939.uds_resizable_cols:
            self.J1939.uds_table.resizeColumnToContents(c)

        # Address claims saved in the data package override source address names again.
        self.J1939.address_claims = {}
        for claim in self.data_package.setdefault("Address Claims", {}).values():
            try:
                raw = int(claim["NAME"], 16)
                self.J1939.address_claims[int(claim["Source Address"])] = j1939_name.decode(raw.to_bytes(8, "little"))
            except (KeyError, ValueError, TypeError):
                continue
        self.J1939.refresh_sources()

    def confirm_quit(self):
        self.close()
    
    def closeEvent(self, event):
        result = QMessageBox.question(self, "Confirm Exit",
            "Are you sure you want to quit the program?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes)
        if result == QMessageBox.Yes:
            logger.debug("Quitting.")
            event.accept()
        else:
            event.ignore()

    def selectRP1210(self, automatic=False):
        logger.debug("Select RP1210 function called.")
        selection = SelectRP1210("CSU_RP1210")
        logger.debug(selection.dll_name)
        if not automatic:
            selection.show_dialog()
        elif not selection.dll_name:
            selection.show_dialog()
        
        dll_name = selection.dll_name
        protocol = selection.protocol
        deviceID = selection.deviceID
        speed    = selection.speed
        channel  = getattr(selection, "channel", 1)

        if dll_name is None: #this is what happens when you hit cancel
            return
        #Close things down
        try:
            self.close_clients()
        except AttributeError:
            pass
        try:
            for thread in self.read_message_threads:
                thread.runSignal = False
        except AttributeError:
            pass
        
        progress = QProgressDialog(self)
        progress.setMinimumWidth(600)
        progress.setWindowTitle("Setting Up RP1210 Clients")
        progress.setMinimumDuration(3000)
        progress.setWindowModality(Qt.WindowModal)
        progress.setMaximum(6)
      
        # Once an RP1210 DLL is selected, we can connect to it using the RP1210 helper file.
        self.RP1210 = RP1210Class(dll_name)
    
        if self.RP1210_toolbar is None:
            self.setup_RP1210_menus()
        
        # We want to connect to multiple clients with different protocols.
        self.client_ids={}
        self.client_ids["CAN"] = self.RP1210.get_client_id("CAN", deviceID, "{}".format(speed), channel)
        progress.setValue(1)
        self.client_ids["J1708"] = self.RP1210.get_client_id("J1708", deviceID, "Auto")
        progress.setValue(2)
        self.client_ids["J1939"] = self.RP1210.get_client_id("J1939", deviceID, "{}".format(speed), channel)
        progress.setValue(3)
        #self.client_ids["ISO15765"] = self.RP1210.get_client_id("ISO15765", deviceID, "Auto")
        #progress.setValue(3)
        
        logger.debug('Client IDs: {}'.format(self.client_ids))

        # If there is a successful connection, save it.
        file_contents={ "dll_name":dll_name,
                        "protocol":protocol,
                        "deviceID":deviceID,
                        "speed":speed,
                        "channel":channel
                       }
        logger.debug(selection.connections_file)
        try:
            with open(selection.connections_file,"w") as rp1210_file:
                json.dump(file_contents, rp1210_file)
        except OSError as e:
            logger.warning(repr(e))
            logger.warning(f"Failed to create {selection.connections_file}")
            
        self.rx_queues={"Logger":queue.Queue(10000)}
        self.read_message_threads={}
        self.extra_queues = {"Logger":queue.Queue(10000)}
        self.isodriver = ISO15765Driver(self, self.extra_queues["Logger"])
      
        # Set all filters to pass.  This allows messages to be read.
        # Constants are defined in an included file
        i = 0
        BUFFER_SIZE = 8192
        logger.debug("BUFFER_SIZE = {}".format(BUFFER_SIZE))
        for protocol, nClientID in self.client_ids.items():
            QCoreApplication.processEvents()
            if nClientID is not None:
                # By turning on Echo Mode, our logger process can record sent messages as well as received.
                fpchClientCommand = (c_char*8192)()
                fpchClientCommand[0] = 1 #Echo mode on
                return_value = self.RP1210.SendCommand(c_short(RP1210_Echo_Transmitted_Messages), 
                                                       c_short(nClientID), 
                                                       byref(fpchClientCommand), 1)
                logger.debug('RP1210_Echo_Transmitted_Messages returns {:d}: {}'.format(return_value,self.RP1210.get_error_code(return_value)))
                
                #Set all filters to pas
                return_value = self.RP1210.SendCommand(c_short(RP1210_Set_All_Filters_States_to_Pass), 
                                                       c_short(nClientID),
                                                       None, 0)
                if return_value == 0:
                    logger.debug("RP1210_Set_All_Filters_States_to_Pass for {} is successful.".format(protocol))
                    #setup a Receive queue. This keeps the GUI responsive and enables messages to be received.
                    self.rx_queues[protocol] = queue.Queue(10000)
                    self.tx_queues[protocol] = queue.Queue(10000)
                    self.extra_queues[protocol] = queue.Queue(10000)
                    self.read_message_threads[protocol] = RP1210ReadMessageThread(self, 
                                                                                  self.rx_queues[protocol],
                                                                                  self.extra_queues[protocol],
                                                                                  self.RP1210.ReadMessage, 
                                                                                  nClientID,
                                                                                  protocol,"CSU_RP1210")
                    self.read_message_threads[protocol].daemon = True #needed to close the thread when the application closes.
                    self.read_message_threads[protocol].start()
                    logger.debug("Started RP1210ReadMessage Thread.")

                    self.statusBar().showMessage("{} connected using {}".format(protocol,dll_name))
                    if protocol == "J1939":
                        self.isodriver = ISO15765Driver(self, self.extra_queues["J1939"])
                    
                else :
                    logger.debug('RP1210_Set_All_Filters_States_to_Pass returns {:d}: {}'.format(return_value,self.RP1210.get_error_code(return_value)))

                if protocol == "J1939":
                    fpchClientCommand[0] = 0x00 #0 = as fast as possible milliseconds
                    fpchClientCommand[1] = 0x00
                    fpchClientCommand[2] = 0x00
                    fpchClientCommand[3] = 0x00
                    
                    return_value = self.RP1210.SendCommand(c_short(RP1210_Set_J1939_Interpacket_Time), 
                                                           c_short(nClientID), 
                                                           byref(fpchClientCommand), 4)
                    logger.debug('RP1210_Set_J1939_Interpacket_Time returns {:d}: {}'.format(return_value,self.RP1210.get_error_code(return_value)))
                    
               
            else:
                logger.debug("{} Client not connected for All Filters to pass. No Queue will be set up.".format(protocol))
            i+=1
            progress.setValue(3+i)
        
        progress.close()
        progress.deleteLater()
        # Not every adapter has J1708 (e.g. PEAK); warn only when no CAN-based client connected.
        # The box is shown from the event loop: opening it while the window-modal progress
        # dialog is still on screen (connection attempts can take seconds) crashes Qt.
        if self.client_ids["J1939"] is None and self.client_ids["CAN"] is None:
            QTimer.singleShot(0, lambda: QMessageBox.information(self, "RP1210 Client Not Connected.",
                "The default RP1210 Device was not found or is unplugged. Please reconnect your Vehicle "
                "Diagnostic Adapter (VDA) and select the RP1210 device to use."))

    def check_connections(self):
        '''
        This function checks the VDA hardware status function to see if it has seen network traffic in the last second.
        
        '''    
        network_connection = {}

        for key in ["J1939", "J1708"]:
            network_connection[key]=False            
            try:
                current_count = self.read_message_threads[key].message_count
                duration = time.time() - self.read_message_threads[key].start_time
                self.message_duration_label[key].setText(status_html("client_connected", "Client Connected<br>{:0.0f} sec.".format(duration)))
                network_connection[key] = True
            except (KeyError, AttributeError) as e:
                current_count = 0
                duration = 0
                self.message_duration_label[key].setText(status_html("client_disconnected", "Client Disconnected<br>{:0.0f} sec.".format(duration)))
                
            count_change = current_count - self.previous_count[key]
            self.previous_count[key] = current_count
            # See if messages come in. Change the 
            if count_change > 0 and not self.network_connected[key]: 
                self.status_icon[key].setText(status_html("network_online", "Network<br>Online"))
                self.network_connected[key] = True
            elif count_change == 0 and self.network_connected[key]:             
                self.status_icon[key].setText(status_html("network_unavailable", "Network<br>Unavailable"))
                self.network_connected[key] = False

            self.message_count_label[key].setText("Message Count:\n{}".format(humanize.intcomma(current_count)))
            self.message_rate_label[key].setText("Message Rate:\n{} msg/sec".format(count_change))
        
        #Get ECM Clock and Date from J1587 if available
        self.data_package["Time Records"]["Personal Computer"]["Last PC Time"] = time.time()
        try:
            if self.ok_to_send_j1587_requests and self.client_ids["J1708"] is not None:
                for pid in [251, 252]: #Clock and Date
                    for tool in [0xB6]: #or 0xAC
                        j1587_request = bytes([0x03, tool, 0, pid])
                        self.RP1210.send_message(self.client_ids["J1708"], j1587_request)
        except (KeyError, AttributeError):
            pass
        
        # Request Time and Date
        try:
            if self.client_ids["J1939"] is not None: 
                self.send_j1939_request(65254)
        except (KeyError, AttributeError):
            pass

        #return True if any connection is present.
        for key, val in network_connection.items():
            if val: 
                return True
        return False

    def get_hardware_status_ex(self):
        """
        Show a dialog box for valid connections for the extended get hardware status command implemented in the 
        vendor's RP1210 DLL.
        """
        logger.debug("get_hardware_status_ex")
        for protocol,nClientID in self.client_ids.items():
            if nClientID is not None:
                self.RP1210.get_hardware_status_ex(nClientID)
                return
        QMessageBox.warning(self, 
                    "Connection Not Present",
                    "There were no Client IDs for an RP1210 device that support the extended hardware status command.",
                    QMessageBox.Cancel,
                    QMessageBox.Cancel)

    def get_hardware_status(self):
        """
        Show a dialog box for valid connections for the regular get hardware status command implemented in the 
        vendor's RP1210 DLL.
        """
        logger.debug("get_hardware_status")
        for protocol,nClientID in self.client_ids.items():
            if nClientID is not None:
                self.RP1210.get_hardware_status(nClientID)
                return
        QMessageBox.warning(self, 
                    "Connection Not Present",
                    "There were no Client IDs for an RP1210 device that support the hardware status command.",
                    QMessageBox.Cancel,
                    QMessageBox.Cancel)
                
    def display_detailed_version(self):
        """
        Show a dialog box for valid connections for the detailed version command implemented in the 
        vendor's RP1210 DLL.
        """
        logger.debug("display_detailed_version")
        for protocol, nClientID in self.client_ids.items():
            if nClientID is not None:
                self.RP1210.display_detailed_version(nClientID)
                return
        # otherwise show a dialog that there are no client IDs
        QMessageBox.warning(self, 
                    "Connection Not Present",
                    "There were no Client IDs for an RP1210 device.",
                    QMessageBox.Cancel,
                    QMessageBox.Cancel)
    
    def display_version(self):
        """
        Show a dialog box for valid connections for the extended get hardware status command implemented in the 
        vendor's RP1210 DLL. This does not require connection to a device, just a valid RP1210 DLL.
        """
        logger.debug("display_version")
        self.RP1210.display_version()

    def disconnectRP1210(self):
        """
        Close all the RP1210 read message threads and disconnect the client.
        """
        logger.debug("disconnectRP1210")
        for protocol, nClientID in self.client_ids.items():
            try:
                self.read_message_threads[protocol].runSignal = False
                del self.read_message_threads[protocol]
            except KeyError:
                pass
            self.client_ids[protocol] = None
        for n in range(128):
            try:
                self.RP1210.ClientDisconnect(n)
            except:
                pass
        logger.debug("RP1210.ClientDisconnect() Finished.")

    def get_iso_parameters(self, additional_params=[]):
        """
        Get Parameters defined in ISO 14229-1 Annex C.
        Additional 2-byte parameters can be passed in as a list.
        Returns a dictionary sieht the 2-byte request parameters as the key and the data as the value.
        """
        container = {}
        data_page_numbers = [[0xf1, b] for b in range(0x80,0x9F)]
        # There are 33 of these. We should move them to a JSON file and have dictionary that we can reference.
        for i in range(len(data_page_numbers)):
            QCoreApplication.processEvents()
            progress_message = "Requesting ISO Data Element 0x{:02X}{:02X}".format(data_page_numbers[i][0],data_page_numbers[i][1])
            logger.info(progress_message)
            message_bytes = bytes(data_page_numbers[i])
            data = self.isodriver.uds_read_data_by_id(message_bytes)
            logger.debug(data)
            container[bytes_to_hex_string(message_bytes)] = data
        return container

   
    def send_can_message(self, data_bytes):
        #initialize the buffer
        if self.client_ids["CAN"] is not None:
            message_bytes = b'\x01'
            message_bytes += data_bytes
            self.RP1210.send_message(self.client_ids["CAN"], message_bytes)

    def send_j1939_message(self, PGN, data_bytes, DA=0xff, SA=0xf9, priority=6, BAM=True):
        #initialize the buffer
        if self.client_ids["J1939"] is not None:
            b0 =  PGN & 0xff
            b1 = (PGN & 0xff00) >> 8
            b2 = (PGN & 0xff0000) >> 16
            if BAM and len(data_bytes) > 8:
                priority |= 0x80
            message_bytes = bytes([b0, b1, b2, priority, SA, DA])
            message_bytes += data_bytes
            self.RP1210.send_message(self.client_ids["J1939"], message_bytes)
    
    def find_j1939_data(self, pgn, sa=0):
        '''
        A function that returns bytes data from the data dictionary holding J1939 data.
        This function is used to check the presence of data in the dictionary.
        '''
        
        try:
            return self.J1939.j1939_unique_ids[repr((pgn,sa))]["Bytes"]
        except KeyError:
            # With "Expand Multiplexed PGNs", a multiplexed PGN has one row per selector:
            # return the most recent of them.
            rows = [r for r in self.J1939.j1939_unique_ids.values()
                    if r.get("PGN", "").strip() == str(pgn) and r.get("SA", "").strip() == str(sa)]
            if rows:
                return max(rows, key=lambda r: r.get("Message Time", 0))["Bytes"]
            return False
          

    def send_j1939_request(self, PGN_to_request, DA=0xff, SA=0xf9): 
        if self.client_ids["J1939"] is not None:
            b0 =  PGN_to_request & 0xff
            b1 = (PGN_to_request & 0xff00) >> 8
            b2 = (PGN_to_request & 0xff0000) >> 16
            message_bytes = bytes([0x00, 0xEA, 0x00, 0x06, SA, DA, b0, b1, b2])
            self.RP1210.send_message(self.client_ids["J1939"], message_bytes)

    def send_j1587_request(self, pid, tool = 0xB6): 
        if self.client_ids["J1708"] is not None:
            if pid < 255:
                j1587_request = bytes([0x03, tool, 0, pid])
            elif pid > 256 and pid < 65535:
                j1587_request = bytes([0x04, tool, 255, 0, pid%256])
            else:
                return 
            self.RP1210.send_message(self.client_ids["J1708"], j1587_request)    
   

    def read_rp1210(self):
        # This function needs to run often to keep the queues from filling
        #try:
        for protocol in self.rx_queues.keys():
            if protocol in self.rx_queues:
                start_time = time.time()
                while self.rx_queues[protocol].qsize():
                    #Get a message from the queue. These are raw bytes
                    #if not protocol == "J1708":
                    rxmessage = self.rx_queues[protocol].get() 
                    # logger.debug(rxmessage)                                  
                    if protocol == "J1939" or protocol == "Logger" :
                        try:
                            self.J1939.fill_j1939_table(rxmessage)
                        except:
                            logger.debug(traceback.format_exc())
                    elif protocol == "J1708":
                        try:
                            self.J1587.fill_j1587_table(rxmessage)    
                            #J1708logger.info("{:0.6f},".format(rxmessage[0]) + ",".join("{:02X}".format(c) for c in rxmessage[1]))
                        except:
                            logger.debug(traceback.format_exc())
                    
                    if time.time() - start_time + .020 > self.update_rate: #give some time to process events
                        logger.debug("Can't keep up with messages.")
                        return
        
    def show_about_dialog(self):
        logger.debug("show_about_dialog Request")
        msg = QMessageBox(self)
        msg.setIconPixmap(app_icons.icon("app").pixmap(64, 64))
        msg.setText("About CSU-RP1210")
        version = "{}.{}.{}".format(CSU_RP1210_version.get("major", 0), CSU_RP1210_version.get("minor", 0),
                                    CSU_RP1210_version.get("patch", 0))
        msg.setInformativeText("Version {}\nHeavy vehicle network diagnostics: J1939, J1587, RP1210 and CAN FD.\n\n"
                               "Help > Keyboard Shortcuts (Ctrl+/) lists every shortcut.".format(version))
        msg.setWindowTitle("About")
        j1939_meta, j1587_meta = self.j1939db.get("_meta", {}), self.j1587db.get("_meta", {})
        msg.setDetailedText(
            "J1939 database: {}\nJ1587 database: {}\n\nThe icons are original artwork of this project "
            "(icons/, tools/make_icons.py) under its license.".format(
                "skeleton" if j1939_meta.get("skeleton") else "licensed, {} units".format(j1939_meta.get("units", "?")),
                "skeleton" if j1587_meta.get("skeleton") else "licensed, {} units".format(j1587_meta.get("units", "?"))))
        msg.setStandardButtons(QMessageBox.Ok)
        msg.setWindowModality(Qt.ApplicationModal)
        msg.exec_()



    def get_address_from_gps(self):
        if self.GPS.lat and self.GPS.lon:
            try:
                geolocator = Nominatim()
                loc = geolocator.reverse((self.lat, self.lon), timeout=5, language='en-US')
                gps_location_string = loc.address
                self.general_location_gps = gps_location_string
            except GeopyError:
                pass  

    def close_clients(self):
        logger.debug("close_clients Request")
        for protocol,nClientID in self.client_ids.items():
            logger.debug("Closing {}".format(protocol))
            self.RP1210.disconnectRP1210(nClientID)
            if protocol in self.read_message_threads:
                self.read_message_threads[protocol].runSignal = False
        try:
            self.GPS.ser.close()
        except:
            pass
        
        logger.debug("Exiting.")

    def decode_can_log_file(self, filename):
        pass

if __name__ == '__main__':

    app = QApplication(sys.argv)
    app.setWindowIcon(app_icons.icon("app"))
    # Immediate feedback while the databases load and the window is built.
    splash = app_icons.show_splash("{}.{}.{}".format(CSU_RP1210_version.get("major", 0),
                                                     CSU_RP1210_version.get("minor", 0),
                                                     CSU_RP1210_version.get("patch", 0)))
    execute = CSU_RP1210(splash)
    sys.exit(app.exec_())