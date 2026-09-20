"""Registro contínuo de projeções durante a apuração.

Enquanto o coletor busca, este laço projeta cada disputa a intervalos
regulares e grava o resultado no diário de auditoria. É o que dá pontos ao
gráfico de convergência: sem registros ao longo da noite, o gráfico tem um
ponto só e não prova nada.

## Três garantias

**Não bloqueia a coleta.** A simulação é trabalho de CPU em numpy, e rodá-la
no laço de eventos travaria as requisições ao TSE justamente no momento de
maior fluxo. Cada projeção vai para uma thread; o coletor continua buscando.

**Falha aqui não derruba nada.** A apuração é essencial, a projeção é
acessória. Erro numa disputa é registrado e a rodada segue para a próxima;
erro na rodada inteira não interrompe o coletor.

**Lê incrementalmente.** Um carregador por cargo, compartilhado entre as 27
UFs daquele cargo. Reler o bruto inteiro a cada projeção custaria segundos
que crescem com o manifesto a noite toda.

## Ritmo

Uma rodada projeta todas as disputas: 1 presidencial + 27 governadores + 27
senadores. Medido com dados completos do simulado, a rodada leva cerca de 20
segundos de CPU. A intervalos de 180 s isso é ~11% de um núcleo, e cada
disputa ganha um ponto a cada 3 minutos — cerca de 120 pontos numa noite de
seis horas.

Se uma rodada demorar mais que o intervalo, o laço **não acumula atraso**:
ele registra o excesso e recomeça, em vez de enfileirar rodadas que nunca
alcançam o presente.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

from ..config import Environment, Thresholds
from ..storage.raw import RawArchive
from . import audit, runner as R
from .incremental import CarregadorIncremental

log = logging.getLogger("obs2026.registro")

#: UFs com disputa estadual. ZZ não tem governador nem senador.
UFS = ("ac", "al", "ap", "am", "ba", "ce", "df", "es", "go", "ma", "mt", "ms",
       "mg", "pa", "pb", "pr", "pe", "pi", "rj", "rn", "rs", "ro", "rr", "sc",
       "sp", "se", "to")


@dataclass
class Estatisticas:
    rodadas: int = 0
    registros: int = 0
    falhas: int = 0
    ultima_duracao_s: float = 0.0
    atrasos: int = 0
    disputas_sem_dado: int = 0


@dataclass
class RegistradorContinuo:
    """Projeta e registra todas as disputas, em intervalos regulares."""

    env: Environment
    thr: Thresholds
    archive: RawArchive
    diario: audit.Diario
    intervalo_s: float = 180.0
    n_sims: int = 1500
    #: Cargos a projetar, como códigos do EA11.
    cargos: tuple[str, ...] = ("1", "3", "5")
    stats: Estatisticas = field(default_factory=Estatisticas)
    _carregadores: dict[str, CarregadorIncremental] = field(
        default_factory=dict)
    _parar: asyncio.Event | None = None

    def _carregador(self, cargo: str) -> CarregadorIncremental:
        c = self._carregadores.get(cargo)
        if c is None:
            c = CarregadorIncremental(archive=self.archive, cargo=cargo)
            self._carregadores[cargo] = c
        return c

    def _disputas(self, cargo: str) -> list[str | None]:
        """Presidente é uma disputa nacional; os demais, uma por UF."""
        return [None] if cargo == "1" else list(UFS)

    def _projetar_uma(self, cargo: str, uf: str | None,
                      obs) -> bool:
        """Roda e registra uma disputa. Devolve se registrou."""
        if not obs:
            self.stats.disputas_sem_dado += 1
            return False
        try:
            R.executar(
                self.env, self.thr, cargo=cargo, uf=uf,
                n_sims=self.n_sims, archive=self.archive,
                diario=self.diario, registrar=True, observacoes=obs,
            )
            return True
        except Exception as exc:                 # noqa: BLE001
            # Projeção é acessória: uma disputa que falha não pode levar a
            # rodada nem a coleta junto.
            self.stats.falhas += 1
            log.warning("projecao falhou em cargo=%s uf=%s: %r",
                        cargo, uf, exc)
            return False

    def _rodada(self) -> dict:
        """Uma passagem por todas as disputas. Roda numa thread."""
        t0 = time.monotonic()
        registradas = 0
        detalhe = {}
        for cargo in self.cargos:
            carregador = self._carregador(cargo)
            detalhe[cargo] = carregador.atualizar()
            for uf in self._disputas(cargo):
                obs = carregador.observacoes(uf)
                if self._projetar_uma(cargo, uf, obs):
                    registradas += 1
        self.stats.rodadas += 1
        self.stats.registros += registradas
        self.stats.ultima_duracao_s = time.monotonic() - t0
        return {"registradas": registradas,
                "duracao_s": round(self.stats.ultima_duracao_s, 2),
                "carga": detalhe}

    def parar(self) -> None:
        if self._parar:
            self._parar.set()

    async def rodar(self, *, max_seconds: float | None = None) -> dict:
        """Laço principal. Roda em paralelo ao coletor."""
        self._parar = asyncio.Event()
        inicio = time.monotonic()
        while not self._parar.is_set():
            if max_seconds and (time.monotonic() - inicio) >= max_seconds:
                break
            try:
                # A thread é o que impede a simulação de travar as
                # requisições ao TSE.
                resumo = await asyncio.to_thread(self._rodada)
                log.info("projecoes registradas: %s em %ss",
                         resumo["registradas"], resumo["duracao_s"])
            except Exception as exc:             # noqa: BLE001
                self.stats.falhas += 1
                log.error("rodada de projecao falhou: %r", exc)
                resumo = {"duracao_s": 0.0}

            sobra = self.intervalo_s - resumo.get("duracao_s", 0.0)
            if sobra <= 0:
                # Sem acumular atraso: rodadas enfileiradas nunca alcançam o
                # presente, e o que importa é o ponto mais recente.
                self.stats.atrasos += 1
                log.warning(
                    "rodada levou %.1fs, acima do intervalo de %.0fs; "
                    "recomecando sem enfileirar",
                    resumo.get("duracao_s", 0.0), self.intervalo_s)
                sobra = 1.0
            try:
                await asyncio.wait_for(self._parar.wait(), timeout=sobra)
            except asyncio.TimeoutError:
                pass
        return self.stats.__dict__
