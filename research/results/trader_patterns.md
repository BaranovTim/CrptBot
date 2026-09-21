# What the best public traders actually do

Source: a year of fills for 54 Hyperliquid addresses that were in the top 400 by 30-day profit on 2026-09-16 and whose fills looked discretionary (not a market maker's). 11,083 round-trip episodes; **3,983 position trades** (held ≥30 min, ≥$5k). Bars, levels and features are ours (Binance USDT perps).

## 1. The leaderboard is a 30-day window on a year

Of 54 traders selected for a great *month*, **21 are net losers on their position trades over the *year*** (worst: $-13.6M). For the median trader, the top 5 trades are **73%** of gross profit. Median 4 liquidations per trader. A leaderboard ranks variance.

| addr | position_trades | maker_share | median_hold_h | long_share | win_rate | avg_win_pct | avg_loss_pct | payoff_ratio | pnl_total | pnl_30d_board | pnl_top5_share | liquidations | coins |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 0x8af700ba | 3 | 0.91 | 683.01 | 0.33 | 1.00 | 10.08 |  |  | 7312035.77 | 6658423.34 | 1.00 | 2 | other,ZECUSDT |
| 0x8bae3527 | 142 | 0.52 | 93.58 | 0.71 | 0.66 | 11.07 | -5.02 | 2.21 | 7233703.41 | 1265471.30 | 0.30 | 6 | other,HYPEUSDT,CELOUSDT,VIRTUALUSDT |
| 0x9a0825ca | 26 | 0.18 | 43.81 | 0.62 | 0.92 | 2.42 | 5.56 |  | 2069354.87 | 795720.84 | 0.98 | 5 | ETHUSDT,BTCUSDT |
| 0xbe10fd36 | 179 | 0.17 | 22.76 | 0.80 | 0.61 | 5.16 | -9.76 | 0.53 | 1231142.64 | 667006.55 | 0.57 | 3 | other,HYPEUSDT,XPLUSDT,SOLUSDT |
| 0x18cd4597 | 32 | 0.56 | 165.66 | 1.00 | 0.81 | 4.77 | -3.09 | 1.54 | 1230902.01 | 602449.68 | 0.64 | 9 | HYPEUSDT,ETHUSDT,BTCUSDT,other |
| 0xd74916ae | 120 | 0.56 | 20.27 | 0.75 | 0.40 | 3.83 | -3.39 | 1.13 | 860424.48 | 679969.06 | 0.83 | 10 | BTCUSDT,other,TAOUSDT,ETHUSDT |
| 0xf21d494b | 72 | 0.01 | 27.34 | 0.94 | 0.53 | 7.54 | -4.54 | 1.66 | 850622.38 | 715470.98 | 0.73 | 6 | other,LITUSDT,PUMPUSDT,SPXUSDT |
| 0xa99c9e54 | 64 | 0.74 | 96.37 | 0.50 | 0.72 | 5.44 | -6.69 | 0.81 | 795501.21 | 554772.82 | 0.61 | 10 | other,BTCUSDT,HYPEUSDT |
| 0x718cc7ee | 98 | 0.02 | 22.81 | 0.91 | 0.36 | 14.64 | -4.87 | 3.00 | 746461.64 | 625564.86 | 0.71 | 4 | other,HYPEUSDT,BTCUSDT,ZECUSDT |
| 0xd93db9fc | 166 | 0.00 | 55.89 | 0.11 | 0.55 | 3.88 | -1.52 | 2.55 | 687123.98 | 544655.97 | 0.63 | 0 | HYPEUSDT,ETHUSDT,XRPUSDT,SOLUSDT |
| 0x77746ff0 | 12 | 0.87 | 46.89 | 0.42 | 0.83 | 5.51 | -0.24 | 23.11 | 642812.15 | 2217018.01 | 0.94 | 5 | HYPEUSDT |
| 0xb7658d7c | 13 | 0.44 | 41.11 | 1.00 | 0.85 | 15.02 | -2.92 | 5.14 | 587277.77 | 593312.60 | 0.95 | 0 | ZECUSDT,PUMPUSDT,ENAUSDT,ASTERUSDT |
| 0x9471f70f | 77 | 0.37 | 57.36 | 0.82 | 0.43 | 5.46 | -6.19 | 0.88 | 543393.81 | 491363.12 | 0.76 | 12 | other,HYPEUSDT,BTCUSDT,ZECUSDT |
| 0xf00bb08f | 105 | 0.22 | 30.65 | 0.97 | 0.64 | 4.43 | -4.85 | 0.91 | 429096.14 | 472275.92 | 0.61 | 4 | HYPEUSDT,ETHUSDT,BTCUSDT,SPXUSDT |
| 0x5740affc | 73 | 0.26 | 51.47 | 0.84 | 0.62 | 16.48 | -9.68 | 1.70 | 409354.90 | 696634.85 | 0.65 | 6 | other,UNIUSDT,MONUSDT,XPLUSDT |
| 0xde8a6d58 | 177 | 0.38 | 19.13 | 0.69 | 0.48 | 6.43 | -4.28 | 1.50 | 375390.63 | 550194.70 | 0.42 | 45 | other,SOLUSDT,ZECUSDT,BTCUSDT |
| 0x025c6243 | 35 | 0.65 | 71.93 | 0.89 | 0.40 | 3.28 | -3.53 | 0.93 | 363398.22 | 814275.01 | 0.98 | 0 | other,HYPEUSDT,XMRUSDT,BTCUSDT |
| 0x51f3a19c | 6 | 0.59 | 108.15 | 0.83 | 1.00 | 3.17 |  |  | 298849.25 | 1359826.03 | 1.00 | 1 | HYPEUSDT |
| 0xf0050934 | 28 | 0.55 | 177.91 | 0.93 | 0.36 | 25.69 | -4.35 | 5.90 | 240211.52 | 728992.77 | 0.97 | 2 | BTCUSDT,other,HYPEUSDT,YZYUSDT |
| 0x381fe486 | 18 | 0.61 | 22.75 | 0.22 | 0.61 | 10.59 | -43.67 | 0.24 | 209037.41 | 957255.00 | 0.91 | 1 | other,ETHUSDT,SOLUSDT,MEGAUSDT |
| 0xa906355b | 67 | 0.75 | 37.56 | 0.99 | 0.48 | 9.31 | -6.19 | 1.50 | 177308.93 | 5344859.32 | 0.50 | 8 | PUMPUSDT,ETHUSDT,XPLUSDT,HYPEUSDT |
| 0xaac091f5 | 1 | 0.32 | 117.05 | 1.00 | 1.00 | 8.28 |  |  | 102923.68 | 2328847.87 | 1.00 | 1 | BTCUSDT |
| 0x75d2ba3d | 22 | 0.14 | 50.33 | 1.00 | 0.59 | 5.23 | -4.07 | 1.28 | 95820.24 | 1104141.53 | 0.96 | 1 | other,BTCUSDT,ZECUSDT,MORPHOUSDT |
| 0x585f4fbe | 152 | 0.70 | 34.21 | 0.92 | 0.82 | 2.93 | -2.13 | 1.37 | 94744.80 | 1772429.10 | 0.39 | 16 | HYPEUSDT,BTCUSDT,ETHUSDT,other |
| 0x512e1d1d | 31 | 0.35 | 118.97 | 0.87 | 0.74 | 8.62 | -4.08 | 2.11 | 79336.88 | 550414.16 | 0.76 | 0 | BTCUSDT,ETHUSDT,other,ENAUSDT |
| 0x005844b2 | 5 | 0.26 | 855.52 | 0.80 | 0.60 | 24.94 | 25.08 |  | 55954.83 | 1556100.15 | 0.88 | 0 | other,HYPEUSDT |
| 0x09714a32 | 72 | 0.29 | 9.68 | 0.94 | 0.72 | 1.96 | -4.79 | 0.41 | 33199.42 | 874587.20 | 0.75 | 12 | HYPEUSDT,other,PONSUSDT,LITUSDT |
| 0x0dd73d2b | 9 | 0.77 | 280.77 | 0.78 | 0.22 | 11.13 | 2.69 |  | 22675.15 | 629996.44 | 1.00 | 2 | other,HYPEUSDT |
| 0x7839e2f2 | 81 | 0.95 | 1.24 | 0.54 | 0.65 | 0.75 | -0.38 | 1.96 | 17194.96 | 4004035.45 | 0.58 | 2 | XRPUSDT,SOLUSDT,ETHUSDT,BTCUSDT |
| 0x89a289b2 | 6 | 0.15 | 11.58 | 0.50 | 0.67 | 10.22 | -7.25 | 1.41 | 12208.16 | 501891.41 | 0.96 | 0 | PONSUSDT |
| 0x8fec806c | 3 | 0.39 | 19.17 | 0.67 | 0.33 | 0.14 | -2.97 | 0.05 | 4064.00 | 1829162.21 | 0.83 | 0 | XRPUSDT,BTCUSDT |
| 0x72db642e | 30 | 0.19 | 408.82 | 0.47 | 0.60 | 15.43 | -15.43 | 1.00 | 3603.23 | 764452.14 | 0.64 | 9 | WLFIUSDT,ETHUSDT,FARTCOINUSDT,HYPEUSDT |
| 0xbafae6af | 239 | 0.01 | 7.68 | 0.82 | 0.61 | 3.61 | -4.48 | 0.81 | 1447.52 | 444844.63 | 0.18 | 49 | HYPEUSDT,BTCUSDT,other,ZECUSDT |
| 0x41906213 | 13 | 0.72 | 5.67 | 0.31 | 0.38 | 5.25 | -0.86 | 6.12 | -524.05 | 557481.08 | 1.00 | 0 | other,XMRUSDT,BTCUSDT |
| 0xde5a8068 | 57 | 0.26 | 127.11 | 0.70 | 0.39 | 7.25 | -13.39 | 0.54 | -2568.09 | 450808.22 | 0.78 | 14 | BTCUSDT,CRVUSDT,ETHUSDT,XRPUSDT |
| 0xa2ecada0 | 111 | 0.05 | 3.40 | 0.95 | 0.65 | 1.24 | -3.36 | 0.37 | -17168.29 | 942628.51 | 0.54 | 10 | BTCUSDT,HYPEUSDT,other,ZECUSDT |
| 0xf482d8ab | 49 | 0.33 | 72.00 | 0.65 | 0.57 | 5.96 | -16.22 | 0.37 | -45762.61 | 835560.28 | 0.59 | 0 | other,HYPEUSDT,SOLUSDT,BTCUSDT |
| 0xf1397a46 | 98 | 0.23 | 11.23 | 0.87 | 0.58 | 1.49 | -3.34 | 0.45 | -82704.02 | 701322.48 | 0.41 | 5 | SOLUSDT,HYPEUSDT,BTCUSDT,ZECUSDT |
| 0xaa8253fb | 58 | 0.80 | 134.40 | 0.67 | 0.52 | 4.45 | -10.05 | 0.44 | -147400.25 | 1604332.18 | 0.69 | 12 | HYPEUSDT,BTCUSDT,ZECUSDT,SOLUSDT |
| 0xb6aef9cf | 9 | 0.40 | 6.55 | 0.67 | 0.44 | 0.69 | -25.16 | 0.03 | -149465.11 | 488897.62 | 0.48 | 3 | BTCUSDT,HYPEUSDT,ZECUSDT,LITUSDT |
| 0xd2b2a8ab | 31 | 0.86 | 41.19 | 0.84 | 0.81 | 3.68 | -9.48 | 0.39 | -272377.14 | 2792312.89 | 0.66 | 3 | HYPEUSDT,ZECUSDT,ETHUSDT,other |
| 0x2f55548a | 54 | 0.08 | 37.26 | 0.93 | 0.56 | 4.26 | -4.98 | 0.85 | -281659.81 | 483246.44 | 0.75 | 6 | other,ASTERUSDT,YZYUSDT,BTCUSDT |
| 0x0423b4b2 | 240 | 0.15 | 21.04 | 0.76 | 0.55 | 5.15 | -5.51 | 0.94 | -372438.37 | 713636.03 | 0.29 | 3 | BTCUSDT,XPLUSDT,other,HYPEUSDT |
| 0x80fb5880 | 307 | 0.03 | 18.79 | 0.76 | 0.62 | 3.14 | -5.02 | 0.62 | -451394.29 | 1893916.50 | 0.50 | 14 | other,BTCUSDT,HYPEUSDT,IPUSDT |
| 0x47a127f4 | 67 | 0.42 | 11.25 | 0.85 | 0.66 | 2.06 | -2.82 | 0.73 | -453640.52 | 1540286.82 | 0.60 | 3 | BTCUSDT,ETHUSDT,ZECUSDT,SOLUSDT |
| 0xdd97539f | 167 | 0.00 | 4.63 | 0.29 | 0.38 | 1.97 | -2.15 | 0.91 | -468893.07 | 2307109.58 | 0.39 | 0 | ETHUSDT,BTCUSDT,SOLUSDT,HYPEUSDT |
| 0xbb1ac608 | 57 | 0.13 | 21.44 | 0.82 | 0.42 | 6.78 | -3.59 | 1.89 | -505415.22 | 457258.27 | 0.73 | 16 | HYPEUSDT,BTCUSDT,ETHUSDT,MEGAUSDT |
| 0x05b5a0ae | 178 | 0.01 | 9.75 | 0.80 | 0.67 | 1.01 | -2.27 | 0.45 | -613162.82 | 1818199.66 | 0.32 | 0 | ETHUSDT,BTCUSDT,UNIUSDT,BCHUSDT |
| 0xfd6a5315 | 28 | 0.92 | 71.07 | 0.68 | 0.61 | 4.90 | -16.67 | 0.29 | -670123.90 | 1343030.06 | 0.75 | 9 | other |
| 0x2057d4f2 | 2 | 0.00 | 3944.94 | 0.50 | 0.00 |  | -3.51 |  | -1505515.37 | 1146594.12 |  | 0 | BTCUSDT,HYPEUSDT |
| 0xdf6097a9 | 22 | 0.02 | 163.90 | 0.64 | 0.45 | 5.54 | -11.72 | 0.47 | -1630642.16 | 876858.93 | 0.95 | 2 | ZROUSDT,other,HYPEUSDT,PAXGUSDT |
| 0xc916baee | 23 | 0.16 | 57.39 | 0.96 | 0.39 | 7.55 | -9.41 | 0.80 | -1971803.85 | 464840.40 | 0.95 | 1 | PUMPUSDT,other,HYPEUSDT,ETHUSDT |
| 0x8f8d2d25 | 217 | 0.36 | 4.55 | 0.56 | 0.68 | 0.98 | -3.68 | 0.27 | -2141021.18 | 1884120.55 | 0.34 | 12 | BTCUSDT,ETHUSDT,SOLUSDT,BCHUSDT |
| 0xa1830e8d | 31 | 0.26 | 203.13 | 0.61 | 0.48 | 1.54 | -8.61 | 0.18 | -13647226.46 | 2513394.75 | 0.88 | 10 | BTCUSDT,ETHUSDT,HYPEUSDT,SOLUSDT |

## 2. Shape of a trade: profitable traders vs losing ones

Same pool, split by whether the year's position trades made money (≥10 trades each).

| cls | trades | traders | median_hold_h | p75_hold_h | long_share | win_rate | avg_win | avg_loss | maker_entry | maker_exit | entry_clips | exit_clips | scale_in_h | flipped_in |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| losing | 1808 | 19 | 15.49 | 65.25 | 0.71 | 0.57 | 2.89 | -5.75 | 0.21 | 0.17 | 4.00 | 2.00 | 0.65 | 0.06 |
| profitable | 2131 | 26 | 28.36 | 118.77 | 0.75 | 0.59 | 6.15 | -5.62 | 0.36 | 0.28 | 5.00 | 2.00 | 0.38 | 0.06 |

Coins with our bars and features: BTCUSDT, DOGEUSDT, ENAUSDT, ETHUSDT, HYPEUSDT, NEARUSDT, SOLUSDT, UNIUSDT, XRPUSDT, ZECUSDT — **2,272** of the 3,983 position trades (1,072 by profitable traders).

## 3. Where they enter, versus where the market is

Each feature at the 1h bar closed before the entry, against the same feature over every 1h bar of that coin in the period. `z` is a rank-sum statistic: |z|>3 is a habit, |z|<2 is noise. `share_top_q` / `share_bottom_q`: fraction of entries in the market's top / bottom quartile (0.25 = no preference). Signs are from the trade's point of view for the directional features (a short's `ret_24h` is negated), so 'buying dips' and 'shorting rips' read alike.

### profitable traders, LONG entries (n=792, 25 traders)

(directional features oriented with the trade: `ret_*` and `ema_*` positive = price had moved in the trade's favour before entry; `res_dist` = distance to the level ahead, `sup_dist` = to the level behind)

| feature | n | entry_median | market_median | share_top_q | share_bottom_q | z |
|---|---|---|---|---|---|---|
| vol_z | 792 | 0.14 | -0.30 | 0.46 | 0.14 | 14.26 |
| rv_pctile | 792 | 0.65 | 0.48 | 0.41 | 0.16 | 10.20 |
| ema20_dist_atr | 792 | -0.50 | -0.04 | 0.22 | 0.40 | -8.17 |
| range24_pos | 792 | 0.36 | 0.50 | 0.21 | 0.40 | -8.07 |
| ret_4h_pct | 792 | -0.39 | -0.01 | 0.23 | 0.39 | -7.33 |
| rsi14 | 792 | 45.38 | 49.45 | 0.22 | 0.38 | -7.13 |
| pdl_dist_atr | 792 | 1.73 | 2.45 | 0.21 | 0.38 | -6.92 |
| ret_1h_pct | 792 | -0.14 | 0.00 | 0.24 | 0.37 | -5.96 |
| ema50_dist_atr | 792 | -0.60 | -0.13 | 0.22 | 0.35 | -5.96 |
| atr_pct | 792 | 1.30 | 1.16 | 0.39 | 0.27 | 5.74 |
| res_dist_atr | 779 | 0.98 | 0.87 | 0.34 | 0.22 | 5.61 |
| ret_24h_pct | 792 | -0.69 | -0.08 | 0.23 | 0.33 | -4.77 |

### profitable traders, SHORT entries (n=280, 22 traders)

(directional features oriented with the trade: `ret_*` and `ema_*` positive = price had moved in the trade's favour before entry; `res_dist` = distance to the level ahead, `sup_dist` = to the level behind)

| feature | n | entry_median | market_median | share_top_q | share_bottom_q | z |
|---|---|---|---|---|---|---|
| vol_z | 280 | 0.01 | -0.30 | 0.37 | 0.17 | 6.20 |
| rv_pctile | 280 | 0.65 | 0.48 | 0.40 | 0.17 | 5.16 |
| res_dist_atr | 275 | 0.94 | 0.85 | 0.36 | 0.21 | 3.00 |
| ret_7d_pct | 280 | 2.62 | 0.56 | 0.35 | 0.24 | 2.26 |
| ret_4h_pct | 280 | -0.17 | 0.01 | 0.26 | 0.33 | -2.15 |
| pdl_dist_atr | 280 | 2.03 | 2.56 | 0.25 | 0.27 | -1.85 |
| ret_1h_pct | 280 | -0.00 | 0.00 | 0.23 | 0.30 | -1.54 |
| sup_dist_atr | 270 | 0.89 | 0.87 | 0.27 | 0.22 | 1.32 |
| rsi14 | 280 | 49.75 | 49.47 | 0.33 | 0.26 | 1.23 |
| range24_pos | 280 | 0.47 | 0.50 | 0.26 | 0.30 | -1.17 |
| bar_pos | 280 | 0.46 | 0.49 | 0.24 | 0.28 | -1.11 |
| ema20_dist_atr | 280 | -0.03 | 0.03 | 0.26 | 0.31 | -1.09 |

### losing traders, LONG entries (n=788, 17 traders)

(directional features oriented with the trade: `ret_*` and `ema_*` positive = price had moved in the trade's favour before entry; `res_dist` = distance to the level ahead, `sup_dist` = to the level behind)

| feature | n | entry_median | market_median | share_top_q | share_bottom_q | z |
|---|---|---|---|---|---|---|
| vol_z | 788 | 0.05 | -0.30 | 0.42 | 0.15 | 12.07 |
| rv_pctile | 788 | 0.62 | 0.48 | 0.37 | 0.17 | 8.44 |
| atr_pct | 788 | 1.00 | 1.16 | 0.25 | 0.35 | -4.96 |
| ema20_dist_atr | 788 | -0.16 | -0.04 | 0.25 | 0.33 | -3.34 |
| range24_pos | 788 | 0.45 | 0.50 | 0.24 | 0.30 | -2.87 |
| rsi14 | 788 | 48.15 | 49.45 | 0.25 | 0.33 | -2.83 |
| ret_24h_pct | 788 | -0.15 | -0.08 | 0.22 | 0.29 | -2.58 |
| sup_dist_atr | 763 | 0.92 | 0.85 | 0.30 | 0.24 | 2.39 |
| pdh_dist_atr | 788 | 2.82 | 2.56 | 0.32 | 0.25 | 2.36 |
| ret_1h_pct | 788 | -0.05 | 0.00 | 0.24 | 0.32 | -2.35 |
| ret_4h_pct | 788 | -0.09 | -0.01 | 0.24 | 0.30 | -2.27 |
| ema50_dist_atr | 788 | -0.19 | -0.13 | 0.27 | 0.32 | -2.23 |

### losing traders, SHORT entries (n=388, 17 traders)

(directional features oriented with the trade: `ret_*` and `ema_*` positive = price had moved in the trade's favour before entry; `res_dist` = distance to the level ahead, `sup_dist` = to the level behind)

| feature | n | entry_median | market_median | share_top_q | share_bottom_q | z |
|---|---|---|---|---|---|---|
| vol_z | 388 | 0.05 | -0.30 | 0.42 | 0.13 | 8.02 |
| rv_pctile | 388 | 0.61 | 0.48 | 0.35 | 0.15 | 5.84 |
| atr_pct | 388 | 0.98 | 1.16 | 0.19 | 0.32 | -4.44 |
| res_dist_atr | 376 | 0.93 | 0.85 | 0.32 | 0.25 | 2.41 |
| sup_dist_atr | 379 | 0.86 | 0.86 | 0.33 | 0.25 | 1.58 |
| ema20_dist_atr | 388 | 0.18 | 0.02 | 0.36 | 0.29 | 1.07 |
| range24_pos | 388 | 0.57 | 0.50 | 0.31 | 0.27 | 1.01 |
| ret_24h_pct | 388 | 0.27 | 0.04 | 0.29 | 0.27 | 0.77 |
| pdl_dist_atr | 388 | 2.90 | 2.51 | 0.31 | 0.29 | 0.71 |
| rsi14 | 388 | 48.62 | 49.60 | 0.30 | 0.33 | -0.69 |
| ret_1h_pct | 388 | 0.00 | 0.00 | 0.26 | 0.27 | -0.56 |
| trend_20_50 | 388 | 1.00 | 1.00 | 0.50 | 0.50 | -0.52 |

## 4. When they enter

**profitable** (n=1072): busiest hours UTC 16:00 (7%, vs 4.2% uniform), 14:00 (7%, vs 4.2% uniform), 15:00 (6%, vs 4.2% uniform), 17:00 (6%, vs 4.2% uniform); quietest 10:00 (0.9%), 08:00 (2.3%), 00:00 (2.6%).
Weekdays Mon..Sun: 17% / 15% / 12% / 18% / 17% / 9% / 13%.

**losing** (n=1176): busiest hours UTC 15:00 (7%, vs 4.2% uniform), 14:00 (7%, vs 4.2% uniform), 17:00 (7%, vs 4.2% uniform), 16:00 (6%, vs 4.2% uniform); quietest 10:00 (1.6%), 11:00 (1.9%), 09:00 (2.0%).
Weekdays Mon..Sun: 16% / 16% / 14% / 17% / 15% / 10% / 12%.

## 5. Where they get out: the stop and the target, read off the book

MFE = the best price reached during the hold, MAE = the worst, both in % of entry from the trade's side. A trader's *effective stop* is where the losers' MAE clusters; the *effective target* is the winners' realised return; `exit_of_mfe` is how much of the best price they kept.

| cls | n | winners_ret_med | winners_ret_p75 | winners_mfe_med | winners_kept_of_mfe | winners_mae_med | losers_ret_med | losers_mae_med | losers_mae_p25 | losers_mfe_med | hold_med_h | entry_bar_pos_med | entry_vs_prev_close_med |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| profitable | 1047 | 1.81 | 4.52 | 3.96 | 0.52 | -1.89 | -1.74 | -3.43 | -7.50 | 1.63 | 23.33 | 0.46 | 0.17 |
| losing | 1162 | 0.83 | 1.95 | 1.72 | 0.54 | -0.88 | -1.42 | -2.42 | -6.33 | 1.32 | 10.69 | 0.48 | 0.00 |

Reading `entry_bar_pos`: 0 = the entry printed at the hour's low (a long) / high (a short), 1 = the worst price of the hour. `entry_vs_prev_close`: how much better than a market order at the previous bar's close (positive = better).

**profitable** in ATR of the 1h bar: losers' MAE median 2.5 ATR (p25 5.8), realised loss median 1.5 ATR; winners' realised gain median 1.3 ATR, MFE median 3.0 ATR.

**losing** in ATR of the 1h bar: losers' MAE median 2.4 ATR (p25 5.2), realised loss median 1.3 ATR; winners' realised gain median 0.8 ATR, MFE median 1.6 ATR.

## 6. News around entries

Headlines in the 6h before + 1h after an entry (our feed, 1328 items, 2025-12-29 → 2026-09-20, matched by asset tag or untagged): entries by profitable traders 0.91 per window, losing 0.78, against **1.46** for a random 7h window of the whole feed. Share of entries with any headline in the window: profitable 10%, losing 7%.

## 7. What happens after they enter (a follower's view)

Return in the trade's direction from the entry VWAP at 1h / 4h / 24h, before fees. Split by class and by how the entry was made (resting order vs taker).

| cls | entry | n | fwd_1h | se_1h | fwd_4h | se_4h | fwd_24h | se_24h |
|---|---|---|---|---|---|---|---|---|
| profitable | resting ≥50% | 409 | 1.36 | 0.24 | 1.16 | 0.25 | 0.94 | 0.30 |
| profitable | taker | 663 | 0.03 | 0.14 | 0.01 | 0.16 | 0.03 | 0.21 |
| losing | resting ≥50% | 243 | -0.15 | 0.30 | -0.18 | 0.30 | 0.53 | 0.40 |
| losing | taker | 933 | 0.08 | 0.07 | -0.08 | 0.09 | 0.08 | 0.14 |

### 7b. From the price a follower would pay

The same trades, measured from the first 15m close AFTER the entry printed -- what a copier gets, having seen the fill. `slip` = how much better the trader's own fill was than that close.

| cls | entry | n | slip | fol_1h | se_1h | fol_4h | se_4h | fol_24h | se_24h |
|---|---|---|---|---|---|---|---|---|---|
| profitable | resting ≥50% | 409 | -1.17 | 0.02 | 0.07 | -0.18 | 0.12 | -0.36 | 0.27 |
| profitable | taker | 663 | 0.09 | 0.07 | 0.07 | 0.06 | 0.11 | 0.07 | 0.22 |
| losing | resting ≥50% | 243 | 0.31 | -0.02 | 0.07 | -0.04 | 0.14 | 0.74 | 0.38 |
| losing | taker | 933 | -0.09 | -0.05 | 0.03 | -0.21 | 0.07 | -0.04 | 0.15 |

### 7c. Out of sample: class decided on the first half-year, trades from the second

Traders with ≥5 position trades before 2026-03-04 are labelled by that half's P&L; only entries after it are scored. This removes the trade's own outcome from its trader's label.

| cls | entry | n | traders | win_rate | ret_med | fwd_1h | fol_1h | fol_se_1h | fwd_4h | fol_4h | fol_se_4h | fwd_24h | fol_24h | fol_se_24h |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| profitable(H1) | resting ≥50% | 36 | 4 | 0.81 | 1.96 | -0.04 | 0.08 | 0.15 | 0.06 | 0.19 | 0.30 | 1.51 | 1.71 | 0.68 |
| profitable(H1) | taker | 130 | 9 | 0.65 | 1.10 | -1.26 | -0.18 | 0.10 | -0.73 | 0.35 | 0.20 | 0.44 | 1.47 | 0.49 |
| losing(H1) | resting ≥50% | 219 | 19 | 0.74 | 0.60 | 0.74 | -0.02 | 0.08 | 0.57 | -0.18 | 0.14 | 0.94 | 0.26 | 0.43 |
| losing(H1) | taker | 503 | 21 | 0.67 | 0.32 | 0.01 | -0.00 | 0.04 | -0.10 | -0.12 | 0.08 | -0.14 | -0.15 | 0.21 |

Robustness of the one positive cell (H1-profitable traders' second-half entries, all entry kinds, coins we cover): 166 entries from 9 traders; from the follower's price at 24h **mean +1.52% (se 0.41), median +0.63%, winsorised +1.30%, 60% positive**; LONG +1.58% (n=102), SHORT +1.43% (n=64) — both sides, so not the market's drift, which was +0.34% mean / 0.00% median over the same bars. 7 of 9 traders and 8 of 10 coins positive; three traders supply 106 of the 166 entries; the big means sit on HYPE, ZEC, ENA, NEAR.

## 8. Can our features predict WHEN they enter?

For every 1h bar of every covered coin: label 1 if a *profitable* trader opened a position trade in the next hour (long model / short model separately). Fitted with the project's own purged CV on agent1/agent2/agent4/regime features, shuffled-label control. If this clears, their entry rule is a function of the chart and can be written down.

### LONG entries

| side | coin | entries | bars | auc | shuffle | spread | clears | top_blocks |
|---|---|---|---|---|---|---|---|---|
| LONG | BTCUSDT | 164 | 10884 | 0.68 | 0.48 | 0.06 | True | agent2 0.12, regime 0.04, agent4 0.00 |
| LONG | ENAUSDT | 35 | 10756 | 0.56 | 0.43 | 0.02 | True | agent2 0.02, agent1 0.01, regime -0.01 |
| LONG | ETHUSDT | 98 | 10540 | 0.67 | 0.50 | 0.07 | True | agent2 0.04, agent1 0.01, regime 0.00 |
| LONG | HYPEUSDT | 264 | 10756 | 0.64 | 0.48 | 0.05 | True | agent2 0.06, regime 0.04, agent4 0.00 |
| LONG | SOLUSDT | 74 | 10540 | 0.54 | 0.52 | 0.07 | False | regime 0.01, agent2 0.01, agent1 0.00 |
| LONG | XRPUSDT | 17 | 10708 |  | 0.40 |  | False |  |
| LONG | ZECUSDT | 63 | 10756 | 0.50 | 0.48 | 0.15 | False | regime 0.01, agent2 0.01, agent4 0.00 |

**As a rule (LONG):** enter when the model's out-of-fold score is in its top decile (the bars that look most like a profitable trader's entry), exit 24h later at the close. Mean -0.335% (se 0.055, n=5523) vs every bar +0.082% (se 0.021). Fees 0.10%.

### SHORT entries

| side | coin | entries | bars | auc | shuffle | spread | clears | top_blocks |
|---|---|---|---|---|---|---|---|---|
| SHORT | BTCUSDT | 63 | 10884 | 0.64 | 0.48 | 0.03 | True | agent2 0.06, regime 0.05, agent1 0.02 |
| SHORT | ETHUSDT | 54 | 10540 | 0.58 | 0.45 | 0.13 | False | agent2 0.04, agent4 -0.00, agent1 -0.01 |
| SHORT | HYPEUSDT | 43 | 10756 | 0.67 | 0.48 | 0.08 | True | regime 0.05, agent1 0.04, agent4 -0.00 |
| SHORT | SOLUSDT | 44 | 10540 | 0.57 | 0.51 | 0.09 | False | agent1 0.01, agent4 0.01, agent2 0.01 |
| SHORT | XRPUSDT | 27 | 10708 | 0.65 | 0.37 | 0.10 | True | agent1 0.06, agent2 0.02, regime 0.01 |
| SHORT | ZECUSDT | 32 | 10756 | 0.70 | 0.49 | 0.15 | True | agent2 0.11, agent4 0.02, agent1 0.01 |

**As a rule (SHORT):** enter when the model's out-of-fold score is in its top decile (the bars that look most like a profitable trader's entry), exit 24h later at the close. Mean -0.382% (se 0.065, n=4538) vs every bar -0.309% (se 0.025). Fees 0.10%.

## 9. The rule their entries suggest, tested as a strategy

Take the three strongest habits from §3 for profitable longs, turn them into an entry rule on our 1h bars, hold for their median hold, exit at the close. Baseline: every bar. This is the honest version of 'copy their pattern' — it uses only what the chart shows.

Rule (long): `vol_z` above its market median AND `rv_pctile` above its market median AND `ema20_dist_atr` below its market median; hold 24h.

Fires on 14.7% of bars. Mean forward return over 24h: **-0.054%** (se 0.042) vs every bar +0.155% (se 0.015). Round trip cost 0.10%.
