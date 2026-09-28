"""
Gera PDFs sintéticos para os testes automatizados e para o teste de ponta a ponta.

O texto é formado por palavras numeradas em sequência (w0000, w0001, ...). Assim os
testes conseguem conferir, no PDF convertido, que nenhuma palavra sumiu e que a
ordem de leitura foi respeitada (inclusive entre colunas).

Uso como script (gera o corpus do teste de ponta a ponta):
    python tests/fabrica_pdf.py pasta_de_saida/
"""

from __future__ import annotations

import io
import re
import sys
from pathlib import Path

import numpy as np
import pymupdf
from PIL import Image

A4 = pymupdf.paper_rect("a4")
MARGEM = 56  # ~2 cm


SUFIXOS = ["", "ab", "cdef", "g", "hijklm", "no", "pqr"]
PADRAO_PALAVRA = r"w(\d+)[a-z]*"


def palavras(inicio: int, quantidade: int) -> list[str]:
    """Palavras numeradas com comprimentos variados (como num texto real).

    Com todas as palavras do mesmo tamanho, os espaços ficariam alinhados de uma
    linha para outra e pareceriam calhas entre colunas.
    """
    return [f"w{i:04d}{SUFIXOS[(i * 5) % len(SUFIXOS)]}" for i in range(inicio, inicio + quantidade)]


def numeros(texto: str) -> list[int]:
    """Números das palavras de teste, na ordem em que aparecem no texto."""
    return [int(m.group(1)) for m in re.finditer(PADRAO_PALAVRA, texto)]


class Fluxo:
    """Texto corrido que continua de uma caixa (coluna/página) para a seguinte."""

    def __init__(self, lista: list[str], por_paragrafo: int = 60):
        self.paragrafos = [
            " ".join(lista[i : i + por_paragrafo]) for i in range(0, len(lista), por_paragrafo)
        ]
        self.pendentes: list[str] = []

    @property
    def vazio(self) -> bool:
        return not self.pendentes and not self.paragrafos

    def preencher(self, pagina: pymupdf.Page, retangulo: pymupdf.Rect, tamanho: float = 10):
        # Entrega só o suficiente para encher a caixa (fill_textbox é lento com textos longos).
        while len(self.pendentes) < 120 and self.paragrafos:
            self.pendentes.append(self.paragrafos.pop(0))
        texto = self.pendentes
        escritor = pymupdf.TextWriter(pagina.rect)
        sobra = escritor.fill_textbox(retangulo, texto, fontsize=tamanho, align=3)
        escritor.write_text(pagina)
        self.pendentes = [linha for linha, _ in sobra]


def uma_coluna(caminho: Path, paginas: int = 3, tamanho: float = 10, sumario: bool = True) -> list[int]:
    """Livro A4 em uma coluna, com título em cada página e sumário."""
    doc = pymupdf.open()
    fluxo = Fluxo(palavras(0, 560 * paginas))
    toc = []
    for n in range(paginas):
        pagina = doc.new_page(width=A4.width, height=A4.height)
        titulo = f"Capitulo {n + 1}"
        pagina.insert_text((MARGEM, MARGEM + 20), titulo, fontsize=20)
        toc.append([1, titulo, n + 1])
        corpo = pymupdf.Rect(MARGEM, MARGEM + 50, A4.width - MARGEM, A4.height - MARGEM)
        fluxo.preencher(pagina, corpo, tamanho)
        if fluxo.vazio:
            break
    if sumario:
        doc.set_toc(toc)
    doc.set_metadata({"title": "Livro de teste", "author": "Pipeline"})
    doc.save(caminho)
    return [n for pagina in pymupdf.open(caminho) for n in numeros(pagina.get_text())]


def duas_colunas(caminho: Path, paginas: int = 2) -> list[int]:
    """Artigo A4 em duas colunas, com título e figura de largura total na 1ª página."""
    doc = pymupdf.open()
    fluxo = Fluxo(palavras(0, 800 * paginas), 45)
    largura_col = (A4.width - 2 * MARGEM - 20) / 2
    for n in range(paginas):
        pagina = doc.new_page(width=A4.width, height=A4.height)
        topo = MARGEM
        if n == 0:
            pagina.insert_text((MARGEM + 120, topo + 20), "Artigo de Teste em Duas Colunas", fontsize=18)
            figura = pymupdf.Rect(MARGEM, topo + 40, A4.width - MARGEM, topo + 160)
            pagina.draw_rect(figura, color=(0, 0, 0), fill=(0.8, 0.8, 0.8), width=1)
            pagina.draw_circle(figura.tl + (60, 60), 40, color=(0, 0, 0), fill=(0.3, 0.3, 0.3))
            topo += 180
        esquerda = pymupdf.Rect(MARGEM, topo, MARGEM + largura_col, A4.height - MARGEM)
        direita = pymupdf.Rect(A4.width - MARGEM - largura_col, topo, A4.width - MARGEM, A4.height - MARGEM)
        fluxo.preencher(pagina, esquerda, 9)
        if not fluxo.vazio:
            fluxo.preencher(pagina, direita, 9)
        if fluxo.vazio:
            break
    doc.save(caminho)
    return [n for pagina in pymupdf.open(caminho) for n in numeros(pagina.get_text())]


def escaneado(caminho: Path) -> None:
    """Página só com imagem: fundo cinza, ruído e compressão JPEG, como um scanner."""
    origem = pymupdf.open()
    pagina = origem.new_page(width=A4.width, height=A4.height)
    Fluxo(palavras(0, 350)).preencher(
        pagina, pymupdf.Rect(MARGEM, MARGEM, A4.width - MARGEM, A4.height - MARGEM), 11
    )
    pix = pagina.get_pixmap(dpi=150, colorspace=pymupdf.csGRAY)
    limpo = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width).astype(float)
    rng = np.random.default_rng(42)
    sujo = np.clip(limpo * 0.8 + 30 + rng.normal(0, 6, limpo.shape), 0, 255).astype(np.uint8)
    buf = io.BytesIO()
    Image.fromarray(sujo).save(buf, "JPEG", quality=80)
    doc = pymupdf.open()
    nova = doc.new_page(width=A4.width, height=A4.height)
    nova.insert_image(nova.rect, stream=buf.getvalue())
    doc.save(caminho)


def em_branco(caminho: Path) -> None:
    doc = pymupdf.open()
    doc.new_page(width=A4.width, height=A4.height)
    doc.save(caminho)


def protegido(caminho: Path) -> None:
    doc = pymupdf.open()
    pagina = doc.new_page()
    pagina.insert_text((72, 72), "segredo")
    doc.save(caminho, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="abc", owner_pw="xyz")


def corrompido(caminho: Path) -> None:
    caminho.write_bytes(b"%PDF-1.7\n" + bytes(range(256)) * 20)


def gerar_corpus(pasta: Path) -> dict[str, Path]:
    """Corpus usado no teste de ponta a ponta do ambiente de homologação."""
    pasta.mkdir(parents=True, exist_ok=True)
    arquivos = {
        "livro": pasta / "livro_uma_coluna.pdf",
        "artigo": pasta / "artigo_duas_colunas.pdf",
        "escaneado": pasta / "pagina_escaneada.pdf",
        "grande": pasta / "livro_grande.pdf",
    }
    uma_coluna(arquivos["livro"], paginas=4)
    duas_colunas(arquivos["artigo"], paginas=3)
    escaneado(arquivos["escaneado"])
    uma_coluna(arquivos["grande"], paginas=100, tamanho=11)
    return arquivos


if __name__ == "__main__":
    destino = Path(sys.argv[1] if len(sys.argv) > 1 else "corpus")
    for nome, arq in gerar_corpus(destino).items():
        print(f"{nome}: {arq} ({pymupdf.open(arq).page_count} páginas)")
