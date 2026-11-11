"""Exceptions used by the OBD core library (no Home Assistant imports)."""

from __future__ import annotations


class OBDError(Exception):
    """Base class for all OBD core errors."""


class TransportError(OBDError):
    """Low level transport problem (Bluetooth / TCP)."""


class TransportUnavailable(TransportError):
    """The adapter is not reachable at the moment (out of range / asleep)."""


class AdapterTimeout(OBDError):
    """The adapter did not answer with a prompt within the timeout."""


class AdapterError(OBDError):
    """The adapter answered with an error (e.g. '?', 'CAN ERROR')."""


class NoDataError(OBDError):
    """The vehicle did not answer a request (ELM327 'NO DATA')."""


class VehicleUnreachable(OBDError):
    """The adapter could not talk to any ECU (ignition off / bus asleep)."""


class ForbiddenCommand(OBDError):
    """A command was blocked because it is not a read-only command."""


class ResponseParseError(OBDError):
    """A response was corrupt, incomplete or could not be decoded."""
