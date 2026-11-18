import os
import sys
import time
import re
import sqlite3
import requests
from dotenv import load_dotenv
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager

load_dotenv()

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB_PATH = os.path.join(BASE_DIR, 'banco.db')

USUARIO_LELLO = os.getenv("LELLO_USUARIO")
SENHA_LELLO = os.getenv("LELLO_SENHA")

URL_LOGIN = "https://pi.lelloimoveis.com.br/portalImoveis/"
URL_CARTEIRA = "https://pi.lelloimoveis.com.br/portalImoveis//pages/carteira/carteira.xhtml"

def conectar_banco():
    return sqlite3.connect(DB_PATH, timeout=30.0)

def realizar_login(driver, wait):
    print("🔐 Acessando Portal Lello...", flush=True)
    driver.get(URL_LOGIN)
    wait.until(EC.presence_of_element_located((By.XPATH, "//input[@type='text']"))).send_keys(USUARIO_LELLO)
    wait.until(EC.presence_of_element_located((By.XPATH, "//input[@type='password']"))).send_keys(SENHA_LELLO)
    driver.execute_script("arguments[0].click();", wait.until(EC.element_to_be_clickable((By.XPATH, "//button[contains(., 'Entrar')]"))))
    time.sleep(4)
    return True

def processar_referencias(referencias_str):
    lista_refs = [r.strip() for r in referencias_str.split(',') if r.strip()]
    if not lista_refs:
        return
        
    print(f"🚀 Iniciando mapeamento de {len(lista_refs)} condomínios novos...", flush=True)
    
    chrome_options = webdriver.ChromeOptions()
    chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1920,1080")
    
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=chrome_options)
    wait = WebDriverWait(driver, 15)
    
    if realizar_login(driver, wait):
        conexao = conectar_banco()
        sucessos = 0
        
        for ref in lista_refs:
            try:
                carteira, hp_alvo = ref.split('/')
                print(f"🔍 Buscando Ref: {ref}...", flush=True)
                
                # Vai direto para a carteira para pular telas intermediárias
                driver.get(URL_CARTEIRA)
                
                campo = wait.until(EC.presence_of_element_located((By.ID, "formFiltro:j_idt54")))
                campo.clear()
                campo.send_keys(carteira)
                
                driver.execute_script("arguments[0].click();", driver.find_element(By.XPATH, "//span[text()='Consultar']/parent::button"))
                
                # Aguarda o grid aparecer
                wait.until(EC.presence_of_element_located((By.XPATH, "//tbody[contains(@id, 'dataTableImoveis')]/tr")))
                time.sleep(1)
                
                hp_limpo = str(int(hp_alvo))
                linhas = driver.find_elements(By.XPATH, "//tbody[contains(@id, 'dataTableImoveis')]/tr")
                
                achou = False
                for linha in linhas:
                    try:
                        coluna_hp = linha.find_element(By.XPATH, ".//td[contains(@class, 'colunaNumero')]").text.strip()
                        if coluna_hp == hp_limpo:
                            botao_link = linha.find_element(By.XPATH, ".//a[contains(@class, 'ui-commandlink')]")
                            driver.execute_script("arguments[0].click();", botao_link)
                            achou = True
                            break
                    except: continue
                        
                if achou:
                    # Aguarda a tela do imóvel carregar procurando pela palavra "CÓDIGO:"
                    texto_codigo = wait.until(EC.presence_of_element_located((By.XPATH, "//*[contains(text(), 'CÓDIGO:')]"))).text
                    match = re.search(r'CÓDIGO:\s*(\d+)', texto_codigo)
                    
                    if match:
                        codigo_real = match.group(1)
                        # Salva no banco de dados e avisa o Python que teve sucesso (Soma +1 na barrinha)
                        conexao.execute("INSERT OR REPLACE INTO de_para_retorno (referencia, imovel_real) VALUES (?, ?)", (ref, codigo_real))
                        conexao.commit()
                        sucessos += 1
                        print(f"✔️ SUCESSO: {ref} salvo como Imóvel {codigo_real}! 🎯", flush=True)
                    else:
                        print(f"⚠️ ERRO: Não consegui extrair o ID da tela para {ref}", flush=True)
                else:
                    print(f"⚠️ ERRO: HP {hp_alvo} não achado na carteira {carteira}", flush=True)
                    
            except Exception as e:
                print(f"🚨 ERRO na referência {ref}: {e}", flush=True)
                
        conexao.close()
    driver.quit()

if __name__ == "__main__":
    if len(sys.argv) > 1:
        processar_referencias(sys.argv[1])