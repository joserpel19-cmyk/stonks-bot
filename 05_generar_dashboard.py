# =============================================================
#  STONKS BOT - GENERADOR DE DASHBOARD EN DIRECTO
# -------------------------------------------------------------
#  Lee pending_bets.csv, settled_bets.csv y bankroll_state.json
#  y genera un dashboard.html auto-contenido con auto-refresh
#  cada 15 segundos.
#
#  Se ejecuta automáticamente al final de 03_paper_trading.py,
#  pero también se puede lanzar a mano:
#       python 05_generar_dashboard.py
#
#  Para ver el dashboard: abre dashboard.html con tu navegador.
#  Déjalo abierto: se refresca solo cuando haya nuevos datos.
# =============================================================

import json, csv, os, datetime as dt
from pathlib import Path

DIR = Path(__file__).parent
ARCH_PENDING  = DIR / "pending_bets.csv"
ARCH_SETTLED  = DIR / "settled_bets.csv"
ARCH_BANKROLL = DIR / "bankroll_state.json"
ARCH_API_USE  = DIR / "api_usage.json"
ARCH_OUT      = DIR / "dashboard.html"

BANKROLL_INICIAL = 20.00


def leer_json(p, default):
    if not p.exists(): return default
    try:
        raw = p.read_text(encoding="utf-8").strip()
        if not raw: return default
        return json.loads(raw)
    except Exception:
        return default


def leer_csv(p):
    if not p.exists(): return []
    with open(p, "r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def generar():
    estado  = leer_json(ARCH_BANKROLL, {"bankroll": BANKROLL_INICIAL, "inicio": dt.datetime.now().isoformat(), "historial": []})
    pending = leer_csv(ARCH_PENDING)
    settled = leer_csv(ARCH_SETTLED)
    api_use = leer_json(ARCH_API_USE, {"agotadas": []})

    bankroll = float(estado.get("bankroll", BANKROLL_INICIAL))
    pnl = round(bankroll - BANKROLL_INICIAL, 2)
    n_set = len(settled)
    n_pen = len(pending)
    ganadas  = sum(1 for s in settled if s.get("resultado") == "GANADA")
    perdidas = n_set - ganadas
    staked = sum(float(s.get("stake_eur", 0) or 0) for s in settled)
    roi = (pnl / staked * 100) if staked > 0 else 0.0
    acierto = (ganadas / n_set * 100) if n_set > 0 else 0.0

    try:
        ini = dt.datetime.fromisoformat(estado.get("inicio"))
        dias = max((dt.datetime.now() - ini).days, 1)
    except Exception:
        dias = 1

    # Exposición actual (stake bloqueado en apuestas abiertas)
    exposicion = sum(float(p.get("stake_eur", 0) or 0) for p in pending)

    # Color
    if pnl > 0:   color, arrow = "#1ea672", "&#9650;"
    elif pnl < 0: color, arrow = "#d9544d", "&#9660;"
    else:         color, arrow = "#888",    "&#9644;"

    # Historial para la gráfica de bankroll
    hist = estado.get("historial", [])[-100:]
    hist_labels = [h["fecha"][5:16].replace("T", " ") for h in hist]
    hist_vals   = [round(float(h["bankroll"]), 2) for h in hist]

    # Tabla últimas liquidadas
    ult_set = list(reversed(settled))[:20]
    filas_set = ""
    for s in ult_set:
        res = s.get("resultado", "")
        c = "#1ea672" if res == "GANADA" else "#d9544d"
        try:  pnl_s = float(s.get("pnl", 0))
        except: pnl_s = 0.0
        filas_set += (
            f"<tr>"
            f"<td>{s.get('partido','')}</td>"
            f"<td>{s.get('seleccion','')}</td>"
            f"<td style='text-align:right'>{s.get('cuota','')}</td>"
            f"<td style='text-align:right'>{s.get('stake_eur','')} &euro;</td>"
            f"<td style='color:{c};font-weight:600'>{res}</td>"
            f"<td style='text-align:right;color:{c};font-weight:600'>{pnl_s:+.2f} &euro;</td>"
            f"</tr>"
        )
    if not filas_set:
        filas_set = "<tr><td colspan=6 style='text-align:center;color:#888;padding:20px'>Todavía no hay apuestas liquidadas (los partidos aún no han terminado)</td></tr>"

    # Tabla apuestas abiertas
    ult_pen = list(reversed(pending))[:30]
    filas_pen = ""
    for p in ult_pen:
        try: edge = float(p.get("edge_pct", 0))
        except: edge = 0
        col_edge = "#1ea672" if edge >= 8 else ("#caa64b" if edge >= 5 else "#888")
        fecha = p.get("fecha", "")[:16].replace("T", " ")
        filas_pen += (
            f"<tr>"
            f"<td>{fecha}</td>"
            f"<td>{p.get('deporte','').replace('_',' ')}</td>"
            f"<td>{p.get('partido','')}</td>"
            f"<td>{p.get('seleccion','')}</td>"
            f"<td>{p.get('casa','')}</td>"
            f"<td style='text-align:right'>{p.get('cuota','')}</td>"
            f"<td style='text-align:right;color:{col_edge};font-weight:600'>{edge:.2f}%</td>"
            f"<td style='text-align:right'>{p.get('stake_eur','')} &euro;</td>"
            f"</tr>"
        )
    if not filas_pen:
        filas_pen = "<tr><td colspan=8 style='text-align:center;color:#888;padding:20px'>Sin apuestas abiertas ahora mismo</td></tr>"

    # Resumen por deporte (pendientes)
    por_deporte = {}
    for p in pending:
        d = p.get("deporte", "?").split("_")[0] or "otros"
        por_deporte.setdefault(d, {"n": 0, "stake": 0.0})
        por_deporte[d]["n"] += 1
        try: por_deporte[d]["stake"] += float(p.get("stake_eur", 0) or 0)
        except: pass
    dep_labels = list(por_deporte.keys())
    dep_counts = [por_deporte[d]["n"] for d in dep_labels]

    n_keys_total = 2
    n_keys_agot  = len(api_use.get("agotadas", []))
    keys_ok = n_keys_total - n_keys_agot

    html = f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta http-equiv="refresh" content="15">
<title>STONKS Bot - Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
* {{ box-sizing: border-box; }}
body {{
  font-family: -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif;
  margin: 0; padding: 20px;
  background: #0f1419; color: #e6e6e6;
}}
.wrap {{ max-width: 1280px; margin: 0 auto; }}
h1 {{ margin: 0 0 4px 0; font-size: 26px; }}
.sub {{ color: #888; margin-bottom: 24px; font-size: 13px; }}
.live {{
  display: inline-block; width: 8px; height: 8px; border-radius: 50%;
  background: #1ea672; margin-right: 6px;
  animation: pulse 1.5s infinite;
}}
@keyframes pulse {{ 0%{{opacity:1}}50%{{opacity:.3}}100%{{opacity:1}} }}
.grid {{
  display: grid; grid-template-columns: repeat(4, 1fr); gap: 14px; margin-bottom: 24px;
}}
.card {{
  background: #1a2028; border: 1px solid #2a3340; border-radius: 10px;
  padding: 16px; position: relative;
}}
.card .lbl {{ color: #888; font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px; }}
.card .val {{ font-size: 26px; font-weight: 700; margin-top: 6px; }}
.card .small {{ font-size: 12px; color: #888; margin-top: 4px; }}
.charts {{ display: grid; grid-template-columns: 2fr 1fr; gap: 14px; margin-bottom: 24px; }}
.chart-box {{ background: #1a2028; border: 1px solid #2a3340; border-radius: 10px; padding: 16px; }}
.chart-box h3 {{ margin: 0 0 10px 0; font-size: 14px; color: #bbb; font-weight: 600; }}
table {{ width: 100%; border-collapse: collapse; font-size: 12.5px; }}
th {{
  background: #232b35; color: #bbb; padding: 8px 10px; text-align: left;
  font-weight: 600; font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px;
}}
td {{ padding: 7px 10px; border-bottom: 1px solid #232b35; }}
tr:hover td {{ background: #1c2530; }}
.section {{ background: #1a2028; border: 1px solid #2a3340; border-radius: 10px; padding: 16px; margin-bottom: 16px; }}
.section h2 {{ margin: 0 0 12px 0; font-size: 16px; color: #ddd; }}
.footer {{ color: #555; font-size: 11px; text-align: center; padding: 20px; }}
@media (max-width: 800px) {{
  .grid {{ grid-template-columns: repeat(2, 1fr); }}
  .charts {{ grid-template-columns: 1fr; }}
}}
</style>
</head>
<body>
<div class="wrap">

<h1><span class="live"></span>STONKS Bot · Dashboard en directo</h1>
<div class="sub">
  Actualizado: {dt.datetime.now():%d/%m/%Y %H:%M:%S} ·
  Día {dias} de paper trading ·
  Auto-refresh cada 15s
</div>

<div class="grid">
  <div class="card">
    <div class="lbl">Bankroll</div>
    <div class="val" style="color:{color}">{bankroll:.2f} &euro;</div>
    <div class="small">inicial 20.00 &euro;</div>
  </div>
  <div class="card">
    <div class="lbl">P&amp;L total</div>
    <div class="val" style="color:{color}">{arrow} {pnl:+.2f} &euro;</div>
    <div class="small">ROI {roi:+.2f}%</div>
  </div>
  <div class="card">
    <div class="lbl">Apuestas resueltas</div>
    <div class="val">{n_set}</div>
    <div class="small">{ganadas} ganadas · {perdidas} perdidas · {acierto:.1f}% acierto</div>
  </div>
  <div class="card">
    <div class="lbl">Apuestas abiertas</div>
    <div class="val">{n_pen}</div>
    <div class="small">{exposicion:.2f} &euro; en juego</div>
  </div>
</div>

<div class="charts">
  <div class="chart-box">
    <h3>Evolución del bankroll</h3>
    <canvas id="chartBank" height="120"></canvas>
  </div>
  <div class="chart-box">
    <h3>Apuestas abiertas por deporte</h3>
    <canvas id="chartDep" height="120"></canvas>
  </div>
</div>

<div class="section">
  <h2>Estado del sistema</h2>
  <div style="display:flex; gap:30px; flex-wrap:wrap;">
    <div><b>APIs disponibles:</b> {keys_ok}/{n_keys_total}</div>
    <div><b>Volumen total apostado:</b> {staked:.2f} &euro;</div>
    <div><b>Modo:</b> Paper trading (sin dinero real)</div>
  </div>
</div>

<div class="section">
  <h2>Últimas apuestas liquidadas</h2>
  <table>
    <thead>
      <tr><th>Partido</th><th>Selección</th><th>Cuota</th><th>Stake</th><th>Resultado</th><th>P&amp;L</th></tr>
    </thead>
    <tbody>{filas_set}</tbody>
  </table>
</div>

<div class="section">
  <h2>Apuestas abiertas (top 30)</h2>
  <table>
    <thead>
      <tr><th>Fecha</th><th>Deporte</th><th>Partido</th><th>Selección</th><th>Casa</th><th>Cuota</th><th>Edge</th><th>Stake</th></tr>
    </thead>
    <tbody>{filas_pen}</tbody>
  </table>
</div>

<div class="footer">
  STONKS Bot · Paper trading · Sin dinero real · Esta página se actualiza sola cada 15 segundos
</div>

</div>

<script>
const histLabels = {json.dumps(hist_labels)};
const histVals   = {json.dumps(hist_vals)};
const depLabels  = {json.dumps(dep_labels)};
const depCounts  = {json.dumps(dep_counts)};

new Chart(document.getElementById('chartBank'), {{
  type: 'line',
  data: {{
    labels: histLabels,
    datasets: [{{
      label: 'Bankroll (€)',
      data: histVals,
      borderColor: '{color}',
      backgroundColor: '{color}22',
      fill: true,
      tension: 0.25,
      pointRadius: 2
    }}]
  }},
  options: {{
    plugins: {{ legend: {{ display: false }} }},
    scales: {{
      x: {{ ticks: {{ color: '#888', maxTicksLimit: 8 }}, grid: {{ color: '#232b35' }} }},
      y: {{ ticks: {{ color: '#888' }}, grid: {{ color: '#232b35' }} }}
    }}
  }}
}});

new Chart(document.getElementById('chartDep'), {{
  type: 'doughnut',
  data: {{
    labels: depLabels,
    datasets: [{{
      data: depCounts,
      backgroundColor: ['#1ea672','#4a9eda','#caa64b','#d9544d','#9b6bdf','#3ba99c','#e08a3c','#6b7e8f','#c25b90','#5bc0de','#8b9e3c']
    }}]
  }},
  options: {{
    plugins: {{ legend: {{ position: 'right', labels: {{ color: '#bbb', font: {{ size: 11 }} }} }} }}
  }}
}});
</script>
</body></html>
"""

    ARCH_OUT.write_text(html, encoding="utf-8")
    print(f"[dashboard] OK -> {ARCH_OUT}")
    return ARCH_OUT


if __name__ == "__main__":
    generar()
