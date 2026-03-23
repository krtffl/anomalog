"""Capacity prediction via AutoARIMA and ARIMA fallback."""

from __future__ import annotations

import warnings
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    pass

MIN_DATA_POINTS = 72


def _check_pro_dependencies() -> None:
    """Verify that pro dependencies are installed."""
    try:
        import statsforecast  # noqa: F401
        import statsmodels  # noqa: F401
    except ImportError as e:
        msg = (
            "Capacity prediction requires the 'pro' extras. "
            "Install with: pip install anomalog[pro]"
        )
        raise ImportError(msg) from e


def _resample_to_5min(
    timestamps: list[datetime], values: list[float]
) -> tuple[list[datetime], np.ndarray]:
    """Resample time series to 5-minute intervals, forward-fill max 3 gaps."""
    if not timestamps:
        return [], np.array([])

    # Sort by timestamp
    paired = sorted(zip(timestamps, values), key=lambda x: x[0])
    ts_sorted = [p[0] for p in paired]
    vals_sorted = [p[1] for p in paired]

    # Build 5-minute grid
    start = ts_sorted[0].replace(second=0, microsecond=0)
    start = start.replace(minute=(start.minute // 5) * 5)
    end = ts_sorted[-1]
    grid: list[datetime] = []
    current = start
    while current <= end:
        grid.append(current)
        current += timedelta(minutes=5)

    if not grid:
        return [], np.array([])

    # Map values to grid buckets (use last value in each bucket)
    bucket_vals: dict[datetime, float] = {}
    for t, v in zip(ts_sorted, vals_sorted):
        bucket = t.replace(second=0, microsecond=0)
        bucket = bucket.replace(minute=(bucket.minute // 5) * 5)
        bucket_vals[bucket] = v

    # Build result with forward-fill (max 3 gaps)
    result_ts: list[datetime] = []
    result_vals: list[float] = []
    gap_count = 0
    last_val: float | None = None

    for g in grid:
        if g in bucket_vals:
            result_ts.append(g)
            result_vals.append(bucket_vals[g])
            last_val = bucket_vals[g]
            gap_count = 0
        elif last_val is not None and gap_count < 3:
            result_ts.append(g)
            result_vals.append(last_val)
            gap_count += 1
        else:
            # Gap too large, reset
            gap_count += 1

    return result_ts, np.array(result_vals)


def _is_monotonically_increasing(values: np.ndarray, tolerance: float = 0.05) -> bool:
    """Check if values are mostly monotonically increasing."""
    if len(values) < 2:
        return False
    diffs = np.diff(values)
    increasing_fraction = np.sum(diffs >= 0) / len(diffs)
    return bool(increasing_fraction >= (1.0 - tolerance))


def train_model(
    timestamps: list[datetime],
    values: list[float],
    metric_hint: str = "",
) -> tuple[object, str, float] | None:
    """Train a prediction model on the time series.

    Returns (model, model_type, rmse) or None if insufficient data.
    """
    _check_pro_dependencies()

    resampled_ts, resampled_vals = _resample_to_5min(timestamps, values)

    if len(resampled_vals) < MIN_DATA_POINTS:
        return None

    # Decide model type based on hints
    use_arima = False
    hint_lower = metric_hint.lower()
    if "disk" in hint_lower or "storage" in hint_lower or "capacity" in hint_lower:
        use_arima = True
    elif _is_monotonically_increasing(resampled_vals):
        use_arima = True

    # Split into train/test (90/10)
    split_idx = int(len(resampled_vals) * 0.9)
    train_vals = resampled_vals[:split_idx]
    test_vals = resampled_vals[split_idx:]

    if use_arima:
        model, model_type, rmse = _train_arima(train_vals, test_vals)
    else:
        model, model_type, rmse = _train_autoarima(train_vals, test_vals)

    return model, model_type, rmse


def _train_arima(
    train_vals: np.ndarray, test_vals: np.ndarray
) -> tuple[object, str, float]:
    """Train ARIMA(1,1,0) for trending metrics."""
    from statsmodels.tsa.arima.model import ARIMA

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        model = ARIMA(train_vals, order=(1, 1, 0))
        fitted = model.fit()

    # Compute RMSE on test set
    if len(test_vals) > 0:
        forecast = fitted.forecast(steps=len(test_vals))
        rmse = float(np.sqrt(np.mean((forecast - test_vals) ** 2)))
    else:
        rmse = 0.0

    return fitted, "arima", rmse


def _train_autoarima(
    train_vals: np.ndarray, test_vals: np.ndarray
) -> tuple[object, str, float]:
    """Train AutoARIMA for general metrics."""
    from statsforecast import StatsForecast
    from statsforecast.models import AutoARIMA

    import pandas as pd

    # StatsForecast expects a DataFrame with columns: unique_id, ds, y
    ds = pd.date_range("2020-01-01", periods=len(train_vals), freq="5min")
    df = pd.DataFrame({
        "unique_id": ["metric"] * len(train_vals),
        "ds": ds,
        "y": train_vals,
    })

    sf = StatsForecast(
        models=[AutoARIMA(season_length=12)],  # 12 * 5min = 1 hour seasonality
        freq="5min",
    )

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        sf.fit(df)

    # Compute RMSE on test set
    if len(test_vals) > 0:
        forecast_df = sf.predict(h=len(test_vals))
        forecast_vals = forecast_df["AutoARIMA"].values
        rmse = float(np.sqrt(np.mean((forecast_vals - test_vals) ** 2)))
    else:
        rmse = 0.0

    return sf, "autoarima", rmse


def predict(
    model: object, model_type: str, horizon_hours: int
) -> list[tuple[str, float]]:
    """Generate predictions for the given horizon.

    Returns list of (iso_timestamp, predicted_value) tuples.
    """
    steps = horizon_hours * 12  # 5-min intervals per hour

    now = datetime.now(timezone.utc)

    if model_type == "arima":
        forecast = model.forecast(steps=steps)  # type: ignore[union-attr]
        results = []
        for i, val in enumerate(forecast):
            ts = now + timedelta(minutes=5 * (i + 1))
            results.append((ts.isoformat(), float(val)))
        return results

    elif model_type == "autoarima":
        forecast_df = model.predict(h=steps)  # type: ignore[union-attr]
        forecast_vals = forecast_df["AutoARIMA"].values
        results = []
        for i, val in enumerate(forecast_vals):
            ts = now + timedelta(minutes=5 * (i + 1))
            results.append((ts.isoformat(), float(val)))
        return results

    msg = f"Unknown model type: {model_type}"
    raise ValueError(msg)
