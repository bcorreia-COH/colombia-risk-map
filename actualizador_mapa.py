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

IMPORTANT: Keep incident descriptions under 80 characters. Return exactly 50 municipalities total across all risk levels.

Required distribution: ~12 red, ~18 orange, ~13 yellow, ~7 green.

JSON structure:
{
  "fecha": "DD Mmm YYYY",
  "municipios": [
    {"n":"Name","d":"Department","lat":0.0,"lng":0.0,"r":"red","sc":"20/25","sz":18,"i":"short incident description under 80 chars","a":"armed actors"}
  ]
}

Risk levels:
- red: active combat or direct threat
- orange: 3+ incidents/30 days in US State Dept restricted dept (Cauca excl Popayan, Valle del Cauca excl Cali/Palmira, Narino excl Pasto, Antioquia excl Metro Medellin, Choco, Putumayo, Huila, Tolima, Arauca, Norte de Santander, Caqueta)
- yellow: 1-2 incidents or excluded cities (Popayan, Cali, Palmira, Pasto, Medellin)
- green: no active conflict (Bogota, Barranquilla, Cartagena, Santa Marta, Bucaramanga, Armenia, Pereira)

CRITICAL: Cauca, Valle del Cauca, Narino, Antioquia all have POSITIVE latitudes (north of equator)."""

def run():
    if not API_KEY:
        print("ERROR: ANTHROPIC_API_KEY not set")
        sys.exit(1)

    now    = datetime.now(COLOMBIA_TZ)
    start  = now - timedelta(days=30)
    fecha  = f"{now.day} {MESES[now.month]} {now.year}"
    inicio = f"{start.day} {MESES[start.month]} {start.year}"
