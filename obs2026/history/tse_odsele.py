"""Leitura dos resultados históricos do TSE (portal de dados abertos).

Arquivos usados, do conjunto `resultados-AAAA`:

    votacao_candidato_munzona_AAAA.zip   votos por candidato, município e zona
    detalhe_votacao_munzona_AAAA.zip     aptos, comparecimento, abstenção,
                                         brancos e nulos, por município e zona

**Os dois são necessários.** O primeiro dá a preferência; o segundo dá o
comparecimento e a taxa de votos válidos. O motor estima as três grandezas
separadamente (§4.3), então faltar o `detalhe` deixa dois terços do modelo sem
dado histórico.

Cada zip traz um CSV por UF, separador `;`, codificação latin-1, com
cabeçalho. O leitor **lê o cabeçalho real** e recusa o arquivo se faltar
coluna exigida, em vez de assumir posição fixa: o TSE mudou o layout entre
ciclos mais de uma vez, e um `IndexError` silencioso viraria voto errado.

Nada aqui converte código para int. Município do TSE é string com zeros à
esquerda, exatamente como em `tse/urls.py`.

Três armadilhas documentadas no `leiame.pdf` que acompanha o zip, todas
capazes de corromper a base em silêncio:

1. **Sentinelas negativas.** `#NULO` vira **-1** e `#NE` vira **-3** nos
   campos numéricos. São marcadores de ausência, não contagens. Somá-los
   produz voto negativo e um total menor que o real. Aqui viram `None`, e a
   linha é descartada com contagem visível — nunca convertida para zero, que
   é afirmação diferente de "desconhecido".

2. **`_BRASIL.csv` é a concatenação das UFs.** O zip de 2022 traz `_BR.csv`
   (38 MB, só Presidente, `SG_UE='BR'`) e `_BRASIL.csv` (4,3 GB, Governador e
   deputados de todas as UFs — o mesmo conteúdo dos arquivos por UF). Ler os
   dois soma tudo duas vezes. Este módulo **recusa** `_BRASIL.csv`.

3. **`VT` é pseudo-UF de voto em trânsito.** O leiame avisa que `SG_UF` pode
   trazer `BR`, `VT` ou `ZZ` além das unidades da federação. `VT` não é
   território: agregá-lo como UF inventaria uma 28ª unidade. A marca
   equivalente por linha é `ST_VOTO_EM_TRANSITO`.
"""

from __future__ import annotations

import csv
import io
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from ..tse.parsers.common import as_text, fold

#: Codificação dos CSV do portal. O TSE publica em latin-1, não UTF-8.
ENCODING = "latin-1"
DELIM = ";"

#: Colunas exigidas em `votacao_candidato_munzona`. A lista é o contrato: se
#: uma sumir, a importação falha com o nome dela, não com erro obscuro.
COLS_CANDIDATO = (
    "ANO_ELEICAO", "NR_TURNO", "CD_ELEICAO", "SG_UF", "SG_UE", "CD_MUNICIPIO",
    "NM_MUNICIPIO", "NR_ZONA", "CD_CARGO", "DS_CARGO", "SQ_CANDIDATO",
    "NR_CANDIDATO", "NM_URNA_CANDIDATO", "NR_PARTIDO", "SG_PARTIDO",
    "ST_VOTO_EM_TRANSITO", "QT_VOTOS_NOMINAIS", "DS_SIT_TOT_TURNO",
)

#: A destinação do voto só existe a partir de 2016. Em 2014 o arquivo traz a
#: situação da candidatura (`DS_SITUACAO_CANDIDATURA`: APTO / INAPTO), que é
#: de onde a destinação é derivada: candidato apto → voto válido; inapto →
#: anulado. Um dos dois tem de existir — sem nenhum, a importação recusa.
COL_DESTINACAO = "NM_TIPO_DESTINACAO_VOTOS"
COL_SITUACAO_CAND = "DS_SITUACAO_CANDIDATURA"

#: Colunas exigidas em `detalhe_votacao_munzona`.
#:
#: Este arquivo e o equivalente historico do bloco `e`/`v` do EA20, com os
#: nomes escritos por extenso. Em particular ele tem uma coluna chamada
#: **QT_VOTOS_CONCORRENTES**: "votos a concorrentes" e nome proprio do TSE,
#: nao interpretacao nossa. E as identidades sao as mesmas do EA20:
#:
#:     QT_VOTOS_CONCORRENTES = QT_TOTAL_VOTOS_VALIDOS
#:                           + QT_TOTAL_VOTOS_ANULADOS
#:                           + QT_TOTAL_VOTOS_ANUL_SUBJUD      (= vvc)
#:     QT_VOTOS = QT_VOTOS_CONCORRENTES
#:              + QT_VOTOS_BRANCOS + QT_TOTAL_VOTOS_NULOS      (= tv)
#:
#: Ha tambem QT_ELEITORES_SECOES_NAO_INSTALADAS, o equivalente de `esni`:
#: eleitores que nao produziram voto porque a secao nao foi instalada.
COLS_DETALHE = (
    "ANO_ELEICAO", "NR_TURNO", "CD_ELEICAO", "SG_UF", "CD_MUNICIPIO",
    "NR_ZONA", "CD_CARGO", "ST_VOTO_EM_TRANSITO",
    "QT_APTOS", "QT_COMPARECIMENTO", "QT_ABSTENCOES",
    "QT_TOTAL_SECOES", "QT_SECOES_NAO_INSTALADAS",
    "QT_ELEITORES_SECOES_NAO_INSTALADAS",
    "QT_VOTOS", "QT_VOTOS_CONCORRENTES", "QT_TOTAL_VOTOS_VALIDOS",
    "QT_TOTAL_VOTOS_ANULADOS", "QT_TOTAL_VOTOS_ANUL_SUBJUD",
    "QT_VOTOS_BRANCOS", "QT_TOTAL_VOTOS_NULOS",
)

#: Cargos de interesse, com o mesmo código do EA11/EA20.
CARGOS = {"1": "presidente", "3": "governador", "5": "senador"}

#: Sentinelas de ausência nos campos numéricos (leiame do TSE).
#: -1 = #NULO (em branco no banco), -3 = #NE (não registrado naquele ano).
SENTINELAS_NUM = frozenset({-1, -3})
SENTINELAS_TXT = frozenset({"#NULO", "#NE"})

#: Membro que duplica os arquivos por UF. Nunca ler junto com eles.
MEMBRO_DUPLICADO = "_BRASIL.CSV"

#: Valores de `SG_UF` que não são território.
UF_NAO_TERRITORIAL = frozenset({"VT"})


class HistoryError(RuntimeError):
    pass


def num(valor, campo: str = "?") -> int | None:
    """Inteiro do repositório histórico, com sentinelas tratadas.

    Devolve `None` para `#NULO`/`#NE` e para -1/-3. Nunca devolve zero no
    lugar de ausência: zero voto e voto desconhecido são afirmações
    diferentes, e confundi-las é o jeito mais fácil de baixar um total sem
    perceber.
    """
    if valor is None:
        return None
    s = str(valor).strip().strip('"')
    if not s or s.upper() in SENTINELAS_TXT:
        return None
    try:
        n = int(s)
    except ValueError:
        raise HistoryError(
            f"{campo}: esperado inteiro, veio {valor!r}"
        ) from None
    return None if n in SENTINELAS_NUM else n


@dataclass(frozen=True, slots=True)
class VotoHistorico:
    """Uma linha de votação por candidato, município e zona."""

    ano: int
    turno: str
    cd_eleicao: str
    uf: str
    cd_municipio: str        # string: zeros à esquerda importam
    nm_municipio: str
    zona: str
    cargo: str
    sq_candidato: str
    nr_candidato: str
    nm_urna: str
    nr_partido: str
    sg_partido: str
    votos: int
    #: Voto em trânsito. `SG_UF='VT'` e esta marca dizem a mesma coisa: a
    #: linha é real, mas não pertence ao território onde o eleitor reside.
    em_transito: bool = False
    #: Equivalente histórico do `dvt` do EA20.
    destinacao: str = ""
    #: `DS_SIT_TOT_TURNO`. Como no EA20, é status da disputa inteira,
    #: replicado em toda linha de município e zona — nunca status local.
    situacao: str = ""

    @property
    def unit(self) -> str:
        """Chave da unidade. Inclui o município porque zona não forma árvore
        territorial simples (§4.1)."""
        return f"{self.uf}:{self.cd_municipio}:{self.zona}"

    @property
    def territorial(self) -> bool:
        return self.uf.upper() not in UF_NAO_TERRITORIAL


@dataclass(frozen=True, slots=True)
class DetalheHistorico:
    """Comparecimento e destinação de voto de uma unidade."""

    ano: int
    turno: str
    cd_eleicao: str
    uf: str
    cd_municipio: str
    zona: str
    cargo: str
    aptos: int
    comparecimento: int
    abstencoes: int
    #: `tv` — total de votos. No Senado de 2018 vai ate 2x o comparecimento.
    total_votos: int
    #: `vvc` — votos a concorrentes. Denominador da maioria absoluta.
    concorrentes: int
    #: `vv` — validos.
    validos: int
    anulados: int
    anulados_sub_judice: int
    brancos: int
    nulos: int
    secoes_total: int
    secoes_nao_instaladas: int
    #: `esni` historico: eleitores que nao produziram voto.
    eleitores_secoes_nao_instaladas: int
    em_transito: bool = False

    @property
    def unit(self) -> str:
        return f"{self.uf}:{self.cd_municipio}:{self.zona}"

    def invariantes(self) -> list[str]:
        """As mesmas identidades do EA20, conferidas no historico."""
        problemas = []
        soma = self.validos + self.anulados + self.anulados_sub_judice
        if self.concorrentes != soma:
            problemas.append(
                f"concorrentes {self.concorrentes} != validos+anulados+"
                f"subjudice {soma}")
        tv = self.concorrentes + self.brancos + self.nulos
        if self.total_votos != tv:
            problemas.append(
                f"total_votos {self.total_votos} != concorrentes+brancos+"
                f"nulos {tv}")
        if self.comparecimento + self.abstencoes != self.aptos:
            problemas.append(
                f"comparecimento+abstencoes != aptos "
                f"({self.comparecimento}+{self.abstencoes} != {self.aptos})")
        return problemas

    @property
    def turnout(self) -> float | None:
        """Comparecimento sobre aptos.

        Atenção à diferença em relação ao ao vivo: aqui o denominador é o
        eleitorado da unidade inteira, porque a eleição acabou e todas as
        seções foram apuradas. No ao vivo, o denominador é o eleitorado das
        seções APURADAS (§4.3) — os dois só coincidem em 100%.
        """
        return (self.comparecimento / self.aptos) if self.aptos else None

    @property
    def taxa_valida(self) -> float | None:
        """Votos a concorrentes sobre o total de votos.

        Usa `concorrentes`, nao `validos`: e o mesmo denominador da maioria
        absoluta, e e o que o motor projeta.
        """
        return ((self.concorrentes / self.total_votos)
                if self.total_votos else None)


#: Códigos das eleições ORDINÁRIAS, por ano e turno (federal, estadual).
#:
#: Os zips do TSE de um ano trazem também as eleições suplementares
#: realizadas depois dele: o de 2018 tem a suplementar de 2020 para o
#: Senado em Mato Grosso (código 438), que elegeu Fávaro depois da cassação
#: da eleita. Lida junto, ela duplicava o candidato e punha em 2018 quem só
#: se elegeu em 2020 (visto em 24/09). Filtrar pelo código é a única forma
#: segura: a suplementar tem o mesmo ano, turno e cargo.
ELEICOES_ORDINARIAS: dict[int, dict[str, frozenset[str]]] = {
    # 2014: um código só para federal e estadual; 268/269 no mesmo zip é a
    # suplementar de governador do Amazonas (2017).
    2014: {"1": frozenset({"143"}), "2": frozenset({"144"})},
    2018: {"1": frozenset({"295", "297"}), "2": frozenset({"296", "298"})},
    2022: {"1": frozenset({"544", "546"}), "2": frozenset({"545", "547"})},
}


def ordinarias(ano: int, turno: str) -> frozenset[str] | None:
    """Códigos da eleição ordinária daquele ano e turno, ou None se o ano
    não está na tabela (aí o chamador decide; nada é filtrado às cegas)."""
    return (ELEICOES_ORDINARIAS.get(ano) or {}).get(str(turno))


def _da_eleicao(r: dict, eleicoes, turno: str) -> bool:
    """A linha pertence às eleições pedidas? Ano fora da tabela de
    ordinárias não é filtrado: não se adivinha código."""
    if eleicoes is None:
        return True
    if eleicoes == "ordinaria":
        try:
            ords = ordinarias(int(r["ANO_ELEICAO"]), turno)
        except (TypeError, ValueError):
            return True
        return ords is None or r["CD_ELEICAO"] in ords
    return r["CD_ELEICAO"] in eleicoes


#: Pasta do projeto que guarda os dados históricos (`eleicao2026/`).
RAIZ_PROJETO = Path(__file__).resolve().parents[3]


def arquivo(nome: str, ano: int | None = None) -> Path:
    """Localiza um arquivo histórico do TSE.

    Organização adotada em 22/09/2026: `eleicao2026/Dados/<ano>/<arquivo>`
    (o eleitorado fica em `Dados/`). Os lugares antigos (a raiz de
    `eleicao2026/`) continuam valendo, e `OBS2026_DADOS` aponta outra pasta.
    Se não achar em lugar nenhum, devolve o caminho da organização nova — o
    erro de "arquivo não existe" diz onde colocá-lo.
    """
    import os
    bases = []
    if os.environ.get("OBS2026_DADOS"):
        bases.append(Path(os.environ["OBS2026_DADOS"]))
    bases += [RAIZ_PROJETO / "Dados", RAIZ_PROJETO / "dados"]
    candidatos = []
    for b in bases:
        if ano is not None:
            candidatos.append(b / str(ano) / nome)
        candidatos.append(b / nome)
    candidatos.append(RAIZ_PROJETO / nome)
    for c in candidatos:
        if c.exists():
            return c
    return candidatos[0]


def _abrir(caminho: Path) -> zipfile.ZipFile:
    try:
        return zipfile.ZipFile(caminho)
    except zipfile.BadZipFile as exc:
        raise HistoryError(
            f"{caminho.name} nao e um zip valido. Se o download ainda esta "
            "em andamento, o diretorio central do zip so aparece no fim do "
            "arquivo — aguarde terminar."
        ) from exc


def membros(caminho: Path, uf: str | None = None) -> list[str]:
    """CSV dentro do zip, opcionalmente filtrando por UF.

    `_BRASIL.csv` é sempre excluído: ele repete os arquivos por UF, e somar
    os dois dobraria todos os votos de Governador.
    """
    with _abrir(caminho) as z:
        nomes = [n for n in z.namelist() if n.lower().endswith(".csv")]
    nomes = [n for n in nomes if not n.upper().endswith(MEMBRO_DUPLICADO)]
    if uf:
        alvo = f"_{uf.upper()}.CSV"
        nomes = [n for n in nomes if n.upper().endswith(alvo)]
    return sorted(nomes)


def _linhas(caminho: Path, membro: str,
            exigidas: tuple[str, ...]) -> Iterator[dict]:
    with _abrir(caminho) as z:
        with z.open(membro) as fh:
            texto = io.TextIOWrapper(fh, encoding=ENCODING, newline="")
            rd = csv.DictReader(texto, delimiter=DELIM)
            campos = {(c or "").strip().upper() for c in (rd.fieldnames or [])}
            faltando = [c for c in exigidas if c not in campos]
            if (exigidas is COLS_CANDIDATO and COL_DESTINACAO not in campos
                    and COL_SITUACAO_CAND not in campos):
                faltando.append(f"{COL_DESTINACAO} (ou {COL_SITUACAO_CAND})")
            if faltando:
                raise HistoryError(
                    f"{caminho.name}/{membro}: colunas ausentes {faltando}. "
                    "Layout do TSE mudou; revise COLS_* antes de importar."
                )
            for linha in rd:
                yield {(k or "").strip().upper(): v
                       for k, v in linha.items() if k}


def _destinacao(r: dict) -> str:
    """Destinação do voto; derivada da situação da candidatura quando o
    arquivo é anterior à coluna (2014)."""
    if COL_DESTINACAO in r:
        return as_text(r.get(COL_DESTINACAO))
    sit = fold(r.get(COL_SITUACAO_CAND) or "")
    return "Válido" if sit == "APTO" else "Anulado"


def ler_votos(caminho: Path, uf: str, *,
              cargos: tuple[str, ...] = ("1", "3", "5"),
              turno: str = "1",
              incluir_transito: bool = True,
              eleicoes: frozenset[str] | str | None = "ordinaria"
              ) -> Iterator[VotoHistorico]:
    """Votos por candidato de uma UF, filtrando cargo e turno.

    `incluir_transito=False` remove as linhas de voto em trânsito. Esses
    votos existem e contam no total da disputa; o que eles não fazem é
    pertencer ao município onde o eleitor mora. Para estimar preferência
    territorial, removê-los é o correto; para conferir totais, não.

    `eleicoes`: por padrão só a eleição ORDINÁRIA do ano (ver
    `ELEICOES_ORDINARIAS`) — o zip traz também suplementares posteriores
    (MT/Senado/2020 no de 2018; RR/Governador no de 2022), e lê-las junto
    duplica candidato e infla total. Um conjunto de códigos restringe a ele;
    `None` lê tudo, e é escolha explícita de quem precisa das suplementares.
    """
    for membro in membros(caminho, uf):
        for r in _linhas(caminho, membro, COLS_CANDIDATO):
            if r["CD_CARGO"] not in cargos or r["NR_TURNO"] != turno:
                continue
            if not _da_eleicao(r, eleicoes, turno):
                continue
            transito = str(r.get("ST_VOTO_EM_TRANSITO", "N")).strip() == "S"
            if transito and not incluir_transito:
                continue
            votos = num(r["QT_VOTOS_NOMINAIS"], "QT_VOTOS_NOMINAIS")
            if votos is None:
                continue                      # sentinela: ausência, não zero
            ano = num(r["ANO_ELEICAO"], "ANO_ELEICAO")
            if ano is None:
                continue
            yield VotoHistorico(
                ano=ano, turno=r["NR_TURNO"], cd_eleicao=r["CD_ELEICAO"],
                uf=r["SG_UF"].strip().lower(),
                cd_municipio=r["CD_MUNICIPIO"].strip(),
                nm_municipio=as_text(r["NM_MUNICIPIO"]),
                zona=r["NR_ZONA"].strip(), cargo=r["CD_CARGO"],
                sq_candidato=r["SQ_CANDIDATO"].strip(),
                nr_candidato=r["NR_CANDIDATO"].strip(),
                nm_urna=as_text(r["NM_URNA_CANDIDATO"]),
                nr_partido=r["NR_PARTIDO"].strip(),
                sg_partido=as_text(r["SG_PARTIDO"]),
                votos=votos, em_transito=transito,
                destinacao=_destinacao(r),
                situacao=as_text(r.get("DS_SIT_TOT_TURNO")),
            )


def ler_detalhe(caminho: Path, uf: str, *,
                cargos: tuple[str, ...] = ("1", "3", "5"),
                turno: str = "1",
                eleicoes: frozenset[str] | str | None = "ordinaria"
                ) -> Iterator[DetalheHistorico]:
    obrig = ("QT_APTOS", "QT_COMPARECIMENTO", "QT_ABSTENCOES",
             "QT_VOTOS", "QT_VOTOS_CONCORRENTES", "QT_TOTAL_VOTOS_VALIDOS",
             "QT_TOTAL_VOTOS_ANULADOS", "QT_TOTAL_VOTOS_ANUL_SUBJUD",
             "QT_VOTOS_BRANCOS", "QT_TOTAL_VOTOS_NULOS",
             "QT_TOTAL_SECOES", "QT_SECOES_NAO_INSTALADAS",
             "QT_ELEITORES_SECOES_NAO_INSTALADAS")
    for membro in membros(caminho, uf):
        for r in _linhas(caminho, membro, COLS_DETALHE):
            if r["CD_CARGO"] not in cargos or r["NR_TURNO"] != turno:
                continue
            if not _da_eleicao(r, eleicoes, turno):
                continue
            campos = {k: num(r[k], k) for k in obrig}
            if any(v is None for v in campos.values()):
                continue                      # sentinela em campo obrigatório
            ano = num(r["ANO_ELEICAO"], "ANO_ELEICAO")
            if ano is None:
                continue
            yield DetalheHistorico(
                ano=ano, turno=r["NR_TURNO"], cd_eleicao=r["CD_ELEICAO"],
                uf=r["SG_UF"].strip().lower(),
                cd_municipio=r["CD_MUNICIPIO"].strip(),
                zona=r["NR_ZONA"].strip(), cargo=r["CD_CARGO"],
                aptos=campos["QT_APTOS"],
                comparecimento=campos["QT_COMPARECIMENTO"],
                abstencoes=campos["QT_ABSTENCOES"],
                total_votos=campos["QT_VOTOS"],
                concorrentes=campos["QT_VOTOS_CONCORRENTES"],
                validos=campos["QT_TOTAL_VOTOS_VALIDOS"],
                anulados=campos["QT_TOTAL_VOTOS_ANULADOS"],
                anulados_sub_judice=campos["QT_TOTAL_VOTOS_ANUL_SUBJUD"],
                brancos=campos["QT_VOTOS_BRANCOS"],
                nulos=campos["QT_TOTAL_VOTOS_NULOS"],
                secoes_total=campos["QT_TOTAL_SECOES"],
                secoes_nao_instaladas=campos["QT_SECOES_NAO_INSTALADAS"],
                eleitores_secoes_nao_instaladas=campos[
                    "QT_ELEITORES_SECOES_NAO_INSTALADAS"],
                em_transito=(str(r.get("ST_VOTO_EM_TRANSITO", "N")).strip()
                             == "S"),
            )


def inspecionar(caminho: Path) -> dict:
    """Descreve o arquivo sem carregá-lo inteiro.

    Existe para ser o primeiro comando rodado sobre um zip novo: diz quantos
    membros há, qual o cabeçalho real e se as colunas exigidas estão lá —
    antes de gastar minutos numa importação que vai falhar no fim.
    """
    with _abrir(caminho) as z:
        todos = [n for n in z.namelist() if n.lower().endswith(".csv")]
        usaveis = [n for n in todos
                   if not n.upper().endswith(MEMBRO_DUPLICADO)]
        outros = [n for n in z.namelist() if not n.lower().endswith(".csv")]
        total = sum(i.file_size for i in z.infolist())
        if not usaveis:
            raise HistoryError(f"{caminho.name}: nenhum CSV utilizavel no zip")
        with z.open(sorted(usaveis)[0]) as fh:
            texto = io.TextIOWrapper(fh, encoding=ENCODING, newline="")
            rd = csv.reader(texto, delimiter=DELIM)
            header = next(rd)
            amostra = next(rd, [])
    campos = [c.strip().upper() for c in header]
    return {
        "arquivo": caminho.name,
        "bytes_zip": caminho.stat().st_size,
        "bytes_descompactado": total,
        "csvs": len(todos),
        "csvs_usaveis": len(usaveis),
        "ignorados": [n for n in todos if n not in usaveis],
        "nao_csv": outros,
        "primeiro": sorted(usaveis)[0],
        "colunas": campos,
        "n_colunas": len(campos),
        "amostra": dict(zip(campos, amostra)),
        "falta_para_candidato": [c for c in COLS_CANDIDATO if c not in campos],
        "falta_para_detalhe": [c for c in COLS_DETALHE if c not in campos],
    }
