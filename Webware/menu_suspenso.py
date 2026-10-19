import time
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

def tratar_multiplas_unidades(navegador, usuario, pasta_boletos, id_ext, condo_ref, baixar_func):
    resultados_multiplos = []
    wait = WebDriverWait(navegador, 10)

    try:
        campo_select = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "div.v-select__slot")))
        navegador.execute_script("arguments[0].click();", campo_select)
        time.sleep(1.5)

        opcoes_elementos = wait.until(EC.presence_of_all_elements_located((By.XPATH, "//div[@role='option']")))
        qtd_opcoes = len(opcoes_elementos)
        
        navegador.execute_script("arguments[0].click();", campo_select) # Fecha

        for i in range(qtd_opcoes):
            input_sel = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "div.v-select__slot")))
            navegador.execute_script("arguments[0].click();", input_sel)
            time.sleep(1)

            itens = wait.until(EC.presence_of_all_elements_located((By.XPATH, "//div[@role='option']")))
            texto_sub_unidade = itens[i].text.strip()
            navegador.execute_script("arguments[0].click();", itens[i])
            time.sleep(1)

            btn_consultar = wait.until(EC.presence_of_element_located((By.XPATH, "//span[contains(text(), 'Consultar')]/..")))
            navegador.execute_script("arguments[0].removeAttribute('disabled');", btn_consultar)
            navegador.execute_script("arguments[0].click();", btn_consultar)
            
            time.sleep(3)
            
            # Coleta os dados desta sub-unidade específica
            lista_pdfs, status, und_real, dono = baixar_func(navegador, usuario, pasta_boletos, id_ext, condo_ref)
            
            # CRIA UMA LINHA INDEPENDENTE PARA ESTA SUB-UNIDADE
            resultados_multiplos.append({
                'unidade_menu': texto_sub_unidade,
                'lista_pdfs': lista_pdfs,
                'status': status,
                'unidade_real': und_real,
                'proprietario': dono
            })

        return resultados_multiplos

    except Exception as e:
        print(f"      -> Erro no menu suspenso: {e}")
        return []