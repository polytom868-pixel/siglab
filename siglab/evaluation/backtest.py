
from __future__ import annotations

import math




def convert_to_spot(prices: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    import pandas as pd

    funding = pd.DataFrame(0.0, index=prices.index, columns=prices.columns)
    return (prices.copy(), funding)


def _cagr_safe(begin_value: float, end_value: float, periods: int) -> float | None:
    if begin_value <= 0:
        return None
    if periods < 1:
        return None
    try:
        ratio = max(min(end_value / begin_value, 10000000000.0), 1e-10)
        cagr = math.pow(ratio, 1.0 / float(periods)) - 1.0
    except (OverflowError, FloatingPointError, ValueError):
        try:
            from decimal import Decimal, getcontext

            getcontext().prec = 28
            ratio_dec = Decimal(str(end_value)) / Decimal(str(begin_value))
            if ratio_dec <= 0:
                return None
            if ratio_dec > 10000000000.0:
                ratio_dec = Decimal("1e10")
            elif ratio_dec < 1e-10:
                ratio_dec = Decimal("1e-10")
            periods_dec = Decimal(str(periods))
            cagr = float(ratio_dec ** (Decimal(1) / periods_dec) - Decimal(1))
        except (ValueError, TypeError, ArithmeticError, ZeroDivisionError):
            return None
    return max(min(cagr, 100.0), -100.0)
