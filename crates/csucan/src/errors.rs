//! RP1210C return codes and descriptions.

pub const NO_ERRORS: i16 = 0;
pub const ERR_DLL_NOT_INITIALIZED: i16 = 128;
pub const ERR_INVALID_CLIENT_ID: i16 = 129;
pub const ERR_CLIENT_ALREADY_CONNECTED: i16 = 130;
pub const ERR_CLIENT_AREA_FULL: i16 = 131;
pub const ERR_INVALID_DEVICE: i16 = 134;
pub const ERR_DEVICE_IN_USE: i16 = 135;
pub const ERR_INVALID_PROTOCOL: i16 = 136;
pub const ERR_TX_QUEUE_FULL: i16 = 137;
pub const ERR_MESSAGE_TOO_LONG: i16 = 141;
pub const ERR_HARDWARE_NOT_RESPONDING: i16 = 142;
pub const ERR_COMMAND_NOT_SUPPORTED: i16 = 143;
pub const ERR_INVALID_COMMAND: i16 = 144;
pub const ERR_ADDRESS_CLAIM_FAILED: i16 = 146;
pub const ERR_CLIENT_DISCONNECTED: i16 = 148;
pub const ERR_CONNECT_NOT_ALLOWED: i16 = 149;
pub const ERR_BUS_OFF: i16 = 151;
pub const ERR_COULD_NOT_TX_ADDRESS_CLAIMED: i16 = 152;
pub const ERR_MESSAGE_NOT_SENT: i16 = 159;

pub fn describe(code: i16) -> &'static str {
    match code {
        NO_ERRORS => "No errors",
        ERR_DLL_NOT_INITIALIZED => "DLL not initialized",
        ERR_INVALID_CLIENT_ID => "Invalid client ID",
        ERR_CLIENT_ALREADY_CONNECTED => "Client already connected",
        ERR_CLIENT_AREA_FULL => "Client area full",
        132 => "Free memory error",
        133 => "Not enough memory",
        ERR_INVALID_DEVICE => "Invalid device",
        ERR_DEVICE_IN_USE => "Device in use",
        ERR_INVALID_PROTOCOL => "Invalid protocol",
        ERR_TX_QUEUE_FULL => "Transmit queue full",
        138 => "Transmit queue corrupt",
        139 => "Receive queue full",
        140 => "Receive queue corrupt",
        ERR_MESSAGE_TOO_LONG => "Message too long",
        ERR_HARDWARE_NOT_RESPONDING => "Hardware not responding",
        ERR_COMMAND_NOT_SUPPORTED => "Command not supported",
        ERR_INVALID_COMMAND => "Invalid command",
        145 => "Transmit message status",
        ERR_ADDRESS_CLAIM_FAILED => "Address claim failed",
        147 => "Cannot set priority",
        ERR_CLIENT_DISCONNECTED => "Client disconnected",
        ERR_CONNECT_NOT_ALLOWED => "Connect not allowed",
        150 => "Change mode failed",
        ERR_BUS_OFF => "Bus off",
        ERR_COULD_NOT_TX_ADDRESS_CLAIMED => "Could not transmit address claimed",
        153 => "Address lost",
        154 => "Code not found",
        155 => "Block not allowed",
        156 => "Multiple clients connected",
        157 => "Address never claimed",
        158 => "Window handle required",
        ERR_MESSAGE_NOT_SENT => "Message not sent",
        160 => "Maximum notifications exceeded",
        161 => "Maximum filters exceeded",
        162 => "Hardware status change",
        _ => "Unknown error code",
    }
}
