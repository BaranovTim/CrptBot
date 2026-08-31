/// Everything that happened, in one place.
///
/// WHY THE SUMMARY IS THE PUBLISHER'S OWN
///     Each card opens to the excerpt the outlet publishes in its feed, with
///     a link to the article at the bottom. Not the full text: reproducing
///     that inside an app you intend to charge for is republishing someone
///     else's work. The excerpt is what RSS exists to syndicate, and the
///     link is how the publisher gets the visit they are owed for it.
///
/// WHY "NO READING" IS NOT "NEUTRAL"
///     The bias and impact labels come from Agent 3's offline keyword scorer,
///     which stays silent on roughly 40% of headlines. Those are shown as
///     NO READING rather than folded into a neutral bucket — "we cannot tell"
///     and "this is balanced" are different claims, and only one of them is
///     something the scorer measured.
library;

import 'package:flutter/material.dart';

import '../api/client.dart';
import '../api/models.dart';
import '../theme/liquid_obsidian.dart';
import '../widgets/article_sheet.dart';
import '../widgets/glass.dart';

enum FeedMode { both, news, whales }

class NewsScreen extends StatefulWidget {
  const NewsScreen({super.key, required this.client, required this.symbol});

  final ApiClient client;

  /// The pair the dashboard is on, so the feed can lead with what is
  /// relevant to it. Macro stories are kept for every coin.
  final String symbol;

  @override
  State<NewsScreen> createState() => _NewsScreenState();
}

class _NewsScreenState extends State<NewsScreen> {
  FeedMode _mode = FeedMode.both;
  bool _onlyThisCoin = true;
  List<NewsItem> _news = const [];
  List<WhaleEvent> _whales = const [];
  bool _loading = true;
  String? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void didUpdateWidget(covariant NewsScreen old) {
    super.didUpdateWidget(old);
    if (old.symbol != widget.symbol && _onlyThisCoin) _load();
  }

  Future<void> _load() async {
    setState(() => _loading = _news.isEmpty && _whales.isEmpty);
    try {
      final results = await Future.wait([
        widget.client
            .news(limit: 60, symbol: _onlyThisCoin ? widget.symbol : null),
        widget.client.whales(limit: 40),
      ]);
      if (!mounted) return;
      setState(() {
        _news = results[0] as List<NewsItem>;
        _whales = results[1] as List<WhaleEvent>;
        _loading = false;
        _error = null;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _loading = false;
        _error = '$e';
      });
    }
  }

  Future<void> _open(NewsItem n) async {
    await showModalBottomSheet<void>(
      context: context,
      backgroundColor: Colors.transparent,
      isScrollControlled: true,
      builder: (_) => ArticleSheet(item: n),
    );
  }

  @override
  Widget build(BuildContext context) {
    return RefreshIndicator(
      onRefresh: _load,
      backgroundColor: Obsidian.surfaceContainer,
      color: Obsidian.primary,
      child: ListView(
        addRepaintBoundaries: false,
        physics: const AlwaysScrollableScrollPhysics(),
        padding: const EdgeInsets.fromLTRB(Obsidian.containerPadding, 8,
            Obsidian.containerPadding, Obsidian.navClearance + 24),
        children: [
          Text('What happened', style: Obsidian.displayLg()),
          const SizedBox(height: 4),
          Text(
              _onlyThisCoin
                  ? '${widget.symbol} and market-wide stories'
                  : 'Everything, all coins',
              style: Obsidian.body(color: Obsidian.outline)),
          const SizedBox(height: 16),
          _switcher(),
          const SizedBox(height: 10),
          _coinToggle(),
          const SizedBox(height: 18),
          if (_error != null)
            Text(_error!, style: Obsidian.body(color: Obsidian.redSoft)),
          if (_loading)
            const Padding(
              padding: EdgeInsets.only(top: 60),
              child: Center(
                  child: CircularProgressIndicator(color: Obsidian.primary)),
            )
          else
            ..._feed(),
        ],
      ),
    );
  }

  Widget _switcher() {
    Widget seg(FeedMode m, String label) {
      final on = _mode == m;
      return Expanded(
        child: InkWell(
          onTap: () => setState(() => _mode = m),
          borderRadius: BorderRadius.circular(Obsidian.rMd),
          child: Container(
            padding: const EdgeInsets.symmetric(vertical: 11),
            decoration: BoxDecoration(
              color: on
                  ? Obsidian.primaryContainer.withValues(alpha: 0.22)
                  : Colors.transparent,
              borderRadius: BorderRadius.circular(Obsidian.rMd),
              border: Border.all(
                  color: on
                      ? Obsidian.primary.withValues(alpha: 0.5)
                      : Colors.white.withValues(alpha: 0.08)),
            ),
            alignment: Alignment.center,
            child: Text(label,
                style: Obsidian.labelSm(
                    size: 10.5,
                    color: on ? Obsidian.primary : Obsidian.outline)),
          ),
        ),
      );
    }

    return Row(children: [
      seg(FeedMode.both, 'BOTH'),
      const SizedBox(width: 8),
      seg(FeedMode.news, 'NEWS'),
      const SizedBox(width: 8),
      seg(FeedMode.whales, 'WHALES'),
    ]);
  }

  Widget _coinToggle() => InkWell(
        onTap: () {
          setState(() => _onlyThisCoin = !_onlyThisCoin);
          _load();
        },
        borderRadius: BorderRadius.circular(Obsidian.rMd),
        child: Padding(
          padding: const EdgeInsets.symmetric(vertical: 6),
          child: Row(
            children: [
              Icon(
                  _onlyThisCoin
                      ? Icons.filter_alt_rounded
                      : Icons.filter_alt_off_rounded,
                  size: 16,
                  color: Obsidian.outline),
              const SizedBox(width: 8),
              Text(
                  _onlyThisCoin
                      ? 'Filtered to ${widget.symbol} — tap to see everything'
                      : 'Showing every coin — tap to filter',
                  style: Obsidian.body(color: Obsidian.outline, size: 12)),
            ],
          ),
        ),
      );

  List<Widget> _feed() {
    final rows = <Widget>[];
    if (_mode != FeedMode.whales) {
      for (final n in _news) {
        rows.add(_newsCard(n));
        rows.add(const SizedBox(height: Obsidian.gutter));
      }
    }
    if (_mode != FeedMode.news) {
      for (final w in _whales.where((w) => !w.mechanical)) {
        rows.add(_whaleCard(w));
        rows.add(const SizedBox(height: Obsidian.gutter));
      }
    }
    if (rows.isEmpty) {
      rows.add(Padding(
        padding: const EdgeInsets.only(top: 60),
        child: Center(
          child: Text('Nothing here yet.',
              style: Obsidian.body(color: Obsidian.outline)),
        ),
      ));
    }
    return rows;
  }

  Widget _newsCard(NewsItem n) {
    final c = biasColour(n.bias);
    return GlassPanel(
      padding: const EdgeInsets.all(16),
      onTap: () => _open(n),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              _chip(n.bias, c),
              const SizedBox(width: 6),
              Flexible(child: _chip(n.impact, Obsidian.outline)),
              const Spacer(),
              Text(_ago(n.publishedAt), style: Obsidian.labelSm(size: 9.5)),
            ],
          ),
          const SizedBox(height: 12),
          Text(n.headline,
              style: Obsidian.bodyLg().copyWith(fontWeight: FontWeight.w600)),
          if (n.summary.isNotEmpty) ...[
            const SizedBox(height: 8),
            Text(n.summary,
                maxLines: 2,
                overflow: TextOverflow.ellipsis,
                style: Obsidian.body(color: Obsidian.outline, size: 12.5)),
          ],
          const SizedBox(height: 10),
          Row(
            children: [
              Text(n.source.toUpperCase(), style: Obsidian.labelSm(size: 9.5)),
              const Spacer(),
              Text('Read', style: Obsidian.labelSm(size: 9.5,
                  color: Obsidian.primary)),
              const Icon(Icons.chevron_right_rounded,
                  size: 14, color: Obsidian.primary),
            ],
          ),
        ],
      ),
    );
  }

  Widget _whaleCard(WhaleEvent w) => GlassPanel(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                _chip('WHALE', Obsidian.primary),
                const SizedBox(width: 6),
                Flexible(child: _chip(w.impact, Obsidian.outline)),
              ],
            ),
            const SizedBox(height: 12),
            Text(w.describe, style: Obsidian.body(size: 14)),
            const SizedBox(height: 8),
            Text(w.note,
                style: Obsidian.labelSm(color: Obsidian.outline, size: 10)),
          ],
        ),
      );

  Widget _chip(String text, Color c) => Container(
        padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
        decoration: BoxDecoration(
          color: c.withValues(alpha: 0.14),
          borderRadius: BorderRadius.circular(6),
        ),
        child: Text(text,
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
            style: Obsidian.labelSm(size: 9, color: c)),
      );

  static String _ago(DateTime? t) {
    if (t == null) return '';
    final d = DateTime.now().difference(t);
    if (d.inMinutes < 60) return '${d.inMinutes}m ago';
    if (d.inHours < 24) return '${d.inHours}h ago';
    return '${d.inDays}d ago';
  }
}
