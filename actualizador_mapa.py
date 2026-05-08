import os, json, re, sys
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
MESES       = {1:'ene',2:'feb',3:'mar',4:'abr',5:'may',6:'jun',
               7:'jul',8:'ago',9:'sep',10:'oct',11:'nov',12:'dic'}

# ══════════════════════════════════════════════════════════════════════
# COH RUBRIC — Sección 3, Plan de Contingencia v3.0
# ══════════════════════════════════════════════════════════════════════

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
    'cauca','valle del cauca','nariño','narino','antioquia',
    'chocó','choco','putumayo','huila','tolima','arauca',
    'norte de santander','caquetá','caqueta'
}
EXCLUDED_CITIES = {
    'popayán','popayan','cali','palmira','pasto',
    'medellín','medellin','bello','envigado','itagüí','itagui'
}

# Ciudades estables — base permanente de puntos VERDES
GREEN_BASELINE = [
    {"n":"Bogotá",               "d":"Cundinamarca (D.C.)", "lat":4.7110, "lng":-74.0721},
    {"n":"Barranquilla",         "d":"Atlántico",           "lat":10.9685,"lng":-74.7813},
    {"n":"Cartagena",            "d":"Bolívar",             "lat":10.3910,"lng":-75.4794},
    {"n":"Santa Marta",          "d":"Magdalena",           "lat":11.2408,"lng":-74.2110},
    {"n":"Bucaramanga",          "d":"Santander",           "lat":7.1254, "lng":-73.1198},
    {"n":"Armenia",              "d":"Quindío",             "lat":4.5339, "lng":-75.6811},
    {"n":"Pereira",              "d":"Risaralda",           "lat":4.8133, "lng":-75.6961},
    {"n":"Manizales",            "d":"Caldas",              "lat":5.0689, "lng":-75.5174},
    {"n":"Tunja",                "d":"Boyacá",              "lat":5.5353, "lng":-73.3678},
    {"n":"Sincelejo",            "d":"Sucre",               "lat":9.3047, "lng":-75.3978},
    {"n":"Leticia",              "d":"Amazonas",            "lat":-4.2133,"lng":-69.9400},
    {"n":"Mitú",                 "d":"Vaupés",              "lat":1.1985, "lng":-70.1734},
    {"n":"Inírida",              "d":"Guainía",             "lat":3.8653, "lng":-67.9239},
    {"n":"Puerto Carreño",       "d":"Vichada",             "lat":6.1891, "lng":-67.4839},
    {"n":"Yopal",                "d":"Casanare",            "lat":5.3378, "lng":-72.3959},
    {"n":"Villavicencio",        "d":"Meta",                "lat":4.1420, "lng":-73.6267},
    {"n":"Neiva",                "d":"Huila",               "lat":2.9273, "lng":-75.2819},
    {"n":"Popayán",              "d":"Cauca",               "lat":2.4419, "lng":-76.6072},
    {"n":"Pasto",                "d":"Nariño",              "lat":1.2136, "lng":-77.2811},
    {"n":"Cali",                 "d":"Valle del Cauca",     "lat":3.4516, "lng":-76.5320},
    {"n":"Medellín",             "d":"Antioquia",           "lat":6.2442, "lng":-75.5812},
    {"n":"Ibagué",               "d":"Tolima",              "lat":4.4389, "lng":-75.2322},
    {"n":"Valledupar",           "d":"Cesar",               "lat":10.4772,"lng":-73.2503},
    {"n":"Montería",             "d":"Córdoba",             "lat":8.7575, "lng":-75.8933},
    {"n":"Riohacha",             "d":"La Guajira",          "lat":11.5381,"lng":-72.9067},
    {"n":"Florencia",            "d":"Caquetá",             "lat":1.6167, "lng":-75.6167},
    {"n":"Mocoa",                "d":"Putumayo",            "lat":1.1519, "lng":-76.6497},
    {"n":"San José del Guaviare","d":"Guaviare",            "lat":2.5683, "lng":-72.6408},
    {"n":"Arauca",               "d":"Arauca",              "lat":7.0842, "lng":-70.7553},
    {"n":"Palmira",              "d":"Valle del Cauca",     "lat":3.5394, "lng":-76.2983},
]

def make_green(m):
    return {
        "n": m["n"], "d": m["d"], "lat": m["lat"], "lng": m["lng"],
        "r": "green", "sz": 10,
        "adjusted_score": 0, "ev_count": 0, "ev_pts": [],
        "sc": "Puntaje ajustado: 0 | VERDE",
        "i":  "Sin incidentes de conflicto armado verificados en los últimos 30 días.",
        "a":  "N/A — monitoreo rutinario",
        "auto_red": False, "auto_red_why": ""
    }

def enforce_coh_rubric(munis):
    amazon = ['leticia','leguizamo']
    corrections = []
    for m in munis:
        dept  = m.get('d','').lower()
        name  = m.get('n','').lower()

        # Corregir latitudes negativas incorrectas
        if m.get('lat', 0) < -0.5:
            if not any(a in name for a in amazon):
                m['lat'] = abs(m['lat'])

        # Calcular puntaje ajustado
        ev_pts   = m.get('ev_pts', [])
        ev_count = m.get('ev_count', len(ev_pts))
        auto_red = m.get('auto_red', False)
        sev      = sum(ev_pts)
        mult     = get_multiplier(ev_count)
        adj      = round(sev * mult, 1)
        m['adjusted_score'] = adj
        m['ev_count']       = ev_count

        # Anulación automática a ROJO
        if auto_red:
            m['r']  = 'red'
            m['sz'] = 22
            why = m.get('auto_red_why','Condición de anulación automática activada')
            m['sc'] = f"ROJO AUTOMÁTICO: {why}"
            continue

        # Determinar zona según puntaje
        zona = score_to_zone(adj)
        m['sz'] = score_to_size(adj)
        m['sc'] = f"Gravedad:{sev}pts ×{mult} = {adj} | {zona.upper()}"

        # Aplicar restricción US State Dept para NARANJA
        if zona == 'orange':
            en_restringido = any(rd in dept for rd in RESTRICTED_DEPTS)
            es_excluida    = any(ec in name for ec in EXCLUDED_CITIES)
            if not en_restringido or es_excluida:
                zona = 'yellow'
                m['sc'] = f"Gravedad:{sev}pts ×{mult} = {adj} | AMARILLO (depto. no restringido)"
                corrections.append(m['n'])

        m['r'] = zona

    if corrections:
        print(f"  Correcciones NARANJA→AMARILLO (US State Dept): {', '.join(corrections)}")
    return munis

# ══════════════════════════════════════════════════════════════════════
# PROMPT DEL SISTEMA
# ══════════════════════════════════════════════════════════════════════
SYSTEM = """Eres un analista de seguridad aplicando el Sistema de Puntuación de Riesgo Municipal de Convoy of Hope Colombia (Sección 3, Plan de Contingencia v3.0). TODOS los campos de texto deben estar en ESPAÑOL.

PASO 1 — Puntos de gravedad por tipo de evento (incluye TODOS los eventos que afectan al municipio):
  10 pts: Fatalidad civil | Ataque a personal/sede CoH | Secuestro/desaparición | Masacre/evento de bajas masivas
   8 pts: Combate activo dentro de 10km con heridos/muertos | Amenaza directa creíble contra humanitarios
   5 pts: Heridos civiles por conflicto/UXO | Combate dentro de 30km con daños | Explosión/IED/UXO | Desplazamiento forzado
   4 pts: Retén ilegal/bloqueo/extorsión | Amenaza contra líder/socio/beneficiario
   2 pts: Movimiento armado dentro de 50km | Daños a propiedad/saqueo | Toque de queda/paro/restricción de acceso
   1 pt:  Tensión comunitaria/protesta | Zona de coca/vía sin despejar/actividad sospechosa

PASO 2 — Multiplicador de frecuencia:
  1 evento: ×1.0 | 2 eventos: ×1.25 | 3-4 eventos: ×1.5 | 5-7 eventos: ×2.0 | 8+ eventos: ×2.5

PASO 3 — El script calcula la zona automáticamente (puntaje ajustado = gravedad × multiplicador):
  0-4: VERDE | 5-14: AMARILLO | 15-29: NARANJA | 30+: ROJO

ANULACIÓN AUTOMÁTICA A ROJO (set auto_red=true):
  - Combate activo dentro de 10km con muertos
  - Evento de bajas masivas (3+ muertos en un solo incidente)
  - Amenaza directa creíble contra CoH o comunidades atendidas
  - Secuestro de personal, socio o beneficiario
  - Zona declarada restringida por el gobierno
  - Desplazamiento masivo forzado desde área de programa CoH
  - Colapso total de acceso (>50% denegación de rutas en 7 días)

Devuelve SOLO un objeto JSON válido — sin preámbulo, sin markdown. Comienza con { y termina con }.
Cubre TODO el país. Devuelve 70-80 municipios incluyendo ciudades estables sin eventos (ev_pts=[], ev_count=0).
Todos los campos de texto ("i", "a", "auto_red_why") deben estar en ESPAÑOL.
Máximo 90 caracteres en el campo "i".

Estructura JSON:
{
  "fecha": "DD Mmm YYYY",
  "municipios": [
    {
      "n": "Nombre del municipio",
      "d": "Departamento",
      "lat": 0.0,
      "lng": 0.0,
      "ev_pts": [10, 5, 4],
      "ev_count": 3,
      "auto_red": false,
      "auto_red_why": "",
      "i": "resumen de eventos en español, máx 90 caracteres",
      "a": "grupos armados activos en español"
    }
  ]
}

CRÍTICO: Colombia está principalmente al NORTE del ecuador — latitudes POSITIVAS.
Cauca ~2-4N, Valle del Cauca ~3-5N, Nariño ~0-2.5N, Antioquia ~5-8N, Bogotá ~4.7N.
Solo Leticia (-4.2) y Puerto Leguízamo (-0.19) son legítimamente negativas."""

def run():
    if not API_KEY:
        print("ERROR: ANTHROPIC_API_KEY no configurada")
        sys.exit(1)

    now    = datetime.now(COLOMBIA_TZ)
    start  = now - timedelta(days=30)
    fecha  = f"{now.day} {MESES[now.month]} {now.year}"
    inicio = f"{start.day} {MESES[start.month]} {start.year}"

    print(f"Actualización Rubrica CoH: {fecha} | Ventana: {inicio} a {fecha}")

    if not HTML_FILE.exists():
        print(f"ERROR: {HTML_FILE} no encontrado")
        sys.exit(1)

    html = HTML_FILE.read_text(encoding='utf-8')

    # Cargar municipios existentes como base
    existing_munis = []
    em = re.search(r'const MUNIS = \[(.*?)\];', html, re.DOTALL)
    if em:
        try:
            existing_munis = json.loads('[' + em.group(1) + ']')
            print(f"Base: {len(existing_munis)} municipios existentes")
        except Exception as e:
            print(f"No se pudo leer MUNIS existente: {e}")

    by_name = {m['n']: m for m in existing_munis}

    # Sembrar ciudades VERDES estables
    for g in GREEN_BASELINE:
        if g['n'] not in by_name:
            by_name[g['n']] = make_green(g)
    print(f"Con base VERDE: {len(by_name)} municipios")

    # Llamar a la API de Anthropic
    client = anthropic.Anthropic(api_key=API_KEY)
    resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=16000,
        system=SYSTEM,
        messages=[{"role":"user","content":
            f"Hoy es {fecha}. Busca TODOS los eventos de seguridad en Colombia "
            f"del {inicio} al {fecha} usando: Indepaz, ACLED, OCHA, Crisis Group, "
            f"Defensoría del Pueblo, medios colombianos. "
            f"Aplica la rúbrica de puntuación CoH a cada municipio. "
            f"Incluye ciudades estables sin eventos (ev_pts=[], ev_count=0). "
            f"Cubre TODAS las regiones: suroccidente (Cauca, Nariño, Valle), "
            f"nororiente (NdS, Arauca, frontera venezolana), Pacífico (Chocó), "
            f"sur (Putumayo, Caquetá), Andina (Antioquia, Huila, Tolima), "
            f"Costa Caribe, Orinoquía, Amazonía. "
            f"Devuelve 70-80 municipios. Campo 'i' máximo 90 caracteres. "
            f"TODO en ESPAÑOL. Devuelve SOLO JSON comenzando con {{ terminando con }}."}],
        tools=[{"type":"web_search_20250305","name":"web_search"}]
    )

    text = "".join(b.text for b in resp.content if b.type == "text")
    if not text:
        print("Sin respuesta — manteniendo datos existentes")
        return

    j0 = text.find('{')
    j1 = text.rfind('}')
    if j0 == -1 or j1 == -1:
        print("Sin JSON — manteniendo datos existentes")
        return

    try:
        data = json.loads(text[j0:j1+1])
    except json.JSONDecodeError as e:
        print(f"Error JSON: {e} — manteniendo datos existentes")
        return

    if not data.get('municipios'):
        print("Sin municipios — manteniendo datos existentes")
        return

    print(f"IA devolvió {len(data['municipios'])} municipios — aplicando rúbrica CoH...")
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

    # Ciudades sin eventos → mantener como VERDE
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
    print(f"ÉXITO: index.html actualizado — {fecha}")

if __name__ == "__main__":
    run()
