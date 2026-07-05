"""
IBKR Portfolio Dashboard — entry point.

Run:  python app.py
Open: http://127.0.0.1:8050

DASH_DEMO=1  -> built-in demo data (no TWS/Gateway required).

The application is organised as the ``dashboard`` package:
  dashboard.theme / formatters / icons / charts / data  - shared layers
  dashboard.shell                                       - chrome, layout,
                                                          navigation + the
                                                          global refresh callback
  dashboard.pages.*                                     - one module per page
This file only loads ``.env``, builds the layout and runs the server.
"""

import os

# --- Load .env file manually (must run before importing dashboard.* so that
# DEMO_MODE / REFRESH / IBKR_* are read from the environment) -----------------
if os.path.exists(".env"):
    with open(".env", encoding="utf-8") as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                os.environ[_k.strip()] = _v.strip()

os.environ["FLASK_SKIP_DOTENV"] = "1"

from dashboard.app_instance import app, server  # noqa: E402
from dashboard.theme import DEMO_MODE, REFRESH  # noqa: E402
# Importing the shell pulls in every page module, which registers all callbacks.
from dashboard import shell  # noqa: E402

app.layout = shell.build_layout()


if __name__ == "__main__":
    print(f"Starting IBKR Portfolio Dashboard (demo={DEMO_MODE}, refresh={REFRESH}s)")
    print("Open http://127.0.0.1:8050")
    app.run(debug=True, host="127.0.0.1", dev_tools_ui=False, port=8050)
