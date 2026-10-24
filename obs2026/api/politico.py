"""Relatórios políticos do telão: partido, ideologia e composição (§6).

Três rotas, todas de leitura:

- `/api/v1/partidos` — cadastro de partidos (cor, nome) e a classificação
  ideológica por número, com o estado da curadoria;
- `/api/v1/politico/resumo` — votos por partido e por bloco, e territórios
  liderados por partido e por bloco (governos, para governador);
- `/api/v1/composicao` — Senado (27 remanescentes + 54 em disputa) e Câmara
  (513, só o resultado do TSE).

## Ideologia só com curadoria assinada

Enquanto `classificacao_2026.yaml` não tiver curador, todo campo de
ideologia sai `null` e o envelope diz por quê. O partido continua: é fato,
não opinião. Nada aqui atribui a um candidato a ideologia da coligação —
vale o partido do próprio candidato (§6: a cor do cabeça de chapa não é
atribuída à coligação inteira).

## Líder não é eleito

Na composição do Senado, uma vaga em disputa aparece em quatro estados, e
cada um com desenho próprio na tela: `eleito` (situação oficial do TSE),
`definido` (matematicamente, pela mesma regra do mapa), `liderando`
(apuração parcial — pode virar) e `aguardando` (sem voto contado). Somar
"liderando" a "eleito" num número só seria o erro de manchete que o §4.7
proíbe; a resposta traz as quatro contagens separadas.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..engine import nivel as NV
from ..politics import camara as CAM
from ..politics import classification as CL
from ..politics import partidos as PA
from ..politics import senado as SEN

BLOCOS = ("esquerda", "centro", "direita")

_leitores: dict[str, CAM.LeitorCamara] = {}


def _classificacao():
    reg = CL.load()
    ativa = not reg.curadoria_pendente and not CL.validar(reg)
    return reg, ativa


def _ideologia(reg, ativa: bool):
    """Função partido → classificação (ou None). Fechada sobre o registro
    lido uma vez por pedido: a mesma resposta não pode misturar versões."""
    def f(p: PA.Partido) -> dict | None:
        if not ativa or not p.cadastrado:
            return None
        e = reg.por_partido(p.sigla, p.numero)
        if e is None:
            return None
        bloco = next(b for b, cats in CL.REGRA_AGRUPAMENTO.items()
                     if e.ideologia in cats)
        return {"categoria": e.ideologia, "rotulo": CL.ROTULOS[e.ideologia],
                "bloco": bloco, "cor": CL.PALETA[e.ideologia],
                "simbolo": CL.SIMBOLOS[e.ideologia],
                "grau_controversia": e.grau_controversia}
    return f


def _meta_classificacao(reg, ativa: bool) -> dict:
    return {
        "estado": "disponivel" if ativa else "curadoria_pendente",
        "versao": reg.versao, "curador": reg.curador,
        "publicada_em": (reg.publicada_em.isoformat()
                         if hasattr(reg.publicada_em, "isoformat") else None),
        "motivo": (None if ativa else
                   "Classificação ideológica aguardando curadoria assinada "
                   "(§6). O mapa por partido segue disponível."),
        "paleta": CL.PALETA, "rotulos": CL.ROTULOS, "simbolos": CL.SIMBOLOS,
        "regra_agrupamento": {k: list(v)
                              for k, v in CL.REGRA_AGRUPAMENTO.items()},
        "sem_classificacao": CL.SEM_CLASSIFICACAO,
        "nota": "Classificação analítica do Observatório, não atributo "
                "oficial do TSE.",
    }


def _remanescentes_passado(ano: int) -> dict:
    """As cadeiras que NÃO estavam em disputa numa eleição passada: as
    eleitas quatro anos antes, lidas do banco histórico.

    É a composição ELEITA (quem ganhou a cadeira e por qual partido), não a
    que estava em exercício: suplente assumindo e troca de partido no
    mandato ficam de fora, e a nota diz isso. Sem aquele ano importado — é o
    caso de 2014, para a tela de 2018 —, as cadeiras não são inventadas: a
    tela mostra o que falta."""
    from sqlalchemy import select as sel

    from ..history import banco as HB
    anterior = ano - 4
    fonte = f"TSE, eleitos ao Senado em {anterior} (banco histórico)"
    faltam = 81 - 27 * (2 if (ano - 2018) % 8 == 0 else 1)
    base = {"obtido_em": None, "fonte": fonte,
            "rotulo": f"eleitas em {anterior}", "eleitos_em": anterior,
            "nota": (f"Eleitos em {anterior}, pelo partido da eleição; não "
                     "acompanha suplente nem troca de partido no mandato.")}
    linhas = []
    if HB.CAMINHO.exists():
        _, S = HB.abrir(criar=False)
        with S() as s:
            linhas = s.execute(sel(HB.HistVoto).where(
                HB.HistVoto.ano == anterior, HB.HistVoto.cargo == "5",
                HB.HistVoto.tipo == "uf",
                HB.HistVoto.eleito.is_(True))).scalars().all()
            # Suplementares entre as duas eleições: quem OCUPOU a cadeira
            # cuja eleição foi anulada (MT: eleita em 2018 cassada, Fávaro
            # eleito em 2020). Decisão de 24/09/2026: a composição mostra
            # quem ocupou a vaga, marcado.
            if linhas:
                linhas += s.execute(sel(HB.HistVoto).where(
                    HB.HistVoto.ano > anterior, HB.HistVoto.ano <= ano,
                    HB.HistVoto.cargo == "5", HB.HistVoto.tipo == "uf",
                    HB.HistVoto.eleito.is_(True),
                    HB.HistVoto.situacao.like("%SUPLEMENTAR%"))
                ).scalars().all()
    if not linhas:
        return {**base, "estado": "indisponivel", "cadeiras": [],
                "motivo": (f"Resultado do Senado de {anterior} não "
                           f"importado: as {faltam} cadeiras eleitas em "
                           f"{anterior} ficam fora desta composição "
                           "(importar com `obs2026 importar-historico "
                           f"--ano {anterior}`).")}
    esperadas = 27 * (2 if (anterior - 2018) % 8 == 0 else 1)
    faltam_eleitas = esperadas - len(linhas)
    return {**base,
            # Menos eleitas que cadeiras (MT/2018: eleita cassada, vaga
            # refeita em suplementar): a composição fica com a vaga
            # faltando, e a tela diz por quê.
            "estado": "ok" if faltam_eleitas <= 0 else "incompleto",
            "motivo": (None if faltam_eleitas <= 0 else
                       f"{faltam_eleitas} das {esperadas} cadeiras eleitas "
                       f"em {anterior} sem eleito no arquivo atual do TSE "
                       "(eleição da vaga anulada e refeita em suplementar, "
                       "que não entra aqui)."),
            "cadeiras": [{"uf": v.uf, "nome": v.nome_urna,
                          "partido_sigla": v.partido_sigla,
                          "partido_numero": v.partido_numero,
                          "eleito_em": v.ano,
                          "suplementar": "SUPLEMENTAR" in (v.situacao or ""),
                          "suplente_em_exercicio": None,
                          "mandato_fim": anterior + 9}
                         for v in linhas]}


def ideologia_remanescente(sigla: str, numero: str, eleito_em: int,
                           ano_composicao: int) -> tuple[dict | None, bool]:
    """Ideologia de quem ocupa uma cadeira eleita noutro ano.

    Regra (24/09/2026): a classificação do ANO DA ELEIÇÃO dele; sem ela
    (2014, suplementar de 2020), a do ano da composição, com o partido
    levado pela linhagem até aquele ano (PMDB de 2014 → MDB de 2018).
    Devolve (ideologia, se usou a régua de outro ano)."""
    from ..config import CONFIG_DIR
    from ..contexto import ANO_ELEICAO
    from ..politics import linhagem as LIN
    proprio = (CONFIG_DIR / f"classificacao_{eleito_em}.yaml").exists()
    ano_cls = eleito_em if proprio else ano_composicao
    sg, num = sigla, numero
    if not proprio:
        chave, _ = LIN.serie(sigla, numero, eleito_em, ate=ano_composicao)
        num, _, sg = chave.partition("|")
    tk = ANO_ELEICAO.set(ano_cls if ano_cls != 2026 else None)
    try:
        p = PA.resolver(num or None, sg)
        return _ideologia(*_classificacao())(p), not proprio
    finally:
        ANO_ELEICAO.reset(tk)


def _vazio_blocos() -> dict:
    return {b: {"votos": 0, "pct": None} for b in
            (*BLOCOS, "sem_classificacao")}


def register(app) -> None:
    from . import app as M
    from ..config import DATA_DIR
    from ..engine import decided as DC
    from ..storage.models import (Candidate, Geography, ObservedResult,
                                  SourceSnapshot)
    from ..storage.raw import RawArchive

    web = M._WEB

    @app.get("/politico.js", include_in_schema=False)
    def politico_js():
        return FileResponse(web / "politico.js",
                            media_type="application/javascript",
                            headers={"Cache-Control": "no-cache"})

    # ------------------------------------------------------------ partidos

    @app.get("/api/v1/partidos")
    def partidos():
        reg, ativa = _classificacao()
        corpo = PA.payload()
        corpo["classificacao"] = _meta_classificacao(reg, ativa)
        # A proposta vai junto mesmo pendente — é o que se revisa antes de
        # assinar —, marcada como tal. O telão só pinta com `disponivel`.
        corpo["classificacao"]["por_partido"] = CL.por_numero(reg)
        return M.envelope(corpo, snapshots=[], estado="cadastro")

    # --------------------------------------------------- apoio: por UF/BR

    def _unidades(s: Session, ct, tipo: str) -> list[dict]:
        """Último total de cada unidade do tipo pedido, com votos válidos
        por candidato. Uma consulta para todos os resultados."""
        geos = {g.id: g for g in s.execute(select(Geography).where(
            Geography.environment == M.env_atual(),
            Geography.tipo == tipo)).scalars().all()}
        if not geos:
            return []
        totais = M._latest_snapshot_ids(s, ct.id, list(geos))
        cands = {c.id: c for c in s.execute(select(Candidate).where(
            Candidate.contest_id == ct.id)).scalars().all()}
        por_snap: dict[tuple[int, int], list] = {}
        ids = [t.snapshot_id for t in totais]
        for i in range(0, len(ids), 900):
            for r in s.execute(select(
                    ObservedResult.snapshot_id, ObservedResult.geography_id,
                    ObservedResult.candidate_id, ObservedResult.votos,
                    ObservedResult.dvt).where(
                    ObservedResult.snapshot_id.in_(ids[i:i + 900]),
                    ObservedResult.contest_id == ct.id)):
                por_snap.setdefault((r.snapshot_id, r.geography_id),
                                    []).append(r)
        out = []
        for t in totais:
            rows = [r for r in por_snap.get((t.snapshot_id, t.geography_id),
                                            [])
                    if r.dvt == "Válido" and r.candidate_id in cands]
            rows.sort(key=lambda r: -r.votos)
            # Votos sub judice por candidato: sem o detalhe, a definição
            # trata qualquer voto sub judice como capaz de mudar o resultado
            # (DF e RN ficaram "liderando" com 100% apurado em 04/10/2026).
            sj = {cands[r.candidate_id].sqcand: r.votos
                  for r in por_snap.get((t.snapshot_id, t.geography_id), [])
                  if r.dvt == "Anulado sub judice" and r.candidate_id in cands}
            out.append({"geo": geos[t.geography_id], "t": t, "sj": sj,
                        "rows": [(cands[r.candidate_id], r.votos)
                                 for r in rows]})
        return out

    def _snaps(s: Session, unidades: list[dict]):
        ids = [u["t"].snapshot_id for u in unidades]
        if not ids:
            return []
        return s.execute(select(SourceSnapshot).where(
            SourceSnapshot.id.in_(ids))).scalars().all()

    def _estado_vagas(u: dict, vagas: int,
                      segundo_turno: bool) -> tuple[str, list]:
        """Estado das vagas de uma UF: eleito / definido / liderando /
        aguardando, com os ocupantes (os `vagas` primeiros)."""
        t, rows = u["t"], u["rows"]
        if not rows or not t.validos_computados:
            return "aguardando", []
        from ..contexto import ano_passado
        if ano_passado() is not None:
            # Eleição passada: só quem o TSE marca como eleito. Vaga sem
            # eleito no arquivo (MT/2018: eleita cassada, vaga refeita em
            # suplementar) fica sem ocupante — nunca o próximo da lista.
            eleitos = [(c, v) for c, v in rows if c.eleito][:vagas]
            if eleitos or vagas > 1:
                return "eleito", eleitos
            # Governador no 1º turno de um ano passado, em UF que foi ao 2º:
            # também é situação oficial ("2º turno"), não aritmética.
            from ..tse.parsers.common import fold
            if fold(rows[0][0].situacao or "").startswith("2"):
                return "segundo_turno", rows[:1]
            return "eleito", []
        topo = rows[:vagas]
        if all(c.eleito for c, _ in topo) and len(topo) == vagas:
            return "eleito", topo
        sj = u.get("sj") or {}
        dfn = DC.avaliar(
            {**{c.sqcand: v for c, v in rows}, **sj}, vagas=vagas,
            concorrentes_observados=t.validos_computados,
            pendente_max=t.pendente_max, sub_judice=t.anulados_sub_judice,
            sub_judice_por_candidato=sj or None,
            encerrada=(t.andamento == "f"),
            tem_segundo_turno=segundo_turno)
        if dfn.disputa_definida:
            return "definido", topo
        # Governador: líder e vice-líder já garantidos no 2º turno. Não é
        # "liderando" — a liderança do 1º turno deixou de decidir.
        if vagas == 1 and dfn.segundo_turno_definido:
            return "segundo_turno", topo
        return "liderando", topo

    # ----------------------------------------------------------- resumo

    @app.get("/api/v1/politico/resumo")
    def resumo(cargo: str = Query(..., pattern="^(1|3|5)$"),
               eleicao: str | None = None,
               s: Session = Depends(M.get_session)):
        """Votos por partido e por bloco; territórios liderados.

        Presidente: votos do total do Brasil; UFs lideradas são recorte do
        voto (nenhuma UF elege presidente). Governador e senador: votos
        somados das 27 UFs, e cada UF é a unidade que decide — "governos"
        conta UFs e eleitorado separadamente, como pede o §6."""
        ct = M._contest_or_404(s, cargo, eleicao)
        reg, ativa = _classificacao()
        ideo = _ideologia(reg, ativa)

        base = _unidades(s, ct, "br") if cargo == "1" else []
        ufs = _unidades(s, ct, "uf")
        fontes = base if cargo == "1" else ufs

        votos_part: dict[str, dict] = {}
        total_validos = 0
        for u in fontes:
            total_validos += u["t"].validos_computados or 0
            for c, v in u["rows"]:
                p = PA.resolver(c.partido_numero, c.partido_sigla)
                k = p.numero if p.cadastrado else f"?{p.sigla}"
                d = votos_part.setdefault(k, {
                    "partido": p.as_dict(), "ideologia": ideo(p),
                    "votos": 0, "candidatos": set()})
                d["votos"] += v
                d["candidatos"].add(c.nome_urna)
        blocos = _vazio_blocos()
        for d in votos_part.values():
            b = d["ideologia"]["bloco"] if d["ideologia"] else \
                "sem_classificacao"
            blocos[b]["votos"] += d["votos"]
            d["pct"] = (100.0 * d["votos"] / total_validos
                        if total_validos else None)
            d["candidatos"] = sorted(d["candidatos"])[:4] \
                if cargo != "1" else sorted(d["candidatos"])
        for b in blocos.values():
            b["pct"] = (100.0 * b["votos"] / total_validos
                        if total_validos else None)
        if not ativa:
            blocos = None

        # Territórios liderados, com eleitorado — o mapa mostra área, e área
        # não é eleitor.
        vagas = ct.vagas or 1
        terr: dict[str, dict] = {}
        estados = {"eleito": 0, "definido": 0, "segundo_turno": 0,
                   "liderando": 0, "aguardando": 0}
        linhas_uf = []
        for u in ufs:
            est, topo = _estado_vagas(u, vagas, M.tem_segundo_turno(s, ct))
            if cargo == "1" and est in ("eleito", "definido",
                                        "segundo_turno"):
                est = "liderando"      # UF não elege presidente (§3)
            estados[est] += 1
            eleitorado = u["t"].eleitorado_total or 0
            ocup = []
            for c, v in topo:
                p = PA.resolver(c.partido_numero, c.partido_sigla)
                k = p.numero if p.cadastrado else f"?{p.sigla}"
                d = terr.setdefault(k, {
                    "partido": p.as_dict(), "ideologia": ideo(p),
                    "territorios": 0, "eleitorado": 0,
                    "por_estado": {"eleito": 0, "definido": 0,
                                   "segundo_turno": 0, "liderando": 0}})
                d["territorios"] += 1
                d["eleitorado"] += eleitorado
                d["por_estado"][est] += 1
                ocup.append({"nome": c.nome_urna, "partido": p.as_dict(),
                             "ideologia": ideo(p),
                             "pct": (100.0 * v / u["t"].validos_computados
                                     if u["t"].validos_computados else None)})
            linhas_uf.append({"uf": u["geo"].uf, "estado": est,
                              "eleitorado": eleitorado,
                              "apurado_pct": (
                                  100.0 * u["t"].eleitorado_apurado
                                  / u["t"].eleitorado_total
                                  if u["t"].eleitorado_total else None),
                              "ocupantes": ocup})
        terr_blocos = None
        if ativa:
            terr_blocos = {b: {"territorios": 0, "eleitorado": 0}
                           for b in (*BLOCOS, "sem_classificacao")}
            for d in terr.values():
                b = d["ideologia"]["bloco"] if d["ideologia"] else \
                    "sem_classificacao"
                terr_blocos[b]["territorios"] += d["territorios"]
                terr_blocos[b]["eleitorado"] += d["eleitorado"]

        dados = {
            "cargo": ct.cargo_nome, "cargo_cd": ct.cargo_cd,
            "vagas_por_uf": vagas,
            "abrangencia_decisoria": ct.abrangencia_decisoria,
            "votos": {
                "universo": ("total do Brasil" if cargo == "1"
                             else "soma das 27 UFs"),
                "validos": total_validos,
                "por_partido": sorted(votos_part.values(),
                                      key=lambda d: -d["votos"]),
                "por_bloco": blocos,
            },
            "territorios": {
                "unidade": "UF",
                "decide": cargo != "1",
                "por_partido": sorted(terr.values(),
                                      key=lambda d: (-d["territorios"],
                                                     -d["eleitorado"])),
                "por_bloco": terr_blocos,
                "estados": estados,
                "ufs": sorted(linhas_uf, key=lambda x: x["uf"]),
            },
            "classificacao": _meta_classificacao(reg, ativa),
        }
        return M.envelope(dados, snapshots=_snaps(s, fontes + ufs),
                          estado="observado" if fontes else "aguardando",
                          denominador=M.DenBase.VALIDOS_COMPUTADOS)

    # --------------------------------------------------------- panorama

    @app.get("/api/v1/panorama")
    def panorama(cargo: str = Query(..., pattern="^(3|5)$"),
                 s: Session = Depends(M.get_session)):
        """Os 27 estados de uma vez: apurado, estado da disputa e os
        primeiros colocados (vagas + 1), com a projeção REGISTRADA mais
        recente de cada UF. Não calcula nada: lê o diário que o coletor
        preenche a cada rodada — 27 simulações por pedido travariam o
        servidor a cada atualização do painel."""
        from ..config import load_environment
        from ..engine import audit as A
        from ..operacao import modelo_ativo
        ct = M._contest_or_404(s, cargo, None)
        env = load_environment(M.env_atual())
        vagas = ct.vagas or 1
        modelo = modelo_ativo()
        ult = A.Diario(DATA_DIR / "previsoes", env.name).ultimos()
        unidades = {u["geo"].uf: u for u in _unidades(s, ct, "uf")}
        from ..tse.urls import UF_DISPUTAS
        agora = datetime.now(timezone.utc)
        ufs = []
        for uf in sorted(UF_DISPUTAS):
            u = unidades.get(uf)
            reg = ult.get((ct.cargo_cd, uf, modelo))
            proj = {}
            publicavel = False
            idade = None
            if reg is not None:
                # Probabilidade só com o gate e a partir do piso de apuração
                # (engine/nivel.py); mediana e faixa saem sempre.
                publicavel = reg.estado_publicacao == "projecao_disponivel"
                com_prob = publicavel and NV.publica_probabilidade(
                    reg.cobertura_pct)
                idade = (agora - datetime.fromisoformat(reg.momento)
                         ).total_seconds()
                for c in reg.candidatos:
                    proj[c.sqcand] = {
                        "pct_mediana": c.pct_concorrentes_q500,
                        "pct_q100": c.pct_concorrentes_q100,
                        "pct_q900": c.pct_concorrentes_q900,
                        "prob_lideranca": (c.prob_lideranca
                                           if com_prob else None),
                        "prob_dentro_vagas": (c.prob_dentro_vagas
                                              if com_prob else None),
                    }
            if u is None:
                ufs.append({"uf": uf, "estado": "aguardando",
                            "apurado_pct": None, "candidatos": [],
                            "projecao_idade_s": idade})
                continue
            est, _ = _estado_vagas(u, vagas, M.tem_segundo_turno(s, ct))
            t = u["t"]
            den = t.validos_computados
            cands = []
            for c, v in u["rows"][:vagas + 1]:
                p = PA.resolver(c.partido_numero, c.partido_sigla)
                cands.append({
                    "sqcand": c.sqcand, "nome": c.nome_urna,
                    "numero": c.numero, "partido": p.as_dict(),
                    "votos": v, "pct": (100.0 * v / den) if den else None,
                    "eleito_tse": bool(c.eleito),
                    "projecao": proj.get(c.sqcand),
                })
            ufs.append({
                "uf": uf, "estado": est, "andamento": t.andamento,
                "apurado_pct": (100.0 * t.eleitorado_apurado
                                / t.eleitorado_total
                                if t.eleitorado_total else None),
                "eleitorado": t.eleitorado_total,
                "candidatos": cands,
                "projecao_idade_s": (round(idade) if idade is not None
                                     else None),
                "projecao_publicavel": publicavel,
            })
        return M.envelope(
            ufs, snapshots=_snaps(s, list(unidades.values())),
            estado="observado" if unidades else "aguardando",
            denominador=M.DenBase.VALIDOS_COMPUTADOS,
            extra={"cargo": ct.cargo_nome, "vagas": vagas, "modelo": modelo,
                   "nota_projecao": (
                       "Projeção registrada pelo coletor a cada rodada; "
                       "sem homologação do gate, é cenário exploratório e "
                       "não traz probabilidade.")})

    # ------------------------------------------------------- composição

    @app.get("/api/v1/composicao")
    def composicao(casa: str = Query(...,
                                     pattern="^(senado|camara|assembleia)$"),
                   uf: str | None = Query(None, pattern="^[a-zA-Z]{2}$"),
                   s: Session = Depends(M.get_session)):
        reg, ativa = _classificacao()
        ideo = _ideologia(reg, ativa)
        meta = _meta_classificacao(reg, ativa)
        if casa == "camara":
            return _camara(ideo, meta, uf.lower() if uf else None)
        if casa == "assembleia":
            return _assembleia(ideo, meta, uf.lower() if uf else None)
        return _senado(s, ideo, meta)

    def _leitor_camara(nome: str | None = None) -> CAM.LeitorCamara:
        """Câmara do ambiente do pedido, ou de `nome` (a comparação ano a
        ano lê a de 2026 do 1º turno quando o servidor está no 2º)."""
        from ..config import load_environment
        env = load_environment(nome or M.env_atual())
        leitor = _leitores.get(env.name)
        if leitor is None:
            leitor = _leitores[env.name] = CAM.LeitorCamara(
                RawArchive(DATA_DIR / "raw", env.name))
        return leitor

    def _leitor_assembleias() -> CAM.LeitorCamara:
        from ..config import load_environment
        env = load_environment(M.env_atual())
        chave = env.name + "|assembleias"
        leitor = _leitores.get(chave)
        if leitor is None:
            leitor = _leitores[chave] = CAM.LeitorCamara(
                RawArchive(DATA_DIR / "raw", env.name),
                cargos=CAM.CARGOS_ASSEMBLEIA)
        return leitor

    def _assembleia(ideo, meta, uf: str | None = None) -> dict:
        """As 27 assembleias (sem `uf`) ou o plenário de uma (com `uf`).
        Só o resultado do TSE, como a Câmara: sem projeção."""
        por_uf = _leitor_assembleias().por_uf()
        if uf:
            r = por_uf.get(uf)
            d = (CAM.assembleia(r, PA.resolver, ideo) if r
                 else {"uf": uf, "vagas": 0, "cadeiras": [], "partidos": [],
                       "por_bloco": {b: {"total": 0} for b in CAM.BLOCOS}})
            fonte = d.get("gerado_em")
        else:
            d = CAM.assembleias(por_uf, PA.resolver, ideo)
            fonte = d["fonte_gerada_em"]
        d["classificacao"] = meta
        d["nota"] = (
            "Só o resultado divulgado pelo TSE — deputado estadual não tem "
            "projeção. Cadeira cheia é candidato com situação oficial de "
            "eleito; enquanto o TSE não marca, as vagas que ele distribui "
            "às listas, com os mais votados de cada uma. No DF a casa é a "
            "Câmara Legislativa (deputado distrital).")
        tem = bool(por_uf) and (not uf or uf in por_uf)
        return M.envelope(
            d, snapshots=[],
            estado=("aguardando" if not tem else "resultado_oficial"
                    if (d.get("andamento") == "f" if uf
                        else d["ufs_encerradas"] == d["ufs_com_dado"])
                    else "apuracao_em_andamento"),
            motivo=(None if tem else
                    "nenhum arquivo de deputado estadual coletado ainda"),
            extra={"source_generated_at": fonte})

    def _camara(ideo, meta, uf: str | None = None) -> dict:
        por_uf = _leitor_camara().por_uf()
        comp = CAM.composicao(por_uf, PA.resolver, ideo)
        comp["classificacao"] = meta
        # Uma UF pedida: os eleitos dela, nome a nome (aba Deputado federal).
        if uf:
            r = por_uf.get(uf)
            comp["uf_detalhe"] = None if r is None else {
                **{k: r.get(k) for k in (
                    "uf", "vagas", "quociente_eleitoral", "andamento",
                    "totalizacao_final", "apurado_pct", "gerado_em")},
                "eleitos": sorted([
                    {**e, "partido": PA.resolver(
                        e["partido_numero"], e["partido_sigla"]).as_dict()}
                    for e in r["eleitos"]], key=lambda e: -e["votos"]),
                "vagas_distribuidas_tse": sum(
                    x["vagas_tse"] for x in r.get("listas") or []),
                "listas": [{**x, "candidatos": [
                    {**c, "partido": PA.resolver(
                        c["partido_numero"], c["partido_sigla"]).as_dict()}
                    for c in x["candidatos"]]}
                    for x in r.get("listas") or []],
            }
        comp["nota"] = (
            "Só o resultado divulgado pelo TSE — deputado federal não tem "
            "projeção. Conta como cadeira o candidato com situação oficial "
            "'Eleito por QP' ou 'Eleito por média'. Enquanto a UF não fecha, "
            "a situação pode mudar.")
        return M.envelope(
            comp, snapshots=[],
            estado=("aguardando" if not por_uf else
                    "resultado_oficial" if comp["ufs_encerradas"] == 27
                    else "apuracao_em_andamento"),
            motivo=(None if por_uf else
                    "nenhum arquivo de deputado federal coletado ainda"),
            extra={"source_generated_at": comp["fonte_gerada_em"]})

    # ---------------------------------------------- blocos nas esferas

    @app.get("/api/v1/blocos")
    def blocos(uf: str | None = Query(None, pattern="^[a-zA-Z]{2}$"),
               s: Session = Depends(M.get_session)):
        """Os blocos ideológicos em todas as esferas, numa resposta só
        (pedido de 05/10/2026): presidente, governo, Senado, Câmara e
        assembleias. Sem `uf`, o país; com `uf`, o estado.

        Duas unidades convivem e cada linha diz a sua: nas disputas
        majoritárias, **votos** (o que o eleitorado escolheu); nas casas,
        **cadeiras** (quem ficou com o poder). No quadro do país o governo
        dos estados conta **estados**, e os que vão ao 2º turno ficam "em
        disputa" — não entram no bloco de quem lidera.

        Sem a classificação assinada tudo cai em "sem classificação" e a
        tela diz por quê: nada de ideologia sem autoria (§6)."""
        reg, ativa = _classificacao()
        ideo = _ideologia(reg, ativa)
        meta = _meta_classificacao(reg, ativa)
        uf = uf.lower() if uf else None

        def zero() -> dict:
            return {b: 0 for b in CAM.BLOCOS}

        def bloco_p(numero, sigla) -> str:
            i = ideo(PA.resolver(numero, sigla))
            return (i or {}).get("bloco") or "sem_classificacao"

        def contest(cargo):
            try:
                return M._contest_or_404(s, cargo, None)
            except HTTPException:
                return None

        def votos(cargo: str, tipo: str) -> tuple[dict, list]:
            """Votos válidos por bloco e as unidades lidas."""
            ct = contest(cargo)
            out, us = zero(), []
            if ct is None:
                return out, us
            for u in _unidades(s, ct, tipo):
                if uf and u["geo"].uf != uf:
                    continue
                us.append((ct, u))
                for c, v in u["rows"]:
                    out[bloco_p(c.partido_numero, c.partido_sigla)] += v
            return out, us

        def nomes(ct, u) -> tuple[str, list]:
            est, topo = _estado_vagas(u, ct.vagas,
                                      M.tem_segundo_turno(s, ct))
            if est == "segundo_turno":
                topo = u["rows"][:2]          # os dois que seguem na disputa
            return est, [{"nome": c.nome_urna or c.nome,
                          "partido": PA.resolver(c.partido_numero,
                                                 c.partido_sigla).sigla,
                          "bloco": bloco_p(c.partido_numero,
                                           c.partido_sigla)}
                         for c, _v in topo]

        linhas = []

        def linha(chave, rotulo, unidade, por, **extra):
            linhas.append({"chave": chave, "rotulo": rotulo,
                           "unidade": unidade, "por_bloco": por,
                           "total": sum(por.values())
                           + int(extra.get("em_disputa") or 0), **extra})

        # ---- presidente: votos
        por, us = votos("1", "uf" if uf else "br")
        linha("presidente", "Presidente", "votos", por,
              nota=("votos válidos no estado — quem decide é o total do "
                    "Brasil" if uf else "votos válidos no país"))

        # ---- governo
        if uf:
            por, us = votos("3", "uf")
            est, quem = nomes(*us[0]) if us else ("aguardando", [])
            linha("governador", "Governador", "votos", por,
                  nota="votos válidos no estado", situacao=est, quem=quem)
        else:
            por, disputa = zero(), 0
            ct = contest("3")
            for u in (_unidades(s, ct, "uf") if ct else []):
                est, quem = nomes(ct, u)
                if est in ("eleito", "definido") and quem:
                    por[quem[0]["bloco"]] += 1
                elif est != "aguardando":
                    disputa += 1
            linha("governadores", "Governos estaduais", "estados", por,
                  em_disputa=disputa, maioria=14,
                  nota="estados com governador eleito · em disputa = 2º "
                       "turno ou apuração em aberto")

        # ---- Senado
        if uf:
            por, us = votos("5", "uf")
            est, quem = nomes(*us[0]) if us else ("aguardando", [])
            linha("senador", "Senador", "votos", por,
                  nota="votos válidos no estado · duas vagas",
                  situacao=est, quem=quem)
        else:
            sen = _senado(s, ideo, meta)["dados"]
            linha("senado", "Senado", "cadeiras",
                  {b: sen["por_bloco"][b]["total"] for b in CAM.BLOCOS},
                  maioria=41,
                  nota="81 cadeiras: as 54 desta eleição e as 27 com "
                       "mandato até 2031")

        # ---- Câmara
        por_uf_cam = _leitor_camara().por_uf()
        por = zero()
        for u_, r in por_uf_cam.items():
            if uf and u_ != uf:
                continue
            for c, _tipo in CAM.ocupantes(r):
                por[bloco_p(c["partido_numero"], c["partido_sigla"])] += 1
        vagas_cam = (por_uf_cam.get(uf) or {}).get("vagas") if uf else 513
        linha("camara", "Bancada federal do estado" if uf
              else "Câmara dos Deputados", "cadeiras", por,
              maioria=None if uf else 257, vagas=vagas_cam,
              nota="deputados federais eleitos pelo estado" if uf
              else "513 cadeiras")

        # ---- assembleias
        por_uf_asb = _leitor_assembleias().por_uf()
        if uf:
            r = por_uf_asb.get(uf)
            a = CAM.assembleia(r, PA.resolver, ideo) if r else None
            linha("assembleia", "Câmara Legislativa" if uf == "df"
                  else "Assembleia Legislativa", "cadeiras",
                  {b: a["por_bloco"][b]["total"] for b in CAM.BLOCOS}
                  if a else zero(),
                  maioria=a["maioria"] if a else None,
                  vagas=a["vagas"] if a else None,
                  nota="deputados estaduais" if uf != "df"
                  else "deputados distritais")
        else:
            a = CAM.assembleias(por_uf_asb, PA.resolver, ideo)
            linha("assembleias", "Assembleias legislativas", "cadeiras",
                  {b: a["por_bloco"][b]["total"] for b in CAM.BLOCOS},
                  vagas=a["total"],
                  nota=f"{a['total']} cadeiras nas {a['ufs_com_dado']} "
                       "casas, somadas")

        tem = any(l["total"] for l in linhas)
        return M.envelope(
            {"escopo": uf or "br", "linhas": linhas, "classificacao": meta},
            snapshots=[],
            estado=("aguardando" if not tem else "observado"),
            motivo=None if tem else "nenhum voto contado ainda")

    def _senado(s: Session, ideo, meta) -> dict:
        try:
            ct = M._contest_or_404(s, "5", None)
        except HTTPException:
            ct = None
        from ..contexto import ANO_ELEICAO, ano_passado
        passado = ano_passado()
        if passado is None:
            # 2026 fixo: é a eleição que este produto cobre. A data do
            # simulado (abril de 2026) diria o mesmo, e a da demonstração de
            # 2022 diria outra coisa — as remanescentes de lá são as de hoje.
            rem = SEN.remanescentes(ano_eleicao=2026)
        else:
            rem = _remanescentes_passado(passado)

        cadeiras = []
        for o in rem["cadeiras"]:
            if passado is None:
                p = PA.resolver(None, o["partido_sigla"])
                ideo_p = ideo(p)
            else:
                # Eleito quatro anos antes: partido e ideologia DAQUELE ano —
                # o PSL de 2018 não existe na classificação de 2022, e a de
                # 2022 não é a régua de quem se elegeu em 2018.
                tk = ANO_ELEICAO.set(o["eleito_em"])
                try:
                    p = PA.resolver(o["partido_numero"], o["partido_sigla"])
                finally:
                    ANO_ELEICAO.reset(tk)
                ideo_p, _ = ideologia_remanescente(
                    o["partido_sigla"], o["partido_numero"], o["eleito_em"],
                    passado)
            cadeiras.append({
                "uf": o["uf"], "origem": "remanescente",
                "estado": "mandato_em_curso", "nome": o["nome"],
                "partido": p.as_dict(), "ideologia": ideo_p,
                "eleito_em": o.get("eleito_em"),
                "suplementar": bool(o.get("suplementar")),
                "suplente_em_exercicio": o.get("suplente_em_exercicio"),
                "mandato_fim": o.get("mandato_fim"), "pct": None,
            })

        disputa_ufs = []
        estados = {"eleito": 0, "definido": 0, "liderando": 0,
                   "aguardando": 0}
        unidades = _unidades(s, ct, "uf") if ct is not None else []
        vagas = (ct.vagas if ct is not None else 2) or 2
        vistas = set()
        for u in unidades:
            est, topo = _estado_vagas(u, vagas, M.tem_segundo_turno(s, ct))
            vistas.add(u["geo"].uf)
            disputa_ufs.append({"uf": u["geo"].uf, "estado": est,
                                "apurado_pct": (
                                    100.0 * u["t"].eleitorado_apurado
                                    / u["t"].eleitorado_total
                                    if u["t"].eleitorado_total else None)})
            for i in range(vagas):
                if i >= len(topo) and passado is not None:
                    estados["sem_eleito"] = estados.get("sem_eleito", 0) + 1
                    cadeiras.append({"uf": u["geo"].uf, "origem": "eleicao",
                                     "estado": "sem_eleito", "nome": None,
                                     "partido": None, "ideologia": None,
                                     "pct": None})
                    continue
                estados[est] += 1
                if i < len(topo):
                    c, v = topo[i]
                    p = PA.resolver(c.partido_numero, c.partido_sigla)
                    cadeiras.append({
                        "uf": u["geo"].uf, "origem": "eleicao",
                        "estado": est, "nome": c.nome_urna,
                        "partido": p.as_dict(), "ideologia": ideo(p),
                        "pct": (100.0 * v / u["t"].validos_computados
                                if u["t"].validos_computados else None),
                    })
                else:
                    cadeiras.append({"uf": u["geo"].uf, "origem": "eleicao",
                                     "estado": "aguardando", "nome": None,
                                     "partido": None, "ideologia": None,
                                     "pct": None})
        # UF sem arquivo ainda: as vagas existem, só não têm voto.
        from ..tse.urls import UF_DISPUTAS
        for uf in sorted(UF_DISPUTAS - vistas):
            for _ in range(vagas):
                estados["aguardando"] += 1
                cadeiras.append({"uf": uf, "origem": "eleicao",
                                 "estado": "aguardando", "nome": None,
                                 "partido": None, "ideologia": None,
                                 "pct": None})

        por_partido: dict[str, dict] = {}
        for c in cadeiras:
            if c["partido"] is None:
                continue
            p = c["partido"]
            # Número E sigla: numa eleição passada o mesmo número aparece com
            # duas legendas (o 22 é PR entre os eleitos de 2018 e PL entre
            # os de 2022; o 44 é PRP e União). Só o número juntava as duas
            # sob o rótulo da primeira (visto em 24/09).
            k = (f"{p['numero']}|{p['sigla']}" if p["cadastrado"]
                 else f"?{p['sigla']}")
            d = por_partido.setdefault(k, {
                "partido": p, "ideologia": c["ideologia"], "total": 0,
                "remanescentes": 0, "eleito": 0, "definido": 0,
                "liderando": 0})
            d["total"] += 1
            d["remanescentes" if c["origem"] == "remanescente"
              else c["estado"]] += 1
        blocos = None
        if meta["estado"] == "disponivel":
            blocos = {b: {"total": 0, "remanescentes": 0, "eleito": 0,
                          "definido": 0, "liderando": 0}
                      for b in (*BLOCOS, "sem_classificacao")}
            for d in por_partido.values():
                b = d["ideologia"]["bloco"] if d["ideologia"] else \
                    "sem_classificacao"
                for k in ("total", "remanescentes", "eleito", "definido",
                          "liderando"):
                    blocos[b][k] += d[k]

        snaps = _snaps(s, unidades)
        dados = {
            "total": 81,
            "cadeiras": cadeiras,
            "remanescentes": {"n": len(rem["cadeiras"]),
                              # Como a tela chama essas cadeiras: "até
                              # 2031" hoje; "eleitas em 2018" num ano passado.
                              "rotulo": rem.get("rotulo") or "até 2031",
                              "eleitos_em": rem.get("eleitos_em"),
                              "estado": rem["estado"],
                              "motivo": rem["motivo"],
                              "referencia_em": rem["obtido_em"],
                              "fonte": rem["fonte"],
                              "nota": (rem.get("nota") or
                                       "Partido de hoje, informado pelo "
                                       "Senado na data de referência; "
                                       "conta o ocupante atual da cadeira.")},
            "em_disputa": {"n": 27 * vagas, "vagas_por_uf": vagas,
                           "estados": estados, "ufs": disputa_ufs},
            "por_partido": sorted(por_partido.values(),
                                  key=lambda d: (-d["total"],
                                                 d["partido"]["sigla"])),
            "por_bloco": blocos,
            "classificacao": meta,
            "maioria": {"simples_presentes": None, "absoluta": 41,
                        "tres_quintos": 49,
                        "nota": "Sem presumir disciplina por cor: blocos "
                                "não votam unidos automaticamente (§6)."},
        }
        return M.envelope(dados, snapshots=snaps,
                          estado=("observado" if unidades else "aguardando"))

    from . import recordes as REC
    REC.register(app, _unidades, _leitor_camara)
    # A bancada ano a ano (historico.py) lê a Câmara de 2026 pelo mesmo
    # leitor: um segundo releria o manifesto inteiro de novo.
    app.state.leitor_camara = _leitor_camara
