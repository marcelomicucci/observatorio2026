"""Orquestração da coleta.

O desenho resolve o problema central do §8.1: uma varredura municipal completa
dos três cargos são 16.897 arquivos, ~14 minutos a 20 req/s. Prometer "todo
município atualizado em segundos" seria mentira.

A saída é não varrer. O EA15 de cada UF lista **todos** os municípios daquela
UF com `and` (andamento) e `ht` (hora da totalização). Logo:

    detecção:      2 eleições x (1 EA14 + 27 EA15)  =  56 requisições
    a cada 20 s:   ~2,8 req/s

e o EA20 municipal só é buscado para quem mudou. O custo de detecção é fixo e
independe do tamanho do país.

Três laços concorrentes, com orçamento compartilhado:

    controle       EA20 nacional/UF, a cada `control_interval_s`.
                   Não depende de detecção: é o número que vai ao ar.
    acompanhamento EA14/EA15 a cada `accompaniment_interval_s`; enfileira o
                   EA20 dos municípios cujo `change_token` mudou.
    reconciliação  varredura municipal completa a cada `reconcile_interval_s`,
                   com prioridade baixa. Existe porque a FAQ avisa que
                   EA14/EA15 podem chegar **antes ou depois** do EA20: a
                   detecção acelera, não é fonte de verdade.

A fila é por prioridade, com envelhecimento, para que nenhum território fique
sem atendimento indefinidamente (§8.1).
"""

from __future__ import annotations

import asyncio
import heapq
import itertools
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from ..storage.raw import RawArchive, SnapshotRef
from .catalog import Catalog
from .client import BlockedError, Fetch, TseClient
from .parsers import accompaniment as ab_parser
from .parsers import ea20 as ea20_parser
from .parsers.common import ParseError
from .urls import Target

log = logging.getLogger("obs2026.collector")

#: Tique da varredura rotativa. Curto de proposito: o custo medio depende de
#: `reconcile_interval_s`, e o tique so controla o quanto a carga fica plana.
RECONCILE_TICK_S = 5.0
#: Depois que o TSE encerra Brasil e todas as UFs, espera este tanto antes
#: de se desligar: os últimos EA20 (municípios, deputados) ainda chegam.
CARENCIA_ENCERRAMENTO_S = 600.0
#: Espera quando o Brasil não foi MARCADO como finalizado mas está completo.
#: No 1º turno de 2026 o TSE deixou o total nacional em "p" com 100% das
#: seções e todas as UFs finalizadas; o coletor ficou 7 horas pedindo
#: 25 mil arquivos por hora que não mudavam. Sem a marca, a espera é maior
#: e qualquer mudança num total a reinicia.
CARENCIA_SEM_MARCA_S = 1800.0


#: Níveis da fila. O nível decide antes do peso: nada de nível mais baixo
#: sai da fila enquanto houver algo de nível mais alto nela.
NIVEL_MUNICIPAL = 0
NIVEL_TOTAIS = 1


@dataclass(order=True)
class _QueueItem:
    # Nível antes do peso. Com peso só, os totais de Brasil e UF tinham peso
    # fixo de 1.000.000 e cada município o seu eleitorado — e São Paulo
    # (9,3 milhões), Rio, BH, Brasília e mais uma dúzia passavam à frente do
    # total nacional. Na noite, o número que a sala olha é o total; o
    # município é detalhe dele. Nenhum peso numérico resolve isso de forma
    # robusta, porque qualquer teto pode ser ultrapassado; um nível resolve.
    neg_nivel: int
    neg_priority: float
    seq: int
    target: Target = field(compare=False)
    enqueued_at: float = field(compare=False, default_factory=time.monotonic)
    reason: str = field(compare=False, default="")


class PriorityQueue:
    """Fila de prioridade com envelhecimento e deduplicação por chave.

    Deduplicar importa: numa rajada de mudanças, o mesmo município pode ser
    enfileirado pelo acompanhamento e pela reconciliação no mesmo ciclo.
    Buscá-lo duas vezes gasta cota e não traz informação.
    """

    def __init__(self, aging_boost_per_minute: float = 0.15) -> None:
        self._heap: list[_QueueItem] = []
        self._index: dict[str, _QueueItem] = {}
        self._counter = itertools.count()
        self.aging = aging_boost_per_minute

    def push(self, target: Target, priority: float, reason: str = "",
             nivel: int = NIVEL_MUNICIPAL) -> bool:
        key = target.key
        cur = self._index.get(key)
        if cur is not None:
            # já na fila: mantém o maior nível e a maior prioridade, sem
            # duplicar requisição
            if (-nivel, -priority) < (cur.neg_nivel, cur.neg_priority):
                cur.neg_nivel = min(cur.neg_nivel, -nivel)
                cur.neg_priority = min(cur.neg_priority, -priority)
                heapq.heapify(self._heap)
            return False
        item = _QueueItem(-nivel, -priority, next(self._counter), target,
                          reason=reason)
        self._index[key] = item
        heapq.heappush(self._heap, item)
        return True

    def pop(self) -> _QueueItem | None:
        while self._heap:
            item = heapq.heappop(self._heap)
            if self._index.get(item.target.key) is item:
                del self._index[item.target.key]
                return item
        return None

    def age(self) -> None:
        """Envelhece a fila para evitar inanição de unidades pequenas."""
        if not self._heap or self.aging <= 0:
            return
        now = time.monotonic()
        for item in self._heap:
            minutes = (now - item.enqueued_at) / 60.0
            item.neg_priority -= self.aging * minutes
            item.enqueued_at = now
        heapq.heapify(self._heap)

    def __len__(self) -> int:
        return len(self._index)


@dataclass
class CollectorStats:
    cycles: int = 0
    accompaniment_fetches: int = 0
    control_fetches: int = 0
    result_fetches: int = 0
    changes_detected: int = 0
    parse_errors: int = 0
    reconcile_sweeps: int = 0
    #: buscas evitadas porque o TSE já marcou a unidade como finalizada
    finalizados_pulados: int = 0
    #: unidades finalizadas que o acompanhamento reabriu (re-totalização)
    finalizados_reabertos: int = 0
    started_at: float = field(default_factory=time.monotonic)

    def as_dict(self, client: TseClient, queue: PriorityQueue) -> dict:
        elapsed = max(1e-6, time.monotonic() - self.started_at)
        return {
            **self.__dict__,
            "elapsed_s": round(elapsed, 1),
            "queue_depth": len(queue),
            "http": dict(client.stats),
            "effective_rps": round(client.stats["requests"] / elapsed, 2),
        }


class Collector:
    def __init__(self, catalog: Catalog, client: TseClient,
                 archive: RawArchive, cfg, *,
                 on_result=None, on_progress=None) -> None:
        self.catalog = catalog
        self.client = client
        self.archive = archive
        self.cfg = cfg
        self.queue = PriorityQueue(cfg.aging_boost_per_minute)
        self.stats = CollectorStats()
        #: última assinatura conhecida por unidade, por disputa
        self.progress_tokens: dict[str, str] = {}
        #: eleitorado conhecido por unidade — peso real da fila
        self.electorate: dict[str, int] = {}
        self.on_result = on_result
        self.on_progress = on_progress
        self._stop = asyncio.Event()
        self._reconcile_cursor = 0
        self._reconcile_cache: list[Target] | None = None
        #: EA20 que o TSE marcou como finalizado. Saem do controle e da
        #: varredura; voltam quando o acompanhamento acusar mudança.
        self.pular_finalizados = bool(getattr(cfg, "pular_finalizados", True))
        self.finalizados: set[str] = set()
        #: assinatura de Brasil e de cada UF, vinda do EA14/EA15 — é o que
        #: reabre um total finalizado se o TSE re-totalizar
        self.tokens_totais: dict[str, str] = {}
        #: alvos reabertos: só voltam a finalizados com um EA20 gerado depois
        #: desta hora (a da totalização que os reabriu)
        self._exige_gerado_apos: dict[str, datetime | None] = {}
        #: Totais de UF dos cargos proporcionais (deputado federal). Têm
        #: cadência própria e não passam por `on_result`: vão para o arquivo
        #: bruto e são lidos de lá, sem banco e sem motor.
        prop = getattr(catalog, "proporcional_targets", None)
        self._proporcionais = prop() if prop else []
        self._cds_proporcionais = {t.cargo for _, _, t in self._proporcionais}
        self._prox_proporcional: dict[str, float] = {}
        #: Chaves de operação (aba Configuração). `pular_finalizados` passado
        #: pela linha de comando fica fixo; senão vale o arquivo, relido a
        #: cada volta.
        self.pular_fixo = False
        self.deputados_ativos = True
        self._env_nome = getattr(getattr(catalog, "env", None), "name",
                                 "sem-ambiente")
        from .. import operacao as _op
        self._op = _op
        cmd = _op.ler_comando(self._env_nome)
        #: Comandos anteriores a este processo não são reexecutados: um
        #: "forçar varredura" de ontem não pode disparar num reinício.
        self._cmd_visto = (cmd or {}).get("id", 0)
        self._prox_batimento = 0.0
        #: Parada ao fim da apuração (pedido de 22/09/2026). Andamento de
        #: Brasil e de cada UF por eleição, do EA14/EA15. Só encerra se
        #: esta execução viu a apuração aberta: sem isso, ligar o coletor
        #: com os arquivos de um ciclo já fechado o desligaria na hora.
        self.encerrar_ao_finalizar = True
        self._andamento_totais: dict[str, str] = {}
        #: Totais com 100% das seções totalizadas, marcados ou não.
        self._completo_totais: dict[str, bool] = {}
        self._carencia = CARENCIA_ENCERRAMENTO_S
        self._viu_aberto = False
        self._tudo_final_desde: float | None = None
        self.encerrado_por_fim = False
        self.parado_pelo_painel = False

    def restaurar_do_arquivo(self) -> dict:
        """Recupera do arquivo bruto o que o coletor já sabia.

        O coletor guarda na memória duas coisas que o tornam barato: o ETag
        de cada arquivo (para o TSE responder 304 em vez de mandar tudo) e a
        última assinatura de progresso de cada município (para só buscar
        quem mudou). Um processo novo começava sem nenhuma das duas — e um
        reinício no meio da noite, por queda ou por o notebook dormir,
        virava 16.897 downloads completos, uns 14 minutos em que as
        mudanças de verdade esperavam na fila.

        As duas coisas estão no manifesto. **ETag** é sempre seguro de
        restaurar: no pior caso o TSE responde com o conteúdo novo.

        **Assinatura de progresso** exige cuidado. Restaurá-la diz "este
        município já está em dia", e se não estiver ele fica parado até a
        varredura de segurança passar, uma hora depois. O caso real é um
        coletor que caiu entre ler o EA15 e buscar os EA20.

        A prova usada é exata: a assinatura inclui a hora da última
        totalização do município, e o manifesto guarda a hora em que o TSE
        **gerou** cada EA20. Um EA20 gerado depois da última totalização
        necessariamente a contém. Só se restaura o município cujo EA20, em
        todos os cargos daquela eleição, foi gerado depois dela; o resto é
        buscado de novo, como antes.

        A primeira versão comparava a hora de *captura* do EA20 com a de
        captura do EA15 — correta, mas cega quando os EA20 vieram de uma
        varredura anterior ao primeiro EA15, e aí quase nada era restaurado.
        """
        ultimo: dict[str, SnapshotRef] = {}
        ea15: dict[tuple[str, str], SnapshotRef] = {}
        ea14: dict[str, SnapshotRef] = {}
        #: hora de geração (no TSE) do EA20 mais recente de cada unidade
        ea20_gerado: dict[tuple[str, str, str, str], datetime] = {}
        #: o mesmo para os arquivos de total (Brasil e UF), por chave
        total_gerado: dict[str, datetime] = {}
        for dia in self.archive.days():
            for ref in self.archive.manifest(dia):
                if ref.status not in (200, 304):
                    continue
                if ref.etag or ref.last_modified:
                    ultimo[ref.key] = ref
                if ref.kind == "ea15" and ref.path and ref.uf:
                    ea15[(ref.election, ref.uf)] = ref
                elif ref.kind == "ea14" and ref.path:
                    ea14[ref.election] = ref
                elif ref.kind == "ea20" and ref.source_generated_at:
                    try:
                        g = datetime.fromisoformat(ref.source_generated_at)
                    except ValueError:
                        continue
                    if ref.mun:
                        k = (ref.election, ref.cargo, ref.uf, ref.mun)
                        if k not in ea20_gerado or g > ea20_gerado[k]:
                            ea20_gerado[k] = g
                    elif (ref.key not in total_gerado
                          or g > total_gerado[ref.key]):
                        total_gerado[ref.key] = g

        for key, ref in ultimo.items():
            st = self.client.st(key)
            st.etag = ref.etag
            st.last_modified = ref.last_modified
            st.last_sha256 = ref.sha256

        por_eleicao: dict[str, list] = {}
        for c in self.catalog.contests:
            por_eleicao.setdefault(c.election, []).append(c)

        tokens = pendentes = 0
        for (eleicao, uf), ref in ea15.items():
            try:
                acc = ab_parser.parse(self.archive.load(ref), ref.kind, uf)
            except Exception as exc:                   # noqa: BLE001
                log.warning("retomada: EA15 %s ilegivel: %r", ref.key, exc)
                continue
            exigidos = [c for c in por_eleicao.get(eleicao, [])
                        if not (uf == "zz" and not c.cargo.includes_exterior)]
            if not exigidos:
                continue
            for unit in acc.units:
                if unit.tpabr != "mu":
                    continue
                gerados = [ea20_gerado.get((eleicao, c.cargo.cd, uf,
                                            unit.cdabr))
                           for c in exigidos]
                if any(g is None for g in gerados):
                    em_dia = False          # algum cargo nunca foi buscado
                elif unit.totalized_at is None:
                    em_dia = True           # nada totalizado ainda
                else:
                    em_dia = all(g >= unit.totalized_at for g in gerados)
                if em_dia:
                    self.progress_tokens[f"{eleicao}:{unit.key}"] = \
                        unit.change_token()
                    tokens += 1
                    # Em dia E finalizado: sai das buscas desde já.
                    if self.pular_finalizados and unit.andamento == "f":
                        for c in exigidos:
                            self.finalizados.add(self.catalog.urls.ea20_mun(
                                eleicao, c.cargo.cd, uf, unit.cdabr).key)
                else:
                    pendentes += 1

        # Totais: Brasil e UF, a partir do EA14, que traz o Brasil e todas as
        # UFs. Mesma prova dos municípios: o arquivo de total gerado depois
        # da última totalização a contém.
        unidades_total: dict[str, object] = {}
        for eleicao, ref in ea14.items():
            try:
                acc = ab_parser.parse(self.archive.load(ref), ref.kind, None)
            except Exception as exc:                   # noqa: BLE001
                log.warning("retomada: EA14 %s ilegivel: %r", ref.key, exc)
                continue
            for u in acc.units:
                if u.tpabr == "br":
                    unidades_total[f"{eleicao}:br"] = u
                elif u.tpabr == "uf":
                    unidades_total[f"{eleicao}:uf:{u.cdabr.lower()}"] = u
        for escopo, u in unidades_total.items():
            self.tokens_totais[escopo] = u.change_token()
        if self.pular_finalizados:
            for t in self.catalog.control_targets():
                u = unidades_total.get(self._escopo_total(t))
                g = total_gerado.get(t.key)
                if u is None or g is None or u.andamento != "f":
                    continue
                if u.totalized_at is None or g >= u.totalized_at:
                    self.finalizados.add(t.key)

        return {"etags": len(ultimo), "tokens": tokens,
                "pendentes": pendentes,
                "finalizados": len(self.finalizados)}

    def _escopo_total(self, t: Target) -> str:
        """Unidade de acompanhamento que cobre um alvo de controle: Brasil
        para o arquivo nacional, a UF para o arquivo estadual."""
        if t.uf is None:
            return f"{t.election}:br"
        return f"{t.election}:uf:{t.uf}"

    def _reabrir(self, key: str, totalizado_em: datetime | None) -> None:
        """Tira um alvo dos finalizados e registra o que ele precisa provar
        para voltar: um EA20 gerado depois da totalização que o reabriu.

        Sem essa exigência havia uma brecha. O TSE avisa que o EA14/EA15
        pode chegar antes do EA20 regenerado: a re-totalização aparece no
        acompanhamento, o coletor busca o EA20, recebe ainda a versão antiga
        — que diz `and: f` — e o remarcaria como finalizado. A versão nova,
        quando saísse, nunca seria buscada.
        """
        if key in self.finalizados:
            self.finalizados.discard(key)
            self.stats.finalizados_reabertos += 1
        self._exige_gerado_apos[key] = totalizado_em

    def _reabrir_totais(self, eleicao: str, escopo: str,
                        totalizado_em: datetime | None = None) -> None:
        alvos = self.catalog.control_targets() + [
            t for _, _, t in self._proporcionais]
        for t in alvos:
            if t.election == eleicao and self._escopo_total(t) == escopo:
                self._reabrir(t.key, totalizado_em)

    def _pular(self, t: Target) -> bool:
        if self.pular_finalizados and t.key in self.finalizados:
            self.stats.finalizados_pulados += 1
            return True
        return False

    def stop(self) -> None:
        self._stop.set()

    # -- captura + arquivo ----------------------------------------------

    async def _capture(self, t: Target) -> tuple[Fetch, dict | None,
                                                 SnapshotRef | None]:
        fetch = await self.client.fetch(t.url, key=t.key)
        payload = None
        if fetch.ok:
            try:
                payload = json.loads(fetch.body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                self.stats.parse_errors += 1
                log.error("payload invalido em %s: %s", t.url, exc)
                return fetch, None, None

        gen = None
        idg = None
        if payload:
            from .parsers.common import parse_dt
            gen = parse_dt(payload.get("dg"), payload.get("hg"))
            idg = str(payload.get("idg") or "") or None

        ref = None
        if fetch.status in (200, 304, 404) or fetch.body is not None:
            ref = self.archive.store(
                key=t.key, kind=t.kind, url=fetch.url, status=fetch.status,
                body=fetch.body, sha256=fetch.sha256,
                captured_at=fetch.captured_at, source_generated_at=gen,
                idg=idg, etag=fetch.etag, last_modified=fetch.last_modified,
                parser_version=fetch.parser_version, election=t.election,
                cargo=t.cargo, uf=t.uf, mun=t.mun,
            )
        return fetch, payload, ref

    # -- laço de acompanhamento -----------------------------------------

    async def cycle_accompaniment(self) -> int:
        """Busca EA14/EA15 e enfileira o EA20 de quem mudou."""
        enqueued = 0

        # Em paralelo. Um de cada vez, os 57 arquivos levavam uns 30 s só de
        # latência de rede — e o controle e a fila só andam entre um
        # acompanhamento e outro, então o total do estado, configurado para
        # a cada 10 s, saía na prática a cada 30. O teto de requisições
        # continua sendo o do token bucket; o que muda é não esperar a
        # resposta de um para pedir o próximo.
        alvos = self.catalog.accompaniment_targets()
        sem = asyncio.Semaphore(max(1, int(getattr(self.cfg, "max_workers",
                                                   16))))

        async def buscar(t: Target):
            async with sem:
                return await self._capture(t)

        capturas = await asyncio.gather(*(buscar(t) for t in alvos),
                                        return_exceptions=True)
        for c in capturas:
            # Bloqueio do TSE para tudo, como antes: insistir reinicia a
            # penalidade.
            if isinstance(c, BlockedError):
                raise c
        for t, c in zip(alvos, capturas):
            if isinstance(c, BaseException):
                log.error("acompanhamento falhou em %s: %r", t.url, c)
                continue
            fetch, payload, _ = c
            self.stats.accompaniment_fetches += 1
            if payload is None:
                continue
            try:
                acc = ab_parser.parse(payload, t.kind, t.uf)
            except ParseError as exc:
                self.stats.parse_errors += 1
                log.error("EA1x fora de contrato em %s: %s", t.url, exc)
                continue

            if self.on_progress:
                self.on_progress(acc)

            for unit in acc.units:
                if unit.electorate:
                    self.electorate[f"{t.election}:{unit.key}"] = \
                        unit.electorate.te
                if unit.tpabr in ("br", "uf"):
                    # Um total finalizado só volta a ser buscado se a
                    # assinatura dele mudar — re-totalização.
                    escopo = (f"{t.election}:br" if unit.tpabr == "br"
                              else f"{t.election}:uf:{unit.cdabr.lower()}")
                    self._andamento_totais[escopo] = unit.andamento
                    sc = unit.sections
                    self._completo_totais[escopo] = bool(
                        sc and sc.ts and sc.st >= sc.ts)
                    if unit.andamento != "f":
                        self._viu_aberto = True
                    tok = unit.change_token()
                    antes = self.tokens_totais.get(escopo)
                    self.tokens_totais[escopo] = tok
                    if antes is not None and antes != tok:
                        # Um total mudou: a contagem de silêncio recomeça.
                        self._tudo_final_desde = None
                        self._reabrir_totais(t.election, escopo,
                                             unit.totalized_at)
                    continue
                if unit.tpabr != "mu" or t.uf is None:
                    continue
                tok_key = f"{t.election}:{unit.key}"
                token = unit.change_token()
                if self.progress_tokens.get(tok_key) == token:
                    continue
                self.progress_tokens[tok_key] = token
                self.stats.changes_detected += 1
                for c in self.catalog.contests:
                    if c.election != t.election:
                        continue
                    if t.uf == "zz" and not c.cargo.includes_exterior:
                        continue
                    tgt = self.catalog.urls.ea20_mun(
                        c.election, c.cargo.cd, t.uf, unit.cdabr
                    )
                    w = (unit.electorate.te if unit.electorate else 1)
                    # Mudou: se estava finalizado, deixa de estar até um
                    # EA20 posterior a esta totalização dizer de novo.
                    self._reabrir(tgt.key, unit.totalized_at)
                    if self.queue.push(tgt, float(w), reason="mudanca"):
                        enqueued += 1
        return enqueued

    def verificar_fim(self, agora: float) -> bool:
        """True quando é hora de desligar: tudo encerrado há 10 min."""
        tot = self._andamento_totais
        marcado = bool(tot) and all(v == "f" for v in tot.values())
        # Sem a marca do Brasil: vale se todas as UFs estão finalizadas e o
        # total nacional tem 100% das seções.
        tudo_f = marcado or (bool(tot) and all(
            v == "f" or (k.endswith(":br") and self._completo_totais.get(k))
            for k, v in tot.items()))
        carencia = self._carencia = (CARENCIA_ENCERRAMENTO_S if marcado
                                     else CARENCIA_SEM_MARCA_S)
        if not (self.encerrar_ao_finalizar and self._viu_aberto and tudo_f):
            if self._tudo_final_desde is not None and not tudo_f:
                log.warning("apuração reaberta pelo TSE: coletor continua")
            self._tudo_final_desde = None
            return False
        if self._tudo_final_desde is None:
            self._tudo_final_desde = agora
            log.warning("%s; o coletor se desliga em %.0f min sem mudança "
                        "nos totais (últimos arquivos)",
                        "TSE encerrou Brasil e todas as UFs" if marcado else
                        "Todas as UFs encerradas e Brasil com 100% das "
                        "seções, sem a marca de finalizado", carencia / 60)
            return False
        return agora - self._tudo_final_desde >= carencia

    # -- laço de controle -------------------------------------------------

    async def cycle_control(self) -> None:
        """EA20 nacional e por UF. Sempre, independentemente de detecção."""
        for t in self.catalog.control_targets():
            if self._pular(t):
                continue
            self.queue.push(t, self.cfg.control_weight, reason="controle",
                            nivel=NIVEL_TOTAIS)

    async def cycle_proporcional(self, agora: float) -> int:
        """Deputado federal: os 27 arquivos de UF, cada cargo no seu ritmo.

        Nível dos totais, peso abaixo do controle: sai antes de qualquer
        município, mas nunca na frente do total de presidente, governador
        ou senador. Custo: 27 arquivos a cada `intervalo_s` (120 s = 0,23
        req/s), e o finalizado sai das buscas como os outros totais."""
        n = 0
        if not self.deputados_ativos:
            return 0
        for chave, intervalo, t in self._proporcionais:
            if agora < self._prox_proporcional.get(chave, 0.0):
                continue
            if self._pular(t):
                continue
            if self.queue.push(t, self.cfg.control_weight * 0.5,
                               reason="proporcional", nivel=NIVEL_TOTAIS):
                n += 1
        for chave, intervalo, _ in self._proporcionais:
            if agora >= self._prox_proporcional.get(chave, 0.0):
                self._prox_proporcional[chave] = agora + intervalo
        return n

    # -- chaves e comandos do painel -------------------------------------

    def aplicar_operacao(self) -> None:
        """Relê `config/operacao.yaml` (barato: só se a data mudou)."""
        try:
            op = self._op.carregar()
        except Exception:                          # noqa: BLE001
            return
        if not self.pular_fixo:
            self.pular_finalizados = bool(op["coleta"]["pular_finalizados"])
        self.deputados_ativos = bool(op["coleta"]["deputados_federais"])
        self.encerrar_ao_finalizar = bool(
            op["coleta"].get("encerrar_ao_finalizar", True))

    def atender_comandos(self) -> dict | None:
        """Executa o pedido do painel, se houver um novo.

        `forcar_totais` enfileira todos os totais (e os deputados) no nível
        dos totais, **inclusive os finalizados** — é para isso que o botão
        existe: desconfiar do que está na tela. `forcar_tudo` acrescenta a
        varredura municipal completa com prioridade baixa, atrás de tudo que
        muda."""
        cmd = self._op.ler_comando(self._env_nome)
        if not cmd or cmd.get("id", 0) <= self._cmd_visto:
            return None
        self._cmd_visto = cmd["id"]
        acao = cmd.get("acao")
        n = 0
        if acao == "parar":
            log.warning("comando parar (#%s): coletor desligando a pedido "
                        "do painel", cmd["id"])
            self.parado_pelo_painel = True
            self.stop()
        if acao in ("forcar_totais", "forcar_tudo"):
            for t in self.catalog.control_targets():
                self.finalizados.discard(t.key)
                if self.queue.push(t, self.cfg.control_weight,
                                   reason="forcado", nivel=NIVEL_TOTAIS):
                    n += 1
            for _, _, t in self._proporcionais:
                self.finalizados.discard(t.key)
                if self.queue.push(t, self.cfg.control_weight * 0.5,
                                   reason="forcado", nivel=NIVEL_TOTAIS):
                    n += 1
            self._prox_proporcional.clear()
        if acao == "forcar_tudo":
            for t in self._reconcile_targets():
                self.finalizados.discard(t.key)
                if self.queue.push(t, 0.0005, reason="forcado"):
                    n += 1
        log.info("comando %s (#%s): %d alvos enfileirados", acao,
                 cmd["id"], n)
        self._op.gravar_estado_coletor(
            self._env_nome, ultimo_comando_id=cmd["id"],
            ultimo_comando=acao, ultimo_comando_alvos=n,
            ultimo_comando_atendido_em=datetime.now(timezone.utc).isoformat())
        return {"acao": acao, "alvos": n}

    def batimento(self, agora: float) -> None:
        if agora < self._prox_batimento:
            return
        self._prox_batimento = agora + 5.0
        try:
            self._op.gravar_estado_coletor(
                self._env_nome, fila=len(self.queue),
                req_s=round(self.client.stats["requests"]
                            / max(1e-6, time.monotonic()
                                  - self.stats.started_at), 2),
                pular_finalizados=self.pular_finalizados,
                deputados_federais=self.deputados_ativos,
                finalizados=len(self.finalizados),
                encerrar_ao_finalizar=self.encerrar_ao_finalizar,
                encerra_em_s=(None if self._tudo_final_desde is None else
                              max(0, round(self._carencia
                                           - (agora
                                              - self._tudo_final_desde)))))
        except OSError:
            pass

    # -- varredura de reconciliação --------------------------------------

    async def cycle_reconcile(self, tick_s: float) -> int:
        """Varredura municipal **rotativa**, prioridade baixa.

        Rede de segurança contra EA15 defasado ou perdido. Nunca substitui o
        acompanhamento; os dois coexistem e o que chegar primeiro vale.

        A varredura é rotativa, não em rajada. Despejar os 16.897 alvos de uma
        vez a cada 15 minutos custa 18,8 req/s de pico — sozinho isso estoura o
        teto interno e deixa a detecção de mudança sem cota. Empurrando uma
        fatia proporcional a cada tique, o mesmo ciclo completo custa a mesma
        média, mas com carga plana e fila rasa.
        """
        alvos = self._reconcile_targets()
        total = len(alvos)
        if total == 0:
            return 0
        fatia = max(1, round(total * tick_s / self.cfg.reconcile_interval_s))
        n = 0
        for _ in range(fatia):
            t = alvos[self._reconcile_cursor % total]
            self._reconcile_cursor += 1
            if self._reconcile_cursor % total == 0:
                self.stats.reconcile_sweeps += 1
            if self._pular(t):
                continue
            key = f"{t.election}:mu:{t.mun}"
            w = self.electorate.get(key, 1) * 0.001   # peso baixo, não zero
            if self.queue.push(t, w, reason="reconciliacao"):
                n += 1
        return n

    def _reconcile_targets(self) -> list[Target]:
        if self._reconcile_cache is None:
            self._reconcile_cache = self.catalog.result_targets()
        return self._reconcile_cache

    # -- consumo da fila --------------------------------------------------

    async def _consumir(self, t: Target) -> None:
        """Captura e processa um alvo. Erro de um não derruba os outros."""
        fetch, payload, ref = await self._capture(t)
        if t.kind == "ea20":
            if t.abr == "mu":
                self.stats.result_fetches += 1
            else:
                self.stats.control_fetches += 1
        if payload is None:
            return
        try:
            result = ea20_parser.parse(payload)
        except ParseError as exc:
            self.stats.parse_errors += 1
            log.error("EA20 fora de contrato em %s: %s", t.url, exc)
            return
        if self.pular_finalizados:
            exige = self._exige_gerado_apos.get(t.key)
            prova = (t.key not in self._exige_gerado_apos or exige is None
                     or (result.generated_at is not None
                         and result.generated_at >= exige))
            if result.andamento == "f" and prova:
                self.finalizados.add(t.key)
                self._exige_gerado_apos.pop(t.key, None)
            else:
                # Ainda contando, ou a versão nova ainda não saiu: segue
                # sendo buscado.
                self.finalizados.discard(t.key)
        if self.on_result and t.cargo not in self._cds_proporcionais:
            self.on_result(result, ref, t)

    async def drain(self, budget: int) -> int:
        """Consome até `budget` itens da fila, em paralelo.

        **Por que paralelo.** A primeira versão buscava em sequência, e isso
        tornava o teto de 20 req/s decorativo: com jitter médio de 60 ms mais
        a latência da rede, o máximo alcançável era cerca de 3 req/s. Medido
        no simulado: 602 capturas em vários minutos, quando a conta de
        orçamento previa 14 minutos para as 16.897 da varredura completa. Na
        prática seriam horas.

        Quem limita a taxa é o token bucket, não a ausência de concorrência.
        Trabalhadores simultâneos apenas mantêm o balde vazio; o teto
        continua sendo exatamente o configurado, e o limite do TSE segue
        respeitado.

        `BlockedError` de qualquer trabalhador interrompe o lote inteiro: o
        bloqueio é por IP, então insistir com os outros só agravaria.
        """
        alvos: list[Target] = []
        while len(alvos) < budget:
            item = self.queue.pop()
            if item is None:
                break
            alvos.append(item.target)
        if not alvos:
            return 0

        fila: asyncio.Queue[Target] = asyncio.Queue()
        for t in alvos:
            fila.put_nowait(t)
        bloqueio: list[BlockedError] = []

        async def trabalhador() -> None:
            while True:
                try:
                    t = fila.get_nowait()
                except asyncio.QueueEmpty:
                    return
                if bloqueio:
                    return          # já houve bloqueio: não insistir
                try:
                    await self._consumir(t)
                except BlockedError as exc:
                    bloqueio.append(exc)
                    return
                except Exception as exc:        # noqa: BLE001
                    self.stats.parse_errors += 1
                    log.error("falha ao processar %s: %r", t.url, exc)

        n = max(1, min(self.cfg.max_workers, len(alvos)))
        await asyncio.gather(*(trabalhador() for _ in range(n)))
        if bloqueio:
            raise bloqueio[0]
        return len(alvos) - fila.qsize()

    # -- laço principal ---------------------------------------------------

    async def run(self, *, max_seconds: float | None = None) -> dict:
        t0 = time.monotonic()
        next_acc = 0.0
        next_ctl = 0.0
        next_rec = time.monotonic() + RECONCILE_TICK_S
        try:
            while not self._stop.is_set():
                now = time.monotonic()
                if max_seconds and (now - t0) >= max_seconds:
                    break
                self.stats.cycles += 1
                self.aplicar_operacao()
                self.atender_comandos()
                self.batimento(now)

                if now >= next_ctl:
                    await self.cycle_control()
                    next_ctl = now + self.cfg.control_interval_s
                if self._proporcionais:
                    await self.cycle_proporcional(now)
                if now >= next_acc:
                    await self.cycle_accompaniment()
                    next_acc = time.monotonic() + \
                        self.cfg.accompaniment_interval_s
                if now >= next_rec:
                    await self.cycle_reconcile(RECONCILE_TICK_S)
                    next_rec = time.monotonic() + RECONCILE_TICK_S

                self.queue.age()
                await self.drain(self.cfg.max_inflight_results)

                if self.verificar_fim(time.monotonic()):
                    self.encerrado_por_fim = True
                    log.warning("APURAÇÃO ENCERRADA PELO TSE — coletor "
                                "desligado. Painel e telão continuam no "
                                "ar com o resultado final.")
                    break

                if len(self.queue) == 0:
                    await asyncio.sleep(0.5)
        except BlockedError as exc:
            # Parar é a resposta correta: insistir reinicia o bloqueio do TSE.
            log.error("COLETA PAUSADA POR BLOQUEIO: %s", exc)
            motivo = "bloqueio do TSE"
        else:
            motivo = ("apuração encerrada pelo TSE" if self.encerrado_por_fim
                      else "pedido do painel" if self.parado_pelo_painel
                      else "fim do tempo ou interrupção")
        # A aba Configuração mostra por que parou, em vez de só "parado".
        try:
            self._op.gravar_estado_coletor(
                self._env_nome, parado_em=datetime.now(timezone.utc)
                .isoformat(), motivo_parada=motivo)
        except OSError:
            pass
        return self.stats.as_dict(self.client, self.queue)
