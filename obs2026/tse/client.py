"""Cliente HTTP para a divulgação do TSE.

Restrições reais publicadas pelo TSE e implementadas aqui:

- Máximo de 100 requisições por segundo **por IP**. Excesso => bloqueio de
  10 minutos, e nova tentativa durante o bloqueio **reinicia** a contagem.
  Por isso, ao detectar bloqueio, este cliente para de verdade: não faz
  "só um retry para testar".
- Respostas 304 contam para o limite. O token bucket é debitado antes de
  saber o status, nunca depois.
- Múltiplos 404 provocam bloqueio. 404 aqui não é retentável: suspende a
  fonte e sinaliza para revalidar o catálogo (§8.1).

O teto interno é 20 req/s, um quinto do permitido. A margem existe porque o
limite é por IP e não por processo: se outro processo da mesma máquina
estiver coletando, os dois somam. O `IpBudget` é protegido por um lockfile
justamente para tornar impossível rodar dois coletores no mesmo IP sem
perceber.

Nada aqui multiplica IPs para contornar limite (§8.1).
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urlsplit

import httpx

PARSER_VERSION = "1"
USER_AGENT = (
    "Observatorio-Eleitoral-2026/0.1 (+contato: operacao@observatorio2026) "
    "httpx"
)


class BlockedError(RuntimeError):
    """O servidor sinalizou bloqueio. Parar é a única resposta correta."""


class HostNotAllowed(RuntimeError):
    """Host fora da lista permitida, inclusive após redirecionamento."""


@dataclass(frozen=True, slots=True)
class Fetch:
    """Resultado de uma requisição. `body` é None em 304 e em erro."""

    url: str
    status: int
    body: bytes | None
    sha256: str | None
    etag: str | None
    last_modified: str | None
    captured_at: datetime
    elapsed_ms: int
    from_cache: bool                 # 304: conteúdo inalterado
    parser_version: str = PARSER_VERSION
    headers: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == 200 and self.body is not None


class TokenBucket:
    """Limitador global de requisições, compartilhado por todo o processo."""

    def __init__(self, rate: float, burst: int) -> None:
        self.rate = float(rate)
        self.burst = float(burst)
        self._tokens = float(burst)
        self._last = time.monotonic()
        self._lock = asyncio.Lock()
        self.blocked_until = 0.0

    async def acquire(self) -> None:
        while True:
            async with self._lock:
                now = time.monotonic()
                if now < self.blocked_until:
                    wait = self.blocked_until - now
                else:
                    self._tokens = min(
                        self.burst, self._tokens + (now - self._last) * self.rate
                    )
                    self._last = now
                    if self._tokens >= 1.0:
                        self._tokens -= 1.0
                        return
                    wait = (1.0 - self._tokens) / self.rate
            await asyncio.sleep(wait)

    def trip_block(self, seconds: float) -> None:
        """Penalidade global. Toda a coleta para, não só a fonte culpada."""
        self.blocked_until = max(self.blocked_until, time.monotonic() + seconds)


class IpLock:
    """Garante um único coletor por máquina.

    O limite do TSE é por IP. Dois coletores rodando por engano somam
    requisições e o operador só descobre quando o bloqueio chega.
    """

    def __init__(self, path: str) -> None:
        self.path = path
        self._fh = None

    def __enter__(self) -> "IpLock":
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                owner = open(self.path).read().strip()
            except OSError:
                owner = "?"
            raise RuntimeError(
                f"ja existe um coletor ativo (lock {self.path}, pid {owner}). "
                "Dois coletores no mesmo IP somam requisicoes e provocam "
                "bloqueio. Encerre o outro ou remova o lock se for orfao."
            ) from None
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        return self

    def __exit__(self, *exc) -> None:
        try:
            os.unlink(self.path)
        except OSError:
            pass


@dataclass
class SourceState:
    """Estado por fonte: condicionais de cache e circuito."""

    etag: str | None = None
    last_modified: str | None = None
    last_sha256: str | None = None
    error_streak: int = 0
    suspended_until: float = 0.0
    suspend_reason: str | None = None

    def suspended(self) -> bool:
        return time.monotonic() < self.suspended_until

    def suspend(self, seconds: float, reason: str) -> None:
        self.suspended_until = max(
            self.suspended_until, time.monotonic() + seconds
        )
        self.suspend_reason = reason


class TseClient:
    def __init__(
        self,
        allowed_hosts: frozenset[str],
        *,
        max_rps: float = 20.0,
        burst: int = 20,
        jitter_ms: tuple[int, int] = (0, 120),
        connect_timeout_s: float = 5.0,
        read_timeout_s: float = 20.0,
        max_attempts: int = 4,
        backoff_base_s: float = 0.5,
        backoff_max_s: float = 30.0,
        retry_on_status: tuple[int, ...] = (500, 502, 503, 504),
        not_found_suspend_s: float = 600.0,
        error_streak_trip: int = 5,
        error_streak_cooldown_s: float = 300.0,
        blocked_cooldown_s: float = 660.0,
        max_bytes: int = 64 * 1024 * 1024,
    ) -> None:
        self.allowed_hosts = allowed_hosts
        self.bucket = TokenBucket(max_rps, burst)
        self.jitter_ms = jitter_ms
        self.max_attempts = max_attempts
        self.backoff_base_s = backoff_base_s
        self.backoff_max_s = backoff_max_s
        self.retry_on_status = tuple(retry_on_status)
        self.not_found_suspend_s = not_found_suspend_s
        self.error_streak_trip = error_streak_trip
        self.error_streak_cooldown_s = error_streak_cooldown_s
        self.blocked_cooldown_s = blocked_cooldown_s
        self.max_bytes = max_bytes
        self.state: dict[str, SourceState] = {}
        self.stats = {
            "requests": 0, "ok": 0, "not_modified": 0, "not_found": 0,
            "errors": 0, "blocked": 0, "bytes": 0, "suspended_skips": 0,
        }
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(read_timeout_s, connect=connect_timeout_s),
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            # Redirecionamento não é seguido automaticamente: o destino tem de
            # passar pela mesma checagem de host (§10).
            follow_redirects=False,
            limits=httpx.Limits(max_connections=40, max_keepalive_connections=20),
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    def _check_host(self, url: str) -> None:
        host = urlsplit(url).hostname or ""
        if host not in self.allowed_hosts:
            raise HostNotAllowed(f"host nao permitido: {host!r} ({url})")

    def st(self, key: str) -> SourceState:
        return self.state.setdefault(key, SourceState())

    async def fetch(self, url: str, key: str | None = None,
                    *, conditional: bool = True) -> Fetch:
        """Busca uma URL respeitando orçamento, cache condicional e circuito.

        `key` identifica a fonte para efeito de ETag e circuito; por padrão é
        a própria URL.
        """
        key = key or url
        self._check_host(url)
        st = self.st(key)

        if st.suspended():
            self.stats["suspended_skips"] += 1
            return Fetch(url, 0, None, None, st.etag, st.last_modified,
                         datetime.now(timezone.utc), 0, False,
                         headers={"x-obs-skip": st.suspend_reason or "suspended"})

        headers: dict[str, str] = {}
        if conditional:
            if st.etag:
                headers["If-None-Match"] = st.etag
            if st.last_modified:
                headers["If-Modified-Since"] = st.last_modified

        attempt = 0
        while True:
            attempt += 1
            lo, hi = self.jitter_ms
            if hi > 0:
                await asyncio.sleep(random.uniform(lo, hi) / 1000.0)
            await self.bucket.acquire()          # 304 também consome cota

            t0 = time.monotonic()
            self.stats["requests"] += 1
            try:
                r = await self._client.get(url, headers=headers)
            except httpx.HTTPError as exc:
                st.error_streak += 1
                self.stats["errors"] += 1
                if st.error_streak >= self.error_streak_trip:
                    st.suspend(self.error_streak_cooldown_s,
                               f"serie de erros de rede: {exc!r}")
                if attempt >= self.max_attempts:
                    return Fetch(url, -1, None, None, st.etag, st.last_modified,
                                 datetime.now(timezone.utc),
                                 int((time.monotonic() - t0) * 1000), False,
                                 headers={"x-obs-error": repr(exc)})
                await self._backoff(attempt)
                continue

            elapsed = int((time.monotonic() - t0) * 1000)
            now = datetime.now(timezone.utc)

            if r.status_code in (301, 302, 303, 307, 308):
                loc = r.headers.get("location", "")
                try:
                    self._check_host(str(httpx.URL(url).join(loc)))
                except HostNotAllowed:
                    st.suspend(self.error_streak_cooldown_s,
                               f"redirecionamento para host nao permitido: {loc}")
                    self.stats["errors"] += 1
                    return Fetch(url, r.status_code, None, None, st.etag,
                                 st.last_modified, now, elapsed, False,
                                 headers={"x-obs-error": "redirect-blocked"})
                url = str(httpx.URL(url).join(loc))
                continue

            if r.status_code == 304:
                st.error_streak = 0
                self.stats["not_modified"] += 1
                return Fetch(url, 304, None, st.last_sha256, st.etag,
                             st.last_modified, now, elapsed, True,
                             headers=dict(r.headers))

            if r.status_code == 404:
                # Não retentar. 404 repetido é causa documentada de bloqueio.
                self.stats["not_found"] += 1
                st.suspend(self.not_found_suspend_s,
                           "404 — revalidar catalogo antes de tentar de novo")
                return Fetch(url, 404, None, None, st.etag, st.last_modified,
                             now, elapsed, False, headers=dict(r.headers))

            if r.status_code in (403, 429) or (
                r.status_code == 503 and "retry-after" in r.headers
            ):
                self.stats["blocked"] += 1
                wait = self.blocked_cooldown_s
                ra = r.headers.get("retry-after")
                if ra and ra.isdigit():
                    wait = max(wait, float(ra))
                # Penalidade GLOBAL: insistir reinicia o bloqueio do TSE.
                self.bucket.trip_block(wait)
                st.suspend(wait, f"bloqueio http {r.status_code}")
                raise BlockedError(
                    f"servidor sinalizou bloqueio ({r.status_code}) em {url}; "
                    f"coleta pausada por {wait:.0f}s sem novas tentativas"
                )

            if r.status_code in self.retry_on_status:
                st.error_streak += 1
                self.stats["errors"] += 1
                if st.error_streak >= self.error_streak_trip:
                    st.suspend(self.error_streak_cooldown_s,
                               f"serie de {r.status_code}")
                if attempt >= self.max_attempts:
                    return Fetch(url, r.status_code, None, None, st.etag,
                                 st.last_modified, now, elapsed, False,
                                 headers=dict(r.headers))
                await self._backoff(attempt)
                continue

            if r.status_code != 200:
                st.error_streak += 1
                self.stats["errors"] += 1
                return Fetch(url, r.status_code, None, None, st.etag,
                             st.last_modified, now, elapsed, False,
                             headers=dict(r.headers))

            body = r.content
            if len(body) > self.max_bytes:
                st.suspend(self.error_streak_cooldown_s, "payload acima do teto")
                raise RuntimeError(
                    f"payload de {len(body)} bytes acima do teto em {url}"
                )

            digest = hashlib.sha256(body).hexdigest()
            st.error_streak = 0
            st.etag = r.headers.get("etag") or st.etag
            st.last_modified = r.headers.get("last-modified") or st.last_modified
            st.last_sha256 = digest
            self.stats["ok"] += 1
            self.stats["bytes"] += len(body)
            return Fetch(url, 200, body, digest, st.etag, st.last_modified,
                         now, elapsed, False, headers=dict(r.headers))

    async def _backoff(self, attempt: int) -> None:
        delay = min(self.backoff_max_s, self.backoff_base_s * (2 ** (attempt - 1)))
        await asyncio.sleep(delay * random.uniform(0.7, 1.3))
