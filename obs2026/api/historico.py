"""Eleições passadas: seletor de anos e comparação ano a ano.

Rotas principais (mais `/comparativo/governadores`, o panorama dos 27
estados, e `/comparativo/camara`, a bancada de 513 cadeiras ano a ano):

- `/api/v1/anos` — o que existe para escolher: os anos importados no banco
  histórico mais o ano ao vivo, com os turnos de cada um;
- `/api/v1/comparativo` — o mesmo território em vários anos, lado a lado:
  eleitorado, comparecimento, abstenção, brancos, nulos, e os dois
  primeiros colocados com partido e ideologia.

## O que a comparação NÃO pode esconder

**Parcial não é final.** O ano ao vivo entra com `apurado_pct` e
`parcial: true`. Abstenção parcial cai conforme o interior chega; comparar
esse número com o final de 2018 é comparar coisas diferentes, e a tela
precisa dizer isso em vez de alinhar as colunas como se fossem iguais.

**Partido muda de nome.** A série de cada partido vem de
`politics/linhagem.py`, assinada. Sem assinatura, cada ano mostra o nome da
época e as séries não se ligam.

**Ideologia é por ano.** Cada eleição usa a classificação daquele ano
(`config/classificacao_AAAA.yaml`). Faltando o arquivo, a coluna vem nula
com motivo — nunca herda a de outro ano, porque partido troca de lado.

**Senado renova em terços.** Comparar "vagas ganhas" entre dois anos é
comparar disputas diferentes. Esta rota entrega o que houve em cada ano; a
composição das 81 cadeiras é outra tela.
"""

from __future__ import annotations

from contextlib import contextmanager

from fastapi import HTTPException, Query
from sqlalchemy import select

from ..politics import camara as CAM
from ..politics import linhagem as LIN
from ..tse.parsers.common import fold
from ..politics import partidos as PA

UFS_BR = ("ac", "al", "am", "ap", "ba", "ce", "df", "es", "go", "ma", "mg",
          "ms", "mt", "pa", "pb", "pe", "pi", "pr", "rj", "rn", "ro", "rr",
          "rs", "sc", "se", "sp", "to")

CARGOS = {"1": "presidente", "3": "governador", "5": "senador",
          "6": "deputado federal"}


def _sobre_aptos(aptos, abstencoes, brancos, nulos, validos) -> dict:
    """As quatro parcelas do eleitorado, que somam 100%.

    Abstenção vem sobre aptos e branco/nulo vêm sobre votos: comparadas
    direto, dizem coisas diferentes. Postas na mesma base, mostram o que a
    abstenção sozinha esconde — em 2022 ela subiu e, ainda assim, mais
    eleitores produziram voto válido do que em 2018.
    """
    if not aptos:
        return {"abstencao_sobre_aptos_pct": None,
                "brancos_sobre_aptos_pct": None,
                "nulos_sobre_aptos_pct": None,
                "participacao_efetiva_pct": None}
    return {
        "abstencao_sobre_aptos_pct": 100.0 * abstencoes / aptos,
        "brancos_sobre_aptos_pct": 100.0 * brancos / aptos,
        "nulos_sobre_aptos_pct": 100.0 * nulos / aptos,
        #: Votos válidos sobre o eleitorado: quantos eleitores efetivamente
        #: escolheram alguém.
        "participacao_efetiva_pct": 100.0 * validos / aptos,
    }


def register(app) -> None:
    from . import app as M
    from ..history.banco import HistTotal, HistVoto, abrir

    _bd: dict = {}

    def sessao():
        if "S" not in _bd:
            _bd["engine"], _bd["S"] = abrir()
        return _bd["S"]()

    def _classificacao_do_ano(ano: int):
        """Classificação assinada daquele ano, ou None com motivo."""
        from pathlib import Path

        from ..config import CONFIG_DIR
        from ..politics import classification as CL

        p = CONFIG_DIR / f"classificacao_{ano}.yaml"
        if not p.exists():
            return None, f"sem classificação de {ano} (config/{p.name})"
        reg = CL.load(Path(p))
        if reg.curadoria_pendente:
            return None, f"classificação de {ano} ainda não assinada"
        return reg, None

    def _ideologia(reg, sigla, numero):
        """Ideologia daquele ano, casando direto com as entradas do arquivo.

        NÃO passa pelo cadastro de partidos de 2026: o 25 de 2018 era o DEM
        e o de 2026 é o PRD, o 17 era o PSL e hoje não existe. Casar pelo
        cadastro atual devolveria a classificação do partido errado — ou
        nenhuma. Aqui a chave é número + sigla como estavam na época.
        """
        from ..politics.classification import REGRA_AGRUPAMENTO
        if reg is None:
            return None
        alvo_num = (numero or "").strip()
        alvo_sig = fold(sigla or "")
        achado = None
        for e in reg.entradas:
            if e.entidade_tipo != "partido":
                continue
            if e.numero and alvo_num and e.numero == alvo_num:
                # Número bate: confere a sigla quando as duas existem, para
                # não herdar classificação de outro partido com o mesmo
                # número em ano diferente.
                if fold(e.entidade_id) == alvo_sig or not alvo_sig:
                    achado = e
                    break
                continue
            if fold(e.entidade_id) == alvo_sig:
                achado = e
                break
        if achado is None:
            return None
        bloco = next((b for b, cats in REGRA_AGRUPAMENTO.items()
                      if achado.ideologia in cats), None)
        return {"bloco": bloco, "ideologia": achado.ideologia,
                "controversia": achado.grau_controversia}

    # ------------------------------------------------------------- anos

    @app.get("/api/v1/anos")
    def anos():
        """Anos disponíveis para escolher, com turnos e o que cada um tem."""
        with sessao() as s:
            linhas = s.execute(select(
                HistTotal.ano, HistTotal.turno, HistTotal.cargo)
                .distinct()).all()
        por_ano: dict[int, dict] = {}
        for ano, turno, cargo in linhas:
            d = por_ano.setdefault(ano, {"ano": ano, "turnos": set(),
                                         "cargos": set(), "vivo": False})
            d["turnos"].add(turno)
            d["cargos"].add(cargo)
        saida = [{"ano": d["ano"], "turnos": sorted(d["turnos"]),
                  "cargos": sorted(d["cargos"]), "ao_vivo": False,
                  "rotulo": str(d["ano"]),
                  # Bancos no formato do ao vivo (`montar-ano`), que o
                  # seletor de anos de Resultado e Mapa lê por
                  # `eleicao_passada=`.
                  "congelados": {t: (f"tse{d['ano']}t{t}"
                                     if M.ambiente_congelado(
                                         f"tse{d['ano']}t{t}") else None)
                                 for t in ("1", "2")}}
                 for d in por_ano.values()]
        # O ano ao vivo não está no banco histórico: ele é a apuração de
        # agora, com outro estatuto (parcial, pode mudar).
        # Desde 24/10/2026 o 1º turno de 2026 é banco fechado (`oficial2026`,
        # congelado) e entra no seletor como os anos passados.
        env = M._ENV_NAME
        t1 = "oficial2026"
        saida.append({"ano": 2026, "turnos": ["1"],
                      "cargos": ["1", "3", "5", "6"], "ao_vivo": True,
                      "rotulo": "2026 (ao vivo)", "ambiente": env,
                      "congelados": {
                          "1": (t1 if env != t1 and M.ambiente_congelado(t1)
                                else None),
                          "2": None}})
        saida.sort(key=lambda x: -x["ano"])
        return M.envelope(saida, snapshots=[], estado="catalogo")

    # ------------------------------------------------------- comparativo

    def _do_historico(s, ano, turno, cargo, tipo, uf, municipio):
        t = s.execute(select(HistTotal).where(
            HistTotal.ano == ano, HistTotal.turno == turno,
            HistTotal.cargo == cargo, HistTotal.tipo == tipo,
            HistTotal.uf == (uf or "br"),
            HistTotal.cd_municipio == (municipio or ""))).scalars().first()
        if t is None:
            return None
        votos = s.execute(select(HistVoto).where(
            HistVoto.ano == ano, HistVoto.turno == turno,
            HistVoto.cargo == cargo, HistVoto.tipo == tipo,
            HistVoto.uf == (uf or "br"),
            HistVoto.cd_municipio == (municipio or ""))
            .order_by(HistVoto.votos.desc())).scalars().all()
        reg, motivo = _classificacao_do_ano(ano)
        lin = LIN.load()
        topo = []
        for v in votos[:3]:
            p = PA.resolver(v.partido_numero, v.partido_sigla)
            topo.append({
                "nome": v.nome_urna, "votos": v.votos,
                "pct_validos": (100.0 * v.votos / t.validos
                                if t.validos else None),
                "partido": v.partido_sigla,
                "partido_numero": v.partido_numero,
                "partido_cor": p.cor,
                "linhagem": LIN.rotulo(v.partido_sigla, v.partido_numero,
                                       ano, lin),
                "ideologia": _ideologia(reg, v.partido_sigla,
                                        v.partido_numero),
                "eleito": v.eleito, "situacao": v.situacao,
            })
        return {
            "ano": ano, "turno": turno, "parcial": False,
            "apurado_pct": 100.0,
            "eleitorado": t.aptos, "comparecimento": t.comparecimento,
            "abstencoes": t.abstencoes,
            "abstencao_pct": (100.0 * t.abstencoes / t.aptos
                              if t.aptos else None),
            "comparecimento_pct": (100.0 * t.comparecimento / t.aptos
                                   if t.aptos else None),
            "brancos": t.brancos, "nulos": t.nulos,
            "brancos_pct": (100.0 * t.brancos / t.total_votos
                            if t.total_votos else None),
            "nulos_pct": (100.0 * t.nulos / t.total_votos
                          if t.total_votos else None),
            # Tudo sobre o eleitorado apto, para as quatro linhas somarem
            # 100%: é a única forma de comparar abstenção com branco e nulo.
            # A leitura que isso destrava (23/09): entre 2018 e 2022 a
            # abstenção subiu, mas brancos e nulos caíram pela metade, e a
            # participação efetiva subiu de 72,7% para 75,6%.
            **_sobre_aptos(t.aptos, t.abstencoes, t.brancos, t.nulos,
                           t.validos),
            "validos": t.validos, "total_votos": t.total_votos,
            "candidatos": topo,
            "classificacao_motivo": motivo,
        }

    def _primeiro_turno_2026() -> str | None:
        """O banco do 1º turno de 2026, quando o servidor está no 2º.

        Desde 24/10/2026 o 2º turno é ambiente próprio, sem Senado, Câmara
        nem os governadores decididos em 04/10: a coluna 2026 da comparação
        busca esses no 1º turno, fechado (`primeiro_turno` em sources.yaml).
        """
        from ..config import load_environment
        t1 = load_environment(M._ENV_NAME).primeiro_turno
        return t1 if t1 and M.ambiente_congelado(t1) else None

    @contextmanager
    def _sessao_2026(nome: str | None):
        """Sessão no banco ao vivo ou, com `nome`, no do 1º turno — com
        `env_atual()` apontando para ele, para `_contest_or_404` achar a
        disputa certa."""
        tk = M._ENV_PEDIDO.set(nome) if nome else None
        gen = M.get_session()
        try:
            yield next(gen), M.env_atual()
        finally:
            gen.close()
            if tk is not None:
                M._ENV_PEDIDO.reset(tk)

    def _do_ao_vivo(cargo, tipo, uf, municipio, turno="1"):
        """A coluna 2026. Senado e o 1º turno vêm do banco do 1º turno
        quando o servidor está no 2º; o 2º turno vem do ao vivo e, onde ele
        não tem a disputa (governador decidido em 04/10), do 1º, que foi o
        final."""
        t1 = _primeiro_turno_2026()
        if t1 and (cargo == "5" or turno == "1"):
            return _coluna_2026(cargo, tipo, uf, municipio, t1)
        try:
            d = _coluna_2026(cargo, tipo, uf, municipio, None)
        except HTTPException:
            d = None
        if d is None and t1:
            d = _coluna_2026(cargo, tipo, uf, municipio, t1)
        return d

    def _coluna_2026(cargo, tipo, uf, municipio, nome):
        """Uma coluna de 2026 lida do banco ao vivo (`nome=None`) ou do 1º
        turno. Parcial enquanto a apuração não fecha."""
        from ..storage.models import (Candidate, Election, Geography,
                                      ObservedResult)

        with _sessao_2026(nome) as (s, env_nome):
            ct = M._contest_or_404(s, cargo, None)
            turno_ct = str(s.execute(select(Election.turno).where(
                Election.id == ct.election_id)).scalar() or "1")
            g = s.execute(select(Geography).where(
                Geography.environment == env_nome,
                Geography.tipo == tipo,
                Geography.uf == (uf or "br"),
                *( [Geography.cd_tse == municipio] if municipio else []
                   ))).scalars().first()
            if g is None:
                return None
            t = M._latest_snapshot_ids(s, ct.id, [g.id])
            if not t:
                return None
            t = t[0]
            cands = {c.id: c for c in s.execute(select(Candidate).where(
                Candidate.contest_id == ct.id)).scalars().all()}
            linhas = s.execute(select(ObservedResult).where(
                ObservedResult.snapshot_id == t.snapshot_id,
                ObservedResult.geography_id == g.id,
                ObservedResult.contest_id == ct.id)).scalars().all()
            linhas = [r for r in linhas if r.candidate_id in cands]
            linhas.sort(key=lambda r: -r.votos)
            reg, motivo = _classificacao_do_ano(2026)
            lin = LIN.load()
            topo = []
            for r in linhas[:3]:
                c = cands[r.candidate_id]
                p = PA.resolver(c.partido_numero, c.partido_sigla)
                topo.append({
                    "nome": c.nome_urna, "votos": r.votos,
                    "pct_validos": (100.0 * r.votos / t.validos_computados
                                    if t.validos_computados else None),
                    "partido": c.partido_sigla,
                    "partido_numero": c.partido_numero,
                    "partido_cor": p.cor,
                    "linhagem": LIN.rotulo(c.partido_sigla, c.partido_numero,
                                           2026, lin),
                    "ideologia": _ideologia(reg, c.partido_sigla,
                                            c.partido_numero),
                    "eleito": c.eleito, "situacao": c.situacao or "",
                })
            apurado = (100.0 * t.secoes_totalizadas / t.secoes_total
                       if t.secoes_total else 0.0)
            return {
                "ano": 2026, "turno": turno_ct,
                # Parcial até a última seção: abstenção e brancos ainda vão
                # mudar, e comparar com um ano fechado engana.
                "parcial": t.andamento != "f",
                "apurado_pct": apurado,
                "eleitorado": t.eleitorado_total,
                "comparecimento": t.comparecimento,
                "abstencoes": t.abstencao,
                "abstencao_pct": (100.0 * t.abstencao / t.eleitorado_apurado
                                  if t.eleitorado_apurado else None),
                "comparecimento_pct": (
                    100.0 * t.comparecimento / t.eleitorado_apurado
                    if t.eleitorado_apurado else None),
                "brancos": t.brancos, "nulos": t.nulos,
                "brancos_pct": (100.0 * t.brancos / t.total_votos
                                if t.total_votos else None),
                "nulos_pct": (100.0 * t.nulos / t.total_votos
                              if t.total_votos else None),
                # Parcial: a base é o eleitorado JÁ APURADO, não o total da
                # unidade — senão a participação efetiva nasce em 5% e sobe
                # a noite toda, medindo apuração e não eleitor.
                **_sobre_aptos(t.eleitorado_apurado, t.abstencao, t.brancos,
                               t.nulos, t.validos_computados),
                "validos": t.validos_computados,
                "total_votos": t.total_votos,
                "candidatos": topo,
                "classificacao_motivo": motivo,
            }

    @app.get("/api/v1/comparativo")
    def comparativo(cargo: str = Query("1", pattern="^(1|3|5)$"),
                    uf: str | None = None,
                    municipio: str | None = None,
                    anos: str = "2018,2022,2026",
                    turno: str = "1"):
        """O mesmo território em vários anos, para ver a evolução."""
        if cargo in ("3", "5") and not uf:
            raise HTTPException(422, "governador e senador se decidem na UF: "
                                     "informe `uf`")
        tipo = "mu" if municipio else ("uf" if uf else "br")
        pedidos = [int(a) for a in anos.split(",") if a.strip().isdigit()]
        colunas = []
        with sessao() as s:
            for ano in pedidos:
                d = (_do_ao_vivo(cargo, tipo, uf, municipio, turno)
                     if ano == 2026
                     else _do_historico(s, ano, turno, cargo, tipo,
                                        uf, municipio))
                if d is None:
                    colunas.append({"ano": ano, "sem_dado": True,
                                    "motivo": "ano não importado ou "
                                              "território sem resultado"})
                else:
                    colunas.append(d)
        colunas.sort(key=lambda c: c["ano"])
        return M.envelope(
            {"cargo": cargo, "cargo_nome": CARGOS[cargo],
             "abrangencia": tipo, "uf": uf, "municipio": municipio,
             "turno": turno, "colunas": colunas,
             "linhagem_ativa": not LIN.load().pendente,
             "nota": ("Ano ao vivo entra como parcial: abstenção, brancos e "
                      "nulos ainda mudam até a última seção. Partido segue "
                      "a linhagem assinada; ideologia é a do próprio ano.")},
            snapshots=[], estado="comparativo")

    # ------------------------------------------- panorama dos governadores

    def _eleitos_uf(s, ano, uf, cargo="3"):
        """Quem se elegeu naquela UF, no RESULTADO FINAL daquele ano.

        Governador se decide no 2º turno onde houve 2º turno: comparar anos
        pelo 1º turno diria que o estado "mudou de lado" quando só mudou a
        etapa (decisão de 22/09/2026). Onde não houve 2º turno, o final é o
        1º — e o campo `turno` diz qual foi.

        Senado: a lista inteira. Em 2018 cada estado elegeu DOIS (54
        cadeiras); pegar só o mais votado apagava metade da renovação
        daquele ano (visto em 24/09). Vale a situação oficial (`eleito`);
        sem ela no arquivo, cai para os mais votados até o número de vagas.
        """
        for turno in ("2", "1"):
            votos = s.execute(select(HistVoto).where(
                HistVoto.ano == ano, HistVoto.turno == turno,
                HistVoto.cargo == cargo, HistVoto.tipo == "uf",
                HistVoto.uf == uf).order_by(
                    HistVoto.votos.desc())).scalars().all()
            if not votos:
                continue
            eleitos = [v for v in votos if v.eleito]
            if not eleitos:
                eleitos = votos[:vagas_por_uf(cargo, ano)]
            return eleitos, turno
        return [], None

    def _lideres_ao_vivo(cargo, uf, n, reg, lin):
        """Quem lidera a UF em 2026: os `n` primeiros.

        Com o servidor no 2º turno: Senado sempre do 1º turno; governador
        do 2º onde ele existe e já tem voto, senão do 1º — nas 21 UFs
        decididas em 04/10 é o resultado final, e nas 6 do 2º turno é o
        retrato de 04/10 até o primeiro voto, marcado parcial."""
        t1 = _primeiro_turno_2026()
        if t1 and cargo == "5":
            return _lideres_2026(cargo, uf, n, reg, lin, t1)
        try:
            d = _lideres_2026(cargo, uf, n, reg, lin, None)
        except HTTPException:
            d = None
        if t1 and (d is None or not d["validos"]):
            d1 = _lideres_2026(cargo, uf, n, reg, lin, t1)
            if d1 is not None and d is not None:
                # O 2º turno ainda sem voto: o 1º não é o final aqui.
                d1["parcial"] = True
                d1["aguardando_2o_turno"] = True
            d = d1 or d
        return d

    def _lideres_2026(cargo, uf, n, reg, lin, nome):
        """Os `n` primeiros da UF no banco ao vivo (`nome=None`) ou no do
        1º turno."""
        from ..storage.models import (Candidate, Election, Geography,
                                      ObservedResult)

        with _sessao_2026(nome) as (s, env_nome):
            ct = M._contest_or_404(s, cargo, None)
            turno_ct = str(s.execute(select(Election.turno).where(
                Election.id == ct.election_id)).scalar() or "1")
            g = s.execute(select(Geography).where(
                Geography.environment == env_nome, Geography.tipo == "uf",
                Geography.uf == uf)).scalars().first()
            if g is None:
                return None
            tot = M._latest_snapshot_ids(s, ct.id, [g.id])
            if not tot:
                return None
            t = tot[0]
            cands = {c.id: c for c in s.execute(select(Candidate).where(
                Candidate.contest_id == ct.id)).scalars().all()}
            linhas = [r for r in s.execute(select(ObservedResult).where(
                ObservedResult.snapshot_id == t.snapshot_id,
                ObservedResult.geography_id == g.id,
                ObservedResult.contest_id == ct.id)).scalars()
                if r.candidate_id in cands]
            if not linhas:
                return None
            linhas.sort(key=lambda r: -r.votos)
            parcial = t.andamento != "f"
            return {
                "vencedores": [{
                    "nome": cands[r.candidate_id].nome_urna,
                    "partido": cands[r.candidate_id].partido_sigla,
                    "partido_numero": cands[r.candidate_id].partido_numero,
                    "linhagem": LIN.rotulo(
                        cands[r.candidate_id].partido_sigla,
                        cands[r.candidate_id].partido_numero, 2026, lin),
                    "ideologia": _ideologia(
                        reg, cands[r.candidate_id].partido_sigla,
                        cands[r.candidate_id].partido_numero),
                    # Liderar não é eleger: sem a situação oficial, é
                    # provisório, e a série conta à parte.
                    "eleito": bool(cands[r.candidate_id].eleito),
                } for r in linhas[:n]],
                "turno": turno_ct, "eleitorado": t.eleitorado_total,
                "parcial": parcial,
                "validos": t.validos_computados,
                "apurado_pct": (100.0 * t.secoes_totalizadas / t.secoes_total
                                if t.secoes_total else 0.0),
            }

    def _composicao_senado(entradas: list[dict], validos: list[str]):
        """As 81 cadeiras de cada ano: os eleitos naquele ano (já em
        `entradas`) mais as cadeiras que continuavam.

        - ano passado: eleitas quatro anos antes e as de suplementar entre
          as duas eleições (`politico._remanescentes_passado`), com a
          ideologia pela regra de 24/09 (classificação do ano da eleição;
          sem ela, a do ano da composição pela linhagem);
        - 2026: as remanescentes informadas pelo Senado, com o partido de
          hoje.
        Vaga sem ocupante no arquivo (MT/2018, eleita cassada) fica fora e
        é contada — a tela diz."""
        from ..politics import senado as SEN
        from . import politico as POL
        saida = []
        por_ano = {}
        for a in validos:
            ano = int(a)
            comp = [dict(e) for e in entradas if e["ano"] == a]
            if ano == 2026:
                rem = SEN.remanescentes(ano_eleicao=2026)
                reg, _ = _classificacao_do_ano(2026)
                for o in rem["cadeiras"]:
                    p = PA.resolver(None, o["partido_sigla"])
                    ideo = _ideologia(reg, p.sigla, p.numero)
                    comp.append({"ano": a, "sigla": p.sigla,
                                 "numero": p.numero, "eleito": 1,
                                 "provisorio": 0, "uf": o["uf"],
                                 "bloco": (ideo or {}).get("bloco")
                                 or "sem_classificacao"})
                estado, motivo, regua = rem["estado"], rem.get("motivo"), []
            else:
                rem = POL._remanescentes_passado(ano)
                regua = set()
                for o in rem["cadeiras"]:
                    ideo, outra = POL.ideologia_remanescente(
                        o["partido_sigla"], o["partido_numero"],
                        o["eleito_em"], ano)
                    if outra:
                        regua.add(o["eleito_em"])
                    comp.append({"ano": a, "ano_origem": o["eleito_em"],
                                 "sigla": o["partido_sigla"],
                                 "numero": o["partido_numero"], "eleito": 1,
                                 "provisorio": 0, "uf": o["uf"],
                                 "suplementar": o.get("suplementar"),
                                 "bloco": (ideo or {}).get("bloco")
                                 or "sem_classificacao"})
                estado, motivo = rem["estado"], rem.get("motivo")
            pb = {b: 0 for b in ("esquerda", "centro", "direita",
                                 "sem_classificacao")}
            for e in comp:
                pb[e["bloco"]] += e["eleito"] + e["provisorio"]
            por_ano[a] = {
                "por_bloco": pb, "cadeiras": len(comp), "total": 81,
                "sem_ocupante": max(0, 81 - len(comp)),
                "remanescentes": {"estado": estado, "motivo": motivo,
                                  "n": len(rem["cadeiras"])},
                "suplementares": [
                    {"uf": e["uf"], "sigla": e["sigla"],
                     "eleito_em": e["ano_origem"]}
                    for e in comp if e.get("suplementar")],
                # Anos cujos eleitos foram classificados pela régua de
                # outro ano (2014 → classificação de 2018).
                "classificados_pela_regua_de": sorted(regua),
            }
            saida += comp
        return {"entradas": saida, "por_ano": por_ano}

    @app.get("/api/v1/comparativo/governadores")
    def comparativo_governadores(anos: str = "2018,2022,2026",
                                 cargo: str = Query("3", pattern="^(3|5)$")):
        """Os 27 estados lado a lado, eleição a eleição.

        Cada UF com quem se elegeu, o partido (na linhagem assinada) e o
        bloco daquele ano. Embaixo, dois agregados: por bloco (cadeiras e
        eleitorado) e **por partido**, na série da linhagem — "o PL saiu de
        X governos para Y". Senador conta cadeiras, não estados: 2018 e
        2026 renovam duas por UF, 2022 uma.
        """
        pedidos = [int(a) for a in anos.split(",") if a.strip().isdigit()]
        lin = LIN.load()
        ufs: dict[str, dict] = {}
        blocos: dict[int, dict] = {}
        entradas: list[dict] = []
        with sessao() as s:
            for ano in pedidos:
                reg, motivo = _classificacao_do_ano(ano)
                agreg = {b: {"ufs": 0, "cadeiras": 0, "eleitorado": 0}
                         for b in ("esquerda", "centro", "direita",
                                   "sem_classificacao")}
                n_vagas = 0
                parcial_ano = False
                sem_eleito: list[dict] = []
                for uf in UFS_BR:
                    linha = ufs.setdefault(uf, {"uf": uf, "anos": {}})
                    if ano == 2026:
                        d = _lideres_ao_vivo(cargo, uf,
                                             vagas_por_uf(cargo, ano),
                                             reg, lin)
                    else:
                        eleitos, turno = _eleitos_uf(s, ano, uf, cargo)
                        t = s.execute(select(HistTotal).where(
                            HistTotal.ano == ano, HistTotal.turno == "1",
                            HistTotal.cargo == cargo, HistTotal.tipo == "uf",
                            HistTotal.uf == uf)).scalars().first()
                        faltam = vagas_por_uf(cargo, ano) - len(eleitos)
                        if eleitos and faltam > 0:
                            # Vaga que o arquivo atual do TSE deixa sem
                            # eleito (MT/2018: eleita cassada, vaga refeita
                            # em suplementar). Dita, não preenchida.
                            sem_eleito.append({"uf": uf, "vagas": faltam})
                        d = None if not eleitos else {
                            "vencedores": [{
                                "nome": v.nome_urna,
                                "partido": v.partido_sigla,
                                "partido_numero": v.partido_numero,
                                "linhagem": LIN.rotulo(
                                    v.partido_sigla, v.partido_numero, ano,
                                    lin),
                                "ideologia": _ideologia(
                                    reg, v.partido_sigla, v.partido_numero),
                                "eleito": True,
                            } for v in eleitos],
                            "turno": turno,
                            "eleitorado": t.aptos if t else None,
                            "parcial": False,
                        }
                    if d:
                        # Os campos do primeiro eleito também no topo: é o
                        # formato que o telão e o painel já liam.
                        d = {**d["vencedores"][0], **d}
                        parcial_ano = parcial_ano or d["parcial"]
                        # Eleitorado repartido entre os eleitos da UF, para o
                        # "% do eleitorado" somar 100% também no Senado.
                        fatia = ((d.get("eleitorado") or 0)
                                 / len(d["vencedores"]))
                        for v in d["vencedores"]:
                            b = ((v["ideologia"] or {}).get("bloco")
                                 or "sem_classificacao")
                            agreg[b]["cadeiras"] += 1
                            agreg[b]["eleitorado"] += fatia
                            n_vagas += 1
                            entradas.append({
                                "ano": str(ano), "sigla": v["partido"],
                                "numero": v["partido_numero"],
                                "eleito": 1 if v["eleito"] else 0,
                                "provisorio": 0 if v["eleito"] else 1,
                                "bloco": b, "uf": uf})
                        for b in {((v["ideologia"] or {}).get("bloco")
                                   or "sem_classificacao")
                                  for v in d["vencedores"]}:
                            agreg[b]["ufs"] += 1
                    linha["anos"][str(ano)] = d
                for x in agreg.values():
                    x["eleitorado"] = round(x["eleitorado"])
                blocos[ano] = {"por_bloco": agreg, "vagas": n_vagas,
                               "parcial": parcial_ano,
                               "sem_eleito": sem_eleito,
                               "classificacao_motivo": motivo}
        validos = [str(a) for a in pedidos if blocos.get(a, {}).get("vagas")]
        series, ref, par = montar_series(entradas, validos, lin)
        series_comp, par_comp = None, None
        if cargo == "5":
            comp = _composicao_senado(entradas, validos)
            for a, c in comp["por_ano"].items():
                blocos[int(a)]["composicao"] = c
            series_comp, _, par_comp = montar_series(
                comp["entradas"], validos, lin, ref=ref,
                par=par.split("-") if par else None)
        return M.envelope(
            {"cargo": cargo,
             "cargo_nome": CARGOS[cargo],
             "unidade": "cadeiras" if cargo == "5" else "estados",
             "anos": pedidos,
             "ufs": [ufs[u] for u in UFS_BR],
             "agregado": {str(a): blocos.get(a) for a in pedidos},
             "series": series, "ordem_por": ref, "variacao_par": par,
             # Senado: a casa inteira em cada ano (eleitos no ano + quem
             # continuava), para a linha "Senado completo".
             "series_composicao": series_comp,
             "variacao_par_composicao": par_comp,
             "linhagem_ativa": not lin.pendente,
             "nota": ("Resultado final de cada ano: onde houve 2º turno, "
                      "vale o 2º. Partido segue a linhagem assinada até a "
                      "legenda de hoje; bloco é o da legenda naquele ano. O "
                      "ano ao vivo mostra quem lidera, contado à parte como "
                      "provisório."
                      + (" Senado: 2018 e 2026 renovam duas cadeiras por "
                         "estado; 2022, uma." if cargo == "5" else ""))},
            snapshots=[], estado="comparativo")

    # ------------------------------------------------ bancada da Câmara

    def _bancada_historica(s, ano):
        """(sigla, número, cadeiras) dos eleitos de um ano, somados no país."""
        from sqlalchemy import func
        linhas = s.execute(select(
            HistVoto.partido_sigla, HistVoto.partido_numero,
            func.count()).where(
                HistVoto.ano == ano, HistVoto.cargo == "6",
                HistVoto.tipo == "uf", HistVoto.eleito.is_(True))
            .group_by(HistVoto.partido_sigla,
                      HistVoto.partido_numero)).all()
        return [{"sigla": sg, "numero": n, "eleito": q, "provisorio": 0}
                for sg, n, q in linhas]

    def _bancada_ao_vivo():
        """A Câmara de 2026 como está agora: eleitos marcados pelo TSE e,
        onde a UF ainda não tem eleito, as vagas provisórias da lista — a
        mesma leitura da tela de composição, sem projeção."""
        leitor = getattr(app.state, "leitor_camara", None)
        if leitor is None:
            return None, None
        # No 2º turno não há Câmara: a de 2026 é a do 1º turno.
        por_uf = leitor(_primeiro_turno_2026()).por_uf()
        if not por_uf:
            return None, None
        f = CAM.em_formacao(por_uf, PA.resolver, lambda _p: None)
        partidos = [{"sigla": d["partido"]["sigla"],
                     "numero": d["partido"]["numero"],
                     "eleito": d["eleito"], "provisorio": d["provisorio"]}
                    for d in f["partidos"]]
        estado = {
            "parcial": (f["eleito"] < CAM.TOTAL_CADEIRAS
                        or any(r.get("andamento") != "f"
                               for r in por_uf.values())
                        or len(por_uf) < 27),
            "eleitos_tse": f["eleito"], "provisorio": f["provisorio"],
            "cadeiras_sem_dado": f["sem_dado"],
            "ufs_encerradas": sum(1 for r in por_uf.values()
                                  if r.get("andamento") == "f"),
            "vagas_declaradas": f["vagas_declaradas"],
        }
        return partidos, estado

    @app.get("/api/v1/comparativo/camara")
    def comparativo_camara(anos: str = "2018,2022,2026"):
        """As 513 cadeiras eleição a eleição, por partido e por bloco.

        **Série por linhagem.** Cada partido entra na série do partido de
        hoje (PSL e DEM de 2018 somam no União Brasil), e a série guarda de
        quais legendas veio cada número — "Podemos 2018: 24" sem dizer que
        eram PODE + PHS + PSC seria inventar uma bancada que não existiu com
        esse nome.

        **Bloco pela legenda do ano, não pela série.** A ideologia é a da
        legenda naquela eleição, no arquivo daquele ano. Uma série pode
        juntar legendas de blocos diferentes; o agregado por bloco soma
        legenda a legenda, e a série mostra o bloco só quando é um só.

        **Ano ao vivo é provisório.** Onde o TSE já marcou eleito, conta;
        onde não marcou, as vagas que o TSE já deu às listas, ocupadas pelos
        mais votados — separado em `provisorio`, nunca somado como eleito.
        """
        pedidos = [int(a) for a in anos.split(",") if a.strip().isdigit()]
        lin = LIN.load()
        entradas: list[dict] = []
        colunas: dict[str, dict] = {}
        with sessao() as s:
            for ano in pedidos:
                if ano == 2026:
                    partidos, estado = _bancada_ao_vivo()
                else:
                    partidos = _bancada_historica(s, ano)
                    estado = {"parcial": False,
                              "eleitos_tse": sum(p["eleito"]
                                                 for p in partidos),
                              "provisorio": 0} if partidos else None
                if not partidos:
                    colunas[str(ano)] = {
                        "ano": ano, "sem_dado": True,
                        "motivo": ("Câmara de 2026 ainda sem arquivo do TSE"
                                   if ano == 2026 else
                                   "ano não importado")}
                    continue
                reg, motivo = _classificacao_do_ano(ano)
                por_bloco = {"esquerda": 0, "centro": 0, "direita": 0,
                             "sem_classificacao": 0}
                for p in partidos:
                    ideo = _ideologia(reg, p["sigla"], p["numero"])
                    bloco = (ideo or {}).get("bloco") or "sem_classificacao"
                    por_bloco[bloco] += p["eleito"] + p["provisorio"]
                    entradas.append({**p, "ano": str(ano), "bloco": bloco})
                colunas[str(ano)] = {
                    "ano": ano, "sem_dado": False,
                    "total": CAM.TOTAL_CADEIRAS,
                    **estado, "por_bloco": por_bloco,
                    "classificacao_motivo": motivo}

        validos = [str(a) for a in pedidos
                   if not colunas.get(str(a), {}).get("sem_dado", True)]
        saida, ref, variacao_par = montar_series(entradas, validos, lin)
        return M.envelope(
            {"anos": pedidos, "colunas": colunas, "series": saida,
             "total": CAM.TOTAL_CADEIRAS, "ordem_por": ref,
             "variacao_par": variacao_par,
             "linhagem_ativa": not lin.pendente,
             "nota": ("Cadeiras eleitas em cada ano (a bancada da posse, não "
                      "a de hoje, que muda com troca de partido). Cada "
                      "partido segue a linhagem assinada até a legenda "
                      "atual, e a tela diz de quais legendas veio o número. "
                      "Bloco é o da legenda naquela eleição. 2026, enquanto "
                      "não fecha, mostra à parte as vagas provisórias.")},
            snapshots=[], estado="comparativo")


def _sig(sigla: str | None) -> str:
    return fold(sigla or "").replace(" ", "")


def vagas_por_uf(cargo: str, ano: int) -> int:
    """Cadeiras em disputa por UF. Senado renova 2/3 e 1/3 alternados:
    duas em 2010, 2018, 2026…; uma em 2014, 2022, 2030."""
    if cargo == "5":
        return 2 if (ano - 2018) % 8 == 0 else 1
    return 1


def montar_series(entradas: list[dict], validos: list[str], lin, *,
                  ref: str | None = None, par: list[str] | None = None):
    """Séries por partido na linhagem — a regra comum de Câmara, governador
    e senador.

    `entradas`: uma por legenda (ou por eleito) e ano, com `ano` (str),
    `sigla`, `numero`, `eleito`, `provisorio` e `bloco` daquele ano.
    Devolve (séries ordenadas, ano de referência da ordem, par da variação).

    - Cada legenda vai para a série do partido de HOJE, e a série guarda de
      quais legendas veio cada número ("União 2018 = PSL + DEM").
    - Bloco é o da legenda no ano; série com blocos diferentes vira "misto".
    - Ordem e variação usam os anos com partidos reconhecidos: no simulado,
      2026 tem só legendas fictícias ("P 9998"), e ordenar por ele poria a
      ficção no topo; "PL −98 de 2022 para 2026" seria manchete falsa.
    """
    series: dict[str, dict] = {}
    for e in entradas:
        total = e["eleito"] + e["provisorio"]
        # A linhagem parte do ano em que a legenda disputou: numa composição
        # de 2018, o PMDB eleito em 2014 é "PMDB de 2014" (→ MDB).
        rot = LIN.rotulo(e["sigla"], e["numero"],
                         int(e.get("ano_origem") or e["ano"]), lin)
        se = series.setdefault(rot["serie"], {
            "serie": rot["serie"], "sigla_hoje": rot["sigla_hoje"],
            "numero_hoje": rot["numero_hoje"], "anos": {}})
        a = se["anos"].setdefault(e["ano"], {
            "cadeiras": 0, "eleito": 0, "provisorio": 0, "componentes": []})
        a["cadeiras"] += total
        a["eleito"] += e["eleito"]
        a["provisorio"] += e["provisorio"]
        comp = next((c for c in a["componentes"]
                     if c["sigla"] == e["sigla"]
                     and c["numero"] == e["numero"]), None)
        if comp is None:
            a["componentes"].append({
                "sigla": e["sigla"], "numero": e["numero"],
                "cadeiras": total, "bloco": e["bloco"],
                "nota": rot["nota"]})
        else:
            comp["cadeiras"] += total

    reais = [a for a in validos
             if any(e["ano"] == a and e["bloco"] != "sem_classificacao"
                    for e in entradas)]
    # `ref`/`par` dados: o chamador impõe a régua de outra série (a
    # composição do Senado segue a dos eleitos — em 2026 do simulado ela
    # mistura 27 cadeiras reais com 54 fictícias e pareceria "real").
    ref = ref or (reais or validos or [None])[-1]
    par = par or (reais if len(reais) >= 2 else validos)[-2:]
    variacao_par = f"{par[0]}-{par[1]}" if len(par) == 2 else None

    saida = []
    for se in series.values():
        # Nome e cor: os do partido de hoje, pelo cadastro. Fora dele, a
        # grafia da própria legenda da série, se ela aparece em algum ano —
        # a série "19|PODE" chamava-se "PSC" só porque o PSC era o maior
        # componente em 2018. Senão, a sigla da última eleição.
        p = PA.resolver(se["numero_hoje"], se["sigla_hoje"])
        ultimo = next(se["anos"][a] for a in reversed(validos)
                      if a in se["anos"])
        propria = next((c["sigla"] for a in se["anos"].values()
                        for c in a["componentes"]
                        if _sig(c["sigla"]) == _sig(se["sigla_hoje"])), None)
        se["sigla"] = (p.sigla if p.cadastrado else
                       propria or ultimo["componentes"][0]["sigla"])
        se["cor"] = p.cor
        for a in se["anos"].values():
            a["componentes"].sort(key=lambda c: -c["cadeiras"])
            blocos = {c["bloco"] for c in a["componentes"]}
            a["bloco"] = blocos.pop() if len(blocos) == 1 else "misto"
            # Série que juntou legendas, ou com outro nome na época: a tela
            # escreve de onde veio o número.
            a["composta"] = (len(a["componentes"]) > 1
                             or _sig(a["componentes"][0]["sigla"])
                             != _sig(se["sigla"]))
        # Variação entre anos consecutivos com dado. Ano que não existe não
        # vira zero; ano que existe e a série não elegeu ninguém, sim.
        se["variacao"] = {}
        pares = list(zip(validos, validos[1:]))
        if len(par) == 2 and tuple(par) not in pares:
            pares.append(tuple(par))
        for x, y in pares:
            se["variacao"][f"{x}-{y}"] = (
                se["anos"].get(y, {}).get("cadeiras", 0)
                - se["anos"].get(x, {}).get("cadeiras", 0))
        saida.append(se)
    saida.sort(key=lambda se: (
        -(se["anos"].get(ref, {}).get("cadeiras", 0) if ref else 0),
        -sum(a["cadeiras"] for a in se["anos"].values()),
        se["sigla"]))
    return saida, ref, variacao_par
