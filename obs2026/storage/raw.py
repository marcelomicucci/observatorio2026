"""Arquivo bruto imutável, endereçado por conteúdo.

Princípio (§8.1): os bytes originais são a fonte de verdade. O banco é
derivado e pode ser reconstruído; o bruto, não. Por isso nada aqui sobrescreve
nem apaga.

Layout:

    data/raw/<ambiente>/<AAAA-MM-DD>/<sha256[:2]>/<sha256>.json      bytes
    data/raw/<ambiente>/manifest/<AAAA-MM-DD>.jsonl                  índice

O conteúdo é gravado uma única vez por hash: o TSE republica o mesmo payload
muitas vezes (o `idg` muda, o conteúdo não), e deduplicar por hash reduz o
volume sem perder nenhuma observação — cada captura vira uma linha no
manifesto, com seu próprio `captured_at`.

`idg` NÃO é tratado como contador global crescente (§8.1): ele é registrado
como veio e serve de evidência, nunca de ordenação.
"""

from __future__ import annotations

import gzip
import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True, slots=True)
class SnapshotRef:
    """Uma captura. Vira uma linha do manifesto e uma linha em source_snapshot."""

    key: str                  # chave lógica da fonte (Target.key)
    kind: str                 # ea11 | ea12 | ea14 | ea15 | ea20
    url: str
    status: int
    sha256: str | None
    bytes_len: int
    captured_at: str          # ISO UTC — quando NÓS recebemos
    source_generated_at: str | None   # dg+hg do payload — quando o TSE gerou
    idg: str | None
    etag: str | None
    last_modified: str | None
    environment: str
    parser_version: str
    path: str | None          # caminho relativo do blob; None em 304/erro
    dedup: bool               # True = conteúdo idêntico já existia
    election: str | None = None
    cargo: str | None = None
    uf: str | None = None
    mun: str | None = None


class RawArchive:
    def __init__(self, root: str | Path, environment: str,
                 compress: bool = True) -> None:
        self.root = Path(root) / environment
        self.environment = environment
        self.compress = compress
        (self.root / "manifest").mkdir(parents=True, exist_ok=True)

    # -- gravação --------------------------------------------------------

    def _blob_path(self, sha: str, day: str) -> Path:
        ext = ".json.gz" if self.compress else ".json"
        return self.root / day / sha[:2] / f"{sha}{ext}"

    def store(self, *, key: str, kind: str, url: str, status: int,
              body: bytes | None, sha256: str | None, captured_at: datetime,
              source_generated_at: datetime | None, idg: str | None,
              etag: str | None, last_modified: str | None,
              parser_version: str, election: str | None = None,
              cargo: str | None = None, uf: str | None = None,
              mun: str | None = None) -> SnapshotRef:
        day = captured_at.astimezone(timezone.utc).strftime("%Y-%m-%d")
        path: str | None = None
        dedup = False

        if body is not None and sha256:
            blob = self._blob_path(sha256, day)
            if blob.exists():
                dedup = True
            else:
                blob.parent.mkdir(parents=True, exist_ok=True)
                tmp = blob.with_suffix(blob.suffix + ".tmp")
                data = gzip.compress(body) if self.compress else body
                tmp.write_bytes(data)
                # rename atômico: nunca existe blob truncado com nome final
                os.replace(tmp, blob)
            path = str(blob.relative_to(self.root)).replace("\\", "/")

        ref = SnapshotRef(
            key=key, kind=kind, url=url, status=status, sha256=sha256,
            bytes_len=len(body) if body else 0,
            captured_at=captured_at.astimezone(timezone.utc).isoformat(),
            source_generated_at=(source_generated_at.isoformat()
                                 if source_generated_at else None),
            idg=idg, etag=etag, last_modified=last_modified,
            environment=self.environment, parser_version=parser_version,
            path=path, dedup=dedup, election=election, cargo=cargo,
            uf=uf, mun=mun,
        )
        man = self.root / "manifest" / f"{day}.jsonl"
        with open(man, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(asdict(ref), ensure_ascii=False) + "\n")
        return ref

    # -- leitura ---------------------------------------------------------

    def load(self, ref_or_path: SnapshotRef | str) -> dict:
        rel = ref_or_path.path if isinstance(ref_or_path, SnapshotRef) \
            else ref_or_path
        if not rel:
            raise FileNotFoundError("snapshot sem blob (304 ou erro)")
        p = self.root / rel
        raw = p.read_bytes()
        if p.suffix == ".gz":
            raw = gzip.decompress(raw)
        return json.loads(raw.decode("utf-8"))

    def manifest(self, day: str):
        p = self.root / "manifest" / f"{day}.jsonl"
        if not p.exists():
            return
        with open(p, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    yield SnapshotRef(**json.loads(line))

    def manifest_desde(self, day: str, inicio: int = 0):
        """Linhas do manifesto a partir de um deslocamento em bytes.

        Devolve pares `(ref, fim)`, em que `fim` é o deslocamento logo
        depois da linha — o valor a guardar para retomar dali.

        Lê em binário, de propósito. Em modo texto, `tell()` depois de um
        `break` no meio de um `for` levanta erro, e o deslocamento de texto
        nem é byte. Lendo linha a linha em binário o deslocamento é exato.

        Para na primeira linha sem quebra final: é a que o coletor ainda está
        escrevendo. Ela não é devolvida e o deslocamento não passa dela — a
        próxima leitura a pega inteira.
        """
        p = self.root / "manifest" / f"{day}.jsonl"
        if not p.exists():
            return
        with open(p, "rb") as fh:
            fh.seek(inicio)
            pos = inicio
            while True:
                linha = fh.readline()
                if not linha or not linha.endswith(b"\n"):
                    return
                pos += len(linha)
                texto = linha.strip()
                if not texto:
                    continue
                yield SnapshotRef(**json.loads(texto.decode("utf-8"))), pos

    def days(self) -> list[str]:
        d = self.root / "manifest"
        return sorted(p.stem for p in d.glob("*.jsonl"))
