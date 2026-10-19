import os
import sys
import re
import time
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
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
import tempfile
import getpass

# =====================================================================
# COMUNICAÇÃO DE INTERRUPÇÃO VIA ARQUIVO (SISTEMA HÍBRIDO PAINEL/ROBÔ)
# =====================================================================
ARQUIVO_TRAVA_LIAX = os.path.join(tempfile.gettempdir(), "trava_liax.flag")

DOMINIO_LELLO = "@lello.com.br"

def obter_credenciais():
    """
    Recebe E-MAIL + SENHA.
    - Pelo painel: python LIAX_BASE.py "jessica.rufino@lello.com.br,SENHA"
    - Rodando direto no terminal: o robô pergunta.
    Se vier só "jessica.rufino" (sem @), completa com @lello.com.br.
    """
    argumentos = sys.argv[1] if len(sys.argv) > 1 else ""
    if "," in argumentos:
        email, senha = argumentos.split(",", 1)
    else:
        try:
            email = input("E-mail Lello (ex: jessica.rufino@lello.com.br): ")
            senha = getpass.getpass("Senha: ")
        except (EOFError, KeyboardInterrupt):
            print("❌ E-mail e senha não informados. Encerrando.")
            sys.exit(1)

    email = email.strip().lower()
    if "@" not in email:
        email += DOMINIO_LELLO
    if not email.split("@")[0] or not senha:
        print("❌ E-mail ou senha vazios. Encerrando.")
        sys.exit(1)
    return email, senha

# E-mail completo -> login do Google / SSO do Liax
# Parte antes do @   -> usuário do Portal Lello
# A senha é a mesma nos dois.
LELLO_EMAIL, LELLO_PASS = obter_credenciais()
LELLO_USER = LELLO_EMAIL.split("@")[0]

# =====================================================================
# CONFIGURAÇÃO DO AMBIENTE
# =====================================================================
chrome_options = Options()
chrome_options.add_argument("--start-maximized") 
chrome_options.add_experimental_option("detach", True) 
chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
chrome_options.add_experimental_option('useAutomationExtension', False)

# Instala e inicializa o WebDriver
driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=chrome_options)
wait = WebDriverWait(driver, 20)

def enviar_email_aviso():
    EMAIL_REMETENTE = "ctrlcond@lello.com.br"
    # 🎯 Injetando a senha que você passou diretamente aqui:
    SENHA_APP = os.environ.get("SENHA_APP_GMAIL", "oebjfpencmjnuoiz") 
    
    # 🎯 E-mails solicitados
    destinatarios = ["ryan.lima@lello.com.br", "jessica.rufino@lello.com.br", "marcio.teixeira@lello.com.br"]
    assunto = "🏁 Automação LIAX Concluída"
    corpo_html = f"Olá Equipe,<br><br>A automação do LIAX finalizou seu processamento. Não há mais documentos na fila para serem validados!<br><br>Atenciosamente,<br>Painel Orquestrador Lello"
    
    try:
        msg = MIMEMultipart()
        msg['From'] = EMAIL_REMETENTE
        msg['To'] = ", ".join(destinatarios)
        msg['Subject'] = assunto
        msg.attach(MIMEText(corpo_html, 'html'))
        
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(EMAIL_REMETENTE, SENHA_APP)
        server.sendmail(EMAIL_REMETENTE, destinatarios, msg.as_string())
        server.quit()
        print(f"✔️ E-mail de conclusão enviado com sucesso para a equipe!")
    except Exception as e:
        print(f"⚠️ Não foi possível enviar o e-mail de aviso: {e}")


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
        xpath = f"//p[contains(., '{texto_label}')]/following::input[1]"
        elem = wait.until(EC.element_to_be_clickable((By.XPATH, xpath)))
        
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", elem)
        time.sleep(0.2)
        
        elem.click() 
        time.sleep(0.1)
        driver.execute_script("arguments[0].value = '';", elem)
        elem.send_keys(Keys.CONTROL + "a")
        elem.send_keys(Keys.DELETE)
        
        elem.send_keys(str(valor))
        time.sleep(0.1)
        
        elem.send_keys(Keys.TAB) 
        time.sleep(0.2)
        print(f"  [OK] {texto_label}")
    except Exception as e:
        print(f"  [X] Falha no campo: {texto_label}")

def _clicar(elem):
    """Clique normal; se algo estiver na frente, cai para clique via JS."""
    try:
        elem.click()
    except Exception:
        driver.execute_script("arguments[0].click();", elem)

def _clicar_avancar_google():
    """Clica no botão 'Avançar' do Google (e-mail ou senha)."""
    xpath = "//span[normalize-space(text())='Avançar']/ancestor::button[1]"
    btn = WebDriverWait(driver, 15).until(EC.element_to_be_clickable((By.XPATH, xpath)))
    _clicar(btn)

def login_liax_google():
    """
    Faz o login no Liax Docs via SSO/Google:
      1. Entrar com SSO -> e-mail -> Enviar
      2. Tela do Google: e-mail -> Avançar -> senha -> Avançar
      3. Aguarda cair na tela inicial do Liax
    Retorna True se logou, False se precisar de ajuda manual.
    """
    wait_login = WebDriverWait(driver, 30)
    try:
        janela_principal = driver.current_window_handle
        print(f">>> Login automático no Liax com {LELLO_EMAIL}...")
        driver.get("https://liaxdocs.liax.com.br/login")

        # 1) Entrar com SSO
        btn_sso = wait_login.until(EC.element_to_be_clickable(
            (By.XPATH, "//div[normalize-space(text())='Entrar com SSO']")))
        _clicar(btn_sso)

        # 2) E-mail + Enviar
        campo_email = wait_login.until(EC.visibility_of_element_located(
            (By.XPATH, "//input[@placeholder='Email']")))
        campo_email.click()
        campo_email.send_keys(LELLO_EMAIL)

        btn_enviar = wait_login.until(EC.element_to_be_clickable(
            (By.XPATH, "//div[normalize-space(text())='Enviar']")))
        handles_antes = set(driver.window_handles)
        _clicar(btn_enviar)

        # 3) Google (pode abrir em popup ou na mesma aba)
        def _foi_para_google(d):
            novas = [h for h in d.window_handles if h not in handles_antes]
            if novas:
                d.switch_to.window(novas[0])
                return True
            return "accounts.google.com" in d.current_url

        WebDriverWait(driver, 30).until(_foi_para_google)
        print(">>> Tela do Google aberta. Preenchendo e-mail...")

        campo_google = wait_login.until(EC.visibility_of_element_located((By.ID, "identifierId")))
        campo_google.click()
        campo_google.send_keys(LELLO_EMAIL)
        _clicar_avancar_google()

        print(">>> Preenchendo senha...")
        campo_senha = wait_login.until(EC.element_to_be_clickable((By.NAME, "Passwd")))
        time.sleep(1)  # animação de transição do Google
        campo_senha.click()
        campo_senha.send_keys(LELLO_PASS)
        _clicar_avancar_google()

        # 4) Aguarda o Liax abrir (se tiver verificação em 2 etapas, dá tempo de aprovar)
        print(">>> Aguardando o Liax abrir...")
        print("    (Se o Google pedir verificação em duas etapas, aprove no celular.)")
        limite = time.time() + 240
        while time.time() < limite:
            handles = driver.window_handles
            if janela_principal in handles:
                driver.switch_to.window(janela_principal)
                url = driver.current_url
                if "liaxdocs.liax.com.br" in url and "/login" not in url:
                    # Fecha qualquer popup que tenha sobrado, deixando só a aba do Liax
                    for h in driver.window_handles:
                        if h != janela_principal:
                            driver.switch_to.window(h)
                            driver.close()
                    driver.switch_to.window(janela_principal)
                    print(">>> ✔️ Login no Liax concluído com sucesso!\n")
                    return True
            time.sleep(1)

        print("⚠️ Tempo esgotado esperando o Liax abrir após o login do Google.")
        return False

    except Exception as e:
        print(f"⚠️ Login automático falhou: {e}")
        return False

def iniciar_liax_manual():
    print(">>> Abrindo a página de login do Liax Docs...")
    driver.get("https://liaxdocs.liax.com.br/login")
    
    print(f"\n{'='*60}")
    print("⚠️ AÇÃO NECESSÁRIA: FAÇA O LOGIN NO LIAX MANUALMENTE!")
    print(f"{'='*60}")
    print("1. Vá até a aba aberta do LIAX e faça o login com o Google.")
    print("2. Volte ao PAINEL ORQUESTRADOR e clique no botão verde [✔️ Continuar LIAX].")
    print(f"{'-'*60}")
    
    # 🎯 MÁGICA DE INTEGRAÇÃO WEB: Cria uma trava física no servidor.
    with open(ARQUIVO_TRAVA_LIAX, "w") as f:
        f.write("aguardando_usuario")
    
    # Pausa a tela infinitamente enquanto a trava física não for apagada pelo botão do seu HTML
    while os.path.exists(ARQUIVO_TRAVA_LIAX):
        time.sleep(1)
        
    print(">>> Botão Continuar detectado pelo Painel! Retomando automação...\n")

def preparar_aba_lello_automatica():
    print(">>> Abrindo o Portal Lello numa nova aba para login automático...")
    driver.execute_script("window.open('about:blank', 'tab_lello');")
    driver.switch_to.window(driver.window_handles[1]) 
    driver.get("https://pi.lelloimoveis.com.br/portalImoveis//pages/fichaCondominio/fichaCondominio.xhtml")
    time.sleep(5) 
    try:
        wait_short = WebDriverWait(driver, 5)
        wait_short.until(EC.element_to_be_clickable((By.XPATH, "//input[contains(@placeholder, 'suário')] | //input[contains(@placeholder, 'nome')]"))).send_keys(LELLO_USER)
        driver.find_element(By.XPATH, "//input[@type='password']").send_keys(LELLO_PASS)
        driver.find_element(By.XPATH, "//button[contains(., 'Entrar')] | //input[@type='submit'] | //span[text()='Entrar']").click()
        time.sleep(5) 
        print(">>> Login no Portal Lello concluído com sucesso!")
    except: 
        print(">>> Portal Lello já estava logado ou os campos não foram encontrados.")
        pass

def processar_documentos():
    contador = 1
    if not os.path.exists("temp"): os.makedirs("temp")
    
    while True:
        try:
            print(f"\n{'='*50}\n--- A PROCESSAR DOCUMENTO {contador} ---\n{'='*50}")
            
            driver.switch_to.window(driver.window_handles[0])
            driver.get("https://liaxdocs.liax.com.br/documents/validate-queue")
            time.sleep(5)
            
            # 🎯 CAÇA O FIM DA FILA PELO POP-UP NA TELA
            try:
                alerta_fim = driver.find_element(By.XPATH, "//*[contains(text(), 'Não há mais documentos') or contains(text(), 'Erro ao buscar imagens')]")
                if alerta_fim:
                    print(f"🏁 Fila finalizada! Alerta na tela detectado.")
                    break # Sai do loop infinito e vai disparar o e-mail
            except:
                pass # Se não apareceu o alerta, a fila continua!
            
            try: wait.until(EC.element_to_be_clickable((By.XPATH, "//span[text()='Detalhes do Documento']"))).click()
            except: pass
            time.sleep(1)

            # --- VERIFICAÇÃO DE FIM DE FILA (Fallback) ---
            try:
                nome_documento = wait.until(EC.presence_of_element_located((By.XPATH, "//p[text()='Nome do documento']/following-sibling::*"))).text
            except:
                print("🏁 Não foi possível encontrar o nome do documento. Assumindo fim da fila!")
                break

            id_liax = driver.find_element(By.XPATH, "//p[text()='Código do Documento']/following-sibling::*").text
            
            # Corta o nome no primeiro traço ou espaço e isola a primeira parte
            nome_cortado = re.split(r'[- ]', nome_documento)[0]
            numero_imovel = re.sub(r'\D', '', nome_cortado)

            if "_" in nome_documento:
                print("Ignorado (contém '_').")
                continue 

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

            print("\n[LIAX] A preencher campos cruzados da Lello...")
            driver.switch_to.window(driver.window_handles[0])
            time.sleep(1)
            
            preencher_campo_liax("Nome do Empreendimento", v_emp)
            preencher_campo_liax("Endereço do Empreendimento", v_end)
            preencher_campo_liax("Bloco", bloco if bloco.strip() else "0")
            preencher_campo_liax("Unidade", unidade)
            preencher_campo_liax("CNPJ do Beneficiário", cnpj)
            preencher_campo_liax("Nome do Pagador", v_sac)
            preencher_campo_liax("Nome da Administradora", v_adm)

            print("\n[LIAX] Limite atingido! A colocar o documento em Stand By...")
            driver.get(f"https://liaxdocs.liax.com.br/documents?documentId={id_liax}")
            time.sleep(3) 
            
            print("A clicar em Filtros...")
            xpath_filtros = "//div[contains(@class, 'css-s8qz9q') and contains(., 'Filtros')]"
            btn_filtros = wait.until(EC.presence_of_element_located((By.XPATH, xpath_filtros)))
            driver.execute_script("arguments[0].click();", btn_filtros)
            
            print("Aguardando o menu abrir...")
            time.sleep(2) 
            
            print("A selecionar Stand By...")
            xpath_stand_by = "//span[@class='css-1bzlmiz' and text()='Colocar documentos em Stand By']"
            btn_stand_by = wait.until(EC.element_to_be_clickable((By.XPATH, xpath_stand_by)))
            driver.execute_script("arguments[0].click();", btn_stand_by)
            
            print("A aguardar a janela de confirmação...")
            time.sleep(3) 
            
            print("A inserir a observação com ActionChains...")
            xpath_obs = "//*[contains(text(), 'Observação')]/following::input[1]"
            obs = wait.until(EC.visibility_of_element_located((By.XPATH, xpath_obs)))
            
            actions = ActionChains(driver)
            actions.move_to_element(obs).click().pause(0.5).send_keys("FEITO PELO RÔBO").perform()
            time.sleep(1)
            
            print("A confirmar...")
            xpath_confirmar = "//div[@class='css-5pz39s' and text()='Confirmar']"
            btn_confirmar = wait.until(EC.element_to_be_clickable((By.XPATH, xpath_confirmar)))
            actions.move_to_element(btn_confirmar).click().perform()
            
            print(f">>> SUCESSO: Documento {id_liax} enviado para Stand By. Seguindo para o próximo...\n")
            print(f"SUCESSO Documento LIAX {id_liax} processado e colocado em Standby com informações Lello!")
            time.sleep(2)
            contador += 1

        except Exception as e:
            print(f"\n[!] Erro capturado durante o fluxo: {e}")
            print("🚨 FALHA ao processar documento na LIAX.")
            print("[!] Reiniciando o ciclo do robô em 5 segundos...\n")
            time.sleep(5)
            continue

if __name__ == "__main__":
    if not login_liax_google():
        print(">>> Passando para o login manual (fallback)...")
        # Garante que só sobra uma aba antes do fallback
        principal = driver.window_handles[0]
        for h in driver.window_handles[1:]:
            driver.switch_to.window(h)
            driver.close()
        driver.switch_to.window(principal)
        iniciar_liax_manual()
    preparar_aba_lello_automatica()
    processar_documentos()
    
    # Quando o loop acabar (fim da fila), envia o e-mail e encerra
    enviar_email_aviso()