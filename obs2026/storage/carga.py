"""Carga incremental do arquivo bruto para o banco.

O painel e o telão leem do banco; o coletor grava no arquivo bruto. Esta é a
ponte, e ela roda de dois jeitos:

- `obs2026 carregar`, à mão, depois de uma coleta avulsa;
- **dentro do `coletar`**, a cada poucos segundos, enquanto a coleta corre.

O segundo é o que importa na noite. Antes, o coletor só gravava no bruto, e o
banco mudava quando alguém lembrava de rodar `carregar` — por mais rápido que
o TSE atualizasse, o telão ficava parado no último comando digitado.

## Incremental

O banco guarda até que byte de cada dia do manifesto já foi lido
(`carga_manifesto`), na mesma transação dos dados. Cada passada lê só o que
chegou depois. O custo acompanha o que mudou, não o tamanho da noite.

## Os números grandes primeiro

Dentro de cada janela de leitura, os EA20 de Brasil e de UF são gravados e
confirmados **antes** dos municipais. Numa rajada de três mil municípios, o
total do estado não espera a fila: é o número que a sala está olhando, e os
municípios são detalhe dele. O ponto de retomada só avança quando a janela
inteira foi gravada; se o processo cair entre as duas etapas, a janela é
relida, e a gravação é idempotente.

## Nada fica para trás em silêncio

Arquivo que o leitor recusa não é pulado: vai para `carga_pendente`, e a
carga relê os pendentes ao iniciar e a cada `RELER_PENDENTES_S`. Corrigido o
leitor e reiniciado o coletor, o que ficou para trás entra sozinho. Foi o que
faltou no simulado de 22/09 (ver `models.CargaPendente`).
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ..tse.parsers import ea20 as ea20_parser
from .ingest import ingest_ea20
from .models import CargaManifesto, CargaPendente
from .raw import RawArchive, SnapshotRef

#: Linhas do manifesto por janela. Grande o bastante para uma rajada caber
#: inteira, pequena o bastante para o total do estado não esperar muito.
JANELA = 2000

#: De quanto em quanto tempo a carga contínua relê os pendentes. Uma falha de
#: leitura persiste até o código mudar (o que exige reiniciar), então reler a
#: cada passada de 5 s só gastaria processador; na partida e a cada 10 min
#: basta.
RELER_PENDENTES_S = 600.0


def _ident(ref) -> str:
    return f"{ref.sha256}|{ref.captured_at}|{ref.key}"[:256]


def reler_pendentes(s: Session, env, archive: RawArchive,
                    limite: int = 5000) -> dict:
    """Tenta de novo os arquivos que a leitura recusou. Devolve contagens."""
    agora = datetime.now(timezone.utc)
    out = {"tentados": 0, "gravados": 0, "ainda_falham": 0}
    for pend in (s.query(CargaPendente).order_by(CargaPendente.id)
                 .limit(limite).all()):
        out["tentados"] += 1
        ref = SnapshotRef(**json.loads(pend.ref_json))
        try:
            r = ea20_parser.parse(archive.load(ref))
        except Exception as exc:                      # noqa: BLE001
            pend.tentativas += 1
            pend.ultima_tentativa_em = agora
            pend.motivo = f"{type(exc).__name__}: {exc}"[:2000]
            out["ainda_falham"] += 1
            continue
        ingest_ea20(s, env, ref, r)
        s.delete(pend)
        out["gravados"] += 1
    s.commit()
    return out


def carregar_novos(s: Session, env, archive: RawArchive, *,
                   completa: bool = False, janela: int = JANELA) -> dict:
    """Grava no banco o que chegou ao bruto desde a última passada."""
    if completa:
        s.query(CargaManifesto).delete()
        s.commit()
    ponto = {c.dia: c for c in s.query(CargaManifesto).all()}

    tot = {"linhas": 0, "ea20": 0, "grandes": 0, "snapshot": 0,
           "candidates": 0, "results": 0, "pendentes": 0}
    flags: dict[str, int] = {}

    def gravar(ref) -> None:
        try:
            r = ea20_parser.parse(archive.load(ref))
        except Exception as exc:                       # noqa: BLE001
            k = f"parse:{type(exc).__name__}"
            flags[k] = flags.get(k, 0) + 1
            # Não se pula: fica pendente, e volta quando o leitor for
            # corrigido (ver `reler_pendentes`).
            ident = _ident(ref)
            if not s.query(CargaPendente).filter_by(ident=ident).first():
                agora = datetime.now(timezone.utc)
                s.add(CargaPendente(
                    ident=ident, dia=ref.captured_at[:10],
                    ref_json=json.dumps(asdict(ref), ensure_ascii=False),
                    motivo=f"{type(exc).__name__}: {exc}"[:2000],
                    tentativas=1, primeira_falha_em=agora,
                    ultima_tentativa_em=agora))
            tot["pendentes"] += 1
            return
        out = ingest_ea20(s, env, ref, r)
        for k in ("snapshot", "candidates", "results"):
            tot[k] += out[k]
        for f in out["flags"]:
            flags[f] = flags.get(f, 0) + 1
        tot["ea20"] += 1

    def fechar_janela(dia: str, fim: int, refs: list) -> None:
        # Brasil e UF primeiro, com commit próprio: o total do estado fica
        # visível no painel antes de começar a fila de municípios.
        grandes = [r for r in refs if not r.mun]
        miudos = [r for r in refs if r.mun]
        for r in grandes:
            gravar(r)
        tot["grandes"] += len(grandes)
        if grandes:
            s.commit()
        for i, r in enumerate(miudos, 1):
            gravar(r)
            if i % 200 == 0:
                s.commit()
        c = ponto.get(dia)
        if c is None:
            c = CargaManifesto(dia=dia)
            s.add(c)
            ponto[dia] = c
        c.deslocamento = fim
        c.atualizado_em = datetime.now(timezone.utc)
        s.commit()

    # Deputado federal vai para o bruto e é lido de lá (politics/camara.py):
    # não tem disputa no banco e não deve gerar aviso de "fora do catálogo".
    proporcionais = {p["cd"] for p in
                     (getattr(env, "proporcionais", None) or {}).values()}

    for dia in archive.days():
        inicio = ponto[dia].deslocamento if dia in ponto else 0
        refs: list = []
        fim = inicio
        for ref, fim_linha in archive.manifest_desde(dia, inicio):
            tot["linhas"] += 1
            fim = fim_linha
            if (ref.kind == "ea20" and ref.path
                    and ref.cargo not in proporcionais):
                refs.append(ref)
            if len(refs) >= janela:
                fechar_janela(dia, fim, refs)
                refs = []
        if fim != inicio:
            fechar_janela(dia, fim, refs)

    tot["flags"] = flags
    return tot


class CargaContinua:
    """Roda `carregar_novos` a cada poucos segundos, enquanto a coleta corre.

    Mesmas garantias do registro de projeções: a passada vai para uma
    thread (gravar no SQLite no laço de eventos atrasaria as requisições ao
    TSE), falha numa passada é registrada e a próxima tenta de novo, e uma
    passada lenta não enfileira outras — recomeça do ponto em que o banco
    está.
    """

    def __init__(self, env, archive: RawArchive, intervalo_s: float = 5.0):
        import asyncio
        from .db import create_all, make_engine, make_session_factory
        self.env = env
        self.archive = archive
        self.intervalo_s = intervalo_s
        eng = make_engine(sqlite_path=env.db_path)
        create_all(eng)
        self._Sess = make_session_factory(eng)
        self._parar = asyncio.Event()
        self.stats = {"passadas": 0, "ea20": 0, "grandes": 0, "falhas": 0,
                      "ultima_s": 0.0, "pendentes_novos": 0,
                      "pendentes_recuperados": 0}
        #: 0 = reler os pendentes já na primeira passada
        self._proxima_releitura = 0.0

    def sincronizar_catalogo(self, cat) -> dict:
        """Geografias e disputas no banco, uma vez, antes da primeira
        passada: o EA20 de um município precisa da geografia dele."""
        from .ingest import sync_catalog
        with self._Sess() as s:
            return sync_catalog(s, cat)

    def _passada(self) -> dict:
        import time
        t0 = time.monotonic()
        with self._Sess() as s:
            if time.monotonic() >= self._proxima_releitura:
                self._proxima_releitura = time.monotonic() + RELER_PENDENTES_S
                rp = reler_pendentes(s, self.env, self.archive)
                self.stats["pendentes_recuperados"] += rp["gravados"]
                if rp["tentados"]:
                    import logging
                    logging.getLogger("obs2026.carga").info(
                        "carga: %s pendentes relidos, %s gravados, %s ainda "
                        "falham", rp["tentados"], rp["gravados"],
                        rp["ainda_falham"])
            tot = carregar_novos(s, self.env, self.archive)
        self.stats["pendentes_novos"] += tot["pendentes"]
        self.stats["passadas"] += 1
        self.stats["ea20"] += tot["ea20"]
        self.stats["grandes"] += tot["grandes"]
        self.stats["ultima_s"] = round(time.monotonic() - t0, 2)
        return tot

    def parar(self) -> None:
        self._parar.set()

    async def rodar(self) -> dict:
        import asyncio
        import logging
        import time
        log = logging.getLogger("obs2026.carga")
        while not self._parar.is_set():
            t0 = time.monotonic()
            try:
                tot = await asyncio.to_thread(self._passada)
                if tot["ea20"]:
                    log.info("carga: %s EA20 no banco (%s de Brasil/UF) "
                             "em %ss", tot["ea20"], tot["grandes"],
                             self.stats["ultima_s"])
            except Exception as exc:                   # noqa: BLE001
                self.stats["falhas"] += 1
                log.error("carga falhou: %r", exc)
            sobra = max(0.5, self.intervalo_s - (time.monotonic() - t0))
            try:
                await asyncio.wait_for(self._parar.wait(), timeout=sobra)
            except asyncio.TimeoutError:
                pass
        # Uma última passada, para o banco terminar igual ao bruto.
        try:
            await asyncio.to_thread(self._passada)
        except Exception as exc:                       # noqa: BLE001
            self.stats["falhas"] += 1
            log.error("carga final falhou: %r", exc)
        return self.stats
