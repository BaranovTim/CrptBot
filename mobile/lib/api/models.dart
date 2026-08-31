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
        volume24h = _d(j['volume_24h']);

  final String symbol, base, quote;
  final double? volume24h;
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
        pNeeded = _d(j['p_needed']),
        sizePct = _d(j['size_pct']);

  final String action;
  final String? tone;
  final String detail;
  final double? ev, sizePct;

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
        calibrationNote = j['calibration_note'] as String? ?? '';

  final String symbol, pair, interval, htf, calibrationNote;
  final List<TimeframeInfo> timeframes;
  final bool stale;
  final double? price, changePct, takeProfit, stopLoss;

  /// The close the model measured its barriers from, and the barrier
  /// DISTANCES. The distances are what the model fixed at that close; the
  /// price they are measured from is not, which is why the app can re-anchor
  /// them to a live price without touching the model's claim.
  final double? anchor, tpPct, slPct;

  /// The window the recommendation and these levels BOTH describe.
  ///
  /// Not always the longer horizon: an entering window wins over a waiting
  /// one, so a BUY from the 1-bar model brings its own probability and its
  /// own barriers. Reading them off different analyses put "BUY" next to a
  /// 51.7% that belonged to a different question.
  final int? windowBars;
  final double? windowPUp;

  /// Where TP/SL sit for an entry at `live`, rather than at the bar close.
  double? liveTakeProfit(double? live) =>
      (live == null || tpPct == null) ? takeProfit : live * (1 + tpPct! / 100);

  double? liveStopLoss(double? live) =>
      (live == null || slPct == null) ? stopLoss : live * (1 - slPct! / 100);
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
        publishedAt = j['published_at'] == null
            ? null
            : DateTime.parse(j['published_at'] as String);

  final String headline, source, url;
  final DateTime? publishedAt;

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
        url = j['url'] as String? ?? '',
        seq = (j['seq'] as num?)?.toInt() ?? 0,
        at = DateTime.parse(j['at'] as String),
        detectedAt = DateTime.parse(j['detected_at'] as String),
        extra = Map<String, String>.from(
            (j['extra'] as Map?)?.map((k, v) => MapEntry('$k', '$v')) ?? {});

  final String id, kind, title, body, severity, symbol, url;

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
        tier = 'free',
        entitled = false,
        operator = false,
        subscriptionEnds = null;

  final String identifier, tier;
  final bool entitled;

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
