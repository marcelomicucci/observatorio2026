"""Classificação política e mapa ideológico (§6).

Este módulo entrega a **máquina**, não o **conteúdo**. A tabela de partidos
vem vazia de propósito.

Classificar um partido brasileiro como esquerda, centro ou direita é um ato
editorial, não um cálculo. O §6 exige, para cada entrada, fonte,
justificativa, autor, data, validade temporal e grau de controvérsia — e o
§12 lista a curadoria da classificação ideológica como decisão de execução
ainda pendente. Preencher essa tabela por conta própria seria inventar
autoria e transformar opinião em dado com aparência de oficial, que é
exatamente o que o §6 proíbe ("não interpretar classificação como atributo
oficial do TSE").

O que está pronto:

- as três dimensões separadas — ideologia, posição em relação ao governo e
  aliança estadual — que o §6 manda não confundir;
- a paleta pedida, com contraste verificado e leitura que não depende só de
  cor;
- o versionamento, a validação e o cálculo de saldo;
- o estado `curadoria_pendente`, que faz o mapa ideológico aparecer
  desabilitado na interface, com o motivo, em vez de aparecer errado.

Para ativar: preencher `config/classificacao_2026.yaml` com autoria real e
rodar `obs2026 validar-classificacao`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import yaml

from ..config import CONFIG_DIR

# -- paleta ---------------------------------------------------------------
# Cores pedidas na especificação. Sem cinza: ausência de classificação tem
# padrão visual próprio (hachura), não uma cor que possa ser lida como
# "centro" (§6).
PALETA = {
    "esquerda": "#C62828",
    "centro_esquerda": "#E06C63",
    "centro": "#F2C94C",
    "centro_direita": "#5B8BC9",
    "direita": "#1565C0",
}

#: Ausência de dado NÃO usa o amarelo do centro (§6, camada 6).
SEM_CLASSIFICACAO = {
    "cor": "#FFFFFF",
    "padrao": "hachura-diagonal",
    "rotulo": "sem classificação",
}

#: A leitura não pode depender só de cor (§11.3). Cada categoria tem símbolo
#: e rótulo textual próprios, usados na legenda, no mapa e na exportação.
SIMBOLOS = {
    "esquerda": "◀",
    "centro_esquerda": "◁",
    "centro": "◆",
    "centro_direita": "▷",
    "direita": "▶",
}

CATEGORIAS = tuple(PALETA.keys())

#: Agrupamento em três blocos, com a regra explícita (§6).
REGRA_AGRUPAMENTO = {
    "esquerda": ("esquerda", "centro_esquerda"),
    "centro": ("centro",),
    "direita": ("centro_direita", "direita"),
}

POSICOES_GOVERNO = ("governo", "oposicao", "independente")


class ClassificationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Entrada:
    ano: int
    entidade_tipo: str          # partido | candidato
    entidade_id: str
    ideologia: str
    posicao_governo: str | None
    fonte: str
    justificativa: str
    autor: str
    decidido_em: date
    valido_de: date | None
    valido_ate: date | None
    grau_controversia: int
    versao: str

    def valida(self) -> list[str]:
        p = []
        if self.ideologia not in CATEGORIAS:
            p.append(f"ideologia fora do vocabulario: {self.ideologia!r}")
        if self.posicao_governo and self.posicao_governo not in POSICOES_GOVERNO:
            p.append(f"posicao_governo invalida: {self.posicao_governo!r}")
        if not self.fonte.strip():
            p.append("fonte vazia")
        if not self.justificativa.strip():
            p.append("justificativa vazia")
        if not self.autor.strip():
            p.append("autor vazio — classificacao exige autoria nominal")
        if not (0 <= self.grau_controversia <= 3):
            p.append("grau_controversia fora de 0..3")
        return p


@dataclass(frozen=True, slots=True)
class Registro:
    versao: str
    ano: int
    curador: str | None
    publicada_em: date | None
    entradas: tuple[Entrada, ...]

    @property
    def curadoria_pendente(self) -> bool:
        return not self.entradas or not self.curador

    def por_partido(self, sigla: str) -> Entrada | None:
        for e in self.entradas:
            if e.entidade_tipo == "partido" and e.entidade_id == sigla:
                return e
        return None

    def bloco(self, sigla: str) -> str | None:
        """Bloco de três categorias. `None` quando não há classificação.

        `None` NÃO é "centro". Um partido não classificado é desconhecido, e
        a interface o desenha com hachura, não com amarelo.
        """
        e = self.por_partido(sigla)
        if e is None:
            return None
        for bloco, cats in REGRA_AGRUPAMENTO.items():
            if e.ideologia in cats:
                return bloco
        return None


def load(path: Path | None = None) -> Registro:
    p = path or (CONFIG_DIR / "classificacao_2026.yaml")
    if not p.exists():
        return Registro(versao="2026.0-inexistente", ano=2026, curador=None,
                        publicada_em=None, entradas=())
    d = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    entradas = []
    for e in d.get("entradas") or []:
        entradas.append(Entrada(
            ano=int(e.get("ano", d.get("ano", 2026))),
            entidade_tipo=e["entidade_tipo"], entidade_id=str(e["entidade_id"]),
            ideologia=e["ideologia"], posicao_governo=e.get("posicao_governo"),
            fonte=e.get("fonte", ""), justificativa=e.get("justificativa", ""),
            autor=e.get("autor", ""),
            decidido_em=e.get("decidido_em"),
            valido_de=e.get("valido_de"), valido_ate=e.get("valido_ate"),
            grau_controversia=int(e.get("grau_controversia", 0)),
            versao=str(d.get("versao", "2026.0")),
        ))
    return Registro(
        versao=str(d.get("versao", "2026.0")), ano=int(d.get("ano", 2026)),
        curador=d.get("curador"), publicada_em=d.get("publicada_em"),
        entradas=tuple(entradas),
    )


def validar(reg: Registro) -> list[str]:
    problemas = []
    if reg.curador is None:
        problemas.append(
            "sem curador: a classificacao exige autoria identificada (§6)"
        )
    vistos = set()
    for e in reg.entradas:
        for p in e.valida():
            problemas.append(f"{e.entidade_tipo}:{e.entidade_id}: {p}")
        chave = (e.ano, e.entidade_tipo, e.entidade_id, e.versao)
        if chave in vistos:
            problemas.append(f"entrada duplicada: {chave}")
        vistos.add(chave)
    return problemas


def saldo(pct_direita: float | None, pct_esquerda: float | None,
          pct_centro: float | None,
          pct_sem_classificacao: float | None) -> dict:
    """Indicador descritivo do §6: `% direita − % esquerda`, de −100 a +100.

    Vem sempre acompanhado de centro e não classificados, porque saldo zero
    pode significar coisas políticas muito diferentes: um município metade
    esquerda metade direita e um município inteiramente de centro dão o mesmo
    zero.

    O §6 é explícito: isto NÃO é medida psicológica de polarização.
    """
    if pct_direita is None or pct_esquerda is None:
        return {"saldo": None, "motivo": "classificacao indisponivel"}
    return {
        "saldo": pct_direita - pct_esquerda,
        "pct_direita": pct_direita,
        "pct_esquerda": pct_esquerda,
        "pct_centro": pct_centro,
        "pct_sem_classificacao": pct_sem_classificacao,
        "aviso": ("Indicador descritivo da composição do voto. Saldo zero "
                  "pode corresponder a situações políticas muito diferentes. "
                  "Não é medida de polarização afetiva."),
    }


def metodologia() -> dict:
    """Payload de `/api/v1/classifications`."""
    reg = load()
    problemas = validar(reg)
    return {
        "versao": reg.versao,
        "ano": reg.ano,
        "curador": reg.curador,
        "publicada_em": (reg.publicada_em.isoformat()
                         if isinstance(reg.publicada_em, date) else None),
        "estado": ("curadoria_pendente" if reg.curadoria_pendente
                   else "disponivel"),
        "motivo": ("A classificação ideológica é um ato editorial e exige "
                   "fonte, justificativa, autor, data, validade e grau de "
                   "controvérsia por entrada (§6). Enquanto a curadoria não "
                   "for feita e assinada, o mapa ideológico fica desabilitado "
                   "e as camadas de apuração continuam disponíveis."
                   if reg.curadoria_pendente else None),
        "entradas": len(reg.entradas),
        "problemas": problemas,
        "vocabulario": {
            "ideologia": list(CATEGORIAS),
            "posicao_governo": list(POSICOES_GOVERNO),
            "regra_agrupamento": {k: list(v)
                                  for k, v in REGRA_AGRUPAMENTO.items()},
        },
        "paleta": PALETA,
        "simbolos": SIMBOLOS,
        "sem_classificacao": SEM_CLASSIFICACAO,
        "dimensoes_separadas": [
            {"nome": "ideologia",
             "nota": "não determina posição em relação ao governo"},
            {"nome": "posicao_governo",
             "nota": "apoio/oposição; muda sem que a ideologia mude"},
            {"nome": "alianca_estadual",
             "nota": "coligações locais contrariam o alinhamento nacional "
                     "com frequência; não herdar do cabeça de chapa"},
        ],
        "regras": [
            "A cor do cabeça de chapa NÃO é atribuída à coligação inteira.",
            "Classificação não é atributo oficial do TSE.",
            "Alteração de classificação gera nova versão; mapas históricos "
            "não são reescritos em silêncio.",
            "Comparação entre anos usa a classificação de cada época OU uma "
            "classificação harmonizada, sempre com o método identificado.",
            "Ausência de classificação tem padrão visual próprio e nunca usa "
            "o amarelo do centro.",
        ],
        "camadas": [
            "partido/bloco do candidato líder, intensidade pela margem",
            "participação dos votos de cada bloco",
            "governos conquistados (contar governos e eleitorado separado)",
            "Senado: duas marcas por UF, podendo ter cores diferentes",
            "variação entre eleições comparáveis",
            "incerteza e ausência de informação",
        ],
    }
