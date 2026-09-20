"""Conexão e sessão.

SQLite para desenvolvimento e simulados; PostgreSQL para produção. A escolha
é da URL, e o schema usa só tipos portáveis entre os dois.

O banco é **derivado**: reconstruível inteiro a partir de `data/raw`. É por
isso que trocar de motor não é um evento arriscado, e por isso que o comando
`recriar` existe sem cerimônia.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from .models import Base


def make_engine(url: str | None = None, *, sqlite_path: Path | None = None,
                echo: bool = False):
    if url is None:
        if sqlite_path is None:
            raise ValueError("informe url ou sqlite_path")
        sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        url = f"sqlite+pysqlite:///{sqlite_path}"

    eng = create_engine(url, echo=echo, future=True)

    if url.startswith("sqlite"):
        @event.listens_for(eng, "connect")
        def _pragmas(dbapi_conn, _rec):
            cur = dbapi_conn.cursor()
            # WAL: leitura do painel não bloqueia a escrita do coletor.
            cur.execute("PRAGMA journal_mode=WAL")
            # NORMAL basta: o dado durável é o bruto em disco, e uma perda de
            # transação na queda de energia é reconstruível a partir dele.
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA busy_timeout=10000")
            cur.close()

    return eng


def make_session_factory(engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False, future=True)


def create_all(engine) -> None:
    Base.metadata.create_all(engine)


def drop_all(engine) -> None:
    Base.metadata.drop_all(engine)
