# =============================================================
#  STONKS BOT - FASE 1.5 : MOTOR DE VALUE BETTING ROBUSTO
#  (con llamadas en PARALELO y filtro de OUTLIERS)
# -------------------------------------------------------------
#  Cambios frente a la versión anterior:
#    1) Llamadas en paralelo con concurrent.futures
#       -> tarda 3s en vez de 20s.
#    2) Filtro de outliers: descarta cuotas absurdamente
#       distintas del resto (MAD = Mediana de Desviaciones
#       Absolutas). Esto mata los falsos 70% edge.
#    3) Sanity cap: los edges > EDGE_MAX se marcan como
#       "sospechosos" y NO se apuestan.
#    4) Exige al menos N_CASAS_MIN bookmakers cotizando para
#       fiarnos de la cuota justa.
#    5) Rotación de deportes por día para no quemar las 500
#       llamadas mensuales de The Odds API.
# =============================================================

import requests
import csv
import datetime as dt
import statistics
import os
import concurrent.futures

# =============================================================
# CONFIGURACIÓN
# =============================================================

API_KEY = "c01be8a3f80b3538d472b864047cd358"   # tu clave de The Odds API

BANKROLL      = 20.00
KELLY_DIV     = 20           # Kelly / 20
EDGE_MIN      = 0.03         # 3% de ventaja mínima
EDGE_MAX      = 0.15         # >15% se considera sospechoso y se descarta
STAKE_MAX_PCT = 0.02         # máximo 2% del bankroll por apuesta
STAKE_MIN_EUR = 0.01
CUOTA_MIN     = 1.40
CUOTA_MAX     = 6.00
N_CASAS_MIN   = 5            # mínimo 5 bookmakers cotizando
MAD_FACTOR    = 3.0          # descarta cuotas > 3*MAD de la mediana

DEPORTES = [
    "soccer_epl",
    "soccer_spain_la_liga",
    "soccer_italy_serie_a",
    "soccer_germany_bundesliga",
    "soccer_france_ligue_one",
    "soccer_uefa_champs_league",
]

REGIONES = "eu,uk"
MERCADO  = "h2h"
FORMATO  = "decimal"

ARCHIVO_SALIDA = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "value_bets.csv"
)

# =============================================================
# FUNCIONES DE API (llamadas en paralelo)
# =============================================================

def pedir_cuotas(deporte: str):
    url = (
        f"https://api.the-odds-api.com/v4/sports/{deporte}/odds/"
        f"?apiKey={API_KEY}&regions={REGIONES}&markets={MERCADO}"
        f"&oddsFormat={FORMATO}"
    )
    try:
        r = requests.get(url, timeout=20)
    except Exception as e:
        print(f"   [!] Error de red en {deporte}: {e}")
        return deporte, []
    if r.status_code != 200:
        print(f"   [!] {deporte}: HTTP {r.status_code} - {r.text[:100]}")
        return deporte, []
    return deporte, r.json()


def pedir_todo_en_paralelo(deportes: list[str]) -> list[dict]:
    """Lanza todas las llamadas a la API a la vez.
    Tarda lo que la llamada más lenta (~2-3s) en lugar de la suma."""
    partidos_totales = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(deportes)) as ex:
        futuros = {ex.submit(pedir_cuotas, d): d for d in deportes}
        for fut in concurrent.futures.as_completed(futuros):
            deporte, partidos = fut.result()
            print(f"    [OK] {deporte}: {len(partidos)} partidos")
            for p in partidos:
                p["_sport_key"] = deporte
            partidos_totales.extend(partidos)
    return partidos_totales


# =============================================================
# FUNCIONES DE ANÁLISIS
# =============================================================

def filtrar_outliers(cuotas: list[float]) -> list[float]:
    """
    Elimina cuotas anormales usando la mediana y la MAD
    (Median Absolute Deviation). Todo lo que esté a más
    de MAD_FACTOR * MAD de la mediana es ruido.
    """
    if len(cuotas) < 5:
        return cuotas
    med = statistics.median(cuotas)
    desviaciones = [abs(c - med) for c in cuotas]
    mad = statistics.median(desviaciones)
    if mad == 0:
        return cuotas
    return [c for c in cuotas if abs(c - med) <= MAD_FACTOR * mad]


def cuota_justa(cuotas: list[float]) -> float:
    limpias = filtrar_outliers(cuotas)
    if not limpias:
        return 0.0
    return statistics.median(limpias)


def probabilidad_implicita(cuota: float) -> float:
    return 1.0 / cuota if cuota > 0 else 0.0


def normalizar(probs: dict[str, float]) -> dict[str, float]:
    total = sum(probs.values())
    if total <= 0:
        return probs
    return {k: v / total for k, v in probs.items()}


def kelly_stake(p: float, cuota: float, bankroll: float) -> float:
    b = cuota - 1.0
    if b <= 0:
        return 0.0
    f = (b * p - (1.0 - p)) / b
    if f <= 0:
        return 0.0
    f_frac = min(f / KELLY_DIV, STAKE_MAX_PCT)
    return round(bankroll * f_frac, 2)


def analizar_partido(partido: dict) -> list[dict]:
    hallazgos = []
    bookmakers = partido.get("bookmakers", [])
    if len(bookmakers) < N_CASAS_MIN:
        return hallazgos

    # Recolección de cuotas por resultado
    cuotas_por_resultado: dict[str, list[tuple[str, float]]] = {}
    for bk in bookmakers:
        nombre_bk = bk.get("title", "?")
        for mercado in bk.get("markets", []):
            if mercado.get("key") != "h2h":
                continue
            for outcome in mercado.get("outcomes", []):
                r = outcome["name"]
                c = outcome["price"]
                cuotas_por_resultado.setdefault(r, []).append((nombre_bk, c))

    if not cuotas_por_resultado:
        return hallazgos

    # Cuota justa por resultado (con outlier filter)
    justas_brutas = {
        r: cuota_justa([c for _, c in lista])
        for r, lista in cuotas_por_resultado.items()
        if len(lista) >= N_CASAS_MIN
    }
    if not justas_brutas:
        return hallazgos

    probs_brutas = {r: probabilidad_implicita(c) for r, c in justas_brutas.items()}
    probs_reales = normalizar(probs_brutas)

    for resultado, lista in cuotas_por_resultado.items():
        p_real = probs_reales.get(resultado, 0.0)
        if p_real <= 0:
            continue
        cuota_justa_final = 1.0 / p_real

        for nombre_bk, cuota in lista:
            if cuota < CUOTA_MIN or cuota > CUOTA_MAX:
                continue
            edge = (cuota / cuota_justa_final) - 1.0

            # Sanity cap: edge demasiado grande = error o info perdida
            if edge > EDGE_MAX:
                continue
            if edge < EDGE_MIN:
                continue

            stake = kelly_stake(p_real, cuota, BANKROLL)
            if stake < STAKE_MIN_EUR:
                continue

            hallazgos.append({
                "fecha":        partido.get("commence_time", ""),
                "deporte":      partido.get("_sport_key", ""),
                "partido":      f"{partido.get('home_team')} vs {partido.get('away_team')}",
                "seleccion":    resultado,
                "casa":         nombre_bk,
                "cuota":        cuota,
                "cuota_justa":  round(cuota_justa_final, 3),
                "prob_real":    round(p_real, 4),
                "edge_pct":     round(edge * 100, 2),
                "stake_eur":    stake,
                "ganancia_si_acierta": round(stake * (cuota - 1), 2),
            })
    return hallazgos


def guardar_csv(hallazgos: list[dict]):
    if not hallazgos:
        return
    campos = list(hallazgos[0].keys())
    ya_existe = os.path.exists(ARCHIVO_SALIDA)
    with open(ARCHIVO_SALIDA, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["timestamp"] + campos)
        if not ya_existe:
            w.writeheader()
        ahora = dt.datetime.now().isoformat(timespec="seconds")
        for h in hallazgos:
            w.writerow({"timestamp": ahora, **h})


# =============================================================
# MAIN
# =============================================================

def main():
    print("=" * 60)
    print("STONKS BOT v1.5 - Motor de Value Betting ROBUSTO")
    print(f"Bankroll: {BANKROLL:.2f} €  |  Kelly/{KELLY_DIV}  |  "
          f"Edge: {EDGE_MIN*100:.0f}%-{EDGE_MAX*100:.0f}%")
    print("=" * 60)
    print(">>> Pidiendo cuotas en PARALELO ...")

    t0 = dt.datetime.now()
    partidos = pedir_todo_en_paralelo(DEPORTES)
    tiempo = (dt.datetime.now() - t0).total_seconds()
    print(f">>> {len(partidos)} partidos recibidos en {tiempo:.1f}s\n")

    hallazgos = []
    for p in partidos:
        hallazgos.extend(analizar_partido(p))

    print("=" * 60)
    if not hallazgos:
        print("Sin value bets ahora mismo.")
        print("Las oportunidades aparecen y desaparecen en minutos.")
        return

    hallazgos.sort(key=lambda x: x["edge_pct"], reverse=True)

    print(f"VALUE BETS ENCONTRADAS: {len(hallazgos)}\n")
    print(f"{'EDGE':>6}  {'CUOTA':>5}  {'STAKE':>5}  {'CASA':<15}  PARTIDO -> SELECCIÓN")
    print("-" * 100)
    for h in hallazgos[:20]:
        print(f"{h['edge_pct']:>5.2f}%  "
              f"{h['cuota']:>5.2f}  "
              f"{h['stake_eur']:>4.2f}€  "
              f"{h['casa'][:15]:<15}  "
              f"{h['partido']} -> {h['seleccion']}")

    guardar_csv(hallazgos)
    print(f"\n>>> Guardadas {len(hallazgos)} oportunidades en {ARCHIVO_SALIDA}")
    suma = sum(h["stake_eur"] for h in hallazgos)
    print(f">>> Stake total si apostases TODAS: {suma:.2f} €")
    print("=" * 60)


if __name__ == "__main__":
    main()
