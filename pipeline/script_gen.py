"""Topic selection and the full video package (script, scenes, titles, tags...) via the LLM router."""
import json
from typing import List

from pydantic import BaseModel, Field, ValidationError, field_validator

from . import config
from .llm import chat_json


class Scene(BaseModel):
    text_overlay: str = Field(max_length=60)
    pixabay_keywords: str


class Package(BaseModel):
    hook: str
    script: str
    scenes: List[Scene]
    title_youtube: str
    description: str
    tags: List[str]
    hashtags: List[str]
    thumbnail_text: str

    @field_validator("scenes")
    @classmethod
    def scene_count(cls, v):
        if not 4 <= len(v) <= 9:
            raise ValueError("need 4-9 scenes")
        return v

    @field_validator("title_youtube")
    @classmethod
    def title_len(cls, v):
        return v[:95]

    @field_validator("script")
    @classmethod
    def script_len(cls, v):
        words = len(v.split())
        if not 90 <= words <= 175:
            raise ValueError(f"script must be 110-150 words, got {words}")
        return v


PICK_SYSTEM = (
    "You are the editor of a YouTube Shorts channel that explains tech and AI news in 60 seconds "
    "for viewers in India and Asia. You pick stories with the highest chance of clicks and shares: "
    "big brands, money, phones, AI tools people use, jobs, government tech policy, India impact. "
    "Avoid politics, crime, deaths, and stories with no clear 'why it matters'. Respond with JSON only."
)

WRITE_SYSTEM = (
    "You write viral 50-second YouTube Shorts scripts about tech news for an Indian and Asian audience. "
    "Rules: use ONLY facts from the provided source text; never invent numbers, quotes, dates or prices. "
    "If a fact is uncertain, leave it out. Simple Indian English, short punchy sentences, no emojis in the script. "
    "Structure: hook (first sentence, under 8 words, creates curiosity) -> what happened -> why it matters "
    "for people in India/Asia -> one-line opinion -> question to drive comments. Respond with JSON only."
)

WRITE_FORMAT = """Return JSON with exactly these keys:
{
  "hook": "under 8 words, shown on screen in the first 2 seconds",
  "script": "110-150 words of voiceover, starting with the hook sentence",
  "scenes": [{"text_overlay": "2-5 word on-screen label", "pixabay_keywords": "2-3 generic stock-video words, e.g. 'smartphone city night'"}],  // 6-8 scenes, in script order
  "title_youtube": "under 60 characters, main keyword first, curiosity, no clickbait lies, may end with #shorts",
  "description": "3-5 sentences summarising the story, then a line 'Sources:' (leave the URLs out, they are added later)",
  "tags": ["10-15 search keywords people in India would type"],
  "hashtags": ["#shorts", "3-5 more niche hashtags"],
  "thumbnail_text": "3-4 bold words"
}
Stock keywords must be generic (no brand names, no people names) so stock footage exists."""


def pick_topic(clusters):
    listing = "\n".join(
        f"{i}. {c['title']} (score {c['score']}, {len(c['items'])} articles, "
        f"countries: {', '.join(sorted({x['country'] for x in c['items']}))})"
        for i, c in enumerate(clusters)
    )
    data, _ = chat_json(PICK_SYSTEM, (
        f"Today's candidate stories:\n{listing}\n\n"
        'Return {"ranking": [indexes best first, all of them], "reason": "one sentence on the top pick"}'
    ), temperature=0.3)
    order = [int(i) for i in data.get("ranking", []) if str(i).isdigit() and int(i) < len(clusters)]
    seen = set()
    order = [i for i in order if not (i in seen or seen.add(i))]
    order += [i for i in range(len(clusters)) if i not in order]
    print(f"[script] pick reason: {data.get('reason')}")
    return [clusters[i] for i in order]


def write_package(title, article, shorter=False):
    extra = "\nIMPORTANT: keep the script to 105-120 words." if shorter else ""
    user = f"Story headline: {title}\n\nSource text:\n{article[:7000]}\n\n{WRITE_FORMAT}{extra}"
    last_err = None
    for _ in range(3):
        msg = user if not last_err else user + f"\n\nYour previous answer was invalid: {last_err}. Fix it."
        data, provider = chat_json(WRITE_SYSTEM, msg)
        try:
            pkg = Package.model_validate(data)
            pkg.hashtags = [h if h.startswith("#") else f"#{h}" for h in pkg.hashtags]
            if "#shorts" not in [h.lower() for h in pkg.hashtags]:
                pkg.hashtags.insert(0, "#shorts")
            return pkg, provider
        except ValidationError as e:
            last_err = json.dumps(e.errors(include_url=False, include_context=False))[:800]
            print(f"[script] validation failed: {last_err}")
    raise RuntimeError(f"LLM could not produce a valid package: {last_err}")


def build_description(pkg, source_urls):
    lines = [pkg.description.strip()]
    if source_urls:
        if "Sources:" not in lines[0]:
            lines.append("\nSources:")
        lines += [f"- {u}" for u in source_urls]
    lines.append("\nThis video uses AI-generated narration and visuals. Facts are taken from the sources above.")
    if config.AFFILIATE_FOOTER:
        lines.append("\n" + config.AFFILIATE_FOOTER)
    lines.append("\n" + " ".join(pkg.hashtags))
    return "\n".join(lines)[:4900]
