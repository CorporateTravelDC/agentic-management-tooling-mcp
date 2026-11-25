"""
gig_mobility/uber_driver.py -- driver-side analyses of Uber's "Download your data" export.

2026-11-25 (operator): added beside gig_analysis.py so every export is analysed by the same code and each
report can be compared with the last (a longitudinal archive), and so a change in Uber's own formats shows up
as a changed fingerprint instead of a silently wrong number. gig_analysis.gig_normalize_export stays the trip
normaliser; these read the other driver files it does not:

  uber_format_fingerprint      every CSV in the export: rows, columns, header hash, column list (names only)
  uber_cancellation_analysis   cancelled / unfulfilled requests vs completed, by ETA, hour, day, product, offer
                               type, airport, surge, fare band and month; the driver's own cancellations listed
  uber_rerun_detection         a cancelled request followed soon after by a matching request (same product, same
                               airport flag, upfront fare within a tolerance): the same job re-offered
  uber_earnings_residual       weekly statement earnings minus the upfront fares of that week's completed trips:
                               an ESTIMATE of tips + incentives + adjustments (the export has no tip column)
  uber_online_hours            hours per earner state per month, utilisation and statement earnings per online hour
  uber_ratings_summary         distribution of ratings received (the file has no dates: nothing here is dated)
  uber_app_offer_events        daily offer received / accepted / rejected / expired counts from the app analytics
                               file, reading ONLY its time and event columns (never location or device columns)

Privacy: no function returns a name, licence plate, coordinate, device id, IP address or message text. Times are
the export's local wall-clock times (America/New_York for a DC driver), labelled as local.
"""

from __future__ import annotations

import collections
import csv
import datetime
import hashlib
import io
import statistics
import zipfile
from pathlib import Path

csv.field_size_limit(1 << 30)

_DOW = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
_APP_COLUMNS = ("datestr", "time", "action_type", "event_name")
_OFFER_EVENTS = {
    "received": ("marketplace_unified_offer_received", "marketplace_offer_received"),
    "accepted": ("dx_unified_offer_focus_accept_tap", "upfront_offer_accept_button",
                 "carbon_upfront_offer_view_v2_accept_button", "marketplace_offer_acknowledgesuccess"),
    "rejected": ("dx_unified_offer_focus_reject_tap", "driveroffersjobboard_offers_rejectsuccess",
                 "marketplace_offer_declinesuccess"),
    "expired": ("driveroffersjobboard_offers_expirysuccess",),
}


def _rows(path: str | Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _num(value) -> float | None:
    try:
        return float(str(value).replace("$", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _ts(value) -> datetime.datetime | None:
    if not value:
        return None
    try:
        return datetime.datetime.fromisoformat(str(value).strip()[:19])
    except ValueError:
        return None


def _since(value: str | None) -> datetime.datetime | None:
    return datetime.datetime.fromisoformat(value) if value else None


def _completed(row: dict) -> bool:
    return row.get("is_completed") == "true" or row.get("status") == "completed"


def _eta_band(seconds: float | None) -> str:
    if seconds is None:
        return "unknown"
    m = seconds / 60
    return "<5 min" if m < 5 else "5-10 min" if m < 10 else "10-15 min" if m < 15 else "15+ min"


def _fare_band(usd: float | None) -> str:
    if not usd:
        return "no upfront fare"
    return "<$15" if usd < 15 else "$15-25" if usd < 25 else "$25-40" if usd < 40 else "$40+"


# ---------------------------------------------------------------------------
# Format fingerprint
# ---------------------------------------------------------------------------

def uber_format_fingerprint(zip_path: str | Path) -> dict:
    """Per-CSV rows, column count, SHA-256 of the header and the column names. Reads no values."""
    files = []
    with zipfile.ZipFile(zip_path) as z:
        for name in sorted(n for n in z.namelist() if n.lower().endswith(".csv")):
            with z.open(name) as f:
                reader = csv.reader(io.TextIOWrapper(f, encoding="utf-8-sig", newline=""))
                header = next(reader, [])
                rows = sum(1 for _ in reader)
            files.append({
                "file": name.split("/", 1)[1] if name.startswith("Uber Data/") else name,
                "rows": rows,
                "columns": len(header),
                "header_sha256": hashlib.sha256(",".join(header).encode()).hexdigest()[:12],
                "column_names": header,
            })
    return {"file_count": len(files), "files": files}


def uber_fingerprint_diff(previous: dict, current: dict) -> dict:
    """Compare two fingerprints: files added/removed, and per file the columns added/removed."""
    p = {f["file"]: f for f in previous.get("files", [])}
    c = {f["file"]: f for f in current.get("files", [])}
    changed = []
    for name in sorted(set(p) & set(c)):
        if p[name]["header_sha256"] != c[name]["header_sha256"]:
            a, b = set(p[name]["column_names"]), set(c[name]["column_names"])
            changed.append({"file": name, "added": sorted(b - a), "removed": sorted(a - b),
                            "reordered_only": a == b})
    return {"added_files": sorted(set(c) - set(p)), "removed_files": sorted(set(p) - set(c)),
            "changed_files": changed, "unchanged": not (set(c) ^ set(p)) and not changed}


# ---------------------------------------------------------------------------
# Cancellations
# ---------------------------------------------------------------------------

def uber_cancellation_analysis(trips_csv: str | Path, since: str | None = None) -> dict:
    """
    Cancelled and unfulfilled requests against all requests, broken down by the fields the export keeps on a
    cancelled row (it keeps no distance for those). Rates are cancelled / all requests in that bucket.
    """
    start = _since(since)
    rows = [r for r in _rows(trips_csv) if _ts(r.get("request_timestamp_local"))
            and (start is None or _ts(r["request_timestamp_local"]) >= start)]
    if not rows:
        return {"error": "No requests in range."}

    def bucket(key_fn):
        tot, cx = collections.Counter(), collections.Counter()
        for r in rows:
            k = key_fn(r)
            tot[k] += 1
            if not _completed(r):
                cx[k] += 1
        return [{"key": k, "requests": tot[k], "cancelled": cx[k],
                 "rate": round(cx[k] / tot[k], 3)} for k in sorted(tot, key=lambda x: str(x))]

    ts = lambda r: _ts(r["request_timestamp_local"])
    cancelled = [r for r in rows if not _completed(r)]
    own = [r for r in cancelled if r.get("status") == "provider_canceled"]
    eta_c = [_num(r.get("eta")) / 60 for r in cancelled if _num(r.get("eta")) is not None]
    eta_k = [_num(r.get("eta")) / 60 for r in rows if _completed(r) and _num(r.get("eta")) is not None]
    return {
        "since": since,
        "requests": len(rows),
        "cancelled": len(cancelled),
        "by_status": dict(collections.Counter(r.get("status", "") for r in cancelled)),
        "rate": round(len(cancelled) / len(rows), 3),
        "median_eta_min": {"cancelled": round(statistics.median(eta_c), 1) if eta_c else None,
                           "completed": round(statistics.median(eta_k), 1) if eta_k else None},
        "cancellation_fees_usd": round(sum(_num(r.get("cancellation_fee_usd")) or 0 for r in cancelled), 2),
        "paid_cancellations": sum(1 for r in cancelled if (_num(r.get("cancellation_fee_usd")) or 0) > 0),
        "reason_codes": [{"status": k[0], "code": k[1], "count": v} for k, v in collections.Counter(
            (r.get("status", ""), r.get("driver_cancellation_reason") or "(none)") for r in cancelled).most_common()],
        "by_eta": bucket(lambda r: _eta_band(_num(r.get("eta")))),
        "by_hour": bucket(lambda r: ts(r).hour),
        "by_day": sorted(bucket(lambda r: ts(r).strftime("%A")), key=lambda b: _DOW.index(b["key"])),
        "by_product": bucket(lambda r: r.get("product_type_name") or "unknown"),
        "by_offer_type": bucket(lambda r: r.get("trip_offer_type") or "unknown"),
        "by_airport": bucket(lambda r: "airport" if r.get("is_airport_trip") == "true" else "not airport"),
        "by_surge": bucket(lambda r: "surged" if r.get("is_surged") == "true" else "not surged"),
        "by_fare_band": bucket(lambda r: _fare_band(_num(r.get("driver_upfront_fare_usd")))),
        "by_month": bucket(lambda r: ts(r).strftime("%Y-%m")),
        "driver_cancellations": [{
            "request_local": r["request_timestamp_local"][:16],
            "day": ts(r).strftime("%A"),
            "product": r.get("product_type_name"),
            "offer_type": r.get("trip_offer_type"),
            "eta_min": round(_num(r.get("eta")) / 60, 1) if _num(r.get("eta")) is not None else None,
            "upfront_fare_usd": _num(r.get("driver_upfront_fare_usd")),
            "airport": r.get("is_airport_trip") == "true",
            "surged": r.get("is_surged") == "true",
            "reason_code": r.get("driver_cancellation_reason") or None,
            "cancellation_fee_usd": _num(r.get("cancellation_fee_usd")),
        } for r in sorted(own, key=lambda r: r["request_timestamp_local"])],
    }


def uber_rerun_detection(trips_csv: str | Path, window_min: float = 30, fare_tolerance: float = 0.15,
                         since: str | None = None) -> dict:
    """
    A cancelled request followed, within window_min, by another request for the same product with the same
    airport flag and (when both carry one) an upfront fare within fare_tolerance: most likely the same job
    offered again. Requests without a fare on either side match on product, airport flag and time only, and are
    marked `fare_checked: false`. Each later request is used by at most one match.
    """
    start = _since(since)
    rows = sorted((r for r in _rows(trips_csv) if _ts(r.get("request_timestamp_local"))
                   and (start is None or _ts(r["request_timestamp_local"]) >= start)),
                  key=lambda r: r["request_timestamp_local"])
    used, matches = set(), []
    for i, c in enumerate(rows):
        if _completed(c):
            continue
        tc, fc = _ts(c["request_timestamp_local"]), _num(c.get("driver_upfront_fare_usd")) or 0
        for j in range(i + 1, len(rows)):
            n = rows[j]
            gap = (_ts(n["request_timestamp_local"]) - tc).total_seconds() / 60
            if gap > window_min:
                break
            if j in used or n.get("product_type_name") != c.get("product_type_name") \
                    or n.get("is_airport_trip") != c.get("is_airport_trip"):
                continue
            fn = _num(n.get("driver_upfront_fare_usd")) or 0
            checked = fc > 0 and fn > 0
            if checked and abs(fn - fc) / max(fn, fc) > fare_tolerance:
                continue
            used.add(j)
            matches.append({
                "cancelled_local": c["request_timestamp_local"][:16], "cancelled_status": c.get("status"),
                "next_local": n["request_timestamp_local"][:16], "gap_min": round(gap, 1),
                "next_outcome": n.get("status"), "product": c.get("product_type_name"),
                "airport": c.get("is_airport_trip") == "true",
                "fare_cancelled_usd": fc or None, "fare_next_usd": fn or None, "fare_checked": checked,
            })
            break
    by_c = collections.Counter(m["cancelled_status"] for m in matches)
    total_cx = sum(1 for r in rows if not _completed(r))
    return {
        "window_min": window_min, "fare_tolerance": fare_tolerance, "since": since,
        "cancelled_requests": total_cx, "followed_by_match": len(matches),
        "share": round(len(matches) / total_cx, 3) if total_cx else None,
        "fare_checked_matches": sum(m["fare_checked"] for m in matches),
        "by_cancelled_status": dict(by_c),
        "next_outcomes": dict(collections.Counter(m["next_outcome"] for m in matches)),
        "chains": _chains(matches),
        "matches": matches,
    }


def _chains(matches: list[dict]) -> list[dict]:
    """Link matches where a re-offer was itself cancelled and re-offered again (cancel, re-offer, cancel...)."""
    nxt = {m["cancelled_local"]: m for m in matches}
    starts = {m["cancelled_local"] for m in matches} - {m["next_local"] for m in matches}
    out = []
    for s in sorted(starts):
        chain, cur = [s], nxt.get(s)
        while cur:
            chain.append(cur["next_local"])
            cur = nxt.get(cur["next_local"])
        if len(chain) > 2:
            out.append({"requests": chain, "length": len(chain)})
    return out


# ---------------------------------------------------------------------------
# Earnings, hours, ratings
# ---------------------------------------------------------------------------

def uber_earnings_residual(trips_csv: str | Path, pay_csv: str | Path, since: str | None = None) -> dict:
    """
    Per statement week: earnings and trips from the weekly pay statement, the summed upfront fares of that week's
    completed trips, and the difference. The export has no tip column, so the difference is an ESTIMATE of tips,
    incentives and adjustments together, never tips alone. Weeks start Monday, local time.
    """
    start = _since(since)
    fares = collections.defaultdict(float)
    for r in _rows(trips_csv):
        t = _ts(r.get("request_timestamp_local"))
        if t and _completed(r):
            monday = (t - datetime.timedelta(days=t.weekday())).date().isoformat()
            fares[monday] += _num(r.get("driver_upfront_fare_usd")) or 0
    weeks = collections.defaultdict(lambda: {"earnings": 0.0, "trips": 0})
    for r in _rows(pay_csv):
        w = str(r.get("week", ""))[:10]
        weeks[w]["earnings"] += _num(r.get("total_earnings")) or 0
        weeks[w]["trips"] += int(_num(r.get("total_number_trips")) or 0)
    out = []
    for w in sorted(weeks):
        if start and datetime.datetime.fromisoformat(w) < start - datetime.timedelta(days=6):
            continue
        e, f = weeks[w]["earnings"], fares.get(w, 0.0)
        out.append({"week": w, "statement_earnings": round(e, 2), "trips": weeks[w]["trips"],
                    "upfront_fares": round(f, 2), "residual_estimate": round(e - f, 2),
                    "residual_share": round((e - f) / e, 3) if e else None})
    te = sum(x["statement_earnings"] for x in out); tf = sum(x["upfront_fares"] for x in out)
    return {"since": since, "weeks": out, "statement_earnings": round(te, 2), "upfront_fares": round(tf, 2),
            "residual_estimate": round(te - tf, 2), "residual_share": round((te - tf) / te, 3) if te else None,
            "note": "residual = tips + incentives + adjustments, estimated; the export has no tip column"}


def uber_online_hours(online_csv: str | Path, pay_csv: str | Path | None = None, since: str | None = None) -> dict:
    """Hours per earner state per month (local), utilisation (on-trip / online) and earnings per online hour."""
    start = _since(since)
    hrs = collections.defaultdict(lambda: collections.defaultdict(float))
    for r in _rows(online_csv):
        t = _ts(r.get("begin_timestamp_local"))
        if t and (start is None or t >= start):
            hrs[t.strftime("%Y-%m")][r.get("earner_state", "")] += (_num(r.get("duration_ms")) or 0) / 3.6e6
    pay = collections.defaultdict(float)
    if pay_csv:
        for r in _rows(pay_csv):
            w = str(r.get("week", ""))[:10]
            if w and (start is None or datetime.datetime.fromisoformat(w) >= start - datetime.timedelta(days=6)):
                pay[w[:7]] += _num(r.get("total_earnings")) or 0
    months = []
    for m in sorted(hrs):
        h = hrs[m]
        online = h["open"] + h["enroute"] + h["ontrip"]
        months.append({"month": m, "online_h": round(online, 1), "ontrip_h": round(h["ontrip"], 1),
                       "enroute_h": round(h["enroute"], 1), "open_h": round(h["open"], 1),
                       "utilisation": round(h["ontrip"] / online, 3) if online else None,
                       "earnings": round(pay[m], 2) if pay_csv else None,
                       "earnings_per_online_h": round(pay[m] / online, 2) if pay_csv and online else None})
    return {"since": since, "months": months,
            "note": "online = open + enroute + ontrip; statement weeks counted in the month they start"}


def uber_ratings_summary(ratings_csv: str | Path) -> dict:
    """Ratings received: counts per star and mean. The export gives no date or trip per rating."""
    vals = [int(v) for v in (r.get("five_star_rating") for r in _rows(ratings_csv)) if str(v).strip().isdigit()]
    if not vals:
        return {"error": "No ratings."}
    c = collections.Counter(vals)
    return {"ratings": len(vals), "mean": round(sum(vals) / len(vals), 3),
            "distribution": {str(k): c.get(k, 0) for k in range(5, 0, -1)},
            "below_five": len(vals) - c.get(5, 0), "dated": False,
            "note": "the ratings file has one column (the star value); no date or trip, so no trend can be dated"}


def uber_app_offer_events(analytics_csv: str | Path) -> dict:
    """
    Daily counts of offers received / accepted / rejected / expired, plus rating-screen events, from the app
    analytics file. Only the time and event columns are read; location, device and network columns never are.
    """
    names = {e: k for k, evs in _OFFER_EVENTS.items() for e in evs}
    days = collections.defaultdict(collections.Counter)
    rating_events = collections.Counter()
    with open(analytics_csv, newline="", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        header = next(reader, [])
        idx = {c: header.index(c) for c in _APP_COLUMNS if c in header}
        if "event_name" not in idx or "datestr" not in idx:
            return {"error": "analytics file has no datestr/event_name column (format changed?)",
                    "columns": len(header)}
        for row in reader:
            ev, day = row[idx["event_name"]], row[idx["datestr"]]
            if ev in names:
                days[day][names[ev]] += 1
            elif ev.startswith(("rating_", "post_ratings_")):
                rating_events[ev] += 1
    per_day = [{"date": d, **{k: days[d].get(k, 0) for k in _OFFER_EVENTS}} for d in sorted(days)]
    tot = {k: sum(x[k] for x in per_day) for k in _OFFER_EVENTS}
    return {"first_day": per_day[0]["date"] if per_day else None, "last_day": per_day[-1]["date"] if per_day else None,
            "totals": tot, "per_day": per_day, "rating_events": dict(rating_events),
            "note": "event counts only; an offer may raise more than one event, so totals are not distinct offers"}


# ---------------------------------------------------------------------------
# Census: every file, every column (the SWIM-feed census, for an Uber export)
# ---------------------------------------------------------------------------
# 2026-11-25 (operator): "a census-style run of everything in the Uber file". One pass over every CSV: fill rate,
# distinct count, type, range and (for safe low-cardinality columns) the value counts. Card columns are
# MASKED: only filled / distinct / type are reported, never a value. Free-text columns (message bodies) are never
# returned either: only their length and how many rows mention each topic, per month.

import re as _re

# 2026-11-25 (operator): the only values never reported are card data -- the card number and its BIN (the BIN is
# the number's first six digits). Card type, brand, bank and dates stay, for payment-method analysis.
_SENSITIVE = _re.compile(r"(^|[._ ])(card_number|card_bin|card_num|card_exp\w*|cvv|cvc)$", _re.I)
_TEXT = _re.compile(r"(^|[._ ])(?<!pronoun_)(content|message|subject|body|description)$", _re.I)
_TOPICS = {
    "tip": r"\btip(s|ped)?\b", "rating": r"\brat(ing|ed)\b|\bstars?\b", "cancel": r"\bcancel",
    "fare_adjustment": r"\badjust|\brefund|\boverpa|\bfare review", "account_status": r"deactivat|suspend|waitlist|reactivat",
    "incentive": r"\bquest\b|\bbonus|\bincentive|\bpromotion|\bsurge|\bboost", "airport": r"\bairport|\bqueue|\bdca\b|\biad\b|\bbwi\b",
    "card": r"\bcard\b|\bdebit\b|\bcredit\b|\bvisa\b|\bmastercard\b|\bamex\b|\bbank\b",
    "safety": r"\bsafety|\bincident|\baccident|\bcollision", "documents": r"\binsurance|\binspection|\bregistration|\bdocument|\bbackground check",
    "pay": r"\bpay(ment|out)?\b|\bcash ?out|\bearning|\binstant pay",
}
_TOPIC_RX = {k: _re.compile(v, _re.I) for k, v in _TOPICS.items()}
_MONEY = _re.compile(r"\$\s?(\d{1,4}(?:\.\d{2})?)")
_DISTINCT_CAP = 50_000


def _kind(v: str) -> str:
    s = v.strip()
    if s.lower() in ("true", "false"):
        return "bool"
    if _re.fullmatch(r"-?\d+", s):
        return "int"
    if _re.fullmatch(r"-?\d*\.\d+(e-?\d+)?", s, _re.I):
        return "float"
    if _re.match(r"\d{4}-\d{2}-\d{2}([ T]\d{2}:\d{2})?", s):
        return "timestamp"
    return "text"


def uber_export_census(zip_path: str | Path, enum_max: int = 30) -> dict:
    """Census of every CSV in the export. Returns per file: rows, date span, and per column: filled, fill_rate,
    distinct, kind, masked/free_text flags, min/max (non-masked), value counts (safe columns with <= enum_max
    distinct values), and for free text: length stats plus topic counts per month (and, for 'tip' rows, the summed
    dollar amounts mentioned, per month). No masked or free-text value is ever returned."""
    files = []
    with zipfile.ZipFile(zip_path) as z:
        for name in sorted(n for n in z.namelist() if n.lower().endswith(".csv")):
            with z.open(name) as f:
                reader = csv.reader(io.TextIOWrapper(f, encoding="utf-8-sig", newline=""))
                header = next(reader, [])
                stats = [{"filled": 0, "values": collections.Counter(), "capped": False, "kinds": collections.Counter(),
                          "lens": []} for _ in header]
                text_cols = [i for i, c in enumerate(header) if _TEXT.search(c)]
                ts_col = next((i for i, c in enumerate(header) if "timestamp" in c.lower() or c.lower() in ("date", "datestr", "week", "date created")), None)
                topics = collections.defaultdict(collections.Counter)
                tip_usd = collections.defaultdict(float)
                rows = 0
                for row in reader:
                    rows += 1
                    month = (row[ts_col][:7] if ts_col is not None and ts_col < len(row) else "") or "unknown"
                    for i, v in enumerate(row[:len(header)]):
                        if v == "":
                            continue
                        st = stats[i]
                        st["filled"] += 1
                        if len(st["kinds"]) < 6 or st["filled"] % 50 == 0:
                            st["kinds"][_kind(v)] += 1
                        if i in text_cols:
                            st["lens"].append(len(v))
                            hit = [k for k, rx in _TOPIC_RX.items() if rx.search(v)]
                            for k in hit:
                                topics[month][k] += 1
                            if "tip" in hit:
                                tip_usd[month] += sum(float(m) for m in _MONEY.findall(v))
                        elif not st["capped"]:
                            st["values"][v] += 1
                            if len(st["values"]) > _DISTINCT_CAP:
                                st["capped"] = True
            cols = []
            for i, c in enumerate(header):
                st = stats[i]
                masked, text = bool(_SENSITIVE.search(c)), i in text_cols
                kind = st["kinds"].most_common(1)[0][0] if st["kinds"] else "empty"
                col = {"column": c, "filled": st["filled"], "fill_rate": round(st["filled"] / rows, 3) if rows else 0,
                       "distinct": None if text else (f">{_DISTINCT_CAP}" if st["capped"] else len(st["values"])),
                       "kind": kind, "masked": masked, "free_text": text}
                if text and st["lens"]:
                    col["length"] = {"median": statistics.median(st["lens"]), "max": max(st["lens"])}
                elif not masked and st["values"] and not st["capped"]:
                    keys = list(st["values"])
                    if kind in ("int", "float"):
                        nums = [float(k) for k in keys if _re.fullmatch(r"-?\d*\.?\d+(e-?\d+)?", k, _re.I)]
                        if nums:
                            col["min"], col["max"] = min(nums), max(nums)
                    elif kind == "timestamp" and "dob" not in c.lower():
                        col["min"], col["max"] = min(keys), max(keys)
                    if len(keys) <= enum_max:
                        col["values"] = dict(st["values"].most_common())
                cols.append(col)
            span = None
            if ts_col is not None:
                tc = next(x for x in cols if x["column"] == header[ts_col])
                span = [tc.get("min"), tc.get("max")]
            entry = {"file": name.split("/", 1)[1] if name.startswith("Uber Data/") else name, "rows": rows,
                     "columns": len(header), "date_column": header[ts_col] if ts_col is not None else None,
                     "date_span": span, "column_census": cols}
            if text_cols:
                entry["topics_by_month"] = {m: dict(v) for m, v in sorted(topics.items())}
                entry["tip_usd_mentioned_by_month"] = {m: round(v, 2) for m, v in sorted(tip_usd.items())}
            files.append(entry)
    return {"file_count": len(files), "files": files,
            "note": "masked columns: filled/distinct/kind only; free text: length and topic counts only"}


# ---------------------------------------------------------------------------
# Signals found by the 2026-11-25 census
# ---------------------------------------------------------------------------

def uber_redispatch_signals(trips_csv: str | Path) -> dict:
    """
    Completed trips that still carry a driver_cancellation_reason code. Uber does not document the codes; a code on
    a COMPLETED trip most likely means the job was cancelled by an earlier driver and re-dispatched (an inference,
    labelled as such). Compared with all completed trips by pickup ETA, airport share and month.
    """
    rows = [r for r in _rows(trips_csv) if _completed(r) and _ts(r.get("request_timestamp_local"))]
    coded = [r for r in rows if r.get("driver_cancellation_reason")]
    eta = lambda rs: round(statistics.median(_num(r["eta"]) / 60 for r in rs if _num(r.get("eta")) is not None), 1) if rs else None
    month = collections.Counter(r["request_timestamp_local"][:7] for r in coded)
    total = collections.Counter(r["request_timestamp_local"][:7] for r in rows)
    return {"completed": len(rows), "with_reason_code": len(coded),
            "codes": [{"code": k, "count": v} for k, v in collections.Counter(r["driver_cancellation_reason"] for r in coded).most_common()],
            "median_eta_min": {"with_code": eta(coded), "all_completed": eta(rows)},
            "airport_share": {"with_code": round(sum(r.get("is_airport_trip") == "true" for r in coded) / len(coded), 3) if coded else None,
                              "all_completed": round(sum(r.get("is_airport_trip") == "true" for r in rows) / len(rows), 3) if rows else None},
            "by_month": [{"month": m, "with_code": month.get(m, 0), "completed": total[m]} for m in sorted(total)],
            "note": "codes are undocumented; 're-dispatched after another driver cancelled' is an inference"}


def uber_upfront_fare_honoring(trips_csv: str | Path) -> dict:
    """Completed trips per month whose upfront fare was not honoured (ufp_type = ufp_not_honored)."""
    rows = [r for r in _rows(trips_csv) if _completed(r) and _ts(r.get("request_timestamp_local"))]
    m = collections.defaultdict(lambda: [0, 0])
    for r in rows:
        k = r["request_timestamp_local"][:7]
        m[k][0] += 1
        m[k][1] += r.get("ufp_type") == "ufp_not_honored"
    nh = sorted(r["request_timestamp_local"][:10] for r in rows if r.get("ufp_type") == "ufp_not_honored")
    return {"completed": len(rows), "not_honored": len(nh), "first": nh[0] if nh else None, "last": nh[-1] if nh else None,
            "by_month": [{"month": k, "not_honored": v[1], "completed": v[0], "rate": round(v[1] / v[0], 3) if v[0] else None}
                         for k, v in sorted(m.items())]}


def uber_message_metrics(zip_path: str | Path) -> dict:
    """
    From Uber's messages to the driver (communications_from_uber): tip notices per day (count and the dollar amounts
    they mention; the same day + amount seen in more than one medium is counted once) and rating values quoted per
    day. Returns numbers and dates only, never message text.
    """
    name = next((n for n in zipfile.ZipFile(zip_path).namelist() if n.endswith("communications_from_uber-0.csv")), None)
    if not name:
        return {"error": "no communications_from_uber file (format changed?)"}
    with zipfile.ZipFile(zip_path) as z:
        rows = list(csv.DictReader(io.TextIOWrapper(z.open(name), encoding="utf-8-sig", newline="")))
    tip_rx, rat_rx = _TOPIC_RX["tip"], _TOPIC_RX["rating"]
    seen, tips = set(), collections.defaultdict(lambda: {"notices": 0, "usd": 0.0})
    ratings = collections.defaultdict(set)
    for r in rows:
        text, day = r.get("content", ""), str(r.get("event_timestamp", ""))[:10]
        if tip_rx.search(text):
            amounts = tuple(sorted(_MONEY.findall(text)))
            if (day, amounts) in seen and amounts:
                continue
            seen.add((day, amounts))
            tips[day]["notices"] += 1
            tips[day]["usd"] = round(tips[day]["usd"] + sum(float(a) for a in amounts), 2)
        if rat_rx.search(text):
            for v in _re.findall(r"\b([1-5]\.\d{1,2})\b", text):
                ratings[day].add(v)
    days = sorted(str(r.get("event_timestamp", ""))[:10] for r in rows if r.get("event_timestamp"))
    return {"first_day": days[0] if days else None, "last_day": days[-1] if days else None, "messages": len(rows),
            "tips_by_day": [{"date": d, **v} for d, v in sorted(tips.items())],
            "tip_usd_total": round(sum(v["usd"] for v in tips.values()), 2),
            "rating_values_by_day": [{"date": d, "values": sorted(v)} for d, v in sorted(ratings.items())],
            "note": "tip amounts are those the notices state; the window is only what the export's message file covers"}
