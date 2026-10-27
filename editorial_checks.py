"""Deterministic editorial checks: section scope, source labels, house style.

Scope tests and name tables live in config/editorial.yaml. Nothing here calls a model.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from editorial_config import compiled, load_config, section_scopes

# ---------------------------------------------------------------------------- source labels


def _host(url: str) -> str:
    return urlparse(str(url or "")).netloc.casefold().removeprefix("www.")


def looks_like_url(value: str) -> bool:
    text = str(value or "").strip()
    return text.startswith(("http://", "https://")) or bool(re.fullmatch(r"[\w.-]+\.[a-z]{2,}(?:/\S*)?", text, re.I))


def _named(host: str) -> str:
    names = load_config()["source_names"]
    for key in sorted(names, key=len, reverse=True):
        if host == key or host.endswith("." + key):
            return str(names[key])
    return ""


def _host_to_name(host: str) -> str:
    """Last-resort readable name for a host with no table entry: 'defensescoop.com' -> 'Defensescoop'."""
    labels = [p for p in host.split(".") if p not in ("www", "com", "org", "net", "co", "uk", "au", "gov", "edu", "news")]
    return (labels[0] if labels else host).replace("-", " ").title()


def source_label(raw_source: str, url: str = "", original_publisher: str = "") -> str:
    """A clean English publication name; never a raw URL.

    Syndicated copies (AOL, MSN, Yahoo...) use the original publisher when the page names one.
    """
    config = load_config()
    host = _host(url)
    raw = " ".join(str(raw_source or "").split())
    syndicated = any(host == h or host.endswith("." + h) for h in config["syndication_hosts"]) or raw.casefold() in {
        "aol", "msn", "yahoo", "yahoo finance", "yahoo news", "futu", "futunn", "newsbreak"}
    if syndicated and original_publisher:
        candidate = original_publisher if original_publisher.startswith("http") else "https://" + original_publisher
        original_host = _host(candidate) if "." in original_publisher and " " not in original_publisher.strip() else ""
        named = _named(original_host) if original_host else ""
        if original_host and not any(original_host == h or original_host.endswith("." + h) for h in config["syndication_hosts"]):
            return named or (original_publisher if not looks_like_url(original_publisher) else _host_to_name(original_host))
        if not looks_like_url(original_publisher) and "." not in original_publisher:
            return original_publisher
    if looks_like_url(raw):
        return _named(_host(raw if raw.startswith("http") else "https://" + raw)) or _named(host) or _host_to_name(_host(raw if raw.startswith("http") else "https://" + raw))
    return _named(_host(raw)) if "." in raw and " " not in raw and _named(_host(raw)) else raw or _named(host) or _host_to_name(host)


# ---------------------------------------------------------------------------- section scope


def item_text(item: dict[str, Any]) -> str:
    return " ".join(str(item.get(k, "")) for k in ("title", "summary")) + " " + urlparse(str(item.get("url", ""))).path.replace("-", " ").replace("/", " ")


def scope_fits(item: dict[str, Any], section: str) -> bool:
    scope = section_scopes().get(section)
    if scope is None:
        return False
    if scope["origin"] and item.get("origin") == scope["origin"]:
        return True
    text = item_text(item)
    if scope["exclude"] and scope["exclude"].search(text):
        return False
    return bool(scope["require"] and scope["require"].search(text))


def off_topic_reason(item: dict[str, Any]) -> str:
    """'' when the item has a portfolio angle and is not celebrity/crime filler."""
    config = load_config()
    text = item_text(item)
    off = compiled(config["off_topic_patterns"])
    angle = compiled(config["portfolio_angle"])
    if item.get("required_include") or item.get("origin") == "Federal Register API":
        return ""
    hook = compiled(config.get("off_topic_unless", []))
    if off and (match := off.search(item_text(item))) and not (hook and hook.search(str(item.get("title", "")))):
        return f"celebrity or human-interest story with no policy or safety hook ({match.group(0).strip()[:40]!r})"
    if angle and not angle.search(text):
        return "no drone, counter-UAS, AAM, AV, rail, supersonic or space-transport angle"
    return ""


def best_section(item: dict[str, Any], exclude: str = "") -> str:
    for name in section_scopes():
        if name != exclude and scope_fits(item, name):
            return name
    return ""


def enforce_section_scope(
    sections: dict[str, list[dict[str, Any]]],
) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, str]]]:
    """Keep each story in the first section whose scope test it passes; reassign or drop the rest. Idempotent."""
    out: dict[str, list[dict[str, Any]]] = {name: [] for name in sections}
    log: list[dict[str, str]] = []
    for section, items in sections.items():
        for item in items:
            story = item
            note = {"title": str(item.get("title", ""))[:140], "url": str(item.get("url", ""))}
            if item.get("required_include") is True:
                out[section].append(story)
                continue
            reason = off_topic_reason(item)
            if reason:
                log.append({"stage": "scope", **note, "reason": f"dropped from {section}: {reason}"})
                continue
            if scope_fits(item, section):
                out[section].append(story)
                continue
            target = best_section(item)
            if not target:
                log.append({"stage": "scope", **note, "reason": f"dropped: fits no section scope (was {section})"})
                continue
            moved = dict(item, section=target)
            out.setdefault(target, []).append(moved)
            log.append({"stage": "scope", **note, "reason": f"moved from {section} to {target}: {section} requires {section_scopes()[section]['definition'].lower()}"})
    return out, log


# ---------------------------------------------------------------------------- house style

_MONEY = re.compile(r"\$\s?(\d[\d,]*(?:\.\d+)?)\s?(MM|mm|MN|mn|M|B|BN|bn|K)\b(?![A-Za-z])")
_MONEY_WORD = {"MM": "million", "MN": "million", "M": "million", "B": "billion", "BN": "billion", "K": "thousand"}


def style_money(text: str) -> str:
    """'$43.6MM' -> '$43.6 million'."""
    def swap(match: re.Match[str]) -> str:
        return f"${match.group(1)} {_MONEY_WORD[match.group(2).upper()]}"
    return _MONEY.sub(swap, str(text or ""))


_EO = re.compile(r"\b(?:Executive Order|E\.O\.|EO)\s*(?:No\.?\s*)?(\d{5})\b", re.I)


def style_executive_orders(text: str) -> str:
    """Name a known executive order by its official title, marked *italic* for the renderer.

    The number moves to a footnote at render time. Orders whose title is not in the table are left as
    written: a title is never guessed.
    """
    from news_engine import EO_DISPLAY_NAMES

    def swap(match: re.Match[str]) -> str:
        name = EO_DISPLAY_NAMES.get(f"EO {match.group(1)}")
        return f"*{name}*" if name else match.group(0)

    return _EO.sub(swap, str(text or ""))


def style_text(text: str) -> str:
    return style_executive_orders(style_money(text))


def strip_exclamations(text: str) -> str:
    return str(text or "").replace("!", "")
