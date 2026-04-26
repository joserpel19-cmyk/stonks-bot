# STONKS Bot

Bot autónomo de **value betting** sobre cuotas deportivas, ejecutándose 24/7 gratis en GitHub Actions.

## Modo

**Paper trading** — sin dinero real. Bankroll inicial virtual: 20 €.

## Cómo funciona (v2 — gran refactor)

1. Cada 2 horas, GitHub Actions despierta el bot.
2. El bot consulta cuotas vía [The Odds API](https://the-odds-api.com/) en una **whitelist de ligas líquidas** (top 5 europeas + Champions/Europa, NBA, MLB, ATP/WTA Slams).
3. **Calcula la probabilidad real (sin vig) usando SOLO bookies afilados** (Pinnacle, Betfair Exchange, Smarkets, Matchbook) con **devigging por método de potencia**.
4. Para fútbol, consulta su **modelo Elo + Poisson propio** (entrenado con resultados históricos de [football-data.org](https://www.football-data.org/)).
5. Combina probabilidad de mercado + modelo. Sólo apuesta si **ambas señales coinciden**.
6. **Dedupe a la mejor cuota** disponible (en v1 generaba 4-5 filas duplicadas por la misma apuesta).
7. Calcula stake con **Kelly dinámico** (más generoso si modelo y mercado coinciden, más fraccional si no).
8. Liquida automáticamente, registrando **CLV (Closing Line Value)** — el indicador real de edge a largo plazo.
9. Genera el dashboard y el Excel y los publica en GitHub Pages.

## Indicadores clave (v2)

- **CLV medio**: si es positivo y consistente, el bot tiene edge real.
- **ROI**: ruido estadístico hasta tener ~100 apuestas resueltas.
- **Acierto**: depende del rango de cuotas (con cuotas 2.5-4.5 lo normal es 25-35%).

## Dashboard

[Ver dashboard en directo](./dashboard.html) (también accesible en `https://USUARIO.github.io/REPO/`).

## Estructura

| Fichero | Qué hace |
|---|---|
| `03_paper_trading.py` | Motor principal del bot |
| `05_generar_dashboard.py` | Genera el dashboard HTML |
| `06_generar_excel.py` | Genera el Excel con gráficas |
| `pending_bets.csv` | Apuestas abiertas (esperando resultado) |
| `settled_bets.csv` | Apuestas liquidadas (con resultado) |
| `bankroll_state.json` | Estado actual del bankroll + histórico |
| `dashboard.html` | Dashboard auto-actualizado |
| `STONKS_Bot_Dashboard.xlsx` | Excel con gráficas |
| `.github/workflows/stonks-bot.yml` | Configuración del cron 24/7 |

## Configuración (Secrets)

En `Settings → Secrets and variables → Actions` debes añadir:

- `THE_ODDS_API_KEYS`: claves de The Odds API separadas por coma. Ej: `clave1,clave2,clave3`
- `FOOTBALL_DATA_KEY` *(opcional pero recomendado para v2)*: token de [football-data.org](https://www.football-data.org/client/register). Sin él, el modelo Elo no se entrena y el bot funciona sólo con consenso sharp.

## Estrategia (v2)

Value betting con triple filtro:

- **Sharp consensus** (Pinnacle/Betfair/Smarkets/Matchbook) como fair odds, no la mediana de TODAS las casas.
- **Modelo Elo + Poisson** propio para fútbol (top ligas), entrenado con histórico real.
- **Devigging por método de potencia** (más preciso que normalizar 1/odds).
- Edge entre **3% y 12%** (en v1 era 3-15%, demasiado tolerante a ruido).
- Cuotas entre **1.50 y 4.50** (en v1 hasta 6.00 — exceso de ruido en longshots).
- Stake: **Kelly /25 a /12** dinámico, máximo 2% del bankroll.
- Dedupe a la **mejor cuota** disponible (no múltiples filas por el mismo bet).
- Tracking de **CLV** para medir edge real con muestras pequeñas.

## Estructura (v2)

| Fichero | Qué hace |
|---|---|
| `03_paper_trading.py` | Motor principal v2 |
| `03_paper_trading_v1_backup.py` | Backup de la versión anterior |
| `07_actualizar_elo.py` | Mantiene el modelo Elo al día con resultados |
| `lib_devig.py` | Devigging matemático (potencia, Shin) |
| `lib_elo.py` | Modelo Elo + Poisson 1X2 |
| `lib_data_football.py` | Cliente de football-data.org |
| `model_state.json` | Estado del modelo Elo (ratings + partidos procesados) |
| `test_v2.py` | Smoke tests del refactor (corre `python test_v2.py`) |
