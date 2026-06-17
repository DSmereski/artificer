import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:ai_team_app_v2/screens/shell/command_palette.dart';

void main() {
  Widget host({required void Function(String) goTo}) => MaterialApp(
        home: CommandPaletteScope(
          goTo: goTo,
          child: const Scaffold(body: Center(child: Text('content'))),
        ),
      );

  testWidgets('Ctrl+K opens the palette, typing filters, Enter runs',
      (tester) async {
    String? navigated;
    await tester.pumpWidget(host(goTo: (l) => navigated = l));

    await tester.sendKeyDownEvent(LogicalKeyboardKey.controlLeft);
    await tester.sendKeyEvent(LogicalKeyboardKey.keyK);
    await tester.sendKeyUpEvent(LogicalKeyboardKey.controlLeft);
    await tester.pumpAndSettle();
    expect(find.byType(TextField), findsOneWidget);

    await tester.enterText(find.byType(TextField), 'boar');
    await tester.pumpAndSettle();
    expect(find.text('Go to Board'), findsOneWidget);

    await tester.testTextInput.receiveAction(TextInputAction.done);
    await tester.pumpAndSettle();
    expect(navigated, 'Board');
  });

  testWidgets('Escape closes the palette', (tester) async {
    await tester.pumpWidget(host(goTo: (_) {}));
    await tester.sendKeyDownEvent(LogicalKeyboardKey.controlLeft);
    await tester.sendKeyEvent(LogicalKeyboardKey.keyK);
    await tester.sendKeyUpEvent(LogicalKeyboardKey.controlLeft);
    await tester.pumpAndSettle();
    expect(find.byType(TextField), findsOneWidget);
    await tester.sendKeyEvent(LogicalKeyboardKey.escape);
    await tester.pumpAndSettle();
    expect(find.byType(TextField), findsNothing);
  });
}
