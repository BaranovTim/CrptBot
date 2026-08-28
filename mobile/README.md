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
