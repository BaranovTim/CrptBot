/// The screener: presets you can take apart.
///
/// THE REQUIREMENT THIS PAGE IS BUILT AROUND
///     "when you recommend these recommendations the parameters change also
///     for a user so that he could change them any time."
///
///     So a preset is not a button that runs a hidden query. Tapping one FILLS
///     THE FILTER LIST with ordinary editable rows — the same rows you would
///     have added by hand — and from that moment the screen runs what is on
///     screen, not what the preset said. Change a number and the preset chip
///     unhighlights, because it is no longer that preset.
///
/// WHY "UNJUDGED" IS ON SCREEN
///     Three criteria across your presets need analyst estimates, which the
///     free data stack does not have. A stock that clears everything else is
///     reported as unjudged rather than dropped, with the criteria named. The
///     alternative — showing a shorter list — would quietly claim those
///     stocks failed on the merits.
library;

import 'package:flutter/material.dart';
import 'package:url_launcher/url_launcher.dart';

import '../api/client.dart';
import '../api/models.dart';
import '../theme/liquid_obsidian.dart';
import '../widgets/glass.dart';
import '../widgets/patient_loader.dart';

class ScreenerScreen extends StatefulWidget {
  const ScreenerScreen({super.key, required this.client});

  final ApiClient client;

  @override
  State<ScreenerScreen> createState() => _ScreenerScreenState();
}

class _ScreenerScreenState extends State<ScreenerScreen> {
  ScreenerCatalogue? _cat;
  ScreenerResult? _result;
  String? _error;
  String? _lastFailure;
  DateTime _waitingSince = DateTime.now();
  bool _busy = false;

  /// The live filter list. Presets copy into here; nothing reads them back.
  final List<ScreenerFilter> _filters = [];

  /// Which preset these filters CAME FROM, cleared the moment one is edited —
  /// so the chip never claims to describe a screen it no longer matches.
  String? _fromPreset;

  String? _sortBy;
  final bool _descending = true;

  @override
  void initState() {
    super.initState();
    _loadCatalogue();
  }

  Future<void> _loadCatalogue() async {
    try {
      final c = await widget.client.screenerCatalogue();
      if (!mounted) return;
      setState(() {
        _cat = c;
        _error = null;
        _waitingSince = DateTime.now();
      });
      await _run();
    } catch (e) {
      if (!mounted) return;
      _lastFailure = '$e';
    }
  }

  void _giveUp() {
    if (!mounted || _error != null) return;
    setState(() => _error = _lastFailure ?? 'No answer from the server.');
  }

  Future<void> _run() async {
    if (_busy) return;
    setState(() => _busy = true);
    try {
      final r = await widget.client.screen(
        // Once anything has been edited the preset name is gone, so what runs
        // is exactly the list on screen.
        preset: _filters.isEmpty ? _fromPreset : null,
        filters: _filters.isEmpty ? null : _filters,
        sortBy: _sortBy,
        descending: _descending,
      );
      if (!mounted) return;
      setState(() {
        _result = r;
        _error = null;
        _lastFailure = null;
      });
    } catch (e) {
      if (!mounted) return;
      _lastFailure = '$e';
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  void _applyPreset(ScreenerPreset p) {
    setState(() {
      _filters
        ..clear()
        ..addAll(p.instantiate());     // copies, so editing cannot mutate it
      _fromPreset = p.id;
    });
    _run();
  }

  void _edited() => setState(() => _fromPreset = null);

  @override
  Widget build(BuildContext context) {
    final cat = _cat;
    if (_error != null) return _errorPanel();
    if (cat == null) {
      return WaitingPanel(
          since: _waitingSince,
          what: 'Loading the screener',
          onPatienceExhausted: _giveUp);
    }

    return RefreshIndicator(
      onRefresh: _run,
      backgroundColor: Obsidian.surfaceContainer,
      color: Obsidian.primary,
      child: ListView(
        addRepaintBoundaries: false,
        physics: const AlwaysScrollableScrollPhysics(),
        padding: const EdgeInsets.fromLTRB(Obsidian.containerPadding, 8,
            Obsidian.containerPadding, Obsidian.navClearance + 24),
        children: [
          Text('Screener', style: Obsidian.displayLg()),
          const SizedBox(height: 4),
          Text(_subtitle(cat),
              style: Obsidian.body(color: Obsidian.outline, size: 12)),
          const SizedBox(height: 18),
          _presetSection(cat),
          const SizedBox(height: Obsidian.gutter),
          _filterSection(cat),
          const SizedBox(height: Obsidian.gutter),
          ..._results(cat),
        ],
      ),
    );
  }

  String _subtitle(ScreenerCatalogue cat) {
    final age = cat.builtAt == null
        ? 'never built'
        : 'built ${_ago(cat.builtAt!)}';
    return '${cat.symbols} US stocks · $age';
  }

  // ------------------------------------------------------------- presets
  Widget _presetSection(ScreenerCatalogue cat) => Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text('RECOMMENDED', style: Obsidian.labelSm(size: 10.5)),
          const SizedBox(height: 4),
          Text('Tap one to fill the filters below. Every number then becomes '
              'yours to change.',
              style: Obsidian.body(color: Obsidian.outline, size: 11.5)),
          const SizedBox(height: 10),
          for (final p in cat.presets) ...[
            _presetCard(p, cat),
            const SizedBox(height: 8),
          ],
        ],
      );

  Widget _presetCard(ScreenerPreset p, ScreenerCatalogue cat) {
    final active = _fromPreset == p.id;
    final byId = cat.byId;
    return GlassPanel(
      active: active,
      padding: const EdgeInsets.all(14),
      onTap: () => _applyPreset(p),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Expanded(
                child: Text(p.name,
                    style: Obsidian.body(size: 13.5, color: active
                        ? Obsidian.primary
                        : null).copyWith(fontWeight: FontWeight.w600)),
              ),
              if (active)
                const Icon(Icons.check_circle_rounded,
                    size: 16, color: Obsidian.primary),
            ],
          ),
          const SizedBox(height: 4),
          Text(p.note,
              style: Obsidian.body(color: Obsidian.outline, size: 11.5)),
          const SizedBox(height: 8),
          Wrap(
            spacing: 6,
            runSpacing: 6,
            children: [
              for (final f in p.filters)
                _chip(f.describe(byId[f.field]),
                    p.unavailable.contains(f.field)),
            ],
          ),
          if (p.unavailable.isNotEmpty) ...[
            const SizedBox(height: 8),
            Text(
                '${p.unavailable.length} of these need analyst estimates, '
                'which this data stack does not have. Stocks are shown as '
                'unjudged on them rather than dropped.',
                style: Obsidian.body(color: Obsidian.amber, size: 10.5)),
          ],
        ],
      ),
    );
  }

  Widget _chip(String text, bool dim) => Container(
        padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
        decoration: BoxDecoration(
          color: (dim ? Obsidian.amber : Obsidian.primary)
              .withValues(alpha: 0.12),
          borderRadius: BorderRadius.circular(6),
        ),
        child: Text(text,
            style: Obsidian.labelSm(
                size: 9.5, color: dim ? Obsidian.amber : Obsidian.primary)),
      );

  // ------------------------------------------------------------- filters
  Widget _filterSection(ScreenerCatalogue cat) => GlassPanel(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Text('FILTERS', style: Obsidian.labelSm(size: 10.5)),
                const Spacer(),
                if (_filters.isNotEmpty)
                  InkWell(
                    onTap: () {
                      setState(() {
                        _filters.clear();
                        _fromPreset = null;
                      });
                      _run();
                    },
                    child: Text('Clear',
                        style: Obsidian.labelSm(
                            size: 10, color: Obsidian.redSoft)),
                  ),
              ],
            ),
            const SizedBox(height: 10),
            if (_filters.isEmpty)
              Text('No filters — every stock matches. Pick a recommendation '
                  'above, or add one.',
                  style: Obsidian.body(color: Obsidian.outline, size: 11.5))
            else
              for (var i = 0; i < _filters.length; i++)
                _filterRow(cat, i),
            const SizedBox(height: 10),
            InkWell(
              onTap: () => _addFilter(cat),
              child: Row(
                children: [
                  const Icon(Icons.add_rounded,
                      size: 16, color: Obsidian.primary),
                  const SizedBox(width: 6),
                  Text('Add a filter',
                      style: Obsidian.body(
                          color: Obsidian.primary, size: 12.5)),
                ],
              ),
            ),
          ],
        ),
      );

  Widget _filterRow(ScreenerCatalogue cat, int i) {
    final f = _filters[i];
    final field = cat.byId[f.field];
    final boolish = f.op == 'is_true' || f.op == 'is_false';
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 5),
      child: Row(
        children: [
          Expanded(
            flex: 5,
            child: Text(field?.label ?? f.field,
                style: Obsidian.body(
                    size: 12.5,
                    color: (field?.unavailable ?? false)
                        ? Obsidian.amber
                        : null)),
          ),
          Expanded(
            flex: 3,
            child: DropdownButton<String>(
              value: f.op,
              isDense: true,
              isExpanded: true,
              underline: const SizedBox.shrink(),
              dropdownColor: Obsidian.surfaceHigh,
              style: Obsidian.dataTable(size: 11.5),
              items: [
                for (final op in _opsFor(field))
                  DropdownMenuItem(value: op, child: Text(_opLabel(op))),
              ],
              onChanged: (v) {
                if (v == null) return;
                setState(() => f.op = v);
                _edited();
                _run();
              },
            ),
          ),
          const SizedBox(width: 8),
          Expanded(
            flex: 3,
            child: boolish
                ? const SizedBox.shrink()
                : TextFormField(
                    initialValue: _trim(f.value),
                    keyboardType: const TextInputType.numberWithOptions(
                        decimal: true, signed: true),
                    style: Obsidian.dataTable(size: 12.5),
                    decoration: const InputDecoration(
                        isDense: true, border: UnderlineInputBorder()),
                    onFieldSubmitted: (v) {
                      setState(() => f.value = double.tryParse(v));
                      _edited();
                      _run();
                    },
                  ),
          ),
          IconButton(
            icon: const Icon(Icons.close_rounded, size: 15),
            color: Obsidian.outline,
            visualDensity: VisualDensity.compact,
            onPressed: () {
              setState(() => _filters.removeAt(i));
              _edited();
              _run();
            },
          ),
        ],
      ),
    );
  }

  List<String> _opsFor(ScreenerField? f) =>
      f?.kind == 'bool' ? const ['is_true', 'is_false']
                        : const ['gt', 'gte', 'lt', 'lte', 'between'];

  static String _opLabel(String op) => switch (op) {
        'gt' => 'over',
        'gte' => 'at least',
        'lt' => 'under',
        'lte' => 'at most',
        'between' => 'between',
        'is_true' => 'above / yes',
        'is_false' => 'below / no',
        _ => op,
      };

  static String _trim(double? v) {
    if (v == null) return '';
    return v == v.roundToDouble() && v.abs() < 1e15
        ? v.toStringAsFixed(0)
        : '$v';
  }

  Future<void> _addFilter(ScreenerCatalogue cat) async {
    final groups = <String, List<ScreenerField>>{};
    for (final f in cat.fields) {
      groups.putIfAbsent(f.group, () => []).add(f);
    }
    final picked = await showModalBottomSheet<ScreenerField>(
      context: context,
      backgroundColor: Colors.transparent,
      isScrollControlled: true,
      builder: (_) => _FieldPicker(groups: groups),
    );
    if (picked == null) return;
    setState(() {
      _filters.add(ScreenerFilter(
          field: picked.id,
          op: picked.kind == 'bool' ? 'is_true' : 'gt',
          value: picked.kind == 'bool' ? null : 0));
      _fromPreset = null;
    });
    _run();
  }

  // ------------------------------------------------------------- results
  List<Widget> _results(ScreenerCatalogue cat) {
    final r = _result;
    if (r == null) {
      return [
        WaitingPanel(
            since: _waitingSince,
            what: 'Screening',
            compact: true,
            onPatienceExhausted: _giveUp)
      ];
    }
    final rows = <Widget>[
      Row(
        children: [
          Text('${r.matched} matched', style: Obsidian.labelSm(size: 10.5)),
          if (r.unjudged > 0) ...[
            const SizedBox(width: 10),
            Text('${r.unjudged} unjudged',
                style: Obsidian.labelSm(size: 10.5, color: Obsidian.amber)),
          ],
          const Spacer(),
          Text('of ${r.scanned}',
              style: Obsidian.labelSm(size: 10.5, color: Obsidian.outline)),
          if (_busy) ...[
            const SizedBox(width: 8),
            const SizedBox(
                width: 10,
                height: 10,
                child: CircularProgressIndicator(
                    strokeWidth: 1.5, color: Obsidian.primary)),
          ],
        ],
      ),
      const SizedBox(height: 10),
    ];

    if (r.rows.isEmpty) {
      rows.add(Padding(
        padding: const EdgeInsets.only(top: 30),
        child: Center(
          child: Text('Nothing clears these filters.',
              style: Obsidian.body(color: Obsidian.outline)),
        ),
      ));
      return rows;
    }

    for (final row in r.rows) {
      rows.add(_resultCard(row, cat));
      rows.add(const SizedBox(height: 8));
    }
    return rows;
  }

  Widget _resultCard(ScreenerRow row, ScreenerCatalogue cat) {
    final byId = cat.byId;
    // Whatever the filters are about, plus price — so the card always says
    // something even with no filters set.
    final shown = <String>{'price', ..._filters.map((f) => f.field)}
        .where((id) => byId.containsKey(id))
        .take(6)
        .toList();
    return GlassPanel(
      padding: const EdgeInsets.all(14),
      onTap: () => _open(row),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Text(row.symbol,
                  style: Obsidian.dataTable(size: 15, w: FontWeight.w700)),
              const SizedBox(width: 8),
              if (row.unknown.isNotEmpty)
                Container(
                  padding:
                      const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
                  decoration: BoxDecoration(
                      color: Obsidian.amber.withValues(alpha: 0.14),
                      borderRadius: BorderRadius.circular(5)),
                  child: Text('${row.unknown.length} unjudged',
                      style:
                          Obsidian.labelSm(size: 8.5, color: Obsidian.amber)),
                ),
              const Spacer(),
              Text(byId['price']?.format(row.metric('price')) ?? '—',
                  style: Obsidian.dataTable(size: 14)),
            ],
          ),
          const SizedBox(height: 10),
          Wrap(
            spacing: 12,
            runSpacing: 6,
            children: [
              for (final id in shown)
                if (id != 'price')
                  _metric(byId[id]!, row.metric(id),
                      row.unknown.contains(id)),
            ],
          ),
          if (row.unknown.isNotEmpty) ...[
            const SizedBox(height: 8),
            Text(
                'Cannot judge: ${row.unknown.map((u) => byId[u]?.label ?? u).join(', ')}',
                style: Obsidian.body(color: Obsidian.amber, size: 10.5)),
          ],
          const SizedBox(height: 8),
          Row(
            children: [
              Text('Open on Yahoo Finance',
                  style:
                      Obsidian.labelSm(size: 9.5, color: Obsidian.primary)),
              const Icon(Icons.open_in_new_rounded,
                  size: 12, color: Obsidian.primary),
            ],
          ),
        ],
      ),
    );
  }

  Widget _metric(ScreenerField f, double? v, bool unknown) => Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisSize: MainAxisSize.min,
        children: [
          Text(f.label.toUpperCase(), style: Obsidian.labelSm(size: 8.5)),
          const SizedBox(height: 2),
          Text(unknown ? '—' : f.format(v),
              style: Obsidian.dataTable(
                  size: 12.5,
                  color: unknown ? Obsidian.amber : null)),
        ],
      );

  Future<void> _open(ScreenerRow row) async {
    final uri = Uri.parse(row.yahooUrl);
    try {
      await launchUrl(uri, mode: LaunchMode.externalApplication);
    } catch (_) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(
        backgroundColor: Obsidian.surfaceHigh,
        content: Text('Could not open ${row.yahooUrl}',
            style: Obsidian.body()),
      ));
    }
  }

  Widget _errorPanel() => Center(
        child: Padding(
          padding: const EdgeInsets.all(Obsidian.containerPadding),
          child: GlassPanel(
            padding: const EdgeInsets.all(22),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                const Icon(Icons.cloud_off_rounded,
                    color: Obsidian.outline, size: 34),
                const SizedBox(height: 14),
                Text('Screener unavailable', style: Obsidian.headlineMd()),
                const SizedBox(height: 10),
                Text(_error!, style: Obsidian.body(size: 12.5)),
                const SizedBox(height: 18),
                FilledButton(
                  style: FilledButton.styleFrom(
                      backgroundColor: Obsidian.primary,
                      foregroundColor: Obsidian.onPrimary),
                  onPressed: () {
                    setState(() {
                      _error = null;
                      _waitingSince = DateTime.now();
                    });
                    _loadCatalogue();
                  },
                  child: const Text('Retry'),
                ),
              ],
            ),
          ),
        ),
      );

  static String _ago(DateTime t) {
    final d = DateTime.now().difference(t.toLocal());
    if (d.inMinutes < 60) return '${d.inMinutes}m ago';
    if (d.inHours < 24) return '${d.inHours}h ago';
    return '${d.inDays}d ago';
  }
}

class _FieldPicker extends StatelessWidget {
  const _FieldPicker({required this.groups});

  final Map<String, List<ScreenerField>> groups;

  @override
  Widget build(BuildContext context) => SafeArea(
        top: false,
        child: Padding(
          padding: const EdgeInsets.all(Obsidian.containerPadding),
          child: GlassPanel(
            active: true,
            padding: const EdgeInsets.fromLTRB(20, 20, 20, 8),
            child: ConstrainedBox(
              constraints: BoxConstraints(
                  maxHeight: MediaQuery.of(context).size.height * 0.7),
              child: ListView(
                shrinkWrap: true,
                children: [
                  Text('ADD A FILTER', style: Obsidian.labelSm(size: 11)),
                  const SizedBox(height: 12),
                  for (final entry in groups.entries) ...[
                    Text(entry.key.toUpperCase(),
                        style: Obsidian.labelSm(
                            size: 9.5, color: Obsidian.outline)),
                    const SizedBox(height: 4),
                    for (final f in entry.value)
                      ListTile(
                        dense: true,
                        contentPadding: EdgeInsets.zero,
                        title: Text(f.label,
                            style: Obsidian.body(
                                size: 13,
                                color: f.unavailable ? Obsidian.amber : null)),
                        subtitle: f.help.isEmpty
                            ? null
                            : Text(f.help,
                                style: Obsidian.body(
                                    color: Obsidian.outline, size: 10.5)),
                        onTap: () => Navigator.of(context).pop(f),
                      ),
                    const SizedBox(height: 8),
                  ],
                ],
              ),
            ),
          ),
        ),
      );
}
