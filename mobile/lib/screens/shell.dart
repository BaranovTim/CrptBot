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

import '../api/client.dart';
import '../api/live_price.dart';
import '../api/models.dart';
import '../api/settings.dart';
import '../api/muted.dart';
import '../api/watchlist.dart';
import '../api/notifications.dart';
import '../theme/liquid_obsidian.dart';
import '../widgets/alert_settings_sheet.dart';
import '../widgets/frosted_nav.dart';
import '../widgets/mesh_background.dart';
import 'dashboard_screen.dart';
import 'market_screen.dart';
import 'news_screen.dart';
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
  String _interval = '1h';

  late final LivePriceService _live = LivePriceService(symbol: _symbol);
  Timer? _alertTimer;
  Timer? _calendarTimer;
  int? _cursor;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _live.start();
    _startAlerts();
    _loadOrder();
    // prime the mute set: `isMuted` is synchronous by necessity and
    // reports nothing muted until this lands
    Muted.instance.load().then((_) => mounted ? setState(() {}) : null);
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
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
    }
  }

  Future<void> _startAlerts() async {
    await Notifications.instance.init();
    // the bell in the top bar reflects whether permission was granted, and
    // that answer only exists after the OS prompt is resolved
    if (mounted) setState(() {});

    // Establish the cursor WITHOUT notifying. The first call deliberately
    // returns nothing, so opening the app does not replay a week of filings.
    try {
      final first = await widget.client.alerts();
      _cursor = first.cursor;
    } catch (_) {
      // no server yet; the timer will keep trying
    }

    _alertTimer =
        Timer.periodic(const Duration(seconds: 20), (_) => _pollAlerts());
    _refreshCalendar();
    _calendarTimer =
        Timer.periodic(const Duration(hours: 6), (_) => _refreshCalendar());
  }

  Future<void> _pollAlerts() async {
    try {
      final r = await widget.client.alerts(cursor: _cursor);
      debugPrint('[alerts] cursor=$_cursor -> ${r.alerts.length} new, '
          'next=${r.cursor}');
      _cursor = r.cursor;
      for (final a in r.alerts) {
        // Silenced pairs are dropped here rather than at the server, because
        // muting is a per-device preference: the same account on a tablet may
        // want the alerts this phone does not. An alert with no symbol at all
        // (a scheduled macro release, say) belongs to no coin and is never
        // silenced by a coin's bell.
        if (a.symbol.isNotEmpty &&
            Muted.instance.isMuted(a.symbol, a.interval)) {
          continue;
        }

        // Two channels, because iOS suppresses this app's notifications while
        // it is in the FOREGROUND — measured, not assumed: presentAlert,
        // presentBanner and presentList all set, delivered via both show()
        // and zonedSchedule(), and nothing appeared until the app was
        // backgrounded. That is ordinary iOS behaviour, and the ordinary
        // answer is to draw your own banner when you are on screen.
        //
        // So: an in-app banner when the user can see the app, and an OS
        // notification for when they cannot.
        await Notifications.instance.showAlert(a);
        _banner(a);
      }
    } catch (e) {
      debugPrint('[alerts] poll failed: $e');
      // a missed poll is not worth surfacing; the next one covers it
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
  void _banner(Alert a) {
    if (!mounted) return;
    final tone = switch (a.kind) {
      'signal' => Obsidian.green,
      'spike' => Obsidian.red,
      'calendar' => Obsidian.primary,
      _ => Obsidian.onSurfaceVariant,
    };
    ScaffoldMessenger.of(context)
      ..removeCurrentSnackBar()
      ..showSnackBar(SnackBar(
        backgroundColor: Obsidian.surfaceHigh,
        duration: const Duration(seconds: 8),
        behavior: SnackBarBehavior.floating,
        margin: const EdgeInsets.fromLTRB(16, 0, 16, Obsidian.navClearance + 8),
        shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(Obsidian.rLg),
            side: BorderSide(color: tone.withValues(alpha: 0.45))),
        content: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          mainAxisSize: MainAxisSize.min,
          children: [
            Text(a.title,
                style: Obsidian.bodyLg(color: tone)
                    .copyWith(fontWeight: FontWeight.w700)),
            const SizedBox(height: 4),
            Text(a.body, style: Obsidian.body(size: 12.5)),
            const SizedBox(height: 4),
            Text(a.whenLine(),
                style: Obsidian.labelSm(color: Obsidian.outline, size: 10)),
          ],
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
        title: Text('Close ThusIldy?', style: Obsidian.headlineMd()),
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
                child: switch (_tab) {
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
                      onPickInterval: (iv) => setState(() => _interval = iv),
                      // Selecting an untrained timeframe still selects it —
                      // the dashboard draws the "no model" panel with the
                      // train command in place. It used to jump to a separate
                      // tab, which meant losing your place to read one line.
                      onNeedsTraining: (iv) =>
                          setState(() => _interval = iv),
                    ),
                  NavTab.news => NewsScreen(
                      client: widget.client, symbol: _symbol),
                  NavTab.market =>
                    MarketScreen(
                      client: widget.client,
                      onPick: _pick,
                      onOrderChanged: _loadOrder,
                    ),
                  // "the last page to buy the subscription" — for an
                  // unsubscribed account this tab IS the paywall, and it is
                  // the only place a sign-out lives for them.
                  NavTab.profile => _entitled
                      ? ProfileScreen(
                          client: widget.client,
                          account: widget.account,
                          onSignOut: _signOut,
                        )
                      : SubscribeScreen(
                          client: widget.client,
                          account: widget.account,
                          onSignOut: _signOut,
                          onRefreshAccount: _refreshAccount,
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
            : const {NavTab.market, NavTab.news},
        // A locked tab still responds — it takes you to the page that
        // explains why it is locked. A padlock that does nothing when pressed
        // reads as a broken app rather than a paywall.
        onSelect: (t) => setState(() =>
            _tab = (!_entitled &&
                    (t == NavTab.market || t == NavTab.news))
                ? NavTab.profile
                : t),
      ),
    ));
  }

  bool get _entitled => widget.account.entitled;

  /// Re-ask the server who we are. Called after a checkout returns.
  Future<void> _refreshAccount() async {
    try {
      final me = await widget.client.me();
      await Settings.instance.saveAccount(me);
      widget.onAccountChanged(me);
    } catch (_) {
      // leave the account as-is; the paywall stays up, which is the safe
      // direction to fail in
    }
  }

  Future<void> _signOut() async {
    await widget.client.logout();
    // the cached account goes with the token, or the next cold start would
    // open straight into a session that no longer exists
    await Settings.instance.clearSession();
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
    if (!Notifications.instance.granted) {
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(
        backgroundColor: Obsidian.surfaceHigh,
        content: Text('Notifications are off for this app in system settings.',
            style: Obsidian.body()),
      ));
      return;
    }
    await showModalBottomSheet<void>(
      context: context,
      backgroundColor: Colors.transparent,
      builder: (_) => AlertSettingsSheet(symbol: _symbol),
    );
    if (mounted) setState(() {});          // the bell reflects the new state
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
          ],
        ));
  }
}
