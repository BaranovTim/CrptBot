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
    'Vanth holds no exchange key, signs nothing and moves no money. It '
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
  // THE SWING RULES. Measured on two separate years across fifteen coins
  // (research/swing_daily.py), and written here in the order they apply.
  HowToStep(
      '07',
      'Daily: buy only above the 200-day average',
      'The same buy signals lost money below the 200-day average in both '
          'test years, under every exit, and made it above. So the daily '
          'card makes no BUY below the line; the card says which side the '
          'coin is on. SELL signals are not gated — they paid on both sides, '
          'best when fading a rally above the line.',
      Obsidian.primary,
      'THE TREND'),
  HowToStep(
      '08',
      'Daily entries have no time limit — the stop trails',
      'Log a daily entry with the stop at the last swing low and no take '
          'profit. Each time a new swing low confirms on the daily chart, '
          'the app moves your stop up to it and tells you. It never moves '
          'back. The trade lives until the structure breaks. Trailed like '
          'this, buys with the trend averaged about +5% per trade in both '
          'years; a fixed target and a ten-day clock lost.',
      _cyan,
      'THE EXIT'),
  HowToStep(
      '09',
      'Expect to lose most of them',
      'With a trailing stop about three trades in four end at the stop, '
          'around −6% each. The year is paid for by the one in four that '
          'runs +15% to +30%. If that is hard to hold, take half at the '
          'first level and trail the rest: roughly half the return, and '
          'about half the trades win. Size from the stop distance — the '
          'quarter-Kelly figure on the card does that — so no single stop '
          'matters.',
      _violet,
      'THE SHAPE'),
  // THE POOLED 4h MODEL. research/wf4h.py: refit every six months over
  // three years, scored as an account with fees and funding paid.
  HowToStep(
      '10',
      '4h: one model, the best of the whole market',
      'Every 4h call comes from one model trained on all fifteen coins, and '
          "each reading is ranked against every coin's readings from the last "
          'ninety days — a STRONG call is in the top 3% of the market, not of '
          'one coin. The target sits on the next confirmed swing; the stop '
          'sits half an ATR past the last one, not on it, because a stop '
          "exactly on a swing is where everyone's stop is, and those were "
          'the trades that got swept. Refitted every six months over three '
          'years, this made money in all six half-years; a model per coin '
          'did in two. Between closes the card follows the order: IN THE '
          'TRADE once the price trades through it, and the outcome when the '
          'trade reaches its target or stop.',
      Obsidian.amber,
      'THE TIMEFRAMES'),
  HowToStep(
      '11',
      '4h: the same size for every call',
      'A 4h call suggests the same share of your account every time. Sizing '
          'by the payoff — which quarter-Kelly does — put the biggest positions '
          'on the tight-stop trades, and those were the ones that lost; the '
          'same size for every call beat it in every test. The share itself '
          'is a default, not a measurement: pick one you can hold through a '
          'run of stops.',
      Obsidian.primary,
      'THE SIZE'),
  // THE ENTRY. research/improve_4h.py and monitor.resting_orders.
  HowToStep(
      '12',
      '4h: enter with a limit order, not at the market',
      'A 4h call comes with an order: a limit half an ATR better than the '
          'close that made the call, good for a day (six 4h bars). The stop moves with '
          'it — it keeps its distance from where you get in — and the target '
          'stays on its level. If the order never fills there is no trade; '
          'about a third do not. While a call carries on, each new close '
          'moves the order to the new price and the app tells you: keep one '
          'order, at the newest price. Once it fills you are in, and later '
          'calls on that coin wait until the trade ends, and the app tells '
          'you when it fills and when the trade ends. HALFWAY to the target, '
          'take a third of the position off and move your stop to your entry: '
          'from then on the trade cannot lose. The card shows the halfway '
          'price and the app tells you when it is reached. Tested over three '
          'years, that wins about 73% of trades (71% in the latest year) '
          'instead of 61%, for about a third less total return — the limit '
          'entry is the habit that separated the profitable big traders from '
          'the losing ones, and the scale-out is what makes most trades end '
          'green.',
      _cyan,
      'THE ENTRY'),
  // THE ROTATION. api/momentum.py, research/new_strategies.py.
  HowToStep(
      '13',
      'The momentum rotation is a weekly basket',
      'Separate from the calls, under them on Market. Every Monday 00:00 '
          'UTC the thirty most-traded Binance perpetuals are ranked on two '
          'things at once — their 15-day return and a week of net taker '
          'buying — and long the top five, short the bottom five, equal '
          'size, held until the next Monday. Tested on every perpetual as it '
          'stood at the time, dead coins included, it had a Sharpe of about '
          '1.0, with weeks that lost 15-18%. The first version, 30-day '
          "momentum on today's fifteen coins, looked as good but was 0.3-0.4 "
          'honestly: those coins are on the list partly because they went '
          'up. Size it small.',
      _violet,
      'THE ROTATION'),
  HowToStep(
      '14',
      'Smart money confirms, it does not decide',
      'The followed traders\' entries are not an input to the model — tested, '
          'they add nothing there, because they are silent on nine bars in '
          'ten. But on the model\'s own 4h calls they matter: when a followed '
          'trader had entered the same way in the last 24h the calls made '
          '+0.6% a trade at a 72% hit rate; when one had entered the other '
          'way, −0.4% at 50%. So agreement raises a call one level, '
          'disagreement lowers it one (a small call it contradicts is '
          'withdrawn), and the card and the notification say which. On '
          'daily the window is 72h and only agreement counts: trailed '
          'entries a followed trader had also taken averaged +7% (half out) '
          'against −0.3% otherwise; disagreement was measured to change '
          'nothing there, so it is noted and the call stands.',
      Obsidian.greenDim,
      'CONFLUENCE'),
  // THE RECORD. api/ledger.py.
  HowToStep(
      '15',
      'The live record is the only score that counts',
      'Under the calls on Market: every 4h order the server placed since the '
          'model went live, which filled, and how each trade ended, after '
          'fees, for your sensitivity setting. Every number elsewhere in this '
          'app is from tests on the past; this one is not. Read it with its '
          'sample size: twenty trades at 60% can show anything from 40% to '
          "80%. For scale: peer-reviewed machine-learning forecasts of "
          'bitcoin were right 51-56% of the time an hour or less ahead, and '
          'no bot or signal seller we could find publishes an audited record '
          'at all — their 80-97% win rates are their own claims.',
      Obsidian.primary,
      'THE RECORD'),
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
