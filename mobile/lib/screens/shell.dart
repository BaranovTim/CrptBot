/// The tab host: mesh floor, a thin top bar, and the floating frosted nav.
///
/// It also owns the two things that must keep running whichever tab is on
/// screen — the live price socket and the alert loop. Putting them in the
/// dashboard would mean price went stale and notifications stopped the moment
/// you opened Market.
library;

import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../api/alert_feed.dart';
import '../api/background.dart';
import '../api/client.dart';
import '../api/market_mode.dart';
import '../api/live_price.dart';
import '../api/market_ticker.dart';
import '../api/models.dart';
import '../api/settings.dart';
import '../api/muted.dart';
import '../api/watchlist.dart';
import '../api/notifications.dart';
import '../api/push.dart';
import '../api/trades.dart';
import '../theme/liquid_obsidian.dart';
import '../widgets/acknowledgement.dart';
import '../widgets/alert_settings_sheet.dart';
import '../widgets/frosted_nav.dart';
import '../widgets/glass.dart';
import '../widgets/mesh_background.dart';
import 'dashboard_screen.dart';
import 'market_screen.dart';
import 'news_screen.dart';
import 'screener_screen.dart';
import 'stock_market_screen.dart';
import 'stock_screen.dart';
import 'profile_screen.dart';
import 'subscribe_screen.dart';

class Shell extends StatefulWidget {
  const Shell({
    super.key,
    required this.client,
    required this.account,
    required this.onAccountChanged,
    required this.onSignOut,
  });

  final ApiClient client;

  /// Who is signed in. Drives which tabs open and what the dashboard shows.
  ///
  /// The app hides screens for the sake of a coherent experience; the API
  /// refuses them for the actual guarantee. Both, because a client-side gate
  /// alone is a gate anybody can edit out, and a server-side gate alone means
  /// a UI full of tabs that answer 402.
  final Account account;

  final ValueChanged<Account> onAccountChanged;
  final VoidCallback onSignOut;

  @override
  State<Shell> createState() => _ShellState();
}

class _ShellState extends State<Shell> with WidgetsBindingObserver {
  NavTab _tab = NavTab.dashboard;
  String _symbol = 'BTCUSDT';

  /// The selected timeframe. Not a chart setting — each timeframe is its own
  /// fitted model, so this decides which model answers.
  String _interval = '4h';

  late final LivePriceService _live = LivePriceService(symbol: _symbol);
  Timer? _alertTimer;
  Timer? _calendarTimer;

  /// A poll is already out. The cursor is shared state now, so two
  /// overlapping polls would each advance it and the second would step over
  /// alerts the first had not yet shown.
  bool _polling = false;

  @override
  void initState() {
    super.initState();
    // The notice, once per account. Deferred to the first frame because it
    // needs a Navigator, and this runs before one exists.
    WidgetsBinding.instance.addPostFrameCallback((_) => _maybeAcknowledge());
    WidgetsBinding.instance.addObserver(this);
    _live.start();
    _startAlerts();
    _loadOrder();
    // prime the mute set: `isMuted` is synchronous by necessity and
    // reports nothing muted until this lands
    Muted.instance.load().then((_) => mounted ? setState(() {}) : null);
    // The market switch decides what four of the five tabs are about, so the
    // shell rebuilds when it changes rather than each screen polling it.
    MarketModeStore.instance.addListener(_onModeChanged);
    // Opening or closing a logged entry changes what the push relay should
    // say about a signal on that coin, and the relay only knows what the
    // phone has shipped. The fingerprint in push.dart makes this free when
    // nothing relevant changed.
    Trades.instance.addListener(_onTradesChanged);
    // A tapped notification, while the app is running...
    _tapSub = Notifications.instance.taps
        .listen((r) => _openSymbol(r.symbol, r.interval));
    // ...and the one that may have STARTED the app, which arrives no other
    // way. Asked once, here, after the first frame can take a setState.
    Notifications.instance.launchRequest().then((r) {
      if (r != null && mounted) _openSymbol(r.symbol, r.interval);
    });
    _positionsSub = _positions.stream.listen(
        (p) => unawaited(Trades.instance.checkLive(p, client: widget.client)));
    _positions.start();
    unawaited(_watchPositions());
    MarketModeStore.instance.load();
    _loadFollowed();
    // Restore the timeframe last looked at, in either market. Without this
    // the setting was written by the stock page and read by nobody.
    Settings.instance.lastInterval().then(
        (v) => mounted ? setState(() => _interval = v) : null);
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    MarketModeStore.instance.removeListener(_onModeChanged);
    Trades.instance.removeListener(_onTradesChanged);
    _tapSub?.cancel();
    _positionsSub?.cancel();
    _positions.dispose();
    _alertTimer?.cancel();
    _calendarTimer?.cancel();
    _live.dispose();
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    // iOS tears down sockets in the background. Coming back to a dead socket
    // that silently never reconnects is how a "live" price ends up frozen at
    // whatever it was when you last looked.
    if (state == AppLifecycleState.resumed) {
      _live.start();
      _pollAlerts();
      // Back from Stripe. The upgrade happened on the server, over a webhook
      // sent while this app was in the background, so nothing in here knows
      // about it yet and no amount of local state can work it out.
      if (_awaitingCheckout) _pollForUpgrade();
      // Whatever price did while this was in the background. The
      // background poll is meant to have caught it, and on Android it
      // frequently has not run at all -- so ask the server for the high
      // and low since each entry, here, where the app is definitely awake.
      unawaited(Trades.instance.settle(widget.client));
    }
  }

  /// Ask repeatedly, because the upgrade is not ours to wait for.
  ///
  /// Stripe posts the webhook to the server within a second or two of the
  /// payment, but a customer who taps straight back can beat it. A single
  /// check would then show the paywall to somebody who has just paid -- the
  /// exact thing this exists to prevent -- so it asks again a few times
  /// before giving up, and gives up quietly: the account is correct on the
  /// server either way, and the next launch will read it.
  Future<void> _pollForUpgrade() async {
    for (final wait in const [Duration.zero, Duration(seconds: 2),
                              Duration(seconds: 5), Duration(seconds: 10)]) {
      if (!mounted) return;
      if (wait > Duration.zero) await Future<void>.delayed(wait);
      if (!mounted) return;
      if (await _refreshAccount()) break;
    }
    _awaitingCheckout = false;
  }

  Future<void> _startAlerts() async {
    await Notifications.instance.init();
    // the bell in the top bar reflects whether permission was granted, and
    // that answer only exists after the OS prompt is resolved
    if (mounted) setState(() {});

    // Wake up while the app is closed. Registered here rather than in main()
    // because there is no point polling for an account that is not signed in.
    unawaited(startBackgroundAlerts());

    // And keep the server's copy of the filtering settings current, so what
    // the relay decides to push matches what this screen would have shown.
    // A no-op unless push is switched on and something actually changed —
    // see the fingerprint in `push.dart`, which is why this can be called
    // from the poll below as well without costing a request every 20s.
    unawaited(PushDelivery.instance.sync(widget.client));

    // NO SEPARATE PRIMING CALL ANY MORE.
    //
    // It used to fetch once with no cursor and keep the answer, which reset
    // the position to "now" on every launch — deliberately discarding
    // everything that happened while the app was closed. Since that is most
    // of the time, most alerts were thrown away by the app itself, one line
    // after the server had gone to the trouble of finding them.
    //
    // The cursor is stored on the device now. A FIRST EVER run still gets no
    // backlog, because there is no stored cursor and the server returns
    // nothing without one.
    await _pollAlerts();

    _alertTimer =
        Timer.periodic(const Duration(seconds: 20), (_) => _pollAlerts());
    _refreshCalendar();
    _calendarTimer =
        Timer.periodic(const Duration(hours: 6), (_) => _refreshCalendar());
  }

  Future<void> _pollAlerts() async {
    // Two polls at once would each advance the shared cursor, and whichever
    // finished second would move it past alerts the first had not delivered.
    if (_polling) return;
    _polling = true;
    try {
      // Muting, sensitivity and the backlog cap all live in `alert_feed`, so
      // the background isolate applies exactly the same rules. Reimplementing
      // them here is how the two would drift.
      unawaited(PushDelivery.instance.sync(widget.client));
      final batch = await collectAlerts(widget.client);
      for (final a in batch.deliver) {
        // Two channels, because iOS suppresses this app's notifications while
        // it is in the FOREGROUND — measured, not assumed: presentAlert,
        // presentBanner and presentList all set, delivered via both show()
        // and zonedSchedule(), and nothing appeared until the app was
        // backgrounded. That is ordinary iOS behaviour, and the ordinary
        // answer is to draw your own banner when you are on screen.
        //
        // So: an in-app banner when the user can see the app, and an OS
        // notification for when they cannot.
        //
        // NO LONGER SUPPRESSED WHEN THE SERVER SAYS IT PUSHED.
        //
        // `a.pushed` means the relay handed the alert to ntfy and ntfy
        // accepted it. It does NOT mean a phone displayed it — ntfy's own
        // app can be battery-optimised, unsubscribed, denied notification
        // permission, or simply not installed, and the server cannot see any
        // of that.
        //
        // Trusting it cost exactly what you would expect: the relay reported
        // 30 successful sends with no errors while the phone showed nothing,
        // because this line was suppressing the one notification that would
        // have got through. That is the "no state in which both go quiet"
        // promise in `push.dart` being broken by the very flag written to
        // uphold it.
        //
        // So both paths post again. A duplicate is a nuisance; silence is a
        // broken feature, and only one of those is worth defending against.
        // Silenced: neither. The alert is still in the bell.
        if (Notifications.instance.silenced) continue;
        await Notifications.instance.showAlert(a);
        // the banner carries the same "you hold this, and here is what to
        // do" lines the lock-screen notification does
        final held = a.kind == 'signal'
            ? await heldLine(a.symbol, alertInterval: a.interval, to: a.extra['to'])
            : null;
        _banner(a, held: held);
      }
    } catch (e) {
      debugPrint('[alerts] poll failed: $e');
      // a missed poll is not worth surfacing; the next one covers it
    } finally {
      _polling = false;
    }
  }

  /// Books the pre-event warnings with the OS.
  ///
  /// These are the only alerts that survive the app being killed, because the
  /// fire time is known in advance and the OS holds it. Everything else here
  /// depends on this process being alive to poll.
  Future<void> _refreshCalendar() async {
    try {
      final events = await widget.client.calendar(days: 21);
      await Notifications.instance.scheduleCalendar(events);
    } catch (_) {
      // keep whatever is already booked with the OS
    }
  }

  /// The in-app version of an alert. Carries the same "when it happened"
  /// line as the notification, because a filing from four days ago should
  /// never read as breaking news.
  ///
  /// TAPPING IT OPENS THE COIN, on the alert's timeframe, exactly as tapping
  /// the same alert in the shade does. Swiping it away still dismisses it.
  /// A banner that names a coin and does nothing when pressed reads as
  /// broken, and it was.
  void _banner(Alert a, {String? held}) {
    if (!mounted) return;
    final tone = switch (a.kind) {
      'signal' => Obsidian.green,
      'spike' => Obsidian.red,
      'calendar' => Obsidian.primary,
      _ => Obsidian.onSurfaceVariant,
    };
    final messenger = ScaffoldMessenger.of(context);
    messenger
      ..removeCurrentSnackBar()
      ..showSnackBar(SnackBar(
        backgroundColor: Obsidian.surfaceHigh,
        duration: const Duration(seconds: 8),
        behavior: SnackBarBehavior.floating,
        margin: const EdgeInsets.fromLTRB(16, 0, 16, Obsidian.navClearance + 8),
        shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(Obsidian.rLg),
            side: BorderSide(color: tone.withValues(alpha: 0.45))),
        content: GestureDetector(
          key: ValueKey('banner-${a.id}'),
          behavior: HitTestBehavior.opaque,
          onTap: a.symbol.isEmpty
              ? null
              : () {
                  messenger.hideCurrentSnackBar();
                  _openSymbol(a.symbol, a.interval);
                },
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            mainAxisSize: MainAxisSize.min,
            children: [
              Text(a.title,
                  style: Obsidian.bodyLg(color: tone)
                      .copyWith(fontWeight: FontWeight.w700)),
              const SizedBox(height: 4),
              if (held != null) ...[
                Text(held,
                    style: Obsidian.body(size: 12.5)
                        .copyWith(fontWeight: FontWeight.w600)),
                const SizedBox(height: 4),
              ],
              Text(a.body, style: Obsidian.body(size: 12.5)),
              const SizedBox(height: 4),
              Text(a.whenLine(),
                  style: Obsidian.labelSm(color: Obsidian.outline, size: 10)),
            ],
          ),
        ),
      ));
  }

  /// Which pairs the swipe moves through, in the order Market shows them.
  List<String> _order = const [];

  Future<void> _loadOrder() async {
    final list = await Watchlist.instance.load();
    if (mounted) setState(() => _order = List<String>.from(list));
  }

  void _pick(Coin c) {
    _setSymbol(c.symbol);
    setState(() => _tab = NavTab.dashboard);
  }

  /// Point everything at a different pair.
  ///
  /// The socket has to move with it. Leaving it on the old pair would show
  /// the previous coin's live price under the new coin's name — which reads
  /// as correct and is not.
  void _setSymbol(String s) {
    if (s == _symbol) return;
    setState(() => _symbol = s);
    _live.switchTo(s);
  }

  /// Show THIS pair on THIS timeframe, from wherever the app is.
  ///
  /// The one door for every "take me to DOGEUSDT 4h": a tapped
  /// notification, a logged entry in Profile, a call on the Market page.
  /// It works out the market from the symbol -- a USDT pair is crypto,
  /// anything else is a stock -- and switches the mode if needed, so a
  /// stock alert opens the stock dashboard even if you were looking at
  /// coins. A null interval keeps the current one.
  Future<void> _openSymbol(String symbol, String? interval) async {
    final sym = symbol.trim().toUpperCase();
    if (sym.isEmpty) return;
    final want = marketFor(sym);
    final crypto = want == MarketMode.crypto;
    if (MarketModeStore.instance.mode != want) {
      await MarketModeStore.instance.set(want);
    }
    if (interval != null && interval.isNotEmpty) {
      unawaited(Settings.instance.saveInterval(interval));
    }
    if (!mounted) return;
    setState(() {
      if (crypto) {
        _symbol = sym;
      } else {
        _stock = sym;
      }
      if (interval != null && interval.isNotEmpty) _interval = interval;
      _tab = NavTab.dashboard;
    });
    if (crypto) _live.switchTo(sym);
  }

  /// Swipe to the neighbouring pair, wrapping at both ends.
  ///
  /// The TIMEFRAME IS DELIBERATELY UNTOUCHED. Looking at 15m on BTC and
  /// swiping to ETH means you want ETH's 15m — being dropped back to 1h
  /// every time would make the gesture useless for comparing pairs, which is
  /// the whole reason to swipe.
  void _swipe(int delta) {
    if (_order.length < 2) return;
    final i = _order.indexOf(_symbol);
    if (i < 0) {
      _setSymbol(_order.first);
      return;
    }
    _setSymbol(_order[(i + delta) % _order.length]);
  }

  /// Android's back gesture, in two steps.
  ///
  /// Anywhere but the first tab, back goes to the first tab — the same thing
  /// the hardware button does in almost every tabbed app, and the thing that
  /// makes a four-tab app feel navigable rather than one press from gone.
  ///
  /// On the first tab it asks before leaving. Losing a screen you were
  /// reading because your thumb was near the edge is a small thing that feels
  /// like the app is broken.
  Future<void> _onBack(bool didPop) async {
    if (didPop) return;                    // the framework already handled it

    if (_tab != NavTab.dashboard) {
      setState(() => _tab = NavTab.dashboard);
      return;
    }

    final leave = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        backgroundColor: Obsidian.surfaceContainer,
        shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(Obsidian.rLg)),
        title: Text('Close Vanth?', style: Obsidian.headlineMd()),
        content: Text(
            'Alerts only arrive while the app is running, so closing it '
            'stops notifications until you open it again.',
            style: Obsidian.body(color: Obsidian.outline)),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(ctx, false),
              child: Text('Stay', style: Obsidian.body())),
          TextButton(
              onPressed: () => Navigator.pop(ctx, true),
              child: Text('Close',
                  style: Obsidian.body(color: Obsidian.redSoft))),
        ],
      ),
    );

    if (leave == true) {
      // SystemNavigator.pop, not exit(0): this asks the platform to move the
      // app to the background the way the home button does, so it can be
      // resumed. exit(0) kills the process, and Android treats a process that
      // kills itself as a crash for the purposes of restart behaviour.
      await SystemNavigator.pop();
    }
  }

  @override
  Widget build(BuildContext context) {
    return PopScope(
      // canPop stays false so the pop is always ours to decide. Returning
      // true here would let the first back press close the app before the
      // dialog could be answered.
      canPop: false,
      onPopInvokedWithResult: (didPop, _) => _onBack(didPop),
      child: Scaffold(
      body: MeshBackground(
        child: SafeArea(
          bottom: false,
          child: Column(
            children: [
              _topBar(),
              // Only the visible tab is built.
              //
              // An IndexedStack would keep the other three alive, which reads
              // like the better choice — until it hits a framework assertion,
              // `!semantics.parentDataDirty`, raised while compiling semantics
              // for the hidden children. It throws every frame, the frame
              // never completes, and the result is a blank screen with no
              // error painted on it.
              Expanded(
                child: MarketModeStore.instance.isStocks
                    ? _stocksBody()
                    : switch (_tab) {
                  NavTab.dashboard => DashboardScreen(
                      // Keyed by pair. Swiping to another coin disposes this
                      // screen's State and builds a fresh one, so no field
                      // can carry the previous coin's numbers across — the
                      // flicker was per-coin state I had cleared by hand,
                      // twice, and missed something both times. This makes
                      // the whole class of bug impossible instead.
                      key: ValueKey(_symbol),
                      client: widget.client,
                      live: _live,
                      symbol: _symbol,
                      onSwipe: _swipe,
                      neighbours:
                          _order.where((s) => s != _symbol).toList(),
                      entitled: _entitled,
                      onSubscribe: () =>
                          setState(() => _tab = NavTab.profile),
                      interval: _interval,
                      onPickInterval: (iv) {
                        Settings.instance.saveInterval(iv);
                        setState(() => _interval = iv);
                      },
                      // Selecting an untrained timeframe still selects it —
                      // the dashboard draws the "no model" panel with the
                      // train command in place. It used to jump to a separate
                      // tab, which meant losing your place to read one line.
                      onNeedsTraining: (iv) {
                        Settings.instance.saveInterval(iv);
                        setState(() => _interval = iv);
                      },
                    ),
                  NavTab.screener => ScreenerScreen(
                      client: widget.client,
                      market: 'crypto',
                      // Tapping a coin points the dashboard at it, exactly as
                      // tapping one in Market does. It used to open the STOCK
                      // page for it, which is where every "training always
                      // errors" report came from: that page trains through
                      // the equity fitter, and Alpaca does not carry
                      // AVGOUSDT.
                      onPick: (s) {
                        _setSymbol(s);
                        setState(() => _tab = NavTab.dashboard);
                      },
                    ),
                  NavTab.news => NewsScreen(
                      client: widget.client, symbol: _symbol),
                  NavTab.market =>
                    MarketScreen(
                      client: widget.client,
                      onPick: _pick,
                      onOrderChanged: _loadOrder,
                      // Symbol AND timeframe. A call belongs to both, so
                      // opening one on the timeframe you happened to be on
                      // last would show a different answer.
                      onOpenSignal: (sig) =>
                          _openSymbol(sig.symbol, sig.interval),
                    ),
                  // "the last page to buy the subscription" — for an
                  // unsubscribed account this tab IS the paywall, and it is
                  // the only place a sign-out lives for them.
                  NavTab.profile => _entitled
                      ? ProfileScreen(
                          client: widget.client,
                          account: widget.account,
                          onSignOut: _signOut,
                          onOpenSymbol: _openSymbol,
                        )
                      : SubscribeScreen(
                          client: widget.client,
                          account: widget.account,
                          onSignOut: _signOut,
                          onCheckoutStarted: () => _awaitingCheckout = true,
                        ),
                },
              ),
            ],
          ),
        ),
      ),
      extendBody: true,
      bottomNavigationBar: FrostedNav(
        current: _tab,
        locked: _entitled
            ? const {}
            : const {NavTab.market, NavTab.screener, NavTab.news},
        // A locked tab still responds — it takes you to the page that
        // explains why it is locked. A padlock that does nothing when pressed
        // reads as a broken app rather than a paywall.
        onSelect: (t) {
          if (!_entitled &&
              (t == NavTab.market || t == NavTab.news ||
                  t == NavTab.screener)) {
            setState(() => _tab = NavTab.profile);
            return;
          }
          // The screener is stocks-only, so selecting it switches the market
          // rather than showing an empty page. Doing it silently would be
          // worse than not switching at all, so the top bar's toggle moves
          // visibly with it.
          setState(() => _tab = t);
        },
      ),
    ));
  }

  bool get _entitled => widget.account.entitled;

  /// One socket carrying every coin you hold an open entry in, whatever
  /// screen is in front. The per-screen checks in profile, market and
  /// dashboard only see the coins those screens show; this one sees the
  /// positions, so a level hit closes the entry while you are on any tab
  /// at all. Zero cost when nothing is held: `watch` on an empty set opens
  /// no socket.
  final MarketTicker _positions = MarketTicker();
  StreamSubscription<Map<String, double>>? _positionsSub;
  StreamSubscription<OpenRequest>? _tapSub;

  /// Set when the customer is handed to Stripe, cleared once we stop
  /// looking. Without it every resume on a free account would poll for an
  /// upgrade that is not coming, on a server that is already busy.
  bool _awaitingCheckout = false;

  /// Re-ask the server who we are. Returns whether that account is entitled.
  ///
  /// The answer is returned rather than read back off `widget.account`,
  /// which does not update until the parent rebuilds -- a frame later than
  /// the caller needs it.
  Future<bool> _refreshAccount() async {
    try {
      final me = await widget.client.me();
      await Settings.instance.saveAccount(me);
      widget.onAccountChanged(me);
      return me.entitled;
    } catch (_) {
      // leave the account as-is; the paywall stays up, which is the safe
      // direction to fail in
      return false;
    }
  }

  /// Which stock the stocks dashboard is showing.
  ///
  /// Separate from `_symbol`, which is a crypto pair. One field for both
  /// would mean the crypto dashboard trying to render AAPL the moment you
  /// switched markets.
  String? _stock;

  /// The first stock on the watchlist, so the dashboard opens on something
  /// rather than telling you to pick a stock you have already picked.
  ///
  /// Loaded once and kept: reading storage inside `build` would hit the disk
  /// on every frame.
  String? _firstFollowed;

  /// The whole followed list, in order, so a swipe knows where to go next.
  List<String> _followed = const [];

  /// Move to the next or previous followed stock, wrapping at both ends.
  ///
  /// Wrapping rather than stopping: with three stocks, a hard stop at the
  /// last one reads as the gesture having failed.
  void _swipeStock(String current, int direction) {
    if (_followed.length < 2) return;
    final i = _followed.indexOf(current);
    if (i < 0) return;
    final next = (i + direction) % _followed.length;
    setState(() => _stock = _followed[next < 0 ? next + _followed.length : next]);
  }

  Future<void> _loadFollowed() async {
    final list = await StockWatchlist.instance.load();
    if (!mounted) return;
    setState(() {
      _followed = list;
      _firstFollowed = list.isEmpty ? null : list.first;
      // A stock removed from the watchlist must not stay on the dashboard.
      if (_stock != null && !list.contains(_stock)) _stock = null;
    });
  }

  /// The stocks half of the app.
  ///
  /// Dashboard and Market are real pages now. News stays crypto-only: the
  /// feeds this app reads are crypto publishers, and relabelling them as
  /// equities news would be a lie about the source.
  Widget _stocksBody() {
    switch (_tab) {
      case NavTab.screener:
        return ScreenerScreen(client: widget.client, market: 'stocks');
      case NavTab.profile:
        return _entitled
            ? ProfileScreen(
                client: widget.client,
                account: widget.account,
                onSignOut: _signOut,
                onOpenSymbol: _openSymbol,
                onAccountChanged: widget.onAccountChanged,
              )
            : SubscribeScreen(
                client: widget.client,
                account: widget.account,
                onSignOut: _signOut,
                onCheckoutStarted: () => _awaitingCheckout = true,
              );
      case NavTab.market:
        return StockMarketScreen(
          client: widget.client,
          onPick: (s) {
            setState(() {
              _stock = s;
              _tab = NavTab.dashboard;
            });
            _loadFollowed();
          },
          onOpenSignal: (sig) {
            _openSymbol(sig.symbol, sig.interval);
            _loadFollowed();
          },
        );
      case NavTab.dashboard:
        final s = _stock ?? _firstFollowed;
        if (s == null) return _pickAStock();
        return StockScreen(
          // Keyed by symbol, so a swipe DISPOSES this state and builds a
          // fresh one. Exactly the fix that killed the stale-data flicker on
          // the crypto dashboard: clearing fields by hand missed something
          // twice, and this makes the whole class of bug impossible.
          key: ValueKey(s),
          client: widget.client,
          symbol: s,
          neighbours: _followed.where((x) => x != s).toList(),
          onSwipe: (dir) => _swipeStock(s, dir),
          // NO BACK ARROW HERE. This is a tab, not a pushed page — there is
          // nothing behind it to go back to, and the arrow implied there was.
          // The screener's pushed copy still passes one, because there it
          // genuinely returns somewhere.
        );
      case NavTab.news:
        return NewsScreen(
          key: const ValueKey('stocks-news'),
          client: widget.client,
          // The stock on the dashboard, so "just this one" filters to it.
          symbol: _stock ?? _firstFollowed ?? '',
          market: 'stocks',
        );
    }
  }

  /// The stocks dashboard with nothing selected.
  Widget _pickAStock() => Center(
        child: Padding(
          padding: const EdgeInsets.all(Obsidian.containerPadding),
          child: GlassPanel(
            padding: const EdgeInsets.all(22),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                const Icon(Icons.candlestick_chart_rounded,
                    color: Obsidian.outline, size: 34),
                const SizedBox(height: 14),
                Text('Pick a stock', style: Obsidian.headlineMd()),
                const SizedBox(height: 10),
                Text('Open one from the Screener, or add it on the Market '
                    'tab, and it appears here with its chart and figures.',
                    textAlign: TextAlign.center,
                    style: Obsidian.body(color: Obsidian.outline, size: 12)),
                const SizedBox(height: 18),
                FilledButton(
                  style: FilledButton.styleFrom(
                      backgroundColor: Obsidian.primary,
                      foregroundColor: Obsidian.onPrimary),
                  onPressed: () => setState(() => _tab = NavTab.screener),
                  child: const Text('Open the Screener'),
                ),
              ],
            ),
          ),
        ),
      );

  void _onTradesChanged() {
    unawaited(PushDelivery.instance.sync(widget.client));
    unawaited(_watchPositions());
  }

  Future<void> _watchPositions() async {
    final held = (await Trades.instance.load())
        .where((t) => t.isOpen &&
            (t.takeProfit != null || t.stopLoss != null) &&
            t.symbol.endsWith('USDT'))
        .map((t) => t.symbol);
    _positions.watch(held);
  }

  void _onModeChanged() {
    if (!mounted) return;
    // The screener's Follow button writes the same list, so coming back to
    // stocks has to re-read it rather than trusting what was loaded at start.
    _loadFollowed();
    // Leaving a stocks-only tab when switching back to crypto, rather than
    // sitting on a page that has nothing to show.
    setState(() {
      if (!MarketModeStore.instance.isStocks &&
          FrostedNav.stocksOnly.contains(_tab)) {
        _tab = NavTab.dashboard;
      }
    });
  }

  /// Show the acknowledgement to an account that has not read this version.
  ///
  /// PER ACCOUNT AND PER VERSION. A new sign-up sees it before touching a
  /// single number, which is the entire point — somebody arriving at a screen
  /// full of BUY and SELL will assume the calls are good unless told what the
  /// measurements actually say.
  Future<void> _maybeAcknowledge() async {
    final id = widget.account.identifier;
    if (id.isEmpty) return;
    final seen = await Settings.instance.acknowledgedVersion(id);
    if (seen >= acknowledgementVersion || !mounted) return;
    final ok = await showAcknowledgement(context);
    if (ok) await Settings.instance.saveAcknowledged(id, acknowledgementVersion);
  }

  Future<void> _signOut() async {
    // BEFORE the token is thrown away — unsubscribing needs to authenticate,
    // and a relay left registered would keep pushing this account's alerts to
    // a phone that has signed out of it.
    await PushDelivery.instance.disable(widget.client);
    await widget.client.logout();
    // the cached account goes with the token, or the next cold start would
    // open straight into a session that no longer exists
    await Settings.instance.clearSession();
    // and stop the background job with it: a wake-up that can only ever get
    // a 401 is a battery cost with no possible payoff
    await stopBackgroundAlerts();
    widget.onSignOut();
  }

  /// The bell opens a sheet rather than toggling in place.
  ///
  /// There are two levels worth controlling — the whole coin, and one
  /// timeframe of it — and a single tap cannot express both. The obvious
  /// alternative was tap-for-one and long-press-for-the-other, which is the
  /// same undiscoverable gesture that made swipe-to-delete invisible on the
  /// market screen. One tap, everything visible.
  Future<void> _openBell() async {
    // Always opens. It used to stop at a snackbar when the OS permission
    // looked missing -- and on phones where the permission check was
    // misread, that made the bell a button that did nothing. The sheet
    // itself now says at the top if the OS has notifications off, with a
    // way to fix it, and the per-coin settings below it still work.
    await Notifications.instance.refreshGranted();
    if (!mounted) return;
    await showModalBottomSheet<void>(
      context: context,
      backgroundColor: Colors.transparent,
      // The sheet is taller than half the screen now — six timeframes, five
      // categories and the four news levels. Without this it is capped at the
      // default height and the bottom rows are simply not reachable.
      isScrollControlled: true,
      builder: (_) => AlertSettingsSheet(symbol: _symbol),
    );
    if (!mounted) return;
    setState(() {});                       // the bell reflects the new state
    // and the relay learns the new per-coin settings now, not at the next
    // poll -- a coin muted here must be silent on the lock screen too
    unawaited(PushDelivery.instance.sync(widget.client));
  }

  Widget _topBar() {
    final granted = Notifications.instance.granted;
    // the bell speaks for what you are looking at: this coin, this timeframe
    final muted = Muted.instance.isMuted(_symbol, _interval);
    // three states, and they are genuinely different things: the OS has
    // refused us, this pair is silenced, or alerts are live
    final lit = granted && !muted;
    return Padding(
        padding: const EdgeInsets.fromLTRB(Obsidian.containerPadding, 4,
            Obsidian.containerPadding, 8),
        child: Row(
          children: [
            Semantics(
              button: true,
              label: lit
                  ? 'Alerts on for $_symbol $_interval. Tap to change.'
                  : 'Alerts muted for $_symbol $_interval. Tap to change.',
              child: InkWell(
                onTap: _openBell,
                customBorder: const CircleBorder(),
                child: Padding(
                  padding: const EdgeInsets.all(6),
                  child: Icon(
                    lit
                        ? Icons.notifications_active_rounded
                        : Icons.notifications_off_rounded,
                    size: 26,
                    color: lit ? Obsidian.green : Obsidian.outline,
                  ),
                ),
              ),
            ),
            const SizedBox(width: 4),
            if (!lit && granted)
              Text('$_symbol · $_interval',
                  style: Obsidian.labelSm(
                      size: 10.5, color: Obsidian.outline)),
            const Spacer(),
            // The dashboard names its own pair in its header, so repeating
            // it here would be noise on the screen that needs it least.
            if (_tab == NavTab.market)
              Text('$_symbol · $_interval',
                  style: Obsidian.labelSm(size: 11)),
            const SizedBox(width: 8),
            // Back on the screener now that crypto has one of its own. It was
            // hidden here while the tab was stocks-only, because a switch
            // that takes you somewhere empty is worse than no switch.
            _marketSwitch(),
          ],
        ));
  }

  /// CRYPTO | STOCKS, top right.
  ///
  /// A segmented control rather than a toggle, because a toggle only shows
  /// the state you are NOT in — and with two markets that read as a button
  /// labelled with the wrong one. Both are visible, one is lit.
  Widget _marketSwitch() {
    final stocks = MarketModeStore.instance.isStocks;

    Widget half(String label, bool on, VoidCallback tap) => InkWell(
          onTap: on ? null : tap,
          borderRadius: BorderRadius.circular(Obsidian.rMd),
          child: Container(
            padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 5),
            decoration: BoxDecoration(
              color: on
                  ? Obsidian.primaryContainer.withValues(alpha: 0.24)
                  : Colors.transparent,
              borderRadius: BorderRadius.circular(Obsidian.rMd),
            ),
            child: Text(label,
                style: Obsidian.labelSm(
                    size: 9.5,
                    color: on ? Obsidian.primary : Obsidian.outline)),
          ),
        );

    return Container(
      decoration: BoxDecoration(
        color: Obsidian.surfaceLowest.withValues(alpha: 0.7),
        borderRadius: BorderRadius.circular(Obsidian.rMd),
        border: Border.all(color: Colors.white.withValues(alpha: 0.09)),
      ),
      padding: const EdgeInsets.all(2),
      child: Row(mainAxisSize: MainAxisSize.min, children: [
        half('CRYPTO', !stocks,
            () => MarketModeStore.instance.set(MarketMode.crypto)),
        half('STOCKS', stocks,
            () => MarketModeStore.instance.set(MarketMode.stocks)),
      ]),
    );
  }
}
