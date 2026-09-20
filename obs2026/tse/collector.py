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


@dataclass(order=True)
class _QueueItem:
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

    def push(self, target: Target, priority: float, reason: str = "") -> bool:
        key = target.key
        cur = self._index.get(key)
        if cur is not None:
            # já na fila: mantém a maior prioridade, não duplica requisição
            if priority > -cur.neg_priority:
                cur.neg_priority = -priority
                heapq.heapify(self._heap)
            return False
        item = _QueueItem(-priority, next(self._counter), target, reason=reason)
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
        for t in self.catalog.accompaniment_targets():
            fetch, payload, _ = await self._capture(t)
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
                    if self.queue.push(tgt, float(w), reason="mudanca"):
                        enqueued += 1
        return enqueued

    # -- laço de controle -------------------------------------------------

    async def cycle_control(self) -> None:
        """EA20 nacional e por UF. Sempre, independentemente de detecção."""
        for t in self.catalog.control_targets():
            self.queue.push(t, self.cfg.control_weight, reason="controle")

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
        if self.on_result:
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

                if now >= next_ctl:
                    await self.cycle_control()
                    next_ctl = now + self.cfg.control_interval_s
                if now >= next_acc:
                    await self.cycle_accompaniment()
                    next_acc = time.monotonic() + \
                        self.cfg.accompaniment_interval_s
                if now >= next_rec:
                    await self.cycle_reconcile(RECONCILE_TICK_S)
                    next_rec = time.monotonic() + RECONCILE_TICK_S

                self.queue.age()
                await self.drain(self.cfg.max_inflight_results)

                if len(self.queue) == 0:
                    await asyncio.sleep(0.5)
        except BlockedError as exc:
            # Parar é a resposta correta: insistir reinicia o bloqueio do TSE.
            log.error("COLETA PAUSADA POR BLOQUEIO: %s", exc)
        return self.stats.as_dict(self.client, self.queue)
