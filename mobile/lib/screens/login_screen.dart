/// Sign in to Vanth.
///
/// This screen used to be a prop. The two fields looked like an auth wall,
/// were never transmitted, and let anybody through — which was the honest
/// thing to build when there was no account server to talk to. There is one
/// now, so the form does what it appears to do.
///
/// WHAT THE PASSWORD DOES AND DOES NOT TOUCH
///     It is sent once, over the link to your own server, and exchanged for a
///     session token. The app stores the TOKEN, never the password. Signing
///     out discards the token; changing the password on the server invalidates
///     every token that account had.
///
/// WHY THE HOST EDITOR IS ON THE SIGN-IN SCREEN
///     Because a wrong host and a wrong password fail identically from the
///     user's side, and only one of them is fixable by typing more carefully.
///     The status row says which it is before you try.
library;

import 'dart:async';
import 'package:url_launcher/url_launcher.dart';
import 'dart:math';

import 'package:flutter/material.dart';

import '../api/client.dart';
import '../api/models.dart';
import '../api/settings.dart';
import '../theme/liquid_obsidian.dart';
import '../widgets/patient_loader.dart';
import '../widgets/glass.dart';
import '../widgets/mesh_background.dart';

class LoginScreen extends StatefulWidget {
  const LoginScreen({super.key, required this.client, required this.onEnter});

  final ApiClient client;
  final ValueChanged<Account> onEnter;

  @override
  State<LoginScreen> createState() => _LoginScreenState();
}

class _LoginScreenState extends State<LoginScreen> {
  final _id = TextEditingController();
  final _key = TextEditingController();
  final _user = TextEditingController();
  final _confirm = TextEditingController();
  bool _obscure = true;
  bool _register = false;
  bool _busy = false;
  /// Set after a registration the server is holding for confirmation. The
  /// screen switches to "check your email" and the address is kept for the
  /// resend button.
  String? _awaitingConfirmationFor;
  /// Providers the server is set up for. Empty until it says otherwise, so
  /// no button is ever drawn for a sign-in that would fail.
  List<({String id, String label})> _providers = const [];
  /// A provider sign-in in progress: the browser has it, we poll.
  Timer? _oauthPoll;
  bool _probing = true;
  bool _linked = false;
  String _linkDetail = 'checking link...';
  String? _error;

  @override
  void initState() {
    super.initState();
    _probe();
  }

  @override
  void dispose() {
    _probeRetry?.cancel();
    _oauthPoll?.cancel();
    _id.dispose();
    _key.dispose();
    _user.dispose();
    _confirm.dispose();
    super.dispose();
  }

  /// When this run of probing started, and the retry that keeps it going.
  ///
  /// One failed health check is not evidence of anything on a phone. It used
  /// to be enough to print "No Link — tap to set host", which is advice to go
  /// and change a setting that was correct — so the probe now keeps trying
  /// for [kPatience] before it says that.
  DateTime _probeSince = DateTime.now();
  Timer? _probeRetry;

  /// Is the server reachable at all?
  ///
  /// Deliberately `/api/health`, which needs no credential. Probing something
  /// gated would report "no link" for an account problem and send the user to
  /// re-type a host that was correct all along.
  ///
  /// `restart: false` continues the current attempt's clock instead of
  /// starting a new one, so the retries add up to one five-minute wait rather
  /// than an endless series of fresh thirty-second ones.
  Future<void> _probe({bool restart = true}) async {
    _probeRetry?.cancel();
    if (restart) _probeSince = DateTime.now();
    setState(() {
      _probing = true;
      if (restart) _linkDetail = 'checking link...';
    });
    try {
      await widget.client.health();
      if (!mounted) return;
      setState(() {
        _linked = true;
        _probing = false;
        _linkDetail = 'Network Link Secure';
      });
      // Separately and best-effort: an old server without this route must
      // not turn a healthy link into "no link".
      try {
        final p = await widget.client.authProviders();
        if (mounted) setState(() => _providers = p);
      } catch (_) {}
    } catch (_) {
      if (!mounted) return;
      final waited = DateTime.now().difference(_probeSince);
      if (patienceExhausted(waited)) {
        setState(() {
          _linked = false;
          _probing = false;
          _linkDetail = 'No Link — tap to set host';
        });
        return;
      }
      setState(() {
        // still spinning, still hopeful, and increasingly candid about it
        _probing = true;
        _linkDetail = quipFor(waited) ?? 'still checking...';
      });
      _probeRetry =
          Timer(const Duration(seconds: 10), () => _probe(restart: false));
    }
  }

  Future<void> _submit() async {
    final id = _id.text.trim().toLowerCase();
    final pw = _key.text;
    if (id.isEmpty || pw.isEmpty) {
      setState(() => _error = 'Both fields are required.');
      return;
    }
    // CHECKED HERE ONLY WHEN CREATING AN ACCOUNT.
    //
    // Signing IN must not validate the shape of what you typed: accounts
    // registered before email was the identifier still exist, and refusing
    // their handle at the door would lock out the people who have been using
    // this the longest. The server is the authority on whether an identifier
    // is known; this is a typo-catcher for the one case that creates a
    // permanent record.
    if (_register && !RegExp(r'^[^@\s]+@[^@\s.]+(\.[^@\s.]+)+$').hasMatch(id)) {
      setState(() => _error = 'That does not look like an email address.');
      return;
    }
    // The same rules the server applies, checked here first so a typo is
    // caught before a round trip. The server is still the authority.
    final username = _user.text.trim();
    if (_register) {
      if (username.length < 4) {
        setState(() => _error = 'A username needs at least 4 characters.');
        return;
      }
      if (!RegExp(r'^[A-Za-z][A-Za-z0-9_]*$').hasMatch(username)) {
        setState(() => _error =
            'Letters, digits and underscores, starting with a letter.');
        return;
      }
      if (_confirm.text != pw) {
        setState(() => _error = 'The two passwords do not match.');
        return;
      }
    }
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      final Account account;
      if (_register) {
        final r = await widget.client.register(
            email: id, username: username, password: pw, confirm: _confirm.text);
        if (r.pending) {
          // Held until the link in the email is opened. No token was
          // issued, so there is nothing to save and nowhere to go yet.
          if (!mounted) return;
          setState(() {
            _busy = false;
            _awaitingConfirmationFor = id;
          });
          return;
        }
        account = r.account;
      } else {
        account = await widget.client.login(id, pw);
      }
      // persist the SESSION, not the password — plus the account itself, so
      // a later cold start with no network can still open the app
      await Settings.instance.saveToken(widget.client.token);
      await Settings.instance.saveAccount(account);
      if (!mounted) return;
      widget.onEnter(account);
    } on ApiException catch (e) {
      if (!mounted) return;
      setState(() {
        _busy = false;
        _error = e.message;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _busy = false;
        _error = '$e';
      });
    }
  }

  Future<void> _resend() async {
    final email = _awaitingConfirmationFor;
    if (email == null) return;
    setState(() => _busy = true);
    try {
      final msg = await widget.client.resendConfirmation(email);
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(
          backgroundColor: Obsidian.surfaceHigh,
          content: Text(msg, style: Obsidian.body())));
    } catch (e) {
      if (mounted) setState(() => _error = '$e');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  /// Sign in with a provider: hand the browser a URL, then ask the server
  /// every two seconds whether the browser has finished. No deep link back
  /// into the app -- see `api/oauth.py` for why polling is the safer
  /// design on a phone.
  Future<void> _signInWith(String provider) async {
    final device = _nonce();
    final url = Uri.parse(widget.client.oauthStartUrl(provider, device));
    setState(() {
      _busy = true;
      _error = null;
    });
    if (!await launchUrl(url, mode: LaunchMode.externalApplication)) {
      if (mounted) {
        setState(() {
          _busy = false;
          _error = 'Could not open the browser.';
        });
      }
      return;
    }
    var polls = 0;
    _oauthPoll?.cancel();
    _oauthPoll = Timer.periodic(const Duration(seconds: 2), (t) async {
      if (!mounted) {
        t.cancel();
        return;
      }
      // Five minutes, matching the server's own expiry for the token it
      // files. After that the person has wandered off, and a poll every
      // two seconds forever is a battery drain in their pocket.
      if (++polls > 150) {
        t.cancel();
        setState(() {
          _busy = false;
          _error = 'Sign-in timed out. Try again.';
        });
        return;
      }
      try {
        final token = await widget.client.oauthPoll(device);
        if (token == null) return;
        t.cancel();
        final me = await widget.client.me();
        await Settings.instance.saveToken(token);
        await Settings.instance.saveAccount(me);
        if (!mounted) return;
        widget.onEnter(me);
      } catch (_) {
        // offline blip; keep polling
      }
    });
  }

  static String _nonce() {
    const chars = 'abcdefghijklmnopqrstuvwxyz0123456789';
    final r = Random.secure();
    return List.generate(32, (_) => chars[r.nextInt(chars.length)]).join();
  }

  Future<void> _editHost() async {
    final ctrl = TextEditingController(text: widget.client.base);
    final saved = await showDialog<String>(
      context: context,
      builder: (ctx) => AlertDialog(
        backgroundColor: Obsidian.surfaceContainer,
        shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(Obsidian.rLg)),
        title: Text('Server address', style: Obsidian.headlineMd()),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              'The address of your Vanth server. Use the https:// '
              'address — over plain http your password would travel in clear '
              'text.',
              style: Obsidian.body(),
            ),
            const SizedBox(height: 16),
            GlassField(controller: ctrl, hint: 'https://your-server'),
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
      await Settings.instance.saveBase(saved);
      await _probe();
    }
  }

  Widget _confirmPanel() => GlassPanel(
        padding: const EdgeInsets.all(24),
        radius: Obsidian.rXl,
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            const Icon(Icons.mark_email_unread_outlined,
                size: 40, color: Obsidian.green),
            const SizedBox(height: 14),
            Text('Check your email',
                textAlign: TextAlign.center, style: Obsidian.headlineMd()),
            const SizedBox(height: 8),
            Text(
                'We sent a confirmation link to\n$_awaitingConfirmationFor\n\n'
                'Open it, then come back and sign in. The link works once '
                'and expires in 24 hours.',
                textAlign: TextAlign.center,
                style: Obsidian.body(color: Obsidian.onSurfaceVariant)),
            if (_error != null) ...[
              const SizedBox(height: 12),
              _errorBanner(_error!),
            ],
            const SizedBox(height: 20),
            SizedBox(
              height: 48,
              child: OutlinedButton(
                onPressed: _busy ? null : _resend,
                style: OutlinedButton.styleFrom(
                  foregroundColor: Obsidian.onSurface,
                  side: BorderSide(color: Colors.white.withValues(alpha: 0.14)),
                  shape: RoundedRectangleBorder(
                      borderRadius: BorderRadius.circular(Obsidian.rLg)),
                ),
                child: Text('RESEND THE LINK',
                    style: Obsidian.labelSm(color: Obsidian.onSurface, size: 12)),
              ),
            ),
            const SizedBox(height: 8),
            TextButton(
              onPressed: () => setState(() {
                _awaitingConfirmationFor = null;
                _register = false;
                _error = null;
              }),
              child: Text('I have confirmed \u2014 sign in',
                  style: Obsidian.body(color: Obsidian.primary, size: 12.5)),
            ),
          ],
        ),
      );

  /// A message from the server or from validation, presented rather than
  /// dumped. Server errors arrive as lower-case fragments ("choose a
  /// username"); on screen they read as a sentence, inside a tinted panel
  /// with an icon, so an error is visibly an error and not stray red text.
  Widget _errorBanner(String message) => Container(
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
        decoration: BoxDecoration(
          color: Obsidian.red.withValues(alpha: 0.10),
          borderRadius: BorderRadius.circular(Obsidian.rMd),
          border: Border.all(color: Obsidian.red.withValues(alpha: 0.30)),
        ),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Padding(
              padding: EdgeInsets.only(top: 1),
              child: Icon(Icons.error_outline_rounded,
                  size: 16, color: Obsidian.redSoft),
            ),
            const SizedBox(width: 9),
            Expanded(
              child: Text(_sentence(message),
                  style: Obsidian.body(color: Obsidian.redSoft, size: 12.5)),
            ),
          ],
        ),
      );

  /// "choose a username" -> "Choose a username." Leaves a message that
  /// already ends in punctuation alone.
  static String _sentence(String m) {
    final t = m.trim();
    if (t.isEmpty) return t;
    final cap = t[0].toUpperCase() + t.substring(1);
    return RegExp(r'[.!?]$').hasMatch(cap) ? cap : '$cap.';
  }

  static IconData _providerIcon(String id) => switch (id) {
        'google' => Icons.g_mobiledata_rounded,
        'github' => Icons.code_rounded,
        'facebook' => Icons.facebook_rounded,
        'apple' => Icons.apple_rounded,
        _ => Icons.login_rounded,
      };

  Widget _label(IconData icon, String text) => Row(
        children: [
          Icon(icon, size: 15, color: Obsidian.outline),
          const SizedBox(width: 7),
          Text(text, style: Obsidian.labelSm(size: 10.5)),
        ],
      );

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
                  Image.asset('assets/logo.png',
                      width: 76, height: 76, filterQuality: FilterQuality.high),
                  const SizedBox(height: 14),
                  Text('Vanth',
                      textAlign: TextAlign.center,
                      style: Obsidian.displayLg().copyWith(letterSpacing: 1.5)),
                  const SizedBox(height: 6),
                  Text(
                      _register
                          ? 'Create an account'
                          : 'Sign in to your account',
                      style: Obsidian.body(color: Obsidian.outline)),
                  const SizedBox(height: 26),
                  if (_awaitingConfirmationFor != null)
                    _confirmPanel()
                  else
                  GlassPanel(
                    padding: const EdgeInsets.all(24),
                    radius: Obsidian.rXl,
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.stretch,
                      children: [
                        if (_register) ...[
                          _label(Icons.person_outline_rounded, 'USERNAME'),
                          const SizedBox(height: 10),
                          GlassField(
                            controller: _user,
                            hint: 'at least 4 characters',
                            capitalization: TextCapitalization.none,
                            onChanged: (_) => setState(() => _error = null),
                          ),
                          const SizedBox(height: 20),
                        ],
                        _label(Icons.alternate_email_rounded,
                            _register ? 'EMAIL' : 'EMAIL OR USERNAME'),
                        const SizedBox(height: 10),
                        GlassField(
                          controller: _id,
                          hint: _register ? 'you@example.com' : 'you@example.com or username',
                          // The right keyboard matters more than it sounds:
                          // an address typed on a keyboard that capitalises
                          // and hides the @ is an address typed wrong.
                          keyboardType: TextInputType.emailAddress,
                          capitalization: TextCapitalization.none,
                          onChanged: (_) => setState(() => _error = null),
                        ),
                        const SizedBox(height: 20),
                        _label(Icons.lock_outline_rounded, 'PASSWORD'),
                        const SizedBox(height: 10),
                        GlassField(
                          controller: _key,
                          hint: _register
                              ? 'at least 8 characters'
                              : 'your password',
                          obscure: _obscure,
                          onChanged: (_) => setState(() => _error = null),
                          trailing: InkWell(
                            onTap: () => setState(() => _obscure = !_obscure),
                            child: Icon(
                                _obscure
                                    ? Icons.visibility_off_rounded
                                    : Icons.visibility_rounded,
                                size: 18,
                                color: Obsidian.outline),
                          ),
                        ),
                        if (_register) ...[
                          const SizedBox(height: 20),
                          _label(Icons.lock_outline_rounded, 'CONFIRM PASSWORD'),
                          const SizedBox(height: 10),
                          GlassField(
                            controller: _confirm,
                            hint: 'the same password again',
                            obscure: _obscure,
                            onChanged: (_) => setState(() => _error = null),
                          ),
                        ],
                        if (_error != null) ...[
                          const SizedBox(height: 16),
                          _errorBanner(_error!),
                        ],
                        const SizedBox(height: 24),
                        SizedBox(
                          height: 52,
                          child: FilledButton(
                            onPressed: _busy ? null : _submit,
                            style: FilledButton.styleFrom(
                              backgroundColor: Obsidian.primaryContainer,
                              shape: RoundedRectangleBorder(
                                  borderRadius:
                                      BorderRadius.circular(Obsidian.rLg)),
                            ),
                            child: _busy
                                ? const SizedBox(
                                    width: 20,
                                    height: 20,
                                    child: CircularProgressIndicator(
                                        strokeWidth: 2, color: Colors.white))
                                : Text(
                                    _register ? 'CREATE ACCOUNT' : 'SIGN IN',
                                    style: Obsidian.labelSm(
                                        color: Colors.white, size: 12.5)),
                          ),
                        ),
                        const SizedBox(height: 14),
                        Center(
                          child: TextButton(
                            onPressed: _busy
                                ? null
                                : () => setState(() {
                                      _register = !_register;
                                      _error = null;
                                    }),
                            child: Text(
                                _register
                                    ? 'Already have an account? Sign in'
                                    : 'No account? Create one',
                                style:
                                    Obsidian.body(color: Obsidian.primary,
                                        size: 12.5)),
                          ),
                        ),
                        if (_providers.isNotEmpty) ...[
                          const SizedBox(height: 10),
                          Row(children: [
                            Expanded(child: Divider(
                                color: Colors.white.withValues(alpha: 0.08))),
                            Padding(
                              padding: const EdgeInsets.symmetric(horizontal: 10),
                              child: Text('OR CONTINUE WITH',
                                  style: Obsidian.labelSm(
                                      color: Obsidian.outline, size: 10)),
                            ),
                            Expanded(child: Divider(
                                color: Colors.white.withValues(alpha: 0.08))),
                          ]),
                          const SizedBox(height: 14),
                          for (final p in _providers) ...[
                            SizedBox(
                              height: 46,
                              child: OutlinedButton.icon(
                                onPressed: _busy ? null : () => _signInWith(p.id),
                                style: OutlinedButton.styleFrom(
                                  foregroundColor: Obsidian.onSurface,
                                  side: BorderSide(
                                      color: Colors.white.withValues(alpha: 0.14)),
                                  shape: RoundedRectangleBorder(
                                      borderRadius:
                                          BorderRadius.circular(Obsidian.rLg)),
                                ),
                                icon: Icon(_providerIcon(p.id), size: 18),
                                label: Text(p.label,
                                    style: Obsidian.labelSm(
                                        color: Obsidian.onSurface, size: 12.5)),
                              ),
                            ),
                            const SizedBox(height: 10),
                          ],
                        ],
                      ],
                    ),
                  ),
                  const SizedBox(height: 22),
                  InkWell(
                    onTap: _editHost,
                    borderRadius: BorderRadius.circular(Obsidian.rLg),
                    child: Padding(
                      padding: const EdgeInsets.symmetric(
                          horizontal: 14, vertical: 10),
                      child: Row(
                        mainAxisSize: MainAxisSize.min,
                        children: [
                          if (_probing)
                            const SizedBox(
                                width: 10,
                                height: 10,
                                child: CircularProgressIndicator(
                                    strokeWidth: 1.5,
                                    color: Obsidian.outline))
                          else
                            Container(
                              width: 8,
                              height: 8,
                              decoration: BoxDecoration(
                                shape: BoxShape.circle,
                                color: _linked
                                    ? Obsidian.green
                                    : Obsidian.redSoft,
                              ),
                            ),
                          const SizedBox(width: 9),
                          Text(_linkDetail,
                              style: Obsidian.labelSm(
                                  size: 10.5,
                                  color: _linked
                                      ? Obsidian.greenDim
                                      : Obsidian.outline)),
                        ],
                      ),
                    ),
                  ),
                  const SizedBox(height: 8),
                  Text('Analysis only. Vanth places no orders.',
                      textAlign: TextAlign.center,
                      style:
                          Obsidian.body(color: Obsidian.outline, size: 11)),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }
}
