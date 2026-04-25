# =============================================================
#  STONKS BOT - FASE 0 : PRUEBA DE CONEXIÓN CON THE ODDS API
# -------------------------------------------------------------
#  Este script NO apuesta dinero. Solo comprueba:
#    1) Que Python funciona en tu ordenador.
#    2) Que tu clave API de "The Odds API" es válida.
#    3) Que recibimos cuotas reales de partidos de fútbol.
#  Si ves "CONEXIÓN OK" al final, hemos terminado la Fase 0.
# =============================================================

import requests   # librería para hablar con internet
import json       # librería para leer la respuesta

# -------------------------------------------------------------
# 1. PEGA AQUÍ TU CLAVE DE THE ODDS API
# -------------------------------------------------------------
# La consigues GRATIS en: https://the-odds-api.com/  (botón "Get API key")
# Sustituye el texto PON_AQUI_TU_API_KEY por la clave real.
# Debe quedar entre las comillas, ejemplo:
#   API_KEY = "a1b2c3d4e5f6g7h8i9j0"
API_KEY = "c01be8a3f80b3538d472b864047cd358"

# -------------------------------------------------------------
# 2. Configuración de la llamada
# -------------------------------------------------------------
DEPORTE  = "soccer_epl"      # Premier League inglesa (cuotas estables 24/7)
REGIONES = "eu"              # casas de apuestas europeas
MERCADOS = "h2h"             # "head to head" = ganador del partido
FORMATO  = "decimal"

URL = (
    f"https://api.the-odds-api.com/v4/sports/{DEPORTE}/odds/"
    f"?apiKey={API_KEY}&regions={REGIONES}&markets={MERCADOS}&oddsFormat={FORMATO}"
)

# -------------------------------------------------------------
# 3. Pedimos los datos
# -------------------------------------------------------------
print(">>> Llamando a The Odds API ...")
try:
    respuesta = requests.get(URL, timeout=15)
except Exception as e:
    print("ERROR DE RED:", e)
    raise SystemExit(1)

print(">>> Código HTTP:", respuesta.status_code)

if respuesta.status_code != 200:
    print("ERROR. Contenido devuelto por el servidor:")
    print(respuesta.text)
    print("\nRevisa que la API_KEY esté bien pegada y no tenga espacios.")
    raise SystemExit(1)

# -------------------------------------------------------------
# 4. Mostramos un resumen bonito
# -------------------------------------------------------------
partidos = respuesta.json()
print(f">>> Partidos recibidos: {len(partidos)}\n")

for p in partidos[:3]:               # mostramos solo los 3 primeros
    local = p.get("home_team")
    visit = p.get("away_team")
    hora  = p.get("commence_time")
    print(f"Partido: {local}  vs  {visit}   ({hora})")
    for casa in p.get("bookmakers", [])[:2]:
        nombre_casa = casa["title"]
        cuotas = casa["markets"][0]["outcomes"]
        cuotas_txt = "  ".join(f"{c['name']}={c['price']}" for c in cuotas)
        print(f"   - {nombre_casa:15s} {cuotas_txt}")
    print()

# -------------------------------------------------------------
# 5. Créditos / cuota de la API (nos interesa saber cuántas
#    llamadas gratis nos quedan este mes).
# -------------------------------------------------------------
print("Llamadas usadas este mes :", respuesta.headers.get("x-requests-used"))
print("Llamadas restantes       :", respuesta.headers.get("x-requests-remaining"))

print("\n==============================")
print("   CONEXIÓN OK - FASE 0 LISTA")
print("==============================")
