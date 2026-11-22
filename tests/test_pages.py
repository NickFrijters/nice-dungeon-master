"""Long narration is shown in screen-sized pages: typing waits for a Continue when the unread text fills the area."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
INDEX = REPO / "skills" / "dnd" / "display" / "templates" / "index.html"
NODE = shutil.which("node")


def _src() -> str:
    return INDEX.read_text(encoding="utf-8")


def _pages_code() -> str:
    src = _src()
    a = src.index("// ── Pages ──")
    b = src.index("function typeNextChar() {")
    return src[a:b]


PRELUDE = """
const listeners = {};
const btn = { cls: new Set(), classList: { add(c) { btn.cls.add(c); }, remove(c) { btn.cls.delete(c); }, contains(c) { return btn.cls.has(c); } },
              addEventListener(t, f) { listeners['btn:' + t] = f; } };
// A scroll area 800 high: the block starts at y=100; "bottom" of the last paragraph is set per test.
const lastP = { y: 100, getBoundingClientRect() { return { bottom: lastP.y }; } };
const currentEl = null;
let curEl = { getBoundingClientRect() { return { top: 100 }; }, querySelector() { return lastP; } };
const textScroll = { clientHeight: 800, get scrollHeight() { return lastP.y + 180; }, scrollTop: 0, getBoundingClientRect() { return { top: 0 }; } };
const document = { hidden: false, body: { classList: { contains() { return false; } } },
  getElementById(id) { return id === 'page-continue' ? btn : null; },
  addEventListener(t, f) { listeners[t] = f; } };
const getComputedStyle = () => ({ paddingTop: '72px' });
let _turn = null, charDelay = 36, charQueue = [], sent = [], resumed = 0, instantRuns = 0;
window = { _relaySend: m => sent.push(m) };
function setTimeout(f) { resumed++; }
function typeNextChar() {}
function instantFlush() { charQueue.length = 0; }
function scrollToBottom() {}
function _turnTypingDone() {}
"""


def _run(body: str) -> dict:
    if NODE is None:
        raise unittest.SkipTest("node not available")
    code = _pages_code().replace("currentEl", "curEl")
    script = PRELUDE.replace("const currentEl = null;\n", "") + "\n" + code + "\n" + body
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


@unittest.skipUnless(NODE, "node not available")
class HoldTest(unittest.TestCase):
    def test_short_text_does_not_hold_and_a_full_page_does(self):
        r = _run("""
const out = {};
lastP.y = 300; out.short = _pageShouldHold();
lastP.y = 560; out.fits = _pageShouldHold();          // more than half the screen, but all still in view: no pause
lastP.y = 700; out.full = _pageShouldHold();          // going on would push the start out of sight at the top
console.log(JSON.stringify(out));
""")
        self.assertEqual(r, {"short": False, "fits": False, "full": True})

    def test_a_voice_reading_the_text_never_holds(self):
        r = _run("""
const out = {};
lastP.y = 700;
_turn = { voice: true, audioEnded: false }; out.voice = _pageShouldHold();
_turn = { voice: true, audioEnded: true }; out.voiceDone = _pageShouldHold();
_turn = { voice: false, audioEnded: true }; out.none = _pageShouldHold();
console.log(JSON.stringify(out));
""")
        self.assertEqual(r, {"voice": False, "voiceDone": True, "none": True})

    def test_hold_shows_the_button_and_continue_hides_it_and_tells_the_other_screens(self):
        r = _run("""
const out = {};
lastP.y = 700; out.should = _pageShouldHold(); out.held = _pageHold(); out.shown = btn.cls.has('show');
_pageContinue(false);
out.afterHeld = _pageHeld; out.afterShown = btn.cls.has('show'); out.sent = sent; out.resumed = resumed;
console.log(JSON.stringify(out));
""")
        self.assertEqual(r, {"should": True, "held": True, "shown": True, "afterHeld": False, "afterShown": False,
                             "sent": [{"t": "page-continue"}], "resumed": 1})

    def test_after_continue_the_next_hold_needs_another_page_of_text(self):
        r = _run("""
const out = {};
lastP.y = 700; _pageShouldHold(); _pageHold(); _pageContinue(false);
lastP.y = 700 + 200; out.small = _pageShouldHold();
lastP.y = 1300; out.page = _pageShouldHold();         // a whole new screen of text after the Continue
console.log(JSON.stringify(out));
""")
        self.assertEqual(r, {"small": False, "page": True})

    def test_a_press_from_another_screen_resumes_without_echoing_it_back(self):
        r = _run("""
const out = {};
lastP.y = 700; _pageShouldHold(); _pageHold();
_pageContinue(true);
out.held = _pageHeld; out.sent = sent.length; out.resumed = resumed;
console.log(JSON.stringify(out));
""")
        self.assertEqual(r, {"held": False, "sent": 0, "resumed": 1})

    def test_a_screen_that_is_behind_keeps_a_credit_and_does_not_stop_at_the_same_place(self):
        r = _run("""
const out = {};
lastP.y = 120; _pageShouldHold();            // block registered, nothing to hold yet
_pageContinue(true); _pageContinue(true); _pageContinue(true);
out.credit = _pageCredit;                    // capped at 2
lastP.y = 700;
out.shouldHold = _pageShouldHold(); out.firstHold = _pageHold();   // spent on credit: no stop
lastP.y = 700 + 800 * 0.55 + 10;
out.second = _pageShouldHold(); out.secondHold = _pageHold();
lastP.y = 700 + 2 * (800 * 0.55 + 10) + 10;
out.third = _pageShouldHold(); out.thirdHold = _pageHold();        // credit used up: stops
console.log(JSON.stringify(out));
""")
        self.assertEqual(r["credit"], 2)
        self.assertFalse(r["firstHold"])
        self.assertFalse(r["secondHold"])
        self.assertTrue(r["thirdHold"])

    def test_enter_and_space_continue_but_not_while_typing_in_an_input(self):
        r = _run("""
const out = {};
const key = (k, tag) => { let pd = false; listeners.keydown({ key: k, target: { tagName: tag }, preventDefault() { pd = true; } }); return pd; };
lastP.y = 700; _pageShouldHold(); _pageHold();
out.inInput = key('Enter', 'TEXTAREA'); out.stillHeld = _pageHeld;
out.other = key('a', 'BODY');
out.space = key(' ', 'BODY'); out.heldAfter = _pageHeld;
console.log(JSON.stringify(out));
""")
        self.assertEqual(r, {"inInput": False, "stillHeld": True, "other": False, "space": True, "heldAfter": False})

    def test_instant_typing_is_cut_into_pages_too(self):
        r = _run("""
const out = {};
charDelay = 0; charQueue = new Array(500).fill('x');
let slices = 0;
instantFlush = function (n) { charQueue.splice(0, n); slices++; lastP.y += 30; };   // each slice adds 30px
_pageInstantRun();
out.heldAfterFirstRun = _pageHeld; out.left = charQueue.length; out.slices = slices;
_pageContinue(false);
out.heldAfterContinue = _pageHeld; out.leftAfter = charQueue.length;
console.log(JSON.stringify(out));
""")
        self.assertTrue(r["heldAfterFirstRun"])
        self.assertGreater(r["left"], 0)
        self.assertLess(r["slices"], 30)
        self.assertTrue(r["heldAfterContinue"] or r["leftAfter"] == 0)
        self.assertLess(r["leftAfter"], r["left"])


class StringChecksTest(unittest.TestCase):
    def test_the_button_the_relay_and_the_hooks_are_in_the_page(self):
        src = _src()
        self.assertIn('id="page-continue"', src)
        self.assertIn("#page-continue.show", src)
        self.assertIn("_relaySend({ t: 'page-continue' })", src.replace("window._relaySend({", "_relaySend({"))
        self.assertIn("m.t==='page-continue'", src)
        self.assertIn("_pageVoiceReading", src)
        self.assertIn("_turn.voice && !_turn.audioEnded", src)

    def test_typing_and_instant_typing_both_ask_the_page_logic(self):
        src = _src()
        a = src.index("function typeNextChar() {")
        self.assertIn("_pageShouldHold()", src[a:a + 600])
        s = src.index("function startTyping() {")
        self.assertIn("_pageInstantRun()", src[s:s + 400])

    def test_a_cleared_screen_lets_go_of_the_hold(self):
        src = _src()
        c = src.index("function clearDisplay(then) {")
        self.assertIn("_pageRelease()", src[c:c + 700])


if __name__ == "__main__":
    unittest.main()
