"""
Minimal SNMPv2c GET client — stdlib only (socket + struct-free BER codec).

Enough to poll printer MIBs (model, page count, toner levels) without pysnmp.
Not a general SNMP library: GET requests only, single community string.
"""

import random
import socket

# ---------------------------------------------------------------- BER encode
TAG_SEQ, TAG_INT, TAG_OCT, TAG_NULL, TAG_OID = 0x30, 0x02, 0x04, 0x05, 0x06
TAG_GETREQ, TAG_RESPONSE = 0xA0, 0xA2


def _len(n):
    if n < 128:
        return bytes([n])
    b = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(b)]) + b


def _tlv(tag, content):
    return bytes([tag]) + _len(len(content)) + content


def enc_int(n):
    if n == 0:
        return _tlv(TAG_INT, b"\x00")
    b = n.to_bytes((n.bit_length() + 7) // 8, "big")
    if b[0] & 0x80:
        b = b"\x00" + b
    return _tlv(TAG_INT, b)


def enc_oid(dotted):
    arcs = [int(a) for a in dotted.strip(".").split(".")]
    out = bytes([40 * arcs[0] + arcs[1]])
    for a in arcs[2:]:
        stack = [a & 0x7F]
        a >>= 7
        while a:
            stack.append((a & 0x7F) | 0x80)
            a >>= 7
        out += bytes(reversed(stack))
    return _tlv(TAG_OID, out)


def build_get(oids, community="public", request_id=None):
    rid = request_id if request_id is not None else random.randint(1, 2 ** 31 - 1)
    varbinds = b"".join(
        _tlv(TAG_SEQ, enc_oid(o) + _tlv(TAG_NULL, b"")) for o in oids)
    pdu = _tlv(TAG_GETREQ, enc_int(rid) + enc_int(0) + enc_int(0)
               + _tlv(TAG_SEQ, varbinds))
    return (_tlv(TAG_SEQ, enc_int(1) + _tlv(TAG_OCT, community.encode())
                 + pdu), rid)


# ---------------------------------------------------------------- BER decode
class BERReader:
    def __init__(self, data):
        self.d, self.p = data, 0

    def _read(self, n):
        b = self.d[self.p:self.p + n]
        self.p += n
        return b

    def tlv(self):
        tag = self._read(1)[0]
        ln = self._read(1)[0]
        if ln & 0x80:
            ln = int.from_bytes(self._read(ln & 0x7F), "big")
        return tag, self._read(ln)


def dec_oid(raw):
    arcs = [raw[0] // 40, raw[0] % 40]
    val, i = 0, 1
    while i < len(raw):
        val = (val << 7) | (raw[i] & 0x7F)
        if not raw[i] & 0x80:
            arcs.append(val)
            val = 0
        i += 1
    return ".".join(str(a) for a in arcs)


def dec_value(tag, raw):
    if tag == TAG_INT:
        n = int.from_bytes(raw, "big")
        if raw and raw[0] & 0x80:  # negative
            n -= 1 << (len(raw) * 8)
        return n
    if tag == TAG_OCT:
        try:
            return raw.decode("utf-8", "replace")
        except Exception:
            return raw.hex()
    if tag == TAG_OID:
        return dec_oid(raw)
    if tag == TAG_NULL:
        return None
    if tag == 0x41:  # Counter32
        return int.from_bytes(raw, "big")
    if tag == 0x42:  # Gauge32
        return int.from_bytes(raw, "big")
    if tag == 0x43:  # TimeTicks
        return int.from_bytes(raw, "big")
    if tag == 0x44:  # Opaque
        return raw.hex()
    if tag in (0x80, 0x81, 0x82):  # noSuchObject / noSuchInstance / endOfMibView
        return None
    return raw.hex() if isinstance(raw, bytes) else raw


def parse_response(data, request_id):
    """Returns {oid: value} for a GET response. Raises on mismatch/error."""
    r = BERReader(data)
    tag, msg = r.tlv()
    if tag != TAG_SEQ:
        raise ValueError("bad message tag")
    m = BERReader(msg)
    vtag, vraw = m.tlv()
    ctag, craw = m.tlv()
    ptag, praw = m.tlv()
    if ptag != TAG_RESPONSE:
        raise ValueError("not a response PDU (tag 0x%02x)" % ptag)
    p = BERReader(praw)
    p.tlv()  # request-id (could verify)
    p.tlv()  # error-status
    p.tlv()  # error-index
    _, vbraw = p.tlv()
    out = {}
    vbs = BERReader(vbraw)
    while vbs.p < len(vbraw):
        _, one = vbs.tlv()
        b = BERReader(one)
        _, oraw = b.tlv()
        vtag2, vraw2 = b.tlv()
        out[dec_oid(oraw)] = dec_value(vtag2, vraw2)
    return out


# ------------------------------------------------------------------ client --
class SNMPError(Exception):
    pass


def get(host, oids, community="public", timeout=4, port=161):
    """SNMPv2c GET. Returns {oid: value}. Raises SNMPError on failure."""
    if isinstance(oids, str):
        oids = [oids]
    pkt, rid = build_get(oids, community)
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(timeout)
    try:
        s.sendto(pkt, (host, port))
        data, _ = s.recvfrom(65535)
    except socket.timeout:
        raise SNMPError("timeout (no SNMP agent at %s?)" % host)
    except Exception as exc:
        raise SNMPError(str(exc)[:80])
    finally:
        s.close()
    try:
        return parse_response(data, rid)
    except Exception as exc:
        raise SNMPError("bad response: %s" % exc)
