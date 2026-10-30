#!/usr/bin/env python3
"""Job-Radar: sucht täglich neue Stellen und schreibt einen Markdown-Report.

Quellen:
  1. Jobbörse der Bundesagentur für Arbeit (REST-Schnittstelle)
  2. Öffentliche Job-Schnittstellen von Greenhouse, Lever und Personio

Ausgabe:
  - reports/YYYY-MM-DD.md   Tagesreport mit allen neuen Treffern
  - report_latest.md        Text für das GitHub-Issue
  - data/seen.json          bereits gemeldete Stellen (verhindert Doppelmeldungen)
  - GITHUB_OUTPUT new_count Anzahl neuer Treffer für den Workflow
"""
from __future__ import annotations

import datetime as dt
import html
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

import requests
import yaml

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.yaml"
SEEN_PATH = ROOT / "data" / "seen.json"
REPORT_DIR = ROOT / "reports"
LATEST_PATH = ROOT / "report_latest.md"
HEADERS = {"User-Agent": "personal-job-radar/1.0 (private use, daily)"}
TIMEOUT = 30


@dataclass
class Job:
    source: str
    id: str
    title: str
    company: str
    location: str
    url: str
    published: str = ""
    text: str = ""
    score: int = 0
    salary: str = "nicht angegeben"
    reasons: str = ""


def log(msg: str) -> None:
    print(f"[job-radar] {msg}", file=sys.stderr)


def strip_html(raw: str) -> str:
    text = re.sub(r"<[^>]+>", " ", html.unescape(raw or ""))
    return re.sub(r"\s+", " ", text).strip()


def get(url: str, **kwargs) -> requests.Response | None:
    try:
        r = requests.get(url, timeout=TIMEOUT, headers={**HEADERS, **kwargs.pop("headers", {})}, **kwargs)
        r.raise_for_status()
        return r
    except requests.RequestException as exc:
        log(f"Fehler bei {url}: {exc}")
        return None


# ---------------------------------------------------------------- Quellen

def fetch_arbeitsagentur(cfg: dict) -> list[Job]:
    jobs: list[Job] = []
    for query in cfg["queries"]:
        for page in range(1, int(cfg.get("max_pages", 2)) + 1):
            params = {
                "was": query,
                "wo": cfg["wo"],
                "umkreis": cfg["umkreis"],
                "angebotsart": 1,
                "veroeffentlichtseit": cfg.get("veroeffentlichtseit", 7),
                "size": 50,
                "page": page,
            }
            r = get(cfg["base_url"], params=params, headers={"X-API-Key": cfg["api_key"]})
            if r is None:
                break
            items = r.json().get("stellenangebote") or []
            for it in items:
                refnr = it.get("refnr", "")
                jobs.append(Job(
                    source="Arbeitsagentur",
                    id=f"ba:{refnr}",
                    title=it.get("titel") or it.get("beruf", ""),
                    company=it.get("arbeitgeber", ""),
                    location=(it.get("arbeitsort") or {}).get("ort", ""),
                    url=it.get("externeUrl") or f"https://www.arbeitsagentur.de/jobsuche/jobdetail/{refnr}",
                    published=it.get("aktuelleVeroeffentlichungsdatum", ""),
                ))
            if len(items) < 50:
                break
    log(f"Arbeitsagentur: {len(jobs)} Rohtreffer")
    return jobs


def fetch_greenhouse(token: str, company: str) -> list[Job]:
    r = get(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs", params={"content": "true"})
    if r is None:
        return []
    return [Job(
        source="Greenhouse",
        id=f"gh:{token}:{j['id']}",
        title=j.get("title", ""),
        company=company,
        location=(j.get("location") or {}).get("name", ""),
        url=j.get("absolute_url", ""),
        published=(j.get("updated_at") or "")[:10],
        text=strip_html(j.get("content", "")),
    ) for j in r.json().get("jobs", [])]


def fetch_lever(token: str, company: str) -> list[Job]:
    r = get(f"https://api.lever.co/v0/postings/{token}", params={"mode": "json"})
    if r is None:
        return []
    jobs = []
    for j in r.json():
        created = j.get("createdAt")
        published = dt.datetime.fromtimestamp(created / 1000).date().isoformat() if created else ""
        jobs.append(Job(
            source="Lever",
            id=f"lever:{token}:{j.get('id')}",
            title=j.get("text", ""),
            company=company,
            location=(j.get("categories") or {}).get("location", ""),
            url=j.get("hostedUrl", ""),
            published=published,
            text=j.get("descriptionPlain", ""),
        ))
    return jobs


def fetch_personio(token: str, company: str) -> list[Job]:
    r = get(f"https://{token}.jobs.personio.de/xml")
    if r is None:
        return []
    try:
        root = ET.fromstring(r.content)
    except ET.ParseError as exc:
        log(f"Personio {token}: XML nicht lesbar ({exc})")
        return []
    jobs = []
    for pos in root.iter("position"):
        pid = pos.findtext("id", "")
        desc = " ".join(strip_html(v.text or "") for v in pos.iter("value"))
        jobs.append(Job(
            source="Personio",
            id=f"personio:{token}:{pid}",
            title=pos.findtext("name", ""),
            company=company,
            location=pos.findtext("office", ""),
            url=f"https://{token}.jobs.personio.de/job/{pid}",
            published=(pos.findtext("createdAt", "") or "")[:10],
            text=desc,
        ))
    return jobs


# ---------------------------------------------------------------- Bewertung

SALARY_PATTERNS = [
    re.compile(r"(\d{2,3})[.\s,](\d{3})\s*(?:€|eur)", re.I),   # 120.000 €
    re.compile(r"(?:€|eur)\s*(\d{2,3})[.\s,](\d{3})", re.I),   # € 120,000
    re.compile(r"(\d{2,3})\s?k\s?(?:€|eur)?\b", re.I),         # 120k
]


def extract_salary(text: str) -> int | None:
    """Höchsten plausiblen Jahresbetrag (40k bis 500k) aus dem Text holen."""
    values = []
    for pat in SALARY_PATTERNS:
        for m in pat.finditer(text):
            if len(m.groups()) == 2:
                values.append(int(m.group(1)) * 1000 + int(m.group(2)))
            else:
                values.append(int(m.group(1)) * 1000)
    values = [v for v in values if 40_000 <= v <= 500_000]
    return max(values) if values else None


def contains(haystack: str, terms: list[str]) -> list[str]:
    return [t for t in terms if t in haystack]


def evaluate(job: Job, cfg: dict) -> bool:
    sc = cfg["scoring"]
    title = f" {job.title.lower()} "
    full = f" {title} {job.company.lower()} {job.text.lower()} "

    if contains(title, sc["exclude_title_terms"]):
        return False
    if contains(f" {job.company.lower()} ", sc.get("exclude_companies") or []):
        return False
    core = contains(title, sc["title_must_match"])
    if not core:
        return False

    reasons = [f"Titel: {', '.join(core[:2])}"]
    score = 3
    if contains(title, sc["seniority_terms"]):
        score += 2
        reasons.append("Führung/Senior")
    if contains(job.company.lower(), sc["watchlist_companies"]):
        score += 2
        reasons.append("Wunscharbeitgeber")
    bonus = contains(full, sc["bonus_terms"])
    score += len(bonus)
    if bonus:
        reasons.append(f"Bonus: {', '.join(t.strip() for t in bonus[:4])}")

    salary = extract_salary(f"{job.title} {job.text}")
    if salary is not None:
        if salary < cfg["min_salary_eur"]:
            return False
        job.salary = f"bis ca. {salary:,} €".replace(",", ".")

    job.score = score
    job.reasons = "; ".join(reasons)
    return score >= cfg["min_score"]


def location_ok(job: Job, locations: list[str]) -> bool:
    loc = job.location.lower()
    return any(l in loc for l in locations) if loc else True


# ---------------------------------------------------------------- Ablauf

def load_seen() -> dict[str, str]:
    if SEEN_PATH.exists():
        return json.loads(SEEN_PATH.read_text(encoding="utf-8"))
    return {}


def save_seen(seen: dict[str, str], forget_after: int) -> None:
    cutoff = (dt.date.today() - dt.timedelta(days=forget_after)).isoformat()
    seen = {k: v for k, v in seen.items() if v >= cutoff}
    SEEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    SEEN_PATH.write_text(json.dumps(seen, indent=1, sort_keys=True, ensure_ascii=False), encoding="utf-8")


def dedupe_key(job: Job) -> str:
    return re.sub(r"\W+", "", f"{job.company}{job.title}".lower())


def render(jobs: list[Job], today: str) -> str:
    lines = [
        f"# Job-Radar {today}",
        "",
        f"{len(jobs)} neue Treffer, sortiert nach Punktzahl.",
        "",
        "| Punkte | Rolle | Unternehmen | Ort | Gehalt | Warum | Quelle |",
        "|---:|---|---|---|---|---|---|",
    ]
    for j in jobs:
        title = j.title.replace("|", "/")
        lines.append(
            f"| {j.score} | [{title}]({j.url}) | {j.company.replace('|', '/')} | {j.location} "
            f"| {j.salary} | {j.reasons} | {j.source} {j.published[:10]} |"
        )
    lines += ["", "Gehaltsfilter greift nur bei Anzeigen mit Gehaltsangabe. Status vor Bewerbung auf der Karriereseite prüfen."]
    return "\n".join(lines) + "\n"


def write_output(name: str, value: str) -> None:
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as fh:
            fh.write(f"{name}={value}\n")


def main() -> int:
    cfg = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    today = dt.date.today().isoformat()

    candidates: list[Job] = []
    if cfg.get("arbeitsagentur", {}).get("enabled"):
        candidates += fetch_arbeitsagentur(cfg["arbeitsagentur"])

    boards = cfg.get("ats_boards") or {}
    ats_jobs: list[Job] = []
    for b in boards.get("greenhouse") or []:
        ats_jobs += fetch_greenhouse(b["token"], b["company"])
    for b in boards.get("lever") or []:
        ats_jobs += fetch_lever(b["token"], b["company"])
    for b in boards.get("personio") or []:
        ats_jobs += fetch_personio(b["token"], b["company"])
    candidates += [j for j in ats_jobs if location_ok(j, cfg["locations"])]
    log(f"Kandidaten gesamt: {len(candidates)}")

    seen = load_seen()
    fresh: dict[str, Job] = {}
    for job in candidates:
        if job.id in seen or not evaluate(job, cfg):
            continue
        key = dedupe_key(job)
        if key not in fresh or job.score > fresh[key].score:
            fresh[key] = job

    results = sorted(fresh.values(), key=lambda j: (-j.score, j.company))[: cfg["max_results"]]
    for job in fresh.values():
        seen[job.id] = today
    save_seen(seen, cfg["forget_after_days"])

    log(f"Neue Treffer: {len(results)}")
    write_output("new_count", str(len(results)))
    if results:
        report = render(results, today)
        REPORT_DIR.mkdir(exist_ok=True)
        (REPORT_DIR / f"{today}.md").write_text(report, encoding="utf-8")
        LATEST_PATH.write_text(report, encoding="utf-8")
        print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
