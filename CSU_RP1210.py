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
from PyQt5.QtGui import QIcon

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
from ISO15765 import *

if sys.maxsize > 2**32:
    print("Must run on 32-bit Python.")
    sys.exit()

import logging
logger.addHandler(logging.StreamHandler(sys.stdout))
logger.setLevel(logging.DEBUG)

module_directory = os.getcwd()

try:
    with open('version.json') as f:
        CSU_RP1210_version = json.load(f)
except:
    print("This is a module that should be run from another program. See the demo code.")

class CSU_RP1210(QMainWindow):
    def __init__(self):
        super(CSU_RP1210,self).__init__()
        
        self.setWindowTitle("CSU RP1210")
        
        progress = QProgressDialog(self)
        progress.setMinimumWidth(600)
        progress.setWindowTitle("Starting Application")
        progress.setMinimumDuration(0)
        progress.setWindowModality(Qt.WindowModal)
        progress.setMaximum(10)
        progress_label = QLabel("Loading the J1939 Database")
        #load the J1939 Database
        progress.setLabel(progress_label)
        try:
            with open("J1939db.json",'r') as j1939_file:
                self.j1939db = json.load(j1939_file) 
        except FileNotFoundError:
            try:
                with open(os.path.join(module_directory,"J1939db.json"),'r') as j1939_file:
                    self.j1939db = json.load(j1939_file) 
            except FileNotFoundError: 
                # Make a data structure to do something anyways
                logger.debug("J1939db.json file was not found.")
                self.j1939db = {"J1939BitDecodings":{},
                                "J1939FMITabledb": {},
                            "J1939LampFlashTabledb": {},
                            "J1939OBDTabledb": {},
                            "J1939PGNdb": {},
                            "J1939SAHWTabledb": {},
                            "J1939SATabledb": {},
                            "J1939SPNdb": {} }
        logger.info("Done Loading J1939db")
        progress.setValue(1)
        QCoreApplication.processEvents()

        self.rx_queues = {}
        self.tx_queues = {}
        progress_label.setText("Loading the J1587 Database")
        try:
            with open(os.path.join(module_directory,"J1587db.json"),'r') as j1587_file:
                self.j1587db = json.load(j1587_file)
        except FileNotFoundError:
            logger.debug("J1587db.json file was not found.")
            self.j1587db = { "FMI": {},
                             "MID": {},
                             "MIDAlias": {},
                             "PID": {"168":{"BitResolution" : 0.05,
                                            "Category" : "live",
                                            "DataForm" : "a a",
                                            "DataLength" : 2,
                                            "DataType" : "Unsigned Integer",
                                            "FormatStr" : "%0.2f",
                                            "Maximum" : 3276.75,
                                            "Minimum" : 0.0,
                                            "Name" : "Battery Potential (Voltage)",
                                            "Period" : "1",
                                            "Priority" : 5,
                                            "Unit" : "volts"},
                                    "245" : { "BitResolution" : 0.1,
                                              "Category" : "hist",
                                              "DataForm" : "n a a a a",
                                              "DataLength" : 4,
                                              "DataType" : "Unsigned Long Integer",
                                              "FormatStr" : "%0.1f",
                                              "Maximum" : 429496729.5,
                                              "Minimum" : 0.0,
                                              "Name" : "Total Vehicle Distance",
                                              "Period" : "10",
                                              "Priority" : 7,
                                              "Unit" : "miles"},
                                    "247" : {
                                        "BitResolution" : 0.05,
                                        "Category" : "hist",
                                        "DataForm" : "n a a a a",
                                        "DataLength" : 4,
                                        "DataType" : "Unsigned Long Integer",
                                        "FormatStr" : "%0.2f",
                                        "Maximum" : 214748364.8,
                                        "Minimum" : 0.0,
                                        "Name" : "Total Engine Hours",
                                        "Period" : "On request",
                                        "Priority" : 8,
                                        "Unit" : "hours"}
                                    },
                             "PIDNames": {},
                             "SID": {} }
        logger.info("Done Loading J1587db")
        progress.setValue(2)
        QCoreApplication.processEvents()
        

        progress_label.setText("Initializing System Variables")
        os.system("TASKKILL /F /IM DGServer2.exe")
        os.system("TASKKILL /F /IM DGServer1.exe")  
        
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
        progress.setValue(3)
        QCoreApplication.processEvents()

        progress_label.setText("Setting Up the Graphical Interface")
        self.init_ui()
        logger.debug("Done Setting Up User Interface.")
        progress.setValue(4)
        QCoreApplication.processEvents()

        progress_label.setText("Setting up the RP1210 Interface")
        self.selectRP1210(automatic=True)
        logger.debug("Done selecting RP1210.")
        progress.setValue(5)
        QCoreApplication.processEvents()

        progress_label.setText("Initializing a New Document")
        self.create_new(False)
        progress.setValue(6)
        QCoreApplication.processEvents()


        progress_label.setText("Starting Loop Timers")
        connections_timer = QTimer(self)
        connections_timer.timeout.connect(self.check_connections)
        connections_timer.start(1003) #milliseconds

        read_timer = QTimer(self)
        read_timer.timeout.connect(self.read_rp1210)
        read_timer.start(self.update_rate) #milliseconds

        progress.setValue(10)
        QCoreApplication.processEvents()

    def init_ui(self):
        # Builds GUI
        # Start with a status bar
        self.statusBar().showMessage("Welcome!")

        self.grid_layout = QGridLayout()
        
        # Build common menu options
        menubar = self.menuBar()

        # File Menu Items
        file_menu = menubar.addMenu('&File')
        open_logger2 = QAction(QIcon(os.path.join(module_directory,r'icons/logger2_48px.png')), '&Import CAN Logger 2', self)
        open_logger2.setShortcut('Ctrl+I')
        open_logger2.setStatusTip('Open a file from the NMFTA/TU CAN Logger 2')
        open_logger2.triggered.connect(self.open_open_logger2)
        file_menu.addAction(open_logger2)


        exit_action = QAction(QIcon(os.path.join(module_directory,r'icons/icons8_Close_Window_48px.png')), '&Quit', self)
        exit_action.setShortcut('Ctrl+Q')
        exit_action.setStatusTip('Exit the program.')
        exit_action.triggered.connect(self.confirm_quit)
        file_menu.addSeparator()
        file_menu.addAction(exit_action)
        
        #build the entries in the dockable tool bar
        file_toolbar = self.addToolBar("File")
        file_toolbar.addAction(exit_action)
        
        # RP1210 Menu Items
        self.rp1210_menu = menubar.addMenu('&RP1210')
        
        help_menu = menubar.addMenu('&Help')
        
        about = QAction(QIcon(os.path.join(module_directory,r'icons/icons8_Help_48px.png')), 'A&bout', self)
        about.setShortcut('F1')
        about.setStatusTip('Display a dialog box with information about the program.')
        about.triggered.connect(self.show_about_dialog)
        help_menu.addAction(about)
        
        help_toolbar = self.addToolBar("Help")
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
            self.status_icon[key] = QLabel("<html><img src='{}/icons/icons8_Unavailable_48px.png'><br>Network<br>Unavailable</html>".format(module_directory))
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

    def setup_RP1210_menus(self):
        connect_rp1210 = QAction(QIcon(os.path.join(module_directory,r'icons/icons8_Connected_48px.png')), '&Client Connect', self)
        connect_rp1210.setShortcut('Ctrl+Shift+C')
        connect_rp1210.setStatusTip('Connect Vehicle Diagnostic Adapter')
        connect_rp1210.triggered.connect(self.selectRP1210)
        self.rp1210_menu.addAction(connect_rp1210)

        rp1210_version = QAction(QIcon(os.path.join(module_directory,r'icons/icons8_Versions_48px.png')), '&Driver Version', self)
        rp1210_version.setShortcut('Ctrl+Shift+V')
        rp1210_version.setStatusTip('Show Vehicle Diagnostic Adapter Driver Version Information')
        rp1210_version.triggered.connect(self.display_version)
        self.rp1210_menu.addAction(rp1210_version)

        rp1210_detailed_version = QAction(QIcon(os.path.join(module_directory,r'icons/icons8_More_Details_48px.png')), 'De&tailed Version', self)
        rp1210_detailed_version.setShortcut('Ctrl+Shift+T')
        rp1210_detailed_version.setStatusTip('Show Vehicle Diagnostic Adapter Detailed Version Information')
        rp1210_detailed_version.triggered.connect(self.display_detailed_version)
        self.rp1210_menu.addAction(rp1210_detailed_version)

        rp1210_get_hardware_status = QAction(QIcon(os.path.join(module_directory,r'icons/icons8_Steam_48px.png')), 'Get &Hardware Status', self)
        rp1210_get_hardware_status.setShortcut('Ctrl+Shift+H')
        rp1210_get_hardware_status.setStatusTip('Determine details regarding the hardware interface status and its connections.')
        rp1210_get_hardware_status.triggered.connect(self.get_hardware_status)
        self.rp1210_menu.addAction(rp1210_get_hardware_status)

        rp1210_get_hardware_status_ex = QAction(QIcon(os.path.join(module_directory,r'icons/icons8_System_Information_48px.png')), 'Get &Extended Hardware Status', self)
        rp1210_get_hardware_status_ex.setShortcut('Ctrl+Shift+E')
        rp1210_get_hardware_status_ex.setStatusTip('Determine the hardware interface status and whether the VDA device is physically connected.')
        rp1210_get_hardware_status_ex.triggered.connect(self.get_hardware_status_ex)
        self.rp1210_menu.addAction(rp1210_get_hardware_status_ex)

        disconnect_rp1210 = QAction(QIcon(os.path.join(module_directory,r'icons/icons8_Disconnected_48px.png')), 'Client &Disconnect', self)
        disconnect_rp1210.setShortcut('Ctrl+Shift+D')
        disconnect_rp1210.setStatusTip('Disconnect all RP1210 Clients')
        disconnect_rp1210.triggered.connect(self.disconnectRP1210)
        self.rp1210_menu.addAction(disconnect_rp1210)

        self.RP1210_toolbar = self.addToolBar("RP1210")
        self.RP1210_toolbar.addAction(connect_rp1210)
        self.RP1210_toolbar.addAction(rp1210_version)
        self.RP1210_toolbar.addAction(rp1210_detailed_version)
        self.RP1210_toolbar.addAction(rp1210_get_hardware_status)
        self.RP1210_toolbar.addAction(rp1210_get_hardware_status_ex)
        self.RP1210_toolbar.addAction(disconnect_rp1210)

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
                        self.rx_queues["Logger"].put((timestamp, rp1210_message))
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
        self.client_ids["CAN"] = self.RP1210.get_client_id("CAN", deviceID, "{}".format(speed))
        progress.setValue(1)
        self.client_ids["J1708"] = self.RP1210.get_client_id("J1708", deviceID, "Auto")
        progress.setValue(2)
        self.client_ids["J1939"] = self.RP1210.get_client_id("J1939", deviceID, "{}".format(speed))
        progress.setValue(3)
        #self.client_ids["ISO15765"] = self.RP1210.get_client_id("ISO15765", deviceID, "Auto")
        #progress.setValue(3)
        
        logger.debug('Client IDs: {}'.format(self.client_ids))

        # If there is a successful connection, save it.
        file_contents={ "dll_name":dll_name,
                        "protocol":protocol,
                        "deviceID":deviceID,
                        "speed":speed
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
                    self.read_message_threads[protocol].setDaemon(True) #needed to close the thread when the application closes.
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
        
        if self.client_ids["J1939"] is None or self.client_ids["J1708"] is None:
            QMessageBox.information(self,"RP1210 Client Not Connected.","The default RP1210 Device was not found or is unplugged. Please reconnect your Vehicle Diagnostic Adapter (VDA) and select the RP1210 device to use.")
        progress.deleteLater()

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
                self.message_duration_label[key].setText("<html><img src='{}/icons/icons8_Connected_48px.png'><br>Client Connected<br>{:0.0f} sec.</html>".format(module_directory, duration))
                network_connection[key] = True
            except (KeyError, AttributeError) as e:
                current_count = 0
                duration = 0
                self.message_duration_label[key].setText("<html><img src='{}/icons/icons8_Disconnected_48px.png'><br>Client Disconnected<br>{:0.0f} sec.</html>".format(module_directory, duration))
                
            count_change = current_count - self.previous_count[key]
            self.previous_count[key] = current_count
            # See if messages come in. Change the 
            if count_change > 0 and not self.network_connected[key]: 
                self.status_icon[key].setText("<html><img src='{}/icons/icons8_Ok_48px.png'><br>Network<br>Online</html>".format(module_directory))
                self.network_connected[key] = True
            elif count_change == 0 and self.network_connected[key]:             
                self.status_icon[key].setText("<html><img src='{}/icons/icons8_Unavailable_48px.png'><br>Network<br>Unavailable</html>".format(module_directory))
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
        msg = QMessageBox()
        msg.setIcon(QMessageBox.Information)
        msg.setText("About CSU-RP1210")
        msg.setInformativeText("""Icons by Icons8\nhttps://icons8.com/""")
        msg.setWindowTitle("About")
        msg.setDetailedText("There will be some details here.")
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
    execute = CSU_RP1210()
    sys.exit(app.exec_())