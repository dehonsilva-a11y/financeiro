import time
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from src.web.utilidades import aguardar_pagina_pronta, capturar_erro_login

def realizar_login(navegador, usuario, senha, url_inicial):
    navegador.get(url_inicial)
    wait = WebDriverWait(navegador, 10)
    try:
        # Preenchimento dos campos
        u = wait.until(EC.presence_of_element_located((By.NAME, "mem")))
        u.clear()
        u.send_keys(usuario)
        
        p = navegador.find_element(By.NAME, "pass")
        p.clear()
        p.send_keys(senha)
        
        # Clique no botão entrar
        navegador.find_element(By.XPATH, "//input[@value='Entrar']").click()
        
        # Espera o processamento do servidor
        aguardar_pagina_pronta(navegador)
        time.sleep(2)

        # VERIFICAÇÃO DE ERRO: Se a URL não mudou ou se contém mensagens de erro
        url_atual = navegador.current_url.lower()
        if "login.asp" in url_atual or "erro" in navegador.page_source.lower():
            print(f"      -> Falha de login detectada para o usuário: {usuario}")
            capturar_erro_login(navegador, usuario)
            
    except Exception as e:
        print(f"      -> Erro técnico nos campos de login: {e}")
        capturar_erro_login(navegador, usuario)