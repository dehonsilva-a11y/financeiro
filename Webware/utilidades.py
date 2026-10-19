import os, time, requests, logging
from selenium.webdriver.support.ui import WebDriverWait
from datetime import datetime
from src.core import config

# Configuração básica do log de sistema (erros técnicos)
logging.basicConfig(
    filename=config.PATH_LOG_SISTEMA,
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

def obter_ip_publico():
    try:
        return requests.get('https://api.ipify.org', timeout=5).text
    except:
        return "IP Desconhecido"

def registrar_auditoria(dados_log):
    """
    Salva uma linha no arquivo de auditoria CSV.
    dados_log: dicionário com as informações do processo.
    """
    import csv
    arquivo_existe = os.path.exists(config.PATH_AUDITORIA_PROCESSO)
    
    colunas = [
        'data_hora', 'usuario', 'ip', 'condominio', 'unidade', 
        'id_externo', 'inicio_sessao', 'fim_sessao', 'duracao_segundos', 
        'status_final', 'detalhes'
    ]
    
    with open(config.PATH_AUDITORIA_PROCESSO, 'a', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=colunas)
        if not arquivo_existe:
            writer.writeheader()
        writer.writerow(dados_log)
        
def aguardar_pagina_pronta(navegador, timeout=30):
    wait = WebDriverWait(navegador, timeout)
    wait.until(lambda d: d.execute_script('return document.readyState') == 'complete')
    time.sleep(1.5)
    pass

def capturar_erro_login(navegador, usuario):
    """Salva screenshot do erro de login, substituindo a anterior."""
    caminho = os.path.join(config.PATH_ERROS_LOGIN, f"{usuario}.png")
    navegador.save_screenshot(caminho)
    print(f"      -> Screenshot de erro de login salva: {usuario}.png")

def capturar_erro_boleto(navegador, nome_identificador):
    """Salva screenshot em pasta diária com nome específico."""
    data_hoje = datetime.now().strftime("%d-%m-%Y")
    pasta_dia = os.path.join(config.PATH_ERROS_BOLETO, data_hoje)
    os.makedirs(pasta_dia, exist_ok=True)
    
    # nome_identificador será algo como Vencimento_IDExterno_Usuario_Unidade
    caminho = os.path.join(pasta_dia, f"{nome_identificador}.png")
    navegador.save_screenshot(caminho)
    print(f"      -> Screenshot de erro de coleta salva: {nome_identificador}.png")