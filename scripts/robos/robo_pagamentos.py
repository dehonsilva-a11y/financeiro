import os
import sys
import re
import io
import json
import time
import uuid
import atexit
import shutil
import hashlib
import tempfile
import datetime
import unicodedata
import warnings
from decimal import Decimal
from concurrent.futures import ThreadPoolExecutor

from dotenv import load_dotenv
load_dotenv()

import pdfplumber
import requests
import httpx

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

# =================================================================
# O QUE O PAINEL (app.py) FAZ COM A SAÍDA DESTE ROBÔ
# Cada linha impressa é lida pelo painel: "🎯" / "sucesso" = +1 sucesso; "🚨" / "erro" / "falha" / "barrou" /
# "ausente" = +1 falha. Por isso: UMA linha "🎯 SUCESSO" por boleto lançado, UMA linha "🚨 FALHA" por boleto
# devolvido à equipe, e as demais mensagens evitam essas palavras.
# =================================================================

# =================================================================
# INTEGRAÇÃO (SISTEMA LELLO)
# =================================================================
API_BASE = os.getenv("LELLO_API_URL", "http://127.0.0.1:5000").rstrip("/")
URL_PENDENTES = f"{API_BASE}/api/automacao/pagamentos_pendentes"
URL_BAIXA = f"{API_BASE}/api/automacao/pagar"
TOKEN = os.getenv("TOKEN_SEGURANCA")
NOME_DO_ROBO = "AUTOMAÇÃO PAGAMENTOS"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


# =============================================================
# CONFIGURAÇÕES DE AMBIENTE E PASTAS
# 🔐 Nenhuma senha, chave ou webhook fica mais dentro do código. Tudo vem do arquivo .env:
#    LELLO_USUARIO, LELLO_SENHA, CHAT_WEBHOOK_URL, GEMINI_API_KEY_1 (…_2, …_3) e TOKEN_SEGURANCA.
# =============================================================
USUARIO_LELLO = (os.getenv("LELLO_USUARIO") or "").strip()
SENHA_LELLO = os.getenv("LELLO_SENHA") or ""
CHAT_WEBHOOK_URL = (os.getenv("CHAT_WEBHOOK_URL") or "").strip()


def carregar_chaves_gemini():
    """Só entram chaves realmente preenchidas (chave vazia/None dava 403 PERMISSION_DENIED)."""
    brutas = [os.getenv(f"GEMINI_API_KEY_{i}") for i in range(1, 7)] + (os.getenv("GEMINI_API_KEYS") or "").split(",")
    chaves = []
    for k in brutas:
        if k and k.strip() and k.strip() not in chaves:
            chaves.append(k.strip())
    return chaves


CHAVES_API = carregar_chaves_gemini()
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
PASTA_DIAG = os.path.join(BASE_DIR, "diagnostico")

# 🐢➡️🐇 Multiplicador das pausas fixas dentro do portal. 1 = igual ao que sempre foi (seguro).
# Depois de validar, dá para tentar ROBO_PAUSA_FATOR=0.7 no .env e ver se o portal acompanha.
try:
    FATOR_PAUSA = min(max(float(os.getenv("ROBO_PAUSA_FATOR", "1")), 0.3), 3.0)
except ValueError:
    FATOR_PAUSA = 1.0

# 🧮 O que fazer quando a soma das verbas que a IA leu não bate com o valor gravado na linha digitável do boleto:
#   "avisar" (padrão): lança e deixa um aviso no log | "bloquear": devolve para a equipe conferir | "desligado": ignora.
#   Vale também para o vencimento: boleto que vence a mais de 45 dias do vencimento da linha costuma ser PDF de outro mês.
CONFERIR_VALOR = (os.getenv("ROBO_CONFERIR_VALOR", "avisar") or "avisar").strip().lower()
TOLERANCIA_VALOR = Decimal("0.05")
LIMITE_DIAS_VENCIMENTO = 45     # vencimento do boleto (lido da linha digitável) a mais que isso do da linha = provável PDF de outro mês

# Se o portal falhar tecnicamente N vezes seguidas, a rodada para (e avisa no Chat) em vez de insistir imóvel por imóvel.
try:
    LIMITE_FALHAS_SEGUIDAS = max(2, int(os.getenv("ROBO_MAX_FALHAS_SEGUIDAS", "4")))
except ValueError:
    LIMITE_FALHAS_SEGUIDAS = 4

# Vários robôs (Força Tarefa) dividem o MESMO login do portal: se um entra, o outro pode ser derrubado e entrar de volta,
# e os dois ficam se expulsando. Sessão que expira de vez em quando é normal; o que não é normal é refazer o login várias vezes
# em poucos minutos — nesse caso a rodada para e avisa em vez de insistir.
try:
    MAX_RELOGINS = max(1, int(os.getenv("ROBO_MAX_RELOGINS", "3")))
except ValueError:
    MAX_RELOGINS = 3
JANELA_RELOGINS = 600           # segundos
_RELOGINS_FEITOS = []           # horários (time.time) dos logins refeitos nesta execução

# 🛠️ Quando o portal recusa o lançamento por causa do vencimento da ficha ("vencimento não definido na ficha"), o robô abre a
# administradora (link "Ad.Cond" da ficha), acerta o "Dia Venc. Cond." para o dia do boleto, grava, volta e refaz o lançamento.
# ROBO_CORRIGIR_VENCIMENTO_FICHA=0 desliga (o robô volta a só devolver a linha para a equipe).
CORRIGIR_VENCIMENTO_FICHA = (os.getenv("ROBO_CORRIGIR_VENCIMENTO_FICHA", "1") or "1").strip().lower() not in ("0", "nao", "não", "false", "desligado")
ESPERA_FICHA_SEGUNDOS = 25
# a mensagem do portal precisa falar de vencimento E dizer que ele não está definido/informado (já sem acento e em minúsculas)
PADRAO_VENCIMENTO_FICHA = re.compile(r"\bnao\s+(?:\w+\s+){0,2}(?:definid|informad|cadastrad|configurad|preenchid)|\bsem\s+(?:dia\s+(?:de\s+)?)?venc")

# 📁 Os boletos ficam numa pasta por mês, dentro da raiz abaixo: "NOV26 - NOVA GESTÃO", "OUT26 - NOVA GESTÃO"...
# O robô acha a pasta sozinho pelo MÊS DO VENCIMENTO de cada linha (mesma regra do restante do sistema), não pela data do
# computador: em outubro, boleto que vence em novembro é procurado na pasta de novembro.
# Além do nome padrão, aceita variações: "NOV 26", "NOV-2026", "NOVEMBRO 2026"... (sem diferença de maiúscula/acento).
# Para forçar UMA pasta qualquer (como era antes): ROBO_PASTA_FIXA=C:\caminho\da\pasta. Para outra raiz: ROBO_RAIZ_BOLETOS.
RAIZES_REDE = [r for r in [os.getenv("ROBO_RAIZ_BOLETOS", "").strip(),
                           r"\\192.168.100.13\Lello2\ARQUIVOS-DIGITALIZACAO\financeiro_imoveis\Condomínio",
                           r"Z:\financeiro_imoveis\Condomínio"] if r]
SUFIXO_PASTA_MES = "NOVA GESTÃO"
PASTA_FIXA = os.getenv("ROBO_PASTA_FIXA", "").strip()
MESES_DA_PASTA = {1: ("jan", "janeiro"), 2: ("fev", "fevereiro"), 3: ("mar", "marco"), 4: ("abr", "abril"), 5: ("mai", "maio"),
                  6: ("jun", "junho"), 7: ("jul", "julho"), 8: ("ago", "agosto"), 9: ("set", "setembro"), 10: ("out", "outubro"),
                  11: ("nov", "novembro"), 12: ("dez", "dezembro")}
PASTA_LOCAL_PAGS = r'C:\Users\ryan.lima\Documents\Pagamentos\pags'
MODO_LEITURA = (os.getenv("ROBO_MODO_LEITURA", "REDE") or "REDE").strip().upper()

URL_LOGIN = "https://pi.lelloimoveis.com.br/portalImoveis/"
URL_MOVIMENTO = "https://pi.lelloimoveis.com.br/portalImoveis/pages/movimentoBoletoCondominio/movimentoBoletoCondominio.xhtml"

PREFIXO_ERRO_TECNICO = "Erro inesperado no portal"

# Memória do robô entre uma rodada e outra (o que já foi lançado, avisos que o painel ainda não recebeu, cache da IA)
PASTA_ESTADO = os.path.join(BASE_DIR, "estado_robo")
ARQ_LEDGER = os.path.join(PASTA_ESTADO, "pagamentos_estado.json")
ARQ_LEDGER_TRAVA = os.path.join(PASTA_ESTADO, "pagamentos_estado.lock")
PASTA_CACHE_IA = os.path.join(PASTA_ESTADO, "cache_ia")
PROMPT_VERSAO = "v1"          # mude se alterar o prompt da IA: invalida o cache
DIAS_CACHE_IA = 20


def pausa(segundos):
    """Pausa fixa dentro do portal (ajustável por ROBO_PAUSA_FATOR)."""
    time.sleep(segundos * FATOR_PAUSA)


def aplicar_patch_httpx():
    """O Gemini (httpx) roda atrás da rede da empresa: mantém a verificação de SSL desligada como sempre foi.
    Para voltar a verificar: ROBO_SSL_VERIFICAR=1 no .env."""
    if os.getenv("ROBO_SSL_VERIFICAR", "0") == "1":
        return
    original = httpx.Client.__init__

    def _patched_client_init(self, *args, **kwargs):
        kwargs['verify'] = False
        headers = kwargs.get('headers', {})
        if isinstance(headers, dict):
            headers['User-Agent'] = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
        kwargs['headers'] = headers
        original(self, *args, **kwargs)

    httpx.Client.__init__ = _patched_client_init


# =============================================================
# FILTROS DA TELA (RESP:...|EMP:...|DIAS:...)
# =============================================================
def sem_acento(texto):
    base = unicodedata.normalize('NFKD', str(texto or ''))
    return ''.join(c for c in base if not unicodedata.combining(c)).strip().lower()


def interpretar_filtro(filtro_bruto):
    """Devolve (responsavel, empreendimento, {dias}) já sem acento/maiúscula. Sem os prefixos, o texto todo é o responsável."""
    filtro_bruto = filtro_bruto or ""
    resp, emp, dias = "", "", set()
    if "RESP:" in filtro_bruto or "EMP:" in filtro_bruto or "DIAS:" in filtro_bruto:
        for p in filtro_bruto.split('|'):
            if p.startswith("RESP:"):
                resp = sem_acento(p.replace("RESP:", ""))
            elif p.startswith("EMP:"):
                emp = sem_acento(p.replace("EMP:", ""))
            elif p.startswith("DIAS:"):
                for d in p.replace("DIAS:", "").split(","):
                    d = d.strip()
                    if d.isdigit():
                        dias.add(int(d))
    else:
        resp = sem_acento(filtro_bruto)
    return resp, emp, dias


def data_do_vencimento(venc):
    """'05/11/2026', '05-11-26' ou '2026-11-05' -> date (None se não der para ler)."""
    texto = str(venc or '')
    m = re.search(r'(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)', texto)
    try:
        if m:
            return datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        m = re.search(r'(?<!\d)(\d{1,2})[/-](\d{1,2})[/-](\d{4}|\d{2})(?!\d)', texto)
        if m:
            ano = int(m.group(3)) + (2000 if len(m.group(3)) == 2 else 0)
            return datetime.date(ano, int(m.group(2)), int(m.group(1)))
    except ValueError:
        pass
    return None


def dia_do_vencimento(venc):
    """'05/11/2026' ou '2026-11-15' -> 5 / 15 (None se não der para ler)."""
    data = data_do_vencimento(venc)
    return data.day if data else None


# =============================================================
# COMUNICAÇÃO COM O PAINEL
# =============================================================
def _requisitar(url, payload, tentativas=1, timeout=30):
    """POST com nova tentativa só para queda de rede / erro 5xx. Devolve (status_http | None, json | None)."""
    ultimo = (None, None)
    for t in range(1, tentativas + 1):
        try:
            r = requests.post(url, json=payload, timeout=timeout)
            try:
                corpo = r.json()
            except ValueError:
                corpo = None
            if r.status_code < 500:
                return r.status_code, corpo
            ultimo = (r.status_code, corpo)
        except requests.RequestException as e:
            ultimo = (None, {'msg': str(e)})
        if t < tentativas:
            time.sleep(min(2 ** t, 15))
    return ultimo


def buscar_boletos_coletados():
    print("🔍 Conectando à API local para buscar fila de pagamentos...", flush=True)
    status, resposta = _requisitar(URL_PENDENTES, {"token": TOKEN}, tentativas=3)
    if status == 401:
        print("🚨 O painel recusou o TOKEN_SEGURANCA deste robô. Confira o .env.", flush=True)
        return []
    if status is None or not isinstance(resposta, dict):
        print(f"🚨 Falha crítica ao conectar com a API de Orquestração (status {status}).", flush=True)
        return []
    if resposta.get('sucesso') and (resposta.get('quantidade') or 0) > 0:
        print(f"📊 O site retornou {resposta['quantidade']} imóveis aguardando pagamento.", flush=True)
        return resposta['dados']
    print("⚠️ Fila vazia. Nenhum imóvel com status 'COLETADO' no momento.", flush=True)
    return []


def informar_status_ao_site(item):
    """Avisa o painel do resultado de UM boleto (por id da linha; imóvel + vencimento como reserva).
    Devolve 'ok' | 'final' (o painel respondeu e repetir não muda nada) | 'pendente' (painel fora do ar: tenta de novo depois)."""
    payload = {
        "token": TOKEN, "imovel": str(item.get('num', '')), "id": item.get('id'), "vencimento": item.get('venc') or "",
        "nome_robo": NOME_DO_ROBO, "sucesso": bool(item.get('sucesso')), "erro_msg": item.get('erro_msg') or "",
    }
    status, corpo = _requisitar(URL_BAIXA, payload, tentativas=4)
    if status == 200 and isinstance(corpo, dict) and corpo.get('sucesso'):
        return 'ok'
    if status is not None and status < 500:
        motivo = (corpo or {}).get('msg') if isinstance(corpo, dict) else None
        print(f"⚠️ O painel não aceitou o aviso do imóvel {item.get('num')} (HTTP {status}{': ' + motivo if motivo else ''}).", flush=True)
        return 'final'
    print(f"⚠️ Painel fora do ar: o aviso do imóvel {item.get('num')} fica guardado e será reenviado na próxima rodada.", flush=True)
    return 'pendente'


# =============================================================
# MEMÓRIA ENTRE RODADAS (anti-duplicidade + avisos pendentes + reserva entre robôs paralelos)
# Cada operador roda um processo separado: tudo aqui passa por uma trava de arquivo.
# =============================================================
class _Trava:
    def __init__(self, caminho=None):
        self.caminho = caminho or ARQ_TRAVA

    def __enter__(self):
        os.makedirs(os.path.dirname(self.caminho), exist_ok=True)
        while True:
            try:
                self.fd = os.open(self.caminho, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                return self
            except FileExistsError:
                try:
                    if time.time() - os.path.getmtime(self.caminho) > 20:
                        os.remove(self.caminho)  # trava órfã
                except OSError:
                    pass
                time.sleep(0.05)

    def __exit__(self, *args):
        try:
            os.close(self.fd)
        except OSError:
            pass
        try:
            os.remove(self.caminho)
        except OSError:
            pass


def _estado_ler():
    try:
        with open(ARQ_LEDGER, encoding="utf-8") as f:
            est = json.load(f)
        if not isinstance(est, dict):
            est = {}
    except Exception:
        est = {}
    est.setdefault('lancados', {})
    est.setdefault('a_informar', [])
    est.setdefault('reservas', {})
    return est


class _Estado:
    """with _Estado() as est: ...  -> lê, deixa mexer e grava de volta (só se não deu exceção), tudo sob trava."""
    def __enter__(self):
        self._trava = _Trava(ARQ_LEDGER_TRAVA)
        self._trava.__enter__()
        try:
            self.est = _estado_ler()
        except Exception:
            self._trava.__exit__()
            raise
        return self.est

    def __exit__(self, tipo, valor, tb):
        try:
            if tipo is None:
                os.makedirs(PASTA_ESTADO, exist_ok=True)
                tmp = ARQ_LEDGER + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(self.est, f, ensure_ascii=False)
                os.replace(tmp, ARQ_LEDGER)
        finally:
            self._trava.__exit__()


def _agora():
    return datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def podar_estado():
    """Joga fora o que já não serve: lançamentos antigos, reservas vencidas, avisos com mais de 7 dias."""
    limite_lanc = (datetime.datetime.now() - datetime.timedelta(days=120)).strftime('%Y-%m-%d %H:%M:%S')
    limite_aviso = (datetime.datetime.now() - datetime.timedelta(days=7)).strftime('%Y-%m-%d %H:%M:%S')
    with _Estado() as est:
        est['lancados'] = {k: v for k, v in est['lancados'].items() if str(v.get('quando', '')) >= limite_lanc}
        est['a_informar'] = [i for i in est['a_informar'] if str(i.get('quando', '')) >= limite_aviso]
        est['reservas'] = {k: v for k, v in est['reservas'].items() if float(v.get('ate', 0)) > time.time()}
    try:
        for nome in os.listdir(PASTA_CACHE_IA):
            caminho = os.path.join(PASTA_CACHE_IA, nome)
            if time.time() - os.path.getmtime(caminho) > DIAS_CACHE_IA * 86400:
                os.remove(caminho)
    except OSError:
        pass


def boleto_ja_lancado(chave):
    with _Estado() as est:
        return est['lancados'].get(chave)


def chave_da_linha(tarefa):
    return f"id:{tarefa['id']}" if tarefa.get('id') is not None else f"{tarefa['num']}|{tarefa.get('venc', '')}"


def reservar_linha(tarefa, minutos=25):
    """False se OUTRO robô (processo) está com esta linha agora."""
    chave = chave_da_linha(tarefa)
    with _Estado() as est:
        atual = est['reservas'].get(chave)
        if atual and float(atual.get('ate', 0)) > time.time() and atual.get('pid') != os.getpid():
            return False
        est['reservas'][chave] = {'pid': os.getpid(), 'ate': time.time() + minutos * 60}
    return True


def liberar_linha(tarefa):
    try:
        with _Estado() as est:
            atual = est['reservas'].get(chave_da_linha(tarefa))
            if atual and atual.get('pid') == os.getpid():
                del est['reservas'][chave_da_linha(tarefa)]
    except Exception:
        pass


def _novo_item_de_aviso(tarefa, deu_certo, msg_erro=""):
    return {'uid': uuid.uuid4().hex, 'num': tarefa['num'], 'id': tarefa.get('id'), 'venc': tarefa.get('venc', ''),
            'sucesso': bool(deu_certo), 'erro_msg': msg_erro or "", 'quando': _agora()}


def _entregar_aviso(item):
    resultado = informar_status_ao_site(item)
    if resultado in ('ok', 'final'):
        with _Estado() as est:
            est['a_informar'] = [i for i in est['a_informar'] if i.get('uid') != item['uid']]
    return resultado


def informar_resultado(tarefa, deu_certo, msg_erro="", chave_boleto=None):
    """Guarda o aviso em disco ANTES de mandar ao painel (se o robô cair ou o painel estiver fora, nada se perde)
    e, quando é um lançamento, anota o boleto como já lançado — assim ele nunca é lançado duas vezes."""
    item = _novo_item_de_aviso(tarefa, deu_certo, msg_erro)
    with _Estado() as est:
        # o mesmo aviso da mesma linha não se acumula se o painel ficar várias rodadas fora do ar
        est['a_informar'] = [i for i in est['a_informar']
                             if not (i.get('id') == item['id'] and i.get('num') == item['num'] and i.get('venc') == item['venc']
                                     and bool(i.get('sucesso')) == item['sucesso'])]
        est['a_informar'].append(item)
        if deu_certo and chave_boleto:
            est['lancados'][chave_boleto] = {'id': tarefa.get('id'), 'num': tarefa['num'], 'venc': tarefa.get('venc', ''), 'quando': _agora()}
    return _entregar_aviso(item)


def reenviar_avisos_pendentes():
    with _Estado() as est:
        itens = list(est['a_informar'])
    if not itens:
        return
    print(f"📨 Reenviando {len(itens)} aviso(s) que ficaram guardados de rodadas anteriores...", flush=True)
    for item in itens:
        r = _entregar_aviso(item)
        print(f"📨 Imóvel {item.get('num')}: {'entregue' if r != 'pendente' else 'continua guardado'}.", flush=True)


# =============================================================
# CHAT GOOGLE
# =============================================================
def enviar_notificacao_chat(id_imovel, administradora, responsavel, tipo="SUCESSO", mensagem_erro=""):
    if not CHAT_WEBHOOK_URL:
        return
    if tipo == "AVISO": texto = f" *Aguardando o Google*\n\n*ID:* {id_imovel}\n*Detalhe:* {mensagem_erro}"
    elif tipo == "FILA": texto = f"🔄 *Automação*\n\n{mensagem_erro}"
    elif tipo == "VERBA_NEGATIVA": texto = f"⚠️ *Boleto Barrado: Verba Negativa*\n\n*ID:* {id_imovel}\n*Resp:* {responsavel}\n*Motivo:* {mensagem_erro}"
    else: return
    try: requests.post(CHAT_WEBHOOK_URL, json={"text": texto}, timeout=10)
    except Exception: pass


# =============================================================
# BUSCA DOS PDFs (a pasta de rede é lida UMA vez por rodada)
# =============================================================
def nome_de_pdf_valido(nome):
    baixo = nome.lower()
    return baixo.endswith(".pdf") and not baixo.endswith("_comprimido.pdf") and not baixo.startswith("~$")


def _esperar_rede(funcao):
    """Roda funcao(); se a rede cair (VPN), avisa no Chat e espera ela voltar."""
    avisou_chat = False
    while True:
        try:
            resultado = funcao()
            if avisou_chat:
                enviar_notificacao_chat("", "", "", "FILA", " *A VPN voltou!* O automação retomou o processamento da rede.")
            return resultado
        except OSError:
            if not avisou_chat:
                enviar_notificacao_chat("", "", "", "FILA", "🚨 *A VPN CAIU!* A automação está esperando. Reconecte a VPN.")
                avisou_chat = True
            print("⚠️ [REDE] VPN caiu. Aguardando conexão...", flush=True)
            time.sleep(10)


def _pdfs_da_pasta(pasta):
    return [(f, os.path.join(pasta, f)) for f in os.listdir(pasta) if nome_de_pdf_valido(f)]


def usa_pasta_unica(modo_leitura="REDE"):
    """Modo LOCAL ou ROBO_PASTA_FIXA: uma pasta só, sem dividir por mês."""
    return modo_leitura == "LOCAL" or bool(PASTA_FIXA)


def listar_pdfs(modo_leitura="REDE"):
    """[(nome, caminho)] da pasta única de leitura (modo LOCAL ou ROBO_PASTA_FIXA)."""
    if modo_leitura == "LOCAL":
        return _pdfs_da_pasta(PASTA_LOCAL_PAGS) if os.path.exists(PASTA_LOCAL_PAGS) else []

    def ler():
        if not os.path.exists(PASTA_FIXA):
            raise OSError(64, "Rede inacessível")
        return _pdfs_da_pasta(PASTA_FIXA)
    return _esperar_rede(ler)


def nome_padrao_da_pasta(ano, mes):
    return f"{MESES_DA_PASTA[mes][0].upper()}{ano % 100:02d} - {SUFIXO_PASTA_MES}"


def rotulo_do_mes(ano, mes):
    return f"{MESES_DA_PASTA[mes][0].upper()}/{ano}"


def pasta_combina_com_mes(nome_da_pasta, ano, mes):
    """'NOV26 - NOVA GESTÃO', 'nov 26', 'Novembro-2026'... valem para (2026, 11). Mês por extenso ou abreviado + ano de 2 ou 4 dígitos."""
    abrev, cheio = MESES_DA_PASTA[mes]
    padrao = rf"(?<![a-z0-9])(?:{cheio}|{abrev})[\s._/\-]*(?:20)?{ano % 100:02d}(?!\d)"
    return re.search(padrao, sem_acento(nome_da_pasta)) is not None


def achar_pasta_do_mes(raiz, ano, mes):
    """Caminho da pasta do mês dentro da raiz (ou None). O nome padrão vale primeiro; depois qualquer variação com mês+ano,
    preferindo a que tem 'NOVA GESTÃO' no nome. Levanta OSError se a raiz não puder ser lida (rede caiu)."""
    exata = os.path.join(raiz, nome_padrao_da_pasta(ano, mes))
    if os.path.isdir(exata):
        return exata
    candidatas = [n for n in os.listdir(raiz) if os.path.isdir(os.path.join(raiz, n)) and pasta_combina_com_mes(n, ano, mes)]
    if not candidatas:
        return None
    alvo = sem_acento(SUFIXO_PASTA_MES)
    candidatas.sort(key=lambda n: (0 if alvo in sem_acento(n) else 1, len(n), n.lower()))
    if len(candidatas) > 1:
        print(f"📁 Mais de uma pasta combina com {rotulo_do_mes(ano, mes)}: {', '.join(candidatas)}. Usando '{candidatas[0]}'.", flush=True)
    return os.path.join(raiz, candidatas[0])


def raiz_dos_boletos():
    for raiz in RAIZES_REDE:
        if os.path.isdir(raiz):
            return raiz
    return None


def listar_pdfs_do_mes(ano, mes):
    """(pasta | None, [(nome, caminho)]) do mês. Na rede, se a VPN cair, espera ela voltar (avisando no Chat)."""
    def ler():
        raiz = raiz_dos_boletos()
        if raiz is None:
            raise OSError(64, "Rede inacessível")
        pasta = achar_pasta_do_mes(raiz, ano, mes)
        return (pasta, _pdfs_da_pasta(pasta)) if pasta else (None, [])
    return _esperar_rede(ler)


def mes_do_vencimento(venc):
    data = data_do_vencimento(venc)
    return (data.year, data.month) if data else None


def meses_da_fila(fila_bruta):
    """Meses (ano, mes) dos vencimentos da fila e o mês da base (o mais comum)."""
    contagem = {}
    for d in fila_bruta or []:
        ym = mes_do_vencimento(d.get('vencimento'))
        if ym:
            contagem[ym] = contagem.get(ym, 0) + 1
    base = max(contagem, key=lambda k: (contagem[k], k)) if contagem else None
    return sorted(contagem), base


def meses_padrao_pelo_relogio(hoje=None):
    """Sem nenhum vencimento para guiar: o mês seguinte e o atual (como o resto do sistema)."""
    hoje = hoje or datetime.date.today()
    seguinte = (hoje.year + (hoje.month == 12), hoje.month % 12 + 1)
    return [seguinte, (hoje.year, hoje.month)]


def _numero_sem_zeros(num):
    return str(num).strip().lstrip("0") or "0"


def padrao_do_imovel(num):
    """O número do imóvel como NÚMERO INTEIRO dentro do nome do arquivo ('278977.pdf', '0278977-2.pdf').
    Antes era 'num in nome': o imóvel 1234 pegava o PDF do 51234 ou do 12345."""
    return re.compile(rf'(?<!\d)0*{re.escape(_numero_sem_zeros(num))}(?!\d)')


def candidatos_do_imovel(num, arquivos):
    """Caminhos dos PDFs do imóvel, melhor primeiro: nome exatamente igual ao número, depois o mais recente."""
    padrao = padrao_do_imovel(num)
    achados = [(n, c) for n, c in arquivos if padrao.search(n)]

    def chave(par):
        nome, caminho = par
        exato = re.fullmatch(r'0*' + re.escape(_numero_sem_zeros(num)), os.path.splitext(nome)[0].strip()) is not None
        try: mtime = os.path.getmtime(caminho)
        except OSError: mtime = 0
        return (0 if exato else 1, -mtime)

    return [c for _, c in sorted(achados, key=chave)]


class IndicePDFs:
    """Os PDFs de leitura, separados por mês: a linha com vencimento em novembro só enxerga a pasta de novembro."""

    def __init__(self, modo_leitura="REDE"):
        self.modo = modo_leitura
        self.meses = {}            # (ano, mes) -> {'pasta': caminho | None, 'arquivos': [...], 'em': time.time()}
        self.fixa = None           # lista de arquivos no modo de pasta única
        self.mes_base = None       # mês mais comum da fila: serve para a linha sem vencimento
        self.carregado_em = 0.0

    @property
    def arquivos(self):
        if self.fixa is not None:
            return self.fixa
        return [a for m in self.meses.values() for a in m['arquivos']]

    def carregar(self, meses=(), mes_base=None):
        self.mes_base = mes_base
        if usa_pasta_unica(self.modo):
            self.fixa = listar_pdfs(self.modo)
            print(f"📁 {len(self.fixa)} PDF(s) na pasta de leitura.", flush=True)
        else:
            for ym in sorted(set(meses)):
                self._carregar_mes(ym)
        self.carregado_em = time.time()

    def _carregar_mes(self, ym):
        pasta, arquivos = listar_pdfs_do_mes(*ym)
        self.meses[ym] = {'pasta': pasta, 'arquivos': arquivos, 'em': time.time()}
        if pasta:
            print(f"📁 {rotulo_do_mes(*ym)}: {len(arquivos)} PDF(s) em '{os.path.basename(pasta)}'.", flush=True)
        else:
            print(f"⚠️ Não existe pasta de boletos de {rotulo_do_mes(*ym)} (esperava algo como '{nome_padrao_da_pasta(*ym)}').", flush=True)
        return self.meses[ym]

    def _mes_do_boleto(self, venc):
        """Mês cuja pasta vale para esta linha: o do vencimento; sem vencimento, o da base."""
        return mes_do_vencimento(venc) or self.mes_base

    def _particao(self, ym):
        return self.meses.get(ym) or self._carregar_mes(ym)

    def pasta_do_vencimento(self, venc):
        ym = self._mes_do_boleto(venc)
        return (self.meses.get(ym) or {}).get('pasta') if ym else None

    def mes_sem_pasta(self, venc):
        """'NOV/2026' se a pasta desse mês foi procurada e não existe; senão ''."""
        ym = self._mes_do_boleto(venc)
        if self.fixa is not None or not ym:
            return ''
        m = self.meses.get(ym)
        return rotulo_do_mes(*ym) if (m is not None and m['pasta'] is None) else ''

    def _candidatos(self, num, venc):
        if self.fixa is not None:
            return candidatos_do_imovel(num, self.fixa)
        ym = self._mes_do_boleto(venc)
        if ym:
            return candidatos_do_imovel(num, self._particao(ym)['arquivos'])
        todos = []                       # nem vencimento nem base: o mês seguinte e o atual
        for padrao in meses_padrao_pelo_relogio():
            todos += self._particao(padrao)['arquivos']
        return candidatos_do_imovel(num, todos)

    def buscar(self, num, venc=''):
        achados = self._candidatos(num, venc)
        if not achados and time.time() - self.carregado_em > 60:   # pode ter chegado PDF novo desde a leitura
            if self.fixa is not None:
                self.carregar()
            else:
                ym = self._mes_do_boleto(venc)
                if ym:
                    self._carregar_mes(ym)
                self.carregado_em = time.time()
            achados = self._candidatos(num, venc)
        return achados


# =============================================================
# LEITURA DO BOLETO (texto, linha digitável, valor e vencimento gravados nela)
# =============================================================
def ler_texto_boleto(caminho_pdf):
    texto = ""
    try:
        with pdfplumber.open(caminho_pdf) as pdf:
            for pagina in pdf.pages:
                texto += (pagina.extract_text() or "") + "\n"
    except Exception:
        pass
    return texto


def extrair_linha_digitavel(texto):
    padrao = r'\d{5}[\.\s]?\d{5}[\.\s]?\d{5}[\.\s]?\d{6}[\.\s]?\d{5}[\.\s]?\d{6}[\.\s]?\d[\.\s]?\d{14}'
    res = re.search(padrao, texto.replace('\n', ' '))
    return re.sub(r'\D', '', res.group(0)) if res else ""


def valor_da_linha_digitavel(linha):
    """Os 10 últimos dígitos da linha digitável de 47 posições são o valor em centavos (0 = 'valor em aberto')."""
    if len(linha or "") != 47 or not linha.isdigit():
        return None
    centavos = int(linha[37:47])
    return Decimal(centavos) / 100 if centavos else None


def vencimento_da_linha_digitavel(linha, referencia=None):
    """O fator de vencimento (4 dígitos) conta dias desde 07/10/1997 e VOLTOU a 1000 em 22/02/2025.
    Entre as duas datas possíveis, vale a mais próxima da data de referência (a do vencimento esperado, ou hoje)."""
    if len(linha or "") != 47 or not linha.isdigit():
        return None
    fator = int(linha[33:37])
    if fator == 0:
        return None
    base = datetime.date(1997, 10, 7)
    candidatas = [base + datetime.timedelta(days=fator), base + datetime.timedelta(days=fator + 9000)]
    ref = referencia or datetime.date.today()
    return min(candidatas, key=lambda d: abs((d - ref).days))


_cache_pdf = {}


def dados_basicos_pdf(caminho):
    """Texto, linha digitável, valor, vencimento e sha1 do PDF (lidos uma vez só)."""
    try:
        st = os.stat(caminho)
        chave = (caminho, st.st_mtime, st.st_size)
    except OSError:
        chave = (caminho, 0, 0)
    if chave in _cache_pdf:
        return _cache_pdf[chave]
    try:
        with open(caminho, "rb") as f:
            sha = hashlib.sha1(f.read()).hexdigest()
    except OSError:
        sha = hashlib.sha1(caminho.encode()).hexdigest()
    texto = ler_texto_boleto(caminho)
    linha = extrair_linha_digitavel(texto)
    dados = {'texto': texto, 'linha': linha, 'valor': valor_da_linha_digitavel(linha), 'sha1': sha}
    _cache_pdf[chave] = dados
    return dados


def escolher_pdf(candidatos, venc_esperado, usados=()):
    """Entre vários PDFs do mesmo imóvel, o que serve a ESTA linha: um que outra linha do imóvel ainda não usou e,
    se houver mais de um, o cujo vencimento (gravado na linha digitável) é o mais perto do vencimento da linha."""
    if not candidatos:
        return None
    livres = [c for c in candidatos if c not in usados] or list(candidatos)
    if len(livres) == 1:
        return livres[0]
    esperado = data_do_vencimento(venc_esperado)
    if not esperado:
        return livres[0]

    def distancia(c):
        v = vencimento_da_linha_digitavel(dados_basicos_pdf(c)['linha'], esperado)
        return abs((v - esperado).days) if v else 10 ** 6

    return min(livres, key=lambda c: (distancia(c), livres.index(c)))


def comprimir_pdf_se_necessario(caminho_pdf):
    """PDFs acima de ~440KB são re-gerados menores. O arquivo novo vai para uma pasta TEMPORÁRIA daqui:
    antes ele era gravado na pasta de rede, ao lado do original, e ficava lá ('_comprimido.pdf') poluindo a busca."""
    LIMITE_BYTES = 440 * 1024
    tamanho_atual = os.path.getsize(caminho_pdf)
    if tamanho_atual <= LIMITE_BYTES:
        return caminho_pdf

    print(f"⚙️ PDF pesadão ({tamanho_atual // 1024}KB). Comprimindo para menos de 450KB...", flush=True)
    novo_caminho = os.path.join(pasta_temporaria(), f"{os.path.splitext(os.path.basename(caminho_pdf))[0]}_comprimido.pdf")
    try:
        imagens = []
        with pdfplumber.open(caminho_pdf) as pdf:
            for page in pdf.pages:
                img = page.to_image(resolution=85).original
                if img.mode in ("RGBA", "P"):
                    img = img.convert("RGB")
                imagens.append(img)
        if imagens:
            imagens[0].save(novo_caminho, "PDF", resolution=85.0, save_all=True, append_images=imagens[1:])
            novo_tamanho = os.path.getsize(novo_caminho)
            if novo_tamanho > LIMITE_BYTES:
                print(f"⚠️ O PDF reduziu para {novo_tamanho // 1024}KB, mas ainda pode esbarrar no limite.", flush=True)
            else:
                print(f"📉 PDF comprimido para {novo_tamanho // 1024}KB.", flush=True)
            return novo_caminho
    except Exception as e:
        print(f"⚠️ Não consegui comprimir o PDF: {e}", flush=True)
    return caminho_pdf


_pasta_tmp = None


def pasta_temporaria():
    global _pasta_tmp
    if _pasta_tmp is None:
        _pasta_tmp = tempfile.mkdtemp(prefix="robo_pags_")
        atexit.register(shutil.rmtree, _pasta_tmp, True)
    return _pasta_tmp


# =============================================================
# VERBAS: validação do que a IA devolveu
# =============================================================
CODIGOS_VERBA = {'231': 'COTA', '212': 'FUNDO RESERVA', '211': 'FUNDO DE OBRAS', '132': 'IPTU', '33': 'EXTRAS'}


def parse_valor_br(bruto):
    """'512,46' | '1.234,56' | 'R$ 512,46' | 512.46 | '-30,00' -> Decimal (None se não for dinheiro)."""
    if bruto is None or isinstance(bruto, bool):
        return None
    try:
        if isinstance(bruto, (int, float)):
            return Decimal(str(bruto)).quantize(Decimal('0.01'))
        t = str(bruto).upper().replace('R$', '').replace('\u00a0', '').replace(' ', '')
        neg = False
        if t.startswith('(') and t.endswith(')'):
            neg, t = True, t[1:-1]
        if t[:1] in ('-', '−'):
            neg, t = True, t[1:]
        if t[-1:] == '-':
            neg, t = True, t[:-1]
        if not t:
            return None
        pt = re.fullmatch(r'(\d{1,3}(?:\.\d{3})+|\d+),(\d{1,2})', t)          # 1.234,56 | 512,46
        us = re.fullmatch(r'(\d{1,3}(?:,\d{3})+|\d+)\.(\d{1,2})', t)           # 1,234.56 | 512.46
        if pt:
            t = pt.group(1).replace('.', '') + '.' + pt.group(2)
        elif us:
            t = us.group(1).replace(',', '') + '.' + us.group(2)
        elif re.fullmatch(r'\d{1,3}(?:\.\d{3})+|\d{1,3}(?:,\d{3})+', t):         # 1.234 | 1,234 (milhar, sem centavos)
            t = re.sub(r'[.,]', '', t)
        elif not re.fullmatch(r'\d+', t):
            return None
        dec = Decimal(t).quantize(Decimal('0.01'))
        return -dec if neg else dec
    except Exception:
        return None


def formatar_valor_portal(dec):
    """Do jeito que o portal recebe: vírgula decimal, sem separador de milhar ('1234,56')."""
    texto = f"{abs(dec):.2f}".replace('.', ',')
    return '-' + texto if dec < 0 else texto


def normalizar_verbas(brutas):
    """Confere o que a IA devolveu. Devolve (verbas_ok, problemas). Nada de 'valor' sem número ou código que o portal
    não conhece: isso virava um lançamento torto/silencioso lá dentro."""
    if isinstance(brutas, dict):
        brutas = [brutas]
    verbas, problemas = [], []
    for i, v in enumerate(brutas or [], 1):
        if not isinstance(v, dict):
            problemas.append(f"item {i} não é uma verba")
            continue
        cod = re.sub(r'\D', '', str(v.get('codigo', ''))).lstrip('0')
        if cod not in CODIGOS_VERBA:
            problemas.append(f"código inválido '{v.get('codigo')}'")
            continue
        dec = parse_valor_br(v.get('valor'))
        if dec is None:
            problemas.append(f"valor ilegível '{v.get('valor')}' (código {cod})")
            continue
        if dec == 0:
            continue
        desc = str(v.get('descricao') or '').strip() or CODIGOS_VERBA[cod]
        verbas.append({'codigo': cod, 'descricao': desc, 'valor': formatar_valor_portal(dec), '_dec': dec})
    return verbas, problemas


def conferir_soma(verbas, valor_boleto):
    """(bate?, soma). Sem valor no boleto (linha digitável ilegível ou 'valor em aberto'), não há com o que comparar."""
    soma = sum((v['_dec'] for v in verbas), Decimal('0'))
    if valor_boleto is None:
        return True, soma
    return abs(soma - valor_boleto) <= TOLERANCIA_VALOR, soma


def brl(dec):
    return f"{dec:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.')


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


class PDFIlegivel(Exception):
    pass


PROMPT_VERBAS = """
    Extraia APENAS as verbas da COTA DO CONDÔMINO. IGNORE balancetes e valores acima de 5000.
    Códigos: "231" (Cota, Água, Gás, 13º), "212" (Fundo Reserva), "211" (Fundo de Obras, RATEIO, QUASE TUDO QUE TIVER PARCELAS), "132" (IPTU), "33" (Extras).
    Regras: APENAS vírgula para decimais. Ignore traços. Sinal de menos (-) ANTES do valor APENAS se for DESCONTO.
    Retorne ESTRITAMENTE uma lista JSON. Exemplo: [{"descricao": "COTA", "valor": "512,46", "codigo": "231"}]
    """


def extrair_verbas_e_valores(caminho_pdf, id_imovel, adm, resp):
    global COTA_DIARIA_ESGOTADA
    if COTA_DIARIA_ESGOTADA: return []
    prompt = PROMPT_VERBAS
    indice_modelo = 0

    try:
        with pdfplumber.open(caminho_pdf) as pdf:
            primeira_pagina = pdf.pages[0]
            imagem = primeira_pagina.to_image(resolution=200).original
            if imagem.mode != "RGB":
                imagem = imagem.convert("RGB")
            img_byte_arr = io.BytesIO()
            imagem.save(img_byte_arr, format='JPEG')
            img_bytes = img_byte_arr.getvalue()
    except Exception as e:
        print(f"⚠️ Não consegui ler o PDF do imóvel {id_imovel}: {e}", flush=True)
        raise PDFIlegivel(str(e))

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


# 💾 Cache da IA: o mesmo PDF (mesmo conteúdo) não gasta cota de novo se o imóvel voltar na próxima rodada.
def _cache_ia_arquivo(sha1):
    return os.path.join(PASTA_CACHE_IA, f"{sha1}_{PROMPT_VERSAO}.json")


def cache_ia_ler(sha1):
    try:
        with open(_cache_ia_arquivo(sha1), encoding="utf-8") as f:
            dados = json.load(f)
        return dados if isinstance(dados, list) and dados else None
    except Exception:
        return None


def cache_ia_gravar(sha1, verbas_brutas):
    try:
        os.makedirs(PASTA_CACHE_IA, exist_ok=True)
        tmp = _cache_ia_arquivo(sha1) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(verbas_brutas, f, ensure_ascii=False)
        os.replace(tmp, _cache_ia_arquivo(sha1))
    except Exception:
        pass


def preparar_dados_ia_background(tarefa):
    """Roda em segundo plano enquanto o portal processa o imóvel anterior.
    Devolve {'estado': ...}: ok | sem_pdf | ia_vazia | invalido | valor_divergente | erro_tecnico."""
    if not tarefa['pdf']:
        return {'estado': 'sem_pasta', 'mes': tarefa['sem_pasta']} if tarefa.get('sem_pasta') else {'estado': 'sem_pdf'}
    print(f"🔍 [BACKGROUND] Lendo boleto {tarefa['num']} silenciosamente...", flush=True)
    try:
        dados = dados_basicos_pdf(tarefa['pdf'])
        linha_dig, valor_boleto = dados['linha'], dados['valor']
        chave_boleto = linha_dig or f"pdf:{dados['sha1']}"

        brutas = cache_ia_ler(dados['sha1'])
        veio_do_cache = brutas is not None
        if veio_do_cache:
            print(f"💾 Imóvel {tarefa['num']}: verbas já lidas antes (sem gastar cota da IA).", flush=True)
        else:
            try:
                brutas = extrair_verbas_e_valores(tarefa['pdf'], tarefa['num'], tarefa['adm'], tarefa['resp'])
            except PDFIlegivel:
                return {'estado': 'invalido', 'detalhe': 'PDF ilegível ou corrompido: não foi possível abrir o boleto.'}
        if not brutas:
            return {'estado': 'ia_vazia'}

        verbas, problemas = normalizar_verbas(brutas)
        if problemas or not verbas:
            motivo = "; ".join(problemas) if problemas else "nenhuma verba com valor"
            return {'estado': 'invalido', 'detalhe': f"A IA devolveu verbas que não consegui validar ({motivo})."}

        avisos = []
        bate, soma = conferir_soma(verbas, valor_boleto)
        if not bate:
            avisos.append(f"soma das verbas lidas R$ {brl(soma)} ≠ valor do boleto R$ {brl(valor_boleto)}")
        esperado = data_do_vencimento(tarefa.get('venc'))
        venc_pdf = vencimento_da_linha_digitavel(linha_dig, esperado)
        if venc_pdf and esperado and abs((venc_pdf - esperado).days) > LIMITE_DIAS_VENCIMENTO:
            avisos.append(f"o boleto vence em {venc_pdf:%d/%m/%Y}, longe do vencimento da linha ({esperado:%d/%m/%Y}): pode ser o PDF de outro mês")
        aviso = "; ".join(avisos) if CONFERIR_VALOR != 'desligado' else ""
        if aviso and CONFERIR_VALOR == 'bloquear':
            return {'estado': 'valor_divergente', 'detalhe': f"Conferir manualmente: {aviso}."}
        if not aviso and not veio_do_cache:
            cache_ia_gravar(dados['sha1'], brutas)      # só guarda a leitura que passou na conferência

        pdf_processado = comprimir_pdf_se_necessario(tarefa['pdf'])
        return {'estado': 'ok', 'pdf': pdf_processado, 'linha_dig': linha_dig, 'verbas': verbas,
                'chave_boleto': chave_boleto, 'aviso': aviso}
    except Exception as e:
        return {'estado': 'erro_tecnico', 'detalhe': f"{type(e).__name__}: {str(e)[:120]}"}


# =============================================================
# AUTOMAÇÃO WEB: GHOST MODE, HUMANIZAÇÃO E ERROS
# =============================================================
def aguardar_carregamento_lello(driver):
    try:
        wait_longo = WebDriverWait(driver, 45)
        wait_longo.until(lambda d: d.execute_script("return document.readyState === 'complete' && (typeof jQuery === 'undefined' || jQuery.active === 0);"))
        wait_longo.until(EC.invisibility_of_element_located((By.CLASS_NAME, "ui-widget-overlay")))
        wait_longo.until(EC.invisibility_of_element_located((By.CLASS_NAME, "ui-dialog-mask")))
        pausa(0.8)
    except Exception: pausa(3)


def checar_erro_na_tela(driver):
    pausa(1.5)
    try:
        modais = driver.find_elements(By.XPATH, "//div[contains(@class, 'ui-dialog') and not(contains(@style, 'display: none'))]")
        for modal in modais:
            if "Erro" in modal.text or "Aviso" in modal.text or "Antes de adicionar" in modal.text:
                texto_erro = modal.find_element(By.XPATH, ".//div[contains(@class, 'ui-dialog-content')]").text
                try:
                    botao_ok = modal.find_element(By.XPATH, ".//button[contains(translate(., 'ok', 'OK'), 'OK')]")
                    driver.execute_script("arguments[0].click();", botao_ok)
                    pausa(0.5)
                except Exception:
                    try:
                        botao_x = driver.find_element(By.XPATH, "//a[contains(@class, 'ui-dialog-titlebar-close')]")
                        driver.execute_script("arguments[0].click();", botao_x)
                        pausa(0.5)
                    except Exception: pass
                if texto_erro.strip(): return texto_erro.strip()
    except Exception: pass
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
    except Exception: return False


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
    pausa(2)
    print(f"🔐 Login concluído. URL: {driver.current_url}", flush=True)
    return True


def garantir_sessao(driver, wait):
    """Se o portal devolveu a tela de login (a sessão caiu no meio da rodada), entra de novo.
    Devolve 'ok' (nada a fazer) | 'relogou' (entrou de novo: precisa reabrir a tela) | 'falhou'."""
    try:
        if driver.find_elements(By.XPATH, "//input[@type='password']"):
            agora = time.time()
            _RELOGINS_FEITOS[:] = [t for t in _RELOGINS_FEITOS if agora - t < JANELA_RELOGINS]
            if len(_RELOGINS_FEITOS) >= MAX_RELOGINS:
                print(f"🔐 A sessão do portal caiu de novo ({len(_RELOGINS_FEITOS)} logins refeitos em {JANELA_RELOGINS // 60} min). "
                      f"Provável outro robô usando o mesmo login ao mesmo tempo — parando para não ficar um derrubando o outro.", flush=True)
                return 'falhou'
            _RELOGINS_FEITOS.append(agora)
            print(f"🔐 A sessão do portal expirou. Entrando de novo... ({len(_RELOGINS_FEITOS)}/{MAX_RELOGINS})", flush=True)
            return 'relogou' if realizar_login(driver, wait) else 'falhou'
    except Exception:
        pass
    return 'ok'


def preencher_rateio(driver, wait, verbas):
    if not verbas or len(verbas) == 0:
        return False, "A IA não extraiu nenhuma verba válida do PDF!"
    for v in verbas:
        print(f" [Lançamento] Verba: {v['codigo']} | R$ {v['valor']} | Desc: {v['descricao']}", flush=True)
        aguardar_carregamento_lello(driver)
        ghost_input(driver, wait, "//input[@placeholder='[Descrição da Verba do Boleto]']", v['descricao'])
        pausa(0.5)
        try:
            campo_valor = wait.until(EC.element_to_be_clickable((By.XPATH, "(//input[@placeholder='[Valor]'])[last()]")))
            driver.execute_script("arguments[0].click(); arguments[0].focus();", campo_valor)
            pausa(0.3)
            driver.execute_script("arguments[0].value = '';", campo_valor)
            campo_valor.send_keys(Keys.END)
            for _ in range(15): campo_valor.send_keys(Keys.BACKSPACE)
            pausa(0.3)
            campo_valor.send_keys(v['valor'])        # já vem normalizado: '1234,56' (sem ponto de milhar)
            pausa(0.3)
            campo_valor.send_keys(Keys.TAB)
            pausa(0.5)
        except Exception as e: print(f" Erro ao digitar o valor: {e}", flush=True)
        try:
            xpath_setinha = "//label[text()='[Verba]']/following-sibling::div[contains(@class, 'ui-selectonemenu-trigger')]"
            driver.execute_script("arguments[0].click();", wait.until(EC.element_to_be_clickable((By.XPATH, xpath_setinha))))
            pausa(1.5)
            driver.execute_script("arguments[0].click();", wait.until(EC.presence_of_element_located((By.XPATH, f"//div[contains(@class, 'ui-selectonemenu-panel') and contains(@style, 'display: block')]//li[normalize-space(text())='{v['codigo']}']"))))
            aguardar_carregamento_lello(driver)
        except Exception: pass
        try:
            driver.execute_script("arguments[0].click();", wait.until(EC.presence_of_element_located((By.XPATH, "//button[contains(., 'ADICIONAR') or contains(., 'Adicionar')]"))))
            aguardar_carregamento_lello(driver)
            msg_erro = checar_erro_na_tela(driver)
            if msg_erro: return False, msg_erro
        except Exception: pass
    try:
        aguardar_carregamento_lello(driver)
        driver.execute_script("arguments[0].click();", wait.until(EC.presence_of_element_located((By.XPATH, "//button[contains(., 'Gravar')] | //span[text()='Gravar']/parent::button"))))
        aguardar_carregamento_lello(driver)
        msg_erro = checar_erro_na_tela(driver)
        if msg_erro: return False, msg_erro
        print(" PROCESSO GRAVADO NO SITE!", flush=True)
        return True, ""
    except Exception: return False, "Falha ao clicar no Gravar."


# =============================================================
# FICHA SEM VENCIMENTO: acerta o "Dia Venc. Cond." da administradora e tenta de novo
# =============================================================
XPATH_LINK_ADM = "//td[contains(@class,'ui-panelgrid-cell')][starts-with(normalize-space(.), 'Ad.Cond')]//a[contains(@class,'ui-commandlink')]"
XPATH_DATA_PREVISTA = "//span[@id='formInsere:dtPrevPagto']"
ID_DIA_VENC_COND = "formCadastro:inputDiaVencimento"
XPATH_GRAVAR = "//button[contains(., 'Gravar')] | //span[text()='Gravar']/parent::button"
XPATH_SETA_VOLTAR = "//i[contains(@class,'fa-arrow-left')]"


def erro_de_vencimento_na_ficha(msg):
    """A mensagem do portal diz que o vencimento não está definido/informado na ficha? (Só nesse caso mexemos no cadastro.)"""
    t = sem_acento(msg)
    if 'vencimento' not in t and 'vencto' not in t:
        return False
    return bool(PADRAO_VENCIMENTO_FICHA.search(t))


def decidir_dia_da_ficha(data_portal, data_boleto):
    """Qual dia gravar em "Dia Venc. Cond."? Devolve (dia, '') ou (None, motivo).
    As duas leituras do vencimento (a 'Dt. Prev. Pagto' que o portal mostra e a data gravada na linha digitável) precisam
    concordar: se divergirem não mexemos no cadastro — errar o dia numa ficha é pior do que deixar o boleto para a equipe."""
    datas = [d for d in (data_portal, data_boleto) if d]
    if not datas:
        return None, "não consegui ler a data de vencimento do boleto"
    if data_portal and data_boleto and data_portal.day != data_boleto.day:
        return None, (f"a data que o portal mostra ({data_portal:%d/%m/%Y}) e a do boleto ({data_boleto:%d/%m/%Y}) "
                      f"não têm o mesmo dia; não mexi na ficha")
    return datas[0].day, ""


def _numero_do_campo(campo):
    """Número que está no campo (ignora o '_' da máscara). None se vazio."""
    bruto = re.sub(r'[_\s]', '', str(campo.get_attribute('value') or ''))
    return int(bruto) if bruto.isdigit() else None


def _parece_confirmacao(msg):
    t = sem_acento(msg)
    return any(p in t for p in ('sucesso', 'gravad', 'salvo', 'atualizad', 'alterad', 'conclu'))


def _ler_data_prevista(driver):
    try:
        for el in driver.find_elements(By.XPATH, XPATH_DATA_PREVISTA):
            data = data_do_vencimento(el.get_attribute('textContent') or el.text)
            if data:
                return data
    except Exception:
        pass
    return None


def _esperar_campo_dia(driver, janelas_antes):
    """Espera a 2ª tela (Cad. Administradora de Condomínios) abrir — na mesma janela ou numa nova."""
    limite = time.time() + ESPERA_FICHA_SEGUNDOS
    while time.time() < limite:
        try:
            novas = [h for h in driver.window_handles if h not in janelas_antes]
            if novas:
                driver.switch_to.window(novas[-1])
            achados = driver.find_elements(By.ID, ID_DIA_VENC_COND)
            if achados:
                return achados[0]
        except Exception:
            pass
        pausa(0.5)
    return None


def _digitar_dia(driver, campo, dia):
    texto = str(dia)
    driver.execute_script("arguments[0].click(); arguments[0].focus();", campo)
    pausa(0.3)
    campo.send_keys(Keys.CONTROL + "a")
    campo.send_keys(Keys.BACKSPACE)
    pausa(0.3)
    for digito in texto:
        campo.send_keys(digito)
        pausa(0.1)
    campo.send_keys(Keys.TAB)
    pausa(0.5)
    if _numero_do_campo(campo) == dia:
        return True
    # a máscara do campo pode ter engolido a digitação: força o valor e avisa a página
    driver.execute_script("""
        arguments[0].focus(); arguments[0].value = arguments[1];
        arguments[0].dispatchEvent(new Event('input', { bubbles: true })); arguments[0].dispatchEvent(new Event('change', { bubbles: true })); arguments[0].dispatchEvent(new Event('blur', { bubbles: true }));
        if (window.jQuery) { window.jQuery(arguments[0]).trigger('change'); }
    """, campo, texto)
    pausa(0.5)
    return _numero_do_campo(campo) == dia


def _voltar_da_ficha(driver, janela_original, janelas_antes):
    """Fecha a janela nova (se a ficha abriu em outra) ou clica na seta de voltar da própria tela."""
    try:
        novas = [h for h in driver.window_handles if h not in janelas_antes]
        if novas:
            for h in novas:
                driver.switch_to.window(h)
                driver.close()
            driver.switch_to.window(janela_original)
            return
        for seta in driver.find_elements(By.XPATH, XPATH_SETA_VOLTAR):
            if seta.is_displayed():
                alvo = seta
                try:
                    alvo = seta.find_element(By.XPATH, "./ancestor::*[self::a or self::button][1]")
                except Exception:
                    pass
                driver.execute_script("arguments[0].click();", alvo)
                aguardar_carregamento_lello(driver)
                return
    except Exception as e:
        print("🔎 [FICHA] Não consegui clicar em voltar; a próxima etapa reabre a tela de movimento.", flush=True)


def corrigir_dia_vencimento_da_ficha(driver, linha_dig):
    """Estamos na tela de movimento, com a ficha do imóvel aberta. Acerta o 'Dia Venc. Cond.' da administradora para o dia do
    vencimento do boleto. Devolve (corrigiu, detalhe)."""
    data_portal = _ler_data_prevista(driver)
    data_boleto = vencimento_da_linha_digitavel(linha_dig)
    dia, motivo = decidir_dia_da_ficha(data_portal, data_boleto)
    if not dia:
        return False, motivo
    try:
        links = driver.find_elements(By.XPATH, XPATH_LINK_ADM)
    except Exception:
        links = []
    if not links:
        return False, "não achei o link da administradora (Ad.Cond) na ficha"
    cod_adm = (links[0].get_attribute('textContent') or links[0].text or '').strip() or '?'
    data_txt = f"{(data_portal or data_boleto):%d/%m/%Y}"
    print(f"🛠️ [FICHA] O portal não aceitou o vencimento da ficha. Abrindo a administradora {cod_adm} para acertar o "
          f"Dia Venc. Cond. para {dia} (o boleto vence em {data_txt}).", flush=True)

    janela_original = driver.current_window_handle
    janelas_antes = list(driver.window_handles)
    try:
        driver.execute_script("arguments[0].click();", links[0])
        aguardar_carregamento_lello(driver)
        campo = _esperar_campo_dia(driver, janelas_antes)
        if campo is None:
            diagnosticar(driver, f"ficha_adm_{cod_adm}_nao_abriu")
            return False, f"a tela da administradora {cod_adm} não abriu"
        atual = _numero_do_campo(campo)
        if atual == dia:
            return False, f"a administradora {cod_adm} já está com Dia Venc. Cond. {dia}; o motivo da recusa é outro"
        if not _digitar_dia(driver, campo, dia):
            diagnosticar(driver, f"ficha_adm_{cod_adm}_digitar")
            return False, f"não consegui digitar o dia {dia} no campo Dia Venc. Cond. da administradora {cod_adm}"
        driver.execute_script("arguments[0].click();", WebDriverWait(driver, 15).until(EC.presence_of_element_located((By.XPATH, XPATH_GRAVAR))))
        aguardar_carregamento_lello(driver)
        msg = checar_erro_na_tela(driver)
        if msg and not _parece_confirmacao(msg):
            diagnosticar(driver, f"ficha_adm_{cod_adm}_gravar")
            return False, f"o portal não aceitou gravar a administradora {cod_adm}: {msg}"
        detalhe = f"Dia Venc. Cond. da administradora {cod_adm}: {atual if atual is not None else 'vazio'} → {dia}"
        print(f"🛠️ [FICHA] {detalhe} (gravado). Voltando para refazer o lançamento...", flush=True)
        return True, detalhe
    except Exception as e:
        diagnosticar(driver, f"ficha_adm_{cod_adm}_travou")
        return False, f"travou ao acertar a administradora {cod_adm} ({type(e).__name__})"
    finally:
        _voltar_da_ficha(driver, janela_original, janelas_antes)


def interagir_portal(driver, wait, num_imovel, pdf_path, linha_dig, verbas):
    """Lança o boleto no portal. Se o portal recusar por vencimento não definido na ficha, acerta a ficha e tenta UMA vez mais;
    se a recusa continuar, devolve o motivo (a linha vai para a equipe e o robô segue para o próximo imóvel)."""
    sucesso, msg = _lancar_no_portal(driver, wait, num_imovel, pdf_path, linha_dig, verbas)
    if sucesso or not CORRIGIR_VENCIMENTO_FICHA or not erro_de_vencimento_na_ficha(msg):
        return sucesso, msg

    corrigiu, detalhe = corrigir_dia_vencimento_da_ficha(driver, linha_dig)
    if not corrigiu:
        print(f"🛠️ [FICHA] Imóvel {num_imovel}: a ficha não foi acertada (o motivo vai junto com a devolução da linha).", flush=True)
        return False, f"{msg} (tentei acertar a ficha, mas {detalhe})"

    print(f"🔁 [FICHA] Refazendo o lançamento do imóvel {num_imovel} com a ficha acertada.", flush=True)
    sucesso, msg2 = _lancar_no_portal(driver, wait, num_imovel, pdf_path, linha_dig, verbas)
    if sucesso:
        return True, ""
    if msg2.startswith(PREFIXO_ERRO_TECNICO):
        return False, msg2          # travou no portal, não foi recusa: a linha continua COLETADO e a ficha já está certa
    return False, f"{msg2} (já acertei a ficha — {detalhe} — e o portal continuou recusando)"


def _lancar_no_portal(driver, wait, num_imovel, pdf_path, linha_dig, verbas):
    """Um lançamento completo no portal (consulta o imóvel, anexa o PDF, preenche o rateio e grava)."""
    etapa = "início"
    for tentativa_mestra in range(1, 4):
        try:
            etapa = "abrindo tela de movimento de boleto"
            driver.get(URL_MOVIMENTO)
            pausa(3)
            sessao = garantir_sessao(driver, wait)
            if sessao == 'falhou':
                return False, f"{PREFIXO_ERRO_TECNICO} (etapa: login | a sessão não pôde ser refeita)"
            if sessao == 'relogou':
                driver.get(URL_MOVIMENTO)       # entrou de novo: volta para a tela certa
                pausa(3)
            etapa = "selecionando filtros Imóvel/Código"
            for i, txt in [(2, 'móvel'), (3, 'digo')]:
                driver.execute_script("arguments[0].click();", wait.until(EC.presence_of_element_located((By.XPATH, f"(//div[contains(@class, 'trigger')])[ {i}]"))))
                driver.execute_script("arguments[0].click();", wait.until(EC.presence_of_element_located((By.XPATH, f"//li[contains(text(), '{txt}')]"))))
                pausa(1)
            etapa = "digitando número do imóvel"
            campo_imovel = wait.until(EC.presence_of_element_located((By.XPATH, "//input[contains(@class, 'ui-inputfield') and not(@type='hidden')]")))
            driver.execute_script("arguments[0].click(); arguments[0].focus();", campo_imovel)
            pausa(0.3)
            campo_imovel.send_keys(Keys.CONTROL + "a")
            campo_imovel.send_keys(Keys.BACKSPACE)
            pausa(0.3)
            for digito in str(num_imovel):
                campo_imovel.send_keys(digito)
                pausa(0.1)
            pausa(0.3)
            campo_imovel.send_keys(Keys.TAB)
            pausa(0.5)
            etapa = "clicando em Consultar"
            driver.execute_script("arguments[0].click();", wait.until(EC.element_to_be_clickable((By.XPATH, "//button[contains(., 'Consultar')]"))))
            pausa(4)

            try:
                texto_tela = driver.find_element(By.TAG_NAME, "body").text
                if not padrao_do_imovel(num_imovel).search(texto_tela):
                    print(f"🚨 TRAVA DE SEGURANÇA ATIVADA! O portal carregou a ficha errada. (Esperava: {num_imovel})", flush=True)
                    return False, "Segurança: O portal carregou o imóvel errado."
            except Exception: pass

            etapa = "anexando o PDF"
            wait.until(EC.presence_of_element_located((By.XPATH, "//input[@type='file']"))).send_keys(os.path.abspath(pdf_path))
            msg_erro = checar_erro_na_tela(driver)
            if msg_erro:
                if "unidade não foi encontrada" in msg_erro.lower() and tentativa_mestra < 3:
                    print(f"⚙️ [RETRY] Portal engasgou com a Unidade. Recarregando a página... ({tentativa_mestra}/3)", flush=True)
                    continue
                return False, msg_erro

            print("⚙️ PAUSA 5s: Aguardando o grid carregar...", flush=True)
            pausa(5)
            etapa = "abrindo o lápis Editar do grid"
            link_editar = wait.until(EC.presence_of_element_located((By.XPATH, "//a[@title='Editar' and contains(@class, 'ui-commandlink')]")))
            driver.execute_script("arguments[0].click();", link_editar)
            pausa(6)

            etapa = "preenchendo linha digitável / rateio"
            msg = "Falha no processo."
            for tentativa in range(1, 4):
                ghost_input(driver, wait, "formInsere:inputLinhaDigitavel", "", is_id=True)
                pausa(1)
                ghost_input(driver, wait, "formInsere:inputLinhaDigitavel", linha_dig, is_id=True)
                print(f"⚙️ PAUSA 5s: Inserindo código de barras... (Tentativa {tentativa}/3)", flush=True)
                pausa(5)
                msg_pre_rateio = checar_erro_na_tela(driver)
                if msg_pre_rateio:
                    if "unidade não foi encontrada" in msg_pre_rateio.lower():
                        pausa(2)
                        continue
                    else: return False, msg_pre_rateio

                sucesso, msg = preencher_rateio(driver, wait, verbas)
                if sucesso: return True, ""
                if "antes de adicionar" in msg.lower() or "dados da verba" in msg.lower() or "unidade não foi encontrada" in msg.lower():
                    if tentativa == 3: break
                    pausa(2); continue
                else: break
            return False, msg
        except Exception as e:
            detalhe = (str(e).strip().splitlines() or [""])[0][:120]
            print(f"🔎 [PORTAL] Tentativa {tentativa_mestra}/3 travou na etapa '{etapa}': {type(e).__name__} {detalhe}", flush=True)
            diagnosticar(driver, f"imovel_{num_imovel}_{etapa}")
            if tentativa_mestra == 3: return False, f"{PREFIXO_ERRO_TECNICO} (etapa: {etapa} | {type(e).__name__})"
    return False, f"{PREFIXO_ERRO_TECNICO}: tentativas esgotadas"


# =============================================================
# MONTAGEM DA FILA
# =============================================================
def montar_tarefas(fila_bruta, filtro_resp, filtro_emp, filtro_dias, indice):
    """Filtra a fila do painel e escolhe o PDF de cada LINHA (cada linha é um boleto; o mesmo imóvel pode ter vários)."""
    tarefas, ids_vistos, usados = [], set(), {}
    for d in fila_bruta:
        id_linha = d.get('id')
        if id_linha is not None:
            if id_linha in ids_vistos:
                continue
            ids_vistos.add(id_linha)
        num = str(d.get('imovel', '')).strip()
        resp = sem_acento(d.get('responsavel', d.get('sacado', '')))
        adm = str(d.get('administradora', d.get('empreendimento', 'N/D'))).strip()
        emp = sem_acento(d.get('empreendimento', ''))
        venc = str(d.get('vencimento', '')).strip()

        if filtro_resp and filtro_resp not in resp: continue
        if filtro_emp and filtro_emp not in emp: continue
        if filtro_dias and venc and dia_do_vencimento(venc) not in filtro_dias: continue
        if not num: continue

        ja_usados = usados.setdefault(_numero_sem_zeros(num), set())
        pdf = escolher_pdf(indice.buscar(num, venc), venc, ja_usados)
        if pdf:
            ja_usados.add(pdf)
        sem_pasta = '' if pdf or not hasattr(indice, 'mes_sem_pasta') else indice.mes_sem_pasta(venc)
        if pdf:
            pasta = os.path.basename(os.path.dirname(pdf))
        else:
            pasta = os.path.basename(indice.pasta_do_vencimento(venc) or '') if hasattr(indice, 'pasta_do_vencimento') else ''
        tarefas.append({'id': id_linha, 'num': num, 'adm': adm, 'resp': resp, 'venc': venc, 'pdf': pdf,
                        'sem_pasta': sem_pasta, 'pasta': pasta})
    return tarefas


# =============================================================
# UM BOLETO: decide o que fazer com o resultado da leitura
# =============================================================
def _devolver(tarefa, motivo):
    """Devolve a linha para a equipe (ERRO PAGAMENTO no painel). Imprime UMA linha 🚨 (o painel conta 1 falha)."""
    rotulo = f" (venc. {tarefa['venc']})" if tarefa.get('venc') else ""
    print(f"🚨 FALHA no imóvel {tarefa['num']}{rotulo}: {motivo}", flush=True)
    informar_resultado(tarefa, False, motivo)
    return 'devolvido'


_MESES_SEM_PASTA_AVISADOS = set()     # um aviso por mês (por execução)


def tratar_tarefa(driver, wait, tarefa, prep):
    """Devolve 'lancado' | 'ja_lancado' | 'devolvido' | 'mantido' | 'tecnico'."""
    num = tarefa['num']
    rotulo = f" (venc. {tarefa['venc']})" if tarefa.get('venc') else ""
    estado = (prep or {}).get('estado', 'erro_tecnico')

    if estado == 'sem_pasta':
        mes = (prep or {}).get('mes') or '?'
        if mes not in _MESES_SEM_PASTA_AVISADOS:
            _MESES_SEM_PASTA_AVISADOS.add(mes)
            msg = (f"⚠️ Não existe pasta de boletos de {mes} na rede. Os imóveis com vencimento nesse mês continuam COLETADO "
                   f"e serão lançados quando a pasta existir.")
            print(msg, flush=True)
            enviar_notificacao_chat("", "", "", "FILA", msg)
        print(f"⏭️ Imóvel {num}{rotulo}: sem pasta de boletos de {mes}. Continua COLETADO.", flush=True)
        return 'mantido'
    if estado == 'sem_pdf':
        onde = f"na pasta '{tarefa['pasta']}'" if tarefa.get('pasta') else "na pasta de leitura"
        return _devolver(tarefa, f"PDF ausente: não achei o boleto deste imóvel {onde}.")
    if estado == 'ia_vazia':
        print(f"⚠️ IA sem resposta para o imóvel {num} (Google instável ou cota). Pulado SEM alterar status: continua COLETADO para a próxima rodada.", flush=True)
        return 'mantido'
    if estado == 'erro_tecnico':
        print(f"⚠️ Imóvel {num}: não deu para preparar o boleto ({(prep or {}).get('detalhe', 'sem detalhe')}). Continua COLETADO para a próxima rodada.", flush=True)
        return 'mantido'
    if estado in ('invalido', 'valor_divergente'):
        return _devolver(tarefa, prep['detalhe'])

    chave_boleto, verbas = prep['chave_boleto'], prep['verbas']
    if prep.get('aviso'):
        print(f"⚠️ [CONFERÊNCIA] Imóvel {num}{rotulo}: {prep['aviso']}.", flush=True)

    # 🛡️ Este boleto já foi lançado antes? (mesma linha digitável)
    anterior = boleto_ja_lancado(chave_boleto)
    if anterior:
        mesma_linha = (tarefa.get('id') is not None and anterior.get('id') == tarefa.get('id')) or \
                      (tarefa.get('id') is None and anterior.get('num') == num and anterior.get('venc') == tarefa.get('venc', ''))
        if mesma_linha:
            informar_resultado(tarefa, True, chave_boleto=chave_boleto)
            print(f"🎯 SUCESSO: imóvel {num}{rotulo} já estava lançado no portal; o painel foi atualizado.", flush=True)
            return 'ja_lancado'
        return _devolver(tarefa, f"Este PDF já foi lançado na linha de vencimento {anterior.get('venc') or 'anterior'} deste imóvel. "
                                 f"Falta o PDF do outro boleto: confira a pasta e recoloque a linha como COLETADO quando ele chegar.")

    verba_negativa = next((v for v in verbas if v['_dec'] < 0), None)
    if verba_negativa:
        return _devolver(tarefa, f"Possui desconto/verba negativa ({verba_negativa.get('descricao', 'N/D')}: R$ {verba_negativa['valor']})")

    if not reservar_linha(tarefa):
        print(f"⏭️ Imóvel {num}{rotulo} está sendo lançado por outro robô agora. Pulando.", flush=True)
        return 'mantido'
    try:
        sucesso, erro_msg = interagir_portal(driver, wait, num, prep['pdf'], prep['linha_dig'], verbas)
        if sucesso:
            informar_resultado(tarefa, True, chave_boleto=chave_boleto)
            print(f"🎯 SUCESSO: imóvel {num}{rotulo} lançado no portal e baixado no painel.", flush=True)
            return 'lancado'
        if erro_msg.startswith(PREFIXO_ERRO_TECNICO):
            print(f"⚠️ Falha técnica (não é rejeição do portal): imóvel {num}{rotulo} continua COLETADO para nova tentativa.", flush=True)
            return 'tecnico'
        return _devolver(tarefa, erro_msg)
    finally:
        liberar_linha(tarefa)


# =============================================================
# MOTOR PRINCIPAL (VIA API/ORQUESTRADOR)
# =============================================================
def abrir_chrome():
    chrome_options = webdriver.ChromeOptions()
    if not CHROME_VISIVEL:
        chrome_options.add_argument("--headless=new")
        chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("--start-maximized")
    chrome_options.add_experimental_option("detach", True)   # Chrome fica aberto no fim para você conferir
    try:
        return webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=chrome_options)
    except Exception:
        print("⚠️ Não consegui baixar o ChromeDriver. Tentando o gerenciador embutido do Selenium...", flush=True)
        return webdriver.Chrome(options=chrome_options)


def verificar_configuracao():
    faltando = []
    if not TOKEN: faltando.append("TOKEN_SEGURANCA")
    if not CHAVES_API: faltando.append("GEMINI_API_KEY_1")
    if not USUARIO_LELLO: faltando.append("LELLO_USUARIO")
    if not SENHA_LELLO: faltando.append("LELLO_SENHA")
    return faltando


def processar(filtro_resp="", filtro_emp="", filtro_dias=None):
    filtro_dias = filtro_dias or set()
    resumo = {'lancado': 0, 'ja_lancado': 0, 'devolvido': 0, 'mantido': 0, 'tecnico': 0}
    try:
        tempo_inicio = time.time()
        podar_estado()
        reenviar_avisos_pendentes()
        fila_bruta = buscar_boletos_coletados()
        if not fila_bruta:
            print(" NENHUMA TAREFA PENDENTE RETORNADA PELO SISTEMA.", flush=True)
            return resumo

        _MESES_SEM_PASTA_AVISADOS.clear()
        indice = IndicePDFs(MODO_LEITURA)
        meses, mes_base = meses_da_fila(fila_bruta)
        indice.carregar(meses, mes_base)
        tarefas_pendentes = montar_tarefas(fila_bruta, filtro_resp, filtro_emp, filtro_dias, indice)

        if not tarefas_pendentes:
            print(f"⚠️ Nenhum imóvel passou pelos filtros.", flush=True)
            return resumo

        print(f"🚀 INICIANDO ORQUESTRAÇÃO DE PAGAMENTO ({len(tarefas_pendentes)} imóveis)", flush=True)

        driver = abrir_chrome()
        wait = WebDriverWait(driver, 15)

        if not realizar_login(driver, wait):
            print("🚨 Login no portal não concluiu (credencial errada, sessão duplicada ou portal fora). Nenhum imóvel foi alterado no sistema.", flush=True)
            return resumo

        falhas_seguidas = 0
        avisou_cota = False
        with ThreadPoolExecutor(max_workers=1) as executor:
            futuro_atual = executor.submit(preparar_dados_ia_background, tarefas_pendentes[0])
            for index, tarefa in enumerate(tarefas_pendentes):
                futuro_proximo = None
                if index + 1 < len(tarefas_pendentes):
                    futuro_proximo = executor.submit(preparar_dados_ia_background, tarefas_pendentes[index + 1])

                prep = futuro_atual.result()
                futuro_atual = futuro_proximo

                # Cota diária da IA acabou: o que já tem leitura guardada (cache) segue; o resto fica COLETADO sem barulho.
                if COTA_DIARIA_ESGOTADA and (prep or {}).get('estado') == 'ia_vazia':
                    if not avisou_cota:
                        print("🛑 Cota diária do Gemini esgotada: os imóveis sem leitura guardada continuam COLETADO para a próxima rodada.", flush=True)
                        avisou_cota = True
                    resumo['mantido'] += 1
                    continue

                print(f"\n🚀 A PROCESSAR IMÓVEL: {tarefa['num']}" + (f" (vencimento {tarefa['venc']})" if tarefa.get('venc') else ""), flush=True)
                resultado = tratar_tarefa(driver, wait, tarefa, prep)
                resumo[resultado] += 1

                if resultado == 'tecnico':
                    falhas_seguidas += 1
                    if falhas_seguidas >= LIMITE_FALHAS_SEGUIDAS:
                        restantes = len(tarefas_pendentes) - index - 1
                        msg = (f"🚨 PORTAL INSTÁVEL: {falhas_seguidas} imóveis seguidos travaram. Rodada interrompida; "
                               f"{restantes} imóvel(is) não foram tentados e continuam COLETADO.")
                        print(msg, flush=True)
                        enviar_notificacao_chat("", "", "", "FILA", msg)
                        break
                elif resultado in ('lancado', 'ja_lancado', 'devolvido'):
                    falhas_seguidas = 0

        minutos = int(time.time() - tempo_inicio) // 60
        segundos = int(time.time() - tempo_inicio) % 60
        print(f"📊 RESUMO: {resumo['lancado']} lançado(s) · {resumo['ja_lancado']} já lançado(s) antes · "
              f"{resumo['devolvido']} devolvido(s) à equipe · {resumo['mantido'] + resumo['tecnico']} mantido(s) como COLETADO.", flush=True)
        print(f" EXECUÇÃO FINALIZADA. TEMPO: {minutos}m {segundos}s", flush=True)

    except Exception as e:
        print(f" Erro Crítico Geral: {e}", flush=True)
    return resumo


def main():
    filtro_bruto = sys.argv[1] if len(sys.argv) > 1 else ""
    filtro_resp, filtro_emp, filtro_dias = interpretar_filtro(filtro_bruto)
    print(f"⚙️ [INÍCIO] Motor de Pags -> Resp: '{filtro_resp}' | Emp: '{filtro_emp}' | Dias: {sorted(filtro_dias)}", flush=True)
    faltando = verificar_configuracao()
    if faltando:
        print("🚨 Faltam no arquivo .env: " + ", ".join(faltando) + ". Abortando.", flush=True)
        sys.exit(1)
    print(f"🔑 {len(CHAVES_API)} chave(s) Gemini carregada(s).", flush=True)
    aplicar_patch_httpx()
    processar(filtro_resp, filtro_emp, filtro_dias)


if __name__ == "__main__":
    main()