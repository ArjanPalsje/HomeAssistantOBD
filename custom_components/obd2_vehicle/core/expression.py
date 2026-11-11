"""Safe formula evaluation for vehicle profiles.

Profile formulas use the WiCAN byte notation so expressions can be copied
verbatim from the documented WiCAN vehicle profiles (meatpiHQ/wican-fw):

* ``B10``       unsigned byte 10
* ``S17``       signed byte 17
* ``[B19:B20]`` big-endian unsigned integer spanning bytes 19..20
* ``[S11:S14]`` big-endian signed integer spanning bytes 11..14
* ``B15:7``     bit 7 of byte 15 (0/1)

Byte indices refer to the *raw ISO-TP frame bytes without CAN ID* (WiCAN
convention): for a single frame ``B0`` is the ISO-TP length byte, ``B1`` the
positive response SID (0x62), ``B2..B3`` the DID and ``B4`` the first data
byte. In multi-frame answers every 8th byte (B8, B16, …) is the PCI byte of a
consecutive frame. :func:`to_wican_bytes` rebuilds that layout from the
reassembled payload, which is deterministic for classic CAN (8 byte frames).

Formulas are parsed into a Python AST and only arithmetic, bit operators,
numeric constants, the byte helpers above, previously computed value names
and the functions ``min``, ``max``, ``abs`` and ``round`` are allowed. No
``eval`` of arbitrary code takes place.
"""

from __future__ import annotations

import ast
import operator
import re
from collections.abc import Mapping

from .exceptions import ResponseParseError

_MULTI = re.compile(r"\[\s*([BS])(\d+)\s*:\s*[BS]?(\d+)\s*\]")
_BIT = re.compile(r"\bB(\d+):(\d+)\b")
_SIGNED = re.compile(r"\bS(\d+)\b")
_BYTE = re.compile(r"\bB(\d+)\b")

_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.LShift: operator.lshift,
    ast.RShift: operator.rshift,
    ast.BitAnd: operator.and_,
    ast.BitOr: operator.or_,
    ast.BitXor: operator.xor,
}
_UNARY_OPS = {ast.USub: operator.neg, ast.UAdd: operator.pos, ast.Invert: operator.invert}
_FUNCS = {"min": min, "max": max, "abs": abs, "round": round}
_BYTE_FUNCS = {"_b", "_s", "_bit", "_multi"}


def to_wican_bytes(payload: bytes) -> bytes:
    """Rebuild the ISO-TP frame byte layout (without CAN IDs) of a payload."""
    if len(payload) <= 7:
        return bytes([len(payload)]) + payload
    out = bytearray([0x10 | ((len(payload) >> 8) & 0x0F), len(payload) & 0xFF])
    out.extend(payload[:6])
    seq = 1
    for start in range(6, len(payload), 7):
        out.append(0x20 | seq)
        out.extend(payload[start : start + 7])
        seq = (seq + 1) & 0x0F
    return bytes(out)


def _translate(expr: str) -> str:
    text = _MULTI.sub(lambda m: f"_multi({int(m[1] == 'S')},{int(m[2])},{int(m[3])})", expr)
    text = _BIT.sub(lambda m: f"_bit({int(m[1])},{int(m[2])})", text)
    text = _SIGNED.sub(lambda m: f"_s({int(m[1])})", text)
    text = _BYTE.sub(lambda m: f"_b({int(m[1])})", text)
    return text


class Formula:
    """A compiled, validated formula."""

    __slots__ = ("source", "_tree", "max_index", "names")

    def __init__(self, source: str) -> None:
        self.source = source
        try:
            tree = ast.parse(_translate(source), mode="eval")
        except SyntaxError as err:
            raise ValueError(f"Invalid formula {source!r}: {err}") from err
        self.max_index = -1
        self.names: set[str] = set()
        self._validate(tree.body)
        self._tree = tree.body

    def _validate(self, node: ast.AST) -> None:
        if isinstance(node, ast.Constant):
            if not isinstance(node.value, (int, float)) or isinstance(node.value, bool):
                raise ValueError(f"Only numeric constants allowed in {self.source!r}")
        elif isinstance(node, ast.BinOp):
            if type(node.op) not in _BIN_OPS:
                raise ValueError(f"Operator not allowed in {self.source!r}")
            self._validate(node.left)
            self._validate(node.right)
        elif isinstance(node, ast.UnaryOp):
            if type(node.op) not in _UNARY_OPS:
                raise ValueError(f"Operator not allowed in {self.source!r}")
            self._validate(node.operand)
        elif isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.keywords:
                raise ValueError(f"Call not allowed in {self.source!r}")
            name = node.func.id
            if name in _BYTE_FUNCS:
                for arg in node.args:
                    if not isinstance(arg, ast.Constant):
                        raise ValueError(f"Byte index must be constant in {self.source!r}")
                idx = [a.value for a in node.args]  # type: ignore[attr-defined]
                self.max_index = max(self.max_index, idx[2] if name == "_multi" else idx[0])
            elif name in _FUNCS:
                for arg in node.args:
                    self._validate(arg)
            else:
                raise ValueError(f"Function {name!r} not allowed in {self.source!r}")
        elif isinstance(node, ast.Name):
            if not re.fullmatch(r"[a-z][a-z0-9_]*", node.id):
                raise ValueError(f"Invalid name {node.id!r} in {self.source!r}")
            self.names.add(node.id)
        else:
            raise ValueError(f"Unsupported syntax in {self.source!r}: {type(node).__name__}")

    def evaluate(self, raw: bytes | None = None, values: Mapping[str, object] | None = None) -> float:
        """Evaluate against WiCAN-layout bytes and/or previously computed values."""
        data = raw or b""
        if self.max_index >= len(data):
            raise ResponseParseError(
                f"Response too short for {self.source!r}: needs byte {self.max_index}, got {len(data)}"
            )
        env = values or {}

        def ev(node: ast.AST) -> float:
            if isinstance(node, ast.Constant):
                return node.value  # type: ignore[return-value]
            if isinstance(node, ast.BinOp):
                return _BIN_OPS[type(node.op)](ev(node.left), ev(node.right))
            if isinstance(node, ast.UnaryOp):
                return _UNARY_OPS[type(node.op)](ev(node.operand))
            if isinstance(node, ast.Name):
                value = env.get(node.id)
                if value is None:
                    raise ResponseParseError(f"Value {node.id!r} not available")
                return float(value)  # type: ignore[arg-type]
            assert isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            name = node.func.id
            if name == "_b":
                return data[node.args[0].value]  # type: ignore[attr-defined]
            if name == "_s":
                byte = data[node.args[0].value]  # type: ignore[attr-defined]
                return byte - 256 if byte > 127 else byte
            if name == "_bit":
                idx, bit = (a.value for a in node.args)  # type: ignore[attr-defined]
                return (data[idx] >> bit) & 1
            if name == "_multi":
                signed, start, end = (a.value for a in node.args)  # type: ignore[attr-defined]
                return int.from_bytes(data[start : end + 1], "big", signed=bool(signed))
            return _FUNCS[name](*(ev(a) for a in node.args))

        try:
            return ev(self._tree)
        except ZeroDivisionError as err:
            raise ResponseParseError(f"Division by zero in {self.source!r}") from err
