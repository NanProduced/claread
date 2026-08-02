"""Compatibility import for the relocated Daily Reader workflow."""

from app.services.daily_reader import workflow as _workflow

# Preserve the old test/tooling import surface, including intentionally
# private structural helpers, while production imports the neutral module.
for _name, _value in vars(_workflow).items():
    if not _name.startswith("__"):
        globals()[_name] = _value
