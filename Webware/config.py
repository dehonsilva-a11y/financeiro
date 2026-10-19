import os

# Caminhos
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_DIR = os.path.join(BASE_DIR, 'data')
SCREENSHOT_DIR = os.path.join(BASE_DIR, 'screenshots')
PATH_ERROS_LOGIN = os.path.join(SCREENSHOT_DIR, 'erros_login')
PATH_ERROS_BOLETO = os.path.join(SCREENSHOT_DIR, 'erros_boleto')

# Boleto
PASTA_BOLETOS = os.path.join(DATA_DIR, 'boletos_pdf')
os.makedirs(PASTA_BOLETOS, exist_ok=True)

# Planilha Logins
EXCEL_PATH = os.path.join(DATA_DIR, 'Automacao_Logins.xlsx')
ABA_LOGIN = 'Dados_Login'

# Planilha Unidades
PATH_BASE_UNIDADES = os.path.join(DATA_DIR, 'Base_Unidades.xlsx')
ABA_BASE_UNIDADES = 'Unidades'

# Planilha Extração Unidades
PASTA_EXTRACAO = os.path.join(DATA_DIR, 'extracao_unidades')
os.makedirs(PASTA_EXTRACAO, exist_ok=True)

# Pastas de Logs
LOGS_DIR = os.path.join(BASE_DIR, 'logs')
PATH_LOG_SISTEMA = os.path.join(LOGS_DIR, 'sistema.log')
PATH_AUDITORIA_PROCESSO = os.path.join(LOGS_DIR, 'auditoria_processo.csv')

# URLs
URL_LOGIN = "https://webware.com.br/bin/administradora/"

# Cria pastas se não existirem
os.makedirs(SCREENSHOT_DIR, exist_ok=True)

for p in [PASTA_BOLETOS, PASTA_EXTRACAO, PATH_ERROS_LOGIN, PATH_ERROS_BOLETO, LOGS_DIR]:
    os.makedirs(p, exist_ok=True)