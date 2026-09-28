#!/usr/bin/env python3
"""
Teste de ponta a ponta (verificação dinâmica) do conversor já empacotado.

Roda o conversor como o usuário roda (pela imagem Docker, em homologação e em
produção) sobre um corpus de PDFs gerado na hora e confere o resultado:

  * funcional: telas no tamanho certo, em cinza, sumário mantido;
  * conteúdo: no modo vetorial, todas as palavras chegam, na ordem de leitura;
  * robustez: PDFs corrompidos, protegidos e vazios dão erro claro, sem travar o lote;
  * desempenho: um livro de 100 páginas converte dentro do tempo limite e cabe no e-mail;
  * versão: a imagem responde com a versão esperada.

Uso:
    # contra a imagem (como na pipeline)
    python scripts/e2e.py --pasta e2e --prefixo /dados \\
        --conversor "docker run --rm --network none --user 1001:1001 -v $PWD/e2e:/dados IMAGEM"

    # contra o código local
    python scripts/e2e.py --pasta e2e --conversor "python convert.py"

Gera um resumo em Markdown (também no resumo do job, se rodar no GitHub Actions).
Sai com código 1 se alguma verificação falhar.
"""

from __future__ import annotations

import argparse
import os
import shlex

# O script executa o conversor que está sendo testado (comando dado pelo operador).
import subprocess  # nosec B404
import sys
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "tests"))
import fabrica_pdf as fabrica  # noqa: E402

LIMITE_EMAIL_MB = 50


@dataclass
class Resultado:
    nome: str
    ok: bool = True
    detalhes: list[str] = field(default_factory=list)
    segundos: float = 0.0

    def falha(self, motivo: str):
        self.ok = False
        self.detalhes.append(motivo)


class Teste:
    def __init__(self, conversor: str, pasta: Path, prefixo: str | None):
        self.comando = shlex.split(conversor)
        self.pasta = pasta.resolve()
        self.prefixo = prefixo or str(self.pasta)  # caminho da pasta visto pelo conversor
        self.resultados: list[Resultado] = []

    def caminho(self, local: Path) -> str:
        return f"{self.prefixo}/{local.resolve().relative_to(self.pasta).as_posix()}"

    def rodar(self, *args: str | Path) -> tuple[int, str, float]:
        convertidos = [self.caminho(a) if isinstance(a, Path) else a for a in args]
        inicio = time.monotonic()
        proc = subprocess.run(  # noqa: S603  # nosec B603
            [*self.comando, *convertidos], capture_output=True, text=True, timeout=1800, check=False
        )
        return proc.returncode, proc.stdout + proc.stderr, time.monotonic() - inicio

    def cenario(self, nome: str) -> Resultado:
        r = Resultado(nome)
        self.resultados.append(r)
        return r

    # ------------------------------------------------------------------ #
    def checar_telas(self, r: Resultado, pdf: Path, tamanho: tuple[int, int]):
        if not pdf.exists():
            r.falha(f"{pdf.name} não foi gerado")
            return
        doc = pymupdf.open(pdf)
        if doc.page_count == 0:
            r.falha("PDF sem páginas")
        for pagina in doc:
            imagens = pagina.get_images()
            if not imagens:
                r.falha(f"tela {pagina.number + 1} sem imagem")
                break
            pix = pymupdf.Pixmap(doc, imagens[0][0])
            if (pix.width, pix.height) != tamanho or pix.n != 1:
                r.falha(f"tela {pagina.number + 1}: {pix.width}x{pix.height}, {pix.n} canais")
                break
        mb = pdf.stat().st_size / 1_048_576
        if mb > LIMITE_EMAIL_MB:
            r.falha(f"{mb:.1f} MB passa do limite do e-mail")
        r.detalhes.append(f"{doc.page_count} telas, {mb:.1f} MB")

    def checar_texto(self, r: Resultado, original: Path, convertido: Path):
        esperado = [n for p in pymupdf.open(original) for n in fabrica.numeros(p.get_text())]
        obtido = [n for p in pymupdf.open(convertido) for n in fabrica.numeros(p.get_text())]
        if obtido != esperado:
            faltando = len(set(esperado) - set(obtido))
            em_ordem = obtido == sorted(obtido)
            r.falha(f"texto diferente do original: {faltando} palavras faltando, em ordem: {em_ordem}")
        else:
            r.detalhes.append(f"{len(obtido)} palavras, na ordem")

    # ------------------------------------------------------------------ #
    def executar(self, versao: str | None, limite_segundos: float):
        entrada = self.pasta / "entrada"
        saida = self.pasta / "saida"
        print("Gerando corpus de teste...")
        corpus = fabrica.gerar_corpus(entrada)
        ruins = self.pasta / "ruins"
        ruins.mkdir(parents=True, exist_ok=True)
        fabrica.corrompido(ruins / "corrompido.pdf")
        fabrica.protegido(ruins / "protegido.pdf")
        fabrica.em_branco(ruins / "em_branco.pdf")

        if versao:
            r = self.cenario("Versão da imagem")
            codigo, log, _ = self.rodar("--version")
            if codigo != 0 or versao not in log:
                r.falha(f"esperado {versao}, recebido: {log.strip()!r}")
            else:
                r.detalhes.append(log.strip())

        r = self.cenario("Conversão padrão (imagem, retrato)")
        destino = saida / "padrao"
        codigo, log, r.segundos = self.rodar(
            corpus["livro"], corpus["artigo"], corpus["escaneado"], "-o", destino
        )
        if codigo != 0:
            r.falha(f"código de saída {codigo}: {log[-500:]}")
        for nome in ("livro_uma_coluna", "artigo_duas_colunas", "pagina_escaneada"):
            self.checar_telas(r, destino / f"{nome}_kindle.pdf", (600, 800))
        livro = destino / "livro_uma_coluna_kindle.pdf"
        if livro.exists():
            titulos = [t[1] for t in pymupdf.open(livro).get_toc()]
            if titulos != [t[1] for t in pymupdf.open(corpus["livro"]).get_toc()]:
                r.falha(f"sumário diferente: {titulos}")

        r = self.cenario("Conteúdo e ordem de leitura (vetorial)")
        destino = saida / "vetorial"
        codigo, log, r.segundos = self.rodar(
            corpus["livro"], corpus["artigo"], "-o", destino, "--tipo", "vetorial"
        )
        if codigo != 0:
            r.falha(f"código de saída {codigo}: {log[-500:]}")
        else:
            self.checar_texto(r, corpus["livro"], destino / "livro_uma_coluna_kindle.pdf")
            self.checar_texto(r, corpus["artigo"], destino / "artigo_duas_colunas_kindle.pdf")

        r = self.cenario("Paisagem")
        destino = saida / "paisagem"
        codigo, log, r.segundos = self.rodar(corpus["livro"], "-o", destino, "--orientacao", "paisagem")
        if codigo != 0:
            r.falha(f"código de saída {codigo}")
        self.checar_telas(r, destino / "livro_uma_coluna_kindle.pdf", (800, 600))

        r = self.cenario("Formato CBZ")
        destino = saida / "cbz"
        codigo, log, r.segundos = self.rodar(corpus["artigo"], "-o", destino, "--formato", "cbz")
        cbz = destino / "artigo_duas_colunas_kindle.cbz"
        if codigo != 0 or not cbz.exists():
            r.falha(f"código de saída {codigo}")
        else:
            with zipfile.ZipFile(cbz) as z:
                r.detalhes.append(f"{len(z.namelist())} imagens")

        r = self.cenario("Robustez: arquivos inválidos no meio do lote")
        destino = saida / "robustez"
        codigo, log, r.segundos = self.rodar(
            ruins / "corrompido.pdf",
            ruins / "protegido.pdf",
            ruins / "em_branco.pdf",
            corpus["artigo"],
            "-o",
            destino,
        )
        if codigo != 1:
            r.falha(f"esperado código 1, recebido {codigo}")
        if "Traceback" in log:
            r.falha("erro não tratado (traceback) na saída")
        if log.count("ERRO:") != 3:
            r.falha(f"esperados 3 erros explicados, encontrados {log.count('ERRO:')}")
        if not (destino / "artigo_duas_colunas_kindle.pdf").exists():
            r.falha("o PDF válido do lote não foi convertido")

        r = self.cenario("Desempenho: livro de 100 páginas")
        destino = saida / "desempenho"
        codigo, log, r.segundos = self.rodar(corpus["grande"], "-o", destino)
        if codigo != 0:
            r.falha(f"código de saída {codigo}")
        if r.segundos > limite_segundos:
            r.falha(f"{r.segundos:.0f}s passa do limite de {limite_segundos:.0f}s")
        self.checar_telas(r, destino / "livro_grande_kindle.pdf", (600, 800))
        r.detalhes.append(f"{r.segundos / 100:.2f}s por página")

    def resumo(self) -> str:
        linhas = [
            "## Teste de ponta a ponta",
            "",
            "| Cenário | Resultado | Tempo | Detalhes |",
            "|---|---|---|---|",
        ]
        for r in self.resultados:
            status = "✅ ok" if r.ok else "❌ falhou"
            detalhes = "; ".join(r.detalhes).replace("|", "/")
            linhas.append(f"| {r.nome} | {status} | {r.segundos:.1f}s | {detalhes} |")
        return "\n".join(linhas) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--conversor", required=True, help="comando que executa o conversor")
    ap.add_argument("--pasta", type=Path, default=Path("e2e"), help="pasta de trabalho")
    ap.add_argument("--prefixo", help="caminho da pasta dentro do contêiner (ex.: /dados)")
    ap.add_argument("--versao", help="versão esperada em --version")
    ap.add_argument("--limite-segundos", type=float, default=240, help="tempo máximo do livro de 100 páginas")
    args = ap.parse_args()

    teste = Teste(args.conversor, args.pasta, args.prefixo)
    teste.pasta.mkdir(parents=True, exist_ok=True)
    teste.executar(args.versao, args.limite_segundos)
    resumo = teste.resumo()
    print(resumo)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(resumo)
    return 0 if all(r.ok for r in teste.resultados) else 1


if __name__ == "__main__":
    sys.exit(main())
