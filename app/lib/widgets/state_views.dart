// Consistent loading / empty / error views.
//
// Replaces the per-screen ad-hoc CircularProgressIndicator / "Nothing
// here yet." Text / inline error strings that drifted across screens
// in the redesign. Use these three widgets anywhere a future-backed
// surface needs a placeholder.
//
// Theming reads from [HiveTokens] so the views match the marble shell.

import 'package:flutter/material.dart';

import '../theme/hive_tokens.dart';

/// In-progress placeholder. Centred spinner with a thin caption.
class LoadingView extends StatelessWidget {
  const LoadingView({super.key, this.message});

  /// Caption rendered under the spinner. Optional — omit on screens
  /// where the surrounding UI already explains what's loading.
  final String? message;

  @override
  Widget build(BuildContext context) {
    final tokens = Theme.of(context).extension<HiveTokens>()!;
    return Center(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          SizedBox(
            width: 28,
            height: 28,
            child: CircularProgressIndicator(
              strokeWidth: 2.4,
              color: tokens.amber1,
            ),
          ),
          if (message != null) ...[
            const SizedBox(height: 12),
            Text(
              message!,
              style: TextStyle(
                color: tokens.ink.withValues(alpha: 0.7),
                fontSize: 13,
              ),
            ),
          ],
        ],
      ),
    );
  }
}

/// "Nothing here yet" surface. Optional title / hint / call-to-action.
class EmptyView extends StatelessWidget {
  const EmptyView({
    super.key,
    required this.title,
    this.hint,
    this.action,
    this.icon,
  });

  /// One-line headline ("No recent renders.", "No saved notes.").
  final String title;

  /// Optional second-line hint with concrete next step
  /// ("Ask Hive to make an image.").
  final String? hint;

  /// Optional action button (e.g. a "Create" CTA).
  final Widget? action;

  /// Optional leading icon — defaults to a friendly outline icon when
  /// omitted.
  final IconData? icon;

  @override
  Widget build(BuildContext context) {
    final tokens = Theme.of(context).extension<HiveTokens>()!;
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(24),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(
              icon ?? Icons.inbox_outlined,
              size: 36,
              color: tokens.amber1.withValues(alpha: 0.6),
            ),
            const SizedBox(height: 16),
            Text(
              title,
              textAlign: TextAlign.center,
              style: TextStyle(
                color: tokens.ink,
                fontSize: 15,
                fontWeight: FontWeight.w600,
              ),
            ),
            if (hint != null) ...[
              const SizedBox(height: 6),
              Text(
                hint!,
                textAlign: TextAlign.center,
                style: TextStyle(
                  color: tokens.ink.withValues(alpha: 0.65),
                  fontSize: 13,
                ),
              ),
            ],
            if (action != null) ...[
              const SizedBox(height: 14),
              action!,
            ],
          ],
        ),
      ),
    );
  }
}

/// Error placeholder with a retry button.
class ErrorView extends StatelessWidget {
  const ErrorView({
    super.key,
    required this.error,
    this.onRetry,
    this.title = 'Something went wrong',
  });

  /// Free-text error message (may be a server message, an exception
  /// string, or a hand-written explanation). Rendered in a muted style
  /// so a stack-trace string doesn't dominate the view.
  final String error;

  /// Optional retry callback. When provided, renders a "Try again"
  /// outlined button below the error text.
  final VoidCallback? onRetry;

  /// Optional headline — defaults to "Something went wrong".
  final String title;

  @override
  Widget build(BuildContext context) {
    final tokens = Theme.of(context).extension<HiveTokens>()!;
    final colors = Theme.of(context).colorScheme;
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(24),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(
              Icons.error_outline,
              size: 32,
              color: colors.error,
            ),
            const SizedBox(height: 12),
            Text(
              title,
              textAlign: TextAlign.center,
              style: TextStyle(
                color: tokens.ink,
                fontSize: 14,
                fontWeight: FontWeight.w600,
              ),
            ),
            const SizedBox(height: 6),
            Text(
              error,
              textAlign: TextAlign.center,
              style: TextStyle(
                color: tokens.ink.withValues(alpha: 0.65),
                fontSize: 12,
              ),
              maxLines: 4,
              overflow: TextOverflow.ellipsis,
            ),
            if (onRetry != null) ...[
              const SizedBox(height: 12),
              OutlinedButton.icon(
                onPressed: onRetry,
                icon: const Icon(Icons.refresh, size: 16),
                label: const Text('Try again'),
              ),
            ],
          ],
        ),
      ),
    );
  }
}
