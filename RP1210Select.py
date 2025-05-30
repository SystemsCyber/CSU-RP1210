from PyQt5.QtWidgets import (QApplication,
                             QMainWindow,
                             QMessageBox,
                             QWidget,
                             QComboBox,
                             QLabel,
                             QDialog,
                             QDialogButtonBox,
                             QVBoxLayout,
                             QErrorMessage
                             )
from PyQt5.QtCore import Qt, QCoreApplication
import sys
import os
import json
import configparser
import traceback
import logging
from RP1210 import get_storage_path
logger = logging.getLogger(__name__)

class SelectRP1210(QDialog):
    """
    A Qt dialog box that parses the RP1210 ini files to enable a user to select the RP1210 device. 
    """
    def __init__(self,title):
        super(SelectRP1210,self).__init__()
        RP1210_config = configparser.ConfigParser()
        try:
            RP1210_config.read(os.path.join(os.environ["WINDIR"],"RP121032.ini"))
            self.apis = sorted(RP1210_config["RP1210Support"]["apiimplementations"].split(","))
            self.current_api_index = 0
            logger.debug("Current RP1210 APIs installed are: " + ", ".join(self.apis))
            self.rp1201_missing = False
        except:
            logger.warning(traceback.format_exc())
            QMessageBox.warning(self,"No RP1210 Device","The RP121032.ini file was not found. Please install an RP1210 compliant Vehicle Diagnostics adatper.")
            self.rp1201_missing = True
            return
        storage = get_storage_path()
        self.selection_filename = os.path.join(storage,"RP1210_selection.txt")
        logger.debug(f"selection_filename path: {self.selection_filename}")
        self.connections_file = os.path.join(storage,"Last_RP1210_Connection.json")
        logger.debug(f"connections_file path: {self.connections_file}")
        
        self.setup_dialog()
        self.setWindowTitle("Select RP1210")
        self.setWindowModality(Qt.ApplicationModal)
        logger.debug("Looking for RP1210 Settings in {}".format(self.connections_file))
        try:
            with open(self.connections_file,"r") as rp1210_file:
                file_contents = json.load(rp1210_file)
            self.dll_name = file_contents["dll_name"]
            self.protocol = file_contents["protocol"]
            self.deviceID = file_contents["deviceID"]
            self.speed    = file_contents["speed"]
        except:
            logger.warning(traceback.format_exc())
            self.dll_name = False
            self.protocol = False
            self.deviceID = False
            self.speed = False
        
    
    def show_dialog(self):
        self.exec_()

    def setup_dialog(self):
        if self.rp1201_missing:
            return
        vendor_label = QLabel("System RP1210 Vendors:")
        self.vendor_combo_box = QComboBox()
        self.vendor_combo_box.setInsertPolicy(QComboBox.NoInsert)
        self.vendor_combo_box.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        self.vendor_combo_box.activated.connect(self.fill_device)

        device_label = QLabel("Available RP1210 Vendor Devices:")
        self.device_combo_box = QComboBox()
        self.device_combo_box.setInsertPolicy(QComboBox.NoInsert)
        self.device_combo_box.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        self.device_combo_box.activated.connect(self.fill_protocol)

        protocol_label = QLabel("Available Device Protocols:")
        self.protocol_combo_box = QComboBox()
        self.protocol_combo_box.setInsertPolicy(QComboBox.NoInsert)
        self.protocol_combo_box.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        self.protocol_combo_box.activated.connect(self.fill_speed)

        speed_label = QLabel("Available Speed Settings")
        self.speed_combo_box = QComboBox()
        self.speed_combo_box.setInsertPolicy(QComboBox.NoInsert)
        self.speed_combo_box.setSizeAdjustPolicy(QComboBox.AdjustToContents)


        self.buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel,
            Qt.Horizontal, self)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)

        self.accepted.connect(self.connect_RP1210)
        self.rejected.connect(self.reject_RP1210)

        try:
            with open(self.selection_filename,"r") as selection_file:
                previous_selections = selection_file.read()
        except FileNotFoundError:
            logger.debug("{} not Found!".format(self.selection_filename))
            previous_selections = "0,0,0"
        self.selection_index = previous_selections.split(',')

        self.fill_vendor()

        self.v_layout = QVBoxLayout()
        self.v_layout.addWidget(vendor_label)
        self.v_layout.addWidget(self.vendor_combo_box)
        self.v_layout.addWidget(device_label)
        self.v_layout.addWidget(self.device_combo_box)
        self.v_layout.addWidget(protocol_label)
        self.v_layout.addWidget(self.protocol_combo_box)
        self.v_layout.addWidget(speed_label)
        self.v_layout.addWidget(self.speed_combo_box)
        self.v_layout.addWidget(self.buttons)

        self.setLayout(self.v_layout)

    def fill_vendor(self):
        if self.rp1201_missing:
            return
        self.vendor_combo_box.clear()
        self.vendor_configs = {}
        for api_string in self.apis:
            self.vendor_configs[api_string] = configparser.ConfigParser()
            try:
                self.vendor_configs[api_string].read(os.path.join(os.environ["WINDIR"],api_string + ".ini"))
                #logger.debug("api_string = {}".format(api_string))
                #logger.debug("The api ini file has the following sections:")
                #logger.debug(vendor_config.sections())
                vendor_name = self.vendor_configs[api_string]['VendorInformation']['name']
                #logger.debug(vendor_name)
                if vendor_name is not None:
                    vendor_combo_box_entry = "{:8} - {}".format(api_string,vendor_name)
                    if len(vendor_combo_box_entry) > 0:
                        self.vendor_combo_box.addItem(vendor_combo_box_entry)
                else:
                    self.apis.remove(api_string) #remove faulty/corrupt api_string
            except configparser.ParsingError:
                pass
            except:
                logger.warning(traceback.format_exc())
                self.apis.remove(api_string) #remove faulty/corrupt api_string
        try:
            self.vendor_combo_box.setCurrentIndex(int(self.selection_index[0]))
        except:
            self.selection_index = '0,0,0'.split(',')
            self.vendor_combo_box.setCurrentIndex(int(self.selection_index[0]))
            pass

        if self.vendor_combo_box.count() > 0:
            self.fill_device()
        else:
            logger.debug("There are no entries in the RP1210 Vendor's ComboBox.")

    def fill_device(self):
        if self.rp1201_missing:
            return
        self.api_string = self.vendor_combo_box.currentText().split("-")[0].strip()
        if self.api_string == '':
            self.selection_index = '0,0,0'.split(',')
            self.vendor_combo_box.setCurrentIndex(int(self.selection_index[0]))
            self.api_string = self.vendor_combo_box.currentText().split("-")[0].strip()
        self.device_combo_box.clear()
        self.protocol_combo_box.clear()
        self.speed_combo_box.clear()
        for key in self.vendor_configs[self.api_string]:
            if "DeviceInformation" in key:
                try:
                    device_id = self.vendor_configs[self.api_string][key]["DeviceID"]
                except KeyError:
                    device_id = None
                    logger.debug("No Device ID for {} in {}.ini".format(key,self.api_string))
                try:
                    device_description = self.vendor_configs[self.api_string][key]["DeviceDescription"]
                except KeyError:
                    device_description = "No device description available"
                try:
                    device_MultiCANChannels = self.vendor_configs[self.api_string][key]["MultiCANChannels"]
                except KeyError:
                    device_MultiCANChannels = None
                try:
                    device_MultiJ1939Channels = self.vendor_configs[self.api_string][key]["MultiJ1939Channels"]
                except KeyError:
                    device_MultiJ1939Channels = None
                try:
                    device_MultiISO15765Channels = self.vendor_configs[self.api_string][key]["MultiISO15765Channels"]
                except KeyError:
                    device_MultiISO15765Channels = None
                try:
                    device_name = self.vendor_configs[self.api_string][key]["DeviceName"]
                except KeyError:
                    device_name = "Device name not provided"
                device_combo_box_entry = "{}: {}, {}".format(device_id,device_name,device_description)
                if len(device_combo_box_entry) > 0:
                    self.device_combo_box.addItem(device_combo_box_entry)
        try:
            self.device_combo_box.setCurrentIndex(int(self.selection_index[1]))
        except:
            pass
        self.fill_protocol()

    def fill_protocol(self):
        if self.rp1201_missing:
            return
        self.protocol_combo_box.clear()
        self.speed_combo_box.clear()
        if self.device_combo_box.currentText() == "":
                self.device_combo_box.setCurrentIndex(0)
        self.device_id = self.device_combo_box.currentText().split(":")[0].strip()

        self.protocol_speed = {}
        for key in self.vendor_configs[self.api_string]:
            if "ProtocolInformation" in key:
                try:
                    protocol_string = self.vendor_configs[self.api_string][key]["ProtocolString"]
                except KeyError:
                    protocol_string = None
                    logger.debug("No Protocol Name for {} in {}.ini".format(key,self.api_string))
                try:
                    protocol_description = self.vendor_configs[self.api_string][key]["ProtocolDescription"]
                except KeyError:
                    protocol_description = "No protocol description available"
                try:
                    if protocol_string is not None:
                        self.protocol_speed[protocol_string] = self.vendor_configs[self.api_string][key]["ProtocolSpeed"]
                    else:
                        self.protocol_speed[protocol_string] = ""
                except KeyError:
                    self.protocol_speed[protocol_string] = ""
                try:
                    protocol_params = self.vendor_configs[self.api_string][key]["ProtocolParams"]
                except KeyError:
                    protocol_params = ""

                devices = self.vendor_configs[self.api_string][key]["Devices"].split(',')
                if self.device_id in devices and protocol_string is not None:
                    device_combo_box_entry = "{}: {}".format(protocol_string,protocol_description)
                    self.protocol_combo_box.addItem(device_combo_box_entry)
            else:
                pass
        try:
            self.protocol_combo_box.setCurrentIndex(int(self.selection_index[2]))

        except:
            logger.warning(traceback.format_exc())
        self.fill_speed()

    def fill_speed(self):
        if self.rp1201_missing:
            return
        self.speed_combo_box.clear()
        if self.protocol_combo_box.currentText() == "":
                self.protocol_combo_box.setCurrentIndex(0)
        self.device_id = self.device_combo_box.currentText().split(":")[0].strip()
        protocol_string = self.protocol_combo_box.currentText().split(":")[0].strip()
        logger.debug(protocol_string)
        logger.debug(self.protocol_speed[protocol_string])
        try:
            protocol_speed = sorted(self.protocol_speed[protocol_string].strip().split(','),reverse=True)
            self.speed_combo_box.addItems(protocol_speed)
        except Exception as e:
            logger.warning(traceback.format_exc())

    def connect_RP1210(self):
        if self.rp1201_missing:
            return
        logger.debug("Accepted Dialog OK")
        vendor_index = self.vendor_combo_box.currentIndex()
        device_index = self.device_combo_box.currentIndex()
        protocol_index = self.protocol_combo_box.currentIndex()
        speed_index = self.speed_combo_box.currentIndex()

        with open(self.selection_filename,"w") as selection_file:
            selection_file.write("{},{},{}".format(vendor_index,device_index,protocol_index,speed_index))
        self.dll_name = self.vendor_combo_box.itemText(vendor_index).split("-")[0].strip()
        self.deviceID = int(self.device_combo_box.itemText(device_index).split(":")[0].strip())
        self.speed = self.speed_combo_box.itemText(speed_index)
        self.protocol = self.protocol_combo_box.itemText(protocol_index).split(":")[0].strip()

    def reject_RP1210(self):
        self.dll_name = None
        self.protocol = None
        self.deviceID = None
        self.speed = None

class Standalone(QMainWindow):
    def __init__(self):
        super(Standalone, self).__init__()
        


if __name__ == '__main__':
    """
    Use this function to test the basic functionality.
    """
    
    app = QCoreApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    else:
        app.close()
    dialog = SelectRP1210("TruckCrypt_test")
    dialog.show_dialog()  
    sys.exit(app.exec_())