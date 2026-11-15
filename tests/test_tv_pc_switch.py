"""The TV | PC switch: one PC at a time, TV by default, typing mirrored on the TV.

The page script is run in node with a small fake DOM, twice, with a shared
relay between the two "screens", so the rules are exercised and not just read.
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


def _switch_script() -> str:
    src = HTML.read_text(encoding="utf-8")
    m = re.search(r'<div id="view-switch".*?</div>\s*<script>(.*?)</script>', src, re.S)
    if not m:
        raise AssertionError("the TV/PC switch script is not where this test looks for it")
    return m.group(1)


HARNESS = r"""
const vm = require('vm');
class El {
  constructor(id) { this.id = id; this.children = []; this.className = ''; this.textContent = '';
    this.dataset = {}; this.style = {}; this.attrs = {}; this.handlers = {}; this.value = ''; this._cls = new Set(); }
  get lastElementChild() { return this.children[this.children.length - 1] || null; }
  appendChild(c) { this.children = this.children.filter(x => x !== c); this.children.push(c); return c; }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return this.attrs[k]; }
  addEventListener(t, f) { (this.handlers[t] = this.handlers[t] || []).push(f); }
  querySelectorAll() { return this.buttons || []; }
  closest(sel) { return this.matches ? this : null; }
  get classList() { const s = this._cls, self = this; return {
    contains: c => s.has(c), add: c => s.add(c), remove: c => s.delete(c),
    toggle: (c, on) => { (on === undefined ? !s.has(c) : on) ? s.add(c) : s.delete(c); } }; }
}
function makeScreen(name, bus, opts) {
  const root = new El('root'); if (opts.tv) root._cls.add('tv-view');
  const store = {}; if (opts.stored) store['dnd-view'] = opts.stored;
  const els = {};
  const sw = new El('view-switch');
  const bTV = new El('b-tv'); bTV.dataset.view = 'tv'; bTV.matches = true;
  const bPC = new El('b-pc'); bPC.dataset.view = 'pc'; bPC.matches = true;
  sw.buttons = [bTV, bPC];
  els['view-switch'] = sw;
  els['text-content'] = new El('text-content');
  els['text-scroll'] = new El('text-scroll');
  els['player-input-text'] = new El('ta');
  const tab = new El('tab'); tab.dataset.char = 'Mira';
  const timers = [], intervals = [];
  const doc = {
    documentElement: root,
    getElementById: id => els[id] || null,
    querySelector: sel => sel.indexOf('char-tab') >= 0 ? tab : null,
    createElement: () => new El('new'), createTextNode: t => ({ nodeText: t }),
    addEventListener: () => {},
  };
  const win = { _relayHandlers: [], _tvPeerConnected: !!opts.peer,
    _relaySend: msg => bus.forEach(s => s.deliver(msg)) };
  const ctx = { document: doc, window: win, location: { search: '' }, sessionStorage: {
      getItem: k => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = v; } },
    setTimeout: (f, ms) => { timers.push([f, ms]); return timers.length; },
    setInterval: (f, ms) => { intervals.push(f); return intervals.length; },
    MutationObserver: function () { this.observe = () => {}; },
    _showToast: () => { ctx.toasts.push(1); }, toasts: [], Math, Date };
  win.window = win; ctx.window = win;
  vm.createContext(ctx);
  vm.runInContext(SCRIPT, ctx);
  const screen = { name, root, store, ctx, win, sw, bTV, bPC, els, timers, intervals,
    deliver: m => win._relayHandlers.forEach(h => h(m)),
    click: b => sw.handlers.click.forEach(f => f({ target: b })),
    isTV: () => root._cls.has('tv-view'),
    pressed: () => sw.buttons.map(b => b.attrs['aria-pressed']),
    runTimers: () => timers.splice(0).forEach(([f]) => f()),
    tick: () => intervals.forEach(f => f()) };
  bus.push(screen);
  return screen;
}
"""


def _run(body: str) -> dict:
    if NODE is None:
        raise unittest.SkipTest("node not available")
    script = ("const SCRIPT = " + json.dumps(_switch_script()) + ";\n" + HARNESS + "\n" + body)
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
class SwitchRulesTest(unittest.TestCase):
    def test_a_new_screen_starts_as_the_tv_with_tv_pressed(self):
        r = _run("""
const bus = [];
const a = makeScreen('A', bus, { tv: true });
console.log(JSON.stringify({ tv: a.isTV(), pressed: a.pressed() }));
""")
        self.assertTrue(r["tv"])
        self.assertEqual(r["pressed"], ["true", "false"])

    def test_choosing_pc_makes_this_screen_the_pc_and_remembers_it(self):
        r = _run("""
const bus = [];
const a = makeScreen('A', bus, { tv: true });
a.click(a.bPC);
console.log(JSON.stringify({ tv: a.isTV(), pressed: a.pressed(), stored: a.store['dnd-view'] }));
""")
        self.assertFalse(r["tv"])
        self.assertEqual(r["pressed"], ["false", "true"])
        self.assertEqual(r["stored"], "pc")

    def test_when_a_second_screen_becomes_the_pc_the_first_drops_to_tv(self):
        r = _run("""
const bus = [];
const a = makeScreen('A', bus, { tv: true });
const b = makeScreen('B', bus, { tv: true });
a.click(a.bPC);
const afterA = { a: a.isTV(), b: b.isTV() };
b.click(b.bPC);
const afterB = { a: a.isTV(), b: b.isTV(), aStored: a.store['dnd-view'], toast: a.ctx.toasts.length };
console.log(JSON.stringify({ afterA, afterB }));
""")
        self.assertEqual(r["afterA"], {"a": False, "b": True})
        self.assertEqual(r["afterB"], {"a": True, "b": False, "aStored": "tv", "toast": 1})

    def test_a_tv_screen_is_left_alone_by_someone_else_becoming_the_pc(self):
        r = _run("""
const bus = [];
const a = makeScreen('A', bus, { tv: true });
const b = makeScreen('B', bus, { tv: true });
a.click(a.bPC);
console.log(JSON.stringify({ bToasts: b.ctx.toasts.length, bTV: b.isTV() }));
""")
        self.assertEqual(r, {"bToasts": 0, "bTV": True})

    def test_a_reloaded_pc_claims_the_role_again_so_the_latest_wins(self):
        r = _run("""
const bus = [];
const b = makeScreen('B', bus, { tv: false, stored: 'pc' });   // an older PC
const a = makeScreen('A', bus, { tv: false, stored: 'pc' });   // reloads later
a.runTimers();                                                  // its delayed claim fires
console.log(JSON.stringify({ a: a.isTV(), b: b.isTV() }));
""")
        self.assertEqual(r, {"a": False, "b": True})

    def test_pressing_pc_on_the_pc_does_not_bounce_it_back(self):
        r = _run("""
const bus = [];
const a = makeScreen('A', bus, { tv: true });
a.click(a.bPC); a.click(a.bPC);
console.log(JSON.stringify({ tv: a.isTV() }));
""")
        self.assertFalse(r["tv"])


@unittest.skipUnless(NODE, "node not available")
class TypingMirrorTest(unittest.TestCase):
    def test_the_tv_shows_what_is_typed_on_the_pc_as_it_happens(self):
        r = _run("""
const bus = [];
const pc = makeScreen('PC', bus, { tv: false, peer: true });
const tv = makeScreen('TV', bus, { tv: true });
const ta = pc.els['player-input-text'];
ta.value = 'I open the door';
pc.tick();
const box = tv.els['text-content'].children[0];
const first = { shown: box && box.className, text: box && box.textContent, name: box && box.children[0] && box.children[0].textContent };
ta.value = 'I open the door slowly';
pc.tick();
const second = box.children[box.children.length - 1].nodeText;
ta.value = '';
pc.tick();
console.log(JSON.stringify({ first, second, cleared: box.className }));
""")
        self.assertEqual(r["first"]["shown"], "show")
        self.assertEqual(r["first"]["name"], "Mira:")
        self.assertEqual(r["second"], "I open the door slowly")
        self.assertEqual(r["cleared"], "")

    def test_nothing_is_sent_while_no_tv_is_connected_or_nothing_changed(self):
        r = _run("""
const bus = [];
const sent = [];
const pc = makeScreen('PC', bus, { tv: false, peer: false });
pc.win._relaySend = m => sent.push(m);
pc.els['player-input-text'].value = 'hello';
pc.tick();
pc.win._tvPeerConnected = true;
pc.tick(); pc.tick();                       // first tick sends, the second finds no change
console.log(JSON.stringify({ sent: sent.length, kind: sent[0] && sent[0].t }));
""")
        self.assertEqual(r, {"sent": 1, "kind": "typing"})

    def test_a_tv_never_reports_typing(self):
        r = _run("""
const bus = [];
const sent = [];
const tv = makeScreen('TV', bus, { tv: true, peer: true });
tv.win._relaySend = m => sent.push(m);
tv.els['player-input-text'].value = 'x';
tv.tick();
console.log(JSON.stringify({ sent: sent.length }));
""")
        self.assertEqual(r, {"sent": 0})


@unittest.skipUnless(NODE, "node not available")
class GoMenuTest(unittest.TestCase):
    def test_every_screen_goes_to_the_main_menu_when_told_to(self):
        r = _run("""
const bus = [];
const a = makeScreen('A', bus, { tv: false });
const b = makeScreen('B', bus, { tv: true });
a.win._relaySend({ t: 'go-menu' });
console.log(JSON.stringify({ a: a.ctx.location.href, b: b.ctx.location.href }));
""")
        self.assertEqual(r, {"a": "/menu", "b": "/menu"})


class PageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = HTML.read_text(encoding="utf-8")

    def test_one_switch_replaces_the_old_button(self):
        self.assertIn('id="view-switch"', self.html)
        self.assertNotIn('id="view-toggle"', self.html)
        self.assertEqual(self.html.count('data-view="tv"'), 1)
        self.assertEqual(self.html.count('data-view="pc"'), 1)

    def test_it_sits_next_to_the_cog(self):
        cog = re.search(r"#settings-cog \{[^}]*left: (\d+)px; bottom: (\d+)px;[^}]*width: (\d+)px", self.html, re.S)
        sw = re.search(r"#view-switch\{ position:fixed; left:(\d+)px; bottom:(\d+)px", self.html)
        self.assertTrue(cog and sw)
        cog_left, cog_bottom, cog_w = map(int, cog.groups())
        sw_left, sw_bottom = map(int, sw.groups())
        self.assertEqual(sw_bottom, cog_bottom)                       # same row
        self.assertGreaterEqual(sw_left, cog_left + cog_w)            # starts after the cog
        self.assertLessEqual(sw_left - (cog_left + cog_w), 16)        # and close to it

    def test_tv_is_the_default(self):
        self.assertIn("sessionStorage.getItem('dnd-view') !== 'pc'", self.html)

    def test_the_phone_view_has_no_switch(self):
        self.assertIn("body.input-only #view-switch{ display:none !important; }", self.html)


if __name__ == "__main__":
    unittest.main()
