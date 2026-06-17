// Mirrors gateway/routes/digest.py::DigestCounts — "what's new since
// last open". Drives the v2 Home landing surface.

class Digest {
  Digest({
    required this.since,
    required this.newImages,
    required this.newEscalations,
    required this.newPinnedTurns,
    required this.completedCalendarFires,
  });

  final int since; // unix epoch seconds
  final int newImages;
  final int newEscalations;
  final int newPinnedTurns;
  final int completedCalendarFires;

  int get total =>
      newImages + newEscalations + newPinnedTurns + completedCalendarFires;
  bool get hasNews => total > 0;

  factory Digest.fromJson(Map<String, dynamic> j) => Digest(
        since: (j['since'] ?? 0) as int,
        newImages: (j['new_images'] ?? 0) as int,
        newEscalations: (j['new_escalations'] ?? 0) as int,
        newPinnedTurns: (j['new_pinned_turns'] ?? 0) as int,
        completedCalendarFires: (j['completed_calendar_fires'] ?? 0) as int,
      );
}
