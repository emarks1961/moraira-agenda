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

# Bewaar-venster: items die langer dan dit geleden zijn afgelopen worden gewist,
# items die verder dan dit vooruit liggen worden genegeerd.
PAST_GRACE_DAYS = 14
FUTURE_HORIZON_DAYS = 550

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; MorairaCalendarBot/2.1; +https://moraira-agenda.netlify.app/)"
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

# ---------------------------------------------------------------------------
# Categorie-normalisatie — één vaste set van ~9 types (was ~20 losse strings)
# Volgorde telt: eerste match wint. Zelfde volgorde als de website (index.html).
# ---------------------------------------------------------------------------
CANON = [
    ("Fiesta & traditie", r"fiesta|festes|festa|traditie|vuurwerk|processie|procesi|romer|moros|cristian|ofrenda|desfil|praalwagen|bloemenoffer|correfoc|castell|mascleta|mascletà|\bcolla|dolçaina|dansa|nit de foc|barraca|penya|peña|bous|toro"),
    ("Muziek",            r"muziek|music|musica|concert|concierto|\bdj\b|orquesta|tribut|\bband\b|rondalla|discomovil|discomóvil|\bjam\b|cantada|coral|simfòn|simfon"),
    ("Familie",           r"famili|kinder|\bkids\b|infantil|niñ|petorro|marionet|titell|bebeteca|nadó|contacont|cuentacuent|taller infantil"),
    ("Sport & buiten",    r"sport|deportiv|esportiv|\brace\b|regatt|regata|carrera|cursa|\btrail\b|marcha|petanca|running|senderis|excursi|caminata|triatl|trixab|trixàb|desafí|desafio|palada|natació|nataci|ciclis"),
    ("Gastronomie & wijn",r"gastro|wijn|\bwine\b|\bvino\b|moscat|\btapa|paella|gourmet|cerveza|oktoberfest|degust|\benot|apitur|apicultura|cuina"),
    ("Theater & film",    r"\bfilm|\bcine\b|theater|teatro|comed|\bmagic|\bmago\b|circ|\bshow\b|espectacul|entertain|monolog"),
    ("Kunst & cultuur",   r"kunst|\bart\b|expos|cultuur|cultura|escultura|sculpt|museo|museu|\bfoto|exfil|monument|pintura|conferenc|xerrada|charla|jornada|\blibro|\bllibre|lectur|literat|biblioteca|\bpoes|\bpoem|recital|taller"),
    ("Markt & winkelen",  r"markt|mercad|winkel|feria|fira|comercio|rastro|artesan"),
    ("Natuur & rondleiding", r"natuur|rondleiding|wandel|\bwalk\b|\bruta\b|sender|\bpaseo|maanlicht|guided|visita guiada|astronom"),
]


def canonical_category(*parts):
    hay = " ".join(p for p in parts if p).lower()
    for name, pattern in CANON:
        if re.search(pattern, hay):
            return name
    return "Overig"


# Plaats-normalisatie — zelfde buckets als de website.
def place_of(location=""):
    s = (location or "").lower()
    if "moraira" in s:
        return "Moraira"
    if "teulada" in s:
        return "Teulada"
    if "benissa" in s:
        return "Benissa"
    if "benitatxell" in s or "poble nou" in s:
        return "Benitatxell"
    if "xàbia" in s or "xabia" in s or "jávea" in s or "javea" in s:
        return "Xàbia"
    if "calp" in s or "calpe" in s:
        return "Calp"
    tail = location.split(",")[-1].strip() if location else ""
    return tail or "Onbekend"


def get_soup(url):
    r = requests.get(url, headers=HEADERS, timeout=35)
    r.raise_for_status()
    return BeautifulSoup(r.text, "html.parser")


def clean(s):
    return re.sub(r"\s+", " ", (s or "")).strip()


def clean_link(url=""):
    return re.sub(r"\?print=print-search\b", "", (url or "").strip())


def localize_link(url=""):
    """Wijs bronlinks naar de Nederlandstalige paginaversie waar die bestaat.
    Idempotent: draait elke run over zowel nieuwe als bestaande items."""
    u = (url or "").strip()
    if not re.match(r"^https?://", u):
        return u
    # moraira.info — elke taal -> Nederlandse /nl/evenementen/...
    u = re.sub(
        r"^(https?://)(?:www\.)?moraira\.info/(?:en|de|fr|es|nl)/"
        r"(?:events|eventos|evenements|veranstaltungen|evenementen)/",
        r"\1www.moraira.info/nl/evenementen/", u)
    # Saxo Disco Garden — spring meteen naar de datumlijst op de venue-pagina
    u = re.sub(
        r"^(https?://www\.moraira\.info/nl/evenementen/saxo-disco-garden)/?(?:#\S*)?$",
        r"\1/#termine", u)
    # benissa.es — elke taal -> /nl/  (werkt voor de agenda én per-evenementpagina's)
    u = re.sub(r"^(https?://)(?:www\.)?benissa\.es/(?:en|de|fr|es|va|nl)/",
               r"\1www.benissa.es/nl/", u)
    # Teulada-Moraira — geen bruikbare diepere agenda-URL, dus de NL-startpagina
    u = re.sub(r"^(https?://)(?:www\.)?info-teulada-moraira\.com/.*$",
               r"\1info-teulada-moraira.com/nl/", u)
    # xabia.org heeft geen Nederlands -> Engels i.p.v. het Spaanse www.
    u = re.sub(r"^(https?://)(?:www\.)?xabia\.org/", r"\1en.xabia.org/", u)
    return u


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


def stable_uid(title, start, location):
    key = f"{clean(title).lower()}|{start}|{clean(location).lower()}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest() + "@moraira-calendar"


def event_dict(title, start, end=None, location="", description="", url="", source="", category=None, stamp=None):
    title = clean(title)
    if not title or not start:
        return None
    if not end:
        end = start
    location = clean(location)
    description = clean(description)
    return {
        "title": title,
        "start": start,
        "end": end,
        "location": location,
        "place": place_of(location),
        "description": description,
        "url": localize_link(clean_link(url)),
        "source": source,
        "category": canonical_category(category or "", title, description),
        "stamp": stamp or NOW,
    }


# ---------------------------------------------------------------------------
# Scrapers (ongewijzigd t.o.v. vorige versie)
# ---------------------------------------------------------------------------
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
            desc = clean(BeautifulSoup(str(item.get("description", "")), "html.parser").get_text(" "))
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
            nearby = " ".join(lines[i + 1:i + 5])
            tm = re.search(r"Hora inicio:\s*(\d{1,2}:\d{2})", nearby, re.I)
            if tm:
                h, m = map(int, tm.group(1).split(":"))
                st = st.replace(hour=h, minute=m)
            zone = re.search(r"Zona:\s*([^|]+?)(?:\s{2,}|$)", nearby, re.I)
            loc = clean(zone.group(1)) + ", Xàbia" if zone else "Xàbia"
            found.append(event_dict(current_title, st, en, loc, "", source["url"], source["name"]))
        elif len(line) > 4 and not re.match(r"^\d", line) and not line.lower().startswith(("hora ", "zona:", "precio", "calendario", "eventos en")):
            if len(line) <= 140:
                current_title = line
    return [x for x in found if x]


def extract_benitatxell_cards(soup, source):
    found = []
    headings = soup.find_all(["h2", "h3", "h4"])
    for h in headings:
        title = clean(h.get_text(" "))
        if not title or title.lower() in ("próximos eventos", "upcoming events", "agenda"):
            continue
        card = h.parent
        txt = clean(card.get_text(" "))
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
        st, en = dates[0], (dates[1] if len(dates) > 1 else dates[0])
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
            status.append((source["name"], len(all_events) - before, "OK"))
        except Exception as e:
            status.append((source["name"], 0, f"FOUT: {e}"))

    ok = 0
    for name, count, state in status:
        print(f"  {name}: {count} gevonden ({state})")
        if state == "OK":
            ok += 1
    return all_events, ok


# ---------------------------------------------------------------------------
# Bestaande moraira.ics inlezen als dezelfde interne dicts
# ---------------------------------------------------------------------------
def _strip_description(desc):
    out = []
    for ln in (desc or "").replace("\\n", "\n").splitlines():
        s = ln.strip().replace("\\", "")
        if re.match(r"(?i)^(categorie|rubriek|bron|meer informatie)\s*:", s):
            continue
        s = re.sub(r"(?i)\s*(bron|meer informatie)\s*:\s*https?://\S+", "", s)
        s = re.sub(r"(?i)\s*controleer kort voor vertrek[^\n]*", "", s)
        if s.strip():
            out.append(s.strip())
    return clean(" ".join(out))


def _recover_url(desc, url_prop):
    u = clean_link(str(url_prop)) if url_prop else ""
    if re.match(r"^https?://", u):
        return u
    m = re.search(r"(?i)(?:bron|meer informatie)\s*:\s*(https?://\S+)", desc or "")
    return clean_link(m.group(1)) if m else ""


def load_existing_items():
    """Returns (items, parse_failed, size_bytes)."""
    if not OUT.exists():
        return [], False, 0
    size = OUT.stat().st_size
    if size < 64:
        return [], False, size
    try:
        cal = Calendar.from_ical(OUT.read_bytes())
    except Exception as e:
        print("Bestaande ICS kon niet worden gelezen:", e)
        return [], True, size

    items = []
    for comp in cal.walk("VEVENT"):
        try:
            dtstart = comp.get("dtstart")
            if not dtstart:
                continue
            start = dtstart.dt
            end = comp.get("dtend").dt if comp.get("dtend") else start
            raw_desc = str(comp.get("description") or "")
            desc = _strip_description(raw_desc)
            location = clean(str(comp.get("location") or ""))
            cats = comp.get("categories")
            cat_raw = ", ".join(str(c) for c in cats.cats) if hasattr(cats, "cats") else clean(str(cats or ""))
            stamp = comp.get("dtstamp").dt if comp.get("dtstamp") else NOW
            e = event_dict(
                str(comp.get("summary") or ""),
                start, end, location, desc,
                _recover_url(raw_desc, comp.get("url")),
                "bestaand", cat_raw, stamp,
            )
            if e:
                items.append(e)
        except Exception as ex:
            print("  item overgeslagen:", ex)
    return items, False, size


# ---------------------------------------------------------------------------
# Samenvoegen + opschonen
# ---------------------------------------------------------------------------
def as_d(v):
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return None


def eff_end(item):
    st, en = item["start"], item.get("end") or item["start"]
    if isinstance(en, datetime) and isinstance(st, datetime):
        if en > st and en.hour == 0 and en.minute == 0:
            en = en - timedelta(seconds=1)
    return en


def in_window(item):
    d = as_d(eff_end(item))
    s = as_d(item["start"])
    if not d or not s:
        return False
    return (TODAY - timedelta(days=PAST_GRACE_DAYS)) <= d and s <= (TODAY + timedelta(days=FUTURE_HORIZON_DAYS))


def _norm_title(s):
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def _title_tokens(s):
    return set(re.findall(r"[a-z0-9]{3,}", (s or "").lower()))


def _overlap(a, b):
    return as_d(a["start"]) <= as_d(eff_end(b)) and as_d(eff_end(a)) >= as_d(b["start"])


def _score(item):
    # Moraira/Teulada-bron wint bij twijfel; daarna langere beschrijving.
    return (2 if item["place"] in ("Moraira", "Teulada") else 1, len(item["description"]))


def _merge(hit, e):
    if as_d(e["start"]) < as_d(hit["start"]):
        hit["start"] = e["start"]
    if as_d(eff_end(e)) > as_d(eff_end(hit)):
        hit["end"] = e.get("end") or e["start"]
    if len(e["title"]) > len(hit["title"]):
        hit["title"] = e["title"]
    if len(e["description"]) > len(hit["description"]):
        hit["description"] = e["description"]
    if not hit["url"] and e["url"]:
        hit["url"] = e["url"]
    if not hit["location"] and e["location"]:
        hit["location"] = e["location"]
        hit["place"] = e["place"]
    if e["stamp"] and (not hit["stamp"] or e["stamp"] > hit["stamp"]):
        hit["stamp"] = e["stamp"]
    hit["category"] = canonical_category(hit["category"], hit["title"], hit["description"])


def dedupe(items):
    items = [x for x in items if x and x.get("start")]
    items.sort(key=lambda x: (as_d(x["start"]) or date.min, -_score(x)[0]))

    # pass 1 — identieke genormaliseerde titel + overlappende datums
    buckets = {}
    for e in items:
        bucket = buckets.setdefault(_norm_title(e["title"]), [])
        hit = next((x for x in bucket if _overlap(e, x)), None)
        if hit:
            _merge(hit, e)
        else:
            bucket.append(e)

    # pass 2 — zelfde dag + zelfde plaats + sterke woordoverlap in de titel
    out = []
    for e in (x for b in buckets.values() for x in b):
        tk = _title_tokens(e["title"])
        hit = None
        for x in out:
            if x["place"] != e["place"] or as_d(x["start"]) != as_d(e["start"]):
                continue
            xt = _title_tokens(x["title"])
            inter = len(tk & xt)
            if inter >= 2 and inter / max(1, min(len(tk), len(xt))) >= 0.5:
                hit = x
                break
        if hit:
            _merge(hit, e)
        else:
            out.append(e)
    return out


# ---------------------------------------------------------------------------
# Schrijven
# ---------------------------------------------------------------------------
def _is_pure_date(v):
    return isinstance(v, date) and not isinstance(v, datetime)


def _as_dt(v):
    """Maak er een tz-bewuste datetime van (pure date -> middernacht Europe/Madrid)."""
    if _is_pure_date(v):
        return datetime(v.year, v.month, v.day, tzinfo=TZ)
    if isinstance(v, datetime) and v.tzinfo is None:
        return v.replace(tzinfo=TZ)
    return v


def add_event_component(item):
    ev = Event()
    st, en = item["start"], item["end"] or item["start"]

    uid = stable_uid(item["title"], st, item["location"])
    ev.add("uid", uid)
    ev.add("summary", item["title"])
    if item["location"]:
        ev.add("location", item["location"])
    if item["description"]:
        ev.add("description", item["description"])
    if re.match(r"^https?://", item["url"] or ""):
        ev.add("url", item["url"])
    ev.add("categories", [item["category"]])
    ev.add("transp", "TRANSPARENT")
    ev.add("dtstamp", _as_dt(item["stamp"] or NOW))

    if _is_pure_date(st) and _is_pure_date(en):
        # hele-dag evenement: DTEND is exclusief -> dag erna
        if en < st:
            en = st
        ev.add("dtstart", st)
        ev.add("dtend", en + timedelta(days=1))
    else:
        st, en = _as_dt(st), _as_dt(en)
        if en <= st:
            en = st + timedelta(hours=2)
        ev.add("dtstart", st)
        ev.add("dtend", en)
    return uid, ev


def build():
    existing, parse_failed, size = load_existing_items()
    if parse_failed:
        print("Afgebroken: bestaande moraira.ics onleesbaar (geen overschrijving, geen dataverlies).")
        return
    if size > 5000 and len(existing) < 10:
        print(f"Afgebroken: bestaande moraira.ics lijkt beschadigd ({size} bytes, {len(existing)} items).")
        return

    print("Bronnen controleren…")
    scraped, sources_ok = collect()

    if not existing and not scraped:
        print("Afgebroken: geen bestaande en geen nieuwe items.")
        return

    merged = [x for x in dedupe([*existing, *scraped]) if in_window(x)]

    # Veiligheidsrem: als geen enkele bron werkte én de agenda fors zou krimpen,
    # dan is er waarschijnlijk iets mis — laat het oude bestand staan.
    if existing and sources_ok == 0 and len(merged) < 0.3 * len(existing):
        print(f"Afgebroken: alle bronnen onbereikbaar en resultaat te klein "
              f"({len(merged)} vs {len(existing)} bestaand).")
        return

    cal = Calendar()
    cal.add("prodid", "-//Moraira Calendar//NL")
    cal.add("version", "2.0")
    cal.add("calscale", "GREGORIAN")
    cal.add("method", "PUBLISH")
    cal.add("x-wr-calname", "Moraira & omgeving – Evenementen")
    cal.add("x-wr-timezone", "Europe/Madrid")
    # REFRESH-INTERVAL bewust weggelaten: icalendar 7.x weigert een string en
    # serialiseert een timedelta fout. X-PUBLISHED-TTL geeft dezelfde hint en
    # werkt als kale tekst op elke versie.
    cal.add("x-published-ttl", "PT1H")

    by_uid = {}
    skipped = 0
    for item in merged:
        try:
            uid, ev = add_event_component(item)
            by_uid[uid] = ev
        except Exception as ex:
            skipped += 1
            print(f"  item overgeslagen bij schrijven ({item.get('title')!r}): {ex}")
    for uid in sorted(by_uid):
        cal.add_component(by_uid[uid])

    if size > 5000 and len(by_uid) < max(10, 0.3 * len(existing)):
        print(f"Afgebroken: te weinig events om weg te schrijven ({len(by_uid)}); oude bestand blijft staan.")
        return

    OUT.write_bytes(cal.to_ical())

    removed = len(existing) + len(scraped) - len(by_uid)
    print(f"Klaar: {len(existing)} bestaand + {len(scraped)} online → "
          f"{len(by_uid)} in moraira.ics (±{max(removed, 0)} samengevoegd/verlopen"
          f"{f'; {skipped} overgeslagen' if skipped else ''}).")


if __name__ == "__main__":
    import sys
    import traceback
    try:
        build()
    except Exception:
        traceback.print_exc()
        print("\nFOUT: moraira.ics is NIET gewijzigd.")
        sys.exit(1)
