/// The tab host: mesh floor, a thin top bar, and the floating frosted nav.
library;

import 'package:flutter/material.dart';

import '../api/client.dart';
import '../api/models.dart';
import '../theme/liquid_obsidian.dart';
import '../widgets/frosted_nav.dart';
import '../widgets/mesh_background.dart';
import 'dashboard_screen.dart';
import 'market_screen.dart';
import 'profile_screen.dart';
import 'training_screen.dart';

class Shell extends StatefulWidget {
  const Shell({super.key, required this.client});

  final ApiClient client;

  @override
  State<Shell> createState() => _ShellState();
}

class _ShellState extends State<Shell> {
  NavTab _tab = NavTab.dashboard;
  String _symbol = 'BTCUSDT';

  void _pick(Coin c) {
    setState(() {
      _symbol = c.symbol;
      // an untrained pair has no probability to show, so send it where the
      // truthful answer lives instead of to an empty dashboard
      _tab = c.trained ? NavTab.dashboard : NavTab.training;
    });
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: MeshBackground(
        child: SafeArea(
          bottom: false,
          child: Column(
            children: [
              _topBar(),
              // Only the visible tab is built.
              //
              // An IndexedStack would keep the other three alive, which reads
              // like the better choice — until it hits a framework assertion,
              // `!semantics.parentDataDirty`, raised while compiling semantics
              // for the hidden children. It throws every frame, the frame
              // never completes, and the result is a blank screen with no
              // error painted on it.
              //
              // Building one tab at a time also means the dashboard's poll
              // timer is disposed when you leave it, instead of quietly
              // polling from behind two other screens.
              Expanded(
                child: switch (_tab) {
                  NavTab.dashboard => DashboardScreen(client: widget.client),
                  NavTab.market =>
                    MarketScreen(client: widget.client, onPick: _pick),
                  NavTab.training =>
                    TrainingScreen(client: widget.client, symbol: _symbol),
                  NavTab.profile => ProfileScreen(client: widget.client),
                },
              ),
            ],
          ),
        ),
      ),
      extendBody: true,
      bottomNavigationBar: FrostedNav(
        current: _tab,
        onSelect: (t) => setState(() => _tab = t),
      ),
    );
  }

  Widget _topBar() => Padding(
        padding: const EdgeInsets.fromLTRB(Obsidian.containerPadding, 4,
            Obsidian.containerPadding, 8),
        child: Row(
          children: [
            Icon(Icons.notifications_none_rounded,
                size: 26, color: Obsidian.onSurface),
            const Spacer(),
            // only on Training, because that is the one screen driven by the
            // pair you picked. The dashboard serves BTCUSDT whatever is
            // selected, so showing "ETHUSDT" above BTC data would be a
            // contradiction the user has no way to resolve — and the
            // dashboard names its own pair in the header anyway
            if (_tab == NavTab.training)
              Text(_symbol, style: Obsidian.labelSm(size: 11)),
          ],
        ),
      );
}
