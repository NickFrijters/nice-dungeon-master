"""Image library: ingest, dedupe, search/show/use, send.py fallback, download safety (network mocked)."""
import io
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "skills" / "dnd" / "scripts"))
import image_library as il  # noqa: E402

PNG_A = b"\x89PNG\r\n\x1a\n" + b"A" * 50
PNG_B = b"\x89PNG\r\n\x1a\n" + b"B" * 50
JPG = b"\xff\xd8\xff\xe0" + b"J" * 50


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        self.env = mock.patch.dict(os.environ, {"DND_CAMPAIGN_ROOT": str(self.root)})
        self.env.start()
        os.environ.pop("DND_IMAGE_LIBRARY", None)

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def campaign(self, name, files, index=None):
        d = self.root / "campaigns" / name / "images"
        d.mkdir(parents=True)
        for fn, data in files.items():
            (d / fn).write_bytes(data)
        if index is not None:
            (d / "index.json").write_text(json.dumps(index), encoding="utf-8")
        return d


class Resp:
    def __init__(self, data, ctype="image/png", clen=None):
        self.data, self.headers = data, {"Content-Type": ctype}
        if clen is not None:
            self.headers["Content-Length"] = str(clen)

    def read(self, n=-1):
        return self.data[:n] if n >= 0 else self.data

    def close(self):
        pass


class LibraryTests(Base):
    def test_default_location_follows_data_root(self):
        self.assertEqual(il.library_dir().resolve(), (self.root / "image-library").resolve())

    def test_ingest_campaign_idempotent_and_metadata(self):
        self.campaign("adv", {"a.png": PNG_A, "b.png": PNG_B, "c.jpg": JPG},
                      {"cult": {"file": "a.png", "title": "Cult assassin", "type": "scene",
                                "tags": ["Cultist"], "source": "pdf p.6"},
                       "map-coast": {"file": "b.png", "title": "Coast", "type": "map", "source": "pdf p.8"}})
        r = il.ingest_campaign("adv")
        self.assertEqual((r["new"], r["duplicate"]), (3, 0))
        r2 = il.ingest_campaign("adv")
        self.assertEqual((r2["new"], r2["duplicate"]), (0, 3))
        idx = il.load_index()
        self.assertEqual(len(idx), 3)
        rec = next(x for x in idx.values() if x["title"] == "Cult assassin")
        self.assertEqual(rec["kind"], "art")
        self.assertEqual(rec["tags"], ["cultist"])
        self.assertEqual(rec["source"], {"adventure": "adv", "page": 6})
        self.assertEqual(rec["campaigns"], ["adv"])
        self.assertIn("cult", rec["aliases"])
        self.assertEqual(len(rec["sha256"]), 64)
        self.assertEqual(next(x for x in idx.values() if x["title"] == "Coast")["kind"], "map")
        self.assertEqual(len(list((il.library_dir() / "files").iterdir())), 3)

    def test_dedupe_across_campaigns_merges(self):
        self.campaign("one", {"x.png": PNG_A}, {"x": {"file": "x.png", "title": "Goblin", "tags": ["a"]}})
        self.campaign("two", {"y.png": PNG_A}, {"y": {"file": "y.png", "title": "Other", "tags": ["b"]}})
        il.ingest_campaign("one")
        il.ingest_campaign("two")
        idx = il.load_index()
        self.assertEqual(len(idx), 1)
        rec = next(iter(idx.values()))
        self.assertEqual(rec["campaigns"], ["one", "two"])
        self.assertEqual(rec["tags"], ["a", "b"])
        self.assertEqual(rec["title"], "Goblin")
        self.assertEqual(len(list((il.library_dir() / "files").iterdir())), 1)

    def test_non_image_skipped(self):
        self.campaign("adv", {"a.png": b"not an image"})
        r = il.ingest_campaign("adv")
        self.assertEqual(r["skipped"], 1)
        self.assertEqual(il.load_index(), {})

    def test_add_file_search_show_stats(self):
        f = self.root / "orc.png"
        f.write_bytes(PNG_A)
        self.assertEqual(il.main(["add", str(f), "--title", "Orc Warchief", "--tags", "orc,boss",
                                  "--source", "Own scan", "--kind", "monster"]), 0)
        self.assertEqual(il.main(["add", str(f), "--title", "Again"]), 0)  # duplicate
        self.assertEqual(len(il.load_index()), 1)
        hits = il.search(["orc", "boss"])
        self.assertEqual(len(hits), 1)
        self.assertEqual(il.search(["dragon"]), [])
        with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            il.main(["show", hits[0]["id"]])
        self.assertEqual(json.loads(out.getvalue())["kind"], "monster")
        st = il.stats()
        self.assertEqual((st["images"], st["by_kind"]), (1, {"monster": 1}))

    def test_unknown_show_fails(self):
        self.assertEqual(il.main(["show", "nope"]), 1)

    def test_use_copies_into_campaign_and_indexes(self):
        iid, _ = il.add_bytes(PNG_A, title="Orc", kind="monster", tags="orc")
        (self.root / "campaigns" / "new").mkdir(parents=True)
        self.assertEqual(il.main(["use", iid, "--campaign", "new"]), 0)
        d = self.root / "campaigns" / "new" / "images"
        cidx = json.loads((d / "index.json").read_text(encoding="utf-8"))
        self.assertEqual(cidx[iid]["type"], "monster")
        self.assertTrue((d / cidx[iid]["file"]).is_file())
        self.assertEqual(il.load_index()[iid]["campaigns"], ["new"])
        self.assertEqual(il.main(["use", iid, "--campaign", "ghost"]), 1)

    def test_send_py_falls_back_to_library(self):
        iid, _ = il.add_bytes(PNG_A, title="Orc", kind="monster")
        (self.root / "campaigns" / "play").mkdir(parents=True)
        rt = self.root / ".runtime"
        rt.mkdir(exist_ok=True)
        (rt / ".campaign").write_text("play", encoding="utf-8")
        env = dict(os.environ, DND_RUNTIME_DIR=str(rt), DND_DISPLAY_PORT="59999", PYTHONUTF8="1")
        send = ROOT / "skills" / "dnd" / "display" / "send.py"
        p = subprocess.run([sys.executable, str(send), "--image", iid], env=env,
                           capture_output=True, text=True, stdin=subprocess.DEVNULL)
        self.assertNotIn("image not found", p.stderr, p.stderr)
        self.assertTrue(any((self.root / "campaigns" / "play" / "images").glob(iid + ".*")))
        p = subprocess.run([sys.executable, str(send), "--image", "no-such-image"], env=env,
                           capture_output=True, text=True, stdin=subprocess.DEVNULL)
        self.assertEqual(p.returncode, 3)
        self.assertIn("image not found", p.stderr)


class DownloadTests(Base):
    def run_add(self, url, resp=None, exc=None):
        calls = []

        def fake(req, timeout):
            calls.append((req, timeout))
            if exc:
                raise exc
            return resp

        with mock.patch.object(il, "_urlopen", fake):
            rc = il.main(["add", url, "--title", "T", "--source", "Some Wiki"])
        return rc, calls

    def test_good_download_records_source_and_polite_headers(self):
        rc, calls = self.run_add("https://img.example.org/a/orc.png", Resp(PNG_A))
        self.assertEqual(rc, 0)
        req, timeout = calls[0]
        self.assertIn("dnd", req.get_header("User-agent").lower())
        self.assertLessEqual(timeout, 30)
        rec = next(iter(il.load_index().values()))
        self.assertEqual(rec["source"]["url"], "https://img.example.org/a/orc.png")
        self.assertEqual(rec["source"]["site"], "img.example.org")
        self.assertEqual(rec["source"]["credit"], "Some Wiki")

    def test_rejects_non_http(self):
        for u in ("file:///etc/passwd", "ftp://x/a.png", "gopher://x/1"):
            rc, calls = self.run_add(u, Resp(PNG_A))
            self.assertEqual((rc, calls), (1, []))
        self.assertEqual(il.load_index(), {})

    def test_rejects_wrong_content_type(self):
        rc, _ = self.run_add("https://x.org/a.png", Resp(PNG_A, ctype="text/html"))
        self.assertEqual(rc, 1)
        self.assertEqual(il.load_index(), {})

    def test_rejects_oversize_by_header_and_by_body(self):
        rc, _ = self.run_add("https://x.org/a.png", Resp(PNG_A, clen=il.MAX_BYTES + 1))
        self.assertEqual(rc, 1)
        big = PNG_A + b"0" * il.MAX_BYTES
        rc, _ = self.run_add("https://x.org/a.png", Resp(big))
        self.assertEqual(rc, 1)
        self.assertEqual(il.load_index(), {})

    def test_rejects_fake_image_bytes(self):
        rc, _ = self.run_add("https://x.org/a.png", Resp(b"<html>nope</html>"))
        self.assertEqual(rc, 1)

    def test_network_error_is_reported_not_raised(self):
        rc, _ = self.run_add("https://x.org/a.png", exc=urllib.error.URLError("boom"))
        self.assertEqual(rc, 1)

    def test_redirect_to_non_http_refused(self):
        h = il._HttpOnly()
        with self.assertRaises(urllib.error.URLError):
            h.redirect_request(urllib.request.Request("https://x.org/"), None, 302, "x", {},
                               "file:///etc/passwd")


class CampaignImageTests(Base):
    """campaign_image(): how a map picture for the combat grid is found (by file, campaign id, or the shared library)."""

    def test_finds_by_file_by_campaign_id_and_from_the_library(self):
        self.campaign("adv", {"cellar.png": PNG_A, "b.png": PNG_B},
                      {"map-cellar": {"file": "b.png", "title": "Cellar", "type": "map"}})
        self.assertEqual(il.campaign_image("cellar.png", "adv")["file"], "cellar.png")
        got = il.campaign_image("map-cellar", "adv")
        self.assertEqual((got["file"], got["kind"]), ("b.png", "map"))
        # not in the campaign yet, but in the shared library: it is copied in
        self.campaign("other", {})
        iid, _ = il.add_bytes(JPG, title="Crypt", kind="map")
        got = il.campaign_image(iid, "other")
        self.assertTrue((self.root / "campaigns" / "other" / "images" / got["file"]).is_file())
        # unknown, or path tricks: nothing
        self.assertIsNone(il.campaign_image("nope.png", "adv"))
        self.assertIsNone(il.campaign_image("../../etc/passwd", "adv"))
        self.assertIsNone(il.campaign_image("", "adv"))


class ImportWiringTests(unittest.TestCase):
    def test_import_campaign_has_extract_images_option(self):
        src = (ROOT / "skills" / "dnd" / "scripts" / "import_campaign.py").read_text(encoding="utf-8")
        self.assertIn("--extract-images", src)
        self.assertIn("image_library", src)


if __name__ == "__main__":
    unittest.main()
