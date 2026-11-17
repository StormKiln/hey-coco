"""Device backends. Each brand/protocol lives in its own module here.

``BACKENDS`` maps the ``backend = "..."`` name used in ``[[devices]]`` to its class, imported
only when a configured device uses it (so an unused backend's library is never loaded).
"""

import importlib

from coco_hub.devices.backend import DeviceBackend

BACKENDS: dict[str, str] = {
    "kasa": "coco_hub.devices.backends.kasa:KasaBackend",
}


def backend_class(name: str) -> type[DeviceBackend]:
    """The class for ``name``. Raises ``ValueError`` for an unknown backend."""
    try:
        path = BACKENDS[name]
    except KeyError:
        known = ", ".join(sorted(BACKENDS))
        raise ValueError(f"unknown device backend {name!r} (known: {known})") from None
    module, _, attr = path.partition(":")
    return getattr(importlib.import_module(module), attr)
