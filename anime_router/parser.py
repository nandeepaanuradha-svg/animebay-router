"""Conservative filename parsing. Seasons are part of the title, never discarded."""

import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal

VIDEO = re.compile(r"\.(mkv|mp4|m4v|avi|webm|ts)$", re.I)
QUALITY = re.compile(r"(?<!\d)(720|1080)p\b", re.I)
EP = r"(?P<episode>\d{1,5}(?:\.\d+)?)(?:v\d+)?"
DASH = re.compile(r"^(?P<title>.+)\s+-\s*(?:Episode\s*|EP\s*)?" + EP + r"(?P<tail>.*)$", re.I)
SE = re.compile(r"^(?P<title>.+?)[ ._-]+S(?P<season>\d{1,2})E" + EP + r"(?P<tail>.*)$", re.I)
WORD = re.compile(r"^(?P<title>.+?)\s+(?:Episode|EP)\s*" + EP + r"(?P<tail>.*)$", re.I)


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).casefold()
    return "".join(ch for ch in text if ch.isalnum())


def canonical_episode(value: str) -> str:
    number = Decimal(value)
    if not number.is_finite() or number < 0 or number > 99999:
        raise ValueError("Invalid episode number")
    return format(number.normalize(), "f")


def display_episode(value: str) -> str:
    whole, dot, fraction = canonical_episode(value).partition(".")
    return whole.zfill(2) + (dot + fraction if dot else "")


@dataclass(frozen=True)
class Release:
    title: str
    episode: str
    quality: str


def parse_release(filename: str, caption: str = "") -> Release:
    """Prefer the document's real filename; captions only supply a missing name."""
    if not filename:
        filename = next((line.strip() for line in caption.splitlines()
                         if VIDEO.search(line.strip())), "")
    name = unicodedata.normalize("NFKC", filename).strip().replace("_", " ")
    if not VIDEO.search(name):
        raise ValueError("No supported video filename")
    qualities = set(QUALITY.findall(name))
    if len(qualities) != 1:
        raise ValueError("Filename must contain exactly one of 720p or 1080p")
    name = VIDEO.sub("", name)
    # Leading release-group labels are not anime titles.
    name = re.sub(r"^(?:\[[^\]]+\]\s*)+", "", name)
    match = SE.match(name) or DASH.match(name) or WORD.match(name)
    if not match:
        raise ValueError("Cannot locate an unambiguous episode number")
    tail = match.group("tail")
    # Refuse ranges, batches and a number attached to a title word.
    if tail and not re.match(r"^[\s\[(]", tail):
        raise ValueError("Episode ranges/batches are not supported")
    tail_without_tags = re.sub(r"\[[^\]]*\]|\([^)]*\)", "", tail)
    if re.search(r"\b(batch|complete|episodes)\b|[-–+]\s*\d", tail_without_tags, re.I):
        raise ValueError("Episode ranges/batches are not supported")
    title = match.group("title").strip(" ._-")
    if match.re is SE:
        title += f" S{int(match.group('season'))}"
    if not normalize(title):
        raise ValueError("Empty anime title")
    return Release(title, canonical_episode(match.group("episode")), qualities.pop() + "p")
