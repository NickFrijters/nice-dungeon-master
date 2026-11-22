"""Left pane = the campaign (characters, factions, quests). Right pane = now (date, time, weather, location, map,
NPCs here, pictures). An NPC's description sits on the line under the name."""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HTML = (ROOT / "skills" / "dnd" / "display" / "templates" / "index.html").read_text(encoding="utf-8")


def block(start_marker, end_marker):
    a = HTML.index(start_marker)
    return HTML[a:HTML.index(end_marker, a)]


class NowPaneTest(unittest.TestCase):
    def test_left_pane_holds_the_campaign_and_not_the_npcs(self):
        left = block('<div id="sidebar">', "<!-- Text -->")
        for needle in ('id="sb-factions"', 'id="sb-quests"', 'id="sb-turn-section"'):
            self.assertIn(needle, left)
        self.assertNotIn('id="sb-npcs"', left)

    def test_right_pane_holds_now(self):
        right = block('<div id="scene-gallery" hidden>', "</div>\n</div>")
        order = [right.index(x) for x in ('id="now-head"', 'id="sb-npcs"', 'class="sg-title"')]
        self.assertEqual(order, sorted(order))                                    # time and place, then who is here, then pictures
        self.assertIn("g.insertBefore(card, document.getElementById('sb-npcs')", HTML)   # the map sits between them
        self.assertIn("head.querySelector('.nh-date').textContent", HTML)
        self.assertIn("function _nowPaneRefresh()", HTML)
        self.assertIn("body.now-pane #world-clock { display: none; }", HTML)          # the corner clock moved into the pane

    def test_npc_description_is_on_its_own_line(self):
        self.assertIn("r = document.createElement('div'); r.className = 'sb-npc-role'", HTML)
        self.assertRegex(HTML, r"#scene-gallery \.sb-npc-role \{ display: block;")

    def test_interface_size_covers_the_right_pane(self):
        self.assertIn("zoom: var(--ui-scale, 1); }", block("#scene-gallery { overflow-y: auto", "\n"))



class ClearRaceTest(unittest.TestCase):
    def test_a_clear_wipes_at_once_so_the_next_block_survives(self):
        a = HTML.index("function clearDisplay(then) {")
        body = HTML[a:HTML.index("\n}\n", a)]
        wipe = body.index("textContent.innerHTML = '';")
        self.assertNotIn("setTimeout(() => {\n", body[:wipe])          # nothing delays the wipe any more
        self.assertIn("setTimeout(() => { textContent.style.opacity = '1'; }, 30);", body)

    def test_location_shown_once(self):
        self.assertIn("card.querySelector('.pm-label').textContent = '';", HTML)
        self.assertIn("card.querySelector('.pm-cap').style.display = hasView ? '' : 'none';", HTML)


if __name__ == "__main__":
    unittest.main()
