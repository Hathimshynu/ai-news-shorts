"""Build the read-only dashboard (docs/index.html) from the data files. GitHub Pages serves /docs."""
import json
from datetime import datetime, timezone

from . import analytics, config, gate


def _load(name, default):
    p = config.DATA / name
    return json.loads(p.read_text()) if p.exists() else default


def build():
    data = {
        "generated": datetime.now(timezone.utc).isoformat(),
        "published": gate.load_all(),
        "analytics": analytics.load(),
        "trends": _load("trends_latest.json", {}),
    }
    html = (config.TEMPLATES / "dashboard.html").read_text()
    blob = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    out = config.ROOT / "docs" / "index.html"
    out.parent.mkdir(exist_ok=True)
    out.write_text(html.replace("/*DATA*/{}", blob))
    print(f"[dashboard] wrote {out}")
    return out


if __name__ == "__main__":
    build()
