"""Drive the display on a Montech HyperFlow Digital AIO pump head.

Protocol reverse-engineered from the vendor Windows app and corrected against
the device's own HID report descriptor. See docs/PROTOCOL.md.

The daemon half of this package is deliberately standard-library only: no
hidapi, no libusb, no pyudev, no D-Bus binding. GUI dependencies live in
`montech_hyperflow.tray` and are never imported by the daemon.
"""

__version__ = "1.0.0"
__all__ = ["__version__"]
