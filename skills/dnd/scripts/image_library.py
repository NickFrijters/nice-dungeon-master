#!/usr/bin/env python3
"""
image_library.py — persistent, cross-campaign flavour-image library.

Lives at <data root>/image-library/ (default ~/.claude/dnd/image-library/, moves
with DND_CAMPAIGN_ROOT; override with DND_IMAGE_LIBRARY):
    index.json          one record per image (see below)
    files/<id>.<ext>    the image files, deduplicated by SHA-256

Record: id, sha256, file, title, caption, kind, tags, source{adventure,page,url,
site,credit,license,note}, added, campaigns (campaigns that used it), aliases
(old campaign-local ids / file names, so `send.py --image <old-id>` still works),
bytes, w/h when known.
kind is one of: map monster npc location item symbol art.

Commands:
    ingest-campaign <name>                 copy every image of a campaign in (idempotent)
    add <file|url> --title T --tags a,b --source "..." [--kind K] [--caption C]
    search <words...>                      all words must match title/caption/tags/kind/source
    show <id>
    use <id> --campaign <name>             make it available in <campaign>/images/
    stats

Downloads (add <url>): http/https only, redirects must stay http/https, 8 MB cap,
image/* content-type AND image magic bytes, 20 s timeout, polite user agent.
"""
import argparse
import datetime
import hashlib
import json
import os
import pathlib
import re
import shutil
import sys
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paths  # noqa: E402

KINDS = ("map", "monster", "npc", "location", "item", "symbol", "art")
MAX_BYTES = 8_000_000
TIMEOUT = 20
USER_AGENT = "dnd-dm-image-library/1.0 (personal D&D table tool; low volume; reuses a local cache)"
EXTS = (".png", ".jpg", ".jpeg", ".webp", ".gif")
_KIND_MAP = {"scene": "art", "handout": "item", "creature": "monster", "place": "location",
             "character": "npc", "token": "monster", "illustration": "art"}


# ── locations ────────────────────────────────────────────────────────────
def library_dir() -> pathlib.Path:
    env = os.environ.get("DND_IMAGE_LIBRARY", "").strip()
    if env:
        return pathlib.Path(env).expanduser()
    return paths._root() / "image-library"


def _index_path():
    return library_dir() / "index.json"


def load_index() -> dict:
    try:
        d = json.loads(_index_path().read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save_index(idx: dict) -> None:
    library_dir().mkdir(parents=True, exist_ok=True)
    tmp = _index_path().with_suffix(".tmp")
    tmp.write_text(json.dumps(idx, indent=1, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    os.replace(tmp, _index_path())


# ── helpers ──────────────────────────────────────────────────────────────
def sniff_ext(data: bytes):
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    return None


def slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s[:40].strip("-") or "image"


def norm_kind(k: str, hint: str = "") -> str:
    k = (k or "").strip().lower()
    k = _KIND_MAP.get(k, k)
    if k in KINDS:
        return k
    h = hint.lower()
    for key in ("map", "symbol", "monster", "npc", "item", "location"):
        if re.search(r"(^|[^a-z])" + key, h):
            return key
    return "art"


def _split_tags(t) -> list:
    if isinstance(t, str):
        t = t.split(",")
    return sorted({str(x).strip().lower() for x in (t or []) if str(x).strip()})


def parse_source(text: str, adventure: str = "") -> dict:
    """'pdf p.6' -> adventure+page; 'https://x/y.jpg' -> url+site; anything else -> note."""
    text = (text or "").strip()
    src = {}
    if adventure:
        src["adventure"] = adventure
    m = re.search(r"https?://\S+", text)
    if m:
        src["url"] = m.group(0)
        src["site"] = urllib.parse.urlparse(m.group(0)).hostname or ""
        rest = text.replace(m.group(0), "").strip(" -:,")
        if rest:
            src["credit"] = rest
        return src
    p = re.search(r"\bp(?:age|p)?\.?\s*(\d+)", text, re.I)
    if p:
        src["page"] = int(p.group(1))
    elif text:
        src["note"] = text
    return src


def new_id(idx: dict, title: str, sha: str) -> str:
    base = f"{slug(title)}-{sha[:6]}"
    iid, n = base, 2
    while iid in idx:
        iid, n = f"{base}-{n}", n + 1
    return iid


def find_by_hash(idx: dict, sha: str):
    for iid, rec in idx.items():
        if rec.get("sha256") == sha:
            return iid
    return None


def resolve(idx: dict, key: str):
    """Library id, alias or stored file name -> id (or None)."""
    if key in idx:
        return key
    for iid, rec in idx.items():
        if key in rec.get("aliases", []) or key == rec.get("file"):
            return iid
    return None


def _merge(rec: dict, tags, source, campaign=None, alias=None, caption="", title="", kind=None):
    rec["tags"] = sorted(set(rec.get("tags", [])) | set(_split_tags(tags)))
    if campaign and campaign not in rec["campaigns"]:
        rec["campaigns"] = sorted(rec["campaigns"] + [campaign])
    if alias and alias != rec["id"] and alias not in rec["aliases"]:
        rec["aliases"] = sorted(rec["aliases"] + [alias])
    for k, v in (source or {}).items():          # fill gaps only; never overwrite known credit
        rec["source"].setdefault(k, v)
    if caption and not rec.get("caption"):
        rec["caption"] = caption
    if title and rec.get("title") in ("", rec["id"]):
        rec["title"] = title
    if kind and rec.get("kind") == "art" and kind != "art":
        rec["kind"] = kind


def add_bytes(data: bytes, title="", caption="", kind="", tags=(), source=None,
              campaign=None, alias=None, name_hint="") -> tuple:
    """Store image bytes. Returns (id, created). Identical content is never stored twice."""
    ext = sniff_ext(data)
    if not ext:
        raise ValueError("not a PNG/JPEG/WebP/GIF image")
    sha = hashlib.sha256(data).hexdigest()
    idx = load_index()
    existing = find_by_hash(idx, sha)
    kind_n = norm_kind(kind, f"{name_hint} {title}")
    if existing:
        _merge(idx[existing], tags, source, campaign, alias, caption, title, kind_n)
        save_index(idx)
        return existing, False
    title = title or name_hint or "image"
    iid = new_id(idx, title, sha)
    fdir = library_dir() / "files"
    fdir.mkdir(parents=True, exist_ok=True)
    fname = iid + ext
    (fdir / fname).write_bytes(data)
    rec = {"id": iid, "sha256": sha, "file": fname, "title": title, "caption": caption,
           "kind": kind_n, "tags": [], "source": {}, "campaigns": [], "aliases": [],
           "added": datetime.date.today().isoformat(), "bytes": len(data)}
    _merge(rec, tags, source, campaign, alias)
    idx[iid] = rec
    save_index(idx)
    return iid, True


# ── downloading ──────────────────────────────────────────────────────────
class _HttpOnly(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urllib.parse.urlparse(newurl).scheme not in ("http", "https"):
            raise urllib.error.URLError(f"redirect to non-http(s) URL refused: {newurl}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _urlopen(req, timeout):          # single network seam: tests replace this
    opener = urllib.request.build_opener(_HttpOnly)
    return getattr(opener, "open")(req, timeout=timeout)


def download(url: str) -> bytes:
    if urllib.parse.urlparse(url).scheme not in ("http", "https"):
        raise ValueError("only http(s) URLs are allowed")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "image/*"})
    resp = _urlopen(req, TIMEOUT)
    try:
        ctype = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if not ctype.startswith("image/"):
            raise ValueError(f"content-type is '{ctype or 'missing'}', not an image")
        clen = resp.headers.get("Content-Length")
        if clen and clen.isdigit() and int(clen) > MAX_BYTES:
            raise ValueError("image larger than the 8 MB limit")
        data = resp.read(MAX_BYTES + 1)
    finally:
        try:
            resp.close()
        except Exception:
            pass
    if len(data) > MAX_BYTES:
        raise ValueError("image larger than the 8 MB limit")
    if not sniff_ext(data):
        raise ValueError("downloaded data is not a PNG/JPEG/WebP/GIF image")
    return data


# ── commands ─────────────────────────────────────────────────────────────
def ingest_dir(name: str, cdir: pathlib.Path) -> dict:
    """Register every image in a campaign images folder (uses its index.json). Idempotent."""
    try:
        cidx = json.loads((cdir / "index.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cidx = {}
    by_file = {v.get("file"): (k, v) for k, v in cidx.items() if isinstance(v, dict)}
    res = {"new": 0, "duplicate": 0, "skipped": 0, "bytes": 0}
    for f in sorted(cdir.iterdir()):
        if not f.is_file() or f.suffix.lower() not in EXTS:
            continue
        key, meta = by_file.get(f.name, (f.stem, {}))
        data = f.read_bytes()
        try:
            iid, created = add_bytes(
                data, title=meta.get("title") or f.stem, caption=meta.get("caption", ""),
                kind=meta.get("type") or meta.get("kind") or "", tags=meta.get("tags"),
                source=parse_source(meta.get("source", ""), adventure=name), campaign=name,
                alias=key, name_hint=f.stem)
        except ValueError:
            res["skipped"] += 1
            continue
        if f.name != key:                      # the bare file name resolves too
            idx = load_index()
            if f.name not in idx[iid]["aliases"]:
                idx[iid]["aliases"] = sorted(idx[iid]["aliases"] + [f.name])
                save_index(idx)
        res["new" if created else "duplicate"] += 1
        res["bytes"] += len(data) if created else 0
    return res


def ingest_campaign(name: str) -> dict:
    cdir = paths.find_campaign(name) / "images"
    if not cdir.is_dir():
        raise FileNotFoundError(f"no images folder for campaign '{name}': {cdir}")
    return ingest_dir(name, cdir)


def extract_pdf_images(pdf: str, campaign: str, min_px: int = 300) -> dict:
    """Extract illustrations from an adventure PDF into <campaign>/images/ (+ index.json)
    and register them in the library. Needs PyMuPDF. Skips logos and tiny bullets."""
    import warnings
    warnings.filterwarnings("ignore")
    import pymupdf
    idir = paths.find_campaign(campaign) / "images"
    idir.mkdir(parents=True, exist_ok=True)
    ipath = idir / "index.json"
    try:
        cidx = json.loads(ipath.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cidx = {}
    doc, seen = pymupdf.Document(pdf), set()
    for pno, page in enumerate(doc, 1):
        for x in page.get_images(full=True):
            xr, smask, w, h = x[0], x[1], x[2], x[3]
            if xr in seen or w < min_px or h < min_px:
                continue
            seen.add(xr)
            pix = pymupdf.Pixmap(doc, xr)
            if pix.n - pix.alpha > 3:
                pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
            if smask:
                pix = pymupdf.Pixmap(pix, pymupdf.Pixmap(doc, smask))
            iid = f"pdf-p{pno}-x{xr}"
            pix.save(idir / f"{iid}.png")
            cidx[iid] = {"file": f"{iid}.png", "title": iid, "type": "art",
                         "source": f"pdf p.{pno}", "w": pix.width, "h": pix.height}
    ipath.write_text(json.dumps(cidx, indent=1, ensure_ascii=False), encoding="utf-8")
    return ingest_dir(campaign, idir)


def search(words) -> list:
    words = [w.lower() for w in words]
    out = []
    for rec in load_index().values():
        hay = " ".join([rec.get("title", ""), rec.get("caption", ""), rec.get("kind", ""),
                        " ".join(rec.get("tags", [])), " ".join(rec.get("aliases", [])),
                        " ".join(str(v) for v in rec.get("source", {}).values()),
                        rec["id"]]).lower()
        if all(w in hay for w in words):
            out.append(rec)
    return sorted(out, key=lambda r: (r.get("title", "").lower(), r["id"]))


def use(key: str, campaign: str) -> str:
    idx = load_index()
    iid = resolve(idx, key)
    if not iid:
        raise KeyError(f"no library image '{key}'")
    rec = idx[iid]
    src = library_dir() / "files" / rec["file"]
    if not src.is_file():
        raise FileNotFoundError(f"library file missing: {src}")
    cdir = paths.find_campaign(campaign)
    if not cdir.is_dir():
        raise FileNotFoundError(f"no campaign '{campaign}'")
    idir = cdir / "images"
    idir.mkdir(exist_ok=True)
    dst = idir / rec["file"]
    if not dst.exists():
        shutil.copyfile(src, dst)
    cpath = idir / "index.json"
    try:
        cidx = json.loads(cpath.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cidx = {}
    cidx[iid] = {"file": rec["file"], "title": rec["title"], "type": rec["kind"],
                 "tags": rec["tags"], "source": _source_text(rec["source"])}
    cpath.write_text(json.dumps(cidx, indent=1, ensure_ascii=False), encoding="utf-8")
    if campaign not in rec["campaigns"]:
        rec["campaigns"] = sorted(rec["campaigns"] + [campaign])
        save_index(idx)
    return iid


def _source_text(s: dict) -> str:
    bits = [s.get("adventure", ""), f"p.{s['page']}" if s.get("page") else "", s.get("url", ""),
            s.get("credit", ""), s.get("license", ""), s.get("note", "")]
    return " ".join(b for b in bits if b)


def campaign_image(key: str, campaign: str):
    """Find an image for a campaign by library id, alias or file name, copying it in from the shared
    library when needed. Returns {"file", "title", "kind"} or None. Used to put a map under the combat grid."""
    key = (key or "").strip()
    if not key or not campaign:
        return None
    try:
        idir = paths.find_campaign(campaign) / "images"
        try:
            cidx = json.loads((idir / "index.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            cidx = {}
        rec = cidx.get(key)
        if rec and (idir / rec["file"]).is_file():
            return {"file": rec["file"], "title": rec.get("title", ""), "kind": rec.get("type", "")}
        base = os.path.basename(key)
        if base == key and (idir / base).is_file():
            return {"file": base, "title": "", "kind": ""}
        iid = use(key, campaign)
        m = load_index()[iid]
        return {"file": m["file"], "title": m.get("title", ""), "kind": m.get("kind", "")}
    except Exception:
        return None


def library_file(key: str):
    """Path of a library image by id/alias/file name, or None. Used by send.py's fallback."""
    idx = load_index()
    iid = resolve(idx, key)
    if not iid:
        return None
    p = library_dir() / "files" / idx[iid]["file"]
    return p if p.is_file() else None


def stats() -> dict:
    idx = load_index()
    kinds, camps, total = {}, {}, 0
    for r in idx.values():
        kinds[r["kind"]] = kinds.get(r["kind"], 0) + 1
        total += r.get("bytes", 0)
        for c in r.get("campaigns", []):
            camps[c] = camps.get(c, 0) + 1
    return {"images": len(idx), "bytes": total, "by_kind": kinds, "by_campaign": camps,
            "from_web": sum(1 for r in idx.values() if r["source"].get("url")),
            "library": str(library_dir())}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Persistent cross-campaign image library.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("ingest-campaign"); p.add_argument("name")
    p = sub.add_parser("add"); p.add_argument("target", help="file path or http(s) URL")
    p.add_argument("--title", default=""); p.add_argument("--caption", default="")
    p.add_argument("--tags", default=""); p.add_argument("--source", default="")
    p.add_argument("--kind", default=""); p.add_argument("--campaign", default="")
    p = sub.add_parser("search"); p.add_argument("words", nargs="*")
    p = sub.add_parser("show"); p.add_argument("id")
    p = sub.add_parser("use"); p.add_argument("id"); p.add_argument("--campaign", required=True)
    sub.add_parser("stats")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "ingest-campaign":
            r = ingest_campaign(a.name)
            print(f"ingested '{a.name}': {r['new']} new, {r['duplicate']} already in library, "
                  f"{r['skipped']} skipped, {r['bytes']/1e6:.1f} MB added")
        elif a.cmd == "add":
            t = a.target
            if re.match(r"^[a-z][a-z0-9+.-]*://", t, re.I):
                data = download(t)
                src = {"url": t, "site": urllib.parse.urlparse(t).hostname or ""}
                if a.source:
                    src["credit"] = a.source      # who made it / licence, as given
                hint = pathlib.Path(urllib.parse.urlparse(t).path).stem
            else:
                f = pathlib.Path(t).expanduser()
                data = f.read_bytes()
                src = parse_source(a.source, "")
                hint = f.stem
            iid, created = add_bytes(data, a.title, a.caption, a.kind, a.tags, src,
                                     campaign=a.campaign or None, name_hint=hint)
            print(("added " if created else "already in library: ") + iid)
        elif a.cmd == "search":
            for r in search(a.words):
                print(f"{r['id']}\t{r['kind']}\t{r['title']}\t{','.join(r['tags'])}")
        elif a.cmd == "show":
            idx = load_index()
            iid = resolve(idx, a.id)
            if not iid:
                raise KeyError(f"no library image '{a.id}'")
            out = dict(idx[iid]); out["path"] = str(library_dir() / "files" / out["file"])
            print(json.dumps(out, indent=1, ensure_ascii=False))
        elif a.cmd == "use":
            iid = use(a.id, a.campaign)
            print(f"ready in '{a.campaign}': send.py --image {iid} --caption \"...\"")
        elif a.cmd == "stats":
            print(json.dumps(stats(), indent=1, ensure_ascii=False))
    except (ValueError, KeyError, OSError, urllib.error.URLError) as e:
        print(f"image_library: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
