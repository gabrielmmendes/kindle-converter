"""Testes unitários das funções de análise de layout e de imagem."""

from __future__ import annotations

import dataclasses

import numpy as np
import pytest

import convert as c


# --------------------------------------------------------------------------- #
# Utilitários
# --------------------------------------------------------------------------- #
def test_trechos_encontra_sequencias():
    m = np.array([0, 1, 1, 0, 0, 1, 0, 1, 1, 1], dtype=bool)
    assert c.trechos(m) == [(1, 3), (5, 6), (7, 10)]


def test_trechos_vazio():
    assert c.trechos(np.zeros(5, dtype=bool)) == []


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        (None, [0, 1, 2, 3, 4, 5, 6, 7, 8, 9]),
        ("1-3", [0, 1, 2]),
        ("2,5,7-8", [1, 4, 6, 7]),
        ("-2", [0, 1]),
        ("9-", [8, 9]),
        ("8-50", [7, 8, 9]),  # além do fim é ignorado
        ("0", []),  # página 0 não existe
        (" 3 , 3 ", [2]),  # espaços e repetição
    ],
)
def test_intervalo_paginas(texto, esperado):
    assert c.intervalo_paginas(texto, 10) == esperado


def test_caixa_de_conteudo_pagina_vazia():
    assert c.caixa_de_conteudo(np.zeros((50, 40), dtype=bool)) is None


def test_caixa_de_conteudo():
    t = np.zeros((100, 80), dtype=bool)
    t[10:20, 5:60] = True
    t[70:75, 30:70] = True
    assert c.caixa_de_conteudo(t) == (5, 70, 10, 75)


# --------------------------------------------------------------------------- #
# Linhas e fragmentos
# --------------------------------------------------------------------------- #
def test_fundir_fragmentos_junta_acento_com_a_linha():
    # linhas de 20 px com 6 px de espaço; um acento de 3 px logo acima da 3ª linha
    blocos = [(0, 20), (26, 46), (50, 53), (55, 75), (81, 101)]
    assert c.fundir_fragmentos(blocos) == [(0, 20), (26, 46), (50, 75), (81, 101)]


def test_fundir_fragmentos_mantem_linhas_normais():
    blocos = [(0, 20), (26, 46), (52, 72)]
    assert c.fundir_fragmentos(blocos) == blocos


# --------------------------------------------------------------------------- #
# Detecção de colunas
# --------------------------------------------------------------------------- #
def _pagina_sintetica(
    colunas: list[tuple[int, int]],
    linhas: int = 40,
    largura: int = 1000,
    altura: int = 1400,
    irregular: bool = True,
):
    """Mapa de tinta com linhas de "texto" (blocos pretos) nas colunas dadas."""
    t = np.zeros((altura, largura), dtype=bool)
    rng = np.random.default_rng(1)
    for i in range(linhas):
        y = 50 + i * 30
        for x0, x1 in colunas:
            fim = x1 if (not irregular or i % 7) else x0 + (x1 - x0) // 2  # fim de parágrafo
            x = x0
            while x < fim:  # palavras de tamanhos variados
                w = int(rng.integers(20, 60))
                t[y : y + 18, x : min(x + w, fim)] = True
                x += w + int(rng.integers(6, 12))
    return t


def test_detecta_duas_colunas():
    t = _pagina_sintetica([(50, 480), (520, 950)])
    calha = c.detectar_calha(t, 50, 950, 50, 1250)
    assert calha is not None
    a, b = calha
    assert 470 <= a <= 490 and 510 <= b <= 530


def test_uma_coluna_nao_tem_calha():
    t = _pagina_sintetica([(50, 950)])
    assert c.detectar_calha(t, 50, 950, 50, 1250) is None


def test_sumario_nao_vira_duas_colunas():
    # títulos curtos à esquerda e números de página alinhados à direita
    t = np.zeros((1400, 1000), dtype=bool)
    for i in range(20):
        y = 50 + i * 40
        t[y : y + 18, 50 : 50 + 100 + (i * 37) % 250] = True
        t[y : y + 18, 920:950] = True
    assert c.detectar_calha(t, 50, 950, 50, 900) is None


def test_segmentar_ordem_de_leitura_com_titulo():
    t = _pagina_sintetica([(50, 480), (520, 950)])
    t[5:35, 100:900] = True  # título de largura total no topo
    faixas = c.segmentar(t, 50, 950, 0, 1300, detectar_colunas=True)
    assert [(f.x0, f.x1) for f in faixas][:1] == [(50, 950)]  # título primeiro
    assert faixas[1].x1 <= 500 and faixas[2].x0 >= 500  # depois esquerda, direita


def test_segmentar_sem_deteccao_de_colunas():
    t = _pagina_sintetica([(50, 480), (520, 950)])
    faixas = c.segmentar(t, 50, 950, 0, 1300, detectar_colunas=False)
    assert len(faixas) == 1


# --------------------------------------------------------------------------- #
# Otimização para e-ink
# --------------------------------------------------------------------------- #
def test_realce_usa_16_tons():
    arr = np.linspace(0, 255, 256 * 10).astype(np.uint8).reshape(10, 256)
    saida = c.realcar(arr, gama=1.6, realce=True)
    assert set(np.unique(saida)) <= set(range(0, 256, 17))


def test_realce_clareia_fundo_cinza_de_scanner():
    arr = np.full((100, 100), 225, dtype=np.uint8)
    arr[40:60, 10:90] = 40  # texto
    saida = c.realcar(arr, gama=1.6, realce=True)
    assert saida[0, 0] == 255 and saida[50, 50] == 0


def test_sem_realce_preserva_fundo():
    arr = np.full((10, 10), 204, dtype=np.uint8)
    saida = c.realcar(arr, gama=1.0, realce=False)
    assert np.all(saida == 204)


# --------------------------------------------------------------------------- #
# Paginação
# --------------------------------------------------------------------------- #
def _opcoes(**extra) -> c.Opcoes:
    base = c.Opcoes(
        largura=600,
        altura=800,
        ppi=167,
        modo="auto",
        tipo="imagem",
        formato="pdf",
        gama=1.6,
        realce=True,
        ignorar_topo=0,
        ignorar_base=0,
        nova_tela_por_pagina=False,
        paginas=None,
    )
    return dataclasses.replace(base, **extra)


def test_paginador_nao_corta_linhas_e_respeita_a_tela():
    t = _pagina_sintetica([(50, 950)], linhas=40)
    faixas = c.segmentar(t, 50, 950, 0, 1300, detectar_colunas=True)
    pag = c.Paginador(_opcoes())
    pag.adicionar_pagina(0, 2.0, t, faixas)
    colocadas = [b for tela in pag.telas for col in tela.colocacoes for b in col.blocos]
    assert colocadas == faixas[0].blocos  # todas as linhas, em ordem
    for tela in pag.telas:
        for col in tela.colocacoes:
            _, y, _, h = col.destino
            assert y >= 0 and y + h <= 800 + 1  # nada passa da tela


def test_paginador_leva_titulo_orfao_para_a_proxima_tela():
    # 31 linhas enchem a tela; a 32ª é um título (espaço grande antes) seguido de texto
    t = np.zeros((1600, 1000), dtype=bool)
    y = 0
    blocos = []
    for i in range(45):
        y += 60 if i == 31 else 8
        t[y : y + 18, 50:950] = True
        blocos.append((y, y + 18))
        y += 18
    faixa = c.Faixa(50, 950, blocos[0][0], blocos[-1][1], blocos)
    pag = c.Paginador(_opcoes())
    pag.adicionar_pagina(0, 2.0, t, [faixa])
    titulo = blocos[31]
    telas_com_titulo = [
        i for i, tela in enumerate(pag.telas) for col in tela.colocacoes if titulo in col.blocos
    ]
    tela = pag.telas[telas_com_titulo[0]]
    # o título não pode ser a última linha da tela
    assert tela.colocacoes[-1].blocos[-1] != titulo


def test_figura_um_pouco_maior_que_a_tela_e_reduzida_inteira():
    t = np.zeros((1600, 1000), dtype=bool)
    t[0:1500, 50:950] = True  # figura sem espaços em branco
    faixa = c.Faixa(50, 950, 0, 1500, [(0, 1500)])
    pag = c.Paginador(_opcoes())
    pag.adicionar_pagina(0, 2.0, t, [faixa])
    telas = [tela for tela in pag.telas if tela.colocacoes]
    assert len(telas) == 1
    _, y, w, h = telas[0].colocacoes[0].destino
    assert h <= 800 + 0.5 and w < 600  # coube inteira, mais estreita


def test_bloco_muito_alto_e_dividido_sem_perder_linhas():
    t = np.zeros((4000, 1000), dtype=bool)
    t[0:3900, 50:950] = True
    t[::25, 50:900] = False  # "entrelinhas" com menos tinta
    faixa = c.Faixa(50, 950, 0, 3900, [(0, 3900)])
    pag = c.Paginador(_opcoes())
    pag.adicionar_pagina(0, 2.0, t, [faixa])
    pedacos = [col for tela in pag.telas for col in tela.colocacoes]
    assert len(pedacos) >= 3
    assert pedacos[0].blocos[0][0] == 0 and pedacos[-1].blocos[-1][1] == 3900
    for a, b in zip(pedacos, pedacos[1:], strict=False):
        assert a.blocos[-1][1] == b.blocos[0][0]  # pedaços contíguos
    assert all(col.destino[3] <= 800 + 0.5 for col in pedacos)


def test_titulo_centralizado_acima_de_tres_colunas():
    t = _pagina_sintetica([(50, 330), (360, 640), (670, 950)])
    t[0:40, 420:580] = True  # título curto sobre a coluna do meio
    faixas = c.segmentar(t, 50, 950, 0, 1300, detectar_colunas=True)
    assert faixas[0].y1 <= 45  # o título vem primeiro
    assert len(faixas) == 4  # título + 3 colunas
    assert [f.x0 for f in faixas[1:]] == sorted(f.x0 for f in faixas[1:])
