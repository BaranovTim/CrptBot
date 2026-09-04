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
/// WHY THE RECOMMENDATIONS ARE A SHEET AND NOT A LIST
///     Seven cards, each with a description and a row of criterion chips, is
///     most of a screenful before a single result. The recommendations are a
///     thing you reach for occasionally and then work from; the results are
///     what you came to read. So they open on demand, as bare names, and the
///     page below stays about stocks.
///
/// WHY "UNJUDGED" IS ON SCREEN
///     Three criteria across your presets need analyst estimates, which the
///     free data stack does not have. A stock that clears everything else is
///     reported as unjudged rather than dropped, with the criteria named. The
///     alternative — showing a shorter list — would quietly claim those
///     stocks failed on the merits.
library;

import 'package:flutter/material.dart';
import '../api/client.dart';
import '../api/models.dart';
import '../api/watchlist.dart';
import '../theme/liquid_obsidian.dart';
import '../widgets/glass.dart';
import '../widgets/patient_loader.dart';
import 'stock_screen.dart';

class ScreenerScreen extends StatefulWidget {
  const ScreenerScreen({super.key, required this.client,
    this.market = 'stocks'});

  final ApiClient client;

  /// stocks or crypto. Two field sets and two preset sets, because a
  /// perpetual has no earnings and a stock has no funding rate — see
  /// `filters.py`. Changing it reloads everything.
  final String market;

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

  /// Which results are already followed, so the button reads correctly on
  /// first paint rather than flickering from Follow to Following.
  Set<String> _following = const {};

  @override
  void initState() {
    super.initState();
    _loadCatalogue();
    StockWatchlist.instance.load().then(
        (l) => mounted ? setState(() => _following = l.toSet()) : null);
  }

  @override
  void didUpdateWidget(covariant ScreenerScreen old) {
    super.didUpdateWidget(old);
    if (old.market != widget.market) {
      // A crypto filter cannot survive into a stocks screen: the field does
      // not exist there. Cleared rather than translated.
      setState(() {
        _cat = null;
        _result = null;
        _filters.clear();
        _fromPreset = null;
        _waitingSince = DateTime.now();
      });
      _loadCatalogue();
    }
  }

  Future<void> _loadCatalogue() async {
    try {
      final c =
          await widget.client.screenerCatalogue(market: widget.market);
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
        market: widget.market,
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
          _title(cat),
          const SizedBox(height: 16),
          _presetBar(cat),
          ..._droppedNote(cat),
          const SizedBox(height: 10),
          _filterSection(cat),
          const SizedBox(height: 22),
          _resultsHeader(),
          const SizedBox(height: 10),
          ..._results(cat),
        ],
      ),
    );
  }

  /// The header from the design: title, PRO badge, subtitle.
  Widget _title(ScreenerCatalogue cat) {
    final crypto = widget.market == 'crypto';
    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  Flexible(
                    child: Text(crypto ? 'Crypto Screener' : 'Stock Screener',
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: Obsidian.displayLg().copyWith(fontSize: 30)),
                  ),
                  const SizedBox(width: 10),
                  Container(
                    padding: const EdgeInsets.symmetric(
                        horizontal: 8, vertical: 3),
                    decoration: BoxDecoration(
                      color: Obsidian.green.withValues(alpha: 0.12),
                      borderRadius: BorderRadius.circular(6),
                      border: Border.all(
                          color: Obsidian.green.withValues(alpha: 0.45)),
                    ),
                    child: Text('PRO',
                        style: Obsidian.labelSm(
                            size: 9.5, color: Obsidian.green)),
                  ),
                ],
              ),
              const SizedBox(height: 3),
              Text('Multi-factor filter matrix · ${_subtitle(cat)}',
                  style: Obsidian.body(color: Obsidian.outline, size: 11.5)),
            ],
          ),
        ),
      ],
    );
  }

  String _subtitle(ScreenerCatalogue cat) {
    final age = cat.builtAt == null
        ? 'never built'
        : 'built ${_ago(cat.builtAt!)}';
    return '${cat.symbols} US stocks · $age';
  }

  // ------------------------------------------------------------- presets
  //
  // One row, not seven cards. See the library doc.
  Widget _presetBar(ScreenerCatalogue cat) {
    final active = _fromPreset == null
        ? null
        : cat.presets.where((p) => p.id == _fromPreset).firstOrNull;
    return InkWell(
      onTap: () => _pickPreset(cat),
      borderRadius: BorderRadius.circular(Obsidian.rMd),
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
        decoration: BoxDecoration(
          color: Obsidian.primaryContainer.withValues(alpha: 0.16),
          borderRadius: BorderRadius.circular(Obsidian.rMd),
          border: Border.all(
              color: Obsidian.primary.withValues(alpha: 0.4)),
        ),
        child: Row(
          children: [
            const Icon(Icons.auto_awesome_rounded,
                size: 16, color: Obsidian.primary),
            const SizedBox(width: 10),
            Expanded(
              child: Text(active?.name ?? 'Recommended screens',
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: Obsidian.body(size: 13, color: Obsidian.primary)
                      .copyWith(fontWeight: FontWeight.w600)),
            ),
            const Icon(Icons.expand_more_rounded,
                size: 18, color: Obsidian.primary),
          ],
        ),
      ),
    );
  }

  /// What the active preset had to leave out, and why.
  ///
  /// Only shown when something WAS left out. A permanent disclaimer is
  /// wallpaper; one that appears on the three presets it applies to is
  /// information.
  List<Widget> _droppedNote(ScreenerCatalogue cat) {
    final p = _fromPreset == null
        ? null
        : cat.presets.where((x) => x.id == _fromPreset).firstOrNull;
    if (p == null || p.dropped.isEmpty) return const [];
    final byId = cat.byId;
    final names =
        p.dropped.map((f) => byId[f.field]?.label ?? f.field).join(', ');
    return [
      const SizedBox(height: 6),
      InkWell(
        onTap: () => _explainMissing(names),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Icon(Icons.lock_outline_rounded,
                size: 12, color: Obsidian.amber),
            const SizedBox(width: 6),
            Expanded(
              child: Text('Running without $names — tap to see why',
                  style: Obsidian.body(color: Obsidian.amber, size: 10.5)),
            ),
          ],
        ),
      ),
    ];
  }

  /// The honest answer to "why can't you screen on this".
  ///
  /// Everything else in this app comes from sources that are free AND
  /// redistributable — prices from Alpaca, filings from the SEC, short
  /// interest from FINRA. Forward estimates are the one category that is
  /// neither: they are opinions collected from banks and sold, and there is
  /// no public filing to derive them from.
  Future<void> _explainMissing(String names) async {
    await showModalBottomSheet<void>(
      context: context,
      backgroundColor: Colors.transparent,
      isScrollControlled: true,
      builder: (_) => SafeArea(
        top: false,
        child: Padding(
          padding: const EdgeInsets.all(Obsidian.containerPadding),
          child: GlassPanel(
            active: true,
            padding: const EdgeInsets.fromLTRB(20, 20, 20, 10),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text('WHY THIS CRITERION IS MISSING',
                    style: Obsidian.labelSm(size: 11)),
                const SizedBox(height: 12),
                Text(names,
                    style: Obsidian.body(size: 13.5, color: Obsidian.amber)),
                const SizedBox(height: 12),
                Text(
                    'These are analyst estimates — what banks think a company '
                    'will earn next year. They are opinions that firms collect '
                    'and sell, not facts filed with a regulator, so unlike '
                    'every other field here there is no public source to '
                    'compute them from.\n\n'
                    'Everything else in this screener is free and legal to '
                    'show you: prices from Alpaca, company figures from SEC '
                    'filings, short interest from FINRA.\n\n'
                    'Unlocking these needs a paid data subscription — around '
                    '\$60 a month for the data itself, plus a separate '
                    'licence to display it to subscribers, which providers '
                    'quote individually and do not publish.\n\n'
                    'Until then the screen runs without them rather than '
                    'returning nothing.',
                    style: Obsidian.body(color: Obsidian.outline, size: 12)),
                const SizedBox(height: 8),
                Center(
                  child: TextButton(
                    onPressed: () => Navigator.of(context).pop(),
                    child: Text('Close',
                        style: Obsidian.body(color: Obsidian.primary)),
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }

  Future<void> _pickPreset(ScreenerCatalogue cat) async {
    final picked = await showModalBottomSheet<ScreenerPreset>(
      context: context,
      backgroundColor: Colors.transparent,
      isScrollControlled: true,
      builder: (_) => _PresetSheet(presets: cat.presets, current: _fromPreset),
    );
    if (picked != null) _applyPreset(picked);
  }

  /// A visible break between the controls and what they returned.
  ///
  /// Both were glass panels of the same size and colour, so the filter rows
  /// and the result cards read as one undifferentiated list and there was no
  /// telling where the screen stopped and the stocks started.
  Widget _resultsHeader() {
    final r = _result;
    return Row(
      children: [
        Text('RESULTS', style: Obsidian.labelSm(size: 11)),
        const SizedBox(width: 10),
        Expanded(
          child: Container(
              height: 1,
              color: Colors.white.withValues(alpha: 0.08)),
        ),
        const SizedBox(width: 10),
        // MATCHED ONLY.
        //
        // The unjudged count is gone from here on purpose: a screen exists to
        // return the stocks that meet every criterion, and a second number
        // beside it saying "and these nearly did" is an invitation to treat
        // near-misses as results. The presets no longer carry criteria that
        // cannot be judged, so a near-miss now means a genuinely missing
        // filing, which is not a match.
        if (r != null)
          Text('${r.matched} of ${r.scanned}',
              style: Obsidian.labelSm(size: 10, color: Obsidian.green)),
        if (_busy) ...[
          const SizedBox(width: 8),
          const SizedBox(
              width: 10,
              height: 10,
              child: CircularProgressIndicator(
                  strokeWidth: 1.5, color: Obsidian.primary)),
        ],
      ],
    );
  }

  // ------------------------------------------------------------- filters
  //
  // THE CRITERIA MATRIX, from the design: a grid of labelled cells rather
  // than a list of rows. Each cell names its field and shows the condition,
  // and an ACTIVE cell is outlined in mint with a dot — so the ones doing
  // work are visible without reading every value.
  Widget _filterSection(ScreenerCatalogue cat) => Container(
        padding: const EdgeInsets.all(14),
        decoration: BoxDecoration(
          color: Obsidian.surfaceLow.withValues(alpha: 0.8),
          borderRadius: BorderRadius.circular(Obsidian.rLg),
          border: Border.all(color: Colors.white.withValues(alpha: 0.10)),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                const Icon(Icons.filter_alt_rounded,
                    size: 15, color: Obsidian.green),
                const SizedBox(width: 8),
                Text('ACTIVE CRITERIA', style: Obsidian.labelSm(size: 10.5)),
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
                    child: Text('Reset All',
                        style: Obsidian.body(
                                color: Obsidian.outline, size: 11)
                            .copyWith(
                                decoration: TextDecoration.underline,
                                decorationColor: Obsidian.outlineVariant)),
                  ),
              ],
            ),
            const SizedBox(height: 12),
            if (_filters.isEmpty)
              Text('Nothing filtered — every ${widget.market == 'crypto'
                      ? 'pair' : 'stock'} matches. Pick a recommendation '
                  'above, or add a criterion.',
                  style: Obsidian.body(color: Obsidian.outline, size: 11.5))
            else
              // Two columns, as in the design. An odd count leaves the last
              // cell full width rather than a gap beside it.
              Column(
                children: [
                  for (var i = 0; i < _filters.length; i += 2)
                    Padding(
                      padding: const EdgeInsets.only(bottom: 8),
                      child: IntrinsicHeight(
                        child: Row(
                          crossAxisAlignment: CrossAxisAlignment.stretch,
                          children: [
                            Expanded(child: _criterion(cat, i)),
                            if (i + 1 < _filters.length) ...[
                              const SizedBox(width: 8),
                              Expanded(child: _criterion(cat, i + 1)),
                            ],
                          ],
                        ),
                      ),
                    ),
                ],
              ),
            const SizedBox(height: 4),
            Divider(color: Colors.white.withValues(alpha: 0.06), height: 18),
            // The applied bar: every criterion as a removable chip, scrolling
            // sideways, with Add Filter at the end.
            SingleChildScrollView(
              scrollDirection: Axis.horizontal,
              child: Row(
                children: [
                  Text('APPLIED:',
                      style: Obsidian.labelSm(
                          size: 9, color: Obsidian.outlineVariant)),
                  const SizedBox(width: 8),
                  for (var i = 0; i < _filters.length; i++) ...[
                    _appliedChip(cat, i),
                    const SizedBox(width: 6),
                  ],
                  InkWell(
                    onTap: () => _addFilter(cat),
                    borderRadius: BorderRadius.circular(20),
                    child: Container(
                      padding: const EdgeInsets.symmetric(
                          horizontal: 10, vertical: 5),
                      decoration: BoxDecoration(
                        color: Obsidian.primary.withValues(alpha: 0.10),
                        borderRadius: BorderRadius.circular(20),
                        border: Border.all(
                            color: Obsidian.primary.withValues(alpha: 0.35)),
                      ),
                      child: Text('+ Add Filter',
                          style: Obsidian.labelSm(
                              size: 9.5, color: Obsidian.primary)),
                    ),
                  ),
                ],
              ),
            ),
          ],
        ),
      );

  /// One cell of the matrix. Tapping it opens the editor for that criterion.
  Widget _criterion(ScreenerCatalogue cat, int i) {
    final f = _filters[i];
    final field = cat.byId[f.field];
    return InkWell(
      onTap: () => _editCriterion(cat, i),
      borderRadius: BorderRadius.circular(Obsidian.rMd),
      child: Container(
        padding: const EdgeInsets.fromLTRB(10, 8, 10, 9),
        decoration: BoxDecoration(
          color: Obsidian.surfaceLowest.withValues(alpha: 0.55),
          borderRadius: BorderRadius.circular(Obsidian.rMd),
          border: Border.all(color: Obsidian.green.withValues(alpha: 0.28)),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          mainAxisSize: MainAxisSize.min,
          children: [
            Row(
              children: [
                Expanded(
                  child: Text(
                      (field?.label ?? f.field).toUpperCase(),
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: Obsidian.labelSm(
                          size: 8.5, color: Obsidian.green)),
                ),
                Container(
                  width: 5,
                  height: 5,
                  decoration: const BoxDecoration(
                      color: Obsidian.green, shape: BoxShape.circle),
                ),
              ],
            ),
            const SizedBox(height: 5),
            Text(_condition(f, field),
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: Obsidian.body(size: 12.5, color: Obsidian.onSurface)),
          ],
        ),
      ),
    );
  }

  static String _condition(ScreenerFilter f, ScreenerField? field) {
    switch (f.op) {
      case 'is_true':
        return field?.id.startsWith('above_') ?? false ? 'Above' : 'Yes';
      case 'is_false':
        return field?.id.startsWith('above_') ?? false ? 'Below' : 'No';
      case 'between':
        return '${field?.format(f.value)} – ${field?.format(f.value2)}';
      case 'lt':
        return 'Under ${field?.format(f.value)}';
      case 'lte':
        return 'At most ${field?.format(f.value)}';
      case 'gte':
        return 'At least ${field?.format(f.value)}';
      default:
        return 'Over ${field?.format(f.value)}';
    }
  }

  Widget _appliedChip(ScreenerCatalogue cat, int i) {
    final f = _filters[i];
    final field = cat.byId[f.field];
    return Container(
      padding: const EdgeInsets.only(left: 10, right: 4, top: 4, bottom: 4),
      decoration: BoxDecoration(
        color: Colors.white.withValues(alpha: 0.05),
        borderRadius: BorderRadius.circular(20),
        border: Border.all(color: Obsidian.green.withValues(alpha: 0.35)),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Text(f.describe(field),
              style: Obsidian.dataTable(size: 10.5, color: Obsidian.green)),
          InkWell(
            onTap: () {
              setState(() => _filters.removeAt(i));
              _edited();
              _run();
            },
            customBorder: const CircleBorder(),
            child: const Padding(
              padding: EdgeInsets.all(4),
              child: Icon(Icons.close_rounded,
                  size: 11, color: Obsidian.outline),
            ),
          ),
        ],
      ),
    );
  }

  /// Editing one criterion — operator and value — in a sheet rather than
  /// inline. The matrix cell has room for the answer, not for the controls.
  Future<void> _editCriterion(ScreenerCatalogue cat, int i) async {
    final f = _filters[i];
    final field = cat.byId[f.field];
    final controller =
        TextEditingController(text: _trim(f.value));
    var op = f.op;

    await showModalBottomSheet<void>(
      context: context,
      backgroundColor: Colors.transparent,
      isScrollControlled: true,
      builder: (ctx) => StatefulBuilder(
        builder: (ctx, setSheet) => SafeArea(
          top: false,
          child: Padding(
            padding: EdgeInsets.only(
                left: Obsidian.containerPadding,
                right: Obsidian.containerPadding,
                top: 16,
                bottom: MediaQuery.of(ctx).viewInsets.bottom + 16),
            child: GlassPanel(
              active: true,
              padding: const EdgeInsets.fromLTRB(20, 20, 20, 10),
              child: Column(
                mainAxisSize: MainAxisSize.min,
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text((field?.label ?? f.field).toUpperCase(),
                      style: Obsidian.labelSm(size: 11)),
                  if ((field?.help ?? '').isNotEmpty) ...[
                    const SizedBox(height: 6),
                    Text(field!.help,
                        style: Obsidian.body(
                            color: Obsidian.outline, size: 11.5)),
                  ],
                  const SizedBox(height: 14),
                  Wrap(
                    spacing: 8,
                    runSpacing: 8,
                    children: [
                      for (final o in _opsFor(field))
                        ChoiceChip(
                          label: Text(_opLabel(o),
                              style: Obsidian.labelSm(size: 10)),
                          selected: op == o,
                          onSelected: (_) => setSheet(() => op = o),
                          selectedColor:
                              Obsidian.primary.withValues(alpha: 0.25),
                          backgroundColor: Obsidian.surfaceHigh,
                        ),
                    ],
                  ),
                  if (op != 'is_true' && op != 'is_false') ...[
                    const SizedBox(height: 14),
                    TextField(
                      controller: controller,
                      autofocus: true,
                      keyboardType: const TextInputType.numberWithOptions(
                          decimal: true, signed: true),
                      style: Obsidian.dataTable(size: 16),
                      decoration: const InputDecoration(
                          isDense: true, labelText: 'Value'),
                    ),
                  ],
                  const SizedBox(height: 16),
                  Row(
                    children: [
                      TextButton(
                        onPressed: () {
                          setState(() => _filters.removeAt(i));
                          Navigator.of(ctx).pop();
                          _edited();
                          _run();
                        },
                        child: Text('Remove',
                            style:
                                Obsidian.body(color: Obsidian.redSoft)),
                      ),
                      const Spacer(),
                      FilledButton(
                        style: FilledButton.styleFrom(
                            backgroundColor: Obsidian.primary,
                            foregroundColor: Obsidian.onPrimary),
                        onPressed: () {
                          setState(() {
                            f.op = op;
                            f.value = double.tryParse(controller.text);
                          });
                          Navigator.of(ctx).pop();
                          _edited();
                          _run();
                        },
                        child: const Text('Apply'),
                      ),
                    ],
                  ),
                ],
              ),
            ),
          ),
        ),
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
    final rows = <Widget>[];

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
    final change = row.metric('change_pct');
    final up = (change ?? 0) >= 0;
    final trained = row.trainedIntervals;
    final rsi = row.metric('rsi14');
    final vol = row.metric('quote_volume') ?? row.metric('avg_volume');

    // A solid card with a verdict-coloured left edge — deliberately NOT the
    // glass used by the controls above, so the eye can tell where the screen
    // ends and the matches begin.
    return Container(
      margin: const EdgeInsets.only(bottom: 8),
      decoration: BoxDecoration(
        color: Obsidian.surfaceLow,
        borderRadius: BorderRadius.circular(Obsidian.rMd),
        border: Border(
          left: BorderSide(
              color: row.unknown.isEmpty ? Obsidian.green : Obsidian.amber,
              width: 3),
        ),
      ),
      child: Material(
        color: Colors.transparent,
        child: InkWell(
          onTap: () => _open(row),
          borderRadius: BorderRadius.circular(Obsidian.rMd),
          child: Padding(
            padding: const EdgeInsets.fromLTRB(12, 12, 12, 12),
            child: Row(
              children: [
                // The ticker medallion from the design.
                Container(
                  width: 42,
                  height: 42,
                  decoration: BoxDecoration(
                    color: Obsidian.surfaceLowest,
                    shape: BoxShape.circle,
                    border: Border.all(
                        color: Colors.white.withValues(alpha: 0.10)),
                  ),
                  alignment: Alignment.center,
                  child: Text(
                      row.short.length > 4
                          ? row.short.substring(0, 4)
                          : row.short,
                      style: Obsidian.labelSm(
                          size: 10, color: Obsidian.onSurfaceVariant)),
                ),
                const SizedBox(width: 12),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                          widget.market == 'crypto'
                              ? '${row.short} / USDT'
                              : row.symbol,
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                          style: Obsidian.bodyLg()
                              .copyWith(fontWeight: FontWeight.w600)),
                      const SizedBox(height: 3),
                      Row(
                        children: [
                          if (trained.isNotEmpty) ...[
                            Container(
                              width: 5,
                              height: 5,
                              decoration: const BoxDecoration(
                                  color: Obsidian.green,
                                  shape: BoxShape.circle),
                            ),
                            const SizedBox(width: 5),
                            Text('BOT TRAINED',
                                style: Obsidian.labelSm(
                                    size: 8.5, color: Obsidian.green)),
                            const SizedBox(width: 8),
                          ] else if (row.name.isNotEmpty) ...[
                            Flexible(
                              child: Text(row.name,
                                  maxLines: 1,
                                  overflow: TextOverflow.ellipsis,
                                  style: Obsidian.body(
                                      color: Obsidian.outlineVariant,
                                      size: 10.5)),
                            ),
                            const SizedBox(width: 8),
                          ],
                          if (rsi != null)
                            Text('RSI ${rsi.toStringAsFixed(1)}',
                                style: Obsidian.dataTable(
                                    size: 10, color: Obsidian.outline)),
                        ],
                      ),
                    ],
                  ),
                ),
                const SizedBox(width: 8),
                Column(
                  crossAxisAlignment: CrossAxisAlignment.end,
                  children: [
                    Text(byId['price']?.format(row.metric('price')) ?? '—',
                        style: Obsidian.dataTable(
                            size: 14, w: FontWeight.w600)),
                    const SizedBox(height: 3),
                    Row(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        if (change != null)
                          Text(
                              '${up ? '+' : ''}'
                              '${change.toStringAsFixed(1)}%',
                              style: Obsidian.dataTable(
                                  size: 11,
                                  color: up ? Obsidian.green : Obsidian.red)),
                        if (vol != null) ...[
                          const SizedBox(width: 6),
                          Text('Vol ${ScreenerField.compact(vol)}',
                              style: Obsidian.labelSm(
                                  size: 8.5,
                                  color: Obsidian.outlineVariant)),
                        ],
                      ],
                    ),
                    const SizedBox(height: 4),
                    _followButton(row.symbol),
                  ],
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }

  Widget _followButton(String symbol) {
    final followed = _following.contains(symbol);
    return InkWell(
      onTap: () => _toggleFollow(symbol),
      borderRadius: BorderRadius.circular(6),
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 5),
        decoration: BoxDecoration(
          color: (followed ? Obsidian.green : Obsidian.primary)
              .withValues(alpha: 0.14),
          borderRadius: BorderRadius.circular(6),
        ),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(followed ? Icons.check_rounded : Icons.add_rounded,
                size: 12,
                color: followed ? Obsidian.green : Obsidian.primary),
            const SizedBox(width: 4),
            Text(followed ? 'Following' : 'Follow',
                style: Obsidian.labelSm(
                    size: 8.5,
                    color: followed ? Obsidian.green : Obsidian.primary)),
          ],
        ),
      ),
    );
  }

  Future<void> _toggleFollow(String symbol) async {
    if (_following.contains(symbol)) {
      await StockWatchlist.instance.remove(symbol);
    } else {
      await StockWatchlist.instance.add(symbol);
    }
    final list = await StockWatchlist.instance.load();
    if (!mounted) return;
    setState(() => _following = list.toSet());
  }

  /// Opens the stock's own page, not a browser.
  ///
  /// It used to launch Yahoo directly, which meant leaving the app to find
  /// out whether a result was worth leaving the app for. The Yahoo link is
  /// still there, at the bottom of that page.
  Future<void> _open(ScreenerRow row) async {
    await Navigator.of(context).push(MaterialPageRoute(
      builder: (ctx) => Scaffold(
        backgroundColor: Obsidian.background,
        body: SafeArea(
          bottom: false,
          child: StockScreen(
            client: widget.client,
            symbol: row.symbol,
            catalogue: _cat,
            onClose: () => Navigator.of(ctx).pop(),
          ),
        ),
      ),
    ));
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


/// The recommendations, as bare names.
///
/// No descriptions and no criterion chips: you already know what "oversold
/// bounce" means, and the seven explanations were what made this a screenful
/// rather than a menu. The criteria are all visible the moment you pick one —
/// they become the filter rows.
class _PresetSheet extends StatelessWidget {
  const _PresetSheet({required this.presets, this.current});

  final List<ScreenerPreset> presets;
  final String? current;

  @override
  Widget build(BuildContext context) => SafeArea(
        top: false,
        child: Padding(
          padding: const EdgeInsets.all(Obsidian.containerPadding),
          child: GlassPanel(
            active: true,
            padding: const EdgeInsets.fromLTRB(20, 20, 20, 10),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text('RECOMMENDED SCREENS',
                    style: Obsidian.labelSm(size: 11)),
                const SizedBox(height: 12),
                for (final p in presets)
                  InkWell(
                    onTap: () => Navigator.of(context).pop(p),
                    borderRadius: BorderRadius.circular(Obsidian.rMd),
                    child: Padding(
                      padding: const EdgeInsets.symmetric(vertical: 11),
                      child: Row(
                        children: [
                          Expanded(
                            child: Text(p.name,
                                style: Obsidian.body(
                                    size: 13.5,
                                    color: p.id == current
                                        ? Obsidian.primary
                                        : null)),
                          ),
                          // The one thing worth saying about a preset before
                          // you pick it: whether we can actually judge it.
                          if (p.dropped.isNotEmpty)
                            Container(
                              padding: const EdgeInsets.symmetric(
                                  horizontal: 6, vertical: 2),
                              decoration: BoxDecoration(
                                  color: Obsidian.amber
                                      .withValues(alpha: 0.14),
                                  borderRadius: BorderRadius.circular(5)),
                              child: Text(
                                  '−${p.dropped.length} criteria',
                                  style: Obsidian.labelSm(
                                      size: 8.5, color: Obsidian.amber)),
                            ),
                          if (p.id == current)
                            const Padding(
                              padding: EdgeInsets.only(left: 8),
                              child: Icon(Icons.check_rounded,
                                  size: 16, color: Obsidian.primary),
                            ),
                        ],
                      ),
                    ),
                  ),
                const SizedBox(height: 6),
                Center(
                  child: TextButton(
                    onPressed: () => Navigator.of(context).pop(),
                    child: Text('Cancel',
                        style: Obsidian.body(color: Obsidian.outline)),
                  ),
                ),
              ],
            ),
          ),
        ),
      );
}
