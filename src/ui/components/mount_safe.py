"""Small helpers for UI updates that may race with Flet navigation.

``Control.page`` is not a nullable property in Flet.  It raises a
``RuntimeError`` while a control is detached (for example, immediately after
``Page.clean()``).  Background parser and telemetry callbacks can legitimately
arrive during that window, so presentation updates must be best effort while
the state changes that precede them remain authoritative.
"""

from typing import Any


def safe_update(control: Any) -> bool:
    """Update a mounted control, returning ``False`` when it is detached.

    The narrow ``RuntimeError`` handling is intentional: it covers Flet's
    detached-control behavior, including a detach race between the probe and
    update, without hiding unrelated errors raised by the update itself.
    """

    try:
        control.page
    except RuntimeError:
        return False

    try:
        control.update()
    except RuntimeError as exc:
        # Control.update() probes .page a second time, so navigation can race
        # the probe above. Preserve all other RuntimeErrors for diagnostics.
        if "Control must be added to the page first" in str(exc):
            return False
        raise
    return True


def mounted_page(control: Any) -> Any:
    """Return a control's page, or ``None`` while the control is detached."""

    try:
        return control.page
    except RuntimeError:
        return None
