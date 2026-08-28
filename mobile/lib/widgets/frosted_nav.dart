/// The floating frosted navigation bar.
///
/// "Positioned at the bottom, detached from the screen edges by 16px. Heavy
/// background blur (50px) and a high-contrast white border."
library;

import 'dart:ui';

import 'package:flutter/material.dart';

import '../theme/liquid_obsidian.dart';

enum NavTab { dashboard, market, training, profile }

class FrostedNav extends StatelessWidget {
  const FrostedNav({
    super.key,
    required this.current,
    required this.onSelect,
    this.locked = const {},
  });

  final NavTab current;
  final ValueChanged<NavTab> onSelect;

  /// Tabs shown with a padlock instead of their icon — the Pro screen state.
  final Set<NavTab> locked;

  static const _items = <NavTab, (IconData, String)>{
    NavTab.dashboard: (Icons.grid_view_rounded, 'Dashboard'),
    NavTab.market: (Icons.query_stats_rounded, 'Market'),
    NavTab.training: (Icons.model_training_rounded, 'Training'),
    NavTab.profile: (Icons.person_rounded, 'Profile'),
  };

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: EdgeInsets.fromLTRB(
          16, 0, 16, 12 + MediaQuery.of(context).padding.bottom * 0.4),
      child: ClipRRect(
        borderRadius: BorderRadius.circular(Obsidian.rXl),
        child: BackdropFilter(
          filter: ImageFilter.blur(sigmaX: 50, sigmaY: 50),
          child: Container(
            height: 68,
            decoration: BoxDecoration(
              color: Color.fromRGBO(40, 42, 46, 0.55),
              borderRadius: BorderRadius.circular(Obsidian.rXl),
              border:
                  Border.all(color: Colors.white.withValues(alpha: 0.22)),
            ),
            child: Row(
              children: [
                for (final e in _items.entries)
                  Expanded(
                    child: _NavItem(
                      icon: locked.contains(e.key)
                          ? Icons.lock_outline_rounded
                          : e.value.$1,
                      label: e.value.$2,
                      selected: current == e.key,
                      onTap: () => onSelect(e.key),
                    ),
                  ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

class _NavItem extends StatelessWidget {
  const _NavItem({
    required this.icon,
    required this.label,
    required this.selected,
    required this.onTap,
  });

  final IconData icon;
  final String label;
  final bool selected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final c = selected ? Obsidian.green : Obsidian.outline;
    return InkWell(
      onTap: onTap,
      borderRadius: BorderRadius.circular(Obsidian.rMd),
      child: Column(
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          Icon(icon, color: c, size: 22, shadows: selected
              ? [BoxShadow(color: c.withValues(alpha: 0.6), blurRadius: 12)]
              : null),
          const SizedBox(height: 4),
          Text(label,
              style: Obsidian.body(color: c, size: 11).copyWith(
                  fontWeight: selected ? FontWeight.w700 : FontWeight.w500)),
        ],
      ),
    );
  }
}
