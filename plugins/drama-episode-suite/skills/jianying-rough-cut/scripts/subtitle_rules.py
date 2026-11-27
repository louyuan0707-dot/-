#!/usr/bin/env python3
"""Shared subtitle language profiles, font discovery, wrapping, and cue splitting."""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path


PROFILES = {
    "latin": {
        "font_name": "Noto Sans SemiBold",
        "size": 14.0,
        "line_spacing": 0,
        # Conservative width for Latin scripts in a 70% safe text box.
        "max_line_units": 30,
        "font_candidates": ("NOTOSANS-SEMIBOLD.TTF", "NotoSans-SemiBold.ttf"),
    },
    "ja": {
        "font_name": "Noto Sans JP SemiBold",
        "size": 10.0,
        "line_spacing": 5,
        "max_line_units": 28,
        "font_candidates": ("NotoSansJP-SemiBold.ttf", "NotoSansJP[wght].ttf"),
    },
    "ko": {
        "font_name": "Noto Sans KR SemiBold",
        "size": 10.0,
        "line_spacing": 5,
        "max_line_units": 28,
        "font_candidates": ("NotoSansKR-SemiBold.ttf", "NotoSansKR[wght].ttf"),
    },
    "th": {
        "font_name": "Noto Sans Thai SemiBold",
        "size": 14.0,
        "line_spacing": 0,
        "max_line_units": 28,
        "font_candidates": ("NotoSansThai-SemiBold.ttf", "NotoSansThai[wdth,wght].ttf"),
    },
    "ar": {
        "font_name": "Noto Naskh Arabic SemiBold",
        "size": 18.0,
        "line_spacing": 0,
        "max_line_units": 24,
        "font_candidates": ("NotoNaskhArabic-SemiBold.ttf", "NotoNaskhArabic[wght].ttf"),
    },
    "zh-hant": {
        "font_name": "Source Han Sans TW Medium",
        "size": 10.0,
        "line_spacing": 0,
        "max_line_units": 28,
        "font_candidates": (
            "SourceHanSansTW-Medium.otf",
            "SourceHanSansTC-Medium.otf",
            "SourceHanSansTW-Medium.ttf",
        ),
    },
}

LATIN_CODES = {
    "en", "vi", "es", "pt", "fr", "it", "de", "nl", "ro", "ca", "gl",
}
NO_SPACE_PROFILES = {"ja", "zh-hant"}
CLAUSE_END_RE = re.compile(r"(?<=[.!?…。！？；;，,、])")
MAX_SUBTITLE_LINES = 2


def normalize_language(language: str) -> str:
    code = (language or "").strip().replace("_", "-").casefold()
    primary = code.split("-", 1)[0]
    if primary in LATIN_CODES:
        return "latin"
    if primary == "ja":
        return "ja"
    if primary == "ko":
        return "ko"
    if primary == "th":
        return "th"
    if primary == "ar":
        return "ar"
    if primary == "zh" and any(tag in code for tag in ("hant", "tw", "hk", "mo")):
        return "zh-hant"
    raise ValueError(
        f"Unsupported subtitle language '{language}'. Use an explicit supported tag: "
        "en/vi/es (or another Latin-script code), ja, ko, th, ar, or zh-Hant/zh-TW."
    )


def profile_for_language(language: str) -> dict:
    key = normalize_language(language)
    profile = {"key": key, **PROFILES[key]}
    if (language or "").strip().replace("_", "-").casefold().split("-", 1)[0] == "en":
        profile["max_line_units"] = 16
        profile["no_punctuation"] = True
    return profile


def font_search_roots() -> list[Path]:
    roots = [Path(r"C:\Windows\Fonts")]
    local_app_data = Path.home() / "AppData" / "Local"
    roots.append(local_app_data / "Microsoft" / "Windows" / "Fonts")
    return roots


def resolve_font_path(profile: dict, override: Path | None = None) -> Path:
    if override is not None:
        path = override.expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"Subtitle font does not exist: {path}")
        return path
    wanted = {name.casefold() for name in profile["font_candidates"]}
    for root in font_search_roots():
        if not root.is_dir():
            continue
        for path in root.iterdir():
            if path.is_file() and path.name.casefold() in wanted:
                return path.resolve()
    raise FileNotFoundError(
        f"Required subtitle font is not installed: {profile['font_name']}. "
        "Install it or pass --font-file with the exact requested font file."
    )


def display_units(text: str) -> int:
    total = 0
    for char in text:
        if char == "\n":
            continue
        if unicodedata.combining(char):
            continue
        total += 2 if unicodedata.east_asian_width(char) in {"W", "F"} else 1
    return total


def _split_long_token(token: str, max_units: int) -> list[str]:
    parts: list[str] = []
    current = ""
    for char in token:
        if current and display_units(current + char) > max_units:
            parts.append(current)
            current = char
        else:
            current += char
    if current:
        parts.append(current)
    return parts


def _tokens_for_wrap(text: str, profile_key: str) -> list[str]:
    text = re.sub(r"\s+", " ", text.strip())
    if not text:
        return []
    if profile_key in NO_SPACE_PROFILES:
        clauses = [part for part in CLAUSE_END_RE.split(text) if part]
        return clauses or list(text)
    return re.findall(r"\S+", text)


def wrap_lines(text: str, profile: dict) -> list[str]:
    max_units = int(profile["max_line_units"])
    key = str(profile["key"])
    lines: list[str] = []
    for paragraph in text.replace("\r", "").split("\n"):
        tokens = _tokens_for_wrap(paragraph, key)
        current = ""
        for token in tokens:
            separator = "" if key in NO_SPACE_PROFILES else (" " if current else "")
            candidate = current + separator + token
            if current and display_units(candidate) > max_units:
                lines.append(current)
                current = ""
            if display_units(token) > max_units:
                chunks = _split_long_token(token, max_units)
                if current:
                    lines.append(current)
                    current = ""
                lines.extend(chunks[:-1])
                current = chunks[-1]
            else:
                separator = "" if key in NO_SPACE_PROFILES else (" " if current else "")
                current = current + separator + token
        if current:
            lines.append(current)
    return [line.strip() for line in lines if line.strip()]


def split_and_wrap_cue(cue: dict, profile: dict) -> list[dict]:
    cue_text = str(cue.get("text", ""))
    if profile.get("no_punctuation"):
        cue_text = "".join(" " if unicodedata.category(char).startswith("P") else char for char in cue_text)
    lines = wrap_lines(cue_text, profile)
    if not lines:
        return []
    groups = [
        lines[index:index + MAX_SUBTITLE_LINES]
        for index in range(0, len(lines), MAX_SUBTITLE_LINES)
    ]
    start = float(cue["start"])
    end = float(cue["end"])
    if len(groups) == 1:
        return [{**cue, "text": "\n".join(groups[0])}]
    weights = [max(1, sum(display_units(line) for line in group)) for group in groups]
    total = sum(weights)
    output: list[dict] = []
    cursor = start
    for index, (group, weight) in enumerate(zip(groups, weights)):
        group_end = end if index == len(groups) - 1 else cursor + (end - start) * weight / total
        output.append({**cue, "start": cursor, "end": group_end, "text": "\n".join(group)})
        cursor = group_end
    return output


def validate_cues(cues: list[dict], profile: dict) -> None:
    """Reject cues that Jianying could render above the two-line ceiling."""
    max_units = int(profile["max_line_units"])
    for cue in cues:
        cue_id = cue.get("id", "unknown")
        lines = str(cue.get("text", "")).replace("\r", "").split("\n")
        if not lines or any(not line.strip() for line in lines):
            raise ValueError(f"Subtitle cue {cue_id} contains a blank line")
        if len(lines) > MAX_SUBTITLE_LINES:
            raise ValueError(
                f"Subtitle cue {cue_id} has {len(lines)} lines; maximum is {MAX_SUBTITLE_LINES}"
            )
        if profile.get("no_punctuation") and any(
            unicodedata.category(char).startswith("P") for char in str(cue.get("text", ""))
        ):
            raise ValueError(f"Subtitle cue {cue_id} contains punctuation")
        for line in lines:
            units = display_units(line.strip())
            if units > max_units:
                raise ValueError(
                    f"Subtitle cue {cue_id} line is too wide for the 70% safe box: "
                    f"{units}>{max_units} display units"
                )


def normalize_cues(cues: list[dict], language: str) -> tuple[list[dict], dict]:
    profile = profile_for_language(language)
    output: list[dict] = []
    for cue in sorted(cues, key=lambda item: (float(item["start"]), float(item["end"]))):
        if float(cue["end"]) <= float(cue["start"]):
            continue
        output.extend(split_and_wrap_cue(cue, profile))
    for index, cue in enumerate(output, start=1):
        cue["id"] = index
    validate_cues(output, profile)
    return output, profile
