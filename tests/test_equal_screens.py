"""Every screen is the same: no TV/PC mode, everything mirrors both ways, every screen speaks.

(This replaces the tests of the TV | PC switch, which no longer exists.)
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HTML = REPO / "skills" / "dnd" / "display" / "templates" / "index.html"
NODE = shutil.which("node")


def _page() -> str:
    return HTML.read_text(encoding="utf-8")


def _node(script: str) -> dict:
    if NODE is None:
        raise unittest.SkipTest("node not available")
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(script)
        path = f.name
    try:
        r = subprocess.run([NODE, path], capture_output=True, text=True, encoding="utf-8", timeout=60)
        if r.returncode != 0:
            raise AssertionError(r.stderr[:1500])
        return json.loads(r.stdout.strip().splitlines()[-1])
    finally:
        Path(path).unlink(missing_ok=True)


class NoModeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = _page()

    def test_the_switch_and_its_rules_are_gone(self):
        for old in ('id="view-switch"', "pc-claim", "dnd-view", 'id="tv-link"', "_tvPeerConnected",
                    "tv-typing", "'tv-hello'"):
            self.assertNotIn(old, self.html, old)

    def test_no_screen_is_ever_put_into_a_tv_mode(self):
        self.assertNotIn(".classList.add('tv-view')", self.html)
        self.assertIn("There is no TV/PC mode any more", self.html)

    def test_every_control_is_on_every_screen(self):
        # nothing hides the cog or Save & exit by screen kind any more
        self.assertNotIn("html.tv-view #exit-btn", self.html)
        self.assertNotRegex(self.html, r"html\.tv-view #settings-cog\s*,\s*html\.tv-view #settings-overlay")

    def test_save_and_exit_sits_right_beside_the_cog(self):
        cog = re.search(r"#settings-cog \{[^}]*left: (\d+)px; bottom: (\d+)px;[^}]*width: (\d+)px", self.html, re.S)
        ex = re.search(r"#exit-btn\{ position:fixed; left:(\d+)px; bottom:(\d+)px", self.html)
        self.assertTrue(cog and ex)
        cog_left, cog_bottom, cog_w = map(int, cog.groups())
        ex_left, ex_bottom = map(int, ex.groups())
        self.assertEqual(ex_bottom, cog_bottom)
        self.assertGreaterEqual(ex_left, cog_left + cog_w)
        self.assertLessEqual(ex_left - (cog_left + cog_w), 16)


class RelayTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = _page()

    def test_a_screen_does_not_receive_its_own_messages(self):
        self.assertIn("payload.relay.from === window._SCREEN_ID", self.html)
        self.assertIn("Object.assign({ from: window._SCREEN_ID }, msg)", self.html)
        self.assertIn("window._SCREEN_ID = Math.random()", self.html)

    def test_what_was_just_applied_is_not_echoed_back(self):
        guard = "window._relayAppliedAt && Date.now() - window._relayAppliedAt < 400"
        self.assertGreaterEqual(self.html.count(guard), 4)       # images, sheet tab, pop-ups (x2), sheets
        self.assertIn("window._relayAppliedAt = Date.now()", self.html)

    def test_sheets_popups_and_images_apply_on_any_screen(self):
        self.assertIn("if (m && m.t === 'img') _imgLightbox(!!m.open, m.im, true);", self.html)
        self.assertIn("if(!m || m.t!=='sheet') return;", self.html)
        self.assertIn("if(!m || m.t!=='srd') return;", self.html)
        self.assertNotIn("m.t==='sheet-tab' && isTV()", self.html)

    def test_narration_starts_by_itself_on_every_screen(self):
        self.assertIn("if (_ttsAutoNarrate && _TTS_AVAILABLE && !_ttsActivePlayBtn", self.html)
        self.assertIn("if (_ttsAutoNarrate && !noAutoNarrate) {", self.html)


def _slice(start_marker: str, end_marker: str) -> str:
    src = _page()
    a = src.index(start_marker)
    a = src.rindex("<script>", 0, a) + len("<script>")
    b = src.index("</script>", a)
    return src[a:b]


@unittest.skipUnless(NODE, "node not available")
class GoMenuTest(unittest.TestCase):
    def test_every_screen_goes_to_the_main_menu_when_told_to(self):
        code = _slice("Save & exit: ask Claude to save", "")
        r = _node("""
const vm = require('vm');
const handlers = [];
const ctx = { location: { search: '', href: '' }, document: { getElementById: () => null },
  window: { _relayHandlers: handlers }, confirm: () => true };
ctx.window.window = ctx.window;
vm.createContext(ctx);
vm.runInContext(%s, ctx);
handlers.forEach(h => h({ t: 'go-menu' }));
console.log(JSON.stringify({ href: ctx.location.href }));
""" % json.dumps(code))
        self.assertEqual(r, {"href": "/menu"})


@unittest.skipUnless(NODE, "node not available")
class VoiceOnEveryScreenTest(unittest.TestCase):
    HARNESS = """
const vm = require('vm');
function makeScreen(name, blockText, state) {
  const sent = [], handlers = [], log = [];
  const btn = { classes: new Set(state || []), click() { log.push('click'); if (ctx.window._ttsRelayPressed) ctx.window._ttsRelayPressed(blk, btn.classes.has('tts-playing') || btn.classes.has('tts-loading')); },
    classList: { contains: c => btn.classes.has(c) } };
  const blk = { text: blockText };
  const bar = { parentElement: blk, querySelector: () => btn };
  const ctx = {
    location: { search: '' },
    document: { querySelectorAll: () => [bar], createElement: () => ({ appendChild() {}, set textContent(v) {} }) },
    window: { _relayHandlers: handlers, _relaySend: m => sent.push(m) },
    _ttsExtractText: b => b.text, _getTtsCtx: () => ({ resume() { log.push('resume'); } }),
    _ttsPlayBlock: (b, d, o) => log.push('play-text:' + o.text), _ttsStopActive: () => log.push('stop'),
  };
  ctx.window.window = ctx.window;
  vm.createContext(ctx);
  vm.runInContext(SCRIPT, ctx);
  return { sent, handlers, log, btn, blk, ctx, deliver: m => handlers.forEach(h => h(m)) };
}
"""

    def _run(self, body: str) -> dict:
        code = _slice("Voice on every screen:", "")
        return _node("const SCRIPT = " + json.dumps(code) + ";\n" + self.HARNESS + body)

    def test_pressing_play_asks_every_other_screen_to_speak_it_too(self):
        r = self._run("""
const a = makeScreen('A', 'The door creaks open.');
a.ctx.window._ttsRelayPressed(a.blk, false);
console.log(JSON.stringify({ sent: a.sent }));
""")
        self.assertEqual(r["sent"], [{"t": "tts-play", "text": "The door creaks open."}])

    def test_pressing_stop_asks_them_to_stop(self):
        r = self._run("""
const a = makeScreen('A', 'x');
a.ctx.window._ttsRelayPressed(a.blk, true);
console.log(JSON.stringify({ sent: a.sent }));
""")
        self.assertEqual(r["sent"], [{"t": "tts-stop"}])

    def test_another_screen_plays_the_same_block_without_echoing(self):
        r = self._run("""
const b = makeScreen('B', 'The door creaks open.');
b.deliver({ t: 'tts-play', text: 'The door creaks open.' });
console.log(JSON.stringify({ log: b.log, sent: b.sent }));
""")
        self.assertIn("click", r["log"])
        self.assertEqual(r["sent"], [])                     # the relayed click did not bounce back

    def test_a_screen_already_speaking_that_block_is_not_stopped_by_it(self):
        r = self._run("""
const b = makeScreen('B', 'The door creaks open.', ['tts-playing']);
b.deliver({ t: 'tts-play', text: 'The door creaks open.' });
console.log(JSON.stringify({ log: b.log }));
""")
        self.assertNotIn("click", r["log"])

    def test_a_block_this_screen_does_not_have_is_still_spoken(self):
        r = self._run("""
const b = makeScreen('B', 'something else');
b.deliver({ t: 'tts-play', text: 'A line from another screen.' });
console.log(JSON.stringify({ log: b.log }));
""")
        self.assertIn("play-text:A line from another screen.", r["log"])

    def test_stop_stops_this_screen_without_echoing(self):
        r = self._run("""
const b = makeScreen('B', 'x');
b.deliver({ t: 'tts-stop' });
console.log(JSON.stringify({ log: b.log, sent: b.sent }));
""")
        self.assertIn("stop", r["log"])
        self.assertEqual(r["sent"], [])


if __name__ == "__main__":
    unittest.main()
