#!/usr/bin/env python3
"""
Render the profile-README cards as self-contained animated SVGs.

    python scripts/build_cards.py            # animated cards  -> cards/
    STATIC=1 python scripts/build_cards.py   # final frame     -> cards_static/ (+ preview.html)

Everything is built for <img> embedding (GitHub strips <script>, blocks external
requests and ignores hover inside <img>), so only these tricks are used:
  * one inline <style> block with CSS @keyframes,
  * SMIL <animate>/<animateTransform> for purely decorative extras (class "fx"),
  * fonts subset with pyftsubset to just the glyphs each card uses, embedded as
    base64 WOFF data URIs.
Under prefers-reduced-motion every CSS animation is switched off and the
decorative SMIL layer is hidden, so the card shows its final frame.

Inputs : data/contributions.json (refreshed daily by .github/workflows/cards.yml)
         assets/fonts/*.woff, assets/portrait-ascii.(png|gif|webp|jpg) [optional]
"""
from __future__ import annotations

import base64
import collections
import datetime as dt
import hashlib
import html
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATIC = os.environ.get("STATIC") == "1"
OUT = ROOT / ("cards_static" if STATIC else "cards")
DATA = ROOT / "data" / "contributions.json"
FONT_DIR = ROOT / "assets" / "fonts"

# ───────────────────────── 1. THEME — edit to match your site ─────────────────────────
# Paste the CSS variables from d:\pranesh-portfolio\app\globals.css here.
THEME = dict(
    bg="#f4f3ef",      # card background (warm paper)
    panel="#f4f3ef",   # raised surface
    panel2="#e7e6df",  # empty heat-map cell / chips / stat boxes
    line="#d8d7cf",    # borders
    text="#111114",
    muted="#6a6a72",
    brand="#2d4bff",   # bright blue: heat-map, bars, accents
    brand2="#1c34d6",  # deeper blue for small text / icons
    ok="#16a34a",      # "live" dot
    tint=0.09,         # strength of the blue wash on tiles / repo cards
)

# (family key, weight) -> file in assets/fonts. Swap files to change fonts.
FONT_FILES = {
    ("sans", 400): "space-grotesk-latin-400-normal.woff",
    ("mono", 400): "jetbrains-mono-latin-400-normal.woff",
    ("serif", 400): "instrument-serif-latin-400-normal.woff",
    ("serif_i", 400): "instrument-serif-latin-400-italic.woff",
}
FAMILY = {  # key -> (embedded family name, fallback stack)
    "sans": ("PS Sans", "system-ui,-apple-system,'Segoe UI',sans-serif"),
    "mono": ("PS Mono", "ui-monospace,SFMono-Regular,Menlo,Consolas,monospace"),
    "serif": ("PS Serif", "Georgia,'Times New Roman',serif"),
    "serif_i": ("PS Serif Italic", "Georgia,'Times New Roman',serif"),
}
MONO_ADV = 0.6  # JetBrains Mono advance width in em (used to size chips/pills)

# ───────────────────────── 2. CONTENT ─────────────────────────
PROFILE = dict(
    name="Pranesh S",
    handle="FULL-STACK DEVELOPER · AI BUILDER",
    tagline="Full-stack developer building AI-assisted software.",
    status="BCA '26 · Coimbatore",  # set "" to hide the pill
)

TILES = [  # file name, index label, title, two description lines, icon, link target lives in README
    dict(file="build", n="01", title="Build", lines=["Products and full-stack", "web projects."], icon="build"),
    dict(file="systems", n="02", title="Systems", lines=["Backend and AI", "engineering."], icon="systems"),
    dict(file="learn", n="03", title="Learn", lines=["Algorithms and", "continuous learning."], icon="learn"),
]

REPOS = [  # file, repo name (for stars/language from the JSON), title, lines, tags, live?
    dict(file="project-envoy", repo="Envoy", title="Envoy Mail",
         lines=["Gmail agent that sorts your inbox with Claude", "and asks approval before destructive actions."],
         tags=["Claude Haiku", "Approval queue", "Google Calendar"], live=False),
    dict(file="project-jobalert", repo="JobAlert-Bot", title="JobAlert Bot",
         lines=["WhatsApp job alerts on the Business Cloud API,", "with semantic matching over pgvector."],
         tags=["FastAPI", "spaCy", "pgvector"], live=True),
    dict(file="project-markdrop", repo="Markdrop", title="MarkDrop",
         lines=["Turns documents into clean Markdown, with", "OTP and OAuth sign-in."],
         tags=["React", "Vite", "Node", "Express"], live=True),
    dict(file="project-agentguard", repo="Agent-Guard", title="Agent Guard",
         lines=["Backend and AI engineering project."],
         tags=[], live=False),
]

# Stagger so the page plays top to bottom (seconds; every card starts on page load).
BASE = dict(hero=0.0, tiles=0.55, activity=1.1, repos=1.75, graph=2.9)

# ───────────────────────── helpers ─────────────────────────
T = THEME


def mix(a: str, b: str, t: float) -> str:
    ca = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
    cb = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(ca, cb))


def esc(s: str) -> str:
    return html.escape(s, quote=True)


def attrs(kind: str | None = None, delay: float = 0.0, cls: str = "") -> str:
    """class/style attributes. Animation classes are skipped entirely in STATIC mode."""
    style = ""
    if kind and not STATIC:
        cls = f"{cls} a {kind}".strip()
        style = f' style="--d:{delay:.2f}s"'
    return (f' class="{cls}"' if cls else "") + style


_subset_cache: dict[str, str] = {}


def subset_b64(src: Path, chars: str) -> str:
    text = "".join(sorted(set(chars) | {" "}))
    key = hashlib.sha1((str(src) + text).encode()).hexdigest()
    if key in _subset_cache:
        return _subset_cache[key]
    exe = shutil.which("pyftsubset")
    base = [exe] if exe else [sys.executable, "-m", "fontTools.subset"]
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "f.woff"
        subprocess.run(
            base + [str(src), f"--text={text}", "--flavor=woff", f"--output-file={out}",
                    "--layout-features=kern,liga", "--no-hinting", "--name-IDs="],
            check=True, capture_output=True,
        )
        b64 = base64.b64encode(out.read_bytes()).decode()
    _subset_cache[key] = b64
    return b64


class Card:
    def __init__(self, name: str, w: int, h: int, label: str):
        self.name, self.w, self.h, self.label = name, w, h, label
        self.defs: list[str] = []
        self.body: list[str] = []
        self.chars: dict[tuple[str, int], set[str]] = collections.defaultdict(set)

    def add(self, s: str) -> None:
        self.body.append(s)

    def text(self, x, y, s, font="sans", weight=400, size=14, fill=None, anchor="start",
             kind=None, delay=0.0, spacing=None) -> None:
        if (font, weight) not in FONT_FILES:
            raise KeyError(f"no font file configured for {(font, weight)}")
        self.chars[(font, weight)] |= set(s)
        extra = f' letter-spacing="{spacing}"' if spacing is not None else ""
        self.add(f'<text x="{x}" y="{y}" font-size="{size}" font-weight="{weight}" '
                 f'fill="{fill or T["text"]}" text-anchor="{anchor}"{extra}'
                 f'{attrs(kind, delay, font)}>{esc(s)}</text>')

    def rich(self, x, y, parts, size=22, anchor="start", kind=None, delay=0.0) -> None:
        """One <text> made of runs: parts = [(string, font, fill), ...] (e.g. serif + serif italic)."""
        spans = []
        for s, font, fill in parts:
            self.chars[(font, 400)] |= set(s)
            spans.append(f'<tspan class="{font}" fill="{fill}">{esc(s)}</tspan>')
        self.add(f'<text x="{x}" y="{y}" font-size="{size}" text-anchor="{anchor}"{attrs(kind, delay)}>'
                 f'{"".join(spans)}</text>')

    def css(self) -> str:
        faces = []
        for (font, weight), chars in sorted(self.chars.items()):
            b64 = subset_b64(FONT_DIR / FONT_FILES[(font, weight)], "".join(chars))
            faces.append(f"@font-face{{font-family:'{FAMILY[font][0]}';font-weight:{weight};"
                         f"src:url(data:font/woff;base64,{b64}) format('woff')}}")
        fams = "".join(f".{k}{{font-family:'{v[0]}',{v[1]}}}" for k, v in FAMILY.items())
        css = "".join(faces) + fams
        if not STATIC:
            css += (
                ".a{animation-duration:.75s;animation-timing-function:cubic-bezier(.2,.7,.2,1);"
                "animation-delay:var(--d,0s);animation-fill-mode:both;transform-box:fill-box}"
                ".rise{animation-name:rise}.fade{animation-name:fade}"
                ".pop{animation-name:pop;transform-origin:50% 50%}"
                ".grow{animation-name:grow;transform-origin:50% 100%}"
                ".sweep{animation-name:sweep;transform-origin:0 50%}"
                ".wipe{animation-name:wipe;transform-origin:50% 100%;transform:scaleY(0)}"
                ".scan{animation-name:scan;opacity:0}"
                "@keyframes rise{from{opacity:0;transform:translateY(12px)}to{opacity:1;transform:none}}"
                "@keyframes fade{from{opacity:0}to{opacity:1}}"
                "@keyframes pop{from{opacity:0;transform:scale(.7)}to{opacity:1;transform:none}}"
                "@keyframes grow{from{transform:scaleY(0)}to{transform:none}}"
                "@keyframes sweep{from{transform:scaleX(0)}to{transform:none}}"
                "@keyframes wipe{from{transform:scaleY(1)}to{transform:scaleY(0)}}"
                "@keyframes scan{0%{transform:translateY(0);opacity:1}90%{opacity:1}100%{transform:translateY(296px);opacity:0}}"
                "@media (prefers-reduced-motion:reduce){.a{animation:none!important}.fx{display:none}}"
            )
        return css

    def render(self) -> str:
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.w}" height="{self.h}" '
            f'viewBox="0 0 {self.w} {self.h}" role="img" aria-label="{esc(self.label)}">'
            f"<title>{esc(self.label)}</title>"
            f"<defs>{''.join(self.defs)}</defs><style>{self.css()}</style>{''.join(self.body)}</svg>"
        )


def panel(c: Card, kind="rise", delay=0.0, r=20) -> None:
    c.add(f'<rect x="0.5" y="0.5" width="{c.w - 1}" height="{c.h - 1}" rx="{r}" fill="{T["panel"]}" '
          f'stroke="{T["line"]}"{attrs(kind, delay)}/>')


# ───────────────────────── data ─────────────────────────
def load_data() -> dict:
    raw = json.loads(DATA.read_text())
    days = sorted((dt.date.fromisoformat(d["date"]), int(d["count"])) for d in raw["days"])
    if raw.get("sample"):
        print("warning: data/contributions.json is SAMPLE data; the Action will replace it", file=sys.stderr)
    return dict(days=days, repos=raw.get("repos") or {}, total=sum(c for _, c in days))


def streaks(days):
    i = len(days) - 1
    if days and days[i][1] == 0:  # today may simply not have happened yet
        i -= 1
    cur = 0
    while i >= 0 and days[i][1] > 0:
        cur += 1
        i -= 1
    longest = run = 0
    for _, n in days:
        run = run + 1 if n else 0
        longest = max(longest, run)
    return cur, longest


def monthly(days, n=12):
    last = days[-1][0]
    y, m = last.year, last.month
    keys = []
    for _ in range(n):
        keys.append((y, m))
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    keys.reverse()
    sums = {k: 0 for k in keys}
    for d, c in days:
        if (d.year, d.month) in sums:
            sums[(d.year, d.month)] += c
    return [(dt.date(y, m, 1).strftime("%b"), sums[(y, m)]) for y, m in keys]


PORTRAIT_PX = 560        # embedded size (px); display size is set in hero()


def ink_portrait() -> tuple[str, int, int] | None:
    """assets/portrait-ascii.(png|gif|webp|jpg): your dotted ASCII portrait on white. For a GIF the last
    frame is used. White becomes transparent and dark becomes ink, so it sits on any card colour."""
    p = next((q for e in ("png", "gif", "webp", "jpg", "jpeg") if (q := ROOT / "assets" / f"portrait-ascii.{e}").exists()), None)
    if p is None:
        return None
    try:
        from PIL import Image
    except ImportError:
        print("warning: Pillow missing, skipping portrait (py -m pip install pillow)", file=sys.stderr)
        return None
    im = Image.open(p)
    if getattr(im, "n_frames", 1) > 1:
        im.seek(im.n_frames - 1)
    g = im.convert("L")
    g = g.resize((PORTRAIT_PX, round(PORTRAIT_PX * g.height / g.width)), Image.LANCZOS)
    ink = tuple(int(T["text"][i:i + 2], 16) for i in (1, 3, 5))
    out = Image.new("RGBA", g.size, ink + (0,))
    out.putalpha(g.point(lambda v: min(255, int((255 - v) * 1.1))))
    buf = io.BytesIO()
    out.save(buf, "PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode(), g.width, g.height


# ───────────────────────── cards ─────────────────────────
def hero() -> Card:
    c = Card("hero", 900, 310, f"{PROFILE['name']}: {PROFILE['tagline']}")
    b = BASE["hero"]
    c.add(f'<rect x="0.5" y="0.5" width="899" height="309" rx="22" fill="{T["bg"]}" stroke="{T["line"]}"/>')
    # eyebrow
    c.add(f'<rect x="56" y="66" width="9" height="9" rx="2" fill="{T["brand"]}"{attrs("pop", b + 0.05)}/>')
    c.text(76, 75, PROFILE["handle"], font="mono", size=12, fill=T["muted"], kind="rise", delay=b + 0.05, spacing="1.5")
    c.text(52, 170, PROFILE["name"], font="serif", size=96, kind="rise", delay=b + 0.18, spacing="-1")
    c.add(f'<rect x="56" y="190" width="132" height="5" rx="2.5" fill="{T["brand"]}"{attrs("sweep", b + 0.55)}/>')
    c.text(56, 238, PROFILE["tagline"], size=18, fill=T["muted"], kind="rise", delay=b + 0.7)
    if PROFILE["status"]:
        w = round(len(PROFILE["status"]) * 12 * MONO_ADV + 46)
        c.add(f'<g{attrs("rise", b + 0.85)}><rect x="56" y="258" width="{w}" height="30" rx="15" '
              f'fill="{T["panel2"]}" stroke="{T["line"]}"/><circle cx="74" cy="273" r="4" fill="{T["ok"]}"/>')
        if not STATIC:
            c.add(f'<g class="fx"><circle cx="74" cy="273" r="4" fill="{T["ok"]}" opacity=".6">'
                  '<animate attributeName="r" values="4;10;4" dur="2.4s" repeatCount="indefinite"/>'
                  '<animate attributeName="opacity" values=".6;0;.6" dur="2.4s" repeatCount="indefinite"/></circle></g>')
        c.add("</g>")
        c.text(88, 277, PROFILE["status"], font="mono", size=12, fill=T["muted"], kind="rise", delay=b + 0.85)

    # portrait: your ASCII image, revealed top to bottom (or a monogram when there is none)
    pic = ink_portrait()
    if pic:
        uri, pw, ph = pic
        dh = 296
        dw = round(dh * pw / ph)
        x0, y0 = 880 - dw, 7
        c.defs.append(
            f'<linearGradient id="pf"><stop offset="0" stop-color="#fff" stop-opacity="0"/>'
            f'<stop offset=".16" stop-color="#fff" stop-opacity="1"/></linearGradient>'
            f'<mask id="pm" maskUnits="userSpaceOnUse" x="{x0}" y="{y0}" width="{dw}" height="{dh}">'
            f'<rect x="{x0}" y="{y0}" width="{dw}" height="{dh}" fill="url(#pf)"/></mask>')
        c.add(f'<image href="{uri}" x="{x0}" y="{y0}" width="{dw}" height="{dh}" mask="url(#pm)"/>')
        if not STATIC:
            d, dur = b + 0.3, 3.4
            ease = "animation-timing-function:cubic-bezier(.45,0,.25,1)"
            # a card-coloured cover shrinks toward the bottom, uncovering the portrait top to bottom
            c.add(f'<rect x="{x0 - 1}" y="{y0 - 1}" width="{dw + 2}" height="{dh + 2}" fill="{T["bg"]}" '
                  f'class="a wipe" style="--d:{d:.2f}s;animation-duration:{dur}s;{ease}"/>')
            # thin blue line riding the reveal edge
            c.add(f'<rect x="{x0 + 24}" y="{y0}" width="{dw - 24}" height="2" fill="{T["brand"]}" '
                  f'class="a scan" style="--d:{d:.2f}s;animation-duration:{dur}s;{ease}"/>')
    else:
        c.add(f'<g{attrs("pop", b + 0.3)}><circle cx="740" cy="155" r="92" fill="{T["panel2"]}" stroke="{T["brand"]}" stroke-width="2"/>')
        initials = "".join(p[0] for p in PROFILE["name"].split()[:2]).upper()
        c.text(740, 182, initials, font="serif", size=80, fill=T["brand"], anchor="middle")
        c.add("</g>")
    return c


ICONS = {  # 24×24 stroke icons
    "build": "M12 3l9 5-9 5-9-5z M3 13l9 5 9-5 M3 17.5l9 5 9-5",
    "systems": "M5 6h14 M5 6l7 12 M19 6l-7 12 M5 6m-2 0a2 2 0 104 0a2 2 0 10-4 0 M19 6m-2 0a2 2 0 104 0a2 2 0 10-4 0 M12 18m-2 0a2 2 0 104 0a2 2 0 10-4 0",
    "learn": "M8 7l-5 5 5 5 M16 7l5 5-5 5 M14 4l-4 16",
}


def tile(i: int, t: dict) -> Card:
    c = Card(t["file"], 300, 190, f"{t['title']}: {' '.join(t['lines'])}")
    b = BASE["tiles"] + 0.15 * i
    c.defs.append(
        f'<linearGradient id="tg" x1="1" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{T["brand"]}" stop-opacity="{T["tint"]}"/>'
        f'<stop offset=".6" stop-color="{T["brand"]}" stop-opacity="0"/></linearGradient>'
    )
    panel(c, "rise", b, r=18)
    c.add(f'<rect x="1" y="1" width="298" height="188" rx="17" fill="url(#tg)"{attrs("rise", b)}/>')
    c.text(28, 42, t["n"], font="mono", size=13, fill=T["muted"], kind="fade", delay=b + 0.15)
    c.add(f'<g transform="translate(232 22) scale(1.8)" fill="none" stroke="{T["brand2"]}" stroke-width="1.5" '
          f'stroke-linecap="round" stroke-linejoin="round"><path d="{ICONS[t["icon"]]}"{attrs("pop", b + 0.2)}/></g>')
    c.text(28, 114, t["title"], font="serif", size=42, kind="rise", delay=b + 0.25)
    for j, line in enumerate(t["lines"]):
        c.text(28, 140 + 20 * j, line, size=14, fill=T["muted"], kind="rise", delay=b + 0.35 + 0.08 * j)
    c.add(f'<rect x="28" y="174" width="40" height="3" rx="1.5" fill="{T["brand"]}"{attrs("sweep", b + 0.55)}/>')
    c.add(f'<path d="M258 168l12-12M261 156h9v9" fill="none" stroke="{T["brand2"]}" stroke-width="2" '
          f'stroke-linecap="round" stroke-linejoin="round"{attrs("fade", b + 0.6)}/>')
    return c


def activity(data) -> Card:
    days = data["days"]
    cur, longest = streaks(days)
    active = sum(1 for _, n in days if n)
    best_day, best_n = max(days, key=lambda x: (x[1], x[0]))
    months = monthly(days)
    mx = max(v for _, v in months) or 1
    b = BASE["activity"]
    c = Card("activity", 900, 400,
             f"Contributions per month. Current streak {cur} days, {active} active days, "
             f"busiest day {best_n} contributions on {best_day:%b %d, %Y}.")
    c.defs.append(f'<linearGradient id="bar" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{mix(T["brand"], "#ffffff", .45)}"/>'
                  f'<stop offset="1" stop-color="{T["brand"]}"/></linearGradient>')
    panel(c, "rise", b)
    c.rich(36, 54, [("Shipping, ", "serif", T["text"]), ("month by month", "serif_i", T["brand"])], size=32, kind="rise", delay=b + 0.1)
    c.text(864, 52, f"last 12 months · {data['total']:,} total", font="mono", size=12, fill=T["muted"],
           anchor="end", kind="fade", delay=b + 0.2)
    stats = [
        ("CURRENT STREAK", f"{cur} day{'s' if cur != 1 else ''}", f"longest {longest} days"),
        ("ACTIVE DAYS", f"{active}", f"of the last {len(days)} days"),
        ("BUSIEST DAY", f"{best_n}", f"{best_day:%b} {best_day.day}, {best_day.year}"),
    ]
    for i, (lab, big, sub) in enumerate(stats):
        x = 36 + i * 276
        d = b + 0.3 + 0.12 * i
        c.add(f'<rect x="{x}" y="78" width="252" height="100" rx="14" fill="{T["panel2"]}" stroke="{T["line"]}"{attrs("rise", d)}/>')
        c.text(x + 20, 106, lab, font="mono", size=11, fill=T["muted"], kind="rise", delay=d + 0.05, spacing="1")
        c.text(x + 20, 150, big, font="serif", size=48, fill=T["text"], kind="rise", delay=d + 0.1)
        c.text(x + 20, 167, sub, font="mono", size=11, fill=T["brand2"], kind="rise", delay=d + 0.15)
    base_y, top_h = 352, 130
    for frac in (0.5, 1.0):
        y = base_y - top_h * frac
        c.add(f'<line x1="36" x2="864" y1="{y}" y2="{y}" stroke="{T["line"]}" stroke-dasharray="3 6" opacity=".7"{attrs("fade", b + 0.5)}/>')
    c.add(f'<line x1="36" x2="864" y1="{base_y}" y2="{base_y}" stroke="{T["line"]}"/>')
    slot = 828 / len(months)
    peak = max(range(len(months)), key=lambda k: months[k][1])
    for k, (label, v) in enumerate(months):
        cx = 36 + slot * k + slot / 2
        h = max(3.0, v / mx * top_h)
        d = b + 0.6 + 0.07 * k
        fill = T["brand2"] if k == peak and v else "url(#bar)"
        c.add(f'<rect x="{cx - 17:.1f}" y="{base_y - h:.1f}" width="34" height="{h:.1f}" rx="6" fill="{fill}"{attrs("grow", d)}/>')
        c.text(f"{cx:.1f}", f"{base_y - h - 8:.1f}", str(v), font="mono", size=11,
               fill=T["brand2"] if k == peak else T["muted"], anchor="middle", kind="fade", delay=d + 0.45)
        c.text(f"{cx:.1f}", base_y + 22, label, font="mono", size=11, fill=T["muted"], anchor="middle",
               kind="fade", delay=d)
    return c


def repo_card(i: int, r: dict, data) -> Card:
    c = Card(r["file"], 440, 212, f"{r['title']}: {' '.join(r['lines'])}")
    b = BASE["repos"] + 0.18 * i
    c.defs.append(
        f'<linearGradient id="rg" x1="1" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{T["brand"]}" stop-opacity="{T["tint"]}"/>'
        f'<stop offset=".55" stop-color="{T["brand"]}" stop-opacity="0"/></linearGradient>'
    )
    panel(c, "rise", b, r=18)
    c.add(f'<rect x="1" y="1" width="438" height="210" rx="17" fill="url(#rg)"{attrs("rise", b)}/>')
    c.text(28, 40, f"PROJECT {i + 1:02d}", font="mono", size=11, fill=T["muted"], kind="fade", delay=b + 0.15, spacing="1")
    badge = "LIVE" if r["live"] else "REPO"
    bx = 412 - len(badge) * 11 * MONO_ADV
    c.add(f'<circle cx="{bx - 12:.1f}" cy="36" r="4" fill="{T["ok"] if r["live"] else T["brand"]}"{attrs("pop", b + 0.2)}/>')
    c.text(412, 40, badge, font="mono", size=11, fill=T["muted"], anchor="end", kind="fade", delay=b + 0.2, spacing="1")
    c.text(28, 86, r["title"], font="serif", size=36, kind="rise", delay=b + 0.25)
    for j, line in enumerate(r["lines"]):
        c.text(28, 112 + 20 * j, line, size=14, fill=T["muted"], kind="rise", delay=b + 0.35 + 0.08 * j)
    x = 28
    for j, tag in enumerate(r["tags"]):
        w = round(len(tag) * 11 * MONO_ADV + 20)
        d = b + 0.55 + 0.07 * j
        c.add(f'<rect x="{x}" y="148" width="{w}" height="24" rx="12" fill="{T["panel2"]}" stroke="{T["line"]}"{attrs("pop", d)}/>')
        c.text(x + 10, 164, tag, font="mono", size=11, fill=T["brand2"], kind="pop", delay=d)
        x += w + 8
    info = data["repos"].get(r["repo"]) or {}
    fx = 28
    if info.get("language"):
        c.add(f'<circle cx="{fx + 4}" cy="193" r="4" fill="{T["brand"]}"{attrs("fade", b + 0.8)}/>')
        c.text(fx + 14, 197, info["language"], font="mono", size=12, fill=T["muted"], kind="fade", delay=b + 0.8)
        fx += 14 + round(len(info["language"]) * 12 * MONO_ADV) + 18
    if info.get("stars"):
        c.add(f'<path transform="translate({fx} 186)" d="M6 .8l1.6 3.4 3.7.4-2.8 2.5.8 3.7L6 8.9 2.7 10.8l.8-3.7L.7 4.6l3.7-.4z" '
              f'fill="{T["muted"]}"{attrs("fade", b + 0.85)}/>')
        c.text(fx + 17, 197, str(info["stars"]), font="mono", size=12, fill=T["muted"], kind="fade", delay=b + 0.85)
    return c


def graph(data) -> Card:
    days = data["days"]
    b = BASE["graph"]
    first = days[0][0]
    off = (first.weekday() + 1) % 7  # Sunday-first rows
    cols = (len(days) + off - 1) // 7 + 1
    x0, y0, step = 52, 96, 15
    h = y0 + 7 * step + 50
    c = Card("graph", 900, h, f"Contribution graph: {data['total']:,} contributions in the last year")
    panel(c, "rise", b)
    c.rich(36, 48, [("Commits, ", "serif", T["text"]), ("day by day", "serif_i", T["brand"])], size=30, kind="rise", delay=b + 0.1)
    c.text(864, 46, f"{data['total']:,} contributions in the last year", font="mono", size=12, fill=T["muted"],
           anchor="end", kind="fade", delay=b + 0.2)
    nz = sorted(n for _, n in days if n)
    q = [nz[int(len(nz) * f)] for f in (0.25, 0.5, 0.75)] if nz else [1, 2, 3]
    levels = [T["panel2"]] + [mix(T["panel2"], T["brand"], t) for t in (0.3, 0.52, 0.76, 1.0)]

    def level(n):
        return 0 if n == 0 else 1 if n <= q[0] else 2 if n <= q[1] else 3 if n <= q[2] else 4

    for lab, row in (("Mon", 1), ("Wed", 3), ("Fri", 5)):
        c.text(44, y0 + row * step + 10, lab, font="mono", size=10, fill=T["muted"], anchor="end", kind="fade", delay=b + 0.3)
    last_label_col = -9
    colcells: dict[int, list[str]] = collections.defaultdict(list)
    for idx, (d, n) in enumerate(days):
        col, row = (idx + off) // 7, (idx + off) % 7
        colcells[col].append(f'<rect x="{x0 + col * step}" y="{y0 + row * step}" width="12" height="12" rx="3" '
                             f'fill="{levels[level(n)]}"><title>{n} on {d:%b} {d.day}</title></rect>')
        if d.day <= 7 and row == 0 or (idx == 0):
            if col - last_label_col >= 4 and col < cols - 1:
                c.text(x0 + col * step, y0 - 10, d.strftime("%b"), font="mono", size=10, fill=T["muted"], kind="fade", delay=b + 0.3)
                last_label_col = col
    for col in range(cols):
        c.add(f'<g{attrs("fade", b + 0.35 + 0.012 * col)}>{"".join(colcells[col])}</g>')
    ly = y0 + 7 * step + 26
    more_x = 864 - round(4 * 10 * MONO_ADV)          # "More" starts here, ends at 864
    sq0 = more_x - 8 - (4 * 15 + 12)                  # five squares, 8px gap before "More"
    ld = b + 1.0
    c.text(sq0 - 8, ly + 9, "Less", font="mono", size=10, fill=T["muted"], anchor="end", kind="fade", delay=ld)
    for k in range(5):
        c.add(f'<rect x="{sq0 + k * 15}" y="{ly}" width="12" height="12" rx="3" fill="{levels[k]}"{attrs("fade", ld)}/>')
    c.text(more_x, ly + 9, "More", font="mono", size=10, fill=T["muted"], kind="fade", delay=ld)
    return c


# ───────────────────────── main ─────────────────────────
def main() -> None:
    data = load_data()
    cards = [hero()]
    cards += [tile(i, t) for i, t in enumerate(TILES)]
    cards += [activity(data)]
    cards += [repo_card(i, r, data) for i, r in enumerate(REPOS)]
    cards += [graph(data)]
    OUT.mkdir(exist_ok=True)
    for c in cards:
        svg = c.render()
        (OUT / f"{c.name}.svg").write_text(svg, encoding="utf-8")
        print(f"{c.name + '.svg':26s}{len(svg) / 1024:7.1f} KB")
    if STATIC:
        write_preview()
        print(f"\nSTATIC frame written to {OUT}/ — open {OUT / 'preview.html'} and screenshot it.")


def write_preview() -> None:
    img = lambda n, w, a="": f'<img src="{n}.svg" width="{w}" alt="{a}">'  # noqa: E731
    tiles = "".join(img(t["file"], "32.5%") for t in TILES)
    repos = "".join(img(r["file"], "49%") for r in REPOS)
    (OUT / "preview.html").write_text(
        "<!doctype html><meta charset=utf-8><title>README preview</title>"
        "<style>body{margin:0;background:#0d1117;display:flex;justify-content:center}"
        "main{width:830px;padding:24px 0;line-height:0}p{margin:0 0 12px;text-align:center}img{vertical-align:top}</style>"
        f"<main><p>{img('hero', '100%')}</p><p>{tiles}</p><p>{img('activity', '100%')}</p>"
        f"<p>{repos}</p><p>{img('graph', '100%')}</p></main>", encoding="utf-8")


if __name__ == "__main__":
    main()
