# kindle-converter

Converte PDFs para leitura no **Kindle básico de 10ª geração** (tela de 6", 600×800) mantendo o layout original: fontes, fórmulas, tabelas e figuras continuam iguais. A conversão roda no **GitHub Actions**: você coloca o PDF no repositório e recebe a versão pronta no Kindle por e-mail, ou baixa o arquivo.

## O que o conversor faz

Uma página A4 inteira numa tela de 6" fica com a letra minúscula. Em vez de mudar o conteúdo, o conversor re-diagrama a página para a tela:

1. **Corta as margens** em branco.
2. **Detecta colunas** (artigos em 2 ou 3 colunas) e segue a ordem de leitura certa, inclusive com título e figuras de largura total.
3. **Amplia cada coluna** até a largura da tela.
4. **Distribui as linhas em telas cheias**, sem cortar nenhuma linha ao meio e sem deixar título sozinho no pé da tela.
5. **Otimiza para e-ink**: fundo branco, preto de verdade, texto fino um pouco mais grosso e 16 tons de cinza, que é o que a tela mostra.
6. **Mantém o sumário** (os marcadores apontam para a tela certa) e o título do documento.

O resultado é um PDF comum, que o Kindle abre direto.

---

## Configuração (uma vez só)

### 1. Crie o repositório

1. No GitHub, crie um repositório **privado**. Num repositório público, qualquer pessoa veria os seus PDFs.
2. Envie todos os arquivos deste projeto, incluindo a pasta `.github/`. Pelo navegador: **Add file → Upload files** e arraste a pasta inteira. Se a pasta `.github` não subir (ela é oculta em alguns sistemas), crie o arquivo `.github/workflows/convert.yml` com **Add file → Create new file** e cole o conteúdo.

### 2. Descubra o e-mail do seu Kindle e autorize o remetente

Na Amazon: **Conta → Gerenciar conteúdo e dispositivos → Preferências → Configurações de documentos pessoais**.

- Anote o **e-mail do Kindle**, algo como `seunome_abc123@kindle.com`.
- Em **Lista de e-mails aprovados para documentos pessoais**, adicione o e-mail que vai enviar os arquivos. Sem isso, a Amazon descarta o documento sem avisar.

### 3. Crie uma senha de app no Gmail

O GitHub precisa de uma senha própria para enviar e-mail pela sua conta. A sua senha normal não funciona.

1. A conta Google precisa ter a verificação em duas etapas ativada.
2. Acesse <https://myaccount.google.com/apppasswords>, crie uma senha com o nome "Kindle" e copie os 16 caracteres.

Se preferir não usar a sua conta principal, crie um Gmail só para isso.

### 4. Cadastre os segredos no repositório

No repositório: **Settings → Secrets and variables → Actions → New repository secret**.

| Nome | Valor |
|---|---|
| `KINDLE_EMAIL` | o e-mail do Kindle (`...@kindle.com`) |
| `SMTP_USER` | o Gmail que envia (`voce@gmail.com`) |
| `SMTP_PASSWORD` | a senha de app de 16 caracteres |

Opcionais, para quem não usa Gmail: `SMTP_HOST`, `SMTP_PORT` (587 ou 465) e `SMTP_FROM`.

Sem esses segredos o workflow continua funcionando. Só não envia o e-mail, e você baixa o arquivo pela página da execução.

---

## Como usar

### Opção A: enviar o PDF para a pasta `input/`

1. No repositório, abra a pasta `input/` → **Add file → Upload files** → arraste o PDF → **Commit changes**.
2. A conversão começa sozinha. Acompanhe na aba **Actions**.
3. Em alguns minutos o arquivo chega ao Kindle (com o Wi-Fi ligado).

Depois de convertido, o PDF original é apagado de `input/` para o repositório não crescer. O histórico do Git ainda guarda o arquivo.

### Opção B: converter a partir de um link

Aba **Actions → Converter PDF para Kindle → Run workflow**. Preencha o link (precisa ser de download direto, que abre o PDF em si) e escolha as opções. Links do Google Drive e do Dropbox em geral abrem uma página, não o PDF, e não funcionam.

### Baixar sem e-mail

Em cada execução, na aba **Actions**, role até **Artifacts** e baixe o `kindle-N`. Depois copie o PDF para a pasta `documents` do Kindle pelo cabo USB. O arquivo fica disponível por 30 dias.

---

## Opções

As mesmas opções valem no botão **Run workflow** e na linha de comando.

| Opção | Valores | Para que serve |
|---|---|---|
| `--modo` | `auto` (padrão) | Detecta 1, 2 ou 3 colunas. Serve para quase tudo. |
| | `largura` | Não procura colunas. Use se o `auto` dividir errado uma tabela ou um formulário. |
| | `pagina` | Uma página inteira por tela, sem reorganizar. Para quadrinhos, slides, partituras e plantas. |
| `--orientacao` | `retrato` (padrão) / `paisagem` | Paisagem deixa a letra cerca de 33% maior. No Kindle, ative a orientação paisagem no menu **Aa** ao abrir o PDF. |
| `--tipo` | `imagem` (padrão) | Texto mais nítido e escuro no e-ink. Não dá para pesquisar nem usar o dicionário. |
| | `vetorial` | Mantém o texto original: dá para pesquisar, e o arquivo fica bem menor. Sem os ajustes de contraste. |
| `--ignorar-topo N` | milímetros | Descarta o cabeçalho de cada página (ex.: título do capítulo repetido). |
| `--ignorar-base N` | milímetros | Descarta o rodapé (ex.: número da página). |
| `--paginas` | `1-50`, `3,5,10-12` | Converte só uma parte. Bom para testar ou dividir livros grandes. |
| `--nova-tela-por-pagina` | | Cada página original começa numa tela nova. |
| `--gama` | `1.6` (padrão) | Quanto escurecer o texto fino. `1.0` desliga. |
| `--sem-realce` | | Não mexe no contraste (fotos, material com muita imagem). |
| `--formato cbz` | | Gera CBZ para usar no [Kindle Comic Converter](https://github.com/ciromattia/kcc). |
| `--modelo paperwhite` | | Para o Paperwhite de 10ª geração (1072×1448). |

**Configuração padrão para os envios automáticos (opção A):** em **Settings → Secrets and variables → Actions → Variables**, crie as variáveis `KINDLE_MODO`, `KINDLE_ORIENTACAO`, `KINDLE_TIPO` ou `KINDLE_OPCOES` (esta aceita qualquer opção, ex.: `--ignorar-base 15`).

### Qual configuração usar

| Tipo de PDF | Sugestão |
|---|---|
| Artigo científico em 2 colunas | padrão (`auto`, `retrato`) |
| Livro ou apostila A4 em uma coluna | `--orientacao paisagem` |
| Livro de bolso ou A5 | padrão |
| Quadrinhos, slides | `--modo pagina` (slides: com `--orientacao paisagem`) |
| PDF escaneado | padrão. Se sobrarem manchas nas margens, use `--modo pagina` |
| Precisa pesquisar no texto | `--tipo vetorial` |

A cada conversão, o log mostra o tamanho da letra em relação ao impresso (ex.: `texto no Kindle: ~79% do tamanho impresso`). Abaixo de uns 70% a leitura cansa, e vale tentar a orientação paisagem.

---

## Rodar no seu computador

```bash
pip install -r requirements.txt
python convert.py livro.pdf                     # gera output/livro_kindle.pdf
python convert.py pasta_com_pdfs/ -o prontos/
python convert.py artigo.pdf --paginas 1-3      # teste rápido
python convert.py --help                        # todas as opções
```

Requer Python 3.10 ou mais novo.

## Limites e cuidados

- **Tamanho:** o envio por e-mail aceita até 50 MB por arquivo. No tipo `imagem`, cada tela ocupa uns 55 KB, então um livro de 350 páginas A5 fica em torno de 23 MB. Para livros maiores, divida com `--paginas` ou baixe pelo artifact e copie por USB. O Git também recusa arquivos acima de 100 MB em `input/`. Nesse caso, use a opção B com um link.
- **Minutos do GitHub Actions:** repositórios privados têm 2.000 minutos grátis por mês. Um livro de 350 páginas leva uns 2 minutos.
- **Links internos** do PDF (notas, referências cruzadas) não são mantidos. O sumário é mantido.
- **Tipo `vetorial`:** a busca pode encontrar texto de trechos vizinhos, porque o recorte esconde o resto da página mas não o remove.
- **Layouts incomuns** (revistas com caixas de texto espalhadas, formulários) podem sair na ordem errada. Nesses casos, use `--modo largura` ou `--modo pagina`.
- Use apenas com documentos que você tem direito de copiar para uso pessoal.

## Estrutura

```
convert.py                      conversor (PyMuPDF + Pillow + NumPy)
send_to_kindle.py               envio por e-mail (Send to Kindle)
requirements.txt
input/                          coloque os PDFs aqui
.github/workflows/convert.yml   automação no GitHub Actions
```
