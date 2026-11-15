"""
test_consequences.py — the consequence ledger (scripts/consequences.py).

Run via: bash scripts/run-tests.sh
"""
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
SKILL = REPO / "skills" / "dnd"
sys.path.insert(0, str(SKILL / "scripts"))


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old = os.environ.get("DND_CAMPAIGN_ROOT")
        os.environ["DND_CAMPAIGN_ROOT"] = self.tmp.name
        camp = pathlib.Path(self.tmp.name) / "campaigns" / "t"
        camp.mkdir(parents=True)
        (camp / "state.md").write_text("# Campaign: t\n**Session count:** 3\n", encoding="utf-8")
        import consequences
        self.c = consequences

    def tearDown(self):
        if self.old is None:
            os.environ.pop("DND_CAMPAIGN_ROOT", None)
        else:
            os.environ["DND_CAMPAIGN_ROOT"] = self.old
        self.tmp.cleanup()

    def door(self):
        return self.c.add("t", ["Gilded Stag Inn", "Hobb Brewer"], "Smashed the front door; Hobb wants payment.", by="Balasar")

    def test_add_uses_state_session_and_persists(self):
        e = self.door()
        self.assertEqual((e["id"], e["session"], e["status"]), ("C1", 3, "open"))
        again = self.c.load("t")
        self.assertEqual(again[0]["subjects"], ["Gilded Stag Inn", "Hobb Brewer"])
        self.assertEqual(again[0]["by"], "Balasar")

    def test_ids_increment_and_pipes_are_sanitised(self):
        self.door()
        e = self.c.add("t", ["Watch"], "a | b", session=9)
        self.assertEqual(e["id"], "C2")
        self.assertEqual(self.c.load("t")[1]["text"], "a / b")

    def test_inn_door_found_fifty_sessions_later(self):
        self.door()
        for n in range(4, 54):          # fifty sessions of unrelated entries
            self.c.add("t", [f"Place {n}"], f"filler {n}", session=n)
        by_place = self.c.for_subjects("t", ["Gilded Stag Inn"])
        self.assertEqual([e["id"] for e in by_place], ["C1"])
        by_npc = self.c.for_subjects("t", ["Hobb Brewer"])
        self.assertEqual([e["id"] for e in by_npc], ["C1"])

    def test_partial_names_match_both_ways(self):
        self.door()
        self.assertTrue(self.c.for_subjects("t", ["Gilded Stag"]))
        self.assertTrue(self.c.for_subjects("t", ["the Gilded Stag Inn at the crossroads"]))
        self.assertFalse(self.c.for_subjects("t", ["Silver Stag"]))
        self.assertFalse(self.c.for_subjects("t", ["ab"]))     # too short to match

    def test_text_mention_matches_when_npc_not_a_subject(self):
        self.c.add("t", ["Market Square"], "Mira the baker saw the theft", session=2)
        self.assertTrue(self.c.for_subjects("t", ["Mira"]))
        self.assertFalse(self.c.for_subjects("t", ["Mir"]))    # whole word only

    def test_open_sorted_before_resolved_then_newest(self):
        a = self.c.add("t", ["Inn"], "old open", session=1)
        b = self.c.add("t", ["Inn"], "resolved one", session=8)
        self.c.add("t", ["Inn"], "new open", session=5)
        self.c.resolve("t", b["id"], "paid up", session=9)
        order = [e["text"].split(" ")[0] for e in self.c.for_subjects("t", ["Inn"])]
        self.assertEqual(order, ["new", "old", "resolved"])
        self.assertIn("resolved S9: paid up", self.c.load("t")[1]["text"])

    def test_resolve_unknown_id(self):
        with self.assertRaises(KeyError):
            self.c.resolve("t", "C99")

    def test_add_requires_subject_and_text(self):
        with self.assertRaises(ValueError):
            self.c.add("t", [], "x")
        with self.assertRaises(ValueError):
            self.c.add("t", ["Inn"], "  ")

    def test_search_is_and(self):
        self.door()
        self.assertEqual(len(self.c.search("t", ["door", "hobb"])), 1)
        self.assertEqual(self.c.search("t", ["door", "zzz"]), [])

    def test_hand_edited_file_still_parses(self):
        p = pathlib.Path(self.tmp.name) / "campaigns" / "t" / "consequences.md"
        p.write_text("# x\nnotes\n- C7 | S2 | open | Keep | by Joris | Burned the banner\n", encoding="utf-8")
        self.assertEqual(self.c.load("t")[0]["id"], "C7")
        self.assertEqual(self.c.add("t", ["Keep"], "n")["id"], "C8")

    def test_cli_roundtrip(self):
        env = dict(os.environ, DND_CAMPAIGN_ROOT=self.tmp.name, PYTHONUTF8="1")
        run = lambda *a: subprocess.run([sys.executable, str(SKILL / "scripts" / "consequences.py"), "-c", "t", *a],
                                        capture_output=True, text=True, encoding="utf-8", env=env)
        self.assertEqual(run("add", "--subject", "Gilded Stag Inn", "--text", "Door smashed", "--by", "Balasar").returncode, 0)
        out = run("scene", "--place", "Gilded Stag Inn", "--present", "Hobb").stdout
        self.assertIn("Door smashed", out)
        self.assertIn("(no consequences", run("scene", "--place", "Nowhere Town").stdout)
        self.assertEqual(run("resolve", "--id", "C1").returncode, 0)
        self.assertEqual(run("resolve", "--id", "C5").returncode, 1)
        self.assertIn("1 entries (0 open)", run("stats").stdout)

    def test_skill_text_wires_ledger_into_load_save_and_scene_entry(self):
        sk = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        cm = (SKILL / "SKILL-commands.md").read_text(encoding="utf-8")
        self.assertIn("consequences.py scene", sk)
        self.assertIn("consequences.py add", sk)
        self.assertIn("consequences.py", cm.split("## `/dm:dnd save`")[0])       # load
        self.assertIn("Consequence ledger sweep", cm.split("## `/dm:dnd save`")[1])  # save
        self.assertIn("consequences.py", (SKILL / "scripts" / "autosave_checkpoint.py").read_text(encoding="utf-8"))

    def test_search_script_includes_ledger(self):
        import campaign_search
        self.assertIn("conseq", campaign_search.DEFAULT_FILES)


if __name__ == "__main__":
    unittest.main()
