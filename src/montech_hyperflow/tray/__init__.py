"""GTK tray indicator for montech-hyperflow.

This subpackage is the ONLY part of the project with GUI dependencies
(PyGObject, GTK 3, AyatanaAppIndicator3). The daemon never imports it, which
is what lets distro packaging split montech-hyperflow from
montech-hyperflow-tray without dragging GTK onto a headless machine.
"""
