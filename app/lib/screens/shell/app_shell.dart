import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../state/chat_state.dart';
import '../../state/home_state.dart';
import '../../state/session.dart';
import '../../theme/hive_theme.dart';
import '../../theme/hive_tokens.dart';
import '../../widgets/hive_motion.dart';
import '../../widgets/status_chip.dart';
import '../board/board_screen.dart';
import '../calendar/calendar_screen.dart';
import '../chat/chat_screen.dart';
import '../escalations/escalations_screen.dart';
import '../home/home_screen.dart';
import '../lora/lora_screen.dart';
import '../more/more_screen.dart';
import '../scout/scout_screen.dart';
import '../skills/skills_screen.dart';
import '../studio/studio_screen.dart';
import '../telemetry/telemetry_screen.dart';
import '../vault/vault_screen.dart';
import 'command_palette.dart';

/// One nav destination. Primary ones show in the phone bottom bar; the
/// rail (desktop) shows ALL of them — the More overflow disappears wide.
class ShellDestination {
  const ShellDestination({
    required this.label,
    required this.icon,
    required this.selectedIcon,
    this.primary = true,
  });
  final String label;
  final IconData icon;
  final IconData selectedIcon;
  final bool primary;
}

const kShellDestinations = <ShellDestination>[
  ShellDestination(
      label: 'Home',
      icon: Icons.home_outlined,
      selectedIcon: Icons.home),
  ShellDestination(
      label: 'Chat',
      icon: Icons.chat_bubble_outline,
      selectedIcon: Icons.chat_bubble),
  ShellDestination(
      label: 'Board',
      icon: Icons.dashboard_outlined,
      selectedIcon: Icons.dashboard),
  ShellDestination(
      label: 'Alerts',
      icon: Icons.priority_high_outlined,
      selectedIcon: Icons.priority_high),
  ShellDestination(
      label: 'Vault',
      icon: Icons.book_outlined,
      selectedIcon: Icons.book,
      primary: false),
  ShellDestination(
      label: 'Skills',
      icon: Icons.school_outlined,
      selectedIcon: Icons.school,
      primary: false),
  ShellDestination(
      label: 'Scout',
      icon: Icons.radar_outlined,
      selectedIcon: Icons.radar,
      primary: false),
  ShellDestination(
      label: 'Studio',
      icon: Icons.brush_outlined,
      selectedIcon: Icons.brush,
      primary: false),
  ShellDestination(
      label: 'Calendar',
      icon: Icons.calendar_month_outlined,
      selectedIcon: Icons.calendar_month,
      primary: false),
  ShellDestination(
      label: 'LoRA',
      icon: Icons.auto_awesome_outlined,
      selectedIcon: Icons.auto_awesome,
      primary: false),
  ShellDestination(
      label: 'Telemetry',
      icon: Icons.monitor_heart_outlined,
      selectedIcon: Icons.monitor_heart,
      primary: false),
];

const double kRailBreakpoint = 600;

/// Canonical page count — always 12 pages in the IndexedStack:
/// 0=Home, 1=Chat, 2=Board, 3=Alerts, 4=Vault, 5=Skills, 6=Scout,
/// 7=Studio, 8=Calendar, 9=LoRA, 10=Telemetry, 11=More.
const int kPageCount = 12;

/// Phone bottom-nav slots → canonical page indices.
/// Slots: 0=Home, 1=Chat, 2=Board, 3=Alerts, 4=More.
const List<int> kPhoneSlotToPage = [0, 1, 2, 3, 11];

/// Resolve a canonical page index to a phone nav slot.
/// Pages 4-10 (Vault..Telemetry) are not primary phone tabs; they are
/// reachable through the More hub. When on one of those pages we
/// highlight the More slot (4) to indicate where they came from.
int phoneSlotForPage(int pageIndex) {
  final slot = kPhoneSlotToPage.indexOf(pageIndex);
  // If not found (pages 4-10), highlight the More slot.
  return slot >= 0 ? slot : 4;
}

/// Resolve a phone nav slot to a canonical page index.
int pageForPhoneSlot(int slot) =>
    kPhoneSlotToPage[slot.clamp(0, kPhoneSlotToPage.length - 1)];

/// Canonical label → page index lookup (for CommandPaletteScope).
const Map<String, int> kLabelToPageIndex = {
  'Home': 0,
  'Chat': 1,
  'Board': 2,
  'Alerts': 3,
  'Vault': 4,
  'Skills': 5,
  'Scout': 6,
  'Studio': 7,
  'Calendar': 8,
  'LoRA': 9,
  'Telemetry': 10,
  'More': 11,
};

/// Pure adaptive layout: bottom NavigationBar under [kRailBreakpoint]
/// logical px, NavigationRail at/above it. No providers — testable.
class AdaptiveScaffold extends StatelessWidget {
  const AdaptiveScaffold({
    super.key,
    required this.destinations,
    required this.body,
    required this.selectedIndex,
    this.onSelect,
    this.badges = const {},
    this.header,
  });

  final List<ShellDestination> destinations;
  final Widget body;
  final int selectedIndex;
  final ValueChanged<int>? onSelect;
  final Map<int, int> badges; // destination index -> badge count
  final Widget? header;

  Widget _badge(int i, Widget child) {
    final n = badges[i] ?? 0;
    return Badge(isLabelVisible: n > 0, label: Text('$n'), child: child);
  }

  @override
  Widget build(BuildContext context) {
    return LayoutBuilder(builder: (context, c) {
      final wide = c.maxWidth >= kRailBreakpoint;
      final clamped = selectedIndex.clamp(0, destinations.length - 1);
      if (!wide) {
        // Phone: primary destinations + a More tab appended by AppShell.
        return Scaffold(
          body: body,
          bottomNavigationBar: NavigationBar(
            selectedIndex: clamped,
            onDestinationSelected: onSelect,
            destinations: [
              for (final (i, d) in destinations.indexed)
                NavigationDestination(
                  icon: _badge(i, Icon(d.icon)),
                  selectedIcon: _badge(
                    i,
                    AmberGlowBox(
                      active: i == clamped,
                      child: Padding(
                        padding: const EdgeInsets.all(4),
                        child: Icon(d.selectedIcon),
                      ),
                    ),
                  ),
                  label: d.label,
                ),
            ],
          ),
        );
      }
      return Scaffold(
        body: Row(children: [
          SafeArea(
            child: SingleChildScrollView(
              child: ConstrainedBox(
                constraints: BoxConstraints(
                    minHeight: c.maxHeight -
                        MediaQuery.of(context).padding.vertical),
                child: IntrinsicHeight(
                  child: NavigationRail(
                    selectedIndex: clamped,
                    onDestinationSelected: onSelect,
                    labelType: NavigationRailLabelType.all,
                    leading: header,
                    destinations: [
                      for (final (i, d) in destinations.indexed)
                        NavigationRailDestination(
                          icon: _badge(i, Icon(d.icon)),
                          selectedIcon: AmberGlowBox(
                            active: i == clamped,
                            child: Padding(
                              padding: const EdgeInsets.all(4),
                              child: Icon(d.selectedIcon),
                            ),
                          ),
                          label: Text(d.label),
                        ),
                    ],
                  ),
                ),
              ),
            ),
          ),
          const VerticalDivider(width: 1),
          Expanded(child: body),
        ]),
      );
    });
  }
}

/// v2 nav shell — adaptive + command palette + status chip.
class AppShell extends ConsumerStatefulWidget {
  const AppShell({super.key});

  @override
  ConsumerState<AppShell> createState() => _AppShellState();
}

class _AppShellState extends ConsumerState<AppShell> {
  /// Canonical page index (0..11). Always into the stable 12-page list.
  int _pageIndex = 0;

  /// Navigate to a canonical page index. Safe to call from any layout.
  void goTo(int pageIndex) =>
      setState(() => _pageIndex = pageIndex.clamp(0, kPageCount - 1));

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context).extension<HiveTokens>()!;
    final openEsc = ref.watch(escalationsProvider).maybeWhen(
          data: (l) => l.where((e) => !e.resolved).length,
          orElse: () => 0,
        );
    final bot = ref.watch(botsProvider).maybeWhen(
        data: (b) => b.isNotEmpty ? b.first : 'terry', orElse: () => 'terry');

    Widget page(ShellDestination d) => switch (d.label) {
          'Home' => _titled('Hive', const HomeScreen(), t,
              action: IconButton(
                icon: Icon(Icons.logout, color: t.slate4, size: 20),
                tooltip: 'Disconnect',
                onPressed: () =>
                    ref.read(sessionProvider.notifier).disconnect(),
              )),
          'Chat' => _titled(bot, ChatScreen(bot: bot), t),
          'Board' => const BoardScreen(),
          'Alerts' => _titled('Escalations', const EscalationsScreen(), t),
          'Vault' => _titled('Vault', const VaultScreen(), t),
          'Skills' => _titled('Skills', const SkillsScreen(), t),
          'Scout' => _titled('Scout', const ScoutScreen(), t),
          'Studio' => _titled('Studio', const StudioScreen(), t),
          'Calendar' => _titled('Calendar', const CalendarScreen(), t),
          'LoRA' => _titled('LoRA', const LoraScreen(), t),
          'Telemetry' => _titled('Telemetry', const TelemetryScreen(), t),
          _ => _titled('More', const MoreScreen(), t),
        };

    // Stable canonical page list — always 12 entries in the same order,
    // independent of layout. This is what IndexedStack uses so Flutter never
    // tears down existing screen widgets across a breakpoint resize.
    const canonicalDests = <ShellDestination>[
      ...kShellDestinations, // indices 0-10
      ShellDestination(
          label: 'More',
          icon: Icons.apps_outlined,
          selectedIcon: Icons.apps), // index 11
    ];
    final stablePages = [for (final d in canonicalDests) page(d)];

    return LayoutBuilder(builder: (context, c) {
      final wide = c.maxWidth >= kRailBreakpoint;

      // ── Phone layout ──────────────────────────────────────────────────────
      // 5 nav slots: Home(0), Chat(1), Board(2), Alerts(3), More(4).
      // Nav-slot index is separate from page index.
      if (!wide) {
        final phoneNavDests = [
          ...kShellDestinations.where((d) => d.primary),
          const ShellDestination(
              label: 'More',
              icon: Icons.apps_outlined,
              selectedIcon: Icons.apps),
        ];
        final phoneSlot = phoneSlotForPage(_pageIndex); // 0..4
        // Alerts is phone slot 3; badge keyed by slot.
        const alertsPhoneSlot = 3;

        return CommandPaletteScope(
          goTo: (label) {
            final i = kLabelToPageIndex[label];
            if (i != null) goTo(i);
          },
          child: AdaptiveScaffold(
            destinations: phoneNavDests,
            selectedIndex: phoneSlot,
            onSelect: (slot) => goTo(pageForPhoneSlot(slot)),
            badges: {alertsPhoneSlot: openEsc},
            header: null,
            body: IndexedStack(
              index: _pageIndex,
              children: stablePages,
            ),
          ),
        );
      }

      // ── Desktop layout ────────────────────────────────────────────────────
      // 11 nav slots: kShellDestinations[0..10] → page indices 0..10.
      // Slot index == page index for desktop (no More entry in rail).
      final desktopSlot = _pageIndex.clamp(0, kShellDestinations.length - 1);
      // Alerts is desktop slot 3; badge keyed by slot.
      const alertsDesktopSlot = 3;

      return CommandPaletteScope(
        goTo: (label) {
          final i = kLabelToPageIndex[label];
          if (i != null) goTo(i);
        },
        child: AdaptiveScaffold(
          destinations: kShellDestinations,
          selectedIndex: desktopSlot,
          onSelect: (slot) => goTo(slot), // slot == page index on desktop
          badges: {alertsDesktopSlot: openEsc},
          header: const Padding(
            padding: EdgeInsets.symmetric(vertical: HiveTokens.s2),
            child: StatusChip(),
          ),
          body: IndexedStack(
            index: _pageIndex,
            children: stablePages,
          ),
        ),
      );
    });
  }

  Widget _titled(String title, Widget body, HiveTokens t, {Widget? action}) =>
      Scaffold(
        backgroundColor: Colors.transparent,
        appBar: AppBar(
          backgroundColor: Colors.transparent,
          elevation: 0,
          title: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              const HexLogo(size: 18),
              const SizedBox(width: 8),
              Text(
                title,
                style: const TextStyle(
                  fontSize: 21,
                  fontWeight: FontWeight.w800,
                  letterSpacing: -0.21,
                ),
              ),
            ],
          ),
          actions: [
            IconButton(
              icon: Icon(Icons.search, color: t.inkFaint, size: 20),
              tooltip: 'Jump to… (Ctrl+K)',
              onPressed: () =>
                  CommandPaletteScope.of(context)?.openPalette(),
            ),
            const Padding(
              padding: EdgeInsets.only(right: HiveTokens.s2),
              child: Center(child: StatusChip()),
            ),
            ?action,
          ],
        ),
        body: hiveScaffoldBackground(body),
      );
}
