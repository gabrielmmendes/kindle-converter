# syntax=docker/dockerfile:1
#
# Imagem do conversor. É esta imagem que a pipeline testa em homologação e
# promove para produção; o workflow de conversão (convert.yml) roda a imagem
# marcada como "producao".
#
# Uso:
#   docker run --rm --user "$(id -u):$(id -g)" -v "$PWD:/dados" IMAGEM livro.pdf -o saida
#   docker run --rm --entrypoint python -e KINDLE_EMAIL=... IMAGEM /app/send_to_kindle.py saida

ARG PYTHON_IMAGE=python:3.13-slim
FROM ${PYTHON_IMAGE}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependências primeiro: a camada fica em cache enquanto requirements.txt não mudar.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY convert.py send_to_kindle.py ./

# Nunca roda como root.
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin conversor
USER 10001

WORKDIR /dados
ENTRYPOINT ["python", "/app/convert.py"]
CMD ["--help"]
