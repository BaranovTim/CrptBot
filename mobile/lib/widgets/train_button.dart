/// Fit a model from the app, instead of from a laptop.
///
/// WHY IT IS A STATE MACHINE AND NOT A BUTTON
///     A fit takes about forty minutes on this server, so "tap and see what
///     happens" is not available. The control has to show queued, running
///     with an elapsed clock, and the RESULT — including the bad results,
///     which are the ones worth having and the ones a fire-and-forget button
///     would throw away.
///
/// WHY A FAILED OR AT-CHANCE FIT IS SHOWN IN FULL
///     "I trained it and nothing happened" needs an answer on screen. A model
///     that comes back at chance is not a malfunction, it is a finding, and
///     hiding it would leave someone re-queueing the same forty minutes
///     wondering why the call never appears.
library;

import 'dart:async';

import 'package:flutter/material.dart';

import '../api/client.dart';
import '../api/models.dart';
import '../theme/liquid_obsidian.dart';

class TrainButton extends StatefulWidget {
  const TrainButton({
    super.key,
    required this.client,
    required this.symbol,
    required this.interval,
    this.market = 'crypto',
    this.onTrained,
  });

  final ApiClient client;
  final String symbol, interval, market;

  /// Called when a fit finishes, so the page can reload and show the call.
  final VoidCallback? onTrained;

  @override
  State<TrainButton> createState() => _TrainButtonState();
}

class _TrainButtonState extends State<TrainButton> {
  TrainJob? _job;
  Timer? _poll;
  bool _busy = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    _refresh();
  }

  @override
  void dispose() {
    _poll?.cancel();
    super.dispose();
  }

  Future<void> _refresh() async {
    try {
      final st = await widget.client.trainStatus(symbol: widget.symbol);
      if (!mounted) return;
      final was = _job;
      final now = st.jobFor(widget.symbol, widget.interval);
      setState(() => _job = now);

      // A fit that has just finished means there is a model where there was
      // none, so the page around this button is out of date.
      if (was != null && !was.done && (now?.done ?? false)) {
        widget.onTrained?.call();
      }
      _schedule(now);
    } catch (e) {
      if (!mounted) return;
      setState(() => _error = '$e');
    }
  }

  void _schedule(TrainJob? job) {
    _poll?.cancel();
    if (job == null || job.done || job.failed) return;
    // Thirty seconds: a forty-minute job does not need a faster clock, and
    // this runs on a server that is busy fitting a model.
    _poll = Timer(const Duration(seconds: 30), _refresh);
  }

  Future<void> _start() async {
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      final r = await widget.client.train(
          widget.symbol, widget.interval, market: widget.market);
      if (!mounted) return;
      if (r['ok'] != true) {
        setState(() => _error = r['error'] as String? ?? 'could not queue');
      }
      await _refresh();
    } catch (e) {
      if (!mounted) return;
      setState(() => _error = '$e');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final j = _job;

    if (j != null && (j.running || j.queued)) return _inProgress(j);
    if (j != null && j.failed) return _result(j, Obsidian.redSoft);
    if (j != null && j.done) return _result(j, Obsidian.green);

    return Column(
      children: [
        FilledButton.icon(
          style: FilledButton.styleFrom(
              backgroundColor: Obsidian.primary,
              foregroundColor: Obsidian.onPrimary),
          onPressed: _busy ? null : _start,
          icon: _busy
              ? const SizedBox(
                  width: 14,
                  height: 14,
                  child: CircularProgressIndicator(strokeWidth: 2))
              : const Icon(Icons.model_training_rounded, size: 18),
          label: Text('Train ${widget.symbol} ${widget.interval}'),
        ),
        const SizedBox(height: 8),
        Text(
            'Runs on the server and takes about 40 minutes. You can close '
            'the app — it keeps going.',
            textAlign: TextAlign.center,
            style: Obsidian.body(color: Obsidian.outline, size: 11)),
        if (_error != null) ...[
          const SizedBox(height: 8),
          Text(_error!,
              textAlign: TextAlign.center,
              style: Obsidian.body(color: Obsidian.redSoft, size: 11.5)),
        ],
      ],
    );
  }

  Widget _inProgress(TrainJob j) {
    final mins = (j.elapsed ?? 0) ~/ 60;
    return Column(
      children: [
        Row(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            const SizedBox(
                width: 14,
                height: 14,
                child: CircularProgressIndicator(
                    strokeWidth: 2, color: Obsidian.primary)),
            const SizedBox(width: 10),
            Text(
                j.queued
                    ? 'Queued — one fit runs at a time'
                    : 'Fitting… ${mins}m elapsed',
                style: Obsidian.body(size: 13, color: Obsidian.primary)),
          ],
        ),
        const SizedBox(height: 8),
        Text(
            j.queued
                ? 'This server has one core, so jobs run in order.'
                : 'About 40 minutes in total. Close the app if you like.',
            textAlign: TextAlign.center,
            style: Obsidian.body(color: Obsidian.outline, size: 11)),
      ],
    );
  }

  Widget _result(TrainJob j, Color c) {
    final auc = j.bestAuc;
    return Column(
      children: [
        Row(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            Icon(
                j.done
                    ? (j.atChance
                        ? Icons.help_outline_rounded
                        : Icons.check_circle_rounded)
                    : Icons.error_outline_rounded,
                size: 16,
                color: j.atChance ? Obsidian.amber : c),
            const SizedBox(width: 8),
            Text(
                j.failed
                    ? 'Training failed'
                    : j.atChance
                        ? 'Fitted, but it found nothing'
                        : 'Model fitted',
                style: Obsidian.body(
                    size: 13, color: j.atChance ? Obsidian.amber : c)),
          ],
        ),
        if (auc != null) ...[
          const SizedBox(height: 6),
          Text('Best AUC ${auc.toStringAsFixed(3)} — 0.50 is a coin flip',
              style: Obsidian.dataTable(size: 11.5, color: Obsidian.outline)),
        ],
        if (j.note.isNotEmpty) ...[
          const SizedBox(height: 8),
          Text(j.note,
              textAlign: TextAlign.center,
              style: Obsidian.body(
                  color: j.atChance ? Obsidian.amber : Obsidian.outline,
                  size: 11)),
        ],
        const SizedBox(height: 10),
        TextButton(
          onPressed: _busy ? null : _start,
          child: Text('Train again',
              style: Obsidian.body(color: Obsidian.primary, size: 12.5)),
        ),
      ],
    );
  }
}
