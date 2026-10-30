
from PyQt5.QtWidgets import (QMainWindow,
                             QWidget,
                             QTreeWidget,
                             QTreeWidgetItem,
                             QMessageBox,
                             QFileDialog,
                             QLabel,
                             QSlider,
                             QCheckBox,
                             QLineEdit,
                             QVBoxLayout,
                             QHBoxLayout,
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
                             QTreeWidgetItemIterator,
                             QTabWidget)
from PyQt5.QtCore import Qt, QTimer, QAbstractTableModel, QCoreApplication, QVariant, QAbstractItemModel, QSortFilterProxyModel
from PyQt5.QtGui import QIcon, QFont, QKeySequence
from PyQt5.QtWidgets import QShortcut

import time
import random
import traceback
from collections import OrderedDict
from RP1210Functions import *
from TableModel.TableModel import *

import logging
logger = logging.getLogger(__name__)

class ComponentInfoTab(QWidget):
    def __init__(self,parent,tabs):
        super(ComponentInfoTab,self).__init__()
        self.root = parent
        self.tabs = tabs
        self.h1_font = QFont()
        self.h1_font.setBold(True)
        self.h1_font.setPointSize(18)
        self.h2_font = QFont()
        self.h2_font.setBold(False)
        self.h2_font.setPointSize(14)
        self.h3_font = QFont()
        self.h3_font.setBold(True)
        #self.h2_font.setPointSize(15)
        self.init_ui()

    def init_ui(self):
        logger.debug("Setting up Component Information Tab.")
        self.component_tab = QScrollArea()
        self.tabs.addTab(self.component_tab, "Component Information")
        outer_layout = QVBoxLayout()
        self.component_tab.setLayout(outer_layout)
        self.tab_layout = QHBoxLayout()
        outer_layout.addLayout(self.tab_layout, 3)

        # J1939-81 Address Claimed (PGN 60928): each device's NAME. A claim overrides the
        # preferred-address meaning of its source address in the J1939 tables.
        claims_box = QGroupBox("J1939 Address Claims (PGN 60928)")
        claims_layout = QVBoxLayout()
        self.claims_table = QTableWidget()
        self.claims_table.setSortingEnabled(True)
        self.claims_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.claims_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        claims_layout.addWidget(self.claims_table)
        claims_box.setLayout(claims_layout)
        outer_layout.addWidget(claims_box, 2)
        
        component_button_box = QGroupBox("Request Buttons")
        component_button_layout = QVBoxLayout()
        component_button_layout.setAlignment(Qt.AlignTop)
        component_button_box.setLayout(component_button_layout)
        self.tab_layout.addWidget(component_button_box)

        get_vin_button = QPushButton("Request &VIN")
        get_vin_button.clicked.connect(self.request_VIN)
        component_button_layout.addWidget(get_vin_button)
        
        get_component_ID_button = QPushButton("Request &Component ID")
        get_component_ID_button.clicked.connect(self.request_component_ID)
        component_button_layout.addWidget(get_component_ID_button)
        
        software_ID_button = QPushButton("Request &Software ID")
        software_ID_button.clicked.connect(self.request_software)
        component_button_layout.addWidget(software_ID_button)

        distance_button = QPushButton("Request ECU &Distances")
        distance_button.clicked.connect(self.request_distance)
        component_button_layout.addWidget(distance_button)

        hours_button = QPushButton("Request ECU H&ours")
        hours_button.clicked.connect(self.request_hours)
        component_button_layout.addWidget(hours_button)
                
        claims_button = QPushButton("Request &Address Claims")
        claims_button.setToolTip("Send a global request for Address Claimed (PGN 60928). Every J1939 device "
                                 "answers with its NAME (industry group, function, manufacturer).")
        claims_button.clicked.connect(self.request_address_claims)
        component_button_layout.addWidget(claims_button)

        refresh_button = QPushButton("R&efresh Data")
        refresh_button.clicked.connect(self.rebuild_trees)
        refresh_button.setToolTip("Refresh the component information (F5)")
        # F5 refreshes while this tab is shown (a shortcut on a hidden tab does not fire).
        QShortcut(QKeySequence("F5"), self.component_tab, activated=self.rebuild_trees)
        component_button_layout.addWidget(refresh_button)
 

        self.tabs.currentChanged.connect(self.rebuild_trees)

        self.component_tree = QTreeWidget()
        self.component_tree.setHeaderLabels(["Item","Value"])
        self.tab_layout.addWidget(self.component_tree)
        
        self.realtime_tree = QTreeWidget()
        self.realtime_tree.setHeaderLabels(["Item","Value"])
        self.tab_layout.addWidget(self.realtime_tree)

    def fill_item(self, item, value, tree):
        item.setExpanded(True)
        if type(value) is dict:
            for key, val in sorted(value.items()): 
                if type(val) is dict:
                    child = QTreeWidgetItem()
                    child.setText(0, str(key))
                    child.setFont(0, self.h2_font)
                    if val:
                        item.addChild(child)
                        tree.setFirstItemColumnSpanned(child,True)
                        self.fill_item(child, val, tree)
                else:
                    child = QTreeWidgetItem()
                    #child.setText(0, str(key) + ": " + self.get_display_value(key, val))
                    child.setText(0, str(key))
                    child.setFont(0, self.h3_font)
                    child.setText(1, self.get_display_value(key, val))
                    if val: #Add only if it is not an empty dictionary. Empty dictionaries are False.
                        item.addChild(child)
                        self.fill_item(child, val, tree)
    
    def get_display_value(self, key, val):
        """
        Format the time stamp to be human readable, but still have the UNIX timestamp.
        """
        try:
            if "PC Time minus" in key:
                display_val = "{:0.3f} seconds".format(val)
            elif "PC Start Time" in key:
                display_val = get_local_time_string(int(val)) + " ({:d} seconds)".format(int(val))
            elif "Permission Time" in key:
                display_val = get_local_time_string(int(val)) + " ({:d} seconds)".format(int(val))
            elif "PC Time" in key:
                display_val = get_local_time_string(int(val)) + " ({:d} seconds)".format(int(val))
            elif "GPS Time" in key:
                display_val = get_local_time_string(int(val)) + " ({:d} seconds)".format(int(val))
            elif "ECM Time" in key:
                display_val = get_local_time_string(int(val)) + " ({:d} seconds)".format(int(val))
            else: 
                display_val = str(val)
        except TypeError:
            display_val = "None"
        return display_val

    def rebuild_trees(self): 

        self.component_tree.clear()
        tree_root = self.component_tree.invisibleRootItem()
        component_branch = QTreeWidgetItem()
        tree_root.addChild(component_branch)
        component_branch.setText(0, "Component Information")
        component_branch.setFont(0, self.h1_font)
        self.component_tree.setFirstItemColumnSpanned(component_branch,True)
        self.fill_item(component_branch, self.root.data_package["Component Information"],self.component_tree)
        
        distance_branch = QTreeWidgetItem()
        tree_root.addChild(distance_branch)
        distance_branch.setText(0, "Distance Data")
        distance_branch.setFont(0, self.h1_font)
        self.component_tree.setFirstItemColumnSpanned(distance_branch,True)
        self.fill_item(distance_branch, self.root.data_package["Distance Information"],self.component_tree)
        
        time_branch = QTreeWidgetItem()
        tree_root.addChild(time_branch)
        time_branch.setText(0,"ECU Time Data")
        time_branch.setFont(0, self.h1_font)
        self.component_tree.setFirstItemColumnSpanned(time_branch,True)
        self.fill_item(time_branch, self.root.data_package["ECU Time Information"],self.component_tree)  

        log_branch = QTreeWidgetItem()
        tree_root.addChild(log_branch)
        log_branch.setText(0,"Session Log Data")
        log_branch.setFont(0, self.h1_font)
        self.component_tree.setFirstItemColumnSpanned(log_branch,True)
        

        # New widget
        self.realtime_tree.clear()
        realtime_root = self.realtime_tree.invisibleRootItem()
        realtime_branch = QTreeWidgetItem()
        realtime_root.addChild(realtime_branch)
        realtime_branch.setText(0, "Real Time Data")
        realtime_branch.setFont(0, self.h1_font)
        self.realtime_tree.setFirstItemColumnSpanned(realtime_branch,True)
        self.fill_item(realtime_branch, self.root.data_package["Time Records"],self.realtime_tree)  

        event_branch = QTreeWidgetItem()
        realtime_root.addChild(event_branch)
        event_branch.setText(0, "Event Data")
        event_branch.setFont(0, self.h1_font)
        self.realtime_tree.setFirstItemColumnSpanned(event_branch,True)
        self.fill_item(event_branch, self.root.data_package["Event Data"],self.realtime_tree)  
        
        self.component_tree.resizeColumnToContents(0)
        self.realtime_tree.resizeColumnToContents(0)
        self.component_tree.resizeColumnToContents(1)
        self.realtime_tree.resizeColumnToContents(1)
        self.fill_claims_table()

    def fill_claims_table(self):
        claims = self.root.data_package.get("Address Claims", {})
        columns = ["Source Address", "Claimed As", "Industry Group", "Vehicle System", "Vehicle System Instance",
                   "Function", "Function Instance", "ECU Instance", "Manufacturer", "Identity Number",
                   "Arbitrary Address Capable", "NAME"]
        self.claims_table.setSortingEnabled(False)
        self.claims_table.clear()
        self.claims_table.setColumnCount(len(columns))
        self.claims_table.setHorizontalHeaderLabels(columns)
        self.claims_table.setRowCount(len(claims))
        for row, (key, claim) in enumerate(sorted(claims.items())):
            for col, name in enumerate(columns):
                item = QTableWidgetItem()
                value = claim.get(name, "")
                # Numbers sort numerically.
                item.setData(Qt.DisplayRole, value if isinstance(value, int) else str(value))
                self.claims_table.setItem(row, col, item)
        self.claims_table.setSortingEnabled(True)
        self.claims_table.resizeColumnsToContents()

    def request_address_claims(self):
        self.root.send_j1939_request(60928, 0xFF)
        # Devices answer within a few hundred milliseconds.
        QTimer.singleShot(1500, self.rebuild_trees)

    def request_VIN(self):
        self.send_requests(65260, 237)
        self.rebuild_trees()
    
    def request_hours(self):
        self.send_requests(65253, 247)
        self.send_requests(65255, 247)
        

    def request_distance(self):
        self.send_requests(65248, 245)
        self.send_requests(65217, 244)
        

    def request_software(self):
        self.send_requests(65242, 234)
        

    def request_component_ID(self):
        self.send_requests(65259, 243)

    def send_requests(self, pgn, mid=234):
        total_requests = 3*(len(self.root.source_addresses) + 1)
        progress = QProgressDialog(self)
        progress.setMinimumWidth(600)
        progress.setWindowTitle("Requesting Vehicle Network Messages")
        progress.setMinimumDuration(0)
        #progress.setWindowModality(Qt.WindowModal) # Improves stability of program
        progress.setModal(False) 
        progress.setMaximum(total_requests)
        request_count = 1
        for i in range(3):
            self.root.send_j1587_request(mid)
            sa_list = [0xff] + self.root.source_addresses
            random.shuffle(sa_list)
            for sa in sa_list:

                if self.root.find_j1939_data(pgn,sa):
                    break
                if progress.wasCanceled():
                    break
                self.root.send_j1939_request(pgn, sa)
                time.sleep(0.1 + 0.1 * random.random())
                request_count += 1
                progress.setValue(request_count)
                QCoreApplication.processEvents()

        progress.deleteLater()             
        self.rebuild_trees()


