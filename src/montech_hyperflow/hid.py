"""HID report-descriptor parsing and the hidraw ioctl.

Nothing here is Montech-specific.
"""

import fcntl

_MAIN_INPUT, _MAIN_OUTPUT, _MAIN_FEATURE = 0x8, 0x9, 0xB
_MAIN_COLLECTION, _MAIN_END_COLLECTION = 0xA, 0xC
_MAIN_KIND = {_MAIN_INPUT: "input", _MAIN_OUTPUT: "output",
              _MAIN_FEATURE: "feature"}


def parse_report_descriptor(rd):
    """Walk a HID report descriptor.

    Returns {(usage_page, usage, report_id, kind): data_bytes} for every
    Input/Output/Feature declared, keyed by the enclosing top-level
    application collection. Malformed or truncated input is tolerated: the
    walk stops, it never raises.

    Do not be tempted to substring-search a descriptor instead. It is a
    self-delimiting item stream, so the *data* bytes of one item can spell the
    header of another -- a four-byte Logical Maximum of 0xFF0106FF contains
    "06 01 FF" while declaring no vendor page at all.
    """
    g = {"usage_page": 0, "report_size": 0, "report_count": 0, "report_id": 0}
    gstack = []
    usages = []
    collections = []
    bits = {}
    i = 0
    while i < len(rd):
        b = rd[i]
        i += 1
        if b == 0xFE:                       # long item: [0xFE][size][tag][data]
            if i + 1 >= len(rd):
                break
            i += 2 + rd[i]
            continue
        size = b & 0x03
        if size == 3:
            size = 4
        item_type = (b >> 2) & 0x03
        tag = (b >> 4) & 0x0F
        if i + size > len(rd):
            break
        data = int.from_bytes(rd[i:i + size], "little") if size else 0
        i += size

        if item_type == 1:                                      # Global
            if tag == 0x0:
                g["usage_page"] = data
            elif tag == 0x7:
                g["report_size"] = data
            elif tag == 0x8:
                g["report_id"] = data
            elif tag == 0x9:
                g["report_count"] = data
            elif tag == 0xA:                                    # Push
                gstack.append(dict(g))
            elif tag == 0xB:                                    # Pop
                if gstack:
                    g = gstack.pop()
        elif item_type == 2:                                    # Local
            if tag == 0x0:                                      # Usage
                if size == 4:      # a 4-byte Usage carries its page up top
                    usages.append((data >> 16, data & 0xFFFF))
                else:
                    usages.append((g["usage_page"], data))
        else:                                                   # Main
            if tag == _MAIN_COLLECTION:
                collections.append(usages[0] if usages
                                   else (g["usage_page"], 0))
            elif tag == _MAIN_END_COLLECTION:
                if collections:
                    collections.pop()
            elif tag in _MAIN_KIND:
                page, usage = (collections[0] if collections
                               else (g["usage_page"], 0))
                key = (page, usage, g["report_id"], _MAIN_KIND[tag])
                bits[key] = bits.get(key, 0) + \
                    g["report_size"] * g["report_count"]
            usages = []
    return {k: v // 8 for k, v in bits.items()}


def _ioc(direction, type_char, nr, size):
    return (direction << 30) | (size << 16) | (ord(type_char) << 8) | nr


def HIDIOCSFEATURE(size):
    """_IOC(_IOC_WRITE|_IOC_READ, 'H', 0x06, len)"""
    return _ioc(3, "H", 0x06, size)


def send_feature(fd, payload):
    """Issue SET_REPORT(Feature) with exactly len(payload) bytes on the wire.

    The size is taken from the buffer so the ioctl size field and the buffer
    length can never drift apart.
    """
    buf = bytearray(payload)
    fcntl.ioctl(fd, HIDIOCSFEATURE(len(buf)), buf)
