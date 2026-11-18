"""Build the architecture diagram (HTML) and render it to PNG with Chromium.

Usage: python assets/architecture/build.py
Writes assets/architecture-en.png and assets/architecture-es.png (used by the READMEs).
Needs Playwright with Chromium (`python -m pip install playwright` + `python -m playwright install chromium`).

Logos come from icons/ (devicon, MIT; simple-icons, CC0) and are embedded as data URIs.
Fonts load from Google Fonts at render time.
"""

from __future__ import annotations

import base64
import re
from pathlib import Path

DIR = Path(__file__).resolve().parent
ICONS = DIR / "icons"
WIDTH, HEIGHT = 1728, 850

FONTS = (
    '<link rel="preconnect" href="https://fonts.googleapis.com">'
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
    '<link href="https://fonts.googleapis.com/css2?family=Oswald:wght@500;600;700'
    '&family=Barlow:wght@400;500;600;700&family=Barlow+Condensed:wght@500;600;700&display=block" rel="stylesheet">'
)


# ── Icons ────────────────────────────────────────────────────────────────────

def uri(svg: str) -> str:
    return "data:image/svg+xml;base64," + base64.b64encode(svg.encode("utf-8")).decode("ascii")


def dev(name: str) -> str:
    """Full-color devicon logo."""
    return uri((ICONS / f"dev_{name}.svg").read_text(encoding="utf-8"))


def si(name: str, color: str) -> str:
    """Monochrome simple-icons logo, painted in the given color."""
    svg = (ICONS / f"si_{name}.svg").read_text(encoding="utf-8")
    svg = re.sub(r"<title>.*?</title>", "", svg)
    return uri(svg.replace("<svg ", f'<svg fill="{color}" ', 1))


def line_icon(body: str, color: str, view: str = "0 0 64 64", width: float = 3.2) -> str:
    return uri(
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{view}" fill="none" stroke="{color}" '
        f'stroke-width="{width}" stroke-linecap="round" stroke-linejoin="round">{body}</svg>'
    )


RUNNER = line_icon(
    '<circle cx="40" cy="9" r="5.5"/>'
    '<path d="M36 18L28 34l10 7-4 17"/><path d="M28 34l-11 3-6 11"/>'
    '<path d="M35 21l-11 1-6 8"/><path d="M37 21l8 8 10-2"/>', "#C17BFF", width=3.6)
MOON = line_icon('<path d="M46 41A21 21 0 1 1 25 13a16 16 0 0 0 21 28z"/>', "#C17BFF")
SCALE = line_icon(
    '<rect x="8" y="12" width="48" height="44" rx="10"/><path d="M20 26a14 14 0 0 1 24 0"/>'
    '<path d="M32 26l5-6"/>', "#C17BFF")
BAND = uri(
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 84" fill="none" stroke="#33D6F2" stroke-width="3" '
    'stroke-linejoin="round" stroke-linecap="round">'
    '<path d="M23 3h18l3 19H20z"/><path d="M20 62h24l-3 19H23z"/>'
    '<rect x="15" y="22" width="34" height="40" rx="13" fill="#05252e"/>'
    '<path d="M21 43h6l3-8 5 15 3-7h6" stroke="#7CF0FF" stroke-width="2.6"/></svg>')
PHONE = uri(
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 48 80" fill="none" stroke="#2AD0E0" stroke-width="3" '
    'stroke-linejoin="round" stroke-linecap="round">'
    '<rect x="4" y="3" width="40" height="74" rx="8" fill="#032026"/><path d="M18 9h12"/>'
    '<path d="M12 46h6l3-7 4 13 3-6h8" stroke="#7CF0FF" stroke-width="2.4"/><circle cx="24" cy="68" r="2.5"/></svg>')
CLOUD = line_icon(
    '<path d="M24 50h40a14 14 0 0 0 1-28 20 20 0 0 0-38-5A16 16 0 0 0 24 50z"/>', "#FF8A3D", "0 0 90 60", 3)
ZIP = line_icon(
    '<path d="M14 4h26l12 12v44H14z"/><path d="M40 4v12h12"/><path d="M27 4v6h-4v6h4v6h-4v6h4"/>'
    '<rect x="21" y="34" width="10" height="10" rx="2"/>', "#FF8A3D")
SHIELD = line_icon(
    '<path d="M32 5l22 8v16c0 15-9 25-22 30C19 54 10 44 10 29V13z"/><path d="M22 31l7 7 13-14"/>', "#46E08E")
LOCK = line_icon(
    '<rect x="12" y="28" width="40" height="30" rx="6"/><path d="M20 28v-8a12 12 0 0 1 24 0v8"/>'
    '<circle cx="32" cy="42" r="4"/><path d="M32 46v5"/>', "#C46BFF", width=3.6)
ARROW = uri(
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 48 40"><defs>'
    '<linearGradient id="a" x1="0" x2="1"><stop offset="0" stop-color="#1663F5"/>'
    '<stop offset="1" stop-color="#3FA2FF"/></linearGradient></defs>'
    '<path d="M2 13h23V2l21 18-21 18V27H2z" fill="url(#a)"/></svg>')

# Pill colors by kind of hop
PILL = {"auto": "#2FD27C", "manual": "#FFB020", "command": "#3FA2FF", "demand": "#FF7A5C"}


# ── Text (English / Spanish) ─────────────────────────────────────────────────

TEXT = {
    "en": {
        "title": 'PERSONAL <span class="c">HEALTH DATA</span> PIPELINE',
        "sub": "From your wrist to Claude: wearable data in a private database you can talk to",
        "cards": [
            ("Your Activity", ["Walks, treadmill and strength", "Sleep, every night", "Weigh-ins"],
             ("SENSORS · CONTINUOUS", "auto"), ("sleep", "weight")),
            ("Mi Band", ["Optical heart-rate sensor", "Accelerometer", "Stress and sleep stages estimated by Xiaomi",
                         "Workouts tracked on the wrist"], ("BLUETOOTH LE · AUTO", "auto"), None),
            ("Phone", ["Mi Fitness app", "Syncs with the band over Bluetooth Low Energy",
                       "Uploads to your Xiaomi account"], ("CLOUD SYNC · AUTO", "auto"), None),
            ("Xiaomi Cloud", ["Full history in your account", "Data export from account.xiaomi.com",
                              "CSV files in a password-protected ZIP", "Arrives by email in days"],
             ("DATA EXPORT · MANUAL", "manual"), None),
            ("ETL · Python", ["Only 3 CSVs read, GPX never", "Account and device ids scrubbed",
                              "Deterministic ids: re-runs never duplicate", "Logs show counts only"],
             ("REFRESH.PY · 1 COMMAND", "command"), None),
            ("Private Database", ["Schema health, not exposed to the API", "Row level security on every table",
                                  "Write-only loader, read-only reader", "Daily and weekly views"],
             ("MCP · ON DEMAND", "demand"), None),
            ("Claude", ["Read-only MCP connector", "4 fixed tools, no free-form SQL", "Ask from the web or your phone",
                        "Weekly Markdown digest too"], ("ANSWERS · SECONDS", "demand"), None),
        ],
        "bar1": "Private by design · public code, private data",
        "bar2": "<b>One manual hop:</b> the Xiaomi export. Everything after it runs in seconds."
                "<br>Exports, normalized records and digests never leave your machine or your own database.",
    },
    "es": {
        "title": 'PIPELINE PERSONAL DE <span class="c">DATOS DE SALUD</span>',
        "sub": "De tu muñeca a Claude: los datos de tu wearable en una base privada con la que podés charlar",
        "cards": [
            ("Tu actividad", ["Caminatas, cinta y fuerza", "El sueño, todas las noches", "Pesajes"],
             ("SENSORES · CONTINUO", "auto"), ("sueño", "peso")),
            ("Mi Band", ["Sensor óptico de pulso", "Acelerómetro", "Estrés y etapas del sueño estimados por Xiaomi",
                         "Entrenamientos registrados en la muñeca"], ("BLUETOOTH LE · AUTOMÁTICO", "auto"), None),
            ("Celular", ["App Mi Fitness", "Sincroniza con la banda por Bluetooth Low Energy",
                         "Sube los datos a tu cuenta de Xiaomi"], ("SUBIDA · AUTOMÁTICA", "auto"), None),
            ("Nube de Xiaomi", ["Todo tu historial en la cuenta", "Export de datos en account.xiaomi.com",
                                "CSV en un ZIP con contraseña", "Llega por mail en unos días"],
             ("EXPORT · MANUAL", "manual"), None),
            ("ETL · Python", ["Lee solo 3 CSV, nunca el GPX", "Borra ids de cuenta y de dispositivo",
                              "Ids determinísticos: repetir nunca duplica", "Los logs muestran solo conteos"],
             ("REFRESH.PY · 1 COMANDO", "command"), None),
            ("Base privada", ["Schema health, sin acceso por la API", "RLS en todas las tablas",
                              "Un usuario escribe, otro solo lee", "Vistas diarias y semanales"],
             ("MCP · CUANDO PREGUNTÁS", "demand"), None),
            ("Claude", ["Conector MCP de solo lectura", "4 herramientas fijas, sin SQL libre",
                        "Preguntás desde la web o el celular", "También un digest semanal en Markdown"],
             ("RESPUESTAS · SEGUNDOS", "demand"), None),
        ],
        "bar1": "Privado desde el diseño · código público, datos privados",
        "bar2": "<b>Un solo salto manual:</b> el export de Xiaomi. Todo lo que sigue tarda segundos."
                "<br>Los exports, los registros normalizados y los digests nunca salen de tu compu ni de tu base.",
    },
}


def logos(i: int, captions) -> str:
    """Logo block for card i (0-based)."""
    if i == 0:
        a, b = captions
        return (f'<img src="{RUNNER}" style="width:112px;margin-top:2px">'
                f'<div class="row" style="gap:30px;margin-top:10px">'
                f'<div><img src="{MOON}" style="width:50px"><div class="cap small">{a}</div></div>'
                f'<div><img src="{SCALE}" style="width:50px"><div class="cap small">{b}</div></div></div>')
    if i == 1:
        return (f'<img src="{BAND}" style="width:96px;filter:drop-shadow(0 0 12px rgba(51,214,242,.55))">'
                f'<img src="{si("xiaomi", "#FF6900")}" style="width:38px;margin-top:12px">')
    if i == 2:
        return (f'<img src="{PHONE}" style="width:64px;filter:drop-shadow(0 0 10px rgba(42,208,224,.5))">'
                f'<div class="row" style="gap:20px;margin-top:12px">'
                f'<img src="{si("bluetooth", "#3FA2FF")}" style="width:40px;filter:drop-shadow(0 0 8px rgba(63,162,255,.6))">'
                f'<img src="{si("android", "#3DDC84")}" style="width:42px"></div>')
    if i == 3:
        return (f'<div style="position:relative;width:132px;height:88px">'
                f'<img src="{CLOUD}" style="width:132px;filter:drop-shadow(0 0 10px rgba(255,138,61,.5))">'
                f'<img src="{si("xiaomi", "#FF6900")}" style="position:absolute;left:48px;top:32px;width:34px"></div>'
                f'<img src="{ZIP}" style="width:52px;margin-top:6px">')
    if i == 4:
        return (f'<img src="{dev("python")}" style="width:74px">'
                f'<img src="{SHIELD}" style="width:58px;margin-top:14px;filter:drop-shadow(0 0 8px rgba(70,224,142,.5))">')
    if i == 5:
        return (f'<img src="{dev("postgresql")}" style="width:64px">'
                f'<div class="cap" style="font-size:19px">PostgreSQL</div>'
                f'<img src="{dev("supabase")}" style="width:46px;margin-top:6px;filter:drop-shadow(0 0 10px rgba(62,207,142,.5))">'
                f'<div class="cap" style="font-size:17px">Supabase</div>')
    return (f'<img src="{si("claude", "#D97757")}" style="width:92px;filter:drop-shadow(0 0 14px rgba(217,119,87,.6))">'
            f'<img src="{si("markdown", "#F4F4F5")}" style="width:58px;margin-top:16px">')


CARD_COLORS = [("#A855F7", "#C17BFF"), ("#1ECBEA", "#33D6F2"), ("#12B5CC", "#2AD0E0"), ("#FF7A1A", "#FF8A3D"),
               ("#2FD27C", "#46E08E"), ("#23C38A", "#3FE0A5"), ("#E06A4A", "#FF8A68")]

CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
html, body { width: %(W)spx; height: %(H)spx; background: #0d1117; overflow: hidden; }
body { -webkit-font-smoothing: antialiased; color: #F4F4F5; font-family: 'Barlow', sans-serif; }
#canvas { position: relative; width: %(W)spx; height: %(H)spx;
  background: radial-gradient(ellipse 70%% 55%% at 50%% 40%%, #131a2b 0%%, #0d1117 70%%); }
h1 { position: absolute; top: 12px; left: 0; width: 100%%; text-align: center;
  font: 600 50px/1.1 'Oswald', sans-serif; letter-spacing: 1.2px; color: #fff; }
h1 .c { color: #23AEF5; text-shadow: 0 0 16px rgba(35,174,245,.35); }
.sub { position: absolute; top: 80px; left: 0; width: 100%%; text-align: center;
  font: 400 23px 'Barlow', sans-serif; letter-spacing: .3px; color: #E4E4E7; }
.arrow { position: absolute; width: 44px; height: 37px; filter: drop-shadow(0 0 6px rgba(40,140,255,.85)); }
.card { position: absolute; top: 152px; width: 216px; height: 540px; border: 2px solid var(--c);
  border-radius: 14px; background: linear-gradient(180deg, color-mix(in srgb, var(--c) 6%%, #000) 0%%, #000 55%%);
  box-shadow: 0 0 7px var(--c), 0 0 18px color-mix(in srgb, var(--c) 40%%, transparent),
              inset 0 0 20px color-mix(in srgb, var(--c) 14%%, transparent);
  display: flex; flex-direction: column; align-items: center; padding: 34px 10px 14px; }
.num { position: absolute; top: -24px; left: 50%%; transform: translateX(-50%%); width: 46px; height: 46px;
  border-radius: 50%%; border: 3px solid var(--c); background: #000; color: #fff;
  font: 600 27px/1 'Oswald', sans-serif; display: flex; align-items: center; justify-content: center;
  box-shadow: 0 0 10px var(--c), inset 0 0 6px color-mix(in srgb, var(--c) 40%%, transparent); }
.title { color: var(--t); font: 600 22px/1.22 'Barlow Condensed', sans-serif; letter-spacing: .7px;
  text-transform: uppercase; text-align: center; min-height: 54px; display: flex; align-items: center;
  text-shadow: 0 0 10px color-mix(in srgb, var(--c) 45%%, transparent); }
.logos { display: flex; flex-direction: column; align-items: center; gap: 2px; margin-top: 10px; }
.row { display: flex; align-items: center; justify-content: center; }
.row > div { display: flex; flex-direction: column; align-items: center; }
.cap { font: 500 20px 'Barlow', sans-serif; color: #fff; text-align: center; line-height: 1.1; }
.cap.small { font-size: 15px; color: #E4E4E7; margin-top: 2px; }
ul { list-style: none; margin-top: auto; width: 100%%; padding: 0 4px 0 8px; }
li { position: relative; padding-left: 15px; font: 400 15.6px/1.3 'Barlow', sans-serif; color: #F4F4F5;
  margin-bottom: 6px; }
li::before { content: ""; position: absolute; left: 1px; top: 8px; width: 5px; height: 5px; border-radius: 50%%;
  background: #fff; }
.pill { margin-top: 12px; width: 100%%; text-align: center; padding: 7px 4px; border-radius: 999px;
  border: 1.6px solid var(--p); color: var(--p); background: color-mix(in srgb, var(--p) 12%%, #000);
  font: 600 14.5px/1 'Barlow Condensed', sans-serif; letter-spacing: .9px;
  box-shadow: 0 0 8px color-mix(in srgb, var(--p) 45%%, transparent); }
.lines { position: absolute; left: 0; top: 0; width: 100%%; height: 100%%; pointer-events: none; }
.bar { position: absolute; top: 750px; left: 0; width: 100%%; display: flex; align-items: center;
  justify-content: center; gap: 22px; }
.bar img { width: 62px; filter: drop-shadow(0 0 8px rgba(196,107,255,.6)); }
.bar .t1 { font: 600 26px/1.15 'Barlow Condensed', sans-serif; letter-spacing: 1.2px; color: #D27BFF;
  text-transform: uppercase; text-shadow: 0 0 10px rgba(196,107,255,.45); }
.bar .t2 { font: 400 17.5px/1.35 'Barlow', sans-serif; color: #E4E4E7; margin-top: 4px; }
.bar .t2 b { font-weight: 600; color: #fff; }
""" % {"W": WIDTH, "H": HEIGHT}


def page(lang: str) -> str:
    t = TEXT[lang]
    width, gap, left = 216, 30, 16
    cards, arrows, centers = [], [], []
    for i, (title, items, (pill, kind), captions) in enumerate(t["cards"]):
        x = left + i * (width + gap)
        centers.append(x + width / 2)
        border, title_color = CARD_COLORS[i]
        bullets = "".join(f"<li>{item}</li>" for item in items)
        cards.append(
            f'<div class="card" style="left:{x}px;--c:{border};--t:{title_color}">'
            f'<div class="num">{i + 1}</div><div class="title">{title}</div>'
            f'<div class="logos">{logos(i, captions)}</div><ul>{bullets}</ul>'
            f'<div class="pill" style="--p:{PILL[kind]}">{pill}</div></div>'
        )
        if i < len(t["cards"]) - 1:
            arrows.append(f'<img class="arrow" src="{ARROW}" style="left:{x + width + gap / 2 - 22}px;top:404px">')

    violet = "#B45CFF"
    lines = []
    for i, cx in enumerate(centers):
        color = CARD_COLORS[i][0]
        lines.append(f'<line x1="{cx}" y1="694" x2="{cx}" y2="708" stroke="{color}" stroke-width="2" stroke-dasharray="3 3"/>')
        lines.append(f'<circle cx="{cx}" cy="716" r="7.5" fill="#000" stroke="{color}" stroke-width="2"/>')
        lines.append(f'<circle cx="{cx}" cy="716" r="2.2" fill="{color}"/>')
    lines.append(f'<line x1="{centers[0]}" y1="730" x2="{centers[-1]}" y2="730" stroke="{violet}" '
                 f'stroke-width="2.2" stroke-dasharray="7 6"/>')
    for cx in centers:
        lines.append(f'<line x1="{cx}" y1="724" x2="{cx}" y2="730" stroke="{violet}" stroke-width="2"/>')
    svg = f'<svg class="lines" viewBox="0 0 {WIDTH} {HEIGHT}">{"".join(lines)}</svg>'

    bar = (f'<div class="bar"><img src="{LOCK}"><img src="{si("github", "#FFFFFF")}" style="width:50px;'
           f'filter:drop-shadow(0 0 8px rgba(255,255,255,.35))">'
           f'<div><div class="t1">{t["bar1"]}</div><div class="t2">{t["bar2"]}</div></div></div>')

    return ('<!doctype html><html lang="' + lang + '"><head><meta charset="utf-8"><title>Architecture</title>'
            + FONTS + f"<style>{CSS}</style></head><body><div id=\"canvas\">"
            f'<h1>{t["title"]}</h1><p class="sub">{t["sub"]}</p>'
            + svg + "".join(cards) + "".join(arrows) + bar + "</div></body></html>")


def render() -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            tab = browser.new_page(viewport={"width": WIDTH, "height": HEIGHT}, device_scale_factor=1)
            for lang in TEXT:
                html = DIR / f"architecture-{lang}.html"
                html.write_text(page(lang), encoding="utf-8")
                tab.goto(html.as_uri(), wait_until="networkidle")
                tab.evaluate("document.fonts.ready.then(() => true)")
                tab.wait_for_timeout(300)
                out = DIR.parent / f"architecture-{lang}.png"
                tab.screenshot(path=str(out), clip={"x": 0, "y": 0, "width": WIDTH, "height": HEIGHT})
                print(f"{out.name}: {out.stat().st_size / 1024:.0f} KB")
        finally:
            browser.close()


if __name__ == "__main__":
    render()
