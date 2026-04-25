# =============================================================
#  STONKS BOT - FASE 2.5 : EJECUCIÓN AUTOMÁTICA + EMAIL
# -------------------------------------------------------------
#  Este script hace 2 cosas:
#    1) Ejecuta el motor de paper trading (03_paper_trading.py).
#    2) Te envía un email con el informe en HTML bonito.
#
#  Está pensado para ser lanzado por el Programador de Tareas
#  de Windows, así el bot funciona solo sin que tengas que
#  abrir nada.
# =============================================================

import subprocess
import json
import csv
import smtplib
import datetime as dt
from email.message import EmailMessage
from pathlib import Path

# -------------------------------------------------------------
# Rutas
# -------------------------------------------------------------
DIR = Path(__file__).parent
SCRIPT_BOT   = DIR / "03_paper_trading.py"
ARCH_STATE   = DIR / "bankroll_state.json"
ARCH_SETTLED = DIR / "settled_bets.csv"
ARCH_PENDING = DIR / "pending_bets.csv"

BANKROLL_INICIAL = 20.00

# -------------------------------------------------------------
# Credenciales de correo (desde config_correo.py)
# -------------------------------------------------------------
try:
    from config_correo import GMAIL_USER, GMAIL_APP_PASSWORD, DESTINATARIO
except ImportError:
    raise SystemExit(
        "FALTA config_correo.py. Créalo siguiendo las instrucciones."
    )

if "PEGA_AQUI" in GMAIL_APP_PASSWORD:
    raise SystemExit(
        "Todavía no has pegado tu App Password de Gmail "
        "en config_correo.py."
    )

# -------------------------------------------------------------
# 1) Ejecutar el motor de paper trading
# -------------------------------------------------------------
print("[1/2] Ejecutando motor de paper trading ...")
proc = subprocess.run(
    ["python", str(SCRIPT_BOT)],
    cwd=str(DIR),
    capture_output=True,
    text=True,
    encoding="utf-8",
    errors="replace",
)
salida_consola = (proc.stdout or "") + (
    "\n--- STDERR ---\n" + proc.stderr if proc.stderr else ""
)
print(salida_consola)

# -------------------------------------------------------------
# 2) Leer estado y construir informe
# -------------------------------------------------------------
def leer_json(p):
    if not p.exists():
        return {}
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)

def leer_csv_list(p):
    if not p.exists():
        return []
    with open(p, "r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))

estado  = leer_json(ARCH_STATE)
settled = leer_csv_list(ARCH_SETTLED)
pending = leer_csv_list(ARCH_PENDING)

bankroll  = estado.get("bankroll", BANKROLL_INICIAL)
inicio    = estado.get("inicio", dt.datetime.now().isoformat())

n_settled = len(settled)
ganadas   = sum(1 for s in settled if s.get("resultado") == "GANADA")
perdidas  = n_settled - ganadas
pnl       = round(bankroll - BANKROLL_INICIAL, 2)
staked    = round(sum(float(s["stake_eur"]) for s in settled), 2) if settled else 0.0
roi       = (pnl / staked * 100) if staked > 0 else 0.0
acierto   = (ganadas / n_settled * 100) if n_settled > 0 else 0.0
try:
    dias = max((dt.datetime.now() - dt.datetime.fromisoformat(inicio)).days, 1)
except Exception:
    dias = 1

# Color según rendimiento
if pnl > 0:
    color = "#1ea672"     # verde
    flecha = "&#9650;"    # triangulo arriba
elif pnl < 0:
    color = "#d9544d"     # rojo
    flecha = "&#9660;"    # triangulo abajo
else:
    color = "#555"
    flecha = "&#9644;"    # linea horizontal

# -------------------------------------------------------------
# 3) Construir HTML del email
# -------------------------------------------------------------
ultimas_settled = list(reversed(settled))[:10]
top_pending     = pending[-10:][::-1]   # las 10 más recientes

def tabla_settled(rows):
    if not rows:
        return "<p><i>Aún no se ha liquidado ninguna apuesta.</i></p>"
    filas_html = ""
    for s in rows:
        c = "#1ea672" if s.get("resultado") == "GANADA" else "#d9544d"
        filas_html += (
            f"<tr>"
            f"<td style='padding:4px 8px;'>{s.get('partido','')}</td>"
            f"<td style='padding:4px 8px;'>{s.get('seleccion','')}</td>"
            f"<td style='padding:4px 8px; text-align:right;'>{s.get('cuota','')}</td>"
            f"<td style='padding:4px 8px; text-align:right;'>{s.get('stake_eur','')} &euro;</td>"
            f"<td style='padding:4px 8px; color:{c}; font-weight:bold;'>{s.get('resultado','')}</td>"
            f"<td style='padding:4px 8px; text-align:right; color:{c}; font-weight:bold;'>"
            f"{float(s.get('pnl',0)):+0.2f} &euro;</td>"
            f"</tr>"
        )
    return (
        "<table style='border-collapse:collapse; width:100%; font-size:13px;' border='1'>"
        "<tr style='background:#f0f0f0;'>"
        "<th style='padding:6px;'>Partido</th>"
        "<th style='padding:6px;'>Apuesta</th>"
        "<th style='padding:6px;'>Cuota</th>"
        "<th style='padding:6px;'>Stake</th>"
        "<th style='padding:6px;'>Resultado</th>"
        "<th style='padding:6px;'>P&amp;L</th></tr>"
        + filas_html + "</table>"
    )

def tabla_pending(rows):
    if not rows:
        return "<p><i>No hay apuestas abiertas ahora mismo.</i></p>"
    filas_html = ""
    for p in rows:
        filas_html += (
            f"<tr>"
            f"<td style='padding:4px 8px;'>{p.get('partido','')}</td>"
            f"<td style='padding:4px 8px;'>{p.get('seleccion','')}</td>"
            f"<td style='padding:4px 8px;'>{p.get('casa','')}</td>"
            f"<td style='padding:4px 8px; text-align:right;'>{p.get('cuota','')}</td>"
            f"<td style='padding:4px 8px; text-align:right;'>{p.get('edge_pct','')} %</td>"
            f"<td style='padding:4px 8px; text-align:right;'>{p.get('stake_eur','')} &euro;</td>"
            f"</tr>"
        )
    return (
        "<table style='border-collapse:collapse; width:100%; font-size:13px;' border='1'>"
        "<tr style='background:#f0f0f0;'>"
        "<th style='padding:6px;'>Partido</th>"
        "<th style='padding:6px;'>Selección</th>"
        "<th style='padding:6px;'>Casa</th>"
        "<th style='padding:6px;'>Cuota</th>"
        "<th style='padding:6px;'>Edge</th>"
        "<th style='padding:6px;'>Stake</th></tr>"
        + filas_html + "</table>"
    )

html = f"""
<html><body style="font-family: Arial, Helvetica, sans-serif; color:#222; max-width:800px; margin:auto;">

<h1 style="color:{color}; margin-bottom:0;">
  <span style="font-size:26px;">{flecha}</span>
  STONKS Bot &mdash; Informe
</h1>
<p style="color:#666; margin-top:2px;">
  {dt.datetime.now():%A, %d %B %Y - %H:%M} &middot; Día {dias} de paper trading
</p>

<div style="background:#f7f7f7; border-left:6px solid {color}; padding:14px 18px; margin:16px 0; border-radius:4px;">
  <table style="border-collapse:collapse; width:100%; font-size:15px;">
    <tr>
      <td style="padding:4px 0;"><b>Bankroll inicial</b></td>
      <td style="padding:4px 0; text-align:right;">{BANKROLL_INICIAL:.2f} &euro;</td>
    </tr>
    <tr>
      <td style="padding:4px 0;"><b>Bankroll actual</b></td>
      <td style="padding:4px 0; text-align:right; color:{color}; font-size:18px;"><b>{bankroll:.2f} &euro;</b></td>
    </tr>
    <tr>
      <td style="padding:4px 0;"><b>P&amp;L total</b></td>
      <td style="padding:4px 0; text-align:right; color:{color};"><b>{pnl:+.2f} &euro;</b></td>
    </tr>
    <tr>
      <td style="padding:4px 0;"><b>ROI</b></td>
      <td style="padding:4px 0; text-align:right; color:{color};"><b>{roi:+.2f} %</b></td>
    </tr>
    <tr>
      <td style="padding:4px 0;"><b>Apuestas resueltas</b></td>
      <td style="padding:4px 0; text-align:right;">{n_settled} &nbsp; ({ganadas} ganadas / {perdidas} perdidas)</td>
    </tr>
    <tr>
      <td style="padding:4px 0;"><b>Porcentaje de acierto</b></td>
      <td style="padding:4px 0; text-align:right;">{acierto:.1f} %</td>
    </tr>
    <tr>
      <td style="padding:4px 0;"><b>Apuestas abiertas</b></td>
      <td style="padding:4px 0; text-align:right;">{len(pending)}</td>
    </tr>
  </table>
</div>

<h3 style="margin-top:26px;">Últimas apuestas liquidadas</h3>
{tabla_settled(ultimas_settled)}

<h3 style="margin-top:26px;">Apuestas abiertas más recientes</h3>
{tabla_pending(top_pending)}

<p style="color:#888; font-size:11px; margin-top:30px; border-top:1px solid #eee; padding-top:10px;">
  STONKS Bot &middot; Modo paper trading &middot; Sin dinero real &middot; Generado automáticamente
</p>

</body></html>
"""

# -------------------------------------------------------------
# 4) Enviar el email
# -------------------------------------------------------------
asunto = (
    f"STONKS Bot  |  Bankroll {bankroll:.2f} EUR  |  "
    f"PnL {pnl:+.2f} EUR  |  {dt.date.today()}"
)

msg = EmailMessage()
msg["From"]    = GMAIL_USER
msg["To"]      = DESTINATARIO
msg["Subject"] = asunto

# Cuerpo plano de respaldo + HTML bonito
texto_plano = (
    f"STONKS BOT - INFORME\n"
    f"====================\n"
    f"Bankroll: {bankroll:.2f} EUR  (inicial 20.00)\n"
    f"P&L:      {pnl:+.2f} EUR\n"
    f"ROI:      {roi:+.2f} %\n"
    f"Apuestas: {n_settled}  ({ganadas} OK / {perdidas} KO)\n"
    f"Abiertas: {len(pending)}\n\n"
    f"--- Salida del motor ---\n{salida_consola}\n"
)
msg.set_content(texto_plano)
msg.add_alternative(html, subtype="html")

print("[2/2] Enviando correo ...")
try:
    with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as s:
        s.starttls()
        s.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        s.send_message(msg)
    print(f"    OK -> email enviado a {DESTINATARIO}")
except Exception as e:
    print("    ERROR al enviar correo:", e)
    print("    Revisa config_correo.py (App Password correcta y 2FA activo).")
