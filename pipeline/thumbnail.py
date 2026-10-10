"""Render the cover design (templates/thumb.html) to JPEGs with Playwright (Chromium).
thumb_yt.jpg (1280x720) -> YouTube thumbnail; thumb_vertical.jpg (1080x1920) -> Facebook Reel cover.
The video's own first frame uses the same design (renderer/src/NewsShort.tsx), which is what the
Instagram and YouTube Shorts feeds show."""
import base64
import html
import re
from pathlib import Path

from . import config

SIZES = {"thumb_yt.jpg": (1280, 720), "thumb_vertical.jpg": (1080, 1920)}


def highlight_index(words):
    """The word to highlight: a number/percentage if there is one, otherwise the first word (usually the name)."""
    for i, w in enumerate(words):
        if re.search(r"\d", w):
            return i
    return 0


def _headline(text):
    words = html.escape(text.strip().upper()).split()
    if len(words) >= 2:
        i = highlight_index(words)
        words[i] = f"<em>{words[i]}</em>"
    return " ".join(words)


def render(text, background_jpg: Path, out_dir: Path, badge="NEW"):
    from playwright.sync_api import sync_playwright

    bg = "data:image/jpeg;base64," + base64.b64encode(background_jpg.read_bytes()).decode()
    template = (config.TEMPLATES / "thumb.html").read_text()
    outputs = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for name, (w, h) in SIZES.items():
            wide = w > h
            layout = {"{{SIZE}}": "15vmin" if wide else "13.5vmin",
                      "{{ALIGN}}": "flex-end" if wide else "center",
                      "{{ITEMS}}": "flex-start" if wide else "center",
                      "{{TEXTALIGN}}": "left" if wide else "center"}
            page_html = (template.replace("{{BG}}", bg).replace("{{TEXT}}", _headline(text))
                         .replace("{{BADGE}}", html.escape(badge or "NEW"))
                         .replace("{{BRAND}}", html.escape(config.CHANNEL_HANDLE)))
            for k, v in layout.items():
                page_html = page_html.replace(k, v)
            page = browser.new_page(viewport={"width": w, "height": h})
            page.set_content(page_html, wait_until="load")
            out = out_dir / name
            page.screenshot(path=str(out), type="jpeg", quality=92)
            page.close()
            outputs.append(out)
        browser.close()
    return outputs
