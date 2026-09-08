/// What this app is, what it is not, and what it has actually measured.
///
/// SHOWN ONCE, ON THE FIRST SIGN-IN OF AN ACCOUNT
///     Not as a formality. Someone arriving at a screen full of BUY and SELL
///     calls will assume the calls are good unless told otherwise, and the
///     measured truth is that most of them score at or near chance. Saying so
///     before they act is the difference between a tool and a trap.
///
/// WRITTEN FROM THE PROJECT'S OWN NUMBERS
///     Not boilerplate. Every claim below is something the training logs
///     actually say — see `train.py`'s summary and the AUC tables in
///     `train_logs/`. If those numbers change, this text changes with them.
library;

import 'package:flutter/material.dart';

import '../theme/liquid_obsidian.dart';
import 'glass.dart';

/// Bumping this shows the acknowledgement again to everyone.
///
/// Tie it to a change in what the app CLAIMS, never to a redesign: asking
/// people to re-read the same words teaches them to dismiss it unread.
const int acknowledgementVersion = 1;

class AckPoint {
  const AckPoint(this.icon, this.title, this.body, this.tone);
  final IconData icon;
  final String title, body;
  final Color tone;
}

const List<AckPoint> acknowledgementPoints = [
  AckPoint(
    Icons.gavel_rounded,
    'It never places an order',
    'ThusIldy holds no exchange key, signs nothing and moves no money. It '
        'reads market data and shows you what its models make of it. Every '
        'trade is one you place yourself, somewhere else — the trade log here '
        'is a record of what you did, not an instruction that was carried out.',
    Obsidian.primary,
  ),
  AckPoint(
    Icons.query_stats_rounded,
    'Most timeframes score at chance',
    'Measured across 120 fits on ten coins: AUC mostly lands between 0.46 and '
        '0.53, where 0.50 is a coin flip. Only about one row in ten clears '
        '0.52 with stable folds and a clean shuffle test. The 1h models are '
        'the consistent exception across assets — small, repeatable, and still '
        'not a promise.',
    Obsidian.amber,
  ),
  AckPoint(
    Icons.percent_rounded,
    'FLAT is the honest answer, not a broken one',
    'A signal has to beat its own costs before it is worth taking. At 1m the '
        'barriers span about 1.4% while fees eat 0.10%, so a call needs a '
        '0.607 probability to be worth it; at 1d the span is ~10.9% and 0.514 '
        'is enough. That is why short timeframes sit at FLAT so often, and it '
        'is arithmetic rather than a fault.',
    Obsidian.primary,
  ),
  AckPoint(
    Icons.school_rounded,
    'This is not financial advice',
    'Nothing here is a recommendation to buy or sell anything, and nobody '
        'behind it is a licensed adviser. Crypto is volatile and leveraged '
        'positions can lose more than you put in. Only risk what you can '
        'afford to lose.',
    Obsidian.redSoft,
  ),
];

/// The how-to, shown in the Bot Instructions tab beside the points above.
class HowToStep {
  const HowToStep(this.n, this.title, this.body, this.tone, this.tag);
  final String n, title, body, tag;

  /// The badge colour. Cycled through the palette rather than all one green,
  /// because a column of identical cards is a column nobody reads — the
  /// design this came from numbers each guide in its own colour for exactly
  /// that reason.
  final Color tone;
}

/// Cyan and violet exist only here and on the screener's medallions, so a
/// step badge cannot be mistaken for a live signal — green and red are
/// reserved for BUY and SELL everywhere else in the app.
const _cyan = Color(0xFF00D2FF);
const _violet = Color(0xFFB07CF0);

const List<HowToStep> howToSteps = [
  HowToStep(
      '01',
      'Pick a pair and a timeframe',
      'Market lists what the server has models for. The timeframe bar on the '
          'dashboard shows which are trained — an untrained one offers to fit '
          'it rather than pretending to have an answer.',
      Obsidian.primary,
      'START HERE'),
  HowToStep(
      '02',
      'Read the call with its confidence',
      'BUY, SELL or FLAT comes with the probability the model gives it and '
          'the probability this timeframe would NEED to cover costs. When the '
          'first is below the second, FLAT is the correct call.',
      _cyan,
      'THE NUMBERS'),
  HowToStep(
      '03',
      'Check the levels before you act',
      'Take profit and stop loss are drawn for the side the call names — on a '
          'SELL the target is below the price and the stop above it. With no '
          'call they are labelled as barriers, because no entry was proposed.',
      _violet,
      'RISK FIRST'),
  HowToStep(
      '04',
      'Place the trade yourself, then log it',
      'Use your own exchange. Log Market Entry at the bottom of the dashboard '
          'records what you did, with your fill price and your levels.',
      Obsidian.amber,
      'YOUR MOVE'),
  HowToStep(
      '05',
      'Let the log settle itself',
      'When price reaches a level you set, the app closes that entry in your '
          'log and marks it HIT TP or HIT SL. It checks the high and low '
          'since your entry, so an overnight wick counts.',
      Obsidian.greenDim,
      'AUTOMATIC'),
  HowToStep(
      '06',
      'Set a daily stop and respect it',
      'Preferences has a daily loss limit. The app cannot stop you trading — '
          'it has no keys — but it will tell you plainly when the day has '
          'gone past the line you drew.',
      Obsidian.redSoft,
      'DISCIPLINE'),
];

/// The first-run dialog. Cannot be dismissed by tapping outside: the one
/// thing it must not become is a flash of text somebody swipes away.
Future<bool> showAcknowledgement(BuildContext context,
    {bool firstRun = true}) async {
  final ok = await showDialog<bool>(
    context: context,
    barrierDismissible: !firstRun,
    builder: (ctx) => Dialog(
      backgroundColor: Colors.transparent,
      insetPadding: const EdgeInsets.symmetric(horizontal: 18, vertical: 40),
      child: GlassPanel(
        active: true,
        padding: const EdgeInsets.all(20),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                const Icon(Icons.shield_outlined,
                    color: Obsidian.primary, size: 22),
                const SizedBox(width: 10),
                Expanded(
                  child: Text('Before you start',
                      style: Obsidian.headlineMd()),
                ),
              ],
            ),
            const SizedBox(height: 14),
            Flexible(
              child: SingleChildScrollView(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    for (final p in acknowledgementPoints) ...[
                      Row(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Icon(p.icon, size: 17, color: p.tone),
                          const SizedBox(width: 10),
                          Expanded(
                            child: Column(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: [
                                Text(p.title,
                                    style: Obsidian.bodyLg().copyWith(
                                        fontSize: 13.5,
                                        fontWeight: FontWeight.w600)),
                                const SizedBox(height: 4),
                                Text(p.body,
                                    style: Obsidian.body(
                                        color: Obsidian.outline, size: 11.5)),
                              ],
                            ),
                          ),
                        ],
                      ),
                      const SizedBox(height: 16),
                    ],
                  ],
                ),
              ),
            ),
            const SizedBox(height: 4),
            SizedBox(
              width: double.infinity,
              height: 48,
              child: FilledButton(
                style: FilledButton.styleFrom(
                    backgroundColor: Obsidian.primary,
                    foregroundColor: Obsidian.onPrimary,
                    shape: RoundedRectangleBorder(
                        borderRadius:
                            BorderRadius.circular(Obsidian.rMd))),
                onPressed: () => Navigator.of(ctx).pop(true),
                child: Text(firstRun ? 'I understand' : 'Close',
                    style: Obsidian.labelSm(
                        color: Obsidian.onPrimary, size: 13)),
              ),
            ),
            if (firstRun) ...[
              const SizedBox(height: 8),
              Text('You can read this again any time under '
                  'Profile → Bot Instructions.',
                  textAlign: TextAlign.center,
                  style: Obsidian.body(color: Obsidian.outline, size: 10.5)),
            ],
          ],
        ),
      ),
    ),
  );
  return ok ?? false;
}
