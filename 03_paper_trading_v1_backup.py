# BACKUP de la VERSIÓN 1 del motor (consenso por mediana de TODAS las casas)
# ============================================================================
# Esta versión queda guardada por si quieres comparar con la nueva (v2).
# La versión v2 (sharp consensus + modelo Elo + Poisson) está en
# 03_paper_trading.py y es la que se ejecuta automáticamente en GitHub Actions.
#
# El código de aquí abajo NO se ejecuta; está dentro de un guard `if False:`
# para que Python ni siquiera lo intente importar.
# ============================================================================

if False:
    # --- empieza copia íntegra de v1 ---
    import requests, csv, json, os, statistics, concurrent.futures
    import datetime as dt
    import threading
    import time

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
    MAX_DEPORTES_POR_RUN = 8
    REGIONES = "eu,uk,us"

    # ... (lógica completa: detectar_value_bets usando mediana de TODAS las
    #      casas como fair odds, MAD outliers, sin modelo propio).
    # Si necesitas el código exacto, revisa el commit anterior en el git log.
