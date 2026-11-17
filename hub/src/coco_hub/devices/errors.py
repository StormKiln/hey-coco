"""Typed device errors every backend raises, so intents can say exactly what went wrong."""


class DeviceError(Exception):
    """Base class. ``str(exc)`` is for logs, never shown with secrets in it."""


class Unreachable(DeviceError):
    """The device didn't answer (off, wrong address, other subnet, or timed out)."""


class AuthRequired(DeviceError):
    """The device needs a login the Hub doesn't have, or rejected the one it has."""


class UnknownDevice(DeviceError):
    """No device (or outlet) by that name."""


class Unsupported(DeviceError):
    """The device can't do what was asked, or the backend doesn't support the device."""
