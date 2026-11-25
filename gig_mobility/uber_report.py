"""
gig_mobility/uber_report.py -- one call for a whole Uber driver export, and the fixed-format synthesis note.

2026-11-25 (operator): every export is analysed by the same code and written up in the same shape, so the vault's
history/ entries form a longitudinal archive and a change in Uber's formats shows up as a changed fingerprint.

    results = uber_run_all(zip_path, workdir, since="2026-09-01")      # extracts to workdir (scratch), runs all
    note    = uber_synthesis_markdown(results, export_date="2026-11-25", toolkit_rev="abc1234",
                                      previous_fingerprint=None)       # the vault note body

uber_run_all extracts only into `workdir` (the caller's scratchpad, deleted after the run). Nothing it returns
contains card data or message text. gig_normalize_export is called on the driver trip file; that file has no
coordinates, so no reverse-geocoding call is made (if a future export adds dropoff coordinates, uber_run_all
refuses to run the normaliser unless geocode=True is passed, the skill's consent gate).
"""

from __future__ import annotations

import csv
import io
import zipfile
from pathlib import Path

from . import uber_driver as ud
from .gig_analysis import (gig_availability_heatmap, gig_cluster_analysis, gig_normalize_export,
                           gig_revenue_analysis)

_FILES = {
    "trips": "Driver/driver_lifetime_trips-0.csv",
    "pay": "Driver/driver_weekly_pay_statements-0.csv",
    "online": "Driver/driver_online_offline-0.csv",
    "ratings": "Driver/driver_lifetime_ratings_received-0.csv",
    "analytics": "Driver/driver_app_analytics-0.csv",
}
_ORDER = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _extract(zip_path: str | Path, workdir: str | Path) -> dict:
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True, mode=0o700)
    out = {}
    with zipfile.ZipFile(zip_path) as z:
        names = z.namelist()
        for key, rel in _FILES.items():
            name = next((n for n in names if n.endswith(rel)), None)
            if name:
                dest = workdir / Path(rel).name
                with z.open(name) as src, open(dest, "wb") as dst:
                    dst.write(src.read())
                out[key] = str(dest)
    return out


def uber_run_all(zip_path: str | Path, workdir: str | Path, since: str | None = None, geocode: bool = False) -> dict:
    """Run every analysis on one export. `since` (ISO date) adds the windowed views next to the lifetime ones."""
    f = _extract(zip_path, workdir)
    res: dict = {"since": since, "files_found": sorted(f), "files_missing": sorted(set(_FILES) - set(f))}
    res["fingerprint"] = ud.uber_format_fingerprint(zip_path)
    res["census"] = ud.uber_export_census(zip_path)
    if "trips" in f:
        with open(f["trips"], newline="", encoding="utf-8-sig") as fh:
            header = next(csv.reader(fh), [])
        if any(c in header for c in ("dropoff_lat", "Dropoff Lat", "end_lat")) and not geocode:
            res["normalize"] = {"error": "trip file has coordinates: gig_normalize_export would geocode; pass geocode=True only with consent"}
        else:
            n = gig_normalize_export(f["trips"], platform="uber")
            recs = n.get("records", [])
            res["normalize"] = {k: v for k, v in n.items() if k != "records"}
            res["revenue"] = gig_revenue_analysis(recs) if recs else {"error": "no records"}
            res["time_of_day"] = gig_cluster_analysis(recs, "time_of_day")
            res["day_of_week"] = gig_cluster_analysis(recs, "day_of_week")
            res["heatmap_cells"] = len(gig_availability_heatmap(recs)["cells"])
            res["normalized_records"] = recs
        res["cancel_all"] = ud.uber_cancellation_analysis(f["trips"])
        res["rerun_all"] = ud.uber_rerun_detection(f["trips"])
        res["redispatch"] = ud.uber_redispatch_signals(f["trips"])
        res["ufp"] = ud.uber_upfront_fare_honoring(f["trips"])
        if since:
            res["cancel_since"] = ud.uber_cancellation_analysis(f["trips"], since=since)
            res["rerun_since"] = ud.uber_rerun_detection(f["trips"], since=since)
        if "pay" in f:
            res["earn_all"] = ud.uber_earnings_residual(f["trips"], f["pay"])
            if since:
                res["earn_since"] = ud.uber_earnings_residual(f["trips"], f["pay"], since=since)
    if "online" in f:
        res["hours_all"] = ud.uber_online_hours(f["online"], f.get("pay"))
    if "ratings" in f:
        res["ratings"] = ud.uber_ratings_summary(f["ratings"])
    if "analytics" in f:
        res["app"] = ud.uber_app_offer_events(f["analytics"])
    res["messages"] = ud.uber_message_metrics(zip_path)
    res["profile"] = _profile(res["census"])
    return res


def _profile(census: dict) -> dict:
    """Non-identity account facts from the driver profile census (rating, trip count, loyalty, first/last trip)."""
    keep = ("status", "app_rating", "lifetime_completed_trips", "driver_loyalty_engagement_tier",
            "is_driver_loyalty_enrolled", "driver_loyalty_enrolled_at_timestamp", "driver_loyalty_period_balance",
            "driver_loyalty_qual_period_start_timestamp", "driver_loyalty_qual_period_end_timestamp",
            "first_completed_trip_timestamp", "last_completed_trip_timestamp", "is_fleet_driver", "signup_timestamp")
    f = next((x for x in census.get("files", []) if x["file"].endswith("driver_account_profile_information-0.csv")), None)
    if not f:
        return {}
    out = {}
    for c in f["column_census"]:
        if c["column"] in keep and c.get("values") and len(c["values"]) == 1:
            out[c["column"]] = next(iter(c["values"]))
    return out


def _usd(v) -> str:
    return "—" if v is None else f"${v:,.2f}"


def _rate_table(rows: list[dict], label: str) -> list[str]:
    out = [f"| {label} | requests | cancelled | rate |", "|---|---|---|---|"]
    out += [f"| {b['key']} | {b['requests']} | {b['cancelled']} | {b['rate']:.1%} |" for b in rows]
    return out


def uber_synthesis_markdown(res: dict, export_date: str, toolkit_rev: str, previous_fingerprint: dict | None = None,
                            mirror: bool = False) -> str:
    """The vault note, in a fixed section order. mirror=True adds the CURRENT.md mirror fields."""
    L: list[str] = []
    A = L.append
    A("---")
    A(f"title: Uber export synthesis {export_date}")
    A(f"export_date: {export_date}")
    A("platform: uber")
    A(f"toolkit_rev: {toolkit_rev}")
    if mirror:
        A(f"mirror_of: 04-Syntheses/personal-uber-exports/history/{export_date}.md")
        A(f"mirror_updated: {export_date}")
    A("tags: [personal, uber, gig-export]")
    A("---")
    A("")
    A(f"# Uber export synthesis — {export_date}")
    A("")
    A(f"Generated by `gig_mobility.uber_report` (toolkit `{toolkit_rev}`) through the `personal-export-analysis` "
      "skill. Same code, same sections every export, so history entries compare line for line. Local times are "
      "America/New_York wall clock as exported. No card data, no message text.")
    since = res.get("since")

    # 1. Account
    p = res.get("profile", {})
    A("")
    A("## 1. Account")
    A("")
    if p:
        A("| field | value |")
        A("|---|---|")
        for k, v in p.items():
            A(f"| {k} | {v} |")
    else:
        A("No driver profile file in this export.")

    # 2. Format
    fp = res.get("fingerprint", {})
    A("")
    A("## 2. Export format")
    A("")
    A(f"{fp.get('file_count', 0)} CSV files. Analysis inputs missing: {', '.join(res.get('files_missing') or []) or 'none'}.")
    if previous_fingerprint:
        d = ud.uber_fingerprint_diff(previous_fingerprint, fp)
        if d["unchanged"]:
            A("Compared with the previous export: **no format change**.")
        else:
            A("Compared with the previous export: **format changed**.")
            for x in d["added_files"]:
                A(f"- file added: `{x}`")
            for x in d["removed_files"]:
                A(f"- file removed: `{x}`")
            for c in d["changed_files"]:
                A(f"- `{c['file']}`: added {c['added'] or '—'}, removed {c['removed'] or '—'}"
                  + (" (order only)" if c["reordered_only"] else ""))
    else:
        A("No previous fingerprint supplied: this entry is the baseline for the next comparison.")
    A("")
    A("| file | rows | columns | header sha256 |")
    A("|---|---|---|---|")
    for x in fp.get("files", []):
        A(f"| `{x['file']}` | {x['rows']} | {x['columns']} | `{x['header_sha256']}` |")

    # 3. Trips and rider fares (gig_analysis)
    n, r = res.get("normalize", {}), res.get("revenue", {})
    A("")
    A("## 3. Trips and rider fares (gig_analysis)")
    A("")
    if "error" in n:
        A(f"Not run: {n['error']}.")
    else:
        A(f"Normalised {n.get('record_count')} trips, skipped {n.get('skipped')} (no begin-trip time: cancelled or "
          f"unfulfilled). Availability-heatmap cells: {res.get('heatmap_cells')} (no coordinates in the driver file). "
          "`fare_usd` is `original_fare_usd`: what the RIDER was charged, not driver pay.")
        if r and "error" not in r:
            A("")
            A("| trips | total | mean | median | p10 | p25 | p75 | p90 | min | max |")
            A("|---|---|---|---|---|---|---|---|---|---|")
            A(f"| {r['record_count']} | {_usd(r['total_usd'])} | {_usd(r['mean_usd'])} | {_usd(r['median_usd'])} | "
              f"{_usd(r['p10_usd'])} | {_usd(r['p25_usd'])} | {_usd(r['p75_usd'])} | {_usd(r['p90_usd'])} | "
              f"{_usd(r['min_usd'])} | {_usd(r['max_usd'])} |")
            fb = r["fare_buckets"]
            A("")
            A(f"Fare buckets: high (≥$30) {fb.get('high', 0)}, mid ($12–30) {fb.get('mid', 0)}, low (<$12) {fb.get('low', 0)}.")
        A("")
        A("| day | trips | mean fare |")
        A("|---|---|---|")
        for d in sorted(res.get("day_of_week", {}).get("clusters", []), key=lambda c: _ORDER.index(c["key"])):
            A(f"| {d['key']} | {d['trip_count']} | {_usd(d['mean_usd'])} |")
        A("")
        A("| hour (local) | trips | mean fare |")
        A("|---|---|---|")
        for c in sorted(res.get("time_of_day", {}).get("clusters", []), key=lambda c: c["key"]):
            A(f"| {c['key']} | {c['trip_count']} | {_usd(c['mean_usd'])} |")

    # 4. Driver pay, hours, tips
    A("")
    A("## 4. Pay, hours and tips")
    A("")
    ea = res.get("earn_all")
    if ea:
        A(f"Weekly statements, lifetime: {_usd(ea['statement_earnings'])}; upfront fares of completed trips "
          f"{_usd(ea['upfront_fares'])}; difference {_usd(ea['residual_estimate'])} ({ea['residual_share']:.0%}). "
          "The difference mixes tips, incentives, adjustments and timing; it is NOT a tip figure.")
    h = res.get("hours_all", {}).get("months", [])
    if h:
        A("")
        A("| month | online h | on-trip h | utilisation | statement earnings | per online hour |")
        A("|---|---|---|---|---|---|")
        for m in h:
            A(f"| {m['month']} | {m['online_h']} | {m['ontrip_h']} | {m['utilisation']:.1%} | {_usd(m['earnings'])} | "
              f"{_usd(m['earnings_per_online_h'])} |" if m["utilisation"] is not None else f"| {m['month']} | 0 | 0 | — | — | — |")
    es = res.get("earn_since")
    if es:
        A("")
        A(f"Since {since}, by statement week:")
        A("")
        A("| week | statement earnings | trips | upfront fares | difference |")
        A("|---|---|---|---|---|")
        for w in es["weeks"]:
            A(f"| {w['week']} | {_usd(w['statement_earnings'])} | {w['trips']} | {_usd(w['upfront_fares'])} | {_usd(w['residual_estimate'])} |")
    msg = res.get("messages", {})
    if "error" not in msg:
        A("")
        A(f"Tips stated in Uber's notices ({msg.get('first_day')} to {msg.get('last_day')}, the whole span of the "
          f"export's message file): {sum(d['notices'] for d in msg.get('tips_by_day', []))} notices, "
          f"{_usd(msg.get('tip_usd_total'))}. The export has no per-trip tip column; this is the only tip source.")
        if msg.get("tips_by_day"):
            A("")
            A("| date | notices | stated |")
            A("|---|---|---|")
            for d in msg["tips_by_day"]:
                A(f"| {d['date']} | {d['notices']} | {_usd(d['usd'])} |")

    # 5. Ratings
    rt = res.get("ratings", {})
    A("")
    A("## 5. Ratings")
    A("")
    if rt and "error" not in rt:
        dist = ", ".join(f"{k}★ {v}" for k, v in rt["distribution"].items())
        A(f"Ratings received (lifetime file, undated): {rt['ratings']}, mean {rt['mean']}; {dist}.")
        cur = p.get("app_rating")
        if cur:
            A(f"Current app rating (profile): {cur}.")
            try:
                c, n_, m_ = float(cur), rt["ratings"], rt["mean"]
                if n_ > 500:
                    older = (n_ * m_ - 500 * c) / (n_ - 500)
                    A(f"If the app rating is the mean of the latest 500 ratings (Uber's published rule), the "
                      f"{n_ - 500} older ratings averaged {older:.3f}: the drop is in the recent 500. Inference; "
                      "the export dates no rating.")
            except (TypeError, ValueError):
                pass
        for d in msg.get("rating_values_by_day", []) if "error" not in msg else []:
            A(f"- rating quoted in Uber's messages on {d['date']}: {', '.join(d['values'])}")

    # 6. Cancellations
    ca = res.get("cancel_all", {})
    A("")
    A("## 6. Cancellations")
    A("")
    if ca and "error" not in ca:
        st = ca["by_status"]
        A(f"Lifetime: {ca['cancelled']} of {ca['requests']} requests ({ca['rate']:.1%}): "
          + ", ".join(f"{k} {v}" for k, v in st.items())
          + f". Median pickup ETA {ca['median_eta_min']['cancelled']} min cancelled vs "
            f"{ca['median_eta_min']['completed']} min completed. Cancellation fees paid to the driver: "
            f"{_usd(ca['cancellation_fees_usd'])} on {ca['paid_cancellations']} requests.")
        cs = res.get("cancel_since")
        if cs and "error" not in cs:
            A(f"Since {since}: {cs['cancelled']} of {cs['requests']} ({cs['rate']:.1%}).")
        for key, label in (("by_eta", "pickup ETA"), ("by_product", "product"), ("by_day", "day"),
                           ("by_airport", "airport"), ("by_surge", "surge"), ("by_offer_type", "offer type"),
                           ("by_fare_band", "upfront fare")):
            A("")
            L.extend(_rate_table(ca[key], label))
        A("")
        A("(“no upfront fare” is mostly cancelled requests: the export drops the fare once a request is cancelled, "
          "so that row is a recording effect, not a cause.)")
        A("")
        A("Driver's own cancellations:")
        A("")
        A("| request (local) | day | product | offer | ETA min | upfront fare | airport | surged | code | fee paid |")
        A("|---|---|---|---|---|---|---|---|---|---|")
        for d in ca["driver_cancellations"]:
            A(f"| {d['request_local']} | {d['day']} | {d['product']} | {d['offer_type']} | {d['eta_min']} | "
              f"{_usd(d['upfront_fare_usd'])} | {'yes' if d['airport'] else 'no'} | {'yes' if d['surged'] else 'no'} | "
              f"{d['reason_code'] or '—'} | {_usd(d['cancellation_fee_usd'])} |")

    # 7. Re-offers
    ra = res.get("rerun_all", {})
    A("")
    A("## 7. Re-offered jobs")
    A("")
    if ra:
        A(f"A cancelled request followed within {ra['window_min']:.0f} min by a request for the same product and "
          f"airport flag (upfront fare within {ra['fare_tolerance']:.0%} where both have one): "
          f"{ra['followed_by_match']} of {ra['cancelled_requests']} cancelled requests ({ra['fare_checked_matches']} "
          f"fare-checked). Outcomes of the re-offer: "
          + ", ".join(f"{k} {v}" for k, v in ra["next_outcomes"].items()) + ".")
        for c in ra.get("chains", []):
            A(f"- repeated chain: {' → '.join(c['requests'])}")
        rs = res.get("rerun_since")
        if rs:
            A(f"Since {since}: {rs['followed_by_match']} of {rs['cancelled_requests']}.")
        A("")
        A("| cancelled (local) | status | re-offer | gap min | outcome | product | fares |")
        A("|---|---|---|---|---|---|---|")
        for m in ra["matches"]:
            A(f"| {m['cancelled_local']} | {m['cancelled_status']} | {m['next_local']} | {m['gap_min']} | "
              f"{m['next_outcome']} | {m['product']} | {_usd(m['fare_cancelled_usd'])} → {_usd(m['fare_next_usd'])} |")
    rd = res.get("redispatch", {})
    if rd:
        A("")
        A(f"Completed trips that still carry a cancellation reason code: {rd['with_reason_code']} of {rd['completed']}; "
          f"median pickup ETA {rd['median_eta_min']['with_code']} min vs {rd['median_eta_min']['all_completed']} min "
          "for all completed. Likely jobs re-dispatched after another driver cancelled (codes are undocumented: inference).")
        A("")
        A("Codes: " + ", ".join(f"{c['code']} ×{c['count']}" for c in rd["codes"]) + ".")

    # 8. Upfront fare honouring
    u = res.get("ufp", {})
    A("")
    A("## 8. Upfront fare not honoured")
    A("")
    if u:
        A(f"{u['not_honored']} of {u['completed']} completed trips (first {u['first']}, last {u['last']}).")
        A("")
        A("| month | not honoured | completed | rate |")
        A("|---|---|---|---|")
        for m in u["by_month"]:
            A(f"| {m['month']} | {m['not_honored']} | {m['completed']} | {m['rate']:.1%} |")

    # 9. Offers (app analytics)
    ap = res.get("app", {})
    A("")
    A("## 9. Offers seen in the app")
    A("")
    if ap and "error" not in ap:
        t = ap["totals"]
        A(f"App analytics span {ap['first_day']} to {ap['last_day']} only. Event counts (not distinct offers): "
          f"received {t['received']}, accepted {t['accepted']}, rejected {t['rejected']}, expired {t['expired']}. "
          "Rating-screen events: " + ", ".join(f"{k} {v}" for k, v in ap.get("rating_events", {}).items()) + ".")
    elif ap:
        A(f"Not run: {ap.get('error')}.")

    # 10. Payment methods
    A("")
    A("## 10. Payment methods")
    A("")
    pm = next((x for x in res.get("census", {}).get("files", []) if x["file"].endswith("payment_methods-0.csv")), None)
    if pm:
        cols = {c["column"]: c for c in pm["column_census"]}
        show = [k for k in ("profile_type", "card_type", "card_brand", "bank_name", "is_collections", "is_disbursements", "payment_added", "deleted_at")
                if cols.get(k, {}).get("filled")]
        A(f"{pm['rows']} methods. " + "; ".join(f"{k}: {cols[k].get('values') or cols[k].get('min')}" for k in show) + ".")
        A("Card numbers and BINs are never read into this note.")
    rt_file = next((x for x in res.get("census", {}).get("files", []) if x["file"].endswith("rider_lifetime_trips-0.csv")), None)
    if rt_file:
        pt = next((c for c in rt_file["column_census"] if c["column"] == "payment_type"), None)
        if pt and pt.get("values"):
            A(f"Rider-side trips ({rt_file['rows']}): payment_type {pt['values']}.")

    # 11. Support and messages
    A("")
    A("## 11. Support tickets and messages (topic counts only)")
    A("")
    for x in res.get("census", {}).get("files", []):
        if "topics_by_month" in x and x["rows"]:
            tot: dict = {}
            for v in x["topics_by_month"].values():
                for k, c in v.items():
                    tot[k] = tot.get(k, 0) + c
            A(f"- `{x['file']}`: {x['rows']} rows, {x['date_span'][0] if x['date_span'] else '?'} to "
              f"{x['date_span'][1] if x['date_span'] else '?'}; topics " + (", ".join(f"{k} {v}" for k, v in sorted(tot.items(), key=lambda i: -i[1])) or "none"))

    # 12. Provenance
    A("")
    A("## 12. Provenance")
    A("")
    A(f"- Source: the operator's own Uber data export, received {export_date}; extracted to the agent scratchpad and deleted after the run.")
    A("- The full census (every column's fill, distinct count, type and range) is kept with the raw export in the "
      "longitudinal store, not in the vault.")
    A("- Prepared by ctdc-agent-anthropic-claude-ea (draft; the operator places it in the vault).")
    return "\n".join(L) + "\n"
