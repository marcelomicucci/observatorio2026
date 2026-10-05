"""Configuração comum dos testes.

O principal: nenhum teste escreve no `config/telao.yaml` de produção. Em
23/09/2026 uma bateria rodando junto com o painel deixou o roteiro do telão
com três telas a menos — inclusive a de "onde ainda falta apurar". Roteiro é
operação: se sumir uma tela na noite, some do projetor.

Em 24/09 escapou de novo, por outro caminho: um arquivo de teste importava o
app no topo do módulo, a importação acontece na COLETA, e a fixture que
apontava para a cópia só rodava no primeiro teste — tarde demais para um
caminho lido na importação. Agora:

- a cópia é apontada em `pytest_configure`, antes de qualquer coleta;
- o arquivo de produção é lido no início e comparado no fim: se mudou, volta
  byte a byte e a sessão avisa em voz alta.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

_ESTADO: dict = {}


def _producao() -> Path:
    from obs2026.config import CONFIG_DIR
    return Path(CONFIG_DIR) / "telao.yaml"


def pytest_configure(config):              # noqa: ARG001
    """Aponta o roteiro para uma cópia temporária antes da coleta."""
    origem = _producao()
    pasta = Path(tempfile.mkdtemp(prefix="obs2026-telao-"))
    destino = pasta / "telao.yaml"
    if origem.exists():
        shutil.copy2(origem, destino)
    _ESTADO.update(
        pasta=pasta, anterior_env=os.environ.get("OBS2026_TELAO"),
        original=origem.read_bytes() if origem.exists() else None)
    os.environ["OBS2026_TELAO"] = str(destino)


def pytest_unconfigure(config):            # noqa: ARG001
    anterior = _ESTADO.get("anterior_env")
    if anterior is None:
        os.environ.pop("OBS2026_TELAO", None)
    else:
        os.environ["OBS2026_TELAO"] = anterior
    # Rede de segurança: se algo escapou e mexeu no roteiro de produção,
    # desfaz — e diz, para a causa ser procurada.
    original = _ESTADO.get("original")
    origem = _producao()
    if original is not None and origem.exists() \
            and origem.read_bytes() != original:
        origem.write_bytes(original)
        print("\n\n*** ATENÇÃO: um teste gravou em config/telao.yaml de "
              "produção. O arquivo foi restaurado; procure o teste. ***\n")
    shutil.rmtree(_ESTADO.get("pasta", ""), ignore_errors=True)
