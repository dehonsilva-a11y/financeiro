import os
import re
import time
import json
import random
import google.generativeai as genai
from webdriver_manager.chrome import ChromeDriverManager
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains
from selenium.common.exceptions import StaleElementReferenceException

# =====================================================================
# CONFIGURAÇÃO DA IA (GEMINI)
# =====================================================================
# Chaves fornecidas - Troque caso a chave 1 atinja o limite de uso:
API_KEY = "AIzaSyAoYWc6eMfJCHihi3prO9LrGphOhgOQUzA"
# API_KEY_2 = "AIzaSyBYjhmR0gcU6C5TjN7u9nzlOnKFR0QmQbY"
# API_KEY_3 = "AIzaSyAGWBDAXzlKNKniJjBX7E8pVsH9oWX7W10"

genai.configure(api_key=API_KEY)

def extrair_dados_boleto_com_ia(texto_visivel):
    """Envia o texto da página para o Gemini classificar as verbas com base nas suas regras."""
    print("\n[IA] Extraindo dados do boleto e classificando contas...")
    model = genai.GenerativeModel('gemini-1.5-flash')
    
    prompt = f"""
    Você é um assistente especialista em contas a pagar de condomínios. Analise o texto OCR.
    Identifique e retorne APENAS um JSON válido (sem markdown ou crases) com os dados.
    
    Regras de Classificação para a chave 'id_conta' nas verbas:
    - Conta 231: Despesas do inquilino (água, gás, energia, consumo, cota condominial, despesas ordinárias).
    - Conta 212: Fundo de reserva.
    - Conta 211: Despesas do proprietário (obra, fachada, pintura, rateio de reformas, benfeitorias).
    - Conta 33: Extras e avulsos (tag de garagem, salão de festas, lavagem de roupa, locações).
    - Conta 132: IPTU.
    Se houver dúvida e for uma cobrança padrão de condomínio, use 231.

    Formato exigido:
    {{
        "linha_digitavel": "extrair apenas os numeros sem espaços",
        "nosso_numero": "extrair o valor exato",
        "data_vencimento": "formato DD/MM/AAAA",
        "valor_total": "valor numérico (ex: 570,57)",
        "verbas": [
            {{
                "descricao": "nome da cobrança/verba", 
                "valor": "valor numérico", 
                "id_conta": "código numérico segundo a regra"
            }}
        ]
    }}
    
    Texto da tela:
    {texto_visivel}
    """
    
    try:
        response = model.generate_content(prompt)
        texto_limpo = response.text.strip().replace("```json", "").replace("```", "")
        dados = json.loads(texto_limpo)
        print("  [OK] Dados extraídos e classificados com sucesso pela IA!")
        return dados
    except Exception as e:
        print(f"  [X] Falha ao extrair dados com IA: {e}")
        return None

# =====================================================================
# CONFIGURAÇÃO DO AMBIENTE SELENIUM
# =====================================================================
chrome_options = Options()
chrome_options.add_argument("--start-maximized") 
chrome_options.add_experimental_option("detach", True) 
chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
chrome_options.add_experimental_option('useAutomationExtension', False)

driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=chrome_options)
wait = WebDriverWait(driver, 20)

# =====================================================================
# FUNÇÕES DE PREENCHIMENTO E CONTROLE
# =====================================================================
def extrair_dado_lello(texto_label):
    try:
        xpath = f"//*[contains(text(), '{texto_label}')]/following::span[contains(@style, 'bold')][1]"
        return driver.find_element(By.XPATH, xpath).text.strip()
    except:
        return ""

def fechar_popups_erro():
    try:
        popups = driver.find_elements(By.XPATH, "//*[contains(@class, 'Toastify')]//button")
        for p in popups:
            driver.execute_script("arguments[0].click();", p)
    except: pass

def preencher_campo_liax(texto_label, valor):
    if not valor or str(valor).strip() == "": return 
    try:
        fechar_popups_erro()
        xpath = f"//p[contains(., '{texto_label}')] | //*[contains(text(), '{texto_label}')]/following::input[1]"
        elem = wait.until(EC.element_to_be_clickable((By.XPATH, xpath)))
        
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", elem)
        time.sleep(0.2)
        
        elem.click() 
        time.sleep(0.1)
        elem.send_keys(Keys.CONTROL + "a")
        elem.send_keys(Keys.DELETE)
        
        elem.send_keys(str(valor))
        time.sleep(0.1)
        
        elem.send_keys(Keys.TAB) 
        time.sleep(0.2)
        print(f"  [OK] {texto_label}: {valor}")
    except Exception as e:
        print(f"  [X] Falha no campo: {texto_label}")

def preencher_verbas_liax(verbas):
    if not verbas:
        return
    print("\n>>> Preenchendo Tabela de Verbas...")
    
    try:
        xpath_btn = "//*[contains(text(), '+ Adicionar Linha')]"
        btn_adicionar = wait.until(EC.presence_of_element_located((By.XPATH, xpath_btn)))
        
        for i, verba in enumerate(verbas):
            if i > 0:
                driver.execute_script("arguments[0].click();", btn_adicionar)
                time.sleep(0.5)
            
            # Pega todos os inputs ativos na tabela após o cabeçalho 'Id da Conta'
            xpath_inputs = "//*[contains(text(), 'Id da Conta')]/following::input[not(@type='hidden')]"
            inputs_tabela = driver.find_elements(By.XPATH, xpath_inputs)
            
            # Assumindo 3 inputs visíveis por linha: 0=Descrição, 1=Valor, 2=Conta
            idx_descricao = i * 3
            idx_valor = (i * 3) + 1
            idx_conta = (i * 3) + 2
            
            if idx_descricao < len(inputs_tabela):
                elem_desc = inputs_tabela[idx_descricao]
                elem_desc.send_keys(Keys.CONTROL + "a", Keys.DELETE)
                elem_desc.send_keys(str(verba.get("descricao", "")))
                time.sleep(0.3)
                elem_desc.send_keys(Keys.ENTER)
                
            if idx_valor < len(inputs_tabela):
                elem_valor = inputs_tabela[idx_valor]
                elem_valor.send_keys(Keys.CONTROL + "a", Keys.DELETE)
                elem_valor.send_keys(str(verba.get("valor", "")))
                time.sleep(0.3)
                elem_valor.send_keys(Keys.TAB)
                
            if idx_conta < len(inputs_tabela):
                elem_conta = inputs_tabela[idx_conta]
                elem_conta.send_keys(Keys.CONTROL + "a", Keys.DELETE)
                elem_conta.send_keys(str(verba.get("id_conta", "")))
                time.sleep(0.3)
                elem_conta.send_keys(Keys.ENTER)
                
            print(f"  [OK] Verba preenchida: {verba.get('descricao')} | R$ {verba.get('valor')} | Conta: {verba.get('id_conta')}")
            
    except Exception as e:
        print(f"  [X] Falha ao tentar preencher as verbas: {e}")

# =====================================================================
# FLUXO PRINCIPAL
# =====================================================================
def realizar_login_liax():
    print(">>> Aceder ao Liax Docs...")
    driver.get("https://liaxdocs.liax.com.br/login")
    time.sleep(3)
    try:
        wait.until(EC.element_to_be_clickable((By.XPATH, "//button[contains(., 'Entrar com SSO')] | //a[contains(., 'Entrar com SSO')]"))).click()
        time.sleep(3)
        email_input = wait.until(EC.element_to_be_clickable((By.XPATH, "//input[@placeholder='Email' or @name='email']")))
        email_input.clear()
        email_input.send_keys("ryan.lima@lello.com.br")
        driver.find_element(By.XPATH, "//button[contains(., 'Enviar')] | //input[@value='Enviar']").click()
        time.sleep(4)
        wait.until(EC.element_to_be_clickable((By.XPATH, "//input[@placeholder='nome.sobrenome']"))).send_keys("ryan.lima")
        driver.find_element(By.XPATH, "//input[@type='password' or @placeholder='Senha']").send_keys("kary2002!")
        driver.find_element(By.ID, "submitButton").click()
        wait.until(EC.url_contains("documents"))
        print("Login Liax concluído!")
    except:
        print("Aviso: Sistema assumiu que já está logado.")

def preparar_aba_lello():
    print("\n>>> Abrir Portal Lello numa nova aba...")
    driver.execute_script("window.open('about:blank', 'tab_lello');")
    driver.switch_to.window(driver.window_handles[1]) 
    driver.get("https://pi.lelloimoveis.com.br/portalImoveis//pages/fichaCondominio/fichaCondominio.xhtml")
    time.sleep(5) 
    try:
        wait.until(EC.element_to_be_clickable((By.XPATH, "//input[contains(@placeholder, 'suário')] | //input[contains(@placeholder, 'nome')]"))).send_keys("ryan.lima")
        driver.find_element(By.XPATH, "//input[@type='password']").send_keys("kary2002!")
        driver.find_element(By.XPATH, "//button[contains(., 'Entrar')] | //input[@type='submit'] | //span[text()='Entrar']").click()
        time.sleep(5) 
    except: pass

def processar_documentos():
    contador = 1
    if not os.path.exists("temp"): os.makedirs("temp")
    
    while True:
        try:
            print(f"\n{'='*50}\n--- A PROCESSAR DOCUMENTO {contador} ---\n{'='*50}")
            
            driver.switch_to.window(driver.window_handles[0])
            driver.get("https://liaxdocs.liax.com.br/documents/validate-queue")
            time.sleep(5)
            
            try: wait.until(EC.element_to_be_clickable((By.XPATH, "//span[text()='Detalhes do Documento']"))).click()
            except: pass
            time.sleep(1)

            nome_documento = wait.until(EC.presence_of_element_located((By.XPATH, "//p[text()='Nome do documento']/following-sibling::*"))).text
            id_liax = driver.find_element(By.XPATH, "//p[text()='Código do Documento']/following-sibling::*").text
            
            nome_cortado = re.split(r'[- ]', nome_documento)[0]
            numero_imovel = re.sub(r'\D', '', nome_cortado)

            if "_" in nome_documento:
                print("Ignorado (contém '_').")
                break 

            # --- LELLO: BUSCA DO IMÓVEL ---
            print(f"\n[LELLO] A Pesquisar Imóvel: {numero_imovel}")
            driver.switch_to.window(driver.window_handles[1])
            driver.get("https://pi.lelloimoveis.com.br/portalImoveis//pages/fichaCondominio/fichaCondominio.xhtml")
            time.sleep(3)
            
            wait.until(EC.element_to_be_clickable((By.ID, "formFiltro:tipoBuscaFichaCondominio_label"))).click()
            time.sleep(0.5)
            driver.find_element(By.XPATH, "//li[text()='Imóvel' or contains(., 'Imóvel')]").click()
            time.sleep(1)
            
            driver.find_element(By.ID, "formFiltro:tipoCampoFichaCondominio_label").click()
            time.sleep(0.5)
            driver.find_element(By.XPATH, "//li[text()='Código' or contains(., 'Código')]").click()
            time.sleep(1)

            input_busca = driver.find_element(By.ID, "formFiltro:valorBuscaFichaCondominio")
            input_busca.clear()
            input_busca.send_keys(numero_imovel)
            driver.find_element(By.XPATH, "//button[contains(., 'Consultar')] | //span[text()='Consultar']").click()
            
            print("[LELLO] A aguardar a tabela de resultados atualizar...")
            xpath_resultado = f"//tbody/tr[contains(., '{numero_imovel}')]/td[2]//a | //tbody/tr[1]/td[2]//a"
            clicou = False
            tentativas = 0
            
            while not clicou and tentativas < 5:
                try:
                    time.sleep(1.5)
                    resultado = wait.until(EC.presence_of_element_located((By.XPATH, xpath_resultado)))
                    driver.execute_script("arguments[0].click();", resultado)
                    clicou = True 
                except StaleElementReferenceException:
                    print("  [!] Tabela atualizou na hora do clique. Insistindo...")
                    tentativas += 1
                except Exception:
                    tentativas += 1
                    
            if not clicou:
                print("  [X] Falha crítica: Não achou o imóvel na tabela.")
                raise Exception("Não foi possível clicar no resultado da busca.")
            
            print("[LELLO] A aguardar carregamento da ficha...")
            wait.until(EC.presence_of_element_located((By.XPATH, "//*[contains(text(), 'Empreendimento:')]")))
            
            v_emp = extrair_dado_lello("Empreendimento:")
            v_end = extrair_dado_lello("Imóvel:")
            
            try:
                xpath_sac = "//*[contains(text(), 'Sacado:')]/following::input[1]"
                v_sac = driver.find_element(By.XPATH, xpath_sac).get_attribute("value").strip()
            except:
                v_sac = extrair_dado_lello("Carteira/Titular:") 
            
            v_adm = extrair_dado_lello("Administradora:")
            
            try:
                match = re.search(r'(?:AP|APTO|APT|CJ|CONJ|SALA|SL)\.?\s*(\d+)', v_end, re.IGNORECASE)
                unidade = match.group(1) if match else re.findall(r'\d+', v_end)[-1] 
            except: unidade = "0"

            link_adm = wait.until(EC.element_to_be_clickable((By.XPATH, "//*[contains(text(), 'Administradora:')]/following::a[1]")))
            driver.execute_script("arguments[0].click();", link_adm)
            time.sleep(3)
            
            bloco = wait.until(EC.presence_of_element_located((By.ID, "formCadastro:nomeBlocoBoletoCondominio"))).get_attribute("value")
            cnpj = wait.until(EC.presence_of_element_located((By.ID, "formCadastro:cnpjBeneficiario"))).get_attribute("value")

            # --- PREENCHIMENTO LIAX E IA ---
            print("\n[LIAX] Retornando ao validador...")
            driver.switch_to.window(driver.window_handles[0])
            time.sleep(2)
            
            preencher_campo_liax("Nome do Empreendimento", v_emp)
            preencher_campo_liax("Endereço do Empreendimento", v_end)
            preencher_campo_liax("Bloco", bloco if bloco.strip() else "0")
            preencher_campo_liax("Unidade", unidade)
            preencher_campo_liax("CNPJ do Beneficiário", cnpj)
            preencher_campo_liax("Nome do Pagador", v_sac)
            preencher_campo_liax("Nome da Administradora", v_adm)

            # =======================================================
            # IA - COLETA E PREENCHIMENTO DINÂMICO (VERBAS E DADOS)
            # =======================================================
            texto_tela = driver.find_element(By.TAG_NAME, "body").text
            dados_extras = extrair_dados_boleto_com_ia(texto_tela)
            
            if dados_extras:
                preencher_campo_liax("Linha Digitável", dados_extras.get("linha_digitavel"))
                preencher_campo_liax("Nosso Numero", dados_extras.get("nosso_numero"))
                preencher_campo_liax("Data de Vencimento", dados_extras.get("data_vencimento"))
                preencher_campo_liax("Valor Total", dados_extras.get("valor_total"))
                
                # Preencher as verbas com os Ids das contas classificados
                preencher_verbas_liax(dados_extras.get("verbas", []))
            
            # =======================================================
            # FINALIZAR EM STAND BY
            # =======================================================
            print("\n[LIAX] Limite atingido! A colocar o documento em Stand By...")
            driver.get(f"https://liaxdocs.liax.com.br/documents?documentId={id_liax}")
            time.sleep(3) 
            
            xpath_filtros = "//div[contains(@class, 'css-s8qz9q') and contains(., 'Filtros')]"
            btn_filtros = wait.until(EC.presence_of_element_located((By.XPATH, xpath_filtros)))
            driver.execute_script("arguments[0].click();", btn_filtros)
            
            time.sleep(2) 
            xpath_stand_by = "//span[@class='css-1bzlmiz' and text()='Colocar documentos em Stand By']"
            btn_stand_by = wait.until(EC.element_to_be_clickable((By.XPATH, xpath_stand_by)))
            driver.execute_script("arguments[0].click();", btn_stand_by)
            
            time.sleep(3) 
            xpath_obs = "//*[contains(text(), 'Observação')]/following::input[1]"
            obs = wait.until(EC.visibility_of_element_located((By.XPATH, xpath_obs)))
            
            actions = ActionChains(driver)
            actions.move_to_element(obs).click().pause(0.5).send_keys("FEITO PELO RÔBO").perform()
            time.sleep(1)
            
            xpath_confirmar = "//div[@class='css-5pz39s' and text()='Confirmar']"
            btn_confirmar = wait.until(EC.element_to_be_clickable((By.XPATH, xpath_confirmar)))
            actions.move_to_element(btn_confirmar).click().perform()
            
            print(f">>> SUCESSO: Documento {id_liax} enviado para Stand By.\n")
            time.sleep(2)
            contador += 1

        except Exception as e:
            print(f"Fim da execução. Erro global: {e}")
            break

if __name__ == "__main__":
    realizar_login_liax()
    preparar_aba_lello()
    processar_documentos()