"""
intelligence.linkedin_analysis -- LinkedIn "Get a copy of your data" export analysis.

Deterministic, local, privacy-preserving: everything here runs against a ZIP
(or already-extracted directory) LinkedIn hands the user under
Settings & Privacy -> Data Privacy -> Get a copy of your data. Nothing is
sent anywhere, no network calls. Never surfaces raw email addresses, phone
numbers, or message/comment/post text -- only aggregate counts,
classifications, and topic/industry labels.

Ported 2026-07-27 from the linkedin-export-analyzer Claude skill, which did
this same analysis by having the model author and run inline Python fresh
every session (non-deterministic prompt-to-code, re-derived each time, only
usable inside a Claude conversation with file access). This module makes it
a real, callable, testable MCP tool instead -- same classification logic
(topic regex table ported verbatim from the skill's inline analysis code),
reimplemented independently for the industry/tenure/connections side since
the skill's own bundled scripts/analyze.py was not available to port
byte-for-byte at the time this module was written. If scripts/analyze.py's
exact industry keyword list turns up later, reconcile INDUSTRY_KEYWORDS
below against it.
"""
import collections
import csv
import io
import re
import zipfile
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Optional

# ---------------------------------------------------------------------------
# Topic classification (comments / shares) -- ported verbatim from the
# linkedin-export-analyzer skill's inline analysis code.
# ---------------------------------------------------------------------------
TOPICS: dict[str, str] = {
    "Technology/IT":        r"\b(tech|software|ai|cyber|cloud|data|digital|linux|security|network|it |infosec|hack|code|developer|programming)\b",
    "Aviation/Transport":   r"\b(aviation|flight|pilot|aircraft|fly|airport|airline|charter|private\s+jet|helicopter|fbo|easa|faa|part\s*135)\b",
    "Hospitality":          r"\b(hotel|hospitality|resort|valet|concierge|guest|front\s+desk|gm|general\s+manager|marriott|hilton|starwood)\b",
    "Executive/Leadership": r"\b(leader|ceo|executive|management|strategy|board|c-suite|vp|director|chief)\b",
    "Security/EP":          r"\b(security|protection|ep\b|executive\s+prot|threat|risk|guard|protective|bodyguard|surveillance)\b",
    "Veterans/Military":    r"\b(veteran|military|marine|army|navy|service\s+member|usmc|deployment|combat|vets)\b",
    "Entrepreneurship":     r"\b(startup|entrepreneur|founder|business\s+owner|venture|small\s+biz|hustle|build)\b",
    "Networking/Career":    r"\b(network|connect|career|opportunity|hire|job|recruit|linkedin|professional\s+dev)\b",
}

# ---------------------------------------------------------------------------
# Industry classification (connections) -- keyword-matched priority list,
# first match wins. The skill's own docs note ~15-20% of connections land
# in "Other / Unclassified" by design; that's expected here too, not a bug.
# ---------------------------------------------------------------------------
INDUSTRY_KEYWORDS: list[tuple[str, str]] = [
    ("Aviation/Transport",        r"\b(aviation|airline|pilot|aircraft|airport|charter|flight|helicopter|faa|part\s*135|chauffeur|limousine|black\s*car|ground\s+transport)\b"),
    ("Technology/IT",             r"\b(software|engineer|developer|it\b|tech(nology)?|cyber|cloud|data\s+scien|devops|sre\b|infosec|programmer)\b"),
    ("Hospitality",               r"\b(hotel|hospitality|resort|valet|concierge|marriott|hilton|starwood|front\s+desk|guest\s+services)\b"),
    ("Security/EP",               r"\b(security|protective|executive\s+protection|\bep\b|threat\s+management|surveillance|investigat)\b"),
    ("Government/Public Safety",  r"\b(police|sheriff|fire\s+dept|ems\b|emergency\s+management|cert\b|ares\b|skywarn|federal|county|municipal|public\s+safety)\b"),
    ("Military/Veterans",         r"\b(usmc|marine\s+corps|\barmy\b|\bnavy\b|air\s+force|veteran|military|dod\b)\b"),
    ("Finance",                   r"\b(bank|finance|financial|accounting|cpa\b|audit|investment|wealth\s+manage)\b"),
    ("Legal",                     r"\b(attorney|lawyer|legal|counsel|paralegal|law\s+firm)\b"),
    ("Healthcare",                r"\b(health|medical|hospital|clinic|nurse|physician|pharma)\b"),
    ("Sales/Marketing",           r"\b(sales|marketing|business\s+development|account\s+exec|growth)\b"),
    ("Real Estate",               r"\b(real\s+estate|realtor|property\s+manage|broker(?!age)?)\b"),
    ("Executive/Leadership",      r"\b(ceo|coo|cfo|cto|president|founder|owner|principal|managing\s+director|executive\s+director)\b"),
    ("Entrepreneurship",          r"\b(entrepreneur|startup|small\s+business|self[- ]employed|consultant|consulting)\b"),
]

_DATE_FORMATS = ("%d %b %Y", "%m/%d/%y", "%Y-%m-%d", "%b %d, %Y", "%d-%b-%y")


def _open_export(export_path: str) -> Callable[[str], Optional[str]]:
    """Return a reader(name_substr) -> csv_text function, whether export_path
    is a .zip file or an already-extracted directory. Matches by substring
    (not exact filename) because Comments/Shares/Reactions/etc. filenames
    embed the exporting user's numeric LinkedIn ID, e.g. Comments_70127804.csv."""
    p = Path(export_path)
    if p.is_dir():
        files = {f.name: f for f in p.rglob("*.csv")}

        def _read_dir(name_substr: str) -> Optional[str]:
            match = next((f for n, f in files.items() if name_substr.lower() in n.lower()), None)
            return match.read_text(encoding="utf-8", errors="replace") if match else None

        return _read_dir

    z = zipfile.ZipFile(export_path)
    members = z.namelist()

    def _read_zip(name_substr: str) -> Optional[str]:
        match = next(
            (m for m in members if name_substr.lower() in m.lower() and m.lower().endswith(".csv")),
            None,
        )
        if not match:
            return None
        with z.open(match) as f:
            return f.read().decode("utf-8", errors="replace")

    return _read_zip


def _rows(reader: Callable[[str], Optional[str]], name_substr: str) -> list[dict]:
    text = reader(name_substr)
    if not text:
        return []
    return [row for row in csv.DictReader(io.StringIO(text)) if row]


def _parse_connections_csv(text: str) -> list[dict]:
    """Connections.csv ships with a 3-line notes preamble before the real
    header -- find the line containing "Connected On" (present in every
    export vintage seen) rather than hardcoding a line number."""
    lines = text.splitlines()
    header_idx = next(
        (i for i, line in enumerate(lines) if "Connected On" in line),
        3 if len(lines) > 3 else 0,
    )
    return [row for row in csv.DictReader(io.StringIO("\n".join(lines[header_idx:]))) if row]


def _classify_first_match(text: str, keyword_table: list[tuple[str, str]]) -> Optional[str]:
    for label, pattern in keyword_table:
        if re.search(pattern, text, re.I):
            return label
    return None


def _classify_all_matches(text: str, topic_table: dict[str, str]) -> list[str]:
    return [label for label, pattern in topic_table.items() if re.search(pattern, text, re.I)]


def _parse_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    value = value.strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def _monthly_series(rows: list[dict], date_field: str = "Date", since: str = "2024-01") -> dict[str, int]:
    counts: collections.Counter = collections.Counter()
    for row in rows:
        d = _parse_date(row.get(date_field))
        if d:
            key = d.strftime("%Y-%m")
            if key >= since:
                counts[key] += 1
    return dict(sorted(counts.items()))


def linkedin_network_breakdown(export_path: str, reference_date: Optional[str] = None) -> dict:
    """
    Industry and tenure breakdown from a LinkedIn "Get a copy of your data" export.

    Args:
        export_path:     Path to the export ZIP, or an already-extracted directory.
        reference_date:  ISO date (YYYY-MM-DD) to compute tenure from. Defaults
                          to today. Use the export date when known, for accuracy.

    Returns:
        dict with total_connections, date_range, industry_breakdown,
        unclassified_pct, tenure_breakdown, growth_by_year, top_companies.
        Never includes names, emails, or any per-connection identifying detail.
    """
    reader = _open_export(export_path)
    text = reader("Connections")
    if not text:
        return {"error": "Connections.csv not found in export", "total_connections": 0}

    rows = _parse_connections_csv(text)
    ref = datetime.strptime(reference_date, "%Y-%m-%d").date() if reference_date else date.today()

    industry_counts: collections.Counter = collections.Counter()
    tenure_counts: collections.Counter = collections.Counter()
    year_counts: collections.Counter = collections.Counter()
    company_counts: collections.Counter = collections.Counter()
    dates: list[date] = []
    unclassified = 0

    for row in rows:
        company = (row.get("Company") or "").strip()
        position = (row.get("Position") or "").strip()
        industry = _classify_first_match(f"{company} {position}", INDUSTRY_KEYWORDS)
        if industry:
            industry_counts[industry] += 1
        else:
            unclassified += 1
        if company:
            company_counts[company] += 1

        connected_on = _parse_date(row.get("Connected On"))
        if connected_on:
            dates.append(connected_on)
            year_counts[connected_on.year] += 1
            tenure_days = (ref - connected_on).days
            if tenure_days < 365:
                tenure_counts["0-1yr"] += 1
            elif tenure_days < 730:
                tenure_counts["1-2yr"] += 1
            elif tenure_days < 1460:
                tenure_counts["2-4yr"] += 1
            else:
                tenure_counts["4yr+"] += 1

    total = len(rows)
    if unclassified:
        industry_counts["Other / Unclassified"] = unclassified

    return {
        "total_connections": total,
        "date_range": {
            "earliest": min(dates).isoformat() if dates else None,
            "latest": max(dates).isoformat() if dates else None,
        },
        "industry_breakdown": dict(industry_counts.most_common()),
        "unclassified_pct": round(100 * unclassified / total, 1) if total else 0.0,
        "tenure_breakdown": dict(tenure_counts),
        "growth_by_year": dict(sorted(year_counts.items())),
        "top_companies": [{"company": c, "count": n} for c, n in company_counts.most_common(15)],
    }


def linkedin_content_analysis(export_path: str) -> dict:
    """
    Post/comment topic analysis and topic co-occurrence from a LinkedIn export.

    Args:
        export_path: Path to the export ZIP, or an already-extracted directory.

    Returns:
        dict with comments_total, comment_topics, comment_monthly,
        shares_total, share_topics, share_monthly, topic_cooccurrence.
        Comment/share text itself is never returned -- only topic labels
        and counts derived from it.
    """
    reader = _open_export(export_path)
    comments = _rows(reader, "Comments")
    shares = _rows(reader, "Shares")

    topic_counts: collections.Counter = collections.Counter()
    co_occur: collections.Counter = collections.Counter()
    for c in comments:
        matched = _classify_all_matches((c.get("Message") or "").lower(), TOPICS)
        for t in matched:
            topic_counts[t] += 1
        for i in range(len(matched)):
            for j in range(i + 1, len(matched)):
                co_occur[tuple(sorted([matched[i], matched[j]]))] += 1

    share_topic_counts: collections.Counter = collections.Counter()
    for s in shares:
        blob = ((s.get("ShareCommentary") or "") + " " + (s.get("SharedUrl") or "")).lower()
        for t in _classify_all_matches(blob, TOPICS):
            share_topic_counts[t] += 1

    return {
        "comments_total": len(comments),
        "comment_topics": dict(topic_counts.most_common(10)),
        "comment_monthly": _monthly_series(comments),
        "shares_total": len(shares),
        "share_topics": dict(share_topic_counts.most_common(8)),
        "share_monthly": _monthly_series(shares),
        "topic_cooccurrence": {
            f"{pair[0]} + {pair[1]}": count for pair, count in co_occur.most_common(12)
        },
    }


def linkedin_engagement_patterns(export_path: str) -> dict:
    """
    Reaction-type breakdown, monthly activity trend, and most-endorsed
    contacts from a LinkedIn export.

    Args:
        export_path: Path to the export ZIP, or an already-extracted directory.

    Returns:
        dict with reactions_total, reaction_types, reaction_monthly, like_pct,
        most_endorsed_contacts.

    LinkedIn's export has no per-reaction author/recipient field, so "most
    engaged with" is derived from Endorsement_Given_Info.csv -- skills the
    user endorsed for named contacts -- the one file in the export that
    actually names specific people the user engaged with. Those names were
    already visible to the user in their own export and reflect the user's
    own prior action; no other contact info (email, message content) is
    surfaced.
    """
    reader = _open_export(export_path)
    reactions = _rows(reader, "Reactions")
    endorsements = _rows(reader, "Endorsement_Given")

    reaction_types = collections.Counter(r.get("Type", "") for r in reactions)
    total = len(reactions)
    like_count = reaction_types.get("LIKE", 0)

    endorsed_counts: collections.Counter = collections.Counter()
    for e in endorsements:
        first = (e.get("Endorsee First Name") or e.get("First Name") or "").strip()
        last = (e.get("Endorsee Last Name") or e.get("Last Name") or "").strip()
        name = f"{first} {last}".strip()
        if name:
            endorsed_counts[name] += 1

    return {
        "reactions_total": total,
        "reaction_types": dict(reaction_types),
        "reaction_monthly": _monthly_series(reactions),
        "like_pct": round(100 * like_count / total, 1) if total else 0.0,
        "most_endorsed_contacts": [{"name": n, "count": c} for n, c in endorsed_counts.most_common(10)],
    }
