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

import json
import threading
import time
from pathlib import Path

from fastapi import Depends
from fastapi.responses import FileResponse, Response
from sqlalchemy import func, select
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

#: Linhas do mapa já calculadas, por (disputa, nível, snapshot). O snapshot é
#: imutável, então a linha também é; a memória só é limpa por tamanho.
_LINHAS: dict[tuple[int, str, int], dict] = {}


#: Resposta pronta do mapa, por lote de retratos (ver `map_layers`).
_RESPOSTAS: dict[tuple, str] = {}
#: Recortes pedidos recentemente: (cargo, abrangencia, uf, eleicao) -> hora.
_PEDIDOS: dict[tuple, float] = {}
#: A própria rota, para o aquecedor chamá-la fora do ciclo do pedido.
_ROTA = None
#: Recorte parado há mais que isto sai do aquecimento.
JANELA_AQUECIMENTO_S = 300.0
#: Intervalo entre passadas do aquecedor.
PASSO_AQUECIMENTO_S = 20.0
#: Quantos recortes, no máximo, cada passada refaz. O telão mostra uma tela
#: por vez: aquecer a fila inteira só tira CPU de quem está pedindo.
MAX_POR_PASSADA = 2
#: Nome da thread do aquecedor. O que ele mesmo pede não conta como pedido
#: da sala, senão ele se realimenta e nunca para.
THREAD_AQUECEDOR = "aquecedor-mapa"
#: Silêncio mínimo desde o último pedido de verdade. No simulado de 23/09 o
#: aquecedor disputava o interpretador com as respostas e a projeção de
#: presidente passava de 2 minutos; agora ele só trabalha no intervalo.
QUIETUDE_S = 4.0


def aquecer(intervalo: float = PASSO_AQUECIMENTO_S) -> None:
    """Laço do aquecedor: roda em thread de fundo, dentro do servidor.

    Cada passada refaz os recortes vistos há pouco. Quando o lote de
    retratos não mudou, a rota devolve o cache e a passada custa quase
    nada; quando mudou, o custo de montar cai aqui e não em quem olha.
    """
    from . import app as M
    while True:
        try:
            agora = time.time()
            # Mais recentes primeiro: é o que está no ar agora.
            alvos = sorted((k for k, t in list(_PEDIDOS.items())
                            if agora - t <= JANELA_AQUECIMENTO_S),
                           key=lambda k: -_PEDIDOS[k])[:MAX_POR_PASSADA]
            ultimo = max(_PEDIDOS.values(), default=0.0)
            if agora - ultimo < QUIETUDE_S:
                time.sleep(1.0)        # a sala está pedindo: sai da frente
                continue
            for k in alvos:
                if _ROTA is None or M._Session is None:
                    break
                if (time.time() - max(_PEDIDOS.values(), default=0.0)
                        < QUIETUDE_S):
                    break              # chegou pedido no meio da passada
                cargo, abr, uf, eleicao = k
                try:
                    with M._Session() as s:
                        _ROTA(cargo=cargo, abrangencia=abr,
                              uf=uf or None, eleicao=eleicao or None, s=s)
                except Exception:                      # noqa: BLE001
                    # Aquecedor nunca derruba o servidor nem aparece na
                    # tela: o pior caso é o pedido seguinte montar sozinho.
                    pass
            for k, t in list(_PEDIDOS.items()):
                if agora - t > JANELA_AQUECIMENTO_S:
                    _PEDIDOS.pop(k, None)
        except Exception:                              # noqa: BLE001
            pass
        time.sleep(intervalo)
#: Horário de geração por snapshot (imutável) — memória do filtro de ciclo.
_QUANDO: dict[int, object] = {}


def _marco_do_ciclo(s, ct):
    """Instante em que o TSE reabriu a apuração desta disputa, ou None.

    O simulado roda três janelas por dia, cada uma começando do zero. Na
    virada convivem dois ciclos: o total do Brasil já zerado e milhares de
    arquivos municipais ainda com o resultado da janela anterior — e o mapa
    mostrava voto dentro de um país em 0%.

    O marco é o arquivo AGREGADO (Brasil ou UF) mais recente que chegou
    zerado DEPOIS de a mesma unidade já ter tido seção totalizada — ou seja,
    andou para trás. Isso só acontece em reabertura. Exigir o "andou para
    trás" evita o caso em que uma UF que ainda não começou publica um
    arquivo novo em 0% no meio da contagem: ali o marco não pode pular para
    a frente, senão o mapa esvazia. A verificação fica no nível agregado de
    propósito — município com zero seção é comum a noite toda.
    """
    from . import app as M
    from ..storage.models import Geography, SourceSnapshot

    ids = [g.id for g in s.execute(select(Geography).where(
        Geography.environment == M.env_atual(),
        Geography.tipo.in_(("br", "uf")))).scalars().all()]
    if not ids:
        return None
    from ..storage.models import UnitTotals
    # Maior número de seções que cada unidade já teve nesta disputa: é o que
    # denuncia o retrocesso.
    maximos = dict(s.execute(
        select(UnitTotals.geography_id,
               func.max(UnitTotals.secoes_totalizadas))
        .where(UnitTotals.contest_id == ct.id,
               UnitTotals.geography_id.in_(ids))
        .group_by(UnitTotals.geography_id)).all())
    zerados = [t.snapshot_id for t in M._latest_snapshot_ids(s, ct.id, ids)
               if t.secoes_totalizadas == 0
               and (maximos.get(t.geography_id) or 0) > 0]
    if not zerados:
        return None
    quando = [r.source_generated_at for r in s.execute(
        select(SourceSnapshot.id, SourceSnapshot.source_generated_at)
        .where(SourceSnapshot.id.in_(zerados[:900])))
        if r.source_generated_at is not None]
    return max(quando) if quando else None


def _sem_ciclo_antigo(s, ct, geos, totais):
    """Tira do mapa a unidade cujo retrato é de um ciclo anterior.

    Vale para município e para UF: na virada de janela as duas coisas ficam
    para trás, cada uma no seu tempo. Sem marco de ciclo (o caso normal da
    noite, em que a apuração só anda para a frente), nada é descartado.
    """
    from ..storage.models import SourceSnapshot

    corte = _marco_do_ciclo(s, ct)
    if corte is None:
        return totais, 0
    # Snapshot é imutável: o horário dele, uma vez lido, não muda mais. Sem
    # esta memória, o mapa do Brasil relia 5.755 horários a cada pedido — e
    # o telão pede a cada troca de tela.
    faltam = [t.snapshot_id for t in totais if t.snapshot_id not in _QUANDO]
    for i in range(0, len(faltam), 900):
        for r in s.execute(select(SourceSnapshot.id,
                                  SourceSnapshot.source_generated_at)
                           .where(SourceSnapshot.id.in_(faltam[i:i + 900]))):
            _QUANDO[r.id] = r.source_generated_at
    if len(_QUANDO) > 200_000:
        _QUANDO.clear()
    mantidos, descartados = [], 0
    for t in totais:
        q = _QUANDO.get(t.snapshot_id)
        if q is not None and q < corte:
            descartados += 1
            continue
        mantidos.append(t)
    return mantidos, descartados


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

    @app.get("/placar", include_in_schema=False)
    def placar():
        """O placar numa janela própria, fora do carrossel — para ensaio.
        No telão ele é o tipo de tela `placar` do roteiro; os dois usam o
        mesmo `placar.js`."""
        return M._html_com_versao(_WEB / "placar.html")

    @app.get("/placar.js", include_in_schema=False)
    def placar_js():
        return FileResponse(_WEB / "placar.js",
                            media_type="application/javascript",
                            headers={"Cache-Control": "no-cache"})

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
        # O que a sala está olhando: o aquecedor refaz só isto — e o que ele
        # mesmo pede não conta, senão se realimenta e nunca para.
        if threading.current_thread().name != THREAD_AQUECEDOR:
            _PEDIDOS[(cargo, abrangencia, (uf or "").lower(),
                      eleicao or "")] = time.time()

        gq = select(Geography).where(Geography.environment == M.env_atual(),
                                     Geography.tipo == tipo)
        if uf:
            gq = gq.where(Geography.uf == uf.lower())
        geos = {g.id: g for g in s.execute(gq).scalars().all()}
        if not geos:
            return M.envelope([], snapshots=[], estado="aguardando",
                              motivo="nenhuma geografia para o filtro")

        # Sem resultado nenhum ainda não é motivo para não haver mapa. O
        # território existe antes do primeiro voto contado, e o mapa vazio
        # é justamente a imagem do começo da noite: tudo aguardando. As
        # unidades sem resultado entram abaixo, marcadas como tais.
        totais = M._latest_snapshot_ids(s, ct.id, list(geos))
        # Vale para os dois níveis: na virada de janela do simulado, UF e
        # município ficam para trás cada um no seu tempo.
        totais, de_outro_ciclo = _sem_ciclo_antigo(s, ct, geos, totais)
        # Quais UFs o TSE já encerrou nesta disputa: serve para distinguir
        # "falta apurar" de "o TSE não atualizou o arquivo deste município".
        encerradas_uf: dict[str, bool] = {}
        if tipo == "mu":
            ids_uf = {g.id: g.uf for g in s.execute(select(Geography).where(
                Geography.environment == M.env_atual(),
                Geography.tipo == "uf")).scalars().all()}
            for t_uf in M._latest_snapshot_ids(s, ct.id, list(ids_uf)):
                encerradas_uf[ids_uf[t_uf.geography_id]] = (
                    t_uf.andamento == "f")

        # Resposta pronta, por lote de retratos. O mapa municipal monta
        # 5.755 linhas e 3,5 MB; o telão pede a cada troca de tela e a cada
        # 30 s. Enquanto o lote de snapshots for o mesmo, a resposta é a
        # mesma — e a chave muda sozinha quando chega boletim novo. A
        # classificação ideológica entra na chave porque assinar a curadoria
        # tem de mudar o mapa na hora.
        from .politico import _classificacao as _cl
        _reg, _ativa = _cl()
        chave_lote = (ct.id, tipo, uf or "", len(totais),
                      max((t.snapshot_id for t in totais), default=0),
                      sum(t.snapshot_id for t in totais),
                      _reg.versao, _ativa)
        pronta = _RESPOSTAS.get(chave_lote)
        if pronta is not None:
            return Response(pronta, media_type="application/json")

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
        # Só as duas colunas: montar 5.570 objetos para ler dois campos era
        # parte do custo de cada pedido de mapa.
        for g_uf, g_ibge in s.execute(select(Geography.uf, Geography.cd_ibge)
                                      .where(Geography.environment == M.env_atual(),
                                             Geography.tipo == "mu",
                                             Geography.cd_ibge.is_not(None))):
            prefixos.setdefault(g_uf, Counter())[g_ibge[:2]] += 1
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

        # Linhas já calculadas vêm da memória; as que faltam, de UMA consulta.
        # Antes era uma consulta por território, e a definição matemática
        # refeita para os 645 municípios de SP a cada pedido — mesmo os que
        # não tinham mudado. Snapshot do TSE é imutável, então a linha que
        # sai dele também é: guardá-la por snapshot é exato, e o custo de um
        # pedido passa a acompanhar o que mudou desde o anterior.
        faltam = [t.snapshot_id for t in totais
                  if (ct.id, tipo, t.snapshot_id) not in _LINHAS]
        por_snap: dict[tuple[int, int], list] = {}
        for i in range(0, len(faltam), 900):
            for r in s.execute(select(
                    ObservedResult.snapshot_id, ObservedResult.geography_id,
                    ObservedResult.candidate_id, ObservedResult.votos,
                    ObservedResult.dvt).where(
                    ObservedResult.snapshot_id.in_(faltam[i:i + 900]))):
                por_snap.setdefault((r.snapshot_id, r.geography_id),
                                    []).append(r)
        if len(_LINHAS) > 200_000:
            _LINHAS.clear()

        linhas = []
        for t in totais:
            chave_memo = (ct.id, tipo, t.snapshot_id)
            if chave_memo in _LINHAS:
                linhas.append(_LINHAS[chave_memo])
                continue
            g = geos[t.geography_id]
            rows = por_snap.get((t.snapshot_id, t.geography_id), [])
            validos = [r for r in rows if r.dvt == "Válido"]
            validos.sort(key=lambda r: -r.votos)

            lider = cands.get(validos[0].candidate_id) if validos else None
            segundo = (cands.get(validos[1].candidate_id)
                       if len(validos) > 1 else None)
            v1 = validos[0].votos if validos else 0
            v2 = validos[1].votos if len(validos) > 1 else 0

            # Sub judice por candidato: sem isso, qualquer voto sub
            # judice na unidade suspendia a definição, mesmo quando ele não
            # muda quem se define (§4.7, decisão de 23/09/2026).
            sj = {cands[r.candidate_id].sqcand: r.votos for r in rows
                  if r.dvt == "Anulado sub judice" and r.candidate_id in cands}
            dfn = DC.avaliar(
                {cands[r.candidate_id].sqcand: r.votos for r in validos
                 if r.candidate_id in cands} | sj,
                vagas=ct.vagas,
                concorrentes_observados=t.validos_computados,
                pendente_max=t.pendente_max,
                sub_judice=t.anulados_sub_judice,
                sub_judice_por_candidato=sj or None,
                encerrada=(t.andamento == "f"),
                tem_segundo_turno=M.tem_segundo_turno(s, ct),
            )
            # Definição matemática é afirmação sobre a DISPUTA, e a
            # disputa não se decide num recorte. Num município de governador
            # a aritmética local fecha ("só dois não eliminados aqui") e
            # virava "2º turno matematicamente definido" na etiqueta do mapa
            # — sobre um segundo turno que se decide no estado inteiro
            # (visto no simulado de 23/09). No recorte, o que existe é a
            # apuração local encerrada, e é só isso que a tela diz.
            # A UF já encerrou e o município não? Não é "falta apurar": é
            # arquivo que o TSE não atualizou neste ciclo. No simulado de
            # 23/09, o Pará fechou em 100% com 6 municípios parados em 0% e
            # 7 no meio — dizer "em aberto" ali faria a sala procurar votos
            # que não existem mais.
            desatualizado = bool(encerradas_uf.get(g.uf)
                                 and t.andamento != "f")
            decide_aqui = (tipo == ct.abrangencia_decisoria)
            if not decide_aqui:
                estado_def = ("local_encerrada" if t.andamento == "f"
                              else "em_aberto")
            elif dfn.disputa_definida:
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
                "aguardando": False,
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
                "lider_partido_numero": (lider.partido_numero
                                         if lider else None),
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
                "desatualizado": desatualizado,
                "definicao": ("nao_atualizado" if desatualizado
                              else estado_def),
                "vagas": ct.vagas,
                "eleitos": [cands[r.candidate_id].nome_urna
                            for r in validos[:ct.vagas]
                            if r.candidate_id in cands
                            and dfn.disputa_definida],
            })

            _LINHAS[chave_memo] = linhas[-1]

        # Territórios sem resultado. Entram com tudo nulo e marcados:
        #
        # `aguardando` e não "0% apurado". As duas coisas coincidem no começo
        # da noite, e deixam de coincidir quando a quebra por município ainda
        # não foi coletada mas o total do estado já foi — Senado em SP com
        # 100% apurado no estado e cada município dizendo "0%" seria uma
        # contradição na mesma tela. "Aguardando resultado" é verdade nos
        # dois casos (§9: ausência é nulo com motivo, nunca zero).
        com_resultado = {t.geography_id for t in totais}
        for gid, g in geos.items():
            if gid in com_resultado:
                continue
            cd_geo = (g.cd_ibge if tipo == "mu" else cod_uf.get(g.uf))
            linhas.append({
                "cd_ibge": cd_geo, "cd_tse": g.cd_tse, "uf": g.uf,
                "nome": g.nome,
                "nome_extenso": (g.nome if tipo == "mu"
                                 else UF_EXTENSO.get(g.uf, g.nome)),
                "capital": g.capital,
                "eleitorado": None,
                "lider_sqcand": None, "lider_nome": None,
                "lider_partido": None, "lider_partido_numero": None,
                "lider_votos": None, "lider_pct": None,
                "segundo_sqcand": None, "segundo_nome": None,
                "segundo_partido": None, "segundo_votos": None,
                "segundo_pct": None,
                "margem_pp": None, "cobertura_pct": None,
                "regiao": REGIOES.get((cd_geo or "")[:1]),
                "unidade_decide": tipo == ct.abrangencia_decisoria,
                "eleito_tse": False,
                "andamento": None, "definicao": None,
                "vagas": ct.vagas, "eleitos": [],
                "aguardando": True,
            })

        # Buraco de publicação: UF encerrada com município parado. Vai para
        # `quality_flags`, que o painel mostra no rodapé — sem isso, só quem
        # passasse o mouse no município certo descobriria (23/09: o Pará
        # fechou em 100% com 13 municípios nessa situação).
        parados = [x["nome"] for x in linhas if x.get("desatualizado")]
        if parados:
            anomalias.append(
                f"{len(parados)} município(s) com arquivo não atualizado "
                f"pelo TSE, embora a UF tenha encerrado: "
                + ", ".join(sorted(parados)[:6])
                + ("…" if len(parados) > 6 else ""))

        # Partido e ideologia do líder. Calculados a cada pedido, fora da
        # memória por snapshot: o snapshot é imutável, a classificação não —
        # assinar a curadoria tem de mudar o mapa na hora, sem reiniciar.
        from ..politics import partidos as PA
        from .politico import _classificacao, _ideologia
        reg, ativa = _classificacao()
        ideo = _ideologia(reg, ativa)
        memo_p: dict = {}
        saida = []
        for x in linhas:
            k = (x["lider_partido_numero"], x["lider_partido"])
            if x["lider_partido"] is None:
                saida.append(x)
                continue
            if k not in memo_p:
                p = PA.resolver(*k)
                memo_p[k] = (p, ideo(p))
            p, i = memo_p[k]
            saida.append({**x, "lider_partido_cor": p.cor,
                          "lider_partido_sigla": p.sigla,
                          "lider_partido_cadastrado": p.cadastrado,
                          "lider_ideologia": i})
        linhas = saida

        # Paleta categórica dos líderes, estável por candidato.
        lideres = sorted({x["lider_sqcand"] for x in linhas
                          if x["lider_sqcand"]})
        corpo = M.envelope(
            linhas, snapshots=snaps,
            estado="observado" if totais else "aguardando",
            denominador=DenBase.VALIDOS_COMPUTADOS,
            quality_flags=anomalias,
            extra={
                "malha": ("municipios.json" if tipo == "mu" else "uf.json"),
                "abrangencia": tipo,
                "classificacao_estado": ("disponivel" if ativa
                                         else "curadoria_pendente"),
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
        # Pré-serializado. O mapa municipal do Brasil tem 5.755 linhas e 3,5 MB;
        # o codificador genérico do FastAPI levava 0,54 s para percorrê-lo,
        # `json.dumps` leva 0,10 s. Tudo aqui já é tipo nativo do JSON.
        texto = json.dumps(corpo, ensure_ascii=False, default=str)
        if len(_RESPOSTAS) > 12:
            _RESPOSTAS.clear()
        _RESPOSTAS[chave_lote] = texto
        return Response(texto, media_type="application/json")

    # O aquecedor chama a rota direto, com sessão própria, e mantém o cache
    # pronto para o telão (ver `aquecer`). Uma thread por processo.
    global _ROTA
    _ROTA = camadas_do_mapa
    if not any(t.name == "aquecedor-mapa" for t in threading.enumerate()):
        threading.Thread(target=aquecer, name="aquecedor-mapa",
                         daemon=True).start()
