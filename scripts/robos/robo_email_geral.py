import imaplib
import email
from email.header import decode_header
import os
import ssl  # <-- Novo aqui

# --- CONTORNO PARA BLOQUEIO DE SSL EM REDE CORPORATIVA ---
os.environ['CURL_CA_BUNDLE'] = ''
os.environ['REQUESTS_CA_BUNDLE'] = ''
try:
    _create_unverified_https_context = ssl._create_unverified_context
except AttributeError:
    pass
else:
    ssl._create_default_https_context = _create_unverified_https_context
# ---------------------------------------------------------
from dotenv import load_dotenv
load_dotenv()
import PyPDF2
import pikepdf
import re
import requests
import unicodedata 
import json
import time
import sys
from datetime import datetime
from io import BytesIO
import pandas as pd
from google import genai 
from bs4 import BeautifulSoup 
import urllib.parse
from email.utils import parseaddr
from thefuzz import fuzz    
import os
import sys

ARQUIVO_TRAVA_API = r"C:\Users\ryan.lima\Documents\SistemaImoveis\trava_api_finmail.txt"

def checar_trava_diaria():
    """Verifica se o robô já foi bloqueado no dia de hoje."""
    if os.path.exists(ARQUIVO_TRAVA_API):
        with open(ARQUIVO_TRAVA_API, 'r', encoding='utf-8') as f:
            data_trava = f.read().strip()
            
        hoje = datetime.now().strftime('%Y-%m-%d')
        # Se a data salva for igual à de hoje, retorna True (bloqueado)
        if data_trava == hoje:
            return True
    return False

def ativar_trava_diaria():
    """Cria a trava registrando a data atual."""
    hoje = datetime.now().strftime('%Y-%m-%d')
    with open(ARQUIVO_TRAVA_API, 'w', encoding='utf-8') as f:
        f.write(hoje)

# =======================================================================
# CONFIGURAÇÕES DO SISTEMA (API) E PASTAS DA REDE
# =======================================================================
EMAIL_CONTA = "ctrlcond@lello.com.br" 
SENHA_APP = os.getenv("SENHA_APP_GMAIL") 
WEBHOOK_URL = "https://chat.googleapis.com/v1/spaces/AAQAdojcNRA/messages?key=AIzaSyDdI0hCZtE6vySjMm-WEfRq3CPzqKqqsHI&token=RGzjUWzI1_hL6jgFgcnj7PH7jTbHtP4uePyoWeokwGc"

URL_API_PENDENCIAS = "http://127.0.0.1:5000/api/automacao/pendencias"
URL_API_CONSULTA   = "http://127.0.0.1:5000/api/automacao/consultar_imovel"
URL_API_COLETA     = "http://127.0.0.1:5000/api/automacao/coletar"
URL_API_RELACIONAMENTO = "http://127.0.0.1:5000/api/automacao/relacionamento"
URL_API_PDF_PROTEGIDO = "http://127.0.0.1:5000/api/automacao/pdf_protegido"
TOKEN_SEGURANCA    = os.getenv("TOKEN_SEGURANCA")
NOME_ROBO          = "AUTOMAÇÃO E-MAIL GERAL"

# 🎯 MÊS ALVO — acompanha a virada de mês sozinho.
# Prioridade: 1) mês enviado pelo painel (argumento, ex.: "11")  2) mês da BASE ATIVA (consulta a API do painel)
#             3) mês do calendário (só se a API estiver fora do ar).
URL_API_MES_ALVO   = "http://127.0.0.1:5000/api/automacao/mes_alvo"

def descobrir_mes_alvo():
    mes_arg = sys.argv[1].strip() if len(sys.argv) > 1 else ""
    mes_api, ano_api = "", None
    try:
        resp = requests.post(URL_API_MES_ALVO, json={"token": TOKEN_SEGURANCA}, timeout=15).json()
        if resp.get("sucesso"):
            mes_api, ano_api = str(resp["mes"]).zfill(2), int(resp["ano"])
    except Exception as e:
        print(f"⚠️ Não foi possível consultar o mês da base ativa na API: {e}", flush=True)
    mes = mes_arg or mes_api or datetime.now().strftime("%m")
    usar_ano_api = bool(ano_api) and (not mes_arg or mes_api == mes_arg.zfill(2))
    return mes, (ano_api if usar_ano_api else datetime.now().year)

MES_ALVO, ANO_ALVO = descobrir_mes_alvo()

PASTA_DESTINO = r"\\192.168.100.13\Lello2\ARQUIVOS-DIGITALIZACAO\financeiro_imoveis\Condomínio\boleto__Cond_antes liax"
PASTA_ARQUIVO_FINAL = r"\\192.168.100.13\Lello2\ARQUIVOS-DIGITALIZACAO\financeiro_imoveis\Condomínio\JUL26 - NOVA GESTÃO"

CHAVES_API_GEMINI = [
    os.getenv("GEMINI_API_KEY_1"), 
    os.getenv("GEMINI_API_KEY_2"), 
    os.getenv("GEMINI_API_KEY_3") 
]
INDICE_CHAVE_ATUAL = 0
client = genai.Client(api_key=CHAVES_API_GEMINI[INDICE_CHAVE_ATUAL])

# =======================================================================
# MOTOR DE COMUNICAÇÃO E CRUZAMENTO INTELIGENTE (DEPARA)
# =======================================================================
def buscar_pendencias_api():
    try:
        resposta = requests.post(URL_API_PENDENCIAS, json={"token": TOKEN_SEGURANCA, "colaborador": ""}).json()
        if resposta.get('sucesso'):
            return resposta.get('dados', [])
    except Exception as e:
        print(f" Erro de conexão com a API de Pendências: {e}", flush=True)
    return []

def descobrir_id_por_similaridade(texto_pdf, pendencias_api):
    if not texto_pdf or len(texto_pdf) < 10: 
        return None

    texto_upper = limpar_texto(texto_pdf)
    melhor_id = None
    maior_certeza = 0

    for caso in pendencias_api:
        num_imovel = str(caso.get('imovel', '')).strip()
        
        nome_cliente = limpar_texto(caso.get('sacado', ''))
        nome_condominio = limpar_texto(caso.get('empreendimento', ''))
        nome_adm = limpar_texto(caso.get('administradora', ''))
        endereco = limpar_texto(caso.get('endereco', ''))
        d_lote = limpar_texto(caso.get('d_lote', ''))

        if not num_imovel:
            continue

        numero_unidade = ""
        texto_busca_unidade = f"{endereco} {d_lote}"
        match_unidade = re.search(r'\b(?:APARTAMENTO|APTO|APT|AP|UNIDADE|UNID|CASA|SALA|CONJ|CJ|LOTE)[\s\.\-\:]*(\d+[A-Z]?)\b', texto_busca_unidade)
        
        if match_unidade:
            numero_unidade = match_unidade.group(1)
        else:
            numeros_soltos = re.findall(r'\d+', d_lote)
            numero_unidade = numeros_soltos[-1] if numeros_soltos else ""

        numero_unidade = numero_unidade.lstrip('0')

        if numero_unidade:
            if not re.search(rf'\b0*{re.escape(numero_unidade)}\b', texto_upper):
                continue

        score_nome = fuzz.token_set_ratio(nome_cliente, texto_upper) if nome_cliente else 0
        score_condo = fuzz.token_set_ratio(nome_condominio, texto_upper) if nome_condominio else 0
        score_adm = fuzz.token_set_ratio(nome_adm, texto_upper) if nome_adm else 0

        certeza_atual = (score_nome + score_condo + score_adm) / 3

        if certeza_atual > maior_certeza:
            maior_certeza = certeza_atual
            melhor_id = num_imovel

    if maior_certeza >= 85:
        print(f"🎯 [DEPARA BASE] Match Seguro! Imóvel {melhor_id} validado com {maior_certeza:.1f}% de certeza.", flush=True)
        return melhor_id

    return None

def validar_imovel_api(id_imovel):
    try:
        dados = {"token": TOKEN_SEGURANCA, "imovel": str(id_imovel)}
        resposta = requests.post(URL_API_CONSULTA, json=dados).json()
        return resposta
    except Exception as e: return None


def validar_match_com_ia(id_imovel, unidade_ia, sacado_ia, texto_total, tentativas=0):
    if not id_imovel:
        return False, 0

    prompt = f"""
    Você é um auditor de boletos de condomínio e precisa validar se o imóvel candidato está correto.
    Dados extraídos do boleto/email:
    IMOVEL_CANDIDATO: {id_imovel}
    UNIDADE: {unidade_ia or 'N/A'}
    SACADO: {sacado_ia or 'N/A'}
    TEXTO COMPLETO: {texto_total[:1500]}

    Com base nessas informações, responda apenas no formato:
    MATCH: SIM ou NAO
    CONFIANCA: XX%
    JUSTIFICATIVA: [uma frase curta]
    """
    try:
        resposta = client.models.generate_content(model='gemini-3.1-flash-lite', contents=prompt)
        texto = resposta.text.strip().upper()
        match = re.search(r'MATCH:\s*(SIM|NAO)', texto)
        conf = re.search(r'CONFIANCA:\s*(\d{1,3})', texto)
        if not match or not conf:
            return False, 0
        return match.group(1) == 'SIM', int(conf.group(1))
    except Exception as e:
        if "429" in str(e) and alternar_chave_api() and tentativas < len(CHAVES_API_GEMINI):
            return validar_match_com_ia(id_imovel, unidade_ia, sacado_ia, texto_total, tentativas + 1)
        return False, 0

def dar_baixa_api(id_imovel, status_novo="COLETADO", observacao=""):
    try:
        dados = {
            "token": TOKEN_SEGURANCA, 
            "imovel": str(id_imovel), 
            "nome_robo": NOME_ROBO,
            "status": status_novo,
            "observacao": observacao
        }
        resposta = requests.post(URL_API_COLETA, json=dados)
        return resposta.status_code == 200
    except Exception as e: return False

def mapear_imoveis_por_condominio(assunto, corpo, pendencias_api):
    ids_encontrados = []
    texto_upper = limpar_texto(assunto + " " + corpo)
    
    condominios_unicos = {}
    for p in pendencias_api:
        cond_original = limpar_texto(p.get('empreendimento', ''))
        if not cond_original or len(cond_original) < 3: continue
        
        cond_limpo = cond_original
        palavras_remover = ['CONDOMINIO', 'EDIFICIO', 'RESIDENCIAL', 'COMERCIAL', 'TORRE', 'CONJUNTO']
        for pal in palavras_remover:
            cond_limpo = cond_limpo.replace(pal, '').strip()
            
        if len(cond_limpo) < 3: continue 
            
        if cond_original not in condominios_unicos: 
            condominios_unicos[cond_original] = {'chave': cond_limpo, 'ids': []}
        condominios_unicos[cond_original]['ids'].append(p.get('imovel'))

    for cond_original, dados_cond in condominios_unicos.items():
        nome_predio = dados_cond['chave']
        if nome_predio in texto_upper or fuzz.partial_ratio(nome_predio, texto_upper) >= 95:
            ids_encontrados.extend(dados_cond['ids'])
            
    return list(set(ids_encontrados))

def registrar_relacionamento_api(lista_ids, tipo_comunicado, assunto, link_gmail):
    if not lista_ids: return False
    try:
        dados = {
            "token": TOKEN_SEGURANCA,
            "imoveis": lista_ids,
            "tipo": tipo_comunicado,
            "assunto": assunto,
            "link": link_gmail
        }
        requests.post(URL_API_RELACIONAMENTO, json=dados)
        return True
    except: return False

# =======================================================================
# LÓGICA DE GMAIL, MARCADORES E IA DE TEXTO
# =======================================================================
def organizar_gmail_marcadores(mail, uid_email, assunto, remetente_email):
    
    remetente_decodificado = ""
    for texto, encoding in decode_header(str(remetente_email)):
        if isinstance(texto, bytes):
            try: remetente_decodificado += texto.decode(encoding or 'utf-8', errors='ignore')
            except: remetente_decodificado += texto.decode('utf-8', errors='ignore')
        else:
            remetente_decodificado += str(texto)

    assunto_limpo = limpar_texto(assunto).lower()
    remetente_limpo = remetente_decodificado.lower()
    marcador_destino = "DIVERSOS"

    if "boleto" in assunto_limpo or "cota" in assunto_limpo or "fatura" in assunto_limpo or "mensalidade" in assunto_limpo or "vencimento" in assunto_limpo:
        administradora = "OUTRAS"
        regras_adm = {"gk ": "GK", "gkcondominios": "GK", "lello": "LELLO", "habita": "HABITA", "auxiliadora": "AUXILIADORA", "guarida": "GUARIDA", "bancobrasil": "BANCO DO BRASIL", "itau": "ITAU", "webware": "WEBWARE"}
        texto_busca = f"{remetente_limpo} {assunto_limpo}"
        
        for chave, nome in regras_adm.items():
            if chave in texto_busca:
                administradora = nome
                break
                
        if administradora == "OUTRAS":
            nome_remetente, endereco_email = parseaddr(remetente_decodificado)
            nome_limpo = limpar_texto(nome_remetente).upper()
            palavras_remover = ['CONDOMINIOS', 'CONDOMINIO', 'ADMINISTRADORA', 'ADM', 'IMOVEIS', 'S/A', 'LTDA', 'RESIDENCIAL', 'EDIFICIO', 'EDIF', 'TORRES', 'TORRE', 'VILA', 'VILLA', 'PARQUE', 'PARK', 'CONJUNTO']
            for p in palavras_remover: 
                nome_limpo = nome_limpo.replace(p, '')
            nome_limpo = nome_limpo.strip()
            
            if nome_limpo and len(nome_limpo) > 2: 
                administradora = nome_limpo.split()[0][:15] 
            else:
                try: administradora = endereco_email.split('@')[1].split('.')[0].upper()
                except: administradora = "OUTRAS"
                
            if administradora in ['NAO-RESPONDA', 'NO-REPLY', 'CONTATO', 'FINANCEIRO', 'FATURAMENTO', 'BOLETO', 'OUTRAS', 'SUPERLOGICA', 'WEBWARE', 'SENDMAIL', 'NOTIFICA', 'MENSAGEM', ''] or len(administradora) <= 2: 
                administradora = "OUTRAS"
                
        marcador_destino = f"CONDOMINIOS - COTA/{administradora}"
        
    elif "ata " in assunto_limpo or "convocacao" in assunto_limpo or "assembleia" in assunto_limpo or "reuniao" in assunto_limpo:
        marcador_destino = "ATA - REUNIAO"
    elif "sorteio" in assunto_limpo: 
        marcador_destino = "SORTEIO"
    elif "multa" in assunto_limpo or "infracao" in assunto_limpo or "advertencia" in assunto_limpo: 
        marcador_destino = "MULTA - AVISO"
    elif "comunicado" in assunto_limpo or "aviso" in assunto_limpo or "enquete" in assunto_limpo or "informativo" in assunto_limpo: 
        marcador_destino = "COMUNICADOS - AVISOS"

    try:
        if "/" in marcador_destino:
            try: mail.create(f'"{marcador_destino.split("/")[0]}"')
            except: pass
        try: mail.create(f'"{marcador_destino}"')
        except: pass 
        mail.uid('STORE', uid_email, '+X-GM-LABELS', f'"{marcador_destino}"')
        print(f"⚙️ Organizado no Gmail sob: {marcador_destino}", flush=True)
    except: pass

def alternar_chave_api():
    global INDICE_CHAVE_ATUAL, client
    INDICE_CHAVE_ATUAL += 1
    if INDICE_CHAVE_ATUAL >= len(CHAVES_API_GEMINI):
        print("🚨 [CRÍTICO] TODAS as chaves de API foram esgotadas!", flush=True)
        return False
    client = genai.Client(api_key=CHAVES_API_GEMINI[INDICE_CHAVE_ATUAL])
    return True

def registrar_historico_tempo_real(dados_evento):
    caminho_arquivo = "historicos/historico_ROBO_GERAL_EMAIL.json"
    os.makedirs(os.path.dirname(caminho_arquivo), exist_ok=True)
    historico = []
    if os.path.exists(caminho_arquivo):
        try:
            with open(caminho_arquivo, "r", encoding="utf-8") as f: historico = json.load(f)
        except: pass
    dados_evento["timestamp"] = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    historico.append(dados_evento)
    with open(caminho_arquivo, "w", encoding="utf-8") as f:
        json.dump(historico, f, ensure_ascii=False, indent=4)

def deve_ignorar_email(assunto, corpo):
    texto_total = (assunto + " " + corpo).lower()
    palavras_positivas = ["boleto", "fatura", "cota", "vencimento", "pagamento", "recibo", "ticket", "mensalidade", "link", "anexo"]
    
    for pos in palavras_positivas:
        if pos in texto_total:
            return False 

    palavras_negativas = ["sorteio", "assembleia", "convocação", "prestação", "aviso importante", "aviso ", "pastas", "manutenção", "ata ", "feliz aniversário", "multa", "infração"]
    for neg in palavras_negativas:
        if neg in texto_total:
            return True 
            
    return False

def limpar_texto(texto):
    if pd.isna(texto): return ""
    return ''.join(c for c in unicodedata.normalize('NFD', str(texto)) if unicodedata.category(c) != 'Mn').upper().strip()


def extrair_unidade(texto):
    if not texto: return ""
    texto_limpo = limpar_texto(texto)
    regex = re.compile(r'\b(?:UNIDADE|UNID|APTO|APT|AP|CASA|SALA|CJ|LOTE)\.?\s*[:\-]?\s*0*([0-9]{1,4}[A-Z]?)\b')
    match = regex.search(texto_limpo)
    if match:
        return match.group(1)
    return ""


def descobrir_regra_senha(corpo_email, tentativas=0):
    if not corpo_email or len(corpo_email.strip()) < 10: 
        return None
    
    # Limpa o texto para análise
    texto = corpo_email.upper()
    
    # Padrões regex para encontrar senhas no corpo do e-mail
    padroes = [
        # Senha explícita
        r'SENHA[:\s]+([0-9]{3,14})',
        r'PASSWORD[:\s]+([0-9]{3,14})',
        r'CÓDIGO[:\s]+([0-9]{3,14})',
        r'CODIGO[:\s]+([0-9]{3,14})',
        r'PIN[:\s]+([0-9]{3,8})',
        r'CHAVE[:\s]+([0-9]{3,14})',
        
        # CPF mencionado como senha
        r'SENHA[:\s]+(?:SER[AÁ]?[:\s]+)?(?:SEU )?CPF[:\s]+([0-9]{3}\.?[0-9]{3}\.?[0-9]{3}\-?[0-9]{2})',
        r'SENHA[:\s]+(?:É )?[O ]?CPF',
        r'UTILIZE[:\s]+SEU CPF',
        
        # Últimos dígitos
        r'[ÚU]LTIMOS?\s+(\d+)\s+D[ÍI]GITOS?\s+(?:DO )?CPF',
        r'[ÚU]LTIMOS?\s+(\d+)\s+N[ÚU]MEROS?',
        
        # Primeiros dígitos
        r'PRIMEIROS?\s+(\d+)\s+D[ÍI]GITOS?',
        
        # Senha fixa mencionada
        r'SENHA[:\s]+FIXA[:\s]+([0-9]+)',
        r'SENHA[:\s]+PADR[ÃA]O[:\s]+([0-9]+)',
    ]
    
    for padrao in padroes:
        match = re.search(padrao, texto)
        if match:
            senha_encontrada = match.group(1).strip() if match.groups() else match.group(0)
            senha_limpa = re.sub(r'[^0-9]', '', senha_encontrada)
            
            if len(senha_limpa) >= 3:
                print(f"🔑 Regra de senha detectada: {senha_limpa[:4]}{'*' * max(0, len(senha_limpa)-4)}", flush=True)
                return f"FIXA:{senha_limpa}"
    
    # Se não encontrou padrão específico, mas menciona CPF
    if re.search(r'\b(CPF|CPF/CNPJ)\b', texto) and re.search(r'\bSENHA\b', texto):
        return "PADRAO:CPF"
    
    # Se menciona "últimos 4 dígitos"
    if re.search(r'[ÚU]LTIMOS?\s+4', texto):
        return "PADRAO:ULTIMOS4"
    
    # Se menciona "primeiros 6 dígitos"
    if re.search(r'PRIMEIROS?\s+6', texto):
        return "PADRAO:PRIMEIROS6"
    
    # Fallback: tenta com IA se disponível
    prompt = f"Leia o e-mail e encontre a senha do PDF. Ex: 4_DIGITOS_CPF, CPF_COMPLETO, FIXA:1234. Se não houver, retorne NENHUMA.\nE-MAIL:\n{corpo_email[:1500]}"
    try:
        resposta = client.models.generate_content(model='gemini-3.1-flash-lite', contents=prompt)
        res = resposta.text.strip().upper()
        return res if "NENHUMA" not in res else None
    except Exception as e:
        if "429" in str(e) and alternar_chave_api() and tentativas < len(CHAVES_API_GEMINI):
            return descobrir_regra_senha(corpo_email, tentativas + 1)
        return None


def pdf_tem_senha(pdf_bytes):
    """True quando o PDF só abre com senha."""
    if not pdf_bytes or not pdf_bytes.startswith(b'%PDF'):
        return False
    try:
        with pikepdf.open(BytesIO(pdf_bytes)):
            return False
    except pikepdf.PasswordError:
        return True
    except Exception:
        return False


def senhas_do_cpf(cpf, regra=""):
    """Senhas que um CPF/CNPJ pode virar. A regra informada pelo colaborador no Raio-X vem primeiro."""
    d = re.sub(r'[^0-9]', '', str(cpf or ''))
    if len(d) < 4:
        return []
    pela_regra = {
        "CPF_COMPLETO": [d], "PRIMEIROS_3": [d[:3]], "PRIMEIROS_4": [d[:4]], "PRIMEIROS_5": [d[:5]],
        "PRIMEIROS_6": [d[:6]], "ULTIMOS_4": [d[-4:]], "ULTIMOS_6": [d[-6:]], "CNPJ_RAIZ": [d[:8]],
    }
    lista = list(pela_regra.get(str(regra or "").upper(), []))
    lista += [d, d[:3], d[:4], d[:5], d[:6], d[:7], d[:8], d[:9], d[-4:], d[-6:], d.lstrip('0'), d[3:6]]
    if len(d) == 11:
        lista += [f"{d[:3]}.{d[3:6]}.{d[6:9]}-{d[9:]}", f"{d[:3]}.{d[3:6]}.{d[6:9]}"]
    if len(d) == 14:
        lista += [d[:8] + "0001", d[:8] + "0001" + d[12:]]
    return lista


def descrever_regra_senha(regra):
    """Texto curto para a equipe ver no Raio-X (sem expor a senha fixa)."""
    if not regra:
        return ""
    r = str(regra).upper()
    if r.startswith("FIXA:"):
        return "senha fixa informada no e-mail"
    return {"PADRAO:CPF": "CPF completo", "PADRAO:ULTIMOS4": "4 últimos dígitos do CPF",
            "PADRAO:PRIMEIROS6": "6 primeiros dígitos do CPF"}.get(r, r[:80])


def avisar_pdf_protegido_api(imovel, assunto, remetente, link_gmail, regra, message_id):
    """Joga o PDF que não abriu para o Raio-X: com imóvel vira caso dele; sem imóvel, entra na lista de e-mails."""
    try:
        requests.post(URL_API_PDF_PROTEGIDO, json={
            "token": TOKEN_SEGURANCA, "imovel": str(imovel or ""), "assunto": assunto, "remetente": remetente,
            "link": link_gmail, "regra": descrever_regra_senha(regra), "message_id": message_id,
        }, timeout=15)
        print(f"🔐 PDF protegido enviado ao Raio-X ({'imóvel ' + str(imovel) if imovel else 'sem imóvel identificado'}).", flush=True)
    except Exception as e:
        print(f"⚠️ Não consegui avisar o Raio-X sobre o PDF protegido: {e}", flush=True)


def tentar_desbloquear_pdf_e_ler(pdf_bytes, cpfs_api, regra_senha=None, mes_atual=None, ano_atual=None, cpfs_prioritarios=None):
    if not pdf_bytes or not pdf_bytes.startswith(b'%PDF'):
        return None, None

    try:
        # Tenta abrir sem senha primeiro
        try:
            with pikepdf.open(BytesIO(pdf_bytes)) as pdf:
                return "".join([p.extract_text() for p in PyPDF2.PdfReader(BytesIO(pdf_bytes)).pages]), pdf_bytes
        except pikepdf.PasswordError:
            pass

        senhas = []

        # 1. Regra fixa do e-mail (prioridade máxima)
        if regra_senha:
            if regra_senha.startswith("FIXA:"):
                senhas.append(regra_senha.split(":")[1].strip())
            elif regra_senha.startswith("PADRAO:"):
                padrao = regra_senha.split(":")[1].strip()
                if padrao == "CPF":
                    # Adiciona todos os CPFs completos primeiro
                    for cpf in cpfs_api:
                        cpf_limpo = re.sub(r'[^0-9]', '', str(cpf))
                        if len(cpf_limpo) == 11:
                            senhas.append(cpf_limpo)
                elif padrao == "ULTIMOS4":
                    for cpf in cpfs_api:
                        cpf_limpo = re.sub(r'[^0-9]', '', str(cpf))
                        if len(cpf_limpo) >= 4:
                            senhas.append(cpf_limpo[-4:])
                elif padrao == "PRIMEIROS6":
                    for cpf in cpfs_api:
                        cpf_limpo = re.sub(r'[^0-9]', '', str(cpf))
                        if len(cpf_limpo) >= 6:
                            senhas.append(cpf_limpo[:6])
            else:
                senhas.append(regra_senha.strip())

        # 1b. CPFs que o colaborador informou no Raio-X (com a regra dele) — antes de qualquer chute
        for cpf_colab, regra_colab in (cpfs_prioritarios or []):
            senhas.extend(senhas_do_cpf(cpf_colab, regra_colab))

        # 2. Variações de CPF (expandido)
        for cpf in cpfs_api:
            cpf_limpo = re.sub(r'[^0-9]', '', str(cpf))
            if len(cpf_limpo) >= 11:
                # CPF completo e variações
                senhas.extend([
                    cpf_limpo,                          # 12345678901
                    cpf_limpo[:3],                      # 123
                    cpf_limpo[:4],                      # 1234
                    cpf_limpo[:5],                      # 12345
                    cpf_limpo[:6],                      # 123456
                    cpf_limpo[:7],                      # 1234567
                    cpf_limpo[:8],                      # 12345678
                    cpf_limpo[:9],                      # 123456789
                    cpf_limpo[-4:],                     # 8901 (últimos 4)
                    cpf_limpo[-6:],                     # 678901 (últimos 6)
                    cpf_limpo.lstrip('0'),              # sem zeros à esquerda
                    cpf_limpo[3:6],                     # 456 (do meio)
                ])
                
                # CPF formatado
                senhas.append(f"{cpf_limpo[:3]}.{cpf_limpo[3:6]}.{cpf_limpo[6:9]}-{cpf_limpo[9:]}")
                senhas.append(f"{cpf_limpo[:3]}.{cpf_limpo[3:6]}.{cpf_limpo[6:9]}")

        # 3. CNPJ (se houver na base - primeiros 8 dígitos + 0001)
        for cpf in cpfs_api:
            doc = re.sub(r'[^0-9]', '', str(cpf))
            if len(doc) == 14:  # CNPJ
                senhas.extend([
                    doc,                                # CNPJ completo
                    doc[:8],                            # Raiz
                    doc[:8] + "0001",                   # Padrão filial
                    doc[:8] + "0001" + doc[12:],        # CNPJ calc
                ])

        # 4. Senhas comuns de condomínios/administradoras
        senhas_comuns = [
            "1234", "123456", "0000", "000000", "1111", "111111",
            "12345678", "password", "senha", "boleto", "cota", 
            "2024", "2025", "2026", str(ano_atual) if ano_atual else "2026",
            "condominio", "admin", "user", "pdf", "doc",
            "1", "12", "123", "1234", "12345", "123456", "1234567", "12345678",
            "0001", "001", "01", "1",
            "9999", "999999", "8888", "7777", "6666", "5555", "4444", "3333", "2222",
            "2026", "26", "2025", "25", "2024", "24",
        ]
        senhas.extend(senhas_comuns)

        # 5. Senhas baseadas em data/mês
        ano_ref = str(ano_atual) if ano_atual else str(datetime.now().year)
        if mes_atual:
            mes_str = str(mes_atual).zfill(2)
            senhas.extend([
                mes_str,                            # 07
                mes_str + ano_ref,                  # 072026
                mes_str + ano_ref[-2:],             # 0726
                "20" + mes_str,                     # 2007
            ])
        
        # 6. Combinações com ano
        for ano in [ano_ref, ano_ref[-2:], "2026", "2025", "2024", "2023", "26", "25", "24", "23"]:
            senhas.extend([
                ano,
                "01" + ano, "02" + ano, "03" + ano,  # meses + ano
                "04" + ano, "05" + ano, "06" + ano,
                "07" + ano, "08" + ano, "09" + ano,
                "10" + ano, "11" + ano, "12" + ano,
            ])

        # 7. Remove duplicatas mantendo ordem (preserva prioridade)
        senhas_unicas = []
        vistas = set()
        for s in senhas:
            s_limpo = str(s).strip()
            if s_limpo and s_limpo not in vistas and len(s_limpo) > 0:
                senhas_unicas.append(s_limpo)
                vistas.add(s_limpo)

        print(f"🔐 Tentando {len(senhas_unicas)} combinações de senha...", flush=True)

        # Tenta cada senha
        for senha in senhas_unicas:
            try:
                with pikepdf.open(BytesIO(pdf_bytes), password=str(senha)) as pdf:
                    pdf_limpo = BytesIO()
                    pdf.save(pdf_limpo)
                    print(f"✅ PDF desbloqueado com senha: {senha[:4]}{'*' * (len(str(senha))-4)}", flush=True)
                    return "".join([p.extract_text() for p in PyPDF2.PdfReader(BytesIO(pdf_limpo.getvalue())).pages]), pdf_limpo.getvalue()
            except pikepdf.PasswordError:
                continue
            except Exception:
                continue
                
        return None, None 
    except Exception as e:
        print(f"❌ Erro no desbloqueio: {e}", flush=True)
        return None, None


def analisar_com_ia_visao(pdf_bytes, assunto, corpo_email, tentativas=0):
    prompt = f"""
    Você é um auditor financeiro rigoroso especializado em condomínios.
    Sua missão é olhar A IMAGEM DO PDF e identificar os dados.
    PODE COLETAR QUALQUER BOLETO (Cota mensal, Fundo de Reserva, Cota Extra, Acordo, etc).
    
    Retorne EXATAMENTE neste formato:
    CONDOMINIO: SIM
    CODIGO_IMOVEL: [Apenas números do ID do imóvel]
    CPF_CNPJ: [Apenas números]
    SACADO: [Nome]
    UNIDADE: [Apenas o número do apto/casa/lote]
    MES_VENCIMENTO: [Mês do vencimento. Ex: {MES_ALVO}]
    
    [DADOS DO E-MAIL]
    ASSUNTO: {assunto}
    CORPO DO EMAIL: {corpo_email[:1500]}
    """
    caminho_temp = "temp_visao.pdf"
    arq_ia = None
    conteudos = [prompt]
    
    if pdf_bytes:
        try:
            with open(caminho_temp, "wb") as f: 
                f.write(pdf_bytes)
            arq_ia = client.files.upload(file=caminho_temp)
            conteudos.insert(0, arq_ia)
        except Exception as e:
            print(f"⚠️ Erro ao preparar PDF para IA: {e}")

    try:
        resposta = client.models.generate_content(model='gemini-3.1-flash-lite', contents=conteudos)
        return resposta.text.strip()
    except Exception as e:
        if "429" in str(e) and alternar_chave_api() and tentativas < len(CHAVES_API_GEMINI):
            return analisar_com_ia_visao(pdf_bytes, assunto, corpo_email, tentativas + 1)
        return ""
    finally:
        if arq_ia:
            try: client.files.delete(name=arq_ia.name)
            except: pass
        if os.path.exists(caminho_temp): 
            try: os.remove(caminho_temp)
            except: pass

def extrair_assunto_e_corpo(msg):
    assunto_bruto = msg.get("Subject", "")
    if not assunto_bruto:
        assunto = ""
    else:
        assunto, encoding = decode_header(assunto_bruto)[0]
        if encoding and "unknown-8bit" in encoding.lower(): 
            encoding = "utf-8"
        if isinstance(assunto, bytes): 
            assunto = assunto.decode(encoding if encoding else "utf-8", errors='ignore')
    
    corpo = ""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain" and "attachment" not in str(part.get("Content-Disposition")):
                charset = part.get_content_charset()
                if not charset or "unknown-8bit" in charset.lower(): 
                    charset = "utf-8"
                try: 
                    corpo = part.get_payload(decode=True).decode(charset, errors='ignore')
                except: pass
    else:
        charset = msg.get_content_charset()
        if not charset or "unknown-8bit" in charset.lower(): 
            charset = "utf-8"
        try: 
            corpo = msg.get_payload(decode=True).decode(charset, errors='ignore')
        except: pass
        
    return str(assunto), str(corpo)

def baixar_pdf_de_link(msg):
    html_body = ""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                charset = part.get_content_charset()
                if not charset or "unknown-8bit" in charset.lower(): 
                    charset = "utf-8"
                try: html_body = part.get_payload(decode=True).decode(charset, errors='ignore')
                except: pass
    else:
        if msg.get_content_type() == "text/html":
            charset = msg.get_content_charset()
            if not charset or "unknown-8bit" in charset.lower(): 
                charset = "utf-8"
            try: html_body = msg.get_payload(decode=True).decode(charset, errors='ignore')
            except: pass
            
    if not html_body: return None, None
    soup = BeautifulSoup(html_body, 'html.parser')
    for link in soup.find_all('a', href=True):
        txt, url = link.text.lower().strip(), link['href']
        if "google.com/url?q=" in url: url = urllib.parse.parse_qs(urllib.parse.urlparse(url).query).get('q', [url])[0]
        if "acessar boleto" in txt or "baixar" in txt or "/emailaccess/link/" in url.lower():
            try:
                res = requests.Session().get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=20, allow_redirects=True)
                if res.status_code == 200 and b'%PDF' in res.content[:10]: return "Boleto_Link.pdf", res.content
            except: pass
            break 
    return None, None

def enviar_notificacao_chat(nome_arquivo, colaborador):
    try: requests.post(WEBHOOK_URL, json={"text": f" *Novo Boleto {MES_ALVO}/{str(ANO_ALVO)[-2:]} Coletado!*\nArquivo: `{nome_arquivo}`\nResponsável: *{colaborador}*"})
    except: pass

# =======================================================================
# 🚀 NOVA LÓGICA DE MATCH EXATO BLINDADA (SCORING)
# =======================================================================
def encontrar_imovel_exato(cpf_ia, unidade_ia, sacado_ia, texto_pdf_limpo, pendencias):
    cpf_limpo = re.sub(r'[^0-9]', '', str(cpf_ia)) if cpf_ia and cpf_ia != "N" else ""
    unidade_num_ia = re.sub(r'[^0-9]', '', str(unidade_ia)) if unidade_ia and unidade_ia != "N" else ""
    sacado_ia_limpo = str(sacado_ia).upper().strip() if sacado_ia and sacado_ia != "N" else ""
    texto_limpo = limpar_texto(texto_pdf_limpo)

    melhor_match = None
    maior_pontuacao = 0

    for p in pendencias:
        pontuacao = 0
        bateu_unidade = False

        # 1. VALIDAÇÃO DE UNIDADE / NÚMERO DO IMÓVEL (Peso 30)
        d_lote = limpar_texto(p.get('d_lote', ''))
        endereco = limpar_texto(p.get('endereco', ''))
        unidade_banco = re.sub(r'[^0-9]', '', d_lote)
        match_endereco = re.search(r'\b(?:AP|APTO|APARTAMENTO|UNIDADE|CASA|SALA|CJ|LOTE)[\s\.\-\:]*(\d+[A-Z]?)\b', endereco)
        unidade_end = re.sub(r'[^0-9]', '', match_endereco.group(1)) if match_endereco else ""

        uni_ia_int = unidade_num_ia.lstrip('0')
        uni_banco_int = unidade_banco.lstrip('0')
        uni_end_int = unidade_end.lstrip('0')

        if uni_ia_int:
            if (uni_ia_int == uni_banco_int and uni_banco_int != "") or (uni_ia_int == uni_end_int and uni_end_int != ""):
                bateu_unidade = True
                pontuacao += 30
            elif uni_ia_int in d_lote or uni_ia_int in endereco or f"UNIDADE {uni_ia_int}" in texto_limpo or f"APTO {uni_ia_int}" in texto_limpo:
                bateu_unidade = True
                pontuacao += 18

        # 2. VALIDAÇÃO DO NOME DO SACADO (Peso 20)
        nome_banco = limpar_texto(p.get('sacado', ''))
        if sacado_ia_limpo and nome_banco:
            score_nome = fuzz.token_set_ratio(sacado_ia_limpo, nome_banco)
            if score_nome >= 90:
                pontuacao += 20
            elif score_nome >= 70:
                pontuacao += 12
            elif score_nome >= 55:
                pontuacao += 6

        # 3. VALIDAÇÃO DO EMPREENDIMENTO (Peso 20)
        empreendimento = limpar_texto(p.get('empreendimento', ''))
        if empreendimento:
            score_empreendimento = fuzz.partial_ratio(empreendimento, texto_limpo)
            if score_empreendimento >= 90:
                pontuacao += 20
            elif score_empreendimento >= 75:
                pontuacao += 12
            elif score_empreendimento >= 60:
                pontuacao += 6

        # 4. VALIDAÇÃO DA ADMINISTRADORA (Peso 20)
        administradora = limpar_texto(p.get('administradora', ''))
        if administradora:
            score_adm = fuzz.partial_ratio(administradora, texto_limpo)
            if score_adm >= 90:
                pontuacao += 20
            elif score_adm >= 75:
                pontuacao += 12
            elif score_adm >= 60:
                pontuacao += 6

        # 5. VALIDAÇÃO DE CPF/CNPJ (Peso 10)
        doc_banco = re.sub(r'[^0-9]', '', str(p.get('cpf_cnpj', '')))
        if cpf_limpo and doc_banco:
            if doc_banco == cpf_limpo or doc_banco in cpf_limpo or cpf_limpo in doc_banco:
                pontuacao += 10

        # 🚨 ESCUDO ANTI-CLONE FATAL 🚨
        if uni_ia_int and not bateu_unidade:
            pontuacao -= 100

        if pontuacao > maior_pontuacao and pontuacao >= 85:
            maior_pontuacao = pontuacao
            melhor_match = p

    return melhor_match, maior_pontuacao

# =======================================================================
# EXECUÇÃO DA VARREDURA (PROCESSO PRINCIPAL)
# =======================================================================
def processar_email():
    print("🔍 Conectando à API interna para estruturar banco de dados local...", flush=True)
    pendencias_da_rodada = buscar_pendencias_api()
    
    cpfs_ativos = set()
    cpfs_prioritarios = []        # (cpf, regra) informados pelo colaborador no Raio-X
    vistos_prioritarios = set()
    for caso in pendencias_da_rodada:
        cpf = str(caso.get('cpf_cnpj', '')).strip()
        if len(cpf) > 3:
            cpfs_ativos.add(cpf)
        cpf_colab = re.sub(r'[^0-9]', '', str(caso.get('cpf_senha_pdf') or ''))
        if len(cpf_colab) >= 11 and (cpf_colab, caso.get('regra_senha_pdf') or '') not in vistos_prioritarios:
            vistos_prioritarios.add((cpf_colab, caso.get('regra_senha_pdf') or ''))
            cpfs_prioritarios.append((cpf_colab, caso.get('regra_senha_pdf') or ''))
    # os CPFs informados no Raio-X também entram nas regras do e-mail ("seu CPF", "4 últimos"...), na frente
    cpfs_para_senha = [c for c, _r in cpfs_prioritarios] + sorted(cpfs_ativos)
    print(f" {len(cpfs_ativos)} senhas de segurança (CPFs) carregadas na memória! "
          f"({len(cpfs_prioritarios)} informadas pela equipe no Raio-X)", flush=True)

    print("⚙️ Conectando com segurança ao Gmail...", flush=True)
    mail = imaplib.IMAP4_SSL("imap.gmail.com")
    mail.login(EMAIL_CONTA, SENHA_APP)
    mail.select("inbox")

    status, mensagens = mail.search(None, '(UNSEEN SENTSINCE "15-Jul-2026")')
    ids_emails = mensagens[0].split()
    print(f"📊 Fila de Triagem: {len(ids_emails)} e-mails não lidos localizados.", flush=True)

    # Pega ano atual para senhas baseadas em data
    ano_atual = ANO_ALVO

    for email_id in ids_emails:
        res_fetch = mail.fetch(email_id, '(RFC822)')
        if not isinstance(res_fetch, tuple) or res_fetch[0] != 'OK':
            continue
        dados_msg = res_fetch[1]
        
        res_uid = mail.fetch(email_id, '(UID)')
        if not isinstance(res_uid, tuple) or res_uid[0] != 'OK':
            continue
        dados_uid = res_uid[1]
        
        msg = email.message_from_bytes(dados_msg[0][1])
        
        uid_match = re.search(r'UID\s+(\d+)', dados_uid[0].decode('utf-8', errors='ignore'))
        uid_email = uid_match.group(1) if uid_match else "0"

        assunto, corpo = extrair_assunto_e_corpo(msg)
        print(f"\n📧 Analisando e-mail: '{assunto}'", flush=True)
        organizar_gmail_marcadores(mail, uid_email, assunto, msg.get("From", ""))
        
        if deve_ignorar_email(assunto, corpo):
            print(f"⚠️ Identificado como Comunicado/Relacionamento. Classificando tipo...", flush=True)
            
            assunto_low = assunto.lower()
            tipo_doc = "COMUNICADO"
            if "ata " in assunto_low or "assembleia" in assunto_low: tipo_doc = "ATA/ASSEMBLEIA"
            elif "sorteio" in assunto_low or "vaga" in assunto_low: tipo_doc = "SORTEIO/VAGAS"
            elif "multa" in assunto_low or "infração" in assunto_low: tipo_doc = "MULTA/AVISO"
            
            if tipo_doc == "COMUNICADO":
                print(f" Pulando: '{assunto}' classificado como Comunicado Geral (Não enviado para o CRM).", flush=True)
                registrar_historico_tempo_real({"status": "IGNORADO COMUNICADO", "assunto_email": assunto})
                continue 

            msg_id_bruto = msg.get('Message-ID', '')
            link_gmail = ""
            if msg_id_bruto:
                codigo_secreto = urllib.parse.quote(msg_id_bruto.strip('<>'))
                link_gmail = f"https://mail.google.com/mail/u/0/#search/rfc822msgid%3A{codigo_secreto}"
            
            imoveis_afetados = mapear_imoveis_por_condominio(assunto, corpo, pendencias_da_rodada)
            
            if imoveis_afetados:
                registrar_relacionamento_api(imoveis_afetados, tipo_doc, assunto, link_gmail)
                print(f" [CRM] Arquivado na aba Relacionamento para {len(imoveis_afetados)} imóveis do prédio!", flush=True)
            else:
                print(f"⚠️ Documento do tipo {tipo_doc} detectado, mas Condomínio não identificado na base.", flush=True)
                
            registrar_historico_tempo_real({"status": f"SALVO COMO {tipo_doc}", "assunto_email": assunto})
            continue
        
        teve_pdf, pdf_bytes, nome_arquivo = False, None, ""
        for part in msg.walk():
            if part.get_content_maintype() == 'multipart' or part.get('Content-Disposition') is None: continue
            if (part.get_filename() or "").lower().endswith('.pdf'):
                teve_pdf, nome_arquivo, pdf_bytes = True, part.get_filename(), part.get_payload(decode=True)
                break 

        if not teve_pdf:
            nome_link, bytes_link = baixar_pdf_de_link(msg)
            if bytes_link: teve_pdf, nome_arquivo, pdf_bytes = True, nome_link, bytes_link

        pdf_final_para_salvar = None
        texto_pdf_bruto = ""
        pdf_protegido_sem_senha = False      # tinha PDF, com senha, e nenhum CPF abriu
        regra_do_email = None
        if teve_pdf and pdf_bytes:
            protegido = pdf_tem_senha(pdf_bytes)
            # a regra do e-mail (às vezes pergunta à IA) só importa quando o PDF tem senha
            regra_do_email = descobrir_regra_senha(corpo) if protegido else None
            texto_pdf, pdf_final = tentar_desbloquear_pdf_e_ler(
                pdf_bytes,
                cpfs_para_senha,
                regra_do_email,
                mes_atual=MES_ALVO,
                ano_atual=ano_atual,
                cpfs_prioritarios=cpfs_prioritarios
            )
            if pdf_final:
                pdf_final_para_salvar = pdf_final
                texto_pdf_bruto = texto_pdf if texto_pdf else ""
            elif protegido:
                pdf_protegido_sem_senha = True
                print("🔐 PDF com senha: nenhum CPF conhecido abriu. Vai para o Raio-X para a equipe informar o CPF.", flush=True)

        msg_id_bruto = msg.get('Message-ID', '')
        link_gmail = ""
        if msg_id_bruto:
            codigo_secreto = urllib.parse.quote(msg_id_bruto.strip('<>'))
            link_gmail = f"https://mail.google.com/mail/u/0/#search/rfc822msgid%3A{codigo_secreto}"

        print("🧠 Decodificando documento com IA de Visão...", flush=True)
        analise_texto = analisar_com_ia_visao(pdf_final_para_salvar, assunto, corpo)
            
        match_imovel = re.search(r'CODIGO_IMOVEL:\s*(\d+)', analise_texto)
        id_imovel = match_imovel.group(1).strip() if match_imovel else ""
        
        match_mes = re.search(r'MES_VENCIMENTO:\s*0*(\d+)', analise_texto)
        mes_venc = match_mes.group(1).strip() if match_mes else ""

        match_cpf = re.search(r'CPF_CNPJ:\s*([^\n]+)', analise_texto)
        cpf_ia = match_cpf.group(1).strip() if match_cpf else ""

        unidade_ia = extrair_unidade(analise_texto)
        if not unidade_ia:
            unidade_ia = extrair_unidade(texto_pdf_bruto)
        if not unidade_ia:
            unidade_ia = extrair_unidade(assunto + " " + corpo)

        match_sacado = re.search(r'SACADO:\s*([^\n]+)', analise_texto)
        sacado_ia = match_sacado.group(1).strip() if match_sacado else ""

        texto_total_pdf = limpar_texto(texto_pdf_bruto + " " + assunto + " " + corpo)

        mes_alvo_limpo = str(int(MES_ALVO))
        if mes_venc and mes_venc != mes_alvo_limpo and mes_venc != MES_ALVO:
            print(f" Rejeitado: O vencimento é mês {mes_venc}. O alvo configurado no painel é {MES_ALVO}.", flush=True)
            continue

        dados_api = None
        if id_imovel:
            dados_api = validar_imovel_api(id_imovel)
            if dados_api and dados_api.get('valido'):
                match_ia, conf_ia = validar_match_com_ia(id_imovel, unidade_ia, sacado_ia, texto_total_pdf)
                if not match_ia or conf_ia < 85:
                    print(f"⚠️ Validação Gemini rejeitou o imóvel {id_imovel} com confiança {conf_ia}%. Tentando fallback...", flush=True)
                    id_imovel = ""
                    dados_api = None
                else:
                    print(f" Confirmação Gemini: imóvel {id_imovel} validado com {conf_ia}% de confiança.", flush=True)

        match_note = ""
        # 🚀 NOVO BLOCO: Match exato com sistema de Scoring Anti-Clone
        if not id_imovel or not dados_api or not dados_api.get('valido'):
            match_cpf = re.search(r'CPF_CNPJ:\s*([^\n]+)', analise_texto)
            cpf_ia = match_cpf.group(1).strip() if match_cpf else ""

            unidade_ia = extrair_unidade(analise_texto)
            if not unidade_ia:
                unidade_ia = extrair_unidade(texto_pdf_bruto)
            if not unidade_ia:
                unidade_ia = extrair_unidade(assunto + " " + corpo)

            match_sacado = re.search(r'SACADO:\s*([^\n]+)', analise_texto)
            sacado_ia = match_sacado.group(1).strip() if match_sacado else ""

            texto_total_pdf = limpar_texto(texto_pdf_bruto + " " + assunto + " " + corpo)
            
            imovel_match, score_match = encontrar_imovel_exato(cpf_ia, unidade_ia, sacado_ia, texto_total_pdf, pendencias_da_rodada)
            
            if imovel_match:
                id_imovel = str(imovel_match.get('imovel', ''))
                dados_api = validar_imovel_api(id_imovel)
                match_note = (
                    f"Match automático 80%: score {score_match}. "
                    f"Unidade: '{unidade_ia}'. "
                    f"Sacado: '{sacado_ia}'. "
                    f"Empreendimento: '{imovel_match.get('empreendimento','')}'. "
                    f"Administradora: '{imovel_match.get('administradora','')}'."
                )
                print(f"🎯 Match inteligente aprovado! Precisão: {score_match}%. Unidade {unidade_ia} identificada com segurança.", flush=True)
            else:
                if cpf_ia:
                    print(f"⚠️ Alerta Anti-Clone: Informações bloqueadas pelo Scoring. Baixa precisão detectada.", flush=True)

        if not id_imovel or not dados_api or not dados_api.get('valido'):
            if id_imovel:
                print(f"⚠️ O código {id_imovel} não é válido ou pertence a outra ADM. Chamando DEPARA...", flush=True)
            else:
                print("⚠️ ID não explícito. Iniciando DEPARA inteligente na base de dados Lello...", flush=True)
            
            texto_total_depara = f"{assunto} {corpo} {texto_pdf_bruto} {analise_texto}"
            id_imovel = descobrir_id_por_similaridade(texto_total_depara, pendencias_da_rodada)

            if id_imovel:
                dados_api = validar_imovel_api(id_imovel)

        if not id_imovel or not dados_api or not dados_api.get('valido'):
            print(" Rejeitado: Não foi possível rastrear a identidade deste imóvel na base.", flush=True)
            if pdf_protegido_sem_senha:
                # sem imóvel: entra na lista de e-mails do Raio-X; a equipe diz de quem é e qual CPF abre
                avisar_pdf_protegido_api(None, assunto, msg.get("From", ""), link_gmail, regra_do_email, msg_id_bruto)
                registrar_historico_tempo_real({"status": "PDF PROTEGIDO SEM IMÓVEL", "assunto_email": assunto})
            continue

        print(f" Transação Aprovada! Morador: {dados_api.get('responsavel', 'N/A')} | Prédio: {dados_api.get('administradora', '')}", flush=True)

        msg_id_bruto = msg.get('Message-ID', '')
        link_gmail = ""
        if msg_id_bruto:
            codigo_secreto = urllib.parse.quote(msg_id_bruto.strip('<>'))
            link_gmail = f"https://mail.google.com/mail/u/0/#search/rfc822msgid%3A{codigo_secreto}"

        if pdf_final_para_salvar:
            try:
                os.makedirs(PASTA_DESTINO, exist_ok=True)
                os.makedirs(PASTA_ARQUIVO_FINAL, exist_ok=True)
                caminho_final = os.path.join(PASTA_DESTINO, f"{id_imovel}.pdf")
                with open(caminho_final, "wb") as f: f.write(pdf_final_para_salvar)
                
                if os.path.getsize(caminho_final) > 0:
                    print(f" Boleto injetado com sucesso no servidor: {caminho_final}", flush=True)
                    observacao_final = match_note
                    if link_gmail:
                        observacao_final = (match_note + ' | ' if match_note else '') + link_gmail
                    if dar_baixa_api(id_imovel, status_novo="COLETADO", observacao=observacao_final): 
                        print(f" Fila Atualizada! Imóvel {id_imovel} alterado para COLETADO no sistema!", flush=True)
                    enviar_notificacao_chat(f"{id_imovel} ({dados_api.get('administradora', '')})", dados_api.get('responsavel', 'N/A'))
                    registrar_historico_tempo_real({"status": "SUCESSO", "imovel_id": id_imovel, "nota": observacao_final})
                else: os.remove(caminho_final)
            except Exception as e: 
                print(f" Erro físico de gravação na rede: {e}", flush=True)
                observacao_final = match_note
                if link_gmail:
                    observacao_final = (match_note + ' | ' if match_note else '') + link_gmail
                dar_baixa_api(id_imovel, status_novo="ERRO DE REDE", observacao=observacao_final)
        else:
            print(f"⚠️ Imóvel validado, mas sem PDF. Atualizando painel para ALERTA!", flush=True)
            observacao_final = match_note
            if pdf_protegido_sem_senha:
                # o boleto veio, mas com senha que nenhum CPF abriu: caso do Raio-X (Senha do PDF)
                avisar_pdf_protegido_api(id_imovel, assunto, msg.get("From", ""), link_gmail, regra_do_email, msg_id_bruto)
                observacao_final = ('PDF com senha: nenhum CPF cadastrado abriu (informe o CPF no Raio-X)'
                                    + (' | ' + match_note if match_note else ''))
            if link_gmail:
                observacao_final = (observacao_final + ' | ' if observacao_final else '') + link_gmail
            dar_baixa_api(id_imovel, status_novo="EMAIL RECEBIDO", observacao=observacao_final)

    mail.logout()
    print("🏁 Varredura da caixa de entrada concluída com sucesso!", flush=True)

if __name__ == "__main__":
    try:
        print(f"🚀 INICIANDO FINMAIL - Rastreando vencimentos para o Mês {MES_ALVO}/{ANO_ALVO}...", flush=True)
        processar_email()
    except Exception as e:
        print(f" [ERRO CRÍTICO GERAL NO MOTOR] {e}", flush=True)