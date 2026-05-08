import os, json, re, sys
import urllib.request, urllib.error
from datetime import datetime, timezone, timedelta, date
from pathlib import Path

try:
    import anthropic
except ImportError:
    print("ERROR: pip install anthropic")
    sys.exit(1)

COLOMBIA_TZ = timezone(timedelta(hours=-5))
HTML_FILE   = Path(__file__).parent / "index.html"
API_KEY     = os.environ.get("ANTHROPIC_API_KEY", "")
SG_KEY      = os.environ.get("SENDGRID_API_KEY", "")
EMAIL_FROM  = os.environ.get("EMAIL_FROM", "")
EMAIL_TO    = os.environ.get("EMAIL_TO", "")
MAP_URL     = os.environ.get("MAP_URL", "https://bcorreia-coh.github.io/colombia-risk-map/")
MESES       = {1:'ene',2:'feb',3:'mar',4:'abr',5:'may',6:'jun',
               7:'jul',8:'ago',9:'sep',10:'oct',11:'nov',12:'dic'}
EVENT_WINDOW_DAYS  = 30   # events older than this expire from score
WHITE_WINDOW_DAYS  = 60   # focus dept municipalities older than this = WHITE
FOCUS_DEPTS = {'cauca','valle del cauca','narino','nariño'}

# Rubrica CoH Seccion 3
def get_multiplier(n):
    if n >= 8: return 2.5
    if n >= 5: return 2.0
    if n >= 3: return 1.5
    if n >= 2: return 1.25
    return 1.0

def score_to_zone(s):
    if s >= 30: return 'red'
    if s >= 15: return 'orange'
    if s >= 5:  return 'yellow'
    return 'green'

def score_to_size(s):
    if s >= 30: return 22
    if s >= 20: return 20
    if s >= 15: return 18
    if s >= 10: return 16
    if s >= 5:  return 13
    return 10

RESTRICTED_DEPTS = {
    'cauca','valle del cauca','narino','nariño','antioquia',
    'choco','chocó','putumayo','huila','tolima','arauca',
    'norte de santander','caqueta','caquetá'
}
EXCLUDED_CITIES = {
    'popayan','popayán','cali','palmira','pasto',
    'medellin','medellín','bello','envigado','itagui','itagüí'
}
ZONE_ES    = {'red':'ROJO','orange':'NARANJA','yellow':'AMARILLO','green':'VERDE'}
ZONE_COLOR = {'red':'#dc2626','orange':'#ea580c','yellow':'#ca8a04','green':'#15803d'}
ZONE_BG    = {'red':'#fef2f2','orange':'#fff7ed','yellow':'#fefce8','green':'#f0fdf4'}

# Date utilities
def parse_event_date(date_str):
    try:
        return datetime.strptime(str(date_str).strip(), '%Y-%m-%d').date()
    except Exception:
        return date.today()

def event_is_active(event, today):
    d = parse_event_date(event.get('date',''))
    return (today - d).days <= EVENT_WINDOW_DAYS

def merge_events(existing, new_events, today):
    existing_keys = set()
    for e in existing:
        k = (e.get('date',''), e.get('pts',0), e.get('desc','')[:40])
        existing_keys.add(k)
    merged = [e for e in existing if event_is_active(e, today)]
    for e in new_events:
        k = (e.get('date',''), e.get('pts',0), e.get('desc','')[:40])
        if k not in existing_keys and event_is_active(e, today):
            merged.append(e)
            existing_keys.add(k)
    return merged


def migrate_if_needed(m, today):
    """
    Convert municipality from old ev_pts format to new dated events format.
    Old: ev_pts=[10,5,4], ev_count=3, adjusted_score=21
    New: events=[{date, pts, desc}, ...]
    Uses synthetic date of today-14 to keep events active for one more cycle
    while the AI confirms with proper occurrence dates.
    """
    if 'events' in m:
        return m  # Already in new format
    ev_pts = m.get('ev_pts', [])
    if not ev_pts:
        m['events'] = []
        return m
    # Synthetic date: 14 days ago keeps them active for 16 more days
    syn_date = str(today - timedelta(days=14))
    m['events'] = [
        {"date": syn_date, "pts": p, "desc": "Evento historico (migracion de formato)"}
        for p in ev_pts
    ]
    if m.get('auto_red') and not m.get('auto_red_date'):
        m['auto_red_date'] = syn_date
    return m

def recalculate(m, today):
    dept = m.get('d','').lower()
    name = m.get('n','').lower()
    amazon = ['leticia','leguizamo']
    if m.get('lat', 0) < -0.5:
        if not any(a in name for a in amazon):
            m['lat'] = abs(m['lat'])

    all_events    = m.get('events', [])
    active_events = [e for e in all_events if event_is_active(e, today)]
    m['events']   = active_events

    auto_red      = m.get('auto_red', False)
    auto_red_date = m.get('auto_red_date', '')
    if auto_red and auto_red_date:
        if not event_is_active({'date': auto_red_date}, today):
            auto_red = False
            m['auto_red'] = False
            m['auto_red_why'] = ''
            m['auto_red_date'] = ''

    if auto_red:
        m['r'] = 'red'; m['sz'] = 22
        m['sc'] = f"ROJO AUTOMATICO: {m.get('auto_red_why','')}"
        m['adjusted_score'] = 99
        m['ev_count'] = len(active_events)
        return m

    sev   = sum(e.get('pts',0) for e in active_events)
    count = len(active_events)
    mult  = get_multiplier(count)
    adj   = round(sev * mult, 1)
    m['adjusted_score'] = adj
    m['ev_count']       = count

    if active_events:
        oldest = min(parse_event_date(e.get('date','')) for e in active_events)
        m['next_expiry'] = str(oldest + timedelta(days=EVENT_WINDOW_DAYS))
    else:
        m['next_expiry'] = ''

    zona = score_to_zone(adj)
    m['sz'] = score_to_size(adj)
    m['sc'] = f"Gravedad:{sev}pts x{mult} = {adj} | {ZONE_ES.get(zona,'')}"

    if zona == 'orange':
        if not any(rd in dept for rd in RESTRICTED_DEPTS) or any(ec in name for ec in EXCLUDED_CITIES):
            zona = 'yellow'
            m['sc'] = f"Gravedad:{sev}pts x{mult} = {adj} | AMARILLO (depto. no restringido)"

    # Track last event date (never decreases)
    if active_events:
        latest = max(parse_event_date(e.get('date','')) for e in active_events)
        current_last = parse_event_date(m.get('last_event_date','2000-01-01'))
        if latest > current_last:
            m['last_event_date'] = str(latest)

    # Focus dept white zone: score=0 but check 60-day memory.
    # Empty last_event_date means no events ever recorded -> WHITE immediately.
    # parse_event_date('') falls back to today (bug) so we check explicitly first.
    if zona == 'green' and m.get('focus_dept'):
        last_ev_str = m.get('last_event_date', '')
        if not last_ev_str:
            # No events ever recorded for this municipality -> WHITE
            zona = 'white'
            m['sc'] = "Sin eventos verificados en 60+ dias | BLANCO"
        else:
            last_ev = parse_event_date(last_ev_str)
            if (today - last_ev).days > WHITE_WINDOW_DAYS:
                zona = 'white'
                m['sc'] = "Sin eventos verificados en 60+ dias | BLANCO"

    m['r'] = zona
    return m

# GREEN baseline
GREEN_BASELINE = [
    {"n":"Bogota",               "d":"Cundinamarca (D.C.)", "lat":4.7110, "lng":-74.0721},
    {"n":"Barranquilla",         "d":"Atlantico",           "lat":10.9685,"lng":-74.7813},
    {"n":"Cartagena",            "d":"Bolivar",             "lat":10.3910,"lng":-75.4794},
    {"n":"Santa Marta",          "d":"Magdalena",           "lat":11.2408,"lng":-74.2110},
    {"n":"Bucaramanga",          "d":"Santander",           "lat":7.1254, "lng":-73.1198},
    {"n":"Armenia",              "d":"Quindio",             "lat":4.5339, "lng":-75.6811},
    {"n":"Pereira",              "d":"Risaralda",           "lat":4.8133, "lng":-75.6961},
    {"n":"Manizales",            "d":"Caldas",              "lat":5.0689, "lng":-75.5174},
    {"n":"Tunja",                "d":"Boyaca",              "lat":5.5353, "lng":-73.3678},
    {"n":"Sincelejo",            "d":"Sucre",               "lat":9.3047, "lng":-75.3978},
    {"n":"Leticia",              "d":"Amazonas",            "lat":-4.2133,"lng":-69.9400},
    {"n":"Mitu",                 "d":"Vaupes",              "lat":1.1985, "lng":-70.1734},
    {"n":"Inirida",              "d":"Guainia",             "lat":3.8653, "lng":-67.9239},
    {"n":"Puerto Carreno",       "d":"Vichada",             "lat":6.1891, "lng":-67.4839},
    {"n":"Yopal",                "d":"Casanare",            "lat":5.3378, "lng":-72.3959},
    {"n":"Villavicencio",        "d":"Meta",                "lat":4.1420, "lng":-73.6267},
    {"n":"Neiva",                "d":"Huila",               "lat":2.9273, "lng":-75.2819},
    {"n":"Popayan",              "d":"Cauca",               "lat":2.4419, "lng":-76.6072},
    {"n":"Pasto",                "d":"Narino",              "lat":1.2136, "lng":-77.2811},
    {"n":"Cali",                 "d":"Valle del Cauca",     "lat":3.4516, "lng":-76.5320},
    {"n":"Medellin",             "d":"Antioquia",           "lat":6.2442, "lng":-75.5812},
    {"n":"Ibague",               "d":"Tolima",              "lat":4.4389, "lng":-75.2322},
    {"n":"Valledupar",           "d":"Cesar",               "lat":10.4772,"lng":-73.2503},
    {"n":"Monteria",             "d":"Cordoba",             "lat":8.7575, "lng":-75.8933},
    {"n":"Riohacha",             "d":"La Guajira",          "lat":11.5381,"lng":-72.9067},
    {"n":"Florencia",            "d":"Caqueta",             "lat":1.6167, "lng":-75.6167},
    {"n":"Mocoa",                "d":"Putumayo",            "lat":1.1519, "lng":-76.6497},
    {"n":"San Jose del Guaviare","d":"Guaviare",            "lat":2.5683, "lng":-72.6408},
    {"n":"Arauca",               "d":"Arauca",              "lat":7.0842, "lng":-70.7553},
    {"n":"Palmira",              "d":"Valle del Cauca",     "lat":3.5394, "lng":-76.2983},
]

def make_green(m, today):
    return {
        "n":m["n"],"d":m["d"],"lat":m["lat"],"lng":m["lng"],
        "r":"green","sz":10,"adjusted_score":0,"ev_count":0,
        "events":[],"sc":"Puntaje ajustado: 0 | VERDE",
        "i":"Sin incidentes de conflicto armado verificados en los ultimos 30 dias.",
        "a":"N/A - monitoreo rutinario",
        "auto_red":False,"auto_red_why":"","auto_red_date":"","next_expiry":"",
        "last_event_date":""
    }

def make_focus_muni(m, today):
    """Seed a focus-department municipality (Cauca/Valle/Narino) as WHITE."""
    return {
        "n":m["n"],"d":m["d"],"lat":m["lat"],"lng":m["lng"],
        "r":"white","sz":8,"adjusted_score":0,"ev_count":0,
        "events":[],"sc":"Sin eventos verificados en 60+ dias | BLANCO",
        "i":"Sin incidentes verificados en los ultimos 60 dias. Zona de presencia historica de grupos armados.",
        "a":m.get("a","Presencia historica de grupos armados ilegales"),
        "auto_red":False,"auto_red_why":"","auto_red_date":"","next_expiry":"",
        "last_event_date":"","focus_dept":True
    }

# All municipalities in the three focus departments with known criminal structures
FOCUS_MUNICIPALITIES = [
  # ── CAUCA ──────────────────────────────────────────────────────────
  {"n":"Caloto",              "d":"Cauca","lat":3.0236,"lng":-76.4208,"a":"FARC-EMC Frente Dagoberto Ramos"},
  {"n":"Toribio",             "d":"Cauca","lat":3.0000,"lng":-76.0900,"a":"FARC-EMC Frente Dagoberto Ramos — resguardos Nasa"},
  {"n":"Miranda",             "d":"Cauca","lat":3.2453,"lng":-76.2267,"a":"FARC-EMC Frente Dagoberto Ramos"},
  {"n":"Corinto",             "d":"Cauca","lat":3.1783,"lng":-76.2556,"a":"FARC-EMC Frente Dagoberto Ramos"},
  {"n":"Jambalo",             "d":"Cauca","lat":2.8700,"lng":-76.1300,"a":"FARC-EMC Frente Dagoberto Ramos — resguardo Nasa"},
  {"n":"Guachene",            "d":"Cauca","lat":3.0869,"lng":-76.4333,"a":"FARC-EMC Frente Dagoberto Ramos"},
  {"n":"Puerto Tejada",       "d":"Cauca","lat":3.2311,"lng":-76.4153,"a":"FARC-EMC / redes criminales urbanas"},
  {"n":"Villa Rica",          "d":"Cauca","lat":3.1700,"lng":-76.4600,"a":"FARC-EMC / redes criminales"},
  {"n":"Padilla",             "d":"Cauca","lat":3.1900,"lng":-76.3100,"a":"Redes criminales — corredor Norte Cauca"},
  {"n":"Santander de Quilichao","d":"Cauca","lat":3.0072,"lng":-76.4847,"a":"FARC-EMC multiples frentes — nodo urbano Norte Cauca"},
  {"n":"Cajibio",             "d":"Cauca","lat":2.5500,"lng":-76.5578,"a":"FARC-EMC Frente Jaime Martinez / alias Ivan Mordisco"},
  {"n":"Morales",             "d":"Cauca","lat":2.7544,"lng":-76.6206,"a":"FARC-EMC Frente Jaime Martinez"},
  {"n":"El Tambo",            "d":"Cauca","lat":2.4578,"lng":-76.8003,"a":"FARC-EMC Frente Jaime Martinez — corredor Panamericana"},
  {"n":"Buenos Aires",        "d":"Cauca","lat":3.0167,"lng":-76.6333,"a":"FARC-EMC Frente Jaime Martinez"},
  {"n":"Suarez",              "d":"Cauca","lat":2.9503,"lng":-76.6611,"a":"FARC-EMC Frente Jaime Martinez"},
  {"n":"Piendamo",            "d":"Cauca","lat":2.6700,"lng":-76.5300,"a":"FARC-EMC Frente Jaime Martinez"},
  {"n":"Timbio",              "d":"Cauca","lat":2.3461,"lng":-76.6822,"a":"FARC-EMC Frente Jaime Martinez"},
  {"n":"Popayan",             "d":"Cauca","lat":2.4419,"lng":-76.6072,"a":"FARC-EMC zona de influencia — ciudad capital excluida de restriccion extranjeros"},
  {"n":"Silvia",              "d":"Cauca","lat":2.6156,"lng":-76.3817,"a":"FARC-EMC Frente Dagoberto Ramos — territorio indigena Nasa/Misak"},
  {"n":"Caldono",             "d":"Cauca","lat":2.7967,"lng":-76.4883,"a":"FARC-EMC Frente Dagoberto Ramos"},
  {"n":"Patia",               "d":"Cauca","lat":2.0697,"lng":-77.0731,"a":"FARC-EMC Frente Jaime Martinez — El Bordo"},
  {"n":"Mercaderes",          "d":"Cauca","lat":1.7944,"lng":-77.1878,"a":"FARC-EMC Frente 6"},
  {"n":"Balboa",              "d":"Cauca","lat":2.0536,"lng":-77.2178,"a":"FARC-EMC Frente 6"},
  {"n":"La Sierra",           "d":"Cauca","lat":1.9100,"lng":-76.9500,"a":"FARC-EMC Frente 6"},
  {"n":"Rosas",               "d":"Cauca","lat":2.2600,"lng":-76.7200,"a":"FARC-EMC Frente Jaime Martinez / Frente 6"},
  {"n":"Argelia",             "d":"Cauca","lat":1.8800,"lng":-77.2300,"a":"FARC-EMC Frente 6 / Segunda Marquetalia"},
  {"n":"Almaguer",            "d":"Cauca","lat":1.9167,"lng":-76.8333,"a":"FARC-EMC Frente 6"},
  {"n":"Bolivar (Cauca)",     "d":"Cauca","lat":1.8611,"lng":-76.9611,"a":"FARC-EMC Frente 6"},
  {"n":"San Sebastian",       "d":"Cauca","lat":1.7800,"lng":-76.8100,"a":"FARC-EMC Frente 6"},
  {"n":"Santa Rosa (Cauca)",  "d":"Cauca","lat":1.6700,"lng":-76.7900,"a":"FARC-EMC Frente 6"},
  {"n":"Sotara",              "d":"Cauca","lat":2.1700,"lng":-76.6300,"a":"FARC-EMC Frente Jaime Martinez / Frente 6"},
  {"n":"Sucre (Cauca)",       "d":"Cauca","lat":1.9600,"lng":-76.9700,"a":"FARC-EMC Frente 6"},
  {"n":"La Vega",             "d":"Cauca","lat":1.9700,"lng":-76.7700,"a":"FARC-EMC Frente 6"},
  {"n":"Purace",              "d":"Cauca","lat":2.2600,"lng":-76.4900,"a":"FARC-EMC (influencia)"},
  {"n":"Totoro",              "d":"Cauca","lat":2.5000,"lng":-76.4000,"a":"FARC-EMC Frente Dagoberto Ramos"},
  {"n":"Inza",                "d":"Cauca","lat":2.5700,"lng":-76.0700,"a":"FARC-EMC Frente Dagoberto Ramos — resguardo Nasa"},
  {"n":"Paez",                "d":"Cauca","lat":2.9000,"lng":-75.8800,"a":"FARC-EMC / ELN — territorio indigena Nasa — Tierradentro"},
  {"n":"Lopez de Micay",      "d":"Cauca","lat":3.1889,"lng":-77.2444,"a":"FARC-EMC Frente Jaime Martinez — Canion del Micay"},
  {"n":"Timbiqui",            "d":"Cauca","lat":2.7700,"lng":-77.6800,"a":"FARC-EMC Segunda Marquetalia — costa Pacifica"},
  {"n":"Guapi",               "d":"Cauca","lat":2.5700,"lng":-77.8900,"a":"FARC-EMC Segunda Marquetalia — costa Pacifica"},
  {"n":"Piamonte",            "d":"Cauca","lat":0.8500,"lng":-76.5800,"a":"FARC-EMC Frente 48 — frontera Putumayo"},
  {"n":"Florencia (Cauca)",   "d":"Cauca","lat":1.6067,"lng":-76.6133,"a":"FARC-EMC Frente 6"},
  # ── VALLE DEL CAUCA ───────────────────────────────────────────────
  {"n":"Buenaventura",        "d":"Valle del Cauca","lat":3.8831,"lng":-77.0311,"a":"FARC-EMC / AGC Clan del Golfo / redes criminales — principal puerto del Pacifico"},
  {"n":"Dagua",               "d":"Valle del Cauca","lat":3.6594,"lng":-76.6944,"a":"FARC-EMC Frente Jaime Martinez — corredor Buenaventura"},
  {"n":"Jamundi",             "d":"Valle del Cauca","lat":3.2578,"lng":-76.5369,"a":"FARC-EMC Frente Jaime Martinez"},
  {"n":"Pradera",             "d":"Valle del Cauca","lat":3.4239,"lng":-76.2406,"a":"FARC-EMC Frente Andes / Segunda Marquetalia"},
  {"n":"Florida (Valle)",     "d":"Valle del Cauca","lat":3.3272,"lng":-76.2311,"a":"FARC-EMC Frente Andes / Segunda Marquetalia"},
  {"n":"Cali",                "d":"Valle del Cauca","lat":3.4516,"lng":-76.5320,"a":"FARC-EMC influencia — ciudad excluida de restriccion extranjeros"},
  {"n":"Palmira",             "d":"Valle del Cauca","lat":3.5394,"lng":-76.2983,"a":"FARC-EMC influencia — ciudad excluida de restriccion extranjeros"},
  {"n":"Yumbo",               "d":"Valle del Cauca","lat":3.5836,"lng":-76.4942,"a":"FARC-EMC / redes criminales"},
  {"n":"Candelaria",          "d":"Valle del Cauca","lat":3.4089,"lng":-76.3461,"a":"Redes criminales / FARC-EMC"},
  {"n":"El Cerrito",          "d":"Valle del Cauca","lat":3.6794,"lng":-76.1450,"a":"Redes criminales"},
  {"n":"Ginebra",             "d":"Valle del Cauca","lat":3.7311,"lng":-76.0644,"a":"Redes criminales"},
  {"n":"Guacari",             "d":"Valle del Cauca","lat":3.7789,"lng":-76.3275,"a":"Redes criminales"},
  {"n":"Buga",                "d":"Valle del Cauca","lat":3.9011,"lng":-76.2964,"a":"Redes criminales — influencia FARC-EMC"},
  {"n":"Tulua",               "d":"Valle del Cauca","lat":4.0867,"lng":-76.1972,"a":"Redes criminales / AGC Clan del Golfo"},
  {"n":"Cartago",             "d":"Valle del Cauca","lat":4.7467,"lng":-75.9122,"a":"AGC Clan del Golfo / redes criminales — Norte del Valle"},
  {"n":"Sevilla",             "d":"Valle del Cauca","lat":4.2681,"lng":-75.9347,"a":"Redes criminales"},
  {"n":"Caicedonia",          "d":"Valle del Cauca","lat":4.3339,"lng":-75.8331,"a":"Redes criminales"},
  {"n":"Zarzal",              "d":"Valle del Cauca","lat":4.3942,"lng":-76.0708,"a":"AGC / redes criminales"},
  {"n":"La Union (Valle)",    "d":"Valle del Cauca","lat":4.5300,"lng":-76.1000,"a":"Redes criminales"},
  {"n":"Roldanillo",          "d":"Valle del Cauca","lat":4.4150,"lng":-76.1569,"a":"AGC Clan del Golfo / redes criminales"},
  {"n":"Toro",                "d":"Valle del Cauca","lat":4.6020,"lng":-76.0820,"a":"AGC Clan del Golfo / FARC-EMC Frente Jaime Martinez"},
  {"n":"Versalles",           "d":"Valle del Cauca","lat":4.5700,"lng":-76.2400,"a":"FARC-EMC / redes criminales"},
  {"n":"La Cumbre",           "d":"Valle del Cauca","lat":3.6500,"lng":-76.5700,"a":"FARC-EMC Frente Jaime Martinez — corredor montanas"},
  {"n":"Restrepo",            "d":"Valle del Cauca","lat":3.8214,"lng":-76.5228,"a":"Redes criminales"},
  {"n":"Calima",              "d":"Valle del Cauca","lat":3.9300,"lng":-76.4800,"a":"FARC-EMC / redes criminales"},
  {"n":"Riofrio",             "d":"Valle del Cauca","lat":4.1200,"lng":-76.3600,"a":"FARC-EMC / redes criminales"},
  {"n":"Trujillo",            "d":"Valle del Cauca","lat":4.2300,"lng":-76.3200,"a":"FARC-EMC / redes criminales"},
  {"n":"Bolivar (Valle)",     "d":"Valle del Cauca","lat":4.3700,"lng":-76.2300,"a":"FARC-EMC Segunda Marquetalia"},
  {"n":"El Dovio",            "d":"Valle del Cauca","lat":4.5200,"lng":-76.2900,"a":"FARC-EMC / redes criminales"},
  {"n":"Ansermanuevo",        "d":"Valle del Cauca","lat":4.7939,"lng":-76.0311,"a":"AGC Clan del Golfo / redes criminales"},
  {"n":"El Aguila",           "d":"Valle del Cauca","lat":4.9200,"lng":-76.0600,"a":"AGC Clan del Golfo / redes criminales"},
  {"n":"El Cairo",            "d":"Valle del Cauca","lat":4.9700,"lng":-76.2400,"a":"FARC-EMC / AGC — frontera Choco"},
  {"n":"Alcala",              "d":"Valle del Cauca","lat":4.6703,"lng":-75.7808,"a":"Redes criminales"},
  {"n":"Obando",              "d":"Valle del Cauca","lat":4.5767,"lng":-75.9858,"a":"AGC Clan del Golfo"},
  {"n":"La Victoria",         "d":"Valle del Cauca","lat":4.5225,"lng":-75.8894,"a":"Redes criminales"},
  {"n":"Ulloa",               "d":"Valle del Cauca","lat":4.7150,"lng":-75.7700,"a":"Redes criminales"},
  # ── NARINO ────────────────────────────────────────────────────────
  {"n":"Tumaco",              "d":"Narino","lat":1.8081,"lng":-78.7618,"a":"FARC-EMC Columna Daniel Aldana / Segunda Marquetalia — epicentro Pacifico Sur"},
  {"n":"Barbacoas",           "d":"Narino","lat":1.6728,"lng":-78.1403,"a":"FARC-EMC Columna Daniel Aldana — corredor fluvial"},
  {"n":"Roberto Payan",       "d":"Narino","lat":1.8539,"lng":-77.9472,"a":"FARC-EMC Columna Daniel Aldana / Segunda Marquetalia"},
  {"n":"Magui Payan",         "d":"Narino","lat":1.8397,"lng":-77.5883,"a":"FARC-EMC Columna Daniel Aldana"},
  {"n":"Olaya Herrera",       "d":"Narino","lat":2.3181,"lng":-78.1567,"a":"FARC-EMC / Segunda Marquetalia — costa Pacifica"},
  {"n":"El Charco",           "d":"Narino","lat":2.4806,"lng":-77.9889,"a":"FARC-EMC / Segunda Marquetalia"},
  {"n":"La Tola",             "d":"Narino","lat":2.9822,"lng":-78.2258,"a":"FARC-EMC / Segunda Marquetalia — costa norte Narino"},
  {"n":"Iscuande",            "d":"Narino","lat":2.4444,"lng":-77.9786,"a":"FARC-EMC / Segunda Marquetalia"},
  {"n":"Mosquera (Narino)",   "d":"Narino","lat":2.5100,"lng":-78.4300,"a":"FARC-EMC Segunda Marquetalia"},
  {"n":"Francisco Pizarro",   "d":"Narino","lat":1.1750,"lng":-78.7300,"a":"FARC-EMC Columna Daniel Aldana — costa norte"},
  {"n":"Ipiales",             "d":"Narino","lat":0.8294,"lng":-77.6441,"a":"FARC-EMC / Los Lobos Ecuador — zona fronteriza Ecuador"},
  {"n":"Cumbal",              "d":"Narino","lat":0.9139,"lng":-77.7892,"a":"FARC-EMC Frente 29 — resguardos indigenas frontera Ecuador"},
  {"n":"Cuaspud",             "d":"Narino","lat":0.9300,"lng":-77.6900,"a":"FARC-EMC — frontera Ecuador"},
  {"n":"Aldana",              "d":"Narino","lat":0.9000,"lng":-77.5900,"a":"FARC-EMC — frontera Ecuador"},
  {"n":"Puerres",             "d":"Narino","lat":0.9333,"lng":-77.5000,"a":"FARC-EMC — zona fronteriza"},
  {"n":"Ricaurte",            "d":"Narino","lat":1.2119,"lng":-77.9844,"a":"FARC-EMC Frente 29 — corredor fronterizo Ecuador"},
  {"n":"Mallama",             "d":"Narino","lat":1.0300,"lng":-77.9700,"a":"FARC-EMC Frente 29"},
  {"n":"Samaniego",           "d":"Narino","lat":1.3444,"lng":-77.5944,"a":"FARC-EMC Columna Comuneros del Sur — epicentro Corregimiento La Albania"},
  {"n":"Los Andes (Narino)",  "d":"Narino","lat":1.4900,"lng":-77.5300,"a":"FARC-EMC Columna Comuneros del Sur"},
  {"n":"Linares",             "d":"Narino","lat":1.3700,"lng":-77.4600,"a":"FARC-EMC Columna Comuneros del Sur"},
  {"n":"Ancuya",              "d":"Narino","lat":1.4200,"lng":-77.4200,"a":"FARC-EMC Columna Comuneros del Sur"},
  {"n":"El Rosario",          "d":"Narino","lat":1.5900,"lng":-77.4800,"a":"FARC-EMC Columna Comuneros del Sur"},
  {"n":"Policarpa",           "d":"Narino","lat":1.7500,"lng":-77.4500,"a":"FARC-EMC Columna Comuneros del Sur"},
  {"n":"Cumbitara",           "d":"Narino","lat":1.6600,"lng":-77.5700,"a":"FARC-EMC Columna Comuneros del Sur"},
  {"n":"La Llanada",          "d":"Narino","lat":1.5100,"lng":-77.5700,"a":"FARC-EMC"},
  {"n":"El Tablon de Gomez",  "d":"Narino","lat":1.4200,"lng":-76.8900,"a":"FARC-EMC"},
  {"n":"Leiva",               "d":"Narino","lat":1.5781,"lng":-77.3494,"a":"FARC-EMC Columna Comuneros del Sur"},
  {"n":"Tuquerres",           "d":"Narino","lat":1.0850,"lng":-77.6200,"a":"FARC-EMC Frente 29 — zona andina"},
  {"n":"Guachucal",           "d":"Narino","lat":0.9800,"lng":-77.6900,"a":"FARC-EMC — resguardos indigenas frontera Ecuador"},
  {"n":"Guaitarilla",         "d":"Narino","lat":1.1400,"lng":-77.6200,"a":"FARC-EMC"},
  {"n":"Imues",               "d":"Narino","lat":1.1700,"lng":-77.6500,"a":"FARC-EMC"},
  {"n":"Ospina",              "d":"Narino","lat":1.1200,"lng":-77.6800,"a":"FARC-EMC"},
  {"n":"Iles",                "d":"Narino","lat":0.9900,"lng":-77.5200,"a":"FARC-EMC"},
  {"n":"Sapuyes",             "d":"Narino","lat":1.0000,"lng":-77.6400,"a":"FARC-EMC"},
  {"n":"Potosi",              "d":"Narino","lat":0.8300,"lng":-77.5400,"a":"FARC-EMC — frontera Ecuador"},
  {"n":"Pasto",               "d":"Narino","lat":1.2136,"lng":-77.2811,"a":"FARC-EMC zona de influencia — ciudad capital excluida de restriccion extranjeros"},
  {"n":"La Florida",          "d":"Narino","lat":1.3000,"lng":-77.4200,"a":"FARC-EMC (influencia)"},
  {"n":"Sandona",             "d":"Narino","lat":1.2900,"lng":-77.4700,"a":"FARC-EMC (influencia)"},
  {"n":"Consaca",             "d":"Narino","lat":1.2500,"lng":-77.4600,"a":"FARC-EMC (zona de influencia)"},
  {"n":"Chachagui",           "d":"Narino","lat":1.3600,"lng":-77.2800,"a":"Redes criminales (influencia)"},
  {"n":"Buesaco",             "d":"Narino","lat":1.3800,"lng":-77.1600,"a":"FARC-EMC / Segunda Marquetalia"},
  {"n":"San Bernardo (Nar.)", "d":"Narino","lat":1.5100,"lng":-76.9700,"a":"FARC-EMC Segunda Marquetalia"},
  {"n":"La Cruz",             "d":"Narino","lat":1.5900,"lng":-76.9700,"a":"FARC-EMC Segunda Marquetalia"},
  {"n":"Alban",               "d":"Narino","lat":1.4500,"lng":-77.4000,"a":"FARC-EMC (influencia)"},
  {"n":"Taminango",           "d":"Narino","lat":1.5700,"lng":-77.2700,"a":"FARC-EMC"},
  {"n":"Arboleda",            "d":"Narino","lat":1.6500,"lng":-77.0900,"a":"FARC-EMC / Segunda Marquetalia"},
  {"n":"San Pablo (Narino)",  "d":"Narino","lat":1.6900,"lng":-76.9600,"a":"FARC-EMC Segunda Marquetalia"},
  {"n":"Colon (Narino)",      "d":"Narino","lat":1.4800,"lng":-76.9500,"a":"FARC-EMC Segunda Marquetalia"},
  {"n":"Providencia (Nar.)",  "d":"Narino","lat":1.5400,"lng":-76.9000,"a":"FARC-EMC"},
  {"n":"Belen (Narino)",      "d":"Narino","lat":1.5900,"lng":-76.8500,"a":"FARC-EMC"},
]

# Email
def build_email(fecha, before, after, counts):
    escalated=[]; deescalated=[]; nuevos=[]; auto_rojos=[]
    order={'green':0,'yellow':1,'orange':2,'red':3}
    for name,m in after.items():
        nz=m.get('r','green')
        if m.get('auto_red'):
            auto_rojos.append((name,m.get('d',''),m.get('auto_red_why',''),m.get('auto_red_date','')))
        if name not in before:
            nuevos.append((name,m.get('d',''),nz,m.get('i','')))
        else:
            oz=before[name].get('r','green')
            if order.get(nz,0)>order.get(oz,0):
                escalated.append((name,m.get('d',''),oz,nz,m.get('i','')))
            elif order.get(nz,0)<order.get(oz,0):
                deescalated.append((name,m.get('d',''),oz,nz,m.get('next_expiry','')))
    escalated.sort(key=lambda x:-order.get(x[3],0))

    def badge(z):
        c=ZONE_COLOR.get(z,'#666'); b=ZONE_BG.get(z,'#f9f9f9')
        return (f'<span style="background:{b};color:{c};border:1px solid {c};'
                f'padding:2px 8px;border-radius:3px;font-size:11px;font-weight:700;'
                f'font-family:monospace">{ZONE_ES.get(z,z.upper())}</span>')

    def arrow(o,n): return f'{badge(o)} &nbsp;&#8594;&nbsp; {badge(n)}'

    re_rows="".join(f'<tr><td style="padding:6px 10px;border-bottom:1px solid #e5e7eb"><strong>{n}</strong><br><small style="color:#6b7280">{d}</small></td><td style="padding:6px 10px;border-bottom:1px solid #e5e7eb">{arrow(o,nw)}</td><td style="padding:6px 10px;border-bottom:1px solid #e5e7eb;font-size:12px">{i}</td></tr>' for n,d,o,nw,i in escalated)
    de_rows="".join(f'<tr><td style="padding:6px 10px;border-bottom:1px solid #e5e7eb"><strong>{n}</strong><br><small style="color:#6b7280">{d}</small></td><td style="padding:6px 10px;border-bottom:1px solid #e5e7eb">{arrow(o,nw)}</td><td style="padding:6px 10px;border-bottom:1px solid #e5e7eb;font-size:11px;color:#6b7280">{"Eventos expiran: "+exp if exp else ""}</td></tr>' for n,d,o,nw,exp in deescalated)
    nw_rows="".join(f'<tr><td style="padding:6px 10px;border-bottom:1px solid #e5e7eb"><strong>{n}</strong><br><small style="color:#6b7280">{d}</small></td><td style="padding:6px 10px;border-bottom:1px solid #e5e7eb">{badge(z)}</td><td style="padding:6px 10px;border-bottom:1px solid #e5e7eb;font-size:12px">{i}</td></tr>' for n,d,z,i in nuevos)

    body=""
    if auto_rojos:
        items="".join(f'<li style="margin-bottom:4px"><strong>{n}</strong> ({d}): {w}<br><small style="color:#9ca3af">Fecha del evento: {dt}</small></li>' for n,d,w,dt in auto_rojos)
        body+=(f'<div style="background:#fef2f2;border:1px solid #dc2626;border-radius:6px;padding:14px 16px;margin-bottom:20px"><h3 style="margin:0 0 8px;color:#dc2626;font-size:14px">Anulaciones Automaticas a ROJO ({len(auto_rojos)})</h3><ul style="margin:0;padding-left:18px;font-size:13px">{items}</ul></div>')

    def tbl(title,color,hdrs,rows):
        ths="".join(f'<th style="padding:8px 10px;text-align:left;font-size:11px;color:#6b7280;border-bottom:2px solid #e5e7eb">{h}</th>' for h in hdrs)
        return (f'<h3 style="color:{color};font-size:14px;margin-top:24px">{title}</h3>'
                f'<table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;font-size:13px;margin-bottom:20px"><thead><tr style="background:#f9fafb">{ths}</tr></thead><tbody>{rows}</tbody></table>')

    if escalated:  body+=tbl(f'Escalaciones ({len(escalated)})','#dc2626',['MUNICIPIO','CAMBIO','INCIDENTE'],re_rows)
    if deescalated:body+=tbl(f'Mejoras ({len(deescalated)})','#15803d',['MUNICIPIO','CAMBIO','EVENTOS EXPIRAN'],de_rows)
    if nuevos:     body+=tbl(f'Municipios Nuevos ({len(nuevos)})','#374151',['MUNICIPIO','ZONA','INCIDENTE'],nw_rows)
    if not body:   body='<p style="color:#6b7280;font-style:italic">No se detectaron cambios de zona esta semana.</p>'

    r=counts.get('red',0);o=counts.get('orange',0);y=counts.get('yellow',0);g=counts.get('green',0)
    html=(f'<!DOCTYPE html><html lang="es"><head><meta charset="UTF-8"></head><body style="margin:0;padding:0;background:#f3f4f6;font-family:Arial,sans-serif"><table width="100%" cellpadding="0" cellspacing="0" style="background:#f3f4f6;padding:24px 0"><tr><td align="center"><table width="620" cellpadding="0" cellspacing="0" style="background:#fff;border-radius:8px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,.1)"><tr><td style="background:#0a1223;padding:24px 28px"><p style="margin:0;font-size:11px;color:#38bdf8;font-family:monospace;letter-spacing:.1em;text-transform:uppercase">CONVOY OF HOPE - COLOMBIA</p><h1 style="margin:6px 0 0;color:#f0f6ff;font-size:20px;font-weight:700">Actualizacion Semanal del Mapa de Riesgo</h1><p style="margin:4px 0 0;color:#8faabb;font-size:13px">{fecha} &nbsp;·&nbsp; CONFIDENCIAL</p></td></tr><tr><td style="background:#080f1e;padding:14px 28px"><table width="100%" cellpadding="0" cellspacing="0"><tr><td align="center" style="color:#f87171;font-family:monospace"><span style="font-size:26px;font-weight:700">{r}</span><br><span style="font-size:10px;letter-spacing:.1em">ROJO</span></td><td align="center" style="color:#fb923c;font-family:monospace"><span style="font-size:26px;font-weight:700">{o}</span><br><span style="font-size:10px;letter-spacing:.1em">NARANJA</span></td><td align="center" style="color:#fde047;font-family:monospace"><span style="font-size:26px;font-weight:700">{y}</span><br><span style="font-size:10px;letter-spacing:.1em">AMARILLO</span></td><td align="center" style="color:#4ade80;font-family:monospace"><span style="font-size:26px;font-weight:700">{g}</span><br><span style="font-size:10px;letter-spacing:.1em">VERDE</span></td></tr></table></td></tr><tr><td style="padding:24px 28px">{body}<div style="text-align:center;margin-top:28px"><a href="{MAP_URL}" style="background:#0a1223;color:#38bdf8;text-decoration:none;padding:12px 28px;border-radius:4px;font-family:monospace;font-size:12px;letter-spacing:.08em;border:1px solid #38bdf8">VER MAPA COMPLETO</a></div><p style="margin-top:28px;font-size:11px;color:#9ca3af;border-top:1px solid #e5e7eb;padding-top:16px">Las zonas se mantienen mientras los eventos permanezcan dentro de la ventana de 30 dias desde su fecha de ocurrencia. Solo cambian cuando los eventos expiran o nuevos eventos modifican la puntuacion.<br><br>CONFIDENCIAL - Solo Personal Autorizado CoH Colombia</p></td></tr></table></td></tr></table></body></html>')

    lines=[f"CoH Colombia - Actualizacion {fecha}","CONFIDENCIAL","",f"ROJO={r} | NARANJA={o} | AMARILLO={y} | VERDE={g}",""]
    if auto_rojos: lines+=[f"ROJOS AUTOMATICOS ({len(auto_rojos)}):"] +[f"  {n} ({d}): {w} | {dt}" for n,d,w,dt in auto_rojos]+[""]
    if escalated:  lines+=[f"ESCALACIONES ({len(escalated)}):"] +[f"  {n} ({d}): {ZONE_ES[o]}->{ZONE_ES[nw]}" for n,d,o,nw,i in escalated]+[""]
    if deescalated:lines+=[f"MEJORAS ({len(deescalated)}):"] +[f"  {n} ({d}): {ZONE_ES[o]}->{ZONE_ES[nw]} | expiran {exp}" for n,d,o,nw,exp in deescalated]+[""]
    if not escalated and not deescalated and not nuevos and not auto_rojos:
        lines+=["No se detectaron cambios de zona esta semana.",""]
    lines.append(f"Ver mapa: {MAP_URL}")
    return html,"\n".join(lines)

def send_email(fecha,before,after,counts):
    if not all([SG_KEY,EMAIL_FROM,EMAIL_TO]):
        print("Credenciales SendGrid no configuradas - omitiendo email"); return
    html_body,text_body=build_email(fecha,before,after,counts)
    recipients=[{"email":r.strip()} for r in EMAIL_TO.split(',')]
    payload=json.dumps({"personalizations":[{"to":recipients}],"from":{"email":EMAIL_FROM,"name":"CoH Colombia Risk Map"},"subject":f"CoH Colombia - Mapa de Riesgo Actualizado: {fecha}","content":[{"type":"text/plain","value":text_body},{"type":"text/html","value":html_body}]}).encode('utf-8')
    req=urllib.request.Request("https://api.sendgrid.com/v3/mail/send",data=payload,headers={"Authorization":f"Bearer {SG_KEY}","Content-Type":"application/json"},method="POST")
    try:
        with urllib.request.urlopen(req) as resp: print(f"Email enviado (status {resp.status})")
    except urllib.error.HTTPError as e: print(f"Error SendGrid {e.code}: {e.read().decode()}")

SYSTEM = """Eres un analista de seguridad aplicando la Rubrica de Puntuacion de Riesgo Municipal de Convoy of Hope Colombia (Seccion 3, Plan de Contingencia v3.0).

REGLA CRITICA DE FECHAS: Cada evento DEBE tener la fecha REAL de ocurrencia (YYYY-MM-DD). Esta fecha determina exactamente cuando el evento expira de la ventana de 30 dias. Si el evento ocurrio el 15 de abril, usa 2026-04-15, NO la fecha de hoy.

TODOS los textos SIEMPRE en ESPANOL.

PUNTOS DE GRAVEDAD POR EVENTO (cada evento por separado con su fecha individual):
  10 pts: Fatalidad civil por conflicto armado
  10 pts: Ataque directo a personal, sede o vehiculo de CoH
  10 pts: Secuestro, rapto o desaparicion forzada
  10 pts: Masacre (multiples victimas en un solo incidente)
   8 pts: Combate activo dentro de 10 km con muertos o heridos confirmados
   8 pts: Amenaza directa creible contra humanitarios o comunidad
   5 pts: Heridos civiles por conflicto o artefactos explosivos (UXO/minas)
   5 pts: Combate armado dentro de 30 km con danos a propiedad
   5 pts: Explosion, IED, AEI o mina antipersona reportada
   5 pts: Desplazamiento forzado o confinamiento de comunidad
   4 pts: Reten ilegal, bloqueo armado o extorsion (vacuna)
   4 pts: Amenaza contra lider social, socio o beneficiario
   2 pts: Movimiento o presencia armada dentro de 50 km
   2 pts: Danos a infraestructura o saqueo (no combate)
   2 pts: Paro armado, toque de queda o restriccion de acceso impuesta por grupos armados
   1 pt:  Tension comunitaria, protesta o violencia localizada
   1 pt:  Zona de cultivos ilicitos, via sin despejar, actividad sospechosa

MULTIPLICADOR DE FRECUENCIA (aplicado al total de gravedad):
  1 evento: x1.0 | 2: x1.25 | 3-4: x1.5 | 5-7: x2.0 | 8+: x2.5

ANULACION AUTOMATICA A ROJO (auto_red=true, auto_red_date=fecha exacta del evento):
  - Combate activo dentro de 10 km de area habitada con muertes
  - Masacre de 3 o mas personas en un solo incidente
  - Amenaza directa creible contra CoH o comunidades que atiende
  - Secuestro de personal, socio o beneficiario
  - Zona declarada restringida por el gobierno colombiano
  - Desplazamiento masivo forzado desde area de programa CoH
  - Colapso total de acceso: mas del 50% de rutas negadas por 7 dias

MUNICIPIOS PRIORITARIOS A CUBRIR (busca eventos especificamente en estos):
Cauca: Cajibio, Corinto, Caloto, Buenos Aires, Lopez de Micay, El Tambo, Morales, Suarez, Caldono, Miranda, Toribio, Silvia, Piendamo, Popayan, Santander de Quilichao, Mercaderes, Patia, Balboa
Valle del Cauca: Cali, Buenaventura, Dagua, Jamundi, Pradera, Florida, Tuluá, Cartago
Narino: Tumaco, Barbacoas, Olaya Herrera, Roberto Payan, El Charco, Pasto, Ipiales, Samaniego, Ricaurte, La Tola, Iscuande
Antioquia: Turbo, Apartado, Ituango, Caucasia, Tarazá, Valdivia, Urrao
Choco: Quibdo, Riosucio, Bojaya, Carmen del Darien
Norte de Santander: Tibu, San Calixto, El Tarra, El Carmen, Cucuta
Arauca: Arauca, Saravena, Tame, Fortul, Puerto Rondon
Putumayo: Puerto Asis, Orito, Valle del Guamuez, Puerto Caicedo, Mocoa
Caqueta: Florencia, San Vicente del Caguan, La Montanita, El Doncello
Huila: Neiva y municipios del sur
Meta, Guaviare, Vichada: conflictos fronterizos y FARC
Ciudades estables (sin eventos): Bogota, Barranquilla, Cartagena, Santa Marta, Bucaramanga, Armenia, Pereira, Manizales, Tunja

Devuelve SOLO JSON valido. Sin preambulo. Sin markdown. Comienza con { termina con }.
Entre 70 y 85 municipios. Campo i maximo 90 caracteres.

{
  "fecha": "DD Mmm YYYY",
  "municipios": [
    {
      "n": "Nombre del municipio",
      "d": "Departamento",
      "lat": 0.0,
      "lng": 0.0,
      "events": [
        {"date": "YYYY-MM-DD", "pts": 10, "desc": "descripcion del evento en espanol max 80 chars"}
      ],
      "auto_red": false,
      "auto_red_why": "",
      "auto_red_date": "",
      "i": "resumen de la situacion del municipio, maximo 90 caracteres",
      "a": "grupos armados activos en el municipio"
    }
  ]
}

COORDENADAS CRITICAS: Colombia esta principalmente al NORTE del ecuador — latitudes POSITIVAS.
Cauca entre 1.5N y 4N. Valle del Cauca entre 3N y 5N. Narino entre 0.5N y 2.5N. Antioquia entre 5N y 8N.
UNICAMENTE Leticia (-4.2) y Puerto Leguizamo (-0.19) tienen latitudes negativas."""

def write_html(html, by_name, fecha_str=''):
    merged = list(by_name.values())
    inner  = json.dumps(merged, ensure_ascii=False)[1:-1]
    html   = re.sub(r'(const MUNIS = \[).*?(\];)',
                    f'\\g<1>\n{inner}\n\\g<2>', html, flags=re.DOTALL)
    if fecha_str:
        html = re.sub(r'(<em id="last-updated">)[^<]*(</em>)',
                      f'\\g<1>{fecha_str}\\g<2>', html)
    return html

def run():
    if not API_KEY:
        print("ERROR: ANTHROPIC_API_KEY no configurada"); sys.exit(1)

    now   = datetime.now(COLOMBIA_TZ)
    today = now.date()
    start = today - timedelta(days=EVENT_WINDOW_DAYS)
    fecha = f"{now.day} {MESES[now.month]} {now.year}"
    inicio= f"{start.day} {MESES[start.month]} {start.year}"

    print(f"Actualizacion CoH: {fecha}")
    print(f"Ventana activa: {inicio} a {fecha}")

    if not HTML_FILE.exists():
        print(f"ERROR: {HTML_FILE} no encontrado"); sys.exit(1)

    html = HTML_FILE.read_text(encoding='utf-8')

    # Load existing municipalities
    existing_munis = []
    em = re.search(r'const MUNIS = \[(.*?)\];', html, re.DOTALL)
    if em:
        try:
            existing_munis = json.loads('[' + em.group(1) + ']')
            print(f"Base cargada: {len(existing_munis)} municipios")
        except Exception as e:
            print(f"No se pudo leer MUNIS: {e}")

    by_name = {m['n']: m for m in existing_munis}
    before  = {k: dict(v) for k,v in by_name.items()}

    # Seed GREEN baseline (rest of country)
    for g in GREEN_BASELINE:
        if g['n'] not in by_name:
            by_name[g['n']] = make_green(g, today)

    # Seed ALL focus department municipalities (Cauca/Valle/Narino)
    for f in FOCUS_MUNICIPALITIES:
        if f['n'] not in by_name:
            by_name[f['n']] = make_focus_muni(f, today)
        else:
            # Mark existing ones as focus dept and update structure info
            by_name[f['n']]['focus_dept'] = True
            if not by_name[f['n']].get('a') or by_name[f['n']]['a'] == 'N/A - monitoreo rutinario':
                by_name[f['n']]['a'] = f.get('a','')
    print(f"  Focus depts seeded: {len(FOCUS_MUNICIPALITIES)} municipios (Cauca/Valle del Cauca/Narino)")

    # Step 0: Migrate old-format municipalities to new dated events format
    migrated = 0
    for name in list(by_name.keys()):
        m = by_name[name]
        if 'events' not in m and m.get('ev_pts'):
            by_name[name] = migrate_if_needed(m, today)
            migrated += 1
    if migrated:
        print(f"  Migrado: {migrated} municipios de formato antiguo a nuevo")

    # Step 1: Expire old events FIRST - before AI search
    for name in list(by_name.keys()):
        old_zone  = by_name[name].get('r','green')
        old_count = len(by_name[name].get('events',[]))
        by_name[name] = recalculate(by_name[name], today)
        new_count = len(by_name[name].get('events',[]))
        if old_count != new_count:
            print(f"  Expirado: {name} — {old_count-new_count} evento(s) eliminado(s) "
                  f"| zona: {old_zone} -> {by_name[name].get('r','')}")

    # Step 2: AI search for new events
    client = anthropic.Anthropic(api_key=API_KEY)
    resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=16000,
        system=SYSTEM,
        messages=[{"role":"user","content":
            f"Hoy es {fecha}. Busca SISTEMATICAMENTE eventos de seguridad y conflicto armado "
            f"en Colombia de los ultimos 60 dias ({str(today - timedelta(days=60))} al {fecha}). "
            f"Para Cauca, Valle del Cauca y Narino busca hasta 60 dias atras. "
            f"Para el resto del pais busca los ultimos 30 dias ({inicio} al {fecha}). "
            f"Fuentes: Indepaz, ACLED, OCHA, Crisis Group, Defensoria del Pueblo Colombia, "
            f"El Tiempo, El Colombiano, El Espectador, W Radio, Semana, Caracol, RCN. "
            f"CRITICO: Usa la fecha REAL de cada evento (YYYY-MM-DD), no la fecha de hoy. "
            f"Busca evento por evento, municipio por municipio, en los departamentos "
            f"mas afectados: Cauca, Narino, Valle del Cauca, Antioquia, Choco, "
            f"Norte de Santander, Arauca, Putumayo, Caqueta, Huila, Tolima. "
            f"Para cada municipio en la lista prioritaria, verifica si hubo eventos "
            f"en los ultimos 30 dias y registra cada uno por separado con su fecha exacta. "
            f"Incluye municipios estables sin eventos (events=[]). "
            f"Entre 70 y 85 municipios total. Campo i maximo 90 chars. TODO en ESPANOL. "
            f"Devuelve SOLO JSON valido comenzando con {{ terminando con }}."}],
        tools=[{"type":"web_search_20250305","name":"web_search"}]
    )

    text = "".join(b.text for b in resp.content if b.type=="text")
    if not text:
        print("Sin respuesta IA - guardando datos con eventos expirados")
        HTML_FILE.write_text(write_html(html,by_name,fecha),encoding='utf-8')
        return

    j0=text.find('{'); j1=text.rfind('}')
    if j0==-1 or j1==-1:
        print("Sin JSON - guardando datos con eventos expirados")
        HTML_FILE.write_text(write_html(html,by_name,fecha),encoding='utf-8')
        return

    try:
        data = json.loads(text[j0:j1+1])
    except json.JSONDecodeError as e:
        print(f"Error JSON: {e} - guardando datos con eventos expirados")
        HTML_FILE.write_text(write_html(html,by_name,fecha),encoding='utf-8')
        return

    if not data.get('municipios'):
        HTML_FILE.write_text(write_html(html,by_name,fecha),encoding='utf-8')
        return

    # Step 3: Merge AI events with existing dated events
    print(f"IA devolvio {len(data['municipios'])} municipios - fusionando con historial...")
    updated=added=0

    for ai_m in data['municipios']:
        name      = ai_m.get('n','')
        ai_events = ai_m.get('events',[])

        if ai_m.get('auto_red') and not ai_m.get('auto_red_date'):
            ai_m['auto_red_date'] = ai_events[0].get('date',str(today)) if ai_events else str(today)

        if name in by_name:
            ex = by_name[name]
            ex['events'] = merge_events(ex.get('events',[]), ai_events, today)
            ex['i'] = ai_m.get('i', ex.get('i',''))
            ex['a'] = ai_m.get('a', ex.get('a',''))
            if ai_m.get('auto_red'):
                ex['auto_red']      = True
                ex['auto_red_why']  = ai_m.get('auto_red_why','')
                ex['auto_red_date'] = ai_m.get('auto_red_date',str(today))
            by_name[name] = recalculate(ex, today)
            updated += 1
        else:
            by_name[name] = recalculate(ai_m, today)
            added += 1

    merged = list(by_name.values())
    counts = {r: sum(1 for x in merged if x.get('r')==r)
              for r in ('red','orange','yellow','green')}
    print(f"Actualizado:{updated} Nuevo:{added} Total:{len(merged)}")
    print(f"ROJO={counts['red']} NARANJA={counts['orange']} AMARILLO={counts['yellow']} VERDE={counts['green']}")

    HTML_FILE.write_text(write_html(html,by_name,data.get('fecha',fecha)),encoding='utf-8')
    print(f"EXITO: index.html actualizado - {fecha}")

    after = {m['n']:m for m in merged}
    send_email(fecha, before, after, counts)

if __name__ == "__main__":
    run()
