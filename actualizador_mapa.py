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

# ── COH SCORING SYSTEM (Section 3, Contingency Plan v3.0) ────────────────────
# Likelihood × Impact = Risk Score → Zone
# L1=Very Unlikely, L2=Unlikely/isolated, L3=Possible/periodic,
# L4=Likely/frequent, L5=Almost Certain/active conflict
# I1=Negligible(<1d), I2=Minor/first aid(1-3d), I3=Moderate/medevac(1-4wk),
# I4=Major/life-threatening(1-3mo), I5=Catastrophic/fatalities
# Score 1-4   → LOW    → GREEN
# Score 5-9   → MEDIUM → YELLOW
# Score 10-16 → HIGH   → ORANGE (restricted depts only, excl. cities below)
# Score 17-25 → CRITICAL → RED

def score_to_zone(score):
    if score >= 17: return 'red'
    if score >= 10: return 'orange'
    if score >= 5:  return 'yellow'
    return 'green'

def score_to_size(score):
    """Map risk score to circle size (10-22)."""
    if score >= 20: return 22
    if score >= 17: return 20
    if score >= 14: return 18
    if score >= 10: return 16
    if score >= 7:  return 14
    if score >= 5:  return 12
    return 10

# ── US STATE DEPT ORANGE RESTRICTION ─────────────────────────────────────────
# ORANGE requires score 10-16 AND being in a restricted dept (not excluded city)
# RED (17-25) applies anywhere — no department restriction
RESTRICTED_DEPTS = {
    'cauca','valle del cauca','nariño','nario','antioquia',
    'chocó','choco','putumayo','huila','tolima','arauca',
    'norte de santander','caquetá','caqueta'
}
EXCLUDED_CITIES = {
    'popayán','popayan','cali','palmira','pasto',
    'medellín','medellin','bello','envigado',
    'itagüí','itagui','sabaneta','la estrella'
}

def enforce_rules(munis):
    """
    Step 1 — Recalculate zone from L×I score (source of truth).
    Step 2 — Apply US State Dept orange restriction.
    Step 3 — Fix coordinate errors (negative lat in non-Amazon depts).
    """
    amazon = ['leticia','leguizamo','puerto leguizamo']
    corrections = []

    for m in munis:
        dept = m.get('d','').lower()
        name = m.get('n','').lower()

        # ── Fix latitude sign ──────────────────────────────────────────
        if m.get('lat', 0) < -0.5:
            if not any(a in name for a in amazon):
                m['lat'] = abs(m['lat'])

        # ── Recalculate zone from score ───────────────────────────────
        L = int(m.get('L', 0))
        I = int(m.get('I', 0))
        if L > 0 and I > 0:
            score = L * I
            m['score'] = score
            m['sc']    = f"L{L}×I{I}={score}/25"
            m['sz']    = score_to_size(score)
            computed_zone = score_to_zone(score)

            # ── Apply orange restriction rule ─────────────────────────
            if computed_zone == 'orange':
                in_restricted = any(rd in dept for rd in RESTRICTED_DEPTS)
                is_excluded   = any(ec in name for ec in EXCLUDED_CITIES)
                if not in_restricted or is_excluded:
                    computed_zone = 'yellow'
                    corrections.append(
                        f"{m['n']} ({m['d']}): score {score} → YELLOW "
                        f"(dept not restricted or excluded city)"
                    )

            m['r'] = computed_zone
        else:
            # AI didn't provide L/I — fall back to existing zone
            pass

    if corrections:
        print(f"  US State Dept rule corrections
