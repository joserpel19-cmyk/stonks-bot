# lib_elo.py
# ----------------------------------------------------------------------
# Modelo Elo + Poisson para fútbol.
#
# 1) Cada equipo tiene un rating Elo. Diferencia Elo -> probabilidad de
#    victoria del local (incluyendo ventaja de campo).
# 2) Combinamos Elo con un modelo de goles esperados (xG) basado en la
#    fuerza ofensiva y defensiva de cada equipo (Dixon-Coles simplificado).
# 3) Generamos un Poisson para producir probabilidades 1X2 (también BTTS,
#    Over/Under si quisiéramos).
#
# Persistencia: model_state.json
# ----------------------------------------------------------------------
from __future__ import annotations
import json
import math
import os
from typing import Dict, Tuple, List

# ---- Parámetros del modelo ----
ELO_INICIAL          = 1500.0
ELO_K                = 20.0          # tasa de aprendizaje
ELO_HFA              = 65.0          # ventaja de jugar en casa (puntos Elo)
ELO_GOAL_BONUS       = True          # ajuste por margen de goles (FiveThirtyEight-style)

# Goles esperados por liga (medias históricas, ajustables luego)
LIGA_GOLES_HOME = {
    "soccer_epl":               1.55,
    "soccer_spain_la_liga":     1.45,
    "soccer_italy_serie_a":     1.45,
    "soccer_germany_bundesliga":1.65,
    "soccer_france_ligue_one":  1.50,
    "soccer_netherlands_eredivisie": 1.75,
    "soccer_portugal_primeira_liga": 1.50,
    "default":                  1.50,
}
LIGA_GOLES_AWAY = {
    "soccer_epl":               1.20,
    "soccer_spain_la_liga":     1.10,
    "soccer_italy_serie_a":     1.20,
    "soccer_germany_bundesliga":1.40,
    "soccer_france_ligue_one":  1.15,
    "soccer_netherlands_eredivisie": 1.40,
    "soccer_portugal_primeira_liga": 1.15,
    "default":                  1.20,
}

# Cuántos puntos Elo equivalen a un gol de diferencia (calibrado FiveThirtyEight)
ELO_PER_GOAL = 100.0

DIR = os.path.dirname(os.path.abspath(__file__))
MODEL_PATH = os.path.join(DIR, "model_state.json")


# ---- Persistencia ----
def cargar_modelo() -> dict:
    if not os.path.exists(MODEL_PATH):
        return {"elo": {}, "ultima_actualizacion": None, "partidos_procesados": []}
    try:
        with open(MODEL_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"elo": {}, "ultima_actualizacion": None, "partidos_procesados": []}


def guardar_modelo(modelo: dict) -> None:
    with open(MODEL_PATH, "w", encoding="utf-8") as f:
        json.dump(modelo, f, indent=2, ensure_ascii=False)


def get_elo(modelo: dict, equipo: str, liga: str | None = None) -> float:
    key = _team_key(equipo, liga)
    return modelo["elo"].get(key, ELO_INICIAL)


def set_elo(modelo: dict, equipo: str, valor: float, liga: str | None = None) -> None:
    key = _team_key(equipo, liga)
    modelo["elo"][key] = round(float(valor), 2)


def _team_key(equipo: str, liga: str | None) -> str:
    """Normaliza el nombre del equipo (case-insensitive, trim).
    Si se da liga, scope por liga para evitar colisiones (ej: River Plate)."""
    nm = (equipo or "").strip().lower()
    if liga:
        return f"{liga}::{nm}"
    return nm


# ---- Update Elo con resultado ----
def expected_score(elo_a: float, elo_b: float, hfa: float = 0.0) -> float:
    """Probabilidad esperada de que A gane sobre B (sin empates)."""
    diff = (elo_a + hfa) - elo_b
    return 1.0 / (1.0 + 10 ** (-diff / 400.0))


def actualizar_con_resultado(
    modelo: dict,
    home: str,
    away: str,
    goles_home: int,
    goles_away: int,
    liga: str | None = None,
    match_id: str | None = None,
) -> Tuple[float, float]:
    """Actualiza el Elo de los dos equipos tras un resultado.
    Devuelve (delta_home, delta_away)."""

    # Idempotencia: si ya procesado, no hacer nada
    if match_id:
        if match_id in modelo.get("partidos_procesados", []):
            return 0.0, 0.0

    elo_h = get_elo(modelo, home, liga)
    elo_a = get_elo(modelo, away, liga)

    # Resultado real: 1 = home gana, 0.5 = empate, 0 = away gana
    if goles_home > goles_away:
        s_h = 1.0
    elif goles_home < goles_away:
        s_h = 0.0
    else:
        s_h = 0.5
    s_a = 1.0 - s_h

    # Esperado
    e_h = expected_score(elo_h, elo_a, hfa=ELO_HFA)
    e_a = 1.0 - e_h

    # Multiplicador por margen de goles (FiveThirtyEight)
    if ELO_GOAL_BONUS:
        gd = abs(goles_home - goles_away)
        if gd <= 1:
            mult = 1.0
        elif gd == 2:
            mult = 1.5
        elif gd == 3:
            mult = 1.75
        else:
            mult = 1.75 + (gd - 3) / 8.0
    else:
        mult = 1.0

    delta_h = ELO_K * mult * (s_h - e_h)
    delta_a = ELO_K * mult * (s_a - e_a)

    set_elo(modelo, home, elo_h + delta_h, liga)
    set_elo(modelo, away, elo_a + delta_a, liga)

    if match_id:
        modelo.setdefault("partidos_procesados", []).append(match_id)
        # límite de tamaño para no inflar el JSON
        if len(modelo["partidos_procesados"]) > 50000:
            modelo["partidos_procesados"] = modelo["partidos_procesados"][-30000:]

    return delta_h, delta_a


# ---- Predicción ----
def goles_esperados(elo_h: float, elo_a: float, liga: str | None = None) -> Tuple[float, float]:
    """Lambda (goles esperados) para Poisson, ajustado por Elo y ventaja local."""
    base_h = LIGA_GOLES_HOME.get(liga, LIGA_GOLES_HOME["default"])
    base_a = LIGA_GOLES_AWAY.get(liga, LIGA_GOLES_AWAY["default"])

    # Diferencia Elo + HFA -> diferencia de goles
    diff_efectiva = (elo_h + ELO_HFA) - elo_a
    delta_goles = diff_efectiva / ELO_PER_GOAL

    # El equipo más fuerte mete más, el débil mete menos (regresion suave)
    lambda_h = max(0.15, base_h + 0.5 * delta_goles)
    lambda_a = max(0.15, base_a - 0.5 * delta_goles)
    return lambda_h, lambda_a


def _poisson_pmf(k: int, lam: float) -> float:
    if lam <= 0:
        return 1.0 if k == 0 else 0.0
    return math.exp(-lam) * (lam ** k) / math.factorial(k)


def predecir_1x2(
    modelo: dict, home: str, away: str, liga: str | None = None, max_goles: int = 8
) -> Dict[str, float]:
    """Probabilidades 1X2 según Elo + Poisson independientes."""
    elo_h = get_elo(modelo, home, liga)
    elo_a = get_elo(modelo, away, liga)
    lh, la = goles_esperados(elo_h, elo_a, liga)

    p_h = p_d = p_a = 0.0
    pmf_h = [_poisson_pmf(i, lh) for i in range(max_goles + 1)]
    pmf_a = [_poisson_pmf(i, la) for i in range(max_goles + 1)]
    for i, ph in enumerate(pmf_h):
        for j, pa in enumerate(pmf_a):
            pij = ph * pa
            if i > j:
                p_h += pij
            elif i == j:
                p_d += pij
            else:
                p_a += pij
    s = p_h + p_d + p_a
    if s <= 0:
        return {"home": 1 / 3, "draw": 1 / 3, "away": 1 / 3}
    return {"home": p_h / s, "draw": p_d / s, "away": p_a / s}


# ---- Confianza del modelo ----
def confianza(modelo: dict, home: str, away: str, liga: str | None = None) -> float:
    """0-1: cuánto confiamos en la predicción.
    0 si los equipos son nuevos (Elo == ELO_INICIAL).
    1 si llevan muchos partidos."""
    h_key = _team_key(home, liga)
    a_key = _team_key(away, liga)
    if h_key not in modelo.get("elo", {}) or a_key not in modelo.get("elo", {}):
        return 0.0
    procesados = len(modelo.get("partidos_procesados", []))
    if procesados < 50:
        return 0.2
    if procesados < 200:
        return 0.5
    if procesados < 500:
        return 0.75
    return 0.9


# ---- Test directo ----
if __name__ == "__main__":
    print(">>> Test Elo + Poisson")
    m = {"elo": {}, "ultima_actualizacion": None, "partidos_procesados": []}

    # Simular liga sintética con 4 equipos durante 200 partidos
    import random
    random.seed(42)
    fuerza_real = {"A": 1700, "B": 1550, "C": 1500, "D": 1400}
    for k, v in fuerza_real.items():
        set_elo(m, k, ELO_INICIAL, "test")  # arrancan iguales

    for i in range(200):
        h, a = random.sample(list(fuerza_real.keys()), 2)
        # Goles esperados según fuerza real
        diff = (fuerza_real[h] + ELO_HFA) - fuerza_real[a]
        lh = max(0.2, 1.5 + 0.5 * diff / ELO_PER_GOAL)
        la = max(0.2, 1.2 - 0.5 * diff / ELO_PER_GOAL)
        gh = sum(1 for _ in range(20) if random.random() < lh / 20)  # rough Poisson
        ga = sum(1 for _ in range(20) if random.random() < la / 20)
        actualizar_con_resultado(m, h, a, gh, ga, "test", match_id=f"sim{i}")

    print("Elo final tras 200 partidos:")
    for eq in fuerza_real:
        e = get_elo(m, eq, "test")
        print(f"  {eq}: real={fuerza_real[eq]:.0f}  modelado={e:.0f}")

    print("\nPredicción A vs D:")
    print(predecir_1x2(m, "A", "D", "test"))
    print("\nConfianza:", confianza(m, "A", "D", "test"))
