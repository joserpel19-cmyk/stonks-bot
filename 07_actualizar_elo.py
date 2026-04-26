# 07_actualizar_elo.py
# ----------------------------------------------------------------------
# Mantiene el modelo Elo al día.
#
# Lógica:
#   1) Si el modelo está vacío -> backfill 2 temporadas de cada liga
#      (2024-25 y 2025-26). Se hace una sóla vez y queda en model_state.json.
#   2) Si ya hay modelo -> sólo trae los partidos terminados de los últimos
#      7 días y aplica los que aún no están procesados (idempotente).
#
# Diseñado para ejecutarse en GitHub Actions cada noche (cron diario).
# Si falla la API (no hay token, rate limit, etc.) no rompe nada — el bot
# de paper trading sigue funcionando, sólo que sin el "boost" del modelo.
# ----------------------------------------------------------------------
from __future__ import annotations
import datetime as dt
import os

import lib_elo as elo
import lib_data_football as fd

# Temporadas a backfillear cuando el modelo está vacío
TEMPORADAS_BACKFILL = [2024, 2025]


def main():
    if not fd.disponible():
        print("[elo-update] FOOTBALL_DATA_KEY no definido. Saltando actualización.")
        print("            Crea cuenta gratis en https://www.football-data.org/client/register")
        print("            y añade el token como Secret en GitHub: FOOTBALL_DATA_KEY")
        return

    modelo = elo.cargar_modelo()
    n_proc = len(modelo.get("partidos_procesados", []))
    print(f"[elo-update] Modelo actual: {len(modelo.get('elo', {}))} equipos / "
          f"{n_proc} partidos procesados")

    nuevos = 0
    for liga_odds, comp_fd in fd.LIGA_MAP.items():
        try:
            if n_proc < 50:
                # Backfill inicial: temporadas completas
                for season in TEMPORADAS_BACKFILL:
                    print(f"[elo-update] Backfill {comp_fd} temporada {season}...")
                    matches = fd.temporada_completa(comp_fd, season)
                    print(f"             -> {len(matches)} partidos terminados")
                    for m in matches:
                        d_h, d_a = elo.actualizar_con_resultado(
                            modelo, m["home"], m["away"], m["goles_h"], m["goles_a"],
                            liga=liga_odds, match_id=f"fd_{m['id']}"
                        )
                        if d_h != 0 or d_a != 0:
                            nuevos += 1
            else:
                # Modo incremental: últimos 7 días
                matches = fd.partidos_finalizados(comp_fd, dias_atras=7)
                if matches:
                    print(f"[elo-update] {comp_fd}: {len(matches)} partidos recientes")
                for m in matches:
                    d_h, d_a = elo.actualizar_con_resultado(
                        modelo, m["home"], m["away"], m["goles_h"], m["goles_a"],
                        liga=liga_odds, match_id=f"fd_{m['id']}"
                    )
                    if d_h != 0 or d_a != 0:
                        nuevos += 1
        except Exception as ex:
            print(f"[elo-update] Error en {comp_fd}: {ex}")
            continue

    modelo["ultima_actualizacion"] = dt.datetime.utcnow().isoformat(timespec="seconds") + "Z"
    elo.guardar_modelo(modelo)
    print(f"[elo-update] OK. Nuevos partidos integrados: {nuevos}. "
          f"Total equipos: {len(modelo['elo'])}.")


if __name__ == "__main__":
    main()
