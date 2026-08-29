/// The tab host: mesh floor, a thin top bar, and the floating frosted nav.
///
/// It also owns the two things that must keep running whichever tab is on
/// screen — the live price socket and the alert loop. Putting them in the
/// dashboard would mean price went stale and notifications stopped the moment
/// you opened Market.
library;

import 'dart:async';

import 'package:flutter/material.dart';

import '../api/client.dart';
import '../api/live_price.dart';
import '../api/models.dart';
import '../api/settings.dart';
import '../api/muted.dart';
import '../api/notifications.dart';
import '../theme/liquid_obsidian.dart';
import '../widgets/frosted_nav.dart';
import '../widgets/mesh_background.dart';
import 'dashboard_screen.dart';
import 'market_screen.dart';
import 'profile_screen.dart';
import 'subscribe_screen.dart';
import 'training_screen.dart';

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
        if (a.symbol.isNotEmpty && Muted.instance.isMuted(a.symbol)) continue;

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

  void _pick(Coin c) {
    setState(() {
      _symbol = c.symbol;
      // an untrained pair has no probability to show, so send it where the
      // truthful answer lives instead of to an empty dashboard
      _tab = c.trained ? NavTab.dashboard : NavTab.training;
    });
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
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
                      client: widget.client,
                      live: _live,
                      entitled: _entitled,
                      onSubscribe: () =>
                          setState(() => _tab = NavTab.profile),
                      interval: _interval,
                      onPickInterval: (iv) => setState(() => _interval = iv),
                      // an untrained timeframe has no probability to show, so
                      // it goes where the truthful answer lives
                      onNeedsTraining: (iv) => setState(() {
                        _interval = iv;
                        _tab = NavTab.training;
                      }),
                    ),
                  NavTab.market =>
                    MarketScreen(client: widget.client, onPick: _pick),
                  NavTab.training => TrainingScreen(
                      client: widget.client,
                      symbol: _symbol,
                      interval: _interval,
                      onPickInterval: (iv) => setState(() => _interval = iv),
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
            : const {NavTab.market, NavTab.training},
        // A locked tab still responds — it takes you to the page that
        // explains why it is locked. A padlock that does nothing when pressed
        // reads as a broken app rather than a paywall.
        onSelect: (t) => setState(() => _tab =
            (!_entitled && (t == NavTab.market || t == NavTab.training))
                ? NavTab.profile
                : t),
      ),
    );
  }

  /// Tapping the bell silences the pair currently selected.
  ///
  /// Not notifications as a whole — that switch lives in the OS, and an app
  /// that keeps its own global mute alongside the system one gives you two
  /// places to look when your phone goes quiet.
  Future<void> _toggleBell() async {
    if (!Notifications.instance.granted) {
      // muting something that is already silent at the OS level would read as
      // a broken toggle. Send them where the actual switch is.
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(
        backgroundColor: Obsidian.surfaceHigh,
        content: Text('Notifications are off for this app in system settings.',
            style: Obsidian.body()),
      ));
      return;
    }
    final nowMuted = await Muted.instance.toggle(_symbol);
    if (!mounted) return;
    setState(() {});
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(
      backgroundColor: Obsidian.surfaceHigh,
      duration: const Duration(seconds: 2),
      content: Text(
        nowMuted
            ? '$_symbol muted — no alerts from this pair'
            : '$_symbol unmuted — alerts on again',
        style: Obsidian.body(),
      ),
    ));
  }

  bool get _entitled => widget.account.entitled;

  /// Re-ask the server who we are. Called after a checkout returns.
  Future<void> _refreshAccount() async {
    try {
      widget.onAccountChanged(await widget.client.me());
    } catch (_) {
      // leave the account as-is; the paywall stays up, which is the safe
      // direction to fail in
    }
  }

  Future<void> _signOut() async {
    await widget.client.logout();
    await Settings.instance.saveToken('');
    widget.onSignOut();
  }

  Widget _topBar() {
    final granted = Notifications.instance.granted;
    final muted = Muted.instance.isMuted(_symbol);
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
                  ? 'Alerts on for $_symbol. Tap to mute.'
                  : 'Alerts muted for $_symbol. Tap to unmute.',
              child: InkWell(
                onTap: _toggleBell,
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
              Text(_symbol, style: Obsidian.labelSm(size: 10.5,
                  color: Obsidian.outline)),
            const Spacer(),
            // only on Training, because that is the one screen driven by the
            // pair you picked. The dashboard serves BTCUSDT whatever is
            // selected, so showing "ETHUSDT" above BTC data would be a
            // contradiction the user has no way to resolve — and the
            // dashboard names its own pair in the header anyway
            if (_tab == NavTab.training)
              Text('$_symbol · $_interval',
                  style: Obsidian.labelSm(size: 11)),
          ],
        ));
  }
}
