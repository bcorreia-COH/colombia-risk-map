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

SYSTEM = """You are a humanitarian security analyst for Colombia. Search for recent armed conflict news and return ONLY a valid JSON object with this exact structure - no markdown, no preamble, no extra text, start your response with { and end with }:
{
  "fecha": "DD Mmm YYYY",
  "municipios": [
    {"n":"Name","d":"Department","lat":0.0,"lng":0.0,"r":"red","sc":"20/25","sz":18,"i":"incident description max 200 chars","a":"armed actors"}
  ],
  "vias": [
    {"nombre":"Road name","r":"red","c":[[lat1,lng1],[lat2,lng2]]}
  ]
}
Risk levels: red=active combat, orange=foreigners restricted, yellow=cautionary, green=permissive.
CRITICAL: Colombian municipalities have POSITIVE latitudes. Cauca, Valle del Cauca, Narino are north of the equator."""

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
            f"Today is {fecha}. Search for Colombia armed conflict incidents from {inicio} to {fecha}. "
            f"Focus on Cauca, Narino, Valle del Cauca but include all active zones. "
            f"Return ONLY the JSON object. Start your response with {{ and end with }}. No other text."}],
        tools=[{"type":"web_search_20250305","name":"web_search"}]
    )

    text = "".join(b.text for b in resp.content if b.type == "text")
    if not text:
        print("No text response received")
        sys.exit(1)

    # Extract JSON from response even if there is surrounding text
    json_start = text.find('{')
    json_end   = text.rfind('}')
    if json_start == -1 or json_end == -1:
        print(f"No JSON found in response: {text[:300]}")
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

    if data.get('fecha'):
        html = re.sub(r'(<em id="last-updated">)[^<]*(</em>)',
                      f'\\g<1>{data["fecha"]}\\g<2>', html)

    if data.get('municipios'):
        inner = json.dumps(data['municipios'], ensure_ascii=False)[1:-1]
        html  = re.sub(r'(const MUNIS = \[).*?(\];)',
                       f'\\g<1>\n{inner}\n\\g<2>', html, flags=re.DOTALL)

    if data.get('vias'):
        inner = json.dumps(data['vias'], ensure_ascii=False)[1:-1]
        html  = re.sub(r'(const SEGMENTS = \[).*?(\];)',
                       f'\\g<1>\n{inner}\n\\g<2>', html, flags=re.DOTALL)

    HTML_FILE.write_text(html, encoding='utf-8')
    print(f"SUCCESS: index.html updated for {fecha}")

if __name__ == "__main__":
    run()
