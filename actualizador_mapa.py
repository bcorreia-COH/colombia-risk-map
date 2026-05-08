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

SYSTEM = """You are a humanitarian security analyst for Colombia. Return ONLY a valid JSON object — no preamble, no markdown, start with { and end with }.

Return municipalities across ALL four risk levels. You MUST include yellow and green zones — not just red and orange. A complete response has at least 15 red, 25 orange, 20 yellow, and 7 green municipalities.

JSON structure:
{
  "fecha": "DD Mmm YYYY",
  "municipios": [
    {"n":"Name","d":"Department","lat":0.0,"lng":0.0,"r":"red","sc":"20/25","sz":18,"i":"incident description max 200 chars","a":"armed actors"}
  ]
}

Risk levels:
- red: active combat, direct threat, access collapsed
- orange: 3+ incidents/30 days, active armed presence within 30km, AND in a US State Dept restricted department (Cauca excl Popayan, Valle del Cauca excl Cali/Palmira, Narino excl Pasto, Antioquia excl Metro Medellin, Choco, Putumayo, Huila, Tolima, Arauca, Norte de Santander, within 30km Venezuelan border, Caqueta)
- yellow: 1-2 incidents/30 days, sporadic activity within 50km, OR excluded cities in restricted departments
- green: no active conflict indicators

CRITICAL coordinate rule: Colombia is mostly NORTH of the equator. Cauca, Valle del Cauca, Narino, Antioquia all have POSITIVE latitudes. Only extreme Amazon south (Leticia -4.2, Puerto Leguizamo -0.19) are negative."""

def run():
    if not API_KEY:
        print("ERROR: ANTHROPIC_API_KEY not set")
        sys.exit(1)

    now    = datetime.now(COLOMBIA_TZ)
    start  = now - timedelta(days=30)
    fecha  = f"{now.day} {MESES[now.month]} {now.year}"
    inicio = f"{start.day} {MESES[start.month]} {start.year}"

    print(f"Running update: {fecha}")
    client = anthropic.Anthropic(api_key=API_KEY)

    resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=8000,
        system=SYSTEM,
        messages=[{"role":"user","content":
            f"Today is {fecha}. Search for Colombia armed conflict news from {inicio} to {fecha}. "
            f"Focus on Cauca, Narino, Valle del Cauca but cover the whole country. "
            f"Include ALL risk levels: red, orange, yellow AND green municipalities. "
            f"Stable cities like Bogota, Barranquilla, Cartagena, Medellin city, Cali, Pasto, Bucaramanga, Armenia, Pereira, Manizales should be yellow or green. "
            f"Return ONLY the JSON object starting with {{ and ending with }}."}],
        tools=[{"type":"web_search_20250305","name":"web_search"}]
    )

    text = "".join(b.text for b in resp.content if b.type == "text")
    if not text:
        print("No text response received")
        sys.exit(1)

    json_start = text.find('{')
    json_end   = text.rfind('}')
    if json_start == -1 or json_end == -1:
        print(f"No JSON found: {text[:300]}")
        sys.exit(1)
    clean = text[json_start:json_end+1]

    try:
        data = json.loads(clean)
    except json.JSONDecodeError as e:
        print(f"JSON parse error: {e}")
        print(f"Response: {clean[:300]}")
        sys.exit(1)

    if not HTML_FILE.exists():
        print(f"ERROR: {HTML_FILE} not found")
        sys.exit(1)

    html = HTML_FILE.read_text(encoding='utf-8')

    # Update date
    if data.get('fecha'):
        html = re.sub(r'(<em id="last-updated">)[^<]*(</em>)',
                      f'\\g<1>{data["fecha"]}\\g<2>', html)

    # Update municipalities only — roads stay fixed
    if data.get('municipios'):
        munis = data['municipios']
        # Fix any negative latitudes in non-Amazon departments
        amazon_only = ['leticia','puerto leguizamo','leguizamo']
        for m in munis:
            name_lower = m.get('n','').lower()
            if m.get('lat', 0) < -0.5 and not any(a in name_lower for a in amazon_only):
                m['lat'] = abs(m['lat'])
        inner = json.dumps(munis, ensure_ascii=False)[1:-1]
        html  = re.sub(r'(const MUNIS = \[).*?(\];)',
                       f'\\g<1>\n{inner}\n\\g<2>', html, flags=re.DOTALL)
        counts = {r: sum(1 for m in munis if m.get('r')==r)
                  for r in ('red','orange','yellow','green')}
        print(f"Municipalities: {counts}")

    HTML_FILE.write_text(html, encoding='utf-8')
    print(f"SUCCESS: index.html updated for {fecha}")

if __name__ == "__main__":
    run()
