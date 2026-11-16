"""Party map: the DM pins where the party is on a map picture (roleplay screen, right-hand column)."""
import os
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
D = ROOT / "skills" / "dnd" / "display"
HTML = (D / "templates" / "index.html").read_text(encoding="utf-8")
APP = (D / "dnd-display-app.py").read_text(encoding="utf-8")


def _clean_place():
    a = APP.index("def _clean_place(")
    b = APP.index("def _clean_map_image(")
    ns = {"os": os}
    exec(APP[a:b], ns)
    return ns["_clean_place"]


class PartyMapTest(unittest.TestCase):
    def test_server_cleans_the_place(self):
        c = _clean_place()
        self.assertIsNone(c({}))
        self.assertIsNone(c({"image": "map.png"}))                       # no pin position
        self.assertIsNone(c({"image": "notes.txt", "x": .5, "y": .5}))   # only picture files
        out = c({"image": "../../etc/greenest.WEBP", "x": "0.42", "y": 1.7, "label": "G" * 90})
        self.assertEqual((out["image"], out["x"], out["y"]), ("greenest.WEBP", 0.42, 1.0))   # path stripped, clamped to the picture
        self.assertEqual(len(out["label"]), 60)
        self.assertNotIn("view", out)
        z = c({"image": "m.png", "x": .5, "y": .5, "view": {"x": .8, "y": .1, "w": .5, "h": .2}})
        self.assertEqual(z["view"], {"x": .8, "y": .1, "w": 0.2, "h": .2})   # the view stays inside the picture
        self.assertNotIn("view", c({"image": "m.png", "x": .5, "y": .5, "view": {"x": "a"}}))

    def test_it_is_saved_with_the_campaign_and_wired_everywhere(self):
        self.assertIn('"turn_order", "place")', APP)                    # persisted and restored per campaign
        self.assertIn('_current_stats["place"] = _clean_place(data["place"])', APP)
        push = (D / "push_stats.py").read_text(encoding="utf-8")
        for needle in ('"--place"', "_resolve_place_image(place)", 'payload["place"] = place'):
            self.assertIn(needle, push)
        for needle in ("function _renderPlace(", "if ('place' in stats) _renderPlace(stats.place)", "pm-pin", ".pm-card"):
            self.assertIn(needle, HTML)
        self.assertIn("--place", (ROOT / "skills" / "dnd" / "SKILL-commands.md").read_text(encoding="utf-8"))
        # combat hides the whole right-hand column, pin card included
        self.assertIn("body.combat #scene-gallery, body.combat #quest-strip", HTML)


if __name__ == "__main__":
    unittest.main()
