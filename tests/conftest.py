from __future__ import annotations

from pathlib import Path

import pytest

import fabrica_pdf as fabrica


@pytest.fixture(scope="session")
def pdfs(tmp_path_factory) -> dict[str, Path]:
    """PDFs de teste gerados uma vez por sessão."""
    pasta = tmp_path_factory.mktemp("pdfs")
    arquivos = {
        "livro": pasta / "livro.pdf",
        "artigo": pasta / "artigo.pdf",
        "escaneado": pasta / "escaneado.pdf",
        "branco": pasta / "branco.pdf",
        "protegido": pasta / "protegido.pdf",
        "corrompido": pasta / "corrompido.pdf",
    }
    fabrica.uma_coluna(arquivos["livro"], paginas=3)
    fabrica.duas_colunas(arquivos["artigo"], paginas=2)
    fabrica.escaneado(arquivos["escaneado"])
    fabrica.em_branco(arquivos["branco"])
    fabrica.protegido(arquivos["protegido"])
    fabrica.corrompido(arquivos["corrompido"])
    return arquivos
