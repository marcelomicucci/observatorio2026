"""Modelo de dados (§9).

Escolhas que a especificação exige e que aparecem no schema:

- **Ambiente e turno entram na unicidade.** Sem isso, um payload de simulado
  e um oficial colidem na mesma linha. O §11.1 pede explicitamente "nenhum
  payload de simulado no oficial", e a forma de garantir isso é estrutural,
  não por disciplina de quem escreve a query.
- **Votos em inteiro de 64 bits.** Nunca float: 138 milhões de votos em
  float64 ainda é exato, mas a primeira média que alguém gravar deixa de ser.
- **Código TSE é string.** `01120` e `1120` são coisas diferentes, e a
  segunda gera 404.
- **UTC internamente**, exibição em America/Sao_Paulo. O horário original do
  TSE fica preservado no bruto e em `source_generated_at`.
- **Ausência é NULL com motivo, nunca zero** (§9). Daí `quality_flags` em
  quase toda tabela de fato.

O banco é derivado: pode ser reconstruído inteiro a partir de `data/raw`.
Por isso o schema pode evoluir sem medo de perder observação.

SQLite é o alvo de desenvolvimento e dos simulados — roda sem infraestrutura
na máquina do operador, que é o que importa a 2 dias do teste. PostgreSQL é o
alvo de produção (§8); o schema usa apenas tipos portáveis entre os dois, e a
escolha é feita pela URL de conexão.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (BigInteger, Boolean, DateTime, Float, ForeignKey,
                        Index, Integer, String, Text, UniqueConstraint)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Election(Base):
    """Ambiente, ciclo, pleito, eleição, turno."""

    __tablename__ = "election"
    id: Mapped[int] = mapped_column(primary_key=True)
    environment: Mapped[str] = mapped_column(String(32), index=True)
    official: Mapped[bool] = mapped_column(Boolean)
    ciclo: Mapped[str] = mapped_column(String(16))
    cd_pleito: Mapped[str] = mapped_column(String(16))
    cd_eleicao: Mapped[str] = mapped_column(String(16))
    cd_eleicao_turno2: Mapped[str | None] = mapped_column(String(16))
    turno: Mapped[str] = mapped_column(String(2))
    nome: Mapped[str] = mapped_column(Text)
    data: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        # Ambiente + eleição + turno: a barreira contra contaminação.
        UniqueConstraint("environment", "cd_eleicao", "turno",
                         name="uq_election_env"),
    )


class Contest(Base):
    """Disputa: eleição x cargo, com a abrangência que de fato decide."""

    __tablename__ = "contest"
    id: Mapped[int] = mapped_column(primary_key=True)
    election_id: Mapped[int] = mapped_column(ForeignKey("election.id"))
    cargo_cd: Mapped[str] = mapped_column(String(8))
    cargo_nome: Mapped[str] = mapped_column(Text)
    #: 'br' para Presidente, 'uf' para Governador e Senador. O município é
    #: recorte do voto, nunca a unidade que elege (§3).
    abrangencia_decisoria: Mapped[str] = mapped_column(String(4))
    #: Lido de `carg.nv` no EA20. 2 para Senador em 2026.
    vagas: Mapped[int] = mapped_column(Integer)
    inclui_exterior: Mapped[bool] = mapped_column(Boolean, default=False)

    __table_args__ = (
        UniqueConstraint("election_id", "cargo_cd", name="uq_contest"),
    )


class Geography(Base):
    """Território. Código TSE é string; IBGE vem do EA12 (`cdi`)."""

    __tablename__ = "geography"
    id: Mapped[int] = mapped_column(primary_key=True)
    environment: Mapped[str] = mapped_column(String(32), index=True)
    tipo: Mapped[str] = mapped_column(String(8))          # br | uf | mu | zn
    cd_tse: Mapped[str] = mapped_column(String(16))
    cd_ibge: Mapped[str | None] = mapped_column(String(8), index=True)
    uf: Mapped[str] = mapped_column(String(2), index=True)
    nome: Mapped[str] = mapped_column(Text)
    nome_fold: Mapped[str] = mapped_column(Text, index=True)
    capital: Mapped[bool] = mapped_column(Boolean, default=False)
    exterior: Mapped[bool] = mapped_column(Boolean, default=False)
    valido_de: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    valido_ate: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("environment", "tipo", "uf", "cd_tse",
                         name="uq_geography"),
    )


class GeoCrosswalk(Base):
    """Correspondência entre catálogos, com regra, versão e qualidade.

    Em 2026 a correspondência TSE->IBGE é OFICIAL: vem em `EA12.mu.cdi`. A
    regra fica registrada mesmo assim, porque a tabela também guarda
    correspondências entre anos, onde território muda.
    """

    __tablename__ = "geo_crosswalk"
    id: Mapped[int] = mapped_column(primary_key=True)
    origem_sistema: Mapped[str] = mapped_column(String(16))
    origem_codigo: Mapped[str] = mapped_column(String(16))
    destino_sistema: Mapped[str] = mapped_column(String(16))
    destino_codigo: Mapped[str] = mapped_column(String(16))
    regra: Mapped[str] = mapped_column(Text)
    versao: Mapped[str] = mapped_column(String(32))
    #: 'oficial' quando vem do próprio TSE; 'revisada' quando um humano
    #: decidiu. Associação aproximada silenciosa é proibida (§2.1), logo não
    #: existe qualidade 'automatica_aproximada'.
    qualidade: Mapped[str] = mapped_column(String(24))

    __table_args__ = (
        UniqueConstraint("origem_sistema", "origem_codigo", "destino_sistema",
                         "versao", name="uq_crosswalk"),
    )


class Electorate(Base):
    """Aptos e seções por geografia e eleição, com fonte e data."""

    __tablename__ = "electorate"
    id: Mapped[int] = mapped_column(primary_key=True)
    geography_id: Mapped[int] = mapped_column(ForeignKey("geography.id"))
    election_id: Mapped[int] = mapped_column(ForeignKey("election.id"))
    aptos: Mapped[int] = mapped_column(BigInteger)
    secoes: Mapped[int | None] = mapped_column(Integer)
    zonas: Mapped[int | None] = mapped_column(Integer)
    fonte: Mapped[str] = mapped_column(String(48))
    fonte_hash: Mapped[str | None] = mapped_column(String(64))
    carga_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: Linha que não conciliou fica aqui com o peso visível (§2.1), em vez de
    #: sumir da conta.
    quarentena: Mapped[bool] = mapped_column(Boolean, default=False)
    quarentena_motivo: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint("geography_id", "election_id", "fonte",
                         name="uq_electorate"),
    )


class Candidate(Base):
    __tablename__ = "candidate"
    id: Mapped[int] = mapped_column(primary_key=True)
    contest_id: Mapped[int] = mapped_column(ForeignKey("contest.id"))
    sqcand: Mapped[str] = mapped_column(String(24), index=True)
    numero: Mapped[str] = mapped_column(String(8))         # string, sempre
    nome: Mapped[str] = mapped_column(Text)
    nome_urna: Mapped[str] = mapped_column(Text)
    partido_numero: Mapped[str] = mapped_column(String(8))
    partido_sigla: Mapped[str] = mapped_column(String(32))
    agr_numero: Mapped[str | None] = mapped_column(String(16))
    agr_nome: Mapped[str | None] = mapped_column(Text)
    agr_tipo: Mapped[str | None] = mapped_column(String(4))
    agr_composicao: Mapped[str | None] = mapped_column(Text)
    #: Destinação do voto: 'Válido', 'Anulado', 'Anulado sub judice'.
    #: Preservada como veio; nunca normalizada em 'válido/inválido'.
    dvt: Mapped[str] = mapped_column(String(32))
    situacao: Mapped[str | None] = mapped_column(String(32))
    eleito: Mapped[bool] = mapped_column(Boolean, default=False)

    __table_args__ = (
        UniqueConstraint("contest_id", "sqcand", name="uq_candidate"),
    )


class PartyClassification(Base):
    """Classificação ideológica versionada (§6).

    Três dimensões separadas de propósito: ideologia, posição em relação ao
    governo e aliança estadual. Uma não determina a outra, e misturá-las numa
    coluna só torna impossível desfazer a confusão depois.
    """

    __tablename__ = "party_classification"
    id: Mapped[int] = mapped_column(primary_key=True)
    ano: Mapped[int] = mapped_column(Integer, index=True)
    entidade_tipo: Mapped[str] = mapped_column(String(16))   # partido|candidato
    entidade_id: Mapped[str] = mapped_column(String(32))
    #: esquerda | centro_esquerda | centro | centro_direita | direita
    #: Não existe 'cinza'/'indefinido' aqui: ausência de classificação é
    #: ausência de linha, com padrão visual próprio (§6).
    ideologia: Mapped[str] = mapped_column(String(24))
    #: governo | oposicao | independente — dimensão SEPARADA da ideologia.
    posicao_governo: Mapped[str | None] = mapped_column(String(16))
    fonte: Mapped[str] = mapped_column(Text)
    justificativa: Mapped[str] = mapped_column(Text)
    autor: Mapped[str] = mapped_column(String(64))
    decidido_em: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    valido_de: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    valido_ate: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: 0 = consensual, 3 = fortemente disputada. Aparece na interface.
    grau_controversia: Mapped[int] = mapped_column(Integer, default=0)
    versao: Mapped[str] = mapped_column(String(32), index=True)

    __table_args__ = (
        UniqueConstraint("ano", "entidade_tipo", "entidade_id", "versao",
                         name="uq_classification"),
    )


class SourceSnapshot(Base):
    """Uma captura. Espelha o manifesto do arquivo bruto."""

    __tablename__ = "source_snapshot"
    id: Mapped[int] = mapped_column(primary_key=True)
    environment: Mapped[str] = mapped_column(String(32), index=True)
    kind: Mapped[str] = mapped_column(String(8))
    source_key: Mapped[str] = mapped_column(String(128), index=True)
    url: Mapped[str] = mapped_column(Text)
    status: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str | None] = mapped_column(String(64), index=True)
    bytes_len: Mapped[int] = mapped_column(Integer, default=0)
    #: Quando o TSE gerou (dg+hg). Pode ser MUITO anterior à captura.
    source_generated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True)
    #: Quando nós recebemos. Latência da fonte = diferença dos dois (§10).
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                  index=True)
    #: Registrado como evidência. NÃO é contador global crescente (§8.1).
    idg: Mapped[str | None] = mapped_column(String(32))
    etag: Mapped[str | None] = mapped_column(String(128))
    parser_version: Mapped[str] = mapped_column(String(8))
    blob_path: Mapped[str | None] = mapped_column(Text)


class ObservedResult(Base):
    """Resultado oficial observado, por snapshot.

    Guarda o votado E a destinação. Nunca guarda percentual calculado: o
    percentual depende do denominador escolhido e é derivado na leitura, com
    a base declarada (§9).
    """

    __tablename__ = "observed_result"
    id: Mapped[int] = mapped_column(primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("source_snapshot.id"),
                                             index=True)
    contest_id: Mapped[int] = mapped_column(ForeignKey("contest.id"),
                                            index=True)
    geography_id: Mapped[int] = mapped_column(ForeignKey("geography.id"),
                                              index=True)
    candidate_id: Mapped[int | None] = mapped_column(ForeignKey("candidate.id"))
    votos: Mapped[int] = mapped_column(BigInteger)
    dvt: Mapped[str] = mapped_column(String(32))
    #: revisão = esta versão reduziu votos em relação à anterior (§8.1).
    #: Ambas ficam.
    revisao: Mapped[bool] = mapped_column(Boolean, default=False)

    __table_args__ = (
        Index("ix_observed_lookup", "contest_id", "geography_id",
              "snapshot_id"),
    )


class UnitTotals(Base):
    """Agregados por unidade e snapshot: o bloco `s`/`e`/`v` do EA20.

    Fica separado de `observed_result` porque é a base de todo denominador e
    de `A_pendente`, e porque precisa ser consultado sem varrer candidatos.
    """

    __tablename__ = "unit_totals"
    id: Mapped[int] = mapped_column(primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("source_snapshot.id"),
                                             index=True)
    contest_id: Mapped[int] = mapped_column(ForeignKey("contest.id"))
    geography_id: Mapped[int] = mapped_column(ForeignKey("geography.id"))
    andamento: Mapped[str] = mapped_column(String(2))
    totalizado_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True))
    # seções
    secoes_total: Mapped[int] = mapped_column(Integer)
    secoes_totalizadas: Mapped[int] = mapped_column(Integer)
    secoes_instaladas: Mapped[int] = mapped_column(Integer)
    secoes_apuradas: Mapped[int] = mapped_column(Integer)
    # eleitorado
    eleitorado_total: Mapped[int] = mapped_column(BigInteger)
    eleitorado_totalizado: Mapped[int] = mapped_column(BigInteger)
    eleitorado_nao_totalizado: Mapped[int] = mapped_column(BigInteger)
    eleitorado_nao_instalado: Mapped[int] = mapped_column(BigInteger)
    eleitorado_apurado: Mapped[int] = mapped_column(BigInteger)
    comparecimento: Mapped[int] = mapped_column(BigInteger)
    abstencao: Mapped[int] = mapped_column(BigInteger)
    # votos
    total_votos: Mapped[int] = mapped_column(BigInteger)
    validos: Mapped[int] = mapped_column(BigInteger)
    validos_computados: Mapped[int] = mapped_column(BigInteger)
    #: Σ vap — o denominador que o TSE usa em `pvap`. Gravado porque não é
    #: reconstruível a partir dos outros campos (há anulados sem candidato).
    nominal_tse: Mapped[int] = mapped_column(BigInteger)
    anulados: Mapped[int] = mapped_column(BigInteger)
    anulados_sub_judice: Mapped[int] = mapped_column(BigInteger)
    brancos: Mapped[int] = mapped_column(BigInteger)
    nulos: Mapped[int] = mapped_column(BigInteger)
    # pendente derivado (§4.2), como intervalo
    pendente_min: Mapped[int] = mapped_column(BigInteger)
    pendente_max: Mapped[int] = mapped_column(BigInteger)
    quality_flags: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint("snapshot_id", "contest_id", "geography_id",
                         name="uq_unit_totals"),
    )


class ForecastRun(Base):
    """Uma execução do motor. Reprodutível por manifesto + código + semente."""

    __tablename__ = "forecast_run"
    id: Mapped[int] = mapped_column(primary_key=True)
    environment: Mapped[str] = mapped_column(String(32), index=True)
    contest_id: Mapped[int] = mapped_column(ForeignKey("contest.id"))
    #: Conjunto de snapshots que esta execução leu. Sem isso não há replay.
    snapshot_set_id: Mapped[str] = mapped_column(String(64), index=True)
    modelo: Mapped[str] = mapped_column(String(16))          # m0 | m1 | m2
    model_version: Mapped[str] = mapped_column(String(32))
    parametros_json: Mapped[str] = mapped_column(Text)
    semente: Mapped[int] = mapped_column(BigInteger)
    n_sims: Mapped[int] = mapped_column(Integer)
    #: Hash de config/thresholds.yaml no momento da execução. Sem igualdade,
    #: o motor recusa publicar probabilidade (§4.7).
    thresholds_hash: Mapped[str] = mapped_column(String(64))
    corte_em: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    fonte_mais_antiga: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True))
    fonte_mais_recente: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True))
    unidades_ausentes: Mapped[int] = mapped_column(Integer, default=0)
    eleitorado_atrasado: Mapped[int] = mapped_column(BigInteger, default=0)
    #: aguardando | cenario_exploratorio | projecao_disponivel |
    #: projecao_suspensa | resultado_oficial
    estado: Mapped[str] = mapped_column(String(32), index=True)
    estado_motivo: Mapped[str | None] = mapped_column(Text)
    erro_monte_carlo_pp: Mapped[float | None] = mapped_column(Float)
    diagnosticos_json: Mapped[str | None] = mapped_column(Text)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ForecastResult(Base):
    """Quantis e probabilidades por território e candidato."""

    __tablename__ = "forecast_result"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("forecast_run.id"),
                                        index=True)
    geography_id: Mapped[int] = mapped_column(ForeignKey("geography.id"))
    candidate_id: Mapped[int] = mapped_column(ForeignKey("candidate.id"))
    votos_q025: Mapped[int] = mapped_column(BigInteger)
    votos_q100: Mapped[int] = mapped_column(BigInteger)
    votos_q500: Mapped[int] = mapped_column(BigInteger)
    votos_q900: Mapped[int] = mapped_column(BigInteger)
    votos_q975: Mapped[int] = mapped_column(BigInteger)
    pct_validos_q100: Mapped[float] = mapped_column(Float)
    pct_validos_q500: Mapped[float] = mapped_column(Float)
    pct_validos_q900: Mapped[float] = mapped_column(Float)
    prob_lideranca: Mapped[float | None] = mapped_column(Float)
    prob_dentro_vagas: Mapped[float | None] = mapped_column(Float)
    prob_maioria_absoluta: Mapped[float | None] = mapped_column(Float)

    __table_args__ = (
        UniqueConstraint("run_id", "geography_id", "candidate_id",
                         name="uq_forecast_result"),
    )


class InstitutionalSeat(Base):
    """Composição institucional: 54 vagas em renovação + 27 remanescentes."""

    __tablename__ = "institutional_seat"
    id: Mapped[int] = mapped_column(primary_key=True)
    casa: Mapped[str] = mapped_column(String(16))          # senado | camara
    uf: Mapped[str] = mapped_column(String(2), index=True)
    mandato_inicio: Mapped[int] = mapped_column(Integer)
    mandato_fim: Mapped[int] = mapped_column(Integer)
    #: True quando a cadeira está em disputa em 2026.
    em_renovacao: Mapped[bool] = mapped_column(Boolean, default=False)
    ocupante: Mapped[str | None] = mapped_column(Text)
    partido: Mapped[str | None] = mapped_column(String(32))
    fonte: Mapped[str] = mapped_column(Text)
    referencia_em: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    substituicao_nota: Mapped[str | None] = mapped_column(Text)


class CargaManifesto(Base):
    """Até onde cada dia do manifesto já foi carregado no banco.

    Existe para a carga ser incremental: sem isto, cada `carregar` relia o
    arquivo bruto inteiro — todos os EA20 capturados desde o começo — e o
    tempo de carga crescia a noite toda, justamente quando mais importa.

    Mora no banco, não num arquivo ao lado, e é atualizada na mesma
    transação que os dados. Assim as duas coisas não descasam: recriar o
    banco apaga o ponto de retomada junto, e uma queda no meio da carga não
    deixa o ponto adiante (pulando dados) nem atrás dos dados gravados.
    """

    __tablename__ = "carga_manifesto"
    dia: Mapped[str] = mapped_column(String(10), primary_key=True)
    deslocamento: Mapped[int] = mapped_column(BigInteger, default=0)
    atualizado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AuditEvent(Base):
    """Auditoria de toda mudança de premissa ou classificação (§10)."""

    __tablename__ = "audit_event"
    id: Mapped[int] = mapped_column(primary_key=True)
    operador: Mapped[str] = mapped_column(String(64))
    acao: Mapped[str] = mapped_column(String(64), index=True)
    entidade: Mapped[str] = mapped_column(String(64))
    entidade_id: Mapped[str] = mapped_column(String(64))
    justificativa: Mapped[str] = mapped_column(Text)
    antes_json: Mapped[str | None] = mapped_column(Text)
    depois_json: Mapped[str | None] = mapped_column(Text)
    em: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


#: Estados do produto (§4.7). Ordem é a progressão esperada.
ESTADOS = (
    "aguardando",
    "cenario_exploratorio",
    "projecao_disponivel",
    "projecao_suspensa",
    "resultado_oficial",
)
