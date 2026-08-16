"""Exception types shared across the forecasting system."""

from __future__ import annotations


class ForecastSystemError(RuntimeError):
    """Base class for every error raised by this system."""


class AsOfLeakError(ForecastSystemError):
    """Raised when material published after the point-in-time cutoff is reached.

    This is always a hard failure. A leak silently invalidates both the backtest
    calibration and the reproducibility guarantee, so it must never be downgraded
    to a warning.
    """


class GuardNotConfiguredError(ForecastSystemError):
    """Raised when a retrieval is attempted without an active point-in-time guard.

    Every retrieval path in the system asks for the active guard before returning
    documents. Failing loudly here is what makes the cutoff impossible to bypass
    by accident.
    """


class ConfigurationError(ForecastSystemError):
    """Raised when the challenge specification or runtime settings are unusable."""


class BudgetExhaustedError(ForecastSystemError):
    """Raised when the configured spend ceiling is reached.

    Callers are expected to catch this and degrade to a deterministic path rather
    than aborting: an empty forecast cell scores the maximum penalty, so a weak
    estimate always beats no estimate.
    """
