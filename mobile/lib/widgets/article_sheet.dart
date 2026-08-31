/// The panel a headline opens into: the publisher's own excerpt, with the
/// link to the full article at the BOTTOM.
///
/// Shared by the dashboard card and the news feed, so both behave the same
/// way — the dashboard used to throw you straight into a browser, which
/// meant leaving the app to find out whether the story was worth leaving
/// the app for.
library;

import 'package:flutter/material.dart';
import 'package:url_launcher/url_launcher.dart';

import '../api/models.dart';
import '../theme/liquid_obsidian.dart';
import 'glass.dart';

Color biasColour(String bias) => switch (bias) {
      'BULL' => Obsidian.green,
      'BEAR' => Obsidian.red,
      'MIXED' => Obsidian.primary,
      _ => Obsidian.outline,
    };

class ArticleSheet extends StatelessWidget {
  const ArticleSheet({super.key, required this.item});

  final NewsItem item;

  @override
  Widget build(BuildContext context) {
    final c = biasColour(item.bias);
    return SafeArea(
      top: false,
      child: Padding(
        padding: const EdgeInsets.all(Obsidian.containerPadding),
        child: GlassPanel(
          active: true,
          padding: const EdgeInsets.all(22),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  Text(item.bias,
                      style: Obsidian.labelSm(color: c, size: 10)),
                  const SizedBox(width: 8),
                  Text(item.impact, style: Obsidian.labelSm(size: 10)),
                  const Spacer(),
                  Text(item.source.toUpperCase(),
                      style: Obsidian.labelSm(size: 10)),
                ],
              ),
              const SizedBox(height: 14),
              Text(item.headline,
                  style: Obsidian.headlineMd().copyWith(height: 1.25)),
              const SizedBox(height: 14),
              Flexible(
                child: SingleChildScrollView(
                  child: Text(
                      item.summary.isEmpty
                          ? 'No summary was published with this headline.'
                          : item.summary,
                      style: Obsidian.body(size: 13.5)),
                ),
              ),
              if (!item.hasReading) ...[
                const SizedBox(height: 12),
                Text(
                    'No bias reading: the offline scorer found nothing '
                    'directional in this headline. That is not the same as '
                    'saying it is neutral.',
                    style: Obsidian.body(color: Obsidian.outline, size: 11)),
              ],
              const SizedBox(height: 18),
              // The link belongs at the BOTTOM, after the summary — the
              // whole point of this sheet is that you can read enough here
              // without being thrown into a browser first.
              if (item.url.isNotEmpty)
                SizedBox(
                  width: double.infinity,
                  height: 46,
                  child: OutlinedButton.icon(
                    onPressed: () => launchUrl(Uri.parse(item.url),
                        mode: LaunchMode.externalApplication),
                    icon: const Icon(Icons.open_in_new_rounded, size: 16),
                    label: Text('Read the full article at ${item.source}',
                        style: Obsidian.body(size: 12.5)),
                    style: OutlinedButton.styleFrom(
                      foregroundColor: Obsidian.primary,
                      side: BorderSide(
                          color: Obsidian.primary.withValues(alpha: 0.4)),
                      shape: RoundedRectangleBorder(
                          borderRadius:
                              BorderRadius.circular(Obsidian.rMd)),
                    ),
                  ),
                ),
            ],
          ),
        ),
      ),
    );
  }
}
