#!/usr/bin/env python3
"""
actualizador_mapa.py — Actualización automática del Mapa de Riesgo CoH Colombia
=================================================================================
Ejecuta cada domingo a las 23:00 hora de Colombia (UTC-5) vía GitHub Actions.
Usa la API de Anthropic para buscar incidentes recientes y actualiza index.html.

Requisitos:
    pip install anthropic

Variables de entorno requeridas:
    ANTHROPIC_API_KEY   — Clave de API de Anthropic
    GOOGLE_MAPS_API_KEY — Opcional; si no se configura usa la clave por defecto
"""

import os
import json
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

try:
    import anthropic
except ImportError:
    print("ERROR: Instale el paquete anthropic: pip install anthropic")
    sys.exit(1)

# ── CONFIGURACIÓN ─────────────────────────────────────────────────────────────
COLOMBIA_TZ = timezone(timedelta(hours=-5))
SCRIPT_DIR  = Path(__file__).parent
HTML_FILE   = SCRIPT_DIR / "index.html"

ANTHROPIC_API_KEY   = os.environ.get("ANTHROPIC_API_KEY", "")
GOOGLE_MAPS_API_KEY = os.environ.get(
    "GOOGLE_MAPS_API_KEY",
    "AIzaSyCsAOTCzt_lrkw242navnK0A5suqWJfEwM"   # fallback to real key
)

# ── COLOMBIA COORDINATE BOUNDS ────────────────────────────────────────────────
COL_LAT_MIN, COL_LAT_MAX = -4.5, 12.6
COL_LNG_MIN, COL_LNG_MAX = -79.5, -66.5

# Departments that are definitively NORTH of the equator (lat must be positive)
DEPTS_NORTE = {
    'cauca','valle del cauca','nariño','nario','antioquia','chocó','choco',
    'córdoba','cordoba','arauca','norte de santander','santander','boyacá',
    'boyaca','cundinamarca','tolima','huila','risaralda','caldas','quindío',
    'quindio','meta','vichada','guainía','guainia','vaupés','vaupes',
    'bolívar','bolivar','sucre','atlántico','atlantico','magdalena','cesar',
    'la guajira','guajira','caquetá','caqueta'
}

MESES_ES = {1:'ene',2:'feb',3:'mar',4:'abr',5:'may',6:'jun',
            7:'jul',8:'ago',9:'sep',10:'oct',11:'nov',12:'dic'}

# ── PROMPTS ───────────────────────────────────────────────────────────────────
SYSTEM_PROMPT = """Eres un analista de seguridad humanitaria especializado en Colombia para Convoy of Hope.
Analiza noticias recientes sobre conflicto armado y genera datos JSON actualizados para el mapa de riesgo.

REGLAS CRÍTICAS — COORDENADAS:
Colombia está principalmente en el hemisferio NORTE. Latitudes POSITIVAS para casi todo el país.
  Cauca:        1°N a 4°N   → lat +1.0 a +4.0  (NUNCA negativo)
  Valle del Cauca: 3°N a 5°N → lat +3.0 a +5.0 (NUNCA negativo)
  Nariño:       0°N a 2.5°N → lat 0.0 a +2.5   (NUNCA negativo salvo Amazonia extrema)
  Ciudades:     Cali=+3.45  Popayán=+2.44  Buenaventura=+3.88  Pasto=+1.21
  ÚNICO caso negativo válido: Leticia (-4.2°), Puerto Leguízamo (-0.19°)

Clasificación de riesgo (criterios CoH):
  ROJO   (red):    Combate activo, amenaza directa, acceso colapsado, 3+ incidentes/30d
  NARANJA (orange): 3+ incidentes/30d, presencia armada <30km
  AMARILLO (yellow): 1-2 incidentes/30d, actividad esporádica <50km
  VERDE  (green):  Sin indicadores de conflicto activo

Responde ÚNICAMENTE con JSON válido. Sin texto adicional. Sin bloques markdown.

{
  "fecha_actualizacion": "DD Mmm YYYY",
  "ventana_inicio": "DD Mmm YYYY",
  "ventana_fin":    "DD Mmm YYYY",
  "estadisticas": {
    "masacres_total": <número>,
    "muertos_total":  <número>,
    "fuente_estadisticas": "<fuente>"
  },
  "municipios": [
    {
      "n":  "<nombre>",
      "d":  "<departamento>",
      "lat": <latitud — positiva para casi toda Colombia>,
      "lng": <longitud — siempre negativa en Colombia>,
      "r":  "<red|orange|yellow|green>",
      "sc": "<puntuación>/25",
      "sz": <10-24>,
      "i":  "<descripción incidentes, máx 250 chars>",
      "a":  "<actores armados>"
    }
  ],
  "vias": [
    {
      "nombre": "<nombre vía>",
      "r":      "<red|orange|yellow>",
      "c":      [[lat1,lng1],[lat2,lng2],...]
    }
  ]
}

Incluir mínimo 60 municipios. Priorizar: Cauca, Nariño, Valle del Cauca.
También incluir zonas activas: Arauca, Norte de Santander, Chocó, Putumayo, Antioquia."""


def get_hoy():
    return datetime.now(COLOMBIA_TZ)

def fmt(dt):
    return f"{dt.day} {MESES_ES[dt.month]} {dt.year}"

def validar_municipios(munis: list) -> list:
    """Corrige coordenadas erróneas — principalmente signo negativo equivocado."""
    corregidos = 0
    for m in munis:
        dept = m.get('d','').lower()
        lat  = m.get('lat', 0)
        lng  = m.get('lng', 0)
        # Corregir lat negativa en departamentos conocidamente norteños
        if any(d in dept for d in DEPTS_NORTE) and lat < -0.3:
            print(f"  ⚠  Corregido: {m['n']} ({m['d']}) lat {lat:.4f} → {abs(lat):.4f}")
            m['lat'] = abs(lat)
            corregidos += 1
        # Bounds check
        if not (COL_LAT_MIN <= m.get('lat',0) <= COL_LAT_MAX):
            print(f"  ✗  Lat fuera de Colombia: {m['n']} lat={m.get('lat')}")
        if not (COL_LNG_MIN <= lng <= COL_LNG_MAX):
            print(f"  ✗  Lng fuera de Colombia: {m['n']} lng={lng}")
    if corregidos:
        print(f"  → {corregidos} coordenada(s) corregida(s)")
    return munis

def validar_vias(vias: list) -> list:
    """Corrige puntos de vías con latitudes erróneas."""
    for v in vias:
        corr = []
        for pt in v.get('c',[]):
            lat, lng = pt[0], pt[1]
            # Zona andina/pacífica: si lat ligeramente negativa y lng andina → probable error
            if -2.5 < lat < -0.3 and -78 < lng < -74:
                lat = abs(lat)
            corr.append([lat, lng])
        v['c'] = corr
    return vias

def fetch_data() -> dict:
    print("Conectando con la API de Anthropic...")
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    hoy    = get_hoy()
    inicio = hoy - timedelta(days=30)
    prompt = (
        f"Hoy es {fmt(hoy)} (hora de Colombia).\n\n"
        f"Analiza noticias recientes sobre conflicto armado en Colombia "
        f"(ventana: {fmt(inicio)} — {fmt(hoy)}).\n\n"
        "Busca: masacres, ataques, atentados de FARC-EMC, ELN, Clan del Golfo, "
        "Segunda Marquetalia; incidentes en vías principales; alertas de la Defensoría; "
        "paros armados, confinamientos, desplazamientos masivos.\n\n"
        "IMPORTANTE: Coordenadas POSITIVAS para Cauca, Valle del Cauca, Nariño "
        "y demás departamentos al norte del ecuador.\n\n"
        f"Fecha de actualización: {fmt(hoy)}\n"
        f"Ventana 30 días: {fmt(inicio)} — {fmt(hoy)}"
    )

    print("Buscando inteligencia de conflicto reciente...")
    resp = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=8000,
        system=SYSTEM_PROMPT,
        messages=[{"role":"user","content":prompt}],
        tools=[{"type":"web_search_20250305","name":"web_search"}]
    )

    texto = "".join(b.text for b in resp.content if b.type == "text")
    if not texto:
        raise ValueError("La API no retornó contenido de texto.")

    clean = re.sub(r'^```(?:json)?\s*','',texto.strip())
    clean = re.sub(r'\s*```$','',clean)
    data  = json.loads(clean)

    print("Validando coordenadas...")
    if 'municipios' in data:
        data['municipios'] = validar_municipios(data['municipios'])
    if 'vias' in data:
        data['vias'] = validar_vias(data['vias'])

    return data

def update_html(data: dict) -> bool:
    if not HTML_FILE.exists():
        print(f"ERROR: No se encontró {HTML_FILE}")
        return False

    html = HTML_FILE.read_text(encoding='utf-8')

    # Fecha y ventana en el header
    if 'fecha_actualizacion' in data:
        html = re.sub(
            r'(<em id="last-updated">)[^<]*(</em>)',
            f'\\g<1>{data["fecha_actualizacion"]}\\g<2>', html
        )
    if 'ventana_inicio' in data and 'ventana_fin' in data:
        html = re.sub(
            r'Ventana: [^<&"]+',
            f'Ventana: {data["ventana_inicio"]} – {data["ventana_fin"]}', html
        )

    # Estadísticas
    s = data.get('estadisticas',{})
    if s.get('masacres_total') and s.get('muertos_total'):
        html = re.sub(
            r'\d+ masacres / \d+ muertos',
            f'{s["masacres_total"]} masacres / {s["muertos_total"]} muertos', html
        )

    # Bloque MUNIS
    if data.get('municipios'):
        inner = json.dumps(data['municipios'], ensure_ascii=False, indent=0)[1:-1].strip()
        html  = re.sub(r'(const MUNIS = \[).*?(\];)',
                       f'\\g<1>\n{inner}\n\\g<2>', html, flags=re.DOTALL)
        counts  = {z:sum(1 for m in data['municipios'] if m.get('r')==z)
                   for z in ('red','orange','yellow','green')}
        id_map  = {'red':'cnt-red','orange':'cnt-orange',
                   'yellow':'cnt-yellow','green':'cnt-green'}
        for z,cnt in counts.items():
            html = re.sub(f'(<span class="sn" id="{id_map[z]}">)[^<]*(</span>)',
                          f'\\g<1>{cnt}\\g<2>', html)

    # Bloque VIAS
    if data.get('vias'):
        inner = json.dumps(data['vias'], ensure_ascii=False, indent=0)[1:-1].strip()
        html  = re.sub(r'(const VIAS = \[).*?(\];)',
                       f'\\g<1>\n{inner}\n\\g<2>', html, flags=re.DOTALL)

    # Garantizar clave de Google Maps
    html = re.sub(
        r'window\.GOOGLE_MAPS_API_KEY\s*=\s*"[^"]*";',
        f'window.GOOGLE_MAPS_API_KEY = "{GOOGLE_MAPS_API_KEY}";',
        html
    )

    HTML_FILE.write_text(html, encoding='utf-8')
    print(f"✅ {HTML_FILE} actualizado.")
    return True


def main():
    print("="*60)
    print("Mapa de Riesgo CoH Colombia — Actualizador Automático")
    print(f"Ejecución: {get_hoy().strftime('%d/%m/%Y %H:%M')} COT")
    print("="*60)

    if not ANTHROPIC_API_KEY:
        print("ERROR: Variable ANTHROPIC_API_KEY no configurada.")
        sys.exit(1)

    try:
        data = fetch_data()
        print(f"Datos: {len(data.get('municipios',[]))} municipios, "
              f"{len(data.get('vias',[]))} vías")
        if not update_html(data):
            sys.exit(1)
        prox = get_hoy() + timedelta(days=7)
        print(f"\nPróxima actualización: {prox.strftime('%d/%m/%Y')} 23:00 COT")
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback; traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()
