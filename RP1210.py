from PyQt5.QtWidgets import QMessageBox, QInputDialog
from PyQt5.QtCore import Qt, QCoreApplication
# Use ctypes to import the RP1210 DLL
from ctypes import *
from ctypes.wintypes import HWND
import json
import os
import sys
import threading
import time
import struct
import traceback
from RP1210Functions import *

import logging
logger = logging.getLogger(__name__)

def get_storage_path():
    """Folder for user files: next to CSU_RP1210.exe when frozen (portable), else the working directory."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.getcwd()

BUFFER_SIZE = 8192

# 32-to-64-bit RP1210 bridge (rp1210_bridge/): lets this 64-bit program use
# vendors' 32-bit-only RP1210 DLLs through rp1210_host32.exe.
BRIDGE_DLL = "rp1210_bridge64.dll"


# CSUCAN: the project's own RP1210 driver (crates/csucan) for PEAK PCAN-Basic
# adapters and Linux SocketCAN. It ships with the application rather than
# being installed in the Windows directory.
CSUCAN_NAME = "CSUCAN"
CSUCAN_LIBRARY = "csucan.dll" if sys.platform == "win32" else "libcsucan.so"


def find_csucan():
    """Path of the bundled CSUCAN driver library, or None."""
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [getattr(sys, "_MEIPASS", None), os.path.dirname(sys.executable), here,
                  os.path.join(here, "target", "release")]
    for d in filter(None, candidates):
        path = os.path.join(d, CSUCAN_LIBRARY)
        if os.path.exists(path):
            return path
    return None


def write_csucan_ini(path):
    """Have CSUCAN write its vendor INI (attached PEAK/SocketCAN devices) to path."""
    lib_path = find_csucan()
    if not lib_path:
        return False
    loader = windll if sys.platform == "win32" else cdll
    lib = loader.LoadLibrary(lib_path)
    lib.CSUCAN_WriteIni.restype = c_short
    return lib.CSUCAN_WriteIni(c_char_p(os.fsencode(path))) == 0


def find_rp1210_bridge():
    """Path of rp1210_bridge64.dll (with rp1210_host32.exe beside it), or None."""
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [getattr(sys, "_MEIPASS", None), os.path.dirname(sys.executable), here,
                  os.path.join(here, "rp1210_bridge", "bin")]
    for d in filter(None, candidates):
        dll = os.path.join(d, BRIDGE_DLL)
        if os.path.exists(dll) and os.path.exists(os.path.join(d, "rp1210_host32.exe")):
            return dll
    return None


def dll_is_64bit(path):
    """True for an x64/ARM64 DLL, False for x86, None if the PE header cannot be read."""
    try:
        with open(path, "rb") as f:
            header = f.read(4096)
        pe = struct.unpack("<L", header[0x3C:0x40])[0]
        if header[pe:pe + 4] != b"PE\0\0":
            return None
        machine = struct.unpack("<H", header[pe + 4:pe + 6])[0]
        return {0x8664: True, 0xAA64: True, 0x014C: False}.get(machine)
    except (OSError, struct.error):
        return None

class RP1210ReadMessageThread(threading.Thread):
    '''This thread is designed to receive messages from the vehicle diagnostic
    adapter (VDA) and put the data into a queue. The class arguments are as
    follows:
    rx_queue - A data structure that takes the received message.
    RP1210_ReadMessage - a function handle to the VDA DLL.
    nClientID - this lets us know which network is being used to receive the
                messages. This will likely be a 1 or 2'''

    def __init__(self, parent, rx_queue, extra_queue, RP1210_ReadMessage, nClientID, protocol, title, filename="NetworkTraffic"):
        threading.Thread.__init__(self)
        self.root = parent
        self.rx_queue = rx_queue
        self.extra_queue = extra_queue
        self.RP1210_ReadMessage = RP1210_ReadMessage
        self.nClientID = nClientID
        self.runSignal = True
        self.message_count = 0
        self.start_time = time.time()
        self.duration = 0
        self.filename = os.path.join(get_storage_path(), protocol + filename + ".bin")
        self.protocol = protocol
        self.pgns_to_block=[61444, 61443, 65134, 65215]
        self.sources_to_block=[0, 11]
        self.can_ids_to_block = []
        
    def run(self):
        ucTxRxBuffer = (c_char * BUFFER_SIZE)()
        # display a valid connection upon start.
        logger.debug("Read Message Client ID: {}".format(self.nClientID))
        message_bytes = b'1210'
        # with open(self.filename,'wb') as log_file:
        #     pass
        while self.runSignal: #Look into threading.events
                self.duration = time.time() - self.start_time
                return_value = self.RP1210_ReadMessage(c_short(self.nClientID),
                                                       byref(ucTxRxBuffer),
                                                       c_short(BUFFER_SIZE),
                                                       c_short(BLOCKING_IO))
                if return_value > 0:
                    current_time = time.time()
                    if ucTxRxBuffer[4] == b'\x00': #Echo is on, so we only want to see what others are sending.
                        self.message_count +=1
                                   
                    if self.protocol == "CAN":
                        vda_timestamp = struct.unpack(">L",ucTxRxBuffer[0:4])[0]
                        extended = ucTxRxBuffer[5]
                        if extended:
                            can_id = struct.unpack(">L",ucTxRxBuffer[6:10])[0] #Swap endianness
                            can_data = ucTxRxBuffer[10:return_value]
                            dlc = int(return_value - 10)
                        
                        else:
                            can_id = struct.unpack(">H",ucTxRxBuffer[6:8])[0] #Swap endianness
                            can_data = ucTxRxBuffer[8:return_value]
                            dlc = int(return_value - 8)
                        
                        # #the following conversion is to emulate the data structure from the NMFTA CAN Logger Project
                        # # See https://github.com/Heavy-Vehicle-Networking-At-U-Tulsa/NMFTA-CAN-Logger/tree/master/_07_Low_Latency_Logger_with_Requests
                        # try:
                        #     microsecond_bytes = struct.pack("<L", int((dlc << 24) + (current_time % 1) * 1000000))
                        # except struct.error:
                        #     continue
                        # # RP1210_ReadMessage API:
                        # #Reverse endianess
                        # #vda_timestamp = struct.pack("<L",struct.unpack(">L",ucTxRxBuffer[0:4])[0])  
                        
                        
                        # #echo_byte = ucTxRxBuffer[4]
                        
                        # # Build the 24 bytes that make up a CAN message.
                        # message_bytes = time_bytes
                        # message_bytes += vda_timestamp
                        # message_bytes += microsecond_bytes
                        # message_bytes += can_id
                        # message_bytes += can_data
                        self.rx_queue.put( (current_time, vda_timestamp, can_id, dlc, can_data) )
                        

                    elif self.protocol == "J1708": 
                        self.rx_queue.put((current_time, ucTxRxBuffer[:return_value]))
                        #self.extra_queue.put((current_time, ucTxRxBuffer[5:return_value]))
                        
                    elif self.protocol == "J1939":
                        pgn = struct.unpack("<L", ucTxRxBuffer[5:8] + b'\x00')[0]
                        sa = struct.unpack("B",ucTxRxBuffer[9])[0]
                        
                        if (pgn not in self.pgns_to_block) or (sa not in self.sources_to_block):
                            self.rx_queue.put({'current_time':current_time,'data':ucTxRxBuffer[:return_value]})
                        #ISO 15765 traffic only
                        if pgn == 0xDA00:
                            dst_addr = struct.unpack("B",ucTxRxBuffer[10])[0]
                            message_data = ucTxRxBuffer[11:return_value]
                            self.extra_queue.put((pgn, 6, sa, dst_addr, message_data))

                    
        logger.debug("RP1210 Receive Thread is finished.")

    def make_log_data(self,message_bytes,return_value,time_bytes,ucTxRxBuffer):
        length_bytes = struct.pack("<H",return_value + 4)
        message_bytes += length_bytes
        message_bytes += time_bytes
        message_bytes += ucTxRxBuffer[:return_value]
        return message_bytes

class RP1210Class():
    """A class to access RP1210 libraries for different devices."""
    def __init__(self, dll_name):
        r"""
        Load the Windows Device Library
        The input argument is the dll_name from one of the manufacturers DLLs in the c:\Windows directory  
        """
        self.nClientID = None
        self.ucTxRxBuffer = (c_char*BUFFER_SIZE)()
        self.dll_name = dll_name
        self.bridge_target = None
        self.dll_path = self.find_dll_path()
        self.create_RP1210_functions()
        
    
    def find_dll_path(self):
        """
        Find the vendor DLL that matches this process: a 64-bit process needs a
        64-bit DLL (System32 on 64-bit Windows), a 32-bit process a 32-bit DLL
        (SysWOW64). A mismatched DLL cannot be loaded.
        """
        is_64bit = sys.maxsize > 2**32
        if self.dll_name.upper() == CSUCAN_NAME:
            path = find_csucan()
            if path:
                logger.info("Using the CSUCAN driver at {}".format(path))
                return path
        windir = os.environ.get("WINDIR", r"C:\Windows")
        order = ["System32", "", "SysWOW64"] if is_64bit else ["SysWOW64", "", "System32"]
        mismatched = []
        for sub in order:
            dll_path = os.path.join(windir, sub, self.dll_name + '.dll')
            logger.debug("looking for RP1210 DLL at {}".format(dll_path))
            if not os.path.exists(dll_path):
                continue
            dll_64bit = dll_is_64bit(dll_path)
            if dll_64bit is None or dll_64bit == is_64bit:
                logger.info("Found RP1210 DLL at {}".format(dll_path))
                return dll_path
            mismatched.append(dll_path)
        if mismatched:
            bridge = find_rp1210_bridge() if is_64bit and sys.platform == "win32" else None
            if bridge:
                logger.info("Only a 32-bit {}.dll is installed; using the RP1210 bridge {}".format(self.dll_name, bridge))
                self.bridge_target = self.dll_name
                return bridge
            logger.warning("Only a {}-bit {}.dll is installed ({}); this is a {}-bit program. Use the {}-bit "
                           "CSU_RP1210 build for this adapter.".format(32 if is_64bit else 64, self.dll_name,
                                                                        mismatched[0], 64 if is_64bit else 32,
                                                                        32 if is_64bit else 64))
            return mismatched[0]
        logger.warning("Could not find RP1210 DLL. Please make sure drivers are installed.")
        logger.debug("We'll try to use just the filename and see what happens.")
        return self.dll_name + '.dll'
        
    def create_RP1210_functions(self):
        """
        Create function prototypes to access the DLL of the RP1210 Drivers.
        """
        #initialize 
        self.ClientConnect = None
        self.ClientDisconnect = None
        self.SendMessage = None
        self.ReadMessage = None
        self.SendCommand = None
        self.ReadVersion = None
        self.ReadDetailedVersion = None
        self.GetHardwareStatus = None
        self.GetErrorMsg = None
        self.GetHardwareStatusEx = None
        self.GetLastErrorMsg = None
        
        logger.debug("Loading the RP1210 driver file at {}".format(self.dll_path))
        try:
            RP1210DLL = windll.LoadLibrary(self.dll_path)
            if self.bridge_target:
                result = RP1210DLL.RP1210Bridge_SetTarget(c_char_p(self.bridge_target.encode("ascii")))
                if result != 0:
                    logger.error("RP1210Bridge_SetTarget failed with return value %d", result)
                    return False
        except:
            logger.debug(traceback.format_exc())
            logger.info("If the RP1210 DLL fails to load, check that its bitness matches this program "
                        "({}-bit) and that the drivers for the VDA are installed.".format(64 if sys.maxsize > 2**32 else 32))
            return False

        # Define windows prototype functions:
        try:
            prototype = WINFUNCTYPE(c_short, HWND, c_short, c_char_p, c_long, c_long, c_short)
            self.ClientConnect = prototype(("RP1210_ClientConnect", RP1210DLL))

            prototype = WINFUNCTYPE(c_short, c_short)
            self.ClientDisconnect = prototype(("RP1210_ClientDisconnect", RP1210DLL))

            prototype = WINFUNCTYPE(c_short, c_short,  POINTER(c_char*BUFFER_SIZE), c_short, c_short, c_short)
            self.SendMessage = prototype(("RP1210_SendMessage", RP1210DLL))

            prototype = WINFUNCTYPE(c_short, c_short, POINTER(c_char*BUFFER_SIZE), c_short, c_short)
            self.ReadMessage = prototype(("RP1210_ReadMessage", RP1210DLL))

            prototype = WINFUNCTYPE(c_short, c_short, c_short, POINTER(c_char*BUFFER_SIZE), c_short)
            self.SendCommand = prototype(("RP1210_SendCommand", RP1210DLL))
        except Exception as e:
            logger.debug(traceback.format_exc())
            logger.debug("\n Critical RP1210 functions were not able to be loaded. There is something wrong with the DLL file.")
            return False

        try:
            prototype = WINFUNCTYPE(c_short, c_char_p, c_char_p, c_char_p, c_char_p)
            self.ReadVersion = prototype(("RP1210_ReadVersion", RP1210DLL))
        except Exception as e:
            logger.exception(e)

        try:
            prototype = WINFUNCTYPE(c_short, c_short, POINTER(c_char*17), POINTER(c_char*17), POINTER(c_char*17))
            self.ReadDetailedVersion = prototype(("RP1210_ReadDetailedVersion", RP1210DLL))
        except Exception as e:
            logger.debug(traceback.format_exc())
            self.ReadDetailedVersion = None

        try:
            prototype = WINFUNCTYPE(c_short, c_short, POINTER(c_char*64), c_short, c_short)
            self.GetHardwareStatus = prototype(("RP1210_GetHardwareStatus", RP1210DLL))
        except Exception as e:
            logger.debug(traceback.format_exc())

        # try:
        #     prototype = WINFUNCTYPE(c_short, c_short, POINTER(c_char*256))
        #     self.GetHardwareStatusEx = prototype(("RP1210_GetHardwareStatusEx", RP1210DLL))
        # except Exception as e:
        #     logger.debug(traceback.format_exc())
            
        try:
            prototype = WINFUNCTYPE(c_short, c_short, POINTER(c_char*80))
            self.GetErrorMsg = prototype(("RP1210_GetErrorMsg", RP1210DLL))
        except Exception as e:
            logger.debug(traceback.format_exc())

        try:
            prototype = WINFUNCTYPE(c_short, c_short, POINTER(c_int), POINTER(c_char*80), c_short)
            self.GetLastErrorMsg = prototype(("RP1210_GetLastErrorMsg", RP1210DLL))
        except Exception as e:
            logger.debug(traceback.format_exc())
        return True

    def get_client_id(self, protocol, deviceID, speed, channel=None):
        """
        Loads the DLL in to Python and assignes self.nClientID. This is used to reference the DLL client in the app.
        Saves successful clients to a json file so it doesn't ask the user for input each time.
        """
        QCoreApplication.processEvents()
        nClientID = None
        params = []
        if len(speed) > 0 and (protocol == "J1939"  or protocol == "CAN" or protocol == "ISO15765"):
            params.append("Baud={}".format(speed))
        # RP1210C multi-channel adapters (e.g. PEAK PCAN-PCI Express FD): select the channel.
        if channel and int(channel) > 1 and protocol in ("J1939", "CAN", "ISO15765"):
            params.append("Channel={}".format(int(channel)))
        protocol_bytes = bytes(protocol + (":" + ",".join(params) if params else ""), 'ascii')
        logger.debug("Connecting with ClientConnect using " + repr(protocol_bytes))
        try:
            nClientID = self.ClientConnect(HWND(None), c_short(deviceID), protocol_bytes, BUFFER_SIZE, BUFFER_SIZE, 0)
            logger.debug("The Client ID is: {}, which means {}".format(nClientID, self.get_error_code(nClientID)))
            
        except Exception as e:
            logger.warning("Client Connect did not work.")
            logger.debug(traceback.format_exc())
        
        if nClientID is None:
            logger.debug("An RP1210 device is not connected properly.")
            return None
        elif nClientID < 128:           
            return nClientID
        else:
            return None

    def display_version(self):
        """
        Displays RP1210 Version information to a diaglog box.
        See the RP1210 API for details.
        """
        message_window = QMessageBox()
        message_window.setIcon(QMessageBox.Information)
        message_window.setWindowTitle('RP1210 Version Information')
        message_window.setStandardButtons(QMessageBox.Ok)

        if self.ReadVersion is None:
            message_window.setText("RP1210_ReadVersion() function is not available.")
            logger.debug("RP1210_ReadVersion() is not supported.")
        else:
            chDLLMajorVersion    = (c_char)()
            chDLLMinorVersion    = (c_char)()
            chAPIMajorVersion    = (c_char)()
            chAPIMinorVersion    = (c_char)()

            #There is no return value for RP1210_ReadVersion
            self.ReadVersion(byref(chDLLMajorVersion),
                                    byref(chDLLMinorVersion),
                                    byref(chAPIMajorVersion),
                                    byref(chAPIMinorVersion))
            logger.debug('Successfully Read DLL and API Versions.')
            DLLMajor = chDLLMajorVersion.value.decode('ascii','ignore')
            DLLMinor = chDLLMinorVersion.value.decode('ascii','ignore')
            APIMajor = chAPIMajorVersion.value.decode('ascii','ignore')
            APIMinor = chAPIMinorVersion.value.decode('ascii','ignore')
            logger.debug("DLL Major Version: {}".format(DLLMajor))
            logger.debug("DLL Minor Version: {}".format(DLLMinor))
            logger.debug("API Major Version: {}".format(APIMajor))
            logger.debug("API Minor Version: {}".format(APIMinor))
            message_window.setText("Driver software versions are as follows:\nDLL Major Version: {}\nDLL Minor Version: {}\nAPI Major Version: {}\nAPI Minor Version: {}".format(DLLMajor,DLLMinor,APIMajor,APIMinor))
        message_window.exec_()
    
    def get_hardware_status_ex(self,nClientID=1):
        """
        Displays RP1210 Extended get hardware status information to a diaglog box.
        See the RP1210 API for details.
        """
        message_window = QMessageBox()
        message_window.setIcon(QMessageBox.Information)
        message_window.setWindowTitle('RP1210 Extended Hardware Status')
        message_window.setStandardButtons(QMessageBox.Ok)

        if self.GetHardwareStatusEx is None:
            message = "RP1210_GetHardwareStatusEx() function is not available."
        else:
            client_info_pointer = (c_char*256)()
            return_value = self.GetHardwareStatusEx(c_short(nClientID),
                                                         byref(client_info_pointer))
            if return_value == 0:
                message = ""
                status_bytes = client_info_pointer.raw
                logger.debug(status_bytes)

                hw_device_located = (status_bytes[0] & 0x01) >> 0
                if hw_device_located:
                    message += "The hardware device was located and it is ready.\n"
                else:
                    message += "The hardware device was not located.\n"

                hw_device_internal = (status_bytes[0] & 0x02) >> 1
                if hw_device_internal:
                    message += "The hardware device is an internal device, non-wireless.\n"
                else:
                    message += "The hardware device is not an internal device, non-wireless.\n"

                hw_device_external = (status_bytes[0] & 0x04) >> 2
                if hw_device_external:
                    message += "The hardware device is an external device, non-wireless.\n"
                else:
                    message += "The hardware device is not an external device, non-wireless.\n"

                hw_device_internal = (status_bytes[0] & 0x08) >> 3
                if hw_device_internal:
                    message += "The hardware device is an internal device, wireless.\n"
                else:
                    message += "The hardware device is not an internal device, wireless.\n"

                hw_device_external = (status_bytes[0] & 0x10) >> 4
                if hw_device_external:
                    message += "The hardware device is an external device, wireless.\n"
                else:
                    message += "The hardware device is not an external device, wireless.\n"

                auto_baud = (status_bytes[0] & 0x20) >> 5
                if auto_baud:
                    message += "The hardware device CAN auto-baud capable.\n"
                else:
                    message += "The hardware device is not CAN auto-baud capable.\n"

                number_of_clients = status_bytes[1]
                message += "The number of connected clients is {}.\n\n".format(number_of_clients)

                number_of_can = status_bytes[1]
                message += "The number of simultaneous CAN channels is {}.\n\n".format(number_of_can)

                message += "There may be more information available than what is currently shown."
            else:
                message = "RP1210_GetHardwareStatusEx failed with a return value of  {}: {}".format(return_value,self.get_error_code(return_value))

        logger.debug(message)
        message_window.setText(message)
        message_window.exec_()


    def get_hardware_status(self, nClientID=1):
        """
        Displays RP1210 Get hardware status information to a diaglog box.
        See the RP1210 API for details.
        """
        message_window = QMessageBox()
        message_window.setIcon(QMessageBox.Information)
        message_window.setWindowTitle('RP1210 Hardware Status')
        message_window.setStandardButtons(QMessageBox.Ok)

        if self.GetHardwareStatus is None:
            message = "RP1210_GetHardwareStatus() function is not available."
        else:
            client_info_pointer = (c_char*64)()
            nInfoSize = 64
            return_value = self.GetHardwareStatus(c_short(nClientID),
                                                         byref(client_info_pointer),
                                                         c_short(nInfoSize),
                                                         c_short(0))
            if return_value == 0 :
                message = ""
                status_bytes = client_info_pointer.raw
                logger.debug(status_bytes)

                hw_device_located = (status_bytes[0] & 0x01) >> 0
                if hw_device_located:
                    message += "The hardware device was located.\n"
                else:
                    message += "The hardware device was not located.\n"

                hw_device_internal = (status_bytes[0] & 0x02) >> 1
                if hw_device_internal:
                    message += "The hardware device is an internal device.\n"
                else:
                    message += "The hardware device is not an internal device.\n"

                hw_device_external = (status_bytes[0] & 0x04) >> 2
                if hw_device_external:
                    message += "The hardware device is an external device.\n"
                else:
                    message += "The hardware device is not an external device.\n"

                number_of_clients = status_bytes[1]
                message += "The number of connected clients is {}.\n\n".format(number_of_clients)

                j1939_active = (status_bytes[2] & 0x01) >> 0
                if j1939_active:
                    message += "The J1939 link is activated.\n"
                else:
                    message += "The J1939 link is not activated.\n"

                traffic_detected = (status_bytes[2] & 0x02) >> 1
                if traffic_detected:
                    message += "J1939 network traffic was detected in the last second.\n"
                else:
                    message += "J1939 network traffic was not detected in the last second.\n"

                bus_off = (status_bytes[2] & 0x04) >> 2
                if bus_off:
                    message += "The CAN controller reports a BUS_OFF status.\n"
                else:
                    message += "The CAN controller does not report a BUS_OFF status.\n"
                number_of_clients = status_bytes[3]
                message += "The number of clients connected to J1939 is {}.\n\n".format(number_of_clients)


                j1708_active = (status_bytes[4] & 0x01) >> 0
                if j1708_active:
                    message += "The J1708 link is activated.\n"
                else:
                    message += "The J1708 link is not activated.\n"

                traffic_detected = (status_bytes[4] & 0x02) >> 1
                if traffic_detected:
                    message += "J1708 network traffic was detected in the last second.\n"
                else:
                    message += "J1708 network traffic was not detected in the last second.\n"

                number_of_clients = status_bytes[5]
                message += "The number of clients connected to J1708 is {}.\n\n".format(number_of_clients)

                can_active = (status_bytes[6] & 0x01) >> 0
                if can_active:
                    message += "The CAN link is activated.\n"
                else:
                    message += "The CAN link is not activated.\n"

                traffic_detected = (status_bytes[6] & 0x02) >> 1
                if traffic_detected:
                    message += "CAN network traffic was detected in the last second.\n"
                else:
                    message += "CAN network traffic was not detected in the last second.\n"

                bus_off = (status_bytes[6] & 0x04) >> 2
                if bus_off:
                    message += "The CAN controller reports a BUS_OFF status.\n"
                else:
                    message += "The CAN controller does not report a BUS_OFF status.\n"
                number_of_clients = status_bytes[7]
                message += "The number of clients connected to CAN is {}.\n\n".format(number_of_clients)

                j1850_active = (status_bytes[8] & 0x01) >> 0
                if j1850_active:
                    message += "The J1850 link is activated.\n"
                else:
                    message += "The J1850 link is not activated.\n"

                traffic_detected = (status_bytes[8] & 0x02) >> 1
                if traffic_detected:
                    message += "J1850 network traffic was detected in the last second.\n"
                else:
                    message += "J1850 network traffic was not detected in the last second.\n"

                number_of_clients = status_bytes[9]
                message += "The number of clients connected to J1850 is {}.\n\n".format(number_of_clients)

                iso_active = (status_bytes[16] & 0x01) >> 0
                if iso_active:
                    message += "The ISO15765 link is activated.\n"
                else:
                    message += "The ISO15765 link is not activated.\n"

                traffic_detected = (status_bytes[16] & 0x02) >> 1
                if traffic_detected:
                    message += "ISO15765 network traffic was detected in the last second.\n"
                else:
                    message += "ISO15765 network traffic was not detected in the last second.\n"

                bus_off = (status_bytes[16] & 0x04) >> 2
                if bus_off:
                    message += "The CAN controller reports a BUS_OFF status.\n"
                else:
                    message += "The CAN controller does not report a BUS_OFF status.\n"
                number_of_clients = status_bytes[17]
                message += "The number of clients connected to ISO15765 is {}.\n\n".format(number_of_clients)

            else:
                message = "RP1210_GetHardwareStatus failed with a return value of  {}: {}".format(return_value,self.get_error_code(return_value))
        logger.debug(message)
        message_window.setText(message)
        message_window.exec_()
    
    def get_hardware_status_data(self, nClientID):
        """
        Interprets byte streams for status data
        """
        vda = False
        can = False
        j1939 = False
        j1708 = False
        iso = False
        if self.GetHardwareStatus is not None:
            client_info_pointer = (c_char*64)()
            nInfoSize = 16
            logger.debug("calling GetHardwareStatus")
            return_value = self.GetHardwareStatus(c_short(nClientID),
                                                         byref(client_info_pointer),
                                                         c_short(nInfoSize),
                                                         c_short(0))
            if return_value == 0:
                vda = True
                status_bytes = client_info_pointer.raw
                
                traffic_detected = (status_bytes[2] & 0x02) >> 1 #J1708
                if traffic_detected:
                    j1939 = True
                else:
                    j1939 = False

                traffic_detected = (status_bytes[4] & 0x02) >> 1 #J1708
                if traffic_detected:
                    j1708 = True
                else:
                    j1708 = False

                traffic_detected = (status_bytes[6] & 0x02) >> 1 #CAN
                if traffic_detected:
                    can = True
                else:
                    can = False

                traffic_detected = (status_bytes[16] & 0x02) >> 1 #ISO
                if traffic_detected:
                    iso = True
                else:
                    iso = False

        return vda,can,j1939,j1708,iso

    def get_error_code(self, code):
        """
        Uses the Vendor's description of the error/status codes when interpeting
        RP1210 information based on return values.
        """
        # Make sure the function prototype is available:
        if self.GetErrorMsg is not None:
            #make sure the error code is an integer
            try:
                code = int(code)
            except:
                logger.warning(traceback.format_exc())
                code = -1
            # Set up the decription buffer
            fpchDescription = (c_char*80)()
            return_value = self.GetErrorMsg(c_short(code), byref(fpchDescription))
            description = fpchDescription.value.decode('ascii','ignore')
            if return_value == 0:
               return description
        else:
            return "Error code interpretation not available."

    def display_detailed_version(self, nClientID):
        """
        Display RP1210 detailed version information from a connected device.
        """
        message_window = QMessageBox()
        message_window.setIcon(QMessageBox.Information)
        message_window.setWindowTitle('RP1210 Detailed Version')
        message_window.setStandardButtons(QMessageBox.Ok)

        if self.ReadDetailedVersion is None:
            message = "RP1210_ReadDetailedVersion() function is not available."
        else:
            chAPIVersionInfo    = (c_char*17)()
            chDLLVersionInfo    = (c_char*17)()
            chFWVersionInfo     = (c_char*17)()
            return_value = self.ReadDetailedVersion(c_short(nClientID),
                                                        byref(chAPIVersionInfo),
                                                        byref(chDLLVersionInfo),
                                                        byref(chFWVersionInfo))
            if return_value == 0 :
                message = 'The PC computer has successfully connected to the RP1210 Device.\nThere is no need to check your USB connection.\n'
                DLL = chDLLVersionInfo.value
                API = chAPIVersionInfo.value
                FW = chAPIVersionInfo.value
                message += "DLL = {}\n".format(DLL.decode('ascii','ignore'))
                message += "API = {}\n".format(API.decode('ascii','ignore'))
                message += "FW  = {}".format(FW.decode('ascii','ignore'))
            else:
                message = "RP1210_ReadDetailedVersion failed with\na return value of  {}: {}".format(return_value,self.get_error_code(return_value))
        message_window.setText(message)
        message_window.exec_()

    def send_message(self, client_id, message_bytes):
        """
        Sends message bytes to a client for transmission on the vehicle network.
        """
        #load the buffer
        msg_len = len(message_bytes)
        for i in range(msg_len):
            self.ucTxRxBuffer[i] = message_bytes[i]
        #call the command
        try:
            return_value = self.SendMessage(c_short(client_id),
                                        byref(self.ucTxRxBuffer),
                                        c_short(msg_len), 0, 0)
            if return_value != 0:
                message = "RP1210_SendMessage failed with a return value of  {}: {}".format(return_value,
                                                                self.get_error_code(return_value))
                logger.warning(message)
        except:
            logger.warning(traceback.format_exc())

    def send_command(self, command_num, client_id, message_bytes):
        """
        Send RP1210 commands using a command number, client ID and message bytes.
        """
        msg_len = len(message_bytes)
        for i in range(msg_len):
            self.ucTxRxBuffer[i] = message_bytes[i]
        try:
            return_value = self.SendCommand(c_short(command_num),
                                        c_short(client_id),
                                        byref(self.ucTxRxBuffer),
                                        c_short(msg_len))
            if return_value != 0:
                message = "RP1210_SendCommand {} failed with a return value of {}: {}".format(command_num,
                                                                                                return_value,
                                                                                                self.get_error_code(return_value))
                logger.warning(message)
            return return_value
        except:
            logger.warning(traceback.format_exc())

    def get_last_error_msg(self,nClientID):
        """
        Look up error codes from RP1210
        """
        nErrorCode, ok = QInputDialog.getInt(self, 
                                            'Last Error Code',
                                            'Enter Error Code:',
                                            value = -1, 
                                            min = 0, 
                                            max=255)
        message_window = QMessageBox()
        message_window.setIcon(QMessageBox.Information)
        message_window.setWindowTitle('RP1210 Get Last Error Message')
        message_window.setStandardButtons(QMessageBox.Ok)
        # Make sure the function prototype is available:
        if self.GetLastErrorMsg is not None and nClientID is not None:
            fpchDescription = (c_char*80)()
            nSubErrorCode = (c_int)()
            return_value = self.GetLastErrorMsg(c_short(nErrorCode),
                                                       byref(nSubErrorCode),
                                                       byref(fpchDescription),
                                                       c_short(nclientID))
            description = fpchDescription.value.decode('ascii','ignore')
            sub_error = nSubErrorCode.value
            if return_value == 0 :
                message = "Client ID is {}.\nError Code {} means {}".format(clientID, nErrorCode, description)
                if sub_error < 0:
                    message_window.setInformativeText("No subordinate error code is available.")
                else:
                    message_window.setInformativeText("Additional Code: {}".format(sub_error))
            else:
                message = "RP1210_GetLastErrorMsg failed with\na return value of  {}: {}".format(return_value,self.get_error_code(return_value))
        else:
            message = "RP1210_GetLastErrorMsg() function is not available."

        logger.debug(message)
        message_window.setText(message)
        message_window.exec_()
