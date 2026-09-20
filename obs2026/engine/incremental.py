"""Carga incremental das observações, para projetar durante a apuração.

## O problema

Projetar a cada dois minutos durante seis horas significa repetir a mesma
leitura ~180 vezes. A versão simples relê o manifesto inteiro e reparsa todos
os 5.755 municípios a cada vez: medido em 3,08 s com o manifesto em 28 mil
linhas — e o manifesto **cresce a noite toda**. Ao fim, seriam centenas de
milhares de linhas relidas a cada projeção, para descobrir o que já se sabia.

## A saída

Duas memórias, ambas baratas:

1. **Deslocamento por arquivo de manifesto.** O manifesto é append-only, então
   basta lembrar até que byte já foi lido e continuar dali. Linhas novas são
   as únicas lidas.

2. **Cache de payload por `sha256`.** O conteúdo é endereçado por hash: se a
   unidade não mudou, o hash é o mesmo e o objeto já parseado é reaproveitado.
   Num ciclo típico da apuração, a esmagadora maioria dos municípios não
   mudou, e o custo cai para o punhado que mudou.

O cache é limitado por hash e não por unidade: uma unidade que volta a um
estado anterior (retotalização que desfaz) reencontra o payload sem reparsar,
e a memória não cresce com o tempo, só com a diversidade de conteúdos.

## O que este módulo NÃO faz

Não decide nada sobre o resultado. Ele só entrega as mesmas observações que
`runner.carregar_observacoes` entregaria, mais rápido. Se houver divergência
entre os dois, o correto é o não incremental — e por isso existe
`conferir()`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from ..storage.raw import RawArchive, SnapshotRef
from ..tse.parsers import ea20 as ea20_parser
from . import models as M


@dataclass
class CarregadorIncremental:
    """Mantém a última observação de cada unidade, atualizando só o que mudou."""

    archive: RawArchive
    cargo: str
    uf: str | None = None
    #: Quantos payloads parseados guardar. 20 mil cobre folgadamente os
    #: 16.897 arquivos de uma varredura completa.
    teto_cache: int = 20_000

    _offsets: dict[str, int] = field(default_factory=dict)
    _refs: dict[tuple[str, str], SnapshotRef] = field(default_factory=dict)
    _por_hash: dict[str, object] = field(default_factory=dict)
    _sujas: set[tuple[str, str]] = field(default_factory=set)
    _obs: dict[tuple[str, str], M.UnitObservation] = field(
        default_factory=dict)

    # -- leitura do manifesto -------------------------------------------

    def _novas_linhas(self) -> int:
        """Lê só o que chegou desde a última passagem. Devolve quantas."""
        n = 0
        for dia in self.archive.days():
            caminho = self.archive.root / "manifest" / f"{dia}.jsonl"
            if not caminho.exists():
                continue
            inicio = self._offsets.get(dia, 0)
            tamanho = caminho.stat().st_size
            if tamanho <= inicio:
                continue
            with open(caminho, "r", encoding="utf-8") as fh:
                fh.seek(inicio)
                for linha in fh:
                    linha = linha.strip()
                    if not linha:
                        continue
                    try:
                        ref = SnapshotRef(**json.loads(linha))
                    except (json.JSONDecodeError, TypeError):
                        # Linha truncada: o coletor ainda está escrevendo.
                        # Não avançamos o deslocamento além dela; a próxima
                        # passagem relê.
                        break
                    n += 1
                    self._registrar(ref)
                self._offsets[dia] = fh.tell()
        return n

    def _registrar(self, ref: SnapshotRef) -> None:
        if ref.kind != "ea20" or not ref.path or not ref.mun:
            return
        if ref.cargo != self.cargo:
            return
        if self.uf and ref.uf != self.uf:
            return
        chave = (ref.uf or "", ref.mun)
        anterior = self._refs.get(chave)
        if anterior is None or ref.captured_at > anterior.captured_at:
            self._refs[chave] = ref
            self._sujas.add(chave)

    # -- parsing com cache por hash --------------------------------------

    def _parsear(self, ref: SnapshotRef):
        chave_hash = ref.sha256 or ref.path
        pronto = self._por_hash.get(chave_hash)
        if pronto is not None:
            return pronto
        try:
            r = ea20_parser.parse(self.archive.load(ref))
        except Exception:                        # noqa: BLE001
            return None
        if len(self._por_hash) >= self.teto_cache:
            # Descarte simples: o conjunto util e' estavel, e um cache que
            # cresce sem limite numa noite de seis horas e' pior do que um
            # que ocasionalmente reparsa.
            self._por_hash.clear()
        self._por_hash[chave_hash] = r
        return r

    # -- interface --------------------------------------------------------

    def atualizar(self) -> dict:
        """Incorpora o que chegou. Devolve estatísticas da passagem."""
        novas = self._novas_linhas()
        reparsadas = 0
        for chave in list(self._sujas):
            ref = self._refs.get(chave)
            if ref is None:
                continue
            r = self._parsear(ref)
            if r is None:
                continue
            uf_ref = chave[0]
            self._obs[chave] = M.UnitObservation(
                unit=f"{uf_ref}:{r.cdabr}", uf=uf_ref,
                stratum=M.estrato(uf_ref, r.electorate.te), result=r,
            )
            reparsadas += 1
        self._sujas.clear()
        return {
            "linhas_novas": novas,
            "unidades_atualizadas": reparsadas,
            "unidades_totais": len(self._obs),
            "cache_payloads": len(self._por_hash),
        }

    def observacoes(self, uf: str | None = None
                    ) -> list[M.UnitObservation]:
        """Observações mantidas, opcionalmente de uma UF só.

        Um carregador por CARGO, e não por (cargo, UF): guardar 27 cópias
        dos mesmos municípios seria desperdício de memória numa noite em que
        ela importa. O filtro por UF é feito aqui, na saída.
        """
        if uf is None:
            return list(self._obs.values())
        alvo = uf.lower()
        return [o for o in self._obs.values() if o.uf == alvo]

    def conferir(self) -> dict:
        """Compara com a carga completa. Só para verificação.

        Se divergir, o correto é a carga completa: este módulo existe para
        ser mais rápido, não para ser mais esperto.
        """
        from . import runner as R
        completo = R.carregar_observacoes(self.archive, self.cargo, self.uf)
        meu = {o.unit: o.result.votes.vvc for o in self.observacoes()}
        dele = {o.unit: o.result.votes.vvc for o in completo}
        faltando = sorted(set(dele) - set(meu))
        sobrando = sorted(set(meu) - set(dele))
        divergentes = sorted(u for u in set(meu) & set(dele)
                             if meu[u] != dele[u])
        return {
            "iguais": not (faltando or sobrando or divergentes),
            "unidades_incremental": len(meu),
            "unidades_completo": len(dele),
            "faltando": faltando[:10],
            "sobrando": sobrando[:10],
            "divergentes": divergentes[:10],
        }
