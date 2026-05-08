import os, json, re, sys
import urllib.request, urllib.error
from datetime import datetime, timezone, timedelta
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
ZONE_COLOR = {'red':'#dc2626','orange':'#ea580c','yellow':'#b45309','green':'#15803d'}
ZONE_BG    = {'red':'#fef2f2','orange':'#fff7ed','yellow':'#fefce8','green':'#f0fdf4'}

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
    {"n":"Iniride",              "d":"Guainia",             "lat":3.8653, "lng":-67.9239},
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

def make_green(m):
    return {
        "n": m["n"], "d": m["d"], "lat": m["lat"], "lng": m["lng"],
        "r": "green", "sz": 10, "adjusted_score": 0,
        "ev_count": 0, "ev_pts": [],
        "sc": "Puntaje ajustado: 0 | VERDE",
        "i":  "Sin incidentes de conflicto armado verificados en los ultimos 30 dias.",
        "a":  "N/A - monitoreo rutinario",
        "auto_red": False, "auto_red_why": ""
    }

def enforce_coh_rubric(munis):
    amazon = ['leticia','leguizamo']
    corrections = []
    for m in munis:
        dept  = m.get('d','').lower()
        name  = m.get('n','').lower()
        if m.get('lat', 0) < -0.5:
            if not any(a in name for a in amazon):
                m['lat'] = abs(m['lat'])
        ev_pts   = m.get('ev_pts', [])
        ev_count = m.get('ev_count', len(ev_pts))
        auto_red = m.get('auto_red', False)
        sev      = sum(ev_pts)
        mult     = get_multiplier(ev_count)
        adj      = round(sev * mult, 1)
        m['adjusted_score'] = adj
        m['ev_count']       = ev_count
        if auto_red:
            m['r']  = 'red'
            m['sz'] = 22
            why = m.get('auto_red_why', 'Condicion de anulacion automatica activada')
            m['sc'] = f"ROJO AUTOMATICO: {why}"
            continue
        zona = score_to_zone(adj)
        m['sz'] = score_to_size(adj)
        m['sc'] = f"Gravedad:{sev}pts x{mult} = {adj} | {ZONE_ES.get(zona, zona.upper())}"
        if zona == 'orange':
            en_restringido = any(rd in dept for rd in RESTRICTED_DEPTS)
            es_excluida    = any(ec in name for ec in EXCLUDED_CITIES)
            if not en_restringido or es_excluida:
                zona = 'yellow'
                m['sc'] = f"Gravedad:{sev}pts x{mult} = {adj} | AMARILLO (depto. no restringido)"
                corrections.append(m['n'])
        m['r'] = zona
    if corrections:
        print(f"  NARANJA->AMARILLO: {', '.join(corrections)}")
    return munis

# Email con SendGrid
def build_email(fecha, before, after, counts):
    escalated   = []
    deescalated = []
    nuevos      = []
    auto_rojos  = []
    order = {'green':0,'yellow':1,'orange':2,'red':3}

    for name, m in after.items():
        nz = m.get('r','green')
        if m.get('auto_red'):
            auto_rojos.append((name, m.get('d',''), m.get('auto_red_why','')))
        if name not in before:
            nuevos.append((name, m.get('d',''), nz, m.get('i','')))
        else:
            oz = before[name].get('r','green')
            if order.get(nz,0) > order.get(oz,0):
                escalated.append((name, m.get('d',''), oz, nz, m.get('i','')))
            elif order.get(nz,0) < order.get(oz,0):
                deescalated.append((name, m.get('d',''), oz, nz))

    escalated.sort(key=lambda x: -order.get(x[3],0))

    def badge(z):
        c = ZONE_COLOR.get(z,'#666')
        b = ZONE_BG.get(z,'#f9f9f9')
        return (f'<span style="background:{b};color:{c};border:1px solid {c};'
                f'padding:2px 8px;border-radius:3px;font-size:11px;font-weight:700;'
                f'font-family:monospace">{ZONE_ES.get(z,z.upper())}</span>')

    def arrow(o, n):
        return f'{badge(o)} &nbsp;&#8594;&nbsp; {badge(n)}'

    re_rows = "".join(
        f'<tr><td style="padding:6px 10px;border-bottom:1px solid #e5e7eb">'
        f'<strong>{n}</strong><br><small style="color:#6b7280">{d}</small></td>'
        f'<td style="padding:6px 10px;border-bottom:1px solid #e5e7eb">{arrow(o,nw)}</td>'
        f'<td style="padding:6px 10px;border-bottom:1px solid #e5e7eb;font-size:12px">{i}</td></tr>'
        for n, d, o, nw, i in escalated
    )
    de_rows = "".join(
        f'<tr><td style="padding:6px 10px;border-bottom:1px solid #e5e7eb">'
        f'<strong>{n}</strong><br><small style="color:#6b7280">{d}</small></td>'
        f'<td style="padding:6px 10px;border-bottom:1px solid #e5e7eb">{arrow(o,nw)}</td></tr>'
        for n, d, o, nw in deescalated
    )
    nw_rows = "".join(
        f'<tr><td style="padding:6px 10px;border-bottom:1px solid #e5e7eb">'
        f'<strong>{n}</strong><br><small style="color:#6b7280">{d}</small></td>'
        f'<td style="padding:6px 10px;border-bottom:1px solid #e5e7eb">{badge(z)}</td>'
        f'<td style="padding:6px 10px;border-bottom:1px solid #e5e7eb;font-size:12px">{i}</td></tr>'
        for n, d, z, i in nuevos
    )

    def tbl(title, color, header_cols, rows):
        ths = "".join(
            f'<th style="padding:8px 10px;text-align:left;font-size:11px;color:#6b7280;'
            f'border-bottom:2px solid #e5e7eb">{h}</th>' for h in header_cols
        )
        return (
            f'<h3 style="color:{color};font-size:14px;margin-top:24px">{title}</h3>'
            f'<table width="100%" cellpadding="0" cellspacing="0" '
            f'style="border-collapse:collapse;font-size:13px;margin-bottom:20px">'
            f'<thead><tr style="background:#f9fafb">{ths}</tr></thead>'
            f'<tbody>{rows}</tbody></table>'
        )

    body = ""
    if auto_rojos:
        items = "".join(f'<li style="margin-bottom:4px"><strong>{n}</strong> ({d}): {w}</li>'
                        for n, d, w in auto_rojos)
        body += (f'<div style="background:#fef2f2;border:1px solid #dc2626;border-radius:6px;'
                 f'padding:14px 16px;margin-bottom:20px">'
                 f'<h3 style="margin:0 0 8px;color:#dc2626;font-size:14px">'
                 f'Anulaciones Automaticas a ROJO ({len(auto_rojos)})</h3>'
                 f'<ul style="margin:0;padding-left:18px;font-size:13px">{items}</ul></div>')
    if escalated:
        body += tbl(f'Escalaciones - Zonas que Empeoraron ({len(escalated)})',
                    '#dc2626', ['MUNICIPIO','CAMBIO','INCIDENTE'], re_rows)
    if deescalated:
        body += tbl(f'Mejoras - Zonas que Mejoraron ({len(deescalated)})',
                    '#15803d', ['MUNICIPIO','CAMBIO'], de_rows)
    if nuevos:
        body += tbl(f'Municipios Nuevos Agregados ({len(nuevos)})',
                    '#374151', ['MUNICIPIO','ZONA','INCIDENTE'], nw_rows)
    if not body:
        body = '<p style="color:#6b7280;font-style:italic">No se detectaron cambios de zona esta semana.</p>'

    r = counts.get('red',0); o = counts.get('orange',0)
    y = counts.get('yellow',0); g = counts.get('green',0)

    html = (
        '<!DOCTYPE html><html lang="es"><head><meta charset="UTF-8"></head>'
        '<body style="margin:0;padding:0;background:#f3f4f6;font-family:Arial,sans-serif">'
        '<table width="100%" cellpadding="0" cellspacing="0" '
        'style="background:#f3f4f6;padding:24px 0"><tr><td align="center">'
        '<table width="620" cellpadding="0" cellspacing="0" '
        'style="background:#fff;border-radius:8px;overflow:hidden;'
        'box-shadow:0 1px 3px rgba(0,0,0,.1)">'
        '<tr><td style="background:#0a1223;padding:24px 28px">'
        '<p style="margin:0;font-size:11px;color:#38bdf8;font-family:monospace;'
        'letter-spacing:.1em;text-transform:uppercase">CONVOY OF HOPE - COLOMBIA</p>'
        f'<h1 style="margin:6px 0 0;color:#f0f6ff;font-size:20px;font-weight:700">'
        f'Actualizacion Semanal del Mapa de Riesgo</h1>'
        f'<p style="margin:4px 0 0;color:#8faabb;font-size:13px">{fecha} &nbsp;·&nbsp; CONFIDENCIAL</p>'
        '</td></tr>'
        '<tr><td style="background:#080f1e;padding:14px 28px">'
        '<table width="100%" cellpadding="0" cellspacing="0"><tr>'
        f'<td align="center" style="color:#f87171;font-family:monospace">'
        f'<span style="font-size:26px;font-weight:700">{r}</span><br>'
        f'<span style="font-size:10px;letter-spacing:.1em">ROJO</span></td>'
        f'<td align="center" style="color:#fb923c;font-family:monospace">'
        f'<span style="font-size:26px;font-weight:700">{o}</span><br>'
        f'<span style="font-size:10px;letter-spacing:.1em">NARANJA</span></td>'
        f'<td align="center" style="color:#fbbf24;font-family:monospace">'
        f'<span style="font-size:26px;font-weight:700">{y}</span><br>'
        f'<span style="font-size:10px;letter-spacing:.1em">AMARILLO</span></td>'
        f'<td align="center" style="color:#4ade80;font-family:monospace">'
        f'<span style="font-size:26px;font-weight:700">{g}</span><br>'
        f'<span style="font-size:10px;letter-spacing:.1em">VERDE</span></td>'
        '</tr></table></td></tr>'
        f'<tr><td style="padding:24px 28px">{body}'
        f'<div style="text-align:center;margin-top:28px">'
        f'<a href="{MAP_URL}" style="background:#0a1223;color:#38bdf8;text-decoration:none;'
        f'padding:12px 28px;border-radius:4px;font-family:monospace;font-size:12px;'
        f'letter-spacing:.08em;border:1px solid #38bdf8">VER MAPA COMPLETO</a></div>'
        '<p style="margin-top:28px;font-size:11px;color:#9ca3af;border-top:1px solid #e5e7eb;'
        'padding-top:16px">Actualizacion automatica generada cada domingo 23:00 COT usando '
        'Indepaz, ACLED, OCHA, Crisis Group, Defensoria del Pueblo y medios colombianos.<br><br>'
        'CONFIDENCIAL - Solo Personal Autorizado CoH Colombia</p>'
        '</td></tr></table></td></tr></table></body></html>'
    )

    lines = [
        "CONVOY OF HOPE - COLOMBIA",
        f"Actualizacion Semanal del Mapa de Riesgo - {fecha}",
        "CONFIDENCIAL", "",
        f"ROJO={r} | NARANJA={o} | AMARILLO={y} | VERDE={g}", ""
    ]
    if auto_rojos:
        lines += [f"ANULACIONES AUTOMATICAS A ROJO ({len(auto_rojos)}):"] + \
                 [f"  - {n} ({d}): {w}" for n,d,w in auto_rojos] + [""]
    if escalated:
        lines += [f"ESCALACIONES ({len(escalated)}):"] + \
                 [f"  - {n} ({d}): {ZONE_ES[o]} -> {ZONE_ES[nw]} - {i}" for n,d,o,nw,i in escalated] + [""]
    if deescalated:
        lines += [f"MEJORAS ({len(deescalated)}):"] + \
                 [f"  - {n} ({d}): {ZONE_ES[o]} -> {ZONE_ES[nw]}" for n,d,o,nw in deescalated] + [""]
    if not escalated and not deescalated and not nuevos and not auto_rojos:
        lines += ["No se detectaron cambios de zona esta semana.", ""]
    lines.append(f"Ver mapa: {MAP_URL}")

    return html, "\n".join(lines)

def send_email(fecha, before, after, counts):
    if not all([SG_KEY, EMAIL_FROM, EMAIL_TO]):
        print("Credenciales SendGrid no configuradas - omitiendo envio de email")
        return
    html_body, text_body = build_email(fecha, before, after, counts)
    recipients = [{"email": r.strip()} for r in EMAIL_TO.split(',')]
    payload = json.dumps({
        "personalizations": [{"to": recipients}],
        "from": {"email": EMAIL_FROM, "name": "CoH Colombia Risk Map"},
        "subject": f"CoH Colombia - Mapa de Riesgo Actualizado: {fecha}",
        "content": [
            {"type": "text/plain", "value": text_body},
            {"type": "text/html",  "value": html_body}
        ]
    }).encode('utf-8')
    req = urllib.request.Request(
        "https://api.sendgrid.com/v3/mail/send",
        data=payload,
        headers={"Authorization": f"Bearer {SG_KEY}", "Content-Type": "application/json"},
        method="POST"
    )
    try:
        with urllib.request.urlopen(req) as resp:
            print(f"Email enviado correctamente (status {resp.status})")
    except urllib.error.HTTPError as e:
        print(f"Error SendGrid {e.code}: {e.read().decode()}")

# Prompt del sistema
SYSTEM = """Eres un analista de seguridad aplicando la Rubrica de Puntuacion de Riesgo Municipal de Convoy of Hope Colombia (Seccion 3, Plan de Contingencia v3.0).

IMPORTANTE: TODOS los campos de texto deben estar en ESPANOL. Esto incluye los campos "i", "a" y "auto_red_why".

PASO 1 - Puntos de gravedad por tipo de evento:
  10 pts: Fatalidad civil | Ataque a CoH | Secuestro/desaparicion | Masacre
   8 pts: Combate dentro de 10km con muertos/heridos | Amenaza directa contra humanitarios
   5 pts: Heridos civiles por conflicto/UXO | Combate dentro de 30km con danos | Explosion/IED/UXO | Desplazamiento forzado
   4 pts: Reten ilegal/bloqueo/extorsion | Amenaza contra lider/socio/beneficiario
   2 pts: Movimiento armado dentro de 50km | Danos a propiedad | Paro armado/restriccion de acceso
   1 pt:  Tension comunitaria/protesta | Zona de coca/via sin despejar

PASO 2 - Multiplicador de frecuencia:
  1 evento: x1.0 | 2: x1.25 | 3-4: x1.5 | 5-7: x2.0 | 8+: x2.5

PASO 3 - El script calcula la zona automaticamente (puntaje ajustado = gravedad x multiplicador):
  0-4: VERDE | 5-14: AMARILLO | 15-29: NARANJA | 30+: ROJO

ANULACION AUTOMATICA A ROJO (set auto_red=true):
  - Combate activo dentro de 10km con muertos confirmados
  - Masacre o evento de bajas masivas (3 o mas muertos en un solo incidente)
  - Amenaza directa creible contra CoH o comunidades atendidas
  - Secuestro de personal, socio o beneficiario
  - Zona declarada restringida por el gobierno
  - Desplazamiento masivo forzado desde area de programa CoH
  - Colapso total de acceso (mas del 50% de rutas denegadas por 7 dias)

Devuelve SOLO un JSON valido. Sin preambulo. Sin markdown. Comienza con { termina con }.
70-80 municipios. Incluye ciudades estables sin eventos (ev_pts=[], ev_count=0).
Campo "i" maximo 90 caracteres. TODOS los textos en ESPANOL.

{
  "fecha": "DD Mmm YYYY",
  "municipios": [
    {
      "n": "Nombre municipio",
      "d": "Departamento",
      "lat": 0.0,
      "lng": 0.0,
      "ev_pts": [10, 5, 4],
      "ev_count": 3,
      "auto_red": false,
      "auto_red_why": "",
      "i": "resumen en espanol maximo 90 caracteres",
      "a": "grupos armados activos en espanol"
    }
  ]
}

COORDENADAS CRITICAS: Colombia es principalmente al NORTE del ecuador - latitudes POSITIVAS.
Solo Leticia (-4.2) y Puerto Leguizamo (-0.19) son negativos."""

def run():
    if not API_KEY:
        print("ERROR: ANTHROPIC_API_KEY no configurada")
        sys.exit(1)

    now    = datetime.now(COLOMBIA_TZ)
    start  = now - timedelta(days=30)
    fecha  = f"{now.day} {MESES[now.month]} {now.year}"
    inicio = f"{start.day} {MESES[start.month]} {start.year}"

    print(f"Actualizacion CoH: {fecha} | Ventana: {inicio} a {fecha}")

    if not HTML_FILE.exists():
        print(f"ERROR: {HTML_FILE} no encontrado")
        sys.exit(1)

    html = HTML_FILE.read_text(encoding='utf-8')

    existing_munis = []
    em = re.search(r'const MUNIS = \[(.*?)\];', html, re.DOTALL)
    if em:
        try:
            existing_munis = json.loads('[' + em.group(1) + ']')
            print(f"Base: {len(existing_munis)} municipios")
        except Exception as e:
            print(f"No se pudo leer MUNIS: {e}")

    by_name = {m['n']: m for m in existing_munis}
    before  = {k: dict(v) for k, v in by_name.items()}

    for g in GREEN_BASELINE:
        if g['n'] not in by_name:
            by_name[g['n']] = make_green(g)
    print(f"Con base VERDE: {len(by_name)} municipios")

    client = anthropic.Anthropic(api_key=API_KEY)
    resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=16000,
        system=SYSTEM,
        messages=[{"role":"user","content":
            f"Hoy es {fecha}. Busca TODOS los eventos de seguridad en Colombia "
            f"del {inicio} al {fecha} usando: Indepaz, ACLED, OCHA, Crisis Group, "
            f"Defensoria del Pueblo, El Tiempo, El Colombiano, W Radio, Semana. "
            f"Aplica la rubrica CoH a cada municipio. "
            f"Incluye ciudades estables sin eventos (ev_pts=[], ev_count=0). "
            f"Cubre TODAS las regiones colombianas. Devuelve 70-80 municipios. "
            f"TODOS los textos en ESPANOL. Campo 'i' maximo 90 caracteres. "
            f"Devuelve SOLO JSON comenzando con {{ terminando con }}."}],
        tools=[{"type":"web_search_20250305","name":"web_search"}]
    )

    text = "".join(b.text for b in resp.content if b.type == "text")
    if not text:
        print("Sin respuesta - manteniendo datos existentes")
        return

    j0 = text.find('{')
    j1 = text.rfind('}')
    if j0 == -1 or j1 == -1:
        print("Sin JSON - manteniendo datos existentes")
        return

    try:
        data = json.loads(text[j0:j1+1])
    except json.JSONDecodeError as e:
        print(f"Error JSON: {e} - manteniendo datos")
        return

    if not data.get('municipios'):
        print("Sin municipios - manteniendo datos")
        return

    print(f"IA devolvio {len(data['municipios'])} municipios - aplicando rubrica CoH...")
    ai_munis = enforce_coh_rubric(data['municipios'])

    updated = added = 0
    for m in ai_munis:
        adj  = m.get('adjusted_score', 0)
        name = m['n']
        if name in by_name:
            if adj > 0 or m.get('auto_red'):
                by_name[name] = m
                updated += 1
        else:
            by_name[name] = m
            added += 1

    for m in ai_munis:
        if m.get('adjusted_score', 0) == 0 and not m.get('auto_red'):
            m['r']  = 'green'
            m['sc'] = 'Puntaje ajustado: 0 | VERDE'
            m['sz'] = 10
            by_name[m['n']] = m

    merged = list(by_name.values())
    counts = {r: sum(1 for x in merged if x.get('r') == r)
              for r in ('red','orange','yellow','green')}
    print(f"Actualizado:{updated} Nuevo:{added} Total:{len(merged)}")
    print(f"ROJO={counts['red']} NARANJA={counts['orange']} AMARILLO={counts['yellow']} VERDE={counts['green']}")

    inner = json.dumps(merged, ensure_ascii=False)[1:-1]
    html  = re.sub(r'(const MUNIS = \[).*?(\];)',
                   f'\\g<1>\n{inner}\n\\g<2>', html, flags=re.DOTALL)
    if data.get('fecha'):
        html = re.sub(r'(<em id="last-updated">)[^<]*(</em>)',
                      f'\\g<1>{data["fecha"]}\\g<2>', html)
    HTML_FILE.write_text(html, encoding='utf-8')
    print(f"EXITO: index.html actualizado - {fecha}")

    after = {m['n']: m for m in merged}
    send_email(fecha, before, after, counts)

if __name__ == "__main__":
    run()
