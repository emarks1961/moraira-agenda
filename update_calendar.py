\
from pathlib import Path
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
from urllib.parse import urljoin
import hashlib
import json
import re

import requests
from bs4 import BeautifulSoup
from dateutil import parser as dateparser
from icalendar import Calendar, Event

TZ = ZoneInfo("Europe/Madrid")
OUT = Path("moraira.ics")
NOW = datetime.now(TZ)
TODAY = NOW.date()

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; MorairaCalendarBot/2.0; +https://moraira-agenda.netlify.app/)"
}

# Bronnen: Moraira/Teulada eerst, daarna relevante plaatsen in de omgeving.
SOURCES = [
    {
        "name": "Teulada-Moraira officiële feestkalender",
        "url": "https://info-teulada-moraira.com/es/informacion-de-la-ciudad/cultura-vida-urbana/fiestas/calendario-de-fiestas/",
        "place": "Moraira / Teulada",
        "priority": 1,
    },
    {
        "name": "Saxo Disco Garden",
        "url": "https://www.moraira.info/en/events/saxo-disco-garden/",
        "place": "Moraira",
        "priority": 1,
    },
    {
        "name": "Benissa officiële agenda",
        "url": "https://www.benissa.es/en/categoria-agenda/eventos-en/?print=print-search",
        "place": "Benissa",
        "priority": 2,
    },
    {
        "name": "El Poble Nou de Benitatxell officiële agenda",
        "url": "https://elpoblenoudebenitatxell.com/agenda/",
        "place": "El Poble Nou de Benitatxell",
        "priority": 2,
    },
    {
        "name": "Xàbia officiële agenda",
        "url": "https://www.xabia.org/agendas/ver/1075/1/" + TODAY.isoformat(),
        "place": "Xàbia",
        "priority": 2,
    },
    {
        "name": "Calp officiële agenda",
        "url": "https://calpe.es/es/eventos",
        "place": "Calp",
        "priority": 2,
    },
]

MONTHS = {
    "ene":1, "enero":1, "jan":1, "january":1,
    "feb":2, "febrero":2, "february":2,
    "mar":3, "marzo":3, "march":3,
    "abr":4, "abril":4, "apr":4, "april":4,
    "may":5, "mayo":5,
    "jun":6, "junio":6, "june":6,
    "jul":7, "julio":7, "july":7,
    "ago":8, "agosto":8, "aug":8, "august":8,
    "sep":9, "sept":9, "septiembre":9, "september":9,
    "oct":10, "octubre":10, "october":10,
    "nov":11, "noviembre":11, "november":11,
    "dic":12, "diciembre":12, "dec":12, "december":12,
}

def get_soup(url):
    r = requests.get(url, headers=HEADERS, timeout=35)
    r.raise_for_status()
    return BeautifulSoup(r.text, "html.parser")

def clean(s):
    return re.sub(r"\s+", " ", (s or "")).strip()

def parse_dt(value):
    if not value:
        return None
    if isinstance(value, (date, datetime)):
        return value
    try:
        dt = dateparser.parse(str(value))
        if not dt:
            return None
        if isinstance(dt, datetime) and dt.tzinfo is None:
            dt = dt.replace(tzinfo=TZ)
        return dt
    except Exception:
        return None

def normalize_location(loc, fallback):
    if isinstance(loc, dict):
        name = clean(loc.get("name"))
        addr = loc.get("address")
        if isinstance(addr, dict):
            bits = [addr.get("streetAddress"), addr.get("addressLocality")]
            address = ", ".join(clean(x) for x in bits if x)
            return ", ".join(x for x in [name, address] if x) or fallback
        return name or fallback
    if isinstance(loc, str):
        return clean(loc) or fallback
    return fallback

def category_for(title, description=""):
    s = (title + " " + description).lower()
    rules = [
        ("Live muziek & concert", ["concert", "music", "música", "tribute", "tributo", "dj", "orquesta", "band", "festival musical"]),
        ("Fiesta & traditie", ["fiesta", "festes", "fiestas", "procesión", "procesio", "moros", "cristianos", "romería", "festa"]),
        ("Gastronomie & wijn", ["gastr", "vino", "wine", "moscatel", "moscatell", "tapa", "paella", "gourmet", "degust"]),
        ("Markt & winkelen", ["mercado", "market", "feria", "fira", "comercio", "shopping"]),
        ("Kunst & cultuur", ["expos", "arte", "art", "cultura", "teatro", "theatre", "cine", "cinema", "liter", "libro", "muse"]),
        ("Sport & buiten", ["sport", "race", "carrera", "walk", "sender", "yoga", "zumba", "ruta", "excurs"]),
    ]
    for cat, keys in rules:
        if any(k in s for k in keys):
            return cat
    return "Overig evenement"

def stable_uid(title, start, location):
    key = f"{clean(title).lower()}|{start}|{clean(location).lower()}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest() + "@moraira-calendar"

def event_dict(title, start, end=None, location="", description="", url="", source="", category=None):
    title = clean(title)
    if not title or not start:
        return None
    if not end:
        end = start
    return {
        "title": title,
        "start": start,
        "end": end,
        "location": clean(location),
        "description": clean(description),
        "url": url,
        "source": source,
        "category": category or category_for(title, description),
    }

def extract_jsonld(soup, source):
    found = []
    for tag in soup.find_all("script", type=lambda v: v and "ld+json" in v):
        raw = tag.string or tag.get_text()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except Exception:
            continue

        queue = data if isinstance(data, list) else [data]
        expanded = []
        for item in queue:
            if isinstance(item, dict) and isinstance(item.get("@graph"), list):
                expanded.extend(item["@graph"])
            else:
                expanded.append(item)

        for item in expanded:
            if not isinstance(item, dict):
                continue
            typ = item.get("@type")
            types = typ if isinstance(typ, list) else [typ]
            if not any(str(t).lower() == "event" for t in types if t):
                continue

            start = parse_dt(item.get("startDate"))
            end = parse_dt(item.get("endDate")) or start
            if not start:
                continue
            loc = normalize_location(item.get("location"), source["place"])
            desc = clean(BeautifulSoup(str(item.get("description","")), "html.parser").get_text(" "))
            url = item.get("url") or source["url"]
            e = event_dict(item.get("name"), start, end, loc, desc, url, source["name"])
            if e:
                found.append(e)
    return found

def extract_links_with_jsonld(soup, source, limit=80):
    found = []
    seen = set()
    domain = re.sub(r"^https?://([^/]+).*", r"\1", source["url"])
    for a in soup.find_all("a", href=True):
        href = urljoin(source["url"], a["href"])
        text = clean(a.get_text(" "))
        if href in seen:
            continue
        if domain not in href or len(text) < 4:
            continue
        if not any(k in href.lower() for k in ["/evento", "/event", "/agenda", "/agendas/"]):
            continue
        seen.add(href)
        if len(seen) > limit:
            break
        try:
            detail = get_soup(href)
            found.extend(extract_jsonld(detail, {**source, "url": href}))
        except Exception:
            pass
    return found

def extract_xabia_text(soup, source):
    # Xàbia publiceert op de agenda overzichtelijk titel, datum, tijden en zone.
    text = soup.get_text("\n", strip=True)
    lines = [clean(x) for x in text.splitlines() if clean(x)]
    found = []
    current_title = None
    for i, line in enumerate(lines):
        if re.match(r"^\d{2}/\d{2}/\d{4}(?:\s+al\s+\d{2}/\d{2}/\d{4})?$", line, re.I):
            if not current_title:
                continue
            bits = re.findall(r"\d{2}/\d{2}/\d{4}", line)
            st = datetime.strptime(bits[0], "%d/%m/%Y").replace(tzinfo=TZ)
            en = datetime.strptime(bits[-1], "%d/%m/%Y").replace(tzinfo=TZ)
            nearby = " ".join(lines[i+1:i+5])
            tm = re.search(r"Hora inicio:\s*(\d{1,2}:\d{2})", nearby, re.I)
            if tm:
                h,m = map(int, tm.group(1).split(":"))
                st = st.replace(hour=h, minute=m)
            zone = re.search(r"Zona:\s*([^|]+?)(?:\s{2,}|$)", nearby, re.I)
            loc = clean(zone.group(1)) + ", Xàbia" if zone else "Xàbia"
            found.append(event_dict(current_title, st, en, loc, "", source["url"], source["name"]))
        elif len(line) > 4 and not re.match(r"^\d", line) and not line.lower().startswith(("hora ", "zona:", "precio", "calendario", "eventos en")):
            # Titels op Xàbia-pagina staan als kopregels; dit is een voorzichtige fallback.
            if len(line) <= 140:
                current_title = line
    return [x for x in found if x]

def extract_benitatxell_cards(soup, source):
    # Agenda gebruikt kaartjes: dag, maand, optioneel einddag/eindmaand, daarna titel.
    found = []
    headings = soup.find_all(["h2","h3","h4"])
    for h in headings:
        title = clean(h.get_text(" "))
        if not title or title.lower() in ("próximos eventos","upcoming events","agenda"):
            continue
        card = h.parent
        txt = clean(card.get_text(" "))
        # Zoek vormen als 29 Ago of 19 Jun 19 Ago.
        pairs = re.findall(r"\b(\d{1,2})\s+(Ene|Feb|Mar|Abr|May|Jun|Jul|Ago|Sep|Oct|Nov|Dic|Jan|Apr|Aug|Dec)\b", txt, re.I)
        if not pairs:
            continue
        year_match = re.search(r"\b(20\d{2})\b", txt)
        year = int(year_match.group(1)) if year_match else TODAY.year
        dates = []
        for d, mon in pairs[:2]:
            month = MONTHS.get(mon.lower())
            if month:
                try:
                    dates.append(datetime(year, month, int(d), 10, 0, tzinfo=TZ))
                except ValueError:
                    pass
        if not dates:
            continue
        st, en = dates[0], (dates[1] if len(dates)>1 else dates[0])
        a = h.find("a", href=True) or card.find("a", href=True)
        url = urljoin(source["url"], a["href"]) if a else source["url"]
        found.append(event_dict(title, st, en, source["place"], "", url, source["name"]))
    return [x for x in found if x]

def collect():
    all_events = []
    status = []
    for source in SOURCES:
        try:
            soup = get_soup(source["url"])
            before = len(all_events)
            all_events.extend(extract_jsonld(soup, source))
            all_events.extend(extract_links_with_jsonld(soup, source))
            if "xabia.org" in source["url"]:
                all_events.extend(extract_xabia_text(soup, source))
            if "elpoblenoudebenitatxell.com" in source["url"]:
                all_events.extend(extract_benitatxell_cards(soup, source))
            status.append((source["name"], len(all_events)-before, "OK"))
        except Exception as e:
            status.append((source["name"], 0, f"FOUT: {e}"))

    for name, count, state in status:
        print(f"{name}: {count} gevonden ({state})")
    return all_events

def load_existing():
    if not OUT.exists():
        return {}
    try:
        cal = Calendar.from_ical(OUT.read_bytes())
    except Exception as e:
        print("Bestaande ICS kon niet worden gelezen:", e)
        return {}
    keep = {}
    for comp in cal.walk("VEVENT"):
        uid = str(comp.get("uid") or "")
        if uid:
            keep[uid] = comp
    return keep

def as_date_key(v):
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return None

def is_relevant_future(item):
    st = item["start"]
    d = as_date_key(st)
    if not d:
        return False
    # Houd vanaf 14 dagen terug t/m circa 18 maanden vooruit.
    return TODAY - timedelta(days=14) <= d <= TODAY + timedelta(days=550)

def dedupe(items):
    # Dedupe op genormaliseerde titel + begindatum; Moraira/Teulada bron wint bij doublures.
    chosen = {}
    for x in items:
        if not is_relevant_future(x):
            continue
        st = x["start"]
        key = (
            re.sub(r"[^a-z0-9]+", "", x["title"].lower())[:90],
            as_date_key(st).isoformat(),
        )
        score = 2 if ("Moraira" in x["location"] or "Teulada" in x["location"]) else 1
        old = chosen.get(key)
        if old is None or score > old[0]:
            chosen[key] = (score, x)
    return [v[1] for v in chosen.values()]

def add_event_component(item):
    ev = Event()
    uid = stable_uid(item["title"], item["start"], item["location"])
    ev.add("uid", uid)
    ev.add("summary", item["title"])
    ev.add("location", item["location"])
    desc = f"Categorie: {item['category']}\nBron: {item['source']}"
    if item["description"]:
        desc += f"\n\n{item['description']}"
    desc += f"\n\nMeer informatie: {item['url']}"
    ev.add("description", desc)
    ev.add("url", item["url"])
    ev.add("transp", "TRANSPARENT")
    ev.add("dtstamp", NOW)

    st, en = item["start"], item["end"]
    ev.add("dtstart", st)
    if isinstance(st, datetime):
        if isinstance(en, datetime) and en > st:
            ev.add("dtend", en)
        else:
            ev.add("dtend", st + timedelta(hours=2))
    else:
        end_date = en if isinstance(en, date) else st
        ev.add("dtend", end_date + timedelta(days=1))
    return uid, ev

def build():
    existing = load_existing()
    found = dedupe(collect())

    # Niet-destructief: bestaande events blijven behouden. Nieuwe/gevonden events worden
    # toegevoegd of vervangen als hun stabiele UID overeenkomt.
    keep = dict(existing)
    for item in found:
        uid, ev = add_event_component(item)
        keep[uid] = ev

    cal = Calendar()
    cal.add("prodid", "-//Moraira Calendar//NL")
    cal.add("version", "2.0")
    cal.add("calscale", "GREGORIAN")
    cal.add("method", "PUBLISH")
    cal.add("x-wr-calname", "Moraira & omgeving – Evenementen")
    cal.add("x-wr-timezone", "Europe/Madrid")
    cal.add("refresh-interval", timedelta(hours=1))
    cal.add("x-published-ttl", "PT1H")

    for uid in sorted(keep):
        cal.add_component(keep[uid])

    OUT.write_bytes(cal.to_ical())
    print(f"Klaar: {len(found)} online gevonden; {len(keep)} totaal in moraira.ics")

if __name__ == "__main__":
    build()
