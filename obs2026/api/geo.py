"""Rotas de geometria e de dados para o mapa.

A junção mapa↔resultado é por **código IBGE**, que o EA12 fornece
(`cdi`) e que a malha do IBGE usa como `codarea`. Não há pareamento por
nome em lugar nenhum deste caminho — é a diferença entre um mapa correto e
um mapa que erra em 45 municípios com apóstrofo no nome.

A malha vem do serviço de malhas territoriais do IBGE, baixada uma vez e
servida localmente: o navegador do leitor nunca busca geometria de terceiros
enquanto olha a apuração.

As camadas seguem o §6. A de bloco ideológico existe mas nasce desligada,
porque a curadoria da classificação está pendente por desenho.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import Depends
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

#: Região pelo primeiro dígito do código IBGE. Não é convenção nossa: é a
#: própria numeração do IBGE, em que o 1 abre o Norte, o 2 o Nordeste e
#: assim por diante. Derivar daqui evita manter uma tabela de 27 UFs que
#: precisaria ser conferida à mão e que ninguém lembraria de atualizar.
REGIOES = {"1": "Norte", "2": "Nordeste", "3": "Sudeste",
           "4": "Sul", "5": "Centro-Oeste"}

#: Nome por extenso das UFs. O TSE manda a sigla e a malha do IBGE manda só
#: o código, então "AM" é tudo que existe no dado — e "AM" numa tela de
#: projeção obriga o espectador a decodificar. Tabela fixa se justifica aqui
#: porque o conjunto é fechado e não muda: a última alteração no quadro de
#: unidades da federação foi em 1988.
UF_EXTENSO = {
    "ac": "Acre", "al": "Alagoas", "ap": "Amapá", "am": "Amazonas",
    "ba": "Bahia", "ce": "Ceará", "df": "Distrito Federal",
    "es": "Espírito Santo", "go": "Goiás", "ma": "Maranhão",
    "mt": "Mato Grosso", "ms": "Mato Grosso do Sul", "mg": "Minas Gerais",
    "pa": "Pará", "pb": "Paraíba", "pr": "Paraná", "pe": "Pernambuco",
    "pi": "Piauí", "rj": "Rio de Janeiro", "rn": "Rio Grande do Norte",
    "rs": "Rio Grande do Sul", "ro": "Rondônia", "rr": "Roraima",
    "sc": "Santa Catarina", "sp": "São Paulo", "se": "Sergipe",
    "to": "Tocantins", "zz": "Exterior",
}

_WEB = Path(__file__).resolve().parent.parent.parent / "web"


def register(app) -> None:
    from . import app as M
    from ..engine import decided as DC
    from ..engine.denominators import Base as DenBase
    from ..storage.models import (Candidate, Geography, ObservedResult,
                                  SourceSnapshot)

    @app.get("/geo/{arquivo}", include_in_schema=False)
    def geometria(arquivo: str):
        """Malha territorial do IBGE, servida localmente."""
        if arquivo not in ("uf.json", "municipios.json"):
            from fastapi import HTTPException
            raise HTTPException(404, "malha desconhecida")
        return FileResponse(_WEB / "geo" / arquivo,
                            media_type="application/geo+json",
                            headers={"Cache-Control": "public, max-age=86400"})

    @app.get("/telao", include_in_schema=False)
    def telao():
        """Tela de projeção.

        Rota separada do painel, não um modo dele: a densidade de informação
        é outra. O que se lê a 40 cm não se lê a 4 m.
        """
        return M._html_com_versao(_WEB / "telao.html")

    @app.get("/mapa.js", include_in_schema=False)
    def mapa_js():
        # `no-cache` não é "não guarde": é "confira antes de usar". Sem
        # isto o navegador aplica cache heurístico e pode servir uma versão
        # antiga do desenho do mapa depois de uma correção — na noite da
        # apuração, com o projetor ligado, ninguém vai desconfiar do cache.
        # O ETag do FileResponse faz a revalidação custar quase nada.
        return FileResponse(_WEB / "mapa.js",
                            media_type="application/javascript",
                            headers={"Cache-Control": "no-cache"})

    @app.get("/api/v1/map-layers")
    def camadas_do_mapa(cargo: str, eleicao: str | None = None,
                        abrangencia: str = "uf", uf: str | None = None,
                        s: Session = Depends(M.get_session)):
        """Dados do mapa, com todas as camadas de uma vez.

        Uma requisição só: o navegador troca de camada sem voltar ao
        servidor, e todas as camadas referenciam o MESMO lote de snapshots
        (§8.2) — trocar de camada não pode trocar de horário.
        """
        ct = M._contest_or_404(s, cargo, eleicao)
        tipo = "mu" if abrangencia == "mu" else "uf"

        gq = select(Geography).where(Geography.environment == M._ENV_NAME,
                                     Geography.tipo == tipo)
        if uf:
            gq = gq.where(Geography.uf == uf.lower())
        geos = {g.id: g for g in s.execute(gq).scalars().all()}
        if not geos:
            return M.envelope([], snapshots=[], estado="aguardando",
                              motivo="nenhuma geografia para o filtro")

        totais = M._latest_snapshot_ids(s, ct.id, list(geos))
        if not totais:
            return M.envelope([], snapshots=[], estado="aguardando",
                              motivo="nenhum resultado capturado ainda")

        cands = {c.id: c for c in s.execute(select(Candidate).where(
            Candidate.contest_id == ct.id)).scalars().all()}
        snaps = s.execute(select(SourceSnapshot).where(
            SourceSnapshot.id.in_([t.snapshot_id for t in totais]))
        ).scalars().all()

        # Codigo IBGE da UF: os 2 primeiros digitos do codigo municipal,
        # derivado do dado e nao de tabela fixa no codigo.
        #
        # Usa a MODA, nao o primeiro encontrado. Motivo concreto: Boa
        # Esperanca do Norte (MT), municipio novo, veio no EA12 com codigo
        # IBGE 5300109 — prefixo 53, que e a faixa do Distrito Federal, nao
        # a do Mato Grosso (51). Pegar o primeiro municipio de MT dava 53 ao
        # estado inteiro, e MT sumia do mapa enquanto o DF aparecia
        # duplicado.
        from collections import Counter
        prefixos: dict[str, Counter] = {}
        anomalias: list[str] = []
        for g in s.execute(select(Geography).where(
                Geography.environment == M._ENV_NAME,
                Geography.tipo == "mu",
                Geography.cd_ibge.is_not(None))).scalars().all():
            prefixos.setdefault(g.uf, Counter())[g.cd_ibge[:2]] += 1
        # `sigla` e nao `uf`: reusar o nome do parametro aqui o sobrescreve,
        # e `recorte_ibge` passa a devolver a ultima UF do laco em vez da
        # pedida — o mapa desenhava o Brasil inteiro para mostrar Alagoas.
        cod_uf = {sigla: c.most_common(1)[0][0]
                  for sigla, c in prefixos.items()}
        for sigla, c in prefixos.items():
            for pref, n in c.items():
                if pref != cod_uf[sigla]:
                    anomalias.append(
                        f"{sigla.upper()}: {n} municipio(s) com prefixo IBGE "
                        f"{pref}, divergente do prefixo da UF "
                        f"({cod_uf[sigla]})")

        linhas = []
        for t in totais:
            g = geos[t.geography_id]
            rows = s.execute(select(ObservedResult).where(
                ObservedResult.snapshot_id == t.snapshot_id,
                ObservedResult.geography_id == t.geography_id,
            )).scalars().all()
            validos = [r for r in rows if r.dvt == "Válido"]
            validos.sort(key=lambda r: -r.votos)

            lider = cands.get(validos[0].candidate_id) if validos else None
            segundo = (cands.get(validos[1].candidate_id)
                       if len(validos) > 1 else None)
            v1 = validos[0].votos if validos else 0
            v2 = validos[1].votos if len(validos) > 1 else 0

            dfn = DC.avaliar(
                {cands[r.candidate_id].sqcand: r.votos for r in validos
                 if r.candidate_id in cands},
                vagas=ct.vagas,
                concorrentes_observados=t.validos_computados,
                pendente_max=t.pendente_max,
                sub_judice=t.anulados_sub_judice,
                encerrada=(t.andamento == "f"),
                tem_segundo_turno=(ct.vagas == 1),
            )
            if dfn.disputa_definida:
                estado_def = "definida"
            elif dfn.segundo_turno_definido:
                estado_def = "segundo_turno_definido"
            elif dfn.sub_judice_em_jogo:
                estado_def = "suspensa_sub_judice"
            else:
                estado_def = "em_aberto"

            cd_geo = (g.cd_ibge if tipo == "mu" else cod_uf.get(g.uf))
            den = t.validos_computados

            # Esta unidade decide a disputa, ou é recorte do voto?
            #
            # Governador e senador se decidem na UF, então o mapa de UFs
            # mostra a unidade que elege. Presidente se decide no total do
            # Brasil: nenhum estado e nenhum município elege presidente, e
            # pintar um deles de "eleito" seria afirmar o que o §3 proíbe.
            decide = (tipo == ct.abrangencia_decisoria)
            eleito = bool(decide and lider is not None and lider.eleito)

            linhas.append({
                # Chave de junção com a malha: código IBGE, sempre.
                "cd_ibge": cd_geo,
                "cd_tse": g.cd_tse,
                "uf": g.uf,
                "nome": g.nome,
                # Município já vem com nome de gente; UF vem como sigla.
                "nome_extenso": (g.nome if tipo == "mu"
                                 else UF_EXTENSO.get(g.uf, g.nome)),
                "capital": g.capital,
                "eleitorado": t.eleitorado_total,
                "lider_sqcand": lider.sqcand if lider else None,
                "lider_nome": lider.nome_urna if lider else None,
                "lider_partido": lider.partido_sigla if lider else None,
                "lider_votos": v1,
                "lider_pct": (100.0 * v1 / den) if den else None,
                # O segundo colocado vem junto: líder e margem deixaram de
                # ser camadas separadas, e ler "44,2% contra 38,9%" diz o
                # que "margem 5,3 pp" sozinho não diz.
                "segundo_sqcand": segundo.sqcand if segundo else None,
                "segundo_nome": segundo.nome_urna if segundo else None,
                "segundo_partido": segundo.partido_sigla if segundo else None,
                "segundo_votos": v2 if segundo else None,
                "segundo_pct": (100.0 * v2 / den) if (den and segundo)
                               else None,
                # Margem sobre votos a concorrentes, a base que decide.
                "margem_pp": ((100.0 * (v1 - v2) / den) if den else None),
                "regiao": REGIOES.get((cd_geo or "")[:1]),
                "unidade_decide": decide,
                # Só onde a unidade decide. Em recorte fica `false` porque a
                # afirmação "eleito aqui" não existe (§3).
                "eleito_tse": eleito,
                "cobertura_pct": ((100.0 * t.eleitorado_apurado
                                   / t.eleitorado_total)
                                  if t.eleitorado_total else None),
                "andamento": t.andamento,
                "definicao": estado_def,
                "vagas": ct.vagas,
                "eleitos": [cands[r.candidate_id].nome_urna
                            for r in validos[:ct.vagas]
                            if r.candidate_id in cands
                            and dfn.disputa_definida],
            })

        # Paleta categórica dos líderes, estável por candidato.
        lideres = sorted({x["lider_sqcand"] for x in linhas
                          if x["lider_sqcand"]})
        return M.envelope(
            linhas, snapshots=snaps, estado="observado",
            denominador=DenBase.VALIDOS_COMPUTADOS,
            quality_flags=anomalias,
            extra={
                "malha": ("municipios.json" if tipo == "mu" else "uf.json"),
                "abrangencia": tipo,
                # Prefixo IBGE do recorte, para o mapa ajustar o enquadramento
                # em vez de desenhar o pais inteiro para mostrar uma UF.
                "recorte_ibge": (cod_uf.get(uf.lower()) if uf else None),
                "lideres": [
                    {"sqcand": sq,
                     "nome": next(x["lider_nome"] for x in linhas
                                  if x["lider_sqcand"] == sq),
                     "partido": next(x["lider_partido"] for x in linhas
                                     if x["lider_sqcand"] == sq)}
                    for sq in lideres],
                "aviso_cartografico": (
                    "Área geográfica não é eleitorado. O mapa vem sempre "
                    "acompanhado de barra ponderada por eleitores e de "
                    "tabela, e a leitura não depende só de cor (§6)."),
                "nota_territorial": (
                    "Presidente é decidido no total do Brasil; governador e "
                    "senador, no total da UF. Liderança em um território "
                    "nunca significa eleição por ele."
                    if tipo == "mu" else None),
            })
