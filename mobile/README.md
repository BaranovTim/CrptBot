# TradingBot — the phone app

A Flutter app for **iOS and Android**, built to the Stitch design in
`app_idea/stitch_liquid_glass_trading_bot/`, reading the Python stack in this
repo through a small local JSON API.

It reads. It does not trade, hold keys, or place orders.

## Run it

The backend is **deployed** — nothing has to be started on a laptop for the
app to work, and `defaultApiBase()` in `lib/api/client.dart` points at the
droplet. So this is one process:

```bash
cd mobile
flutter run                 # whichever device is connected
flutter run -d "iPhone 17 Pro"
flutter run -d emulator-5554
```

### Against a local backend instead

That is now the special case, so it is the one that has to say so. Start the
API on your Mac:

```bash
python3 serve.py
```

It prints the address to use for each target — `localhost:8787` for the iOS
simulator, `10.0.2.2:8787` for the Android emulator, and the Mac's LAN address
for a physical phone. Point the app at it by tapping the status row on the
login screen, or the API host row on Profile, or bake it into the build:

```bash
flutter run --dart-define=API_BASE=http://192.168.1.20:8787
```

Keep `collect.py` running too if you want the bars to stay current — the app
shows `STALE` rather than pretending otherwise when they are not.

## How a notification reaches your phone

Four things had to be true and only the last one was.

**1. The server has to notice.** `AlertEngine` used to refresh only inside an
`/api/alerts` request, so detection was a side effect of somebody looking. A
transition is defined against the previous observation — with nobody polling
there was no previous observation, so a signal that flipped at 03:00 was not
delivered late, it was **never detected**. It now runs its own loop from
process start (`AlertEngine.start`, every 30s), and a poll only reads what has
already been noticed.

**2. It has to survive a restart.** The log and the seen-ids are persisted to
`data_cache/alerts.v1.json`. A deploy used to empty both, so the next refresh
re-primed from scratch and everything around the restart was swallowed.

**3. The app has to ask from where it left off.** The cursor was a field on
the shell's State, reset to "now" on every launch — so the app deliberately
skipped everything that happened while it was closed, one line after the
server had gone to the trouble of finding it. It lives in storage now
(`alert_feed.dart`), capped at six per collection and six hours of backlog so
a phone that was off overnight does not come back to twenty notifications.

**4. Android has to display it.** `showAlert` went through `zonedSchedule`
with `inexactAllowWhileIdle`, which Android batches and defers while dozing.
The same file already recorded this — `sendTestSuite` abandoned scheduling for
exactly this reason — so the **test button worked while real alerts did not**.
Android now uses `show()`; iOS keeps `zonedSchedule`, where the measurement
went the other way.

### With the app closed

`background.dart` registers a periodic background job on both platforms. It
needs **no paid developer account on either** — the iOS half runs as a
BGAppRefreshTask, and background modes are Info.plist declarations rather than
entitlements tied to an App ID. Push Notifications is the capability a free
personal team cannot have; this is not that.

**Android.** WorkManager, fifteen minutes, which Android treats as a target
rather than a promise. Fine for 1h/4h/1d calls and news; a 15m signal can
arrive late. Force-stopping the app from Settings ends it until you open the
app again — Android's rule for every app. Swiping it out of recents does not.
Aggressive battery management (Xiaomi, Huawei, OnePlus, some Samsungs) will
kill it regardless; that is the first thing to check if alerts stop.

**iOS, which is weaker.** Three moving parts have to agree exactly, and iOS
warns about none of them when they don't — it simply never launches the app:

| | |
|---|---|
| `Info.plist` | `BGTaskSchedulerPermittedIdentifiers` → `com.tradingbot.tradingbotApp.alertPoll`, and `UIBackgroundModes` → `fetch` |
| `AppDelegate.swift` | `WorkmanagerPlugin.registerPeriodicTask` with the same id, in `didFinishLaunchingWithOptions` — BGTaskScheduler rejects a handler registered any later |
| `background.dart` | the same id as `uniqueName`, and `initialDelay` (iOS reads that, not `frequency`) |

The fifteen minutes is a *request*. iOS decides when and whether to run a
refresh, from how often you open the app, battery, and network — a few times a
day is normal. **Swiping the app away in the app switcher stops background
refresh entirely** until you reopen it: harmless on Android, the off switch on
iOS. Low Power Mode disables it, as does Settings → General → Background App
Refresh.

Real-time delivery to a sleeping phone still needs push — FCM or APNs — and
APNs needs the paid account.

The scheduled calendar warnings are unaffected on both: they are handed to the
OS in advance with a fire time and arrive on the minute regardless.

### iOS deployment target

**14.0, not Flutter's default 13.0.** `workmanager_apple` requires it, and the
build fails with a `Target Integrity` error naming the package rather than
anything about notifications. Set in `ios/Podfile` and all three
`IPHONEOS_DEPLOYMENT_TARGET` entries in the Xcode project.

### What is allowed to buzz

Muting is per device, and now per category as well as per pair — the bell
sheet has an **EVERY COIN** section. News runs at about seventy items a day on
these feeds, 35 of them inside one six-hour stretch, so silencing it has to be
easier than revoking the notification permission and losing the trade signals
with it. Signals are additionally gated by the sensitivity setting, using the
same rule as the dashboard card, so the screen and the lock screen can never
disagree.

News gets four levels rather than a switch, because "news" is not one thing
and an on/off control at seventy a day is answered "off":

| | |
|---|---|
| **Everything** | every headline that arrives |
| **Only BULL or BEAR** | direction, either size. Drops MIXED and unreadable |
| **Strong influence only** | STRONG IMPACT, either direction. Keeps MIXED |
| **None** | silent; still all there in the News tab |

The middle two are **literal filters, not a hierarchy**. A STRONG IMPACT story
the scorer reads as MIXED passes *Strong influence* and fails *Only BULL or
BEAR* — the settings ask different questions ("which way?" and "how much?").

Both readings come from `LexiconScorer`, a keyword count that stays silent on
roughly half of all headlines, so every setting but **Everything** is trusting
that scorer to have read the one that mattered. The sheet says so on screen.

## Waiting is not failing

A request that has not answered yet is not an error, and the app no longer
says it is. `lib/widgets/patient_loader.dart` holds the rule: keep the spinner
up and keep retrying for **five minutes**, count the wait out loud in mm:ss,
and add a new line of commentary every minute it drags on. Only when the whole
window passes with nothing getting through does anything red appear.

This replaced a twenty-second HTTP timeout that painted "No link to the
service" over sessions that were about to recover — a lift, a cell handover,
wifi reassociating, or the server building a dashboard it had not cached.

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

## Timeframes

The selector row on the dashboard changes **which model answers**, not just
which candles are drawn. Each of 1m / 5m / 15m / 1h / 4h / 1d is a separately
fitted model, because an order block on a 1m chart and one on a 1d chart are
not the same object — different flow, different horizon, different distance in
ATR terms.

Untrained timeframes stay in the row, dimmed with a **hollow ring**. Hiding
them would make the selector look complete when it is not, and the badge is
the point: only a fitted model has an honest probability. Tapping one goes to
the training screen with the command to fit it, because the server answers
`409 untrained` rather than pretending.

The header shows the pair *and* its context timeframe — `1h windows · 4h
context` — since the higher timeframe is part of what the model was fitted on.

### Across timeframes

The panel under the levels table shows what every fitted timeframe says at
once, as bars centred on 50% (anchoring at zero would make every reading look
enormous). It loads **after** the main screen rather than with it: consensus
builds a dashboard per timeframe, and putting it in the same `Future.wait`
held the screen on a spinner for twelve seconds.

It is a view, not a model. The rows are independent models answering their own
questions — the 1m model is not told what the 4h model thinks. Disagreement is
the useful part; rows that agree are one piece of evidence repeated.

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
