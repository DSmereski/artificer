import 'package:flutter/material.dart';

import '../../theme/hive_tokens.dart';
import '../calendar/calendar_screen.dart';
import '../lora/lora_screen.dart';
import '../scout/scout_screen.dart';
import '../skills/skills_screen.dart';
import '../studio/studio_screen.dart';
import '../telemetry/telemetry_screen.dart';
import '../vault/vault_screen.dart';

/// Hub for secondary surfaces — keeps the bottom nav to the 4 flagships
/// (Home/Chat/Board/Escalations) while everything else lives one tap in.
class MoreScreen extends StatelessWidget {
  const MoreScreen({super.key});

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context).extension<HiveTokens>()!;
    final tiles = <_Tile>[
      _Tile('Vault', Icons.menu_book_outlined,
          () => const VaultScreen(), true),
      _Tile('Skills', Icons.auto_awesome_outlined,
          () => const SkillsScreen(), true),
      _Tile('Scout', Icons.radar_outlined, () => const ScoutScreen(), true),
      _Tile('Studio', Icons.image_outlined, () => const StudioScreen(), true),
      _Tile('Calendar', Icons.event_outlined,
          () => const CalendarScreen(), true),
      _Tile('LoRAs', Icons.model_training_outlined,
          () => const LoraScreen(), true),
      _Tile('Telemetry', Icons.insights_outlined,
          () => const TelemetryScreen(), true),
    ];
    return GridView.count(
      crossAxisCount: 2,
      padding: const EdgeInsets.all(14),
      mainAxisSpacing: 12,
      crossAxisSpacing: 12,
      childAspectRatio: 1.5,
      children: [
        for (final tile in tiles)
          InkWell(
            borderRadius: BorderRadius.circular(14),
            onTap: tile.enabled
                ? () => Navigator.of(context).push(
                    MaterialPageRoute<void>(builder: (_) => tile.build!()))
                : null,
            child: Opacity(
              opacity: tile.enabled ? 1 : 0.4,
              child: Container(
                decoration: BoxDecoration(
                  color: t.slate1.withValues(alpha: 0.5),
                  borderRadius: BorderRadius.circular(14),
                ),
                child: Column(
                  mainAxisAlignment: MainAxisAlignment.center,
                  children: [
                    Icon(tile.icon, color: t.amber1, size: 28),
                    const SizedBox(height: 8),
                    Text(tile.label,
                        style: TextStyle(
                            color: t.ink,
                            fontSize: 14,
                            fontWeight: FontWeight.w600)),
                    if (!tile.enabled)
                      Text('soon',
                          style: TextStyle(color: t.slate4, fontSize: 10)),
                  ],
                ),
              ),
            ),
          ),
      ],
    );
  }
}

class _Tile {
  const _Tile(this.label, this.icon, this.build, this.enabled);
  final String label;
  final IconData icon;
  final Widget Function()? build;
  final bool enabled;
}
