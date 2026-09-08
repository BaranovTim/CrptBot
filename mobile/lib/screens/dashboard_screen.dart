/// The bot dashboard.
///
/// Laid out to match `bot_dashboard_updated/screen.png`, with three
/// substitutions where the mockup's sample state and the real backend
/// disagree — and in each case the backend wins:
///
///   RECOMMENDED ACTION   the mockup shows a confident BUY. The card renders
///                        whatever `evaluate()` actually decided, which today
///                        is usually FLAT, because EV after costs does not
///                        clear the threshold. A dashboard that always says
///                        BUY is a screenshot, not an instrument.
///
///   BOT ACTIVE           becomes WATCHING. Nothing here places an order, and
///                        a badge implying autonomy would be the single most
///                        misleading pixel in the app.
///
///   Data by TradingView  becomes Binance, which is where the bars are
///                        actually from.
library;

import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import 'package:url_launcher/url_launcher.dart';

import '../api/client.dart';
import '../api/live_price.dart';
import '../api/models.dart';
import '../api/trades.dart';
import '../api/settings.dart';
import '../theme/liquid_obsidian.dart';
import '../widgets/article_sheet.dart';
import '../widgets/analysis_panels.dart';
import '../widgets/positions_panel.dart';
import '../widgets/trade_entry.dart';
import '../widgets/glass.dart';
import '../widgets/horizon_sheet.dart';
import '../widgets/indicator_sheet.dart';
import '../widgets/patient_loader.dart';
import '../widgets/sparkline.dart';
import '../widgets/timeframe_bar.dart';
import '../widgets/train_button.dart';
import '../widgets/status_dot.dart';

class DashboardScreen extends StatefulWidget {
  /// Exposed for testing: the countdown format is the thing that was wrong,
  /// so it is the thing worth asserting on directly rather than through a
  /// rendered widget.
  static String countdown(Duration away) => _DashboardScreenState.countdown(away);

  const DashboardScreen({
    super.key,
    required this.client,
    required this.live,
    required this.symbol,
    required this.onSwipe,
    required this.neighbours,
    required this.entitled,
    required this.onSubscribe,
    required this.interval,
    required this.onPickInterval,
    required this.onNeedsTraining,
  });

  final ApiClient client;

  /// The pair being shown.
  ///
  /// This did not exist until 2026-08-30, and its absence was the bug: the
  /// screen called `client.dashboard(interval:)` with no symbol, so the
  /// server answered with ITS default — BTCUSDT — no matter what you picked
  /// in Market. Selecting ETH set the shell's `_symbol`, the top bar agreed,
  /// and the dashboard quietly served BTC.
  final String symbol;

  /// Swipe left/right to the next or previous followed pair. -1 or +1.
  final ValueChanged<int> onSwipe;

  /// The other pairs being followed, so they can be fetched before you swipe.
  final List<String> neighbours;

  /// Whether this account may see the analysis, or only the graph.
  ///
  /// A free account is not shown a screen full of blanked-out panels: the
  /// endpoints behind them return 402 and there is nothing to blank. It gets
  /// the chart, which is genuinely free, and one panel saying what the rest
  /// is.
  final bool entitled;
  final VoidCallback onSubscribe;

  /// The selected timeframe. Each one is a separately fitted model, so this
  /// changes which model answers, not just which candles are drawn.
  final String interval;
  final ValueChanged<String> onPickInterval;

  /// Called when the chosen timeframe has no model. It no longer navigates
  /// anywhere — the panel explaining it is drawn in place — but the shell
  /// still wants to know, so the timeframe bar can mark the gap.
  final ValueChanged<String> onNeedsTraining;

  /// Price arrives here from Binance directly, not through the server. The
  /// server's copy is up to 15s stale by the time it reaches the phone, which
  /// is the whole reason this exists.
  final LivePriceService live;

  @override
  State<DashboardScreen> createState() => _DashboardScreenState();
}

class _DashboardScreenState extends State<DashboardScreen> {
  Dashboard? _data;
  List<double> _series = const [];
  List<WhaleEvent> _whales = const [];
  List<NewsItem> _news = const [];

  /// Positions you logged for THIS pair. Read from the device, not the
  /// server — see `trades.dart` on why, and on what that costs.
  List<TradeEntry> _positions = const [];
  Consensus? _consensus;
  String? _error;
  String? _untrained;
  Timer? _timer;

  /// When the last good answer arrived — the clock the spinner counts.
  ///
  /// Reset on every success and on an explicit retry, so it always measures
  /// "how long has this been failing", never "how long has the app been
  /// open". Nothing is called an error until it crosses [kPatience]; see
  /// `patient_loader.dart` for why five minutes and not twenty seconds.
  DateTime _waitingSince = DateTime.now();

  /// The most recent failure, held but not shown.
  ///
  /// Kept separate from [_error] on purpose: this is what went wrong, [_error]
  /// is the decision to tell you about it. A phone produces plenty of the
  /// first that never deserve the second.
  String? _lastFailure;

  /// A request is already out. Without this the 10s refresh timer stacks new
  /// attempts on top of one that is still waiting out its own timeout, so a
  /// dead network produces a growing pile of doomed sockets.
  bool _inFlight = false;
  StreamSubscription<LiveTick>? _tick;
  LiveTick? _live;

  @override
  void initState() {
    super.initState();
    // repaint on every trade. setState on a tick is cheap here because the
    // only things that move are the price, the change badge and the two
    // re-anchored barrier rows
    _tick = widget.live.stream.listen((t) {
      if (mounted) setState(() => _live = t);
    });
    _load();
    _loadPositions();
    // the user's chosen signal strength, restored before the first payload
    // lands so a call is never briefly shown then withdrawn
    Settings.instance.sensitivity().then(
        (v) => mounted ? setState(() => _sensitivity = v) : null);
    // the service caches per closing bar, so this costs a JSON round trip,
    // not a feature recompute
    _timer = Timer.periodic(const Duration(seconds: 10), (_) => _load(quiet: true));
  }

  /// Payloads already fetched this session, keyed by pair and timeframe.
  ///
  /// The point is the swipe: moving between four coins used to mean a full
  /// round trip and a spinner every time, even going back to one seen a
  /// second earlier. Bounded by the watchlist times six timeframes, so a few
  /// dozen small JSON objects at most.
  // OUTSIDE the State, deliberately.
  //
  // The screen is now keyed by symbol, so swiping to another coin DISPOSES
  // this State and builds a fresh one — which is what makes stale data
  // structurally impossible rather than a matter of remembering to clear
  // every field. But a cache that died with the State would make every
  // swipe a round trip again, so it lives here and survives.
  //
  // Keyed by pair AND timeframe, so nothing can be read back under the
  // wrong one.
  static final Map<String, Dashboard> _cache = {};
  static final Map<String, List<double>> _seriesCache = {};

  String get _key => '${widget.symbol}:${widget.interval}';

  /// How strong a call has to be before the app shows it as one.
  ///
  /// Defaults to 'strong' — the original behaviour — so a fresh install is
  /// conservative rather than chatty.
  String _sensitivity = 'strong';

  /// Fetch the neighbouring pairs at the current timeframe, quietly.
  ///
  /// Runs AFTER the visible pair has loaded and one at a time, so it never
  /// competes with the screen you are looking at. On a warmed server each of
  /// these is milliseconds; the first swipe is then instant instead of
  /// paying a round trip.
  Future<void> _prefetchNeighbours() async {
    if (!widget.entitled) return;
    for (final sym in widget.neighbours) {
      final k = '$sym:${widget.interval}';
      if (_cache.containsKey(k)) continue;
      try {
        final d = await widget.client
            .dashboard(symbol: sym, interval: widget.interval);
        final series =
            await widget.client.chart(symbol: sym, interval: widget.interval);
        if (!mounted) return;
        _cache[k] = d;
        _seriesCache[k] = series;
      } catch (_) {
        // a prefetch that fails costs nothing; the real load will report it
      }
    }
  }

  ScheduledEvent? _major;

  /// The next market-moving release, fetched once per screen load.
  ///
  /// Deliberately NOT inside the main `Future.wait`: it is context, and a
  /// calendar fetch failing must not stop the dashboard from rendering.
  Future<void> _loadMajor() async {
    try {
      final m = await widget.client.nextMajor();
      if (mounted) setState(() => _major = m);
    } catch (_) {
      // no banner is the correct failure here
    }
  }

  /// A countdown banner for the next big scheduled event.
  ///
  /// Shown from three days out. Non-Farm Payrolls is the largest scheduled
  /// volatility event of the month — the whole market repositions around it —
  /// and knowing it lands on Friday changes whether you open a position on
  /// Thursday. A notification an hour before is too late for that decision,
  /// which is why this sits on the screen you already look at.
  List<Widget> _majorEvent() {
    final e = _major;
    if (e == null) return const [];
    final away = e.away;
    if (away.isNegative || away.inDays > 3) return const [];

    final when = countdown(away);
    // inside two hours it stops being a diary note and starts being a warning
    final imminent = away.inHours < 2;
    final tone = imminent ? Obsidian.redSoft : Obsidian.primary;

    return [
      GlassPanel(
        glow: imminent ? Obsidian.red : null,
        glowOpacity: 0.25,
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Icon(Icons.event_rounded, size: 16, color: tone),
                const SizedBox(width: 8),
                Expanded(
                  child: Text('HIGH-IMPACT RELEASE',
                      style: Obsidian.labelSm(size: 10.5, color: tone)),
                ),
                Text(when,
                    style: Obsidian.dataTable(
                        size: 13, color: tone, w: FontWeight.w700)),
              ],
            ),
            const SizedBox(height: 10),
            Text(e.title,
                style:
                    Obsidian.bodyLg().copyWith(fontWeight: FontWeight.w600)),
            const SizedBox(height: 6),
            Text(
                '${_localTime(e.at)} · known to the whole market. '
                'Expect volatility, not direction.',
                style: Obsidian.body(color: Obsidian.outline, size: 11.5)),
            if (e.estimated) ...[
              const SizedBox(height: 8),
              Row(
                children: [
                  const Icon(Icons.info_outline_rounded,
                      size: 13, color: Obsidian.outline),
                  const SizedBox(width: 6),
                  Expanded(
                    child: Text(
                        'Date estimated from the publisher\'s rule — it can '
                        'shift for holidays.',
                        style: Obsidian.body(
                            color: Obsidian.outline, size: 10.5)),
                  ),
                ],
              ),
            ],
          ],
        ),
      ),
      const SizedBox(height: Obsidian.gutter),
    ];
  }

  /// A countdown that cannot disagree with the date beside it.
  ///
  /// THE BUG THIS FIXES: `Duration.inDays` TRUNCATES. An event 3 days and 20
  /// hours away rendered as "in 3 days" directly above a date four calendar
  /// days later, so the two halves of the same panel contradicted each other
  /// and the shorter number was the one people read.
  ///
  /// Days now always carry their hours, and nothing is rounded away.
  static String countdown(Duration away) {
    if (away.isNegative) return 'now';
    final d = away.inDays;
    final h = away.inHours % 24;
    final m = away.inMinutes % 60;
    if (d > 0) return 'in ${d}d ${h}h';
    if (away.inHours > 0) return 'in ${away.inHours}h ${m}m';
    return 'in $m min';
  }

  static String _localTime(DateTime utc) {
    final t = utc.toLocal();
    final d = '${t.day.toString().padLeft(2, '0')}/'
        '${t.month.toString().padLeft(2, '0')}';
    return '$d at ${t.hour.toString().padLeft(2, '0')}:'
        '${t.minute.toString().padLeft(2, '0')} your time';
  }

  Future<void> _loadConsensus() async {
    // captured, so a slow reply for the previous coin cannot land on this one
    final forSymbol = widget.symbol;
    try {
      final c = await widget.client.consensus(symbol: forSymbol);
      if (mounted && forSymbol == widget.symbol) {
        setState(() => _consensus = c);
      }
    } catch (_) {
      // supporting context; its absence is not worth an error state
    }
  }

  @override
  void didUpdateWidget(covariant DashboardScreen old) {
    super.didUpdateWidget(old);
    // a swipe changes the pair; a timeframe tap changes the interval. Both
    // invalidate everything on screen, and neither may leave the previous
    // coin's numbers visible under the new coin's name.
    //
    // But a pair already seen this session does not need a spinner. Showing
    // the cached payload for the pair being switched TO is not stale data
    // under the wrong name — it is that pair's own last answer, replaced the
    // moment the refresh lands.
    if (old.interval != widget.interval || old.symbol != widget.symbol) {
      final hit = _cache[_key];
      setState(() {
        _data = hit;
        _series = _seriesCache[_key] ?? const [];
        _untrained = null;

        // Belt and braces. The screen is keyed by symbol so a coin change
        // builds a whole new State and none of this can survive — but the
        // TIMEFRAME does not change the key, and the same reasoning applies
        // to it.
        //
        // EVERY piece of per-coin state, not just the payload.
        //
        // These two were missed and produced the flicker after a swipe: for a
        // moment the screen showed the PREVIOUS coin's numbers under the new
        // coin's name, then snapped across.
        //
        //   _live       the socket tick. `switchTo` is asynchronous, so
        //               between the swipe and the new socket's first frame
        //               this still held the old pair's price — and the header
        //               prefers the live price over the payload's.
        //   _consensus  fetched per symbol and never cleared, so the
        //               agreement rows belonged to the coin just left.
        //
        // A dash for a moment is honest. The wrong coin's price is not.
        if (old.symbol != widget.symbol) {
          _live = null;
          _consensus = null;
        }
      });
      _load(quiet: hit != null);
    }
  }

  @override
  void dispose() {
    _timer?.cancel();
    _tick?.cancel();
    super.dispose();
  }

  /// Hand off to the Binance app, falling back to the web trade page.
  Future<void> _openBinance(String symbol) async {
    final app = Uri.parse('bnc://app.binance.com/futures/$symbol');
    final web = Uri.parse('https://www.binance.com/en/futures/$symbol');
    try {
      if (await canLaunchUrl(app)) {
        await launchUrl(app);
        return;
      }
    } catch (_) {
      // canLaunchUrl throws if the scheme is not declared; fall through
    }
    await launchUrl(web, mode: LaunchMode.externalApplication);
  }

  /// The wait has gone past [kPatience] with nothing getting through.
  ///
  /// Called by the spinner's own clock, so the message arrives exactly when
  /// the five minutes are up rather than whenever the next attempt happens to
  /// fail. Until this runs, [_error] stays null and the screen keeps waiting.
  void _giveUp() {
    if (!mounted || _error != null) return;
    setState(() => _error = _lastFailure ?? 'No answer from the server.');
  }

  /// Back to waiting: a fresh clock, and no verdict.
  void _tryAgain() {
    setState(() {
      _error = null;
      _lastFailure = null;
      _waitingSince = DateTime.now();
    });
    _load();
  }

  /// The trades logged for this pair, newest first.
  ///
  /// Open ones only. A closed trade is history and belongs in the Profile
  /// list rather than stacked above the news on the pair you are reading
  /// about right now.
  Future<void> _loadPositions() async {
    // Settle against the levels first: a position whose stop was touched
    // while the app was closed should not still be sitting above the news
    // showing a live loss it is no longer taking.
    try {
      await Trades.instance.settle(widget.client);
    } catch (_) {
      // offline; the list below still draws what is stored
    }
    final list = await Trades.instance.forSymbol(widget.symbol);
    if (mounted) setState(() => _positions = list);
  }

  Future<void> _closePosition(TradeEntry t) async {
    final price = await askExitPrice(context, t, livePrice: _livePrice);
    if (price == null || price <= 0) return;
    await Trades.instance.close(t.id, price);
    await _loadPositions();
  }

  Future<void> _load({bool quiet = false}) async {
    final iv = widget.interval;

    // A background refresh gives way to a request already in flight. The
    // foreground one does not: pull-to-refresh has to feel like it did
    // something.
    if (quiet && _inFlight) return;
    _inFlight = true;
    try {
      await _fetch(iv);
    } finally {
      _inFlight = false;
    }
  }

  Future<void> _fetch(String iv) async {
    // Free tier: fetch ONLY the chart. Requesting the gated endpoints and
    // catching six 402s would work and would also make every screen refresh
    // hammer the server with calls whose answer is known in advance.
    if (!widget.entitled) {
      final freeSym = widget.symbol;
      final freeKey = '$freeSym:$iv';
      try {
        final series =
            await widget.client.chart(symbol: freeSym, interval: iv, n: 96);
        if (!mounted) return;
        _seriesCache[freeKey] = series;
        if (freeKey != _key) return;       // swiped away; do not paint it
        setState(() {
          _series = series;
          _error = null;
          _lastFailure = null;
          _waitingSince = DateTime.now();
          _untrained = null;
        });
        unawaited(_loadMajor());
      } catch (e) {
        // Recorded, not reported. The spinner's clock decides when this stops
        // being a slow moment and becomes a fault.
        _lastFailure = e.toString();
      }
      return;
    }

    // The pair this request is FOR, captured now.
    //
    // THE BUG THIS FIXES, which was seen on a phone: the header said BTC
    // while the graph was ETH. `_key` is derived from `widget.symbol`, so a
    // response that arrived after a swipe was written into the NEW pair's
    // cache slot — poisoning ETH's entry with BTC's dashboard. Landing on
    // ETH later then showed BTC's header beside ETH's freshly fetched chart.
    //
    // Everything below is keyed and guarded on what was asked for, never on
    // what happens to be on screen when the answer comes back.
    final sym = widget.symbol;
    final forKey = '$sym:$iv';

    try {
      final results = await Future.wait([
        widget.client.dashboard(symbol: sym, interval: iv),
        widget.client.chart(symbol: sym, interval: iv, n: 96),
        widget.client.whales(limit: 6),
        widget.client.news(limit: 8),
      ]);
      if (!mounted) return;

      final data = results[0] as Dashboard;
      final series = results[1] as List<double>;
      _cache[forKey] = data;               // always the pair it was asked for
      _seriesCache[forKey] = series;

      // stale answer for a pair we have since swiped away from: cached above
      // so it is not wasted, but it must not touch the screen
      if (forKey != _key) return;

      setState(() {
        _data = data;
        _series = series;
        _whales = results[2] as List<WhaleEvent>;
        _news = results[3] as List<NewsItem>;
        _error = null;
        _lastFailure = null;
        _waitingSince = DateTime.now();
        _untrained = null;
      });
      unawaited(_prefetchNeighbours());
      // Deliberately NOT awaited with the rest.
      //
      // Consensus builds a dashboard per trained timeframe, and each one is a
      // full feature computation the first time it is asked for. Putting it in
      // the same Future.wait held the whole screen on a spinner for twelve
      // seconds. It is supporting context, so it arrives when it arrives.
      unawaited(_loadConsensus());
      unawaited(_loadMajor());
    } on UntrainedException catch (e) {
      // a normal state, not a fault: this timeframe simply has no model yet
      if (!mounted || forKey != _key) return;
      setState(() {
        _untrained = e.message;
        _error = null;
        _lastFailure = null;
        _waitingSince = DateTime.now();   // the server answered; it is alive
      });
    } catch (e) {
      // an error belonging to a pair no longer on screen is not this pair's
      // error, and recording it would blame the wrong coin
      if (!mounted || forKey != _key) return;
      // Held, not shown — the wait is still young. `_giveUp` reads this if
      // and when the clock runs out.
      _lastFailure = e.toString();
    }
  }

  /// Horizontal drag -> previous/next pair.
  ///
  /// `onHorizontalDragEnd` rather than a PageView: the body is a ListView
  /// inside a RefreshIndicator, and nesting that in a horizontal PageView
  /// makes both gestures fight — the vertical scroll starts stealing
  /// horizontal drags and pull-to-refresh becomes unreliable. Flutter's
  /// arena already separates a horizontal drag from a vertical one, so
  /// listening for the one we want leaves scrolling and refresh untouched.
  ///
  /// The threshold is on VELOCITY, not distance: a slow diagonal drag while
  /// scrolling should not change coin, and a quick flick should.
  Widget _swipeable(Widget child) => GestureDetector(
        behavior: HitTestBehavior.opaque,
        onHorizontalDragEnd: (d) {
          final v = d.primaryVelocity ?? 0;
          if (v.abs() < 240) return;          // too slow to be deliberate
          widget.onSwipe(v < 0 ? 1 : -1);     // drag left = next
        },
        child: child,
      );

  @override
  Widget build(BuildContext context) {
    if (!widget.entitled) return _swipeable(_freeBody());
    if (_untrained != null) return _swipeable(_untrainedPanel());
    final d = _data;
    if (d == null) return _swipeable(_placeholder());

    return _swipeable(RefreshIndicator(
      onRefresh: _load,
      backgroundColor: Obsidian.surfaceContainer,
      color: Obsidian.primary,
      child: ListView(
        // addRepaintBoundaries: a BackdropFilter samples what is painted
        // BEHIND it, and ListView puts every child in its own RepaintBoundary
        // by default. Inside that layer the backdrop is empty, so the glass
        // panels blur nothing and paint nothing — the screen comes up blank
        // with no error anywhere. Opting out gives the filter a real backdrop.
        addRepaintBoundaries: false,
        physics: const AlwaysScrollableScrollPhysics(),
        padding: const EdgeInsets.fromLTRB(Obsidian.containerPadding, 8,
            Obsidian.containerPadding, Obsidian.navClearance + 24),
        children: [
          _header(d),
          const SizedBox(height: 14),
          ..._majorEvent(),
          TimeframeBar(
            timeframes: d.timeframes,
            selected: d.interval,
            onSelect: (tf) => tf.trained
                ? widget.onPickInterval(tf.interval)
                : widget.onNeedsTraining(tf.interval),
          ),
          const SizedBox(height: Obsidian.gutter),
          ..._costWarning(d),
          // ABOVE THE NEWS, deliberately. What you are holding outranks what
          // happened; a headline you have no position in is context, and a
          // position you do hold is the reason you opened the app.
          if (_positions.isNotEmpty) ...[
            PositionsPanel(
              entries: _positions,
              short: d.symbol.endsWith('USDT')
                  ? d.symbol.substring(0, d.symbol.length - 4)
                  : d.symbol,
              livePrice: _livePrice ?? d.price,
              onClose: _closePosition,
            ),
            const SizedBox(height: Obsidian.gutter),
          ],
          ..._recentActivity(),
          _chartCard(d),
          const SizedBox(height: Obsidian.panelGap),
          _indicatorGrid(d),
          const SizedBox(height: Obsidian.gutter),
          _recommendation(d),
          const SizedBox(height: Obsidian.gutter),
          _levels(d),
          const SizedBox(height: Obsidian.gutter),
          ..._consensusPanel(),
          _note(d),
          const SizedBox(height: Obsidian.gutter),
          // THE VERY BOTTOM, and that is the right place for it. Everything
          // above is the app telling you what it sees; this is you telling
          // the app what you did. Putting it higher would make the page read
          // as though it were offering to trade for you, which it is not and
          // cannot be — there is no key on this device.
          LogEntryCard(
            symbol: d.symbol,
            short: d.symbol.endsWith('USDT')
                ? d.symbol.substring(0, d.symbol.length - 4)
                : d.symbol,
            livePrice: _livePrice ?? d.price,
            // Seeded from the model's own call, so the numbers you are
            // looking at are the numbers you log. A default, not advice.
            suggestedSide: d.side.isEmpty
                ? (d.recommendation.action == 'SELL' ? 'SHORT' : 'LONG')
                : (d.isShort ? 'SHORT' : 'LONG'),
            suggestedTp: d.liveTakeProfit(_livePrice),
            suggestedSl: d.liveStopLoss(_livePrice),
            onLogged: _loadPositions,
          ),
        ],
      ),
    ));
  }

  /// What an unsubscribed account sees: the graph, and an honest account of
  /// what is behind the rest.
  Widget _freeBody() {
    final live = widget.live.last.price;
    return RefreshIndicator(
      onRefresh: _load,
      backgroundColor: Obsidian.surfaceContainer,
      color: Obsidian.primary,
      child: ListView(
        addRepaintBoundaries: false,
        physics: const AlwaysScrollableScrollPhysics(),
        padding: const EdgeInsets.fromLTRB(Obsidian.containerPadding, 8,
            Obsidian.containerPadding, Obsidian.navClearance + 24),
        children: [
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text('BTC / USDT', style: Obsidian.displayLg()),
                    const SizedBox(height: 4),
                    Text('Live price · ${widget.interval}',
                        style: Obsidian.body(color: Obsidian.outline)),
                  ],
                ),
              ),
              if (live != null)
                Text(_fmtPrice(live),
                    style: Obsidian.dataTable(size: 22, w: FontWeight.w700)),
            ],
          ),
          const SizedBox(height: 18),
          // shown on the free tier too: a scheduled public release is not
          // part of the product, it is a fact about the market
          ..._majorEvent(),
          GlassPanel(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                Text('PRICE', style: Obsidian.labelSm(size: 10.5)),
                const SizedBox(height: 14),
                if (_series.isEmpty)
                  SizedBox(
                      height: 150,
                      child: WaitingPanel(
                          since: _waitingSince,
                          what: 'Loading the chart',
                          onPatienceExhausted: _giveUp))
                else
                  Sparkline(values: _series, color: Obsidian.primary),
              ],
            ),
          ),
          if (_error != null) ...[
            const SizedBox(height: Obsidian.gutter),
            Text(_error!,
                style: Obsidian.body(color: Obsidian.redSoft, size: 12.5)),
          ],
          const SizedBox(height: Obsidian.gutter),
          GlassPanel(
            onTap: widget.onSubscribe,
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    const Icon(Icons.lock_outline_rounded,
                        size: 17, color: Obsidian.outline),
                    const SizedBox(width: 9),
                    Text('THE REST NEEDS A SUBSCRIPTION',
                        style: Obsidian.labelSm(size: 10.5)),
                  ],
                ),
                const SizedBox(height: 12),
                Text(
                    'Model probability, expected value, entry and exit levels, '
                    'indicators, whale filings and alerts — across six '
                    'independently fitted timeframes.',
                    style: Obsidian.body(color: Obsidian.outline, size: 12.5)),
                const SizedBox(height: 14),
                Row(
                  children: [
                    Text('See what is included',
                        style: Obsidian.body(
                            color: Obsidian.primary, size: 12.5)),
                    const SizedBox(width: 6),
                    const Icon(Icons.arrow_forward_rounded,
                        size: 15, color: Obsidian.primary),
                  ],
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }

  static String _fmtPrice(double v) {
    final s = v.toStringAsFixed(v.abs() >= 100 ? 2 : 4);
    final parts = s.split('.');
    final whole = parts[0].replaceAllMapped(
        RegExp(r'(\d)(?=(\d{3})+$)'), (m) => '${m[1]},');
    return '\$$whole.${parts[1]}';
  }

  // ---------------------------------------------------------------- header
  Widget _header(Dashboard d) {
    final s = d.status;
    final c = s.active ? Obsidian.green : Obsidian.outline;
    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(d.pair.replaceAll(' / ', '/'), style: Obsidian.displayLg()),
              const SizedBox(height: 4),
              Text('${d.interval} windows · ${d.htf} context · ${s.detail}',
                  style: Obsidian.body(size: 13)),
            ],
          ),
        ),
        Padding(
          padding: const EdgeInsets.only(top: 6),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              StatusDot(live: s.active),
              const SizedBox(width: 8),
              Text(s.label, style: Obsidian.labelSm(color: c, size: 12)),
            ],
          ),
        ),
      ],
    );
  }

  /// Shown when fees eat too much of the barrier span to trade this
  /// timeframe, whatever the probability says.
  ///
  /// Placed ABOVE the odds on purpose. A confident percentage sitting over a
  /// trade that cannot clear its own costs is the most expensive thing this
  /// screen could show.
  List<Widget> _costWarning(Dashboard d) {
    final c = d.timeframes
        .where((t) => t.interval == d.interval)
        .map((t) => t.cost)
        .firstOrNull;
    if (c == null || (!c.untradeable && !c.marginal)) return const [];
    final colour = c.untradeable ? Obsidian.red : Obsidian.tone('warn');
    return [
      GlassPanel(
        padding: const EdgeInsets.all(16),
        glow: c.untradeable ? colour : null,
        glowOpacity: 0.2,
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Icon(
                c.untradeable
                    ? Icons.block_rounded
                    : Icons.warning_amber_rounded,
                color: colour,
                size: 22),
            const SizedBox(width: 12),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                      c.untradeable
                          ? 'Costs exceed the whole range at ${d.interval}'
                          : 'Costs take a large bite at ${d.interval}',
                      style: Obsidian.bodyLg(color: colour)
                          .copyWith(fontWeight: FontWeight.w600)),
                  const SizedBox(height: 6),
                  Text(c.note, style: Obsidian.body(size: 12.5)),
                ],
              ),
            ),
          ],
        ),
      ),
      const SizedBox(height: Obsidian.gutter),
    ];
  }

  // ----------------------------------------------------------- whale alert
  /// Recent market activity — a filing or a headline — or nothing at all.
  ///
  /// WHY THIS CAN NOW BE ABSENT
  ///     It used to pin the newest non-mechanical filing to the top of the
  ///     dashboard permanently, so the screen opened with a two-day-old
  ///     insider sale above the price every single time. A panel that is
  ///     always there stops being read.
  ///
  ///     Now it appears only while the item is still recent, and how recent
  ///     depends on the item: a high-conviction filing stays up for three
  ///     days, a small routine one for twelve hours, a news headline for
  ///     eight. Nothing recent means no panel and the chart moves up.
  List<Widget> _recentActivity() {
    // mechanical transactions are filtered out by the same rule the terminal
    // uses: code F is tax withholding on vesting, nobody decided anything
    final whale = _whales
        .where((w) => !w.mechanical && w.isRecent)
        .cast<WhaleEvent?>()
        .firstWhere((_) => true, orElse: () => null);
    final news = _news
        .where((n) => n.isRecent)
        .cast<NewsItem?>()
        .firstWhere((_) => true, orElse: () => null);

    // whichever actually happened most recently leads
    final whaleAt = whale?.publishedAt;
    final newsAt = news?.publishedAt;
    final showNews = news != null &&
        (whale == null || (newsAt != null && whaleAt != null &&
            newsAt.isAfter(whaleAt)));

    if (showNews) return _newsCard(news);
    if (whale == null) return const [];
    return _whaleCard(whale);
  }

  static Color _biasTone(String bias) => switch (bias) {
        'BULL' => Obsidian.green,
        'BEAR' => Obsidian.red,
        'MIXED' => Obsidian.primary,
        _ => Obsidian.outline,
      };

  Widget _newsChip(String text, Color c) => Container(
        padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 3),
        decoration: BoxDecoration(
          color: c.withValues(alpha: 0.14),
          borderRadius: BorderRadius.circular(5),
        ),
        child: Text(text,
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
            style: Obsidian.labelSm(size: 8.5, color: c)),
      );

  List<Widget> _newsCard(NewsItem n) {
    return [
      GlassPanel(
        padding: const EdgeInsets.all(18),
        // The summary opens IN the app; the link to the publisher sits at
        // the bottom of it. Jumping straight to a browser meant leaving the
        // app to find out whether the story was worth leaving the app for.
        onTap: () => showModalBottomSheet<void>(
          context: context,
          backgroundColor: Colors.transparent,
          isScrollControlled: true,
          builder: (_) => ArticleSheet(item: n),
        ),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Container(
              width: 44,
              height: 44,
              decoration: BoxDecoration(
                color: Obsidian.primary.withValues(alpha: 0.14),
                shape: BoxShape.circle,
                border: Border.all(
                    color: Obsidian.primary.withValues(alpha: 0.40)),
              ),
              child: const Icon(Icons.article_outlined,
                  color: Obsidian.primary, size: 21),
            ),
            const SizedBox(width: 14),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    children: [
                      // Direction and size, up front. "News" alone told you
                      // something had happened and nothing about whether it
                      // mattered or which way.
                      _newsChip(n.bias, _biasTone(n.bias)),
                      const SizedBox(width: 6),
                      Flexible(
                          child: _newsChip(n.impact, Obsidian.outline)),
                      const Spacer(),
                      Text(_ago(n.publishedAt),
                          style: Obsidian.labelSm(size: 10)),
                    ],
                  ),
                  const SizedBox(height: 8),
                  Text(n.headline, style: Obsidian.body(size: 14)),
                  if (n.summary.isNotEmpty) ...[
                    const SizedBox(height: 6),
                    Text(n.summary,
                        maxLines: 2,
                        overflow: TextOverflow.ellipsis,
                        style: Obsidian.body(
                            color: Obsidian.outline, size: 12)),
                  ],
                  const SizedBox(height: 6),
                  Text('${n.source} · tap to read here',
                      style: Obsidian.labelSm(
                          color: Obsidian.outline, size: 10.5)),
                ],
              ),
            ),
          ],
        ),
      ),
      const SizedBox(height: Obsidian.gutter),
    ];
  }

  List<Widget> _whaleCard(WhaleEvent w) {
    return [
      GlassPanel(
        padding: const EdgeInsets.all(18),
        // a filing needs explaining, not just linking: what the code means,
        // when it actually happened, and why it is not a trigger
        onTap: () => _explainWhale(w),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Container(
              width: 44,
              height: 44,
              decoration: BoxDecoration(
                color: Obsidian.red.withValues(alpha: 0.15),
                shape: BoxShape.circle,
                border: Border.all(
                    color: Obsidian.red.withValues(alpha: 0.45)),
              ),
              child: const Icon(Icons.warning_amber_rounded,
                  color: Obsidian.redSoft, size: 22),
            ),
            const SizedBox(width: 14),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    children: [
                      Expanded(
                        child: Text('Whale Activity Detected',
                            style: Obsidian.bodyLg().copyWith(
                                fontWeight: FontWeight.w600)),
                      ),
                      Text(_ago(w.publishedAt),
                          style: Obsidian.labelSm(size: 10)),
                    ],
                  ),
                  const SizedBox(height: 6),
                  Text(w.describe, style: Obsidian.body(size: 14)),
                  const SizedBox(height: 6),
                  Text('${w.impact} · ${w.note} · tap to explain',
                      style: Obsidian.labelSm(
                          color: Obsidian.outline, size: 10.5)),
                ],
              ),
            ),
          ],
        ),
      ),
      const SizedBox(height: Obsidian.gutter),
    ];
  }

  /// How long ago, said the way a person would.
  static String _ago(DateTime? at) {
    if (at == null) return '';
    final d = DateTime.now().toUtc().difference(at.toUtc());
    if (d.inMinutes < 60) return '${d.inMinutes}m ago';
    if (d.inHours < 48) return '${d.inHours}h ago';
    return '${d.inDays}d ago';
  }

  Future<void> _openUrl(String url) async {
    final uri = Uri.tryParse(url);
    if (uri == null) return;
    await launchUrl(uri, mode: LaunchMode.externalApplication);
  }

  /// What a Form 4 actually says, in the app.
  ///
  /// The filing is disclosure of something that already happened, up to five
  /// days earlier. Presenting it as an event that has just occurred is the
  /// single easiest way to misread this data, so both times are shown and
  /// the lag is named.
  void _explainWhale(WhaleEvent w) {
    showModalBottomSheet<void>(
      context: context,
      backgroundColor: Colors.transparent,
      isScrollControlled: true,
      builder: (_) => SafeArea(
        top: false,
        child: Padding(
          padding: const EdgeInsets.all(Obsidian.containerPadding),
          child: GlassPanel(
            active: true,
            padding: const EdgeInsets.all(22),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text('INSIDER FILING',
                    style: Obsidian.labelSm(
                        size: 11, color: Obsidian.redSoft)),
                const SizedBox(height: 12),
                Text(w.describe,
                    style: Obsidian.bodyLg()
                        .copyWith(fontWeight: FontWeight.w600)),
                const SizedBox(height: 16),
                _explainRow('Reading', w.impact),
                if (w.code.isNotEmpty)
                  _explainRow('Transaction code', w.code),
                if (w.conviction != null)
                  _explainRow('Conviction',
                      w.conviction!.toStringAsFixed(2)),
                if (w.eventTime != null)
                  _explainRow('Trade happened', _ago(w.eventTime)),
                if (w.publishedAt != null)
                  _explainRow('Disclosed', _ago(w.publishedAt)),
                const SizedBox(height: 14),
                Text(
                    'A Form 4 discloses a trade that already happened — the '
                    'law allows up to five days, so the market may have '
                    'moved on it before you saw it. Treat it as lagging '
                    'evidence about what insiders were doing, never as a '
                    'trigger to act now.',
                    style:
                        Obsidian.body(color: Obsidian.outline, size: 12)),
                const SizedBox(height: 16),
                Row(
                  mainAxisAlignment: MainAxisAlignment.spaceBetween,
                  children: [
                    if (w.url.isNotEmpty)
                      TextButton(
                        onPressed: () => _openUrl(w.url),
                        child: Text('Open the filing',
                            style:
                                Obsidian.body(color: Obsidian.primary)),
                      )
                    else
                      const SizedBox.shrink(),
                    TextButton(
                      onPressed: () => Navigator.of(context).pop(),
                      child: Text('Close', style: Obsidian.body()),
                    ),
                  ],
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }

  Widget _explainRow(String label, String value) => Padding(
        padding: const EdgeInsets.only(bottom: 8),
        child: Row(
          children: [
            Expanded(
                child: Text(label,
                    style:
                        Obsidian.body(color: Obsidian.outline, size: 12.5))),
            Text(value, style: Obsidian.dataTable(size: 12.5)),
          ],
        ),
      );

  // ------------------------------------------------------------ chart card
  double? get _livePrice => _live?.price;

  Widget _chartCard(Dashboard d) {
    final change = _live?.changePct ?? d.changePct ?? 0;
    final up = change >= 0;
    final tone = up ? Obsidian.green : Obsidian.red;
    final live = d.live;
    final shown = _livePrice ?? d.price;
    return GlassPanel(
      padding: const EdgeInsets.fromLTRB(18, 18, 18, 12),
      onTap: () => _openBinance(d.symbol),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text('24H', style: Obsidian.labelSm(size: 12)),
                    const SizedBox(height: 4),
                    Text('TIMEFRAME', style: Obsidian.labelSm(size: 12)),
                  ],
                ),
              ),
              Column(
                crossAxisAlignment: CrossAxisAlignment.end,
                children: [
                  Text(_money(shown), style: Obsidian.displayLg()),
                  const SizedBox(height: 2),
                  Text('${up ? '+' : ''}${change.toStringAsFixed(2)}%',
                      style: Obsidian.dataTable(
                          color: tone, size: 15, w: FontWeight.w700)),
                ],
              ),
            ],
          ),
          const SizedBox(height: 8),
          Sparkline(values: _series, color: tone),
          const SizedBox(height: 6),
          // two lines, not one: sharing a row with the credit was ellipsising
          // the volume reading into "vol…", which reads as a rendering fault
          if (live != null) _liveStrip(live),
          const SizedBox(height: 6),
          Row(
            mainAxisAlignment: MainAxisAlignment.end,
            children: [
              StatusDot(live: widget.live.connected, size: 6),
              const SizedBox(width: 6),
              Text(
                  widget.live.connected
                      ? 'LIVE from Binance · tap to open'
                      : 'Binance · tap to open',
                  style: Obsidian.labelSm(
                      color: Obsidian.outline.withValues(alpha: 0.75),
                      size: 9.5)),
            ],
          ),
        ],
      ),
    );
  }

  /// The forming bar, from the intra-bar watch. Everything here changes
  /// second to second, unlike the indicators below which are last-close.
  Widget _liveStrip(LiveReading l) {
    // three items, not five: this shares a row with the data credit and the
    // fourth was being ellipsised into "v…", which reads as a glitch
    final parts = <String>[
      if (l.movePct != null)
        '${l.movePct! >= 0 ? '+' : ''}${l.movePct!.toStringAsFixed(2)}%',
      if (l.moveAtr != null) '${l.moveAtr!.toStringAsFixed(2)}atr',
      if (l.volumePace != null) 'vol ${l.volumePace!.toStringAsFixed(1)}x',
    ];
    return Text(parts.join('  ·  '),
        overflow: TextOverflow.ellipsis,
        style: Obsidian.labelSm(
            color: l.beyondSpike ? Obsidian.green : Obsidian.outline,
            size: 10));
  }

  // ------------------------------------------------------------ indicators
  Widget _indicatorGrid(Dashboard d) {
    // THE HTF TILE IS REPLACED, NOT REMOVED. It read "4H STRUCTURE · BULL" —
    // one rung up, saying nothing about where this is going over months. The
    // long view is what that slot is better spent on, and it is the one thing
    // here that is NOT a model output, so it opens a panel that says so.
    //
    // Rendered by the SHARED grid so the stock dashboard cannot drift from
    // this one — they were two renderings of the same payload.
    return IndicatorGrid(
      indicators:
          d.indicators.map((i) => i.key == 'htf' ? _horizonTile() : i).toList(),
      onTap: _openIndicator,
    );
  }

  /// A tile in the indicator grid's shape, standing in for `htf`.
  Indicator _horizonTile() => Indicator.fromJson({
        'key': 'horizon',
        'label': 'THE LONG VIEW',
        'value': '1M · 6M · 1Y',
        'note': 'What history did over months, and how little of it there is',
        'tone': null,
      });

  Future<void> _openHorizon() async {
    HorizonReport? r;
    try {
      r = await widget.client.horizon(widget.symbol);
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(
        backgroundColor: Obsidian.surfaceHigh,
        content: Text('Could not load the long view: $e',
            style: Obsidian.body()),
      ));
      return;
    }
    if (!mounted) return;
    await showModalBottomSheet<void>(
      context: context,
      backgroundColor: Colors.transparent,
      isScrollControlled: true,
      builder: (_) => HorizonSheet(report: r!),
    );
  }

  Future<void> _openIndicator(Indicator ind) async {
    if (ind.key == 'horizon') return _openHorizon();
    showModalBottomSheet<void>(
      context: context,
      backgroundColor: Colors.transparent,
      isScrollControlled: true,
      builder: (_) => IndicatorSheet(
        client: widget.client,
        indicator: ind,
        symbol: widget.symbol,
        interval: widget.interval,
      ),
    );
  }

  Widget _recommendation(Dashboard d) {
    final r = d.recommendation;

    // The server reports the strongest level this entry clears; the user
    // decides how strong is strong enough. A withheld call becomes FLAT and
    // SAYS SO — a recommendation that silently does not exist is worse than
    // one that explains why it is being held back.
    final gated = r.action != 'FLAT' &&
        r.action != 'STALE' &&
        r.strength.isNotEmpty &&
        !r.clears(_sensitivity);

    final action = gated ? 'FLAT' : r.action;
    final tone = gated ? 'flat' : r.tone;
    final c = Obsidian.tone(tone);
    final glowing = tone == 'up' || tone == 'down';
    return GlassPanel(
      active: true,
      radius: Obsidian.rLg,
      padding: const EdgeInsets.symmetric(horizontal: 22, vertical: 26),
      glow: glowing ? c : null,
      glowOpacity: 0.35,
      child: Column(
        children: [
          Text('RECOMMENDED ACTION',
              style: Obsidian.labelSm(size: 11.5)),
          const SizedBox(height: 12),
          Text(
            action,
            style: Obsidian.displayLg(color: c).copyWith(
              fontSize: 52,
              shadows: glowing
                  ? [BoxShadow(color: c.withValues(alpha: 0.55), blurRadius: 28)]
                  : null,
            ),
          ),
          const SizedBox(height: 12),
          Text(
              gated
                  ? 'A ${r.strength} signal is available here. Your setting is '
                      '"$_sensitivity", so it is not shown as a call — change '
                      'it in Profile to act on weaker ones.'
                  : r.detail,
              textAlign: TextAlign.center, style: Obsidian.body(size: 14.5)),
          if (!gated && r.strength.isNotEmpty) ...[
            const SizedBox(height: 8),
            Text('${r.strength.toUpperCase()} SIGNAL',
                style: Obsidian.labelSm(color: c, size: 10)),
          ],
          if (!gated && r.sizePct != null && r.sizePct! > 0) ...[
            const SizedBox(height: 10),
            Text('quarter-Kelly size ${r.sizePct!.toStringAsFixed(2)}% of equity',
                style: Obsidian.labelSm(color: c, size: 10.5)),
          ],
        ],
      ),
    );
  }

  // -------------------------------------------------------------- levels
  Widget _levels(Dashboard d) {
    // TP and SL follow the LIVE price: the model fixed the barrier DISTANCE
    // at the last close, not the price it is measured from, so these are the
    // levels for an entry right now. The probability is still the one read at
    // the close — the footnote inside the panel says so.
    return LevelsPanel(
      price: _livePrice ?? d.price,
      takeProfit: d.liveTakeProfit(_livePrice),
      stopLoss: d.liveStopLoss(_livePrice),
      pUp: d.windowPUp,
      windowBars: d.windowBars,
      interval: d.interval,
      side: d.side,
    );
  }

  /// Every fitted timeframe, side by side.
  ///
  /// Worth showing because disagreement is the useful part: rows that all
  /// say the same thing are one piece of evidence repeated, rows that
  /// conflict are a reason to wait. It is a view, not a model — nothing here
  /// feeds back into a prediction.
  List<Widget> _consensusPanel() {
    final c = _consensus;
    if (c == null || c.rows.length < 2) return const [];
    return [
      GlassPanel(
        padding: const EdgeInsets.fromLTRB(18, 16, 18, 14),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Text('ACROSS TIMEFRAMES', style: Obsidian.labelSm(size: 11)),
                const Spacer(),
                if (c.agreement != null)
                  Text('${(c.agreement! * 100).round()}% agree',
                      style: Obsidian.labelSm(
                          color: c.agreement! >= 0.8
                              ? Obsidian.green
                              : Obsidian.onSurfaceVariant,
                          size: 10.5)),
              ],
            ),
            const SizedBox(height: 12),
            for (final r in c.rows)
              Padding(
                padding: const EdgeInsets.only(bottom: 8),
                child: Row(
                  children: [
                    SizedBox(
                      width: 42,
                      child: Text(r.interval,
                          style: Obsidian.dataTable(
                              size: 13,
                              color: r.interval == widget.interval
                                  ? Obsidian.green
                                  : Obsidian.onSurfaceVariant)),
                    ),
                    Expanded(child: _consensusBar(r)),
                    const SizedBox(width: 10),
                    SizedBox(
                      width: 52,
                      child: Text(
                          r.pUp == null
                              ? '—'
                              : '${(r.pUp! * 100).toStringAsFixed(1)}%',
                          textAlign: TextAlign.right,
                          style: Obsidian.dataTable(
                              size: 13,
                              color: r.stale
                                  ? Obsidian.outline
                                  : Obsidian.onSurface)),
                    ),
                  ],
                ),
              ),
            const SizedBox(height: 2),
            Text(c.note,
                style: Obsidian.body(color: Obsidian.outline, size: 10.5)),
          ],
        ),
      ),
      const SizedBox(height: Obsidian.gutter),
    ];
  }

  /// A bar centred on 50%: right of centre leans up, left leans down.
  /// Anchoring at zero instead would make every reading look enormous.
  Widget _consensusBar(ConsensusRow r) {
    final p = r.pUp;
    if (p == null) return const SizedBox(height: 6);
    final lean = (p - 0.5).clamp(-0.5, 0.5);
    final up = lean >= 0;
    final colour =
        r.stale ? Obsidian.outline : (up ? Obsidian.green : Obsidian.red);
    return LayoutBuilder(builder: (_, box) {
      final half = box.maxWidth / 2;
      final w = (lean.abs() / 0.5) * half;
      return SizedBox(
        height: 8,
        child: Stack(
          children: [
            Positioned.fill(
              child: Container(
                margin: const EdgeInsets.symmetric(vertical: 3),
                color: Colors.white.withValues(alpha: 0.05),
              ),
            ),
            Positioned(
              left: up ? half : half - w,
              width: w.clamp(1.0, half),
              top: 0,
              bottom: 0,
              child: DecoratedBox(
                decoration: BoxDecoration(
                  color: colour.withValues(alpha: 0.75),
                  borderRadius: BorderRadius.circular(2),
                ),
              ),
            ),
            Positioned(
              left: half - 0.5,
              top: 0,
              bottom: 0,
              child: Container(
                  width: 1, color: Colors.white.withValues(alpha: 0.25)),
            ),
          ],
        ),
      );
    });
  }

  Widget _note(Dashboard d) => Padding(
        padding: const EdgeInsets.symmetric(horizontal: 4),
        child: Text(
            'TP and SL track the live price — they are the levels for an entry '
            'now, using the barrier distance the model fixed at the '
            '${d.interval} close (${_money(d.anchor)}). '
            '${d.calibrationNote}',
            style: Obsidian.body(color: Obsidian.outline, size: 11.5)),
      );

  // ------------------------------------------------------------- fallbacks
  Widget _untrainedPanel() {
    final tfs = _data?.timeframes ?? const <TimeframeInfo>[];
    return ListView(
      padding: const EdgeInsets.fromLTRB(Obsidian.containerPadding, 8,
          Obsidian.containerPadding, Obsidian.navClearance + 24),
      children: [
        if (tfs.isNotEmpty) ...[
          TimeframeBar(
            timeframes: tfs,
            selected: widget.interval,
            onSelect: (tf) => tf.trained
                ? widget.onPickInterval(tf.interval)
                : widget.onNeedsTraining(tf.interval),
          ),
          const SizedBox(height: Obsidian.gutter),
        ],
        GlassPanel(
          padding: const EdgeInsets.all(22),
          child: Column(
            children: [
              const Icon(Icons.model_training_rounded,
                  color: Obsidian.outline, size: 34),
              const SizedBox(height: 14),
              Text('No model for ${widget.interval}',
                  style: Obsidian.headlineMd()),
              const SizedBox(height: 10),
              Text(
                'Each timeframe is its own fitted model — a pattern on a 1m '
                'chart is not the same object as the one on a 1d chart, so a '
                'single fit cannot serve both. There is no probability to '
                'show here until this one is trained.',
                textAlign: TextAlign.center,
                style: Obsidian.body(size: 13.5),
              ),
              const SizedBox(height: 18),
              TrainButton(
                client: widget.client,
                symbol: widget.symbol,
                interval: widget.interval,
                onTrained: _load,
              ),
              const SizedBox(height: 18),
              // The command is kept underneath, for running it from a machine
              // when the server is busy or unreachable.
              Container(
                width: double.infinity,
                padding:
                    const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
                decoration: BoxDecoration(
                  color: Obsidian.surfaceLowest.withValues(alpha: 0.8),
                  borderRadius: BorderRadius.circular(Obsidian.rMd),
                  border:
                      Border.all(color: Colors.white.withValues(alpha: 0.08)),
                ),
                child: Row(
                  children: [
                    Expanded(
                      child: SelectableText(
                        'python3 train.py --symbol ${widget.symbol} '
                        '--intervals ${widget.interval}',
                        style: Obsidian.dataTable(size: 11.5),
                      ),
                    ),
                    InkWell(
                      onTap: () {
                        Clipboard.setData(ClipboardData(
                            text: 'python3 train.py --symbol ${widget.symbol} '
                                '--intervals ${widget.interval}'));
                        ScaffoldMessenger.of(context).showSnackBar(SnackBar(
                          backgroundColor: Obsidian.surfaceHigh,
                          duration: const Duration(seconds: 2),
                          content:
                              Text('Copied', style: Obsidian.body()),
                        ));
                      },
                      child: const Padding(
                        padding: EdgeInsets.only(left: 10),
                        child: Icon(Icons.copy_rounded,
                            size: 16, color: Obsidian.outline),
                      ),
                    ),
                  ],
                ),
              ),
              const SizedBox(height: 10),
              Text('Runs on the server, not the phone — fitting is a batch '
                  'job measured in minutes.',
                  textAlign: TextAlign.center,
                  style: Obsidian.body(color: Obsidian.outline, size: 11)),
            ],
          ),
        ),
      ],
    );
  }

  Widget _placeholder() {
    if (_error != null) {
      return Center(
        child: Padding(
          padding: const EdgeInsets.all(Obsidian.containerPadding),
          child: GlassPanel(
            padding: const EdgeInsets.all(22),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                const Icon(Icons.cloud_off_rounded,
                    color: Obsidian.outline, size: 34),
                const SizedBox(height: 14),
                Text('No link to the service',
                    style: Obsidian.headlineMd()),
                const SizedBox(height: 10),
                // The five minutes are the point of the sentence. Without
                // them this looks like the app gave up instantly, which is
                // exactly what it used to do.
                Text('Tried for ${kPatience.inMinutes} minutes and got '
                    'nothing back.',
                    textAlign: TextAlign.center,
                    style: Obsidian.body(color: Obsidian.outline, size: 12)),
                const SizedBox(height: 10),
                Text(_error!, style: Obsidian.body(size: 12.5)),
                const SizedBox(height: 18),
                FilledButton(
                  style: FilledButton.styleFrom(
                      backgroundColor: Obsidian.primary,
                      foregroundColor: Obsidian.onPrimary),
                  onPressed: _tryAgain,
                  child: const Text('Retry'),
                ),
              ],
            ),
          ),
        ),
      );
    }
    return WaitingPanel(
      since: _waitingSince,
      what: 'Loading ${widget.symbol}',
      onPatienceExhausted: _giveUp,
    );
  }

  static String _money(double? v) {
    if (v == null) return '—';
    final s = v.toStringAsFixed(v.abs() >= 100 ? 2 : 4);
    final parts = s.split('.');
    final whole = parts[0].replaceAllMapped(
        RegExp(r'(\d)(?=(\d{3})+$)'), (m) => '${m[1]},');
    return '\$$whole.${parts[1]}';
  }
}
