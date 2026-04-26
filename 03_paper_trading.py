# STONKS BOT - PAPER TRADING v2
# =============================================================================
# CAMBIOS GRANDES vs v1 (resumen rápido):
#
#  1) FAIR PROBABILITY usa SOLO bookies afilados (Pinnacle, Betfair Exchange,
#     Smarkets, Matchbook). En v1 usábamos la mediana de TODAS las casas, lo
#     que metía ruido de soft books y creaba "value" donde no había.
#
#  2) DEVIGGING POR POTENCIA (lib_devig.py). Mucho más preciso que normalizar
#     1/cuota. Especialmente importante en mercados con favoritos.
#
#  3) MODELO PROPIO Elo + Poisson para fútbol (lib_elo.py). El bot ya no se
#     limita a comparar bookies entre sí — tiene una opinión propia basada
#     en la fuerza histórica de los equipos. Sólo apostamos cuando la
#     opinión propia y el mercado sharp coinciden.
#
#  4) DEDUPE A MEJOR PRECIO. Si una misma selección sale como value en
#     varias casas, sólo registramos la de mejor cuota.
#
#  5) FILTROS ESTRICTOS. Cuotas 1.5-4.5 (en v1 hasta 6.0 → mucho underdog
#     ruidoso), edge 3-12%, mínimo 2 sharp books con cuota.
#
#  6) WHITELIST DE LIGAS. Sólo deportes donde el modelo o la liquidez tienen
#     sentido. En v1 metíamos AFL australiano y mercados oscuros.
#
#  7) CLV TRACKING. Al liquidar registramos cuál era el precio de cierre
#     en sharp books (closing line). El CLV es el indicador real de edge a
#     largo plazo, mucho mejor que el ROI con muestras pequeñas.
#
#  8) KELLY DINÁMICO. Si modelo Y mercado coinciden con fuerza, Kelly
#     un poco mayor; si sólo uno, Kelly mucho más bajo.
# =============================================================================

import csv, json, os, concurrent.futures, threading, time, math, statistics
import datetime as dt
import requests

# Librerías propias (en este mismo directorio)
from lib_devig import devig_power, consenso_sharp
import lib_elo as elo

# ---- API KEYS (igual que antes) ----
_env_keys = os.environ.get("THE_ODDS_API_KEYS", "").strip()
if not _env_keys:
    raise SystemExit(
        "ERROR: falta la variable de entorno THE_ODDS_API_KEYS.\n"
        "En GitHub Actions debe estar configurada como Secret del repo.\n"
        "En local, define la variable antes de ejecutar."
    )
API_KEYS = [k.strip() for k in _env_keys.split(",") if k.strip()]

# =============================================================================
# CONFIG (todo lo "tunable" en un sólo bloque)
# =============================================================================
BANKROLL_INICIAL = 20.00
KELLY_DIV_MIN    = 25     # Kelly muy fraccional cuando sólo coincide mercado
KELLY_DIV_MAX    = 12     # Kelly menos fraccional cuando modelo + mercado coinciden
STAKE_MAX_PCT    = 0.02   # nunca más del 2% del bankroll en una apuesta
STAKE_MIN_EUR    = 0.01

# Filtros de selección
EDGE_MIN         = 0.03   # 3% mínimo
EDGE_MAX         = 0.12   # 12% máximo (más arriba huele a error)
CUOTA_MIN        = 1.50   # cuotas <1.50 paga muy poco para el riesgo
CUOTA_MAX        = 4.50   # cuotas >4.50 son ruido salvo edge muy claro
SHARP_MIN        = 2      # mínimo 2 bookies afilados con cuota válida

# Bookies afilados (usados para fair probability)
SHARP_BOOKIES = {
    "Pinnacle":          2.0,
    "Betfair":           1.6,   # Exchange (back side)
    "Betfair Exchange":  1.6,
    "Smarkets":          1.4,
    "Matchbook":         1.4,
    # Pinnacle "soft" no existe, está sólo el sharp
}

# Whitelist de deportes/ligas. NADA de exotismos en v2.
LIGAS_WHITELIST = {
    # Fútbol top 5 + algunos extras con liquidez (modelo Elo activo aquí)
    "soccer_epl":                      {"modelo": True,  "min_partidos_modelo": 30},
    "soccer_spain_la_liga":             {"modelo": True,  "min_partidos_modelo": 30},
    "soccer_italy_serie_a":             {"modelo": True,  "min_partidos_modelo": 30},
    "soccer_germany_bundesliga":        {"modelo": True,  "min_partidos_modelo": 30},
    "soccer_france_ligue_one":          {"modelo": True,  "min_partidos_modelo": 30},
    "soccer_netherlands_eredivisie":    {"modelo": True,  "min_partidos_modelo": 30},
    "soccer_portugal_primeira_liga":    {"modelo": True,  "min_partidos_modelo": 30},
    "soccer_uefa_champs_league":        {"modelo": True,  "min_partidos_modelo": 30},
    "soccer_uefa_europa_league":        {"modelo": True,  "min_partidos_modelo": 30},
    "soccer_efl_champ":                 {"modelo": True,  "min_partidos_modelo": 30},

    # Tenis ATP/WTA: liquidez ok pero sin modelo (lo añadiremos en v3)
    "tennis_atp_french_open":           {"modelo": False},
    "tennis_atp_madrid":                {"modelo": False},
    "tennis_atp_rome":                  {"modelo": False},
    "tennis_atp_wimbledon":             {"modelo": False},
    "tennis_atp_us_open":               {"modelo": False},
    "tennis_wta_french_open":           {"modelo": False},
    "tennis_wta_madrid":                {"modelo": False},
    "tennis_wta_rome":                  {"modelo": False},
    "tennis_wta_wimbledon":             {"modelo": False},
    "tennis_wta_us_open":               {"modelo": False},

    # NBA/MLB: liquidez muy alta, sharp consensus muy fiable
    "basketball_nba":                   {"modelo": False},
    "baseball_mlb":                     {"modelo": False},
}

# Cuando NO hay modelo (tenis, NBA), exigimos edge mínimo más alto
EDGE_MIN_SIN_MODELO = 0.04

REGIONES = "eu,uk,us"
EVITAR_DUPLICADOS = True

DIR = os.path.dirname(os.path.abspath(__file__))
ARCH_PENDING  = os.path.join(DIR, "pending_bets.csv")
ARCH_SETTLED  = os.path.join(DIR, "settled_bets.csv")
ARCH_BANKROLL = os.path.join(DIR, "bankroll_state.json")
ARCH_API_USE  = os.path.join(DIR, "api_usage.json")


# =============================================================================
# ROTACIÓN DE API KEYS (idéntica a v1)
# =============================================================================
_api_lock = threading.Lock()
_api_agotadas = set()
_api_uso = {}

def _cargar_api_disco():
    if not os.path.exists(ARCH_API_USE):
        return set(), {}
    try:
        with open(ARCH_API_USE) as f:
            data = json.loads(f.read() or "{}")
            return set(data.get("agotadas", [])), data.get("uso", {})
    except Exception:
        return set(), {}

_a, _u = _cargar_api_disco()
_api_agotadas.update(_a)
_api_uso.update(_u)


def _guardar_api_disco():
    try:
        with open(ARCH_API_USE, "w") as f:
            json.dump({"agotadas": sorted(_api_agotadas), "uso": _api_uso}, f, indent=2)
    except Exception:
        pass


def _registrar_uso(key, used, remaining):
    if used is None and remaining is None:
        return
    short = key[:6] + "..." + key[-4:]
    with _api_lock:
        _api_uso[short] = {
            "used": int(used) if used is not None else _api_uso.get(short, {}).get("used"),
            "remaining": int(remaining) if remaining is not None else _api_uso.get(short, {}).get("remaining"),
            "last": dt.datetime.utcnow().isoformat(timespec="seconds") + "Z",
        }


def api_key_activa():
    with _api_lock:
        for k in API_KEYS:
            if k not in _api_agotadas:
                return k
    return None


def marcar_agotada(key):
    with _api_lock:
        if key in _api_agotadas:
            return
        _api_agotadas.add(key)
        _guardar_api_disco()
        print(f"   [!] API key agotada. {len(_api_agotadas)}/{len(API_KEYS)}")


def _get(url, _retries=2):
    key = api_key_activa()
    if not key:
        return None, "no_keys"
    full = url + ("&apiKey=" if "?" in url else "?apiKey=") + key
    try:
        r = requests.get(full, timeout=20)
    except Exception as e:
        return None, str(e)
    used = r.headers.get("x-requests-used")
    remaining = r.headers.get("x-requests-remaining")
    _registrar_uso(key, used, remaining)
    _guardar_api_disco()
    body = (r.text or "").lower()
    if r.status_code in (401, 403) or "quota" in body or "out of" in body:
        marcar_agotada(key)
        return _get(url, _retries)
    if r.status_code == 429:
        if _retries > 0:
            time.sleep(2)
            return _get(url, _retries - 1)
        return None, "rate_limited"
    if r.status_code != 200:
        return None, f"HTTP {r.status_code}"
    return r.json(), None


# =============================================================================
# ESTADO / CSV (compatibles con v1 — los archivos existentes siguen valiendo)
# =============================================================================
def cargar_estado():
    if os.path.exists(ARCH_BANKROLL):
        with open(ARCH_BANKROLL, "r", encoding="utf-8") as f:
            return json.load(f)
    e = {"bankroll": BANKROLL_INICIAL,
         "inicio":   dt.datetime.now().isoformat(timespec="seconds"),
         "historial": []}
    guardar_estado(e); return e


def guardar_estado(e):
    with open(ARCH_BANKROLL, "w", encoding="utf-8") as f:
        json.dump(e, f, indent=2, ensure_ascii=False)


def leer_csv(p):
    if not os.path.exists(p): return []
    with open(p, "r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def escribir_csv(p, filas, campos):
    with open(p, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=campos); w.writeheader()
        for r in filas: w.writerow(r)


def anexar_csv(p, fila, campos):
    ex = os.path.exists(p)
    with open(p, "a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=campos)
        if not ex: w.writeheader()
        # rellenar campos faltantes con "" para CSVs de v1
        fila_full = {k: fila.get(k, "") for k in campos}
        w.writerow(fila_full)


# =============================================================================
# KELLY (con factor dinámico modelo+mercado)
# =============================================================================
def kelly_stake(p, cuota, br, factor_confianza: float = 1.0):
    """factor_confianza ∈ [0, 1]:
       1.0 = modelo + mercado coinciden con fuerza -> Kelly más generoso.
       0.0 = sólo mercado disponible -> Kelly muy fraccional.
    """
    b = cuota - 1.0
    if b <= 0:
        return 0.0
    f = (b * p - (1.0 - p)) / b
    if f <= 0:
        return 0.0
    # Kelly divisor interpolado
    div = KELLY_DIV_MIN + (KELLY_DIV_MAX - KELLY_DIV_MIN) * factor_confianza
    div = max(div, 1.0)
    return round(br * min(f / div, STAKE_MAX_PCT), 2)


# =============================================================================
# DESCUBRIMIENTO + FETCH DE CUOTAS
# =============================================================================
def descubrir_deportes_activos():
    """Sólo devuelve ligas en LIGAS_WHITELIST que estén activas hoy."""
    data, err = _get("https://api.the-odds-api.com/v4/sports/")
    if not data:
        print(f"   [!] /sports error: {err}")
        return []
    eleg = []
    for d in data:
        if not d.get("active") or d.get("has_outrights"):
            continue
        key = d.get("key", "")
        if key in LIGAS_WHITELIST:
            eleg.append(key)
    return eleg


def pedir_cuotas(deporte):
    url = (f"https://api.the-odds-api.com/v4/sports/{deporte}/odds/"
           f"?regions={REGIONES}&markets=h2h&oddsFormat=decimal")
    data, err = _get(url)
    return deporte, (data or [])


def escanear_todo():
    deportes = descubrir_deportes_activos()
    if not deportes:
        print("   [!] Sin ligas whitelist activas hoy"); return []
    print(f"   Escaneando {len(deportes)} ligas activas (whitelist v2)")
    partidos = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(deportes), 12)) as ex:
        for fut in concurrent.futures.as_completed([ex.submit(pedir_cuotas, d) for d in deportes]):
            d, ps = fut.result()
            for p in ps:
                p["_sport"] = d
            partidos.extend(ps)
    return partidos


# =============================================================================
# DETECCIÓN DE VALUE BETS
# =============================================================================
def _es_soccer(liga: str) -> bool:
    return liga.startswith("soccer_")


def _seleccion_a_lado(seleccion: str, home: str, away: str) -> str | None:
    """Mapea el campo 'name' de The Odds API a 'home', 'draw' o 'away'."""
    if seleccion == home:
        return "home"
    if seleccion == away:
        return "away"
    if seleccion.lower() in ("draw", "tie", "x"):
        return "draw"
    return None


def detectar_value_bets(partidos, bankroll, modelo_elo: dict):
    H = []
    estadisticas = {"total_partidos": len(partidos), "skip_pocos_sharp": 0,
                    "skip_sin_value": 0, "candidatos": 0}

    for p in partidos:
        liga = p.get("_sport", "")
        if liga not in LIGAS_WHITELIST:
            continue

        cfg = LIGAS_WHITELIST[liga]
        usa_modelo = cfg.get("modelo", False)

        bks = p.get("bookmakers", [])

        # 1) Recoger cuotas por bookie con estructura {bookie: {selección: cuota}}
        cuotas_por_bookie: dict = {}
        for bk in bks:
            title = bk.get("title", "?")
            for m in bk.get("markets", []):
                if m.get("key") != "h2h":
                    continue
                d = {}
                for o in m.get("outcomes", []):
                    nm = o.get("name")
                    pr = o.get("price")
                    if nm and pr and pr > 1.01:
                        d[nm] = float(pr)
                if d:
                    cuotas_por_bookie[title] = d

        # 2) Separar sharp vs soft
        sharp_books = {b: d for b, d in cuotas_por_bookie.items() if b in SHARP_BOOKIES}
        soft_books  = {b: d for b, d in cuotas_por_bookie.items() if b not in SHARP_BOOKIES}

        # Necesitamos al menos SHARP_MIN bookies afilados con TODAS las selecciones
        sharp_books_validos = {}
        # Conjunto unificado de selecciones (entre todos)
        todas_sel = set()
        for d in cuotas_por_bookie.values():
            todas_sel.update(d.keys())
        for b, d in sharp_books.items():
            if all(s in d for s in todas_sel):
                sharp_books_validos[b] = d

        if len(sharp_books_validos) < SHARP_MIN:
            estadisticas["skip_pocos_sharp"] += 1
            continue

        # 3) Fair probability sharp
        probs_sharp = consenso_sharp(sharp_books_validos, SHARP_BOOKIES)
        if not probs_sharp or abs(sum(probs_sharp.values()) - 1.0) > 0.05:
            continue

        # 4) Fair probability del modelo (si aplica)
        probs_modelo = None
        confianza_modelo = 0.0
        if usa_modelo and _es_soccer(liga):
            home = p.get("home_team", "")
            away = p.get("away_team", "")
            if home and away:
                pm = elo.predecir_1x2(modelo_elo, home, away, liga)
                # Mapear home/draw/away al naming del partido
                probs_modelo = {
                    home: pm["home"],
                    "Draw": pm["draw"],
                    away: pm["away"],
                }
                confianza_modelo = elo.confianza(modelo_elo, home, away, liga)

        # 5) Probabilidad final = blend modelo + mercado sharp
        if probs_modelo and confianza_modelo > 0.0:
            w_m = confianza_modelo * 0.55       # max 55% peso modelo
            w_s = 1.0 - w_m
            probs_finales = {}
            for s in probs_sharp:
                pm = probs_modelo.get(s, probs_sharp[s])
                probs_finales[s] = w_m * pm + w_s * probs_sharp[s]
        else:
            probs_finales = dict(probs_sharp)

        # 6) Buscar mejores precios en SOFT books y comparar con probs_finales
        # Tomamos la MEJOR cuota disponible por selección (en cualquier bookie no sharp).
        # En v1 registrábamos múltiples filas por la misma apuesta — eso era ruido.
        mejor_por_sel: dict = {}  # {selección: (cuota, bookie)}
        for b, d in cuotas_por_bookie.items():
            for s, c in d.items():
                if c < CUOTA_MIN or c > CUOTA_MAX:
                    continue
                cur = mejor_por_sel.get(s)
                if cur is None or c > cur[0]:
                    mejor_por_sel[s] = (c, b)

        if not mejor_por_sel:
            estadisticas["skip_sin_value"] += 1
            continue

        # 7) Para cada selección con mejor cuota, ver si hay edge real
        edge_min_local = EDGE_MIN if (usa_modelo and confianza_modelo > 0.4) else EDGE_MIN_SIN_MODELO

        for sel, (c, bk) in mejor_por_sel.items():
            pr = probs_finales.get(sel, 0.0)
            if pr <= 0:
                continue
            # Probabilidad implícita de la cuota (sin devig, pura de mercado)
            cuota_fair = 1.0 / pr
            edge = (c / cuota_fair) - 1.0
            if edge < edge_min_local or edge > EDGE_MAX:
                continue

            # Regla extra: NUNCA apostar contra el modelo si éste tiene confianza alta
            if probs_modelo and confianza_modelo > 0.4:
                p_modelo_sel = probs_modelo.get(sel, 0)
                p_mercado_sel = probs_sharp.get(sel, 0)
                # Si el modelo cree que es <60% de la prob de mercado, nope
                if p_modelo_sel < 0.6 * p_mercado_sel and p_mercado_sel > 0:
                    continue

            # Factor confianza: 0 si no hay modelo, hasta 1 si modelo + mercado fuerte
            factor = 0.0
            if probs_modelo and confianza_modelo > 0:
                # Edge según el modelo (no mercado)
                p_mod_sel = probs_modelo.get(sel, pr)
                edge_modelo = (c * p_mod_sel) - 1.0
                if edge_modelo > 0.02:  # modelo también ve edge
                    factor = min(1.0, confianza_modelo * (edge_modelo / 0.10))

            stk = kelly_stake(pr, c, bankroll, factor_confianza=factor)
            if stk < STAKE_MIN_EUR:
                continue

            # Closing line provisional (= sharp consensus actual al detectar)
            closing_provisional = round(1.0 / probs_sharp.get(sel, 1.0), 4) if probs_sharp.get(sel, 0) > 0 else ""

            H.append({
                "id":             f"{p.get('id','')}|{sel}|{bk}",
                "match_id":       p.get("id", ""),
                "fecha":          p.get("commence_time", ""),
                "deporte":        liga,
                "partido":        f"{p.get('home_team')} vs {p.get('away_team')}",
                "home_team":      p.get("home_team", ""),
                "away_team":      p.get("away_team", ""),
                "seleccion":      sel,
                "casa":           bk,
                "cuota":          c,
                "prob_real":      round(pr, 4),
                "prob_modelo":    round(probs_modelo.get(sel, 0), 4) if probs_modelo else "",
                "prob_sharp":     round(probs_sharp.get(sel, 0), 4),
                "edge_pct":       round(edge * 100, 2),
                "stake_eur":      stk,
                "factor_conf":    round(factor, 3),
                "n_sharp":        len(sharp_books_validos),
                "fair_open":      closing_provisional,  # se actualizará al cerrar
                "creada":         dt.datetime.now().isoformat(timespec="seconds"),
            })
            estadisticas["candidatos"] += 1

    # Imprimir diagnóstico
    print(f"   Diagnóstico v2: {estadisticas}")
    return H


def registrar_pendientes(H):
    if not H: return 0
    ex = leer_csv(ARCH_PENDING)
    ya = {b["id"] for b in ex} if EVITAR_DUPLICADOS else set()
    nuevos = [h for h in H if h["id"] not in ya]
    if not nuevos: return 0
    # Para mantener compatibilidad, escribimos todos los campos que aparezcan
    # en la primera fila NUEVA + los que ya estaban en el CSV existente.
    campos_existentes = list(ex[0].keys()) if ex else []
    campos_nuevos = list(H[0].keys())
    campos = list(dict.fromkeys(campos_existentes + campos_nuevos))  # union ordenada
    # Si el CSV existe pero le faltan campos nuevos, lo reescribimos con todos
    if ex and any(c not in campos_existentes for c in campos_nuevos):
        escribir_csv(ARCH_PENDING, ex + nuevos, campos)
    else:
        for n in nuevos: anexar_csv(ARCH_PENDING, n, campos)
    return len(nuevos)


# =============================================================================
# LIQUIDACIÓN (con CLV)
# =============================================================================
def pedir_scores(deporte):
    url = f"https://api.the-odds-api.com/v4/sports/{deporte}/scores/?daysFrom=3"
    data, err = _get(url)
    return data or []


def mapa_resultados(deportes):
    mapa = {}
    if not deportes: return mapa
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(deportes), 12)) as ex:
        futs = {ex.submit(pedir_scores, d): d for d in deportes}
        for fut in concurrent.futures.as_completed(futs):
            for m in fut.result():
                if not m.get("completed"):
                    continue
                sc = m.get("scores") or []
                if len(sc) < 2:
                    continue
                try:
                    s1 = float(sc[0]["score"]); s2 = float(sc[1]["score"])
                except Exception:
                    continue
                if s1 > s2:
                    g = sc[0]["name"]
                elif s2 > s1:
                    g = sc[1]["name"]
                else:
                    g = "Draw"
                mapa[m["id"]] = {
                    "ganador": g,
                    "score":   f"{s1:g}-{s2:g}",
                    "completed_at": m.get("last_update", ""),
                    "scores":  {sc[0]["name"]: s1, sc[1]["name"]: s2},
                }
    return mapa


def liquidar_pendientes(estado):
    pend = leer_csv(ARCH_PENDING)
    if not pend: return 0, 0, 0.0
    deps = list({b["deporte"] for b in pend})
    mapa = mapa_resultados(deps)
    nuevas = []; liq = 0; gan = 0; pnl_t = 0.0

    # Campos del CSV de settled (incluye nuevos: prob_modelo, prob_sharp, factor_conf, n_sharp, fair_open, clv_pct)
    campos = ["id","match_id","fecha","deporte","partido","home_team","away_team",
              "seleccion","casa","cuota","prob_real","prob_modelo","prob_sharp",
              "edge_pct","stake_eur","factor_conf","n_sharp","fair_open","creada",
              "resultado","score","pnl","bankroll_tras","clv_pct"]

    for b in pend:
        res = mapa.get(b["match_id"])
        if not res:
            nuevas.append(b)
            continue
        stk = float(b.get("stake_eur") or 0); c = float(b.get("cuota") or 0)
        if res["ganador"] == b["seleccion"]:
            pnl = round(stk * (c - 1), 2); resultado = "GANADA"; gan += 1
        else:
            pnl = -stk; resultado = "PERDIDA"
        estado["bankroll"] = round(estado["bankroll"] + pnl, 2)
        pnl_t += pnl; liq += 1

        # CLV: si tenemos fair_open registrado, comparamos con el resultado real
        # (no tenemos la cuota de cierre real porque ya pasó el partido, así que
        # CLV aproximado = (cuota_apostada / fair_open) - 1)
        clv = ""
        try:
            fair_open = float(b.get("fair_open") or 0)
            if fair_open > 1.0:
                clv = round(((c / fair_open) - 1.0) * 100, 2)
        except Exception:
            pass

        fila = {k: b.get(k, "") for k in campos if k in b}
        fila.update({
            "resultado": resultado,
            "score": res["score"],
            "pnl": pnl,
            "bankroll_tras": estado["bankroll"],
            "clv_pct": clv,
        })
        anexar_csv(ARCH_SETTLED, fila, campos)

    if pend:
        # Mantener todos los campos del pending, no sólo los del primero
        all_cols = list({k for row in pend for k in row.keys()})
        # Asegurar el orden: campos conocidos primero
        orden = ["id","match_id","fecha","deporte","partido","home_team","away_team",
                 "seleccion","casa","cuota","prob_real","prob_modelo","prob_sharp",
                 "edge_pct","stake_eur","factor_conf","n_sharp","fair_open","creada"]
        cols = [c for c in orden if c in all_cols] + [c for c in all_cols if c not in orden]
        escribir_csv(ARCH_PENDING, nuevas, cols)
    estado["historial"].append({
        "fecha": dt.datetime.now().isoformat(timespec="seconds"),
        "bankroll": estado["bankroll"],
    })
    guardar_estado(estado)
    return liq, gan, pnl_t


# =============================================================================
# INFORME (incluye CLV)
# =============================================================================
def informe(estado):
    s = leer_csv(ARCH_SETTLED); p = leer_csv(ARCH_PENDING)
    n = len(s); gan = sum(1 for x in s if x.get("resultado") == "GANADA"); per = n - gan
    pnl = round(sum(float(x.get("pnl") or 0) for x in s), 2) if s else 0.0
    stk = round(sum(float(x.get("stake_eur") or 0) for x in s), 2) if s else 0.0
    roi = (pnl / stk * 100) if stk > 0 else 0.0

    clvs = [float(x["clv_pct"]) for x in s if x.get("clv_pct") not in (None, "", "None")]
    clv_mean = round(statistics.mean(clvs), 2) if clvs else None

    try:
        dias = max((dt.datetime.now() - dt.datetime.fromisoformat(estado["inicio"])).days, 1)
    except Exception:
        dias = 1
    print(f"\n+{'-'*60}+")
    print(f"|  INFORME PAPER TRADING v2 - dia {dias:<3}{' '*(60-32-len(str(dias)))}|")
    print(f"+{'-'*60}+")
    print(f"  Bankroll inicial   : {BANKROLL_INICIAL:>7.2f} EUR")
    print(f"  Bankroll actual    : {estado['bankroll']:>7.2f} EUR")
    print(f"  P&L total          : {pnl:>+7.2f} EUR")
    print(f"  Apuestas resueltas : {n}   (gan {gan} / per {per})")
    if n: print(f"  Acierto            : {gan / n * 100:>6.2f} %")
    print(f"  Volumen apostado   : {stk:>7.2f} EUR")
    if stk: print(f"  ROI                : {roi:>+6.2f} %")
    if clv_mean is not None:
        flecha = "↑ POSITIVO" if clv_mean > 0 else "↓ NEGATIVO"
        print(f"  CLV medio (~edge)  : {clv_mean:>+6.2f} %  {flecha}")
    print(f"  Apuestas abiertas  : {len(p)}")
    print(f"+{'-'*60}+")
    if n < 100:
        print(f"  [i] Sample {n} muy pequeña — el ROI hay que ignorarlo.")
        print(f"      El indicador real con esta muestra es el CLV medio.")


# =============================================================================
# MIGRACIÓN DE CSVs (v1 -> v2)
# =============================================================================
COLS_V2_PENDING = ["id","match_id","fecha","deporte","partido","home_team","away_team",
                   "seleccion","casa","cuota","prob_real","prob_modelo","prob_sharp",
                   "edge_pct","stake_eur","factor_conf","n_sharp","fair_open","creada"]

COLS_V2_SETTLED = COLS_V2_PENDING + ["resultado","score","pnl","bankroll_tras","clv_pct"]


def migrar_csv_si_necesario(path: str, cols_v2: list[str]):
    """Si el CSV existe pero no tiene las columnas v2, lo reescribe con
    todas las columnas v2 (rellenando con '' las celdas faltantes). Sólo
    se ejecuta una vez; en runs siguientes ya no toca el archivo."""
    if not os.path.exists(path):
        return
    rows = leer_csv(path)
    if not rows:
        return
    cols_actuales = list(rows[0].keys())
    if all(c in cols_actuales for c in cols_v2):
        return  # ya está al día
    print(f"   [migración] Actualizando esquema de {os.path.basename(path)}...")
    cols_unidas = list(dict.fromkeys(cols_actuales + cols_v2))
    escribir_csv(path, rows, cols_unidas)


def limpiar_duplicados_pending():
    """v1 creaba a veces filas duplicadas exactas (mismo id) — limpiamos
    una sola vez al arrancar para no llevar herencia mala."""
    if not os.path.exists(ARCH_PENDING):
        return
    rows = leer_csv(ARCH_PENDING)
    if not rows:
        return
    visto = set()
    unicos = []
    for r in rows:
        key = r.get("id", "")
        if key and key not in visto:
            visto.add(key)
            unicos.append(r)
    if len(unicos) < len(rows):
        cols = list(rows[0].keys())
        escribir_csv(ARCH_PENDING, unicos, cols)
        print(f"   [migración] Eliminadas {len(rows) - len(unicos)} filas duplicadas de pending_bets.csv")


# =============================================================================
# MAIN
# =============================================================================
def main():
    # Migrar CSVs v1 -> v2 si hace falta (idempotente)
    migrar_csv_si_necesario(ARCH_PENDING, COLS_V2_PENDING)
    migrar_csv_si_necesario(ARCH_SETTLED, COLS_V2_SETTLED)
    limpiar_duplicados_pending()

    e = cargar_estado()

    # Cargar modelo Elo (lo actualiza el script 07_actualizar_elo.py)
    modelo = elo.cargar_modelo()
    n_partidos_modelo = len(modelo.get("partidos_procesados", []))
    print(f">>> Modelo Elo: {len(modelo.get('elo', {}))} equipos, "
          f"{n_partidos_modelo} partidos procesados")

    print(">>> [1/3] Escaneando cuotas (whitelist v2)...")
    partidos = escanear_todo()
    print(f"    {len(partidos)} partidos recibidos")
    H = detectar_value_bets(partidos, e["bankroll"], modelo)
    nuevos = registrar_pendientes(H)
    print(f"    {len(H)} value bets candidatas | {nuevos} nuevas registradas")

    print("\n>>> [2/3] Liquidando partidos terminados ...")
    liq, gan, pnl_t = liquidar_pendientes(e)
    if liq:
        print(f"    {liq} liquidadas | {gan} ganadas | P&L sesion: {pnl_t:+.2f} EUR")
    else:
        print("    Sin partidos para liquidar.")

    print("\n>>> [3/3] Informe global")
    informe(e)

    # Dashboard + Excel (igual que en v1)
    for nombre, archivo in [("dashboard HTML", "05_generar_dashboard.py"),
                             ("Excel", "06_generar_excel.py")]:
        try:
            import importlib.util, sys
            spec = importlib.util.spec_from_file_location(nombre, os.path.join(DIR, archivo))
            mod = importlib.util.module_from_spec(spec)
            sys.modules[nombre] = mod
            spec.loader.exec_module(mod)
            mod.generar()
        except Exception as ex:
            print(f"   [!] No se pudo generar {nombre}: {ex}")


if __name__ == "__main__":
    main()
