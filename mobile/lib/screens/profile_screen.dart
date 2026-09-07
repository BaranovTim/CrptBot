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
import 'package:flutter/services.dart' show Clipboard, ClipboardData;
import 'package:url_launcher/url_launcher.dart';

import '../api/background.dart';
import '../api/client.dart';
import '../api/models.dart';
import '../api/power.dart';
import '../api/muted.dart';
import '../api/notifications.dart';
import '../api/push.dart';
import '../api/settings.dart';
import '../api/trades.dart';
import '../api/watchlist.dart';
import '../theme/liquid_obsidian.dart';
import '../widgets/glass.dart';
import '../widgets/positions_panel.dart';
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
  String _sensitivity = 'strong';

  /// What the SERVER says about this account's relay, not what this device
  /// hopes. A topic sitting in local storage that the server has forgotten is
  /// the exact state that looks fine and delivers nothing.
  Map<String, dynamic>? _push;
  String? _pushTopic;
  bool _pushBusy = false;

  /// Whether Android is allowed to defer the alert poll, and when it last
  /// actually got to run. Both are measurements, not settings: without them
  /// "the job is throttled" and "the job is fine, nothing happened" look
  /// identical, and they need opposite responses.
  /// Every trade logged on this device, open and closed.
  List<TradeEntry> _trades = const [];

  bool _batteryExempt = true;
  DateTime? _bgLastRun;
  int _bgRuns = 0;

  @override
  void initState() {
    super.initState();
    _probe();
    _countMuted();
    _loadPush();
    _loadBackgroundHealth();
    _loadTrades();
    Settings.instance.sensitivity().then(
        (v) => mounted ? setState(() => _sensitivity = v) : null);
  }

  Future<void> _loadTrades() async {
    final all = await Trades.instance.load();
    if (!mounted) return;
    // Open first, then most recently closed. The ones you can still act on
    // are the ones worth putting at the top.
    all.sort((a, b) {
      if (a.isOpen != b.isOpen) return a.isOpen ? -1 : 1;
      return b.openedAt.compareTo(a.openedAt);
    });
    setState(() => _trades = all);
  }

  Future<void> _closeTrade(TradeEntry t) async {
    // NO LIVE PRICE HERE, on purpose. This screen holds no websocket, and
    // offering the dashboard's last known price for a different pair would
    // be worse than offering nothing. The field starts empty and you type
    // the fill you actually got.
    final price = await askExitPrice(context, t);
    if (price == null || price <= 0) return;
    await Trades.instance.close(t.id, price);
    await _loadTrades();
  }

  Future<void> _deleteTrade(TradeEntry t) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        backgroundColor: Obsidian.surfaceContainer,
        title: Text('Delete this entry?', style: Obsidian.headlineMd()),
        content: Text(
            'It is removed from your log for good. Closing a trade keeps it '
            'as a record; deleting it does not.',
            style: Obsidian.body(color: Obsidian.outline, size: 12.5)),
        actions: [
          TextButton(
              onPressed: () => Navigator.of(ctx).pop(false),
              child: Text('Keep', style: Obsidian.body())),
          TextButton(
              onPressed: () => Navigator.of(ctx).pop(true),
              child: Text('Delete',
                  style: Obsidian.body(color: Obsidian.redSoft))),
        ],
      ),
    );
    if (ok != true) return;
    await Trades.instance.remove(t.id);
    await _loadTrades();
  }

  Future<void> _loadBackgroundHealth() async {
    final exempt = await Power.instance.isExempt();
    final (last, runs) = await lastBackgroundRun();
    if (!mounted) return;
    setState(() {
      _batteryExempt = exempt;
      _bgLastRun = last;
      _bgRuns = runs;
    });
  }

  /// What the background-check tile says, which is the whole point of it.
  ///
  /// A fifteen-minute job that last ran four hours ago is not a mystery to
  /// investigate, it is a diagnosis on screen.
  String get _backgroundDetail {
    if (_bgLastRun == null) {
      return _bgRuns == 0
          ? 'Never run yet. On a fresh install that is normal for the first '
              'quarter of an hour.'
          : 'Ran $_bgRuns times, last time unknown.';
    }
    final ago = DateTime.now().difference(_bgLastRun!);
    final when = ago.inMinutes < 60
        ? '${ago.inMinutes}m ago'
        : ago.inHours < 48
            ? '${ago.inHours}h ago'
            : '${ago.inDays}d ago';
    final late = ago.inMinutes > 45
        ? ' — the job asks for every 15 minutes, so this one is being '
            'deferred.'
        : '';
    return 'Last checked $when · $_bgRuns times in total$late';
  }

  Future<void> _fixBattery() async {
    await Power.instance.requestExemption();
    // The dialog is another activity; re-read when we come back rather than
    // assuming the answer, because the user is free to decline it.
    await Future<void>.delayed(const Duration(milliseconds: 600));
    await _loadBackgroundHealth();
  }

  Future<void> _loadPush() async {
    final t = await PushDelivery.instance.topic();
    Map<String, dynamic>? sub;
    try {
      final r = await widget.client.pushStatus();
      sub = r['subscription'] as Map<String, dynamic>?;
    } catch (_) {
      sub = null;                     // offline; the tile says so
    }
    if (!mounted) return;
    setState(() {
      _pushTopic = t;
      _push = sub;
    });
  }

  Future<void> _togglePush() async {
    setState(() => _pushBusy = true);
    if (_push != null) {
      await PushDelivery.instance.disable(widget.client);
    } else {
      await PushDelivery.instance.enable(widget.client);
    }
    await _loadPush();
    if (mounted) setState(() => _pushBusy = false);
  }

  Future<void> _openPushSheet() async {
    if (_push == null) {
      await _togglePush();
      if (!mounted || _push == null) {
        if (mounted) {
          ScaffoldMessenger.of(context).showSnackBar(SnackBar(
            backgroundColor: Obsidian.surfaceHigh,
            content: Text(
                'Could not reach the server to set this up. Try again when '
                'the connection is back.',
                style: Obsidian.body()),
          ));
        }
        return;
      }
    }
    if (!mounted) return;
    await showModalBottomSheet<void>(
      context: context,
      backgroundColor: Colors.transparent,
      isScrollControlled: true,
      builder: (ctx) => _pushSheet(ctx),
    );
    await _loadPush();
  }

  Widget _pushSheet(BuildContext ctx) {
    final topic = _pushTopic ?? '';
    final server = (_push?['server'] as String?) ?? 'https://ntfy.sh';
    final url = PushDelivery.instance.subscribeUrl(topic, server);
    return SafeArea(
      top: false,
      child: Padding(
        padding: const EdgeInsets.all(Obsidian.containerPadding),
        child: GlassPanel(
          active: true,
          padding: const EdgeInsets.all(20),
          child: SingleChildScrollView(
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text('DELIVERY WITH THE APP CLOSED',
                    style: Obsidian.labelSm(size: 10.5)),
                const SizedBox(height: 12),
                // The honest explanation, in the place someone reads it.
                Text(
                    'Both phone systems hold this app\u2019s background checks '
                    'back while you are not using it — Android can defer them '
                    'for hours once the app drops into a low usage bucket, '
                    'and iOS runs them a few times a day at best. That is why '
                    'alerts kept arriving late, in a clump, the moment you '
                    'reopened the app.\n\n'
                    'The server now sends them to a free app called ntfy '
                    'instead, which is allowed to wake your phone whenever it '
                    'likes. Install ntfy, subscribe to the address below, and '
                    'alerts arrive about a minute after the server sees them '
                    '— whatever ThusIldy is doing.\n\n'
                    'On Android, turn on ntfy\u2019s "instant delivery" in its '
                    'own settings. That makes it about a second instead of '
                    'about a minute, and nothing on the phone can defer it.',
                    style: Obsidian.body(color: Obsidian.outline, size: 12.5)),
                const SizedBox(height: 18),
                Text('YOUR PRIVATE ADDRESS',
                    style: Obsidian.labelSm(size: 10.5)),
                const SizedBox(height: 8),
                Container(
                  width: double.infinity,
                  padding: const EdgeInsets.symmetric(
                      horizontal: 14, vertical: 12),
                  decoration: BoxDecoration(
                    color: Colors.white.withValues(alpha: 0.04),
                    borderRadius: BorderRadius.circular(Obsidian.rMd),
                  ),
                  child: SelectableText(topic,
                      style: Obsidian.dataTable(size: 12.5)),
                ),
                const SizedBox(height: 8),
                Text(
                    'Anyone who knows this can read your alerts, so it is a '
                    'random string rather than your name. Keep it to yourself.',
                    style: Obsidian.body(color: Obsidian.outline, size: 11)),
                const SizedBox(height: 16),
                Row(
                  children: [
                    Expanded(
                      child: OutlinedButton.icon(
                        onPressed: () async {
                          await Clipboard.setData(ClipboardData(text: topic));
                          if (ctx.mounted) Navigator.of(ctx).maybePop();
                        },
                        icon: const Icon(Icons.copy_rounded, size: 16),
                        label: Text('Copy', style: Obsidian.body(size: 12.5)),
                      ),
                    ),
                    const SizedBox(width: 10),
                    Expanded(
                      child: FilledButton.icon(
                        style: FilledButton.styleFrom(
                            backgroundColor: Obsidian.primary,
                            foregroundColor: Obsidian.onPrimary),
                        onPressed: () => launchUrl(Uri.parse(url),
                            mode: LaunchMode.externalApplication),
                        icon: const Icon(Icons.open_in_new_rounded, size: 16),
                        label:
                            Text('Open ntfy', style: Obsidian.body(size: 12.5)),
                      ),
                    ),
                  ],
                ),
                const SizedBox(height: 14),
                TextButton.icon(
                  onPressed: () async {
                    final ok =
                        await PushDelivery.instance.sendTest(widget.client);
                    if (!ctx.mounted) return;
                    ScaffoldMessenger.of(ctx).showSnackBar(SnackBar(
                      backgroundColor: Obsidian.surfaceHigh,
                      content: Text(
                          ok
                              ? 'Sent. If nothing arrives, ntfy is not '
                                  'subscribed to that address yet.'
                              : 'The server could not send it. Check the '
                                  'connection and try again.',
                          style: Obsidian.body()),
                    ));
                  },
                  icon: const Icon(Icons.send_rounded,
                      size: 16, color: Obsidian.primary),
                  label: Text('Send one test push',
                      style: Obsidian.body(color: Obsidian.primary, size: 12.5)),
                ),
                Divider(
                    height: 22, color: Colors.white.withValues(alpha: 0.06)),
                TextButton(
                  onPressed: () async {
                    await PushDelivery.instance.disable(widget.client);
                    if (ctx.mounted) Navigator.of(ctx).maybePop();
                  },
                  child: Text('Turn off server delivery',
                      style:
                          Obsidian.body(color: Obsidian.redSoft, size: 12.5)),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }

  /// What the delivery tile says underneath its title.
  String get _pushDetail {
    if (_push == null) {
      return _pushTopic == null
          ? 'Off. Alerts only arrive while the app is open, which on iOS is '
              'most of why they arrive late.'
          : 'Set up on this phone but not registered on the server — tap to '
              'register it again.';
    }
    final err = (_push!['last_error'] as String?) ?? '';
    final sent = (_push!['sent'] as num?)?.toInt() ?? 0;
    if (err.isNotEmpty) return 'Last send failed: $err';
    return sent == 0
        ? 'On. Nothing relayed yet — the next alert will be the first.'
        : 'On. $sent alert${sent == 1 ? '' : 's'} relayed so far.';
  }

  static const _levels = <String, (String, String)>{
    'strong': ('Strong only', 'Entries with a real margin over costs. '
        'Fewest calls, and the ones the model is most confident in.'),
    'medium': ('Medium and above', 'A thinner margin. More calls, each with '
        'less room for the model to be wrong.'),
    'small': ('Anything profitable', 'Every entry whose expected value clears '
        'fees at all. Most calls, thinnest edge.'),
  };

  Future<void> _pickSensitivity() async {
    final chosen = await showModalBottomSheet<String>(
      context: context,
      backgroundColor: Colors.transparent,
      builder: (ctx) => SafeArea(
        top: false,
        child: Padding(
          padding: const EdgeInsets.all(Obsidian.containerPadding),
          child: GlassPanel(
            active: true,
            padding: const EdgeInsets.all(20),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text('SIGNAL STRENGTH', style: Obsidian.labelSm(size: 10.5)),
                const SizedBox(height: 14),
                for (final e in _levels.entries) ...[
                  InkWell(
                    onTap: () => Navigator.pop(ctx, e.key),
                    child: Padding(
                      padding: const EdgeInsets.symmetric(vertical: 12),
                      child: Row(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Icon(
                              e.key == _sensitivity
                                  ? Icons.radio_button_checked_rounded
                                  : Icons.radio_button_unchecked_rounded,
                              size: 20,
                              color: e.key == _sensitivity
                                  ? Obsidian.primary
                                  : Obsidian.outline),
                          const SizedBox(width: 14),
                          Expanded(
                            child: Column(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: [
                                Text(e.value.$1,
                                    style: Obsidian.bodyLg().copyWith(
                                        fontWeight: FontWeight.w600)),
                                const SizedBox(height: 4),
                                Text(e.value.$2,
                                    style: Obsidian.body(
                                        color: Obsidian.outline, size: 12)),
                              ],
                            ),
                          ),
                        ],
                      ),
                    ),
                  ),
                  if (e.key != 'small')
                    Divider(
                        height: 1,
                        color: Colors.white.withValues(alpha: 0.05)),
                ],
                const SizedBox(height: 14),
                // Said plainly, because the obvious next question is "why is
                // there no looser option".
                Text(
                    'There is no weaker setting. Below this the model does '
                    'produce many more signals — and every one of them loses '
                    'money on average once fees are paid.',
                    style: Obsidian.body(color: Obsidian.outline, size: 11.5)),
              ],
            ),
          ),
        ),
      ),
    );
    if (chosen == null || !mounted) return;
    await Settings.instance.saveSensitivity(chosen);
    if (mounted) setState(() => _sensitivity = chosen);
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
          _section('SIGNALS', [
            _tile(
              icon: Icons.tune_rounded,
              title: 'Signal strength',
              subtitle: _levels[_sensitivity]?.$1 ?? 'Strong only',
              onTap: _pickSensitivity,
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
              icon: Icons.schedule_rounded,
              title: 'Background checks',
              subtitle: _backgroundDetail,
              trailing: StatusDot(
                  live: _bgLastRun != null &&
                      DateTime.now().difference(_bgLastRun!).inMinutes <= 45),
              onTap: _loadBackgroundHealth,
            ),
            if (Power.instance.supported && !_batteryExempt) ...[
              _divider(),
              _tile(
                icon: Icons.battery_alert_rounded,
                title: 'Battery optimisation is on',
                subtitle: 'Android is free to hold the alert check back for '
                    'hours while the phone is idle. Turning this off for '
                    'ThusIldy is the only fix on the device itself.',
                trailing: const Icon(Icons.chevron_right_rounded,
                    size: 18, color: Obsidian.amber),
                onTap: _fixBattery,
              ),
            ],
            _divider(),
            _tile(
              icon: _push != null
                  ? Icons.cloud_done_rounded
                  : Icons.cloud_off_rounded,
              title: 'Delivery with the app closed',
              subtitle: _pushDetail,
              trailing: _pushBusy
                  ? const SizedBox(
                      width: 14,
                      height: 14,
                      child: CircularProgressIndicator(
                          strokeWidth: 2, color: Obsidian.primary))
                  : StatusDot(live: _push != null),
              onTap: _pushBusy ? null : _openPushSheet,
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
          ..._tradesSection(),
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

  /// Your trade log: everything entered on this device, open first.
  ///
  /// LOGGED, NOT PLACED. Nothing in this app has ever sent an order — these
  /// are trades you made elsewhere and recorded here so they can be marked
  /// against the same prices the rest of the app uses. The empty state says
  /// so, because the empty state is where someone forms their idea of what
  /// the feature is.
  List<Widget> _tradesSection() {
    final open = _trades.where((t) => t.isOpen).length;
    final closed = _trades.length - open;
    final realised = _trades
        .where((t) => !t.isOpen)
        .map((t) => t.pnl(null) ?? 0)
        .fold<double>(0, (a, b) => a + b);

    return [
      Row(
        children: [
          Text('YOUR TRADES', style: Obsidian.labelSm(size: 10.5)),
          const Spacer(),
          if (_trades.isNotEmpty)
            Text(
                closed == 0
                    ? '$open open'
                    : '$open open · $closed closed · '
                        '${realised >= 0 ? '+' : ''}${money(realised, dp: 2)}',
                style: Obsidian.dataTable(
                    size: 11,
                    color: closed == 0
                        ? Obsidian.outline
                        : (realised >= 0 ? Obsidian.green : Obsidian.red))),
        ],
      ),
      const SizedBox(height: 10),
      if (_trades.isEmpty)
        GlassPanel(
          padding: const EdgeInsets.all(18),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(children: [
                const Icon(Icons.receipt_long_rounded,
                    size: 18, color: Obsidian.outline),
                const SizedBox(width: 10),
                Text('No trades logged yet',
                    style: Obsidian.body(size: 13.5)),
              ]),
              const SizedBox(height: 8),
              Text(
                  'Log Market Entry sits at the bottom of any coin\u2019s '
                  'dashboard. It records a trade you entered elsewhere — this '
                  'app holds no exchange key and places no orders.',
                  style: Obsidian.body(color: Obsidian.outline, size: 11.5)),
            ],
          ),
        )
      else
        for (final t in _trades) ...[
          Dismissible(
            key: ValueKey(t.id),
            direction: DismissDirection.endToStart,
            confirmDismiss: (_) async {
              await _deleteTrade(t);
              return false;          // the handler reloads; do not animate out
            },
            background: Container(
              alignment: Alignment.centerRight,
              padding: const EdgeInsets.only(right: 20),
              child: const Icon(Icons.delete_outline_rounded,
                  color: Obsidian.redSoft, size: 20),
            ),
            child: PositionCard(
              entry: t,
              short: t.symbol.endsWith('USDT')
                  ? t.symbol.substring(0, t.symbol.length - 4)
                  : t.symbol,
              showSymbol: true,
              // No websocket on this screen, so an open trade marks against
              // its entry until you close it. `pnlPct(null)` returns null
              // rather than zero, and the card prints a dash.
              livePrice: null,
              onClose: t.isOpen ? () => _closeTrade(t) : null,
            ),
          ),
          const SizedBox(height: Obsidian.panelGap),
        ],
      const SizedBox(height: Obsidian.gutter - Obsidian.panelGap),
    ];
  }

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
