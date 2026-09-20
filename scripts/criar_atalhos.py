"""Cria os atalhos da pasta: painel, telao e o script que sobe tudo.

Os atalhos ficam em `Desktop\\eleicao2026\\`, ao lado dos arquivos historicos,
nao dentro do repositorio: e a pasta que a pessoa abre. Sao gerados por
script, e nao versionados como arquivos prontos, porque um `.url` guarda um
endereco absoluto — colocar isso no repositorio seria versionar a maquina de
alguem.

Rode uma vez:  python scripts/criar_atalhos.py
"""

from __future__ import annotations

import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
PASTA = RAIZ.parent
PORTA = 8026

ATALHOS = {
    "Observatório 2026 — painel.url": f"http://localhost:{PORTA}/",
    "Observatório 2026 — telão.url": f"http://localhost:{PORTA}/telao",
}

# O .cmd sai sem acentos de proposito: o interpretador de lote roda na
# codepage do console, e um acento no lugar errado vira erro de sintaxe numa
# noite em que ninguem vai querer depurar arquivo .cmd.
INICIAR = f"""@echo off
title Observatorio Eleitoral 2026
cd /d "%~dp0{RAIZ.name}"

echo Subindo o painel em http://localhost:{PORTA}
echo Feche esta janela (ou Ctrl+C) para desligar o servidor.
echo.

start "Observatorio 2026 - servidor" cmd /k python -m uvicorn obs2026.api.app:app --port {PORTA}

rem Sem esta espera o navegador abre antes do servidor atender e mostra
rem erro de conexao; o operador acha que quebrou e reinicia tudo.
timeout /t 5 /nobreak >nul

start "" http://localhost:{PORTA}/
"""


def main() -> int:
    if not PASTA.exists():
        print(f"pasta nao encontrada: {PASTA}")
        return 1
    feitos = []
    for nome, url in ATALHOS.items():
        alvo = PASTA / nome
        alvo.write_text(
            f"[InternetShortcut]\nURL={url}\nIconIndex=0\n",
            encoding="utf-8")
        feitos.append(alvo)

    alvo = PASTA / "Iniciar Observatório 2026.cmd"
    alvo.write_text(INICIAR, encoding="utf-8")
    feitos.append(alvo)

    for f in feitos:
        print(f"criado: {f}")
    print()
    print("Os dois .url so funcionam com o servidor no ar. O .cmd sobe o "
          "servidor e abre o painel sozinho.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
