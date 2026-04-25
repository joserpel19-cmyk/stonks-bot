# STONKS BOT - PAPER TRADING (multi-sport + multi-API)
import requests, csv, json, os, statistics, concurrent.futures
import datetime as dt

# ---- CONFIG ----
# Las claves API se leen SIEMPRE de la variable de entorno THE_ODDS_API_KEYS
# (separadas por coma). En GitHub Actions se inyectan desde GitHub Secrets.
# Para correr en local, define la variable de entorno antes de ejecutar:
#   PowerShell:  $env:THE_ODDS_API_KEYS="clave1,clave2"; python 03_paper_trading.py
#   CMD:         set THE_ODDS_API_KEYS=clave1,clave2 && python 03_paper_trading.py
_env_keys = os.environ.get("THE_ODDS_API_KEYS", "").strip()
if not _env_keys:
    raise SystemExit(
        "ERROR: falta la variable de entorno THE_ODDS_API_KEYS.\n"
        "En GitHub Actions debe estar configurada como Secret del repo.\n"
        "En local, define la variable antes de ejecutar."
    )
API_KEYS = [k.strip() for k in _env_keys.split(",") if k.strip()]

BANKROLL_INICIAL = 20.00
KELLY_DIV        = 20
EDGE_MIN         = 0.03
EDGE_MAX         = 0.15
STAKE_MAX_PCT    = 0.02
STAKE_MIN_EUR    = 0.01
CUOTA_MIN        = 1.40
CUOTA_MAX        = 6.00
N_CASAS_MIN      = 5
MAD_FACTOR       = 3.0
EVITAR_DUPLICADOS = True

PREFIJOS_DEPORTES = [
    "soccer_","tennis_","basketball_","baseball_","icehockey_",
    "americanfootball_","mma_","rugbyleague_","rugbyunion_",
    "cricket_","aussierules_","boxing_",
]
MAX_DEPORTES_POR_RUN = 15
REGIONES = "eu,uk"

DIR = os.path.dirname(os.path.abspath(__file__))
ARCH_PENDING  = os.path.join(DIR, "pending_bets.csv")
ARCH_SETTLED  = os.path.join(DIR, "settled_bets.csv")
ARCH_BANKROLL = os.path.join(DIR, "bankroll_state.json")
ARCH_API_USE  = os.path.join(DIR, "api_usage.json")

# ---- MULTI-API KEY ROTATION (thread-safe, en memoria) ----
import threading
_api_lock = threading.Lock()
_api_agotadas = set()

def _cargar_agotadas_disco():
    if not os.path.exists(ARCH_API_USE):
        return set()
    try:
        with open(ARCH_API_USE) as f:
            raw = f.read().strip()
            if not raw:
                return set()
            data = json.loads(raw)
            return set(data.get("agotadas", []))
    except Exception:
        return set()

_api_agotadas.update(_cargar_agotadas_disco())

def _guardar_agotadas_disco():
    try:
        with open(ARCH_API_USE, "w") as f:
            json.dump({"agotadas": sorted(_api_agotadas)}, f)
    except Exception:
        pass

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
        _guardar_agotadas_disco()
        print(f"   [!] API key agotada. Agotadas: {len(_api_agotadas)}/{len(API_KEYS)}")

# ---- ESTADO ----
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

# ---- MATEMÁTICAS ----
def filtrar_outliers(cs):
    if len(cs) < 5: return cs
    m = statistics.median(cs)
    mad = statistics.median([abs(c-m) for c in cs])
    if mad == 0: return cs
    return [c for c in cs if abs(c-m) <= MAD_FACTOR*mad]

def kelly_stake(p, cuota, br):
    b = cuota - 1.0
    if b <= 0: return 0.0
    f = (b*p - (1.0-p))/b
    if f <= 0: return 0.0
    return round(br * min(f/KELLY_DIV, STAKE_MAX_PCT), 2)

# ---- CSV ----
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
        w.writerow(fila)

# ---- API CALLS ----
import time

def _get(url, _retries=2):
    """GET con rotación automática de API keys.
       - 401/403 o 'quota' en body -> key agotada, cambia.
       - 429 -> rate limit transitorio, espera y reintenta con la misma.
       - otros -> devuelve error sin marcar agotada."""
    key = api_key_activa()
    if not key:
        return None, "no_keys"
    full = url + ("&apiKey=" if "?" in url else "?apiKey=") + key
    try:
        r = requests.get(full, timeout=20)
    except Exception as e:
        return None, str(e)

    cuerpo = (r.text or "").lower()
    if r.status_code in (401, 403) or "quota" in cuerpo or "out of" in cuerpo:
        marcar_agotada(key)
        return _get(url, _retries)  # prueba con la siguiente key
    if r.status_code == 429:
        if _retries > 0:
            time.sleep(2)
            return _get(url, _retries - 1)
        return None, "rate_limited"
    if r.status_code != 200:
        return None, f"HTTP {r.status_code}"
    return r.json(), None

def descubrir_deportes_activos():
    data, err = _get("https://api.the-odds-api.com/v4/sports/")
    if not data:
        print(f"   [!] /sports error: {err}")
        return []
    eleg = []
    for d in data:
        if not d.get("active") or d.get("has_outrights"): continue
        key = d.get("key","")
        if any(key.startswith(p) for p in PREFIJOS_DEPORTES):
            eleg.append({"key":key, "grupo":d.get("group",""), "title":d.get("title",key)})
    # round-robin por grupo
    por_g = {}
    for d in eleg: por_g.setdefault(d["grupo"], []).append(d)
    sel = []
    while len(sel) < MAX_DEPORTES_POR_RUN and por_g:
        vacios = []
        for g, lst in list(por_g.items()):
            if lst and len(sel) < MAX_DEPORTES_POR_RUN:
                sel.append(lst.pop(0))
            if not lst: vacios.append(g)
        for g in vacios: del por_g[g]
    return [d["key"] for d in sel]

def pedir_cuotas(deporte):
    url = (f"https://api.the-odds-api.com/v4/sports/{deporte}/odds/"
           f"?regions={REGIONES}&markets=h2h&oddsFormat=decimal")
    data, err = _get(url)
    return deporte, (data or [])

def escanear_todo():
    deportes = descubrir_deportes_activos()
    if not deportes:
        print("   [!] Sin deportes activos detectados"); return []
    print(f"   Escaneando {len(deportes)} deportes activos")
    partidos = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(deportes),12)) as ex:
        for fut in concurrent.futures.as_completed([ex.submit(pedir_cuotas, d) for d in deportes]):
            d, ps = fut.result()
            for p in ps: p["_sport"] = d
            partidos.extend(ps)
    return partidos

# ---- VALUE BETS ----
def detectar_value_bets(partidos, bankroll):
    H = []
    for p in partidos:
        bks = p.get("bookmakers", [])
        if len(bks) < N_CASAS_MIN: continue
        cuotas_res = {}
        for bk in bks:
            for m in bk.get("markets", []):
                if m.get("key") != "h2h": continue
                for o in m.get("outcomes", []):
                    cuotas_res.setdefault(o["name"], []).append((bk.get("title","?"), o["price"]))
        if not cuotas_res: continue
        probs_b = {}
        for r, lst in cuotas_res.items():
            if len(lst) < N_CASAS_MIN: continue
            cl = filtrar_outliers([c for _,c in lst])
            if not cl: continue
            probs_b[r] = 1.0 / statistics.median(cl)
        tot = sum(probs_b.values())
        if tot <= 0: continue
        probs = {k: v/tot for k,v in probs_b.items()}
        for res, lst in cuotas_res.items():
            pr = probs.get(res, 0.0)
            if pr <= 0: continue
            cj = 1.0/pr
            for bk, c in lst:
                if c < CUOTA_MIN or c > CUOTA_MAX: continue
                edge = (c/cj) - 1.0
                if edge < EDGE_MIN or edge > EDGE_MAX: continue
                stk = kelly_stake(pr, c, bankroll)
                if stk < STAKE_MIN_EUR: continue
                H.append({
                    "id":        f"{p.get('id','')}|{res}|{bk}",
                    "match_id":  p.get("id",""),
                    "fecha":     p.get("commence_time",""),
                    "deporte":   p.get("_sport",""),
                    "partido":   f"{p.get('home_team')} vs {p.get('away_team')}",
                    "home_team": p.get("home_team",""),
                    "away_team": p.get("away_team",""),
                    "seleccion": res,
                    "casa":      bk,
                    "cuota":     c,
                    "prob_real": round(pr,4),
                    "edge_pct":  round(edge*100, 2),
                    "stake_eur": stk,
                    "creada":    dt.datetime.now().isoformat(timespec="seconds"),
                })
    return H

def registrar_pendientes(H):
    if not H: return 0
    ex = leer_csv(ARCH_PENDING)
    ya = {b["id"] for b in ex} if EVITAR_DUPLICADOS else set()
    nuevos = [h for h in H if h["id"] not in ya]
    if not nuevos: return 0
    campos = list(H[0].keys())
    for n in nuevos: anexar_csv(ARCH_PENDING, n, campos)
    return len(nuevos)

# ---- LIQUIDACIÓN ----
def pedir_scores(deporte):
    url = f"https://api.the-odds-api.com/v4/sports/{deporte}/scores/?daysFrom=3"
    data, err = _get(url)
    return data or []

def mapa_resultados(deportes):
    mapa = {}
    if not deportes: return mapa
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(deportes),12)) as ex:
        futs = {ex.submit(pedir_scores, d): d for d in deportes}
        for fut in concurrent.futures.as_completed(futs):
            for m in fut.result():
                if not m.get("completed"): continue
                sc = m.get("scores") or []
                if len(sc) < 2: continue
                try:
                    s1 = float(sc[0]["score"]); s2 = float(sc[1]["score"])
                except: continue
                g = sc[0]["name"] if s1>s2 else (sc[1]["name"] if s2>s1 else "Draw")
                mapa[m["id"]] = {"ganador":g, "score":f"{s1:g}-{s2:g}",
                                 "completed_at": m.get("last_update","")}
    return mapa

def liquidar_pendientes(estado):
    pend = leer_csv(ARCH_PENDING)
    if not pend: return 0, 0, 0.0
    deps = list({b["deporte"] for b in pend})
    mapa = mapa_resultados(deps)
    nuevas = []; liq = 0; gan = 0; pnl_t = 0.0
    campos = ["id","match_id","fecha","deporte","partido","home_team","away_team",
              "seleccion","casa","cuota","prob_real","edge_pct","stake_eur","creada",
              "resultado","score","pnl","bankroll_tras"]
    for b in pend:
        res = mapa.get(b["match_id"])
        if not res: nuevas.append(b); continue
        stk = float(b["stake_eur"]); c = float(b["cuota"])
        if res["ganador"] == b["seleccion"]:
            pnl = round(stk*(c-1), 2); resultado = "GANADA"; gan += 1
        else:
            pnl = -stk; resultado = "PERDIDA"
        estado["bankroll"] = round(estado["bankroll"]+pnl, 2)
        pnl_t += pnl; liq += 1
        fila = {k: b.get(k,"") for k in campos if k in b}
        fila.update({"resultado":resultado, "score":res["score"], "pnl":pnl,
                     "bankroll_tras":estado["bankroll"]})
        anexar_csv(ARCH_SETTLED, fila, campos)
    if pend:
        escribir_csv(ARCH_PENDING, nuevas, list(pend[0].keys()))
    estado["historial"].append({"fecha":dt.datetime.now().isoformat(timespec="seconds"),
                                "bankroll":estado["bankroll"]})
    guardar_estado(estado)
    return liq, gan, pnl_t

# ---- INFORME ----
def informe(estado):
    s = leer_csv(ARCH_SETTLED); p = leer_csv(ARCH_PENDING)
    n = len(s); gan = sum(1 for x in s if x.get("resultado")=="GANADA"); per = n-gan
    pnl = round(sum(float(x["pnl"]) for x in s), 2) if s else 0.0
    stk = round(sum(float(x["stake_eur"]) for x in s), 2) if s else 0.0
    roi = (pnl/stk*100) if stk>0 else 0.0
    try:
        dias = max((dt.datetime.now()-dt.datetime.fromisoformat(estado["inicio"])).days, 1)
    except: dias = 1
    print(f"\n+{'-'*58}+")
    print(f"|  INFORME PAPER TRADING - dia {dias:>3}{' '*(58-30-len(str(dias)))}|")
    print(f"+{'-'*58}+")
    print(f"  Bankroll inicial  : {BANKROLL_INICIAL:>7.2f} EUR")
    print(f"  Bankroll actual   : {estado['bankroll']:>7.2f} EUR")
    print(f"  P&L total         : {pnl:>+7.2f} EUR")
    print(f"  Apuestas resueltas: {n}   (gan {gan} / per {per})")
    if n: print(f"  Acierto           : {gan/n*100:>6.2f} %")
    print(f"  Volumen apostado  : {stk:>7.2f} EUR")
    if stk: print(f"  ROI               : {roi:>+6.2f} %")
    print(f"  Apuestas abiertas : {len(p)}")
    print(f"+{'-'*58}+")

def main():
    e = cargar_estado()
    print(">>> [1/3] Escaneando cuotas en paralelo ..