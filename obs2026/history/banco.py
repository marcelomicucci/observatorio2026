"""Banco histórico: as eleições passadas, prontas para comparar.

Separado do banco ao vivo de propósito. O de 2026 é escrito pelo coletor a
cada segundo durante a noite e representa uma apuração em andamento; este
aqui é escrito uma vez, por importação, e representa resultado final. Juntar
os dois no mesmo arquivo misturaria "parcial" com "definitivo" na mesma
consulta — o erro que o §9 chama de ausência virando zero.

O que entra, por ano e turno:

- **totais por unidade** (Brasil, UF e município): eleitorado apto,
  comparecimento, abstenção, brancos, nulos, válidos e votos a concorrentes.
  É o que sustenta a comparação de participação ano a ano;
- **votos por candidato** nas mesmas três abrangências, com partido e
  situação. Para presidente, governador e senador, até o município; para
  deputado federal, só no nível da UF e só quem se elegeu — a bancada é o
  que se compara, e a granularidade municipal do proporcional custaria
  milhões de linhas sem uso.

Município é string com zeros à esquerda, como no resto do sistema. Nada aqui
converte para int.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import (BigInteger, Boolean, Index, Integer, String, Text,
                        UniqueConstraint, create_engine, event)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from ..config import DATA_DIR

#: Um arquivo só, fora dos ambientes: o histórico não é "de 2026" nem de
#: demonstração — é o passado, igual para todos.
CAMINHO = DATA_DIR / "db" / "historico.sqlite"


class Base(DeclarativeBase):
    pass


class HistTotal(Base):
    """Totais de uma unidade numa eleição — o denominador de tudo."""

    __tablename__ = "hist_total"
    id: Mapped[int] = mapped_column(primary_key=True)
    ano: Mapped[int] = mapped_column(Integer, index=True)
    turno: Mapped[str] = mapped_column(String(2))
    cargo: Mapped[str] = mapped_column(String(2))
    #: br | uf | mu
    tipo: Mapped[str] = mapped_column(String(2))
    uf: Mapped[str] = mapped_column(String(2), index=True)
    cd_municipio: Mapped[str] = mapped_column(String(8), default="")
    nome: Mapped[str] = mapped_column(Text, default="")
    aptos: Mapped[int] = mapped_column(BigInteger, default=0)
    comparecimento: Mapped[int] = mapped_column(BigInteger, default=0)
    abstencoes: Mapped[int] = mapped_column(BigInteger, default=0)
    total_votos: Mapped[int] = mapped_column(BigInteger, default=0)
    concorrentes: Mapped[int] = mapped_column(BigInteger, default=0)
    validos: Mapped[int] = mapped_column(BigInteger, default=0)
    anulados: Mapped[int] = mapped_column(BigInteger, default=0)
    anulados_sub_judice: Mapped[int] = mapped_column(BigInteger, default=0)
    brancos: Mapped[int] = mapped_column(BigInteger, default=0)
    nulos: Mapped[int] = mapped_column(BigInteger, default=0)
    secoes_total: Mapped[int] = mapped_column(Integer, default=0)

    __table_args__ = (
        UniqueConstraint("ano", "turno", "cargo", "tipo", "uf",
                         "cd_municipio", name="uq_hist_total"),
        Index("ix_hist_total_consulta", "ano", "cargo", "tipo", "uf"),
    )


class HistVoto(Base):
    """Votos de um candidato numa unidade."""

    __tablename__ = "hist_voto"
    id: Mapped[int] = mapped_column(primary_key=True)
    ano: Mapped[int] = mapped_column(Integer, index=True)
    turno: Mapped[str] = mapped_column(String(2))
    cargo: Mapped[str] = mapped_column(String(2))
    tipo: Mapped[str] = mapped_column(String(2))
    uf: Mapped[str] = mapped_column(String(2), index=True)
    cd_municipio: Mapped[str] = mapped_column(String(8), default="")
    sqcand: Mapped[str] = mapped_column(String(24), index=True)
    numero: Mapped[str] = mapped_column(String(8), default="")
    nome_urna: Mapped[str] = mapped_column(Text, default="")
    partido_numero: Mapped[str] = mapped_column(String(8), default="")
    partido_sigla: Mapped[str] = mapped_column(String(32), default="")
    votos: Mapped[int] = mapped_column(BigInteger, default=0)
    #: `DS_SIT_TOT_TURNO` — status da disputa inteira, replicado em cada
    #: linha pelo TSE. Aqui fica só no nível que decide (br/uf).
    situacao: Mapped[str] = mapped_column(String(48), default="")
    eleito: Mapped[bool] = mapped_column(Boolean, default=False)
    #: Destinação do voto, como no EA20: válido, anulado, sub judice.
    destinacao: Mapped[str] = mapped_column(String(32), default="")

    __table_args__ = (
        UniqueConstraint("ano", "turno", "cargo", "tipo", "uf",
                         "cd_municipio", "sqcand", name="uq_hist_voto"),
        Index("ix_hist_voto_consulta", "ano", "cargo", "tipo", "uf"),
    )


def abrir(caminho: Path | None = None, *, criar: bool = True):
    """Devolve (engine, Session) do banco histórico.

    WAL como no banco ao vivo: sem ele, uma importação em andamento tranca
    qualquer leitura, e o painel cai enquanto se importa um ano novo. O
    `busy_timeout` cobre a janela em que o escritor troca de transação.
    """
    p = caminho or CAMINHO
    p.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{p}", future=True)

    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_con, _registro):          # noqa: ANN001
        cur = dbapi_con.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=10000")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.close()

    if criar:
        Base.metadata.create_all(engine)
    return engine, sessionmaker(engine, expire_on_commit=False, future=True)


def anos_disponiveis(session) -> list[int]:
    """Anos já importados, do mais novo para o mais antigo."""
    from sqlalchemy import select
    return [a for (a,) in session.execute(
        select(HistTotal.ano).distinct().order_by(HistTotal.ano.desc()))]
