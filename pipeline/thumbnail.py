"""Render the HTML thumbnail template to JPEGs with Playwright (Chromium)."""
import base64
import html
from pathlib import Path

from . import config

SIZES = {"thumb_yt.jpg": (1280, 720), "thumb_vertical.jpg": (1080, 1920)}


def _headline(text):
    words = html.escape(text.strip()).split()
    if len(words) >= 2:  # highlight the last word in yellow
        words[-1] = f"<em>{words[-1]}</em>"
    return " ".join(words)


def render(text, background_jpg: Path, out_dir: Path):
    from playwright.sync_api import sync_playwright

    bg = "data:image/jpeg;base64," + base64.b64encode(background_jpg.read_bytes()).decode()
    template = (config.TEMPLATES / "thumb.html").read_text()
    outputs = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for name, (w, h) in SIZES.items():
            size = "13vmin" if w > h else "11vmin"
            page_html = (template.replace("{{BG}}", bg).replace("{{TEXT}}", _headline(text))
                         .replace("{{BRAND}}", html.escape(config.CHANNEL_HANDLE)).replace("{{SIZE}}", size))
            page = browser.new_page(viewport={"width": w, "height": h})
            page.set_content(page_html, wait_until="load")
            out = out_dir / name
            page.screenshot(path=str(out), type="jpeg", quality=90)
            page.close()
            outputs.append(out)
        browser.close()
    return outputs
