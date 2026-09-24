"""Which coins does the 4h system actually make money on? A screen, coin by coin.

WHY A SCREEN AND NOT A POOL
    Since 2026-09-24 every coin is ranked against its own history
    (Agent5Config.rank_scope = "coin"), so a coin's calls do not depend on
    which other coins are served. That makes the question per coin: scored by
    the model the server runs (trained on the fifteen, five seeds, stop 0.5
    ATR beyond the level) and traded exactly as the server trades (resting
    entry, a third off halfway), does THIS coin's record pay?

THE TRAP, AND THE GUARD
    Screen thirty coins and keep the ten best-looking, and some will be
    luck. So a coin must pay on the development half-years (Sep 2023 -> Sep
    2025) AND, separately, on the holdout year (Sep 2025 -> Sep 2026), with
    at least MIN_TRADES trades in each. The report also says how many coins
    that passed development went on to pass the holdout: if that is no better
    than chance, the screen is finding luck, not coins.

    python3 research/coin_screen.py fetch       # 4h history for the candidates
    python3 research/coin_screen.py build       # the lab datasets (15 + candidates)
    python3 research/coin_screen.py run         # the fifteen's model scores them all
    python3 research/coin_screen.py report      # per coin, and the pass list

Reuses research/expand_coins.py (its fetch, frames and lab plumbing) in its
own staging folder; the fifteen's files are linked read-only, never written.
"""
from __future__ import annotations

import json
import pickle
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import research.expand_coins as X          # noqa: E402

# the thirty most-traded USDT perpetuals (median daily dollar volume, Jun-Aug
# 2026) listed before April 2023, less the fifteen served and the ten that
# failed on 2026-09-24; value = Binance's listing date (exchangeInfo onboardDate)
CANDIDATES = {
    "XLMUSDT": "2020-01-20", "TRXUSDT": "2020-01-15", "INJUSDT": "2022-08-16",
    "XMRUSDT": "2020-02-03", "FETUSDT": "2023-01-15", "1000SHIBUSDT": "2021-05-10",
    "APTUSDT": "2022-10-18", "HBARUSDT": "2021-03-17", "OPUSDT": "2022-06-01",
    "ETCUSDT": "2020-01-16", "DASHUSDT": "2020-02-04", "ICPUSDT": "2021-07-30",
    "CRVUSDT": "2020-09-01", "LDOUSDT": "2022-09-21", "ATOMUSDT": "2020-02-07",
    "ALGOUSDT": "2020-06-16", "CHZUSDT": "2021-01-21", "GALAUSDT": "2021-09-17",
    "IDUSDT": "2023-03-23", "AXSUSDT": "2020-11-20", "SANDUSDT": "2021-01-18",
    "DYDXUSDT": "2021-09-09", "1000LUNCUSDT": "2022-09-08", "ZENUSDT": "2020-11-24",
    "APEUSDT": "2022-03-17", "TLMUSDT": "2023-03-29", "ENSUSDT": "2021-11-29",
    "ARUSDT": "2021-09-28", "STXUSDT": "2023-02-20", "VETUSDT": "2020-02-14",
}
MIN_TRADES = 20
STAGING = Path("data_cache/staging_screen")
OUT = Path("research/results/coin_screen")


def _point_expand_coins() -> None:
    """expand_coins reads its folders and coin lists as module globals at
    call time: repoint them here, before anything runs."""
    X.STAGING = STAGING
    X.BARS = STAGING / "bars"
    X.LAB_FRAMES = STAGING / "research_frames"
    X.TRAIN_4H = STAGING / "train_frames_4h"
    X.FRAMES_1D = STAGING / "frames_1d"
    X.MODELS = STAGING / "models"
    X.LAB_OUT = OUT
    X.NEW = dict(CANDIDATES)
    X.ALL = sorted(X.OLD + list(CANDIDATES))


def rest_seed(sym: str, interval: str) -> int:
    """The coin's whole history via Binance's REST klines, 1,500 bars a
    request, into the same store format the archive path writes (both go
    through marketdata.binance._finalise). The monthly archive ran ~13 s a
    file from here; this is ~9 requests for four years of 4h."""
    from marketdata.binance import fetch_klines_rest
    from livefeed.store import BarStore

    store = BarStore(sym, interval, directory=X.BARS)
    marker = store.dir / ".seeded_from"
    start = X._start(sym, interval)
    if marker.exists() and marker.read_text().strip() <= start:
        return 0
    step = {"4h": pd.Timedelta(hours=4), "1d": pd.Timedelta(days=1)}[interval]
    t = pd.Timestamp(start, tz="UTC"); now = pd.Timestamp.now(tz="UTC"); parts = []
    while t < now:
        df = fetch_klines_rest(sym, interval, start=str(t.tz_convert(None)), limit=1500)
        if df is None or df.empty:
            break
        parts.append(df)
        nxt = df.index.max() + pd.Timedelta(milliseconds=1)
        if nxt <= t:
            break
        t = nxt
        time.sleep(0.35)                     # ~10 weight a call, well under 2,400 a minute
    if not parts:
        return 0
    bars = pd.concat(parts); bars = bars[~bars.index.duplicated(keep="last")].sort_index()
    n = store.append(bars)
    store.dir.mkdir(parents=True, exist_ok=True)
    marker.write_text(start)
    return n


def fetch_rest() -> None:
    """Every candidate's 4h and 1d bars, sequentially (one weight budget)."""
    X.BARS.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    for sym in CANDIDATES:
        try:
            n4, n1 = rest_seed(sym, "4h"), rest_seed(sym, "1d")
            print(f"{sym}: +{n4:,} 4h, +{n1:,} 1d bars [{time.time() - t0:.0f}s]", flush=True)
        except Exception as e:
            print(f"{sym}: REST failed ({e})", flush=True)


def fetch(part: int = 0, parts: int = 1) -> None:
    """4h and 1d bars, funding and the 4h lab frames (cut where the fifteen's
    lab data ends), per candidate; the fifteen's frames linked. `part` of
    `parts` takes every parts-th coin, so several processes can share it."""
    from marketdata.funding import load_funding

    for d in (X.BARS, X.LAB_FRAMES):
        d.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    for sym in list(CANDIDATES)[part::parts]:
        p = X.LAB_FRAMES / f"{sym}_4h_frames.pkl"
        if p.exists():
            continue
        try:
            b4 = X.fetch_bars(sym, "4h")
            X.fetch_bars(sym, "1d")               # the 4h features' higher timeframe
            load_funding(sym, start=X.FUNDING_START)
            pickle.dump(X._frames(b4[b4.index <= X.LAB_END], sym, "4h"), open(p, "wb"))
            print(f"{sym}: {len(b4):,} bars from {b4.index[0]:%Y-%m-%d} [{time.time() - t0:.0f}s]", flush=True)
        except Exception as e:                     # a coin that cannot be built sits out
            print(f"{sym}: SKIPPED ({e})", flush=True)
    X.link_old()


def build() -> None:
    L = X.lab()
    have = sorted(p.name.split("_4h_")[0] for p in X.LAB_FRAMES.glob("*_4h_frames.pkl"))
    X.ALL = have                                   # the candidates that could be built
    if not all((L.CACHE / f"{s}_sb0.5.pkl").exists() for s in ("long", "short")):
        L.build_stop_buffer(0.5)
    for side in ("long", "short"):
        D = L.load(side, "sb0.5")
        print(f"{side}: {len(D):,} rows over {D['coin'].nunique()} coins", flush=True)


def run() -> None:
    X.lab_run(["x15_bag5"])


def report() -> dict:
    import research.improve_4h as I
    L = X.lab()
    I._BARS.clear()
    R = L.with_ranks(pickle.load(open(L.SCORES / "x15_bag5_scores.pkl", "rb")))
    C = R[R["rank_coin"] >= 0.97].copy()
    idx = []
    for coin, g in C.groupby("coin"):
        d = I.coin_data(coin)
        idx.append(pd.Series(d["bars"].index.get_indexer(pd.DatetimeIndex(g["t"])), index=g.index))
    C["i"] = pd.concat(idx)
    C = C[C["i"] >= 0].reset_index(drop=True)
    D = I.served(C, wait=6, scale_part=1 / 3, scale_at=0.5)
    D["net"] = D["pnl"] - L.COST - D["funding"]
    D["hold"] = D["window"] >= L.DEV_WINDOWS
    rows = []
    for coin, g in D.groupby("coin"):
        dv, ho = g[~g["hold"]], g[g["hold"]]
        rows.append(dict(coin=coin, served=coin in X.OLD,
                         n_dev=len(dv), n_hold=len(ho),
                         win_dev=float((dv["net"] > 0).mean()) if len(dv) else np.nan,
                         win_hold=float((ho["net"] > 0).mean()) if len(ho) else np.nan,
                         net_dev=float(dv["net"].mean()) if len(dv) else np.nan,
                         net_hold=float(ho["net"].mean()) if len(ho) else np.nan))
    T = pd.DataFrame(rows).sort_values(["served", "net_dev"], ascending=[False, False])
    T["enough"] = (T["n_dev"] >= MIN_TRADES) & (T["n_hold"] >= MIN_TRADES)
    T["pays_dev"] = T["enough"] & (T["net_dev"] > 0)
    T["pays_hold"] = T["enough"] & (T["net_hold"] > 0)
    T["passes"] = T["pays_dev"] & T["pays_hold"]
    new = T[~T["served"]]
    served = T[T["served"]]
    pd.set_option("display.width", 200)
    print("per coin: the fifteen's model, each coin ranked on its own history, a third off halfway")
    print(T.round(3).to_string(index=False))
    print(f"\nthe fifteen served today: {int(served['pays_dev'].sum())}/{len(served)} pay on development, "
          f"{int(served['pays_hold'].sum())}/{len(served)} on the holdout")
    k_dev = int(new["pays_dev"].sum())
    k_both = int(new["passes"].sum())
    base = float(new.loc[new["enough"], "pays_hold"].mean()) if new["enough"].any() else float("nan")
    print(f"candidates: {len(new)} screened, {int(new['enough'].sum())} with enough trades; "
          f"{k_dev} pay on development; of those {k_both} also pay on the holdout "
          f"({k_both / max(k_dev, 1):.0%}), against {base:.0%} of all candidates paying on the holdout")
    picks = new[new["passes"]].sort_values("net_dev", ascending=False)
    print("PASS (pays in both periods):", ", ".join(picks["coin"]))
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "screen.json").write_text(json.dumps({
        "rows": T.replace({np.nan: None}).to_dict("records"),
        "passes": list(picks["coin"])}, indent=1, default=float))
    return {"table": T, "picks": list(picks["coin"])}


if __name__ == "__main__":
    _point_expand_coins()
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "fetch":
        fetch(*[int(a) for a in sys.argv[2:4]])
    elif cmd == "fetch-rest":
        fetch_rest()
    else:
        {"build": build, "run": run, "report": report}[cmd]()
