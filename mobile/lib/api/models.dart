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
        trained = j['trained'] as bool;

  final String symbol, name, short, pair;
  final double? price, changePct;
  final bool trained;
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
        sizePct = _d(j['size_pct']);

  final String action;
  final String? tone;
  final String detail;
  final double? ev, sizePct;
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
        tpPct = _d((j['levels'] as Map)['tp_pct']),
        slPct = _d((j['levels'] as Map)['sl_pct']),
        calibrationNote = j['calibration_note'] as String? ?? '';

  final String symbol, pair, interval, calibrationNote;
  final bool stale;
  final double? price, changePct, takeProfit, stopLoss;

  /// The close the model measured its barriers from, and the barrier
  /// DISTANCES. The distances are what the model fixed at that close; the
  /// price they are measured from is not, which is why the app can re-anchor
  /// them to a live price without touching the model's claim.
  final double? anchor, tpPct, slPct;

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
        conviction = _d(j['conviction']);

  final String describe, impact, side, code, note;
  final bool mechanical;
  final double? conviction;
}

class TrainingInfo {
  TrainingInfo.fromJson(Map<String, dynamic> j)
      : symbol = j['symbol'] as String,
        trained = j['trained'] as bool,
        title = j['title'] as String,
        detail = j['detail'] as String,
        command = j['command'] as String?,
        horizons = (j['horizons'] as List?)
                ?.map((e) => e as Map<String, dynamic>)
                .toList() ??
            const [];

  final String symbol, title, detail;
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
        url = j['url'] as String? ?? '',
        seq = (j['seq'] as num?)?.toInt() ?? 0,
        at = DateTime.parse(j['at'] as String),
        detectedAt = DateTime.parse(j['detected_at'] as String),
        extra = Map<String, String>.from(
            (j['extra'] as Map?)?.map((k, v) => MapEntry('$k', '$v')) ?? {});

  final String id, kind, title, body, severity, symbol, url;
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
        at = DateTime.parse(j['at'] as String);

  final String key, title, impact, source, url, note;
  final DateTime at;

  Duration get away => at.difference(DateTime.now());
}
