"""
J1939-81 NAME (Address Claimed, PGN 60928) and source-address names.

decode(data)                      -> the NAME fields of an Address Claimed payload.
describe(name, db)                -> the fields with names from the J1939 database
                                     (industry group, vehicle system, function, manufacturer).
source_name(db, sa, ig, claim)    -> what a source address means: the claimed function when
                                     the device sent Address Claimed, else the preferred-address
                                     table for the industry group (global for 0-127 and 248-255).
"""

ADDRESS_CLAIMED = 60928          # 0xEE00
ON_HIGHWAY = 1


def decode(data):
    """NAME fields of an 8-byte Address Claimed payload (little-endian), or None."""
    if data is None or len(data) < 8:
        return None
    raw = int.from_bytes(bytes(data[:8]), "little")
    return {
        "raw": raw,
        "identity_number": raw & 0x1FFFFF,
        "manufacturer_code": (raw >> 21) & 0x7FF,
        "ecu_instance": (raw >> 32) & 0x7,
        "function_instance": (raw >> 35) & 0x1F,
        "function": (raw >> 40) & 0xFF,
        "vehicle_system": (raw >> 49) & 0x7F,
        "vehicle_system_instance": (raw >> 56) & 0xF,
        "industry_group": (raw >> 60) & 0x7,
        "arbitrary_address_capable": bool(raw >> 63),
    }


def industry_group_name(db, ig):
    return (db or {}).get("J1939IndustryGroupdb", {}).get(str(ig), f"Industry group {ig}")


def function_name(db, ig, vehicle_system, function):
    """Functions 0-127 are global; 128-254 depend on the industry group and vehicle system."""
    table = (db or {}).get("J1939Functiondb", {})
    if function < 128:
        name = table.get(str(function))
    else:
        name = table.get(f"{ig}_{vehicle_system}_{function}") or table.get(f"0_0_{function}")
    return name or f"Function {function}"


def describe(name, db=None):
    """Display fields of a decoded NAME (dict in the order the Component Information tab shows)."""
    ig, vs = name["industry_group"], name["vehicle_system"]
    vs_name = (db or {}).get("J1939VehicleSystemdb", {}).get(f"{ig}_{vs}", f"Vehicle system {vs}")
    mfr = (db or {}).get("J1939Manufacturerdb", {}).get(str(name["manufacturer_code"]), "")
    return {
        "NAME": f"{name['raw']:016X}",
        "Industry Group": f"{ig} {industry_group_name(db, ig)}",
        "Vehicle System": f"{vs} {vs_name}",
        "Vehicle System Instance": name["vehicle_system_instance"],
        "Function": f"{name['function']} {function_name(db, ig, vs, name['function'])}",
        "Function Instance": name["function_instance"],
        "ECU Instance": name["ecu_instance"],
        "Manufacturer": f"{name['manufacturer_code']} {mfr}".strip(),
        "Identity Number": name["identity_number"],
        "Arbitrary Address Capable": "Yes" if name["arbitrary_address_capable"] else "No",
    }


def claimed_name(name, db=None):
    """Short label from a claim, e.g. 'Engine #1' or 'Virtual Terminal #2'."""
    label = function_name(db, name["industry_group"], name["vehicle_system"], name["function"])
    return label + (f" #{name['function_instance'] + 1}" if name["function_instance"] else " #1")


def preferred_name(db, sa, ig=ON_HIGHWAY):
    """Name of a preferred source address. 128-247 are industry-group specific."""
    db = db or {}
    if 128 <= sa <= 247:
        by_ig = db.get("J1939SATabledbByIG", {})
        if str(ig) in by_ig:
            return by_ig[str(ig)].get(str(sa), f"IG{ig} address {sa}")
        if ig != ON_HIGHWAY:
            # The main table holds the on-highway (IG1) assignments; other groups need
            # J1939SATabledbByIG (create the database again with Tools > J1939 Database).
            return f"IG{ig} address {sa}"
    return db.get("J1939SATabledb", {}).get(str(sa), "Unknown")


def source_name(db, sa, ig=ON_HIGHWAY, claim=None):
    """Source address name: an Address Claimed NAME overrides the preferred-address table."""
    if sa == 254:
        return "Null address"
    if sa == 255:
        return "Global"
    if claim is not None:
        return claimed_name(claim, db) + " (claimed)"
    return preferred_name(db, sa, ig)
