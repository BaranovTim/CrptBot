import pandas as pd
import pandas_ta as ta
import config
from oandapyV20 import API
import oandapyV20.endpoints.instruments as instruments

client = API(access_token=config.OANDA_API_KEY)

timeframe = '1H'
instrument = 'BTCUSDT'

def get_candles(tf):
    params = {
        'granularity': tf,
        'price': 'A' #ask for price
    };

    r = instruments.InstrumentsCandles(instrument = instrument, params=params)
    candles = client.request(r)["candles"]

    data = []

    for c in candles:
        if c["complete"]:
            data.append({
                "time": c["timeframe"],
                "open": float(c["ask"]["o"]),
                "high": float(c["ask"]["h"]),
                "low": float(c["ask"]["l"]),
                "close": float(c["ask"]["c"])
        })

    df = pd.DataFrame(data)
    df["time"] = pd.to_datetime(df["time"])

    return df

price = get_candles(timeframe)
price