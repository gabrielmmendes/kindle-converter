"""Testes de integração: convertem PDFs gerados e inspecionam o resultado."""

from __future__ import annotations

import zipfile
from pathlib import Path

import numpy as np
import pymupdf
import pytest
from PIL import Image

import convert as c
from fabrica_pdf import numeros


def rodar(entrada: Path, saida: Path, *opcoes: str) -> int:
    return c.main([str(entrada), "-o", str(saida), *opcoes])


def imagem_da_tela(pagina: pymupdf.Page) -> Image.Image:
    """Imagem da tela como está gravada no PDF (tons de cinza, 1 canal)."""
    pix = pymupdf.Pixmap(pagina.parent, pagina.get_images()[0][0])
    assert pix.n == 1, "a tela deveria estar em tons de cinza"
    return Image.frombytes("L", (pix.width, pix.height), pix.samples)


# --------------------------------------------------------------------------- #
# Saída em imagem (padrão)
# --------------------------------------------------------------------------- #
def test_saida_padrao_tem_telas_600x800_em_cinza(pdfs, tmp_path):
    assert rodar(pdfs["livro"], tmp_path) == 0
    doc = pymupdf.open(tmp_path / "livro_kindle.pdf")
    assert doc.page_count >= 3
    for pagina in doc:
        assert abs(pagina.rect.width / pagina.rect.height - 600 / 800) < 0.01
        img = imagem_da_tela(pagina)
        assert img.size == (600, 800)
        assert img.mode == "L"
        assert set(np.unique(np.asarray(img))) <= set(range(0, 256, 17))


def test_mantem_sumario_e_titulo(pdfs, tmp_path):
    rodar(pdfs["livro"], tmp_path)
    original = pymupdf.open(pdfs["livro"])
    doc = pymupdf.open(tmp_path / "livro_kindle.pdf")
    assert [t[1] for t in doc.get_toc()] == [t[1] for t in original.get_toc()]
    paginas = [t[2] for t in doc.get_toc()]
    assert paginas == sorted(paginas) and paginas[-1] <= doc.page_count
    assert doc.metadata["title"] == "Livro de teste"


def test_pdf_escaneado_fica_com_fundo_branco(pdfs, tmp_path):
    rodar(pdfs["escaneado"], tmp_path)
    doc = pymupdf.open(tmp_path / "escaneado_kindle.pdf")
    arr = np.asarray(imagem_da_tela(doc[0]))
    assert np.median(arr) == 255  # o cinza do scanner virou branco


# --------------------------------------------------------------------------- #
# Saída vetorial: permite conferir o texto
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("nome", ["livro", "artigo"])
@pytest.mark.parametrize("orientacao", ["retrato", "paisagem"])
def test_nenhuma_palavra_se_perde_e_a_ordem_e_mantida(pdfs, tmp_path, nome, orientacao):
    """Todas as palavras chegam ao Kindle, uma vez só, na ordem de leitura.

    No artigo em duas colunas isso prova que a coluna da esquerda é lida inteira
    antes da direita.
    """
    rodar(pdfs[nome], tmp_path, "--tipo", "vetorial", "--orientacao", orientacao)
    original = [n for p in pymupdf.open(pdfs[nome]) for n in numeros(p.get_text())]
    convertido = [n for p in pymupdf.open(tmp_path / f"{nome}_kindle.pdf") for n in numeros(p.get_text())]
    assert len(original) > 500
    assert convertido == original


def test_sumario_aponta_para_a_tela_do_titulo(pdfs, tmp_path):
    rodar(pdfs["livro"], tmp_path, "--tipo", "vetorial")
    doc = pymupdf.open(tmp_path / "livro_kindle.pdf")
    for _, titulo, pagina in doc.get_toc():
        assert doc[pagina - 1].search_for(titulo), f"{titulo} não está na tela {pagina}"


def test_artigo_em_colunas_fica_com_letra_maior_que_o_impresso(pdfs, tmp_path, capsys):
    rodar(pdfs["artigo"], tmp_path)
    saida = capsys.readouterr().out
    percentual = int(saida.split("texto no Kindle: ~")[1].split("%")[0])
    assert percentual >= 95


def test_ignorar_topo_remove_cabecalho(pdfs, tmp_path):
    rodar(pdfs["livro"], tmp_path, "--tipo", "vetorial", "--ignorar-topo", "35")
    texto = "".join(p.get_text() for p in pymupdf.open(tmp_path / "livro_kindle.pdf"))
    assert "Capitulo" not in texto
    assert numeros(texto)


def test_intervalo_de_paginas(pdfs, tmp_path):
    rodar(pdfs["livro"], tmp_path, "--tipo", "vetorial", "--paginas", "2")
    original = pymupdf.open(pdfs["livro"])
    esperado = numeros(original[1].get_text())
    obtido = [n for p in pymupdf.open(tmp_path / "livro_kindle.pdf") for n in numeros(p.get_text())]
    assert obtido == esperado


# --------------------------------------------------------------------------- #
# Modelos, modos e formatos
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("opcoes", "tamanho"),
    [
        (["--orientacao", "paisagem"], (800, 600)),
        (["--modelo", "paperwhite"], (1072, 1448)),
    ],
)
def test_tamanho_das_telas(pdfs, tmp_path, opcoes, tamanho):
    rodar(pdfs["artigo"], tmp_path, *opcoes)
    doc = pymupdf.open(tmp_path / "artigo_kindle.pdf")
    assert imagem_da_tela(doc[0]).size == tamanho


def test_modo_pagina_gera_uma_tela_por_pagina(pdfs, tmp_path):
    rodar(pdfs["livro"], tmp_path, "--modo", "pagina")
    assert pymupdf.open(tmp_path / "livro_kindle.pdf").page_count == pymupdf.open(pdfs["livro"]).page_count


def test_nova_tela_por_pagina(pdfs, tmp_path):
    rodar(pdfs["livro"], tmp_path, "--tipo", "vetorial", "--nova-tela-por-pagina")
    doc = pymupdf.open(tmp_path / "livro_kindle.pdf")
    # cada capítulo (um por página) começa no topo de uma tela
    for _, titulo, pagina in doc.get_toc():
        assert doc[pagina - 1].search_for(titulo)[0].y0 < 30


def test_modo_largura_nao_divide_colunas(pdfs, tmp_path):
    rodar(pdfs["artigo"], tmp_path, "--modo", "largura")
    rodar(pdfs["artigo"], tmp_path / "auto")
    largura = pymupdf.open(tmp_path / "artigo_kindle.pdf").page_count
    auto = pymupdf.open(tmp_path / "auto" / "artigo_kindle.pdf").page_count
    assert largura < auto  # página inteira reduzida: menos telas


def test_formato_cbz(pdfs, tmp_path):
    rodar(pdfs["artigo"], tmp_path, "--formato", "cbz")
    rodar(pdfs["artigo"], tmp_path / "pdf")
    with zipfile.ZipFile(tmp_path / "artigo_kindle.cbz") as z:
        nomes = z.namelist()
        assert nomes == sorted(nomes) and nomes[0] == "00001.png"
        with z.open(nomes[0]) as f:
            assert Image.open(f).size == (600, 800)
    assert len(nomes) == pymupdf.open(tmp_path / "pdf" / "artigo_kindle.pdf").page_count


def test_pasta_de_entrada(pdfs, tmp_path):
    pasta = tmp_path / "entrada"
    (pasta / "sub").mkdir(parents=True)
    (pasta / "sub" / "A.PDF").write_bytes(pdfs["artigo"].read_bytes())
    (pasta / "notas.txt").write_text("ignorar")
    assert rodar(pasta, tmp_path / "saida") == 0
    assert (tmp_path / "saida" / "A_kindle.pdf").exists()


# --------------------------------------------------------------------------- #
# Erros
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("nome", "mensagem"),
    [
        ("protegido", "senha"),
        ("corrompido", "não é um PDF"),
        ("branco", "conteúdo visível"),
    ],
)
def test_erros_sao_explicados_e_nao_interrompem_o_lote(pdfs, tmp_path, capsys, nome, mensagem):
    codigo = c.main([str(pdfs[nome]), str(pdfs["artigo"]), "-o", str(tmp_path)])
    saida = capsys.readouterr().out
    assert codigo == 1
    assert mensagem in saida
    assert "Traceback" not in saida
    assert (tmp_path / "artigo_kindle.pdf").exists()  # o outro arquivo foi convertido


def test_sem_pdfs_nao_e_erro(tmp_path, capsys):
    assert c.main([str(tmp_path), "-o", str(tmp_path / "out")]) == 0
    assert "Nenhum PDF" in capsys.readouterr().out


def test_entrada_inexistente(tmp_path, capsys):
    assert c.main([str(tmp_path / "nao_existe.pdf")]) == 0
    assert "não encontrado" in capsys.readouterr().out


def test_cbz_vetorial_e_invalido(pdfs):
    with pytest.raises(SystemExit) as erro:
        c.main([str(pdfs["artigo"]), "--formato", "cbz", "--tipo", "vetorial"])
    assert erro.value.code == 2


def test_versao(capsys):
    with pytest.raises(SystemExit):
        c.main(["--version"])
    assert c.__version__ in capsys.readouterr().out
