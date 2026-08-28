# TradingBot — the phone app

A Flutter app for **iOS and Android**, built to the Stitch design in
`app_idea/stitch_liquid_glass_trading_bot/`, reading the Python stack in this
repo through a small local JSON API.

It reads. It does not trade, hold keys, or place orders.

## Run it

Two processes. The API first, on your Mac:

```bash
python3 serve.py
```

It prints the address to use for each target. Then:

```bash
cd mobile
flutter run                 # whichever device is connected
flutter run -d "iPhone 17 Pro"
flutter run -d emulator-5554
```

The base URL is picked per platform — `localhost:8787` for the iOS simulator,
`10.0.2.2:8787` for the Android emulator. A **physical phone** needs your
Mac's LAN address, which `serve.py` prints. Set it without rebuilding by
tapping the status row on the login screen, or the API host row on Profile.
To bake it in:

```bash
flutter run --dart-define=API_BASE=http://192.168.1.20:8787
```

Keep `collect.py` running too if you want the bars to stay current — the app
shows `STALE` rather than pretending otherwise when they are not.

## Screens

| Screen | Source of truth |
|---|---|
| Secure link | probes `/api/coins`; the credential fields are a local label and never leave the device |
| Dashboard | `/api/dashboard` — status, whale filings, price, indicators, the recommendation, TP/SL |
| Market | `/api/coins` — live prices, and which pairs actually have a fitted model |
| Training | `/api/training` — fitted horizons, or the exact command to fit one |
| Profile | the Pro panel from the design, plus the API host setting |

## Three places the app disagrees with the mockup

In each case the mockup showed a sample state and the backend won:

**RECOMMENDED ACTION.** The mockup shows a confident `BUY`. The card renders
whatever `evaluate()` decided, which is usually `FLAT`, because EV after costs
does not clear the threshold. `tests/test_api.py` asserts a waiting backend
can never render as BUY — a dashboard that always says BUY is a screenshot,
not an instrument.

**BOT ACTIVE → WATCHING.** Nothing here places an order. A badge implying
autonomy would be the most misleading pixel in the app.

**Data by TradingView → Binance.** Which is where the bars are actually from.

The mockup's **NOT TRAINED** badge needed no change: models exist for BTCUSDT
and nothing else, so it was already telling the truth. Tapping an untrained
pair routes to Training rather than to a dashboard with no honest probability
to show.

## Two rendering traps this hit, both silent

Worth knowing before editing the widgets, because neither produced an error.

**`BackdropFilter` paints nothing inside these scrollables on Impeller.**
Panels became invisible while their non-glass siblings rendered normally — a
heading would show and the card under it would not. Turning off ListView's
repaint boundaries did not help. `GlassPanel` now uses a translucent fill and
a gradient stroke instead, which over a near-uniform obsidian floor is
visually the same thing: blurring an almost-flat backdrop by 30px produces
almost exactly the flat fill you get by not blurring. `FrostedNav` keeps a
real blur — it sits outside the scroll view, where it works.

**`CrossAxisAlignment.stretch` on a Row inside a ListView** asks children to
fill an unbounded cross axis, and the layout fails with "BoxConstraints forces
an infinite height" — which takes down the whole screen, not just that row.
The stat-card grid wraps its Row in `IntrinsicHeight`.

## Notifications — what is reliable and what is not

These are LOCAL notifications. There is no push server, no Firebase, no APNs
certificate, and the split that follows is a consequence of that, not a
choice:

| | |
|---|---|
| **Scheduled events** (FOMC, anything in `calendar.json`) | **Fully reliable.** The OS is handed the fire time in advance and delivers it whether the app is running, backgrounded or killed. Verified: a T-5min warning arrived as a banner with the app closed. |
| **Signals, filings, news, spikes** | Only as timely as the app's next poll. Foreground: ~20s. Backgrounded: iOS suspends the app, so **not at all** until you open it. |

Making the second row as reliable as the first needs real push, which needs an
Apple Developer account and a server holding APNs/FCM credentials. `/api/alerts`
is already shaped for it — it would become the thing that *pushes* rather than
the thing that is polled — but nothing here pretends to be that today.

**iOS suppresses this app's notifications while it is in the foreground.**
Measured, not assumed: `presentAlert`, `presentBanner` and `presentList` all
set, delivered through both `show()` and `zonedSchedule()`, and nothing
appeared until the app was backgrounded. That is ordinary iOS behaviour, so
the app draws its **own** in-app banner when it is on screen and leaves OS
notifications for when it is not.

Every alert carries **when the thing happened**, not when it was noticed. An
SEC filing discloses a trade up to five days old, so a whale alert reads
`Traded 24/08 · disclosed 26/08 (52h later)`. Collapsing those into "now"
would imply a freshness the data does not have.

### Where scheduled events come from

`federalreserve.gov` publishes the FOMC calendar as plain HTML, no key, years
ahead. Statements land at 14:00 America/New_York on the **second** day of a
meeting — computed through a real timezone, because half the year that is
19:00 UTC and half it is 18:00, and a one-hour error puts a 60-minute warning
on the wrong side of the event.

BLS (CPI, payrolls) returns **403 to anything that is not a browser**, so those
cannot be fetched. Add them by hand in `data_cache/calendar.json`:

```json
[{"title": "US CPI (Aug)", "at": "2026-09-11T12:30:00+00:00",
  "impact": "high", "note": "08:30 ET"}]
```

`at` must carry a UTC offset — a naive timestamp is rejected rather than
guessed at. One malformed row is skipped with a warning instead of taking the
Fed dates down with it.

## Live price

Price comes **straight from Binance to the phone**, not through the Python
server. The server's copy is a REST fetch cached 5s behind a 10s poll — up to
15 seconds stale next to the Binance app, which was the original complaint.

The stream is `fstream.binance.com/ws/btcusdt@bookTicker`, and that endpoint
was chosen by measurement. Over 8 seconds each, against the USD-M futures host:

```
/ws/btcusdt@aggTrade      0 messages
/ws/btcusdt@ticker        0 messages
/ws/btcusdt@bookTicker    6994 messages
/stream?streams=a/b       handshake 101, then silence
```

The trade streams and the combined `/stream?streams=` form complete their
handshake and then deliver nothing — no error, no close frame, a socket that
looks healthy and says nothing. Two consequences are baked into the client:
the connection indicator is derived from **when a frame last arrived**, never
from whether the socket is open; and a **watchdog** forces a reconnect after
15 seconds of silence, because reconnect logic hanging off `onDone`/`onError`
cannot help when neither ever fires.

At ~875 messages/second, frames are coalesced and emitted at ~8 Hz. Calling
`setState` on each would empty the battery.

The 24h change still comes from the server: it needs the `@ticker` stream,
which is one of the silent ones, and a few seconds of staleness on a 24-hour
number does not matter.

**TP and SL track the live price.** The model fixed the barrier *distance* at
the last close, not the price it is measured from — so the levels shown are
the ones for an entry right now, and the anchor close is printed underneath.
The probability is still the one read at that close, which the footnote says.

## Tapping the chart

Opens the Binance app at BTCUSDT perpetuals, falling back to the web trade
page when the app is not installed. iOS needs `bnc` declared in
`LSApplicationQueriesSchemes` or `canLaunchUrl` always returns false; Android
11+ needs the matching `<queries>` entries or every tap goes to the browser.

## `build/` is a symlink, on purpose

This project lives on an iCloud-synced Desktop. iCloud stamps directories with
`com.apple.FinderInfo` and `com.apple.fileprovider.fpfs#P`, and `codesign`
refuses to sign inside them:

```
Failed to codesign .../Flutter.framework/Flutter with identity -.
  resource fork, Finder information, or similar detritus not allowed
```

Flutter strips `com.apple.FinderInfo` from the *binary* before signing, but
not from the bundle *directory*, so the build fails every time. The fix is to
keep build output off the synced volume:

```bash
mkdir -p ~/Library/Caches/tradingbot-flutter-build
ln -s ~/Library/Caches/tradingbot-flutter-build mobile/build
```

Recreate that link after a `flutter clean`, or move the repo somewhere iCloud
does not sync.

## What is deliberately not here

**No auth.** There is no account server, so the login screen's fields are a
local label. Nothing is transmitted, nothing is stored. Building a convincing
credential form that quietly posted a password somewhere would be worse than
building nothing.

**No billing.** The Pro panel is the design; there is no store product and no
entitlement server, so nothing can charge anyone. It says so on the screen.

Worth remembering if that panel ever becomes real: tiering by *precision*
means deliberately serving worse numbers to people who paid less. Tiering by
coverage — more pairs, more timeframes, alert latency, history — sells the
same honest number to everyone. The listed perks are the second kind.

**No prediction journal.** The app reads live state and keeps nothing, so
none of it accumulates into a track record. That gap is in the backend, not
here.
