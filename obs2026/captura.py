"""Fotografa as telas do projetor e monta o pacote para mandar no grupo.

Na noite da apuração alguém sempre pede "manda aí como está". Refazer isso à
mão — alternar para o projetor, apertar PrtScr, recortar, digitar os números
— custa um minuto e sai errado, porque no minuto seguinte os números mudaram.
Aqui uma linha de comando produz imagem e texto do mesmo instante.

## O obturador

Usa o Chrome (ou o Edge) que já está na máquina, sem cabeça, sem instalar
nada. `--screenshot` tira a foto e `--dump-dom` devolve o HTML final na
mesma execução; é no HTML que se lê `data-pronto`, o atributo que a página do
telão só marca quando terminou de desenhar, mapa incluído.

Isso troca uma espera adivinhada por uma confirmação: sem ela a captura teria
que apostar num tempo fixo, e tempo fixo erra sempre para um dos lados — foto
em branco quando a rede demora, foto atrasada quando não demora. Se a página
disser que deu erro, **nenhum arquivo é gravado**: mandar para um grupo a
foto de uma tela quebrada é pior do que não mandar foto.

A página é aberta com `estatico=1`, que desliga o carrossel e a atualização
automática. Sem isso o recorte poderia trocar entre o carregamento e o
obturador, e a imagem sairia com o título de um lugar e os números de outro.

## Por que não envia sozinho

O envio é do operador, e fica do lado dele de propósito:

1. **A API oficial do WhatsApp não manda para grupo.** A Cloud API da Meta
   entrega para números, não para grupos. Não é limitação deste código.
2. **Robô em cima do WhatsApp Web** contraria os termos de uso e arrisca o
   número da pessoa — na noite em que ela mais precisa dele.
3. **Mandar mensagem no nome de alguém é ato de quem assina.** Foto de
   projeção eleitoral circulando num grupo sem ninguém ter conferido é
   exatamente o tipo de coisa que não deve sair de um laço automático.

Então o comando faz todo o trabalho e para um passo antes: grava as imagens,
escreve a legenda, **põe os arquivos na área de transferência** e abre a
pasta. No grupo, é Ctrl+V. O que era um minuto vira um segundo, e continua
havendo uma pessoa entre a projeção e o grupo.

## A legenda

Sai do mesmo instante da foto, com o horário da fonte no TSE, a cobertura, os
primeiros colocados e — quando o gate estatístico passou — a projeção com a
faixa. Fecha sempre dizendo que o número não é oficial. Uma imagem
encaminhada perde o contexto de onde veio no primeiro repasse; o texto vai
junto dela, colado.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

BRT = ZoneInfo("America/Sao_Paulo")

#: Onde procurar o navegador, em ordem de preferência. O Chrome primeiro
#: porque é o que renderiza o telão no projetor — fotografar com o mesmo
#: motor evita diferença entre o que se vê e o que se manda.
CAMINHOS = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
)
NO_PATH = ("chrome", "google-chrome", "chromium", "msedge")

#: Teto de espera por foto. O orçamento de tempo virtual do Chrome faz os
#: temporizadores da página correrem na frente do relógio, então o limite de
#: verdade é a rede; 90 s é folgado para uma página local.
TIMEOUT_S = 90.0
#: Tempo virtual concedido à página. Enquanto há requisição pendente o
#: relógio virtual fica parado, então isto não é "espere 25 s": é "deixe a
#: página avançar o equivalente a 25 s de temporizadores".
TEMPO_VIRTUAL_MS = 25_000


class CapturaError(RuntimeError):
    pass


def encontrar_navegador(preferido: str | None = None) -> Path:
    """Chrome ou Edge já instalado. Não baixa nada."""
    if preferido:
        p = Path(preferido)
        if not p.exists():
            raise CapturaError(f"navegador informado não existe: {p}")
        return p
    for c in CAMINHOS:
        if Path(c).exists():
            return Path(c)
    for n in NO_PATH:
        achado = shutil.which(n)
        if achado:
            return Path(achado)
    raise CapturaError(
        "nenhum navegador encontrado. A captura usa o Chrome ou o Edge que "
        "já estiver instalado; informe o caminho com `--navegador` se ele "
        "estiver fora do lugar padrão.")


def _slug(s: str) -> str:
    from .tse.parsers.common import fold
    # `fold` devolve em maiúsculas — ela existe para casar nome de município,
    # não para nomear arquivo.
    s = re.sub(r"[^a-z0-9]+", "-", fold(s or "tela").lower())
    return s.strip("-")[:48] or "tela"


def _num(v: float | None, casas: int = 1) -> str:
    """Número no formato brasileiro. `None` vira 'n/d', nunca zero."""
    if v is None:
        return "n/d"
    return f"{v:,.{casas}f}".replace(",", "\x00").replace(".", ",") \
                            .replace("\x00", ".")


def _hora(iso: str | None) -> str:
    if not iso:
        return "n/d"
    try:
        return datetime.fromisoformat(iso).astimezone(BRT).strftime("%H:%M:%S")
    except ValueError:
        return iso


@dataclass
class Foto:
    indice: int
    titulo: str
    imagem: Path
    legenda: str


@dataclass
class Pacote:
    pasta: Path
    fotos: list[Foto] = field(default_factory=list)
    puladas: list[tuple[int, str, str]] = field(default_factory=list)
    arquivo_legenda: Path | None = None

    @property
    def texto(self) -> str:
        return "\n\n".join(f.legenda for f in self.fotos)


def _disparar(navegador: Path, url: str, destino: Path,
              largura: int, altura: int) -> str:
    """Tira a foto e devolve o HTML final. Levanta se a página não ficou boa.

    A foto e o DOM saem da **mesma** execução: fotografar numa e conferir
    noutra confirmaria o estado de uma página que não é a fotografada.
    """
    with tempfile.TemporaryDirectory(prefix="obs2026-foto-") as perfil:
        argv = [
            str(navegador),
            "--headless=new",
            "--disable-gpu",
            "--hide-scrollbars",
            "--force-device-scale-factor=1",
            f"--window-size={largura},{altura}",
            # Perfil próprio e descartável: a captura não pode mexer no
            # navegador que a pessoa está usando, nem depender dele estar
            # fechado.
            f"--user-data-dir={perfil}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-extensions",
            f"--virtual-time-budget={TEMPO_VIRTUAL_MS}",
            f"--screenshot={destino}",
            "--dump-dom",
            url,
        ]
        try:
            r = subprocess.run(argv, capture_output=True, timeout=TIMEOUT_S)
        except subprocess.TimeoutExpired as exc:
            raise CapturaError(
                f"o navegador não respondeu em {TIMEOUT_S:.0f}s") from exc

    dom = r.stdout.decode("utf-8", "replace")
    m = re.search(r'data-pronto="([^"]*)"', dom)
    estado = m.group(1) if m else None
    if estado == "1":
        if not destino.exists() or destino.stat().st_size == 0:
            raise CapturaError("a página ficou pronta mas nenhuma imagem "
                               "foi gravada")
        return dom

    # Nada de arquivo pela metade em disco: o operador varre a pasta e manda
    # o que estiver lá.
    destino.unlink(missing_ok=True)
    if estado == "erro":
        motivo = "n/d"
        me = re.search(r'class="vazio"[^>]*>([^<]{0,200})', dom)
        if me:
            motivo = me.group(1).strip()
        raise CapturaError(f"a tela não renderizou: {motivo}")
    if estado == "0" or estado is None:
        erro = r.stderr.decode("utf-8", "replace").strip().splitlines()
        cauda = erro[-1] if erro else ""
        raise CapturaError(
            "a página não terminou de desenhar dentro do orçamento de tempo. "
            "O servidor está no ar em " + url.split("/telao")[0] + "? "
            + (f"({cauda})" if cauda else ""))
    raise CapturaError(f"estado de prontidão inesperado: {estado!r}")


def _legenda(api: str, tela: dict, quantos: int = 5) -> str:
    """O mesmo instante da foto, em texto, com a ressalva de sempre."""
    qs = {"cargo": tela["cargo"]}
    if tela.get("uf"):
        qs["uf"] = tela["uf"]
    if tela.get("municipio"):
        qs["municipio"] = tela["municipio"]
    abr = "mu" if tela.get("municipio") else (
        "br" if tela["cargo"] == "1" else "uf")

    def pega(rota: str, extra: dict | None = None):
        try:
            with httpx.Client(base_url=api, timeout=60.0) as c:
                r = c.get(rota, params={**qs, **(extra or {})})
                r.raise_for_status()
                return r.json()
        except (httpx.HTTPError, ValueError):
            return None

    res = pega("/api/v1/results", {"abrangencia": abr})
    cands = pega("/api/v1/candidates")
    proj = pega("/api/v1/projection", {"sims": 1200, "registrar": "false"})

    linhas = [tela["titulo"]]
    if res and res.get("dados"):
        u = res["dados"][0]
        e = u["eleitorado"]
        cob = 100.0 * e["apurado"] / e["total"] if e.get("total") else None
        linhas.append(
            f"{_num(cob, 2)}% do eleitorado apurado"
            f"  ·  fonte TSE {_hora(res.get('source_generated_at'))}")
    else:
        linhas.append("sem dados para este recorte")

    lista = (((cands or {}).get("dados") or [{}])[0] or {}).get(
        "candidatos") or []
    lista.sort(key=lambda c: -(c["oficial"].get("votos") or 0))
    projecoes = {x["sqcand"]: x for x in ((proj or {}).get("dados") or [])}
    publicavel = bool((proj or {}).get("publicavel"))

    for i, c in enumerate(lista[:quantos], 1):
        o = c["oficial"]
        nome = (c.get("enriquecimento") or {}).get("nome") or o["nome_urna"]
        linha = (f"{i}. {nome} ({o['partido']})  "
                 f"{_num(o.get('pct'), 2)}%")
        pj = projecoes.get(o["sqcand"])
        # Sem o gate, a projeção não vira número num texto que vai circular:
        # encaminhada uma vez, "cenário exploratório" some e sobra o número.
        if pj and publicavel:
            p = pj["projecao"]
            linha += (f"  ·  proj. {_num(p.get('pct_mediana'))}% "
                      f"({_num(p.get('pct_q100'))}–{_num(p.get('pct_q900'))})")
        linhas.append(linha)

    if proj is not None:
        if publicavel:
            linhas.append(
                f"Projeção {str(proj.get('modelo', '')).upper()} · mediana e "
                f"intervalo de 80% · {proj.get('n_sims')} simulações")
        else:
            linhas.append(
                "Sem projeção publicada: o gate estatístico não passou. "
                "Os percentuais acima são o apurado, não previsão.")

    if tela.get("aviso_recorte"):
        linhas.append(tela["aviso_recorte"])

    agora = datetime.now(BRT).strftime("%d/%m %H:%M:%S")
    linhas.append(
        f"— Observatório Eleitoral 2026, {agora}. Número não oficial; "
        f"o resultado oficial é o do TSE.")
    return "\n".join(linhas)


def roteiro(api: str) -> list[dict]:
    """As telas do projetor, como a API as resolve."""
    try:
        with httpx.Client(base_url=api, timeout=30.0) as c:
            r = c.get("/api/v1/telao/roteiro")
            r.raise_for_status()
            return r.json().get("dados") or []
    except httpx.HTTPError as exc:
        raise CapturaError(
            f"não consegui ler o roteiro em {api}. O servidor está no ar "
            f"(`python -m uvicorn obs2026.api.app:app --port 8026`)? "
            f"[{exc!r}]") from exc


CARGOS = {"1": "Presidente", "3": "Governador", "5": "Senador"}


def _tela_avulsa(cargo: str, uf: str | None, municipio: str | None,
                 camada: str) -> dict:
    """Um recorte pedido na linha de comando, sem passar pelo roteiro.

    O município vem por **código TSE**, não por nome: `PAU D'ARCO` existe em
    PA e em TO, e a mesma regra que vale no roteiro vale aqui (§2.1).
    """
    nome = CARGOS.get(cargo)
    if nome is None:
        raise CapturaError(f"cargo {cargo!r} desconhecido; use 1, 3 ou 5")
    if cargo != "1" and not uf:
        raise CapturaError(
            f"{nome.lower()} é decidido por UF; informe --uf")
    if municipio and not str(municipio).isdigit():
        raise CapturaError(
            "informe o município pelo código TSE de 5 dígitos: nome de "
            "município não é chave única no país")
    if municipio and not uf:
        raise CapturaError("--municipio exige --uf: o código TSE é único "
                           "por UF, não no país")
    onde = (f"{municipio}/{uf.upper()}" if municipio
            else uf.upper() if uf else "Brasil")
    return {
        "indice": -1, "cargo": cargo, "uf": uf,
        "municipio": str(municipio).zfill(5) if municipio else None,
        "camada": camada, "titulo": f"{nome} — {onde}",
        "erro": None,
        "aviso_recorte": (
            "Recorte do voto. A disputa é decidida "
            + ("no total do Brasil" if cargo == "1"
               else f"no total de {(uf or '').upper()}")
            + " — liderar aqui não é ser eleito por aqui."
            if municipio else None),
    }


def capturar(*, api: str = "http://localhost:8026",
             telas: list[int] | None = None,
             recorte: dict | None = None,
             destino: Path,
             largura: int = 1920, altura: int = 1080,
             navegador: str | None = None,
             com_legenda: bool = True) -> Pacote:
    """Fotografa as telas pedidas do roteiro e monta o pacote."""
    nav = encontrar_navegador(navegador)

    if recorte is not None:
        alvos = [_tela_avulsa(**recorte)]
    else:
        todas = roteiro(api)
        if not todas:
            raise CapturaError("o roteiro está vazio; veja config/telao.yaml")

        por_indice = {t["indice"]: t for t in todas}
        if telas is None:
            alvos = [t for t in todas if not t["erro"]]
        else:
            alvos = []
            for i in telas:
                t = por_indice.get(i)
                if t is None:
                    raise CapturaError(
                        f"tela {i} não existe; o roteiro tem "
                        f"{sorted(por_indice)}")
                alvos.append(t)
        if not alvos:
            raise CapturaError(
                "nenhuma tela válida no roteiro: "
                + "; ".join(f"{t['indice']}: {t['erro']}" for t in todas))

    agora = datetime.now(BRT)
    pasta = destino / agora.strftime("%Y-%m-%d")
    pasta.mkdir(parents=True, exist_ok=True)
    carimbo = agora.strftime("%H%M%S")

    pacote = Pacote(pasta=pasta)
    for t in alvos:
        if t["erro"]:
            pacote.puladas.append((t["indice"], t["titulo"], t["erro"]))
            continue
        arq = pasta / f"{carimbo}-{max(t['indice'], 0)}-{_slug(t['titulo'])}.png"
        base = f"{api.rstrip('/')}/telao?estatico=1"
        if t["indice"] >= 0:
            url = f"{base}&tela={t['indice']}"
        else:
            url = (f"{base}&cargo={t['cargo']}&camada={t['camada']}"
                   + (f"&uf={t['uf']}" if t["uf"] else "")
                   + (f"&municipio={t['municipio']}" if t["municipio"] else ""))
        try:
            _disparar(nav, url, arq, largura, altura)
        except CapturaError as exc:
            pacote.puladas.append((t["indice"], t["titulo"], str(exc)))
            continue
        legenda = _legenda(api, t) if com_legenda else t["titulo"]
        pacote.fotos.append(Foto(t["indice"], t["titulo"], arq, legenda))

    if pacote.fotos and com_legenda:
        alvo = pasta / f"{carimbo}-legendas.txt"
        alvo.write_text(pacote.texto + "\n", encoding="utf-8")
        pacote.arquivo_legenda = alvo
    return pacote


def para_area_de_transferencia(caminhos: list[Path]) -> bool:
    """Põe os arquivos na área de transferência do Windows.

    É o último passo automatizável antes do envio: no grupo, Ctrl+V cola as
    imagens. O envio em si continua sendo um ato de quem assina.
    """
    if not caminhos or os.name != "nt":
        return False
    lista = ",".join("'" + str(p).replace("'", "''") + "'" for p in caminhos)
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             f"Set-Clipboard -Path @({lista})"],
            capture_output=True, timeout=30)
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def abrir_pasta(pasta: Path) -> bool:
    try:
        if os.name == "nt":
            os.startfile(pasta)  # noqa: S606
            return True
    except OSError:
        pass
    return False
