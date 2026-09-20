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

    def budget_floor_rps(self, *, n_accompaniment: int, n_control: int,
                         n_municipal: int) -> dict:
        """Piso de requisições por segundo em regime permanente.

        As três fontes de carga *contínua* somam antes de qualquer EA20 movido
        por mudança. Se esse piso já encosta no teto, a detecção fica sem cota
        justamente na hora em que ela importa — e a primeira evidência disso
        seria um bloqueio de 10 minutos no meio da apuração.
        """
        acc = n_accompaniment / self.accompaniment_interval_s
        ctl = n_control / self.control_interval_s
        rec = n_municipal / self.reconcile_interval_s
        floor = acc + ctl + rec
        return {
            "acompanhamento_rps": acc,
            "controle_rps": ctl,
            "reconciliacao_rps": rec,
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
    )
