"""Chaves de operação: o que se muda com o sistema rodando.

`config/operacao.yaml` guarda as escolhas que o operador faz durante a
noite pela aba Configuração do painel — qual modelo de projeção está em
produção, se o coletor pula o que o TSE já finalizou, se busca deputados.
Tudo que lê este arquivo o relê pela data de modificação, então mudar uma
chave vale na próxima passada, sem reiniciar nada.

Por que um arquivo e não o banco: o coletor e o servidor são processos
diferentes e o banco é derivado (reconstruível a partir do bruto); uma
escolha do operador não é derivada de nada. E por que não
`collector.yaml`: aquele é a cadência revisada com calma, protegida pela
trava de orçamento; este é o painel de bordo.

## Comandos

"Forçar atualização" é um pedido ao coletor, que roda em outro processo. O
painel grava o pedido em `data/comandos/<ambiente>.json` com um número
crescente; o coletor confere o arquivo a cada volta do laço, executa o que
ainda não executou e grava em `<ambiente>-estado.json` a hora da última
volta e o último comando atendido. É esse estado que o painel mostra — o
botão não diz "feito" antes de o coletor dizer.
"""

from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from .config import CONFIG_DIR, DATA_DIR

BRT = ZoneInfo("America/Sao_Paulo")
ARQUIVO = CONFIG_DIR / "operacao.yaml"
PASTA_COMANDOS = DATA_DIR / "comandos"

#: Modelos de projeção disponíveis, na ordem em que aparecem no painel. Um
#: modelo novo entra aqui e em `engine/runner.py` (VERSOES e o `build`).
MODELOS = {
    "m2": {
        "nome": "M2 — hierárquico com grupo da capital",
        "descricao": (
            "Município → porte → grupo (capital + arranjo populacional do "
            "IBGE) → UF, com incerteza larga para grupo sem dado. Calibrado "
            "em 2018 e testado em 2022: cobertura do intervalo de 80% de "
            "77–82%; no presidente de 2022, a maior chance dada ao perdedor "
            "caiu de 91% (M1) para 56%. No gate completo de 22/09 perdeu "
            "para o M0 em 1 de 18 rodadas."),
        "recomendado": False,
    },
    "m3": {
        "nome": "M3 — M2 + inclinação histórica de cada município",
        "descricao": (
            "A hierarquia e a incerteza do M2, com a estimativa de cada "
            "município pendente corrigida pelo voto dele na eleição anterior "
            "(fatia do PT no 2º turno presidencial de 2022). Ataca o viés de "
            "ordem de chegada: se o primeiro a chegar pende para um lado, o "
            "modelo sabe que o que falta pende para o outro. Passou no gate "
            "de 2022 (22/09): vence o M0 nas 18 rodadas, cobertura 81%."),
        "recomendado": True,
    },
    "m33": {
        "nome": "M3.3 — M3 com a faixa 20% mais estreita",
        "descricao": (
            "O número central do M3, com os choques de preferência do "
            "simulador × 0,8. Na noite real do 1º turno de 2026 a faixa de "
            "80% do M3 conteve o resultado em 96–100% dos casos: larga "
            "demais. Com 0,8 ficou em 92–100% em 2026, 76% em 2022 e "
            "74–76% em 2018 — o maior estreitamento que passa nas três. "
            "Faixas menores decidem as probabilidades mais cedo. A rever "
            "depois do 2º turno."),
        "recomendado": False,
    },
    "m31": {
        "nome": "M3.1 — M3 + resposta à inclinação por bloco (em teste)",
        "descricao": (
            "O M3 com o slope de cada candidato freado pela própria "
            "incerteza e puxado para a média do bloco ideológico dele "
            "(medida em 2018), em vez de zero. Mira o candidato regional que "
            "não segue o eixo presidencial. Em teste: só entra em produção "
            "se passar no gate."),
        "recomendado": False,
    },
    "m32": {
        "nome": "M3.2 — M3 + calibrador dinâmico da incerteza (em teste)",
        "descricao": (
            "O número central do M3, com a largura do intervalo medida na "
            "noite: cada município já contado é previsto como se estivesse "
            "pendente e comparado com o que deu. Estado heterogêneo alarga o "
            "intervalo; eleição bem explicada estreita. No ensaio de 2018 "
            "(22/09) ficou neutro — mesma cobertura do M3, sem reduzir a "
            "diferença entre UFs. Não recomendado."),
        "recomendado": False,
    },
    "m1": {
        "nome": "M1 — município e porte",
        "descricao": (
            "Modelo anterior. No ensaio de 2022 subestimou a incerteza "
            "(cobertura de 54–59% num intervalo nominal de 80%) e demorou a "
            "virar para Lula na ordem real de chegada. Fica para comparação "
            "e para voltar atrás."),
        "recomendado": False,
    },
    "m0": {
        "nome": "M0 — referência ingênua",
        "descricao": (
            "Linha de base do gate estatístico (§11.2). Não é modelo de "
            "produção: existe para os outros provarem que são melhores."),
        "recomendado": False,
    },
}

PADRAO = {
    "modelo_projecao": "m2",
    "coleta": {
        "pular_finalizados": True,
        "deputados_federais": True,
        "encerrar_ao_finalizar": True,
    },
}

CABECALHO = """\
# Chaves de operação — editadas pela aba Configuração do painel.
#
# Relido pela data de modificação: mudar aqui vale na próxima passada do
# coletor e do servidor, sem reiniciar. A versão anterior de cada gravação
# fica em config/operacao.anterior/.
#
#   modelo_projecao            m2 | m3 | m33 | m31 | m32 | m1 | m0
#                              (m0 só referência)
#   coleta.pular_finalizados   não busca de novo o que o TSE finalizou;
#                              segue vigiando pelo EA14/EA15
#   coleta.deputados_federais  busca os 27 arquivos de UF de deputado
#                              federal (só resultado, sem projeção)
#   coleta.encerrar_ao_finalizar  o coletor se desliga sozinho 10 min depois
#                              de o TSE encerrar Brasil e todas as UFs
"""

_memo: dict = {"mtime": None, "dados": None}


def _mesclar(base: dict, novo: dict) -> dict:
    out = dict(base)
    for k, v in (novo or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _mesclar(out[k], v)
        else:
            out[k] = v
    return out


def carregar(caminho: Path | None = None) -> dict:
    """Chaves vigentes, com o padrão por baixo. Nunca levanta: arquivo
    ilegível vale como padrão, e o painel mostra o problema."""
    p = caminho or ARQUIVO
    try:
        mt = p.stat().st_mtime
    except OSError:
        return json.loads(json.dumps(PADRAO))
    if caminho is None and _memo["mtime"] == mt:
        return _memo["dados"]
    try:
        d = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        d = {}
    dados = _mesclar(PADRAO, d)
    if dados.get("modelo_projecao") not in MODELOS:
        dados["modelo_projecao"] = PADRAO["modelo_projecao"]
    if caminho is None:
        _memo.update(mtime=mt, dados=dados)
    return dados


def modelo_ativo() -> str:
    return carregar()["modelo_projecao"]


def validar(d: dict) -> list[str]:
    erros = []
    if d.get("modelo_projecao") not in MODELOS:
        erros.append(f"modelo {d.get('modelo_projecao')!r} desconhecido; "
                     f"use um de {list(MODELOS)}")
    c = d.get("coleta") or {}
    for k in ("pular_finalizados", "deputados_federais",
              "encerrar_ao_finalizar"):
        if k in c and not isinstance(c[k], bool):
            erros.append(f"coleta.{k} deve ser verdadeiro ou falso")
    return erros


def gravar(novo: dict, caminho: Path | None = None) -> dict:
    """Grava as chaves (só as conhecidas), guardando a versão anterior."""
    p = caminho or ARQUIVO
    atual = carregar(p if caminho else None)
    d = _mesclar(atual, {
        "modelo_projecao": novo.get("modelo_projecao",
                                    atual["modelo_projecao"]),
        "coleta": {k: v for k, v in (novo.get("coleta") or {}).items()
                   if k in PADRAO["coleta"]},
    })
    erros = validar(d)
    if erros:
        raise ValueError("; ".join(erros))
    agora = datetime.now(BRT)
    if p.exists():
        pasta = p.parent / "operacao.anterior"
        pasta.mkdir(exist_ok=True)
        shutil.copy2(p, pasta / f"operacao-{agora:%Y%m%d-%H%M%S}.yaml")
        for velha in sorted(pasta.glob("operacao-*.yaml"))[:-30]:
            velha.unlink(missing_ok=True)
    texto = (CABECALHO + f"#\n# Gravado em {agora:%d/%m/%Y %H:%M:%S}.\n\n"
             + yaml.safe_dump(d, allow_unicode=True, sort_keys=False))
    tmp = p.with_suffix(".yaml.tmp")
    tmp.write_text(texto, encoding="utf-8")
    os.replace(tmp, p)
    return d


# ------------------------------------------------------------- comandos

COMANDOS = {
    "forcar_totais": "buscar agora os totais de Brasil e UF (e deputados), "
                     "inclusive os já finalizados",
    "forcar_tudo": "enfileirar também a varredura municipal completa "
                   "(prioridade baixa; ~14 min de cota a 20 req/s)",
    "parar": "desligar o coletor: termina a volta atual e sai",
}


def _arq_comando(env: str) -> Path:
    return PASTA_COMANDOS / f"{env}.json"


def _arq_estado(env: str) -> Path:
    return PASTA_COMANDOS / f"{env}-estado.json"


def pedir(env: str, acao: str) -> dict:
    if acao not in COMANDOS:
        raise ValueError(f"comando {acao!r} desconhecido")
    PASTA_COMANDOS.mkdir(parents=True, exist_ok=True)
    atual = ler_comando(env)
    cmd = {"id": (atual or {}).get("id", 0) + 1, "acao": acao,
           "pedido_em": datetime.now(timezone.utc).isoformat()}
    p = _arq_comando(env)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cmd), encoding="utf-8")
    os.replace(tmp, p)
    return cmd


def ler_comando(env: str) -> dict | None:
    try:
        return json.loads(_arq_comando(env).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def gravar_estado_coletor(env: str, **campos) -> None:
    """Batimento do coletor: hora da volta, último comando atendido."""
    PASTA_COMANDOS.mkdir(parents=True, exist_ok=True)
    p = _arq_estado(env)
    d = ler_estado_coletor(env) or {}
    d.update(campos, visto_em=datetime.now(timezone.utc).isoformat())
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)


def ler_estado_coletor(env: str) -> dict | None:
    try:
        return json.loads(_arq_estado(env).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
