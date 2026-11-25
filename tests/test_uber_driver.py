"""tests/test_uber_driver.py -- gig_mobility/uber_driver.py on small synthetic exports (no real data)."""
from __future__ import annotations

import csv
import io
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gig_mobility import uber_driver as ud  # noqa: E402

TRIP_COLS = ["request_timestamp_local", "status", "is_completed", "product_type_name", "eta", "driver_upfront_fare_usd",
             "is_airport_trip", "is_surged", "trip_offer_type", "driver_cancellation_reason", "cancellation_fee_usd",
             "license_plate"]


def write(path, cols, rows):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in cols})
    return path


def trip(ts, status="completed", product="Comfort", eta=300, fare=20.0, airport="false", **kw):
    return {"request_timestamp_local": ts, "status": status, "is_completed": "true" if status == "completed" else "false",
            "product_type_name": product, "eta": str(eta), "driver_upfront_fare_usd": "" if fare is None else str(fare),
            "is_airport_trip": airport, "is_surged": "false", "trip_offer_type": "standard", "license_plate": "SECRET1", **kw}


def test_cancellations_and_driver_list(tmp_path):
    p = write(tmp_path / "t.csv", TRIP_COLS, [
        trip("2026-09-01 08:00:00"), trip("2026-09-01 09:00:00", "requester_canceled", eta=1200),
        trip("2026-09-02 17:00:00", "provider_canceled", fare=30, driver_cancellation_reason="42", airport="true"),
        trip("2026-08-01 10:00:00", "requester_canceled"),
    ])
    a = ud.uber_cancellation_analysis(p)
    assert a["requests"] == 4 and a["cancelled"] == 3 and a["rate"] == 0.75
    assert [b for b in a["by_eta"] if b["key"] == "15+ min"][0]["cancelled"] == 1
    assert len(a["driver_cancellations"]) == 1 and a["driver_cancellations"][0]["reason_code"] == "42"
    assert "SECRET1" not in str(a)
    import json; json.dumps(a)                                          # JSON-serialisable
    assert ud.uber_cancellation_analysis(p, since="2026-09-01")["requests"] == 3


def test_rerun_detection_matches_same_job_and_chains(tmp_path):
    p = write(tmp_path / "t.csv", TRIP_COLS, [
        trip("2026-09-05 10:00:00", "requester_canceled", fare=25.0),
        trip("2026-09-05 10:06:00", "requester_canceled", fare=26.0),     # re-offer, cancelled again
        trip("2026-09-05 10:15:00", "completed", fare=25.5),              # third offer, completed
        trip("2026-09-05 10:20:00", "completed", fare=60.0),              # different job (fare)
        trip("2026-09-06 12:00:00", "provider_canceled", fare=18.0),
        trip("2026-09-06 12:50:00", "completed", fare=18.0),              # outside the 30-min window
        trip("2026-09-07 12:00:00", "requester_canceled", fare=None),
        trip("2026-09-07 12:05:00", "completed", fare=None),              # matches on time/product only
    ])
    r = ud.uber_rerun_detection(p)
    assert r["cancelled_requests"] == 4 and r["followed_by_match"] == 3
    assert r["fare_checked_matches"] == 2
    assert r["chains"] == [{"requests": ["2026-09-05 10:00", "2026-09-05 10:06", "2026-09-05 10:15"], "length": 3}]
    assert all(m["next_local"] != "2026-09-05 10:20" for m in r["matches"])


def test_earnings_residual_by_week(tmp_path):
    t = write(tmp_path / "t.csv", TRIP_COLS, [trip("2026-09-01 08:00:00", fare=20), trip("2026-09-03 08:00:00", fare=30),
                                              trip("2026-09-04 08:00:00", "requester_canceled", fare=40)])
    pay = write(tmp_path / "p.csv", ["firstname", "week", "total_earnings", "total_number_trips"],
                [{"firstname": "NAME", "week": "2026-08-31 00:00:00", "total_earnings": "62.5", "total_number_trips": "2"}])
    e = ud.uber_earnings_residual(t, pay)
    assert e["weeks"] == [{"week": "2026-08-31", "statement_earnings": 62.5, "trips": 2, "upfront_fares": 50.0,
                           "residual_estimate": 12.5, "residual_share": 0.2}]
    assert "NAME" not in str(e)


def test_online_hours(tmp_path):
    o = write(tmp_path / "o.csv", ["earner_state", "begin_timestamp_local", "duration_ms"], [
        {"earner_state": "open", "begin_timestamp_local": "2026-09-01 08:00:00", "duration_ms": str(3 * 3.6e6)},
        {"earner_state": "ontrip", "begin_timestamp_local": "2026-09-01 11:00:00", "duration_ms": str(1 * 3.6e6)},
        {"earner_state": "offline", "begin_timestamp_local": "2026-09-01 12:00:00", "duration_ms": str(9 * 3.6e6)}])
    m = ud.uber_online_hours(o)["months"][0]
    assert m["online_h"] == 4.0 and m["utilisation"] == 0.25


def test_ratings_are_undated(tmp_path):
    p = write(tmp_path / "r.csv", ["five_star_rating"], [{"five_star_rating": v} for v in "55541"])
    r = ud.uber_ratings_summary(p)
    assert r["distribution"] == {"5": 3, "4": 1, "3": 0, "2": 0, "1": 1} and r["dated"] is False and r["mean"] == 4.0


def test_app_events_read_only_event_columns(tmp_path):
    cols = ["datestr", "time", "action_type", "event_name", "msg.location.lat", "msg.driver_app.device.ip_address"]
    p = write(tmp_path / "a.csv", cols, [
        {"datestr": "2026-11-01", "event_name": "marketplace_unified_offer_received", "msg.location.lat": "38.9", "msg.driver_app.device.ip_address": "10.9.9.9"},
        {"datestr": "2026-11-01", "event_name": "dx_unified_offer_focus_reject_tap"},
        {"datestr": "2026-11-02", "event_name": "driveroffersjobboard_offers_expirysuccess"},
        {"datestr": "2026-11-02", "event_name": "rating_raterider_shown"}])
    e = ud.uber_app_offer_events(p)
    assert e["totals"] == {"received": 1, "accepted": 0, "rejected": 1, "expired": 1}
    assert e["rating_events"] == {"rating_raterider_shown": 1}
    assert "38.9" not in str(e) and "10.9.9.9" not in str(e)
    bad = write(tmp_path / "b.csv", ["when", "what"], [{"when": "x", "what": "y"}])
    assert "error" in ud.uber_app_offer_events(bad)


def test_fingerprint_and_diff(tmp_path):
    def mk(path, header):
        with zipfile.ZipFile(path, "w") as z:
            z.writestr("Uber Data/Driver/a.csv", header + "\nvalue1,value2\n")
            z.writestr("Uber Data/readme.html", "<p>x</p>")
        return path
    f1 = ud.uber_format_fingerprint(mk(tmp_path / "1.zip", "x,y"))
    f2 = ud.uber_format_fingerprint(mk(tmp_path / "2.zip", "x,z"))
    assert f1["file_count"] == 1 and f1["files"][0]["rows"] == 1 and "value1" not in str(f1)
    d = ud.uber_fingerprint_diff(f1, f2)
    assert d["changed_files"] == [{"file": "Driver/a.csv", "added": ["z"], "removed": ["y"], "reordered_only": False}]
    assert ud.uber_fingerprint_diff(f1, f1)["unchanged"] is True


def test_census_masks_card_data_and_free_text(tmp_path):
    z = tmp_path / "e.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("Uber Data/Account and Profile/payment_methods-0.csv",
                    "payment_added,card_type,card_bin,card_brand,bank_name,is_disbursements\n2025-02-01,debit,411111,visa,Example Bank,true\n")
        zf.writestr("Uber Data/Account and Profile/communications_from_uber-0.csv",
                    "medium,content,direction,event_timestamp\n"
                    "push,Your rider tipped you $5.00 on card 4111111111111111,out,2026-09-03 10:00:00\n"
                    "sms,Your rating is 4.90 stars,out,2026-10-01 10:00:00\n")
        zf.writestr("Uber Data/Rider/rider_lifetime_trips-0.csv",
                    "card_number,payment_type,product_type_name\n4111111111111111,card,uberX\n")
    c = ud.uber_export_census(z)
    blob = str(c)
    for secret in ("411111", "4111111111111111", "tipped you"):
        assert secret not in blob, secret
    pm = {x["column"]: x for x in c["files"][1]["column_census"]}
    assert pm["card_brand"]["values"] == {"visa": 1} and pm["card_bin"]["masked"] is True and "values" not in pm["card_bin"]
    comm = next(f for f in c["files"] if f["file"].endswith("communications_from_uber-0.csv"))
    assert comm["topics_by_month"]["2026-09"]["tip"] == 1 and comm["topics_by_month"]["2026-09"]["card"] == 1
    assert comm["topics_by_month"]["2026-10"]["rating"] == 1
    assert comm["tip_usd_mentioned_by_month"] == {"2026-09": 5.0}
    assert comm["date_span"] == ["2026-09-03 10:00:00", "2026-10-01 10:00:00"]
    import json; json.dumps(c)


def test_redispatch_and_fare_honoring(tmp_path):
    cols = TRIP_COLS + ["ufp_type"]
    p = write(tmp_path / "t.csv", cols, [
        trip("2025-03-01 10:00:00", driver_cancellation_reason="12", ufp_type="ufp_not_honored", eta=600),
        trip("2025-03-02 10:00:00", ufp_type="ufp_honored"),
        trip("2026-03-01 10:00:00", ufp_type="ufp_honored"),
        trip("2026-03-02 10:00:00", "requester_canceled", driver_cancellation_reason="16")])
    r = ud.uber_redispatch_signals(p)
    assert r["completed"] == 3 and r["with_reason_code"] == 1 and r["codes"] == [{"code": "12", "count": 1}]
    u = ud.uber_upfront_fare_honoring(p)
    assert u["not_honored"] == 1 and u["first"] == u["last"] == "2025-03-01"
    assert [m["rate"] for m in u["by_month"]] == [0.5, 0.0]


def test_message_metrics_dedupes_tips_across_mediums(tmp_path):
    z = tmp_path / "m.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("Uber Data/Account and Profile/communications_from_uber-0.csv",
                    "medium,content,direction,event_timestamp\n"
                    "push,A rider tipped you $5.00,out,2026-11-01 10:00:00\n"
                    "inbox,A rider tipped you $5.00,out,2026-11-01 10:05:00\n"
                    "inbox,A rider tipped you $3.00,out,2026-11-01 11:00:00\n"
                    "inbox,Your rating is 4.87,out,2026-11-02 09:00:00\n")
    m = ud.uber_message_metrics(z)
    assert m["tips_by_day"] == [{"date": "2026-11-01", "notices": 2, "usd": 8.0}] and m["tip_usd_total"] == 8.0
    assert m["rating_values_by_day"] == [{"date": "2026-11-02", "values": ["4.87"]}]
    assert "tipped you" not in str(m)


def test_run_all_and_synthesis_end_to_end(tmp_path):
    from gig_mobility import uber_report as ur
    z = tmp_path / "x.zip"
    trips = io.StringIO(); w = csv.DictWriter(trips, fieldnames=TRIP_COLS + ["original_fare_usd", "begintrip_timestamp_local", "ufp_type"]); w.writeheader()
    for r in (trip("2026-09-01 08:00:00", original_fare_usd="30", begintrip_timestamp_local="2026-09-01 08:05:00"),
              trip("2026-09-01 09:00:00", "requester_canceled", original_fare_usd="0")):
        w.writerow({**{c: "" for c in w.fieldnames}, **r})
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("Uber Data/Driver/driver_lifetime_trips-0.csv", trips.getvalue())
        zf.writestr("Uber Data/Driver/driver_weekly_pay_statements-0.csv",
                    "firstname,week,total_earnings,total_number_trips,license_plate\nNAME,2026-08-31 00:00:00,25,1,PLATE9\n")
        zf.writestr("Uber Data/Driver/driver_lifetime_ratings_received-0.csv", "five_star_rating\n5\n4\n")
        zf.writestr("Uber Data/Account and Profile/payment_methods-0.csv", "profile_type,card_bin,is_collections\nGOOGLE_PAY,411111,true\n")
    res = ur.uber_run_all(z, tmp_path / "work", since="2026-09-01")
    assert res["normalize"]["record_count"] == 1 and res["cancel_all"]["cancelled"] == 1
    md = ur.uber_synthesis_markdown(res, "2026-11-25", "abc1234", previous_fingerprint=res["fingerprint"], mirror=True)
    for h in ("## 1. Account", "## 2. Export format", "## 6. Cancellations", "## 10. Payment methods", "## 12. Provenance"):
        assert h in md
    assert "no format change" in md and "mirror_of:" in md and "411111" not in md and "PLATE9" not in md and "NAME" not in md
