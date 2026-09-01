/// What reaches the phone, and from where — one rule set, two callers.
///
/// WHY THIS IS NOT INSIDE THE SHELL
///     Alerts have to be collected from two places: the foreground poll while
///     you are looking at the app, and a background job while you are not.
///     Those are different isolates and cannot share objects, so the only way
///     they can apply the SAME rules is if the rules live in a function both
///     call. When the filtering lived in `Shell._pollAlerts`, the background
///     path would have had to reimplement muting and sensitivity, and the two
///     would have drifted apart the first time either changed.
///
/// THE CURSOR IS PERSISTED, AND THAT IS THE POINT
///     It used to be a field on the shell's State, reset to "now" on every
///     launch. So the app deliberately skipped everything that happened while
///     it was closed — which is nearly everything. It now lives in storage,
///     and reopening the app resumes from where it left off.
///
///     Bounded, though: see [maxCatchUp] and [maxBacklogAge]. A cursor from a
///     week ago must not fire two hundred notifications at once.
library;

import 'dart:async';

import 'package:flutter/foundation.dart' show debugPrint;
import 'package:shared_preferences/shared_preferences.dart';

import 'client.dart';
import 'models.dart';
import 'muted.dart';
import 'settings.dart';

/// The most notifications one collection may post.
///
/// A phone that has been off overnight can come back to a dozen headlines.
/// Posting all of them trains you to clear the shade without reading it,
/// which costs you the one that mattered.
const int maxCatchUp = 6;

/// Nothing older than this is worth waking you for after the fact.
///
/// A signal is a statement about a window that has since closed; a filing
/// from yesterday is not news. Both are still in the app when you open it.
const Duration maxBacklogAge = Duration(hours: 6);

/// Where the collection got to. Shared by both isolates.
const String cursorKey = 'alerts.cursor.v1';

Future<int?> loadCursor() async {
  try {
    final prefs = await SharedPreferences.getInstance();
    final v = prefs.getInt(cursorKey);
    return (v == null || v <= 0) ? null : v;
  } catch (_) {
    return null;
  }
}

Future<void> saveCursor(int cursor) async {
  try {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setInt(cursorKey, cursor);
  } catch (_) {
    // an unsaved cursor costs a repeat, not a miss
  }
}

/// Ranked most worth interrupting for, first.
///
/// Used only when a collection has to be trimmed: if six things happened
/// overnight and only some can be shown, a BUY signal must outrank a
/// headline. Within a kind, newest first.
int _rank(Alert a) => switch (a.kind) {
      'signal' => 0,
      'spike' => 1,
      'calendar' => 2,
      'whale' => 3,
      _ => 4,
    };

/// The alerts that should actually be delivered, in the order to deliver them.
///
/// Pure, and separately tested: everything that decides whether your phone
/// buzzes is in here, where it can be checked without a network or a device.
List<Alert> selectDeliverable(
  List<Alert> incoming, {
  required String sensitivity,
  required bool Function(String symbol, String interval) isMuted,
  bool Function(String kind)? isKindMuted,
  String newsLevel = 'all',
  DateTime? now,
  int limit = maxCatchUp,
}) {
  final t = now ?? DateTime.now().toUtc();
  final kept = <Alert>[];

  for (final a in incoming) {
    // A whole category silenced — news, most likely. Checked first because it
    // is the cheapest and the most total.
    if (isKindMuted != null && isKindMuted(a.kind)) continue;

    // A pair silenced on THIS device. Muting is per-device on purpose: the
    // same account on a tablet may want what this phone does not.
    if (a.symbol.isNotEmpty && isMuted(a.symbol, a.interval)) continue;

    // Too old to interrupt for. It is still in the app.
    if (t.difference(a.detectedAt.toUtc()) > maxBacklogAge) continue;

    // The sensitivity setting governs the notification exactly as it governs
    // the dashboard card. Without this the app could be withholding a small
    // call as too weak to show while its notification was already on the lock
    // screen — the same reading, two different answers.
    //
    // An alert with no strength at all is not a signal (a filing, a headline,
    // a scheduled release) and is never gated by a signal setting.
    if (a.kind == 'signal' && !clearsSensitivity(a.strength, sensitivity)) {
      continue;
    }

    // And the news level does the same job for headlines. A story that does
    // not clear it is not lost — it is in the News tab, where you went
    // looking for it rather than being interrupted by it.
    if (a.kind == 'news' &&
        !clearsNewsLevel(a.bias, a.impact, newsLevel)) {
      continue;
    }
    kept.add(a);
  }

  kept.sort((x, y) {
    final r = _rank(x).compareTo(_rank(y));
    return r != 0 ? r : y.detectedAt.compareTo(x.detectedAt);
  });
  return kept.length <= limit ? kept : kept.sublist(0, limit);
}

/// Does this headline clear the chosen news level?
///
/// The four levels are LITERAL FILTERS, not a nested hierarchy, and one case
/// makes the difference visible: a STRONG IMPACT story the scorer reads as
/// MIXED passes `strong` and fails `directional`. That is deliberate — the
/// two settings ask different questions ("which way?" and "how much?") and
/// collapsing them would silently answer one with the other.
///
/// Both readings come from a keyword scorer that stays silent on roughly half
/// of all headlines, so anything but `all` is trusting that scorer. `all` is
/// the only setting that cannot hide a story because a lexicon missed it.
bool clearsNewsLevel(String bias, String impact, String level) {
  switch (level) {
    case 'none':
      return false;
    case 'directional':
      return bias == 'BULL' || bias == 'BEAR';
    case 'strong':
      return impact == 'STRONG IMPACT';
    case 'all':
    default:
      return true;
  }
}

/// Does a signal of this strength clear the user's chosen setting?
///
/// Mirrors `Recommendation.clears`, against the same three levels. Kept as a
/// free function because an Alert is not a Recommendation and inventing one
/// to reuse the method would be the tail wagging the dog.
bool clearsSensitivity(String strength, String setting) {
  const order = {'strong': 3, 'medium': 2, 'small': 1};
  final s = order[strength] ?? 0;
  if (s == 0) return false;              // no strength is not a call
  final needed = order[setting] ?? 3;    // unknown setting = strictest
  return s >= needed;
}

/// One collection: ask the server what is new, decide what deserves a
/// notification, advance the cursor.
///
/// Returns what should be delivered. Deliberately does NOT deliver: the
/// foreground also wants to draw an in-app banner, and the background does
/// not, so the caller decides. The cursor is advanced either way, because a
/// caller that failed to draw a banner has still SEEN the alert.
Future<AlertBatch> collectAlerts(ApiClient client) async {
  final cursor = await loadCursor();
  final r = await client.alerts(cursor: cursor);

  await Muted.instance.load();
  final sensitivity = await Settings.instance.sensitivity();
  final newsLevel = await Settings.instance.newsAlerts();

  // A first run has no cursor, so the server returns nothing and this simply
  // records where to start. That is what stops a fresh install replaying a
  // week of filings.
  final deliver = cursor == null
      ? const <Alert>[]
      : selectDeliverable(r.alerts,
          sensitivity: sensitivity,
          isMuted: (s, i) => Muted.instance.isMuted(s, i),
          isKindMuted: Muted.instance.isKindMuted,
          newsLevel: newsLevel);

  await saveCursor(r.cursor);
  final suppressed = cursor == null ? 0 : r.alerts.length - deliver.length;
  debugPrint('[alerts] cursor=$cursor -> ${r.alerts.length} new, '
      '${deliver.length} to deliver, $suppressed held back');
  return AlertBatch(deliver, r.alerts.length);
}

class AlertBatch {
  const AlertBatch(this.deliver, this.total);

  /// What earned a notification.
  final List<Alert> deliver;

  /// How many the server returned, before filtering. The difference is not
  /// loss: muted pairs, weak signals and stale items are all still readable
  /// in the app.
  final int total;
}
