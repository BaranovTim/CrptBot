/// The paywall — the only screen an unsubscribed account can reach.
///
/// WHAT IT SHOWS AND WHY
///     The price, what the subscription unlocks, and the accuracy disclosure,
///     all served by the API rather than hardcoded here. A price baked into
///     the app is a price that disagrees with Stripe the moment either
///     changes, and the person looking at the disagreement is the one about
///     to pay.
///
/// THE DISCLOSURE IS NOT OPTIONAL
///     The backtested accuracy of these models is at or near chance. Anyone
///     being asked for money is entitled to read that before deciding, on the
///     same screen as the price rather than buried in a settings page. It
///     comes down from the server with the plans so it cannot drift out of
///     sync with what is actually true.
library;

import 'package:flutter/material.dart';
import 'package:url_launcher/url_launcher.dart';

import '../api/client.dart';
import '../api/models.dart';
import '../theme/liquid_obsidian.dart';
import '../widgets/glass.dart';

class SubscribeScreen extends StatefulWidget {
  const SubscribeScreen({
    super.key,
    required this.client,
    required this.account,
    required this.onSignOut,
    required this.onCheckoutStarted,
  });

  final ApiClient client;
  final Account account;
  final VoidCallback onSignOut;
  /// Called once the customer has been handed to Stripe.
  ///
  /// NOT "payment finished" -- nothing here can know that. It tells the
  /// shell to start watching for the upgrade when the app comes back.
  final VoidCallback onCheckoutStarted;

  @override
  State<SubscribeScreen> createState() => _SubscribeScreenState();
}

class _SubscribeScreenState extends State<SubscribeScreen> {
  List<dynamic> _plans = const [];
  bool _configured = false;
  bool _loading = true;
  String? _error;
  String _picked = 'monthly';

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final j = await widget.client.plans();
      if (!mounted) return;
      setState(() {
        _plans = (j['plans'] as List?) ?? const [];
        _configured = j['configured'] as bool? ?? false;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = '$e';
        _loading = false;
      });
    }
  }

  Future<void> _buy() async {
    try {
      final url = await widget.client.checkout(_picked);
      final uri = Uri.parse(url);
      if (!await launchUrl(uri, mode: LaunchMode.externalApplication)) {
        throw ApiException('Could not open the payment page.');
      }
      // `launchUrl` completes when the BROWSER OPENS, not when the customer
      // comes back -- so asking the server about the account here would run
      // while they were still typing their card number, get "free", and
      // leave the paywall up over a payment that then succeeded.
      //
      // The shell watches for it on resume instead. The answer has to come
      // from the server either way: Stripe tells it, not us, and trusting
      // the browser's return would let anyone grant themselves a
      // subscription by opening a URL.
      widget.onCheckoutStarted();
    } catch (e) {
      if (!mounted) return;
      showDialog<void>(
        context: context,
        builder: (ctx) => AlertDialog(
          backgroundColor: Obsidian.surfaceContainer,
          title: Text('Not available yet', style: Obsidian.headlineMd()),
          content: Text('$e', style: Obsidian.body()),
          actions: [
            TextButton(
                onPressed: () => Navigator.pop(ctx),
                child: Text('OK', style: Obsidian.body())),
          ],
        ),
      );
    }
  }

  String _price(Map p) {
    final amount = (p['amount'] as num? ?? 0) / 100.0;
    final cur = (p['currency'] as String? ?? 'eur').toUpperCase();
    final sym = cur == 'EUR' ? '€' : (cur == 'USD' ? '\$' : '$cur ');
    return '$sym${amount.toStringAsFixed(amount % 1 == 0 ? 0 : 2)}';
  }

  @override
  Widget build(BuildContext context) {
    if (_loading) {
      return const Center(
          child: CircularProgressIndicator(color: Obsidian.primary));
    }

    final included = _plans.isEmpty
        ? const <dynamic>[]
        : (_plans.first['included'] as List?) ?? const [];
    final disclosure =
        _plans.isEmpty ? '' : (_plans.first['disclosure'] as String? ?? '');

    return ListView(
      addRepaintBoundaries: false,
      padding: const EdgeInsets.fromLTRB(Obsidian.containerPadding, 8,
          Obsidian.containerPadding, Obsidian.navClearance + 24),
      children: [
        Text('Unlock ThusIldy', style: Obsidian.displayLg()),
        const SizedBox(height: 6),
        Text(
            widget.account.identifier.isEmpty
                ? 'Signed in'
                : 'Signed in as ${widget.account.identifier}',
            style: Obsidian.body(color: Obsidian.outline)),
        const SizedBox(height: 22),

        if (_error != null) ...[
          GlassPanel(
            child: Text(_error!,
                style: Obsidian.body(color: Obsidian.redSoft, size: 12.5)),
          ),
          const SizedBox(height: Obsidian.gutter),
        ],

        for (final p in _plans) ...[
          _planCard(p as Map),
          const SizedBox(height: Obsidian.gutter),
        ],

        const SizedBox(height: 6),
        GlassPanel(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text('WHAT YOU GET', style: Obsidian.labelSm(size: 10.5)),
              const SizedBox(height: 14),
              for (final line in included) ...[
                Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    const Padding(
                      padding: EdgeInsets.only(top: 2),
                      child: Icon(Icons.check_rounded,
                          size: 15, color: Obsidian.greenDim),
                    ),
                    const SizedBox(width: 10),
                    Expanded(
                        child: Text('$line',
                            style: Obsidian.body(size: 13))),
                  ],
                ),
                const SizedBox(height: 10),
              ],
            ],
          ),
        ),
        const SizedBox(height: Obsidian.gutter),

        SizedBox(
          height: 54,
          child: FilledButton(
            onPressed: _buy,
            style: FilledButton.styleFrom(
              backgroundColor: _configured
                  ? Obsidian.primaryContainer
                  : Obsidian.surfaceHigh,
              shape: RoundedRectangleBorder(
                  borderRadius: BorderRadius.circular(Obsidian.rLg)),
            ),
            child: Text(
                _configured ? 'SUBSCRIBE' : 'PAYMENTS NOT SET UP YET',
                style: Obsidian.labelSm(
                    color: _configured ? Colors.white : Obsidian.outline,
                    size: 12.5)),
          ),
        ),

        if (!_configured) ...[
          const SizedBox(height: 12),
          Text(
              'The owner has not connected a payment provider yet. Access can '
              'still be granted directly on the server.',
              textAlign: TextAlign.center,
              style: Obsidian.body(color: Obsidian.outline, size: 11.5)),
        ],

        const SizedBox(height: 22),
        if (disclosure.isNotEmpty)
          GlassPanel(
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const Padding(
                  padding: EdgeInsets.only(top: 1),
                  child: Icon(Icons.info_outline_rounded,
                      size: 16, color: Obsidian.redSoft),
                ),
                const SizedBox(width: 10),
                Expanded(
                  child: Text(disclosure,
                      style: Obsidian.body(
                          color: Obsidian.outline, size: 11.5)),
                ),
              ],
            ),
          ),

        const SizedBox(height: 18),
        Center(
          child: TextButton(
            onPressed: widget.onSignOut,
            child: Text('Sign out',
                style: Obsidian.body(color: Obsidian.outline, size: 12.5)),
          ),
        ),
      ],
    );
  }

  Widget _planCard(Map p) {
    final id = p['id'] as String? ?? '';
    final selected = id == _picked;
    return GlassPanel(
      active: selected,
      glow: selected ? Obsidian.primaryContainer : null,
      onTap: () => setState(() => _picked = id),
      child: Row(
        children: [
          Icon(
              selected
                  ? Icons.radio_button_checked_rounded
                  : Icons.radio_button_unchecked_rounded,
              size: 20,
              color: selected ? Obsidian.primary : Obsidian.outline),
          const SizedBox(width: 14),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text('${p['name']}',
                    style: Obsidian.bodyLg()
                        .copyWith(fontWeight: FontWeight.w600)),
                if (p['note'] != null) ...[
                  const SizedBox(height: 4),
                  Text('${p['note']}',
                      style: Obsidian.labelSm(
                          color: Obsidian.greenDim, size: 10)),
                ],
              ],
            ),
          ),
          Column(
            crossAxisAlignment: CrossAxisAlignment.end,
            children: [
              Text(_price(p),
                  style: Obsidian.dataTable(size: 20, w: FontWeight.w700)),
              Text('per ${p['period']}',
                  style: Obsidian.labelSm(size: 9.5)),
            ],
          ),
        ],
      ),
    );
  }
}
