"""Event handler registrations.

Importing this package (main.py does it once) registers every handler on
the bus as an import side effect — the `@bus.on(...)` decorators run here.

To add a new automatic behavior: create a module in this package, subscribe
with @bus.on(YourEvent), and add it to the imports below.
"""

from __future__ import annotations

from . import generation, ingestion, study  # noqa: F401
