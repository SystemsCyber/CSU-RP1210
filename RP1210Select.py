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
from RP1210 import get_storage_path, find_csucan, write_csucan_ini, CSUCAN_NAME
logger = logging.getLogger(__name__)


def select_item(combo, key, separator):
    """Select the entry whose text (or its part before separator) is key; True if found."""
    if key is None or key is False:
        return False
    for i in range(combo.count()):
        text = combo.itemText(i)
        if (text.split(separator)[0] if separator else text).strip() == str(key).strip():
            combo.setCurrentIndex(i)
            return True
    return False

class SelectRP1210(QDialog):
    """
    A Qt dialog box that parses the RP1210 ini files to enable a user to select the RP1210 device. 
    """
    def __init__(self,title):
        super(SelectRP1210,self).__init__()
        RP1210_config = configparser.ConfigParser()
        self.apis = []
        try:
            RP1210_config.read(os.path.join(os.environ.get("WINDIR", ""),"RP121032.ini"))
            self.apis = sorted(a.strip() for a in RP1210_config["RP1210Support"]["apiimplementations"].split(",") if a.strip())
        except (KeyError, configparser.Error):
            logger.info("No vendor RP1210 drivers are registered (RP121032.ini not found).")
        storage = get_storage_path()
        # CSUCAN (bundled): PEAK PCAN-Basic and SocketCAN devices, listed live
        # in a generated vendor INI so new hardware shows up without vendor tools.
        self.local_ini = {}
        if find_csucan():
            csucan_ini = os.path.join(storage, CSUCAN_NAME + ".ini")
            try:
                if write_csucan_ini(csucan_ini):
                    self.local_ini[CSUCAN_NAME] = csucan_ini
                    self.apis.append(CSUCAN_NAME)
            except OSError:
                logger.warning(traceback.format_exc())
        self.current_api_index = 0
        logger.debug("Current RP1210 APIs available are: " + ", ".join(self.apis))
        self.rp1201_missing = not self.apis
        if self.rp1201_missing:
            QMessageBox.warning(self,"No RP1210 Device","No RP1210 drivers were found. Install an RP1210 compliant Vehicle Diagnostics Adapter, or a PEAK adapter driver for the built-in CSUCAN driver.")
            return
        self.selection_filename = os.path.join(storage,"RP1210_selection.txt")
        logger.debug(f"selection_filename path: {self.selection_filename}")
        self.connections_file = os.path.join(storage,"Last_RP1210_Connection.json")
        logger.debug(f"connections_file path: {self.connections_file}")
        logger.debug("Looking for RP1210 Settings in {}".format(self.connections_file))
        try:
            with open(self.connections_file,"r") as rp1210_file:
                file_contents = json.load(rp1210_file)
            self.dll_name = file_contents["dll_name"]
            self.protocol = file_contents["protocol"]
            self.deviceID = file_contents["deviceID"]
            self.speed    = file_contents["speed"]
            self.channel  = file_contents.get("channel", 1)
        except (OSError, ValueError, KeyError, TypeError):
            logger.info("No previous RP1210 connection in {}".format(self.connections_file))
            self.dll_name = False
            self.protocol = False
            self.deviceID = False
            self.speed = False
            self.channel = 1
        # The dialog selects by name from each vendor INI: the last connection's
        # vendor, device, protocol and speed when that adapter offers them,
        # otherwise J1939 at 250 kbit/s (list positions differ between vendors).
        self.preferred = {"dll_name": self.dll_name, "deviceID": self.deviceID,
                          "protocol": self.protocol or "J1939", "speed": self.speed or "250"}

        self.setup_dialog()
        self.setWindowTitle("Select RP1210")
        self.setWindowModality(Qt.ApplicationModal)

    
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
        self.protocol_combo_box.activated.connect(self.protocol_chosen)

        channel_label = QLabel("Channel (multi-channel adapters):")
        self.channel_combo_box = QComboBox()
        self.channel_combo_box.setInsertPolicy(QComboBox.NoInsert)
        self.channel_combo_box.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        self.device_channels = {}

        speed_label = QLabel("Available Speed Settings")
        self.speed_combo_box = QComboBox()
        self.speed_combo_box.setInsertPolicy(QComboBox.NoInsert)
        self.speed_combo_box.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        self.speed_combo_box.activated.connect(self.speed_chosen)


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
        self.v_layout.addWidget(channel_label)
        self.v_layout.addWidget(self.channel_combo_box)
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
                ini_path = self.local_ini.get(api_string) or os.path.join(os.environ.get("WINDIR", ""), api_string + ".ini")
                self.vendor_configs[api_string].read(ini_path)
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
        if not (self.preferred["dll_name"] and select_item(self.vendor_combo_box, self.preferred["dll_name"], "-")):
            try:   # older installs: only the list positions in RP1210_selection.txt
                index = int(self.selection_index[0])
            except (ValueError, IndexError):
                index = 0
            self.vendor_combo_box.setCurrentIndex(index if 0 <= index < self.vendor_combo_box.count() else 0)

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
                channel_counts = [int(c) for c in (device_MultiCANChannels, device_MultiJ1939Channels)
                                  if c is not None and str(c).strip().isdigit()]
                self.device_channels[str(device_id).strip()] = max(channel_counts) if channel_counts else 1
                device_combo_box_entry = "{}: {}, {}".format(device_id,device_name,device_description)
                if len(device_combo_box_entry) > 0:
                    self.device_combo_box.addItem(device_combo_box_entry)
        # The saved device ID applies only to the adapter it was saved for.
        if self.api_string == self.preferred["dll_name"]:
            select_item(self.device_combo_box, self.preferred["deviceID"], ":")
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
            if "ProtocolInformation" not in key:
                continue
            section = self.vendor_configs[self.api_string][key]
            protocol_string = section.get("ProtocolString", "").strip()
            if not protocol_string:
                logger.debug("No Protocol Name for {} in {}.ini".format(key,self.api_string))
                continue
            protocol_description = section.get("ProtocolDescription", "No protocol description available")
            devices = [d.strip() for d in section.get("Devices", "").split(',')]
            if self.device_id in devices:
                self.protocol_speed[protocol_string] = section.get("ProtocolSpeed", "")
                self.protocol_combo_box.addItem("{}: {}".format(protocol_string,protocol_description))
        # Keep the chosen protocol when this adapter offers it, else the first real one.
        if not select_item(self.protocol_combo_box, self.preferred["protocol"], ":"):
            for i in range(self.protocol_combo_box.count()):
                if not self.protocol_combo_box.itemText(i).startswith("NULL:"):
                    self.protocol_combo_box.setCurrentIndex(i)
                    break
        self.fill_speed()

    def protocol_chosen(self):
        self.preferred["protocol"] = self.protocol_combo_box.currentText().split(":")[0].strip()
        self.fill_speed()

    def speed_chosen(self):
        self.preferred["speed"] = self.speed_combo_box.currentText()

    def saved_channel(self):
        try:
            with open(self.connections_file) as f:
                return int(json.load(f).get("channel", 1))
        except (OSError, ValueError, TypeError, AttributeError):
            return 1

    def fill_channel(self):
        self.channel_combo_box.clear()
        count = self.device_channels.get(str(self.device_id), 1)
        self.channel_combo_box.addItems([str(c) for c in range(1, count + 1)])
        self.channel_combo_box.setEnabled(count > 1)
        # The saved channel applies only to the adapter and device it was saved for.
        previous = 1
        if (self.api_string == self.preferred["dll_name"]
                and str(self.device_id) == str(self.preferred["deviceID"]).strip()):
            try:
                previous = int(getattr(self, "channel", None) or self.saved_channel())
            except (TypeError, ValueError):
                previous = 1
        if 1 <= previous <= count:
            self.channel_combo_box.setCurrentIndex(previous - 1)

    def fill_speed(self):
        if self.rp1201_missing:
            return
        self.fill_channel()
        self.speed_combo_box.clear()
        if self.protocol_combo_box.currentText() == "":
                self.protocol_combo_box.setCurrentIndex(0)
        self.device_id = self.device_combo_box.currentText().split(":")[0].strip()
        protocol_string = self.protocol_combo_box.currentText().split(":")[0].strip()
        # Speeds in the order the vendor INI lists them.
        speeds = []
        for s in self.protocol_speed.get(protocol_string, "").split(','):
            if s.strip() and s.strip() not in speeds:
                speeds.append(s.strip())
        logger.debug("{} speeds: {}".format(protocol_string, speeds))
        self.speed_combo_box.addItems(speeds)
        select_item(self.speed_combo_box, self.preferred["speed"], None)

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
        self.channel = int(self.channel_combo_box.currentText() or 1)

    def reject_RP1210(self):
        self.dll_name = None
        self.protocol = None
        self.deviceID = None
        self.speed = None
        self.channel = None

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