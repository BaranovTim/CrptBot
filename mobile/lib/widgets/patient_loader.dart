/// Waiting well: a slow answer is not a broken one.
///
/// THE BUG THIS FIXES
///     One HTTP call timing out after twenty seconds put a red "No link to
///     the service" panel on screen with a TimeoutException underneath it.
///     On a phone that verdict is wrong far more often than it is right —
///     a lift, a cell handover, wifi reassociating, or the server building a
///     dashboard it has not cached yet. Every one of those fixes itself in
///     seconds, and the app was declaring a fault before the network had
///     finished trying.
///
/// WHAT HAPPENS INSTEAD
///     The spinner stays up and the request is retried for five minutes.
///     Only when that entire window has passed with nothing getting through
///     is it called a failure — because by then it genuinely is one, and the
///     message is worth reading rather than worth dismissing.
///
/// WHY IT TALKS WHILE IT WAITS
///     A spinner that has not moved in three minutes is indistinguishable
///     from a frozen app. The honest fix is not to hide the wait but to own
///     it, so this counts the time out loud and grows visibly less patient.
///     The clock is real: it is the actual age of the wait, not a fake
///     progress bar inching toward a number nobody chose.
library;

import 'dart:async';

import 'package:flutter/material.dart';

import '../theme/liquid_obsidian.dart';

/// How long the app keeps trying before it calls the server unreachable.
///
/// Five minutes is long enough to cover every transient a phone produces —
/// including a full server restart, which takes about ninety seconds — and
/// short enough that a genuinely dead host is still reported while you are
/// holding the phone rather than after you have put it away.
const Duration kPatience = Duration(minutes: 5);

/// One line per minute waited, in order. Index 0 is shown at 1:00.
///
/// The list is exactly as long as the patience window, and the last entry is
/// clamped rather than wrapped, so a quip never contradicts the clock beside
/// it by starting the sequence over.
const List<String> kWaitingQuips = <String>[
  "Damn, why does it take so looong",
  "I'll be 90 by the time this loads",
  "Still going. Somewhere a very small hamster is running very fast",
  "Four minutes in. I have started reading the terms of service",
  "One more minute and I am calling it. No hard feelings",
];

/// The quip for a wait of this length, or null for the first minute.
///
/// Nothing is said before 1:00 on purpose: most waits end well inside it, and
/// a joke about how slow this is would arrive after the data did.
String? quipFor(Duration waited) {
  final minutes = waited.inMinutes;
  if (minutes < 1) return null;
  final i = minutes - 1;
  return kWaitingQuips[i >= kWaitingQuips.length ? kWaitingQuips.length - 1 : i];
}

/// Has this wait gone on long enough to be called a failure?
bool patienceExhausted(Duration waited) => waited >= kPatience;

String formatWaited(Duration d) {
  final m = d.inMinutes;
  final s = d.inSeconds % 60;
  return '$m:${s.toString().padLeft(2, '0')}';
}

/// A spinner that admits how long it has been spinning.
class WaitingPanel extends StatefulWidget {
  const WaitingPanel({
    super.key,
    required this.since,
    this.what = 'Loading',
    this.compact = false,
    this.onPatienceExhausted,
  });

  /// When this unbroken run of waiting began. Reset it on every success, or
  /// the clock measures the session instead of the outage.
  final DateTime since;

  final String what;

  /// Inline in a list, rather than filling an empty screen.
  final bool compact;

  /// Fired once, the moment the wait crosses [kPatience].
  ///
  /// The decision to give up lives here rather than in the caller's retry
  /// loop because this widget is the thing holding a clock. A caller that
  /// checked the elapsed time inside its own catch block could only give up
  /// on the next failed attempt, so the error would appear some seconds after
  /// the five minutes it claims to have waited — or never, if the retries had
  /// stopped.
  final VoidCallback? onPatienceExhausted;

  @override
  State<WaitingPanel> createState() => _WaitingPanelState();
}

class _WaitingPanelState extends State<WaitingPanel> {
  Timer? _tick;
  bool _announced = false;

  @override
  void initState() {
    super.initState();
    // one second, because the display is mm:ss. It repaints two short strings
    // and a spinner that is already animating every frame.
    _tick = Timer.periodic(const Duration(seconds: 1), (_) {
      if (!mounted) return;
      setState(() {});
      _checkPatience();
    });
    // in case this panel is built on an already-old wait, e.g. coming back to
    // a screen that has been failing in the background
    WidgetsBinding.instance.addPostFrameCallback((_) => _checkPatience());
  }

  void _checkPatience() {
    if (_announced) return;
    if (!patienceExhausted(DateTime.now().difference(widget.since))) return;
    _announced = true;
    _tick?.cancel();
    widget.onPatienceExhausted?.call();
  }

  @override
  void dispose() {
    _tick?.cancel();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final waited = DateTime.now().difference(widget.since);
    final quip = quipFor(waited);
    final long = waited.inSeconds >= 20;

    final column = Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        const SizedBox(
          height: 30,
          width: 30,
          child: CircularProgressIndicator(
              color: Obsidian.primary, strokeWidth: 2.4),
        ),
        const SizedBox(height: 18),
        Text(long ? 'Still ${widget.what.toLowerCase()}' : widget.what,
            style: Obsidian.body(size: 13.5)),
        // The clock appears only once the wait is worth remarking on. Showing
        // 0:03 would turn every ordinary load into an event.
        if (long) ...[
          const SizedBox(height: 6),
          Text(formatWaited(waited),
              style: Obsidian.dataTable(size: 12, w: FontWeight.w600)
                  .copyWith(color: Obsidian.outline)),
        ],
        if (quip != null) ...[
          const SizedBox(height: 14),
          // switched on the text itself, so a new line crossfades in rather
          // than replacing the old one mid-blink
          AnimatedSwitcher(
            duration: const Duration(milliseconds: 400),
            child: Text(
              quip,
              key: ValueKey<String>(quip),
              textAlign: TextAlign.center,
              style: Obsidian.body(color: Obsidian.outline, size: 12.5)
                  .copyWith(fontStyle: FontStyle.italic),
            ),
          ),
        ],
      ],
    );

    return Padding(
      padding: EdgeInsets.symmetric(
          horizontal: Obsidian.containerPadding,
          vertical: widget.compact ? 40 : 0),
      child: widget.compact ? column : Center(child: column),
    );
  }
}
