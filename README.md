# Notion → Markdown converter

Conversor de páginas públicas do Notion para Markdown com suporte completo a conteúdo dinâmico, toggles e assets locais.

## Objetivo ✅

Converter páginas públicas do Notion (Share to web) em Markdown de alta fidelidade, incluindo:

- Conteúdo expandido de toggles "(click to open)"
- Emojis e ícones renderizados como texto
- Download de imagens e arquivos para uso offline
- Remoção de placeholders e conteúdo lazy-loaded

## Pré-requisitos 🔧

- Python 3.8+
- Playwright (para renderização completa do JavaScript do Notion)
- A página Notion deve estar configurada como "Share to web" (pública)

## Instalação

```bash
# criar venv
python -m venv .venv

# ativar (Windows PowerShell)
.\.venv\Scripts\Activate.ps1

# instalar dependências
pip install -r requirements.txt

# instalar navegadores do Playwright
python -m playwright install chromium
```

### Instalando navegadores Playwright

Um erro comum ao executar scripts com Playwright é receber a mensagem "Executable doesn't exist" — isso significa que os binários do navegador (Chromium, Firefox, WebKit) não foram instalados no ambiente. Para resolver manualmente, execute:

```bash
pip install playwright && python -m playwright install chromium
```

Você também pode usar o utilitário incluído neste projeto para instalar navegadores:

```bash
python scripts\install_playwright.py --browsers chromium
```

Há também a variável de ambiente PLAYWRIGHT_AUTO_INSTALL. Habilite-a quando quiser que o projeto tente instalar automaticamente os navegadores (por exemplo em ambientes de desenvolvimento ou CI controlados). Não habilite em ambientes restritos ou de produção sem garantir permissões e segurança, pois a instalação baixa binários externos.


## Configurar página pública

1. Abra a página no Notion
2. Clique em "Share" (canto superior direito)
3. Ative "Share to web"
4. Copie a URL pública gerada

## Uso

### Conversão básica

```bash
# Conversão simples (nome gerado automaticamente: "Título - YYYYMMDD-HHMMSS.md")
python convert_from_public.py --page-url "https://reveloteam.notion.site/...-2a07a027d073804db87ce866829208c1"

# Especificar nome de saída
python convert_from_public.py --page-url "<URL>" --output meu-documento.md
```

### Conversão completa (recomendado)

```bash
# Com expansão de toggles, scroll completo e download de assets
python convert_from_public.py \
  --page-url "<URL>" \
  --expand-toggles \
  --download-assets \
  --max-scroll-steps 260 \
  --scroll-wait-ms 200
```

### Opções principais

- `--expand-toggles` — Expande todos os toggles "(click to open)" antes de capturar (recomendado)
- `--download-assets` — Baixa imagens/arquivos e reescreve links para caminhos locais
- `--assets-dir <dir>` — Pasta para salvar assets (padrão: `<nome_arquivo>_assets`)
- `--max-scroll-steps <n>` — Máximo de passos de rolagem para forçar lazy-load (padrão: 220)
- `--scroll-wait-ms <ms>` — Tempo de espera entre rolagens em ms (padrão: 250)
- `--screenshot <path>` — Salvar screenshot e HTML de debug
- `--headful` — Executar navegador visível (útil para debug)

### Exemplo completo com assets offline

```bash
python convert_from_public.py \
  --page-url "https://reveloteam.notion.site/CONFIDENTIAL-Specs-Generation-Rubrics-Labeling-Guidelines-v2-0-2a07a027d073804db87ce866829208c1" \
  --expand-toggles \
  --download-assets \
  --max-scroll-steps 260 \
  --scroll-wait-ms 150 \
  --screenshot "_debug/capture"
```

Isso gera:

- `[CONFIDENTIAL] Specs Generation Rubrics - Labeling Guidelines (v2.0) - YYYYMMDD-HHMMSS.md`
- `[CONFIDENTIAL] Specs Generation Rubrics - Labeling Guidelines (v2.0) - YYYYMMDD-HHMMSS_assets/` (pasta com imagens/arquivos)
- `_debug/capture.png` e `_debug/capture.html` (para diagnóstico)

## Diretório de saída via `.env`

Se você orientar um diretório base via variável de ambiente, o conversor cria uma pasta com o título da página e coloca o `.md` + os assets dentro dela.

Adicione ao seu `.env` (crie se não existir):

```env
NOTION_EXPORT_DIR=./output
```

Exemplo de saída usando isso:

- `./out/<Título da Página>/<Título da Página> - YYYYMMDD-HHMMSS.md`
- `./out/<Título da Página>/<Título da Página> - YYYYMMDD-HHMMSS_assets/`

## Recursos

### ✅ Conteúdo expandido

- Força carregamento de conteúdo lazy-loaded via scroll incremental
- Expande toggles "(click to open)" e captura o conteúdo interno
- Remove placeholders como "Carregando código..."

### ✅ Ícones e emojis

- Converte ícones do Notion (spritesheet/data-URI) em texto emoji
- Remove dependências de URLs externas

### ✅ Assets offline

- Download de todas as imagens referenciadas
- Download de arquivos anexados
- Rewrite automático de links para caminhos locais
- URL-encoding correto para nomes com caracteres especiais

### ✅ Nome automático

- Extrai título da página automaticamente
- Inclui timestamp no formato `YYYYMMDD-HHMMSS`
- Sanitização de caracteres especiais

## Arquivos do projeto

- `convert_from_public.py` — Conversor principal (Playwright + Markdown)
- `requirements.txt` — Dependências Python
- `sanitize_assets.py` — Script auxiliar para pós-processamento de assets
- `README.md` — Esta documentação

## Segurança & privacidade

⚠️ **Importante:**

- Use apenas em páginas que você tem permissão para converter
- A página deve estar configurada como "Share to web" pelo proprietário
- Respeite os termos de confidencialidade do conteúdo
- Não redistribua conteúdo protegido sem autorização
