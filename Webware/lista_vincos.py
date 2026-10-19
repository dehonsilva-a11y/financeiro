from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

def extrair_vincos_iniciais(navegador, usuario_atual):
    lista_unidades = []
    try:
        WebDriverWait(navegador, 10).until(EC.presence_of_element_located((By.CSS_SELECTOR, "div.box")))
        cards = navegador.find_elements(By.CSS_SELECTOR, "div.box")
        
        for card in cards:
            nome_condo = card.find_element(By.CLASS_NAME, "txt-titulo").text.strip()
            # Lógica para pegar o número da unidade dentro do card
            corpo = card.find_element(By.CLASS_NAME, "card-body").text
            unidade = "Não identificada"
            for linha in corpo.split('\n'):
                if "/" in linha: unidade = linha.replace("Unidade", "").strip(); break
            
            lista_unidades.append({
                "usuario_login": usuario_atual,
                "condominio": nome_condo,
                "unidade": unidade,
                "Status_Coleta": "Pendente"
            })
    except: pass
    return lista_unidades