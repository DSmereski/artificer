import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:ai_team_app_v2/screens/shell/app_shell.dart';

void main() {
  testWidgets('narrow width uses bottom NavigationBar', (tester) async {
    tester.view.physicalSize = const Size(400, 800);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.reset);
    await tester.pumpWidget(const MaterialApp(
        home: AdaptiveScaffold(destinations: kShellDestinations, body: Text('x'),
            selectedIndex: 0)));
    expect(find.byType(NavigationBar), findsOneWidget);
    expect(find.byType(NavigationRail), findsNothing);
  });

  testWidgets('wide width uses NavigationRail', (tester) async {
    tester.view.physicalSize = const Size(1200, 800);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.reset);
    await tester.pumpWidget(const MaterialApp(
        home: AdaptiveScaffold(destinations: kShellDestinations, body: Text('x'),
            selectedIndex: 0)));
    expect(find.byType(NavigationRail), findsOneWidget);
    expect(find.byType(NavigationBar), findsNothing);
  });

  // I1 fix: stable 12-page IndexedStack — slot↔page mapping unit tests.
  //
  // We use the extracted pure-function approach (phoneSlotForPage /
  // pageForPhoneSlot / kLabelToPageIndex) rather than mounting the full
  // AppShell, because AppShell requires live Riverpod providers
  // (escalationsProvider, botsProvider, sessionProvider, gatewayClientProvider)
  // that are awkward to stub in a unit test. The mapping functions are the
  // key correctness surface of the fix.
  group('stable shell page mapping (I1 fix)', () {
    test('kPageCount is 12', () {
      expect(kPageCount, 12);
    });

    test('kPhoneSlotToPage has 5 entries covering phone slots 0..4', () {
      expect(kPhoneSlotToPage.length, 5);
    });

    test('pageForPhoneSlot round-trips for all 5 phone slots', () {
      for (var slot = 0; slot < kPhoneSlotToPage.length; slot++) {
        expect(pageForPhoneSlot(slot), kPhoneSlotToPage[slot],
            reason: 'slot $slot');
      }
    });

    test('phoneSlotForPage: primary pages map to their slot', () {
      // Home=page0→slot0, Chat=1→1, Board=2→2, Alerts=3→3, More=11→4
      expect(phoneSlotForPage(0), 0); // Home
      expect(phoneSlotForPage(1), 1); // Chat
      expect(phoneSlotForPage(2), 2); // Board
      expect(phoneSlotForPage(3), 3); // Alerts
      expect(phoneSlotForPage(11), 4); // More
    });

    test('phoneSlotForPage: non-primary pages (4-10) highlight More slot', () {
      // Vault=4 .. Telemetry=10 are reachable via More hub; highlight slot 4.
      for (var page = 4; page <= 10; page++) {
        expect(phoneSlotForPage(page), 4,
            reason: 'page $page should highlight More slot');
      }
    });

    test('kLabelToPageIndex contains all 12 canonical pages', () {
      expect(kLabelToPageIndex.length, 12);
      const expectedLabels = [
        'Home', 'Chat', 'Board', 'Alerts', 'Vault', 'Skills',
        'Scout', 'Studio', 'Calendar', 'LoRA', 'Telemetry', 'More',
      ];
      for (final label in expectedLabels) {
        expect(kLabelToPageIndex.containsKey(label), isTrue,
            reason: 'missing label: $label');
      }
    });

    test('kLabelToPageIndex indices are 0..11', () {
      final indices = kLabelToPageIndex.values.toList()..sort();
      expect(indices, List.generate(12, (i) => i));
    });

    test('desktop slot == page index for pages 0..10', () {
      // On desktop, slot i maps directly to page i (no More entry in rail).
      for (var i = 0; i <= 10; i++) {
        // goTo(i) sets _pageIndex=i; desktop slot = _pageIndex.clamp(0,10)
        expect(i.clamp(0, kShellDestinations.length - 1), i,
            reason: 'desktop slot for page $i');
      }
    });

    test('desktop slot clamps page 11 (More) to slot 10 (Telemetry)', () {
      // When on page 11 on desktop (shouldn't normally happen but is safe),
      // the nav clamps to the last desktop slot.
      expect(11.clamp(0, kShellDestinations.length - 1), 10);
    });
  });
}
