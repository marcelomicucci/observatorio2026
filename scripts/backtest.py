"""Backtest por cortes de cobertura sobre uma disputa ja coletada (§11.2).

    python scripts/backtest.py al senador

Lembrete que o relatorio repete: quando so existem resultados finais, a ordem
de chegada e CONSTRUIDA por nos. Isto e teste de estresse, nao replay
historico real.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from obs2026.config import DATA_DIR                      # noqa: E402
from obs2026.engine import backtest as B, models as M     # noqa: E402
from obs2026.storage.raw import RawArchive                # noqa: E402
from obs2026.tse.parsers import ea20                      # noqa: E402

CARGOS = {"presidente": "1", "governador": "3", "senador": "5"}


def carregar(uf: str, cargo_cd: str, env: str = "simulado2026"):
    arc = RawArchive(DATA_DIR / "raw", env)
    vistos = {}
    for day in arc.days():
        for ref in arc.manifest(day):
            if (ref.kind == "ea20" and ref.path and ref.cargo == cargo_cd
                    and ref.mun and ref.uf == uf):
                vistos[ref.mun] = ref
    out = []
    for mun, ref in vistos.items():
        r = ea20.parse(arc.load(ref))
        out.append(M.UnitObservation(
            unit=f"{uf}:{mun}", uf=uf,
            stratum=M.estrato(uf, r.electorate.te), result=r))
    return out


def main(uf: str, cargo_key: str):
    cd = CARGOS[cargo_key]
    obs = carregar(uf, cd)
    if not obs:
        sys.exit(f"sem dados de {cargo_key}/{uf}. "
                 f"Rode: python scripts/coletar_uf.py {uf} {cargo_key}")
    seats = obs[0].result.cargo.vagas
    senado = seats > 1
    cands = [c.sqcand for c in obs[0].result.cargo.candidatos
             if c.conta_como_valido]
    real = B.verdade(obs, cands)

    validos = f"{real['validos']:,}".replace(",", ".")
    print(f"\n{cargo_key.upper()} {uf.upper()} — {len(obs)} unidades, "
          f"{len(cands)} candidatos, {validos} votos validos")
    print(f"vagas: {seats}")
    print("\nAVISO: a ordem de chegada abaixo e SINTETICA. Teste de estresse, "
          "nao replay historico real (§11.2).\n")

    for ordem in (B.ArrivalOrder.ELEITORADO_CRESCENTE, B.ArrivalOrder.ALEATORIA):
        print(f"--- ordem: {ordem.value} ---")
        print(f"{'corte':>6} {'cob':>7} {'mod':>4} {'err_med':>8} "
              f"{'err_max':>8} {'c80':>5} {'c95':>5} {'larg80':>7}  vagas")
        reps = B.run_suite(obs, cands, seats=seats, senado=senado,
                           ordens=(ordem,), n_sims=1200)
        for r in reps:
            print(f"{r.corte_alvo_pct:5.0f}% {r.cobertura_eleitorado_pct:6.1f}% "
                  f"{r.modelo.upper():>4} {r.erro_abs_pp_mediano:8.2f} "
                  f"{r.erro_abs_pp_maximo:8.2f} "
                  f"{r.cobertura_intervalo_80*100:4.0f}% "
                  f"{r.cobertura_intervalo_95*100:4.0f}% "
                  f"{r.largura_media_80_pp:7.2f}  "
                  f"{'sim' if r.acertou_vagas else 'NAO'}")
        g = B.gate_m1_vence_m0(reps)
        print(f"gate M1>M0: {'PASSOU' if g['passou'] else 'NAO PASSOU'}  "
              f"({g['m1_melhor']} vitorias, {g['empates']} empates, "
              f"{g['m1_pior']} derrotas)\n")


if __name__ == "__main__":
    if len(sys.argv) < 3 or sys.argv[2].lower() not in CARGOS:
        sys.exit("uso: backtest.py <uf> <presidente|governador|senador>")
    main(sys.argv[1].lower(), sys.argv[2].lower())
