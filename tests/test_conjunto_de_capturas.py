"""O `snapshot_set_id` de cada projeção e a conferência contra o manifesto.

A projeção registrada tem de dizer com que arquivos foi feita, de um jeito
que se confira depois sem depender do horário.
"""

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from obs2026.config import load_environment, load_thresholds
from obs2026.engine import audit, conjuntos, models as M, runner as R
from obs2026.engine.incremental import CarregadorIncremental
from obs2026.engine.registro_continuo import RegistradorContinuo
from obs2026.storage.raw import RawArchive

FIX = Path(__file__).parent / "fixtures" / "simulado2026"
T0 = datetime(2026, 10, 4, 20, 0, tzinfo=timezone.utc)
MUNS = ("01120", "01139", "01007")


def _payload(mun: str, votos: int = 0) -> bytes:
    d = json.loads((FIX / "ea20_ac01120_sen.json").read_text(encoding="utf-8"))
    d["cdabr"] = mun
    d["idg"] = f"{mun}-{votos}"          # conteúdo diferente, hash diferente
    return json.dumps(d).encode("utf-8")


def _capturar(arc: RawArchive, mun: str, minuto: int, votos: int = 0,
              status: int = 200):
    corpo = _payload(mun, votos) if status == 200 else None
    return arc.store(
        key=f"ea20:5:ac:{mun}", kind="ea20", url="http://teste", status=status,
        body=corpo, sha256=hashlib.sha256(corpo).hexdigest() if corpo else None,
        captured_at=T0 + timedelta(minutes=minuto), source_generated_at=None,
        idg=None, etag=None, last_modified=None, parser_version="t",
        election="21272", cargo="5", uf="ac", mun=mun)


def _arquivo(tmp_path) -> RawArchive:
    arc = RawArchive(tmp_path / "raw", "simulado2026")
    for i, mun in enumerate(MUNS):
        _capturar(arc, mun, i)
    return arc


def test_id_depende_so_do_conteudo_e_nao_da_ordem(tmp_path):
    obs = R.carregar_observacoes(_arquivo(tmp_path), "5", "ac")
    assert len(obs) == 3 and all(o.sha256 for o in obs)
    a = R.id_do_conjunto(obs)
    assert a == R.id_do_conjunto(list(reversed(obs)))
    assert len(a) == 64
    assert R.id_do_conjunto(obs[:2]) != a


def test_sem_hash_em_alguma_unidade_nao_ha_id(tmp_path):
    obs = R.carregar_observacoes(_arquivo(tmp_path), "5", "ac")
    o = obs[0]
    sem = M.UnitObservation(o.unit, o.uf, o.stratum, o.result, o.capital)
    assert R.id_do_conjunto([sem, *obs[1:]]) is None
    assert R.id_do_conjunto([]) is None


def test_carga_incremental_e_completa_dao_o_mesmo_id(tmp_path):
    arc = _arquivo(tmp_path)
    inc = CarregadorIncremental(archive=arc, cargo="5")
    inc.atualizar()
    completo, posicao = R.carregar_com_posicao(arc, "5", "ac")
    assert R.id_do_conjunto(inc.observacoes("ac")) == R.id_do_conjunto(completo)
    assert inc.posicao() == posicao

    antes = R.id_do_conjunto(inc.observacoes("ac"))
    _capturar(arc, MUNS[0], 10, votos=7)             # o município mudou
    _capturar(arc, MUNS[1], 11, status=304)          # este não: 304 sem corpo
    inc.atualizar()
    depois = R.id_do_conjunto(inc.observacoes("ac"))
    assert depois != antes
    assert depois == R.id_do_conjunto(R.carregar_observacoes(arc, "5", "ac"))
    assert sum(inc.posicao().values()) > sum(posicao.values())


def _projetar(tmp_path, arc):
    env = load_environment("simulado2026")
    diario = audit.Diario(tmp_path / "previsoes", env.name)
    reg = RegistradorContinuo(env=env, thr=load_thresholds(), archive=arc,
                              diario=diario, n_sims=200, cargos=("5",))
    reg._disputas = lambda cargo: ["ac"]
    return reg, diario


def test_registro_leva_o_id_e_o_conjunto_se_refaz_pelo_manifesto(tmp_path):
    arc = _arquivo(tmp_path)
    reg, diario = _projetar(tmp_path, arc)
    assert reg._rodada()["registradas"] == 1
    _capturar(arc, MUNS[2], 20, votos=3)
    assert reg._rodada()["registradas"] == 1

    registros = diario.ler()
    assert len(registros) == 2
    assert registros[0].snapshot_set_id != registros[1].snapshot_set_id
    anot = conjuntos.ler_anotacoes(diario.caminho_conjuntos)
    assert [a["snapshot_set_id"] for a in anot] == [
        r.snapshot_set_id for r in registros]
    assert [a["registro_id"] for a in anot] == [r.registro_id for r in registros]

    res = conjuntos.conferir(arc, anot)
    assert res["iguais"] == 2 and not res["divergentes"]


def test_conferencia_acusa_conjunto_que_nao_e_o_do_manifesto(tmp_path):
    arc = _arquivo(tmp_path)
    reg, diario = _projetar(tmp_path, arc)
    reg._rodada()
    anot = conjuntos.ler_anotacoes(diario.caminho_conjuntos)
    # Uma posição que não é a que o motor leu: falta a última captura.
    dia, ate = next(iter(anot[0]["manifesto_ate"].items()))
    linhas = (arc.root / "manifest" / f"{dia}.jsonl").read_bytes().splitlines(True)
    anot[0]["manifesto_ate"][dia] = ate - len(linhas[-1])
    res = conjuntos.conferir(arc, anot)
    assert res["iguais"] == 0
    assert res["divergentes"][0]["unidades_refeitas"] == 2


def test_diario_continua_no_formato_que_um_servidor_antigo_le(tmp_path):
    arc = _arquivo(tmp_path)
    reg, diario = _projetar(tmp_path, arc)
    reg._rodada()
    linha = json.loads(diario.caminho.read_text(encoding="utf-8").splitlines()[0])
    campos = {f for f in audit.Registro.__dataclass_fields__}
    assert set(linha) == campos            # nenhum campo novo no registro
    assert isinstance(linha["snapshot_set_id"], str)
