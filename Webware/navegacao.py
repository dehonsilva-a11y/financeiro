from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from src.web.utilidades import aguardar_pagina_pronta

def ir_ate_area_boleto(navegador, index_unidade):
    wait = WebDriverWait(navegador, 30)
    try:
        cards = navegador.find_elements(By.CSS_SELECTOR, "div.box")
        botao_acessar = cards[index_unidade].find_element(By.CSS_SELECTOR, "a.bt")
        botao_acessar.click()

        wait.until(EC.url_contains("aInicioSkin.asp"))
        aguardar_pagina_pronta(navegador)

        url_atual = navegador.current_url
        if "aInicioSkin.asp" in url_atual:
            url_2via = url_atual.replace("skin/aInicioSkin.asp", "aplic/u_2via.asp")
            navegador.get(url_2via)
            aguardar_pagina_pronta(navegador)

            url_portal = "https://portalwm.webware.com.br/relatorioboleto/BoletoSegundaViaNovo"
            navegador.get(url_portal)
            aguardar_pagina_pronta(navegador)
            return True
    except Exception as e:
        print(f"      -> Falha ao navegar para boletos: {e}")
        return False