"""Topic selection, the full video package, and the fact-check pass (all via the free LLM router)."""
import json
import re
from typing import List, Optional

from pydantic import BaseModel, Field, ValidationError, field_validator
from rapidfuzz import fuzz

from . import config
from .llm import chat_json

CATEGORIES = [
    "Breaking AI News", "New AI Models", "New AI Features", "AI Automation Tutorials",
    "AI Tools You Should Know", "Trending Technology", "Build With AI", "AI Tips and Hidden Features",
    "AI Experiments", "Future of Technology",
]


class Scene(BaseModel):
    text_overlay: str = Field(max_length=60)
    pixabay_keywords: str
    visual_direction: str = ""


class Pronunciation(BaseModel):
    term: str
    say_as: str


class Package(BaseModel):
    category: str = "Breaking AI News"
    hooks: List[str] = []
    hook: str
    script: str
    cta: str = ""
    scenes: List[Scene]
    pronunciation: List[Pronunciation] = []
    titles: List[str] = []
    title_youtube: str
    primary_keyword: str = ""
    related_keywords: List[str] = []
    description: str
    pinned_comment: str = ""
    ig_opening: str = ""
    ig_question: str = ""
    tags: List[str]
    hashtags: List[str]
    thumbnail_text: str
    thumbnail_badge: str = "NEW"

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

    @field_validator("thumbnail_text")
    @classmethod
    def cover_words(cls, v):
        return " ".join(v.replace("#", "").split()[:5])

    @field_validator("thumbnail_badge")
    @classmethod
    def badge_words(cls, v):
        return " ".join((v or "NEW").split()[:3]).upper()[:18] or "NEW"

    @field_validator("category")
    @classmethod
    def known_category(cls, v):
        best = max(CATEGORIES, key=lambda c: fuzz.ratio(c.lower(), (v or "").lower()))
        return best

    @field_validator("script")
    @classmethod
    def script_len(cls, v):
        words = len(v.split())
        if not 80 <= words <= 175:
            raise ValueError(f"script must be 100-150 words, got {words}")
        return v

    @property
    def runtime_estimate(self):
        """Seconds at a natural ~165 words per minute (before the speed-up)."""
        return round(len(self.script.split()) / 165 * 60)


PICK_SYSTEM = (
    "You are the editor of 'Tech Talk Hathim', a short-video channel about AI and technology for viewers in "
    "India and Asia. Pick stories that give viewers a reason to watch, learn, save, share or comment: big AI "
    "releases, new models, useful features, tools people can use today, automation workflows, India impact. "
    "Prefer stories with an official/primary source. Avoid politics, crime, deaths, rumours and stories with no "
    "clear 'why it matters'. Do not pick a story only because it is trending. Respond with JSON only."
)

WRITE_SYSTEM = (
    "You write 45-55 second vertical videos for 'Tech Talk Hathim' (AI and tech, audience in India and Asia). "
    "Hard rules: use ONLY facts from the provided source text; never invent features, numbers, prices, dates, "
    "benchmarks or quotes. Say clearly if something is only announced, in beta or region-limited. If unsure, "
    "leave it out. Natural, energetic, conversational Indian English; short sentences; no emojis in the script; "
    "no generic intro like 'Hey guys'. Structure: 0-3s hook (surprising fact, relatable problem or question, "
    "truthful, not clickbait) -> 3-8s why it matters / what viewers will learn -> main content (what happened, "
    "how it works or how to use it, practical impact for India/Asia) -> one clear takeaway + ONE natural CTA. "
    "Respond with JSON only."
)

WRITE_FORMAT = """Return JSON with exactly these keys:
{
  "category": "one of: %s",
  "hooks": ["3 different hook options, each under 10 words"],
  "hook": "the best of the three (shown on screen in the first 2 seconds)",
  "script": "100-140 words of voiceover, starting with the chosen hook and ending with the CTA",
  "cta": "the single call to action used at the end, e.g. a question or 'Follow Tech Talk Hathim for daily AI updates'",
  "scenes": [{"text_overlay": "2-5 word on-screen label", "pixabay_keywords": "2-3 generic stock-video words", "visual_direction": "what the viewer should see"}],
  "pronunciation": [{"term": "hard technical name from the script", "say_as": "phonetic spelling for text-to-speech"}],
  "primary_keyword": "2-4 word phrase people actually type into Google/YouTube for this story, e.g. 'gemini free for students' or 'chatgpt agent mode'. Use the real product/company name.",
  "related_keywords": ["4-6 related search phrases people would type (include an India angle if relevant)"],
  "titles": ["3 title options, 40-60 characters, primary keyword in the first 40 characters, Title Case, specific (name + number/benefit), honest (no 'shocking', no ALL CAPS)"],
  "title_youtube": "the best of the three titles (no hashtags; #Shorts is added automatically)",
  "description": "First sentence (max 150 characters) contains the primary keyword and says what happened. Then 2-3 sentences: what it means for viewers in India/Asia and what they will learn. Use 2 of the related keywords naturally. No URLs, no hashtags.",
  "pinned_comment": "one friendly question to pin as the first comment",
  "ig_opening": "one punchy first line for the Instagram caption that includes the primary keyword",
  "ig_question": "one question that invites real discussion",
  "tags": ["10-15 search tags: the primary keyword first, then related keywords, product and company names"],
  "hashtags": ["3-5 specific hashtags, most relevant first (e.g. #ChatGPT #OpenAI #AINews); #Shorts is added automatically"],
  "thumbnail_text": "2-4 huge cover words that make people stop scrolling: the product/company name or the key number + the outcome, e.g. 'GEMINI NOW FREE', '96%% FASTER', 'CHATGPT AGENTS'. Never a full sentence. Must be true.",
  "thumbnail_badge": "1-3 word label for the cover badge, e.g. 'NEW', 'FREE', 'INDIA', 'JUST LAUNCHED', 'BETA'"
}
Use 6-8 scenes, in script order. Stock keywords must be generic (no brand or people names). Only include
pronunciation entries for names text-to-speech may say wrongly (can be an empty list).""" % ", ".join(CATEGORIES)

FACT_SYSTEM = (
    "You are a strict fact-checker for short tech videos. Compare every factual claim in the script with the "
    "source text. A claim is 'supported' only if the source text states it. Copy the exact supporting sentence "
    "from the source as evidence. Also list the other sentences but mark their type: 'cta' for calls to action "
    "(follow, subscribe, comment) and 'opinion' for commentary that states no checkable fact (e.g. 'this is "
    "becoming essential'). Any number, name, date, price, feature or 'X did Y' statement is type 'fact'. "
    "Respond with JSON only."
)


# ---------------------------------------------------------------- topic selection
def pick_topic(clusters, avoid_categories=()):
    listing = "\n".join(
        f"{i}. {c['title']} (score {c['score']}, sources: {', '.join(c.get('kinds', []))}, "
        f"official source: {'yes' if c.get('primary') else 'no'}, {len(c['items'])} articles)"
        for i, c in enumerate(clusters)
    )
    avoid = f"\nRecent videos were in these categories, prefer variety: {', '.join(avoid_categories)}" if avoid_categories else ""
    data, _ = chat_json(PICK_SYSTEM, (
        f"Candidate stories:\n{listing}{avoid}\n\nCategories: {', '.join(CATEGORIES)}\n\n"
        'Return {"ranking": [indexes best first, all of them], "category": "category of the top pick", '
        '"reason": "one sentence on why the top pick will interest viewers"}'
    ), temperature=0.3)
    order = []
    for i in data.get("ranking", []):
        try:
            i = int(i)
        except (TypeError, ValueError):
            continue
        if 0 <= i < len(clusters) and i not in order:
            order.append(i)
    order += [i for i in range(len(clusters)) if i not in order]
    print(f"[script] pick reason: {data.get('reason')}")
    ranked = [clusters[i] for i in order]
    if ranked:
        ranked[0]["reason"] = data.get("reason", "")
        ranked[0]["category"] = data.get("category", "")
    return ranked


# ---------------------------------------------------------------- writing
def write_package(title, article, shorter=False, remove_claims=None):
    extra = "\nIMPORTANT: keep the script to 95-115 words." if shorter else ""
    if remove_claims:
        extra += ("\nIMPORTANT: a fact-check found these claims NOT supported by the source. Do not include them:\n- "
                  + "\n- ".join(remove_claims))
    user = f"Story headline: {title}\n\nSource text:\n{article[:9000]}\n\n{WRITE_FORMAT}{extra}"
    last_err = None
    for attempt in range(3):
        msg = user if not last_err else user + f"\n\nYour previous answer was invalid: {last_err}. Fix it."
        data, provider = chat_json(WRITE_SYSTEM, msg)
        try:
            pkg = Package.model_validate(data)
            pkg.hashtags = [h if h.startswith("#") else f"#{h}" for h in pkg.hashtags]
            if pkg.hook not in pkg.hooks:
                pkg.hooks = [pkg.hook] + pkg.hooks[:2]
            issues = seo_issues(pkg)
            if issues and attempt < 2:
                last_err = "SEO rules not met: " + "; ".join(issues)
                print(f"[script] {last_err}")
                continue
            return fix_seo(pkg), provider
        except ValidationError as e:
            last_err = json.dumps(e.errors(include_url=False, include_context=False))[:800]
            print(f"[script] validation failed: {last_err}")
    raise RuntimeError(f"LLM could not produce a valid package: {last_err}")


# ---------------------------------------------------------------- SEO
def _clean_title(t):
    return re.sub(r"\s*#shorts\b", "", t, flags=re.I).strip(" -|:")


def _has_keyword(text, kw):
    """All meaningful words of the keyword appear in the text (any order), or a close fuzzy match."""
    words = [w for w in re.findall(r"[\w.]+", kw.lower()) if len(w) > 2 or w.isdigit()]
    low = text.lower()
    return bool(words) and (all(w in low for w in words) or fuzz.partial_ratio(kw.lower(), low) >= 90)


def seo_issues(pkg):
    """Rules that make a Short findable on YouTube and Google search."""
    issues, kw = [], pkg.primary_keyword.strip()
    title = _clean_title(pkg.title_youtube)
    if not kw:
        return ["primary_keyword is empty"]
    if not 30 <= len(title) <= 65:
        issues.append(f"title must be 30-65 characters (it is {len(title)})")
    if not _has_keyword(title[:50], kw):
        issues.append(f"title must contain the primary keyword '{kw}' in its first 50 characters")
    if not _has_keyword(pkg.description[:160], kw):
        issues.append(f"the description's first sentence must contain the primary keyword '{kw}'")
    if len(pkg.thumbnail_text.split()) > 4:
        issues.append("thumbnail_text must be 2-4 words")
    return issues


def fix_seo(pkg):
    """Last-resort fixes in code so every upload follows the rules, even if the model didn't."""
    kw = pkg.primary_keyword.strip()
    title = _clean_title(pkg.title_youtube)
    if kw and not _has_keyword(title[:50], kw):
        title = f"{kw.title()}: {title}"
    if len(title) > 65:
        title = title[:65].rsplit(" ", 1)[0].rstrip(" -|:,")
    pkg.title_youtube = f"{title} #Shorts"
    if kw and not _has_keyword(pkg.description[:160], kw):
        pkg.description = f"{kw[0].upper() + kw[1:]}: {pkg.description}"
    tags, seen = [], set()
    for t in [kw, *pkg.related_keywords, *pkg.tags]:
        t = t.strip().lstrip("#")
        if t and t.lower() not in seen and sum(len(x) + 2 for x in tags) + len(t) < 450:
            tags.append(t); seen.add(t.lower())
    pkg.tags = tags
    tags_h = [h if h.startswith("#") else f"#{h}" for h in pkg.hashtags if h.lower() != "#shorts"]
    pkg.hashtags = tags_h[:5] + ["#Shorts"]
    pkg.thumbnail_text = " ".join(pkg.thumbnail_text.split()[:4])
    return pkg


# ---------------------------------------------------------------- fact check
def _norm(s):
    return re.sub(r"\s+", " ", re.sub(r"[^\w%₹$.\s]", " ", s.lower())).strip()


def evidence_in_source(evidence, article):
    """The model must quote the source; we verify the quote really is there (no trusting the model)."""
    ev = _norm(evidence)
    return len(ev) >= 12 and fuzz.partial_ratio(ev, _norm(article)) >= 85


def fact_check(pkg, article):
    data, provider = chat_json(FACT_SYSTEM, (
        f"Source text:\n{article[:9000]}\n\nScript:\n{pkg.script}\n\n"
        'Return {"claims": [{"claim": "...", "type": "fact|opinion|cta", "verdict": "supported|unsupported|unclear", '
        '"evidence": "exact sentence copied from the source, or empty", '
        '"status": "available|beta|announced|n/a"}]}'
    ), temperature=0)
    claims = []
    for c in data.get("claims", []):
        text = str(c.get("claim", ""))
        kind = _claim_type(text, c.get("type"), getattr(pkg, "cta", ""))
        verdict = str(c.get("verdict", "unclear")).lower()
        evidence = str(c.get("evidence") or "")
        verified = verdict == "supported" and evidence_in_source(evidence, article)
        claims.append({"claim": text, "type": kind, "verdict": verdict, "evidence": evidence[:300],
                       "status": c.get("status", "n/a"), "verified": verified})
    facts = [c for c in claims if c["type"] == "fact"]
    bad = [c["claim"] for c in facts if not c["verified"]]
    return {"claims": claims, "unverified": bad, "passed": bool(facts) and not bad, "checker": provider}


_CTA_WORDS = re.compile(r"\b(follow|subscribe|like and share|comment below|let me know|tell me in the comments)\b", re.I)
_NUMBERISH = re.compile(r"\d|percent|crore|lakh|million|billion|dollar|rupee|₹|\$")


def _claim_type(text, said_type, cta=""):
    """fact | opinion | cta. Calls to action are never fact-checked; anything with numbers always is."""
    t = str(said_type or "fact").lower()
    if _CTA_WORDS.search(text) or (cta and fuzz.partial_ratio(text.lower(), cta.lower()) >= 90):
        return "cta"
    if _NUMBERISH.search(text.lower()):
        return "fact"  # never let a number slip through as "opinion"
    return t if t in ("opinion", "cta") else "fact"


# ---------------------------------------------------------------- metadata
def build_description(pkg, source_urls):
    lines = [pkg.description.strip()]
    if pkg.related_keywords:
        lines.append("\n🔎 Related: " + " | ".join(pkg.related_keywords[:6]))
    if source_urls:
        lines.append("\nSources:")
        lines += [f"- {u}" for u in source_urls]
    lines.append("\nNarration and visuals are AI-generated. Facts are taken from the sources above.")
    if config.AFFILIATE_FOOTER:
        lines.append("\n" + config.AFFILIATE_FOOTER)
    lines.append("\n" + " ".join(pkg.hashtags))
    return "\n".join(lines)[:4900]


def tts_text(pkg):
    """Script with pronunciation fixes applied (only for the voice; captions keep the real spelling)."""
    text = pkg.script
    for p in pkg.pronunciation:
        if p.term and p.say_as:
            text = re.sub(rf"\b{re.escape(p.term)}\b", p.say_as, text)
    return text
