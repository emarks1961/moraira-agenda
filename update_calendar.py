from pathlib import Path
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
from urllib.parse import urljoin
from collections import defaultdict
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
    "User-Agent": "Mozilla/5.0 (compatible; MorairaCalendarBot/3.0; +https://moraira-agenda.netlify.app/)"
}

SOURCES = [
    {"name":"Teulada-Moraira officiële feestkalender","url":"https://info-teulada-moraira.com/es/informacion-de-la-ciudad/cultura-vida-urbana/fiestas/calendario-de-fiestas/","place":"Moraira / Teulada","priority":"A"},
    {"name":"Saxo Disco Garden","url":"https://www.moraira.info/en/events/saxo-disco-garden/","place":"Moraira","priority":"A"},
    {"name":"Benissa officiële agenda","url":"https://www.benissa.es/en/categoria-agenda/eventos-en/?print=print-search","place":"Benissa","priority":"B"},
    {"name":"El Poble Nou de Benitatxell officiële agenda","url":"https://elpoblenoudebenitatxell.com/agenda/","place":"El Poble Nou de Benitatxell","priority":"B"},
    {"name":"Xàbia officiële agenda","url":"https://www.xabia.org/agendas/ver/1075/1/" + TODAY.isoformat(),"place":"Xàbia","priority":"C"},
    {"name":"Calp officiële agenda","url":"https://calpe.es/es/eventos","place":"Calp","priority":"C"},
]

MONTHS = {
    "ene":1,"enero":1,"jan":1,"january":1,"feb":2,"febrero":2,"february":2,
    "mar":3,"marzo":3,"march":3,"abr":4,"abril":4,"apr":4,"april":4,
    "may":5,"mayo":5,"jun":6,"junio":6,"june":6,"jul":7,"julio":7,"july":7,
    "ago":8,"agosto":8,"aug":8,"august":8,"sep":9,"sept":9,"septiembre":9,"september":9,
    "oct":10,"octubre":10,"october":10,"nov":11,"noviembre":11,"november":11,
    "dic":12,"diciembre":12,"dec":12,"december":12,
}

CATEGORY_RULES = [
    ("Live muziek & concert","🎵",["concert","music","música","musica","tribute","tributo","dj","orquesta","orquestra","band","festival musical","live music"]),
    ("Fiesta & traditie","🎉",["fiesta","festes","fiestas","procesión","procesio","moros","cristianos","romería","romeria","festa","verbena","focs","desfile"]),
    ("Gastronomie & wijn","🍷",["gastr","vino","wine","moscatel","moscatell","tapa","paella","gourmet","degust","cata","food"]),
    ("Markt & winkelen","🛍️",["mercado","market","feria","fira","comercio","shopping","artesanía","artesania"]),
    ("Kunst & cultuur","🎭",["expos","arte","art","cultura","teatro","theatre","cine","cinema","liter","libro","muse","danza","dance","auditori"]),
    ("Sport & buiten","🏃",["sport","race","carrera","walk","sender","ruta","excurs","regata","nautic","náutic","trail","cicl","bike","golf"]),
]

LOW_VALUE_WORDS = [
    "workshop","taller","curso","class","clase","club de lectura","meeting","reunión","reunion",
    "charla","seminario","training","entrenamiento","pilates","yoga","zumba","manualidades"
]

MAJOR_WORDS = [
    "festival","fiesta","festes","fiestas","concert","concierto","concerts","tribute","tributo",
    "gourmet","gastr","wine","vino","moscatel","moscatell","feria","fira","mercado medieval",
    "oktoberfest","race","regata","carrera","moros","cristianos","procesión","romería","expo",
    "exposición","exposicion","teatro","auditori","orquesta","orquestra","live music"
]

MAX_SURROUNDING_PER_WEEK = 10

def get_soup(url):
    r = requests.get(url, headers=HEADERS, timeout=35)
    r.raise_for_status()
    return BeautifulSoup(r.text, "html.parser")

def clean(s):
    return re.sub(r"\s+", " ", (s or "")).strip()

def parse_dt(value):
    if not value: return None
    if isinstance(value,(date,datetime)): return value
    try:
        dt = dateparser.parse(str(value))
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
            bits=[addr.get("streetAddress"),addr.get("addressLocality")]
            address=", ".join(clean(x) for x in bits if x)
            return ", ".join(x for x in [name,address] if x) or fallback
        return name or fallback
    if isinstance(loc,str): return clean(loc) or fallback
    return fallback

def category_for(title, description=""):
    s=(title+" "+description).lower()
    for cat,emoji,keys in CATEGORY_RULES:
        if any(k in s for k in keys): return cat,emoji
    return "Overig evenement","📌"

def stable_uid(title,start,location):
    key=f"{clean(title).lower()}|{start}|{clean(location).lower()}"
    return hashlib.sha1(key.encode()).hexdigest()+"@moraira-calendar"

def event_dict(title,start,end=None,location="",description="",url="",source="",priority="B"):
    title=clean(title)
    if not title or not start: return None
    cat,emoji=category_for(title,description)
    return {"title":title,"start":start,"end":end or start,"location":clean(location),"description":clean(description),
            "url":url,"source":source,"priority":priority,"category":cat,"emoji":emoji}

def extract_jsonld(soup, source):
    found=[]
    for tag in soup.find_all("script", type=lambda v:v and "ld+json" in v):
        raw=tag.string or tag.get_text()
        if not raw: continue
        try: data=json.loads(raw)
        except Exception: continue
        queue=data if isinstance(data,list) else [data]
        expanded=[]
        for item in queue:
            if isinstance(item,dict) and isinstance(item.get("@graph"),list): expanded.extend(item["@graph"])
            else: expanded.append(item)
        for item in expanded:
            if not isinstance(item,dict): continue
            typ=item.get("@type"); types=typ if isinstance(typ,list) else [typ]
            if not any(str(t).lower()=="event" for t in types if t): continue
            start=parse_dt(item.get("startDate")); end=parse_dt(item.get("endDate")) or start
            if not start: continue
            loc=normalize_location(item.get("location"),source["place"])
            desc=clean(BeautifulSoup(str(item.get("description","")),"html.parser").get_text(" "))
            url=item.get("url") or source["url"]
            e=event_dict(item.get("name"),start,end,loc,desc,url,source["name"],source["priority"])
            if e: found.append(e)
    return found

def extract_links_with_jsonld(soup, source, limit=60):
    found=[]; seen=set()
    domain=re.sub(r"^https?://([^/]+).*",r"\1",source["url"])
    for a in soup.find_all("a",href=True):
        href=urljoin(source["url"],a["href"]); text=clean(a.get_text(" "))
        if href in seen or domain not in href or len(text)<4: continue
        if not any(k in href.lower() for k in ["/evento","/event","/agenda","/agendas/"]): continue
        seen.add(href)
        if len(seen)>limit: break
        try:
            found.extend(extract_jsonld(get_soup(href),{**source,"url":href}))
        except Exception:
            pass
    return found

def extract_xabia_text(soup,source):
    lines=[clean(x) for x in soup.get_text("\n",strip=True).splitlines() if clean(x)]
    found=[]; current_title=None
    for i,line in enumerate(lines):
        if re.match(r"^\d{2}/\d{2}/\d{4}(?:\s+al\s+\d{2}/\d{2}/\d{4})?$",line,re.I):
            if not current_title: continue
            bits=re.findall(r"\d{2}/\d{2}/\d{4}",line)
            st=datetime.strptime(bits[0],"%d/%m/%Y").replace(tzinfo=TZ)
            en=datetime.strptime(bits[-1],"%d/%m/%Y").replace(tzinfo=TZ)
            nearby=" ".join(lines[i+1:i+5])
            tm=re.search(r"Hora inicio:\s*(\d{1,2}:\d{2})",nearby,re.I)
            if tm:
                h,m=map(int,tm.group(1).split(":")); st=st.replace(hour=h,minute=m)
            zone=re.search(r"Zona:\s*([^|]+?)(?:\s{2,}|$)",nearby,re.I)
            loc=clean(zone.group(1))+", Xàbia" if zone else "Xàbia"
            found.append(event_dict(current_title,st,en,loc,"",source["url"],source["name"],source["priority"]))
        elif len(line)>4 and not re.match(r"^\d",line) and not line.lower().startswith(("hora ","zona:","precio","calendario","eventos en")):
            if len(line)<=140: current_title=line
    return [x for x in found if x]

def extract_benitatxell_cards(soup,source):
    found=[]
    for h in soup.find_all(["h2","h3","h4"]):
        title=clean(h.get_text(" "))
        if not title or title.lower() in ("próximos eventos","upcoming events","agenda"): continue
        card=h.parent; txt=clean(card.get_text(" "))
        pairs=re.findall(r"\b(\d{1,2})\s+(Ene|Feb|Mar|Abr|May|Jun|Jul|Ago|Sep|Oct|Nov|Dic|Jan|Apr|Aug|Dec)\b",txt,re.I)
        if not pairs: continue
        ym=re.search(r"\b(20\d{2})\b",txt); year=int(ym.group(1)) if ym else TODAY.year
        dates=[]
        for d,mon in pairs[:2]:
            month=MONTHS.get(mon.lower())
            if month:
                try: dates.append(datetime(year,month,int(d),10,0,tzinfo=TZ))
                except ValueError: pass
        if not dates: continue
        st,en=dates[0],dates[1] if len(dates)>1 else dates[0]
        a=h.find("a",href=True) or card.find("a",href=True)
        url=urljoin(source["url"],a["href"]) if a else source["url"]
        found.append(event_dict(title,st,en,source["place"],"",url,source["name"],source["priority"]))
    return [x for x in found if x]

def collect():
    all_events=[]
    for source in SOURCES:
        try:
            soup=get_soup(source["url"])
            all_events.extend(extract_jsonld(soup,source))
            all_events.extend(extract_links_with_jsonld(soup,source))
            if "xabia.org" in source["url"]: all_events.extend(extract_xabia_text(soup,source))
            if "elpoblenoudebenitatxell.com" in source["url"]: all_events.extend(extract_benitatxell_cards(soup,source))
        except Exception as e:
            print(source["name"],"FOUT:",e)
    return all_events

def load_existing():
    if not OUT.exists(): return {}
    cal=Calendar.from_ical(OUT.read_bytes()); keep={}
    for comp in cal.walk("VEVENT"):
        uid=str(comp.get("uid") or "")
        if uid: keep[uid]=comp
    return keep

def as_date_key(v):
    if isinstance(v,datetime): return v.date()
    if isinstance(v,date): return v
    return None

def is_relevant_future(item):
    d=as_date_key(item["start"])
    return bool(d and TODAY-timedelta(days=14)<=d<=TODAY+timedelta(days=550))

def text_blob(item):
    return f"{item['title']} {item['description']} {item['location']}".lower()

def selection_score(item):
    s=text_blob(item); score=0
    if item["category"]!="Overig evenement": score+=3
    if any(k in s for k in MAJOR_WORDS): score+=4
    if item["category"] in ("Live muziek & concert","Fiesta & traditie","Gastronomie & wijn"): score+=2
    if item["category"]=="Kunst & cultuur": score+=1
    if any(k in s for k in LOW_VALUE_WORDS): score-=5
    return score

def keep_by_priority(item):
    p=item.get("priority","B"); s=text_blob(item); low=any(k in s for k in LOW_VALUE_WORDS)
    if p=="A": return not (low and item["category"]=="Overig evenement")
    if p=="B": return item["category"]!="Overig evenement" and not low
    return selection_score(item)>=5

def dedupe(items):
    chosen={}
    for x in items:
        if not is_relevant_future(x) or not keep_by_priority(x): continue
        key=(re.sub(r"[^a-z0-9]+","",x["title"].lower())[:90],as_date_key(x["start"]).isoformat())
        ps={"A":3,"B":2,"C":1}.get(x.get("priority"),1)
        if key not in chosen or ps>chosen[key][0]: chosen[key]=(ps,x)
    return [v[1] for v in chosen.values()]

def limit_surrounding_per_week(items):
    a=[x for x in items if x.get("priority")=="A"]
    surrounding=[x for x in items if x.get("priority")!="A"]
    by_week=defaultdict(list)
    for x in surrounding:
        iso=as_date_key(x["start"]).isocalendar()
        by_week[(iso.year,iso.week)].append(x)
    selected=list(a)
    for group in by_week.values():
        group.sort(key=lambda x:(selection_score(x),x.get("priority")=="B"),reverse=True)
        selected.extend(group[:MAX_SURROUNDING_PER_WEEK])
    return selected

def add_event_component(item):
    ev=Event(); uid=stable_uid(item["title"],item["start"],item["location"])
    place=item["location"].split(",")[-1].strip() if item["location"] else ""
    title=f"{item['emoji']} {item['title']}"
    if place and place.lower() not in item["title"].lower(): title+=f" · {place}"
    ev.add("uid",uid); ev.add("summary",title); ev.add("location",item["location"])
    desc=f"Categorie: {item['category']}\nBron: {item['source']}"
    if item["description"]: desc+=f"\n\n{item['description']}"
    desc+=f"\n\nMeer informatie: {item['url']}"
    ev.add("description",desc); ev.add("url",item["url"]); ev.add("transp","TRANSPARENT"); ev.add("dtstamp",NOW)
    st,en=item["start"],item["end"]; ev.add("dtstart",st)
    if isinstance(st,datetime): ev.add("dtend",en if isinstance(en,datetime) and en>st else st+timedelta(hours=2))
    else: ev.add("dtend",(en if isinstance(en,date) else st)+timedelta(days=1))
    return uid,ev

def existing_is_bot_event(comp):
    desc=str(comp.get("description") or "")
    return "Bron:" in desc and "Meer informatie:" in desc

def existing_location_priority(comp):
    loc=str(comp.get("location") or "").lower()
    if "moraira" in loc or "teulada" in loc: return "A"
    if "benissa" in loc or "benitatxell" in loc: return "B"
    if "xàbia" in loc or "xabia" in loc or "calp" in loc or "calpe" in loc: return "C"
    return "A"

def build():
    existing=load_existing()
    found=limit_surrounding_per_week(dedupe(collect()))
    keep={}
    for uid,comp in existing.items():
        p=existing_location_priority(comp)
        if not existing_is_bot_event(comp) or p=="A":
            keep[uid]=comp
    for item in found:
        uid,ev=add_event_component(item); keep[uid]=ev
    cal=Calendar()
    for k,v in [("prodid","-//Moraira Calendar//NL"),("version","2.0"),("calscale","GREGORIAN"),
                ("method","PUBLISH"),("x-wr-calname","Moraira & omgeving – Evenementen"),("x-wr-timezone","Europe/Madrid")]:
        cal.add(k,v)
    cal.add("refresh-interval",timedelta(hours=1)); cal.add("x-published-ttl","PT1H")
    for uid in sorted(keep): cal.add_component(keep[uid])
    OUT.write_bytes(cal.to_ical())
    a_count=sum(1 for x in found if x.get("priority")=="A")
    print(f"Klaar: {len(found)} geselecteerd ({a_count} Moraira/Teulada, {len(found)-a_count} omgeving); {len(keep)} totaal.")

if __name__=="__main__":
    build()
