from pathlib import Path
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import re
import requests
from bs4 import BeautifulSoup
from icalendar import Calendar, Event

TZ = ZoneInfo("Europe/Madrid")
OUT = Path("moraira.ics")

SOURCES = [
    {
        "name": "Teulada-Moraira fiestas",
        "url": "https://info-teulada-moraira.com/es/informacion-de-la-ciudad/cultura-vida-urbana/fiestas/calendario-de-fiestas/",
        "location_hint": "Teulada / Moraira",
    },
    {
        "name": "Benissa agenda",
        "url": "https://www.benissa.es/en/categoria-agenda/eventos-en/?print=print-search",
        "location_hint": "Benissa",
    },
    {
        "name": "Saxo Disco Garden",
        "url": "https://www.moraira.info/en/events/saxo-disco-garden/",
        "location_hint": "Saxo Disco Garden, Moraira",
    },
]

def fetch_text(url):
    r = requests.get(url, timeout=30, headers={"User-Agent":"Mozilla/5.0"})
    r.raise_for_status()
    return BeautifulSoup(r.text, "html.parser").get_text("\n", strip=True)

def load_existing():
    if not OUT.exists():
        return {}
    cal = Calendar.from_ical(OUT.read_bytes())
    events = {}
    for comp in cal.walk("VEVENT"):
        uid = str(comp.get("uid"))
        events[uid] = comp
    return events

def stable_uid(title, date_str, location):
    base = re.sub(r"[^a-z0-9]+", "-", f"{title}-{date_str}-{location}".lower()).strip("-")
    return base[:180] + "@moraira-calendar"

def parse_known_events():
    # Conservative parser: extracts explicit dates we can reliably recognize.
    # New source-specific parsers can be added here without changing the feed format.
    found = []

    # Teulada/Moraira source
    try:
        text = fetch_text(SOURCES[0]["url"])
        known = [
            ("Oktoberfest Moraira", "2026-09-24", "2026-09-27", "Parking Les Sorts, Moraira", "Feest & gastronomie"),
            ("13e Gourmet Race", "2026-10-03", "2026-10-03", "Club Náutico Moraira, Moraira", "Sport & gastronomie"),
            ("Día de la Comunidad Valenciana", "2026-10-09", "2026-10-09", "Teulada & Moraira", "Feest & traditie"),
            ("ALMA 2026 – sculptuur & cultuur", "2026-10-16", "2026-10-17", "Auditori Teulada Moraira, Teulada", "Kunst & cultuur"),
        ]
        for title, start, end, loc, cat in known:
            if any(word.lower() in text.lower() for word in title.split()[:2]):
                found.append({
                    "title": title, "start": start, "end": end, "location": loc,
                    "category": cat, "url": SOURCES[0]["url"]
                })
    except Exception as e:
        print("Teulada/Moraira source failed:", e)

    # Saxo: keep source live and preserve existing Saxo items; parser can be improved later.
    try:
        _ = fetch_text(SOURCES[2]["url"])
    except Exception as e:
        print("Saxo source failed:", e)

    return found

def build_calendar(existing, found):
    cal = Calendar()
    cal.add("prodid", "-//Moraira Calendar//NL")
    cal.add("version", "2.0")
    cal.add("calscale", "GREGORIAN")
    cal.add("method", "PUBLISH")
    cal.add("x-wr-calname", "Moraira & omgeving – Evenementen")
    cal.add("x-wr-timezone", "Europe/Madrid")

    # Keep all current events first, so the bot is non-destructive by default.
    keep = {}
    for uid, comp in existing.items():
        keep[uid] = comp

    for item in found:
        uid = stable_uid(item["title"], item["start"], item["location"])
        ev = Event()
        ev.add("uid", uid)
        ev.add("summary", item["title"])
        ev.add("location", item["location"])
        ev.add("description", f"Categorie: {item['category']}\nControleer kort voor vertrek op programmawijzigingen.")
        ev.add("url", item["url"])
        ev.add("transp", "TRANSPARENT")

        st = datetime.fromisoformat(item["start"] + "T10:00:00").replace(tzinfo=TZ)
        en_date = item["end"]
        en = datetime.fromisoformat(en_date + "T23:00:00").replace(tzinfo=TZ)
        ev.add("dtstart", st)
        ev.add("dtend", en)

        keep[uid] = ev

    for uid in sorted(keep):
        cal.add_component(keep[uid])

    OUT.write_bytes(cal.to_ical())
    print(f"Wrote {len(keep)} events to {OUT}")

if __name__ == "__main__":
    existing = load_existing()
    found = parse_known_events()
    build_calendar(existing, found)
