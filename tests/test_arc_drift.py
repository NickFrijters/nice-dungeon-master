"""
test_arc_drift.py — drift verdict log, stall detection and escalation level.

Run: bash scripts/run-tests.sh
"""
import pathlib
import sys
import tempfile
import textwrap
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
SKILL = REPO / "skills" / "dnd"
sys.path.insert(0, str(SKILL / "scripts"))

import arc_drift as ad  # noqa: E402

STRUCTURED = textwrap.dedent("""\
    # Campaign: t
    **Created:** x  **Last session:** x  **Session count:** 3  **Ruleset:** 2014

    ## Campaign Arc
    ```yaml
    type: structured
    current_chapter: "1.1"
    current_chapter_detail:
      id: "1.1"
      telegraph_scene: "A family runs past with kobolds behind."
    next_chapter: "1.2"
    outstanding_beats: ["a", "b", "c"]
    steering_notes: >
      Open with a bang.
      Keep fights small.
    ```

    ## Arc History
""")

DYNAMIC = textwrap.dedent("""\
    **Session count:** 2

    ## Campaign Arc
    ```yaml
    type: dynamic
    current_act: 1
    current_beat: "1a"
    outstanding_beats:
      - "1a"
      - "1b"
    steering_notes: >
      Push the courier.
    ```
""")

WORLD = "# World\n\n## Setting Anchors\n- Tone: grim but hopeful\n- Place: the Keep\n\n## Factions\nx\n"


def mk(state=STRUCTURED, world=WORLD):
    d = pathlib.Path(tempfile.mkdtemp())
    (d / "state.md").write_text(state, encoding="utf-8")
    if world is not None:
        (d / "world.md").write_text(world, encoding="utf-8")
    return d


def log(d, *rows):
    """rows: (session, position, verdict, outstanding)"""
    lines = [ad.LOG_HEADER] + [
        f"S{s} | 2026-01-0{s} | {p} | {v} | outstanding={o} | n | m\n" for s, p, v, o in rows]
    (d / ad.LOG_NAME).write_text("".join(lines), encoding="utf-8")


class ArcParse(unittest.TestCase):
    def test_structured(self):
        a = ad.read_arc(mk())
        self.assertEqual(a["type"], "structured")
        self.assertEqual(a["position"], "1.1")
        self.assertEqual(a["outstanding"], ["a", "b", "c"])
        self.assertIn("kobolds", a["telegraph_scene"])
        self.assertEqual(a["steering_notes"], "Open with a bang. Keep fights small.")

    def test_dynamic_dash_list(self):
        a = ad.read_arc(mk(DYNAMIC))
        self.assertEqual(a["position"], "1a")
        self.assertEqual(a["outstanding"], ["1a", "1b"])

    def test_no_arc(self):
        self.assertEqual(ad.read_arc(mk("# x\n"))["type"], "none")


class Record(unittest.TestCase):
    def test_record_and_parse(self):
        d = mk()
        line = ad.record(d, "drifting", "party stayed in the inn | all day", "rumour of fire")
        self.assertTrue(line.startswith("S3 |"))
        rows = ad.parse_log(d)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["verdict"], "drifting")
        self.assertEqual(rows[0]["outstanding"], 3)
        self.assertEqual(rows[0]["move"], "rumour of fire")
        self.assertNotIn("|", rows[0]["note"].replace("/", ""))

    def test_bad_verdict(self):
        with self.assertRaises(ValueError):
            ad.record(mk(), "fine")


class Escalation(unittest.TestCase):
    def lvl(self, *rows):
        d = mk()
        log(d, *rows)
        return ad.status(d)

    def test_empty_is_zero(self):
        self.assertEqual(ad.status(mk())["level"], 0)

    def test_progress_resets(self):
        s = self.lvl((1, "1.1", "drifting", 3), (2, "1.1", "on-course", 2))
        self.assertEqual((s["level"], s["drift_streak"], s["saves_without_progress"]), (0, 0, 0))

    def test_detour_is_free_at_first(self):
        self.assertEqual(self.lvl((1, "1.1", "detour", 3))["level"], 0)

    def test_streak_escalates(self):
        self.assertEqual(self.lvl((1, "1.1", "drifting", 3))["level"], 1)
        self.assertEqual(self.lvl((1, "1.1", "drifting", 3), (2, "1.1", "drifting", 3))["level"], 2)
        r = [(i, "1.1", "drifting", 3) for i in range(1, 4)]
        self.assertEqual(self.lvl(*r)["level"], 3)

    def test_off_arc_goes_straight_to_decide(self):
        self.assertEqual(self.lvl((1, "1.1", "off-arc", 3))["level"], 3)

    def test_stall_escalates_even_with_detours(self):
        r = [(i, "1.1", "detour", 3) for i in range(1, 5)]
        s = self.lvl(*r)
        self.assertEqual(s["saves_without_progress"], 3)
        self.assertEqual(s["level"], 2)
        r.append((5, "1.1", "detour", 3))
        self.assertEqual(self.lvl(*r)["level"], 3)

    def test_chapter_change_counts_as_progress(self):
        s = self.lvl((1, "1.1", "detour", 3), (2, "1.1", "detour", 3), (3, "1.2", "detour", 3))
        self.assertEqual(s["saves_without_progress"], 0)


class Anchors(unittest.TestCase):
    def test_present(self):
        d = mk()
        self.assertIn("grim but hopeful", ad.anchors(d))
        self.assertNotIn("Factions", ad.anchors(d))
        self.assertTrue(ad.status(d)["anchors_present"])

    def test_missing_or_empty(self):
        self.assertEqual(ad.anchors(mk(world=None)), "")
        self.assertEqual(ad.anchors(mk(world="## Setting Anchors\n*(none yet)*\n\n## X\n")), "")
        self.assertFalse(ad.status(mk(world="# W\n"))["anchors_present"])


class SkillText(unittest.TestCase):
    """The DM only drifts less if the procedures actually say so."""
    def test_procedures_mention_drift_check(self):
        cmds = (SKILL / "SKILL-commands.md").read_text(encoding="utf-8")
        main = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("arc_drift.py record", cmds)
        self.assertIn("arc_drift.py status", cmds)
        self.assertIn("Setting Anchors", cmds)
        self.assertIn("Setting Anchors", main)
        self.assertIn("arc_drift.py", (SKILL / "SKILL-scripts.md").read_text(encoding="utf-8"))
        self.assertIn("Setting Anchors", (SKILL / "templates" / "world.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
