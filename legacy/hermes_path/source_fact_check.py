"""Independent provenance, link, and claim-support gate for a dated edition."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ai_providers import OllamaProvider  # noqa: E402


FACT_MODEL = "llama3.3:70b-instruct-q4_K_M"
HEADERS = {"User-Agent": "AdvancedTransportationAccuracyGate/1.0"}
TRACKING_KEYS = {"fbclid", "gclid", "mc_cid", "mc_eid"}


def normalize_url(value: str) -> str:
    parts = urlsplit((value or "").strip())
    query = urlencode(
        [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
         if not k.lower().startswith("utm_") and k.lower() not in TRACKING_KEYS]
    )
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, query, ""))


def _words(value: str) -> set[str]:
    return {word for word in re.findall(r"[a-z0-9]+", value.casefold()) if len(word) > 2}


def title_overlap(left: str, right: str) -> float:
    a, b = _words(left), _words(right)
    return len(a & b) / max(1, len(a | b))


def _normalized_claim_text(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.casefold()))


def load_inputs(edition_date: str) -> tuple[dict, dict]:
    run = ROOT / "data" / "runs" / edition_date
    briefing = json.loads((run / f"briefing_{edition_date}.json").read_text(encoding="utf-8"))
    manifest = json.loads((run / "candidate_manifest.json").read_text(encoding="utf-8"))
    return briefing, manifest


def provenance_errors(briefing: dict, manifest: dict) -> tuple[list[str], list[dict]]:
    items = manifest.get("items", []) if isinstance(manifest, dict) else []
    index = {normalize_url(str(item.get("url", ""))): item for item in items}
    errors: list[str] = []
    evidence: list[dict] = []
    for section, stories in briefing.get("sections", {}).items():
        if not isinstance(stories, list):
            continue
        for story in stories:
            url = normalize_url(str(story.get("url", "")))
            record = index.get(url)
            title = str(story.get("title", "")).strip()
            if not record:
                errors.append(f"{title}: URL is absent from the saved candidate manifest.")
                continue
            source_title = str(record.get("original_title") or record.get("title") or "")
            if title_overlap(title, source_title) < 0.45:
                errors.append(
                    f"{title}: published headline does not match collected source headline {source_title!r}."
                )
            evidence.append(
                {
                    "id": str(story.get("id") or url),
                    "section": section,
                    "title": title,
                    "summary": str(story.get("summary", "")),
                    "additional_published_claims": {
                        "innovative_uas_use": str(story.get("innovative_uas_use", "")),
                        "administration_win": bool(story.get("is_administration_win", False)),
                        "eo_number": str(story.get("eo_number", "")),
                        "eo_section": str(story.get("eo_section", "")),
                        "win_explanation": str(story.get("win_explanation", "")),
                    },
                    "url": str(story.get("url", "")),
                    "source": str(story.get("source", "")),
                    "evidence_title": source_title,
                    "evidence_text": str(
                        record.get("description")
                        or record.get("summary")
                        or record.get("pasted_context")
                        or ""
                    )[:2400],
                    "evidence_source": str(record.get("source", "")),
                    "evidence_published": str(record.get("published", "")),
                }
            )
    return errors, evidence


def _check_url(url: str) -> tuple[str, int | None, str]:
    try:
        response = requests.get(
            url,
            headers=HEADERS,
            timeout=20,
            allow_redirects=True,
            stream=True,
        )
        status = response.status_code
        final_url = str(response.url)
        response.close()
        return url, status, final_url
    except requests.RequestException as exc:
        return url, None, str(exc)


def check_urls(evidence: list[dict]) -> tuple[list[str], list[str], list[dict]]:
    errors: list[str] = []
    warnings: list[str] = []
    results: list[dict] = []
    urls = sorted({item["url"] for item in evidence})
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(_check_url, url): url for url in urls}
        for future in as_completed(futures):
            url, status, detail = future.result()
            results.append({"url": url, "status": status, "detail": detail})
            if status in {404, 410}:
                errors.append(f"Source URL is dead ({status}): {url}")
            elif status is None or status >= 400:
                warnings.append(f"Source URL could not be independently fetched ({status or 'network'}): {url}")
    return errors, warnings, sorted(results, key=lambda item: item["url"])


def _fact_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "verdicts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "supported": {"type": "boolean"},
                        "unsupported_claims": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["id", "supported", "unsupported_claims"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["verdicts"],
        "additionalProperties": False,
    }


def _single_fact_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "supported": {"type": "boolean"},
            "unsupported_claims": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["supported", "unsupported_claims"],
        "additionalProperties": False,
    }


def model_fact_check(evidence: list[dict], cache_dir: Path) -> list[dict]:
    provider = OllamaProvider(
        model=FACT_MODEL,
        num_ctx=32768,
        timeout_seconds=1800,
        max_attempts=2,
        temperature=0.0,
        num_predict=300,
    )
    cache_dir.mkdir(parents=True, exist_ok=True)
    verdicts: list[dict] = []

    def normalized_verdict(item: dict, raw: dict) -> dict:
        original = [str(value) for value in raw.get("unsupported_claims", [])]
        inactive = {
            key
            for key, value in item.get("additional_published_claims", {}).items()
            if not value
        }
        remaining = [claim for claim in original if claim not in inactive]
        supported = bool(raw.get("supported", False)) or bool(original and not remaining)
        return {
            "id": item["id"],
            "supported": supported,
            "unsupported_claims": remaining,
        }

    for item in evidence:
        if (
            not str(item.get("evidence_text", "")).strip()
            and _normalized_claim_text(str(item.get("summary", "")))
            == "source headline " + _normalized_claim_text(str(item.get("evidence_title", "")))
            and not any(item.get("additional_published_claims", {}).values())
        ):
            verdicts.append(
                {"id": item["id"], "supported": True, "unsupported_claims": []}
            )
            continue
        serialized = json.dumps(item, ensure_ascii=False, sort_keys=True)
        digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        cache_path = cache_dir / f"{digest}.json"
        if cache_path.is_file():
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            if cached.get("evidence_hash") == digest:
                verdicts.append(normalized_verdict(item, cached))
                continue
        prompt = (
            "Act as an independent newsroom fact checker. Decide whether every "
            "material factual assertion in the published headline, summary, and additional "
            "published claims is explicitly "
            "supported by the supplied source headline and evidence text. Do not use outside "
            "knowledge and do not infer missing dates, numbers, actors, actions, causal claims, "
            "government decisions, or significance. Mark supported=false if any material claim "
            "is absent, contradicted, or ambiguous. Return only the verdict object.\n\n"
            + serialized
        )
        result = provider.generate_structured(
            [{"role": "user", "content": prompt}],
            schema_name="single_article_fact_check",
            schema=_single_fact_schema(),
            max_output_tokens=300,
        )
        cached = {"evidence_hash": digest, **result.value}
        cache_path.write_text(json.dumps(cached, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        verdicts.append(normalized_verdict(item, result.value))
    return verdicts


def run(edition_date: str, *, skip_model: bool = False) -> dict:
    briefing, manifest = load_inputs(edition_date)
    errors, evidence = provenance_errors(briefing, manifest)
    url_errors, warnings, url_results = check_urls(evidence)
    errors.extend(url_errors)
    verdicts: list[dict] = []
    if not errors and not skip_model:
        run_dir = ROOT / "data" / "runs" / edition_date
        verdicts = model_fact_check(evidence, run_dir / "fact_check_items")
        expected = {item["id"] for item in evidence}
        returned = {str(item.get("id", "")) for item in verdicts}
        if returned != expected:
            errors.append("Independent fact checker did not return exactly one verdict per story.")
        for verdict in verdicts:
            if not verdict.get("supported", False):
                claims = "; ".join(str(x) for x in verdict.get("unsupported_claims", []))
                errors.append(f"Unsupported article claim ({verdict.get('id')}): {claims or 'unspecified'}")

    report = {
        "status": "passed" if not errors else "blocked",
        "date": edition_date,
        "fact_model": None if skip_model else FACT_MODEL,
        "stories_checked": len(evidence),
        "errors": errors,
        "warnings": warnings,
        "url_results": url_results,
        "verdicts": verdicts,
    }
    run_dir = ROOT / "data" / "runs" / edition_date
    (run_dir / "fact_check_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False))
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True)
    parser.add_argument("--skip-model", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    return 0 if run(args.date, skip_model=args.skip_model)["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
