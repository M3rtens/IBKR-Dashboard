"""IBKR Portfolio Dashboard package.

The application is split into:
  theme         - colours, fonts and shared constants
  app_instance  - the singleton Dash app
  formatters    - number/value formatting helpers
  icons         - SVG icon bodies and image helpers
  charts        - Plotly figure builders and the SVG sparkline
  data          - the data layer (demo + live IBKR snapshots, news, history)
  shell         - app chrome (sidebar/topbar), layout assembly and the global
                  data-refresh callback
  pages.*       - one module per page, each owning its layout and callbacks

The entry point is the top-level ``app.py`` (run: ``python app.py``).
"""
