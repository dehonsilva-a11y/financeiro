import os
from dotenv import load_dotenv
load_dotenv()
import sys
import pdfplumber
import re
import time
import json
import datetime
import io
import requests 
from PIL import Image
import threading
import tempfile
import hashlib
import httpx
import warnings
from concurrent.futures import ThreadPoolExecutor

from google import genai
from google.genai import types

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.keys import Keys
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager

warnings.filterwarnings("ignore")

_original_client_init = httpx.Client.__init__
def _patched_client_init(self, *args, **kwargs):
    kwargs['verify'] = False
    headers = kwargs.get('headers', {})
    if isinstance(headers, dict):
        headers['User-Agent'] = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
    kwargs['headers'] = headers
    _original_client_init(self, *args, **kwargs)

httpx.Client.__init__ = _patched_client_init

# =================================================================
# INTEGRAÇÃO (SISTEMA LELLO)
# =================================================================
URL_PENDENTES = "http://127.0.0.1:5000/api/automacao/pagamentos_pendentes"
URL_BAIXA = "http://127.0.0.1:5000/api/automacao/pagar"
TOKEN = os.getenv("TOKEN_SEGURANCA")
NOME_DO_ROBO = "AUTOMAÇÃO PAGAMENTOS"

filtro_bruto = sys.argv[1] if len(sys.argv) > 1 else ""
filtro_resp = ""
filtro_emp = ""
filtro_dias = []

# 🛑 DESEMPACOTA OS FILTROS DO HTML
if "RESP:" in filtro_bruto or "EMP:" in filtro_bruto or "DIAS:" in filtro_bruto:
    partes = filtro_bruto.split('|')
    for p in partes:
        if p.startswith("RESP:"): filtro_resp = p.replace("RESP:", "").strip().lower()
        if p.startswith("EMP:"): filtro_emp = p.replace("EMP:", "").strip().lower()
        if p.startswith("DIAS:"): 
            dias_str = p.replace("DIAS:", "").strip()
            if dias_str:
                filtro_dias = [d.strip() for d in dias_str.split(",")]
else:
    filtro_resp = filtro_bruto.strip().lower()

print(f"⚙️ [INÍCIO] Motor de Pags -> Resp: '{filtro_resp}' | Emp: '{filtro_emp}' | Dias: {filtro_dias}", flush=True)

def buscar_boletos_coletados():
    print("🔍 Conectando à API local para buscar fila de pagamentos...", flush=True)
    try:
        resposta = requests.post(URL_PENDENTES, json={"token": TOKEN}).json()
        if resposta.get('sucesso') and resposta.get('quantidade') > 0:
            print(f"📊 O site retornou {resposta['quantidade']} imóveis aguardando pagamento.", flush=True)
            return resposta['dados']
        else:
            print("⚠️ Fila vazia. Nenhum imóvel com status 'COLETADO' no momento.", flush=True)
            return []
    except Exception as e:
        print(f" Falha crítica ao conectar com a API de Orquestração: {e}", flush=True)
        return []

def informar_status_ao_site(imovel, deu_certo, msg_erro=""):
    try:
        dados = {
            "token": TOKEN,
            "imovel": str(imovel),
            "nome_robo": NOME_DO_ROBO,
            "sucesso": deu_certo,
            "erro_msg": msg_erro
        }
        requests.post(URL_BAIXA, json=dados)
    except Exception as e:
        print(f" Erro ao enviar a resposta do imóvel {imovel} para a API: {e}", flush=True)

# =============================================================
# CONFIGURAÇÕES DE AMBIENTE E PASTAS
# =============================================================
# 🔧 Só entram chaves realmente preenchidas (chave vazia/None dava 403 PERMISSION_DENIED)
CHAVES_EMBUTIDAS = [
    "AQ.Ab8RN6J8UecXhjgR7WpcMagsghruklH6gOg3HPc9ljJAPZjWVQ",
    "AQ.Ab8RN6IORADZtzjVVPleyl6F3J_PI1LPOCLRg_zeAeaeptD4Eg",
]
CHAVES_API = []
for _k in CHAVES_EMBUTIDAS + [os.getenv("GEMINI_API_KEY_1"), os.getenv("GEMINI_API_KEY_2"), os.getenv("GEMINI_API_KEY_3")]:
    if _k and _k.strip() and _k.strip() not in CHAVES_API: CHAVES_API.append(_k.strip())
if not CHAVES_API:
    print("🚨 Nenhuma GEMINI_API_KEY_* preenchida no .env. Abortando.", flush=True)
    sys.exit(1)
print(f"🔑 {len(CHAVES_API)} chave(s) Gemini carregada(s).", flush=True)
indice_chave_atual = 0
chaves_invalidas = set()

# 🔧 Cadeia de modelos: a cota diária é POR MODELO, então cada modelo extra multiplica a capacidade.
# O robô usa o primeiro e só cai para os seguintes se o anterior estiver sem cota, sobrecarregado (503) ou indisponível (404).
# Para trocar sem mexer no código: GEMINI_MODELOS="modeloA,modeloB,modeloC" no .env
MODELOS_IA = [m.strip() for m in os.getenv(
    "GEMINI_MODELOS",
    "gemini-3.1-flash-lite,gemini-3.5-flash-lite,gemini-3.8-flash"
).split(",") if m.strip()]

# 🔧 Chrome VISÍVEL por padrão (para validar o processo). Para voltar ao modo invisível: ROBO_CHROME_VISIVEL=0
CHROME_VISIVEL = os.getenv("ROBO_CHROME_VISIVEL", "1") == "1"
PASTA_DIAG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "diagnostico")

CAMINHO_REDE = r"\\192.168.100.13\Lello2\ARQUIVOS-DIGITALIZACAO\financeiro_imoveis\Condomínio\OUT26 - NOVA GESTÃO"
PASTA_LOCAL_PAGS = r'C:\Users\ryan.lima\Documents\Pagamentos\pags'

URL_LOGIN = "https://pi.lelloimoveis.com.br/portalImoveis/"
URL_MOVIMENTO = "https://pi.lelloimoveis.com.br/portalImoveis/pages/movimentoBoletoCondominio/movimentoBoletoCondominio.xhtml"

USUARIO_LELLO = ("ryan.lima")
SENHA_LELLO = ("Kary2002!")


# =============================================================
# CHAT GOOGLE
# =============================================================
def enviar_notificacao_chat(id_imovel, administradora, responsavel, tipo="SUCESSO", mensagem_erro=""):
    webhook_url = "https://chat.googleapis.com/v1/spaces/AAQA48Gjz8I/messages?key=AIzaSyDdI0hCZtE6vySjMm-WEfRq3CPzqKqqsHI&token=yL8jBOG6nVLI-LmGL9IRUcJDLBq2ugiQgjQ03r9u_Lc"
    if tipo == "AVISO": texto = f" *Aguardando o Google*\n\n*ID:* {id_imovel}\n*Detalhe:* {mensagem_erro}"
    elif tipo == "FILA": texto = f"🔄 *Automação*\n\n{mensagem_erro}"
    elif tipo == "VERBA_NEGATIVA": texto = f"⚠️ *Boleto Barrado: Verba Negativa*\n\n*ID:* {id_imovel}\n*Resp:* {responsavel}\n*Motivo:* {mensagem_erro}"
    else: return 
    try: requests.post(webhook_url, json={"text": texto})
    except: pass

# =============================================================
# BUSCADOR DINÂMICO E PROCESSAMENTO DE PDF E IA
# =============================================================
def buscar_pdf_inteligente(num_imovel, adm, resp, modo_leitura="REDE"):
    if modo_leitura == "LOCAL":
        if not os.path.exists(PASTA_LOCAL_PAGS): return None
        arquivos = os.listdir(PASTA_LOCAL_PAGS)
        return next((os.path.join(PASTA_LOCAL_PAGS, f) for f in arquivos if num_imovel in f and f.lower().endswith(".pdf")), None)
    else:
        avisou_chat = False
        while True:
            try:
                if not os.path.exists(CAMINHO_REDE): raise OSError(64, "Rede inacessível")
                arquivos = os.listdir(CAMINHO_REDE)
                pdf_encontrado = next((os.path.join(CAMINHO_REDE, f) for f in arquivos if num_imovel in f and f.lower().endswith(".pdf")), None)
                if avisou_chat: enviar_notificacao_chat("", "", "", "FILA", " *A VPN voltou!* O automação retomou o processamento da rede.")
                return pdf_encontrado
            except OSError as e:
                if not avisou_chat:
                    enviar_notificacao_chat(num_imovel, adm, resp, "FILA", f"🚨 *A VPN CAIU!* (Imóvel: {num_imovel}). Reconecte a VPN.")
                    avisou_chat = True
                print("⚠️ [REDE] VPN caiu. Aguardando conexão...", flush=True)
                time.sleep(10)

def comprimir_pdf_se_necessario(caminho_pdf):
    LIMITE_BYTES = 440 * 1024 
    tamanho_atual = os.path.getsize(caminho_pdf)
    if tamanho_atual <= LIMITE_BYTES: return caminho_pdf
        
    print(f"⚙️ PDF pesadão ({tamanho_atual//1024}KB). Comprimindo para menos de 450KB...", flush=True)
    novo_caminho = caminho_pdf.replace(".pdf", "_comprimido.pdf")
    try:
        imagens = []
        with pdfplumber.open(caminho_pdf) as pdf:
            for page in pdf.pages:
                img = page.to_image(resolution=85).original
                if img.mode in ("RGBA", "P"): img = img.convert("RGB")
                imagens.append(img)
        if imagens:
            imagens[0].save(novo_caminho, "PDF", resolution=85.0, save_all=True, append_images=imagens[1:])
            novo_tamanho = os.path.getsize(novo_caminho)
            if novo_tamanho > LIMITE_BYTES: print(f"⚠️ O PDF reduziu para {novo_tamanho//1024}KB, mas ainda pode esbarrar no limite.", flush=True)
            else: print(f" PDF comprimido com sucesso para {novo_tamanho//1024}KB!", flush=True)
            return novo_caminho
    except Exception as e: print(f"⚠️ Falha ao comprimir: {e}", flush=True)
    return caminho_pdf

def ler_texto_boleto(caminho_pdf):
    texto = ""
    try:
        with pdfplumber.open(caminho_pdf) as pdf:
            for pagina in pdf.pages: texto += (pagina.extract_text() or "") + "\n"
    except: pass
    return texto

# =============================================================
# 🚦 COORDENAÇÃO DE COTA DO GEMINI ENTRE OS ROBÔS (RYAN/JESSICA/ELLEN...)
# Cada operador é um processo separado. Antes, todos começavam na Chave 1 e
# giravam juntos, estourando a cota ao mesmo tempo. Agora todos consultam um
# "quadro de horários" compartilhado: cada chave só recebe 1 chamada a cada
# GEMINI_INTERVALO_S segundos (somando todos os robôs) e chaves/modelos sem cota
# ficam bloqueados até a cota voltar.
# =============================================================
PASTA_COORD = os.path.join(tempfile.gettempdir(), "robo_pagamentos_gemini")
ARQ_ESTADO = os.path.join(PASTA_COORD, "estado.json")
ARQ_TRAVA = os.path.join(PASTA_COORD, "estado.lock")
INTERVALO_S = float(os.getenv("GEMINI_INTERVALO_S", "6"))
IDS_CHAVES = [hashlib.sha1(k.encode()).hexdigest()[:8] for k in CHAVES_API]  # identifica a chave sem expô-la
COTA_DIARIA_ESGOTADA = False
modelos_indisponiveis = set()  # (índice da chave, modelo) que devolveram 404 nesta execução
_clientes_ia = {}
os.makedirs(PASTA_COORD, exist_ok=True)

class _Trava:
    def __enter__(self):
        while True:
            try:
                self.fd = os.open(ARQ_TRAVA, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                return self
            except FileExistsError:
                try:
                    if time.time() - os.path.getmtime(ARQ_TRAVA) > 20: os.remove(ARQ_TRAVA)  # trava órfã
                except OSError: pass
                time.sleep(0.05)
    def __exit__(self, *args):
        try: os.close(self.fd)
        except OSError: pass
        try: os.remove(ARQ_TRAVA)
        except OSError: pass

def _ler_estado():
    try:
        with open(ARQ_ESTADO, encoding="utf-8") as f: return json.load(f)
    except Exception: return {"proximo": {}, "bloqueio": {}}

def _gravar_estado(est):
    tmp = ARQ_ESTADO + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f: json.dump(est, f)
    os.replace(tmp, ARQ_ESTADO)

def _proxima_meia_noite_pacifico():
    # A cota DIÁRIA do Gemini zera à meia-noite do Pacífico (≈04h em São Paulo), não à meia-noite daqui.
    try:
        from zoneinfo import ZoneInfo
        agora = datetime.datetime.now(ZoneInfo("America/Los_Angeles"))
        return (agora + datetime.timedelta(days=1)).replace(hour=0, minute=1, second=0, microsecond=0).timestamp()
    except Exception:
        return time.time() + 4 * 3600

def bloquear_chave(idx, modelo, ate):
    with _Trava():
        est = _ler_estado()
        est["bloqueio"][f"{IDS_CHAVES[idx]}|{modelo}"] = ate
        _gravar_estado(est)

def reservar_chave_e_modelo(preferencia):
    """Escolhe (chave, modelo) livre com a menor fila e reserva o horário. Retorna (idx, modelo) ou (None, quando_libera)."""
    ordem = [MODELOS_IA[(preferencia + k) % len(MODELOS_IA)] for k in range(len(MODELOS_IA))]
    with _Trava():
        est = _ler_estado()
        agora = time.time()
        melhor, libera = None, None
        for modelo in ordem:
            for i in range(len(CHAVES_API)):
                if i in chaves_invalidas or (i, modelo) in modelos_indisponiveis: continue
                ate = float(est["bloqueio"].get(f"{IDS_CHAVES[i]}|{modelo}", 0))
                if ate > agora:
                    libera = ate if libera is None else min(libera, ate)
                    continue
                prox = max(agora, float(est["proximo"].get(IDS_CHAVES[i], 0)))
                if melhor is None or prox < melhor[1]: melhor = (i, prox, modelo)
            if melhor: break  # só usa o modelo reserva se o principal estiver sem chave livre
        if not melhor: return None, libera
        idx, slot, modelo = melhor
        est["proximo"][IDS_CHAVES[idx]] = slot + INTERVALO_S
        _gravar_estado(est)
    espera = slot - time.time()
    if espera > 0: time.sleep(espera)
    return idx, modelo

chaves_vertex = set()  # chaves "AQ." que o Google recusou no modo normal e passam a usar o modo Vertex (express)

def _cliente(idx):
    if idx not in _clientes_ia:
        if idx in chaves_vertex: _clientes_ia[idx] = genai.Client(vertexai=True, api_key=CHAVES_API[idx])
        else: _clientes_ia[idx] = genai.Client(api_key=CHAVES_API[idx])
    return _clientes_ia[idx]

def extrair_verbas_e_valores(caminho_pdf, id_imovel, adm, resp):
    global COTA_DIARIA_ESGOTADA
    if COTA_DIARIA_ESGOTADA: return []
    prompt = """
    Extraia APENAS as verbas da COTA DO CONDÔMINO. IGNORE balancetes e valores acima de 5000.
    Códigos: "231" (Cota, Água, Gás, 13º), "212" (Fundo Reserva), "211" (Fundo de Obras, RATEIO, QUASE TUDO QUE TIVER PARCELAS), "132" (IPTU), "33" (Extras).
    Regras: APENAS vírgula para decimais. Ignore traços. Sinal de menos (-) ANTES do valor APENAS se for DESCONTO.
    Retorne ESTRITAMENTE uma lista JSON. Exemplo: [{"descricao": "COTA", "valor": "512,46", "codigo": "231"}]
    """
    indice_modelo = 0

    try:
        with pdfplumber.open(caminho_pdf) as pdf:
            primeira_pagina = pdf.pages[0]
            imagem = primeira_pagina.to_image(resolution=200).original
            img_byte_arr = io.BytesIO()
            imagem.save(img_byte_arr, format='JPEG')
            img_bytes = img_byte_arr.getvalue()
    except Exception as e:
        print(f" Erro na leitura do PDF: {e}", flush=True)
        return []

    tentativas_maximas = 10
    tentativa_atual = 0

    config_ia = types.GenerateContentConfig(
        response_mime_type="application/json",
        temperature=0.0,
        safety_settings=[
            types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_HATE_SPEECH, threshold=types.HarmBlockThreshold.BLOCK_NONE),
            types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_HARASSMENT, threshold=types.HarmBlockThreshold.BLOCK_NONE),
            types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT, threshold=types.HarmBlockThreshold.BLOCK_NONE),
            types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT, threshold=types.HarmBlockThreshold.BLOCK_NONE),
        ]
    )

    while tentativa_atual < tentativas_maximas:
        if COTA_DIARIA_ESGOTADA: return []
        idx, modelo_ia = reservar_chave_e_modelo(indice_modelo)
        if idx is None:
            quando_libera = modelo_ia
            if quando_libera is None:
                print("⚠️ Nenhuma combinação chave/modelo utilizável (chaves recusadas ou modelos indisponíveis). Confira o .env e GEMINI_MODELOS.", flush=True)
                return []
            espera = quando_libera - time.time()
            if espera <= 120:
                print(f"⏳ Todas as chaves no limite por minuto. Aguardando {int(espera) + 1}s...", flush=True)
                time.sleep(max(espera, 1) + 1)
                tentativa_atual += 1
                continue
            COTA_DIARIA_ESGOTADA = True
            hora = datetime.datetime.fromtimestamp(quando_libera).strftime('%H:%M')
            print(f"🛑 COTA DIÁRIA do Gemini esgotada em todas as chaves/modelos. Só volta por volta de {hora}. Ative o faturamento no Google AI Studio ou use chaves de outros projetos.", flush=True)
            return []

        try:
            resposta = _cliente(idx).models.generate_content(
                model=modelo_ia,
                contents=[types.Part.from_bytes(data=img_bytes, mime_type='image/jpeg'), prompt],
                config=config_ia
            )

            texto_json = resposta.text.replace('```json', '').replace('```', '').strip()
            verbas_encontradas = json.loads(texto_json)

            if isinstance(verbas_encontradas, dict):
                for key, val in verbas_encontradas.items():
                    if isinstance(val, list):
                        verbas_encontradas = val
                        break
                else:
                    verbas_encontradas = [verbas_encontradas]

            time.sleep(2)
            if verbas_encontradas and len(verbas_encontradas) > 0:
                return verbas_encontradas

        except Exception as e:
            erro_txt = str(e)
            erro_str = erro_txt.upper()
            if "429" in erro_str or "EXHAUSTED" in erro_str:
                q = re.search(r"quotaId['\"]?\s*[:=]\s*['\"]([^'\"]+)", erro_txt, re.I)
                quota_id = q.group(1) if q else "?"
                if "PERDAY" in erro_str.replace("_", "").replace(" ", ""):
                    bloquear_chave(idx, modelo_ia, _proxima_meia_noite_pacifico())
                    print(f"⚠️ [COTA] Chave {idx + 1} sem cota DIÁRIA no modelo {modelo_ia} ({quota_id}).", flush=True)
                    continue  # não gasta tentativa: essa combinação já ficou bloqueada, o próximo ciclo escolhe outra
                else:
                    m = re.search(r"retryDelay['\"]?\s*[:=]\s*['\"]?(\d+(?:\.\d+)?)", erro_txt, re.I)
                    atraso = float(m.group(1)) if m else 30.0
                    bloquear_chave(idx, modelo_ia, time.time() + atraso + 1)
                    print(f"⚠️ [COTA] Chave {idx + 1} no limite por minuto ({modelo_ia}, {quota_id}). Pausa de {int(atraso) + 1}s só nessa chave.", flush=True)
                tentativa_atual += 1
                continue
            elif "404" in erro_str or "NOT_FOUND" in erro_str:
                modelos_indisponiveis.add((idx, modelo_ia))
                print(f"⚠️ Modelo '{modelo_ia}' indisponível para a chave {idx + 1} (404). Pulando para o próximo da cadeia.", flush=True)
                continue
            elif "403" in erro_str or "401" in erro_str or "PERMISSION_DENIED" in erro_str or "API_KEY_INVALID" in erro_str or "UNAUTHENTICATED" in erro_str:
                if CHAVES_API[idx].startswith("AQ.") and idx not in chaves_vertex:
                    # Chaves no formato "AQ." costumam ser do Vertex AI (express mode): tenta de novo nesse modo
                    chaves_vertex.add(idx); _clientes_ia.pop(idx, None)
                    print(f"⚠️ Chave {idx + 1} (formato AQ.) recusada no modo normal. Tentando como chave Vertex...", flush=True)
                    tentativa_atual += 1
                    continue
                chaves_invalidas.add(idx)
                print(f"⚠️ Chave {idx + 1} recusada (403/inválida). Verifique o .env.", flush=True)
                tentativa_atual += 1
                continue
            elif "503" in erro_str or "UNAVAILABLE" in erro_str or "500" in erro_str or "DEADLINE" in erro_str or "OVERLOADED" in erro_str:
                espera = min(5 * (2 ** tentativa_atual), 40)
                indice_modelo += 1
                print(f"⚠️ Google sobrecarregado (503). Aguardando {espera}s e alternando o modelo...", flush=True)
                time.sleep(espera)
                tentativa_atual += 1
                continue
            else:
                print(f"⚠️ IA falhou ou recusou ({modelo_ia}): {erro_str[:200]}", flush=True)

        tentativa_atual += 1
        if tentativa_atual < tentativas_maximas: time.sleep(10)

    return []

def extrair_linha_digitavel(texto):
    padrao = r'\d{5}[\.\s]?\d{5}[\.\s]?\d{5}[\.\s]?\d{6}[\.\s]?\d{5}[\.\s]?\d{6}[\.\s]?\d[\.\s]?\d{14}'
    res = re.search(padrao, texto.replace('\n', ' '))
    return re.sub(r'\D', '', res.group(0)) if res else ""

def preparar_dados_ia_background(tarefa):
    if not tarefa['pdf']: return None
    print(f"🔍 [BACKGROUND] Lendo boleto {tarefa['num']} silenciosamente...", flush=True)
    try:
        texto_bruto = ler_texto_boleto(tarefa['pdf'])
        linha_dig = extrair_linha_digitavel(texto_bruto)
        verbas = extrair_verbas_e_valores(tarefa['pdf'], tarefa['num'], tarefa['adm'], tarefa['resp'])
        pdf_processado = comprimir_pdf_se_necessario(tarefa['pdf'])
        return (pdf_processado, linha_dig, verbas)
    except: return None

# =============================================================
# AUTOMAÇÃO WEB: GHOST MODE, HUMANIZAÇÃO E ERROS
# =============================================================
def aguardar_carregamento_lello(driver):
    try:
        wait_longo = WebDriverWait(driver, 45) 
        wait_longo.until(lambda d: d.execute_script("return document.readyState === 'complete' && (typeof jQuery === 'undefined' || jQuery.active === 0);"))
        wait_longo.until(EC.invisibility_of_element_located((By.CLASS_NAME, "ui-widget-overlay")))
        wait_longo.until(EC.invisibility_of_element_located((By.CLASS_NAME, "ui-dialog-mask")))
        time.sleep(0.8) 
    except: time.sleep(3) 

def checar_erro_na_tela(driver):
    time.sleep(1.5) 
    try:
        modais = driver.find_elements(By.XPATH, "//div[contains(@class, 'ui-dialog') and not(contains(@style, 'display: none'))]")
        for modal in modais:
            if "Erro" in modal.text or "Aviso" in modal.text or "Antes de adicionar" in modal.text:
                texto_erro = modal.find_element(By.XPATH, ".//div[contains(@class, 'ui-dialog-content')]").text
                try:
                    botao_ok = modal.find_element(By.XPATH, ".//button[contains(translate(., 'ok', 'OK'), 'OK')]")
                    driver.execute_script("arguments[0].click();", botao_ok)
                    time.sleep(0.5) 
                except:
                    try: 
                        botao_x = driver.find_element(By.XPATH, "//a[contains(@class, 'ui-dialog-titlebar-close')]")
                        driver.execute_script("arguments[0].click();", botao_x)
                        time.sleep(0.5)
                    except: pass
                if texto_erro.strip(): return texto_erro.strip()
    except: pass
    return None

def ghost_input(driver, wait, xpath_ou_id, texto, is_id=False):
    try:
        el = wait.until(EC.presence_of_element_located((By.ID if is_id else By.XPATH, xpath_ou_id)))
        driver.execute_script("""
            arguments[0].focus(); arguments[0].value = arguments[1];
            arguments[0].dispatchEvent(new Event('input', { bubbles: true })); arguments[0].dispatchEvent(new Event('change', { bubbles: true })); arguments[0].dispatchEvent(new Event('blur', { bubbles: true }));
            if (window.jQuery) { window.jQuery(arguments[0]).trigger('change'); }
        """, el, texto)
        return True
    except: return False

def diagnosticar(driver, etapa):
    """Mostra URL/título atuais e salva um print da tela para entender onde o portal travou."""
    try:
        print(f"🔎 [DIAG] Etapa: {etapa} | URL: {driver.current_url} | Título: {driver.title}", flush=True)
    except Exception: pass
    try:
        os.makedirs(PASTA_DIAG, exist_ok=True)
        arq = os.path.join(PASTA_DIAG, f"{datetime.datetime.now().strftime('%H%M%S')}_{re.sub(r'[^A-Za-z0-9_]+', '_', etapa)}.png")
        driver.save_screenshot(arq)
        print(f"🔎 [DIAG] Print salvo em: {arq}", flush=True)
    except Exception: pass

def realizar_login(driver, wait):
    print("🔐 Realizando login...", flush=True)
    driver.get(URL_LOGIN)
    campo_user = wait.until(EC.presence_of_element_located((By.XPATH, "//input[@type='text']")))
    campo_user.clear(); campo_user.send_keys(USUARIO_LELLO)
    campo_senha = wait.until(EC.presence_of_element_located((By.XPATH, "//input[@type='password']")))
    campo_senha.clear(); campo_senha.send_keys(SENHA_LELLO)
    driver.execute_script("arguments[0].click();", wait.until(EC.element_to_be_clickable((By.XPATH, "//button[contains(., 'Entrar')]"))))
    # 🔧 Antes só dormia 4s sem conferir nada. Agora confere se saiu da tela de login.
    try:
        WebDriverWait(driver, 25).until(EC.invisibility_of_element_located((By.XPATH, "//input[@type='password']")))
    except TimeoutException:
        diagnosticar(driver, "login_nao_concluiu")
        return False
    time.sleep(2)
    print(f"🔐 Login concluído. URL: {driver.current_url}", flush=True)
    return True

def preencher_rateio(driver, wait, verbas):
    if not verbas or len(verbas) == 0:
        return False, "A IA não extraiu nenhuma verba válida do PDF!"
    for v in verbas:
        print(f" [Lançamento] Verba: {v['codigo']} | R$ {v['valor']} | Desc: {v['descricao']}", flush=True)
        aguardar_carregamento_lello(driver)
        ghost_input(driver, wait, "//input[@placeholder='[Descrição da Verba do Boleto]']", v['descricao'])
        time.sleep(0.5)
        try:
            campo_valor = wait.until(EC.element_to_be_clickable((By.XPATH, "(//input[@placeholder='[Valor]'])[last()]")))
            driver.execute_script("arguments[0].click(); arguments[0].focus();", campo_valor)
            time.sleep(0.3)
            driver.execute_script("arguments[0].value = '';", campo_valor)
            campo_valor.send_keys(Keys.END)
            for _ in range(15): campo_valor.send_keys(Keys.BACKSPACE)
            time.sleep(0.3)
            campo_valor.send_keys(v['valor'].replace('.', ''))
            time.sleep(0.3)
            campo_valor.send_keys(Keys.TAB)
            time.sleep(0.5)
        except Exception as e: print(f" Erro ao digitar o valor: {e}", flush=True)
        try:
            xpath_setinha = "//label[text()='[Verba]']/following-sibling::div[contains(@class, 'ui-selectonemenu-trigger')]"
            driver.execute_script("arguments[0].click();", wait.until(EC.element_to_be_clickable((By.XPATH, xpath_setinha))))
            time.sleep(1.5) 
            driver.execute_script("arguments[0].click();", wait.until(EC.presence_of_element_located((By.XPATH, f"//div[contains(@class, 'ui-selectonemenu-panel') and contains(@style, 'display: block')]//li[normalize-space(text())='{v['codigo']}']"))))
            aguardar_carregamento_lello(driver) 
        except: pass
        try:
            driver.execute_script("arguments[0].click();", wait.until(EC.presence_of_element_located((By.XPATH, "//button[contains(., 'ADICIONAR') or contains(., 'Adicionar')]"))))
            aguardar_carregamento_lello(driver)
            msg_erro = checar_erro_na_tela(driver)
            if msg_erro: return False, msg_erro 
        except: pass
    try:
        aguardar_carregamento_lello(driver)
        driver.execute_script("arguments[0].click();", wait.until(EC.presence_of_element_located((By.XPATH, "//button[contains(., 'Gravar')] | //span[text()='Gravar']/parent::button"))))
        aguardar_carregamento_lello(driver)
        msg_erro = checar_erro_na_tela(driver)
        if msg_erro: return False, msg_erro
        print(" PROCESSO GRAVADO NO SITE!", flush=True)
        return True, ""
    except: return False, "Falha ao clicar no Gravar."

def interagir_portal(driver, wait, num_imovel, pdf_path, linha_dig, verbas):
    etapa = "início"
    for tentativa_mestra in range(1, 4):
        try:
            etapa = "abrindo tela de movimento de boleto"
            driver.get(URL_MOVIMENTO)
            time.sleep(3)
            etapa = "selecionando filtros Imóvel/Código"
            for i, txt in [(2, 'móvel'), (3, 'digo')]:
                driver.execute_script("arguments[0].click();", wait.until(EC.presence_of_element_located((By.XPATH, f"(//div[contains(@class, 'trigger')])[ {i}]"))))
                driver.execute_script("arguments[0].click();", wait.until(EC.presence_of_element_located((By.XPATH, f"//li[contains(text(), '{txt}')]"))))
                time.sleep(1)
            etapa = "digitando número do imóvel"
            campo_imovel = wait.until(EC.presence_of_element_located((By.XPATH, "//input[contains(@class, 'ui-inputfield') and not(@type='hidden')]")))
            driver.execute_script("arguments[0].click(); arguments[0].focus();", campo_imovel)
            time.sleep(0.3)
            campo_imovel.send_keys(Keys.CONTROL + "a")
            campo_imovel.send_keys(Keys.BACKSPACE)
            time.sleep(0.3)
            for digito in str(num_imovel):
                campo_imovel.send_keys(digito)
                time.sleep(0.1)
            time.sleep(0.3)
            campo_imovel.send_keys(Keys.TAB)
            time.sleep(0.5)
            etapa = "clicando em Consultar"
            driver.execute_script("arguments[0].click();", wait.until(EC.element_to_be_clickable((By.XPATH, "//button[contains(., 'Consultar')]"))))
            time.sleep(4)
            
            try:
                texto_tela = driver.find_element(By.TAG_NAME, "body").text
                if str(num_imovel) not in texto_tela:
                    print(f"🚨 TRAVA DE SEGURANÇA ATIVADA! O portal carregou a ficha errada. (Esperava: {num_imovel})", flush=True)
                    return False, f"Segurança: O portal carregou o imóvel errado."
            except: pass 
            
            etapa = "anexando o PDF"
            wait.until(EC.presence_of_element_located((By.XPATH, "//input[@type='file']"))).send_keys(os.path.abspath(pdf_path))
            msg_erro = checar_erro_na_tela(driver)
            if msg_erro: 
                if "unidade não foi encontrada" in msg_erro.lower() and tentativa_mestra < 3:
                    print(f"⚙️ [RETRY] Portal engasgou com a Unidade. Recarregando a página... ({tentativa_mestra}/3)", flush=True)
                    continue 
                return False, msg_erro
            
            print("⚙️ PAUSA 5s: Aguardando o grid carregar...", flush=True)
            time.sleep(5)
            etapa = "abrindo o lápis Editar do grid"
            link_editar = wait.until(EC.presence_of_element_located((By.XPATH, "//a[@title='Editar' and contains(@class, 'ui-commandlink')]")))
            driver.execute_script("arguments[0].click();", link_editar)
            time.sleep(6) 
            
            etapa = "preenchendo linha digitável / rateio"
            for tentativa in range(1, 4):
                ghost_input(driver, wait, "formInsere:inputLinhaDigitavel", "", is_id=True)
                time.sleep(1)
                ghost_input(driver, wait, "formInsere:inputLinhaDigitavel", linha_dig, is_id=True)
                print(f"⚙️ PAUSA 5s: Inserindo código de barras... (Tentativa {tentativa}/3)", flush=True)
                time.sleep(5)
                msg_pre_rateio = checar_erro_na_tela(driver)
                if msg_pre_rateio:
                    if "unidade não foi encontrada" in msg_pre_rateio.lower():
                        time.sleep(2)
                        continue 
                    else: return False, msg_pre_rateio
                
                sucesso, msg = preencher_rateio(driver, wait, verbas)
                if sucesso: return True, ""
                if "antes de adicionar" in msg.lower() or "dados da verba" in msg.lower() or "unidade não foi encontrada" in msg.lower():
                    if tentativa == 3: break 
                    time.sleep(2); continue 
                else: break
            return False, msg if 'msg' in locals() else "Falha no processo."
        except Exception as e: 
            detalhe = (str(e).strip().splitlines() or [""])[0][:120]
            print(f"🔎 [PORTAL] Tentativa {tentativa_mestra}/3 travou na etapa '{etapa}': {type(e).__name__} {detalhe}", flush=True)
            diagnosticar(driver, f"imovel_{num_imovel}_{etapa}")
            if tentativa_mestra == 3: return False, f"Erro inesperado no portal (etapa: {etapa} | {type(e).__name__})"
    return False, "Erro inesperado no portal: tentativas esgotadas"


# =============================================================
# MOTOR PRINCIPAL (VIA API/ORQUESTRADOR)
# =============================================================
def processar():
    try:
        tempo_inicio = time.time()
        fila_bruta = buscar_boletos_coletados()
        if not fila_bruta:
            print(" NENHUMA TAREFA PENDENTE RETORNADA PELO SISTEMA.", flush=True)
            return

        tarefas_pendentes = []
        for d in fila_bruta:
            num = str(d.get('imovel', '')).strip()
            resp = str(d.get('responsavel', d.get('sacado', ''))).strip().lower()
            adm = str(d.get('administradora', d.get('empreendimento', 'N/D'))).strip()
            emp = str(d.get('empreendimento', '')).strip().lower()
            venc = str(d.get('vencimento', '')).strip()

            if filtro_resp and filtro_resp not in resp: continue
            if filtro_emp and filtro_emp not in emp: continue
            if len(filtro_dias) > 0 and venc:
                dia_boleto = venc.split('/')[0] if '/' in venc else ""
                if dia_boleto not in filtro_dias: continue

            if num:
                pdf_original = buscar_pdf_inteligente(num, adm, resp, modo_leitura="REDE")
                tarefas_pendentes.append({'num': num, 'adm': adm, 'resp': resp, 'pdf': pdf_original})

        if not tarefas_pendentes:
            print(f"⚠️ Nenhum imóvel passou pelos filtros.", flush=True)
            return

        print(f"🚀 INICIANDO ORQUESTRAÇÃO DE PAGAMENTO ({len(tarefas_pendentes)} imóveis)", flush=True)
        
        if not USUARIO_LELLO or not SENHA_LELLO:
            print("🚨 LELLO_USUARIO / LELLO_SENHA não encontrados no .env. Abortando.", flush=True)
            return

        chrome_options = webdriver.ChromeOptions()
        if not CHROME_VISIVEL:
            chrome_options.add_argument("--headless=new")
            chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--window-size=1920,1080")
        chrome_options.add_argument("--start-maximized")
        chrome_options.add_experimental_option("detach", True)   # Chrome fica aberto no fim para você conferir
        
        driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=chrome_options)
        wait = WebDriverWait(driver, 15)
        
        if not realizar_login(driver, wait):
            print("🚨 Login no portal não concluiu (credencial errada, sessão duplicada ou portal fora). Nenhum imóvel foi alterado no sistema.", flush=True)
            return

        with ThreadPoolExecutor(max_workers=1) as executor:
            futuro_atual = executor.submit(preparar_dados_ia_background, tarefas_pendentes[0])
            for index, tarefa in enumerate(tarefas_pendentes):
                print(f"\n🚀 A PROCESSAR IMÓVEL: {tarefa['num']}", flush=True)
                
                futuro_proximo = None
                if index + 1 < len(tarefas_pendentes):
                    futuro_proximo = executor.submit(preparar_dados_ia_background, tarefas_pendentes[index + 1])

                resultado_ia = futuro_atual.result()

                if COTA_DIARIA_ESGOTADA:
                    print("🛑 Rodada interrompida: cota diária do Gemini esgotada. Os imóveis restantes continuam COLETADO.", flush=True)
                    break

                if resultado_ia:
                    pdf_processado, linha_dig, verbas = resultado_ia
                    if not verbas:
                        print(f"⚠️ IA sem resposta para o imóvel {tarefa['num']} (Google instável). Pulado SEM alterar status: continua COLETADO para a próxima rodada.", flush=True)
                        futuro_atual = futuro_proximo; continue
                    verba_negativa = next((v for v in verbas if str(v.get('valor', '')).strip().startswith('-')), None)
                    if verba_negativa:
                        motivo_erro = f"Possui desconto/verba negativa ({verba_negativa.get('descricao', 'N/D')}: R$ {verba_negativa.get('valor', '')})"
                        print(f"⚠️ {motivo_erro}", flush=True)
                        informar_status_ao_site(tarefa['num'], deu_certo=False, msg_erro=motivo_erro)
                        futuro_atual = futuro_proximo; continue
                        
                    sucesso, erro_msg = interagir_portal(driver, wait, tarefa['num'], pdf_processado, linha_dig, verbas)
                    
                    if sucesso:
                        informar_status_ao_site(tarefa['num'], deu_certo=True)
                    else:
                        print(f" Portal barrou o lançamento: {erro_msg}", flush=True)
                        if erro_msg.startswith("Erro inesperado no portal"):
                            print(f"⚠️ Falha técnica (não é rejeição do portal): imóvel {tarefa['num']} continua COLETADO para nova tentativa.", flush=True)
                        else:
                            informar_status_ao_site(tarefa['num'], deu_certo=False, msg_erro=erro_msg)
                else:
                    print(f"⚠️ PDF ausente ou falha na IA", flush=True)
                    informar_status_ao_site(tarefa['num'], deu_certo=False, msg_erro="PDF ausente ou falha na IA")
                
                futuro_atual = futuro_proximo

        tempo_fim = time.time()
        minutos = int(tempo_fim - tempo_inicio) // 60
        segundos = int(tempo_fim - tempo_inicio) % 60
        print(f" EXECUÇÃO FINALIZADA. TEMPO: {minutos}m {segundos}s", flush=True)

    except Exception as e: 
        print(f" Erro Crítico Geral: {e}", flush=True)

if __name__ == "__main__": 
    processar()