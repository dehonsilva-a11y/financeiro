import os, time, requests
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from src.extratores import detalhes_boleto
from src.processadores import menu_suspenso
from src.web.utilidades import capturar_erro_boleto

def fechar_modal(navegador):
    """Tenta fechar o popup inicial caso ele apareça."""
    try:
        wait = WebDriverWait(navegador, 10)
        # Busca o botão 'Fechar' pelo texto ou estrutura de botão
        btn = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[contains(., 'Fechar')] | //span[contains(text(), 'Fechar')]/..")))
        navegador.execute_script("arguments[0].click();", btn)
        time.sleep(1)
    except: 
        pass

def processar_pagina(navegador, user, pasta, id_ext, condo_ref, e_primeira):
    if e_primeira: 
        fechar_modal(navegador)
    
    btn_consulta = navegador.find_elements(By.ID, "container-btn-consulta")
    if btn_consulta and btn_consulta[0].is_displayed():
        print("      -> Processando múltiplas unidades do menu...")
        # Retorna a lista de dicionários do menu
        return menu_suspenso.tratar_multiplas_unidades(
            navegador, user, pasta, id_ext, condo_ref, baixar_pdfs_da_tela
        )

    # Para unidade única, encapsulamos em uma lista para manter o padrão
    pdfs, status, und, dono = baixar_pdfs_da_tela(navegador, user, pasta, id_ext, condo_ref)
    return [{
        'unidade_menu': und,
        'lista_pdfs': pdfs,
        'status': status,
        'unidade_real': und,
        'proprietario': dono
    }]

def baixar_pdfs_da_tela(navegador, user, pasta, id_ext, condo_ref):
    dados_baixados = []
    # Extrai detalhes reais de dentro da página
    und_real, dono = detalhes_boleto.extrair_detalhes_pagina(navegador)
    
    # Espera os recibos carregarem via JavaScript
    try: 
        WebDriverWait(navegador, 15).until(EC.presence_of_element_located((By.ID, "recibos")))
        time.sleep(2) # Pausa técnica para renderização final
    except: 
        pass

    cards = navegador.find_elements(By.CLASS_NAME, "recibo")
    
    # SE NÃO HOUVER BOLETOS: Tira screenshot do motivo
    if not cards:
        u_limpa = und_real.replace('/', '-')
        # Formata nome: SEM_BOLETO_ID_USUARIO_UNIDADE
        nome_print = f"SEM_BOLETO_{id_ext}_{user}_{u_limpa}"
        capturar_erro_boleto(navegador, nome_print)
        return [], "Nenhum boleto", und_real, dono

    # Inicia sessão para download via Requests (mais rápido e estável que o Chrome)
    session = requests.Session()
    for c in navegador.get_cookies(): 
        session.cookies.set(c['name'], c['value'])

    for card in cards:
        try:
            # Extrai vencimento
            venc = card.find_element(By.CLASS_NAME, "recibo-data-vencimento").text.split(":")[-1].strip().replace("/", "-")
            link = card.find_element(By.XPATH, ".//a[contains(., 'Gerar Boleto')]").get_attribute('href')
            
            # Nome conforme solicitado: Vencimento_IdExterno_Usuario_Unidade_Condominio.pdf
            c_limpo = condo_ref.replace(" ", "_")[:20]
            nome_arq = f"{venc}_{id_ext}_{user}_{und_real.replace('/','-')}_{c_limpo}.pdf"
            
            res = session.get(link)
            if res.status_code == 200:
                with open(os.path.join(pasta, nome_arq), 'wb') as f: 
                    f.write(res.content)
                dados_baixados.append({"vencimento": venc})
        except: 
            continue
        
    return dados_baixados, f"{len(dados_baixados)} coletados", und_real, dono