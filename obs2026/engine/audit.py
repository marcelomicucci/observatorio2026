"""Auditoria das próprias projeções (§7, §11.2).

Toda vez que uma projeção é produzida, ela é **registrada com o que se sabia
naquele instante**. Depois, quando o resultado final chega, cada registro é
confrontado com a verdade e vira nota.

O valor disso não é acadêmico. Uma projeção que ninguém conferiu depois é uma
opinião com aparência de número. Registrar antes e conferir depois é a única
forma de a frase "nosso intervalo de 80% cobre 80% das vezes" ser verificável
em vez de decorativa.

## O que se registra, e por quê cada campo existe

    momento            quando a projeção foi feita
    cobertura          quanto do eleitorado estava apurado naquele momento
                       — sem isso não dá para saber se um erro grande aos 3%
                       é fracasso ou comportamento esperado
    snapshot_set_id    exatamente quais arquivos o motor leu
    modelo + versão    porque reanálise com modelo novo é OUTRA série (§5.2)
    semente            sem ela não há reprodução
    thresholds_hash    prova de quais limiares valiam naquele instante
    quantis            mediana e intervalos, por candidato
    probabilidades     liderança, vaga, maioria absoluta
    definicao          o que já estava matematicamente definido
    estado             se a projeção estava publicada ou apenas registrada

O registro é **append-only**. Reprocessar não apaga o que foi dito antes: se
o valor de uma projeção passada mudar, isso é falsificação do histórico, não
correção.

## Como se avalia depois

Erro em pontos percentuais, erro de votos, cobertura dos intervalos e Brier
score dos eventos binários. Tudo por faixa de cobertura, porque a única
pergunta interessante é "a que altura da apuração a projeção passou a valer".

Uma nota metodológica que o §11.2 exige: **poucas eleições não sustentam
alegação de calibração fina**. Com uma dúzia de execuções não se demonstra
que um intervalo de 95% cobre 95%. O módulo devolve os intervalos de
confiança das próprias métricas justamente para isso ficar visível.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True, slots=True)
class RegistroCandidato:
    sqcand: str
    nome: str
    votos_observados: int
    votos_q100: int
    votos_q500: int
    votos_q900: int
    pct_concorrentes_q100: float
    pct_concorrentes_q500: float
    pct_concorrentes_q900: float
    prob_lideranca: float | None = None
    prob_dentro_vagas: float | None = None
    prob_maioria_absoluta: float | None = None
    estado_matematico: str = "em_aberto"


@dataclass(frozen=True, slots=True)
class Registro:
    """Uma projeção, congelada no instante em que foi feita."""

    registro_id: str
    momento: str                      # ISO UTC
    ambiente: str
    eleicao: str
    turno: str
    cargo: str
    abrangencia: str                  # br | uf
    unidade: str                      # 'br' ou a UF
    # --- o que se sabia ---
    snapshot_set_id: str | None
    fonte_gerada_em: str | None
    eleitorado_total: int
    eleitorado_apurado: int
    secoes_totalizadas: int
    secoes_total: int
    pendente_min: int
    pendente_max: int
    # --- como foi calculado ---
    modelo: str
    model_version: str
    semente: int
    n_sims: int
    thresholds_hash: str
    thresholds_homologados: bool
    estado_publicacao: str            # projecao_disponivel | cenario_exploratorio | ...
    # --- o que foi dito ---
    candidatos: tuple[RegistroCandidato, ...]
    erro_monte_carlo_pp: float
    disputa_matematicamente_definida: bool
    segundo_turno_matematicamente_definido: bool
    avisos: tuple[str, ...] = field(default_factory=tuple)
    #: Votos a concorrentes JA contados no momento do registro. Sem ele o
    #: grafico de convergencia precisaria estimar o denominador do
    #: observado, e comparar duas linhas com denominadores diferentes e pior
    #: do que nao comparar.
    concorrentes_observados: int = 0
    #: Horario da fonte mais antiga entre as unidades lidas. A diferenca
    #: para `fonte_gerada_em` mede o quanto o retrato estava borrado: durante
    #: a apuracao nao ha instante em que todos os municipios estejam
    #: simultaneos, e o §8.2 manda registrar essa dispersao.
    fonte_mais_antiga: str | None = None
    #: Unidades sem nenhuma secao apurada no momento do registro.
    unidades_sem_dado: int = 0
    eleitorado_sem_dado: int = 0

    @property
    def cobertura_pct(self) -> float:
        return (100.0 * self.eleitorado_apurado / self.eleitorado_total
                if self.eleitorado_total else 0.0)

    @property
    def cobertura_secoes_pct(self) -> float:
        return (100.0 * self.secoes_totalizadas / self.secoes_total
                if self.secoes_total else 0.0)

    @property
    def dispersao_fonte_s(self) -> float | None:
        """Segundos entre a fonte mais antiga e a mais recente do lote.

        Zero significa retrato simultaneo; um valor grande significa que a
        projecao misturou dado de momentos diferentes — o que e inevitavel
        durante a apuracao, mas precisa ser visivel.
        """
        if not (self.fonte_gerada_em and self.fonte_mais_antiga):
            return None
        a = datetime.fromisoformat(self.fonte_mais_antiga)
        b = datetime.fromisoformat(self.fonte_gerada_em)
        return (b - a).total_seconds()

    @property
    def chave_serie(self) -> str:
        """Identifica a série. Modelo diferente = série diferente (§5.2)."""
        return (f"{self.ambiente}:{self.eleicao}:{self.turno}:{self.cargo}:"
                f"{self.unidade}:{self.modelo}:{self.model_version}")


#: Estado da leitura incremental de `Diario.ultimos`, por arquivo.
_ULTIMOS: dict[str, dict] = {}


class Diario:
    """Diário append-only de projeções, em JSONL.

    Um arquivo por ambiente. Append-only de propósito: o histórico do que foi
    dito não pode ser reescrito, ou a auditoria perde o sentido.
    """

    def __init__(self, raiz: Path, ambiente: str) -> None:
        self.caminho = Path(raiz) / f"previsoes-{ambiente}.jsonl"
        self.caminho.parent.mkdir(parents=True, exist_ok=True)
        self.ambiente = ambiente

    def registrar(self, r: Registro) -> Registro:
        with open(self.caminho, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(asdict(r), ensure_ascii=False) + "\n")
        return r

    @staticmethod
    def _decodificar(linha: str) -> Registro:
        d = json.loads(linha)
        d["candidatos"] = tuple(
            RegistroCandidato(**c) for c in d["candidatos"])
        d["avisos"] = tuple(d.get("avisos", ()))
        return Registro(**d)

    def ler(self) -> list[Registro]:
        if not self.caminho.exists():
            return []
        saida = []
        with open(self.caminho, encoding="utf-8") as fh:
            for linha in fh:
                linha = linha.strip()
                if not linha:
                    continue
                saida.append(self._decodificar(linha))
        return saida

    def ultimos(self) -> dict[tuple[str, str, str], Registro]:
        """O registro mais recente de cada disputa, por (cargo, unidade,
        modelo).

        Incremental: guarda até que byte o diário já foi lido e continua
        dali. O diário ganha uma linha por disputa a cada 2 minutos a noite
        toda; relê-lo inteiro a cada vez que o telão pede uma projeção
        trocaria um gargalo por outro. O estado fica no módulo, não na
        instância, porque as rotas criam um `Diario` por pedido.
        """
        chave = str(self.caminho)
        est = _ULTIMOS.setdefault(chave, {"pos": 0, "ult": {}})
        if not self.caminho.exists():
            return est["ult"]
        if self.caminho.stat().st_size < est["pos"]:
            est.update(pos=0, ult={})          # arquivo trocado
        with open(self.caminho, "rb") as fh:
            fh.seek(est["pos"])
            while True:
                linha = fh.readline()
                # Linha sem quebra final é a que está sendo escrita agora:
                # fica para a próxima leitura.
                if not linha or not linha.endswith(b"\n"):
                    break
                est["pos"] += len(linha)
                texto = linha.strip()
                if not texto:
                    continue
                try:
                    r = self._decodificar(texto.decode("utf-8"))
                except (ValueError, TypeError):
                    continue
                est["ult"][(r.cargo, r.unidade, r.modelo)] = r
        return est["ult"]

    def series(self) -> dict[str, list[Registro]]:
        out: dict[str, list[Registro]] = {}
        for r in self.ler():
            out.setdefault(r.chave_serie, []).append(r)
        for v in out.values():
            v.sort(key=lambda x: x.momento)
        return out


# ---------------------------------------------------------------- captura ---

def capturar(*, forecast, definicao, ambiente: str, eleicao: str, turno: str,
             cargo: str, abrangencia: str, unidade: str,
             snapshot_set_id: str | None, fonte_gerada_em: str | None,
             eleitorado_total: int, eleitorado_apurado: int,
             secoes_totalizadas: int, secoes_total: int,
             pendente_min: int, pendente_max: int,
             modelo: str, model_version: str, thresholds_hash: str,
             thresholds_homologados: bool, estado_publicacao: str,
             nomes: dict[str, str] | None = None,
             concorrentes_observados: int = 0,
             fonte_mais_antiga: str | None = None,
             unidades_sem_dado: int = 0,
             eleitorado_sem_dado: int = 0) -> Registro:
    """Monta o registro a partir de um `Forecast` e de uma `Definicao`."""
    nomes = nomes or {}
    q = forecast.quantis((0.10, 0.50, 0.90))
    lider = forecast.prob_lideranca()
    vagas = forecast.prob_dentro_das_vagas()
    maioria = forecast.prob_maioria_absoluta()
    estados = {s.sqcand: s.estado.value for s in definicao.situacoes}

    momento = datetime.now(timezone.utc)
    cands = tuple(
        RegistroCandidato(
            sqcand=c, nome=nomes.get(c, c),
            votos_observados=forecast.observed.get(c, 0),
            votos_q100=q[c]["votos"]["q100"],
            votos_q500=q[c]["votos"]["q500"],
            votos_q900=q[c]["votos"]["q900"],
            pct_concorrentes_q100=q[c]["pct_concorrentes"]["q100"],
            pct_concorrentes_q500=q[c]["pct_concorrentes"]["q500"],
            pct_concorrentes_q900=q[c]["pct_concorrentes"]["q900"],
            prob_lideranca=lider.get(c),
            prob_dentro_vagas=vagas.get(c),
            prob_maioria_absoluta=maioria.get(c),
            estado_matematico=estados.get(c, "em_aberto"),
        )
        for c in forecast.candidates
    )
    rid = (f"{ambiente}-{eleicao}-{turno}-{cargo}-{unidade}-"
           f"{momento.strftime('%Y%m%dT%H%M%S')}")
    return Registro(
        registro_id=rid, momento=momento.isoformat(), ambiente=ambiente,
        eleicao=eleicao, turno=turno, cargo=cargo, abrangencia=abrangencia,
        unidade=unidade, snapshot_set_id=snapshot_set_id,
        fonte_gerada_em=fonte_gerada_em,
        eleitorado_total=eleitorado_total,
        eleitorado_apurado=eleitorado_apurado,
        secoes_totalizadas=secoes_totalizadas, secoes_total=secoes_total,
        pendente_min=pendente_min, pendente_max=pendente_max,
        modelo=modelo, model_version=model_version, semente=forecast.seed,
        n_sims=forecast.n_sims, thresholds_hash=thresholds_hash,
        thresholds_homologados=thresholds_homologados,
        estado_publicacao=estado_publicacao, candidatos=cands,
        erro_monte_carlo_pp=float(
            forecast.diagnostics.get("erro_monte_carlo_pp", 0.0)),
        disputa_matematicamente_definida=definicao.disputa_definida,
        segundo_turno_matematicamente_definido=(
            definicao.segundo_turno_definido),
        avisos=definicao.avisos,
        concorrentes_observados=concorrentes_observados,
        fonte_mais_antiga=fonte_mais_antiga,
        unidades_sem_dado=unidades_sem_dado,
        eleitorado_sem_dado=eleitorado_sem_dado,
    )


# ------------------------------------------------------------- avaliação ---

@dataclass(frozen=True, slots=True)
class NotaCandidato:
    sqcand: str
    nome: str
    pct_final: float
    pct_projetado: float
    erro_pp: float
    dentro_do_intervalo_80: bool
    largura_80_pp: float
    prob_dita: float | None
    aconteceu: bool | None


@dataclass(frozen=True, slots=True)
class Nota:
    registro_id: str
    momento: str
    cobertura_pct: float
    modelo: str
    model_version: str
    erro_pp_mediano: float
    erro_pp_maximo: float
    cobertura_intervalo_80: float
    largura_media_80_pp: float
    acertou_lideranca: bool
    acertou_vagas: bool
    brier_vagas: float | None
    definicao_matematica_bateu: bool | None
    candidatos: tuple[NotaCandidato, ...]

    #: Cortes do §11.2. A faixa é o intervalo em que a cobertura caiu.
    FAIXAS = (1, 5, 10, 25, 50, 75, 90, 99)

    @property
    def faixa_ordem(self) -> int:
        """Índice numérico da faixa, para ordenar sem depender do rótulo."""
        c = self.cobertura_pct
        for i, limite in enumerate(Nota.FAIXAS):
            if c < limite:
                return i
        return len(Nota.FAIXAS)

    @property
    def faixa_cobertura(self) -> str:
        i = self.faixa_ordem
        if i >= len(Nota.FAIXAS):
            return ">=99%"
        return f"<{Nota.FAIXAS[i]}%"


def _mediana(xs: list[float]) -> float:
    if not xs:
        return float("nan")
    ys = sorted(xs)
    n = len(ys)
    return ys[n // 2] if n % 2 else (ys[n // 2 - 1] + ys[n // 2]) / 2


def avaliar(registro: Registro, final: dict[str, int], *,
            concorrentes_final: int, vagas: int) -> Nota:
    """Confronta uma projeção registrada com o resultado final conhecido.

    `final` são os votos finais por `sqcand`; `concorrentes_final` é o
    denominador da maioria absoluta no fim (vvc).
    """
    if concorrentes_final <= 0:
        raise ValueError("resultado final sem votos a concorrentes")

    pct_final = {k: 100.0 * v / concorrentes_final for k, v in final.items()}
    ordem_real = sorted(final, key=lambda k: -final[k])
    eleitos_reais = set(ordem_real[:vagas])
    lider_real = ordem_real[0] if ordem_real else None

    notas: list[NotaCandidato] = []
    erros: list[float] = []
    dentro = 0
    larguras: list[float] = []
    brier_termos: list[float] = []
    definicao_ok: bool | None = None

    for c in registro.candidatos:
        alvo = pct_final.get(c.sqcand)
        if alvo is None:
            continue                     # candidato sumiu do resultado final
        erro = abs(c.pct_concorrentes_q500 - alvo)
        ok80 = c.pct_concorrentes_q100 <= alvo <= c.pct_concorrentes_q900
        larg = c.pct_concorrentes_q900 - c.pct_concorrentes_q100
        erros.append(erro)
        dentro += int(ok80)
        larguras.append(larg)

        aconteceu = c.sqcand in eleitos_reais
        if c.prob_dentro_vagas is not None:
            brier_termos.append((c.prob_dentro_vagas - float(aconteceu)) ** 2)

        # A definição matemática NÃO pode errar. Se errou, houve retotalização
        # ou bug — e isso é grave, não uma métrica a mais.
        if c.estado_matematico == "matematicamente_eleito":
            definicao_ok = bool(aconteceu) and (definicao_ok is not False)
        elif c.estado_matematico == "matematicamente_eliminado":
            definicao_ok = (not aconteceu) and (definicao_ok is not False)

        notas.append(NotaCandidato(
            sqcand=c.sqcand, nome=c.nome, pct_final=alvo,
            pct_projetado=c.pct_concorrentes_q500, erro_pp=erro,
            dentro_do_intervalo_80=ok80, largura_80_pp=larg,
            prob_dita=c.prob_dentro_vagas, aconteceu=aconteceu,
        ))

    projetada = sorted(registro.candidatos,
                       key=lambda c: -c.votos_q500)
    lider_proj = projetada[0].sqcand if projetada else None
    eleitos_proj = {c.sqcand for c in projetada[:vagas]}

    return Nota(
        registro_id=registro.registro_id, momento=registro.momento,
        cobertura_pct=registro.cobertura_pct, modelo=registro.modelo,
        model_version=registro.model_version,
        erro_pp_mediano=_mediana(erros),
        erro_pp_maximo=max(erros) if erros else float("nan"),
        cobertura_intervalo_80=(dentro / len(notas)) if notas else float("nan"),
        largura_media_80_pp=(sum(larguras) / len(larguras)
                             if larguras else float("nan")),
        acertou_lideranca=(lider_proj == lider_real),
        acertou_vagas=(eleitos_proj == eleitos_reais),
        brier_vagas=(sum(brier_termos) / len(brier_termos)
                     if brier_termos else None),
        definicao_matematica_bateu=definicao_ok,
        candidatos=tuple(notas),
    )


def consolidar(notas: list[Nota]) -> dict:
    """Resumo por faixa de cobertura, com intervalo de confiança das métricas.

    O intervalo é o ponto: com poucas execuções, a incerteza sobre a própria
    métrica é grande, e apresentá-la impede a leitura de que o modelo "está
    calibrado" quando o que há é amostra pequena (§11.2).
    """
    por_faixa: dict[str, list[Nota]] = {}
    ordem: dict[str, int] = {}
    for n in notas:
        por_faixa.setdefault(n.faixa_cobertura, []).append(n)
        ordem[n.faixa_cobertura] = n.faixa_ordem

    saida = {}
    # Ordem numérica da cobertura, não alfabética do rótulo: "<10%" antes de
    # "<5%" numa ordenação de texto, o que embaralha a leitura.
    for faixa, ns in sorted(por_faixa.items(), key=lambda kv: ordem[kv[0]]):
        k = len(ns)
        erros = [n.erro_pp_mediano for n in ns
                 if not math.isnan(n.erro_pp_mediano)]
        cobs = [n.cobertura_intervalo_80 for n in ns
                if not math.isnan(n.cobertura_intervalo_80)]
        p = (sum(cobs) / len(cobs)) if cobs else float("nan")
        # Erro padrão binomial da cobertura observada.
        ee = (math.sqrt(p * (1 - p) / len(cobs))
              if cobs and 0 <= p <= 1 and len(cobs) > 0 else float("nan"))
        briers = [n.brier_vagas for n in ns if n.brier_vagas is not None]
        falhas_definicao = [n.registro_id for n in ns
                            if n.definicao_matematica_bateu is False]
        saida[faixa] = {
            "execucoes": k,
            "erro_pp_mediano": _mediana(erros),
            "cobertura_intervalo_80": p,
            "cobertura_ic95": (
                None if math.isnan(ee) else
                [max(0.0, p - 1.96 * ee), min(1.0, p + 1.96 * ee)]),
            "nominal": 0.80,
            "calibrado": (
                None if math.isnan(ee) else
                abs(p - 0.80) <= 1.96 * ee),
            "largura_media_80_pp": (
                sum(n.largura_media_80_pp for n in ns) / k if k else None),
            "acerto_lideranca": sum(n.acertou_lideranca for n in ns) / k,
            "acerto_vagas": sum(n.acertou_vagas for n in ns) / k,
            "brier_vagas": (sum(briers) / len(briers)) if briers else None,
            "falhas_de_definicao_matematica": falhas_definicao,
            "amostra_pequena": k < 30,
        }
    return saida
