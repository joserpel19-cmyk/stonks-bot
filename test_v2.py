# test_v2.py
# ----------------------------------------------------------------------
# Smoke-tests del motor v2. Verifica que:
#   1) El devigging por potencia da probabilidades que suman ~1.
#   2) El Elo converge a la fuerza real con datos sintéticos.
#   3) El Poisson genera 1X2 razonable.
#   4) La integración entre devig y consenso sharp funciona.
#
# Ejecuta:
#     python test_v2.py
# Sin necesidad de claves API ni internet.
# ----------------------------------------------------------------------
import math
import random
import sys

import lib_devig as dv
import lib_elo as elo


def test_devig_basico():
    print(">>> Test 1: devigging de cuotas con vig conocido")
    # Caso: 1X2 típico. Overround alrededor del 3-5%.
    cuotas = [2.10, 3.40, 3.80]
    over = dv.overround(cuotas)
    assert 1.01 < over < 1.10, f"overround fuera de rango razonable: {over}"
    print(f"   Overround = {over:.4f}  (margen ~{(over-1)*100:.2f}%)")

    p_mult = dv.devig_multiplicative(cuotas)
    p_pow  = dv.devig_power(cuotas)
    p_shin = dv.devig_shin(cuotas)

    for nombre, ps in [("multiplicative", p_mult),
                        ("power",          p_pow),
                        ("shin",           p_shin)]:
        s = sum(ps)
        assert 0.99 < s < 1.01, f"{nombre}: suma {s} no es ~1"
        assert all(0 <= p <= 1 for p in ps), f"{nombre}: prob fuera de [0,1]"
        print(f"   {nombre:>15}: {[f'{p:.4f}' for p in ps]}  suma={s:.6f}")

    # Power debe ser distinto de multiplicative en favoritos asimétricos
    assert p_pow != p_mult, "power debería diferir de multiplicative"
    print("   [OK]")


def test_devig_extremo():
    print(">>> Test 2: devigging con favorito muy claro (cuotas 1.30 vs 4.00)")
    # Tenis: favorito vs underdog
    cuotas = [1.30, 4.00]
    p_pow = dv.devig_power(cuotas)
    s = sum(p_pow)
    assert 0.99 < s < 1.01, f"suma {s}"
    # Power method da probabilidad mayor al favorito que multiplicative
    p_mult = dv.devig_multiplicative(cuotas)
    print(f"   power favorito: {p_pow[0]:.4f}, mult favorito: {p_mult[0]:.4f}")
    print("   [OK]")


def test_consenso_sharp():
    print(">>> Test 3: consenso sharp pondera correctamente")
    bookies = {
        "Pinnacle": {"H": 2.10, "D": 3.40, "A": 3.80},
        "Betfair":  {"H": 2.12, "D": 3.45, "A": 3.85},
        "Smarkets": {"H": 2.08, "D": 3.42, "A": 3.82},
    }
    pesos = {"Pinnacle": 2.0, "Betfair": 1.5, "Smarkets": 1.0}
    cons = dv.consenso_sharp(bookies, pesos)
    s = sum(cons.values())
    assert 0.99 < s < 1.01, f"consenso suma {s}"
    assert "H" in cons and "D" in cons and "A" in cons
    # H debe ser el más probable
    assert cons["H"] > cons["D"] and cons["H"] > cons["A"]
    print(f"   consenso: {cons}, suma={s:.6f}")
    print("   [OK]")


def test_elo_converge():
    print(">>> Test 4: el Elo converge a la fuerza real (4 equipos, 500 partidos)")
    random.seed(123)
    fuerza_real = {"A": 1700, "B": 1550, "C": 1500, "D": 1400}

    modelo = {"elo": {}, "ultima_actualizacion": None, "partidos_procesados": []}

    for i in range(500):
        h, a = random.sample(list(fuerza_real.keys()), 2)
        # P(home gana) según fuerza real (con HFA)
        diff = (fuerza_real[h] + elo.ELO_HFA) - fuerza_real[a]
        p_h = 1.0 / (1.0 + 10 ** (-diff / 400.0))
        # Sample resultado realista usando lambdas
        lh = max(0.3, 1.5 + 0.5 * diff / elo.ELO_PER_GOAL)
        la = max(0.3, 1.2 - 0.5 * diff / elo.ELO_PER_GOAL)
        # Aproximación sencilla a Poisson (suficiente para test)
        gh = sum(1 for _ in range(int(2 * lh + 5)) if random.random() < lh / (2 * lh + 5))
        ga = sum(1 for _ in range(int(2 * la + 5)) if random.random() < la / (2 * la + 5))
        elo.actualizar_con_resultado(modelo, h, a, gh, ga, "test", match_id=f"sim{i}")

    # Tras 500 partidos, el orden de Elo debe coincidir con la fuerza real
    elos = {k: elo.get_elo(modelo, k, "test") for k in fuerza_real}
    print(f"   Elo final: {elos}")
    print(f"   Fuerza real: {fuerza_real}")

    orden_real = sorted(fuerza_real, key=lambda k: -fuerza_real[k])
    orden_modelo = sorted(elos, key=lambda k: -elos[k])
    print(f"   Orden real:    {orden_real}")
    print(f"   Orden modelo:  {orden_modelo}")
    assert orden_real == orden_modelo, "El Elo no aprendió el orden correcto"

    # El equipo más fuerte debe tener Elo > 1500, el más débil < 1500
    assert elos["A"] > 1500, f"A={elos['A']} debería ser > 1500"
    assert elos["D"] < 1500, f"D={elos['D']} debería ser < 1500"
    print("   [OK]")


def test_poisson_predice():
    print(">>> Test 5: predicción 1X2 razonable")
    modelo = {"elo": {}, "ultima_actualizacion": None, "partidos_procesados": []}
    elo.set_elo(modelo, "Strong", 1700, "test")
    elo.set_elo(modelo, "Weak", 1300, "test")

    p = elo.predecir_1x2(modelo, "Strong", "Weak", "test")
    s = p["home"] + p["draw"] + p["away"]
    assert 0.99 < s < 1.01, f"suma {s}"
    # El fuerte en casa debe tener clarísimo edge
    assert p["home"] > 0.6, f"home={p['home']}, debería ser >0.6"
    assert p["away"] < 0.2, f"away={p['away']}, debería ser <0.2"
    print(f"   Strong vs Weak: {p}")
    print("   [OK]")

    # Caso simétrico: equipos iguales en campo neutral... bueno, igual hay HFA
    elo.set_elo(modelo, "X", 1500, "test")
    elo.set_elo(modelo, "Y", 1500, "test")
    p2 = elo.predecir_1x2(modelo, "X", "Y", "test")
    print(f"   X vs Y (Elo igual con HFA): {p2}")
    # Con HFA, home tiene ventaja
    assert p2["home"] > p2["away"], "HFA debería favorecer al local"
    print("   [OK]")


def test_idempotencia_elo():
    print(">>> Test 6: idempotencia del updater Elo")
    modelo = {"elo": {}, "ultima_actualizacion": None, "partidos_procesados": []}
    elo.actualizar_con_resultado(modelo, "A", "B", 2, 1, "L", match_id="m1")
    elo_a_1 = elo.get_elo(modelo, "A", "L")
    # Repetir el mismo match_id no debería volver a actualizar
    elo.actualizar_con_resultado(modelo, "A", "B", 2, 1, "L", match_id="m1")
    elo_a_2 = elo.get_elo(modelo, "A", "L")
    assert abs(elo_a_1 - elo_a_2) < 1e-6, f"{elo_a_1} != {elo_a_2}"
    print(f"   Tras 2 llamadas con mismo match_id: Elo A = {elo_a_1:.2f} (sin cambio)")
    print("   [OK]")


def test_kelly_dinamico():
    print(">>> Test 7: Kelly dinámico crece con factor_confianza")
    # Importar la función del motor
    sys.path.insert(0, ".")
    import importlib.util, os
    spec = importlib.util.spec_from_file_location(
        "motor", os.path.join(os.path.dirname(__file__), "03_paper_trading.py"))
    # Le hace falta THE_ODDS_API_KEYS aunque sólo testeemos la función
    os.environ.setdefault("THE_ODDS_API_KEYS", "test_key_dummy")
    motor = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(motor)

    bankroll = 20.0
    p, cuota = 0.55, 2.10  # +EV claro
    s_bajo  = motor.kelly_stake(p, cuota, bankroll, factor_confianza=0.0)
    s_alto  = motor.kelly_stake(p, cuota, bankroll, factor_confianza=1.0)
    print(f"   factor=0.0 -> stake {s_bajo} EUR")
    print(f"   factor=1.0 -> stake {s_alto} EUR")
    assert s_alto >= s_bajo, "Kelly con factor=1 debería ser >= Kelly con factor=0"
    print("   [OK]")


def main():
    print("=" * 60)
    print("STONKS v2 - Smoke tests")
    print("=" * 60)
    tests = [
        test_devig_basico,
        test_devig_extremo,
        test_consenso_sharp,
        test_elo_converge,
        test_poisson_predice,
        test_idempotencia_elo,
        test_kelly_dinamico,
    ]
    fallidos = 0
    for t in tests:
        try:
            t()
            print()
        except AssertionError as ex:
            print(f"   [FALLO] {ex}\n")
            fallidos += 1
        except Exception as ex:
            print(f"   [ERROR] {type(ex).__name__}: {ex}\n")
            fallidos += 1
    print("=" * 60)
    if fallidos == 0:
        print("TODOS LOS TESTS PASAN")
        return 0
    else:
        print(f"{fallidos} test(s) fallaron")
        return 1


if __name__ == "__main__":
    sys.exit(main())
