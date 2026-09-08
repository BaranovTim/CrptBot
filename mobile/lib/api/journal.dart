/// What your logged trades add up to.
///
/// EVERY NUMBER HERE COMES FROM YOUR OWN JOURNAL
///     The design this screen is built from shows a "Trading Portfolio" worth
///     $48,290.45 and a row of exchange integrations reporting "Connected".
///     This app holds no exchange key, reads no balance and places no orders,
///     so none of that is knowable and none of it is shown. What IS knowable
///     is what you logged: how many trades, how many won, what they made.
///     Those are computed here and nothing else is invented to sit beside
///     them.
///
/// CLOSED TRADES ONLY, FOR THE RECORD
///     Win rate and profit factor count finished trades. An open position has
///     no outcome yet, and folding its floating profit into a win rate would
///     make the record improve every time the market ticked your way.
library;

import 'trades.dart';

class JournalStats {
  const JournalStats({
    required this.total,
    required this.open,
    required this.closed,
    required this.wins,
    required this.losses,
    required this.realised,
    required this.unrealised,
    required this.grossWin,
    required this.grossLoss,
    required this.realisedToday,
  });

  factory JournalStats.of(List<TradeEntry> trades,
      {Map<String, double> prices = const {}, DateTime? now}) {
    final today = (now ?? DateTime.now()).toUtc();
    var open = 0, closed = 0, wins = 0, losses = 0;
    var realised = 0.0, unrealised = 0.0;
    var grossWin = 0.0, grossLoss = 0.0, realisedToday = 0.0;

    for (final t in trades) {
      if (t.isOpen) {
        open++;
        unrealised += t.pnl(prices[t.symbol]) ?? 0;
        continue;
      }
      closed++;
      final p = t.pnl(null) ?? 0;
      realised += p;
      if (p > 0) {
        wins++;
        grossWin += p;
      } else if (p < 0) {
        losses++;
        grossLoss += -p;
      }
      // A break-even trade is neither, and counting it as a win would be the
      // easiest possible way to flatter the record.
      final at = t.closedAt;
      if (at != null &&
          at.toUtc().year == today.year &&
          at.toUtc().month == today.month &&
          at.toUtc().day == today.day) {
        realisedToday += p;
      }
    }
    return JournalStats(
      total: trades.length,
      open: open,
      closed: closed,
      wins: wins,
      losses: losses,
      realised: realised,
      unrealised: unrealised,
      grossWin: grossWin,
      grossLoss: grossLoss,
      realisedToday: realisedToday,
    );
  }

  final int total, open, closed, wins, losses;
  final double realised, unrealised, grossWin, grossLoss, realisedToday;

  /// Wins over decided trades, or null when nothing has been decided.
  ///
  /// Null rather than 0%: "no trades yet" and "you lose every time" are very
  /// different statements, and a screen must not print the second when it
  /// means the first.
  double? get winRate {
    final decided = wins + losses;
    return decided == 0 ? null : wins / decided * 100.0;
  }

  /// Gross profit over gross loss. Null until there is a loss to divide by —
  /// an unbeaten record has no finite profit factor, and printing a made-up
  /// large number would read as a measurement.
  double? get profitFactor =>
      grossLoss <= 0 ? null : grossWin / grossLoss;

  double get net => realised + unrealised;
}
