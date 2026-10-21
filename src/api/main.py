"""Application entrypoint; original six-table API retained under legacy profile."""
from src import config
if config.PROJECT_PROFILE == "legacy":
    from src.api.legacy import app
else:
    from src.petkit.api import app
