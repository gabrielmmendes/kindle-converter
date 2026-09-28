#!/usr/bin/env python3
"""
send_to_kindle.py: envia os PDFs convertidos para o e-mail do Kindle (Send to Kindle).

Configuração por variáveis de ambiente (no GitHub, em Settings > Secrets):
    KINDLE_EMAIL   endereço do Kindle, ex.: seunome_abc123@kindle.com   (obrigatório)
    SMTP_USER      usuário do e-mail que envia, ex.: voce@gmail.com      (obrigatório)
    SMTP_PASSWORD  senha de app desse e-mail                             (obrigatório)
    SMTP_HOST      servidor SMTP (padrão: smtp.gmail.com)
    SMTP_PORT      porta SMTP (padrão: 587; use 465 para SSL direto)
    SMTP_FROM      remetente, se for diferente de SMTP_USER

O remetente precisa estar na "Lista de e-mails aprovados" da sua conta Amazon.

Uso:
    python send_to_kindle.py output/
"""

from __future__ import annotations

import os
import smtplib
import ssl
import sys
from email.message import EmailMessage
from pathlib import Path

LIMITE_MB = 50  # limite de anexo do Send to Kindle por e-mail


def main() -> int:
    pasta = Path(sys.argv[1] if len(sys.argv) > 1 else "output")
    destino = os.environ.get("KINDLE_EMAIL", "").strip()
    usuario = os.environ.get("SMTP_USER", "").strip()
    senha = os.environ.get("SMTP_PASSWORD", "").strip()
    host = os.environ.get("SMTP_HOST", "").strip() or "smtp.gmail.com"
    porta = int(os.environ.get("SMTP_PORT", "").strip() or 587)
    remetente = os.environ.get("SMTP_FROM", "").strip() or usuario

    if not (destino and usuario and senha):
        print("Envio por e-mail não configurado (faltam KINDLE_EMAIL, SMTP_USER ou SMTP_PASSWORD).")
        print("Os arquivos continuam disponíveis para download na execução do workflow.")
        return 0

    arquivos = sorted(p for p in pasta.glob("*") if p.suffix.lower() == ".pdf")
    if not arquivos:
        print(f"Nenhum PDF em {pasta}/ para enviar.")
        return 0

    contexto = ssl.create_default_context()
    smtp: smtplib.SMTP
    if porta == 465:
        smtp = smtplib.SMTP_SSL(host, porta, context=contexto, timeout=120)
    else:
        smtp = smtplib.SMTP(host, porta, timeout=120)
        smtp.starttls(context=contexto)

    falhas = 0
    with smtp:
        smtp.login(usuario, senha)
        for arq in arquivos:
            mb = arq.stat().st_size / 1_048_576
            if mb > LIMITE_MB:
                print(
                    f"::warning::{arq.name} tem {mb:.0f} MB (limite do Kindle por e-mail: "
                    f"{LIMITE_MB} MB). Baixe pelo artifact e copie por USB, ou converta "
                    f"em partes com --paginas."
                )
                falhas += 1
                continue
            msg = EmailMessage()
            # Não use a palavra "convert" no assunto: a Amazon converteria o PDF
            # para o formato do Kindle e o layout se perderia.
            msg["Subject"] = f"Documento {arq.stem}"
            msg["From"] = remetente
            msg["To"] = destino
            msg.set_content("Enviado automaticamente pelo kindle-converter.")
            msg.add_attachment(arq.read_bytes(), maintype="application", subtype="pdf", filename=arq.name)
            try:
                smtp.send_message(msg)
                print(f"Enviado: {arq.name} ({mb:.1f} MB)")
            except smtplib.SMTPException as erro:
                print(f"::error::Falha ao enviar {arq.name}: {erro}")
                falhas += 1
    return 1 if falhas else 0


if __name__ == "__main__":
    sys.exit(main())
