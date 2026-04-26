# lib_devig.py
# ----------------------------------------------------------------------
# Devigging: convertir cuotas con margen del bookmaker en probabilidades
# reales (sin "vig" = sin comisión). El método ingenuo (1/odds normalizado)
# es sesgado en favoritos vs. underdogs. Usamos el "power method", el
# estándar en quant betting.
#
# Referencias:
#   - Joseph Buchdahl, "Squares & Sharps", capítulos sobre devigging
#   - "The Power Method" en https://www.football-data.co.uk/
# ----------------------------------------------------------------------
from __future__ import annotations
import math
from typing import Iterable, List, Tuple


def implied_raw(cuotas: Iterable[float]) -> List[float]:
    """Probabilidades 'crudas' = 1/cuota. Suma > 1 por el vig."""
    return [1.0 / float(c) for c in cuotas]


def overround(cuotas: Iterable[float]) -> float:
    """Suma de probabilidades crudas. Margen del bookie = overround - 1."""
    return sum(implied_raw(cuotas))


# ---- Método ingenuo (multiplicativo) -- de referencia ----
def devig_multiplicative(cuotas: List[float]) -> List[float]:
    raw = implied_raw(cuotas)
    s = sum(raw)
    if s <= 0:
        return [0.0] * len(cuotas)
    return [p / s for p in raw]


# ---- Método potencia ----
def devig_power(cuotas: List[float], tol: float = 1e-10, max_iter: int = 200) -> List[float]:
    """Devigging por potencia: encontrar k tal que sum((1/odds)^k) == 1.
    k < 1 cuando hay vig positivo (el caso normal).
    Devuelve probabilidades reales."""
    raw = implied_raw(cuotas)
    if any(p <= 0 for p in raw):
        return [0.0] * len(cuotas)
    s = sum(raw)
    if abs(s - 1.0) < 1e-9:
        return list(raw)  # ya estaba sin vig (exchange con liquidez perfecta)

    # Bisección de k en (0, 2).
    lo, hi = 1e-6, 2.0
    f_lo = sum(p ** lo for p in raw) - 1.0  # > 0 si overround > 1
    f_hi = sum(p ** hi for p in raw) - 1.0  # < 0 esperado

    # Si por seguridad no hay cambio de signo, devolvemos multiplicative
    if f_lo * f_hi > 0:
        return devig_multiplicative(cuotas)

    for _ in range(max_iter):
        k = 0.5 * (lo + hi)
        f_k = sum(p ** k for p in raw) - 1.0
        if abs(f_k) < tol:
            break
        if f_k * f_lo > 0:
            lo, f_lo = k, f_k
        else:
            hi, f_hi = k, f_k
    return [p ** k for p in raw]


# ---- Método Shin (modela 'inside trader bias') ----
def devig_shin(cuotas: List[float], tol: float = 1e-10, max_iter: int = 200) -> List[float]:
    """Modelo de Shin: asume una fracción z de apostadores informados.
    Más realista que multiplicative para cuotas con favoritos largos.
    Devuelve probs reales."""
    raw = implied_raw(cuotas)
    n = len(raw)
    if n < 2 or any(p <= 0 for p in raw):
        return [0.0] * n

    # Bisección sobre z en [0, 0.4]
    lo, hi = 0.0, 0.4

    def total(z: float) -> float:
        # p_i = (sqrt(z^2 + 4*(1-z)*pi^2/sum) - z) / (2*(1-z))
        S = sum(raw)
        out = []
        for pi in raw:
            num = math.sqrt(z * z + 4.0 * (1.0 - z) * pi * pi / S) - z
            den = 2.0 * (1.0 - z) if z < 1.0 else 1e-9
            out.append(num / den)
        return sum(out)

    f_lo = total(lo) - 1.0  # ~ overround - 1
    f_hi = total(hi) - 1.0
    if f_lo * f_hi > 0:
        return devig_power(cuotas)

    for _ in range(max_iter):
        z = 0.5 * (lo + hi)
        f_z = total(z) - 1.0
        if abs(f_z) < tol:
            break
        if f_z * f_lo > 0:
            lo, f_lo = z, f_z
        else:
            hi, f_hi = z, f_z

    z_final = 0.5 * (lo + hi)
    S = sum(raw)
    probs = []
    for pi in raw:
        num = math.sqrt(z_final * z_final + 4.0 * (1.0 - z_final) * pi * pi / S) - z_final
        den = 2.0 * (1.0 - z_final) if z_final < 1.0 else 1e-9
        probs.append(num / den)
    s = sum(probs)
    if s <= 0:
        return devig_power(cuotas)
    return [p / s for p in probs]  # normalización final por seguridad


# ---- Consenso ponderado entre múltiples bookies ----
def consenso_sharp(odds_por_bookie: dict, pesos: dict | None = None) -> dict:
    """Combina cuotas de varios bookmakers afilados en un único set de
    probabilidades sin vig.

    odds_por_bookie: {nombre_bookie: {selección: cuota}}
    pesos: peso por bookie (default igual peso); más peso a Pinnacle/Betfair.

    Devuelve {selección: prob_real} normalizado.
    """
    if not odds_por_bookie:
        return {}

    pesos = pesos or {}
    # Recoger todas las selecciones presentes
    selecciones = set()
    for d in odds_por_bookie.values():
        selecciones.update(d.keys())
    selecciones = sorted(selecciones)
    n = len(selecciones)
    if n < 2:
        return {}

    acumulado = {s: 0.0 for s in selecciones}
    peso_total = 0.0
    for bookie, cuotas_dict in odds_por_bookie.items():
        # Sólo si el bookie tiene cuotas para TODAS las selecciones
        if any(s not in cuotas_dict for s in selecciones):
            continue
        cuotas = [cuotas_dict[s] for s in selecciones]
        probs = devig_power(cuotas)
        if abs(sum(probs) - 1.0) > 0.05:  # devig falló
            continue
        w = pesos.get(bookie, 1.0)
        for s, p in zip(selecciones, probs):
            acumulado[s] += w * p
        peso_total += w

    if peso_total <= 0:
        return {}
    return {s: acumulado[s] / peso_total for s in selecciones}


# ---- Test directo ----
if __name__ == "__main__":
    print(">>> Test devigging")
    cuotas_caso = [2.10, 3.40, 3.80]  # típico 1X2 con vig ~5%
    print(f"Cuotas: {cuotas_caso}")
    print(f"Overround: {overround(cuotas_caso):.4f} (margen {(overround(cuotas_caso)-1)*100:.2f}%)")
    print(f"Multiplicative: {[f'{p:.4f}' for p in devig_multiplicative(cuotas_caso)]}")
    print(f"Power method  : {[f'{p:.4f}' for p in devig_power(cuotas_caso)]}")
    print(f"Shin method   : {[f'{p:.4f}' for p in devig_shin(cuotas_caso)]}")

    print("\n>>> Test consenso sharp")
    bookies = {
        "Pinnacle": {"Home": 2.10, "Draw": 3.40, "Away": 3.80},
        "Betfair":  {"Home": 2.12, "Draw": 3.45, "Away": 3.85},
        "Smarkets": {"Home": 2.11, "Draw": 3.42, "Away": 3.82},
    }
    pesos = {"Pinnacle": 2.0, "Betfair": 1.5, "Smarkets": 1.0}
    cons = consenso_sharp(bookies, pesos)
    print(f"Consenso afilado: {cons}")
    print(f"Suma: {sum(cons.values()):.4f}")
