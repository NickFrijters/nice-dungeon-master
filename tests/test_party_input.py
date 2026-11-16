"""Party Input: one row per character, GO sends every filled row, rows stay in sync between screens."""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
DISPLAY = REPO / "skills" / "dnd" / "display"
SCRIPTS = REPO / "skills" / "dnd" / "scripts"
HTML = DISPLAY / "templates" / "index.html"
NODE = shutil.which("node")
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(DISPLAY))

import paths  # noqa: E402


class PartyInputRouteTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        (root / "campaigns").mkdir()
        (root / ".runtime").mkdir()
        cls._env = {k: os.environ.get(k) for k in ("DND_CAMPAIGN_ROOT", "DND_RUNTIME_DIR")}
        os.environ["DND_CAMPAIGN_ROOT"] = str(root)
        os.environ["DND_RUNTIME_DIR"] = str(root / ".runtime")
        import runtime_paths  # remembers its folder from first import: pin both
        cls._patches = [mock.patch.object(paths, "_root", lambda: root),
                        mock.patch.object(runtime_paths, "RT", str(root / ".runtime"))]
        for p in cls._patches:
            p.start()
        spec = importlib.util.spec_from_file_location("display_app_party_input", DISPLAY / "dnd-display-app.py")
        cls.mod = importlib.util.module_from_spec(spec)
        sys.modules["display_app_party_input"] = cls.mod
        spec.loader.exec_module(cls.mod)
        cls.client = cls.mod.app.test_client()
        assert str(root) in cls.mod.QUEUE_FILE, "test would touch the real runtime folder"

    @classmethod
    def tearDownClass(cls):
        for p in cls._patches:
            p.stop()
        for k, v in cls._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        cls.tmp.cleanup()

    def setUp(self):
        if os.path.exists(self.mod.QUEUE_FILE):
            os.remove(self.mod.QUEUE_FILE)
        self.mod._input_gate = None
        self.mod._staged.clear()
        self.mod._queue_status.clear()      # the 'queued' list now accumulates until the DM takes the file
        self.client.post("/stats", json={"players": [{"name": "Mira"}, {"name": "Joris"}], "replace_players": True})

    def _queue(self):
        return Path(self.mod.QUEUE_FILE).read_text(encoding="utf-8").splitlines()

    def _go(self, entries, **kw):
        kw.setdefault("headers", {"X-DND-Device": "test-device"})        # the page always sends its device id
        return self.client.post("/party-input", json={"entries": entries}, **kw)

    def test_every_filled_row_reaches_the_dm_in_one_batch(self):
        r = self._go([{"character": "Everybody", "text": "We look around."},
                      {"character": "Mira", "text": "I draw my rapier."},
                      {"character": "Joris", "text": "I check the door."}])
        self.assertEqual(r.status_code, 204)
        self.assertEqual(self._queue(), ["[Everybody]: We look around.", "[Mira]: I draw my rapier.",
                                         "[Joris]: I check the door."])

    def test_empty_rows_are_simply_not_sent(self):
        r = self._go([{"character": "Everybody", "text": ""}, {"character": "Mira", "text": "   "},
                      {"character": "Joris", "text": "I wait."}])
        self.assertEqual(r.status_code, 204)
        self.assertEqual(self._queue(), ["[Joris]: I wait."])

    def test_nothing_filled_in_is_refused(self):
        self.assertEqual(self._go([{"character": "Mira", "text": ""}]).status_code, 400)
        self.assertEqual(self._go([]).status_code, 400)
        self.assertFalse(os.path.exists(self.mod.QUEUE_FILE))

    def test_garbage_is_refused(self):
        h = {"X-DND-Device": "test-device"}
        self.assertEqual(self.client.post("/party-input", json={"entries": "nope"}, headers=h).status_code, 400)
        self.assertEqual(self.client.post("/party-input", json={}, headers=h).status_code, 400)
        self.assertEqual(self.client.post("/party-input", json={"entries": []}).status_code, 403)   # no device id at all

    def test_an_unknown_character_is_refused_and_nothing_is_half_sent(self):
        r = self._go([{"character": "Mira", "text": "I wait."}, {"character": "Intruder", "text": "Hi."}])
        self.assertEqual(r.status_code, 403)
        self.assertFalse(os.path.exists(self.mod.QUEUE_FILE))

    def test_the_dms_input_gate_still_applies(self):
        self.client.post("/input-gate", json={"allowed": ["Mira"]})
        r = self._go([{"character": "Mira", "text": "I act."}, {"character": "Joris", "text": "So do I."}])
        self.assertEqual(r.status_code, 409)
        self.assertIn("Joris", r.get_data(as_text=True))
        self.assertFalse(os.path.exists(self.mod.QUEUE_FILE))
        self.assertEqual(self._go([{"character": "Mira", "text": "I act."}]).status_code, 204)

    def test_shell_characters_are_stripped(self):
        self._go([{"character": "Mira", "text": "I say `rm -rf` and $HOME"}])
        line = self._queue()[0]
        self.assertNotIn("`", line)
        self.assertNotIn("$", line)

    def test_a_character_listed_twice_keeps_the_last_text(self):
        self._go([{"character": "Mira", "text": "first"}, {"character": "Mira", "text": "second"}])
        self.assertEqual(self._queue(), ["[Mira]: second"])

    def test_the_screens_are_told_and_nothing_stays_staged(self):
        sent = []
        with mock.patch.object(self.mod, "_broadcast", side_effect=sent.append):
            self._go([{"character": "Mira", "text": "I act."}])
        self.assertTrue(any(m.get("staged_inputs") == {} and m.get("queue_status") == ["Mira"] for m in sent))
        self.assertEqual(self.mod._staged, {})

    def test_a_new_device_waits_for_approval(self):
        with mock.patch.object(self.mod, "_REQUIRE_APPROVAL", True):
            r = self._go([{"character": "Mira", "text": "I act."}],
                         environ_overrides={"REMOTE_ADDR": "192.168.77.23"},
                         headers={"X-DND-Device": "new-phone"})
        self.assertEqual(r.status_code, 202)
        self.assertFalse(os.path.exists(self.mod.QUEUE_FILE))


def _rows_code() -> str:
    src = HTML.read_text(encoding="utf-8")
    a = src.index("const _partyRowsEl = document.getElementById('party-rows');")
    b = src.index("// ── Keep the reading column clear of the fixed PARTY INPUT panel")
    return src[a:b]


HARNESS = r"""
const vm = require('vm');
class El {
  constructor(tag) { this.tag = tag; this.children = []; this.dataset = {}; this.attrs = {}; this._cls = new Set();
    this.hidden = false; this.disabled = false; this.value = ''; this._t = ''; this.style = {}; this.handlers = {}; this.selectionStart = 0; }
  appendChild(c) { this.children.push(c); c.parent = this; return c; }
  set textContent(v) { this._t = v; if (v === '') this.children = []; }
  get textContent() { return this._t; }
  set className(v) { this._cls = new Set(String(v).split(' ').filter(Boolean)); }
  get className() { return Array.from(this._cls).join(' '); }
  get classList() { const s = this._cls; return { contains: c => s.has(c), add: c => s.add(c), remove: c => s.delete(c),
    toggle: (c, on) => { (on === undefined ? !s.has(c) : on) ? s.add(c) : s.delete(c); } }; }
  setAttribute(k, v) { this.attrs[k] = v; }
  addEventListener(t, f) { (this.handlers[t] = this.handlers[t] || []).push(f); }
  fire(t, ev) { (this.handlers[t] || []).forEach(f => f(ev || {})); }
  querySelector(sel) { const w = n => { for (const c of n.children) { if (c.tag === sel) return c; const d = w(c); if (d) return d; } return null; }; return w(this); }
  querySelectorAll(sel) { return this.children.filter(c => sel === '.party-row' && c._cls.has('party-row')); }
  setSelectionRange(a) { this.selectionStart = a; }
}
function makeScreen() {
  const els = { 'party-rows': new El('div'), 'party-go-btn': new El('button'), 'party-hint': new El('span') };
  const sent = [], toasts = [], timers = [], handlers = [], calls = { turnStart: 0, fetches: [] };
  const ctx = {
    document: { getElementById: id => els[id], createElement: tag => new El(tag), activeElement: null },
    window: { _relayHandlers: handlers, _relaySend: m => sent.push(m) },
    _inputBadge: new El('span'), _inputAllowed: null, _authHeaders: () => ({ 'X-DND-Device': 'dev' }), _deviceId: 'dev',
    localStorage: { setItem() {}, removeItem() {} }, _turnStart: () => { calls.turnStart++; },
    _showToast: (m) => toasts.push(m),
    setTimeout: (f) => { timers.push(f); return timers.length; }, clearTimeout: () => {},
    fetch: async (url, opts) => { calls.fetches.push({ url, body: JSON.parse(opts.body) }); return ctx.nextResponse; },
    nextResponse: { ok: true, status: 204, text: async () => '' },
  };
  ctx.window.window = ctx.window;
  vm.createContext(ctx);
  vm.runInContext(SCRIPT, ctx);
  const rows = () => els['party-rows'].children;
  const input = name => rows().find(r => r.dataset.char === name).querySelector('input');
  const type = (name, text) => { const i = input(name); i.value = text; i.fire('input'); };
  return { ctx, els, sent, toasts, timers, handlers, calls, rows, input, type,
           run: code => vm.runInContext(code, ctx), go: els['party-go-btn'],
           deliver: m => handlers.forEach(h => h(m)),
           names: () => rows().map(r => r.dataset.char),
           filled: () => rows().filter(r => r._cls.has('filled')).map(r => r.dataset.char) };
}
const PARTY = [{ name: 'Mira' }, { name: 'Joris' }];
"""


def _run(body: str) -> dict:
    if NODE is None:
        raise unittest.SkipTest("node not available")
    script = ("const SCRIPT = " + json.dumps(_rows_code()) + ";\n" + HARNESS +
              "\n(async () => {\n" + body + "\n})().catch(e => { console.error(e); process.exit(1); });")
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
class RowsTest(unittest.TestCase):
    def test_one_row_per_character_with_everybody_first(self):
        r = _run("""
const s = makeScreen(); s.run("_buildPartyRows(PARTY)".replace('PARTY', JSON.stringify(PARTY)));
console.log(JSON.stringify({ names: s.names(), every: s.rows()[0]._cls.has('everybody'),
  go: s.go.textContent, disabled: s.go.disabled }));
""")
        self.assertEqual(r["names"], ["Everybody", "Mira", "Joris"])
        self.assertTrue(r["every"])
        self.assertTrue(r["disabled"])                       # nothing to send yet

    def test_a_row_with_text_is_marked_and_go_counts_the_filled_rows(self):
        r = _run("""
const s = makeScreen(); s.run("_buildPartyRows(" + JSON.stringify(PARTY) + ")");
s.type('Mira', 'I draw my rapier.'); s.type('Everybody', 'We look around.');
const two = { filled: s.filled(), go: s.go.textContent, disabled: s.go.disabled };
s.type('Mira', '   ');                                      // blank again
console.log(JSON.stringify({ two, after: { filled: s.filled(), go: s.go.textContent } }));
""")
        self.assertEqual(sorted(r["two"]["filled"]), ["Everybody", "Mira"])
        self.assertIn("(2)", r["two"]["go"])
        self.assertFalse(r["two"]["disabled"])
        self.assertEqual(r["after"]["filled"], ["Everybody"])
        self.assertIn("(1)", r["after"]["go"])

    def test_a_stats_update_for_the_same_party_does_not_disturb_typing(self):
        r = _run("""
const s = makeScreen(); s.run("_buildPartyRows(" + JSON.stringify(PARTY) + ")");
s.type('Mira', 'half a sentence');
const before = s.input('Mira');
s.run("_buildPartyRows(" + JSON.stringify(PARTY) + ")");   // an HP change arrives
console.log(JSON.stringify({ same: s.input('Mira') === before, value: s.input('Mira').value }));
""")
        self.assertTrue(r["same"])
        self.assertEqual(r["value"], "half a sentence")

    def test_a_new_character_joining_keeps_what_is_typed(self):
        r = _run("""
const s = makeScreen(); s.run("_buildPartyRows(" + JSON.stringify(PARTY) + ")");
s.type('Mira', 'I wait.'); s.type('Everybody', 'Quiet now.');
s.run("_buildPartyRows(" + JSON.stringify([...PARTY, { name: 'Sylvar' }]) + ")");
console.log(JSON.stringify({ names: s.names(), mira: s.input('Mira').value, all: s.input('Everybody').value }));
""")
        self.assertEqual(r["names"], ["Everybody", "Mira", "Joris", "Sylvar"])
        self.assertEqual((r["mira"], r["all"]), ("I wait.", "Quiet now."))

    def test_the_dms_gate_hides_the_rows_it_is_not_waiting_on(self):
        r = _run("""
const s = makeScreen(); s.run("_buildPartyRows(" + JSON.stringify(PARTY) + ")");
s.run("_inputAllowed = ['Mira']; _applyRowGate();");
const some = s.rows().map(x => [x.dataset.char, x.hidden]);
s.run("_inputAllowed = []; _applyRowGate();");
const none = { disabled: s.rows().every(x => x.querySelector('input').disabled), hint: s.els['party-hint'].textContent };
s.run("_inputAllowed = null; _applyRowGate();");
console.log(JSON.stringify({ some, none, open: s.rows().every(x => !x.hidden) }));
""")
        self.assertEqual(dict(r["some"]), {"Everybody": True, "Mira": False, "Joris": True})
        self.assertTrue(r["none"]["disabled"])
        self.assertIn("resolving", r["none"]["hint"])
        self.assertTrue(r["open"])


@unittest.skipUnless(NODE, "node not available")
class GoTest(unittest.TestCase):
    def test_go_sends_every_filled_row_clears_them_and_starts_the_loading_bar(self):
        r = _run("""
const s = makeScreen(); s.run("_buildPartyRows(" + JSON.stringify(PARTY) + ")");
s.type('Everybody', 'We look around.'); s.type('Joris', 'I check the door.');
await s.run("_partyGo()");
console.log(JSON.stringify({ posted: s.calls.fetches, turn: s.calls.turnStart, left: s.filled(),
  clear: s.sent.filter(m => m.t === 'party-fields').pop() }));
""")
        self.assertEqual(r["posted"][0]["url"], "/party-input")
        self.assertEqual(r["posted"][0]["body"]["entries"],
                         [{"character": "Everybody", "text": "We look around."},
                          {"character": "Joris", "text": "I check the door."}])
        self.assertEqual(r["turn"], 1)
        self.assertEqual(r["left"], [])
        self.assertEqual(r["clear"]["fields"], {})            # the other screens clear theirs too

    def test_a_refused_send_keeps_the_text_so_nothing_is_lost(self):
        r = _run("""
const s = makeScreen(); s.run("_buildPartyRows(" + JSON.stringify(PARTY) + ")");
s.type('Mira', 'I act.');
s.ctx.nextResponse = { ok: false, status: 409, text: async () => 'The DM is not waiting for: Mira' };
await s.run("_partyGo()");
console.log(JSON.stringify({ kept: s.input('Mira').value, toast: s.toasts[0], turn: s.calls.turnStart, again: !s.go.disabled }));
""")
        self.assertEqual(r["kept"], "I act.")
        self.assertIn("not waiting", r["toast"])
        self.assertEqual(r["turn"], 0)
        self.assertTrue(r["again"])

    def test_a_new_device_waiting_for_approval_keeps_the_text(self):
        r = _run("""
const s = makeScreen(); s.run("_buildPartyRows(" + JSON.stringify(PARTY) + ")");
s.type('Mira', 'I act.');
s.ctx.nextResponse = { ok: false, status: 202, text: async () => '' };
await s.run("_partyGo()");
console.log(JSON.stringify({ kept: s.input('Mira').value, label: s.go.textContent }));
""")
        self.assertEqual(r["kept"], "I act.")
        self.assertIn("approval", r["label"])

    def test_a_network_failure_keeps_the_text(self):
        r = _run("""
const s = makeScreen(); s.run("_buildPartyRows(" + JSON.stringify(PARTY) + ")");
s.type('Mira', 'I act.');
s.ctx.fetch = async () => { throw new Error('offline'); };
await s.run("_partyGo()");
console.log(JSON.stringify({ kept: s.input('Mira').value, toasts: s.toasts.length }));
""")
        self.assertEqual(r["kept"], "I act.")
        self.assertEqual(r["toasts"], 1)

    def test_ctrl_enter_in_a_row_is_go(self):
        r = _run("""
const s = makeScreen(); s.run("_buildPartyRows(" + JSON.stringify(PARTY) + ")");
s.type('Mira', 'I act.');
let prevented = false;
s.input('Mira').fire('keydown', { ctrlKey: true, key: 'Enter', preventDefault() { prevented = true; } });
await Promise.resolve(); await Promise.resolve();
console.log(JSON.stringify({ prevented, posted: s.calls.fetches.length }));
""")
        self.assertEqual(r, {"prevented": True, "posted": 1})

    def test_plain_enter_does_not_send(self):
        r = _run("""
const s = makeScreen(); s.run("_buildPartyRows(" + JSON.stringify(PARTY) + ")");
s.type('Mira', 'I act.');
s.input('Mira').fire('keydown', { key: 'Enter', preventDefault() {} });
await Promise.resolve();
console.log(JSON.stringify({ posted: s.calls.fetches.length }));
""")
        self.assertEqual(r, {"posted": 0})


@unittest.skipUnless(NODE, "node not available")
class SyncTest(unittest.TestCase):
    def test_typing_is_shared_with_the_other_screens_shortly_after(self):
        r = _run("""
const s = makeScreen(); s.run("_buildPartyRows(" + JSON.stringify(PARTY) + ")");
s.type('Mira', 'I creep forward');
const before = s.sent.length;
s.timers.splice(0).forEach(f => f());                       // the short delay passes
console.log(JSON.stringify({ before, sent: s.sent.filter(m => m.t === 'party-fields').pop() }));
""")
        self.assertEqual(r["before"], 0)                      # not sent on every keystroke
        self.assertEqual(r["sent"]["fields"], {"Mira": "I creep forward"})

    def test_text_typed_on_another_screen_appears_here(self):
        r = _run("""
const s = makeScreen(); s.run("_buildPartyRows(" + JSON.stringify(PARTY) + ")");
s.deliver({ t: 'party-fields', fields: { Joris: 'I check the door.', Everybody: 'Hush.' } });
console.log(JSON.stringify({ joris: s.input('Joris').value, all: s.input('Everybody').value, filled: s.filled().sort(),
  go: s.go.textContent, echoed: s.sent.length }));
""")
        self.assertEqual((r["joris"], r["all"]), ("I check the door.", "Hush."))
        self.assertEqual(r["filled"], ["Everybody", "Joris"])
        self.assertIn("(2)", r["go"])
        self.assertEqual(r["echoed"], 0)                      # applying it does not send it back

    def test_the_last_one_to_type_wins_and_a_cleared_field_clears_here(self):
        r = _run("""
const s = makeScreen(); s.run("_buildPartyRows(" + JSON.stringify(PARTY) + ")");
s.type('Mira', 'mine');
s.deliver({ t: 'party-fields', fields: { Mira: 'theirs' } });
const after = s.input('Mira').value;
s.deliver({ t: 'party-fields', fields: {} });
console.log(JSON.stringify({ after, cleared: s.input('Mira').value, filled: s.filled() }));
""")
        self.assertEqual(r["after"], "theirs")
        self.assertEqual(r["cleared"], "")
        self.assertEqual(r["filled"], [])

    def test_the_caret_stays_put_in_the_field_being_edited(self):
        r = _run("""
const s = makeScreen(); s.run("_buildPartyRows(" + JSON.stringify(PARTY) + ")");
const i = s.input('Mira'); i.value = 'abcdef'; i.selectionStart = 3; s.ctx.document.activeElement = i;
s.deliver({ t: 'party-fields', fields: { Mira: 'abcdefgh' } });
console.log(JSON.stringify({ value: i.value, caret: i.selectionStart }));
""")
        self.assertEqual(r, {"value": "abcdefgh", "caret": 3})

    def test_a_screen_that_just_opened_gets_what_is_already_typed(self):
        r = _run("""
const s = makeScreen(); s.run("_buildPartyRows(" + JSON.stringify(PARTY) + ")");
s.deliver({ t: 'fields-request' });
const none = s.sent.length;
s.type('Mira', 'already here');
s.timers.splice(0);
s.deliver({ t: 'fields-request' });
console.log(JSON.stringify({ none, reply: s.sent.filter(m => m.t === 'party-fields').pop() }));
""")
        self.assertEqual(r["none"], 0)                        # nothing typed: no reply needed
        self.assertEqual(r["reply"]["fields"], {"Mira": "already here"})

    def test_text_for_a_character_this_screen_does_not_have_is_ignored(self):
        r = _run("""
const s = makeScreen(); s.run("_buildPartyRows(" + JSON.stringify(PARTY) + ")");
s.deliver({ t: 'party-fields', fields: { Stranger: 'boo' } });
console.log(JSON.stringify({ filled: s.filled() }));
""")
        self.assertEqual(r["filled"], [])


class PageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = HTML.read_text(encoding="utf-8")

    def test_the_rows_and_go_are_in_the_panel(self):
        self.assertIn('id="party-rows"', self.html)
        self.assertIn('id="party-go-btn"', self.html)

    def test_the_old_stage_controls_are_hidden_on_the_main_screen(self):
        rule = re.search(r"#char-tabs, #player-input-text[^{]*\{([^}]*)\}", self.html, re.S)
        self.assertIsNotNone(rule)
        for hidden in ("#player-input-text", "#input-footer", "#staged-queue", "#waiting-indicator"):
            self.assertIn(hidden, rule.group(0))
        self.assertIn("display:none", rule.group(1).replace(" ", ""))

    def test_the_party_list_feeds_the_rows_and_the_gate_reaches_them(self):
        self.assertIn("_buildPartyRows(players);", self.html)
        self.assertIn("_applyRowGate();\n}", self.html)

    def test_the_panel_is_vertically_compact(self):
        pad = re.search(r"\.party-row input\{[^}]*padding:(\d+)px (\d+)px;", self.html)
        self.assertTrue(pad)
        self.assertLessEqual(int(pad.group(1)), 5)                      # slim fields: ~25px rows, not ~38px
        gap = re.search(r"#party-rows\{ display:flex; flex-direction:column; gap:(\d+)px; \}", self.html)
        self.assertLessEqual(int(gap.group(1)), 3)
        self.assertIn("#input-body{ padding:0 12px 8px; gap:4px; }", self.html)
        self.assertRegex(self.html, r"#party-go-btn\{[^}]*padding:4px 16px;")

    def test_a_filled_row_looks_different(self):
        self.assertIn(".party-row.filled{", self.html)


if __name__ == "__main__":
    unittest.main()
