"""Armazenamento temporário e privado do PDF.

O arquivo vive só enquanto a extração roda: é apagado ao final (sucesso ou
falha). Fica em diretório fora de STATIC/MEDIA, nunca servido pela aplicação.
"""

import hashlib
import logging
import os
import uuid
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings

logger = logging.getLogger(__name__)

PDF_MAGIC = b"%PDF-"


@dataclass(frozen=True)
class StoredUpload:
    path: Path
    sha256: str
    size: int


def upload_dir() -> Path:
    directory = Path(settings.EXAMS_UPLOAD_DIR)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    # `mkdir(mode=)` não aperta um diretório que já existia (e sofre o umask).
    directory.chmod(0o700)
    return directory


def save_upload(chunks) -> StoredUpload:
    path = upload_dir() / f"{uuid.uuid4().hex}.pdf"
    digest = hashlib.sha256()
    size = 0
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as handle:
        for chunk in chunks:
            handle.write(chunk)
            digest.update(chunk)
            size += len(chunk)
    return StoredUpload(path=path, sha256=digest.hexdigest(), size=size)


def delete_upload(path: str | Path | None) -> None:
    if not path:
        return
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:
        logger.warning("Não foi possível apagar o arquivo temporário do laudo")
