"""Aba Configuração: as chaves que o operador mexe com o sistema no ar.

Leitura:
    GET  /api/v1/configuracao            tudo o que a aba mostra, de uma vez

Gravação (só da própria máquina, só JSON, só do painel — `local.py`):
    POST /api/v1/configuracao/operacao       modelo, pular finalizados,
                                             deputados
    POST /api/v1/configuracao/comando        forçar atualização (coletor)
    POST /api/v1/configuracao/classificacao  assinar / nova versão
    POST /api/v1/configuracao/cores          cor de partido
    POST /api/v1/configuracao/senado         rebaixar a composição do Senado
    POST /api/v1/configuracao/cache          esquecer projeções em memória

Tudo que grava guarda a versão anterior e devolve o estado novo lido do
disco — o painel mostra o que ficou gravado, não o que pediu.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from fastapi import HTTPException, Request

from .. import operacao as OP
from ..politics import classification as CL
from ..politics import partidos as PA
from ..politics import senado as SEN
from .local import somente_local


def _pid_vivo(pid: int) -> bool:
    """Processo ainda existe? No Windows, `os.kill(pid, 0)` manda Ctrl+C —
    por isso a consulta pela API do sistema."""
    if os.name == "nt":
        import ctypes
        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x1000, False, pid)   # QUERY_LIMITED_INFORMATION
        if not h:
            return False
        codigo = ctypes.c_ulong()
        ok = k.GetExitCodeProcess(h, ctypes.byref(codigo))
        k.CloseHandle(h)
        return bool(ok) and codigo.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def register(app) -> None:
    from . import app as M
    from ..config import load_environment, load_thresholds

    def estado() -> dict:
        env = load_environment(M._ENV_NAME)
        thr = load_thresholds()
        op = OP.carregar()
        reg = CL.load()
        problemas = CL.validar(reg)
        ativa = not reg.curadoria_pendente and not problemas
        por_num = {}
        for e in reg.entradas:
            if e.entidade_tipo == "partido" and e.numero:
                por_num[e.numero] = e
        partidos = []
        for p in PA.cadastro()["lista"]:
            e = por_num.get(p["numero"])
            partidos.append({
                "numero": p["numero"], "sigla": p["sigla"],
                "nome": p.get("nome"), "cor": p["cor"],
                "ideologia": e.ideologia if e else None,
                "grau_controversia": e.grau_controversia if e else 0,
                "justificativa": e.justificativa if e else "",
                "fonte": e.fonte if e else "",
                "autor": (e.autor or None) if e else None,
                "decidido_em": (e.decidido_em.isoformat()
                                if e and hasattr(e.decidido_em, "isoformat")
                                else None),
            })
        sen = SEN.carregar()
        coletor = OP.ler_estado_coletor(env.name)
        return {
            "ambiente": {"nome": env.name, "rotulo": env.label,
                         "oficial": env.official,
                         "demonstracao": env.demonstracao},
            "operacao": op,
            "modelos": [{"id": k, **v} for k, v in OP.MODELOS.items()],
            "comandos": OP.COMANDOS,
            "ultimo_comando": OP.ler_comando(env.name),
            "coletor": coletor,
            "classificacao": {
                "estado": "disponivel" if ativa else "curadoria_pendente",
                "versao": reg.versao, "curador": reg.curador,
                "publicada_em": (reg.publicada_em.isoformat()
                                 if hasattr(reg.publicada_em, "isoformat")
                                 else None),
                "problemas": [x for x in problemas
                              if "autor vazio" not in x
                              and "sem curador" not in x],
                "categorias": [{"id": c, "rotulo": CL.ROTULOS[c],
                                "cor": CL.PALETA[c],
                                "simbolo": CL.SIMBOLOS[c]}
                               for c in CL.CATEGORIAS],
                "regra_agrupamento": {k: list(v) for k, v in
                                      CL.REGRA_AGRUPAMENTO.items()},
            },
            "partidos": partidos,
            "senado": {
                "obtido_em": sen.get("obtido_em") if sen else None,
                "remanescentes": (len(SEN.remanescentes(2026, sen)["cadeiras"])
                                  if sen else 0),
                "fonte": SEN.FONTE,
            },
            "homologacao": {"thresholds": thr.homologated,
                            "assinado_por": thr.signed_by,
                            "assinado_em": thr.signed_at,
                            "gate": _gate()},
        }

    def _gate() -> dict:
        """Resumo da evidência do gate (scripts/gate2022.py), sem o
        detalhe por disputa."""
        from .. import homologacao as H
        try:
            ev = H.ler_evidencia()
        except H.HomologacaoError as exc:
            return {"disponivel": False, "motivo": str(exc)}
        return {
            "disponivel": True, "gerado_em": ev.get("gerado_em"),
            "disputas": ev.get("disputas"), "passou": ev.get("passou"),
            "passa_erro_mediano": ev.get("passa_erro_mediano"),
            "passa_cobertura": ev.get("passa_cobertura"),
            "cobertura80_m2_pct": ev.get("cobertura80_m2_pct"),
            "tolerancia_pp": ev.get("tolerancia_pp"),
            "por_rodada": ev.get("por_rodada"),
            "recusa": H.motivos_de_recusa(ev),
        }

    @app.get("/api/v1/configuracao")
    def configuracao():
        return M.envelope(estado(), snapshots=[], estado="configuracao")

    async def corpo(req: Request, acao: str) -> dict:
        somente_local(req, acao)
        try:
            d = await req.json()
        except ValueError as exc:
            raise HTTPException(400, "corpo não é JSON válido") from exc
        if not isinstance(d, dict):
            raise HTTPException(400, "esperado um objeto JSON")
        return d

    @app.post("/api/v1/configuracao/operacao")
    async def gravar_operacao(req: Request):
        d = await corpo(req, "gravar a operação")
        try:
            OP.gravar(d)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return M.envelope(estado(), snapshots=[], estado="configuracao")

    @app.post("/api/v1/configuracao/coletor/iniciar")
    async def iniciar_coletor(req: Request):
        """Abre o coletor numa janela própria, como se fosse digitado.

        Janela própria (e não filho escondido do servidor): o operador vê o
        log, e reiniciar o servidor não derruba a coleta. O lock de um
        coletor por máquina continua valendo — aqui só se confere antes, para
        responder com clareza em vez de abrir uma janela que fecha sozinha."""
        await corpo(req, "iniciar o coletor")
        env = load_environment(M._ENV_NAME)
        if env.demonstracao:
            raise HTTPException(409, f"{env.name} é demonstração, montada de "
                                     "arquivo histórico: não há o que "
                                     "coletar no TSE.")
        lock = Path(env.lock_path)
        if lock.exists():
            try:
                pid = int(lock.read_text().strip() or 0)
            except (OSError, ValueError):
                pid = 0
            if pid and _pid_vivo(pid):
                raise HTTPException(409, f"o coletor já está rodando "
                                         f"(processo {pid}).")
            # Lock de um coletor que morreu sem limpar (janela fechada no X).
            lock.unlink(missing_ok=True)
        raiz = Path(__file__).resolve().parents[2]
        flags = (subprocess.CREATE_NEW_CONSOLE if os.name == "nt" else 0)
        proc = subprocess.Popen(
            [sys.executable, "-m", "obs2026.cli", "coletar", "--env",
             env.name], cwd=str(raiz), creationflags=flags)
        r = estado()
        r["iniciado"] = {"pid": proc.pid, "ambiente": env.name}
        return M.envelope(r, snapshots=[], estado="configuracao")

    @app.post("/api/v1/configuracao/comando")
    async def comando(req: Request):
        d = await corpo(req, "comandar o coletor")
        try:
            cmd = OP.pedir(M._ENV_NAME, str(d.get("acao") or ""))
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        r = estado()
        r["pedido"] = cmd
        return M.envelope(r, snapshots=[], estado="configuracao")

    @app.post("/api/v1/configuracao/classificacao")
    async def classificacao(req: Request):
        """`{"curador": "...", "alteracoes": [...]}`.

        Sem alterações e com a proposta pendente, é a assinatura da proposta
        como está. Com alterações, é uma versão nova assinada por quem
        gravou. Nos dois casos o nome é obrigatório — a aba não deixa gravar
        classificação anônima."""
        d = await corpo(req, "assinar a classificação")
        curador = str(d.get("curador") or "").strip()
        alts = d.get("alteracoes") or []
        if not isinstance(alts, list):
            raise HTTPException(400, "`alteracoes` é uma lista")
        try:
            if alts:
                r = CL.gravar_versao(curador, alts)
            else:
                reg = CL.load()
                if not reg.curadoria_pendente:
                    raise HTTPException(
                        422, "nada a gravar: a classificação já está "
                             "assinada e nenhuma linha mudou")
                r = CL.assinar(curador)
        except CL.ClassificationError as exc:
            raise HTTPException(422, str(exc)) from exc
        out = estado()
        out["gravado"] = r
        return M.envelope(out, snapshots=[], estado="configuracao")

    @app.post("/api/v1/configuracao/cores")
    async def cores(req: Request):
        d = await corpo(req, "mudar cor de partido")
        c = d.get("cores") or {}
        if not isinstance(c, dict) or not c:
            raise HTTPException(400, "esperado {\"cores\": {numero: cor}}")
        try:
            PA.gravar_cores({str(k): str(v) for k, v in c.items()})
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return M.envelope(estado(), snapshots=[], estado="configuracao")

    @app.post("/api/v1/configuracao/senado")
    async def senado(req: Request):
        """Rebaixa a composição atual do Senado. Usa a internet (dados
        abertos do Senado): é passo de preparo, não de noite de apuração."""
        await corpo(req, "atualizar o Senado")
        try:
            SEN.baixar()
        except Exception as exc:                  # noqa: BLE001
            raise HTTPException(
                502, f"não foi possível baixar do Senado ({exc}); a versão "
                     "anterior em disco continua valendo") from exc
        return M.envelope(estado(), snapshots=[], estado="configuracao")

    @app.post("/api/v1/configuracao/reler-dia")
    async def reler_dia(req: Request):
        """Faz a carga contínua reler o manifesto de hoje desde o começo.

        Apaga só o marcador de "até onde li" do dia; a carga do coletor
        relê na passada seguinte (5 s). Idempotente: o que já está no banco
        não duplica. Foi o reparo feito à mão no simulado de 22/09."""
        d = await corpo(req, "reler o dia")
        from datetime import datetime, timezone
        from sqlalchemy import delete, func, select
        from ..storage.models import CargaManifesto, CargaPendente
        dia = str(d.get("dia") or datetime.now(timezone.utc).date())
        if M._Session is None:
            M.configure(M._ENV_NAME)
        with M._Session() as s:
            n = s.execute(delete(CargaManifesto).where(
                CargaManifesto.dia == dia)).rowcount
            pend = s.execute(select(func.count(CargaPendente.id))).scalar()
            s.commit()
        r = estado()
        r["relido"] = {"dia": dia, "marcadores_apagados": n,
                       "pendentes": pend}
        return M.envelope(r, snapshots=[], estado="configuracao")

    @app.post("/api/v1/configuracao/homologar")
    async def homologar(req: Request):
        """Homologa o gate com o nome de quem assina. Recusa se a evidência
        não passou — não há atalho (ver `homologacao.py`)."""
        from .. import homologacao as H
        d = await corpo(req, "homologar o gate")
        try:
            H.homologar(str(d.get("assinante") or ""))
        except H.HomologacaoError as exc:
            raise HTTPException(422, str(exc)) from exc
        return M.envelope(estado(), snapshots=[], estado="configuracao")

    @app.post("/api/v1/configuracao/revogar")
    async def revogar(req: Request):
        from .. import homologacao as H
        d = await corpo(req, "revogar a homologação")
        H.revogar(str(d.get("motivo") or ""))
        return M.envelope(estado(), snapshots=[], estado="configuracao")

    @app.post("/api/v1/configuracao/cache")
    async def cache(req: Request):
        """Esquece as linhas de mapa guardadas em memória. Não apaga nada
        em disco: a próxima leitura recalcula do banco."""
        await corpo(req, "limpar a memória")
        from . import geo
        n = len(geo._LINHAS)
        geo._LINHAS.clear()
        r = estado()
        r["limpas"] = n
        return M.envelope(r, snapshots=[], estado="configuracao")
