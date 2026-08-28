/// The "Secure Neural Trading Link" screen.
///
/// HONESTY NOTE, because this screen looks like an auth wall and is not one.
/// There is no account server in this project — nothing issues credentials,
/// nothing verifies them. So the two fields are a LOCAL session label only:
/// what you type is held in memory, is never transmitted, and is never
/// persisted. Building a convincing credential form that quietly posted a
/// password to something would be worse than building nothing.
///
/// What IS real here is the status row. "Network Link Secure" pings the
/// Python service and reports what actually came back, and tapping it opens
/// the host editor — which is the one genuinely necessary setting, since a
/// physical phone has to be pointed at the Mac's LAN address.
library;

import 'package:flutter/material.dart';

import '../api/client.dart';
import '../theme/liquid_obsidian.dart';
import '../widgets/glass.dart';
import '../widgets/mesh_background.dart';
import '../widgets/status_dot.dart';

class LoginScreen extends StatefulWidget {
  const LoginScreen({super.key, required this.client, required this.onEnter});

  final ApiClient client;
  final VoidCallback onEnter;

  @override
  State<LoginScreen> createState() => _LoginScreenState();
}

class _LoginScreenState extends State<LoginScreen> {
  final _id = TextEditingController();
  final _key = TextEditingController();
  bool _obscure = true;
  bool _probing = true;
  bool _linked = false;
  String _linkDetail = 'checking link...';

  @override
  void initState() {
    super.initState();
    _probe();
  }

  @override
  void dispose() {
    _id.dispose();
    _key.dispose();
    super.dispose();
  }

  Future<void> _probe() async {
    setState(() {
      _probing = true;
      _linkDetail = 'checking link...';
    });
    try {
      await widget.client.coins();
      if (!mounted) return;
      setState(() {
        _linked = true;
        _probing = false;
        _linkDetail = 'Network Link Secure';
      });
    } catch (_) {
      if (!mounted) return;
      setState(() {
        _linked = false;
        _probing = false;
        _linkDetail = 'No Link — tap to set host';
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
        content: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              'Run  python3 serve.py  on your Mac. It prints the address to '
              'use for the simulator, the emulator and a physical phone.',
              style: Obsidian.body(),
            ),
            const SizedBox(height: 16),
            GlassField(controller: ctrl, hint: 'http://192.168.1.20:8787'),
          ],
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx),
            child: Text('Cancel', style: Obsidian.body()),
          ),
          TextButton(
            onPressed: () => Navigator.pop(ctx, ctrl.text.trim()),
            child: Text('Save', style: Obsidian.body(color: Obsidian.primary)),
          ),
        ],
      ),
    );
    if (saved != null && saved.isNotEmpty) {
      widget.client.base = saved;
      await _probe();
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: MeshBackground(
        child: SafeArea(
          child: Center(
            child: SingleChildScrollView(
              padding: const EdgeInsets.all(Obsidian.containerPadding),
              child: Column(
                mainAxisAlignment: MainAxisAlignment.center,
                children: [
                  Text('SECURE NEURAL TRADING LINK',
                      textAlign: TextAlign.center,
                      style: Obsidian.labelSm(color: Obsidian.primary, size: 13)),
                  const SizedBox(height: 28),
                  GlassPanel(
                    padding: const EdgeInsets.all(24),
                    radius: Obsidian.rXl,
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.stretch,
                      children: [
                        _label(Icons.mail_outline_rounded, 'IDENTIFIER'),
                        const SizedBox(height: 10),
                        GlassField(
                            controller: _id, hint: 'Enter your credentials'),
                        const SizedBox(height: 22),
                        Row(
                          mainAxisAlignment: MainAxisAlignment.spaceBetween,
                          children: [
                            _label(Icons.lock_outline_rounded, 'SECURITY KEY'),
                            Text('Recover Key?',
                                style: Obsidian.labelSm(
                                    color: Obsidian.primary, size: 12)),
                          ],
                        ),
                        const SizedBox(height: 10),
                        GlassField(
                          controller: _key,
                          obscure: _obscure,
                          hint: '••••••••••',
                          trailing: IconButton(
                            icon: Icon(
                                _obscure
                                    ? Icons.visibility_off_outlined
                                    : Icons.visibility_outlined,
                                color: Obsidian.outline),
                            onPressed: () =>
                                setState(() => _obscure = !_obscure),
                          ),
                        ),
                        const SizedBox(height: 10),
                        Text(
                          'Local session only — no account server exists yet, '
                          'and nothing you type here leaves the device.',
                          style: Obsidian.body(
                              color: Obsidian.outline, size: 11.5),
                        ),
                        const SizedBox(height: 20),
                        _linkRow(),
                        const SizedBox(height: 24),
                        _connectButton(),
                        const SizedBox(height: 20),
                        Divider(color: Colors.white.withValues(alpha: 0.08)),
                        const SizedBox(height: 14),
                        Center(
                          child: Wrap(
                            alignment: WrapAlignment.center,
                            children: [
                              Text('No active node? ', style: Obsidian.body()),
                              Text('Register New Node',
                                  style: Obsidian.bodyLg(
                                          color: Obsidian.primary)
                                      .copyWith(
                                          fontWeight: FontWeight.w700,
                                          decoration:
                                              TextDecoration.underline,
                                          decorationColor: Obsidian.primary)),
                            ],
                          ),
                        ),
                      ],
                    ),
                  ),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }

  Widget _label(IconData icon, String text) => Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(icon, size: 18, color: Obsidian.onSurfaceVariant),
          const SizedBox(width: 8),
          Text(text, style: Obsidian.labelSm(size: 13)),
        ],
      );

  Widget _linkRow() => InkWell(
        onTap: _editHost,
        borderRadius: BorderRadius.circular(Obsidian.rMd),
        child: Container(
          padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
          decoration: BoxDecoration(
            color: Obsidian.surfaceLowest.withValues(alpha: 0.6),
            borderRadius: BorderRadius.circular(Obsidian.rMd),
            border:
                Border.all(color: Colors.white.withValues(alpha: 0.08)),
          ),
          child: Row(
            children: [
              if (_probing)
                const SizedBox(
                    width: 12,
                    height: 12,
                    child: CircularProgressIndicator(
                        strokeWidth: 2, color: Obsidian.primary))
              else
                StatusDot(live: _linked),
              const SizedBox(width: 12),
              Expanded(
                child: Text(_linkDetail,
                    style: Obsidian.dataTable(
                        size: 13,
                        color: _linked
                            ? Obsidian.onSurface
                            : Obsidian.onSurfaceVariant)),
              ),
              Icon(Icons.tune_rounded, size: 16, color: Obsidian.outline),
            ],
          ),
        ),
      );

  Widget _connectButton() => Container(
        decoration: BoxDecoration(
          borderRadius: BorderRadius.circular(Obsidian.rLg),
          boxShadow: Obsidian.glow(Obsidian.primary, opacity: 0.35, blur: 26),
        ),
        child: FilledButton(
          style: FilledButton.styleFrom(
            backgroundColor: Obsidian.primary,
            foregroundColor: Obsidian.onPrimary,
            minimumSize: const Size.fromHeight(60),
            shape: RoundedRectangleBorder(
                borderRadius: BorderRadius.circular(Obsidian.rLg)),
          ),
          onPressed: widget.onEnter,
          child: Row(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              Text('INITIALIZE CONNECTION',
                  style: Obsidian.bodyLg(color: Obsidian.onPrimary)
                      .copyWith(
                          fontWeight: FontWeight.w700, letterSpacing: 0.5)),
              const SizedBox(width: 10),
              const Icon(Icons.arrow_forward_rounded, size: 20),
            ],
          ),
        ),
      );
}
