from selenium.webdriver.common.by import By

def extrair_detalhes_pagina(navegador):
    """Lê Proprietário e Unidade de dentro da página de boletos."""
    try:
        unidade = navegador.find_element(By.XPATH, "//h4[contains(., 'Unidade:')]//strong").text.strip()
        proprietario = navegador.find_element(By.XPATH, "//h4[contains(., 'Proprietário:')]//strong").text.strip()
        return unidade, proprietario
    except:
        return "Desconhecida", "Não Encontrado"