/// Account, subscription, alerts, connection.
///
/// The previous version of this screen was mostly a mock: a "Pro" panel with
/// perks, and a note admitting nothing could charge you. There is a real
/// account behind it now, so the screen shows real state — who you are, what
/// your subscription actually says, and whether this phone can reach the
/// server — rather than an aspirational layout.
///
/// Everything shown here is asked of the server or read from this device.
/// Nothing is inferred: an app that decides locally what tier you are is an
/// app that can be edited into deciding differently.
library;

import 'package:flutter/material.dart';

import '../api/client.dart';
import '../api/models.dart';
import '../api/muted.dart';
import '../api/notifications.dart';
import '../api/settings.dart';
import '../api/watchlist.dart';
import '../theme/liquid_obsidian.dart';
import '../widgets/glass.dart';
import '../widgets/status_dot.dart';

class ProfileScreen extends StatefulWidget {
  const ProfileScreen({
    super.key,
    required this.client,
    required this.account,
    required this.onSignOut,
  });

  final ApiClient client;
  final Account account;
  final VoidCallback onSignOut;

  @override
  State<ProfileScreen> createState() => _ProfileScreenState();
}

class _ProfileScreenState extends State<ProfileScreen> {
  bool _checking = true;
  bool _linked = false;
  bool _testing = false;
  int _muted = 0;

  @override
  void initState() {
    super.initState();
    _probe();
    _countMuted();
  }

  Future<void> _probe() async {
    setState(() => _checking = true);
    var ok = false;
    try {
      await widget.client.health();
      ok = true;
    } catch (_) {
      ok = false;
    }
    if (!mounted) return;
    setState(() {
      _linked = ok;
      _checking = false;
    });
  }

  Future<void> _countMuted() async {
    final muted = await Muted.instance.load();
    final following = await Watchlist.instance.load();
    if (!mounted) return;
    // count only pairs still followed: a mute left over from a coin you
    // dropped is not something to report as active
    setState(() =>
        _muted = following.where((s) => muted.contains(s)).length);
  }

  /// Fire one of every notification kind so they can be seen for real.
  ///
  /// Keep the app open while this runs: the spacing between them is an
  /// `await`, not a scheduled alarm. Scheduling was tried first and Android
  /// silently never delivered it — see `sendTestSuite`.
  Future<void> _sendTests() async {
    if (!Notifications.instance.granted) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(
        backgroundColor: Obsidian.surfaceHigh,
        content: Text(
            'Notifications are turned off for ThusIldy in system settings, '
            'so nothing would be delivered.',
            style: Obsidian.body()),
      ));
      return;
    }
    final n = await Notifications.instance.sendTestSuite();
    if (!mounted) return;
    setState(() => _testing = true);
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(
      backgroundColor: Obsidian.surfaceHigh,
      duration: const Duration(seconds: 6),
      content: Text(
          '$n test alerts booked. Lock your phone now — the first arrives in '
          '8 seconds, then one every 7.',
          style: Obsidian.body()),
    ));
  }

  Future<void> _editHost() async {
    final ctrl = TextEditingController(text: widget.client.base);
    final saved = await _prompt(
      title: 'Server address',
      body: 'Where your ThusIldy server is reachable. Use the https:// '
          'address — over plain http your password and session token travel '
          'in clear text.',
      ctrl: ctrl,
      hint: 'https://your-server',
    );
    if (saved == null) return;
    widget.client.base = saved;
    await Settings.instance.saveBase(saved);
    await _probe();
  }

  Future<void> _editToken() async {
    final ctrl = TextEditingController(text: widget.client.token);
    final saved = await _prompt(
      title: 'Access token',
      body: 'Normally this is your sign-in session and you never touch it. '
          'The operator key from the server\'s .env goes here instead if you '
          'are connecting as the owner.',
      ctrl: ctrl,
      hint: 'paste the token',
      obscure: true,
    );
    if (saved == null) return;
    widget.client.token = saved;
    await Settings.instance.saveToken(saved);
    await _probe();
  }

  Future<String?> _prompt({
    required String title,
    required String body,
    required TextEditingController ctrl,
    required String hint,
    bool obscure = false,
  }) =>
      showDialog<String>(
        context: context,
        builder: (ctx) => AlertDialog(
          backgroundColor: Obsidian.surfaceContainer,
          shape: RoundedRectangleBorder(
              borderRadius: BorderRadius.circular(Obsidian.rLg)),
          title: Text(title, style: Obsidian.headlineMd()),
          content: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(body, style: Obsidian.body()),
              const SizedBox(height: 16),
              GlassField(controller: ctrl, hint: hint, obscure: obscure),
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
      ).then((v) => (v == null || v.isEmpty) ? null : v);

  Future<void> _confirmSignOut() async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        backgroundColor: Obsidian.surfaceContainer,
        title: Text('Sign out?', style: Obsidian.headlineMd()),
        content: Text(
            'Your watchlist and alert settings stay on this device. You will '
            'need your password to sign back in.',
            style: Obsidian.body(color: Obsidian.outline)),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(ctx, false),
              child: Text('Stay', style: Obsidian.body())),
          TextButton(
              onPressed: () => Navigator.pop(ctx, true),
              child: Text('Sign out',
                  style: Obsidian.body(color: Obsidian.redSoft))),
        ],
      ),
    );
    if (ok == true) widget.onSignOut();
  }

  // ------------------------------------------------------------- rendering
  @override
  Widget build(BuildContext context) {
    final a = widget.account;
    return RefreshIndicator(
      onRefresh: () async {
        await _probe();
        await _countMuted();
      },
      backgroundColor: Obsidian.surfaceContainer,
      color: Obsidian.primary,
      child: ListView(
        addRepaintBoundaries: false,
        physics: const AlwaysScrollableScrollPhysics(),
        padding: const EdgeInsets.fromLTRB(Obsidian.containerPadding, 8,
            Obsidian.containerPadding, Obsidian.navClearance + 24),
        children: [
          Text('Profile', style: Obsidian.displayLg()),
          const SizedBox(height: 20),
          _identityCard(a),
          const SizedBox(height: Obsidian.gutter),
          _section('SUBSCRIPTION', [
            _tile(
              icon: a.tier == 'admin'
                  ? Icons.verified_user_rounded
                  : Icons.workspace_premium_rounded,
              title: _tierName(a),
              subtitle: _tierDetail(a),
              trailing: StatusDot(live: a.entitled),
            ),
          ]),
          const SizedBox(height: Obsidian.gutter),
          _section('ALERTS', [
            _tile(
              icon: Notifications.instance.granted
                  ? Icons.notifications_active_rounded
                  : Icons.notifications_off_rounded,
              title: 'System permission',
              subtitle: Notifications.instance.granted
                  ? 'Granted — alerts can reach this phone'
                  : 'Denied in system settings; nothing will be delivered',
              trailing: StatusDot(live: Notifications.instance.granted),
            ),
            _divider(),
            _tile(
              icon: Icons.send_rounded,
              title: 'Send test notifications',
              subtitle: _testing
                  ? 'Sent — pull down the shade to see them'
                  : 'One of each kind, right now',
              trailing: _testing
                  ? const Icon(Icons.check_rounded,
                      size: 18, color: Obsidian.greenDim)
                  : const Icon(Icons.chevron_right_rounded,
                      size: 18, color: Obsidian.outline),
              onTap: _sendTests,
            ),
            _divider(),
            _tile(
              icon: Icons.notifications_paused_rounded,
              title: 'Silenced pairs',
              subtitle: _muted == 0
                  ? 'None — every followed pair can alert you'
                  : '$_muted of your pairs are muted',
              trailing: Text('$_muted',
                  style: Obsidian.dataTable(size: 15, w: FontWeight.w700)),
            ),
          ]),
          const SizedBox(height: Obsidian.gutter),
          _section('CONNECTION', [
            _tile(
              icon: _checking ? Icons.sync_rounded : Icons.dns_rounded,
              title: 'Server address',
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
            _divider(),
            _tile(
              icon: Icons.key_rounded,
              title: 'Access token',
              subtitle: widget.client.token.isEmpty
                  ? 'none — server is on this network'
                  : '•' * 16,
              onTap: _editToken,
            ),
          ]),
          const SizedBox(height: Obsidian.gutter),
          _section('ABOUT', [
            _tile(
              icon: Icons.gavel_rounded,
              title: 'What ThusIldy does',
              subtitle: 'Reads and analyses. It never places an order, holds '
                  'a key, or moves money.',
            ),
            _divider(),
            _tile(
              icon: Icons.query_stats_rounded,
              title: 'Accuracy',
              subtitle: 'Most timeframes backtest at or near chance (AUC '
                  '0.46–0.53, where 0.5 is a coin flip). The 1h models are '
                  'the exception at 0.52–0.54 across four assets. Small and '
                  'consistent is not the same as reliable.',
            ),
          ]),
          const SizedBox(height: 26),
          SizedBox(
            height: 50,
            child: OutlinedButton(
              onPressed: _confirmSignOut,
              style: OutlinedButton.styleFrom(
                side: BorderSide(color: Colors.white.withValues(alpha: 0.12)),
                shape: RoundedRectangleBorder(
                    borderRadius: BorderRadius.circular(Obsidian.rLg)),
              ),
              child: Text('SIGN OUT',
                  style:
                      Obsidian.labelSm(color: Obsidian.redSoft, size: 12)),
            ),
          ),
        ],
      ),
    );
  }

  String _tierName(Account a) => switch (a.tier) {
        'admin' => 'Owner',
        'pro' => a.entitled ? 'Subscribed' : 'Subscription lapsed',
        _ => 'Free',
      };

  String _tierDetail(Account a) {
    if (a.tier == 'admin') {
      return 'Full access, no billing, never expires';
    }
    final ends = a.subscriptionEnds;
    if (a.tier == 'pro' && ends != null) {
      final d = ends.toLocal();
      final when = '${d.year}-${d.month.toString().padLeft(2, '0')}-'
          '${d.day.toString().padLeft(2, '0')}';
      return a.entitled ? 'Active until $when' : 'Ended $when';
    }
    if (a.entitled) return 'Active';
    return 'Chart only — the analysis needs a subscription';
  }

  Widget _identityCard(Account a) => GlassPanel(
        padding: const EdgeInsets.all(20),
        child: Row(
          children: [
            Container(
              width: 58,
              height: 58,
              decoration: BoxDecoration(
                shape: BoxShape.circle,
                color: Obsidian.surfaceLowest,
                border:
                    Border.all(color: Colors.white.withValues(alpha: 0.10)),
              ),
              alignment: Alignment.center,
              child: Text(
                  a.operator
                      ? '★'
                      : (a.identifier.isEmpty
                          ? '?'
                          : a.identifier.characters.first.toUpperCase()),
                  style: Obsidian.headlineMd()),
            ),
            const SizedBox(width: 16),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(a.operator ? 'Operator' : a.identifier,
                      style: Obsidian.headlineMd()),
                  const SizedBox(height: 6),
                  Container(
                    padding: const EdgeInsets.symmetric(
                        horizontal: 9, vertical: 4),
                    decoration: BoxDecoration(
                      color: (a.entitled ? Obsidian.greenDim : Obsidian.outline)
                          .withValues(alpha: 0.15),
                      borderRadius: BorderRadius.circular(6),
                    ),
                    child: Text(_tierName(a).toUpperCase(),
                        style: Obsidian.labelSm(
                            size: 9.5,
                            color: a.entitled
                                ? Obsidian.greenDim
                                : Obsidian.outline)),
                  ),
                ],
              ),
            ),
          ],
        ),
      );

  Widget _section(String label, List<Widget> rows) => Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Padding(
            padding: const EdgeInsets.only(left: 4, bottom: 10),
            child: Text(label, style: Obsidian.labelSm(size: 10.5)),
          ),
          GlassPanel(
            padding: EdgeInsets.zero,
            child: Column(children: rows),
          ),
        ],
      );

  Widget _divider() => Divider(
      height: 1, thickness: 1, color: Colors.white.withValues(alpha: 0.05));

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
          padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 15),
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Icon(icon, size: 19, color: Obsidian.outline),
              const SizedBox(width: 14),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(title,
                        style: Obsidian.bodyLg()
                            .copyWith(fontWeight: FontWeight.w600)),
                    const SizedBox(height: 4),
                    Text(subtitle,
                        style: Obsidian.body(
                            color: Obsidian.outline, size: 12)),
                  ],
                ),
              ),
              if (trailing != null) ...[
                const SizedBox(width: 12),
                trailing,
              ] else if (onTap != null) ...[
                const SizedBox(width: 12),
                const Icon(Icons.chevron_right_rounded,
                    size: 18, color: Obsidian.outline),
              ],
            ],
          ),
        ),
      );
}
