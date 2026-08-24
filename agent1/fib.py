"""Fibonacci levels off the last confirmed swing leg.

Fib is only meaningful with an objective anchor.  Ours is the most recent
pair of confirmed opposing pivots — no discretion, no hand-drawn leg.

We emit a continuous ``dist_to_fib_618_atr`` rather than a boolean "is price
at the 0.618?".  A boolean needs an arbitrary tolerance, and picking that
tolerance is exactly the kind of unvalidated assumption Agent 5 exists to
avoid.  Hand it the distance; let the tree find the threshold, if there is one.
"""
from __future__ import annotations

from typing import List

import numpy as np
import pandas as pd

from .pivots import HIGH, Pivot

FIB_COLUMNS = ("fib_position", "fib_leg_direction", "dist_to_fib_618_atr")

GOLDEN = 0.618


def compute_fib(bars: pd.DataFrame, pivots: List[Pivot], atr: pd.Series) -> pd.DataFrame:
    n = len(bars) # number of bars in the DataFrame
    c = bars["close"].to_numpy(float) # Price of the close of each bar as a numpy array
    a = atr.to_numpy(float) # ATR values as a numpy array

    piv = sorted(pivots, key=lambda p: (p.confirmed_at, p.index)) # Sort the pivots by their confirmation time and index, so we can process them in order
    k = 0 # How many pivots we have processed so far
    seen: List[Pivot] = [] # List to keep track of the pivots that have been confirmed up to the current bar


    # Initialize an массив to hold the Fibonacci position for each bar, filled with NaN
    pos = np.full(n, np.nan) #Где сейчас цена внутри ноги, от 0 до 1. 0.0 — на нижнем конце, 1.0 — на верхнем, 0.5 — ровно посередине. Может выйти за границы: 1.2 значит, что цена пробила ногу вверх.
    leg_dir = np.full(n, np.nan) # Куда шла нога: 1.0 вверх (закончилась вершиной), -1.0 вниз (закончилась дном)
    d618 = np.full(n, np.nan) # Расстояние от текущей цены до золотого уровня 0.618, в единицах ATR

    for t in range(n): # для каждой свечи
        while k < len(piv) and piv[k].confirmed_at <= t: # Пока число не подтвержденных пивотов меньше, чем общее число пивотов, и время подтверждения текущего пивота меньше или равно текущему времени
            seen.append(piv[k])
            k += 1
        if len(seen) < 2:
            continue
        atr_t = a[t]
        if np.isnan(atr_t) or atr_t <= 0: # Если ATR не определен или меньше или равен нулю, пропускаем эту свечу
            continue

        end = seen[-1] # Последний подтвержденный пивот, который мы видим на текущей свече
        start = None
        for p in reversed(seen[:-1]): # Идем по списку подтвержденных пивотов в обратном порядке, начиная с предпоследнего
            if p.kind != end.kind and p.index < end.index: # Если пивот противоположного типа и находится до конца ноги, то это начало ноги
                start = p 
                break
        if start is None: 
            continue

        lo = min(start.price, end.price) # Нижняя граница ноги
        hi = max(start.price, end.price) # Верхняя граница ноги
        span = hi - lo # Длина ноги
        if span <= 0:
            continue

        up_leg = end.kind == HIGH # True if the last confirmed pivot is a high (up leg), False if it's a low (down leg)
        pos[t] = (c[t] - lo) / span # Calculate the position of the current price within the leg, normalized to [0, 1]
        leg_dir[t] = 1.0 if up_leg else -1.0 # Куда шла нога: 1.0 вверх (закончилась вершиной), -1.0 вниз (закончилась дном)
        # Retracement measured back from where the leg ended.
        # retracement is measured BACK from where the leg ended.
        # up-leg: come down 61.8% from the high. down-leg: come up from the low
        level = hi - GOLDEN * span if up_leg else lo + GOLDEN * span # Calculate the 0.618 Fibonacci retracement level based on the leg direction
        d618[t] = (level - c[t]) / atr_t # Calculate the distance from the current price to the 0.618 Fibonacci level, normalized by ATR

    return pd.DataFrame(
        {"fib_position": pos, "fib_leg_direction": leg_dir, "dist_to_fib_618_atr": d618},
        index=bars.index,
    )
