"""Keccak-256 (the Ethereum variant: original Keccak padding 0x01, not NIST SHA3's 0x06), standard library only.
Used to derive and verify function selectors and event topics from their Solidity signatures, so that the adapters'
constants are checked by tests instead of being trusted magic strings. Not used on hot paths."""

_RC = [
    0x0000000000000001, 0x0000000000008082, 0x800000000000808A, 0x8000000080008000, 0x000000000000808B,
    0x0000000080000001, 0x8000000080008081, 0x8000000000008009, 0x000000000000008A, 0x0000000000000088,
    0x0000000080008009, 0x000000008000000A, 0x000000008000808B, 0x800000000000008B, 0x8000000000008089,
    0x8000000000008003, 0x8000000000008002, 0x8000000000000080, 0x000000000000800A, 0x800000008000000A,
    0x8000000080008081, 0x8000000000008080, 0x0000000080000001, 0x8000000080008008,
]
_ROT = [[0, 36, 3, 41, 18], [1, 44, 10, 45, 2], [62, 6, 43, 15, 61], [28, 55, 25, 21, 56], [27, 20, 39, 8, 14]]
_M = (1 << 64) - 1
_RATE = 136   # bytes: 1600-bit state, 512-bit capacity


def _rotl(x, n):
    return ((x << n) | (x >> (64 - n))) & _M if n else x


def _f(a):
    """Keccak-f[1600] permutation on a 5×5 lane state a[x][y]."""
    for rc in _RC:
        c = [a[x][0] ^ a[x][1] ^ a[x][2] ^ a[x][3] ^ a[x][4] for x in range(5)]
        d = [c[(x - 1) % 5] ^ _rotl(c[(x + 1) % 5], 1) for x in range(5)]
        a = [[a[x][y] ^ d[x] for y in range(5)] for x in range(5)]
        b = [[0] * 5 for _ in range(5)]
        for x in range(5):
            for y in range(5):
                b[y][(2 * x + 3 * y) % 5] = _rotl(a[x][y], _ROT[x][y])
        a = [[b[x][y] ^ ((~b[(x + 1) % 5][y]) & b[(x + 2) % 5][y]) for y in range(5)] for x in range(5)]
        a[0][0] ^= rc
    return a


def keccak256(data: bytes) -> bytes:
    msg = bytearray(data) + b"\x01"
    msg += b"\x00" * (-len(msg) % _RATE)
    msg[-1] |= 0x80
    a = [[0] * 5 for _ in range(5)]
    for off in range(0, len(msg), _RATE):
        block = msg[off:off + _RATE]
        for i in range(_RATE // 8):
            a[i % 5][i // 5] ^= int.from_bytes(block[8 * i:8 * i + 8], "little")
        a = _f(a)
    out = b"".join(a[i % 5][i // 5].to_bytes(8, "little") for i in range(25))
    return out[:32]


def topic(signature: str) -> str:
    """Event topic0 of a canonical signature, e.g. topic("Transfer(address,address,uint256)")."""
    return "0x" + keccak256(signature.encode()).hex()


def selector(signature: str) -> str:
    """4-byte function selector, e.g. selector("balanceOf(address)") == "0x70a08231"."""
    return "0x" + keccak256(signature.encode())[:4].hex()
