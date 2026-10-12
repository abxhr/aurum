"""Two-level spend categorisation (category > sub-category) with trip awareness.

Order of authority:
  1. a merchant you corrected yourself (merchant_categories, source 'user')   never overridden
  2. the rule table below (first match wins; specific before general)
  3. what other merchants sharing the same brand word were filed under       (learned, needs a clear majority)
  4. 'Other'
Trips are detected separately from spending abroad, so a meal or a taxi on a trip keeps its category and gains a trip tag.
"""
import re
from collections import Counter, defaultdict
from datetime import date, timedelta

import plugins
from database import get_db, get_setting

TAXONOMY = {
    "Groceries": ["Supermarket", "Convenience store", "Quick delivery"],
    "Dining": ["Restaurant", "Cafe & coffee", "Fast food", "Delivery", "Bakery & dessert", "Bars & lounges", "Work canteen"],
    "Transport": ["Fuel", "Taxi & rides", "Public transit", "Parking & tolls", "Car services", "Car rental"],
    "Travel": ["Flights", "Accommodation", "Tours & activities", "Trains, buses & ferries", "Visas & documents", "eSIM & connectivity", "Travel services"],
    "Shopping": ["Clothing & shoes", "Jewellery & accessories", "Perfume & beauty", "Online marketplace", "Gifts & flowers", "Sports & outdoor", "Books & stationery", "General retail"],
    "Entertainment": ["Cinema & shows", "Attractions & leisure", "Gaming", "Events & tickets"],
    "Subscriptions": ["Streaming", "Software & AI", "Cloud storage", "Memberships"],
    "Bills & Utilities": ["Mobile & internet", "Utilities", "Insurance", "Rent & property"],
    "Health & Fitness": ["Pharmacy", "Clinic & hospital", "Optical", "Gym & fitness", "Nutrition"],
    "Personal Care": ["Salon & grooming", "Spa & wellness", "Tailoring & laundry"],
    "Education": ["Tuition & courses", "Driving & exams"],
    "Home & Furniture": ["Furniture & decor", "Hardware & repairs"],
    "Electronics": ["Devices", "Repairs & accessories"],
    "Government & Fines": ["Government services", "Fines", "Post & courier"],
    "Cash & ATM": ["Cash withdrawal", "Exchange"],
    "Fees & Charges": ["Bank fees", "VAT", "Late-payment fee"],
    "Charity": ["Donations"],
    "Payments & Transfers": ["Card payment"],
    "Internal": ["Profit netting", "Investing"],
    "Other": [""],
}

# (category, sub-category, regex). First match wins, so specific rules sit above general ones. Matched against lower-cased text.
# (category, sub-category, regex). First match wins, so specific rules sit above general ones. Matched against lower-cased text. Local rules for your own merchants
# go in a parser plugin (plugins.py: CATEGORY_RULES, CATEGORY_SEED); they are tried first.
_RAW = [
    ('Internal', 'Investing', 'etoro|interactive brokers|\\bibkr\\b'),
    ('Internal', 'Profit netting', '^bonus$|\\bbilled profit'),
    ('Payments & Transfers', 'Card payment', 'payment received|thank you|credit card payment|standing instruction|transfer payment|auto debit|mobile payment|loyalty points redemption|welcome bonus|cashback redeemed|cashback for|% cashback'),
    ('Fees & Charges', 'Late-payment fee', 'late payment'),
    ('Fees & Charges', 'VAT', '\\bvat\\b'),
    ('Fees & Charges', 'Bank fees', 'annual fee|svc chg|service charge|card fee|fx fee|foreign transaction fee|overlimit|interest charge|annual membership'),
    ('Cash & ATM', 'Cash withdrawal', 'atm|cash advance|cash withdrawal'),
    ('Charity', 'Donations', 'charit|donation|orphan'),
    ('Travel', 'eSIM & connectivity', 'airalo|esim|holafly|nomad sim'),
    ('Travel', 'Flights', 'airlines?|airways?|^fly\\b|ryanair|easyjet|wizz|\\bunited \\d|lufthansa|klm|flight (booking|ticket)'),
    ('Travel', 'Accommodation', 'hotel|hostel|resort|\\binn\\b|premier inn|marriott|hilton|radisson|booking\\.com|\\bbkg\\b|agoda|airbnb|meininger|a&o |\\bmotel|lodge|apartment|villa |ramada|holiday inn|sheraton|hyatt|novotel|ibis|accor|\\bb&b\\b|guest ?house|a and o'),
    ('Travel', 'Visas & documents', '\\bvisa\\b|e-?visa|ukvi|bls international|vfs|ecitizen|immigration|passport|embassy|consulate'),
    ('Travel', 'Trains, buses & ferries', 'trainline|\\btrains?\\b|\\brail|sncf|trenitalia|\\bgare\\b|flixbus|omio|ferry|ferries|eurostar|national express|bus station|\\bbus\\b|megabus|scotrail|lner|avanti|onward travel'),
    ('Travel', 'Tours & activities', 'getyourguide|klook|todaytix|viator|tiqets|musement|\\btours?\\b|excursion|museum|museo|cathedral|duomo|gallery|national trust|zoo\\b|aquarium|island ?packers|national park|\\bpark pass|ticket office|historic|castle|palace tour|cruise'),
    ('Travel', 'Travel services', 'trip\\.com|trip dot com|expedia|travel agency|travel & to|tourism|travel insurance|lounge key|priority pass'),
    ('Dining', 'Delivery', 'deliveroo|zomato|uber ?eats|just eat|doordash|grubhub'),
    ('Groceries', 'Quick delivery', 'instashop|kibsons|nownow'),
    ('Dining', 'Work canteen', '\\bekfc|canteen'),
    ('Dining', 'Bars & lounges', '\\bbar\\b|\\bpub\\b|lounge|tavern|jazz|brewery|taproom|cocktail|speakeasy|nightclub'),
    ('Dining', 'Cafe & coffee', 'cafe|café|cafeteria|coffee|starbucks|costa\\b|dunkin|peet|bean\\b|caffeine|chai|\\bpret\\b|espresso|latte|tea ?room|tea house|whittard|larder|koukh al shay|kopitiam|sub ko'),
    ('Dining', 'Bakery & dessert', 'bakery|bakeries|bake|cake|dessert|donut|doughnut|ice ?cream|chocola|sweets|patisserie|gelato|cheesecake|baskin|cinnabon|krispy|confection|halwa'),
    ('Dining', 'Fast food', "kfc|mcdonald|burger|subway|hardee|texas chicken|popeyes|shake shack|five guys|pizza|doner|shawarma|nando|wingstop|domino|chicken|\\bwrap|falafel|kebab|taco|fried|loco bear|dave'?s hot|sliim|jollibee|taco bell|chipotle|in-n-out|wendy|mcd\\b|a&w\\b|texas roadhouse|fuddruckers|sbarro"),
    ('Dining', 'Restaurant', 'restaurant|restuarant|restauran|resto\\b|grill|kitchen|bistro|thai|sushi|noodle|steak|diner|eatery|brasserie|trattoria|osteria|ristorante|pizzeria|tandoor|curry|dim sum|ramen|pho\\b|dishoom|scran|fnb_uss|din tai|shogun|p\\.?f\\.? ?chang|cheesecake factory|il forno|palace|paragon|sangeetha|kerala|dhaba|food court|buffet|cuisine|cheese cake|hotpot|bbq|barbecue|seafood|fish market|spitz|vickys|daves|hot chicken|shack|tavola|\\bbbq\\b|eat\\b|eats\\b|dine|dining|aaraamam|nayaab|abu ?shakra|ocakbasi|lokanta|yemek|restoran|kolaci'),
    ('Groceries', 'Supermarket', 'waitrose|union ?coop|west ?zone|supermarket|grocer|tesco|sainsbury|\\bcoop\\b|lidl|aldi|marks (&|and) spencer|m&s food|trader joe|smart and final|whole foods|kroger|billa|migros|conad|jabal al noor'),
    ('Groceries', 'Convenience store', '7-?eleven|convenience|minimart|mini market|\\bspar\\b|family mart|lawson|circle k|londis|premier stores'),
    ('Transport', 'Fuel', '\\bshell\\b|chevron|petrol|fuel|service station|\\bepco|\\bgas\\b|\\bbp\\b|exxon|\\bmobil\\b|service station|petrol station'),
    ('Transport', 'Taxi & rides', 'taxi|uber(?! ?eats)|\\blime\\b|\\bbolt\\b|\\bcab\\b|yango|\\brides?\\b|\\bhala\\b|\\bsq \\*uber|freenow|free now|grab\\b|gojek'),
    ('Transport', 'Public transit', '\\brta\\b|\\bmetro\\b|\\btram\\b|\\btfl|oyster|\\bmrt\\b|\\bbts\\b|ratp|transport for|\\btube\\b|public transport|translink'),
    ('Transport', 'Parking & tolls', 'parking|toll\\b|\\betb parking|park ?n|garage fee|car park'),
    ('Transport', 'Car rental', 'car rental|rent a car|hertz|budget rent|avis|sixt|europcar|thrifty|enterprise rent|dollar rent'),
    ('Transport', 'Car services', 'tyre|tire|auto|motors?\\b|garage|vehicles? testing|car wash|carwash|mechanic|service centre|service center|motor ?cycle|lube|battery|autozone'),
    ('Government & Fines', 'Fines', '\\bfines?\\b|traffic|violation|\\bmoi\\b|police'),
    ('Government & Fines', 'Post & courier', 'fedex|ups\\b|courier|post office|royal mail'),
    ('Government & Fines', 'Government services', 'municipality|\\bgov|ministry|notary'),
    ('Subscriptions', 'Streaming', 'netflix|spotify|starz|disney|apple tv|youtube|prime video|crunchyroll|hbo|paramount|deezer'),
    ('Subscriptions', 'Software & AI', 'openai|chatgpt|anthropic|claude|github|midjourney|adobe|microsoft|canva|notion|vercel|cursor|figma|office 365|jetbrains|1password|lastpass|nord|expressvpn'),
    ('Subscriptions', 'Cloud storage', 'google one|icloud|dropbox|google storage|apple\\.com/bill|apple\\.com|google \\*|google play'),
    ('Subscriptions', 'Memberships', 'amazon prime|prime membership|membership|\\bplus\\b subscr|deliveroo plus'),
    ('Bills & Utilities', 'Mobile & internet', '\\bdu\\b|telecom|broadband|internet bill|vodafone|mobile bill'),
    ('Bills & Utilities', 'Utilities', 'utility|water bill|electricity|gas bill'),
    ('Bills & Utilities', 'Insurance', 'insurance|assurance|\\baxa\\b|\\bgig\\b'),
    ('Bills & Utilities', 'Rent & property', '\\brent\\b|landlord|property|maintenance fee|service fee|owners association|real estate|realty'),
    ('Health & Fitness', 'Pharmacy', 'pharmac|\\bphy\\b|boots|drugstore|chemist|apotek|farmacia|apotheke|planet pharm|superdrug|watsons'),
    ('Health & Fitness', 'Optical', 'optic|vision house|lenskart|lens\\b|eyewear|spectacle|glasses|eye care'),
    ('Health & Fitness', 'Nutrition', 'nutrition|supplement|\\bgnc\\b|protein|vitamin|health food'),
    ('Health & Fitness', 'Clinic & hospital', 'hospital|clinic|medical|dental|dentist|physio|\\bdr\\.? |doctor|\\blab\\b|laborator|diagnostic|\\bhealth ?care|derma|polyclinic|vaccin'),
    ('Health & Fitness', 'Gym & fitness', 'gym|fitness|fit rep|crossfit|yoga|pilates|sports club|swim|padel|tennis|climb|boxing|\\bmma\\b'),
    ('Personal Care', 'Tailoring & laundry', 'tailor|tailoring|dry ?clean|laundry|alteration|stitch|cobbler'),
    ('Personal Care', 'Spa & wellness', '\\bspa\\b|massage|wellness|hammam|sauna|reflexology|\\bzen\\b'),
    ('Personal Care', 'Salon & grooming', 'salon|saloon|barber|gents|nails?\\b|beauty|grooming|hair|threading|lashes|brow'),
    ('Education', 'Driving & exams', 'driving|ielts|toefl|\\bexam|british council'),
    ('Education', 'Tuition & courses', 'university|college|school|academy|institute|tuition|udemy|coursera|masterclass|training|course|nursery|kindergarten|edu\\b|study|lessons?\\b'),
    ('Electronics', 'Repairs & accessories', 'mobile ?phone repair|phone repair|repair|accessor|case\\b|charger|cable|screen protector'),
    ('Electronics', 'Devices', 'apple store|samsung|huawei|dell\\b|microcenter|best buy|currys|mediamarkt|\\bpc\\b|laptop|electronics|computer|gadget|gopro|dji'),
    ('Home & Furniture', 'Hardware & repairs', 'hardware|building mat|paint|plumb|electrical works|cement|tools|handyman|ironmonger|diy|b&q|screwfix'),
    ('Home & Furniture', 'Furniture & decor', 'ikea|home centre|home center|furniture|homestore|carpent|curtain|decor|mattress|bedding|sleep|pottery barn|west elm|bed bath|home ?goods'),
    ('Shopping', 'Gifts & flowers', 'flower|florist|gift|hallmark|balloon|petals|bouquet|blooms'),
    ('Shopping', 'Jewellery & accessories', 'jewel|jeweller|swarovski|\\bwatch|\\bgold\\b|diamond|pandora|tiffany|cartier|handbag|sunglass'),
    ('Shopping', 'Perfume & beauty', 'perfum|fragrance|sephora|cosmetic|makeup|make-up|\\bfaces\\b|\\blush\\b|body shop|bath ?& ?body|mac cosmetics'),
    ('Shopping', 'Sports & outdoor', 'decathlon|sports|columbia|adidas|nike|puma|under armour|north face|outdoor|go outdoors|cycle|bicycle|\\bgolf'),
    ('Shopping', 'Clothing & shoes', 'zara|h ?& ?m|h and m|charles|keith|new yorker|american eagle|6th ?street|uniqlo|bershka|pull ?& ?bear|massimo|shoe|footwear|apparel|fashion|boutique|clothing|garment|saree|silks|textile|wedding|kendra|abaya|thobe|bata\\b|skechers|primark|next\\b|topshop|gap\\b|levi|tommy|calvin|zalando|asos|shein|g-star|amit retail|aeropostale|lacoste|vero moda|forever 21|jack ?& ?jones|town team|brands for less|bf-'),
    ('Shopping', 'Books & stationery', 'book|hatchards|stationer|whsmith|wh smith|jarir|virgin megastore|waterstones|blackwell|daiso|office supplies|art supplies'),
    ('Shopping', 'Online marketplace', 'amazon|temu|aliexpress|alibaba|ebay|etsy|\\bsouq|carousell|dubizzle|wish\\.com|jumia|flipkart|myntra|lazada|shopee'),
    ('Entertainment', 'Cinema & shows', 'cinem|movie|imax|theatre|theater|opera|concert|comedy|film|\\bshows?\\b|musical'),
    ('Entertainment', 'Gaming', 'playstation|xbox|steam|nintendo|game|gaming|arcade|hive|boardgame|riot|epic games|twitch|roblox'),
    ('Entertainment', 'Attractions & leisure', 'waterpark|oasis|bowling|shooting|activate|escape|laser|theme park|la perle|burj|expo|kidzania|sharja shooting|motiongate|legoland|seaworld|warner|adventure|trampoline|karting|paintball'),
    ('Entertainment', 'Events & tickets', 'ticketmaster|eventbrite|ticket|tickets|event'),
]
RULES = [(c, s, re.compile(p, re.I)) for c, s, p in _RAW]

# merchants that are plain words the rules cannot know: lower-cased substring -> (category, sub-category)
SEED = {}      # lower-cased merchant fragment -> (category, sub-category); yours go in a plugin (CATEGORY_SEED)

OVERRIDE_SOURCES = ("user",)

# ───────────────────────── classification ─────────────────────────
_CLEAN = re.compile(r"[*_#]+")


def _text(desc):
    return re.sub(r"\s+", " ", _CLEAN.sub(" ", (desc or "").lower())).strip()


_plugin_cache = {}


def _plugin_rules():
    raw = plugins.get("CATEGORY_RULES", [])
    if _plugin_cache.get("n") != len(raw):
        _plugin_cache["n"] = len(raw)
        _plugin_cache["r"] = [(c, s_, re.compile(p, re.I)) for c, s_, p in raw]
    return _plugin_cache.get("r", [])


def rule_classify(desc):
    t = _text(desc)
    for k, v in {**plugins.get("CATEGORY_SEED", {}), **SEED}.items():
        if k in t:
            return v + ("seed",)
    for cat, sub, rx in _plugin_rules() + RULES:
        if rx.search(t):
            return cat, sub, "rule"
    return None


def classify(desc, merchant_override=None):
    """-> (category, sub, source). merchant_override is (category, sub) from a user correction."""
    if merchant_override:
        return merchant_override[0], merchant_override[1], "user"
    r = rule_classify(desc)
    return r if r else ("Other", "", "none")


# ───────────────────────── where a charge happened, and trips ─────────────────────────
# (iso3, iso2, country). A trip is one country: consecutive days of on-the-ground spending there.
_C = [("GBR", "GB", "United Kingdom"), ("USA", "US", "United States"), ("DEU", "DE", "Germany"), ("ITA", "IT", "Italy"), ("FRA", "FR", "France"), ("ESP", "ES", "Spain"),
      ("NLD", "NL", "Netherlands"), ("CHE", "CH", "Switzerland"), ("AUT", "AT", "Austria"), ("BEL", "BE", "Belgium"), ("PRT", "PT", "Portugal"), ("GRC", "GR", "Greece"),
      ("TUR", "TR", "Turkey"), ("THA", "TH", "Thailand"), ("SGP", "SG", "Singapore"), ("MYS", "MY", "Malaysia"), ("IDN", "ID", "Indonesia"), ("VNM", "VN", "Vietnam"),
      ("PHL", "PH", "Philippines"), ("HKG", "HK", "Hong Kong"), ("CHN", "CN", "China"), ("JPN", "JP", "Japan"), ("KOR", "KR", "South Korea"), ("IND", "IN", "India"),
      ("LKA", "LK", "Sri Lanka"), ("NPL", "NP", "Nepal"), ("MDV", "MV", "Maldives"), ("EGY", "EG", "Egypt"), ("MAR", "MA", "Morocco"), ("ZAF", "ZA", "South Africa"),
      ("KEN", "KE", "Kenya"), ("TZA", "TZ", "Tanzania"), ("CAN", "CA", "Canada"), ("AUS", "AU", "Australia"), ("NZL", "NZ", "New Zealand"), ("SAU", "SA", "Saudi Arabia"),
      ("QAT", "QA", "Qatar"), ("BHR", "BH", "Bahrain"), ("OMN", "OM", "Oman"), ("KWT", "KW", "Kuwait"), ("JOR", "JO", "Jordan"), ("GEO", "GE", "Georgia"),
      ("AZE", "AZ", "Azerbaijan"), ("ARM", "AM", "Armenia"), ("UZB", "UZ", "Uzbekistan"), ("KAZ", "KZ", "Kazakhstan"), ("RUS", "RU", "Russia"), ("CZE", "CZ", "Czechia"),
      ("HUN", "HU", "Hungary"), ("POL", "PL", "Poland"), ("SWE", "SE", "Sweden"), ("NOR", "NO", "Norway"), ("DNK", "DK", "Denmark"), ("FIN", "FI", "Finland"),
      ("IRL", "IE", "Ireland"), ("ISR", "IL", "Israel"), ("CYP", "CY", "Cyprus"), ("MLT", "MT", "Malta"), ("HRV", "HR", "Croatia"), ("SRB", "RS", "Serbia"),
      ("BIH", "BA", "Bosnia"), ("ALB", "AL", "Albania"), ("MNE", "ME", "Montenegro"), ("LUX", "LU", "Luxembourg"), ("TWN", "TW", "Taiwan"), ("PAK", "PK", "Pakistan"), ("MEX", "MX", "Mexico"),
      ("BGD", "BD", "Bangladesh"), ("MUS", "MU", "Mauritius"), ("ISL", "IS", "Iceland"), ("ARE", "AE", "United Arab Emirates")]
ISO3 = {i3: n for i3, _, n in _C}
ISO2 = {n: i2 for _, i2, n in _C}


def HOME():
    """The country where you live: charges there are not trips. Settings > Home country; a plugin may supply a default (DEFAULT_HOME_COUNTRY)."""
    return get_setting("home_country", "") or plugins.get("DEFAULT_HOME_COUNTRY", "") or "United States"


def flag(country):
    i = ISO2.get(country) or ""
    return "".join(chr(0x1F1E6 + ord(c) - 65) for c in i) if i else "🌍"


# currencies that point to exactly one country (USD and EUR do not)
CCY_COUNTRY = {"GBP": "United Kingdom", "SGD": "Singapore", "OMR": "Oman", "MAD": "Morocco", "TRY": "Turkey", "QAR": "Qatar", "INR": "India", "IDR": "Indonesia", "TWD": "Taiwan",
               "THB": "Thailand", "MYR": "Malaysia", "JPY": "Japan", "CHF": "Switzerland", "KES": "Kenya", "AUD": "Australia", "CAD": "Canada", "HKD": "Hong Kong", "KWD": "Kuwait",
               "BHD": "Bahrain", "EGP": "Egypt", "LKR": "Sri Lanka", "GEL": "Georgia", "RSD": "Serbia", "CZK": "Czechia", "PLN": "Poland", "HUF": "Hungary", "DKK": "Denmark",
               "SEK": "Sweden", "NOK": "Norway", "NZD": "New Zealand", "PHP": "Philippines", "VND": "Vietnam", "KRW": "South Korea", "CNY": "China", "PKR": "Pakistan",
               "BDT": "Bangladesh", "NPR": "Nepal", "MVR": "Maldives", "MUR": "Mauritius", "ZAR": "South Africa", "SAR": "Saudi Arabia", "JOD": "Jordan"}
UK_CODES = {"ENG", "SCT", "WLS", "NIR", "LND", "LON"}
US_STATES = set("AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC".split())
_CITIES = {
    "United Kingdom": "london edinburgh glasgow manchester birmingham liverpool oxford cambridge brighton heathrow gatwick",
    "Italy": "roma rome firenze florence milano milan venezia venice napoli naples bologna torino",
    "Germany": "berlin muenchen munich hamburg frankfurt koln cologne", "France": "paris lyon marseille", "Spain": "madrid barcelona valencia seville",
    "Netherlands": "amsterdam rotterdam", "Switzerland": "zurich geneva", "Austria": "vienna wien", "Turkey": "istanbul ankara antalya izmir",
    "Portugal": "lisboa lisbon porto", "Greece": "athens", "Singapore": "singapore", "Taiwan": "taipei", "Indonesia": "bali jakarta",
    "India": "mumbai delhi bangalore bengaluru chennai hyderabad", "Thailand": "bangkok phuket", "Ireland": "dublin", "Malaysia": "kuala lumpur", "Japan": "tokyo osaka kyoto",
    "Egypt": "cairo", "Mexico": "mexico city cancun oaxaca", "Canada": "toronto vancouver montreal",
    "United States": "los angeles chicago boston miami seattle denver phoenix las vegas orlando new york san francisco san diego houston atlanta austin dallas",
}
_MULTI = ("kuala lumpur", "los angeles", "las vegas", "new york", "san francisco", "san diego", "mexico city")


def _split_cities(cs):
    for m in _MULTI:
        cs = cs.replace(m, m.replace(" ", "_"))
    return [w.replace("_", " ") for w in cs.split() if w]


def _city_rx():
    hints = plugins.get("CITY_HINTS", {})
    key = len(hints)
    if _city_cache.get("k") != key:
        merged = {n: cs + " " + hints.get(n, "") for n, cs in _CITIES.items()}
        merged.update({n: cs for n, cs in hints.items() if n not in merged})
        _city_cache["k"], _city_cache["v"] = key, [(re.compile((r"\b" if len(w) < 5 else "") + re.escape(w) + (r"\b" if len(w) < 5 else "")), n) for n, cs in merged.items() for w in _split_cities(cs)]
    return _city_cache["v"]


_city_cache = {}
_home_cache = {}


def _home_rx():
    pat = plugins.get("HOME_REGEX", "")
    if pat and _home_cache.get("p") != pat:
        _home_cache["p"], _home_cache["rx"] = pat, re.compile(pat)
    return _home_cache.get("rx") if pat else None

ONLINE_RX = re.compile(r"getyourguide|klook|viator|tiqets|musement|todaytix|agoda|booking\.com|\bbkg\b|trip\.com|expedia|hostelworld|airbnb|airalo|holafly|aliexpress|alibaba|"
                       r"temu|amazon|ebay|etsy|shein|paypal|google|apple\.com|openai|github|netflix|spotify|e-?visa|playstation|ryanair|easyjet|wizz")


def _online(t):
    extra = plugins.get("ONLINE_REGEX_EXTRA", "")
    return bool(ONLINE_RX.search(t) or (extra and re.search(extra, t)))


def place_of(desc, ccy="", hint=None):
    """-> (country or None, online). Judged from where the charge says it happened; currency only when it names a single country."""
    t = _text(desc)
    if hint:
        country, online = hint
        if online:
            return None, True
        if country:
            return country, False
    if _online(t):
        return None, True
    hrx = _home_rx()
    if hrx and hrx.search(t):
        return HOME(), False
    m = re.search(r"\s([a-z]{3})$", t)
    if m:
        c3 = m.group(1).upper()
        if c3 in ISO3:
            return ISO3[c3], False
        if c3 in UK_CODES:
            return "United Kingdom", False
    m2 = re.search(r"\s([a-z]{2})$", t)
    if m2 and ccy == "USD" and m2.group(1).upper() in US_STATES:
        return "United States", False
    hits = [(mm.end(), n) for rx, n in _city_rx() for mm in [rx.search(t)] if mm]
    if hits:
        return max(hits)[1], False                       # the place name nearest the end of the line is the real location
    return CCY_COUNTRY.get(ccy), False


# On-the-ground spending proves you were there. Bookings, flights and anything billed online prove nothing about location.
EVIDENCE_CATS = {"Dining", "Groceries", "Transport", "Entertainment", "Health & Fitness", "Personal Care", "Cash & ATM", "Other"}
EVIDENCE_TRAVEL_SUBS = {"Trains, buses & ferries"}
NOT_TAGGED = {"Subscriptions", "Bills & Utilities", "Education", "Fees & Charges", "Internal", "Payments & Transfers", "Government & Fines", "Charity", "Electronics"}
NOT_TAGGED_TRAVEL = {"Flights", "Visas & documents", "Travel services", "eSIM & connectivity"}


def _evidence(cat, sub):
    return cat in EVIDENCE_CATS or (cat == "Shopping" and sub != "Online marketplace") or (cat == "Travel" and sub in EVIDENCE_TRAVEL_SUBS)


def _taggable(cat, sub):
    return cat not in NOT_TAGGED and not (cat == "Travel" and sub in NOT_TAGGED_TRAVEL)


def detect_trips(items):
    """items: dicts with txn_date, country, category, subcategory, amount (online / home charges already have country None or the home country).
    One trip per country and run of days (gap <= 6 days). Needs 3+ charges over 2+ days, or 5+ charges."""
    by = defaultdict(list)
    for t in items:
        if t["amount"] > 0 and t.get("country") and t["country"] != HOME() and _evidence(t["category"], t.get("subcategory", "")):
            by[t["country"]].append(t["txn_date"])
    trips = []
    for country, dates in by.items():
        dates.sort()
        group = [dates[0]]
        for d in dates[1:] + [None]:
            if d is not None and (date.fromisoformat(d) - date.fromisoformat(group[-1])).days <= 6:
                group.append(d)
                continue
            if (len(group) >= 3 and len(set(group)) >= 2) or len(group) >= 5:
                trips.append({"country": country, "start": group[0], "end": group[-1], "charges": len(group)})
            group = [d] if d else []
    trips.sort(key=lambda t: (t["start"], t["country"]))
    seen = Counter()
    for t in trips:
        label = f"{date.fromisoformat(t['start']).strftime('%b %Y')} · {flag(t['country'])} {t['country']}"
        seen[label] += 1
        t["label"] = label + (f" ({seen[label]})" if seen[label] > 1 else "")
    return trips


EURO = {"Germany", "Italy", "France", "Spain", "Netherlands", "Austria", "Belgium", "Portugal", "Greece", "Ireland", "Montenegro", "Croatia", "Luxembourg", "Malta", "Cyprus", "Finland"}


def _ccy_matches(country, ccy):
    return bool(ccy) and (CCY_COUNTRY.get(ccy) == country or (ccy == "USD" and country == "United States") or (ccy == "EUR" and country in EURO))


def booking_trip(trips, day, ccy=""):
    """Travel charges with no location (hotels, hostels, flights, tours, visas booked online) belong to the trip they were bought for:
    the trip they were paid during if the currency matches, otherwise the next trip starting within 75 days, otherwise one just ended."""
    d = date.fromisoformat(day)
    ahead = [t for t in trips if 0 < (date.fromisoformat(t["start"]) - d).days <= 75]
    inside = [t for t in trips if date.fromisoformat(t["start"]) - timedelta(days=1) <= d <= date.fromisoformat(t["end"]) + timedelta(days=3)]
    pool = sorted(ahead, key=lambda t: t["start"]) + inside
    for t in pool:
        if _ccy_matches(t["country"], ccy):
            return t["label"]
    return pool[0]["label"] if pool else ""


def trip_for(trips, country, day):
    for t in trips:
        if t["country"] == country and (date.fromisoformat(t["start"]) - timedelta(days=1)).isoformat() <= day <= (date.fromisoformat(t["end"]) + timedelta(days=1)).isoformat():
            return t["label"]
    return ""


# ───────────────────────── applying everything ─────────────────────────
def merchant_key(desc):
    import transactions as TX
    return TX.normalize_merchant(desc)


def ensure_columns():
    with get_db() as conn:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(statement_transactions)").fetchall()}
        for col in ("subcategory", "trip", "trip_manual"):
            if col not in cols:
                conn.execute(f"ALTER TABLE statement_transactions ADD COLUMN {col} TEXT DEFAULT ''")
        mcols = {r["name"] for r in conn.execute("PRAGMA table_info(merchant_categories)").fetchall()}
        for col, ddl in (("subcategory", "TEXT DEFAULT ''"), ("source", "TEXT DEFAULT 'user'"), ("country", "TEXT"), ("online", "INTEGER DEFAULT 0")):
            if col not in mcols:
                conn.execute(f"ALTER TABLE merchant_categories ADD COLUMN {col} {ddl}")


def ai_overrides(conn):
    return {r["merchant_key"]: (r["category"], r["subcategory"] or "") for r in
            conn.execute("SELECT merchant_key, category, subcategory FROM merchant_categories WHERE source='ai'").fetchall()}


def user_overrides(conn):
    return {r["merchant_key"]: (r["category"], r["subcategory"] or "") for r in
            conn.execute("SELECT merchant_key, category, subcategory FROM merchant_categories WHERE COALESCE(source,'user')='user'").fetchall()}


def recategorize(dry_run=False):
    """Classify every transaction, learn from sibling merchants for the leftovers, work out where each charge happened and tag trips."""
    import transactions as TX
    ensure_columns()
    with get_db() as conn:
        rows = [dict(r) for r in conn.execute("SELECT id, description, amount, category, subcategory, txn_date, fx_currency, trip, trip_manual FROM statement_transactions").fetchall()]
        ov = user_overrides(conn)
        ai = ai_overrides(conn)
        hints = {r["merchant_key"]: (r["country"], bool(r["online"])) for r in conn.execute("SELECT merchant_key, country, online FROM merchant_categories WHERE country IS NOT NULL OR online=1").fetchall()}
        custom = TX.custom_categories()
    out, keys = {}, {}
    for r in rows:
        key = keys[r["id"]] = merchant_key(r["description"])
        low = _text(r["description"])
        hit = next((c["name"] for c in custom if any(k in low for k in c["keywords"])), None)
        if key in ov:
            c0, s0 = ov[key]
            if not s0:                     # a correction made before sub-categories existed: keep your category, fill the sub from the rules
                rc = rule_classify(r["description"])
                s0 = rc[1] if rc and rc[0] == c0 else ""
            out[r["id"]] = (c0, s0, "user")
        elif hit:
            out[r["id"]] = (hit, "", "custom")
        else:
            out[r["id"]] = classify(r["description"])
            if out[r["id"]][2] == "none" and key in ai:
                out[r["id"]] = (ai[key][0], ai[key][1], "ai")
    # leftovers: file them with the majority of merchants that share the brand word
    brand = defaultdict(Counter)
    for r in rows:
        c, s_, src = out[r["id"]]
        w = _brand(r["description"])
        if src in ("rule", "seed", "user") and w:
            brand[w][(c, s_)] += 1
    learned = 0
    for r in rows:
        c, s_, src = out[r["id"]]
        if src == "none":
            w = _brand(r["description"])
            if w and brand.get(w):
                (cs, n), tot = brand[w].most_common(1)[0], sum(brand[w].values())
                if n >= 2 and n / tot >= 0.8:
                    out[r["id"]] = (cs[0], cs[1], "learned"); learned += 1
    items = []
    for r in rows:
        country, online = place_of(r["description"], r["fx_currency"] or "", hints.get(keys[r["id"]]))
        items.append({"id": r["id"], "txn_date": r["txn_date"], "amount": r["amount"], "country": country, "online": online,
                      "category": out[r["id"]][0], "subcategory": out[r["id"]][1]})
    trips = detect_trips(items)
    manual = {r["id"]: r["trip_manual"] for r in rows if r["trip_manual"] is not None}
    ccy = {r["id"]: r["fx_currency"] or "" for r in rows}
    labels = {t["label"] for t in trips}

    def auto_trip(it):
        if it["amount"] <= 0:
            return ""
        if it["country"] and it["country"] != HOME() and not it["online"] and _taggable(it["category"], it["subcategory"]):
            return trip_for(trips, it["country"], it["txn_date"])
        if it["category"] == "Travel" and (it["online"] or not it["country"] or (it["country"] == HOME() and it["subcategory"] in NOT_TAGGED_TRAVEL)):   # a hotel in your home country is a staycation, not a trip booking
            return booking_trip(trips, it["txn_date"], ccy[it["id"]])
        return ""

    def final_trip(it):
        m = manual.get(it["id"])
        if m == "-":
            return ""
        if m and m in labels:
            return m
        return auto_trip(it)
    trip_of = {it["id"]: final_trip(it) for it in items}
    changes = [(out[r["id"]][0], out[r["id"]][1], trip_of[r["id"]], r["id"]) for r in rows
               if (out[r["id"]][0], out[r["id"]][1], trip_of[r["id"]]) != (r["category"], r["subcategory"] or "", r["trip"] or "")]
    other = sum(1 for r in rows if out[r["id"]][0] == "Other" and r["amount"] > 0)
    summary = {"transactions": len(rows), "changed": len(changes), "still_other": other, "learned": learned,
               "trips": [f"{t['label']} ({t['start']} to {t['end']}, {t['charges']} charges)" for t in trips]}
    if not dry_run:
        with get_db() as conn:
            conn.executemany("UPDATE statement_transactions SET category=?, subcategory=?, trip=? WHERE id=?", changes)
    return summary


_BRAND_SKIP = {"the", "new", "old", "www", "llc", "trading", "co", "ltd", "shop", "store", "restaurant", "cafe"}


def _brand(desc):
    for w in _text(desc).split():
        w = re.sub(r"[^a-z]", "", w)
        if len(w) >= 4 and w not in _BRAND_SKIP:
            return w
    return ""


def set_merchant(desc, category, sub=""):
    """Your correction: remembered for this merchant, applied to every past and future charge."""
    ensure_columns()
    key = merchant_key(desc)
    from datetime import datetime
    with get_db() as conn:
        conn.execute("""INSERT INTO merchant_categories(merchant_key,category,updated_at,subcategory,source) VALUES (?,?,?,?, 'user')
                        ON CONFLICT(merchant_key) DO UPDATE SET category=excluded.category, subcategory=excluded.subcategory, updated_at=excluded.updated_at, source='user'""",
                     (key, category, datetime.utcnow().isoformat(), sub))
    return key


# ───────────────────────── AI for the long tail ─────────────────────────
# Cost rules: rules and learned brands go first (free). Only merchants never asked about reach a model, one small request each, on a
# classifier service (Jev, or OpenAI's Decisions API: billed on input tokens only, a fraction of a cent per merchant). Every answer,
# even "cannot tell", is stored, so a merchant costs money once, ever. A chat model is used only if no classifier is reachable.
CHEAP = {"openai": "gpt-4.1-mini", "anthropic": "claude-haiku-5-5"}
MAX_BATCH = 80
MIN_CONF = 0.4

SUB_DESC = {
    "Supermarket": "supermarkets and hypermarkets", "Convenience store": "small convenience shops and corner stores", "Quick delivery": "app-based grocery delivery",
    "Restaurant": "sit-down restaurants and food courts", "Cafe & coffee": "coffee shops, cafes and tea houses", "Fast food": "fast food, pizza, burgers, takeaway chains",
    "Delivery": "food delivery apps", "Bakery & dessert": "bakeries, sweets, ice cream", "Bars & lounges": "bars, pubs, lounges", "Work canteen": "staff canteens",
    "Fuel": "petrol stations", "Taxi & rides": "taxis, ride-hailing, e-scooters", "Public transit": "metro, tram, bus passes, transit cards", "Parking & tolls": "parking and road tolls",
    "Car services": "garages, tyres, car testing", "Car rental": "car hire",
    "Flights": "airlines", "Accommodation": "hotels, hostels, apartments", "Tours & activities": "tours, tickets, museums, excursions", "Trains, buses & ferries": "intercity trains, buses, ferries",
    "Visas & documents": "visas and travel documents", "eSIM & connectivity": "travel eSIMs", "Travel services": "travel agents and booking sites",
    "Clothing & shoes": "clothes, shoes, tailors", "Jewellery & accessories": "jewellery, watches, bags", "Perfume & beauty": "perfume, cosmetics", "Online marketplace": "online marketplaces",
    "Gifts & flowers": "flowers and gifts", "Sports & outdoor": "sports gear", "Books & stationery": "books and stationery", "General retail": "other shops",
    "Cinema & shows": "cinemas, theatre, shows", "Attractions & leisure": "attractions, bowling, parks, leisure venues", "Gaming": "video games", "Events & tickets": "event tickets",
    "Streaming": "streaming subscriptions", "Software & AI": "software and AI subscriptions", "Cloud storage": "cloud storage", "Memberships": "paid memberships",
    "Mobile & internet": "phone and internet", "Utilities": "electricity, water, cooling", "Insurance": "insurance", "Rent & property": "rent and property",
    "Pharmacy": "pharmacies", "Clinic & hospital": "doctors, clinics, hospitals", "Optical": "opticians", "Gym & fitness": "gyms and fitness", "Nutrition": "supplements and nutrition",
    "Salon & grooming": "hair salons and barbers", "Spa & wellness": "spas and massage", "Tailoring & laundry": "tailors and laundry",
    "Tuition & courses": "tuition, universities, courses", "Driving & exams": "driving school and exams", "Furniture & decor": "furniture and decor", "Hardware & repairs": "hardware and repairs",
    "Devices": "electronics stores", "Repairs & accessories": "phone repair and accessories", "Government services": "government services and fees", "Fines": "traffic and other fines",
    "Post & courier": "post and couriers", "Cash withdrawal": "ATM cash", "Exchange": "currency exchange", "Bank fees": "bank fees", "VAT": "VAT on fees", "Late-payment fee": "late fees",
    "Donations": "charities, donations, relief organisations",
}
PRESENCE = [("physical", "a shop, restaurant, hotel, transport or attraction where the cardholder was physically present"),
            ("online", "an online platform, app, booking site or subscription: its location says nothing about where the cardholder was")]


def ai_enabled():
    with get_db() as conn:
        r = conn.execute("SELECT value FROM settings WHERE key='ai_categorise'").fetchone()
    return (r["value"] if r else "1") == "1"


def set_ai_enabled(on):
    with get_db() as conn:
        conn.execute("INSERT OR REPLACE INTO settings VALUES ('ai_categorise',?)", ("1" if on else "0",))


def _known_keys():
    with get_db() as conn:
        return {r["merchant_key"] for r in conn.execute("SELECT merchant_key FROM merchant_categories").fetchall()}


def unknown_merchants(limit=MAX_BATCH):
    """Merchants still in 'Other' that nobody (you, the rules, or a model) has classified yet."""
    import transactions as TX
    ensure_columns()
    with get_db() as conn:
        rows = conn.execute("SELECT description, amount, fx_currency FROM statement_transactions WHERE category='Other' AND amount>0").fetchall()
    known, agg = _known_keys(), {}
    for r in rows:
        k = TX.normalize_merchant(r["description"])
        if k in known:
            continue
        a = agg.setdefault(k, {"merchant": " ".join(re.sub(r"[*_#]+", " ", r["description"]).split())[:48], "total": 0.0, "currency": r["fx_currency"] or ""})
        a["total"] += r["amount"]
    return sorted(({**v, "key": k} for k, v in agg.items()), key=lambda x: -x["total"])[:limit]


def unplaced_merchants(limit=MAX_BATCH):
    """Foreign-currency merchants the rules can file but cannot place on a map (USD / EUR charges without a location)."""
    import transactions as TX
    ensure_columns()
    with get_db() as conn:
        rows = conn.execute("SELECT description, fx_currency FROM statement_transactions WHERE fx_currency!='' AND amount>0").fetchall()
        hinted = {r["merchant_key"] for r in conn.execute("SELECT merchant_key FROM merchant_categories WHERE country IS NOT NULL OR online=1 OR source='ai_place'").fetchall()}
    agg = {}
    for r in rows:
        k = TX.normalize_merchant(r["description"])
        if k in hinted or k in agg:
            continue
        country, online = place_of(r["description"], r["fx_currency"])
        if country is None and not online:
            agg[k] = {"key": k, "merchant": " ".join(re.sub(r"[*_#]+", " ", r["description"]).split())[:48], "currency": r["fx_currency"]}
    return list(agg.values())[:limit]


CAT_DESC = {
    "Groceries": "supermarkets, grocery stores, grocery delivery", "Dining": "restaurants, cafes, bakeries, bars, food delivery, takeaway",
    "Transport": "fuel, taxis, ride-hailing, parking, tolls, public transit, car services, car hire", "Travel": "flights, hotels, hostels, tours and attractions tickets, intercity trains and buses, visas, travel agents",
    "Shopping": "clothes, shoes, gifts, flowers, jewellery, perfume, sports gear, books, online marketplaces, general shops", "Entertainment": "cinemas, theme parks, bowling, games, events",
    "Subscriptions": "streaming, software, AI tools, cloud storage, memberships", "Bills & Utilities": "phone, internet, electricity, water, insurance, rent",
    "Health & Fitness": "pharmacies, clinics, hospitals, opticians, gyms", "Personal Care": "hair salons, barbers, spas, tailors, laundry", "Education": "tuition, universities, courses, driving school",
    "Home & Furniture": "furniture, decor, hardware", "Electronics": "electronics stores, phone repair", "Government & Fines": "government fees, fines, post and courier",
    "Cash & ATM": "cash withdrawals, currency exchange", "Fees & Charges": "bank fees and charges", "Charity": "charities and donations", "Other": "cannot tell what it is"}


def _questions(with_category):
    qs = []
    if with_category:
        qs.append({"name": "category", "instructions": "Which spending category fits this card merchant? The cardholder lives in " + HOME() + " and may travel abroad, so unfamiliar foreign names are usually restaurants, cafes, transit, shops, hostels or attractions.",
                   "choices": [(c, CAT_DESC.get(c, c)) for c in TAXONOMY if c not in ("Internal", "Payments & Transfers")]})
    qs.append({"name": "country", "instructions": "Which country is this merchant located in?",
               "choices": [(n, n) for n in ISO2] + [(HOME(), HOME()), ("Unknown", "cannot tell")]})
    qs.append({"name": "presence", "instructions": "Does a charge from this merchant show the cardholder was physically there?", "choices": PRESENCE})
    return qs


def _sub_question(cat):
    return [{"name": "sub", "instructions": f"Which kind of {cat.lower()} merchant is this?", "choices": [(sub, SUB_DESC.get(sub, sub.lower())) for sub in TAXONOMY[cat] if sub]}]


def _ask(item, with_category):
    from decide import decide
    text = f"{item['merchant']}" + (f" [{item['currency']}]" if item.get("currency") else "")
    ans, backend = decide(text, _questions(with_category))
    if ans and with_category and ans.get("category", {}).get("confidence", 0) >= MIN_CONF and len([x for x in TAXONOMY.get(ans["category"]["choice"], []) if x]) > 1:
        more, _ = decide(text + f" ({ans['category']['choice']})", _sub_question(ans["category"]["choice"]))
        if more:
            ans["sub"] = more["sub"]
    return item, (ans, backend)


def _run(items, with_category):
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=8) as ex:
        return list(ex.map(lambda it: _ask(it, with_category), items))


def ai_classify(max_batch=MAX_BATCH):
    """Files new unknown merchants and places foreign ones. Uses the cheapest classifier service available."""
    import decide
    ensure_columns()
    if not decide.available():
        return _chat_classify(max_batch)
    from datetime import datetime
    now, filed, placed, backend = datetime.utcnow().isoformat(), 0, 0, None
    unknown = unknown_merchants(max_batch)
    results = _run(unknown, True) + _run(unplaced_merchants(max_batch), False)
    with get_db() as conn:
        for item, (ans, b) in results:
            if not ans:
                continue
            backend = b
            cat, sub = "Other", ""
            if "category" in ans and ans["category"]["confidence"] >= MIN_CONF and ans["category"]["choice"] in TAXONOMY:
                cat = ans["category"]["choice"]
                if "sub" in ans and ans["sub"]["confidence"] >= 0.35 and ans["sub"]["choice"] in TAXONOMY[cat]:
                    sub = ans["sub"]["choice"]
            country = ans.get("country", {})
            country = country["choice"] if country.get("confidence", 0) >= MIN_CONF and country.get("choice") not in (None, "Unknown") else None
            online = int(ans.get("presence", {}).get("choice") == "online" and ans["presence"]["confidence"] >= 0.6)
            src = "ai" if "category" in ans else "ai_place"
            conn.execute("""INSERT INTO merchant_categories(merchant_key,category,updated_at,subcategory,source,country,online) VALUES (?,?,?,?,?,?,?)
                            ON CONFLICT(merchant_key) DO UPDATE SET country=COALESCE(excluded.country, merchant_categories.country), online=MAX(merchant_categories.online, excluded.online)
                            WHERE merchant_categories.source!='user'""", (item["key"], cat, now, sub, src, country, online))
            filed += src == "ai" and cat != "Other"
            placed += bool(country)
    recategorize()
    chat = None
    left = _leftovers(max_batch)
    if left:                                  # one batched call on the cheapest chat model for what the classifier could not decide
        chat = _chat_classify(max_batch, left)
    summary = recategorize()
    return {"ok": True, "asked": len(results), "classified": filed, "placed": placed, "remaining": summary["still_other"], "model": backend or "none",
            "chat_fallback": (chat or {}).get("classified", 0)}


def _leftovers(limit=MAX_BATCH):
    """Merchants the classifier service could not place in a category with confidence: they go to one cheap chat call instead."""
    import transactions as TX
    with get_db() as conn:
        ai_other = {r["merchant_key"] for r in conn.execute("SELECT merchant_key FROM merchant_categories WHERE source='ai' AND category='Other'").fetchall()}
        rows = conn.execute("SELECT description, amount, fx_currency FROM statement_transactions WHERE category='Other' AND amount>0").fetchall()
    agg = {}
    for r in rows:
        k = TX.normalize_merchant(r["description"])
        if k in ai_other:
            a = agg.setdefault(k, {"key": k, "merchant": " ".join(re.sub(r"[*_#]+", " ", r["description"]).split())[:48], "currency": r["fx_currency"] or "", "total": 0.0})
            a["total"] += r["amount"]
    return sorted(agg.values(), key=lambda x: -x["total"])[:limit]


def auto_classify():
    """Called after a statement import: quietly sorts new unknown merchants when AI categorising is on and a model can be reached."""
    try:
        if ai_enabled() and (unknown_merchants(1) or unplaced_merchants(1)):
            return ai_classify()
    except Exception:
        pass
    return None


# ── fallback when no classifier service is reachable: one cheap chat call for the whole batch ──
def _menu():
    cats = [c for c in TAXONOMY if c not in ("Internal", "Payments & Transfers", "Other")]
    lines = [f"{i}:{c}=" + ",".join(f"{j}:{s}" for j, s in enumerate(TAXONOMY[c]) if s) for i, c in enumerate(cats)]
    return cats, "\n".join(lines)


def _chat_classify(max_batch, items=None):
    import agent as AG
    items = items if items is not None else unknown_merchants(max_batch)
    if not items:
        return {"ok": True, "asked": 0, "classified": 0, "remaining": 0}
    cfg = AG.get_settings()
    prov = cfg["provider"]
    if AG.key_source(prov) == "none":
        return {"ok": False, "error": "No API key yet (AI settings).", "asked": 0}
    cats, menu = _menu()
    prompt = ("Classify card merchants for a resident of " + HOME() + " who may travel (foreign names are usually restaurants, cafes, transit, shops, hostels, attractions, charities). "
              "Menu is category index:name=sub index:name.\n" + menu +
              "\nAnswer one line per merchant as id,category index,sub index. Use 99,0 if you cannot tell. No other text.\n\n" +
              "\n".join(f"{i}|{m['merchant']}{' [' + m['currency'] + ']' if m['currency'] else ''}" for i, m in enumerate(items)))
    model = CHEAP.get(prov) or (cfg["openai_model"] if prov == "openai" else cfg["model"])
    try:
        client = AG._client(prov).with_options(timeout=45)
        if prov == "openai":
            r = client.chat.completions.create(model=model, messages=[{"role": "user", "content": prompt}], max_completion_tokens=12 * len(items) + 50)
            text = r.choices[0].message.content or ""
            import aiusage
            aiusage.log("merchant sorting", model, getattr(r.usage, "prompt_tokens", 0), getattr(r.usage, "completion_tokens", 0))
        else:
            r = client.messages.create(model=model, max_tokens=12 * len(items) + 50, messages=[{"role": "user", "content": prompt}])
            text = "".join(b.text for b in r.content if getattr(b, "type", "") == "text")
            import aiusage
            aiusage.log("merchant sorting", model, getattr(r.usage, "input_tokens", 0), getattr(r.usage, "output_tokens", 0))
    except Exception as e:
        return {"ok": False, "error": AG._friendly_error(e, prov)[0], "asked": len(items)}
    from datetime import datetime
    now, n, seen = datetime.utcnow().isoformat(), 0, set()
    with get_db() as conn:
        for m in re.finditer(r"(\d+)\s*[,|:]\s*(\d+)\s*[,|:]\s*(\d+)", text):
            i, ci, si = (int(x) for x in m.groups())
            if i in seen or not 0 <= i < len(items):
                continue
            seen.add(i)
            if ci < len(cats):
                cat = cats[ci]; subs = [x for x in TAXONOMY[cat] if x]
                sub = subs[si] if si < len(subs) else ""
                n += 1
            else:
                cat, sub = "Other", ""
            conn.execute("""INSERT INTO merchant_categories(merchant_key,category,updated_at,subcategory,source) VALUES (?,?,?,?, 'ai')
                            ON CONFLICT(merchant_key) DO UPDATE SET category=excluded.category, subcategory=excluded.subcategory, updated_at=excluded.updated_at
                            WHERE merchant_categories.source='ai' AND merchant_categories.category='Other'""", (items[i]["key"], cat, now, sub))
        for i, it in enumerate(items):
            if i not in seen:
                conn.execute("""INSERT OR IGNORE INTO merchant_categories(merchant_key,category,updated_at,subcategory,source) VALUES (?,?,?,?, 'ai')""", (it["key"], "Other", now, ""))
    summary = recategorize()
    return {"ok": True, "asked": len(items), "classified": n, "remaining": summary["still_other"], "model": model}
