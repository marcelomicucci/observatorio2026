"""Carga e validação da configuração.

Duas garantias que este módulo impõe:

1. **Barreira de ambiente.** Simulado e oficial nunca compartilham diretório
   de bruto, banco nem chave de unicidade (§9). Um payload de simulado não
   pode encostar no produto oficial.

2. **Recusa explícita do ambiente incompleto.** Os códigos de produção de 2026
   ainda não foram publicados pelo TSE. Em vez de deixar `None` virar string
   vazia e produzir uma URL que gera 404 em massa, `load_environment` levanta
   erro com a razão.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = REPO_ROOT / "config"
DATA_DIR = REPO_ROOT / "data"


class ConfigError(RuntimeError):
    pass


def _read(path: Path) -> dict:
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@dataclass(frozen=True, slots=True)
class CargoConfig:
    key: str
    cd: str
    abrangencia_decisoria: str
    expected_seats: int
    includes_exterior: bool


@dataclass(frozen=True, slots=True)
class Environment:
    name: str
    label: str
    base: str
    ambiente: str
    official: bool
    ciclo: str
    cd_pleito: str
    seed_elections: tuple[str, ...]
    allowed_hosts: frozenset[str]
    cargos: dict[str, CargoConfig]
    #: Cargos proporcionais coletados só no total da UF, sem projeção e sem
    #: municípios: `{chave: {"cd": "6", "intervalo_s": 120.0}}`. Ficam fora
    #: de `cargos` de propósito — o que está em `cargos` vira disputa no
    #: catálogo, no banco e no motor.
    proporcionais: dict[str, dict] = field(default_factory=dict)
    #: Faixa do telão para ambiente não oficial. `None` usa o texto padrão
    #: de simulado.
    aviso: str | None = None
    #: Ambiente montado a partir de arquivo histórico, para demonstração. O
    #: coletor recusa: não há o que buscar no TSE.
    demonstracao: bool = False
    #: Eleição passada no formato do ao vivo (`history/congelado.py`), lida
    #: pelo seletor de anos. Só estes o painel pode pedir por `eleicao=`.
    congelado: bool = False
    #: Pleitos do catálogo que este ambiente coleta. Vazio = todos do ciclo.
    #: Desde 18/10/2026 o catálogo de produção traz o 2º turno como pleito
    #: próprio (3221) no mesmo ciclo do 1º (3220): sem o filtro, os dois
    #: turnos caem no mesmo banco e a API escolhe um deles sem critério.
    pleitos: tuple[str, ...] = ()
    #: Ambiente do 2º turno: o banco (congelado) do 1º turno do mesmo ano.
    #: A comparação ano a ano lê dele o que o 2º turno não tem — Senado,
    #: Câmara, governador das UFs decididas no 1º turno.
    primeiro_turno: str | None = None

    def aceita_pleito(self, cd: str) -> bool:
        return not self.pleitos or str(cd) in self.pleitos

    @property
    def raw_root(self) -> Path:
        return DATA_DIR / "raw"

    @property
    def db_path(self) -> Path:
        return DATA_DIR / "db" / f"{self.name}.sqlite"

    @property
    def lock_path(self) -> Path:
        return DATA_DIR / f"collector-{self.name}.lock"

    def cargo_by_cd(self, cd: str) -> CargoConfig | None:
        for c in self.cargos.values():
            if c.cd == str(cd):
                return c
        return None


@dataclass(frozen=True, slots=True)
class CollectorConfig:
    max_rps: float
    burst: int
    jitter_ms: tuple[int, int]
    #: Requisicoes simultaneas. Nao altera o teto de taxa — so permite
    #: alcanca-lo. Ver o comentario em config/collector.yaml.
    max_workers: int
    connect_timeout_s: float
    read_timeout_s: float
    max_attempts: int
    backoff_base_s: float
    backoff_max_s: float
    retry_on_status: tuple[int, ...]
    not_found_suspend_s: float
    error_streak_trip: int
    error_streak_cooldown_s: float
    blocked_cooldown_s: float
    accompaniment_interval_s: float
    reconcile_interval_s: float
    control_interval_s: float
    max_inflight_results: int
    aging_boost_per_minute: float
    control_weight: float
    #: Registro continuo de projecoes durante a apuracao.
    projecao_ativa: bool = True
    projecao_intervalo_s: float = 180.0
    projecao_n_sims: int = 1500
    carga_ativa: bool = True
    carga_intervalo_s: float = 5.0
    #: Não buscar EA20 de unidade que o TSE já marcou como finalizada.
    pular_finalizados: bool = True

    def budget_floor_rps(self, *, n_accompaniment: int, n_control: int,
                         n_municipal: int,
                         proporcional_rps: float = 0.0) -> dict:
        """Piso de requisições por segundo em regime permanente.

        As três fontes de carga *contínua* somam antes de qualquer EA20 movido
        por mudança. Se esse piso já encosta no teto, a detecção fica sem cota
        justamente na hora em que ela importa — e a primeira evidência disso
        seria um bloqueio de 10 minutos no meio da apuração.
        """
        acc = n_accompaniment / self.accompaniment_interval_s
        ctl = n_control / self.control_interval_s
        rec = n_municipal / self.reconcile_interval_s
        floor = acc + ctl + rec + proporcional_rps
        return {
            "acompanhamento_rps": acc,
            "controle_rps": ctl,
            "reconciliacao_rps": rec,
            "proporcional_rps": proporcional_rps,
            "piso_rps": floor,
            "teto_rps": self.max_rps,
            "folga_rps": self.max_rps - floor,
            "cabe": floor <= self.max_rps,
        }

    def assert_budget(self, **kw) -> dict:
        b = self.budget_floor_rps(**kw)
        if not b["cabe"]:
            raise ConfigError(
                "cadencia inviavel: o piso continuo de "
                f"{b['piso_rps']:.1f} req/s excede o teto interno de "
                f"{b['teto_rps']:.1f} req/s "
                f"(acompanhamento {b['acompanhamento_rps']:.1f} + controle "
                f"{b['controle_rps']:.1f} + reconciliacao "
                f"{b['reconciliacao_rps']:.1f}). "
                "Aumente reconcile_interval_s, reduza a cadencia ou eleve "
                "max_rps — sempre abaixo dos 100 req/s do TSE."
            )
        return b


@dataclass(frozen=True, slots=True)
class Thresholds:
    """Limiares de liberação, com hash para rastreabilidade (§4.7)."""

    homologated: bool
    signed_by: str | None
    signed_at: str | None
    gates: dict
    monte_carlo: dict
    config_hash: str
    #: Modelo cuja evidência foi assinada (`evidencia.modelo`). A assinatura
    #: é de UM modelo: trocar o modelo em produção sem assinar de novo não
    #: leva a homologação junto. `None` em arquivo antigo, sem o registro.
    modelo_homologado: str | None = None

    def vale_para(self, modelo: str | None) -> bool:
        """A homologação cobre este modelo?"""
        return self.homologated and (
            self.modelo_homologado is None or modelo is None
            or self.modelo_homologado == modelo)

    def require_homologated(self) -> None:
        if not self.homologated:
            raise ConfigError(
                "thresholds.yaml nao esta homologado: o produto opera em "
                "'cenario_exploratorio' e nao publica probabilidade de vitoria"
            )


def load_environment(name: str, config_dir: Path = CONFIG_DIR) -> Environment:
    src = _read(config_dir / "sources.yaml")
    envs = src.get("environments", {})
    if name not in envs:
        raise ConfigError(
            f"ambiente {name!r} nao existe. Disponiveis: {sorted(envs)}"
        )
    e = envs[name]
    seed = e.get("seed") or {}
    missing = [k for k in ("base", "ambiente") if not e.get(k)]
    if missing or not seed.get("ciclo"):
        raise ConfigError(
            f"ambiente {name!r} incompleto (faltam {missing or ['seed.ciclo']}). "
            "Os codigos do ambiente de producao ainda nao foram publicados "
            "pelo TSE; preencher config/sources.yaml com os valores oficiais "
            "antes de coletar. Nenhum valor sera adivinhado."
        )
    cargos = {
        k: CargoConfig(
            key=k, cd=str(v["cd"]),
            abrangencia_decisoria=v["abrangencia_decisoria"],
            expected_seats=int(v["expected_seats"]),
            includes_exterior=bool(v["includes_exterior"]),
        )
        for k, v in (src.get("cargos") or {}).items()
    }
    return Environment(
        name=name,
        label=e.get("label", name),
        base=e["base"],
        ambiente=e["ambiente"],
        official=bool(e.get("official", False)),
        ciclo=str(seed["ciclo"]),
        cd_pleito=str(seed.get("cd_pleito") or ""),
        seed_elections=tuple(str(x) for x in seed.get("elections") or ()),
        allowed_hosts=frozenset(src.get("allowed_hosts") or ()),
        cargos=cargos,
        proporcionais={
            k: {"cd": str(v["cd"]),
                "intervalo_s": float(v.get("intervalo_s", 120)),
                # Onde o cargo existe: só nestas UFs, ou em todas menos
                # aquelas. Vazio = as 27.
                "ufs": tuple(str(u).lower() for u in v.get("ufs") or ()),
                "exceto_ufs": tuple(str(u).lower()
                                    for u in v.get("exceto_ufs") or ())}
            for k, v in (src.get("proporcionais") or {}).items()
        },
        aviso=e.get("aviso"),
        demonstracao=bool(e.get("demonstracao", False)),
        congelado=bool(e.get("congelado", False)),
        pleitos=tuple(str(x) for x in e.get("pleitos") or ()),
        primeiro_turno=e.get("primeiro_turno") or None,
    )


def load_collector(config_dir: Path = CONFIG_DIR) -> CollectorConfig:
    c = _read(config_dir / "collector.yaml")
    r, rt, ci, ca, pr = (c["rate"], c["retry"], c["circuit"],
                         c["cadence"], c["priority"])
    if float(r["max_rps"]) > 100.0:
        raise ConfigError(
            f"max_rps={r['max_rps']} excede o limite de 100 req/s por IP "
            "publicado pelo TSE"
        )
    return CollectorConfig(
        max_rps=float(r["max_rps"]), burst=int(r["burst"]),
        jitter_ms=tuple(r["jitter_ms"]),
        max_workers=int(r.get("max_workers", 16)),
        connect_timeout_s=float(r["connect_timeout_s"]),
        read_timeout_s=float(r["read_timeout_s"]),
        max_attempts=int(rt["max_attempts"]),
        backoff_base_s=float(rt["backoff_base_s"]),
        backoff_max_s=float(rt["backoff_max_s"]),
        retry_on_status=tuple(int(x) for x in rt["retry_on_status"]),
        not_found_suspend_s=float(ci["not_found_suspend_s"]),
        error_streak_trip=int(ci["error_streak_trip"]),
        error_streak_cooldown_s=float(ci["error_streak_cooldown_s"]),
        blocked_cooldown_s=float(ci["blocked_cooldown_s"]),
        accompaniment_interval_s=float(ca["accompaniment_interval_s"]),
        reconcile_interval_s=float(ca["reconcile_interval_s"]),
        control_interval_s=float(ca["control_interval_s"]),
        max_inflight_results=int(ca["max_inflight_results"]),
        aging_boost_per_minute=float(pr["aging_boost_per_minute"]),
        control_weight=float(pr["control_weight"]),
        projecao_ativa=bool((c.get("projecao") or {}).get("ativa", True)),
        projecao_intervalo_s=float(
            (c.get("projecao") or {}).get("intervalo_s", 180)),
        projecao_n_sims=int((c.get("projecao") or {}).get("n_sims", 1500)),
        carga_ativa=bool((c.get("carga") or {}).get("ativa", True)),
        carga_intervalo_s=float(
            (c.get("carga") or {}).get("intervalo_s", 5)),
        pular_finalizados=bool(ca.get("pular_finalizados", True)),
    )


def load_thresholds(config_dir: Path = CONFIG_DIR) -> Thresholds:
    p = config_dir / "thresholds.yaml"
    t = _read(p)
    return Thresholds(
        homologated=bool(t.get("homologated", False)),
        signed_by=t.get("signed_by"),
        signed_at=t.get("signed_at"),
        gates=t.get("release_gates") or {},
        monte_carlo=t.get("monte_carlo") or {},
        config_hash=file_hash(p),
        modelo_homologado=((t.get("evidencia") or {}).get("modelo") or None),
    )
