"""Minimal ABI helpers for the EVM adapters (standard library only).
Only what the adapters read: static words, addresses, signed ints, dynamic strings and address arrays from eth_call
results and log data, plus argument encoding for address / uint calls. Hex strings in, Python values out."""

ZERO = "0x" + "0" * 40
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"   # Transfer(address,address,uint256)


def words(h):
    """'0x' + n×64 hex → list of 64-hex-char words (no prefix)."""
    h = h[2:] if h.startswith("0x") else h
    return [h[i:i + 64] for i in range(0, len(h) - len(h) % 64, 64)]


def u256(w):
    return int(w, 16)


def i256(w):
    x = int(w, 16)
    return x - (1 << 256) if x >= (1 << 255) else x


def addr(w):
    """Address from a 32-byte word or topic (last 20 bytes), lowercase."""
    return "0x" + w[-40:].lower()


def topic_for(address):
    """Indexed address topic for log filters."""
    return "0x" + "0" * 24 + address[2:].lower()


def enc_addr(address):
    return address[2:].lower().rjust(64, "0")


def enc_uint(n):
    return format(n, "x").rjust(64, "0")


def call_data(selector, *args):
    """selector + static args (addresses as '0x…' strings, ints as uint256)."""
    return selector + "".join(enc_addr(a) if isinstance(a, str) else enc_uint(a) for a in args)


def dyn_string(ws, i):
    """String at head word i (offset in bytes from the start of the encoded tuple)."""
    off = u256(ws[i]) // 32
    n = u256(ws[off])
    raw = bytes.fromhex("".join(ws[off + 1: off + 1 + (n + 31) // 32]))[:n]
    return raw.decode(errors="replace")


def dyn_addresses(ws, i):
    """address[] at head word i."""
    off = u256(ws[i]) // 32
    n = u256(ws[off])
    return [addr(w) for w in ws[off + 1: off + 1 + n]]


def string_result(h):
    """eth_call result of a string getter (name(), symbol()); also handles bytes32 tokens. Empty → ''."""
    if not h or h == "0x":
        return ""
    b = bytes.fromhex(h[2:])
    if len(b) >= 96:
        n = int.from_bytes(b[32:64], "big")
        return b[64:64 + n].decode(errors="replace")
    return b.rstrip(b"\0").decode(errors="replace")
