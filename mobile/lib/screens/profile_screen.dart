/// Profile, carrying the "Unlock Quantum Flux Pro" panel from the mockup.
///
/// The panel is built to the design. What is NOT built is a purchase flow —
/// there is no billing backend, no store product and no entitlement server,
/// so a working-looking Subscribe button would be a button that takes money
/// for nothing. It explains its own state instead.
///
/// There is also a design question worth leaving on the screen rather than
/// burying: tiering by *precision* means deliberately serving numbers you
/// know are worse to people who paid less. Tiering by coverage — more pairs,
/// more timeframes, alert latency, history — sells the same honest number to
/// everyone. The panel lists the second kind.
library;

import 'package:flutter/material.dart';

import '../api/client.dart';
import '../api/settings.dart';
import '../theme/liquid_obsidian.dart';
import '../widgets/glass.dart';
import '../widgets/status_dot.dart';

class ProfileScreen extends StatefulWidget {
  const ProfileScreen({super.key, required this.client});

  final ApiClient client;

  @override
  State<ProfileScreen> createState() => _ProfileScreenState();
}

class _ProfileScreenState extends State<ProfileScreen> {
  bool _linked = false;
  bool _checking = true;

  @override
  void initState() {
    super.initState();
    _probe();
  }

  Future<void> _probe() async {
    setState(() => _checking = true);
    try {
      await widget.client.coins();
      if (!mounted) return;
      setState(() {
        _linked = true;
        _checking = false;
      });
    } catch (_) {
      if (!mounted) return;
      setState(() {
        _linked = false;
        _checking = false;
      });
    }
  }

  Future<void> _editHost() async {
    final ctrl = TextEditingController(text: widget.client.base);
    final saved = await showDialog<String>(
      context: context,
      builder: (ctx) => AlertDialog(
        backgroundColor: Obsidian.surfaceContainer,
        shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(Obsidian.rLg)),
        title: Text('API host', style: Obsidian.headlineMd()),
        content: GlassField(controller: ctrl, hint: 'http://192.168.1.20:8787'),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(ctx),
              child: Text('Cancel', style: Obsidian.body())),
          TextButton(
              onPressed: () => Navigator.pop(ctx, ctrl.text.trim()),
              child:
                  Text('Save', style: Obsidian.body(color: Obsidian.primary))),
        ],
      ),
    );
    if (saved != null && saved.isNotEmpty) {
      widget.client.base = saved;
      await Settings.instance.saveBase(saved);
      await _probe();
    }
  }

  Future<void> _editToken() async {
    final ctrl = TextEditingController(text: widget.client.token);
    final saved = await showDialog<String>(
      context: context,
      builder: (ctx) => AlertDialog(
        backgroundColor: Obsidian.surfaceContainer,
        shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(Obsidian.rLg)),
        title: Text('Bearer token', style: Obsidian.headlineMd()),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              'Only needed when the backend runs on a server. It is the '
              'TRADINGBOT_TOKEN from that machine\'s .env file — leave it '
              'empty when the backend is on your own computer.',
              style: Obsidian.body(size: 12.5),
            ),
            const SizedBox(height: 16),
            GlassField(controller: ctrl, hint: 'paste the token'),
          ],
        ),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(ctx),
              child: Text('Cancel', style: Obsidian.body())),
          TextButton(
              onPressed: () => Navigator.pop(ctx, ctrl.text.trim()),
              child:
                  Text('Save', style: Obsidian.body(color: Obsidian.primary))),
        ],
      ),
    );
    if (saved != null) {
      widget.client.token = saved;
      await Settings.instance.saveToken(saved);
      await _probe();
    }
  }

  @override
  Widget build(BuildContext context) {
    return ListView(
      // addRepaintBoundaries: a BackdropFilter samples what is painted
      // BEHIND it, and ListView puts every child in its own RepaintBoundary
      // by default. Inside that layer the backdrop is empty, so the glass
      // panels blur nothing and paint nothing — the screen comes up blank
      // with no error anywhere. Opting out gives the filter a real backdrop.
      addRepaintBoundaries: false,
      padding: const EdgeInsets.fromLTRB(Obsidian.containerPadding, 8,
          Obsidian.containerPadding, Obsidian.navClearance + 24),
      children: [
        GlassPanel(
          active: true,
          radius: Obsidian.rXl,
          padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 32),
          child: Column(
            children: [
              Container(
                width: 120,
                height: 120,
                decoration: BoxDecoration(
                  shape: BoxShape.circle,
                  border: Border.all(
                      color: Colors.white.withValues(alpha: 0.18)),
                  boxShadow:
                      Obsidian.glow(Obsidian.primary, opacity: 0.30, blur: 40),
                ),
                child: const Icon(Icons.lock_rounded,
                    size: 52, color: Obsidian.primary),
              ),
              const SizedBox(height: 26),
              RichText(
                textAlign: TextAlign.center,
                text: TextSpan(children: [
                  TextSpan(text: 'Unlock ', style: Obsidian.displayLg()),
                  TextSpan(
                    text: 'Quantum Flux Pro',
                    style: Obsidian.displayLg(color: Obsidian.primary).copyWith(
                      shadows: [
                        BoxShadow(
                            color: Obsidian.primary.withValues(alpha: 0.5),
                            blurRadius: 24)
                      ],
                    ),
                  ),
                ]),
              ),
              const SizedBox(height: 14),
              Text(
                'Get access to advanced trading signals, more pairs and '
                'longer history.',
                textAlign: TextAlign.center,
                style: Obsidian.body(size: 15),
              ),
              const SizedBox(height: 24),
              ..._perk(Icons.donut_large_rounded, 'Every pair, not just BTC'),
              ..._perk(Icons.timelapse_rounded, 'All timeframes and horizons'),
              ..._perk(Icons.notifications_active_rounded,
                  'Push alerts the moment a spike fires'),
              ..._perk(Icons.receipt_long_rounded,
                  'Full prediction log with calibration'),
              const SizedBox(height: 22),
              Container(
                width: double.infinity,
                padding: const EdgeInsets.all(14),
                decoration: BoxDecoration(
                  color: Obsidian.surfaceLowest.withValues(alpha: 0.7),
                  borderRadius: BorderRadius.circular(Obsidian.rMd),
                  border: Border.all(
                      color: Colors.white.withValues(alpha: 0.08)),
                ),
                child: Text(
                  'No billing is wired up. There is no store product and no '
                  'entitlement server, so nothing here can charge you — the '
                  'panel is the design, not a checkout.',
                  style: Obsidian.body(color: Obsidian.outline, size: 12),
                ),
              ),
            ],
          ),
        ),
        const SizedBox(height: Obsidian.gutter),
        GlassPanel(
          padding: EdgeInsets.zero,
          child: Column(
            children: [
              _tile(
                icon: _checking ? Icons.sync_rounded : Icons.dns_rounded,
                title: 'API host',
                subtitle: widget.client.base,
                trailing: _checking
                    ? const SizedBox(
                        width: 14,
                        height: 14,
                        child: CircularProgressIndicator(
                            strokeWidth: 2, color: Obsidian.primary))
                    : StatusDot(live: _linked),
                onTap: _editHost,
              ),
              Divider(
                  height: 1,
                  thickness: 1,
                  color: Colors.white.withValues(alpha: 0.05)),
              _tile(
                icon: Icons.key_rounded,
                title: 'Bearer token',
                subtitle: widget.client.token.isEmpty
                    ? 'none — backend is on this network'
                    : '•' * 12,
                onTap: _editToken,
              ),
              Divider(
                  height: 1,
                  thickness: 1,
                  color: Colors.white.withValues(alpha: 0.05)),
              _tile(
                icon: Icons.gavel_rounded,
                title: 'What this app does',
                subtitle: 'Reads and advises. It never places an order.',
              ),
            ],
          ),
        ),
      ],
    );
  }

  List<Widget> _perk(IconData icon, String text) => [
        Padding(
          padding: const EdgeInsets.only(bottom: 12),
          child: Row(
            children: [
              Icon(icon, size: 18, color: Obsidian.green),
              const SizedBox(width: 12),
              Expanded(child: Text(text, style: Obsidian.body(size: 14))),
            ],
          ),
        ),
      ];

  Widget _tile({
    required IconData icon,
    required String title,
    required String subtitle,
    Widget? trailing,
    VoidCallback? onTap,
  }) =>
      InkWell(
        onTap: onTap,
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 18, vertical: 16),
          child: Row(
            children: [
              Icon(icon, size: 20, color: Obsidian.onSurfaceVariant),
              const SizedBox(width: 14),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(title, style: Obsidian.bodyLg()),
                    const SizedBox(height: 3),
                    Text(subtitle,
                        style: Obsidian.body(size: 12),
                        overflow: TextOverflow.ellipsis),
                  ],
                ),
              ),
              ?trailing,
            ],
          ),
        ),
      );
}
