# STONKS Bot

Bot autónomo de **value betting** sobre cuotas deportivas, ejecutándose 24/7 gratis en GitHub Actions.

## Modo

**Paper trading** — sin dinero real. Bankroll inicial virtual: 20 €.

## Cómo funciona

1. Cada 4 horas, GitHub Actions despierta el bot.
2. El bot consulta cuotas en ~12 deportes vía [The Odds API](https://the-odds-api.com/) (rotación de claves).
3. Filtra cuotas anómalas con MAD (Median Absolute Deviation).
4. Detecta apuestas con ventaja estadística (`edge` entre 3% y 15%).
5. Calcula stake con **Kelly fraccional /20** (apuesta céntimos sobre 20 €).
6. Liquida automáticamente las apuestas cuyos partidos ya terminaron.
7. Genera el dashboard y el Excel y los publica en GitHub Pages.

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

## Estrategia

Value betting puro con gestión de riesgo extrema:

- Edge mínimo: **3%**
- Edge máximo (sanity cap): **15%**
- Stake máximo: **2%** del bankroll
- Kelly: dividido entre **20** (ultra conservador)
- Mínimo **5 casas** cotizando para fiarnos del precio justo
