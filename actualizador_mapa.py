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

# ── US STATE DEPT CLASSIFICATION RULES ───────────────────────────────────────
# ORANGE can only be assigned to municipalities in these departments
RESTRICTED_DEPTS = {
    'cauca','valle del cauca','nariño','nario','antioquia',
    'chocó','choco','putumayo','huila','tolima','arauca',
    'norte de santander','caquetá','caqueta'
}

# These cities are EXCLUDED from orange even inside restricted departments
EXCLUDED_CITIES = {
    'popayán','popayan','cali','palmira','pasto',
    'medellín','medellin','bello','envigado',
    'itagüí','itagui','sabaneta','la estrella'
}

def enforce_rules(munis):
    """
    Enforce US State Dept classification rules regardless of what the AI returns.
    - RED: allowed anywhere (active combat)
    - ORANGE: only in restricted departments, only if NOT an excluded city
    - YELLOW: everything else with conflict activity
    - GREEN: no active conflict
    Also fixes negative latitudes for non-Amazon municipalities.
    """
    amazon_exceptions = ['leticia','leguizamo','puerto leguizamo']
    corrected = 0

    for m in munis:
        dept  = m.get('d', '').lower()
        name  = m.get('n', '').lower()
        risk  = m.get('r', 'yellow')

        # Fix negative latitudes (Colombia is mostly north of equator)
        if m.get('lat', 0) < -0.5:
            if not any(a in name for a in amazon_exceptions):
                m['lat'] = abs(m['lat'])

        # Enforce orange restriction rule
        if risk == 'orange':
            in_restricted = any(rd in dept for rd in RESTRICTED_DEPTS)
            is_excluded   = any(ec in name for ec in EXCLUDED_CITIES)
            if not in_restricted or is_excluded:
                m['r'] = 'yellow'
                corrected += 1
                print(f"  Rule correction: {m['n']} ({m['d']}) orange → yellow")

    if corrected:
        print(f"  {corrected} classification(s) corrected to comply with US State Dept rules")
    return munis

# ── SYSTEM PROMPT ─────────────────────────────────────────────────────────────
SYSTEM = """You are a humanitarian security analyst for Convoy of Hope Colombia.

Search recent news and classify armed conflict activity across ALL of Colombia.
Return ONLY a valid JSON object — no preamble, no markdown. Start with { and end with }.

Cover the ENTIRE country. Include conflict activity in all departments:
southwest (Cauca, Narino, Valle del Cauca), northeast (Norte de Santander, Arauca),
Pacific (Choco), south (Putumayo, Caqueta), Andean (Antioquia, Huila, Tolima),
Caribbean coast, and any other department with recent activity.

Return 65-75 municipalities. Keep every "i" field under 90 characters.

JSON structure:
{
  "fecha": "DD Mmm YYYY",
  "municipios": [
    {
      "n": "Municipality name",
      "d": "Department",
      "lat": 0.0,
      "lng": 0.0,
      "r": "red|orange|yellow|green",
      "sc": "score/25",
      "sz": 15,
      "i": "incident summary under 90 chars",
      "a": "active armed groups"
    }
  ]
}

Risk classification guidelines (the script will enforce these automatically):
- red: Active combat, direct attack, credible imminent threat — applies anywhere in Colombia
- orange: 3+ incidents/30 days, active armed presence within 30km — only in restricted departments
- yellow: 1-2 incidents/30 days, sporadic activity, OR any conflict outside restricted departments
- green: No active conflict indicators — stable cities and departments

Stable cities that should generally be green unless specific recent incidents:
Bogota, Barranquilla, Cartagena, Santa Marta, Bucaramanga, Armenia, Pereira,
Manizales, Ibague, Valledupar, Monteria, Sincelejo, Riohacha (unless specific incidents)

CRITICAL coordinate rule:
Colombia is mostly NORTH of the equator — latitudes are POSITIVE for almost all departments.
Cauca ~2-4N, Valle del Cauca ~3-5N, Narino ~0-2.5N, Antioquia ~5-8N.
Only Leticia (-4.2) and Puerto Leguizamo (-0.19) are legitimately negative."""

def run():
    if not API_KEY:
        print("ERROR: ANTHROPIC_API_KEY not set")
        sys.exit(1)

    now    = datetime.now(COLOMBIA_TZ)
    start  = now - timedelta(days=30)
    fecha  = f"{now.day} {MESES[now.month]} {now.year}"
    inicio = f"{start.day} {MESES[start.month]} {start.year}"

    print(f"Running update: {fecha}")
    print(f"Coverage window: {inicio} to {fecha}")

    if not HTML_FILE.exists():
        print(f"ERROR: {HTML_FILE} not found")
        sys.exit(1)

    html = HTML_FILE.read_text(encoding='utf-8')

    # Load existing municipalities as baseline
    existing_munis = []
    existing_match = re.search(r'const MUNIS = \[(.*?)\];', html, re.DOTALL)
    if existing_match:
        try:
            existing_munis = json.loads('[' + existing_match.group(1) + ']')
            print(f"Baseline: {len(existing_munis)} existing municipalities loaded")
        except Exception as e:
            print(f"Could not parse existing MUNIS: {e}")

    by_name = {m['n']: m for m in existing_munis}

    # Call API
    client = anthropic.Anthropic(api_key=API_KEY)
    resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=16000,
        system=SYSTEM,
        messages=[{"role":"user","content":
            f"Today is {fecha}. Search for armed conflict news across ALL of Colombia "
            f"from {inicio} to {fecha}. Cover every region — southwest, northeast, "
            f"Pacific coast, Amazon, Caribbean, Andean. Include active conflict zones "
            f"AND stable areas. Return 65-75 municipalities. "
            f"Keep every 'i' field under 90 characters. "
            f"Return ONLY valid JSON starting with {{ and ending with }}."}],
        tools=[{"type":"web_search_20250305","name":"web_search"}]
    )

    text = "".join(b.text for b in resp.content if b.type == "text")
    if not text:
        print("No text response — keeping existing data unchanged")
        return

    json_start = text.find('{')
    json_end   = text.rfind('}')
    if json_start == -1 or json_end == -1:
        print("No JSON found — keeping existing data unchanged")
        return

    try:
        data = json.loads(text[json_start:json_end+1])
    except json.JSONDecodeError as e:
        print(f"JSON parse error: {e} — keeping existing data unchanged")
        return

    # Process and merge
    if data.get('municipios'):
        ai_munis = enforce_rules(data['municipios'])
        updated = added = 0
        for m in ai_munis:
            if m['n'] in by_name:
                by_name[m['n']] = m
                updated += 1
            else:
                by_name[m['n']] = m
                added += 1
        print(f"Merged: {updated} updated + {added} new = {len(by_name)} total")

    merged = list(by_name.values())
    counts = {r: sum(1 for x in merged if x.get('r') == r)
              for r in ('red','orange','yellow','green')}
    print(f"Final: RED={counts['red']} | ORANGE={counts['orange']} | YELLOW={counts['yellow']} | GREEN={counts['green']}")

    # Update HTML
    inner = json.dumps(merged, ensure_ascii=False)[1:-1]
    html  = re.sub(r'(const MUNIS = \[).*?(\];)',
                   f'\\g<1>\n{inner}\n\\g<2>', html, flags=re.DOTALL)

    if data.get('fecha'):
        html = re.sub(r'(<em id="last-updated">)[^<]*(</em>)',
                      f'\\g<1>{data["fecha"]}\\g<2>', html)

    HTML_FILE.write_text(html, encoding='utf-8')
    print(f"SUCCESS: index.html updated for {fecha}")

if __name__ == "__main__":
    run()
