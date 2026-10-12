"""Run: python backend/test_categorizer.py. Rule checks on common merchant types (including truncated statement names), trips, and the home-country setting.
Runs with no parser plugins and a scratch database, so it checks exactly what a fresh checkout does."""
import os
import tempfile

os.environ["PORTFOLIO_DB"] = os.path.join(tempfile.mkdtemp(), "t.db")
os.environ["PORTFOLIO_PARSERS_DIR"] = tempfile.mkdtemp()
import database
database.init_db()
database.seed_if_empty()
import categorizer as CZ

CASES = {
    "A&O EDINBURGH EDINBURGH": ("Travel", "Accommodation"), "HILTON GARDEN INN AUSTIN TX": ("Travel", "Accommodation"),
    "Trainline.com London": ("Travel", "Trains, buses & ferries"), "UBER RIDES TAIPEI CITY": ("Transport", "Taxi & rides"),
    "AIRALO AMSTERDAM": ("Travel", "eSIM & connectivity"), "NETFLIX.COM": ("Subscriptions", "Streaming"), "YOUTUBE PREMIUM": ("Subscriptions", "Streaming"),
    "STARBUCKS STORE 1234": ("Dining", "Cafe & coffee"), "DOORDASH*PIZZA PLACE": ("Dining", "Delivery"), "SHELL SERVICE STATION 55": ("Transport", "Fuel"),
    "CITY PARKING METER": ("Transport", "Parking & tolls"), "ACME PHARMACY #12": ("Health & Fitness", "Pharmacy"), "SUPERFINE GENTS BARBER": ("Personal Care", "Salon & grooming"),
    "STATE UNIVERSITY TUITION": ("Education", "Tuition & courses"), "AMAZON MKTPLACE": ("Shopping", "Online marketplace"), "IKEA STORE": ("Home & Furniture", "Furniture & decor"),
    "RYANAIR 0123456": ("Travel", "Flights"), "BIG SUPERMARKET 22": ("Groceries", "Supermarket"),
}
for d, want in CASES.items():
    got = CZ.classify(d)[:2]
    assert got == want, (d, got, want)

# trips: one per country, from on-the-ground charges only; home, online and booking charges never create or join one
with database.get_db() as c:
    c.execute("INSERT OR REPLACE INTO settings VALUES ('home_country','Canada')")
assert CZ.HOME() == "Canada"


def _t(day, country, cat="Dining", sub=""):
    return {"txn_date": day, "country": country, "category": cat, "subcategory": sub, "amount": 10}


items = ([_t(f"2026-08-{d:02d}", "United States") for d in (10, 10, 11, 12)] + [_t("2026-08-12", "United Kingdom")] + [_t("2026-08-11", None, "Shopping", "Online marketplace")]
         + [_t("2026-08-11", "Canada", "Entertainment")] + [_t("2026-08-11", "Germany", "Travel", "Flights")])
trips = CZ.detect_trips(items)
assert [t["country"] for t in trips] == ["United States"] and "\U0001F1FA\U0001F1F8" in trips[0]["label"], trips
assert CZ.trip_for(trips, "United States", "2026-08-13") and not CZ.trip_for(trips, "United Kingdom", "2026-08-12")
assert CZ.place_of("GetYourGuide Tickets Berlin DEU") == (None, True)
assert CZ.place_of("A&O EDINBURGH EDINBURGH", "GBP")[0] == "United Kingdom" and CZ.place_of("mc zaza cafeedinburgh")[0] == "United Kingdom"
assert CZ.place_of("tst*vickyslos angeles", "USD")[0] == "United States" and CZ.place_of("SOME STORE", "USD")[0] is None
assert CZ.place_of("CAFE CENTRAL LISBON PRT", "EUR")[0] == "Portugal"
# bookings go to the trip they were bought for: next trip within 75 days, or the one whose currency matches
tr = [{"country": "United States", "label": "A", "start": "2026-08-14", "end": "2026-08-18"}, {"country": "United Kingdom", "label": "B", "start": "2026-09-04", "end": "2026-09-12"}]
assert CZ.booking_trip(tr, "2026-08-01") == "A" and CZ.booking_trip(tr, "2026-08-16", "GBP") == "B" and CZ.booking_trip(tr, "2026-05-01") == ""
print("categorizer ok")
