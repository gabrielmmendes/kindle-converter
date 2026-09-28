"""Testes do envio por e-mail, com um servidor SMTP falso."""

from __future__ import annotations

import smtplib

import pytest

import send_to_kindle as envio


class SMTPFalso:
    instancias: list[SMTPFalso] = []

    def __init__(self, host, porta, **kwargs):
        self.host, self.porta = host, porta
        self.tls = False
        self.login_feito = None
        self.mensagens = []
        SMTPFalso.instancias.append(self)

    def starttls(self, **kwargs):
        self.tls = True

    def login(self, usuario, senha):
        self.login_feito = (usuario, senha)

    def send_message(self, msg):
        if "falha" in msg["Subject"]:
            raise smtplib.SMTPDataError(554, b"rejeitado")
        self.mensagens.append(msg)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


@pytest.fixture
def smtp(monkeypatch):
    SMTPFalso.instancias = []
    monkeypatch.setattr(smtplib, "SMTP", SMTPFalso)
    monkeypatch.setattr(smtplib, "SMTP_SSL", SMTPFalso)
    for var in ("KINDLE_EMAIL", "SMTP_USER", "SMTP_PASSWORD", "SMTP_HOST", "SMTP_PORT", "SMTP_FROM"):
        monkeypatch.delenv(var, raising=False)
    return SMTPFalso


@pytest.fixture
def configurado(monkeypatch):
    monkeypatch.setenv("KINDLE_EMAIL", "leitor_123@kindle.com")
    monkeypatch.setenv("SMTP_USER", "remetente@gmail.com")
    monkeypatch.setenv("SMTP_PASSWORD", "senha-de-app")


def test_sem_configuracao_nao_envia_e_nao_falha(smtp, tmp_path, monkeypatch, capsys):
    (tmp_path / "a.pdf").write_bytes(b"%PDF-1.7")
    monkeypatch.setattr("sys.argv", ["send_to_kindle.py", str(tmp_path)])
    assert envio.main() == 0
    assert smtp.instancias == []
    assert "não configurado" in capsys.readouterr().out


def test_envia_um_email_por_pdf(smtp, configurado, tmp_path, monkeypatch):
    (tmp_path / "Ação_kindle.pdf").write_bytes(b"%PDF-1.7 um")
    (tmp_path / "b_kindle.pdf").write_bytes(b"%PDF-1.7 dois")
    (tmp_path / "c.cbz").write_bytes(b"PK")
    monkeypatch.setattr("sys.argv", ["send_to_kindle.py", str(tmp_path)])
    assert envio.main() == 0
    servidor = smtp.instancias[0]
    assert (servidor.host, servidor.porta, servidor.tls) == ("smtp.gmail.com", 587, True)
    assert servidor.login_feito == ("remetente@gmail.com", "senha-de-app")
    assert len(servidor.mensagens) == 2
    for msg in servidor.mensagens:
        assert msg["To"] == "leitor_123@kindle.com"
        assert "convert" not in msg["Subject"].lower()  # evitaria perder o layout
        anexo = next(msg.iter_attachments())
        assert anexo.get_content_type() == "application/pdf"
    nomes = {next(m.iter_attachments()).get_filename() for m in servidor.mensagens}
    assert nomes == {"Ação_kindle.pdf", "b_kindle.pdf"}


def test_porta_465_usa_ssl_direto(smtp, configurado, tmp_path, monkeypatch):
    monkeypatch.setenv("SMTP_PORT", "465")
    monkeypatch.setenv("SMTP_HOST", "smtp.exemplo.com")
    (tmp_path / "a.pdf").write_bytes(b"%PDF")
    monkeypatch.setattr("sys.argv", ["send_to_kindle.py", str(tmp_path)])
    assert envio.main() == 0
    assert smtp.instancias[0].porta == 465 and smtp.instancias[0].tls is False


def test_arquivo_grande_demais_e_pulado(smtp, configurado, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(envio, "LIMITE_MB", 0.00001)
    (tmp_path / "a.pdf").write_bytes(b"%PDF" + b"0" * 100)
    monkeypatch.setattr("sys.argv", ["send_to_kindle.py", str(tmp_path)])
    assert envio.main() == 1
    assert smtp.instancias[0].mensagens == []
    assert "limite" in capsys.readouterr().out


def test_falha_de_envio_marca_erro(smtp, configurado, tmp_path, monkeypatch):
    (tmp_path / "falha.pdf").write_bytes(b"%PDF")
    (tmp_path / "ok.pdf").write_bytes(b"%PDF")
    monkeypatch.setattr("sys.argv", ["send_to_kindle.py", str(tmp_path)])
    assert envio.main() == 1
    assert len(smtp.instancias[0].mensagens) == 1


def test_pasta_sem_pdfs(smtp, configurado, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["send_to_kindle.py", str(tmp_path)])
    assert envio.main() == 0
    assert "Nenhum PDF" in capsys.readouterr().out
