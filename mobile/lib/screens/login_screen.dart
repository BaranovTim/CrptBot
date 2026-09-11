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
  bool _obscure = true;
  bool _register = false;
  bool _busy = false;
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
    _id.dispose();
    _key.dispose();
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
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      final account = _register
          ? await widget.client.register(id, pw)
          : await widget.client.login(id, pw);
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
                  Text('VANTH',
                      textAlign: TextAlign.center,
                      style: Obsidian.displayLg().copyWith(letterSpacing: 6)),
                  const SizedBox(height: 6),
                  Text(
                      _register
                          ? 'Create an account'
                          : 'Sign in to your account',
                      style: Obsidian.body(color: Obsidian.outline)),
                  const SizedBox(height: 26),
                  GlassPanel(
                    padding: const EdgeInsets.all(24),
                    radius: Obsidian.rXl,
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.stretch,
                      children: [
                        _label(Icons.alternate_email_rounded, 'EMAIL'),
                        const SizedBox(height: 10),
                        GlassField(
                          controller: _id,
                          hint: 'you@example.com',
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
                        if (_error != null) ...[
                          const SizedBox(height: 16),
                          Text(_error!,
                              style: Obsidian.body(
                                  color: Obsidian.redSoft, size: 12.5)),
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
