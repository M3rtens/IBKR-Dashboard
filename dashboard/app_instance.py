"""The singleton Dash application instance.

Page and shell modules import ``app`` from here to register their callbacks,
which avoids a circular dependency with the top-level entry point.

Custom CSS lives in ``assets/style.css`` (auto-loaded by Dash), so no
``index_string`` override is needed here.
"""

from dash import Dash

from dashboard.theme import EXTERNAL_SS

app = Dash(
    __name__,
    external_stylesheets=EXTERNAL_SS,
    title="IBKR Portfolio",
    update_title=None,
    assets_folder="../assets",
)
server = app.server
