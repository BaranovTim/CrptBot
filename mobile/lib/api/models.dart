/// Typed views over the JSON the Python service returns.
///
/// Every numeric field is nullable, and that is deliberate rather than lazy.
/// The backend sends `null` for anything it does not know — an absent order
/// block, an indicator still inside its warmup, a probability with no fitted
/// model. Coercing those to 0 would be the same mistake the Python side goes
/// to some length to avoid: zero means "the level is exactly here", which is
/// the opposite of "there is no level".
library;

double? _d(dynamic v) => v == null ? null : (v as num).toDouble();

class Coin {
  Coin.fromJson(Map<String, dynamic> j)
      : symbol = j['symbol'] as String,
        name = j['name'] as String,
        short = j['short'] as String,
        pair = j['pair'] as String,
        price = _d(j['price']),
        changePct = _d(j['change_pct']),
        trained = j['trained'] as bool,
        trainedIntervals = ((j['trained_intervals'] as List?) ?? const [])
            .map((e) => '$e')
            .toList(),
        listed = j['listed'] as bool? ?? true;

  final String symbol, name, short, pair;
  final double? price, changePct;
  final bool trained;

  /// Which timeframes actually have a fitted model. A coin is not simply
  /// "trained" — each timeframe is its own model, because a pattern means
  /// something different on a 1m chart than on a 1d one.
  final List<String> trainedIntervals;

  /// False when Binance does not list this perpetual — a delisted pair, or a
  /// near miss like PEPEUSDT when the contract is actually 1000PEPEUSDT.
  /// Without it the row is just dashes with no explanation.
  final bool listed;
}

class SymbolInfo {
  SymbolInfo.fromJson(Map<String, dynamic> j)
      : symbol = j['symbol'] as String,
        base = j['base'] as String,
        quote = j['quote'] as String? ?? 'USDT',
        volume24h = _d(j['volume_24h']),
        served = j['served'] as bool? ?? false;

  final String symbol, base, quote;
  final double? volume24h;

  /// This server makes calls on it. The rest of Binance's list can be
  /// followed for its chart and price, but no call will come.
  final bool served;
}

/// How much of the take-profit-to-stop-loss span the round trip in fees eats.
///
/// The number that decides whether a timeframe is tradeable AT ALL, and it has
/// nothing to do with how good the model is. At 1m on BTCUSDT the span is
/// ~0.092% against a 0.100% round trip: the barriers sit inside the fees, and
/// an AUC of 0.99 would still lose money.
class CostDrag {
  CostDrag.fromJson(Map<String, dynamic> j)
      : spanPct = _d(j['span_pct']),
        costPct = _d(j['cost_pct']),
        share = _d(j['cost_share']),
        verdict = j['verdict'] as String? ?? 'unknown',
        note = j['note'] as String? ?? '';

  final double? spanPct, costPct, share;
  final String verdict, note;

  bool get untradeable => verdict == 'untradeable';
  bool get marginal => verdict == 'marginal';
}

class TimeframeInfo {
  TimeframeInfo.fromJson(Map<String, dynamic> j)
      : interval = j['interval'] as String,
        label = j['label'] as String,
        htf = j['htf'] as String? ?? '',
        trained = j['trained'] as bool? ?? false,
        bars = (j['bars'] as num?)?.toInt() ?? 0,
        cost = j['cost'] == null
            ? null
            : CostDrag.fromJson(j['cost'] as Map<String, dynamic>);

  final String interval, label, htf;
  final bool trained;
  final int bars;
  final CostDrag? cost;
}

class Indicator {
  Indicator.fromJson(Map<String, dynamic> j)
      : key = j['key'] as String,
        label = j['label'] as String,
        value = j['value'] as String,
        note = j['note'] as String,
        tone = j['tone'] as String?;

  final String key, label, value, note;
  final String? tone;
}

class Recommendation {
  Recommendation.fromJson(Map<String, dynamic> j)
      : action = j['action'] as String,
        tone = j['tone'] as String?,
        detail = j['detail'] as String? ?? '',
        ev = _d(j['ev']),
        strength = j['strength'] as String? ?? '',
        smartNote = j['smart_note'] as String? ?? '',
        called = j['called'] as String? ?? '',
        outcome = j['outcome'] as String? ?? '',
        pNeeded = _d(j['p_needed']),
        sizePct = _d(j['size_pct']),
        order = j['order'] is Map
            ? EntryOrder.fromJson((j['order'] as Map).cast<String, dynamic>())
            : null;

  /// The resting order a pooled 4h call enters with (see [EntryOrder]).
  /// Null on timeframes that enter at the close.
  final EntryOrder? order;

  /// A trade a resting order opened, running: WAIT that is not a missed call.
  bool get inTrade => action == 'WAIT' && order?.state == 'filled';

  /// BUY, SELL, FLAT, STALE -- or WAIT: a call the forming bar has already
  /// settled. Its target was reached or its stop was hit before the bar
  /// closed, so there is nothing to enter until the next close.
  final String action;
  final String? tone;
  final String detail;
  final double? ev, sizePct;

  /// On WAIT: what the model said at the close (BUY or SELL), and what the
  /// bar did to it ("target" reached, or "stop" hit). Empty otherwise.
  final String called, outcome;

  /// "Smart money agrees: 2 followed traders opened LONG in the last 24h."
  /// -- set when the followed traders' entries changed or confirmed this
  /// call (4h only, where it was measured). Empty otherwise.
  final String smartNote;

  /// strong | medium | small, or "" when the expected value does not clear
  /// breakeven after costs. The server reports the strongest level this
  /// entry qualifies for; the app shows or withholds it based on the
  /// sensitivity the user picked.
  final String strength;

  /// The probability this timeframe would need for an entry to pay for its
  /// costs. Shown beside FLAT so it reads as a measurement, not a shrug.
  final double? pNeeded;

  /// Does this clear the bar the user asked for?
  bool clears(String setting) {
    const rank = {'strong': 3, 'medium': 2, 'small': 1, '': 0};
    return (rank[strength] ?? 0) >= (rank[setting] ?? 3) &&
        (rank[strength] ?? 0) > 0;
  }
}

/// A call's resting entry: a limit order some way better than the close
/// that produced the call, good for a few bars, instead of buying at the
/// close. The stop keeps its distance from this price; the target stays on
/// its level. research/improve_4h.py -- and the resting-order habit that
/// separated the profitable big traders from the losing ones.
class EntryOrder {
  EntryOrder.fromJson(Map<String, dynamic> j)
      : state = j['state'] as String? ?? '',
        limit = _d(j['limit']),
        stop = _d(j['stop']),
        target = _d(j['target']),
        fill = _d(j['fill']),
        level = j['level'] as String? ?? '',
        placedAt = _t(j['placed_at']),
        validUntil = _t(j['valid_until']),
        holdUntil = _t(j['hold_until']),
        filledAt = _t(j['filled_at']),
        exit = _d(j['exit']),
        scalePart = _d(j['scale_part']),
        scalePrice = _d(j['scale_price']),
        taken = j['taken'] as bool? ?? false,
        retPct = _d(j['ret_pct']);

  /// open | filled | target | stop | timeout | expired
  final String state, level;
  final double? limit, stop, target, fill, exit;
  final DateTime? placedAt, validUntil, holdUntil, filledAt;

  /// THE SCALE-OUT: `scalePart` of the position comes off at `scalePrice`
  /// (halfway from the entry to the target) and the stop on the rest moves
  /// to the entry. `taken` once it has; `retPct` is the whole trade's
  /// return, both parts weighted, once it has ended.
  final double? scalePart, scalePrice, retPct;
  final bool taken;

  bool get isOpen => state == 'open';

  /// "a third", "half", or a percentage -- for the card's words.
  String get partWords {
    final p = scalePart;
    if (p == null) return '';
    if ((p - 1 / 3).abs() < 0.01) return 'a third';
    if ((p - 0.5).abs() < 0.01) return 'half';
    return '${(p * 100).round()}%';
  }

  /// The same, as the short badge text: ⅓, ½ or a percentage.
  String get partShort {
    final p = scalePart;
    if (p == null) return '';
    if ((p - 1 / 3).abs() < 0.01) return '⅓';
    if ((p - 0.5).abs() < 0.01) return '½';
    return '${(p * 100).round()}%';
  }

  static DateTime? _t(Object? v) =>
      v is String && v.isNotEmpty ? DateTime.tryParse(v)?.toUtc() : null;
}

class Analysis {
  Analysis.fromJson(Map<String, dynamic> j)
      : name = j['name'] as String,
        barsLeft = j['bars_left'] as int,
        pUp = _d(j['p_up']),
        entry = _d(j['entry']),
        takeProfit = _d(j['take_profit']),
        stopLoss = _d(j['stop_loss']),
        evLong = _d(j['ev_long']),
        evShort = _d(j['ev_short']),
        action = j['action'] as String? ?? '',
        reason = j['reason'] as String? ?? '',
        endsAt = j['ends_at'] as String? ?? '';

  final String name, action, reason, endsAt;
  final int barsLeft;
  final double? pUp, entry, takeProfit, stopLoss, evLong, evShort;
}

class LiveReading {
  LiveReading.fromJson(Map<String, dynamic> j)
      : price = _d(j['price']),
        movePct = _d(j['move_pct']),
        moveAtr = _d(j['move_atr']),
        volumePace = _d(j['volume_pace']),
        takerBuyRatio = _d(j['taker_buy_ratio']),
        elapsed = _d(j['elapsed']),
        beyondSpike = j['beyond_spike_threshold'] as bool? ?? false;

  final double? price, movePct, moveAtr, volumePace, takerBuyRatio, elapsed;
  final bool beyondSpike;
}

class BotStatus {
  BotStatus.fromJson(Map<String, dynamic> j)
      : active = j['active'] as bool,
        label = j['label'] as String,
        detail = j['detail'] as String,
        trades = j['trades'] as bool? ?? false;

  final bool active, trades;
  final String label, detail;
}

class Dashboard {
  Dashboard.fromJson(Map<String, dynamic> j)
      : symbol = j['symbol'] as String,
        pair = j['pair'] as String,
        interval = j['interval'] as String,
        htf = j['htf'] as String? ?? '',
        timeframes = ((j['timeframes'] as List?) ?? const [])
            .map((e) => TimeframeInfo.fromJson(e as Map<String, dynamic>))
            .toList(),
        stale = j['stale'] as bool,
        price = _d(j['price']),
        changePct = _d(j['change_pct']),
        status = BotStatus.fromJson(j['status'] as Map<String, dynamic>),
        live = j['live'] == null
            ? null
            : LiveReading.fromJson(j['live'] as Map<String, dynamic>),
        indicators = (j['indicators'] as List)
            .map((e) => Indicator.fromJson(e as Map<String, dynamic>))
            .toList(),
        analyses = (j['analyses'] as List)
            .map((e) => Analysis.fromJson(e as Map<String, dynamic>))
            .toList(),
        recommendation = Recommendation.fromJson(
            j['recommendation'] as Map<String, dynamic>),
        takeProfit = _d((j['levels'] as Map)['take_profit']),
        stopLoss = _d((j['levels'] as Map)['stop_loss']),
        anchor = _d((j['levels'] as Map)['anchor']),
        windowBars = ((j['levels'] as Map)['window_bars'] as num?)?.toInt(),
        windowPUp = _d((j['levels'] as Map)['p_up']),
        tpPct = _d((j['levels'] as Map)['tp_pct']),
        slPct = _d((j['levels'] as Map)['sl_pct']),
        tpOffsetPct = _d((j['levels'] as Map)['tp_offset_pct']),
        slOffsetPct = _d((j['levels'] as Map)['sl_offset_pct']),
        side = (j['levels'] as Map)['side'] as String? ?? '',
        levelsReached = (j['levels'] as Map)['reached'] as String? ?? '',
        entryLimit = _d((j['levels'] as Map)['entry_limit']),
        scaleOut = _d((j['levels'] as Map)['scale_out']),
        levelGeometry = (j['levels'] as Map)['geometry'] as String? ??
            (j['recommendation'] as Map?)?['geometry'] as String? ??
            'atr',
        trendAbove = (j['trend'] as Map?)?['above'] as bool?,
        trendEma = _d((j['trend'] as Map?)?['ema']),
        calibrationNote = j['calibration_note'] as String? ?? '';

  final String symbol, pair, interval, htf, calibrationNote;

  /// The resting order's price when the call enters with one: the levels
  /// are measured from here, and a logged entry defaults to it.
  final double? entryLimit;

  /// Where part of the position comes off (halfway to the target) while
  /// that has not happened yet; null otherwise.
  final double? scaleOut;

  /// The 200-day trend, on the timeframes that gate on it (daily). Null
  /// elsewhere, or with too little history to say.
  final bool? trendAbove;
  final double? trendEma;
  final List<TimeframeInfo> timeframes;
  final bool stale;
  final double? price, changePct, takeProfit, stopLoss;

  /// The close the model measured its barriers from, and the barrier
  /// DISTANCES. The distances are what the model fixed at that close; the
  /// price they are measured from is not, which is why the app can re-anchor
  /// them to a live price without touching the model's claim.
  final double? anchor, tpPct, slPct;

  /// The same two distances, SIGNED, measured off the levels the model
  /// actually named rather than off the long-oriented barriers.
  ///
  /// `tpPct`/`slPct` are magnitudes and say nothing about direction, which is
  /// how a SELL ended up drawn with its target above the price and its stop
  /// below — the two levels swapped, on the one call where getting them the
  /// wrong way round is worst. These carry the sign, so one expression is
  /// right for both directions. Null against a server too old to send them,
  /// which is why the magnitudes are still read.
  final double? tpOffsetPct, slOffsetPct;

  /// LONG, SHORT, or "" when no position is proposed and the levels are just
  /// the model's barriers.
  final String side;

  bool get isShort => side == 'SHORT';

  /// The window the recommendation and these levels BOTH describe.
  ///
  /// Not always the longer horizon: an entering window wins over a waiting
  /// one, so a BUY from the 1-bar model brings its own probability and its
  /// own barriers. Reading them off different analyses put "BUY" next to a
  /// 51.7% that belonged to a different question.
  final int? windowBars;
  final double? windowPUp;

  /// Where TP/SL sit for an entry at `live`, rather than at the bar close.
  ///
  /// The signed offsets are preferred and the magnitudes are the fallback,
  /// flipped by `side` — an older server sends only the magnitudes, and on a
  /// SHORT those have to be inverted here or the panel contradicts the call
  /// printed directly above it.
  /// How the levels were placed. "atr": a distance from the close, which
  /// follows the live price. "structure": the next swing and the last
  /// swing -- PRICES, fixed on the chart, never re-anchored.
  final String levelGeometry;
  bool get levelsAreFixed => levelGeometry == 'structure';

  /// What the forming bar had already done to the call's levels when the
  /// server built this: "target", "stop" or "". The server reads the bar's
  /// high and low, which the phone does not have.
  final String levelsReached;

  /// The call's outcome as of NOW: the server's reading, or -- between
  /// server refreshes -- the live price sitting past one of the levels.
  ///
  /// Only for a call with fixed levels. A structure call names two prices
  /// and the model's number is the chance of reaching one before the other
  /// from the close; once the price is past the target, that trade is over
  /// and a "BUY" with the target below the price is a call to chase, not to
  /// enter. DOGE did +13% in a day and the card kept saying BUY with the
  /// target 2% under the price. ATR levels re-anchor to the live price and
  /// can never be behind it, so they never settle here.
  String outcome(double? live) {
    if (recommendation.outcome.isNotEmpty) return recommendation.outcome;
    if (levelsReached.isNotEmpty) return levelsReached;
    final a = recommendation.action;
    if ((a != 'BUY' && a != 'SELL') || !levelsAreFixed || live == null) {
      return '';
    }
    final tp = takeProfit, sl = stopLoss;
    // A RESTING ORDER THAT HAS NOT FILLED is not over when the price runs
    // past its target: the order stays good for its window, as it was
    // measured, and may still fill on a pullback. Past the stop is
    // different -- the stop sits beyond the order, so reaching it means the
    // order filled on the way.
    final unfilled = recommendation.order?.isOpen ?? false;
    if (isShort) {
      if (sl != null && live >= sl) return 'stop';
      if (!unfilled && tp != null && live <= tp) return 'target';
    } else {
      if (sl != null && live <= sl) return 'stop';
      if (!unfilled && tp != null && live >= tp) return 'target';
    }
    return '';
  }

  double? liveTakeProfit(double? live) {
    if (live == null || levelsAreFixed) return takeProfit;
    final off = tpOffsetPct ?? (tpPct == null ? null : (isShort ? -tpPct! : tpPct!));
    return off == null ? takeProfit : live * (1 + off / 100);
  }

  double? liveStopLoss(double? live) {
    if (live == null || levelsAreFixed) return stopLoss;
    final off = slOffsetPct ?? (slPct == null ? null : (isShort ? slPct! : -slPct!));
    return off == null ? stopLoss : live * (1 + off / 100);
  }
  final BotStatus status;
  final LiveReading? live;
  final List<Indicator> indicators;
  final List<Analysis> analyses;
  final Recommendation recommendation;
}

class WhaleEvent {
  WhaleEvent.fromJson(Map<String, dynamic> j)
      : describe = j['describe'] as String,
        impact = j['impact'] as String,
        side = j['side'] as String,
        mechanical = j['mechanical'] as bool? ?? false,
        code = j['code'] as String? ?? '',
        note = j['note'] as String? ?? '',
        conviction = _d(j['conviction']),
        url = j['url'] as String? ?? '',
        publishedAt = j['published_at'] == null
            ? null
            : DateTime.parse(j['published_at'] as String),
        eventTime = j['event_time'] == null
            ? null
            : DateTime.parse(j['event_time'] as String);

  final String describe, impact, side, code, note, url;
  final bool mechanical;
  final double? conviction;

  /// When the filing was DISCLOSED. Insider filings lag the trade by 24-120
  /// hours, so this is the moment the market could have known — which is the
  /// only honest basis for calling something recent.
  final DateTime? publishedAt;

  /// When the trade actually happened, which is earlier and sometimes much
  /// earlier. Shown alongside, never instead.
  final DateTime? eventTime;

  /// How long this stays worth pinning to the top of the screen.
  ///
  /// Bigger convictions earn a longer window: a chief executive selling two
  /// million dollars is still context two days later, while a small routine
  /// disposal stops being news within half a day. Before this, ONE whale was
  /// pinned permanently regardless of age — the dashboard led with a filing
  /// from days ago every time it opened.
  Duration get relevantFor {
    final c = conviction ?? 0.0;
    if (c >= 0.75) return const Duration(hours: 72);
    if (c >= 0.4) return const Duration(hours: 36);
    return const Duration(hours: 12);
  }

  bool get isRecent {
    final at = publishedAt;
    if (at == null) return false;
    return DateTime.now().toUtc().difference(at.toUtc()) < relevantFor;
  }
}

class NewsItem {
  NewsItem.fromJson(Map<String, dynamic> j)
      : headline = j['headline'] as String? ?? '',
        source = j['source'] as String? ?? '',
        url = j['url'] as String? ?? '',
        summary = j['summary'] as String? ?? '',
        bias = j['bias'] as String? ?? 'NO READING',
        impact = j['impact'] as String? ?? 'NO READING',
        assets = ((j['assets'] as List?) ?? const []).cast<String>(),
        macro = j['macro'] as bool? ?? false,
        publishedAt = j['published_at'] == null
            ? null
            : DateTime.parse(j['published_at'] as String);

  final String headline, source, url;

  /// The publisher's own excerpt. Deliberately not the full article — that
  /// would be republishing their work, which is why every summary ends with
  /// a link to the source instead.
  final String summary;

  /// BULL | BEAR | MIXED | NO READING, and STRONG/MEDIUM/ALMOST NO IMPACT.
  ///
  /// "NO READING" is not the same claim as neutral. The scorer is a keyword
  /// count and stays silent on roughly 40% of headlines; saying MIXED there
  /// would assert balance it never measured.
  final String bias, impact;

  final List<String> assets;
  final bool macro;
  final DateTime? publishedAt;

  bool get hasReading => bias != 'NO READING';

  /// News ages faster than a filing: by the next day it is priced in and
  /// everybody has seen it.
  static const relevantFor = Duration(hours: 8);

  bool get isRecent {
    final at = publishedAt;
    if (at == null) return false;
    return DateTime.now().toUtc().difference(at.toUtc()) < relevantFor;
  }
}

class TrainingInfo {
  TrainingInfo.fromJson(Map<String, dynamic> j)
      : symbol = j['symbol'] as String,
        interval = j['interval'] as String? ?? '',
        htf = j['htf'] as String? ?? '',
        timeframes = ((j['timeframes'] as List?) ?? const [])
            .map((e) => TimeframeInfo.fromJson(e as Map<String, dynamic>))
            .toList(),
        trained = j['trained'] as bool,
        cost = j['cost'] == null
            ? null
            : CostDrag.fromJson(j['cost'] as Map<String, dynamic>),
        title = j['title'] as String,
        detail = j['detail'] as String,
        command = j['command'] as String?,
        horizons = (j['horizons'] as List?)
                ?.map((e) => e as Map<String, dynamic>)
                .toList() ??
            const [];

  final String symbol, interval, htf, title, detail;
  final CostDrag? cost;
  final List<TimeframeInfo> timeframes;
  final String? command;
  final bool trained;
  final List<Map<String, dynamic>> horizons;
}


class Alert {
  Alert.fromJson(Map<String, dynamic> j)
      : id = j['id'] as String,
        kind = j['kind'] as String,
        title = j['title'] as String,
        body = j['body'] as String,
        severity = j['severity'] as String? ?? 'medium',
        symbol = j['symbol'] as String? ?? '',
        interval = j['interval'] as String? ?? '',
        strength = j['strength'] as String? ?? '',
        bias = j['bias'] as String? ?? '',
        impact = j['impact'] as String? ?? '',
        url = j['url'] as String? ?? '',
        seq = (j['seq'] as num?)?.toInt() ?? 0,
        pushed = j['pushed'] as bool? ?? false,
        at = DateTime.parse(j['at'] as String),
        detectedAt = DateTime.parse(j['detected_at'] as String),
        extra = Map<String, String>.from(
            (j['extra'] as Map?)?.map((k, v) => MapEntry('$k', '$v')) ?? {});

  final String id, kind, title, body, severity, symbol, url;

  /// strong | medium | small for a signal, '' for everything else.
  ///
  /// The same sensitivity setting gates the dashboard card and the
  /// notification. Without this the app could withhold a small call as too
  /// weak to show while its notification was already on the lock screen.
  final String strength;

  /// Is this the call going flat — an EXIT rather than an entry?
  ///
  /// An exit has no strength by definition: there is no expected value to
  /// grade because there is no position being opened. So it must not be
  /// filtered by a setting that grades entry strength, which is precisely
  /// what happened — the server sent every exit and the phone dropped all of
  /// them, silently, because `clearsSensitivity('')` is false.
  bool get isExit => kind == 'signal' && extra['to'] == 'FLAT';

  /// A step in a trade already running -- halfway (take a third off), the
  /// target, the stop, the time limit. About a POSITION, so it goes only to
  /// a phone with an entry logged on that coin and timeframe: someone with
  /// nothing on the coin was being told a third of a trade they never had
  /// "worked out". Mirrored by `MANAGED_ORDER_STEPS` in `api/push.py`.
  bool get managesTrade =>
      kind == 'signal' && managedOrderSteps.contains(extra['order']);

  static const managedOrderSteps = {'partial', 'target', 'stop', 'timeout'};

  /// Did the server hand this to the push relay?
  ///
  /// KEPT FOR DIAGNOSIS, NOT USED TO SUPPRESS ANYTHING — and the difference
  /// is why alerts went quiet once already.
  ///
  /// It means ntfy accepted the message. It does NOT mean a phone displayed
  /// it: the ntfy app can be battery-optimised, unsubscribed, denied
  /// notification permission, or absent, and the server sees none of that.
  /// While this gated the app's own notifications, the relay logged thirty
  /// clean sends and the phone showed nothing.
  ///
  /// Both paths now post. Duplicates are a nuisance; silence is a broken
  /// feature.
  final bool pushed;

  /// For a news alert: BULL | BEAR | MIXED | NO READING, and
  /// STRONG IMPACT | MEDIUM IMPACT | ALMOST NO IMPACT | NO READING.
  ///
  /// Both come from Agent 3's offline scorer, the same source as the labels
  /// on the dashboard card — so the news filter and the card can never
  /// disagree about the same story. Empty for every other kind.
  final String bias, impact;

  /// Which timeframe produced it, '' for alerts that belong to no timeframe
  /// (a filing, a macro release). Muting is per symbol AND per interval, and
  /// cannot be without this.
  final String interval;
  final int seq;

  /// When the underlying thing HAPPENED.
  final DateTime at;

  /// When this process noticed. For a filing these differ by days.
  final DateTime detectedAt;

  final Map<String, String> extra;

  /// The line every notification ends with.
  ///
  /// A filing gets both timestamps, because "a trade on the 24th, disclosed
  /// on the 26th" is a materially different statement from "something
  /// happened just now", and only one of them is true.
  String whenLine() {
    final disclosed = extra['disclosed_at'];
    if (kind == 'whale' && disclosed != null) {
      final d = DateTime.tryParse(disclosed);
      if (d != null) {
        final lag = d.difference(at).inHours;
        return 'Traded ${_stamp(at)} · disclosed ${_stamp(d)}'
            '${lag > 0 ? ' (${lag}h later)' : ''}';
      }
    }
    if (kind == 'calendar') return 'Scheduled for ${_stamp(at)}';
    return _stamp(at);
  }

  static String _stamp(DateTime utc) {
    final l = utc.toLocal();
    final n = DateTime.now();
    String two(int v) => v.toString().padLeft(2, '0');
    final time = '${two(l.hour)}:${two(l.minute)}';
    final sameDay = l.year == n.year && l.month == n.month && l.day == n.day;
    return sameDay ? time : '${two(l.day)}/${two(l.month)} $time';
  }
}

class ScheduledEvent {
  ScheduledEvent.fromJson(Map<String, dynamic> j)
      : key = j['key'] as String,
        title = j['title'] as String,
        impact = j['impact'] as String? ?? 'medium',
        source = j['source'] as String? ?? '',
        url = j['url'] as String? ?? '',
        note = j['note'] as String? ?? '',
        priority = (j['priority'] as num?)?.toInt() ?? 0,
        estimated = j['estimated'] as bool? ?? false,
        at = DateTime.parse(j['at'] as String);

  final String key, title, impact, source, url, note;
  final DateTime at;

  /// How loudly this deserves to be announced. Non-Farm Payrolls is 100,
  /// FOMC 80, everything else lower. Set on the server so the ranking is one
  /// decision in one place rather than a list of special cases in the UI.
  final int priority;

  /// The date was computed from a publisher's rule rather than read from
  /// them. Shown, never hidden — a payroll date that is quietly a week wrong
  /// is worse than no date.
  final bool estimated;

  bool get major => priority >= 100;

  Duration get away => at.difference(DateTime.now());
}


class ConsensusRow {
  ConsensusRow.fromJson(Map<String, dynamic> j)
      : interval = j['interval'] as String,
        htf = j['htf'] as String? ?? '',
        action = j['action'] as String? ?? '',
        tone = j['tone'] as String?,
        stale = j['stale'] as bool? ?? false,
        pUp = _d(j['p_up']),
        ev = _d(j['ev']);

  final String interval, htf, action;
  final String? tone;
  final bool stale;
  final double? pUp, ev;
}

/// What every fitted timeframe says, side by side.
///
/// Independent models, one per timeframe — NOT one model reading several.
/// `agreement` describes these rows and nothing else: it is a measure of how
/// much the models echo each other, not evidence about the market.
class Consensus {
  Consensus.fromJson(Map<String, dynamic> j)
      : symbol = j['symbol'] as String,
        note = j['note'] as String? ?? '',
        agreement = _d(j['agreement']),
        rows = (j['rows'] as List)
            .map((e) => ConsensusRow.fromJson(e as Map<String, dynamic>))
            .toList();

  final String symbol, note;
  final double? agreement;
  final List<ConsensusRow> rows;
}

/// Who is signed in, and what they are allowed to see.
///
/// `entitled` is computed on the SERVER, never here. A client that decides
/// its own entitlement is a client that can be edited to decide differently —
/// the app hides screens for a good experience, and the API refuses them for
/// the actual guarantee.
class Account {
  Account.fromJson(Map<String, dynamic> j)
      : identifier = j['identifier'] as String? ?? '',
        username = j['username'] as String?,
        emailVerified = j['email_verified'] as bool? ?? true,
        tier = j['tier'] as String? ?? 'free',
        entitled = j['entitled'] as bool? ?? false,
        operator = j['operator'] as bool? ?? false,
        subscriptionEnds = (j['subscription_ends'] as num?) == null
            ? null
            : DateTime.fromMillisecondsSinceEpoch(
                ((j['subscription_ends'] as num).toDouble() * 1000).round(),
                isUtc: true);

  const Account.anonymous()
      : identifier = '',
        username = null,
        emailVerified = true,
        tier = 'free',
        entitled = false,
        operator = false,
        subscriptionEnds = null;

  final String identifier, tier;
  final bool entitled;

  /// Chosen at sign-up. Null on accounts older than the field, which show
  /// the email where a name would go.
  final String? username;

  /// False only while a confirmation link is outstanding. Defaults true so
  /// a server that predates the field reads as "nothing to confirm".
  final bool emailVerified;

  /// What to call this person on screen.
  String get displayName => username ?? identifier;

  /// Signed in with the shared operator key rather than an account. Full
  /// access, no username to display.
  final bool operator;

  final DateTime? subscriptionEnds;

  bool get signedIn => identifier.isNotEmpty || operator;
}

/// One indicator's recent history, for the tile that expands into it.
class IndicatorSeries {
  IndicatorSeries.fromJson(Map<String, dynamic> j)
      : key = j['key'] as String? ?? '',
        label = j['label'] as String? ?? '',
        unit = j['unit'] as String? ?? '',
        explain = j['explain'] as String? ?? '',
        note = j['note'] as String? ?? '',
        min = (j['min'] as num?)?.toDouble(),
        max = (j['max'] as num?)?.toDouble(),
        values = ((j['points'] as List?) ?? const [])
            .map((p) => (p as Map<String, dynamic>)['v'] as num?)
            .map((v) => v?.toDouble())
            .toList();

  final String key, label, unit, explain, note;

  /// Fixed bounds where the indicator has them (RSI is always 0-100), so the
  /// shape is comparable between visits rather than rescaling to whatever
  /// happens to be on screen. Null means "scale to the data".
  final double? min, max;

  /// Nulls are kept, not dropped: a gap in an indicator is information, and
  /// closing it would draw a line through data that does not exist.
  final List<double?> values;

  bool get isEmpty => values.where((v) => v != null).length < 2;
}

// --------------------------------------------------------------- screener
//
// The app deliberately hardcodes NONE of the screener. Fields, operators and
// presets all arrive from `/api/screener/catalogue`, so adding a filter is a
// server change and an old build keeps working rather than showing a control
// that does nothing.

class ScreenerField {
  ScreenerField.fromJson(Map<String, dynamic> j)
      : id = j['id'] as String,
        label = j['label'] as String? ?? j['id'] as String,
        group = j['group'] as String? ?? '',
        kind = j['kind'] as String? ?? 'number',
        needs = j['needs'] as String? ?? 'price',
        help = j['help'] as String? ?? '';

  final String id, label, group, kind, needs, help;

  /// Nothing on the free data stack can fill this in.
  ///
  /// Shown greyed with the reason rather than hidden: a preset that names a
  /// criterion the server cannot judge must say so, or the results look like
  /// they honoured it.
  bool get unavailable => needs == 'estimates';

  /// How to render a raw number for this field.
  String format(num? v) {
    if (v == null) return '—';
    switch (kind) {
      case 'percent':
        return '${v.toStringAsFixed(v.abs() < 10 ? 2 : 1)}%';
      case 'currency':
        return _compactMoney(v);
      case 'shares':
        return compact(v);
      case 'ratio':
        return v.toStringAsFixed(2);
      case 'bool':
        return v == 0 ? 'no' : 'yes';
      default:
        return v.abs() >= 1000 ? compact(v) : v.toStringAsFixed(2);
    }
  }

  static String compact(num v) {
    final a = v.abs();
    if (a >= 1e12) return '${(v / 1e12).toStringAsFixed(2)}T';
    if (a >= 1e9) return '${(v / 1e9).toStringAsFixed(2)}B';
    if (a >= 1e6) return '${(v / 1e6).toStringAsFixed(2)}M';
    if (a >= 1e3) return '${(v / 1e3).toStringAsFixed(1)}K';
    return v.toStringAsFixed(2);
  }

  static String _compactMoney(num v) => '\$${compact(v)}';
}

class ScreenerFilter {
  ScreenerFilter({required this.field, required this.op, this.value,
    this.value2});

  ScreenerFilter.fromJson(Map<String, dynamic> j)
      : field = j['field'] as String,
        op = j['op'] as String? ?? 'gt',
        value = _d(j['value']),
        value2 = _d(j['value2']);

  final String field;
  String op;
  double? value;
  double? value2;

  Map<String, dynamic> toJson() => {
        'field': field,
        'op': op,
        if (value != null) 'value': value,
        if (value2 != null) 'value2': value2,
      };

  ScreenerFilter copy() =>
      ScreenerFilter(field: field, op: op, value: value, value2: value2);

  /// Reads as a sentence, because a row of dropdowns does not.
  String describe(ScreenerField? f) {
    final label = f?.label ?? field;
    switch (op) {
      case 'is_true':
        return field.startsWith('above_') ? '$label — above' : '$label — yes';
      case 'is_false':
        return field.startsWith('above_') ? '$label — below' : '$label — no';
      case 'between':
        return '$label ${f?.format(value)} to ${f?.format(value2)}';
      case 'lt':
        return '$label under ${f?.format(value)}';
      case 'lte':
        return '$label at most ${f?.format(value)}';
      case 'gte':
        return '$label at least ${f?.format(value)}';
      default:
        return '$label over ${f?.format(value)}';
    }
  }
}

class ScreenerPreset {
  ScreenerPreset.fromJson(Map<String, dynamic> j)
      : id = j['id'] as String,
        name = j['name'] as String,
        note = j['note'] as String? ?? '',
        filters = ((j['filters'] as List?) ?? const [])
            .map((e) => ScreenerFilter.fromJson(e as Map<String, dynamic>))
            .toList(),
        dropped = ((j['dropped'] as List?) ?? const [])
            .map((e) => ScreenerFilter.fromJson(e as Map<String, dynamic>))
            .toList(),
        unavailable = ((j['unavailable'] as List?) ?? const [])
            .map((e) => '$e')
            .toList();

  final String id, name, note;

  /// The filters this preset expands into. Copied on use, never shared —
  /// editing a preset's numbers must not rewrite the preset itself.
  final List<ScreenerFilter> filters;

  /// Criteria left OUT because nothing can judge them.
  ///
  /// Shipped separately from `filters` rather than silently omitted: the
  /// screen runs without them, and the page says which ones it dropped. A
  /// preset that quietly ignored a criterion you asked for would be worse
  /// than one that returned nothing.
  final List<ScreenerFilter> dropped;

  /// Which of its criteria the server cannot judge on the free data stack.
  final List<String> unavailable;

  List<ScreenerFilter> instantiate() =>
      filters.map((f) => f.copy()).toList();
}

class ScreenerCatalogue {
  ScreenerCatalogue.fromJson(Map<String, dynamic> j)
      : market = j['market'] as String? ?? 'stocks',
        fields = ((j['fields'] as List?) ?? const [])
            .map((e) => ScreenerField.fromJson(e as Map<String, dynamic>))
            .toList(),
        operators =
            ((j['operators'] as List?) ?? const []).map((e) => '$e').toList(),
        presets = ((j['presets'] as List?) ?? const [])
            .map((e) => ScreenerPreset.fromJson(e as Map<String, dynamic>))
            .toList(),
        unavailable = ((j['unavailable'] as List?) ?? const [])
            .map((e) => '$e')
            .toList(),
        builtAt = j['built_at'] == null
            ? null
            : DateTime.tryParse(j['built_at'] as String),
        symbols = (j['symbols'] as num?)?.toInt() ?? 0;

  /// stocks or crypto. Two completely different field sets — a perpetual has
  /// no earnings and a stock has no funding rate.
  final String market;
  final List<ScreenerField> fields;
  final List<String> operators;
  final List<ScreenerPreset> presets;
  final List<String> unavailable;

  /// When the table was last rebuilt. Shown, because an overnight snapshot
  /// presented as live is the kind of thing someone sizes a position on.
  final DateTime? builtAt;
  final int symbols;

  Map<String, ScreenerField> get byId =>
      {for (final f in fields) f.id: f};
}

class ScreenerRow {
  ScreenerRow.fromJson(Map<String, dynamic> j)
      : symbol = j['symbol'] as String,
        metrics = Map<String, dynamic>.from(
            (j['metrics'] as Map?) ?? const {}),
        passed = ((j['passed'] as List?) ?? const []).map((e) => '$e').toList(),
        failed = ((j['failed'] as List?) ?? const []).map((e) => '$e').toList(),
        unknown =
            ((j['unknown'] as List?) ?? const []).map((e) => '$e').toList();

  final String symbol;
  final Map<String, dynamic> metrics;
  final List<String> passed, failed, unknown;

  /// The company. A screener row reading only "CLMT" tells you nothing about
  /// what you just matched.
  String get name => (metrics['name'] as String?) ?? '';

  double? metric(String id) {
    final v = metrics[id];
    return v is num ? v.toDouble() : null;
  }

  /// Which timeframes this app has a fitted model for. Crypto rows carry it;
  /// equity rows do not yet, so an empty list means "not known", not "no".
  List<String> get trainedIntervals =>
      ((metrics['trained_intervals'] as List?) ?? const [])
          .map((e) => '$e')
          .toList();

  /// The short ticker for the medallion. BTCUSDT reads as BTC.
  String get short => symbol.endsWith('USDT')
      ? symbol.substring(0, symbol.length - 4)
      : symbol;

  String get yahooUrl => 'https://finance.yahoo.com/quote/$symbol';
}

class ScreenerResult {
  ScreenerResult.fromJson(Map<String, dynamic> j)
      : matched = (j['matched'] as num?)?.toInt() ?? 0,
        unjudged = (j['unjudged'] as num?)?.toInt() ?? 0,
        scanned = (j['scanned'] as num?)?.toInt() ?? 0,
        rows = ((j['rows'] as List?) ?? const [])
            .map((e) => ScreenerRow.fromJson(e as Map<String, dynamic>))
            .toList(),
        builtAt = j['built_at'] == null
            ? null
            : DateTime.tryParse(j['built_at'] as String);

  final int matched;

  /// Passed every criterion that could be judged, but not every criterion.
  /// Counted separately so the page never implies these were rejected.
  final int unjudged;
  final int scanned;
  final List<ScreenerRow> rows;
  final DateTime? builtAt;
}

/// One stock's page: chart, the screener's own metrics, and the honest gap
/// where a signal would be.
///
/// The metrics are the SAME row the screen matched on, not a recomputation —
/// a detail page that disagreed with the list it came from would destroy
/// trust in both.
class StockDetail {
  StockDetail.fromJson(Map<String, dynamic> j)
      : symbol = j['symbol'] as String,
        known = j['known'] as bool? ?? false,
        metrics = Map<String, dynamic>.from((j['metrics'] as Map?) ?? const {}),
        series = ((j['series'] as List?) ?? const [])
            .map((e) => (e as num).toDouble())
            .toList(),
        trained = j['trained'] as bool? ?? false,
        recommendation = j['recommendation'] == null
            ? null
            : Recommendation.fromJson(
                j['recommendation'] as Map<String, dynamic>),
        takeProfit = _d((j['levels'] as Map?)?['take_profit']),
        stopLoss = _d((j['levels'] as Map?)?['stop_loss']),
        anchor = _d((j['levels'] as Map?)?['anchor']),
        side = (j['levels'] as Map?)?['side'] as String? ?? '',
        windowPUp = _d((j['levels'] as Map?)?['p_up']),
        windowBars = ((j['levels'] as Map?)?['window_bars'] as num?)?.toInt(),
        indicators = ((j['indicators'] as List?) ?? const [])
            .map((e) => Indicator.fromJson(e as Map<String, dynamic>))
            .toList(),
        untrainedNote = j['untrained_note'] as String? ?? '',
        trainCommand = j['train_command'] as String? ?? '',
        interval = j['interval'] as String? ?? '1d',
        intervals = ((j['intervals'] as List?) ?? const ['1d'])
            .map((e) => '$e')
            .toList(),
        builtAt = j['built_at'] == null
            ? null
            : DateTime.tryParse(j['built_at'] as String);

  final String symbol;

  /// False when the ticker is not in the table at all — different from being
  /// present with blank fields.
  final bool known;
  final Map<String, dynamic> metrics;
  final List<double> series;
  /// Whether a model exists for this symbol AND this interval.
  ///
  /// Per interval, not per symbol: a stock with a daily model and no hourly
  /// one is trained for one and untrained for the other, exactly as a crypto
  /// pair is.
  final bool trained;

  /// The fitted model's call — the same object the crypto dashboard draws,
  /// produced by the same code. Null when nothing is fitted yet.
  final Recommendation? recommendation;

  /// Flattened the same way `Dashboard` flattens them, rather than wrapped in
  /// a class of their own — two shapes for the same three numbers is how the
  /// two pages start disagreeing.
  final double? takeProfit, stopLoss, anchor;

  /// LONG, SHORT or "" — the same field the crypto dashboard carries, so the
  /// levels panel can say which side its take-profit belongs to.
  final String side;
  final double? windowPUp;
  final int? windowBars;
  final List<Indicator> indicators;

  /// Why there is no call, and the exact command that would fix it.
  final String untrainedNote, trainCommand;

  /// Which timeframe the series is on, and which are offered. Sent by the
  /// server so a new one appears without shipping an APK.
  final String interval;
  final List<String> intervals;
  final DateTime? builtAt;

  double? metric(String id) {
    final v = metrics[id];
    return v is num ? v.toDouble() : null;
  }

  String get yahooUrl => 'https://finance.yahoo.com/quote/$symbol';
}

class StockQuote {
  StockQuote.fromJson(Map<String, dynamic> j)
      : symbol = j['symbol'] as String,
        known = j['known'] as bool? ?? false,
        name = j['name'] as String? ?? '',
        price = _d(j['price']),
        changePct = _d(j['change_pct']),
        rsi14 = _d(j['rsi14']),
        marketCap = _d(j['market_cap']),
        relVolume = _d(j['rel_volume']),
        trained = ((j['trained'] as List?) ?? const [])
            .map((e) => '$e')
            .toList(),
        session = j['session'] as String? ?? '',
        sessionLabel = j['session_label'] as String? ?? '',
        extendedPrice = _d(j['extended_price']),
        extendedChangePct = _d(j['extended_change_pct']);

  final String symbol;
  final bool known;

  /// The company, not the ticker. "CLMT" tells you nothing.
  final String name;
  final double? price, changePct, rsi14, marketCap, relVolume;

  /// Which timeframes have a fitted model. Empty means no call exists for
  /// this stock at any timeframe — shown, not implied.
  final List<String> trained;

  /// open | pre | post | closed, and a label for it. Crypto never needed
  /// this; a stock price with no session attached is Friday's close being
  /// read on a Tuesday.
  final String session, sessionLabel;

  /// Where it is trading outside the regular session. Kept SEPARATE from
  /// `price`, never merged: "the close" and "trading now" are different
  /// numbers and conflating them is how a stale figure gets acted on.
  final double? extendedPrice, extendedChangePct;

  bool get isExtended => extendedPrice != null;
}

/// What happened over long horizons — counts, not forecasts.
///
/// The distinction is the whole reason this type exists separately from
/// `Recommendation`. That one is a fitted model's output. This is arithmetic
/// over history, at horizons where no model can be fitted because the
/// independent observations do not exist: about six one-year windows have
/// occurred in the entire history of the instrument.
class HorizonRow {
  HorizonRow.fromJson(Map<String, dynamic> j)
      : horizon = j['horizon'] as String,
        n = (j['n'] as num?)?.toInt() ?? 0,
        upRate = _d(j['up_rate']),
        ciLow = _d(j['ci_low']),
        ciHigh = _d(j['ci_high']),
        medianPct = _d(j['median_pct']),
        worstPct = _d(j['worst_pct']),
        bestPct = _d(j['best_pct']),
        medianDrawdownPct = _d(j['median_drawdown_pct']),
        worstDrawdownPct = _d(j['worst_drawdown_pct']),
        meaningful = j['meaningful'] as bool? ?? false,
        note = j['note'] as String? ?? '';

  final String horizon;

  /// How many NON-OVERLAPPING windows this is computed from. Shown as
  /// prominently as the rate itself: six observations and eighty-one are
  /// different kinds of statement.
  final int n;
  final double? upRate, ciLow, ciHigh, medianPct, worstPct, bestPct;

  /// How far underwater a position went before the horizon was reached. For
  /// anyone actually holding, this matters more than the outcome.
  final double? medianDrawdownPct, worstDrawdownPct;
  final bool meaningful;
  final String note;

  /// The interval is wide enough to contain a coin flip, so the rate cannot
  /// be read as a tendency in either direction.
  bool get spansEven =>
      ciLow != null && ciHigh != null && ciLow! <= 50 && ciHigh! >= 50;
}

class HorizonReport {
  HorizonReport.fromJson(Map<String, dynamic> j)
      : symbol = j['symbol'] as String,
        historyYears = _d(j['history_years']) ?? 0,
        disclaimer = j['disclaimer'] as String? ?? '',
        rows = ((j['horizons'] as List?) ?? const [])
            .map((e) => HorizonRow.fromJson(e as Map<String, dynamic>))
            .toList();

  final String symbol;
  final double historyYears;
  final String disclaimer;
  final List<HorizonRow> rows;
}

/// A queued or completed model fit, triggered from the app.
class TrainJob {
  TrainJob.fromJson(Map<String, dynamic> j)
      : id = j['id'] as String,
        symbol = j['symbol'] as String,
        interval = j['interval'] as String,
        state = j['state'] as String? ?? 'queued',
        note = j['note'] as String? ?? '',
        atChance = j['at_chance'] as bool? ?? false,
        elapsed = (j['elapsed'] as num?)?.toInt(),
        results = ((j['results'] as List?) ?? const [])
            .map((e) => Map<String, dynamic>.from(e as Map))
            .toList();

  final String id, symbol, interval, state, note;

  /// The pipeline's own verdict, carried through rather than left in a log.
  /// A fit that finds nothing is the most useful thing it can report.
  final bool atChance;
  final int? elapsed;
  final List<Map<String, dynamic>> results;

  bool get running => state == 'running';
  bool get queued => state == 'queued';
  bool get done => state == 'done';
  bool get failed => state == 'failed';

  /// The best AUC across horizons, for a one-line summary.
  double? get bestAuc {
    double? best;
    for (final r in results) {
      final a = (r['auc'] as num?)?.toDouble();
      if (a != null && (best == null || a > best)) best = a;
    }
    return best;
  }
}

class TrainStatus {
  TrainStatus.fromJson(Map<String, dynamic> j)
      : current = j['current'] == null
            ? null
            : TrainJob.fromJson(j['current'] as Map<String, dynamic>),
        queued = ((j['queued'] as List?) ?? const [])
            .map((e) => TrainJob.fromJson(e as Map<String, dynamic>))
            .toList(),
        finished = ((j['finished'] as List?) ?? const [])
            .map((e) => TrainJob.fromJson(e as Map<String, dynamic>))
            .toList(),
        minutesPerFit = (j['minutes_per_fit'] as num?)?.toInt() ?? 40;

  final TrainJob? current;
  final List<TrainJob> queued, finished;

  /// So the app can say "about eighty minutes" instead of "soon".
  final int minutesPerFit;

  TrainJob? jobFor(String symbol, String interval) {
    for (final j in [?current, ...queued, ...finished]) {
      if (j.symbol == symbol && j.interval == interval) return j;
    }
    return null;
  }
}

/// One recommended setup: a preset, how many match it right now, and the
/// leading few.
///
/// Fetched for every preset in ONE request — the carousel needs a count on
/// every pill, and asking per preset would be seven round trips to draw one
/// row of chips.
class ScreenerSetup {
  ScreenerSetup.fromJson(Map<String, dynamic> j)
      : id = j['id'] as String,
        name = j['name'] as String,
        note = j['note'] as String? ?? '',
        count = (j['count'] as num?)?.toInt() ?? 0,
        filters = ((j['filters'] as List?) ?? const [])
            .map((e) => ScreenerFilter.fromJson(e as Map<String, dynamic>))
            .toList(),
        dropped = ((j['dropped'] as List?) ?? const [])
            .map((e) => ScreenerFilter.fromJson(e as Map<String, dynamic>))
            .toList(),
        rows = ((j['rows'] as List?) ?? const [])
            .map((e) => ScreenerRow.fromJson(e as Map<String, dynamic>))
            .toList();

  final String id, name, note;

  /// How many match today. Shown on the pill — a strategy finding nothing is
  /// still listed, because "nothing qualifies right now" is an answer.
  final int count;
  final List<ScreenerFilter> filters, dropped;

  /// The leading matches, for the carousel cards.
  final List<ScreenerRow> rows;

  List<ScreenerFilter> instantiate() =>
      filters.map((f) => f.copy()).toList();
}


/// One live, non-FLAT call: which pair, which timeframe, how strong.
///
/// WHY THE TIMEFRAME IS PART OF THE IDENTITY
///     A call is not a property of a coin, it is a property of a coin AND a
///     horizon. SUI can be SELL on 1h and FLAT on 1d at the same moment, and
///     both are correct answers to different questions. So the row carries
///     its interval, and tapping it opens the dashboard on THAT interval —
///     landing on whatever timeframe you last looked at would show you a
///     different call from the one you tapped.
class LiveSignal {
  LiveSignal.fromJson(Map<String, dynamic> j)
      : symbol = j['symbol'] as String,
        interval = j['interval'] as String,
        action = j['action'] as String,
        strength = j['strength'] as String? ?? '',
        detail = j['detail'] as String? ?? '',
        side = j['side'] as String? ?? '',
        ev = _d(j['ev']),
        price = _d(j['price']),
        changePct = _d(j['change_pct']),
        pUp = _d(j['p_up']),
        takeProfit = _d(j['take_profit']),
        stopLoss = _d(j['stop_loss']),
        entryLimit = _d(j['entry_limit']),
        order = j['order'] is Map
            ? EntryOrder.fromJson((j['order'] as Map).cast<String, dynamic>())
            : null;

  final String symbol, interval, action, strength, detail, side;
  final double? ev, price, changePct, pUp, takeProfit, stopLoss;

  /// The resting order's price, when the call enters with one.
  final double? entryLimit;
  final EntryOrder? order;

  bool get isSell => action == 'SELL';

  /// True for a symbol the equity side serves. Crypto pairs are quoted in
  /// USDT; nothing on the US equity list is.
  bool get isEquity => !symbol.endsWith('USDT');

  String get short => symbol.endsWith('USDT')
      ? symbol.substring(0, symbol.length - 4)
      : symbol;

  /// STRONG / MEDIUM / LOW as the app words it.
  ///
  /// The server's third level is called `small`, which is accurate inside the
  /// model — "the smallest margin that still clears costs" — and reads as a
  /// typo on a list. LOW is the same thing said out loud.
  String get strengthLabel => switch (strength) {
        'strong' => 'STRONG',
        'medium' => 'MEDIUM',
        'small' => 'LOW',
        _ => 'UNGRADED',
      };
}

class LiveSignals {
  LiveSignals.fromJson(Map<String, dynamic> j)
      : signals = ((j['signals'] as List?) ?? const [])
            .map((e) => LiveSignal.fromJson(e as Map<String, dynamic>))
            .toList(),
        watched = (j['watched_symbols'] as num?)?.toInt() ?? 0,
        intervals = ((j['intervals'] as List?) ?? const [])
            .map((e) => '$e')
            .toList();

  final List<LiveSignal> signals;

  /// The current call on one pair, or null when the server has none. The
  /// list only carries pairs with a live non-FLAT call, so absence means
  /// FLAT (or STALE, or gated) -- "no view", either way.
  String? callFor(String symbol, String interval) {
    for (final s in signals) {
      if (s.symbol == symbol && s.interval == interval) return s.action;
    }
    return null;
  }

  /// How many pairs the server actually had a current reading for. Shown so
  /// an empty list reads as "nothing is calling right now" rather than
  /// "something is broken".
  final int watched;
  final List<String> intervals;
}

/// The followed traders: who holds this coin, and what they just did.
///
/// INFORMATION, NOT A CALL. The server says so in `note` and the panel
/// repeats it. A leaderboard selects on past profit; whether following pays
/// is being measured, and until it is, nothing here is a recommendation.
class SmartMoney {
  SmartMoney.fromJson(Map<String, dynamic> j)
      : available = j['available'] as bool? ?? false,
        tracked = (j['tracked'] as num?)?.toInt() ?? 0,
        note = j['note'] as String? ?? '',
        polledAt = _t(j['polled_at']),
        consensus = j['consensus'] == null
            ? null
            : SmartConsensus.fromJson(j['consensus'] as Map<String, dynamic>),
        events = ((j['events'] as List?) ?? const [])
            .map((e) => SmartEvent.fromJson(e as Map<String, dynamic>))
            .toList(),
        traders = ((j['traders'] as List?) ?? const [])
            .map((e) => SmartTrader.fromJson(e as Map<String, dynamic>))
            .toList();

  final bool available;
  final int tracked;
  final String note;
  final DateTime? polledAt;
  final SmartConsensus? consensus;
  final List<SmartEvent> events;
  final List<SmartTrader> traders;

  static DateTime? _t(dynamic v) =>
      v == null ? null : DateTime.tryParse(v as String)?.toUtc();
}

class SmartConsensus {
  SmartConsensus.fromJson(Map<String, dynamic> j)
      : symbol = j['symbol'] as String? ?? '',
        tracked = (j['tracked'] as num?)?.toInt() ?? 0,
        long = (j['long'] as num?)?.toInt() ?? 0,
        short = (j['short'] as num?)?.toInt() ?? 0,
        longNotional = _d(j['long_notional']) ?? 0,
        shortNotional = _d(j['short_notional']) ?? 0,
        holders = ((j['holders'] as List?) ?? const [])
            .map((e) => SmartHolder.fromJson(e as Map<String, dynamic>))
            .toList();

  final String symbol;
  final int tracked, long, short;
  final double longNotional, shortNotional;
  final List<SmartHolder> holders;

  /// Share of the followed who hold this coin at all.
  int get holding => long + short;
}

class SmartHolder {
  SmartHolder.fromJson(Map<String, dynamic> j)
      : address = j['address'] as String? ?? '',
        short = j['short'] as String? ?? '',
        name = j['name'] as String? ?? '',
        side = j['side'] as String? ?? '',
        notional = _d(j['notional']) ?? 0,
        entry = _d(j['entry']),
        leverage = _d(j['leverage']),
        upnl = _d(j['upnl']),
        winRate = _d(j['win_rate']),
        pnl30d = _d(j['pnl_30d']);

  final String address, short, name, side;
  final double notional;
  final double? entry, leverage, upnl, winRate, pnl30d;

  String get who => name.isNotEmpty ? name : short;
  bool get isLong => side == 'LONG';
}

class SmartEvent {
  SmartEvent.fromJson(Map<String, dynamic> j)
      : id = j['id'] as String? ?? '',
        kind = j['kind'] as String? ?? '',
        address = j['address'] as String? ?? '',
        coin = j['coin'] as String? ?? '',
        symbol = j['symbol'] as String? ?? '',
        side = j['side'] as String? ?? '',
        notional = _d(j['notional']) ?? 0,
        entry = _d(j['entry']),
        leverage = _d(j['leverage']),
        priceAt = _d(j['price_at']),
        at = DateTime.tryParse(j['at'] as String? ?? '')?.toUtc() ??
            DateTime.fromMillisecondsSinceEpoch(0, isUtc: true),
        trader = Map<String, dynamic>.from((j['trader'] as Map?) ?? const {});

  final String id, kind, address, coin, symbol, side;
  final double notional;
  final double? entry, leverage, priceAt;
  final DateTime at;
  final Map<String, dynamic> trader;

  String get who {
    final n = (trader['display_name'] as String?)?.trim() ?? '';
    if (n.isNotEmpty) return n;
    final a = address.toLowerCase();
    return a.length >= 12 ? '${a.substring(0, 6)}…${a.substring(a.length - 4)}' : a;
  }

  double? get winRate => _d(trader['win_rate']);
  bool get isLong => side == 'LONG';
}

class SmartTrader {
  SmartTrader.fromJson(Map<String, dynamic> j)
      : address = j['address'] as String? ?? '',
        short = j['short'] as String? ?? '',
        name = j['name'] as String? ?? '',
        score = _d(j['score']) ?? 0,
        winRate = _d(j['win_rate']) ?? 0,
        closedTrades = (j['closed_trades'] as num?)?.toInt() ?? 0,
        profitFactor = _d(j['profit_factor']) ?? 0,
        pnl30d = _d(j['pnl_30d']) ?? 0,
        accountValue = _d(j['account_value']) ?? 0,
        weeksPositive = (j['weeks_positive'] as num?)?.toInt() ?? 0,
        weeksCovered = (j['weeks_covered'] as num?)?.toInt() ?? 0,
        open = ((j['open'] as List?) ?? const []).map((e) => '$e').toList();

  final String address, short, name;
  final double score, winRate, profitFactor, pnl30d, accountValue;
  final int closedTrades, weeksPositive, weeksCovered;
  final List<String> open;

  String get who => name.isNotEmpty ? name : short;
}


/// One coin in the weekly momentum rotation.
class MomentumPick {
  MomentumPick.fromJson(Map<String, dynamic> j)
      : symbol = j['symbol'] as String,
        // `ret_pct` over `lookback_days`; `ret_30d` is what older servers sent
        ret30d = _d(j['ret_pct']) ?? _d(j['ret_30d']) ?? 0,
        weekPct = _d(j['week_pct']),
        rank = (j['rank'] as num?)?.toInt() ?? 0,
        followed = j['followed'] as bool? ?? true;

  final String symbol;

  /// The return the pick was ranked on, over the rotation's lookback.
  final double ret30d;

  /// Whether this app follows the coin -- only those have a chart to open.
  /// The rotation ranks the most-traded perpetuals, most of which it does not.
  final bool followed;

  /// The coin's own move since this week's Monday close, in % (not signed
  /// by the side it is held on). Null when there is no price yet.
  final double? weekPct;
  final int rank;

  String get short => symbol.endsWith('USDT')
      ? symbol.substring(0, symbol.length - 4)
      : symbol;
}

/// The weekly momentum rotation (api/momentum.py): long the best returns
/// among the most-traded perpetuals, short the worst, Monday to Monday.
class Momentum {
  Momentum.fromJson(Map<String, dynamic> j)
      : available = j['available'] as bool? ?? false,
        weekStart = DateTime.tryParse(j['week_start'] as String? ?? '')?.toUtc(),
        nextRebalance =
            DateTime.tryParse(j['next_rebalance'] as String? ?? '')?.toUtc(),
        universe = (j['universe'] as num?)?.toInt() ?? 0,
        universeRule = j['universe_rule'] as String? ?? '',
        signal = j['signal'] as String? ?? '',
        lookbackDays = (j['lookback_days'] as num?)?.toInt() ?? 30,
        weekPct = _d(j['week_pct']),
        measured = j['measured'] as String? ?? '',
        longs = _picks(j['longs']),
        shorts = _picks(j['shorts']),
        liveRanking = _picks(j['live_ranking']);

  final bool available;
  final DateTime? weekStart, nextRebalance;
  final int universe, lookbackDays;

  /// Which coins were ranked, in words ("the 30 most-traded Binance
  /// perpetuals"); empty from older servers.
  final String universeRule;

  /// What they were ranked on, in words; empty from older servers.
  final String signal;

  /// This week so far for the pair as measured: half the capital long the
  /// three, half short the other three.
  final double? weekPct;
  final String measured;
  final List<MomentumPick> longs, shorts, liveRanking;

  static List<MomentumPick> _picks(Object? v) => (v is List ? v : const [])
      .map((e) => MomentumPick.fromJson((e as Map).cast<String, dynamic>()))
      .toList();
}

/// The live record of the 4h calls (`GET /api/record`, api/ledger.py): every
/// resting order the server placed since the model went live, per
/// sensitivity level, and how the filled ones ended -- next to what the
/// walk-forward expected and what independent studies measured elsewhere.
class LiveRecord {
  LiveRecord.fromJson(Map<String, dynamic> j)
      : since = DateTime.tryParse(j['since'] as String? ?? '')?.toUtc(),
        costPct = _d(j['cost_pct']) ?? 0.10,
        levels = {
          for (final e in ((j['levels'] as Map?) ?? const {}).entries)
            e.key as String:
                RecordLevel.fromJson((e.value as Map).cast<String, dynamic>())
        },
        recent = {
          for (final e in ((j['recent'] as Map?) ?? const {}).entries)
            e.key as String: ((e.value as List?) ?? const [])
                .map((x) =>
                    RecordTrade.fromJson((x as Map).cast<String, dynamic>()))
                .toList()
        },
        expected = {
          for (final e
              in ((((j['expected'] as Map?) ?? const {})['levels'] as Map?) ??
                      const {})
                  .entries)
            e.key as String: RecordExpected.fromJson(
                (e.value as Map).cast<String, dynamic>())
        },
        expectedNote =
            ((j['expected'] as Map?) ?? const {})['note'] as String? ?? '',
        benchmarks = ((j['benchmarks'] as List?) ?? const [])
            .map((x) => Benchmark.fromJson((x as Map).cast<String, dynamic>()))
            .toList();

  final DateTime? since;
  final double costPct;
  final Map<String, RecordLevel> levels;
  final Map<String, List<RecordTrade>> recent;
  final Map<String, RecordExpected> expected;
  final String expectedNote;
  final List<Benchmark> benchmarks;
}

class RecordLevel {
  RecordLevel.fromJson(Map<String, dynamic> j)
      : orders = (j['orders'] as num?)?.toInt() ?? 0,
        filled = (j['filled'] as num?)?.toInt() ?? 0,
        expired = (j['expired'] as num?)?.toInt() ?? 0,
        openOrders = (j['open_orders'] as num?)?.toInt() ?? 0,
        inTrade = (j['in_trade'] as num?)?.toInt() ?? 0,
        closed = (j['closed'] as num?)?.toInt() ?? 0,
        targets = (j['targets'] as num?)?.toInt() ?? 0,
        stops = (j['stops'] as num?)?.toInt() ?? 0,
        backToEntry = (j['back_to_entry'] as num?)?.toInt() ?? 0,
        timeouts = (j['timeouts'] as num?)?.toInt() ?? 0,
        wins = (j['wins'] as num?)?.toInt() ?? 0,
        winRate = _d(j['win_rate']),
        avgNetPct = _d(j['avg_net_pct']),
        sumNetPct = _d(j['sum_net_pct']) ?? 0.0;

  final int orders, filled, expired, openOrders, inTrade, closed;
  final int targets, stops, timeouts, wins;

  /// Trades that took their part off halfway and then came back to the
  /// entry: wins overall, not stops.
  final int backToEntry;
  final double? winRate, avgNetPct;
  final double sumNetPct;
}

class RecordTrade {
  RecordTrade.fromJson(Map<String, dynamic> j)
      : symbol = j['symbol'] as String? ?? '',
        side = j['side'] as String? ?? '',
        state = j['state'] as String? ?? '',
        fill = _d(j['fill']),
        exit = _d(j['exit']),
        netPct = _d(j['net_pct']),
        taken = j['taken'] as bool? ?? false,
        closedAt = DateTime.tryParse(j['closed_at'] as String? ?? '')?.toUtc();

  final String symbol, side, state;

  /// The third came off halfway (a stop after that is the entry).
  final bool taken;
  final double? fill, exit, netPct;
  final DateTime? closedAt;

  String get short => symbol.replaceAll('USDT', '');
}

class RecordExpected {
  RecordExpected.fromJson(Map<String, dynamic> j)
      : winRate = _d(j['win_rate']),
        avgNetPct = _d(j['avg_net_pct']),
        holdWinRate = _d(j['hold_win_rate']),
        holdAvgNetPct = _d(j['hold_avg_net_pct']),
        tradesPerMonth = (j['trades_per_month'] as num?)?.toInt();

  final double? winRate, avgNetPct, holdWinRate, holdAvgNetPct;
  final int? tradesPerMonth;
}

/// What an independent study measured for other bots or signal sellers.
class Benchmark {
  Benchmark.fromJson(Map<String, dynamic> j)
      : what = j['what'] as String? ?? '',
        figure = j['figure'] as String? ?? '',
        source = j['source'] as String? ?? '';

  final String what, figure, source;
}
