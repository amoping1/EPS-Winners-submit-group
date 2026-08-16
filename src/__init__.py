"""Agents vs Wall Street forecasting system.

Pipeline overview::

    orchestrator -> [HD, ADI, HAS, DE] in parallel
                      A1 sector & context
                      A2 quantitative model
                      A3 reasoning & forecast
                      A4 validation
                    -> workbook writer -> submission/*.xlsx

Every retrieval in the system runs behind the point-in-time guard in
:mod:`src.asof`, which is what makes the backtest honest and the competition run
reproducible after the event.
"""

from __future__ import annotations

__version__ = "0.1.0"
