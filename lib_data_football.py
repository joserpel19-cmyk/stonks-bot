# lib_data_football.py
# ----------------------------------------------------------------------
# Cliente para football-data.org (free tier).
#   - Cuenta gratuita: 10 requests/min, 100/día
#   - Cubre top ligas europeas: PL, PD, SA, BL1, FL1, DED, PPL, ELC + CL/EL
#   - Necesitas registrarte en https://www.football-data.org/client/register
#     y poner el token en la env var FOOTBALL_DATA_KEY
#
# Lo usamos para:
#   1) Backfill de resultados históricos (alimenta el Elo)
#   2) Resultados del día anterior para mantener Elo al día
#   3) Próximos partidos con head-to-head (para validar match_id de The Odds API)
# ----------------------------------------------------------------------
from __future__ import annotations
import os
import time
import json
import datetime as dt
from typing import List, Dict, Optional

try:
    import requests  # ya está en requirements.txt
except ImportError:
    requests = None

BASE = "https://api.football-data.org/v4"

# Mapeo de claves de The Odds API a códigos de competición de football-data.org
LIGA_MAP = {
    "soccer_epl":                     "PL",   # Premier League
    "soccer_spain_la_liga":           "PD",   # Primera División
    "soccer_italy_serie_a":           "SA",   # Serie A
    "soccer_germany_bundesliga":      "BL1",  # Bundesliga
    "soccer_france_ligue_one":        "FL1",  # Ligue 1
    "soccer_netherlands_eredivisie":  "DED",  # Eredivisie
    "soccer_portugal_primeira_liga":  "PPL",  # Primeira Liga
    "soccer_efl_champ":               "ELC",  # Championship inglesa
    "soccer_uefa_champs_league":      "CL",   # Champions
    "soccer_uefa_europa_league":      "EL",   # Europa League
}

# Cooldown sencillo para no exceder 10 req/min
_LAST_CALL = [0.0]
_COOLDOWN = 6.5  # segundos -> ~9 req/min de margen


def _rate_limit():
    elapsed = time.time() - _LAST_CALL[0]
    if elapsed < _COOLDOWN:
        time.sleep(_COOLDOWN - elapsed)
    _LAST_CALL[0] = time.time()


def _get_token() -> Optional[str]:
    return os.environ.get("FOOTBALL_DATA_KEY", "").strip() or None


def disponible() -> bool:
    """Hay token y librería requests cargada."""
    return _get_token() is not None and requests is not None


def _get(path: str, params: dict | None = None) -> Optional[dict]:
    if not disponible():
        return None
    _rate_limit()
    headers = {"X-Auth-Token": _get_token()}
    try:
        r = requests.get(BASE + path, headers=headers, params=params or {}, timeout=20)
    except Exception as e:
        print(f"   [football-data] error red: {e}")
        return None
    if r.status_code == 429:
        print("   [football-data] rate limit, esperando 60s...")
        time.sleep(60)
        return _get(path, params)
    if r.status_code != 200:
        print(f"   [football-data] HTTP {r.status_code} en {path}")
        return None
    try:
        return r.json()
    except Exception:
        return None


# ---- Endpoints ----
def partidos_finalizados(
    competicion: str, dias_atras: int = 7, dias_adelante: int = 0
) -> List[Dict]:
    """Lista partidos terminados (status=FINISHED) de una competición.
    competicion: código football-data (PL, PD, SA, ...).
    """
    hoy = dt.date.today()
    desde = (hoy - dt.timedelta(days=dias_atras)).isoformat()
    hasta = (hoy + dt.timedelta(days=dias_adelante)).isoformat()
    data = _get(
        f"/competitions/{competicion}/matches",
        {"dateFrom": desde, "dateTo": hasta, "status": "FINISHED"},
    )
    if not data:
        return []
    out = []
    for m in data.get("matches", []):
        try:
            full = m["score"]["fullTime"]
            gh = full.get("home")
            ga = full.get("away")
            if gh is None or ga is None:
                continue
            out.append({
                "id":       str(m["id"]),
                "fecha":    m.get("utcDate"),
                "home":     m["homeTeam"]["name"],
                "away":     m["awayTeam"]["name"],
                "goles_h":  int(gh),
                "goles_a":  int(ga),
                "competicion": competicion,
            })
        except Exception:
            continue
    return out


def head_to_head(equipo_id: int, n: int = 5) -> List[Dict]:
    """Últimos N partidos de un equipo (cualquier competición)."""
    data = _get(f"/teams/{equipo_id}/matches", {"limit": n, "status": "FINISHED"})
    if not data:
        return []
    return data.get("matches", [])


def temporada_completa(competicion: str, season_year: int) -> List[Dict]:
    """Backfill: todos los partidos terminados de una temporada.
    season_year = año de inicio (2025 para temporada 25/26)."""
    data = _get(
        f"/competitions/{competicion}/matches",
        {"season": str(season_year), "status": "FINISHED"},
    )
    if not data:
        return []
    out = []
    for m in data.get("matches", []):
        try:
            full = m["score"]["fullTime"]
            gh = full.get("home")
            ga = full.get("away")
            if gh is None or ga is None:
                continue
            out.append({
                "id":       str(m["id"]),
                "fecha":    m.get("utcDate"),
                "home":     m["homeTeam"]["name"],
                "away":     m["awayTeam"]["name"],
                "goles_h":  int(gh),
                "goles_a":  int(ga),
                "competicion": competicion,
            })
        except Exception:
            continue
    return out


def equipo_por_nombre_aproximado(nombre: str, candidatos: List[str]) -> Optional[str]:
    """Búsqueda fuzzy muy básica para reconciliar nombres entre The Odds API y
    football-data.org (ej. 'Manchester City' vs 'Manchester City FC')."""
    nm = nombre.strip().lower()
    # Coincidencia exacta
    for c in candidatos:
        if c.strip().lower() == nm:
            return c
    # Coincidencia parcial (uno contiene al otro)
    for c in candidatos:
        cl = c.strip().lower()
        if nm in cl or cl in nm:
            return c
    # Por palabras significativas (>=4 chars)
    palabras = [p for p in nm.split() if len(p) >= 4]
    for c in candidatos:
        cl = c.strip().lower()
        if any(p in cl for p in palabras):
            return c
    return None


# ---- Test directo ----
if __name__ == "__main__":
    if not disponible():
        print("AVISO: define FOOTBALL_DATA_KEY en variables de entorno para probar.")
        raise SystemExit(0)
    print(">>> Premier League, partidos últimos 7 días:")
    ms = partidos_finalizados("PL", dias_atras=7)
    for m in ms[:5]:
        print(f"  {m['fecha'][:10]} {m['home']} {m['goles_h']}-{m['goles_a']} {m['away']}")
    print(f"Total: {len(ms)}")
