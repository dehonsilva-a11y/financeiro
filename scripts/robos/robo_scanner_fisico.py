import os
import shutil
import re
import requests
import json
import time
import sys
from datetime import datetime
from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()

# =======================================================================
# CONFIGURAÇÕES DO SISTEMA (API) E PASTAS DO SCANNER
# =======================================================================
URL_API_DESCOBRIR  = "http://127.0.0.1:5000/api/automacao/descobrir_imovel"
URL_API_COLETA     = "http://127.0.0.1:5000/api/automacao/coletar"
TOKEN_SEGURANCA    = os.getenv("TOKEN_SEGURANCA")
NOME_ROBO          = "AUTOMAÇÃO SCANNER FÍSICO"

CHAVES_API_GEMINI = [
    os.getenv("GEMINI_API_KEY_1"), 
    os.getenv("GEMINI_API_KEY_2"), 
    os.getenv("GEMINI_API_KEY_3")  
]
# Remove chaves nulas ou vazias
CHAVES_API_GEMINI = [k for k in CHAVES_API_GEMINI if k]

INDICE_CHAVE_ATUAL = 0
client = genai.Client(api_key=CHAVES_API_GEMINI[INDICE_CHAVE_ATUAL]) if CHAVES_API_GEMINI else None

WEBHOOK_URL = "https://chat.googleapis.com/v1/spaces/AAQAe4V21ys/messages?key=AIzaSyDdI0hCZtE6vySjMm-WEfRq3CPzqKqqsHI&token=BNpworqFmZgcXP1S_UdCAlwbdh4RlJu2muRcM-uvYbI"

RAIZ_SCANNER = r"\\192.168.100.13\Lello2\ARQUIVOS-DIGITALIZACAO\financeiro_imoveis\Condomínio\SCANNER"

PASTA_ENTRADA_SCANNER = os.path.join(RAIZ_SCANNER, "1_ENTRADA")
PASTA_DUPLICADOS = os.path.join(RAIZ_SCANNER, "2_DUPLICADOS")
PASTA_REJEITADOS = os.path.join(RAIZ_SCANNER, "3_REJEITADOS")

PASTA_DESTINO_GERAL = r"Z:\financeiro_imoveis\Condomínio\boleto__Cond_antes liax"
PASTA_DESTINO_MES = r"\\192.168.100.13\Lello2\ARQUIVOS-DIGITALIZACAO\financeiro_imoveis\Condomínio\JUL26 - NOVA GESTÃO"

for pasta in [PASTA_ENTRADA_SCANNER, PASTA_DUPLICADOS, PASTA_REJEITADOS, PASTA_DESTINO_GERAL, PASTA_DESTINO_MES]:
    os.makedirs(pasta, exist_ok=True)

# =======================================================================
# FUNÇÕES DE COMUNICAÇÃO COM A API LOCAL (BANCO DE DADOS)
# =======================================================================
def descobrir_imovel_por_similaridade(dados_ia):
    """ Manda os dados lidos para a API calcular o Fuzzy Match (>80%) no Banco de Dados """
    try:
        dados_ia["token"] = TOKEN_SEGURANCA
        resposta = requests.post(URL_API_DESCOBRIR, json=dados_ia, timeout=45) # 👈 Alterado aqui
        if resposta.status_code == 200:
            return resposta.json()
        print(f"   [AVISO API] Status {resposta.status_code} na descoberta.", flush=True)
        return None
    except Exception as e:
        print(f"   [ERRO API] Falha de conexão/resposta com a API de Descoberta: {e}", flush=True)
        return None

def dar_baixa_api(id_imovel, score):
    """ Manda o sinal de sucesso (COLETADO) para a API com observação """
    try:
        dados = {
            "token": TOKEN_SEGURANCA, 
            "imovel": str(id_imovel), 
            "nome_robo": NOME_ROBO,
            "status": "COLETADO",
            "observacao": f"Boleto físico capturado via Scanner IA (Similaridade: {score}%)"
        }
        resposta = requests.post(URL_API_COLETA, json=dados, timeout=15)
        return resposta.status_code == 200
    except Exception as e:
        print(f"   [ERRO API] Falha ao enviar baixa para a API: {e}", flush=True)
        return False

# =======================================================================
# HISTÓRICO E UTILIDADES
# =======================================================================
def alternar_chave_api():
    global INDICE_CHAVE_ATUAL, client
    INDICE_CHAVE_ATUAL += 1
    if INDICE_CHAVE_ATUAL >= len(CHAVES_API_GEMINI):
        print("  [CRÍTICO] TODAS as chaves de API do Scanner foram esgotadas!", flush=True)
        return False
    nova_chave = CHAVES_API_GEMINI[INDICE_CHAVE_ATUAL]
    print(f"  🔄 Limite de cota atingido! Alternando para a Chave {INDICE_CHAVE_ATUAL + 1}...", flush=True)
    client = genai.Client(api_key=nova_chave)
    return True

def registrar_historico_scanner(dados_evento):
    caminho_arquivo = "historicos/historico_SCANNER_FISICO.json"
    os.makedirs(os.path.dirname(caminho_arquivo), exist_ok=True)
    historico = []
    if os.path.exists(caminho_arquivo):
        try:
            with open(caminho_arquivo, "r", encoding="utf-8") as f:
                historico = json.load(f)
        except Exception:
            historico = []

    dados_evento["timestamp"] = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    historico.append(dados_evento)
    with open(caminho_arquivo, "w", encoding="utf-8") as f:
        json.dump(historico, f, ensure_ascii=False, indent=4)

def enviar_notificacao_chat(mensagem):
    try:
        requests.post(WEBHOOK_URL, json={"text": mensagem}, timeout=10)
    except Exception:
        pass

def classificar_subpasta_rejeicao(motivo):
    motivo_lower = motivo.lower()
    if "não é" in motivo_lower or "natureza" in motivo_lower or "reserva" in motivo_lower:
        return "1_NAO_EH_BOLETO"
    elif "mês" in motivo_lower:
        return "2_MES_INCORRETO"
    elif "similaridade" in motivo_lower or "abaixo" in motivo_lower:
        return "3_BAIXA_SIMILARIDADE"
    else:
        return "4_DADOS_NAO_BATERAM_NA_BASE"

# =======================================================================
# LÓGICA DE AUDITORIA COM IA VISUAL (OCR NATIVO JSON)
# =======================================================================
def analisar_boleto_escaneado_com_ia(caminho_pdf, tentativas=0):
    print("  👁️ Lendo os dados visuais do boleto para cruzamento...", flush=True)
    
    # PROMPT ATUALIZADO: Com foco cirúrgico na inteligência do bloco e unidade
    prompt = """
    Você é um auditor financeiro rigoroso especializado em condomínios.
    Sua missão é olhar A IMAGEM DO PDF ESCANEADO e extrair DADOS ESTRATÉGICOS PARA CRUZAMENTO.
    Preciso que trabalhe com familiaridade, buscando os dados da nossa base e confrontando com o que está impresso no boleto físico.
    
    ⚠️ Buscamos APENAS a Cota Condominial mensal normal.
    Se for Reserva de espaço, Taxa de Mudança, Acordo Judicial ou Consumo individual isolado, retorne "CONDOMINIO": "NAO".
    
    Concentre-se em capturar perfeitamente as 3 chaves principais de busca: 
    "EMPREENDIMENTO": "NOME PRINCIPAL do Condomínio extraído do documento, IGNORANDO completamente palavras genéricas como 'Condomínio', 'Edifício', 'Residencial', 'Cond.', 'Edif.'. Exemplo: Se no boleto estiver impresso 'CONDOMÍNIO EDIFÍCIO PARQUE DO OLIMPO', me retorne EXATAMENTE APENAS 'PARQUE DO OLIMPO'.",
    2. UNIDADE E BLOCO
    3. NOME DO SACADO (Pagador)
    
    🚨 ATENÇÃO ESPECIAL À UNIDADE E BLOCO (MUITO IMPORTANTE) 🚨
    Na impressão do boleto, a unidade/bloco pode vir "suja" com barras, traços ou espaços estranhos (Exemplos: "21/b", "M/1109", "3 / 82", "0 122").
    Você precisa PADRONIZAR essa extração para que eu consiga encontrar na minha base de dados.
    - Remova barras ("/") e traços ("-").
    - Identifique o que é Bloco e o que é Apartamento/Unidade e formate por extenso sempre que notar ambiguidade.
    - Exemplo 1: Se ler "21/b", retorne "BLOCO B APTO 21" (ou BLOCO 2 APTO 21).
    - Exemplo 2: Se ler "M/1109", retorne "BLOCO M APTO 1109" ou "M 1109".
    - Exemplo 3: Se ler "3 / 82" ou "3 82", retorne "BLOCO 3 APTO 82".
    
    Retorne a seguinte estrutura JSON exata:
    {
      "CONDOMINIO": "SIM" ou "NAO",
      "SACADO": "Nome completo do pagador impresso (remova prefixos como Sr./Sra.)",
      "EMPREENDIMENTO": "Nome do Condomínio extraído do documento sem sujeiras",
      "UNIDADE_BLOCO": "Bloco e Unidade padronizados (Ex: BLOCO B APTO 21)",
      "CPF_CNPJ": "Apenas os numeros",
      "MES_VENCIMENTO": "Apenas o numero do mes de vencimento. Ex: 7 ou 07"
    }
    """
    
    arquivo_upload = None
    try:
        arquivo_upload = client.files.upload(file=caminho_pdf)
        
        config = types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.1
        )

        resposta = client.models.generate_content(
            model='gemini-2.5-flash', 
            contents=[arquivo_upload, prompt],
            config=config
        )
        
        return json.loads(resposta.text)

    except Exception as e:
        msg_erro = str(e)
        
        # 1. Se o erro for 503 (Servidor Ocupado), NÃO troca de chave, apenas espera e tenta de novo.
        if "503" in msg_erro and tentativas < 3:
            print("  ⚠️ Servidor do Google sobrecarregado (503). Aguardando 5s para tentar de novo com a mesma chave...", flush=True)
            time.sleep(5) 
            return analisar_boleto_escaneado_com_ia(caminho_pdf, tentativas + 1)
            
        # 2. Se o erro for 429 (Limite de Cota Atingido), aí sim troca de chave!
        elif ("429" in msg_erro or "Quota" in msg_erro) and tentativas < len(CHAVES_API_GEMINI):
            if alternar_chave_api():
                return analisar_boleto_escaneado_com_ia(caminho_pdf, tentativas + 1)
                
        print(f"  [ERRO IA] Falha no processamento visual do boleto: {e}", flush=True)
        return {}
    finally:
        if arquivo_upload:
            try:
                client.files.delete(name=arquivo_upload.name)
            except Exception:
                pass

# =======================================================================
# MOTOR PRINCIPAL DO SCANNER
# =======================================================================
def processar_pastas_scanner():
    arquivos_entrada = [f for f in os.listdir(PASTA_ENTRADA_SCANNER) if f.lower().endswith('.pdf')]
    
    if not arquivos_entrada: 
        print("Nenhum boleto físico novo encontrado na pasta de entrada.", flush=True)
        return 

    print(f"\n[SCANNER] Encontrados {len(arquivos_entrada)} boletos físicos para auditar.", flush=True)

    # ---------------------------------------------------------
    # LÓGICA DE MÊS ATUAL E MÊS SEGUINTE
    # ---------------------------------------------------------
    mes_atual = datetime.now().month
    mes_seguinte = 1 if mes_atual == 12 else mes_atual + 1
    meses_permitidos = [str(mes_atual), str(mes_seguinte)]
    
    print(f"[FILTRO] Aceitando boletos dos meses: {mes_atual} e {mes_seguinte}.", flush=True)

    for arquivo in arquivos_entrada:
        caminho_original = os.path.join(PASTA_ENTRADA_SCANNER, arquivo)
        nome_limpo = re.sub(r'^(API_REJEITOU_|NAO_BOLETO_|MES_ERRADO_|DUPLICADO_)+', '', arquivo)
        
        print(f"\n📄 Lendo boleto escaneado: {arquivo}", flush=True)

        analise_ia = analisar_boleto_escaneado_com_ia(caminho_original)
        
        if not analise_ia or str(analise_ia.get("CONDOMINIO", "")).upper() != "SIM":
            motivo_erro = "Documento físico não foi reconhecido como cota condominial válida."
            print(f"   Rejeitado: {motivo_erro}", flush=True)
            
            nome_subpasta = classificar_subpasta_rejeicao(motivo_erro)
            pasta_final_rejeicao = os.path.join(PASTA_REJEITADOS, nome_subpasta)
            os.makedirs(pasta_final_rejeicao, exist_ok=True)
            
            caminho_rejeitado = os.path.join(pasta_final_rejeicao, f"NAO_BOLETO_{nome_limpo}")
            try:
                shutil.move(caminho_original, caminho_rejeitado)
            except Exception as e:
                print(f"  [ERRO REDE] Falha ao mover arquivo: {e}")
            
            registrar_historico_scanner({
                "arquivo_original": arquivo,
                "status": "IGNORADO_NAO_BOLETO",
                "motivo": motivo_erro,
                "caminho_rede": caminho_rejeitado
            })
            continue

        mes_venc = str(analise_ia.get("MES_VENCIMENTO", "")).strip().lstrip('0')

        if mes_venc and mes_venc not in meses_permitidos:
            motivo_erro = f"Mês de vencimento é {mes_venc}, mas o robô aceita apenas o mês atual ({meses_permitidos[0]}) e o seguinte ({meses_permitidos[1]})."
            print(f"   Rejeitado: {motivo_erro}", flush=True)
            nome_subpasta = classificar_subpasta_rejeicao(motivo_erro)
            pasta_final_rejeicao = os.path.join(PASTA_REJEITADOS, nome_subpasta)
            os.makedirs(pasta_final_rejeicao, exist_ok=True)
            
            caminho_rejeitado = os.path.join(pasta_final_rejeicao, f"MES_ERRADO_{nome_limpo}")
            try:
                shutil.move(caminho_original, caminho_rejeitado)
            except Exception as e:
                print(f"  [ERRO REDE] Falha ao mover arquivo: {e}")
            continue

        # -------------------------------------------------------------
        # TRATAMENTO PYTHON EXTRA DA UNIDADE ANTES DE MANDAR PARA API
        # -------------------------------------------------------------
        # Remove fisicamente qualquer traço ou barra caso a IA os traga no Payload
        unidade_limpa_python = str(analise_ia.get("UNIDADE_BLOCO", "")).replace("/", " ").replace("-", " ").strip()
        
        print(f"  📡 Cruzando dados com a base: {str(analise_ia.get('SACADO', ''))[:20]}... [Unidade Identificada: {unidade_limpa_python}]", flush=True)
        payload_api = {
            "sacado": analise_ia.get("SACADO", ""),
            "empreendimento": analise_ia.get("EMPREENDIMENTO", ""),
            "unidade": unidade_limpa_python,
            "cpf": analise_ia.get("CPF_CNPJ", "")
        }
        
        dados_api = descobrir_imovel_por_similaridade(payload_api)

        if not dados_api or not dados_api.get('valido'):
            motivo_erro = dados_api.get('msg', 'Rejeitado por Baixa Similaridade') if dados_api else "Falha de comunicação com a API do Banco de Dados."
            print(f"   Rejeitado pela API: {motivo_erro}", flush=True)
            
            nome_subpasta = classificar_subpasta_rejeicao(motivo_erro)
            pasta_final_rejeicao = os.path.join(PASTA_REJEITADOS, nome_subpasta)
            os.makedirs(pasta_final_rejeicao, exist_ok=True)
            
            caminho_rejeitado = os.path.join(pasta_final_rejeicao, f"API_REJEITOU_{nome_limpo}")
            try:
                shutil.move(caminho_original, caminho_rejeitado)
            except Exception as e:
                print(f"  [ERRO REDE] Falha ao mover arquivo: {e}")
            
            registrar_historico_scanner({
                "arquivo_original": arquivo,
                "status": "REJEITADO_VALIDACAO",
                "motivo": motivo_erro,
                "caminho_rede": caminho_rejeitado
            })
            continue

        id_imovel = dados_api.get('imovel_real')
        score = dados_api.get('score')
        responsavel = dados_api.get('responsavel', 'N/A')
        status_api = dados_api.get('status_atual', '')

        print(f"   MATCH PERFEITO! Imóvel ID: {id_imovel} (Score: {score}%) | Resp: {responsavel}", flush=True)

        nome_final = f"{id_imovel}.pdf"
        caminho_geral = os.path.join(PASTA_DESTINO_GERAL, nome_final)
        caminho_mes = os.path.join(PASTA_DESTINO_MES, nome_final)

        if status_api in ['PAGO', 'COLETADO']:
            print("  ⚠️ Este imóvel já estava marcado como 'coletado' no sistema web!", flush=True)
            caminho_duplicado = os.path.join(PASTA_DUPLICADOS, f"DUPLICADO_{nome_final}")
            try:
                shutil.move(caminho_original, caminho_duplicado)
            except Exception as e:
                print(f"  [ERRO REDE] Falha ao mover arquivo duplicado: {e}")
            
            registrar_historico_scanner({
                "arquivo_original": arquivo,
                "imovel_id": id_imovel,
                "responsavel": responsavel,
                "status": "IGNORADO_DUPLICADO",
                "motivo": "Já estava coletado na API.",
                "caminho_rede": caminho_duplicado
            })
            continue
        
        print("   Imóvel pendente na API. Salvando e dando baixa...", flush=True)
        try:
            shutil.copy2(caminho_original, caminho_geral)
            shutil.move(caminho_original, caminho_mes)
            print(f"   Salvo nas pastas de destino com sucesso.", flush=True)
            
            if dar_baixa_api(id_imovel, score):
                print(f"   Sucesso! Status do imóvel {id_imovel} alterado para COLETADO no painel.", flush=True)
            
            enviar_notificacao_chat(f"📄 *Físico Coletado com IA (Match {score}%)*\nImóvel: `{id_imovel}`\nResponsável: *{responsavel}*")
            registrar_historico_scanner({
                "arquivo_original": arquivo,
                "imovel_id": id_imovel,
                "responsavel": responsavel,
                "status": "SUCESSO",
                "motivo": f"Similaridade {score}%",
                "caminho_rede": caminho_mes
            })
            
        except Exception as e:
            print(f"   Erro ao salvar/mover o PDF físico: {e}", flush=True)
            registrar_historico_scanner({
                "arquivo_original": arquivo,
                "imovel_id": id_imovel,
                "responsavel": responsavel,
                "status": "ERRO_REDE",
                "motivo": str(e),
                "caminho_rede": ""
            })

    print("\nLote do Scanner processado e sincronizado com sucesso!", flush=True)

# =======================================================================
# EXECUÇÃO ÚNICA (SEM LOOP) PARA O ORQUESTRADOR
# =======================================================================
if __name__ == "__main__":
    print("🚀 [SISTEMA] Iniciando varredura única do Scanner Físico...", flush=True)
    processar_pastas_scanner()
    print("🏁 Processo do Scanner concluído. Desligando motor.", flush=True)