/// How a price is written, in one place.
///
/// THE BUG THIS EXISTS TO KILL
///     Five files each carried `toStringAsFixed(v >= 100 ? 2 : 4)`. That is
///     right for BTC and wrong for anything cheap: 1000PEPEUSDT trades at
///     0.003624 and rendered as "0.0036", throwing away the two digits that
///     carry the move. A 0.5% change was invisible.
///
/// SIGNIFICANT DIGITS, NOT DECIMAL PLACES
///     The question is never "how many decimals" — it is "how many digits of
///     this number actually say something". A fixed decimal count answers
///     that correctly for exactly one order of magnitude and badly for every
///     other, which is why five copies of the same constant were all wrong
///     for the same coin.
library;

import 'dart:math' as math;

/// A price with enough digits to be acted on, grouped for reading.
///
/// Null renders as an em dash, never as zero: a price of "$0.00" reads as a
/// level, and it is the most dangerous number this could invent.
String priceText(double? v, {String prefix = r'$'}) {
  if (v == null) return '—';
  final a = v.abs();
  int decimals;
  if (a >= 1000) {
    decimals = 2;
  } else if (a >= 1) {
    decimals = 4;
  } else if (a == 0) {
    decimals = 2;
  } else {
    // How far below the point the first meaningful digit sits, plus four
    // digits of precision after it. 0.003624 has two leading zeros, so six
    // decimals — exactly the digits that coin moves in.
    final leadingZeros = -(a.abs() == 0 ? 0 : (_log10(a).floor() + 1));
    decimals = (leadingZeros + 4).clamp(2, 8);
  }

  var s = v.toStringAsFixed(decimals);
  // Trim padding beyond two decimals — 0.8240 reads worse than 0.824 and
  // says the same thing. Never below two, so money stays money.
  if (s.contains('.') && decimals > 2) {
    final parts = s.split('.');
    var frac = parts[1];
    while (frac.length > 2 && frac.endsWith('0')) {
      frac = frac.substring(0, frac.length - 1);
    }
    s = '${parts[0]}.$frac';
  }

  final neg = s.startsWith('-');
  if (neg) s = s.substring(1);
  final parts = s.split('.');
  final whole = parts[0]
      .replaceAllMapped(RegExp(r'(\d)(?=(\d{3})+$)'), (m) => '${m[1]},');
  return '${neg ? '-' : ''}$prefix$whole.${parts[1]}';
}

double _log10(double x) => x <= 0 ? 0 : math.log(x) / math.ln10;
