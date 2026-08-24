from __future__ import annotations

import numpy as np
import pandas as pd

from .config import Agent1Config

CANDLE_COLUMNS = ("cdl_bull_count_3", "cdl_bear_count_3")

"""
    Score every bar for bullish and bearish candlestick patterns.
    Ts function checks 10 patterns: 5 for bullish ; 5 for bearish signals

    Returns two integer Series aligned to ``bars.index``: how many of the five
    bullish patterns fired on each bar, and how many bearish.  Not which ones —
    the identity is discarded on purpose, see the module docstring.

"""
def _native_signals(bars: pd.DataFrame):
    """
    Score every bar for bullish and bearish candlestick patterns.

    Returns two integer Series aligned to ``bars.index``: how many of the five
    bullish patterns fired on each bar, and how many bearish.  Not which ones —
    the identity is discarded on purpose, see the module docstring.

    """
    o = bars["open"].astype(float)
    h = bars["high"].astype(float)
    l = bars["low"].astype(float)
    c = bars["close"].astype(float)

    body = (c - o).abs()
    rng = (h - l).replace(0.0, np.nan) #replaces zero range with NaN to avoid division by zero
    upper = h - c.combine(o, max) # c is combined with o to get the max: finds the upper shadow length
    lower = c.combine(o, min) - l # finds the lower shadow length
    bull_bar = c > o
    bear_bar = c < o

    o1, c1, h1, l1 = o.shift(1), c.shift(1), h.shift(1), l.shift(1) # На одну свечу назад
    body1 = (c1 - o1).abs()
    o2, c2 = o.shift(2), c.shift(2) # На две свечи назад
    body2 = (c2 - o2).abs()
    mid2 = (o2 + c2) / 2.0
    avg_body = body.rolling(14, min_periods=14).mean() # Скользящее среднее тела свечи за последние 14 свечей

    bull = {
        "engulfing": (c1 < o1) & bull_bar & (c >= o1) & (o <= c1), # Зелёная свеча полностью «съедает» тело предыдущей красной
        "hammer": (lower >= 2 * body) & (upper <= body) & (body > 0), # Молот — длинная нижняя тень, маленькое тело, верхняя тень почти отсутствует
        "piercing": (c1 < o1) & bull_bar & (o < c1) & (c > (o1 + c1) / 2) & (c < o1), # Смысл: тот же сюжет, что и в поглощении, но слабее
        "morning_star": (c2 < o2) & (body2 > avg_body) & (body1 < body2 * 0.5)
                        & bull_bar & (c > mid2), # Утренняя звезда — три свечи: красная, маленькая (звезда), зелёная, закрытие которой выше середины красной свечи
        "marubozu": bull_bar & (body >= 0.9 * rng), # тело = 90%+ всего диапазона
    }
    bear = {
        "engulfing": (c1 > o1) & bear_bar & (c <= o1) & (o >= c1),
        "shooting_star": (upper >= 2 * body) & (lower <= body) & (body > 0),
        "dark_cloud": (c1 > o1) & bear_bar & (o > c1) & (c < (o1 + c1) / 2) & (c > o1),
        "evening_star": (c2 > o2) & (body2 > avg_body) & (body1 < body2 * 0.5)
                        & bear_bar & (c < mid2),
        "marubozu": bear_bar & (body >= 0.9 * rng),
    }
    bull_hits = sum(s.fillna(False).astype(int) for s in bull.values()) # Out of 5 parameters how many showed a bullish signal
    bear_hits = sum(s.fillna(False).astype(int) for s in bear.values()) # Out of 5 parameters how many showed a bearish signal
    return bull_hits, bear_hits



'''
Это та же самая фунция что и _native_signals, но использует библиотеку pandas_ta_classic, которая умеет распознавать больше паттернов.
Однако эта функция не используется в основном коде, так как библиотека является не надежной и не поддерживается. 
Она оставлена здесь для справки и для тех, кто хочет использовать больше паттернов.
'''
def _pandas_ta_classic_signals(bars: pd.DataFrame):
    import pandas_ta_classic as ta  # noqa: F401  (optional dependency)

    df = bars[["open", "high", "low", "close"]].copy()
    res = df.ta.cdl_pattern(name="all")
    # The library emits +100 / -100 / 0 per pattern.
    bull_hits = (res > 0).sum(axis=1)
    bear_hits = (res < 0).sum(axis=1)
    return bull_hits, bear_hits



# Фунция решает какую из двух функций использовать для распознавания паттернов. 
# Если cfg.use_pandas_ta_classic = True, то используется библиотека pandas_ta_classic, иначе используется нативная функция _native_signals.
def compute_candles(bars: pd.DataFrame, cfg: Agent1Config) -> pd.DataFrame:
    if cfg.use_pandas_ta_classic:
        try:
            bull_hits, bear_hits = _pandas_ta_classic_signals(bars)
        except ImportError:
            bull_hits, bear_hits = _native_signals(bars)
    else:
        bull_hits, bear_hits = _native_signals(bars)

    w = cfg.candle_window
    out = pd.DataFrame(
        {
            "cdl_bull_count_3": bull_hits.rolling(w, min_periods=w).sum(), # Берет количество сигналов за последние w свечей и суммирует их для более стабильного результата. Если сигналов меньше чем w, то суммирует сколько есть.
            "cdl_bear_count_3": bear_hits.rolling(w, min_periods=w).sum(),
        },
        index=bars.index, # Индекс это время свечей, чтобы можно было сопоставить сигналы с конкретными свечами.
    )
    return out.astype(float)
