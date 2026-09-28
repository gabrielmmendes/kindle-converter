#!/usr/bin/env python3
"""
convert.py: converte PDFs para leitura confortável no Kindle.

Alvo padrão: Kindle básico de 10ª geração (tela de 6", 600x800 pixels, 167 ppi).

O conteúdo original é mantido como está (fontes, fórmulas, tabelas e figuras). O
script re-diagrama as páginas para a tela do Kindle:

  1. corta as margens em branco;
  2. detecta colunas (artigos em 2 ou 3 colunas) e define a ordem de leitura;
  3. amplia cada coluna até a largura da tela;
  4. distribui as linhas em telas cheias, sem cortar nenhuma linha ao meio;
  5. otimiza para e-ink (contraste, escurecimento do texto fino, 16 tons de cinza);
  6. mantém o sumário (marcadores) e o título do PDF original.

Exemplos:
    python convert.py livro.pdf
    python convert.py input/ -o output/
    python convert.py artigo.pdf --modo auto --paginas 1-5
    python convert.py apostila.pdf --orientacao paisagem
    python convert.py hq.pdf --modo pagina
    python convert.py livro.pdf --tipo vetorial        # mantém o texto pesquisável
"""
from __future__ import annotations

import argparse
import io
import statistics
import sys
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pymupdf
from PIL import Image

MODELOS = {
    "basico": {"nome": "Kindle básico 10ª geração", "largura": 600, "altura": 800, "ppi": 167},
    "paperwhite": {"nome": "Kindle Paperwhite 10ª geração", "largura": 1072, "altura": 1448, "ppi": 300},
}

LIMIAR_TINTA = 210          # pixel mais escuro que isso (0-255) conta como conteúdo
ESCALA_ANALISE = 2.0        # a análise do layout é feita a 144 dpi
MAX_PIXELS_ANALISE = 2400   # limite para páginas muito grandes
SUPERAMOSTRAGEM = 2         # renderiza 2x maior e reduz com Lanczos (texto mais limpo)
AMPLIACAO_MAXIMA = 1.6      # nunca aumenta o texto mais que 1,6x o tamanho impresso
MM = 72 / 25.4              # pontos por milímetro


# --------------------------------------------------------------------------- #
# Estruturas
# --------------------------------------------------------------------------- #
@dataclass
class Faixa:
    """Área contínua de leitura: página inteira, uma coluna ou um bloco largo."""
    x0: int
    x1: int
    y0: int
    y1: int
    blocos: list[tuple[int, int]]  # linhas/blocos (y0, y1) em pixels da análise


@dataclass(eq=False)
class Colocacao:
    """Um recorte da página original desenhado em uma tela do Kindle."""
    pagina: int
    clip: pymupdf.Rect                      # em pontos, na página original
    destino: list[float]                    # x, y, largura, altura em pixels da tela
    chave: tuple | None = None              # identifica a faixa (para emendar blocos)
    escala: float = 1.0                     # px de análise por ponto
    k: float = 1.0                          # px de tela por px de análise
    x_px: tuple[int, int] = (0, 0)          # faixa horizontal (px de análise)
    blocos: list[tuple[int, int]] = field(default_factory=list)  # linhas incluídas

    @property
    def fim_px(self) -> int:
        return self.blocos[-1][1]


@dataclass
class Tela:
    colocacoes: list[Colocacao] = field(default_factory=list)


@dataclass
class Opcoes:
    largura: int
    altura: int
    ppi: int
    modo: str
    tipo: str
    formato: str
    gama: float
    realce: bool
    ignorar_topo: float      # pontos
    ignorar_base: float      # pontos
    nova_tela_por_pagina: bool
    paginas: str | None


# --------------------------------------------------------------------------- #
# Utilitários
# --------------------------------------------------------------------------- #
def trechos(mascara: np.ndarray) -> list[tuple[int, int]]:
    """Sequências de True em um vetor booleano, como pares (início, fim)."""
    m = np.concatenate(([False], mascara, [False])).astype(np.int8)
    d = np.diff(m)
    return list(zip(np.flatnonzero(d == 1).tolist(), np.flatnonzero(d == -1).tolist()))


def intervalo_paginas(texto: str | None, total: int) -> list[int]:
    if not texto:
        return list(range(total))
    paginas: list[int] = []
    for parte in texto.replace(" ", "").split(","):
        if not parte:
            continue
        if "-" in parte:
            a, b = parte.split("-", 1)
            ini = int(a) if a else 1
            fim = int(b) if b else total
        else:
            ini = fim = int(parte)
        paginas.extend(range(max(1, ini) - 1, min(total, fim)))
    return sorted(set(paginas))


def pixmap_para_array(pix: pymupdf.Pixmap) -> np.ndarray:
    arr = np.frombuffer(pix.samples, dtype=np.uint8)
    return arr.reshape(pix.height, pix.stride)[:, : pix.width]


# --------------------------------------------------------------------------- #
# Análise do layout
# --------------------------------------------------------------------------- #
def escala_da_pagina(pagina: pymupdf.Page) -> float:
    r = pagina.rect
    return min(ESCALA_ANALISE, MAX_PIXELS_ANALISE / max(r.width, r.height))


def mapa_de_tinta(pagina: pymupdf.Page, escala: float, opc: Opcoes) -> np.ndarray:
    pix = pagina.get_pixmap(matrix=pymupdf.Matrix(escala, escala),
                            colorspace=pymupdf.csGRAY, alpha=False)
    tinta = pixmap_para_array(pix) < LIMIAR_TINTA
    topo = int(opc.ignorar_topo * escala)
    base = int(opc.ignorar_base * escala)
    if topo > 0:
        tinta[:topo] = False
    if base > 0:
        tinta[-base:] = False
    return tinta


def caixa_de_conteudo(tinta: np.ndarray) -> tuple[int, int, int, int] | None:
    linhas = np.flatnonzero(tinta.any(axis=1))
    if len(linhas) == 0:
        return None
    colunas = np.flatnonzero(tinta.any(axis=0))
    return int(colunas[0]), int(colunas[-1]) + 1, int(linhas[0]), int(linhas[-1]) + 1


def detectar_calha(tinta: np.ndarray, x0: int, x1: int, y0: int, y1: int) -> tuple[int, int] | None:
    """Procura o espaço vertical em branco entre duas colunas de texto."""
    largura = x1 - x0
    if largura < 150:
        return None
    sub = tinta[y0:y1, x0:x1]
    blocos = trechos(sub.any(axis=1))
    if len(blocos) < 6:
        return None

    # Para cada posição x, soma a altura dos blocos (linhas) que estão vazios nela.
    altura_vazia = np.zeros(largura)
    total = 0
    for a, b in blocos:
        altura_vazia += (b - a) * ~sub[a:b].any(axis=0)
        total += b - a
    i0, i1 = int(largura * 0.2), int(largura * 0.8)
    pico = altura_vazia[i0:i1].max()
    # A calha é o trecho mais vazio da região central. O limiar acompanha o pico
    # para que uma coluna curta (fim de capítulo) não seja confundida com a calha.
    candidatos = altura_vazia >= max(0.5 * total, 0.92 * pico)

    minimo = max(10, int(largura * 0.012))
    maximo = int(largura * 0.15)
    opcoes = [(a, b) for a, b in trechos(candidatos)
              if minimo <= b - a <= maximo and a >= i0 and b <= i1]
    if not opcoes:
        return None
    centro = largura / 2
    a, b = max(opcoes, key=lambda r: (r[1] - r[0], -abs((r[0] + r[1]) / 2 - centro)))

    esquerda = int(sub[:, :a].sum())
    direita = int(sub[:, b:].sum())
    if esquerda + direita == 0 or min(esquerda, direita) < 0.05 * (esquerda + direita):
        return None

    # Colunas de verdade encostam na calha: as linhas da esquerda terminam perto dela
    # e as da direita começam perto dela. Isso descarta sumários, listas e tabelas.
    faixa = max(4, int(largura * 0.06))
    lado_esq = lado_dir = encosta_esq = encosta_dir = 0
    for i, j in blocos:
        if sub[i:j, :a].any():
            lado_esq += 1
            encosta_esq += bool(sub[i:j, a - faixa:a].any())
        if sub[i:j, b:].any():
            lado_dir += 1
            encosta_dir += bool(sub[i:j, b:b + faixa].any())
    if min(lado_esq, lado_dir) < 3:
        return None
    if encosta_esq < 0.5 * lado_esq or encosta_dir < 0.5 * lado_dir:
        return None
    return x0 + a, x0 + b


def fundir_fragmentos(blocos: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Junta pedaços pequenos (acentos, índices, traços) à linha vizinha mais próxima."""
    if len(blocos) < 3:
        return blocos
    mediana = statistics.median(b - a for a, b in blocos)
    blocos = [list(b) for b in blocos]
    mudou = True
    while mudou and len(blocos) > 1:
        mudou = False
        for i, (a, b) in enumerate(blocos):
            if b - a >= 0.35 * mediana:
                continue
            gap_ant = a - blocos[i - 1][1] if i > 0 else None
            gap_prox = blocos[i + 1][0] - b if i + 1 < len(blocos) else None
            limite = 0.5 * mediana
            opcoes = [(g, j) for g, j in ((gap_ant, i - 1), (gap_prox, i + 1))
                      if g is not None and g <= limite]
            if not opcoes:
                continue
            _, j = min(opcoes)
            k, m = min(i, j), max(i, j)
            blocos[k] = [blocos[k][0], blocos[m][1]]
            del blocos[m]
            mudou = True
            break
    return [tuple(b) for b in blocos]


def nova_faixa(tinta: np.ndarray, x0: int, x1: int, y0: int, y1: int) -> Faixa | None:
    sub = tinta[y0:y1, x0:x1]
    blocos = [(y0 + a, y0 + b) for a, b in trechos(sub.any(axis=1))]
    if not blocos:
        return None
    blocos = fundir_fragmentos(blocos)
    return Faixa(x0, x1, blocos[0][0], blocos[-1][1], blocos)


def segmentar(tinta: np.ndarray, x0: int, x1: int, y0: int, y1: int,
              detectar_colunas: bool, prof: int = 0, altura_min: int = 0) -> list[Faixa]:
    """Divide uma área em faixas na ordem de leitura (detecta até 4 colunas)."""
    linhas = np.flatnonzero(tinta[y0:y1, x0:x1].any(axis=1))
    if len(linhas) == 0:
        return []
    y0, y1 = y0 + int(linhas[0]), y0 + int(linhas[-1]) + 1
    if prof == 0:
        altura_min = int(0.2 * (y1 - y0))  # sub-áreas menores não são divididas de novo

    calha = None
    if detectar_colunas and prof < 3 and (prof == 0 or y1 - y0 >= altura_min):
        calha = detectar_calha(tinta, x0, x1, y0, y1)
    if calha is None:
        f = nova_faixa(tinta, x0, x1, y0, y1)
        return [f] if f else []

    ga, gb = calha
    # Só o miolo da calha decide se uma linha atravessa as colunas (evita que letras
    # encostadas na margem da coluna, como hífens e itálicos, contem como "largo").
    folga = max(1, (gb - ga) // 4)
    ma, mb = ga + folga, gb - folga
    blocos = [(y0 + a, y0 + b) for a, b in trechos(tinta[y0:y1, x0:x1].any(axis=1))]
    regioes: list[list] = []   # [tipo, início, fim]
    for a, b in blocos:
        tipo = "largo" if tinta[a:b, ma:mb].any() else "colunas"
        if regioes and regioes[-1][0] == tipo:
            regioes[-1][2] = b
        else:
            regioes.append([tipo, a, b])

    # Trechos de "colunas" muito baixos são só linhas curtas dentro de um bloco largo.
    for r in regioes:
        if r[0] == "colunas" and r[2] - r[1] < 0.06 * (y1 - y0):
            r[0] = "largo"
    fundidas: list[list] = []
    for r in regioes:
        if fundidas and fundidas[-1][0] == r[0]:
            fundidas[-1][2] = r[2]
        else:
            fundidas.append(list(r))

    if len(fundidas) == 1 and fundidas[0][0] == "largo":
        f = nova_faixa(tinta, x0, x1, y0, y1)
        return [f] if f else []

    faixas: list[Faixa] = []
    for tipo, a, b in fundidas:
        if tipo == "largo":
            faixas += segmentar(tinta, x0, x1, a, b, detectar_colunas, prof + 1, altura_min)
        else:
            corte = titulo_centralizado(tinta, x0, ga, gb, x1, a, b)
            if corte:
                faixas += segmentar(tinta, x0, x1, a, corte, detectar_colunas, prof + 1, altura_min)
                a = corte
            faixas += segmentar(tinta, x0, ga, a, b, detectar_colunas, prof + 1, altura_min)
            faixas += segmentar(tinta, gb, x1, a, b, detectar_colunas, prof + 1, altura_min)
    return faixas


def titulo_centralizado(tinta, x0, ga, gb, x1, a, b) -> int | None:
    """Título curto e centralizado acima das colunas, que não chega a cruzar a calha.

    Ex.: em uma página de 3 colunas, um título centralizado fica todo em cima da
    coluna do meio. Sem este ajuste ele seria lido no meio do texto.
    """
    esq = np.flatnonzero(tinta[a:b, x0:ga].any(axis=1))
    dir_ = np.flatnonzero(tinta[a:b, gb:x1].any(axis=1))
    if len(esq) == 0 or len(dir_) == 0 or abs(int(esq[0]) - int(dir_[0])) < 3:
        return None
    lx0, lx1, outro = (gb, x1, a + int(esq[0])) if dir_[0] < esq[0] else (x0, ga, a + int(dir_[0]))
    blocos = [(a + i, a + j) for i, j in trechos(tinta[a:outro, lx0:lx1].any(axis=1)) if a + j < outro - 1]
    if not blocos:
        return None
    corte = blocos[-1][1]
    if corte - a > 0.25 * (b - a):
        return None
    colunas = np.flatnonzero(tinta[a:corte, x0:x1].any(axis=0))
    centro_titulo = x0 + (colunas[0] + colunas[-1]) / 2
    if abs(centro_titulo - (x0 + x1) / 2) > 0.06 * (x1 - x0):
        return None
    return corte


def margens_do_documento(doc: pymupdf.Document, paginas: list[int], opc: Opcoes) -> dict:
    """Margens esquerda/direita típicas do documento (mediana), por tamanho de página.

    Usar a mesma largura em todas as páginas evita que o tamanho da letra mude
    de uma tela para outra quando uma página tem só linhas curtas. Páginas pares
    e ímpares são separadas porque livros costumam ter margens espelhadas.
    """
    grupos: dict[tuple, list[tuple[float, float]]] = {}
    for pno in paginas:
        pagina = doc[pno]
        escala = escala_da_pagina(pagina)
        caixa = caixa_de_conteudo(mapa_de_tinta(pagina, escala, opc))
        if caixa:
            formato = (round(pagina.rect.width), round(pagina.rect.height))
            valor = (caixa[0] / escala, caixa[1] / escala)
            grupos.setdefault(formato + (pno % 2,), []).append(valor)
            grupos.setdefault(formato, []).append(valor)
    return {chave: (statistics.median(v[0] for v in valores),
                    statistics.median(v[1] for v in valores))
            for chave, valores in grupos.items() if len(valores) >= 3}


def margem_da_pagina(margens: dict, pagina: pymupdf.Page) -> tuple[float, float] | None:
    formato = (round(pagina.rect.width), round(pagina.rect.height))
    return margens.get(formato + (pagina.number % 2,)) or margens.get(formato)


# --------------------------------------------------------------------------- #
# Paginação: distribui as linhas em telas cheias
# --------------------------------------------------------------------------- #
class Paginador:
    def __init__(self, opc: Opcoes):
        self.W, self.H = opc.largura, opc.altura
        self.margem = round(self.W * 0.015)          # respiro nas laterais da tela
        self.Wu = self.W - 2 * self.margem           # largura útil
        self.ampliacao_max = AMPLIACAO_MAXIMA * opc.ppi / 72  # px de tela por ponto
        self.espaco_entre_faixas = round(self.H * 0.025)
        self.telas: list[Tela] = [Tela()]
        self.y = 0.0
        self.primeira_tela: dict[int, int] = {}
        self.escalas: list[float] = []   # px de tela por ponto, para estatística

    @property
    def atual(self) -> Tela:
        return self.telas[-1]

    def nova_tela(self):
        if self.atual.colocacoes:
            self.telas.append(Tela())
        self.y = 0.0

    def _dx(self, largura_px: float, k: float) -> float:
        return self.margem + (self.Wu - largura_px * k) / 2

    def _colocar(self, pno, escala, x0, x1, a, b, k, dx, dy, chave) -> Colocacao:
        clip = pymupdf.Rect(x0 / escala, a / escala, x1 / escala, b / escala)
        destino = [dx, dy, (x1 - x0) * k, (b - a) * k]
        c = Colocacao(pno, clip, destino, chave, escala, k, (x0, x1), [(a, b)])
        self.atual.colocacoes.append(c)
        self.primeira_tela.setdefault(pno, len(self.telas) - 1)
        return c

    @staticmethod
    def _estender(c: Colocacao, blocos: list[tuple[int, int]]):
        c.blocos.extend(blocos)
        c.clip.y1 = c.fim_px / c.escala
        c.destino[3] = (c.fim_px - c.blocos[0][0]) * c.k

    def adicionar_pagina(self, pno: int, escala: float, tinta: np.ndarray, faixas: list[Faixa]):
        for i, faixa in enumerate(faixas):
            largura = faixa.x1 - faixa.x0
            k = min(self.Wu / largura, self.ampliacao_max / escala)  # px de tela por px de análise
            dx = self._dx(largura, k)
            self.escalas.append(k * escala)
            # Espaço bem maior que o normal entre linhas indica título/nova seção.
            lacunas = [b[0] - a[1] for a, b in zip(faixa.blocos, faixa.blocos[1:])]
            limite_titulo = 2.5 * statistics.median(lacunas) + 2 if len(lacunas) >= 3 else None
            anterior = None
            for a, b in faixa.blocos:
                self._adicionar_bloco(pno, escala, tinta, faixa, (pno, i), a, b, k, dx,
                                      anterior, limite_titulo)
                anterior = b

    def _adicionar_bloco(self, pno, escala, tinta, faixa, chave, a, b, k, dx, anterior, limite_titulo):
        h = (b - a) * k
        if h > self.H:
            self._bloco_grande(pno, escala, tinta, faixa, a, b, k)
            return
        for tentativa in range(2):
            ultima = self.atual.colocacoes[-1] if self.atual.colocacoes else None
            continua = ultima is not None and ultima.chave == chave and ultima.fim_px == anterior
            if ultima is None:
                gap = 0.0
            elif continua:
                gap = (a - anterior) * k
            else:
                gap = self.espaco_entre_faixas
            if self.y + gap + h <= self.H + 0.5:
                if continua:
                    self._estender(ultima, [(a, b)])
                else:
                    self._colocar(pno, escala, faixa.x0, faixa.x1, a, b, k, dx, self.y + gap, chave)
                self.y += gap + h
                return
            # Não coube: se a tela termina com um título, ele desce junto para a próxima.
            if tentativa == 0 and continua and self._mover_titulo(ultima, limite_titulo, dx):
                continue
            break
        self.nova_tela()
        self._colocar(pno, escala, faixa.x0, faixa.x1, a, b, k, dx, 0.0, chave)
        self.y = h

    def _mover_titulo(self, c: Colocacao, limite: float | None, dx: float) -> bool:
        """Evita título órfão no pé da tela.

        Se a tela termina com um título (bloco depois de um espaço grande) seguido de
        no máximo uma linha, o título desce para a tela seguinte junto com o texto.
        Títulos em várias partes ("Capítulo 3" + "O Fim") descem juntos.
        """
        if limite is None:
            return False
        primeiro_da_tela = self.atual.colocacoes[0] is c

        def quebra(j: int) -> bool:  # há um espaço grande antes do bloco j?
            if j == 0:
                return not primeiro_da_tela
            return c.blocos[j][0] - c.blocos[j - 1][1] >= limite

        n = len(c.blocos)
        inicio = next((j for j in range(n - 1, -1, -1) if quebra(j)), None)
        if inicio is None or n - inicio > 2:
            return False
        while inicio > 0 and quebra(inicio - 1):
            inicio -= 1
        if inicio == 0 and primeiro_da_tela:
            return False
        if (c.fim_px - c.blocos[inicio][0]) * c.k > 0.3 * self.H:
            return False
        if inicio == 0:
            self.atual.colocacoes.remove(c)
            self.nova_tela()
            c.destino[1] = 0.0
            self.atual.colocacoes.append(c)
            self.y = c.destino[3]
            return True
        movidos = c.blocos[inicio:]
        c.blocos = c.blocos[:inicio]
        c.clip.y1 = c.fim_px / c.escala
        c.destino[3] = (c.fim_px - c.blocos[0][0]) * c.k
        self.nova_tela()
        novo = self._colocar(c.pagina, c.escala, *c.x_px, movidos[0][0], movidos[0][1],
                             c.k, dx, 0.0, c.chave)
        self._estender(novo, movidos[1:])
        self.y = novo.destino[3]
        return True

    def _bloco_grande(self, pno, escala, tinta, faixa, a, b, k):
        """Bloco mais alto que a tela: figura grande ou texto sem espaço entre linhas."""
        self.nova_tela()
        altura_px = b - a
        if altura_px * k <= 1.6 * self.H:
            # Reduz para caber inteiro (figuras, capas, tabelas).
            k2 = self.H / altura_px
            dx = self._dx(faixa.x1 - faixa.x0, k2)
            self._colocar(pno, escala, faixa.x0, faixa.x1, a, b, k2, dx, 0.0, None)
            self.y = self.H
            return
        # Muito alto: corta nos pontos com menos tinta (entre linhas, se houver).
        dx = self._dx(faixa.x1 - faixa.x0, k)
        max_px = int(self.H / k)
        tinta_por_linha = tinta[a:b, faixa.x0:faixa.x1].sum(axis=1)
        ini = a
        while ini < b:
            if b - ini <= max_px:
                fim = b
            else:
                j0, j1 = ini + int(max_px * 0.65) - a, ini + max_px - a
                janela = tinta_por_linha[j0:j1]
                fim = a + j0 + (len(janela) - 1 - int(np.argmin(janela[::-1])))
                fim = max(fim, ini + 1)
            if self.atual.colocacoes:
                self.nova_tela()
            self._colocar(pno, escala, faixa.x0, faixa.x1, ini, fim, k, dx, 0.0, None)
            self.y = (fim - ini) * k
            ini = fim

    def pagina_inteira(self, pno: int, escala: float, caixa: tuple[int, int, int, int]):
        x0, x1, y0, y1 = caixa
        k = min(self.Wu / (x1 - x0), self.H / (y1 - y0))
        dx = self._dx(x1 - x0, k)
        dy = (self.H - (y1 - y0) * k) / 2
        self.nova_tela()
        self._colocar(pno, escala, x0, x1, y0, y1, k, dx, dy, None)
        self.escalas.append(k * escala)
        self.y = self.H


# --------------------------------------------------------------------------- #
# Renderização
# --------------------------------------------------------------------------- #
def realcar(arr: np.ndarray, gama: float, realce: bool) -> np.ndarray:
    """Ajusta o recorte para e-ink: fundo branco, preto de verdade, texto mais grosso."""
    a = arr.astype(np.float32)
    if realce:
        fundo = float(np.percentile(a, 60))
        branco = fundo if fundo > 170 else 255.0
        preto = min(float(np.percentile(a, 0.5)), 80.0)
        if branco - preto > 30:
            a = (a - preto) / (branco - preto)
        else:
            a = a / 255.0
        a = np.clip(a, 0.0, 1.0)
    else:
        a = a / 255.0
    if gama and gama != 1.0:
        a = a ** gama
    return (np.round(a * 15) * 17).astype(np.uint8)  # 16 tons de cinza


def renderizar_tela(doc: pymupdf.Document, tela: Tela, opc: Opcoes) -> Image.Image:
    canvas = Image.new("L", (opc.largura, opc.altura), 255)
    for c in tela.colocacoes:
        dx, dy, dw, dh = c.destino
        largura, altura = max(1, round(dw)), max(1, round(dh))
        fator = dw / c.clip.width * SUPERAMOSTRAGEM
        pix = doc[c.pagina].get_pixmap(matrix=pymupdf.Matrix(fator, fator), clip=c.clip,
                                       colorspace=pymupdf.csGRAY, alpha=False)
        img = Image.fromarray(pixmap_para_array(pix).copy())
        img = img.resize((largura, altura), Image.Resampling.LANCZOS)
        img = Image.fromarray(realcar(np.asarray(img), opc.gama, opc.realce))
        canvas.paste(img, (round(dx), round(dy)))
    return canvas


def tamanho_pagina_pt(opc: Opcoes) -> tuple[float, float]:
    return opc.largura * 72 / opc.ppi, opc.altura * 72 / opc.ppi


def gerar_pdf_imagem(doc, telas, opc, destino: Path, ao_progredir):
    saida = pymupdf.open()
    w, h = tamanho_pagina_pt(opc)
    for i, tela in enumerate(telas):
        buf = io.BytesIO()
        renderizar_tela(doc, tela, opc).save(buf, "PNG", optimize=True)
        pagina = saida.new_page(width=w, height=h)
        pagina.insert_image(pagina.rect, stream=buf.getvalue())
        ao_progredir(i + 1)
    return saida


def gerar_pdf_vetorial(doc, telas, opc, destino: Path, ao_progredir):
    saida = pymupdf.open()
    w, h = tamanho_pagina_pt(opc)
    f = 72 / opc.ppi
    for i, tela in enumerate(telas):
        pagina = saida.new_page(width=w, height=h)
        for c in tela.colocacoes:
            dx, dy, dw, dh = c.destino
            alvo = pymupdf.Rect(dx * f, dy * f, (dx + dw) * f, (dy + dh) * f)
            pagina.show_pdf_page(alvo, doc, c.pagina, clip=c.clip)
        ao_progredir(i + 1)
    return saida


def gerar_cbz(doc, telas, opc, destino: Path, ao_progredir):
    with zipfile.ZipFile(destino, "w", zipfile.ZIP_STORED) as z:
        for i, tela in enumerate(telas):
            buf = io.BytesIO()
            renderizar_tela(doc, tela, opc).save(buf, "PNG", optimize=True)
            z.writestr(f"{i + 1:05d}.png", buf.getvalue())
            ao_progredir(i + 1)


# --------------------------------------------------------------------------- #
# Sumário e metadados
# --------------------------------------------------------------------------- #
def localizar_tela(telas: list[Tela], pno: int, ponto: pymupdf.Point | None) -> int | None:
    """Tela onde aparece um ponto (x, y) da página original, se der para achar."""
    if ponto is None:
        return None
    for i, tela in enumerate(telas):
        for c in tela.colocacoes:
            if c.pagina == pno and c.clip.x0 - 5 <= ponto.x <= c.clip.x1 and c.clip.y1 > ponto.y:
                return i
    return None


def copiar_sumario(doc: pymupdf.Document, saida: pymupdf.Document, paginas: list[int],
                   telas: list[Tela], primeira_tela: dict[int, int]):
    sumario = doc.get_toc(simple=False)
    if not sumario:
        return
    # página original -> primeira tela onde ela aparece (páginas vazias apontam para a seguinte)
    destino: dict[int, int] = {}
    proxima = len(telas) - 1
    for pno in reversed(paginas):
        proxima = primeira_tela.get(pno, proxima)
        destino[pno] = proxima
    novo, nivel_anterior = [], 0
    for item in sumario:
        nivel, titulo, pagina = item[:3]
        pno = pagina - 1
        if pno not in destino:
            continue
        # O ponto de destino vem em coordenadas PDF (y cresce para cima).
        ponto = None
        info = item[3] if len(item) > 3 and isinstance(item[3], dict) else {}
        alvo = info.get("to")
        if isinstance(alvo, pymupdf.Point):
            altura = doc[pno].rect.height
            if 0 <= alvo.y <= altura:
                ponto = pymupdf.Point(alvo.x, altura - alvo.y)
        tela = localizar_tela(telas, pno, ponto)
        nivel = max(1, min(nivel, nivel_anterior + 1))
        novo.append([nivel, titulo, (tela if tela is not None else destino[pno]) + 1])
        nivel_anterior = nivel
    try:
        saida.set_toc(novo)
    except Exception as erro:  # sumário malformado não deve impedir a conversão
        print(f"    aviso: não foi possível copiar o sumário ({erro})")


# --------------------------------------------------------------------------- #
# Conversão de um arquivo
# --------------------------------------------------------------------------- #
def converter(caminho: Path, pasta_saida: Path, opc: Opcoes) -> Path:
    inicio = time.time()
    try:
        doc = pymupdf.open(caminho)
    except Exception:
        raise RuntimeError("não foi possível abrir (arquivo corrompido ou não é um PDF)") from None
    if doc.needs_pass:
        raise RuntimeError("o PDF está protegido por senha")
    for pagina in doc:
        if pagina.rotation:
            pagina.remove_rotation()

    paginas = intervalo_paginas(opc.paginas, doc.page_count)
    if not paginas:
        raise RuntimeError("nenhuma página no intervalo pedido")
    print(f"  {len(paginas)} página(s) de {doc.page_count}")

    paginador = Paginador(opc)
    margens = margens_do_documento(doc, paginas, opc) if opc.modo != "pagina" else {}

    for n, pno in enumerate(paginas, 1):
        pagina = doc[pno]
        escala = escala_da_pagina(pagina)
        tinta = mapa_de_tinta(pagina, escala, opc)
        caixa = caixa_de_conteudo(tinta)
        if caixa is None:
            continue  # página em branco
        if opc.modo == "pagina":
            paginador.pagina_inteira(pno, escala, caixa)
            continue
        x0, x1, y0, y1 = caixa
        tipica = margem_da_pagina(margens, pagina)
        if tipica:
            mx0, mx1 = tipica
            x0 = max(0, min(x0, int(mx0 * escala)))
            x1 = min(tinta.shape[1], max(x1, int(mx1 * escala)))
        if opc.nova_tela_por_pagina:
            paginador.nova_tela()
        faixas = segmentar(tinta, x0, x1, y0, y1, detectar_colunas=opc.modo == "auto")
        paginador.adicionar_pagina(pno, escala, tinta, faixas)
        if n % 25 == 0:
            print(f"    analisadas {n}/{len(paginas)} páginas")

    telas = [t for t in paginador.telas if t.colocacoes]
    if not telas:
        raise RuntimeError("não foi encontrado conteúdo visível no PDF")

    # Tamanho do texto em relação ao impresso (1,0 = igual ao papel).
    if paginador.escalas:
        relativo = statistics.median(paginador.escalas) * 72 / opc.ppi
        print(f"  texto no Kindle: ~{relativo:.0%} do tamanho impresso")
        if relativo < 0.7 and opc.modo != "pagina" and opc.largura < opc.altura:
            print("    dica: a letra ficou pequena; experimente --orientacao paisagem")

    pasta_saida.mkdir(parents=True, exist_ok=True)
    extensao = "cbz" if opc.formato == "cbz" else "pdf"
    destino = pasta_saida / f"{caminho.stem}_kindle.{extensao}"

    total = len(telas)

    def ao_progredir(i):
        if i % 50 == 0 or i == total:
            print(f"    telas geradas: {i}/{total}")

    if opc.formato == "cbz":
        gerar_cbz(doc, telas, opc, destino, ao_progredir)
    else:
        gerador = gerar_pdf_vetorial if opc.tipo == "vetorial" else gerar_pdf_imagem
        saida = gerador(doc, telas, opc, destino, ao_progredir)
        copiar_sumario(doc, saida, paginas, telas, paginador.primeira_tela)
        meta = doc.metadata or {}
        saida.set_metadata({
            "title": (meta.get("title") or "").strip() or caminho.stem,
            "author": meta.get("author") or "",
            "subject": meta.get("subject") or "",
            "creator": "kindle-converter",
            "producer": f"PyMuPDF {pymupdf.VersionBind}",
        })
        saida.save(destino, garbage=3, deflate=True)
        saida.close()
    doc.close()

    mb = destino.stat().st_size / 1_048_576
    print(f"  -> {destino} ({total} telas, {mb:.1f} MB, {time.time() - inicio:.0f}s)")
    return destino


# --------------------------------------------------------------------------- #
# Linha de comando
# --------------------------------------------------------------------------- #
def listar_pdfs(entradas: list[str]) -> list[Path]:
    arquivos: list[Path] = []
    for e in entradas:
        p = Path(e)
        if p.is_dir():
            arquivos += sorted(x for x in p.rglob("*") if x.is_file() and x.suffix.lower() == ".pdf")
        elif p.is_file():
            arquivos.append(p)
        else:
            print(f"aviso: '{e}' não encontrado")
    return arquivos


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Converte PDFs para leitura no Kindle mantendo o layout original.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("Exemplos:")[1] if "Exemplos:" in __doc__ else None,
    )
    ap.add_argument("entradas", nargs="+", help="arquivos PDF ou pastas com PDFs")
    ap.add_argument("-o", "--destino", default="output", help="pasta de saída (padrão: output)")
    ap.add_argument("--modelo", choices=MODELOS, default="basico",
                    help="modelo do Kindle (padrão: basico, 600x800)")
    ap.add_argument("--modo", choices=["auto", "largura", "pagina"], default="auto",
                    help="auto: detecta colunas | largura: uma coluna só | "
                         "pagina: página inteira em cada tela (HQs, slides, partituras)")
    ap.add_argument("--orientacao", choices=["retrato", "paisagem"], default="retrato",
                    help="paisagem deixa a letra ~33%% maior (gire o Kindle para ler)")
    ap.add_argument("--tipo", choices=["imagem", "vetorial"], default="imagem",
                    help="imagem: mais nítido no e-ink | vetorial: mantém o texto pesquisável")
    ap.add_argument("--formato", choices=["pdf", "cbz"], default="pdf",
                    help="cbz: para usar no Kindle Comic Converter (KCC)")
    ap.add_argument("--gama", type=float, default=1.6,
                    help="escurece o texto fino; 1.0 desliga (padrão: 1.6)")
    ap.add_argument("--sem-realce", action="store_true",
                    help="não ajusta contraste nem fundo (fotos, PDFs coloridos)")
    ap.add_argument("--ignorar-topo", type=float, default=0, metavar="MM",
                    help="descarta N mm do topo de cada página (cabeçalho)")
    ap.add_argument("--ignorar-base", type=float, default=0, metavar="MM",
                    help="descarta N mm da base de cada página (rodapé, nº de página)")
    ap.add_argument("--nova-tela-por-pagina", action="store_true",
                    help="cada página original começa em uma tela nova")
    ap.add_argument("--paginas", help="intervalo, ex.: 1-20 ou 3,5,10-12")
    args = ap.parse_args(argv)

    if args.formato == "cbz" and args.tipo == "vetorial":
        ap.error("o formato cbz só funciona com --tipo imagem")

    m = MODELOS[args.modelo]
    largura, altura = m["largura"], m["altura"]
    if args.orientacao == "paisagem":
        largura, altura = altura, largura
    opc = Opcoes(largura=largura, altura=altura, ppi=m["ppi"], modo=args.modo, tipo=args.tipo,
                 formato=args.formato, gama=args.gama, realce=not args.sem_realce,
                 ignorar_topo=args.ignorar_topo * MM, ignorar_base=args.ignorar_base * MM,
                 nova_tela_por_pagina=args.nova_tela_por_pagina, paginas=args.paginas)

    arquivos = listar_pdfs(args.entradas)
    if not arquivos:
        print("Nenhum PDF encontrado.")
        return 0

    print(f"{m['nome']}: telas de {largura}x{altura}, modo {args.modo}, tipo {args.tipo}")
    falhas = 0
    for arq in arquivos:
        print(f"\n{arq}")
        try:
            converter(arq, Path(args.destino), opc)
        except Exception as erro:
            falhas += 1
            print(f"  ERRO: {erro}")
    print(f"\nConcluído: {len(arquivos) - falhas} convertido(s), {falhas} com erro.")
    return 1 if falhas else 0


if __name__ == "__main__":
    sys.exit(main())
