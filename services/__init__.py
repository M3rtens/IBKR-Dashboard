"""Backend service layer for the IBKR dashboard.

Non-UI modules that talk to external systems or perform analysis, kept separate
from the ``dashboard`` presentation package:

  services.ibkr_client  - thread-safe IBKR TWS/Gateway API wrapper (ib_insync)
  services.flex_query    - IBKR Flex Query loader (history, dividends, trades)
  services.optimizer     - portfolio optimisation methods
"""
