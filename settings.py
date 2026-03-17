from dotenv import load_dotenv
import os

load_dotenv()

# Diretório base para exportação (opcional) - usado para criar uma pasta por título dentro dele.
# Exemplo em `.env`: NOTION_EXPORT_DIR=./out
EXPORT_BASE_DIR = os.getenv('NOTION_EXPORT_DIR')

# Playwright configuration:
# Quando True, permite instalação automática dos navegadores Playwright a partir de variáveis de ambiente.
# Exemplo em `.env`: PLAYWRIGHT_AUTO_INSTALL=true
PLAYWRIGHT_AUTO_INSTALL = os.getenv('PLAYWRIGHT_AUTO_INSTALL', 'False').lower() in ('1', 'true', 'yes')

# Lista ou string com navegadores a instalar/usar pelo Playwright. Padrão: 'chromium'
# Pode ser uma string única "chromium" ou uma lista separada por vírgulas em `.env`:
# PLAYWRIGHT_BROWSERS=chromium,firefox,webkit
_browsers_env = os.getenv('PLAYWRIGHT_BROWSERS', 'chromium')
if ',' in _browsers_env:
    PLAYWRIGHT_BROWSERS = [b.strip() for b in _browsers_env.split(',') if b.strip()]
else:
    PLAYWRIGHT_BROWSERS = _browsers_env.strip()


# HTML parser constant used with BeautifulSoup
HTML_PARSER = 'html.parser'

# Whether to normalize Notion "spanned" code blocks into <pre><code>
NORMALIZE_NOTION_CODE_BLOCKS = os.getenv('NORMALIZE_NOTION_CODE_BLOCKS', 'True').lower() in ('1','true','yes')


# --- Heurísticas de hidratação e retries (novas configurações) ---
# Essas configurações controlam heurísticas usadas ao "hidratar"/carregar
# conteúdo dinâmico (ex.: páginas do Notion via Playwright).
# Podem ser sobrescritas via variáveis de ambiente quando aplicável.

# Número máximo de tentativas de retry para ações de hidratação (int)
# Exemplo em `.env`: HYDRATION_MAX_RETRIES=3
HYDRATION_MAX_RETRIES = int(os.getenv('HYDRATION_MAX_RETRIES', '2'))

# Timeout total (ms) a aguardar por hidratação/carregamento da página (int)
# Exemplo em `.env`: HYDRATION_TIMEOUT_MS=20000
HYDRATION_TIMEOUT_MS = int(os.getenv('HYDRATION_TIMEOUT_MS', '15000'))

# Número de passos/scrolls a executar durante a heurística de scroll (int)
# Exemplo em `.env`: HYDRATION_SCROLL_STEPS=8
HYDRATION_SCROLL_STEPS = int(os.getenv('HYDRATION_SCROLL_STEPS', '5'))

# Permitir cliques em elementos "toggle"/expansíveis durante hidratação (bool)
# Use '1', 'true' ou 'yes' para True. Exemplo: HYDRATION_CLICK_TOGGLES=false
HYDRATION_CLICK_TOGGLES = os.getenv('HYDRATION_CLICK_TOGGLES', 'True').lower() in ('1', 'true', 'yes')

# Padrões de placeholder/placeholder texts que indicam conteúdo não carregado.
# Pode ser sobrescrito por variável de ambiente separada por vírgulas:
# PLACEHOLDER_PATTERNS="Carregando,Loading,(click to open)"
_placeholder_env = os.getenv('PLACEHOLDER_PATTERNS')
if _placeholder_env:
    PLACEHOLDER_PATTERNS = [p.strip() for p in _placeholder_env.split(',') if p.strip()]
else:
    PLACEHOLDER_PATTERNS = [
        'Carregando',
        'Loading',
        'Loading code',
        '(click to open)',
    ]

# Compatibilidade: permitir detecção por regex configurável e padrões regex adicionais
PLACEHOLDER_USE_REGEX = os.getenv('PLACEHOLDER_USE_REGEX', 'True').lower() in ('1','true','yes')
_placeholder_regex_env = os.getenv('PLACEHOLDER_REGEX_PATTERNS')
if _placeholder_regex_env:
    PLACEHOLDER_REGEX_PATTERNS = [p.strip() for p in _placeholder_regex_env.split(',') if p.strip()]
else:
    PLACEHOLDER_REGEX_PATTERNS = [
        r"\bcarregando\b",
        r"\bloading\b",
        r"loading code",
        r"\(click to open\)",
    ]

# Tags cujo conteúdo textual deve ser ignorado ao detectar placeholders (ex.: script, style)
PLACEHOLDER_DETECTION_IGNORE_TAGS = [t.strip().lower() for t in os.getenv('PLACEHOLDER_DETECTION_IGNORE_TAGS', 'script,style').split(',')]

# Delay entre tentativas de retry durante hidratação (ms)
HYDRATION_RETRY_DELAY_MS = int(os.getenv('HYDRATION_RETRY_DELAY_MS', '500'))

# Nota: mantemos leitura via os.getenv para compatibilidade com configuração existente.
