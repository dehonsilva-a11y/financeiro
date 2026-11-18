from dotenv import load_dotenv
load_dotenv()
from flask import Flask, render_template, request, redirect, url_for, jsonify, session, send_file, make_response
from flask import send_file, request, jsonify
from werkzeug.middleware.proxy_fix import ProxyFix
import sqlite3
import difflib
import re
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import make_msgid, parseaddr
from datetime import datetime, timedelta
import functools
import threading
import time
import os
import gzip
import shutil
import sys
import random
import unicodedata
import socket
import secrets
import hashlib
from werkzeug.security import generate_password_hash, check_password_hash
import fitz
import subprocess
import uuid
from werkzeug.utils import secure_filename
import pandas as pd
import requests
from io import BytesIO
import time
from werkzeug.utils import secure_filename
from apscheduler.schedulers.background import BackgroundScheduler
import atexit
import io
import zipfile
from PyPDF2 import PdfReader, PdfWriter
import tempfile
import tempfile
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
import json
import glob
from html import escape as _esc_html


# Virada de mês (arquivar / meta por mês / backup) — arquivo virada_mes.py.
# Funciona com o arquivo na mesma pasta do app.py (src/) ou na raiz do projeto (ao lado do run.py).
try:
    from . import virada_mes
except ImportError:
    import virada_mes

app = Flask(__name__)
app.secret_key = os.getenv("APP_SECRET_KEY")
if not app.secret_key:
    raise RuntimeError("Falta a variável APP_SECRET_KEY (no arquivo .env). Sem ela o login não funciona: defina um texto longo e aleatório e NÃO troque depois, senão todo mundo cai da sessão.")

# Sessão: vale 12 horas e o cookie não é acessível por JavaScript.
# COOKIE_SEGURO=1 no .env quando o sistema estiver em https (aí o cookie só trafega criptografado).
app.config.update(
    PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    SESSION_COOKIE_SECURE=os.getenv('COOKIE_SEGURO', '0') == '1',
)

app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

# 🎯 BLINDAGEM DE CACHE: Obriga o navegador/Cloudflare a SEMPRE ler o dado fresco do banco!
@app.after_request
def add_header(response):
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, post-check=0, pre-check=0, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '-1'
    return response

# --- CONFIGURAÇÕES GLOBAIS ---
EMAIL_REMETENTE = "ctrlcond@lello.com.br"

# --- CONFIGURAÇÕES GLOBAIS ---
EMAIL_REMETENTE = "ctrlcond@lello.com.br"
SENHA_APP_GMAIL = os.getenv("SENHA_APP_GMAIL")
TOKEN_API_ROBO = os.getenv("TOKEN_SEGURANCA")
ARQUIVO_TRAVA_LIAX = os.path.join(tempfile.gettempdir(), "trava_liax.flag")

ICONES_ROBOS = {
    "AUTOMAÇÃO WEBWARE": "https://cdn-icons-png.flaticon.com/512/4712/4712139.png", 
    "AUTOMAÇÃO WINKER": "https://cdn-icons-png.flaticon.com/512/4712/4712139.png", 
    "AUTOMAÇÃO PAGAMENTOS": "https://cdn-icons-png.flaticon.com/512/2830/2830284.png",
    "AUTOMAÇÃO SCANNER FÍSICO": "https://cdn-icons-png.flaticon.com/512/2282/2282216.png", 
    "AUTOMAÇÃO DA LIAX": "https://cdn-icons-png.flaticon.com/512/4712/4712139.png",
    "AUTOMAÇÃO FINMAIL": "https://cdn-icons-png.flaticon.com/512/2082/2082810.png"
}

PROCESSOS_ATIVOS = {}  
STATUS_ROBOS = {
    "WEBWARE": {"rodando": False, "sucessos": 0, "falhas": 0, "logs": []},
    "WINKER": {"rodando": False, "sucessos": 0, "falhas": 0, "logs": []},
    "PAGAMENTOS": {"rodando": False, "sucessos": 0, "falhas": 0, "logs": []},
    "SCANNER": {"rodando": False, "sucessos": 0, "falhas": 0, "logs": []},
    "LIAX": {"rodando": False, "sucessos": 0, "falhas": 0, "logs": []},
    "SUPERLOGICA": {"rodando": False, "sucessos": 0, "falhas": 0, "logs": []},
    "BASE_LELLO": {"rodando": False, "sucessos": 0, "falhas": 0, "logs": []},
    "CONVERSOR_RETORNO": {"rodando": False, "sucessos": 0, "falhas": 0, "logs": [], "total_alvo": 0},
    "FINMAIL": {
        "rodando": False, 
        "sucessos": 0,
        "falhas": 0,
        "erro_senha": 0,
        "comunicados": 0,
        "atas": 0,
        "logs": []
    }
}

SCHEDULER_DISPAROS_THREAD_STARTED = False
SCHEDULER_DISPAROS_ULTIMA_EXECUCAO = None
FINMAIL_PAUSA_ATE = None

def log_robo_console(robo_id, msg, tipo="info"):
    timestamp = datetime.now().strftime("%H:%M:%S")
    STATUS_ROBOS[robo_id]["logs"].insert(0, {"hora": timestamp, "msg": msg, "tipo": tipo})
    if len(STATUS_ROBOS[robo_id]["logs"]) > 60: 
        STATUS_ROBOS[robo_id]["logs"].pop()

# =================================================================
# BASE DE DADOS E INFRAESTRUTURA (ALTA PERFORMANCE)
# =================================================================
def conectar_banco():
    """Abre a conexão com caminho absoluto garantindo estabilidade máxima em produção e redes"""
    import os
    base_dir = os.path.dirname(os.path.abspath(__file__))
    root_dir = os.path.dirname(base_dir)
    db_path = os.path.join(root_dir, 'banco.db')
    
    conexao = sqlite3.connect(db_path, timeout=30.0) 
    conexao.row_factory = sqlite3.Row
    _registrar_funcoes_sql(conexao)      # chave_adm() / vinculo_de(): administradora sem acento nem maiúscula
    return conexao

def inicializar_banco_producao():
    """Roda apenas 1x quando o servidor liga para montar a estrutura"""
    import os
    base_dir = os.path.dirname(os.path.abspath(__file__))
    root_dir = os.path.dirname(base_dir)
    db_path = os.path.join(root_dir, 'banco.db')
    conexao = sqlite3.connect(db_path)
    
    conexao.execute('''
        CREATE TABLE IF NOT EXISTS pendencias_internet (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            imovel TEXT,
            carteira TEXT,
            mes_ref TEXT,
            conta TEXT,
            nome_arquivo TEXT,
            arquivo_bytes BLOB,
            observacao_solicitante TEXT,
            usuario_solicitante TEXT,
            status TEXT DEFAULT 'PENDENTE',
            data_solicitacao TEXT
        )
    ''')

    conexao.execute('''
        CREATE TABLE IF NOT EXISTS contas_internet (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            imovel TEXT,
            carteira TEXT,
            mes_ref TEXT,
            conta TEXT,
            nome_arquivo TEXT,
            link_drive TEXT,
            usuario TEXT,
            data_registro TEXT
        )
    ''')
    
    try: conexao.execute("ALTER TABLE imoveis ADD COLUMN dt_comdesc TEXT")
    except: pass
    try: conexao.execute("ALTER TABLE imoveis ADD COLUMN acesso TEXT")
    except: pass
    try: conexao.execute("ALTER TABLE imoveis ADD COLUMN media_emissao TEXT")
    except: pass
    try: conexao.execute("ALTER TABLE cadastro_mestre_imoveis ADD COLUMN media_emissao TEXT")
    except: pass

    conexao.execute('''
        CREATE TABLE IF NOT EXISTS de_para_retorno (
            referencia TEXT PRIMARY KEY,
            imovel_real TEXT,
            nome_sacado TEXT
        )
    ''')
    
    # 🔴 NOVA TABELA DE VÍNCULOS (Com Responsável e Email Juntos)
    conexao.execute('''
        CREATE TABLE IF NOT EXISTS vinculos_adm (
            administradora TEXT PRIMARY KEY,
            responsavel TEXT,
            email TEXT
        )
    ''')
    conexao.execute('CREATE TABLE IF NOT EXISTS historico_observacoes (id INTEGER PRIMARY KEY AUTOINCREMENT, imovel_id INTEGER, data_hora TEXT, usuario TEXT, texto TEXT)')
    conexao.execute('CREATE TABLE IF NOT EXISTS produtividade (id INTEGER PRIMARY KEY AUTOINCREMENT, usuario TEXT, acao TEXT, imovel_id INTEGER, data_hora TEXT)')
    conexao.execute('CREATE TABLE IF NOT EXISTS historico_emails (id INTEGER PRIMARY KEY AUTOINCREMENT, colaborador TEXT, administradora TEXT, qtd_imoveis INTEGER, data_hora TEXT)')
    garantir_tabelas_disparo(conexao)   # disparos em segundo plano + solicitação de login/senha
    garantir_tabelas_login(conexao)     # usuário + senha (mantém o nome de sempre em session['usuario'])
    garantir_tabelas_resposta_acesso(conexao)   # respostas das administradoras ao pedido de login
    garantir_tabela_mudancas(conexao)           # mural que faz a tela se atualizar sozinha
    conexao.execute('''
        CREATE TABLE IF NOT EXISTS log_disparos_diarios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            data TEXT,
            responsavel TEXT,
            qtd_imoveis INTEGER DEFAULT 0,
            data_hora TEXT
        )
    ''')
    
    conexao.execute('''
        CREATE TABLE IF NOT EXISTS relacionamento (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            imovel TEXT,
            tipo TEXT,
            assunto TEXT,
            link TEXT,
            data_hora TEXT
        )
    ''')

    # 🔐 NOVA TABELA DE USUÁRIOS DO SISTEMA (Com suporte a cargos dinâmicos)
    conexao.execute('''
        CREATE TABLE IF NOT EXISTS usuarios_painel (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT UNIQUE,
            is_master INTEGER DEFAULT 0
        )
    ''')
    
    # 🛡️ Atualiza a tabela se ela já existia antes
    try: conexao.execute("ALTER TABLE usuarios_painel ADD COLUMN is_master INTEGER DEFAULT 0")
    except: pass
    
    # Garante que os administradores padrão não fiquem de fora e já nasçam como Master
    conexao.execute("INSERT OR IGNORE INTO usuarios_painel (nome, is_master) VALUES ('ryan', 1)")
    conexao.execute("INSERT OR IGNORE INTO usuarios_painel (nome, is_master) VALUES ('marcio', 1)")
    conexao.execute("INSERT OR IGNORE INTO usuarios_painel (nome, is_master) VALUES ('paulo', 1)")
    conexao.execute("INSERT OR IGNORE INTO usuarios_painel (nome, is_master) VALUES ('alessandra', 1)")
    
    # Reforça o status de Master caso já existissem antes da coluna ser criada
    conexao.execute("UPDATE usuarios_painel SET is_master = 1 WHERE nome IN ('ryan', 'marcio', 'paulo', 'alessandra')")

    # 🚀 CRIAÇÃO DE ÍNDICES: Ponteiros de velocidade máxima para o SQLite
    try: conexao.execute("CREATE INDEX IF NOT EXISTS idx_imoveis_performance ON imoveis(ativo, administradora)")
    except: pass
    try: conexao.execute("CREATE INDEX IF NOT EXISTS idx_vinculos_performance ON vinculos_adm(administradora)")
    except: pass
    
    # 🎯 NOVOS ÍNDICES PARA CORRIGIR A LENTIDÃO DA TELA INICIAL
    try: conexao.execute("CREATE INDEX IF NOT EXISTS idx_imovel_numero ON imoveis(imovel)")
    except: pass
    try: conexao.execute("CREATE INDEX IF NOT EXISTS idx_hist_imovel_id ON historico_observacoes(imovel_id)")
    except: pass
    try: conexao.execute("CREATE INDEX IF NOT EXISTS idx_prod_imovel_id ON produtividade(imovel_id)")
    except: pass
    
    # 👇 ADICIONE ESTAS 3 LINHAS AQUI (ÍNDICES TURBO DE ALTA PERFORMANCE) 👇
    try: conexao.execute("CREATE INDEX IF NOT EXISTS idx_hist_turbo ON historico_observacoes(imovel_id, data_hora DESC, id DESC)")
    except: pass
    try: conexao.execute("CREATE INDEX IF NOT EXISTS idx_prod_turbo ON produtividade(imovel_id, acao, data_hora DESC)")
    except: pass
    # 👆 ================================================================ 👆

    # ⚡ Aba Pendências: ordena/pagina por (ativo, dia do vencimento, id) direto pelo índice, sem ordenar a base toda
    try: conexao.execute("CREATE INDEX IF NOT EXISTS idx_imoveis_pendencias ON imoveis(ativo, substr(vencimento, 1, 2), id)")
    except: pass

    conexao.commit()
    conexao.close()


# Usada como função SQL (chave_adm/vinculo_de) e chamada por linha em várias listagens - para ~700
# administradoras/responsáveis distintos repetidos em dezenas de milhares de linhas, cachear o resultado
# (texto -> texto, função pura) evita refazer o mesmo normalize()+join() caro milhares de vezes por tela carregada.
@functools.lru_cache(maxsize=8192)
def normalizar_texto(valor):
    if valor is None:
        return ''
    texto = str(valor).strip().upper()
    texto = unicodedata.normalize('NFKD', texto)
    texto = ''.join(ch for ch in texto if not unicodedata.combining(ch))
    return texto


def _registrar_funcoes_sql(conexao):
    """Ensina o SQLite a comparar administradoras como o resto do sistema: sem acento, sem diferença de
    maiúscula/minúscula e sem espaço sobrando.

    POR QUE EXISTE: o UPPER() do SQLite só mexe em letras ASCII ('Administração' vira 'ADMINISTRAçãO') e o vínculo
    costuma estar gravado sem acento ('ADMINISTRACAO'). O JOIN antigo
    (TRIM(UPPER(i.administradora)) = TRIM(UPPER(v.administradora))) não casava as duas grafias e o boleto aparecia como
    'Não Atribuído' mesmo com a administradora vinculada.

    Funções registradas na conexão:
      chave_adm(texto) -> chave normalizada (sem acento, MAIÚSCULA, sem espaço nas pontas)
      vinculo_de(adm)  -> rowid da linha de vinculos_adm dessa administradora, ou NULL se não houver. Uso:
                          LEFT JOIN vinculos_adm v ON v.rowid = vinculo_de(i.administradora)
                          Devolve UMA linha por administradora: havendo grafias repetidas, vence quem tem responsável
                          de verdade e, depois, o vínculo mais antigo. Por isso o JOIN também não duplica mais boleto.
    """
    estado = {'mapa': None, 'marca': None}

    def _mapa():
        # recarrega sozinho se esta conexão alterou alguma coisa desde a última leitura
        if estado['mapa'] is None or estado['marca'] != conexao.total_changes:
            mapa = {}
            for rid, adm, _resp in conexao.execute(
                    "SELECT rowid, administradora, responsavel FROM vinculos_adm "
                    "ORDER BY CASE WHEN responsavel IS NULL OR TRIM(responsavel) = '' "
                    "OR TRIM(responsavel) = 'Não Atribuído' THEN 1 ELSE 0 END, rowid").fetchall():
                chave = normalizar_texto(adm)
                if chave:
                    mapa.setdefault(chave, rid)
            estado['mapa'], estado['marca'] = mapa, conexao.total_changes
        return estado['mapa']

    conexao.create_function('chave_adm', 1, normalizar_texto, deterministic=True)
    conexao.create_function('vinculo_de', 1, lambda adm: _mapa().get(normalizar_texto(adm)))


def enviar_email_smtp(destinatario, assunto, corpo_html):
    try:
        if not destinatario: 
            return False
            
        # 🎯 CAÇA-EMAILS (REGEX): Extrai todos os e-mails válidos da string, ignorando sujeiras e espaços
        lista_emails = re.findall(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', destinatario)
        
        if not lista_emails:
            print(f"⚠️ Falha: Nenhum e-mail válido encontrado no texto '{destinatario}'.")
            return False

        # Junta bonitinho para o cabeçalho do e-mail
        destinatario_formatado = ', '.join(lista_emails)
        print(f"🚀 Disparando e-mail múltiplo para: {lista_emails}", flush=True)

        msg = MIMEMultipart()
        msg['From'] = EMAIL_REMETENTE
        msg['To'] = destinatario_formatado
        msg['Subject'] = assunto
        msg.attach(MIMEText(corpo_html, 'html'))
        
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(EMAIL_REMETENTE, SENHA_APP_GMAIL)
        
        # O sendmail usa a lista real de destinatários para disparar
        server.sendmail(EMAIL_REMETENTE, lista_emails, msg.as_string())
        server.quit()
        return True
    except Exception as e:
        print(f"🚨 Erro fatal ao enviar email para {destinatario}: {e}", flush=True)
        return False


# 🔒 Controle de disparos simultâneos (evita 2 pessoas disparando a mesma carteira ao mesmo tempo)
DISPAROS_EM_ANDAMENTO = set()
DISPAROS_LOCK = threading.Lock()

# =================================================================
# ⚡ DISPAROS EM SEGUNDO PLANO (jobs) + SOLICITAÇÃO DE LOGIN/SENHA
# -----------------------------------------------------------------
# Antes o disparo rodava DENTRO da requisição: se passasse do tempo limite do servidor/navegador, a tela mostrava erro
# mas o envio continuava "solto" no servidor (e travava a carteira: "já existe um disparo em andamento").
# Agora o disparo vira um JOB: roda numa thread própria, grava o andamento no banco (tabela disparo_jobs) e a tela
# apenas acompanha. Dá para fechar o pop-up (o disparo continua), reabrir depois e até parar o disparo.
# =================================================================
JOB_TRAVADO_APOS_SEGUNDOS = 180        # sem sinal de vida por esse tempo = job considerado interrompido (queda/reinício)
DIAS_ESPERA_SOLICITACAO_ACESSO = 7     # não pede de novo login/senha do mesmo imóvel antes disso
MAX_IMOVEIS_POR_EMAIL_ACESSO = 80      # e-mails de solicitação muito longos são divididos em partes
# Portais (normalizados) em que o boleto NÃO depende de login de portal da administradora → não pedir login/senha
PORTAIS_SEM_LOGIN = {'CTRL', 'WHATSAPP', 'DOC', 'REMOVIDO', 'PROPRIETARIO'}
# Assunto do pedido de login: o código [AC-xxxxxxxx] é o que identifica a resposta depois.
ASSUNTO_PEDIDO_ACESSO = 'Solicitação de Login e Senha de Acesso ao Portal - Boletos de Condomínio'
STATUS_FINALIZADOS = ('PAGO', 'COLETADO', 'PAGO NA 275', 'PAGO NA ADM')   # boleto já resolvido: não entra na solicitação
# 🤖⚠️ Voltar um boleto de COLETADO para um destes é suspeito: a tela pergunta "o robô coletou errado?" antes de salvar.
STATUS_REGRESSAO_ROBO = ('', 'SOLICITADO', 'NÃO EMITIDO')
# ---------------------------------------------------------------------------------------------------------------------
# 🔒 TRAVAS DA AUTOMAÇÃO (robôs de coleta e de pagamento, disparo de e-mail de cobrança, pedido de login/senha)
#   • boleto já resolvido (PAGO, PAGO NA ADM, PAGO NA 275, COLETADO): a automação NÃO pede nada à administradora e NÃO
#     muda o status, mesmo que o caso esteja no "radar" de emissão do dia;
#   • CHEQUE/DOC: o caso é tratado à mão — nenhum robô atua nele (nem coleta, nem e-mail, nem pedido de login).
# Pessoa mudando o status pela tela continua livre. Para mudar a regra, é só mexer nestas linhas.
STATUS_CHEQUE = ('CHEQUE/DOC',)
# Status que o usuário comum NÃO consegue trocar na mão (só gestor). PAGO ficou livre: a equipe pode corrigir PAGO -> COLETADO.
STATUS_TRAVADOS_PARA_USUARIO = ('PAGO NA ADM', 'PAGO NA 275')

# 🟢 LIBERAÇÃO TEMPORÁRIA (pedido de 18/11/2026): até este prazo, marcar COLETADO não para para checar
# erro de login no Raio-X — a equipe pode coletar manual mesmo com o aviso de login/senha pendente.
# Depois do prazo a trava volta sozinha, sem precisar lembrar de reverter nada.
LIBERACAO_COLETADO_SEM_LOGIN_ATE = datetime(2026, 11, 19, 23, 59, 59)


def coleta_sem_login_liberada():
    return datetime.now() <= LIBERACAO_COLETADO_SEM_LOGIN_ATE
STATUS_FORA_DA_AUTOMACAO = STATUS_FINALIZADOS + STATUS_CHEQUE
_SQL_FORA_DA_AUTOMACAO = "(" + ", ".join(f"'{s}'" for s in STATUS_FORA_DA_AUTOMACAO) + ")"


def fora_da_automacao(status):
    """True = a automação não pode atuar neste caso nem mudar o status dele."""
    return str(status or '').strip().upper() in STATUS_FORA_DA_AUTOMACAO


def sql_livre_para_automacao(coluna='status_interno'):
    """Pedaço de SQL: só deixa passar o caso que a automação PODE tocar (vazio, SOLICITADO, AUTOMAÇÃO, erro...)."""
    return f"UPPER(TRIM(IFNULL({coluna}, ''))) NOT IN {_SQL_FORA_DA_AUTOMACAO}"
# Quem é gestor vem de usuarios_painel.is_master — ver COLABORADORES_INICIAIS no bloco de acesso.
# "S: xxx", "Senha: xxx", "pass: xxx" no texto livre da coluna Acesso = a credencial já está anotada
RE_SENHA_NO_TEXTO = re.compile(r'\b(s|senha|pass(?:word)?)\b\s*[:\-]\s*\S+', re.IGNORECASE)


def _agora_str():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def garantir_tabelas_disparo(conexao):
    conexao.execute('''
        CREATE TABLE IF NOT EXISTS disparo_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tipo TEXT NOT NULL,                       -- 'EMISSAO' (cobrança de boleto) | 'LOGIN' (solicitação de login/senha)
            chave TEXT NOT NULL,                      -- carteira normalizada: trava 2 disparos simultâneos da mesma carteira
            usuario TEXT,
            alvo TEXT,
            status TEXT NOT NULL DEFAULT 'rodando',   -- rodando | concluido | erro | cancelado | interrompido
            total INTEGER DEFAULT 0,
            processados INTEGER DEFAULT 0,
            enviados INTEGER DEFAULT 0,
            sem_email INTEGER DEFAULT 0,
            falhas INTEGER DEFAULT 0,
            ja_disparados INTEGER DEFAULT 0,
            cancelar INTEGER DEFAULT 0,
            msg TEXT,
            resultado TEXT,
            criado_em TEXT,
            atualizado_em TEXT,
            finalizado_em TEXT
        )
    ''')
    conexao.execute('''
        CREATE TABLE IF NOT EXISTS solicitacoes_acesso (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            imovel_id INTEGER,
            imovel TEXT,
            administradora TEXT,
            usuario TEXT,
            data_hora TEXT,
            job_id INTEGER
        )
    ''')
    conexao.execute("CREATE INDEX IF NOT EXISTS idx_solic_acesso_imovel ON solicitacoes_acesso(imovel_id, data_hora)")
    conexao.execute("CREATE INDEX IF NOT EXISTS idx_disparo_jobs_status ON disparo_jobs(status, tipo, chave)")
    conexao.commit()
    # as colunas do código/Message-ID do pedido moram aqui também: sem elas o disparo quebra
    garantir_tabelas_resposta_acesso(conexao)


class SessaoSMTP:
    """Uma ÚNICA conexão com o Gmail para o lote inteiro (antes: login novo a cada e-mail, ~1-3 s cada).
    Reconecta sozinha se a conexão cair; falha de login/limite diário do Gmail interrompe o restante com mensagem clara."""

    def __init__(self):
        self.servidor = None
        self.erro_fatal = None

    def _conectar(self):
        servidor = smtplib.SMTP('smtp.gmail.com', 587, timeout=30)
        servidor.starttls()
        servidor.login(EMAIL_REMETENTE, SENHA_APP_GMAIL)
        self.servidor = servidor

    def fechar(self):
        if self.servidor is not None:
            try:
                self.servidor.quit()
            except Exception:
                pass
            self.servidor = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.fechar()

    def enviar(self, destinatario, assunto, corpo_html):
        if self.erro_fatal or not destinatario:
            return False
        lista_emails = re.findall(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', str(destinatario))
        if not lista_emails:
            print(f"⚠️ Falha: Nenhum e-mail válido encontrado no texto '{destinatario}'.", flush=True)
            return False

        msg = MIMEMultipart()
        msg['From'] = EMAIL_REMETENTE
        msg['To'] = ', '.join(lista_emails)
        msg['Subject'] = assunto
        # Message-ID próprio: é por ele que a resposta da administradora é reconhecida depois
        identificador = make_msgid(domain='lello.com.br')
        msg['Message-ID'] = identificador
        msg.attach(MIMEText(corpo_html, 'html'))

        for tentativa in (1, 2):
            try:
                if self.servidor is None:
                    self._conectar()
                self.servidor.sendmail(EMAIL_REMETENTE, lista_emails, msg.as_string())
                return identificador
            except smtplib.SMTPAuthenticationError as e:
                self.erro_fatal = 'O Gmail recusou o login do e-mail remetente (senha de app inválida ou revogada).'
                print(f"🚨 {self.erro_fatal} ({e})", flush=True)
                self.fechar()
                return False
            except (smtplib.SMTPServerDisconnected, smtplib.SMTPConnectError, ConnectionError, TimeoutError, OSError) as e:
                # conexão caiu/expirou: abre outra e tenta mais uma vez
                self.fechar()
                if tentativa == 2:
                    print(f"🚨 Falha de conexão com o Gmail ao enviar para {lista_emails}: {e}", flush=True)
                    return False
            except smtplib.SMTPException as e:
                texto = str(e).lower()
                if 'sending limit' in texto or 'quota' in texto:
                    self.erro_fatal = 'O Gmail atingiu o limite diário de envios do remetente.'
                    self.fechar()
                print(f"🚨 Gmail recusou o envio para {lista_emails}: {e}", flush=True)
                return False
        return False


def usuario_eh_gestor(usuario):
    u = (usuario or '').strip().lower()
    conexao = conectar_banco()
    try:
        linha = conexao.execute('SELECT is_master FROM usuarios_painel WHERE nome = ?', (u,)).fetchone()
        return bool(linha and linha['is_master'] == 1)
    except Exception:
        return False
    finally:
        conexao.close()


def _job_para_dict(linha):
    if not linha:
        return None
    d = dict(linha)
    try:
        d['resultado'] = json.loads(d['resultado']) if d.get('resultado') else None
    except Exception:
        d['resultado'] = None
    total = d.get('total') or 0
    if total:
        d['percentual'] = min(100, int(round(100 * (d.get('processados') or 0) / total)))
    else:
        d['percentual'] = 0 if d.get('status') == 'rodando' else 100
    d['em_andamento'] = d.get('status') == 'rodando'
    return d


def expirar_jobs_travados(conexao):
    """Job 'rodando' sem sinal de vida (servidor reiniciou/caiu) vira 'interrompido' e libera a carteira."""
    limite = (datetime.now() - timedelta(seconds=JOB_TRAVADO_APOS_SEGUNDOS)).strftime('%Y-%m-%d %H:%M:%S')
    conexao.execute(
        "UPDATE disparo_jobs SET status = 'interrompido', finalizado_em = ?, "
        "msg = 'O disparo foi interrompido (reinício ou queda do servidor). O que já foi enviado ficou registrado; é seguro disparar de novo.' "
        "WHERE status = 'rodando' AND atualizado_em < ?", (_agora_str(), limite))
    conexao.commit()


def criar_job_disparo(tipo, chave, usuario, alvo):
    """Devolve (job_id, None) se criou, ou (None, id_do_job_existente) se já há um rodando para a mesma carteira."""
    conexao = conectar_banco()
    try:
        garantir_tabelas_disparo(conexao)
        expirar_jobs_travados(conexao)
        conexao.isolation_level = None          # transação manual: checar + inserir é uma operação só (vale entre processos)
        conexao.execute('BEGIN IMMEDIATE')
        try:
            existente = conexao.execute(
                "SELECT id FROM disparo_jobs WHERE tipo = ? AND chave = ? AND status = 'rodando' ORDER BY id DESC LIMIT 1",
                (tipo, chave)).fetchone()
            if existente:
                conexao.execute('ROLLBACK')
                return None, existente['id']
            agora = _agora_str()
            cur = conexao.execute(
                "INSERT INTO disparo_jobs (tipo, chave, usuario, alvo, status, criado_em, atualizado_em) VALUES (?, ?, ?, ?, 'rodando', ?, ?)",
                (tipo, chave, usuario, alvo, agora, agora))
            job_id = cur.lastrowid
            conexao.execute('COMMIT')
            return job_id, None
        except Exception:
            try:
                conexao.execute('ROLLBACK')
            except Exception:
                pass
            raise
    finally:
        conexao.close()


def atualizar_job(job_id, **campos):
    if not campos:
        return
    campos['atualizado_em'] = _agora_str()
    colunas = ', '.join(f'{k} = ?' for k in campos)      # nomes de coluna vêm só do nosso código
    conexao = conectar_banco()
    try:
        conexao.execute(f'UPDATE disparo_jobs SET {colunas} WHERE id = ?', list(campos.values()) + [job_id])
        conexao.commit()
    except Exception as e:
        print(f'⚠️ Não foi possível atualizar o andamento do job {job_id}: {e}', flush=True)
    finally:
        conexao.close()


def job_foi_cancelado(job_id):
    conexao = conectar_banco()
    try:
        linha = conexao.execute('SELECT cancelar FROM disparo_jobs WHERE id = ?', (job_id,)).fetchone()
        return bool(linha and linha['cancelar'])
    except Exception:
        return False
    finally:
        conexao.close()


def ler_job(job_id):
    conexao = conectar_banco()
    try:
        garantir_tabelas_disparo(conexao)
        expirar_jobs_travados(conexao)
        return _job_para_dict(conexao.execute('SELECT * FROM disparo_jobs WHERE id = ?', (job_id,)).fetchone())
    finally:
        conexao.close()


def _executar_job(job_id, tipo, usuario, alvo):
    """Corpo da thread do disparo. Sempre deixa o job num estado final (concluido/erro/cancelado)."""
    def progresso(estado):
        campos = {k: estado[k] for k in ('total', 'processados', 'enviados', 'sem_email', 'falhas', 'ja_disparados') if k in estado}
        atualizar_job(job_id, **campos)

    def cancelado():
        return job_foi_cancelado(job_id)

    try:
        if tipo == 'LOGIN':
            resultado = executar_solicitacao_acessos(usuario, alvo, progresso=progresso, cancelado=cancelado, job_id=job_id)
        else:
            resultado = executar_processamento_disparos_diarios(
                send_emails=True, usuario_solicitante=usuario, responsavel_alvo=alvo, progresso=progresso, cancelado=cancelado)

        erro_smtp = resultado.get('erro_smtp')
        if resultado.get('cancelado'):
            status, msg = 'cancelado', 'Disparo interrompido por você. O que já foi enviado ficou registrado.'
        elif erro_smtp and not resultado.get('qtd_enviados'):
            status, msg = 'erro', erro_smtp
        else:
            status, msg = 'concluido', erro_smtp or ''
        final = {
            'status': status, 'msg': msg, 'resultado': json.dumps(resultado, ensure_ascii=False), 'finalizado_em': _agora_str(),
            'enviados': resultado.get('qtd_enviados', 0), 'sem_email': resultado.get('qtd_sem_email', 0),
            'falhas': resultado.get('qtd_falhas', 0), 'ja_disparados': resultado.get('qtd_ja_disparados', 0),
        }
        atualizar_job(job_id, **final)
        if status == 'concluido':   # barra de progresso fecha em 100%
            conexao_fim = conectar_banco()
            try:
                conexao_fim.execute('UPDATE disparo_jobs SET processados = total WHERE id = ?', (job_id,))
                conexao_fim.commit()
            finally:
                conexao_fim.close()
    except Exception as e:
        print(f'🚨 Erro no job de disparo {job_id} ({tipo}): {e}', flush=True)
        atualizar_job(job_id, status='erro', msg=str(e)[:500], finalizado_em=_agora_str())


def iniciar_job_disparo(tipo, chave, usuario, alvo):
    """Cria o job e dispara a thread. Devolve (job_id, None) ou (None, id_do_job_ja_rodando)."""
    job_id, existente = criar_job_disparo(tipo, chave, usuario, alvo)
    if job_id is None:
        return None, existente
    threading.Thread(target=_executar_job, args=(job_id, tipo, usuario, alvo), daemon=True, name=f'disparo-{job_id}').start()
    return job_id, None


def buscar_emissoes_hoje(conexao, agora, usuario_solicitante=None, responsavel_alvo=None):
    """Fonte única de 'casos com possível emissão hoje'.
    - responsavel_alvo: carteira de um colaborador específico (comparação sem acento/caixa).
    - usuario_solicitante (sem responsavel_alvo): comportamento antigo, filtra por LIKE no nome de quem está logado.
    """
    hoje_dia_str = agora.strftime('%d')
    dia_sem_zero = str(agora.day)
    hoje_completo = agora.strftime('%d/%m/%Y')

    query = '''
        SELECT i.id, i.imovel, i.media_emissao, i.vencimento, i.administradora, i.sacado, i.empreendimento, i.endereco, i.cpf_cnpj,
               COALESCE(NULLIF(TRIM(v.responsavel), ''), 'Não Atribuído') AS responsavel
        FROM imoveis i
        LEFT JOIN vinculos_adm v ON v.rowid = vinculo_de(i.administradora)
        WHERE i.ativo = 1
          AND i.media_emissao IS NOT NULL
          AND ''' + sql_livre_para_automacao('i.status_interno') + '''
    '''
    parametros = []

    if usuario_solicitante and not responsavel_alvo:
        query += " AND v.responsavel LIKE ?"
        parametros.append(f"%{usuario_solicitante}%")

    candidatos = conexao.execute(query, parametros).fetchall()
    alvo_norm = normalizar_texto(responsavel_alvo) if responsavel_alvo else None

    registros = []
    for item in candidatos:
        if alvo_norm and normalizar_texto(item['responsavel']) != alvo_norm:
            continue
        media = str(item['media_emissao'] or '').strip()
        if media in [hoje_dia_str, dia_sem_zero, hoje_completo]:
            registros.append(item)

    # Administradora cadastrada 2x em vinculos_adm repete o imóvel no JOIN → 1 e-mail por imóvel (vence o vínculo com responsável de verdade)
    unicos = {}
    for r in registros:
        atual = unicos.get(r['id'])
        if atual is None or (atual['responsavel'] == 'Não Atribuído' and r['responsavel'] != 'Não Atribuído'):
            unicos[r['id']] = r
    # Boleto com desconto vem em 2 linhas (mesmo imóvel, mesmo vencimento): a administradora recebe 1 e-mail
    # por BOLETO, não 2. Vencimentos alternativos (dia 05 e 15) continuam sendo e-mails separados.
    por_boleto = {}
    for r in sorted(unicos.values(), key=lambda x: x['id']):
        por_boleto.setdefault((r['imovel'], _venc_texto(r['vencimento']) or str(r['vencimento'] or '').strip()), r)
    return list(por_boleto.values())


def imoveis_ja_disparados_hoje(conexao, data_hoje):
    """IDs dos imóveis que já tiveram e-mail de cobrança disparado hoje (por qualquer pessoa)."""
    linhas = conexao.execute(
        "SELECT DISTINCT imovel_id FROM produtividade WHERE acao = 'DISPAROU EMAIL COBRANCA' AND data_hora LIKE ?",
        (data_hoje + '%',)
    ).fetchall()
    return {l['imovel_id'] for l in linhas}


def resolver_nome_responsavel(nome):
    """Devolve o nome exatamente como está em vinculos_adm (ex.: 'Jessica' -> 'Jéssica') ou None se não existir."""
    alvo = normalizar_texto(nome)
    if not alvo or alvo == normalizar_texto('Não Atribuído'):
        return None
    conexao = conectar_banco()
    try:
        linhas = conexao.execute("SELECT DISTINCT TRIM(responsavel) AS r FROM vinculos_adm WHERE responsavel IS NOT NULL AND TRIM(responsavel) != ''").fetchall()
    finally:
        conexao.close()
    for l in linhas:
        if normalizar_texto(l['r']) == alvo:
            return l['r']
    return None


def executar_processamento_disparos_diarios(send_emails=False, usuario_solicitante=None, responsavel_alvo=None, progresso=None, cancelado=None):
    """usuario_solicitante = quem apertou o botão (assina a ação no prontuário/produtividade).
    responsavel_alvo      = de quem é a carteira que será disparada (se vazio, é a do próprio usuário).
    progresso(dict)       = (opcional) chamado a cada e-mail para a tela acompanhar o andamento.
    cancelado()           = (opcional) devolve True quando o operador pediu para parar o disparo."""
    agora = datetime.now()
    data_hoje = agora.strftime('%Y-%m-%d')

    conexao = conectar_banco()
    smtp = SessaoSMTP() if send_emails else None   # 1 conexão com o Gmail para o lote todo
    try:
        registros = buscar_emissoes_hoje(conexao, agora, usuario_solicitante, responsavel_alvo)

        # Ao enviar de verdade, pula quem já recebeu e-mail hoje (evita duplicar se outra pessoa já disparou)
        qtd_ja_disparados = 0
        if send_emails and registros:
            ja_feitos = imoveis_ja_disparados_hoje(conexao, data_hoje)
            total_antes = len(registros)
            registros = [r for r in registros if r['id'] not in ja_feitos]
            qtd_ja_disparados = total_antes - len(registros)

        resultado = {
            'sucesso': True, 'qtd': len(registros), 'qtd_encontrados': len(registros),
            'qtd_enviados': 0, 'qtd_sem_email': 0, 'qtd_falhas': 0,
            'qtd_ja_disparados': qtd_ja_disparados, 'qtd_bloqueados_status': 0, 'responsaveis': [],
            'cancelado': False, 'erro_smtp': None
        }
        if progresso:
            progresso({'total': len(registros), 'processados': 0, 'ja_disparados': qtd_ja_disparados})
        if not registros:
            return resultado

        data_hora_atual = agora.strftime('%Y-%m-%d %H:%M:%S')
        por_responsavel = {}
        processados = 0

        # Quem assina a ação no prontuário? O operador logado ou o robô.
        nome_operador = usuario_solicitante.title() if usuario_solicitante else 'RPA ATIVO'

        for item in registros:
            if cancelado and cancelado():
                resultado['cancelado'] = True
                break

            # 🔒 O caso pode ter sido resolvido (ou virado cheque) DEPOIS de a lista ser montada: confere o status de agora.
            if send_emails:
                atual = conexao.execute('SELECT status_interno FROM imoveis WHERE id = ?', (item['id'],)).fetchone()
                if atual is not None and fora_da_automacao(atual['status_interno']):
                    resultado['qtd_bloqueados_status'] += 1
                    print(f"🔒 Disparo: imóvel {item['imovel']} está {str(atual['status_interno']).upper()}; e-mail não enviado.", flush=True)
                    processados += 1
                    if progresso:
                        progresso({'total': len(registros), 'processados': processados, 'enviados': resultado['qtd_enviados'],
                                   'sem_email': resultado['qtd_sem_email'], 'falhas': resultado['qtd_falhas'], 'ja_disparados': qtd_ja_disparados})
                    continue

            responsavel = item['responsavel']
            por_responsavel[responsavel] = por_responsavel.get(responsavel, 0) + 1

            if not send_emails:
                continue

            adm = item['administradora']
            vinc = conexao.execute('SELECT responsavel, email FROM vinculos_adm WHERE rowid = vinculo_de(?)', (adm,)).fetchone()

            destinatario = None
            if vinc and vinc['email']:
                destinatario = str(vinc['email']).strip()

            if not destinatario:
                resultado['qtd_sem_email'] += 1
                conexao.execute('INSERT INTO historico_observacoes (imovel_id, data_hora, usuario, texto) VALUES (?, ?, ?, ?)', (item['id'], data_hora_atual, nome_operador, f'⚠️ Email NÃO enviado: A administradora "{adm}" não possui e-mail cadastrado nas Configurações.'))
                conexao.commit()
            else:
                # EXTRAÇÃO DO MÊS/ANO DE REFERÊNCIA
                venc_str = str(item['vencimento'] or '')
                mes_ref = ""
                if '/' in venc_str:
                    partes = venc_str.split('/')
                    if len(partes) >= 2:
                        mes_ref = f"{partes[1]}/{partes[2] if len(partes) == 3 else '2026'}"

                assunto = f"Solicitação de Boleto - Envio - Referência de Cobrança ({mes_ref})"

                tabela_individual = f'''<table style="width: 100%; border-collapse: collapse; margin-top: 15px; font-family: Arial, sans-serif; font-size: 13px;">
                    <thead><tr style="background-color: #0f172a; color: white; text-align: left;">
                    <th style="padding: 10px; border: 1px solid #dee2e6;">Nº Imóvel</th><th style="padding: 10px; border: 1px solid #dee2e6;">Sacado</th><th style="padding: 10px; border: 1px solid #dee2e6;">Empreendimento</th><th style="padding: 10px; border: 1px solid #dee2e6;">Endereço</th><th style="padding: 10px; border: 1px solid #dee2e6;">CPF/CNPJ</th></tr></thead>
                    <tbody><tr>
                    <td style="padding: 8px; border: 1px solid #dee2e6; text-align: center;"><b>{item['imovel']}</b></td><td style="padding: 8px; border: 1px solid #dee2e6;">{item['sacado']}</td><td style="padding: 8px; border: 1px solid #dee2e6;">{item['empreendimento']}</td><td style="padding: 8px; border: 1px solid #dee2e6;">{item['endereco']}</td><td style="padding: 8px; border: 1px solid #dee2e6;">{item['cpf_cnpj']}</td></tr></tbody></table>'''

                corpo = f"Prezados,<br><br>Solicitamos o envio do boleto de condomínio <b>(Referente ao mês {mes_ref})</b> para o pagamento do imóvel listado abaixo:<br><br>{tabela_individual}<br>Ficamos no aguardo do boleto para o respectivo pagamento bancário.<br><br>Atenciosamente,<br><b>Financeiro - Lello</b>"

                enviado = smtp.enviar(destinatario, assunto, corpo)

                if enviado:
                    resultado['qtd_enviados'] += 1
                    conexao.execute('INSERT INTO historico_emails (colaborador, administradora, qtd_imoveis, data_hora) VALUES (?, ?, 1, ?)', (nome_operador, adm, data_hora_atual))
                    try:
                        conexao.execute('INSERT INTO produtividade (usuario, acao, imovel_id, data_hora) VALUES (?, ?, ?, ?)', (nome_operador, 'DISPAROU EMAIL COBRANCA', item['id'], data_hora_atual))
                    except Exception:
                        pass

                    marco_mud = marco_das_mudancas(conexao)
                    conexao.execute('UPDATE imoveis SET status_interno = ? WHERE id = ? AND ' + sql_livre_para_automacao(),
                                    ('SOLICITADO', item['id']))
                    assinar_mudancas(conexao, marco_mud, nome_operador, [item['imovel']])

                    # Deixa claro no prontuário quando alguém disparou a carteira de outro colaborador
                    if responsavel_alvo and normalizar_texto(responsavel) != normalizar_texto(nome_operador):
                        texto_obs = f'✉️ Status alterado para SOLICITADO via disparo em lote feito por {nome_operador} na carteira de {responsavel}.'
                    else:
                        texto_obs = '✉️ Status alterado automaticamente para SOLICITADO via disparo em lote.'
                    conexao.execute('INSERT INTO historico_observacoes (imovel_id, data_hora, usuario, texto) VALUES (?, ?, ?, ?)', (item['id'], data_hora_atual, nome_operador, texto_obs))
                else:
                    resultado['qtd_falhas'] += 1
                    motivo = smtp.erro_fatal or 'Erro no servidor do Gmail ao enviar.'
                    conexao.execute('INSERT INTO historico_observacoes (imovel_id, data_hora, usuario, texto) VALUES (?, ?, ?, ?)', (item['id'], data_hora_atual, nome_operador, f'⚠️ Falha: {motivo}'))

                # 💾 Salva a cada e-mail: se o servidor cair no meio, o que já foi enviado fica registrado (e não é reenviado depois)
                conexao.commit()

            processados += 1
            if progresso:   # (sempre logo após o commit: sem transação aberta, para não travar o banco)
                progresso({'total': len(registros), 'processados': processados, 'enviados': resultado['qtd_enviados'],
                           'sem_email': resultado['qtd_sem_email'], 'falhas': resultado['qtd_falhas'], 'ja_disparados': qtd_ja_disparados})

        resultado['erro_smtp'] = smtp.erro_fatal if smtp else None

        # Salva que essa carteira já foi processada hoje.
        # (Disparo cancelado ou sem nenhum envio possível por erro do Gmail NÃO conta como "processado": o aviso volta a aparecer.)
        if not resultado['cancelado'] and not (send_emails and resultado['erro_smtp'] and not resultado['qtd_enviados']):
            for resp, qtd in por_responsavel.items():
                conexao.execute('INSERT INTO log_disparos_diarios (data, responsavel, qtd_imoveis, data_hora) VALUES (?, ?, ?, ?)', (data_hoje, resp, qtd, data_hora_atual))

        conexao.commit()
        resultado['responsaveis'] = [{'responsavel': r, 'qtd_imoveis': q} for r, q in sorted(por_responsavel.items())]
        return resultado
    finally:
        if smtp:
            smtp.fechar()
        conexao.close()


# =================================================================
# 🔑 SOLICITAÇÃO DE LOGIN E SENHA ÀS ADMINISTRADORAS
# =================================================================
def _tem_valor(v):
    return v is not None and str(v).strip() not in ('', 'None', 'NULL', '-')


def mapa_vinculos_normalizados(conexao):
    """administradora (sem acento/caixa) -> linha de vinculos_adm. Se houver duplicata, vence quem tem responsável de verdade."""
    mapa = {}
    for r in conexao.execute(
            "SELECT administradora, responsavel, email FROM vinculos_adm "
            "ORDER BY CASE WHEN responsavel IS NULL OR TRIM(responsavel) = '' OR TRIM(responsavel) = 'Não Atribuído' THEN 1 ELSE 0 END, rowid"):
        mapa.setdefault(normalizar_texto(r['administradora']), r)
    return mapa


def buscar_casos_sem_acesso(conexao, responsavel_alvo=None):
    """Imóveis ativos, ainda pendentes, que NÃO têm login e/ou senha de portal.
    Tem credencial = login+senha no cadastro (login_acesso/senha_acesso) OU 'S:'/'Senha:' anotado no texto da coluna Acesso.
    Fora da lista: portais em que o boleto não depende de login (CTRL, WhatsApp, DOC...)."""
    mapa = mapa_vinculos_normalizados(conexao)
    alvo_norm = normalizar_texto(responsavel_alvo) if responsavel_alvo else None
    marcas = ','.join('?' * len(STATUS_FORA_DA_AUTOMACAO))
    linhas = conexao.execute(f'''
        SELECT i.id, i.imovel, i.administradora, i.sacado, i.empreendimento, i.endereco, i.cpf_cnpj, i.vencimento, i.acesso,
               m.login_acesso, m.senha_acesso, m.portal
        FROM imoveis i
        LEFT JOIN cadastro_mestre_imoveis m ON m.imovel = i.imovel
        WHERE i.ativo = 1 AND i.administradora IS NOT NULL AND TRIM(i.administradora) != ''
          AND (i.status_interno IS NULL OR UPPER(TRIM(i.status_interno)) NOT IN ({marcas}))
    ''', STATUS_FORA_DA_AUTOMACAO).fetchall()

    casos = []
    for r in linhas:
        if normalizar_texto(r['portal']) in PORTAIS_SEM_LOGIN:
            continue
        if RE_SENHA_NO_TEXTO.search(r['acesso'] or ''):
            continue
        tem_login, tem_senha = _tem_valor(r['login_acesso']), _tem_valor(r['senha_acesso'])
        if tem_login and tem_senha:
            continue
        falta = 'login e senha' if not tem_login and not tem_senha else ('senha' if tem_login else 'login')

        chave_adm = normalizar_texto(r['administradora'])
        vinc = mapa.get(chave_adm)
        responsavel = (vinc['responsavel'] or '').strip() if vinc else ''
        if not responsavel:
            responsavel = 'Não Atribuído'
        if alvo_norm and normalizar_texto(responsavel) != alvo_norm:
            continue
        email = str(vinc['email']).strip() if vinc and vinc['email'] else ''
        if email and not re.search(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', email):
            email = ''
        casos.append({
            'id': r['id'], 'imovel': r['imovel'], 'administradora': (r['administradora'] or '').strip(), 'adm_chave': chave_adm,
            'sacado': r['sacado'], 'empreendimento': r['empreendimento'], 'endereco': r['endereco'], 'cpf_cnpj': r['cpf_cnpj'],
            'portal': (r['portal'] or '').strip(), 'falta': falta, 'responsavel': responsavel, 'email': email,
        })
    return casos


def ids_solicitados_recentemente(conexao, ids=None):
    limite = (datetime.now() - timedelta(days=DIAS_ESPERA_SOLICITACAO_ACESSO)).strftime('%Y-%m-%d %H:%M:%S')
    if ids is None:
        linhas = conexao.execute('SELECT DISTINCT imovel_id FROM solicitacoes_acesso WHERE data_hora >= ?', (limite,)).fetchall()
    else:
        ids = list(ids)
        if not ids:
            return set()
        marcas = ','.join('?' * len(ids))
        linhas = conexao.execute(f'SELECT DISTINCT imovel_id FROM solicitacoes_acesso WHERE data_hora >= ? AND imovel_id IN ({marcas})', [limite] + ids).fetchall()
    return {l['imovel_id'] for l in linhas}


def resumir_solicitacao_acessos(responsavel_alvo=None):
    """Números para a tela de confirmação: o que será enviado, o que fica de fora e por quê."""
    conexao = conectar_banco()
    try:
        garantir_tabelas_disparo(conexao)
        casos = buscar_casos_sem_acesso(conexao, responsavel_alvo)
        ja = ids_solicitados_recentemente(conexao)
        por_adm = {}
        for c in casos:
            g = por_adm.setdefault(c['adm_chave'], {'administradora': c['administradora'], 'responsavel': c['responsavel'],
                                                    'enviar': 0, 'aguardando': 0, 'sem_email': 0, 'email_ok': bool(c['email'])})
            if c['id'] in ja:
                g['aguardando'] += 1
            elif not c['email']:
                g['sem_email'] += 1
            else:
                g['enviar'] += 1
        enviaveis = sum(g['enviar'] for g in por_adm.values())
        emails = sum(-(-g['enviar'] // MAX_IMOVEIS_POR_EMAIL_ACESSO) for g in por_adm.values() if g['enviar'])
        lista = sorted(por_adm.values(), key=lambda g: (-g['enviar'], -(g['aguardando'] + g['sem_email']), g['administradora']))
        return {
            'sucesso': True, 'total_casos': len(casos), 'enviaveis': enviaveis, 'emails': emails,
            'administradoras_a_enviar': sum(1 for g in por_adm.values() if g['enviar']),
            'aguardando_retorno': sum(g['aguardando'] for g in por_adm.values()),
            'sem_email': sum(g['sem_email'] for g in por_adm.values()),
            'dias_espera': DIAS_ESPERA_SOLICITACAO_ACESSO,
            'por_administradora': lista[:60], 'total_administradoras': len(lista),
        }
    finally:
        conexao.close()


def montar_email_solicitacao_acessos(administradora, lote, codigo=''):
    esc = _esc_html
    linhas = ''.join(
        f'''<tr>
            <td style="padding: 8px; border: 1px solid #dee2e6; text-align: center;"><b>{esc(str(c['imovel'] or ''))}</b></td>
            <td style="padding: 8px; border: 1px solid #dee2e6;">{esc(str(c['sacado'] or ''))}</td>
            <td style="padding: 8px; border: 1px solid #dee2e6;">{esc(str(c['empreendimento'] or ''))}</td>
            <td style="padding: 8px; border: 1px solid #dee2e6;">{esc(str(c['endereco'] or ''))}</td>
            <td style="padding: 8px; border: 1px solid #dee2e6;">{esc(str(c['cpf_cnpj'] or ''))}</td>
            <td style="padding: 8px; border: 1px solid #dee2e6; text-align: center;">{esc(c['falta'])}</td></tr>'''
        for c in lote)
    tabela = f'''<table style="width: 100%; border-collapse: collapse; margin-top: 15px; font-family: Arial, sans-serif; font-size: 13px;">
        <thead><tr style="background-color: #0f172a; color: white; text-align: left;">
        <th style="padding: 10px; border: 1px solid #dee2e6;">Nº Imóvel</th><th style="padding: 10px; border: 1px solid #dee2e6;">Sacado</th><th style="padding: 10px; border: 1px solid #dee2e6;">Empreendimento</th><th style="padding: 10px; border: 1px solid #dee2e6;">Endereço</th><th style="padding: 10px; border: 1px solid #dee2e6;">CPF/CNPJ</th><th style="padding: 10px; border: 1px solid #dee2e6;">Precisamos de</th></tr></thead>
        <tbody>{linhas}</tbody></table>'''
    assunto = f'{ASSUNTO_PEDIDO_ACESSO} [AC-{codigo}]' if codigo else ASSUNTO_PEDIDO_ACESSO
    corpo = (f"Prezados,<br><br>Para agilizarmos o recebimento mensal dos boletos de condomínio, solicitamos o envio do "
             f"<b>login e senha de acesso ao portal</b> (e o link de acesso, se houver) referentes aos imóveis listados abaixo:<br><br>{tabela}<br>"
             f"Caso o boleto desses imóveis seja enviado por outro canal (por exemplo, por e-mail), pedimos a gentileza de nos informar qual.<br><br>"
             f"Agradecemos desde já a atenção.<br><br>Atenciosamente,<br><b>Financeiro - Lello</b>")
    return assunto, corpo


def executar_solicitacao_acessos(usuario_solicitante, responsavel_alvo=None, progresso=None, cancelado=None, job_id=None):
    """Envia 1 e-mail por administradora (dividido em partes se passar de MAX_IMOVEIS_POR_EMAIL_ACESSO) pedindo login e senha
    dos imóveis que não têm. Registra cada imóvel em solicitacoes_acesso (evita pedir de novo antes de DIAS_ESPERA...)
    e deixa uma linha no prontuário. NÃO muda o status do imóvel."""
    conexao = conectar_banco()
    smtp = SessaoSMTP()
    try:
        garantir_tabelas_disparo(conexao)
        casos = buscar_casos_sem_acesso(conexao, responsavel_alvo)
        ja = ids_solicitados_recentemente(conexao)

        resultado = {
            'sucesso': True, 'tipo': 'LOGIN', 'qtd_encontrados': len(casos), 'qtd_enviados': 0, 'qtd_emails': 0,
            'qtd_sem_email': 0, 'qtd_falhas': 0, 'qtd_ja_disparados': 0, 'administradoras': 0, 'cancelado': False, 'erro_smtp': None,
        }
        grupos = {}
        for c in casos:
            if c['id'] in ja:
                resultado['qtd_ja_disparados'] += 1
            elif not c['email']:
                resultado['qtd_sem_email'] += 1
            else:
                grupos.setdefault(c['adm_chave'], []).append(c)

        total = sum(len(v) for v in grupos.values())
        if progresso:
            progresso({'total': total, 'processados': 0, 'sem_email': resultado['qtd_sem_email'], 'ja_disparados': resultado['qtd_ja_disparados']})

        nome_operador = (usuario_solicitante or 'Sistema').title()
        processados = 0
        adms_enviadas = set()

        for chave_adm, itens in sorted(grupos.items(), key=lambda kv: (-len(kv[1]), kv[0])):
            for i in range(0, len(itens), MAX_IMOVEIS_POR_EMAIL_ACESSO):
                if cancelado and cancelado():
                    resultado['cancelado'] = True
                    break
                lote = itens[i:i + MAX_IMOVEIS_POR_EMAIL_ACESSO]
                # confere de novo: outra pessoa pode ter solicitado esses imóveis enquanto este disparo rodava
                ja_agora = ids_solicitados_recentemente(conexao, [c['id'] for c in lote])
                if ja_agora:
                    resultado['qtd_ja_disparados'] += len(ja_agora)
                    lote = [c for c in lote if c['id'] not in ja_agora]
                if lote:
                    codigo = uuid.uuid4().hex[:8]
                    assunto, corpo = montar_email_solicitacao_acessos(lote[0]['administradora'], lote, codigo)
                    identificador = smtp.enviar(lote[0]['email'], assunto, corpo)
                    if identificador:
                        data_hora = _agora_str()
                        resultado['qtd_enviados'] += len(lote)
                        resultado['qtd_emails'] += 1
                        adms_enviadas.add(chave_adm)
                        for c in lote:
                            conexao.execute('''INSERT INTO solicitacoes_acesso
                                               (imovel_id, imovel, administradora, usuario, data_hora, job_id, codigo, message_id, assunto)
                                               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                                            (c['id'], c['imovel'], c['administradora'], nome_operador, data_hora, job_id,
                                             codigo, str(identificador), assunto))
                            conexao.execute('INSERT INTO historico_observacoes (imovel_id, data_hora, usuario, texto) VALUES (?, ?, ?, ?)',
                                            (c['id'], data_hora, nome_operador, f"🔑 Solicitado por e-mail à administradora: {c['falta']} de acesso ao portal."))
                    else:
                        resultado['qtd_falhas'] += len(lote)
                conexao.commit()
                processados += len(itens[i:i + MAX_IMOVEIS_POR_EMAIL_ACESSO])
                if progresso:
                    progresso({'total': total, 'processados': processados, 'enviados': resultado['qtd_enviados'],
                               'sem_email': resultado['qtd_sem_email'], 'falhas': resultado['qtd_falhas'], 'ja_disparados': resultado['qtd_ja_disparados']})
            if resultado['cancelado']:
                break

        resultado['administradoras'] = len(adms_enviadas)
        resultado['erro_smtp'] = smtp.erro_fatal
        return resultado
    finally:
        smtp.fechar()
        conexao.close()


def loop_scheduler_disparos_diarios():
    global SCHEDULER_DISPAROS_ULTIMA_EXECUCAO
    while True:
        agora = datetime.now()
        data_hoje = agora.strftime('%Y-%m-%d')

        deve_executar = False
        if SCHEDULER_DISPAROS_ULTIMA_EXECUCAO != data_hoje and agora.hour >= 8:
            if agora.hour > 8:
                deve_executar = True
            elif agora.hour == 8 and agora.minute >= 0:
                deve_executar = True

        if deve_executar:
            try:
                executar_processamento_disparos_diarios()
            except Exception as erro:
                print(f'Erro no scheduler de disparos diários: {erro}')

        time.sleep(60)


def iniciar_scheduler_disparos_diarios():
    global SCHEDULER_DISPAROS_THREAD_STARTED
    if SCHEDULER_DISPAROS_THREAD_STARTED:
        return
    SCHEDULER_DISPAROS_THREAD_STARTED = True
    thread = threading.Thread(target=loop_scheduler_disparos_diarios, daemon=True)
    thread.start()


# ==============================================================================
# 🔐 ACESSO AO SISTEMA — usuário + senha
# ==============================================================================
# REGRA DE OURO DESTA PARTE: o que vai para session['usuario'] continua sendo
# EXATAMENTE o mesmo nome de sempre ('Ryan', 'Jessica', 'João'...). Todo o resto
# do sistema — produtividade, carteiras, matriz, relatórios, home office — lê
# esse nome. Nenhuma linha de histórico muda por causa do login.
# O usuário digitado é só a chave para achar a pessoa; quem manda é nome_exibicao.

SESSAO_HORAS = 12                      # quanto tempo a pessoa fica logada
MAX_TENTATIVAS_LOGIN = 5               # erros seguidos antes de travar
MINUTOS_BLOQUEIO_LOGIN = 15            # quanto tempo fica travado
HORAS_VALIDADE_LINK_SENHA = 1          # validade do link de "esqueci minha senha"
TAMANHO_MINIMO_SENHA = 8

# Quem já usava o sistema. (usuário para digitar, nome que vai para a sessão, é gestor)
# O nome do meio é o que o sistema inteiro já usa hoje — não altere sem migrar os dados.
COLABORADORES_INICIAIS = [
    ('alessandra', 'Alessandra', 1),
    ('arthur',     'Arthur',     0),
    ('ellen',      'Ellen',      0),
    ('geovanna',   'Geovanna',   0),
    ('gisele',     'Gisele',     0),
    ('heloisa',    'Heloisa',    0),
    ('jessica',    'Jessica',    0),
    ('joao',       'João',       0),
    ('marcio',     'Marcio',     1),
    ('paulo',      'Paulo',      1),
    ('ryan',       'Ryan',       1),
    ('victor',     'Victor',     0),
]

ALFABETO_SENHA_TEMP = 'abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789'   # sem l/I/0/O


def _texto_login(valor):
    """'João', ' JOAO ', 'joão' -> 'joao'. É assim que o usuário digitado vira chave de busca."""
    bruto = ' '.join(str(valor or '').split())
    sem_acento = unicodedata.normalize('NFKD', bruto).encode('ascii', 'ignore').decode('ascii')
    return sem_acento.strip().lower()


def _pasta_do_banco():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _senha_temporaria():
    return '-'.join(''.join(secrets.choice(ALFABETO_SENHA_TEMP) for _ in range(4)) for _ in range(3))


def _marca_senha(senha_hash):
    """Pedaço curto do hash guardado na sessão: se a senha mudar, as sessões antigas caem sozinhas."""
    return hashlib.sha256(str(senha_hash or '').encode('utf-8')).hexdigest()[:12]


def _hash_token(token):
    return hashlib.sha256(str(token or '').encode('utf-8')).hexdigest()


def _agora_texto():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def problema_na_senha(senha, usuario_login=''):
    """Devolve a mensagem do que está errado, ou '' se a senha serve."""
    senha = str(senha or '')
    if len(senha) < TAMANHO_MINIMO_SENHA:
        return f'A senha precisa ter pelo menos {TAMANHO_MINIMO_SENHA} caracteres.'
    if senha.strip() != senha:
        return 'A senha não pode começar nem terminar com espaço.'
    if _texto_login(senha) == _texto_login(usuario_login):
        return 'A senha não pode ser igual ao seu usuário.'
    if senha.lower() in ('12345678', 'senha123', 'password', 'lello123', '123456789', 'abcd1234'):
        return 'Essa senha é fácil demais de adivinhar. Escolha outra.'
    return ''


def garantir_tabelas_login(conexao):
    """Prepara usuarios_painel para login com senha e cadastra quem já usava o sistema.
    Não toca em produtividade, histórico, carteiras nem em qualquer dado de trabalho."""
    conexao.execute('''CREATE TABLE IF NOT EXISTS usuarios_painel (
        id INTEGER PRIMARY KEY AUTOINCREMENT, nome TEXT UNIQUE, is_master INTEGER DEFAULT 0)''')
    for coluna, tipo in [('usuario_login', 'TEXT'), ('nome_exibicao', 'TEXT'), ('email', 'TEXT'),
                         ('senha_hash', 'TEXT'), ('precisa_trocar_senha', 'INTEGER DEFAULT 1'),
                         ('ativo', 'INTEGER DEFAULT 1'), ('tentativas', 'INTEGER DEFAULT 0'),
                         ('bloqueado_ate', 'TEXT'), ('ultimo_acesso', 'TEXT'),
                         ('token_senha', 'TEXT'), ('token_expira', 'TEXT')]:
        try:
            conexao.execute(f'ALTER TABLE usuarios_painel ADD COLUMN {coluna} {tipo}')
        except sqlite3.OperationalError:
            pass
    conexao.execute('''CREATE TABLE IF NOT EXISTS log_acessos (
        id INTEGER PRIMARY KEY AUTOINCREMENT, data_hora TEXT, usuario_digitado TEXT,
        usuario TEXT, sucesso INTEGER, motivo TEXT, ip TEXT)''')
    conexao.execute('CREATE TABLE IF NOT EXISTS config_sistema (chave TEXT PRIMARY KEY, valor TEXT)')
    ler = conexao.cursor()            # cursor próprio: lê por nome de coluna sem mexer na conexão de quem chamou
    ler.row_factory = sqlite3.Row

    # 1) Todo mundo que já usava o sistema continua existindo, com o mesmo nome de sempre.
    #    'nome' é a chave que o resto do código já consulta (sempre minúsculo), então não muda.
    for login, exibicao, master in COLABORADORES_INICIAIS:
        conexao.execute('INSERT OR IGNORE INTO usuarios_painel (nome, is_master) VALUES (?, ?)',
                        (exibicao.lower(), master))
        if master:
            conexao.execute('UPDATE usuarios_painel SET is_master = 1 WHERE nome = ?', (exibicao.lower(),))

    # 2) Completa o cadastro das linhas que ainda não têm usuário/nome (inclusive as criadas pela tela de usuários)
    mapa_inicial = {login: exibicao for login, exibicao, _ in COLABORADORES_INICIAIS}
    ocupados = {_texto_login(l['usuario_login']) for l in
                ler.execute('SELECT usuario_login FROM usuarios_painel WHERE usuario_login IS NOT NULL')
                if _texto_login(l['usuario_login'])}
    for linha in ler.execute('SELECT id, nome, usuario_login, nome_exibicao FROM usuarios_painel').fetchall():
        base = _texto_login(linha['nome'])
        login = _texto_login(linha['usuario_login'])
        if not login:
            login = base or f"usuario{linha['id']}"
            sufixo = 1
            while login in ocupados:
                sufixo += 1
                login = f'{base}{sufixo}'
            ocupados.add(login)
            conexao.execute('UPDATE usuarios_painel SET usuario_login = ? WHERE id = ?', (login, linha['id']))
        if not str(linha['nome_exibicao'] or '').strip():
            # .title() e nada mais: é o que o login antigo fazia, então o nome sai igualzinho ao do histórico
            exibicao = mapa_inicial.get(login) or ' '.join(str(linha['nome'] or '').split()).title()
            conexao.execute('UPDATE usuarios_painel SET nome_exibicao = ? WHERE id = ?', (exibicao, linha['id']))
    conexao.execute('UPDATE usuarios_painel SET ativo = 1 WHERE ativo IS NULL')
    conexao.execute('UPDATE usuarios_painel SET precisa_trocar_senha = 1 WHERE precisa_trocar_senha IS NULL')
    conexao.execute('UPDATE usuarios_painel SET tentativas = 0 WHERE tentativas IS NULL')
    conexao.commit()

    # 3) Primeira vez: gera uma senha temporária para cada um e grava num arquivo, UMA vez só.
    ja_gerou = ler.execute("SELECT valor FROM config_sistema WHERE chave = 'login_senhas_iniciais'").fetchone()
    pendentes = ler.execute("""SELECT id, nome_exibicao, usuario_login FROM usuarios_painel
                                   WHERE senha_hash IS NULL AND IFNULL(ativo, 1) = 1 ORDER BY id""").fetchall()
    if pendentes and not ja_gerou:
        linhas = []
        for u in pendentes:
            senha = _senha_temporaria()
            conexao.execute('UPDATE usuarios_painel SET senha_hash = ?, precisa_trocar_senha = 1 WHERE id = ?',
                            (generate_password_hash(senha), u['id']))
            linhas.append(f"{(u['nome_exibicao'] or ''):<14} usuário: {(u['usuario_login'] or ''):<12} senha: {senha}")
        caminho = os.path.join(_pasta_do_banco(), 'senhas_primeiro_acesso.txt')
        try:
            with open(caminho, 'w', encoding='utf-8') as arquivo:
                arquivo.write('SENHAS DE PRIMEIRO ACESSO — Portal de Cobranças\n')
                arquivo.write(f'Geradas em {_agora_texto()}\n\n')
                arquivo.write('Entregue a senha de cada pessoa. Na primeira entrada o sistema obriga a trocar.\n')
                arquivo.write('APAGUE ESTE ARQUIVO depois de distribuir.\n\n')
                arquivo.write('\n'.join(linhas) + '\n')
            conexao.execute("INSERT OR REPLACE INTO config_sistema (chave, valor) VALUES ('login_senhas_iniciais', ?)",
                            (_agora_texto(),))
            conexao.commit()
            print(f'🔐 Senhas de primeiro acesso geradas em: {caminho}', flush=True)
            print('   Entregue a cada pessoa e apague o arquivo depois.', flush=True)
        except Exception as erro:
            print(f'🚨 Não consegui gravar o arquivo de senhas iniciais: {erro}', flush=True)


def _buscar_usuario_para_login(conexao, texto):
    """Acha a pessoa pelo que ela digitou: usuário, nome ou e-mail. Aceita com e sem acento."""
    chave = _texto_login(texto)
    if not chave:
        return None
    linha = conexao.execute("SELECT * FROM usuarios_painel WHERE LOWER(TRIM(IFNULL(usuario_login, ''))) = ?",
                            (chave,)).fetchone()
    if linha:
        return linha
    if '@' in str(texto or ''):
        linha = conexao.execute("SELECT * FROM usuarios_painel WHERE LOWER(TRIM(IFNULL(email, ''))) = ?",
                                (str(texto).strip().lower(),)).fetchone()
        if linha:
            return linha
    for candidata in conexao.execute('SELECT * FROM usuarios_painel').fetchall():
        if _texto_login(candidata['nome']) == chave or _texto_login(candidata['nome_exibicao']) == chave:
            return candidata
    # Por último, o jeito antigo: 'ryan.lima@lello.com.br' e 'ryan.lima' viram 'ryan'.
    primeiro_nome = _texto_login(str(texto or '').split('@')[0].split('.')[0])
    if primeiro_nome and primeiro_nome != chave:
        return conexao.execute("SELECT * FROM usuarios_painel WHERE LOWER(TRIM(IFNULL(usuario_login, ''))) = ?",
                               (primeiro_nome,)).fetchone()
    return None


def _ip_do_pedido():
    return (request.headers.get('X-Forwarded-For', '').split(',')[0].strip() or request.remote_addr or '')[:60]


def registrar_acesso(usuario_digitado, usuario, sucesso, motivo=''):
    try:
        conexao = conectar_banco()
        conexao.execute("""INSERT INTO log_acessos (data_hora, usuario_digitado, usuario, sucesso, motivo, ip)
                           VALUES (?, ?, ?, ?, ?, ?)""",
                        (_agora_texto(), str(usuario_digitado or '')[:60], str(usuario or '')[:60],
                         1 if sucesso else 0, str(motivo or '')[:120], _ip_do_pedido()))
        conexao.commit()
        conexao.close()
    except Exception as erro:
        print(f'⚠️ Não consegui registrar o acesso: {erro}', flush=True)


def abrir_sessao(linha):
    """Entra no sistema. session['usuario'] recebe o MESMO nome de sempre."""
    nome_exibicao = str(linha['nome_exibicao'] or linha['nome'] or '').strip() or str(linha['nome'] or '')
    session.clear()
    session.permanent = True
    session['usuario'] = nome_exibicao
    session['usuario_id'] = linha['id']
    session['senha_marca'] = _marca_senha(linha['senha_hash'])


def _pedido_quer_json():
    return (request.is_json or request.args.get('ajax') == 'true'
            or request.path.startswith('/api/')
            or request.headers.get('X-Requested-With') == 'XMLHttpRequest'
            or 'application/json' in (request.headers.get('Accept') or ''))


def _responder_sem_sessao(motivo='entrar'):
    if _pedido_quer_json():
        return jsonify({'sucesso': False, 'sessao_expirada': True,
                        'msg': 'Sua sessão terminou. Entre de novo.'}), 401
    return redirect(url_for('login', **({'motivo': motivo} if motivo else {})))


def usuario_logado():
    """Linha do usuário da sessão, direto do banco (ou None)."""
    if not session.get('usuario_id'):
        return None
    conexao = conectar_banco()
    try:
        return conexao.execute('SELECT * FROM usuarios_painel WHERE id = ?', (session.get('usuario_id'),)).fetchone()
    finally:
        conexao.close()


def login_obrigatorio(f):
    @functools.wraps(f)
    def wrap(*args, **kwargs):
        if not session.get('usuario') or not session.get('usuario_id'):
            return _responder_sem_sessao()
        usuario = usuario_logado()
        # Desligou, desativou ou trocou a senha em outro lugar: a sessão cai na hora
        if usuario is None or int(usuario['ativo'] or 0) != 1 or _marca_senha(usuario['senha_hash']) != session.get('senha_marca'):
            session.clear()
            return _responder_sem_sessao('expirou')
        session['usuario'] = str(usuario['nome_exibicao'] or usuario['nome'] or '').strip()
        if int(usuario['precisa_trocar_senha'] or 0) == 1 and request.endpoint not in ('definir_senha', 'logout', 'static'):
            if _pedido_quer_json():
                return jsonify({'sucesso': False, 'trocar_senha': True,
                                'msg': 'Defina sua nova senha para continuar.'}), 401
            return redirect(url_for('definir_senha'))
        return f(*args, **kwargs)
    return wrap


def gestor_obrigatorio(f):
    @functools.wraps(f)
    @login_obrigatorio
    def wrap(*args, **kwargs):
        if not usuario_eh_gestor(session.get('usuario')):
            if _pedido_quer_json():
                return jsonify({'sucesso': False, 'msg': 'Acesso negado.'}), 403
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return wrap


# ==============================================================================
# 📬 RESPOSTAS DAS ADMINISTRADORAS AO PEDIDO DE LOGIN
# ==============================================================================
# O pedido sai com um código no assunto ([AC-xxxxxxxx]) e o Message-ID fica guardado.
# Quando a administradora responde, o FinMail reconhece a mensagem e manda o texto
# para cá; aqui o texto é lido e vira uma sugestão de credencial para conferência.
# NADA é gravado no cadastro sem alguém clicar em aplicar: senha errada trava o
# portal e derruba o robô para todos os imóveis daquela administradora.

# O que a equipe escreve hoje na coluna Acesso: 'L: PO76NE6471 S: lello123',
# 'l: FA34DO7053 - s: lello123', 'U: 145000709 - S: 109050'. São esses os formatos.
# O valor tem que estar na MESMA linha do rótulo: senão 'Segue o acesso:' engole o
# 'Login:' da linha de baixo e a leitura sai vazia.
_ANTES = r'(?:^|[\s\|>*•\-])'
_VALOR_LOGIN = r'[ \t]*([^\s,;<>|]{3,80})'
_VALOR_SENHA = r'[ \t]*([^\s,;<>|]{3,60})'
_ROTULO_LOGIN = r'(?:login|log in|usu[áa]rio|usuario|user|acesso|e-?mail de acesso)'
_ROTULO_SENHA = r'(?:senha|password|pass|pwd)'
# 'L: PO76NE6471', 'u: 145000709' — abreviado, como a equipe anota na planilha
RE_LOGIN = re.compile(_ANTES + r'(?:' + _ROTULO_LOGIN + r'|l|u)\s*[:\-=]' + _VALOR_LOGIN, re.IGNORECASE)
RE_SENHA = re.compile(_ANTES + r'(?:' + _ROTULO_SENHA + r'|s)\s*[:\-=]' + _VALOR_SENHA, re.IGNORECASE)
# 'o usuário é sindico@predio.com.br e a senha é Abc@2026' — escrito por extenso
RE_LOGIN_PROSA = re.compile(_ANTES + _ROTULO_LOGIN + r'\s+(?:é|eh)' + _VALOR_LOGIN, re.IGNORECASE)
RE_SENHA_PROSA = re.compile(_ANTES + _ROTULO_SENHA + r'\s+(?:é|eh)' + _VALOR_SENHA, re.IGNORECASE)
RE_LINK_PORTAL = re.compile(r'https?://[^\s<>"\']{6,200}', re.IGNORECASE)
RE_CODIGO_PEDIDO = re.compile(r'\[AC-([0-9a-f]{8})\]', re.IGNORECASE)

# Frases que dizem "não existe portal" — o sistema já tem a marca [X] RECEBE POR E-MAIL/WPP
FRASES_SEM_PORTAL = (
    'nao temos portal', 'não temos portal', 'nao possuimos portal', 'não possuímos portal',
    'nao ha portal', 'não há portal', 'sem portal', 'nao trabalhamos com portal',
    'enviamos por e-mail', 'enviamos por email', 'enviado por e-mail', 'enviado por email',
    'mandamos por e-mail', 'mandamos por email', 'enviamos por whatsapp', 'envio por whatsapp',
    'boleto e enviado', 'boleto é enviado', 'nao disponibilizamos acesso', 'não disponibilizamos acesso',
)
FRASES_VAI_MANDAR = (
    'vamos providenciar', 'estamos providenciando', 'em breve', 'assim que possivel',
    'assim que possível', 'encaminharemos', 'vamos verificar', 'estamos verificando',
    'setor responsavel', 'setor responsável', 'aguarde', 'retornaremos',
)
# Palavras que aparecem em RESPOSTA AUTOMÁTICA e não valem nada
FRASES_AUTOMATICAS = ('ausencia temporaria', 'ausência temporária', 'estou de ferias', 'estou de férias',
                      'out of office', 'recebemos sua mensagem', 'protocolo de atendimento',
                      'nao responda este e-mail', 'não responda este e-mail')
# Devolução do servidor de e-mail (endereço errado / caixa cheia)
FRASES_BOUNCE = ('delivery status notification', 'undelivered mail', 'mail delivery failed',
                 'returned to sender', 'address not found', 'nao foi possivel entregar',
                 'não foi possível entregar', 'destinatario desconhecido', 'destinatário desconhecido')

# Coisas que parecem login mas não são (o texto do próprio pedido volta citado na resposta)
LIXO_CREDENCIAL = {'', '-', '--', 'xxx', 'n/a', 'na', 'nao', 'não', 'senha', 'login', 'usuario', 'usuário',
                   'ctrlcond@lello.com.br', 'lello', 'portal', 'acesso', 'segue', 'abaixo', 'anexo'}


def _texto_plano(texto):
    """Tira HTML e normaliza espaços, mantendo as quebras de linha (os dados vêm em linhas)."""
    bruto = re.sub(r'<br\s*/?>|</p>|</div>|</tr>', '\n', str(texto or ''), flags=re.IGNORECASE)
    bruto = re.sub(r'<[^>]+>', ' ', bruto)
    bruto = bruto.replace('&nbsp;', ' ').replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
    linhas = [' '.join(l.split()) for l in bruto.splitlines()]
    return '\n'.join(l for l in linhas if l)


def _sem_o_pedido_original(texto):
    """Corta o trecho citado do nosso próprio pedido, que volta junto na resposta e confunde a leitura."""
    corte = len(texto)
    for marca in ('Solicitação de Login e Senha', 'Para agilizarmos o recebimento',
                  'Em ', '-----Mensagem original', 'De: ', 'Em qua', 'Em seg', 'Em ter', 'Em qui', 'Em sex'):
        achou = texto.find(marca)
        if 0 < achou < corte:
            corte = achou
    return texto[:corte].strip() or texto.strip()


def _limpo(valor):
    v = str(valor or '').strip(' .,;:*|\'"<>()[]')
    return '' if v.lower() in LIXO_CREDENCIAL or len(v) < 3 else v


def _primeiro_valor(texto, regex, regex_prosa):
    """Primeiro valor aproveitável. Devolve (valor, veio_no_formato_padrao)."""
    for padrao, eh_padrao in ((regex, True), (regex_prosa, False)):
        for achado in padrao.finditer(texto):
            valor = _limpo(achado.group(1))
            if valor:
                return valor, eh_padrao
    return '', False


def classificar_resposta_acesso(assunto, corpo):
    """O que esta resposta é: 'credencial', 'sem_portal', 'vai_mandar', 'automatica', 'bounce' ou 'outro'."""
    inteiro = _sem_acento_minusculo(f'{assunto}\n{corpo}')
    if any(f in inteiro for f in (_sem_acento_minusculo(x) for x in FRASES_BOUNCE)):
        return 'bounce'
    if any(f in inteiro for f in (_sem_acento_minusculo(x) for x in FRASES_AUTOMATICAS)):
        return 'automatica'
    if any(f in inteiro for f in (_sem_acento_minusculo(x) for x in FRASES_SEM_PORTAL)):
        return 'sem_portal'
    return ''


def extrair_credenciais_do_texto(assunto, corpo):
    """Lê a resposta e devolve o que dá para aproveitar.
    tipo: credencial | sem_portal | vai_mandar | automatica | bounce | anexo | outro"""
    texto = _sem_o_pedido_original(_texto_plano(corpo))
    achado = {'tipo': 'outro', 'login': '', 'senha': '', 'link': '', 'trecho': texto[:600], 'confianca': 'baixa'}

    tipo_direto = classificar_resposta_acesso(assunto, texto)
    if tipo_direto in ('bounce', 'automatica'):
        achado['tipo'] = tipo_direto
        return achado

    login, login_padrao = _primeiro_valor(texto, RE_LOGIN, RE_LOGIN_PROSA)
    senha, senha_padrao = _primeiro_valor(texto, RE_SENHA, RE_SENHA_PROSA)
    links = [l for l in RE_LINK_PORTAL.findall(texto)
             if 'lello.com.br' not in l.lower() and 'google.com' not in l.lower()]
    achado['login'], achado['senha'], achado['link'] = login, senha, (links[0][:200] if links else '')

    if login and senha:
        achado['tipo'] = 'credencial'
        # 'Login: X  Senha: Y', do jeito que a equipe já anota: é o caso que dá para confiar.
        # Escrito por extenso no meio do texto a leitura erra mais, então fica em média.
        achado['confianca'] = 'alta' if (login_padrao and senha_padrao) else 'media'
    elif tipo_direto == 'sem_portal':
        achado['tipo'] = 'sem_portal'
        achado['confianca'] = 'media'
    elif login or senha:
        achado['tipo'] = 'credencial'          # veio pela metade: a pessoa completa na conferência
        achado['confianca'] = 'baixa'
    elif any(_sem_acento_minusculo(f) in _sem_acento_minusculo(texto) for f in FRASES_VAI_MANDAR):
        achado['tipo'] = 'vai_mandar'
    return achado


def garantir_tabelas_resposta_acesso(conexao):
    for coluna, tipo in [('codigo', 'TEXT'), ('message_id', 'TEXT'), ('assunto', 'TEXT'), ('respondido_em', 'TEXT')]:
        try:
            conexao.execute(f'ALTER TABLE solicitacoes_acesso ADD COLUMN {coluna} {tipo}')
        except sqlite3.OperationalError:
            pass
    conexao.execute('''CREATE TABLE IF NOT EXISTS respostas_acesso (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        codigo TEXT, administradora TEXT, remetente TEXT, assunto TEXT,
        data_hora TEXT, tipo TEXT, confianca TEXT,
        login TEXT, senha TEXT, link TEXT, trecho TEXT, tem_anexo INTEGER DEFAULT 0,
        message_id TEXT, link_gmail TEXT,
        status TEXT DEFAULT 'pendente',          -- pendente | aplicado | descartado
        usuario TEXT, decidido_em TEXT)''')
    conexao.execute('CREATE INDEX IF NOT EXISTS idx_resp_acesso_status ON respostas_acesso(status, id)')
    conexao.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_resp_acesso_msg ON respostas_acesso(message_id)')
    conexao.commit()


def _imoveis_do_pedido(conexao, codigo, administradora):
    """Os imóveis que estavam naquele pedido (ou, sem código, os da administradora que seguem sem acesso)."""
    if codigo:
        linhas = conexao.execute(
            'SELECT DISTINCT imovel FROM solicitacoes_acesso WHERE codigo = ? ORDER BY imovel', (codigo,)).fetchall()
        if linhas:
            return [str(l['imovel']) for l in linhas]
    if administradora:
        linhas = conexao.execute(
            """SELECT DISTINCT imovel FROM solicitacoes_acesso
               WHERE UPPER(TRIM(administradora)) = UPPER(TRIM(?)) ORDER BY data_hora DESC LIMIT 60""",
            (administradora,)).fetchall()
        return [str(l['imovel']) for l in linhas]
    return []


@app.route('/api/automacao/pedidos_acesso', methods=['POST'])
def api_pedidos_acesso():
    """O FinMail pergunta: quais códigos/Message-ID/e-mails são de pedido de login?
    Com isso ele reconhece a resposta sem precisar adivinhar."""
    dados = request.get_json(silent=True) or {}
    if not dados or dados.get('token') != TOKEN_API_ROBO:
        return jsonify({'sucesso': False, 'msg': 'Acesso Negado'}), 403
    conexao = conectar_banco()
    try:
        garantir_tabelas_resposta_acesso(conexao)
        # só os pedidos dos últimos 90 dias: resposta mais velha que isso não vem mais
        limite = (datetime.now() - timedelta(days=90)).strftime('%Y-%m-%d')
        codigos = [r['codigo'] for r in conexao.execute(
            "SELECT DISTINCT codigo FROM solicitacoes_acesso "
            "WHERE IFNULL(codigo, '') != '' AND substr(IFNULL(data_hora, ''), 1, 10) >= ?", (limite,)).fetchall()]
        ids = [r['message_id'] for r in conexao.execute(
            "SELECT DISTINCT message_id FROM solicitacoes_acesso "
            "WHERE IFNULL(message_id, '') != '' AND substr(IFNULL(data_hora, ''), 1, 10) >= ?", (limite,)).fetchall()]
        emails = [r['email'] for r in conexao.execute(
            "SELECT DISTINCT email FROM vinculos_adm WHERE IFNULL(email, '') != ''").fetchall()]
    finally:
        conexao.close()
    enderecos = sorted({e.strip().lower() for linha in emails
                        for e in re.findall(r'[\w.%+-]+@[\w.-]+\.[A-Za-z]{2,}', str(linha or ''))})
    return jsonify({'sucesso': True, 'codigos': codigos, 'message_ids': ids, 'emails_administradoras': enderecos,
                    'assunto_pedido': ASSUNTO_PEDIDO_ACESSO})


@app.route('/api/automacao/resposta_acesso', methods=['POST'])
def api_resposta_acesso():
    """O FinMail manda a resposta crua; a leitura e a classificação são feitas aqui,
    para melhorar os padrões sem precisar mexer no robô."""
    dados = request.get_json(silent=True) or {}
    if not dados or dados.get('token') != TOKEN_API_ROBO:
        return jsonify({'sucesso': False, 'msg': 'Acesso Negado'}), 403

    assunto = str(dados.get('assunto') or '')
    corpo = str(dados.get('corpo') or '')
    remetente = str(dados.get('remetente') or '')[:160]
    message_id = str(dados.get('message_id') or '')[:200]
    codigo = str(dados.get('codigo') or '').lower()
    if not codigo:
        achou = RE_CODIGO_PEDIDO.search(assunto)
        codigo = achou.group(1).lower() if achou else ''

    lido = extrair_credenciais_do_texto(assunto, corpo)
    if dados.get('tem_anexo') and lido['tipo'] == 'outro':
        lido['tipo'] = 'anexo'

    conexao = conectar_banco()
    try:
        garantir_tabelas_resposta_acesso(conexao)
        if message_id and conexao.execute('SELECT 1 FROM respostas_acesso WHERE message_id = ?', (message_id,)).fetchone():
            return jsonify({'sucesso': True, 'repetida': True})

        administradora = str(dados.get('administradora') or '')
        if not administradora:
            endereco = (parseaddr(remetente)[1] or '').strip().lower()
            if endereco:
                linha = conexao.execute(
                    "SELECT administradora FROM vinculos_adm WHERE LOWER(IFNULL(email, '')) LIKE ? LIMIT 1",
                    (f'%{endereco}%',)).fetchone()
                administradora = linha['administradora'] if linha else ''
        if not administradora and codigo:
            linha = conexao.execute('SELECT administradora FROM solicitacoes_acesso WHERE codigo = ? LIMIT 1',
                                    (codigo,)).fetchone()
            administradora = linha['administradora'] if linha else ''

        conexao.execute("""INSERT INTO respostas_acesso
            (codigo, administradora, remetente, assunto, data_hora, tipo, confianca, login, senha, link, trecho,
             tem_anexo, message_id, link_gmail, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pendente')""",
            (codigo, administradora, remetente, assunto[:200], _agora_str(), lido['tipo'], lido['confianca'],
             lido['login'], lido['senha'], lido['link'], lido['trecho'], 1 if dados.get('tem_anexo') else 0,
             message_id, str(dados.get('link_gmail') or '')[:400]))
        if codigo:
            conexao.execute("UPDATE solicitacoes_acesso SET respondido_em = ? WHERE codigo = ?", (_agora_str(), codigo))
        conexao.commit()
    finally:
        conexao.close()
    print(f"📬 Resposta de acesso recebida ({lido['tipo']}, confiança {lido['confianca']}) de {remetente}", flush=True)
    return jsonify({'sucesso': True, 'tipo': lido['tipo'], 'confianca': lido['confianca'],
                    'login': lido['login'], 'senha': lido['senha']})


@app.route('/respostas_acesso')
@login_obrigatorio
def tela_respostas_acesso():
    conexao = conectar_banco()
    try:
        garantir_tabelas_resposta_acesso(conexao)
        mostrar = request.args.get('status', 'pendente')
        if mostrar not in ('pendente', 'aplicado', 'descartado', 'todas'):
            mostrar = 'pendente'
        onde = '' if mostrar == 'todas' else 'WHERE status = ?'
        parametros = () if mostrar == 'todas' else (mostrar,)
        respostas = [dict(r) for r in conexao.execute(
            f'SELECT * FROM respostas_acesso {onde} ORDER BY id DESC LIMIT 200', parametros).fetchall()]
        for r in respostas:
            r['imoveis'] = _imoveis_do_pedido(conexao, r.get('codigo'), r.get('administradora'))
        contagem = {linha['status']: linha['q'] for linha in conexao.execute(
            'SELECT status, COUNT(*) q FROM respostas_acesso GROUP BY status').fetchall()}
    finally:
        conexao.close()
    return render_template('respostas_acesso.html', respostas=respostas, contagem=contagem, mostrar=mostrar)


@app.route('/api/acessos_resposta/decidir', methods=['POST'])
@login_obrigatorio
def api_decidir_resposta_acesso():
    """Aplica a credencial nos imóveis escolhidos (ou só descarta a resposta)."""
    dados = request.get_json(silent=True) or {}
    acao = str(dados.get('acao') or '').lower()
    usuario = session.get('usuario', 'Sistema')
    conexao = conectar_banco()
    try:
        garantir_tabelas_resposta_acesso(conexao)
        resposta = conexao.execute('SELECT * FROM respostas_acesso WHERE id = ?', (dados.get('id'),)).fetchone()
        if resposta is None:
            return jsonify({'sucesso': False, 'msg': 'Resposta não encontrada.'}), 404
        if resposta['status'] != 'pendente':
            return jsonify({'sucesso': False, 'msg': 'Esta resposta já foi resolvida.'}), 400

        if acao == 'descartar':
            conexao.execute("UPDATE respostas_acesso SET status = 'descartado', usuario = ?, decidido_em = ? WHERE id = ?",
                            (usuario, _agora_str(), resposta['id']))
            conexao.commit()
            return jsonify({'sucesso': True})

        if acao != 'aplicar':
            return jsonify({'sucesso': False, 'msg': 'Ação desconhecida.'}), 400

        imoveis = [str(i).strip() for i in (dados.get('imoveis') or []) if str(i).strip()]
        if not imoveis:
            return jsonify({'sucesso': False, 'msg': 'Escolha ao menos um imóvel.'}), 400

        sem_portal = str(dados.get('tipo') or resposta['tipo']) == 'sem_portal'
        login = '[X] RECEBE POR E-MAIL/WPP' if sem_portal else str(dados.get('login') or resposta['login'] or '').strip()
        senha = '' if sem_portal else str(dados.get('senha') or resposta['senha'] or '').strip()
        link = '' if sem_portal else str(dados.get('link') or resposta['link'] or '').strip()
        if not login:
            return jsonify({'sucesso': False, 'msg': 'Sem login para gravar.'}), 400

        agora_tela = datetime.now().strftime('%d/%m/%Y %H:%M')
        aplicados = []
        for imovel in imoveis:
            existe = conexao.execute('SELECT 1 FROM cadastro_mestre_imoveis WHERE imovel = ?', (imovel,)).fetchone()
            if not existe:
                continue
            conexao.execute("""UPDATE cadastro_mestre_imoveis
                SET login_acesso = ?, senha_acesso = ?, cred_user = ?, cred_data = ?,
                    link_portal = CASE WHEN ? != '' THEN ? ELSE link_portal END
                WHERE imovel = ?""", (login, senha, usuario, agora_tela, link, link, imovel))
            conexao.execute('INSERT INTO historico_credenciais (imovel, data_hora, usuario, texto) VALUES (?, ?, ?, ?)',
                            (imovel, agora_tela, usuario,
                             f"Resposta da administradora aplicada: {'sem portal' if sem_portal else 'login e senha'}"))
            aplicados.append(imovel)

        conexao.execute("UPDATE respostas_acesso SET status = 'aplicado', usuario = ?, decidido_em = ?, login = ?, senha = ?, link = ? WHERE id = ?",
                        (usuario, _agora_str(), login, senha, link, resposta['id']))
        conexao.commit()
    finally:
        conexao.close()
    return jsonify({'sucesso': True, 'aplicados': aplicados, 'qtd': len(aplicados)})


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'GET':
        if session.get('usuario_id') and usuario_logado() is not None:
            return redirect(url_for('index'))
        avisos = {'expirou': 'Sua sessão terminou. Entre de novo.',
                  'saiu': 'Você saiu do sistema.',
                  'senha_alterada': 'Senha alterada. Entre com a nova.'}
        return render_template('login.html', aviso=avisos.get(request.args.get('motivo', '')))

    dados = request.get_json(silent=True) or request.form
    digitado = str(dados.get('usuario') or '').strip()
    senha = str(dados.get('senha') or '')

    def recusar(mensagem, motivo='', codigo=401, extra=None):
        registrar_acesso(digitado, '', False, motivo or mensagem)
        if _pedido_quer_json():
            corpo = {'sucesso': False, 'msg': mensagem}
            corpo.update(extra or {})
            return jsonify(corpo), codigo
        return render_template('login.html', erro=mensagem), codigo

    if not digitado or not senha:
        return recusar('Preencha usuário e senha.', 'campos vazios', 400)

    conexao = conectar_banco()
    try:
        usuario = _buscar_usuario_para_login(conexao, digitado)
        if usuario is None:
            return recusar('Usuário ou senha incorretos.', 'usuário não encontrado')
        if int(usuario['ativo'] or 0) != 1:
            return recusar('Este acesso está desativado. Fale com o gestor.', 'usuário desativado', 403)
        if not usuario['senha_hash']:
            return recusar('Este usuário ainda não tem senha. Peça ao gestor para gerar a sua.', 'sem senha', 403)

        bloqueado_ate = str(usuario['bloqueado_ate'] or '')
        if bloqueado_ate and bloqueado_ate > _agora_texto():
            falta = max(1, int((datetime.strptime(bloqueado_ate, '%Y-%m-%d %H:%M:%S') - datetime.now()).total_seconds() // 60) + 1)
            return recusar(f'Muitas tentativas. Tente de novo em {falta} minuto(s).', 'bloqueado', 429)

        if not check_password_hash(usuario['senha_hash'], senha):
            tentativas = int(usuario['tentativas'] or 0) + 1
            if tentativas >= MAX_TENTATIVAS_LOGIN:
                ate = (datetime.now() + timedelta(minutes=MINUTOS_BLOQUEIO_LOGIN)).strftime('%Y-%m-%d %H:%M:%S')
                conexao.execute('UPDATE usuarios_painel SET tentativas = 0, bloqueado_ate = ? WHERE id = ?', (ate, usuario['id']))
                conexao.commit()
                return recusar(f'Muitas tentativas. O acesso ficou travado por {MINUTOS_BLOQUEIO_LOGIN} minutos.', 'bloqueou', 429)
            conexao.execute('UPDATE usuarios_painel SET tentativas = ? WHERE id = ?', (tentativas, usuario['id']))
            conexao.commit()
            restam = MAX_TENTATIVAS_LOGIN - tentativas
            nunca_entrou = not str(usuario['ultimo_acesso'] or '').strip() and int(usuario['precisa_trocar_senha'] or 0) == 1
            aviso = ('Esta é sua primeira entrada: use a senha temporária que o gestor entregou '
                     '(o sistema a gravou no arquivo senhas_primeiro_acesso.txt, na pasta do banco.db).'
                     if nunca_entrou else 'Usuário ou senha incorretos.')
            return recusar(aviso, 'senha errada', 401, {'restam': restam})

        conexao.execute("""UPDATE usuarios_painel SET tentativas = 0, bloqueado_ate = NULL, ultimo_acesso = ?,
                           token_senha = NULL, token_expira = NULL WHERE id = ?""", (_agora_texto(), usuario['id']))
        conexao.commit()
        usuario = conexao.execute('SELECT * FROM usuarios_painel WHERE id = ?', (usuario['id'],)).fetchone()
    finally:
        conexao.close()

    abrir_sessao(usuario)
    registrar_acesso(digitado, session.get('usuario'), True, 'entrou')
    destino = url_for('definir_senha') if int(usuario['precisa_trocar_senha'] or 0) == 1 else url_for('index')
    if _pedido_quer_json():
        return jsonify({'sucesso': True, 'destino': destino})
    return redirect(destino)


@app.route('/api/acesso/esqueci_senha', methods=['POST'])
def api_esqueci_senha():
    """Manda o link de nova senha para o e-mail cadastrado. A resposta é sempre a mesma,
    para a tela de login não virar uma lista de quem tem conta aqui."""
    dados = request.get_json(silent=True) or request.form
    digitado = str(dados.get('usuario') or '').strip()
    resposta = {'sucesso': True, 'msg': 'Se houver uma conta com e-mail cadastrado, o link chega em alguns minutos. '
                                        'Se não chegar, fale com o gestor.'}
    if not digitado:
        return jsonify({'sucesso': False, 'msg': 'Digite seu usuário primeiro.'}), 400

    conexao = conectar_banco()
    try:
        usuario = _buscar_usuario_para_login(conexao, digitado)
        email = str((usuario['email'] if usuario else '') or '').strip()
        if usuario is None or int(usuario['ativo'] or 0) != 1 or '@' not in email:
            registrar_acesso(digitado, '', False, 'esqueci a senha sem e-mail cadastrado')
            return jsonify(resposta)
        token = secrets.token_urlsafe(32)
        expira = (datetime.now() + timedelta(hours=HORAS_VALIDADE_LINK_SENHA)).strftime('%Y-%m-%d %H:%M:%S')
        conexao.execute('UPDATE usuarios_painel SET token_senha = ?, token_expira = ? WHERE id = ?',
                        (_hash_token(token), expira, usuario['id']))
        conexao.commit()
        nome = str(usuario['nome_exibicao'] or usuario['nome'] or '').strip()
    finally:
        conexao.close()

    link = url_for('definir_senha', t=token, _external=True)
    corpo = (f"<p>Olá, {_esc_html(nome)}!</p>"
             f"<p>Chegou um pedido de nova senha para o seu acesso ao <b>Portal de Cobranças</b>.</p>"
             f"<p><a href='{link}' style=\"background:#c20332;color:#fff;padding:12px 22px;border-radius:8px;"
             f"text-decoration:none;font-weight:600;display:inline-block\">Criar nova senha</a></p>"
             f"<p style='color:#6b5c62;font-size:13px'>O link vale por {HORAS_VALIDADE_LINK_SENHA} hora. "
             f"Se não foi você quem pediu, pode ignorar este e-mail: sua senha continua a mesma.</p>"
             f"<p style='color:#6b5c62;font-size:12px'>{_esc_html(link)}</p>")
    enviar_email_smtp(email, 'Nova senha — Portal de Cobranças', corpo)
    registrar_acesso(digitado, nome, True, 'pediu link de nova senha')
    return jsonify(resposta)


@app.route('/definir_senha', methods=['GET', 'POST'])
def definir_senha():
    """Mesma tela para três casos: primeira senha, troca obrigatória e link do 'esqueci minha senha'."""
    _entrada = request.get_json(silent=True) or {}
    token = str(request.values.get('t') or _entrada.get('t') or '').strip()
    conexao = conectar_banco()
    try:
        usuario = None
        if token:
            usuario = conexao.execute("""SELECT * FROM usuarios_painel WHERE token_senha = ?
                                         AND IFNULL(token_expira, '') > ?""",
                                      (_hash_token(token), _agora_texto())).fetchone()
            if usuario is None:
                return render_template('definir_senha.html', link_invalido=True), 400
        elif session.get('usuario_id'):
            usuario = conexao.execute('SELECT * FROM usuarios_painel WHERE id = ?', (session.get('usuario_id'),)).fetchone()
        if usuario is None or int(usuario['ativo'] or 0) != 1:
            session.clear()
            return redirect(url_for('login'))

        nome = str(usuario['nome_exibicao'] or usuario['nome'] or '').strip()
        primeira_vez = int(usuario['precisa_trocar_senha'] or 0) == 1
        if request.method == 'GET':
            return render_template('definir_senha.html', nome=nome, token=token,
                                   primeira_vez=primeira_vez, email_atual=str(usuario['email'] or ''),
                                   minimo=TAMANHO_MINIMO_SENHA)

        dados = request.get_json(silent=True) or request.form
        nova = str(dados.get('senha') or '')
        confirma = str(dados.get('confirma') or '')
        email = str(dados.get('email') or '').strip()

        def recusar(mensagem):
            if _pedido_quer_json():
                return jsonify({'sucesso': False, 'msg': mensagem}), 400
            return render_template('definir_senha.html', nome=nome, token=token, primeira_vez=primeira_vez,
                                   email_atual=email or str(usuario['email'] or ''), minimo=TAMANHO_MINIMO_SENHA,
                                   erro=mensagem), 400

        if nova != confirma:
            return recusar('As duas senhas não são iguais.')
        problema = problema_na_senha(nova, usuario['usuario_login'])
        if problema:
            return recusar(problema)
        if email and not re.match(r'^[^@\s]+@[^@\s]+\.[a-zA-Z]{2,}$', email):
            return recusar('Esse e-mail não parece válido.')
        if primeira_vez and not email and not str(usuario['email'] or '').strip():
            return recusar('Informe seu e-mail de trabalho: é por ele que você recupera a senha depois.')

        conexao.execute("""UPDATE usuarios_painel SET senha_hash = ?, precisa_trocar_senha = 0, tentativas = 0,
                           bloqueado_ate = NULL, token_senha = NULL, token_expira = NULL,
                           email = CASE WHEN ? != '' THEN ? ELSE email END WHERE id = ?""",
                        (generate_password_hash(nova), email, email, usuario['id']))
        conexao.commit()
        usuario = conexao.execute('SELECT * FROM usuarios_painel WHERE id = ?', (usuario['id'],)).fetchone()
    finally:
        conexao.close()

    abrir_sessao(usuario)
    registrar_acesso(usuario['usuario_login'], nome, True, 'definiu nova senha')
    if _pedido_quer_json():
        return jsonify({'sucesso': True, 'destino': url_for('index')})
    return redirect(url_for('index'))


@app.route('/logout')
def logout():
    saiu = session.get('usuario')
    session.clear()
    if saiu:
        registrar_acesso(saiu, saiu, True, 'saiu')
    return redirect(url_for('login', motivo='saiu'))


# ------------------------------------------------------------------------------
# Tela do gestor: senhas, e-mail e quem pode entrar
# ------------------------------------------------------------------------------
@app.route('/acessos')
@gestor_obrigatorio
def tela_acessos():
    conexao = conectar_banco()
    try:
        pessoas = [dict(linha) for linha in conexao.execute(
            """SELECT id, nome_exibicao, usuario_login, email, is_master, ativo, ultimo_acesso,
                      precisa_trocar_senha, CASE WHEN senha_hash IS NULL THEN 0 ELSE 1 END AS tem_senha
               FROM usuarios_painel ORDER BY IFNULL(ativo, 1) DESC, LOWER(IFNULL(nome_exibicao, nome))""")]
        acessos = [dict(linha) for linha in conexao.execute(
            'SELECT data_hora, usuario, usuario_digitado, sucesso, motivo, ip FROM log_acessos ORDER BY id DESC LIMIT 40')]
    finally:
        conexao.close()
    return render_template('acessos.html', pessoas=pessoas, acessos=acessos, eu=session.get('usuario'))


@app.route('/api/acessos/gerar_senha', methods=['POST'])
@gestor_obrigatorio
def api_acessos_gerar_senha():
    dados = request.get_json(silent=True) or {}
    conexao = conectar_banco()
    try:
        usuario = conexao.execute('SELECT * FROM usuarios_painel WHERE id = ?', (dados.get('id'),)).fetchone()
        if usuario is None:
            return jsonify({'sucesso': False, 'msg': 'Usuário não encontrado.'}), 404
        senha = _senha_temporaria()
        conexao.execute("""UPDATE usuarios_painel SET senha_hash = ?, precisa_trocar_senha = 1, tentativas = 0,
                           bloqueado_ate = NULL, token_senha = NULL, token_expira = NULL WHERE id = ?""",
                        (generate_password_hash(senha), usuario['id']))
        conexao.commit()
        nome = str(usuario['nome_exibicao'] or usuario['nome'] or '')
        email = str(usuario['email'] or '').strip()
    finally:
        conexao.close()

    enviada = False
    if '@' in email:
        enviada = bool(enviar_email_smtp(
            email, 'Sua senha de acesso — Portal de Cobranças',
            f"<p>Olá, {_esc_html(nome)}!</p><p>Sua senha temporária do <b>Portal de Cobranças</b> é:</p>"
            f"<p style='font-size:20px;font-weight:700;letter-spacing:1px'>{_esc_html(senha)}</p>"
            f"<p>Entre com o usuário <b>{_esc_html(str(usuario['usuario_login'] or ''))}</b>. "
            f"O sistema vai pedir que você troque por uma senha sua.</p>"))
    registrar_acesso(session.get('usuario'), nome, True, 'gestor gerou senha temporária')
    return jsonify({'sucesso': True, 'senha': senha, 'email_enviado': enviada, 'email': email})


@app.route('/api/acessos/salvar_email', methods=['POST'])
@gestor_obrigatorio
def api_acessos_salvar_email():
    dados = request.get_json(silent=True) or {}
    email = str(dados.get('email') or '').strip()
    if email and not re.match(r'^[^@\s]+@[^@\s]+\.[a-zA-Z]{2,}$', email):
        return jsonify({'sucesso': False, 'msg': 'Esse e-mail não parece válido.'}), 400
    conexao = conectar_banco()
    try:
        conexao.execute('UPDATE usuarios_painel SET email = ? WHERE id = ?', (email, dados.get('id')))
        conexao.commit()
    finally:
        conexao.close()
    return jsonify({'sucesso': True})


@app.route('/api/acessos/ativar', methods=['POST'])
@gestor_obrigatorio
def api_acessos_ativar():
    dados = request.get_json(silent=True) or {}
    ativo = 1 if dados.get('ativo') else 0
    conexao = conectar_banco()
    try:
        usuario = conexao.execute('SELECT * FROM usuarios_painel WHERE id = ?', (dados.get('id'),)).fetchone()
        if usuario is None:
            return jsonify({'sucesso': False, 'msg': 'Usuário não encontrado.'}), 404
        if int(usuario['id']) == int(session.get('usuario_id') or 0) and not ativo:
            return jsonify({'sucesso': False, 'msg': 'Você não pode desativar o seu próprio acesso.'}), 400
        conexao.execute('UPDATE usuarios_painel SET ativo = ? WHERE id = ?', (ativo, usuario['id']))
        conexao.commit()
        nome = str(usuario['nome_exibicao'] or usuario['nome'] or '')
    finally:
        conexao.close()
    registrar_acesso(session.get('usuario'), nome, True, 'ativou o acesso' if ativo else 'desativou o acesso')
    return jsonify({'sucesso': True})

def _preparar_vinculos_norm(conexao):
    """⚡ PERFORMANCE DA ABA PENDÊNCIAS
    O JOIN antigo (TRIM(UPPER(i.administradora)) = TRIM(UPPER(v.administradora))) não consegue usar índice:
    o SQLite varria as ~750 linhas de vinculos_adm PARA CADA boleto (≈1 s por consulta, 3 consultas por tela).
    Aqui montamos, 1x por requisição (~1 ms), uma tabela TEMP com a chave já normalizada e indexada (PRIMARY KEY),
    com UMA linha por administradora. Se houver vínculos duplicados (ex.: '8A CONDOMíNIOS' x '8A Condomínios'),
    vence o que tem responsável de verdade (não 'Não Atribuído'), e depois o mais antigo.
    Isso também evita que o mesmo boleto apareça 2x na tela."""
    _registrar_funcoes_sql(conexao)
    conexao.execute("DROP TABLE IF EXISTS temp.vinc_norm")
    conexao.execute("CREATE TEMP TABLE vinc_norm (chave TEXT PRIMARY KEY, responsavel TEXT, email TEXT)")
    # a chave é a MESMA de chave_adm() (sem acento): 'Administração' e 'ADMINISTRACAO' caem na mesma linha
    conexao.executemany(
        "INSERT OR IGNORE INTO temp.vinc_norm (chave, responsavel, email) VALUES (?, ?, ?)",
        [(normalizar_texto(r[0]), r[1], r[2]) for r in conexao.execute(
            "SELECT administradora, responsavel, email FROM vinculos_adm "
            "ORDER BY CASE WHEN responsavel IS NULL OR TRIM(responsavel) = '' OR TRIM(responsavel) = 'Não Atribuído' THEN 1 ELSE 0 END, "
            "rowid").fetchall() if normalizar_texto(r[0])])


def _sem_acento_minusculo(texto):
    """Normaliza para busca (sem acento, minúsculo) — registrada no SQLite como norm()."""
    if texto is None:
        return ''
    decomposto = unicodedata.normalize('NFD', str(texto))
    return ''.join(ch for ch in decomposto if not unicodedata.combining(ch)).lower()


def resumo_carteira_do_topo(conexao, usuario_atual):
    """Os quatro números da faixa do topo: tamanho da carteira de quem está logado, quanto falta,
    quanto já foi resolvido e quantos boletos a pessoa coletou HOJE.
    Os três primeiros são uma foto da planilha de agora; o último é o trabalho do dia da pessoa.
    Quem não tem carteira (gestor, por exemplo) vê a base inteira."""
    eu = str(usuario_atual or '').strip().lower()
    base_sql = """SELECT COUNT(*) AS total,
                        SUM(CASE WHEN UPPER(TRIM(IFNULL(i.status_interno, ''))) IN {status} THEN 1 ELSE 0 END) AS resolvidos
                 FROM imoveis i LEFT JOIN temp.vinc_norm v ON v.chave = chave_adm(i.administradora)
                 WHERE i.ativo = 1 AND i.administradora IS NOT NULL AND TRIM(i.administradora) != ''
                 {uma}""".format(status=SQL_STATUS_COLETADO, uma=SQL_UMA_LINHA_POR_BOLETO)
    try:
        linha = conexao.execute(base_sql + " AND LOWER(TRIM(IFNULL(v.responsavel, ''))) = ?", (eu,)).fetchone()
        minha = bool(linha and linha['total'])
        if not minha:                                  # sem carteira própria: mostra a operação inteira
            linha = conexao.execute(base_sql).fetchone()
        total = int(linha['total'] or 0)
        resolvidos = int(linha['resolvidos'] or 0)
        marcas = ', '.join('?' * len(ACOES_ENTREGA_BOLETO))
        coletei = conexao.execute(
            f"""SELECT COUNT(DISTINCT i.id)
                FROM produtividade p JOIN imoveis i ON i.id = p.imovel_id
                WHERE p.acao IN ({marcas}) AND LOWER(TRIM(IFNULL(p.usuario, ''))) = ?
                  AND substr(p.data_hora, 1, 10) = ?""",
            ACOES_ENTREGA_BOLETO + (eu, datetime.now().strftime('%Y-%m-%d'))).fetchone()[0]
    except Exception as erro:
        print(f'⚠️ Não consegui montar os números do topo: {erro}', flush=True)
        return None
    return {'minha': minha, 'total': total, 'resolvidos': resolvidos, 'pendentes': max(0, total - resolvidos),
            'coletei_hoje': int(coletei or 0), 'pct': round(resolvidos * 100.0 / total, 1) if total else 0.0}


# ==============================================================================
# 🤖 AUTOMAÇÃO FICA OCULTA NA LISTA ATÉ PERTO DO VENCIMENTO
# ==============================================================================
# Para ninguém mexer num caso que o robô ainda vai coletar sozinho, os imóveis de portal automático ficam fora da
# lista de pendências. Eles aparecem quando:
#   1) falta pouco para o vencimento (DIAS_PARA_APARECER dias ou menos, ou já venceu); ou
#   2) o robô tentou coletar e deu erro (é o caso que está no Raio-X RPA): aparece com um aviso.
# Caso já resolvido (coletado/pago) nunca é escondido. Quem busca um imóvel pelo número vê ele sempre, e o botão
# "Ver ocultos" mostra tudo. Hoje: Webware, Winker e CTRL; para incluir outro portal, é só somar à tupla abaixo
# (ou gravar 'ocultar_auto_portais' = 'WINKER,WEBWARE,CTRL,SUPERLOGICA' na config_sistema; 'ocultar_auto_dias' muda os 10 dias;
# 'ocultar_auto_ativo' = '0' desliga tudo).
#
# O ROBÔ DE E-MAIL (FinMail) pega boleto de qualquer portal, não só do CTRL. Por isso a regra do histórico vale para
# QUALQUER imóvel (CTRL, Autônoma, WhatsApp, Diversos, Superlógica, sem portal...): o imóvel só conta como automação quando
# o robô de e-mail vem coletando o boleto dele há pelo menos 2 meses SEGUIDOS até o último mês de vencimento fechado
# (base de dezembro: nov e out; se pegou também set, ago e jul, melhor ainda), e ninguém da equipe teve de coletar à mão
# nesses meses. O histórico olhado vai até 5 meses para trás e o aviso do caso mostra "o robô coletou X dos últimos 5
# meses". Pego só 1 mês, ou pegou antes mas parou, ou imóvel novo: continua na lista normal.
# Winker e Webware seguem como sempre: ficam ocultos pelo portal, sem exigir histórico.
# O STATUS "AUTOMAÇÃO" segue a mesma prova: no upload da base (e uma vez ao subir esta versão) só recebe AUTOMAÇÃO o imóvel
# de Winker/Webware (portais com robô) ou o que o robô de e-mail já pega sozinho (CTRL, Autônoma, WhatsApp, Diversos, sem
# portal...). Quem não tem comprovação fica em BRANCO (ter login cadastrado não basta); Superlógica sempre em branco.
# Isso não impede o robô: as filas dos robôs aceitam qualquer status que não seja resolvido/CHEQUE-DOC.
# (config_sistema: 'ocultar_auto_ctrl_meses' = meses seguidos exigidos, padrão 2 (0 = esconde todo CTRL e usa 2 para os
#  demais portais); 'ocultar_auto_ctrl_janela' = quantos meses olhar para trás, padrão 5;
#  'ocultar_auto_email_qualquer_portal' = '0' volta a valer só para o CTRL.)
# IMPORTANTE: ocultar vale só para a LISTA de pendências. O radar de emissão do dia, o contador "Emissões Previstas Hoje",
# o disparo diário de e-mail de cobrança (RPA) e o disparo manual NÃO usam o oculto: caso de CTRL oculto continua recebendo
# a cobrança por e-mail (a administradora costuma responder no próprio e-mail e o robô pega a resposta).
PORTAIS_OCULTOS_ATE_VENCER = ('WINKER', 'WEBWARE', 'CTRL')
MESES_SEGUIDOS_DO_ROBO = {'CTRL': 2}
JANELA_HISTORICO_ROBO = 5
_CACHE_ROBO_SEGUIDO = {'chave': None, 'quando': 0.0, 'valor': {}}
DIAS_PARA_APARECER = 10
# O robô de e-mail avisa assim que não deu certo; o caso volta para a lista com o aviso (como o erro do Raio-X nos outros portais)
STATUS_PROBLEMA_DO_ROBO_EMAIL = {
    'EMAIL RECEBIDO': 'o e-mail chegou, mas sem o PDF do boleto',
    'ERRO DE REDE': 'não conseguiu gravar o boleto na rede',
    'ERRO PAGAMENTO': 'erro ao pagar o boleto',
}


def config_ocultar_automacao(conexao):
    """(ativo, portais normalizados, dias) — padrão do código, com ajuste opcional na config_sistema."""
    ativo, portais, dias = True, tuple(PORTAIS_OCULTOS_ATE_VENCER), DIAS_PARA_APARECER
    try:
        cfg = {r['chave']: r['valor'] for r in conexao.execute(
            "SELECT chave, valor FROM config_sistema WHERE chave IN "
            "('ocultar_auto_ativo', 'ocultar_auto_portais', 'ocultar_auto_dias')").fetchall()}
        if str(cfg.get('ocultar_auto_ativo', '1')).strip() == '0':
            ativo = False
        if str(cfg.get('ocultar_auto_portais') or '').strip():
            portais = tuple(_portal_normal(x) for x in str(cfg['ocultar_auto_portais']).split(',') if x.strip())
        if str(cfg.get('ocultar_auto_dias') or '').strip().isdigit():
            dias = int(cfg['ocultar_auto_dias'])
    except Exception:
        pass
    return ativo, tuple(_portal_normal(p) for p in portais), dias


def _rotulo_portal_auto(portal):
    chave = _portal_normal(portal)
    return 'CTRL' if chave == 'CTRL' else str(portal or '').strip().title()


def meses_seguidos_exigidos(conexao, portal):
    """Quantos meses seguidos o robô precisa ter coletado para o portal contar como automação (0 = não exige nada)."""
    n = MESES_SEGUIDOS_DO_ROBO.get(_portal_normal(portal), 0)
    if _portal_normal(portal) == 'CTRL':
        try:
            r = conexao.execute("SELECT valor FROM config_sistema WHERE chave = 'ocultar_auto_ctrl_meses'").fetchone()
            if r and str(r['valor'] or '').strip().isdigit():
                n = int(r['valor'])
        except Exception:
            pass
    return n


def regra_do_robo_para_qualquer_portal(conexao):
    """(ligada, meses seguidos exigidos): o robô de e-mail também esconde imóvel de outro portal (Autônoma, WhatsApp,
    Diversos, Superlógica, sem portal...) quando o pegou pelo menos N meses seguidos. N é o mesmo número do CTRL
    (padrão 2); se o CTRL estiver em 0 (esconde tudo), os outros portais usam 2."""
    ligada = True
    try:
        r = conexao.execute("SELECT valor FROM config_sistema WHERE chave = 'ocultar_auto_email_qualquer_portal'").fetchone()
        if r and str(r['valor'] or '').strip() == '0':
            ligada = False
    except Exception:
        pass
    return ligada, (meses_seguidos_exigidos(conexao, 'CTRL') or MESES_SEGUIDOS_DO_ROBO['CTRL'])


def janela_historico_robo(conexao):
    """Quantos meses de vencimento olhar para trás (padrão 5)."""
    n = JANELA_HISTORICO_ROBO
    try:
        r = conexao.execute("SELECT valor FROM config_sistema WHERE chave = 'ocultar_auto_ctrl_janela'").fetchone()
        if r and str(r['valor'] or '').strip().isdigit() and int(r['valor']) > 0:
            n = int(r['valor'])
    except Exception:
        pass
    return n


def texto_portais_ocultos(conexao):
    """'Winker, Webware e CTRL — CTRL só com 3 meses seguidos coletado pelo robô' (vai no aviso da lista)."""
    nomes, regras = [], []
    for p in config_ocultar_automacao(conexao)[1]:
        nome = _rotulo_portal_auto(p)
        nomes.append(nome)
        n = meses_seguidos_exigidos(conexao, p)
        if n:
            regras.append(f"{nome} só com pelo menos {n} {'mês seguido' if n == 1 else 'meses seguidos'} coletado pelo robô")
    texto = nomes[0] if len(nomes) == 1 else ', '.join(nomes[:-1]) + ' e ' + nomes[-1] if nomes else ''
    ligada, n_geral = regra_do_robo_para_qualquer_portal(conexao)
    if ligada and nomes:
        regras.append(f"qualquer outro portal que o robô de e-mail coletou pelo menos {n_geral} "
                      f"{'mês seguido' if n_geral == 1 else 'meses seguidos'}")
    return texto + (' — ' + '; '.join(regras) if regras else '')


def meses_anteriores(alvo, quantidade):
    """(2026, 12), 3 -> ['2026-11', '2026-10', '2026-09']."""
    ano, mes = alvo
    saida = []
    for _ in range(quantidade):
        mes -= 1
        if mes == 0:
            ano, mes = ano - 1, 12
        saida.append(f'{ano:04d}-{mes:02d}')
    return saida


def imoveis_que_o_robo_pega_sozinho(conexao, minimo, janela, alvo=None):
    """{chave do imóvel (sem zeros à esquerda): (meses seguidos, meses pegos na janela)} dos imóveis cujo boleto o ROBÔ DE E-MAIL
    vem coletando há pelo menos `minimo` meses SEGUIDOS, contando para trás a partir do último mês de vencimento fechado
    (o mês anterior ao da base ativa) e olhando até `janela` meses.
    'Pego pelo robô' num mês = o último 'COLETOU BOLETO' de cada boleto (imóvel + vencimento) daquele mês foi do robô de
    e-mail e ninguém da equipe coletou à mão no mês (como no painel de produtividade). PAGO depois da coleta não atrapalha
    (o crédito da coleta fica); coleta desfeita (o status voltou) não conta; mês sem boleto na base quebra a sequência.
    `alvo` (ano, mes) troca o mês de referência (o upload de uma base nova conta a partir do mês que está entrando).
    Devolve None quando não há exigência (minimo <= 0)."""
    if minimo <= 0:
        return None
    if not alvo:
        alvo, _, _ = mes_alvo_da_base_ativa(conexao)
    if not alvo:
        return {}
    janela = max(janela, minimo)
    meses = meses_anteriores(alvo, janela)                 # do mais recente para o mais antigo
    # Pesado (lê todas as coletas): guarda o resultado até chegar coleta nova/desfeita ou passar 5 minutos
    assinatura = conexao.execute("SELECT COUNT(*) AS n, IFNULL(MAX(id), 0) AS m FROM produtividade "
                                 "WHERE acao = 'COLETOU BOLETO'").fetchone()
    chave_cache = (tuple(alvo), minimo, janela, assinatura['n'], assinatura['m'])
    if _CACHE_ROBO_SEGUIDO['chave'] == chave_cache and time.time() - _CACHE_ROBO_SEGUIDO['quando'] < 300:
        return dict(_CACHE_ROBO_SEGUIDO['valor'])
    ultimo = {}
    for e in conexao.execute("""SELECT p.usuario, i.imovel, TRIM(i.vencimento) AS venc
                                FROM produtividade p JOIN imoveis i ON i.id = p.imovel_id
                                WHERE p.acao = 'COLETOU BOLETO' ORDER BY p.id"""):
        mes = _mes_do_vencimento(e['venc'])
        if mes not in meses:
            continue
        chave = str(e['imovel'] or '').strip().lstrip('0')
        if chave:
            ultimo[(chave, _venc_texto(e['venc']))] = (mes, _classificar_executor(e['usuario'])[0])
    por_imovel = {}
    for (chave, _venc), (mes, grupo) in ultimo.items():
        por_imovel.setdefault(chave, {}).setdefault(mes, set()).add(grupo)
    resultado = {}
    for chave, por_mes in por_imovel.items():
        pego = [m in por_mes and 'finmail' in por_mes[m] and 'manual' not in por_mes[m] for m in meses]
        seguidos = 0
        for p in pego:
            if not p:
                break
            seguidos += 1
        if seguidos >= minimo:
            resultado[chave] = (seguidos, sum(pego))
    _CACHE_ROBO_SEGUIDO.update(chave=chave_cache, quando=time.time(), valor=dict(resultado))
    return resultado


def preparar_automacao_oculta(conexao):
    """Monta temp.auto_info (uma linha por imóvel de portal automático: portal + motivo do erro do robô, se houver).
    Devolve (ativo, dias). Erro do robô = caso aberto no Raio-X (exceto os já 'ajustados', que esperam o robô tentar de novo)
    ou, no CTRL, um status que o robô de e-mail grava quando falha (e-mail sem PDF, erro de rede).
    Portal com regra de histórico (CTRL) só entra aqui para o imóvel que o robô coletou nos meses seguidos exigidos."""
    ativo, portais, dias = config_ocultar_automacao(conexao)
    conexao.execute("DROP TABLE IF EXISTS temp.auto_info")
    conexao.execute("CREATE TEMP TABLE auto_info (chave TEXT PRIMARY KEY, portal TEXT, erro TEXT, detalhe TEXT DEFAULT '')")
    if not ativo or not portais:
        return False, dias
    erros = {}
    try:
        casos, _ = casos_raio_x_em_cache(conexao)
        for caso in casos:
            if caso.get('esta_ajustado'):
                continue
            erros[str(caso.get('imovel') or '').strip().lstrip('0')] = str(
                caso.get('motivo_resumido') or 'Falha do robô')
    except Exception as erro:
        print(f'⚠️ Não consegui ler os erros do robô para a lista de pendências: {erro}', flush=True)
    qualificados, com_regra_de_status, janelas = {}, set(), {}
    janela_base = janela_historico_robo(conexao)
    for p in portais:
        n = meses_seguidos_exigidos(conexao, p)
        if n:
            janelas[p] = max(janela_base, n)
            try:
                qualificados[p] = imoveis_que_o_robo_pega_sozinho(conexao, n, janela_base)
            except Exception as erro:
                print(f'⚠️ Não consegui ler o histórico do robô ({p}): {erro}', flush=True)
                qualificados[p] = {}             # na dúvida, não esconde
        if p in MESES_SEGUIDOS_DO_ROBO:
            com_regra_de_status.add(p)
    # 🆕 Qualquer portal: o robô de e-mail também pega boleto de Autônoma, WhatsApp, Diversos, Superlógica, sem portal...
    qualificados_geral = {}
    geral_ligada, geral_meses = regra_do_robo_para_qualquer_portal(conexao)
    if geral_ligada:
        try:
            qualificados_geral = imoveis_que_o_robo_pega_sozinho(conexao, geral_meses, janela_base) or {}
        except Exception as erro:
            print(f'⚠️ Não consegui ler o histórico do robô de e-mail (outros portais): {erro}', flush=True)
            qualificados_geral = {}              # na dúvida, não esconde
    erros_por_status = {}
    if com_regra_de_status or qualificados_geral:
        try:
            marcas = ', '.join('?' * len(STATUS_PROBLEMA_DO_ROBO_EMAIL))
            for r in conexao.execute(
                    f"SELECT imovel, UPPER(TRIM(status_interno)) AS st FROM imoveis WHERE ativo = 1 "
                    f"AND UPPER(TRIM(IFNULL(status_interno, ''))) IN ({marcas})", tuple(STATUS_PROBLEMA_DO_ROBO_EMAIL)):
                erros_por_status[str(r['imovel'] or '').strip().lstrip('0')] = STATUS_PROBLEMA_DO_ROBO_EMAIL[r['st']]
        except Exception as erro:
            print(f'⚠️ Não consegui ler os avisos do robô de e-mail: {erro}', flush=True)
    linhas = []
    portal_do_cadastro = {}
    for r in conexao.execute("SELECT imovel, portal FROM cadastro_mestre_imoveis WHERE portal IS NOT NULL").fetchall():
        pn = _portal_normal(r['portal'])
        portal_do_cadastro[str(r['imovel'] or '').strip().lstrip('0')] = str(r['portal'] or '').strip()
        if pn in portais:
            chave = str(r['imovel'] or '').strip().lstrip('0')
            if not chave:
                continue
            if pn in qualificados and qualificados[pn] is not None and chave not in qualificados[pn]:
                continue                          # o robô ainda não provou que pega este imóvel sozinho
            erro_caso = erros.get(chave, '')
            if not erro_caso and pn in com_regra_de_status:
                erro_caso = erros_por_status.get(chave, '')
            detalhe = ''
            if qualificados.get(pn) and chave in qualificados[pn]:
                seguidos, pegos = qualificados[pn][chave]
                detalhe = (f"o robô coletou {pegos} dos últimos {janelas[pn]} meses, "
                           f"{seguidos} {'seguido' if seguidos == 1 else 'seguidos'} até o mês passado")
            linhas.append((chave, _rotulo_portal_auto(r['portal']), erro_caso, detalhe))
    if qualificados_geral:
        ja_incluidos = {l[0] for l in linhas}
        for chave, (seguidos, pegos) in qualificados_geral.items():
            if chave in ja_incluidos:
                continue                          # Winker/Webware/CTRL já entraram pelas regras acima
            erro_caso = erros.get(chave, '') or erros_por_status.get(chave, '')
            cadastrado = portal_do_cadastro.get(chave, '')
            onde = f"portal {cadastrado}; " if cadastrado else ''
            detalhe = (f"{onde}o robô de e-mail coletou {pegos} dos últimos {max(janela_base, geral_meses)} meses, "
                       f"{seguidos} {'seguido' if seguidos == 1 else 'seguidos'} até o mês passado")
            linhas.append((chave, 'E-mail', erro_caso, detalhe))
    conexao.executemany("INSERT OR IGNORE INTO temp.auto_info (chave, portal, erro, detalhe) VALUES (?, ?, ?, ?)", linhas)
    return True, dias


def sql_automacao_oculta(dias):
    """(condição, parâmetros): o caso é de portal automático, ainda está em aberto, o robô não errou e o
    vencimento está a mais de `dias` dias. Usa o apelido `a` (temp.auto_info) e `i` (imoveis)."""
    limite = (datetime.now() + timedelta(days=dias)).strftime('%Y%m%d')
    cond = ("(a.chave IS NOT NULL AND a.erro = ''"
            f" AND UPPER(TRIM(IFNULL(i.status_interno, ''))) NOT IN {SQL_STATUS_COLETADO}"
            " AND LENGTH(TRIM(IFNULL(i.vencimento, ''))) >= 10"
            " AND substr(i.vencimento, 7, 4) || substr(i.vencimento, 4, 2) || substr(i.vencimento, 1, 2) > ?)")
    return cond, [limite]


SQL_JOIN_AUTO_INFO = "LEFT JOIN temp.auto_info a ON a.chave = LTRIM(i.imovel, '0')"


# ==============================================================================
# 🧾 CADA LINHA DA PLANILHA É UM BOLETO
# ==============================================================================
# O relatório do Lello não traz valor nem número do boleto, então não há como saber se duas linhas com o MESMO
# imóvel e o MESMO vencimento são o mesmo boleto listado 2x ou dois boletos de verdade. Decisão do operador:
# são dois boletos. Por isso a lista mostra TODAS as linhas (1.786 na planilha = 1.786 na tela) e cada uma tem o
# SEU status: coletar uma NÃO fecha a outra. O robô, o pagamento e o status em lote mexem numa linha por vez.
# Imóvel com vencimento alternativo (05 e 15) continua com boletos separados, como sempre foi.
_SQL_FINAIS = "(" + ", ".join(f"'{s}'" for s in STATUS_FINALIZADOS) + ")"

# Antes, este pedaço de SQL deixava passar só uma linha por (imóvel + vencimento). Agora toda linha da planilha
# aparece. O nome ficou porque as listas ainda o concatenam: vazio = não filtra nada.
SQL_UMA_LINHA_POR_BOLETO = ""

_PESO_FINAL = {'PAGO': 4, 'PAGO NA ADM': 3, 'PAGO NA 275': 3, 'COLETADO': 2}


def _data_br(texto):
    """'15/11/2026', '15/11/26' ou '15-11-2026' -> datetime (ou None)."""
    achou = re.search(r'(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})', str(texto or ''))
    if not achou:
        return None
    dia, mes, ano = int(achou.group(1)), int(achou.group(2)), int(achou.group(3))
    if ano < 100:
        ano += 2000
    try:
        return datetime(ano, mes, dia)
    except ValueError:
        return None


def _venc_texto(texto):
    """O vencimento sempre no mesmo formato (dd/mm/aaaa), para comparar sem susto."""
    data = _data_br(texto)
    return data.strftime('%d/%m/%Y') if data else ''


def ids_alvo_da_linha(conexao, caso):
    """Linha(s) que mudam quando alguém mexe no status de 'caso' (imovel, vencimento, id).
    Cada linha da planilha é um boleto com status próprio: o normal é SÓ a linha clicada.
    Exceção: o id clicado é de uma linha antiga (a base foi reimportada depois que a tela abriu). Aí o status
    vai também para a linha ativa do mesmo vencimento (a primeira) — ou, se o vencimento mudou de data, para a
    única linha ativa do mesmo mês, só quando não há dúvida de qual é."""
    id_clicado = int(caso['id'])
    atual = conexao.execute("SELECT ativo FROM imoveis WHERE id = ?", (id_clicado,)).fetchone()
    if atual is None or atual['ativo']:
        return [id_clicado]
    venc = _venc_texto(caso['vencimento']) or str(caso['vencimento'] or '').strip()
    ativos = conexao.execute("SELECT id, vencimento FROM imoveis WHERE ativo = 1 AND imovel = ? ORDER BY id",
                             (caso['imovel'],)).fetchall()
    chave = lambda a: _venc_texto(a['vencimento']) or str(a['vencimento'] or '').strip()   # noqa: E731
    iguais = [a['id'] for a in ativos if chave(a) == venc]
    if iguais:
        return sorted({id_clicado, iguais[0]})
    data = _data_br(venc)
    do_mes = {chave(a) for a in ativos
              if data and _data_br(chave(a)) and (_data_br(chave(a)).month, _data_br(chave(a)).year) == (data.month, data.year)}
    if len(do_mes) == 1:
        return sorted({id_clicado, next(a['id'] for a in ativos if chave(a) in do_mes)})
    return [id_clicado]


def id_ativo_do_boleto(conexao, imovel, vencimento, padrao=None, preferido=None):
    """A linha ativa DESTE boleto. Onde a produtividade e o prontuário ficam gravados.
    'preferido' = a linha que a pessoa clicou: se ela está ativa e é do mesmo vencimento, é ela mesma (com
    linhas repetidas, cada uma é um boleto). Sem preferido, a mais nova do vencimento; sem vencimento
    conhecido, a mais nova do imóvel (como sempre foi)."""
    venc = _venc_texto(vencimento) or str(vencimento or '').strip()
    linhas = conexao.execute("SELECT id, vencimento FROM imoveis WHERE ativo = 1 AND imovel = ? ORDER BY id DESC",
                             (imovel,)).fetchall()
    if preferido is not None:
        for l in linhas:
            if l['id'] == preferido and (_venc_texto(l['vencimento']) or str(l['vencimento'] or '').strip()) == venc:
                return l['id']
    for l in linhas:
        if (_venc_texto(l['vencimento']) or str(l['vencimento'] or '').strip()) == venc:
            return l['id']
    return linhas[0]['id'] if linhas else padrao


def marcar_status_do_boleto(conexao, caso, status):
    """UPDATE do status da linha 'caso' (só ela: cada linha é um boleto). Devolve os ids mexidos."""
    ids = ids_alvo_da_linha(conexao, caso)
    conexao.execute(f"UPDATE imoveis SET status_interno = ? WHERE id IN ({','.join('?' * len(ids))})",
                    [status] + ids)
    return ids


def escolher_linhas_do_boleto(linhas, vencimento_robo=''):
    """Qual boleto o robô acabou de baixar? Função PURA (testável) usada por /api/automacao/coletar.
    linhas = linhas ativas do imóvel [{'id','vencimento','status_interno'}].
    Devolve {'ids', 'vencimento', 'ja_resolvido', 'aviso', 'erro', 'pendentes', 'todos', 'linhas_abertas'}.
    Linhas repetidas (mesmo imóvel + mesmo vencimento) são boletos diferentes: o robô fecha UMA por vez — a
    primeira ainda aberta — e o vencimento só conta como resolvido quando TODAS as linhas dele estiverem.
    Regra: o boleto tem que ser identificado pelo VENCIMENTO. Sem ele, só dá para saber de qual
    boleto se trata quando o imóvel tem um vencimento pendente só — com dois, não se adivinha."""
    por_venc = {}
    for l in linhas:
        chave = _venc_texto(l['vencimento']) or str(l['vencimento'] or '').strip()
        por_venc.setdefault(chave, []).append(l)
    ordenar = lambda v: (_data_br(v) or datetime.max, v)   # noqa: E731
    abertas = {v: [x for x in ls if not fora_da_automacao(x['status_interno'])]      # 🔒 resolvido ou CHEQUE/DOC = fora
               for v, ls in por_venc.items()}
    tem_cheque = lambda v: any(str(x['status_interno'] or '').strip().upper() in STATUS_CHEQUE for x in por_venc.get(v, []))   # noqa: E731
    finalizados = {v for v, ab in abertas.items() if not ab}          # TODAS as linhas desse vencimento já resolvidas
    pendentes = sorted((v for v in por_venc if v not in finalizados), key=ordenar)
    r = {'ids': [], 'vencimento': '', 'ja_resolvido': False, 'aviso': '', 'erro': '', 'cheque': False,
         'pendentes': pendentes, 'todos': sorted(por_venc, key=ordenar), 'linhas_abertas': 0}
    if not por_venc:
        r['erro'] = 'Imóvel não localizado.'
        return r

    def alvo_em(venc):
        r['vencimento'] = venc
        ab = abertas.get(venc) or []
        r['linhas_abertas'] = len(ab)
        r['ids'] = [min(int(x['id']) for x in ab)] if ab else sorted(int(x['id']) for x in por_venc[venc])
        return r

    informado = _venc_texto(vencimento_robo)
    if informado:
        if informado in por_venc:
            if informado in finalizados:
                r['vencimento'], r['ja_resolvido'] = informado, True
                if tem_cheque(informado):
                    r['cheque'], r['aviso'] = True, 'Caso marcado como CHEQUE/DOC: a automação não mexe no status.'
                return r
            return alvo_em(informado)
        if not pendentes:
            r['vencimento'], r['ja_resolvido'] = informado, True
            r['aviso'] = f'Todos os vencimentos deste imóvel já estavam resolvidos (o robô baixou o de {informado}).'
            return r
        if len(pendentes) == 1:
            r['aviso'] = (f'O boleto baixado vence em {informado}, mas a base só tem o vencimento '
                          f'{pendentes[0]} pendente: marquei esse.')
            return alvo_em(pendentes[0])
        data = _data_br(informado)
        do_mes = [v for v in pendentes if data and _data_br(v)
                  and (_data_br(v).month, _data_br(v).year) == (data.month, data.year)]
        if not do_mes:
            r['erro'] = (f'O boleto baixado vence em {informado}, que não bate com nenhum vencimento pendente '
                         f'deste imóvel ({", ".join(pendentes)}).')
            return r
        escolhido = min(do_mes, key=lambda v: abs((_data_br(v) - data).days))
        r['aviso'] = f'O boleto baixado vence em {informado}; o vencimento mais próximo na base é {escolhido}: marquei esse.'
        return alvo_em(escolhido)

    if len(pendentes) == 1:
        return alvo_em(pendentes[0])
    if not pendentes:
        r['ja_resolvido'] = True
        if any(tem_cheque(v) for v in por_venc):
            r['cheque'], r['aviso'] = True, 'Caso marcado como CHEQUE/DOC: a automação não mexe no status.'
        return r
    r['erro'] = (f'Este imóvel tem {len(pendentes)} vencimentos pendentes ({", ".join(pendentes)}) e o robô '
                 f'não informou o vencimento do boleto que baixou. Marque pelo painel.')
    return r


def _vencimentos_por_imovel(conexao):
    """{nº do imóvel: [vencimentos da base ativa, dd/mm/aaaa, em ordem]} — numa consulta só (sem subconsulta por linha)."""
    mapa = {}
    for r in conexao.execute("SELECT imovel, GROUP_CONCAT(DISTINCT vencimento) AS v FROM imoveis WHERE ativo = 1 GROUP BY imovel"):
        mapa[r['imovel']] = sorted({_venc_texto(v) or v.strip() for v in str(r['v'] or '').split(',') if v.strip()},
                                   key=lambda v: (_data_br(v) or datetime.max, v))
    return mapa


def unificar_status_gemeos(conexao):
    """Aposentada: linhas repetidas (mesmo imóvel + mesmo vencimento) são boletos diferentes e NÃO andam mais
    juntas. Fica só para quem ainda a chama (devolve sempre 0 e não mexe em nada)."""
    return 0


def vencimento_da_linha_da_planilha(row):
    """(vencimento, dt_comdesc) de uma linha da planilha, exatamente como o upload sempre calculou:
    com Dt.ComDesc preenchida, o boleto 'com desconto' vence nessa data. Separado para o upload poder
    olhar a planilha inteira ANTES de inserir (e herdar o status de cada boleto certo)."""
    venc_bruto = str(row.get('Vencimento', '')).strip()
    dt_comdesc = str(row.get('Dt.ComDesc', '')).replace('nan', '').strip()
    venc = venc_bruto
    if dt_comdesc and dt_comdesc.lower() not in ['nan', 'nat', 'none', '-', '']:
        if len(dt_comdesc) >= 10 and '-' in dt_comdesc:
            try:
                partes = dt_comdesc.split(' ')[0].split('-')
                if len(partes) == 3: dt_comdesc = f"{partes[2]}/{partes[1]}/{partes[0]}"
            except: pass
        elif ' ' in dt_comdesc:
            dt_comdesc = dt_comdesc.split(' ')[0]
        venc = dt_comdesc
    else:
        if venc and len(venc) >= 10 and '-' in venc:
            try:
                partes = venc.split(' ')[0].split('-')
                if len(partes) == 3: venc = f"{partes[2]}/{partes[1]}/{partes[0]}"
            except: pass
        elif venc and ' ' in venc:
            venc = venc.split(' ')[0]
    if venc.lower() in ['nan', 'nat', 'none']: venc = ''
    return venc, dt_comdesc


class HerancaDeStatus:
    """Na reimportação da planilha, cada boleto herda o status que já tinha.
    Boleto = imóvel + vencimento (com dois vencimentos, 05 e 15, cada um herda o seu) e, quando o mesmo imóvel
    + vencimento aparece em mais de uma linha, cada LINHA herda da sua: a 1ª da planilha nova da 1ª que já
    existia, a 2ª da 2ª. Sobrou linha nova = boleto novo, em branco. Se a planilha nova tem MENOS linhas do que a
    base tinha, ficam as que ainda estavam em aberto (nunca se esconde trabalho pendente).
    Tira a 'fotografia' do banco ANTES de desativar a base antiga."""
    def __init__(self, conexao, vencimentos_novos):
        """vencimentos_novos = [(imovel, vencimento)] de TODAS as linhas da planilha nova."""
        self.novos = {(i, _venc_texto(v) or v.strip()) for i, v in vencimentos_novos}
        self.qtd_nova = {}                       # quantas linhas a planilha nova tem de cada boleto
        for i, v in vencimentos_novos:
            k = (i, _venc_texto(v) or v.strip())
            self.qtd_nova[k] = self.qtd_nova.get(k, 0) + 1
        self.consumidos = {}                     # quantas linhas de cada boleto antigo já foram herdadas
        self.por_chave, antigos_ativos, ultimo = {}, {}, {}
        for r in conexao.execute('SELECT id, imovel, vencimento, status_interno, observacao, ativo FROM imoveis ORDER BY id'):
            chave = (r['imovel'], _venc_texto(r['vencimento']) or str(r['vencimento'] or '').strip())
            self.por_chave.setdefault(chave, []).append(r)
            if r['ativo']:
                antigos_ativos.setdefault(r['imovel'], set()).add(chave[1])
            ultimo[r['imovel']] = chave[1]
        # 'base de referência' de cada imóvel para o caso do vencimento ter mudado de data
        self.antigos = {i: v for i, v in antigos_ativos.items()}
        for i, v in ultimo.items():
            self.antigos.setdefault(i, {v})
        self.mapa_mudanca = self._mapear_vencimentos_trocados()

    def _mapear_vencimentos_trocados(self):
        """Mesmo imóvel, mesmo mês, um vencimento sumiu e um apareceu: foi só troca de data (herda).
        Se houver mais de um candidato, não se adivinha — o boleto recomeça em branco."""
        mapa, mes = {}, lambda v: ((_data_br(v).month, _data_br(v).year) if _data_br(v) else None)   # noqa: E731
        por_imovel = {}
        for i, v in self.novos:
            por_imovel.setdefault(i, set()).add(v)
        for imovel, novos in por_imovel.items():
            antigos = self.antigos.get(imovel, set())
            for m in {mes(v) for v in novos if mes(v)}:
                sem_par_novos = [v for v in novos if mes(v) == m and (imovel, v) not in self.por_chave]
                sem_par_antigos = [v for v in antigos if mes(v) == m and (imovel, v) not in self.novos]
                if len(sem_par_novos) == 1 and len(sem_par_antigos) == 1:
                    mapa[(imovel, sem_par_novos[0])] = sem_par_antigos[0]
        return mapa

    @staticmethod
    def _melhor(linhas):
        ativas = [l for l in linhas if l['ativo']]
        candidatas = ativas or [linhas[-1]]
        return max(candidatas, key=lambda l: (_PESO_FINAL.get(str(l['status_interno'] or '').strip().upper(), 0),
                                              bool(str(l['status_interno'] or '').strip()),
                                              bool(str(l['observacao'] or '').strip()), l['id']))

    def herdar(self, imovel, venc):
        """(status, observação) que o boleto (imovel, venc) já tinha; (None, None) se é um boleto novo."""
        venc_n = _venc_texto(venc) or str(venc or '').strip()
        if not venc_n:
            return None, None
        chave = (imovel, venc_n)
        qtd_nova = self.qtd_nova.get(chave, 1)
        if chave not in self.por_chave:
            antigo = self.mapa_mudanca.get(chave)
            chave = (imovel, antigo) if antigo else None
        if not chave or chave not in self.por_chave:
            return None, None
        ativas = [l for l in self.por_chave[chave] if l['ativo']]
        if ativas:
            if len(ativas) > qtd_nova:           # a planilha encolheu: ficam as que ainda estavam em aberto
                ativas = sorted(ativas, key=lambda l: (str(l['status_interno'] or '').strip().upper() in STATUS_FINALIZADOS, l['id']))
            k = self.consumidos.get(chave, 0)
            self.consumidos[chave] = k + 1
            if k >= len(ativas):                 # linha repetida nova: é um boleto novo
                return None, None
            melhor = ativas[k]
        else:
            melhor = self._melhor(self.por_chave[chave])
        if not (melhor['status_interno'] or melhor['observacao']):
            return None, None
        return melhor['status_interno'], melhor['observacao']


# ==============================================================================
# 🔴 AO VIVO — a tela se atualiza sozinha quando outra pessoa (ou o robô) mexe
# ==============================================================================
# Quem está com a mesma listagem aberta vê a mudança de status em poucos segundos,
# sem apertar F5. Funciona assim:
#   1) um GATILHO no banco anota, em mudancas_status, toda troca de status_interno —
#      não importa quem fez: a tela, o disparo em lote, o Winker, o Webware, o scanner.
#      Como é o próprio SQLite que anota, nenhum caminho novo fica de fora por esquecimento.
#   2) a tela pergunta de tempos em tempos "mudou alguma coisa depois do nº X?".
#      Quando nada mudou — que é quase sempre — a resposta é um número só.
# Não usamos WebSocket de propósito: ele exigiria trocar o servidor e deixar uma conexão
# aberta por pessoa. A pergunta periódica custa quase nada e não quebra se a rede oscilar.
DIAS_GUARDA_MUDANCAS = 2            # o mural só serve para o "agora"; o histórico fica em historico_observacoes
MAX_MUDANCAS_POR_PEDIDO = 400       # virada de mês/importação mexe em milhares: aí a tela só avisa, não repinta


def garantir_tabela_mudancas(conexao):
    conexao.execute('''CREATE TABLE IF NOT EXISTS mudancas_status (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        imovel_id INTEGER,
        imovel TEXT,
        status TEXT,
        usuario TEXT DEFAULT '',
        data_hora TEXT)''')
    try:        # o vencimento vai junto: a tela precisa dele para achar a linha certa quando o imóvel tem dois
        conexao.execute('ALTER TABLE mudancas_status ADD COLUMN vencimento TEXT')
    except sqlite3.OperationalError:
        pass
    # O gatilho só dispara quando o status REALMENTE muda (reimportar a planilha não enche o mural).
    # Em banco recém-criado a tabela imoveis ainda não existe: aí o gatilho entra na próxima subida.
    try:
        conexao.execute('DROP TRIGGER IF EXISTS gatilho_mudanca_status')      # refeito para anotar o vencimento
        conexao.execute('''CREATE TRIGGER gatilho_mudanca_status
            AFTER UPDATE OF status_interno ON imoveis FOR EACH ROW
            WHEN IFNULL(OLD.status_interno, '') <> IFNULL(NEW.status_interno, '')
            BEGIN
                INSERT INTO mudancas_status (imovel_id, imovel, status, usuario, data_hora, vencimento)
                VALUES (NEW.id, NEW.imovel, NEW.status_interno, '', datetime('now', 'localtime'), NEW.vencimento);
            END''')
        # Linhas repetidas (mesmo imóvel + MESMO vencimento) são boletos DIFERENTES, cada um com o seu status.
        # O gatilho que as fazia andar juntas foi aposentado — e é removido dos bancos que ainda o têm.
        conexao.execute('DROP TRIGGER IF EXISTS gatilho_gemeos_status')
    except sqlite3.OperationalError as erro:
        print(f'⚠️ Mural de mudanças ainda sem a tabela de imóveis ({erro}).', flush=True)
    conexao.commit()


def limpar_mural_de_mudancas(conexao):
    try:
        limite = (datetime.now() - timedelta(days=DIAS_GUARDA_MUDANCAS)).strftime('%Y-%m-%d %H:%M:%S')
        conexao.execute('DELETE FROM mudancas_status WHERE IFNULL(data_hora, "") < ?', (limite,))
        conexao.commit()
    except Exception:
        pass


# ==============================================================================
# 🤖⚠️ CONTROLE DE ERROS DO ROBÔ — casos em que alguém tirou um boleto de COLETADO
# porque o robô coletou errado (PDF errado, valor errado, boleto de outro mês etc.)
# A pessoa confirma na tela ("o robô coletou errado?") e o caso fica aqui até alguém tratar.
# ==============================================================================
def garantir_tabela_erros_robo(conexao):
    conexao.execute("""CREATE TABLE IF NOT EXISTS erros_robo (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        imovel_id INTEGER,
        imovel TEXT,
        vencimento TEXT,
        status_anterior TEXT,
        status_novo TEXT,
        usuario TEXT,
        data_hora TEXT,
        tratado INTEGER DEFAULT 0,
        data_tratado TEXT,
        tratado_por TEXT)""")
    conexao.commit()


def listar_erros_robo(conexao, tratado=0):
    """Casos em aberto (ou já tratados, se tratado=1) com os dados do imóvel para a tela mostrar."""
    linhas = conexao.execute(
        """SELECT er.id, er.imovel_id, er.imovel, er.vencimento, er.status_anterior, er.status_novo,
                  er.usuario, er.data_hora, er.tratado, er.data_tratado, er.tratado_por,
                  i.administradora, i.empreendimento, i.sacado, i.status_interno AS status_atual,
                  v.responsavel
           FROM erros_robo er
           LEFT JOIN imoveis i ON i.id = er.imovel_id
           LEFT JOIN vinculos_adm v ON TRIM(UPPER(v.administradora)) = TRIM(UPPER(i.administradora))
           WHERE IFNULL(er.tratado, 0) = ?
           ORDER BY er.data_hora DESC""", (tratado,)).fetchall()
    out = []
    for l in linhas:
        d = dict(l)
        resp = str(d.get('responsavel') or '').strip().title()
        if resp == 'Jéssica':
            resp = 'Jessica'
        d['responsavel'] = resp
        out.append(d)
    return out


def marco_das_mudancas(conexao):
    """Onde o mural estava ANTES de mexer no status. Serve para saber o que foi esta pessoa que fez."""
    try:
        return int(conexao.execute('SELECT IFNULL(MAX(id), 0) FROM mudancas_status').fetchone()[0])
    except Exception:
        return 0


def assinar_mudancas(conexao, marco, usuario, imoveis=None):
    """Põe o nome de quem mexeu nas linhas que o gatilho anotou depois do marco.
    'imoveis' limita a assinatura aos imóveis daquela ação — duas pessoas salvando ao mesmo
    tempo não levam o crédito uma da outra."""
    nome = str(usuario or '').strip()
    if not nome:
        return
    try:
        sql = "UPDATE mudancas_status SET usuario = ? WHERE id > ? AND IFNULL(usuario, '') = ''"
        parametros = [nome, int(marco or 0)]
        lista = [str(i).strip() for i in (imoveis or []) if str(i or '').strip()]
        if lista:
            sql += f" AND imovel IN ({', '.join('?' * len(lista))})"
            parametros += lista
        conexao.execute(sql, parametros)
    except Exception:
        pass


@app.route('/api/sistema/mudancas')
@login_obrigatorio
def api_mudancas_status():
    """A tela pergunta: mudou algo depois do nº 'desde'? Sem 'desde', devolve o número de agora
    e o que mudou no último minuto (o intervalo entre desenhar a página e a primeira pergunta)."""
    desde = request.args.get('desde', type=int)
    conexao = conectar_banco()
    try:
        try:
            ultimo = int(conexao.execute('SELECT IFNULL(MAX(id), 0) FROM mudancas_status').fetchone()[0])
        except sqlite3.OperationalError:
            garantir_tabela_mudancas(conexao)
            ultimo = 0

        if desde is None:
            # primeira pergunta da tela: confere se o gatilho existe mesmo neste banco.
            # Sem ele nada é anotado e a tela ficaria parada sem ninguém entender por quê.
            gatilho = bool(conexao.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'trigger' AND name = 'gatilho_mudanca_status'").fetchone())
            if not gatilho:
                garantir_tabela_mudancas(conexao)
                gatilho = bool(conexao.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'trigger' AND name = 'gatilho_mudanca_status'").fetchone())
            corte = (datetime.now() - timedelta(seconds=60)).strftime('%Y-%m-%d %H:%M:%S')
            linhas = conexao.execute(
                'SELECT id, imovel_id, imovel, status, usuario, data_hora, vencimento FROM mudancas_status '
                'WHERE IFNULL(data_hora, "") >= ? ORDER BY id LIMIT ?', (corte, MAX_MUDANCAS_POR_PEDIDO)).fetchall()
            muitas = False
        elif ultimo <= desde:
            gatilho = None
            # o caso comum: ninguém mexeu em nada. Uma consulta só e pronto.
            return jsonify({'sucesso': True, 'ultimo': ultimo, 'mudancas': [], 'muitas': False, 'resumo': None})
        else:
            gatilho = None
            muitas = (ultimo - desde) > MAX_MUDANCAS_POR_PEDIDO
            linhas = [] if muitas else conexao.execute(
                'SELECT id, imovel_id, imovel, status, usuario, data_hora, vencimento FROM mudancas_status '
                'WHERE id > ? ORDER BY id LIMIT ?', (desde, MAX_MUDANCAS_POR_PEDIDO)).fetchall()

        if random.randint(1, 120) == 1:        # faxina de vez em quando, sem job dedicado
            limpar_mural_de_mudancas(conexao)

        mudancas = [{'id': l['id'], 'imovel_id': l['imovel_id'], 'imovel': l['imovel'],
                     'vencimento': l['vencimento'] or '',
                     'status': l['status'] or '', 'usuario': l['usuario'] or '',
                     'hora': str(l['data_hora'] or '')[11:16]} for l in linhas]

        resumo = None
        if mudancas or muitas:                 # os números do topo só são refeitos quando algo mudou mesmo
            try:
                _preparar_vinculos_norm(conexao)
                resumo = resumo_carteira_do_topo(conexao, session.get('usuario', ''))
            except Exception:
                resumo = None
    finally:
        conexao.close()
    return jsonify({'sucesso': True, 'ultimo': ultimo, 'mudancas': mudancas, 'muitas': muitas,
                    'resumo': resumo, 'gatilho': gatilho})


@app.route('/')
@login_obrigatorio
def index():
    conexao = conectar_banco()
    _preparar_vinculos_norm(conexao)
    conexao.create_function("norm", 1, _sem_acento_minusculo, deterministic=True)
    usuario_atual = session.get('usuario', '').strip().lower()
    eh_ajax = request.args.get('ajax') == 'true'

    # 🎯 Captura a busca via texto (Nº Imóvel) e Modo Histórico
    filtro_busca = request.args.get('busca', '').strip()
    rapido = request.args.get('rapido', '').strip().lower()      # atalhos de 1 clique acima da tabela
    ver_ok_inc = request.args.get('ver_ok') == '1'                # Inconsistência: também mostra os já OK (mês atual + seguinte)
    mes_historico = request.args.get('mes_historico', '').strip()
    if request.args.get('ocultos') in ('0', '1'):                # botão "Ver ocultos" (fica guardado na sessão)
        session['ver_ocultos'] = request.args.get('ocultos') == '1'
    ver_ocultos = bool(session.get('ver_ocultos', False))

    # 1. Verifica se o usuário clicou no botão "Limpar"
    if request.args.get('limpar') == '1':
        session.pop('filtro_resp', None)
        session.pop('filtro_per', None)
        session.pop('filtro_status', None)
        session['ignorar_automatico'] = True 
        return redirect(url_for('index'))

    # 2. CAPTURA DE MÚLTIPLOS CHECKBOXES E PREENCHIMENTO "TUDO SELECIONADO"
    # Detecta se houve de fato um envio de formulário para não confundir com o primeiro acesso
    form_submetido = 'filtrar' in request.args or 'responsavel' in request.args or 'periodo' in request.args or 'status_interno' in request.args

    if form_submetido:
        filtro_resp = request.args.getlist('responsavel')
        filtro_per = request.args.getlist('periodo')
        filtro_status = request.args.getlist('status_interno') 
        
        session['filtro_resp'] = filtro_resp
        session['filtro_per'] = filtro_per
        session['filtro_status'] = filtro_status
        session['ignorar_automatico'] = False
    else:
        # Tenta pegar os filtros salvos na memória do usuário
        filtro_resp = session.get('filtro_resp')
        filtro_per = session.get('filtro_per')
        filtro_status = session.get('filtro_status')

        # 💡 A MÁGICA ACONTECE AQUI: Se a memória estiver vazia (Primeiro acesso ou após "Limpar")
        if filtro_resp is None and filtro_per is None and filtro_status is None:
            
            # 1. Busca TODOS os responsáveis disponíveis no banco
            resp_raw = conexao.execute("SELECT DISTINCT TRIM(responsavel) AS colab FROM vinculos_adm WHERE responsavel IS NOT NULL AND TRIM(responsavel) != ''").fetchall()
            filtro_resp = [r['colab'].strip().title() for r in resp_raw]
            if 'Não Atribuído' not in filtro_resp: filtro_resp.append('Não Atribuído')
            
            # 2. Busca TODOS os Períodos (Dias) disponíveis no banco
            per_raw = conexao.execute("SELECT DISTINCT substr(vencimento, 1, 2) AS dia FROM imoveis WHERE vencimento IS NOT NULL AND vencimento != ''").fetchall()
            filtro_per = [r['dia'] for r in per_raw if r['dia'] and r['dia'].isdigit()]
            
            # 3. Busca TODOS os Status Internos disponíveis no banco
            status_raw = conexao.execute("SELECT DISTINCT status_interno FROM imoveis").fetchall()
            filtro_status = [str(r['status_interno']).strip().upper() for r in status_raw if r['status_interno'] and str(r['status_interno']).strip()]
            filtro_status.append('STATUS...') # Representa os casos em branco/nulos
            
            # Regra para usuário comum (Operadores não-gestores)
            if usuario_atual and not usuario_eh_gestor(usuario_atual) and not session.get('ignorar_automatico') and not mes_historico:
                filtro_resp = [usuario_atual.title()]
                filtro_per = ['01', '02', '03', '04'] # Padrão dos operadores, mas os status vêm 100% checados!

            # 4. Salva tudo marcado na sessão para renderizar na tela HTML
            session['filtro_resp'] = filtro_resp
            session['filtro_per'] = filtro_per
            session['filtro_status'] = filtro_status
        else:
            # Caso contrário, apenas garante que não vai quebrar se vier Nulo
            filtro_resp = filtro_resp or []
            filtro_per = filtro_per or []
            filtro_status = filtro_status or []

    # (O restante do código de "Filtro Automático" continua normal a partir daqui...)

    if not isinstance(filtro_resp, list): filtro_resp = [filtro_resp] if filtro_resp else []
    if not isinstance(filtro_per, list): filtro_per = [filtro_per] if filtro_per else []
    if not isinstance(filtro_status, list): filtro_status = [filtro_status] if filtro_status else []

    # 3. Filtro Automático
    filtro_automatico = False
    
    if filtro_busca:
        filtro_resp = []
        filtro_per = []
        filtro_status = []
        filtro_automatico = False
    elif usuario_atual and not usuario_eh_gestor(usuario_atual) and set(filtro_resp) == {usuario_atual.title()} and not session.get('ignorar_automatico') and not mes_historico:
        filtro_automatico = True
        
    try:
        pagina_atual = max(1, int(request.args.get('pagina', 1)))
    except ValueError:
        pagina_atual = 1

    # ⚡ Paginação SEMPRE no banco: 100 linhas por vez (rolagem infinita).
    # Antes, com qualquer filtro ativo (o que é sempre, pois o padrão já vem "tudo marcado"), o LIMIT era
    # descartado e a tela recebia a base inteira de uma vez.
    # Para os filtros de coluna / exportação o front pede o restante em blocos maiores via ?inicio=N&por_pagina=M.
    itens_por_pagina = 100
    offset = (pagina_atual - 1) * itens_por_pagina
    if eh_ajax:
        try:
            por_pagina_req = int(request.args.get('por_pagina', 0))
            if 1 <= por_pagina_req <= 1000:
                itens_por_pagina = por_pagina_req
                offset = (pagina_atual - 1) * itens_por_pagina
        except ValueError:
            pass
        try:
            inicio_req = int(request.args.get('inicio', -1))
            if inicio_req >= 0:
                offset = inicio_req
        except ValueError:
            pass

    # =========================================================
    # MONTAGEM INTELIGENTE DO SQL (Otimizado com Status Global + MODO HISTÓRICO)
    # =========================================================
    
    # Se o usuário escolheu viajar no tempo, troca a tabela principal para o cofre
    if mes_historico:
        prefixo = "h."
        where_sql = "WHERE h.mes_referencia = ?"
        parametros = [mes_historico]
    else:
        prefixo = "i."
        # boleto com desconto vem em 2 linhas na planilha (mesmo imóvel e mesmo vencimento): mostra 1
        where_sql = "WHERE i.administradora IS NOT NULL AND i.administradora != '' AND i.ativo = 1" + SQL_UMA_LINHA_POR_BOLETO
        parametros = []
    
    # 🔎 Busca no servidor (Imóvel / Empreendimento / Sacado), sem acento e sem diferenciar maiúscula.
    # O Nº do imóvel exato continua vindo primeiro (ver ORDER BY). Termo só com números = busca só no Nº do imóvel.
    ordem_busca_sql = ""
    ordem_busca_params = []
    if filtro_busca:
        termo_busca = _sem_acento_minusculo(filtro_busca).strip()
        termo_like = '%' + termo_busca.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
        if termo_busca.isdigit():
            where_sql += f" AND ({prefixo}imovel = ? OR {prefixo}imovel LIKE ? ESCAPE '\\')"
            parametros.extend([filtro_busca, termo_like])
        else:
            where_sql += (f" AND ({prefixo}imovel = ? OR norm({prefixo}imovel) LIKE ? ESCAPE '\\'"
                          f" OR norm(i.empreendimento) LIKE ? ESCAPE '\\' OR norm(i.sacado) LIKE ? ESCAPE '\\')")
            parametros.extend([filtro_busca, termo_like, termo_like, termo_like])
        ordem_busca_sql = f"({prefixo}imovel = ?) DESC, "
        ordem_busca_params = [filtro_busca]

    if len(filtro_resp) > 0:
        placeholders = ', '.join(['?'] * len(filtro_resp))
        if mes_historico:
            where_sql += f" AND LOWER(TRIM(IFNULL(v.responsavel, h.responsavel))) IN ({placeholders})"
        else:
            where_sql += f" AND LOWER(TRIM(v.responsavel)) IN ({placeholders})"
        parametros.extend([r.strip().lower() for r in filtro_resp])
        
    if len(filtro_per) > 0 and not rapido:
        placeholders = ', '.join(['?'] * len(filtro_per))
        where_sql += f" AND substr({prefixo}vencimento, 1, 2) IN ({placeholders})"
        parametros.extend([str(p).zfill(2) for p in filtro_per])

    if len(filtro_status) > 0 and not rapido:
        condicoes_status = []
        status_limpos = []
        for s in filtro_status:
            s_upper = s.strip().upper()
            if s_upper == "STATUS..." or "BRANCO" in s_upper:
                condicoes_status.append(f"({prefixo}status_interno IS NULL OR {prefixo}status_interno = '' OR {prefixo}status_interno = 'STATUS...')")
            else:
                status_limpos.append(s_upper)
        
        if len(status_limpos) > 0:
            placeholders = ', '.join(['?'] * len(status_limpos))
            condicoes_status.append(f"UPPER({prefixo}status_interno) IN ({placeholders})")
            parametros.extend(status_limpos)
            
        if len(condicoes_status) > 0:
            where_sql += " AND (" + " OR ".join(condicoes_status) + ")"

    # ⚠️ Inconsistências do Bradesco (planilha Inc. Bradesco): selo nas linhas + atalho "Inconsistência"
    # Cruzar TODAS as pendências com a base ativa inteira é caro pra fazer a cada tela carregada — por isso
    # isso vem de um cache (inc_lista_e_por_id_em_cache), não mais recalculado toda vez que alguém entra.
    inc_lista, inc_por_id = [], {}
    if not mes_historico:
        try:
            inc_lista, inc_por_id = inc_lista_e_por_id_em_cache(conexao)
        except Exception as erro_inc:
            print(f'⚠️ Não consegui ler as inconsistências do Bradesco: {erro_inc}', flush=True)
            inc_lista, inc_por_id = [], {}

    # ---- atalhos de 1 clique (vence hoje / vencidos / sem login / não emitido / inconsistência) ----
    # Enquanto um atalho está ligado, ele manda sozinho: os filtros de dia e de status ficam de lado,
    # senão um filtro guardado da sessão esconderia justamente o que o operador pediu para ver.
    if rapido and not mes_historico:
        hoje_ddmmaaaa = datetime.now().strftime('%d/%m/%Y')
        ordenavel = (f"substr({prefixo}vencimento, 7, 4) || substr({prefixo}vencimento, 4, 2) "
                     f"|| substr({prefixo}vencimento, 1, 2)")
        hoje_ordenavel = datetime.now().strftime('%Y%m%d')
        if rapido == 'vence_hoje':
            where_sql += f" AND substr({prefixo}vencimento, 1, 10) = ?"
            parametros.append(hoje_ddmmaaaa)
        elif rapido == 'vencidos':
            where_sql += (f" AND LENGTH(TRIM(IFNULL({prefixo}vencimento, ''))) >= 10 AND {ordenavel} < ?"
                          f" AND UPPER(IFNULL({prefixo}status_interno, '')) NOT IN {SQL_STATUS_COLETADO}")
            parametros.append(hoje_ordenavel)
        elif rapido == 'sem_login':
            # sem credencial no cadastro mestre E sem senha anotada no texto da coluna Acesso
            where_sql += (" AND NOT EXISTS (SELECT 1 FROM cadastro_mestre_imoveis c WHERE c.imovel = i.imovel"
                          "   AND TRIM(IFNULL(c.login_acesso, '')) != '' AND TRIM(IFNULL(c.senha_acesso, '')) != ''"
                          "   AND c.login_acesso NOT LIKE '[X]%')"
                          " AND UPPER(IFNULL(i.acesso, '')) NOT LIKE '%SENHA%'"
                          " AND UPPER(IFNULL(i.acesso, '')) NOT LIKE '%S:%'")
        elif rapido == 'nao_emitido':
            where_sql += f" AND UPPER(TRIM(IFNULL({prefixo}status_interno, ''))) = 'NÃO EMITIDO'"
        elif rapido == 'inconsistencia':
            # boletos da base ativa que têm devolução PENDENTE no Inc. Bradesco (os de outros meses aparecem no painel)
            if inc_por_id:
                where_sql += " AND i.id IN (" + ','.join(str(int(x)) for x in inc_por_id) + ")"
            else:
                where_sql += " AND 0"

    # 🤖 Automação (Webware/Winker) oculta até perto do vencimento ou até o robô errar. Busca por imóvel enxerga tudo.
    auto_ativo, auto_ocultos, auto_com_erro, auto_dias, auto_portais_txt = False, 0, 0, DIAS_PARA_APARECER, ''
    if not mes_historico:
        try:
            auto_ativo, auto_dias = preparar_automacao_oculta(conexao)
            auto_portais_txt = texto_portais_ocultos(conexao)
        except Exception as erro:
            print(f'⚠️ Não consegui preparar a automação oculta: {erro}', flush=True)
            auto_ativo = False
    if auto_ativo and not mes_historico:
        cond_oculto, params_oculto = sql_automacao_oculta(auto_dias)
        if not eh_ajax:
            auto_ocultos = conexao.execute(
                f"SELECT COUNT(*) FROM imoveis i LEFT JOIN temp.vinc_norm v ON v.chave = chave_adm(i.administradora) "
                f"{SQL_JOIN_AUTO_INFO} {where_sql} AND {cond_oculto}",
                parametros + params_oculto).fetchone()[0]
            auto_com_erro = conexao.execute(
                f"SELECT COUNT(*) FROM imoveis i LEFT JOIN temp.vinc_norm v ON v.chave = chave_adm(i.administradora) "
                f"{SQL_JOIN_AUTO_INFO} {where_sql} AND a.chave IS NOT NULL AND a.erro != ''"
                f" AND UPPER(TRIM(IFNULL(i.status_interno, ''))) NOT IN {SQL_STATUS_COLETADO}",
                parametros).fetchone()[0]
        if not ver_ocultos and not filtro_busca and rapido != 'inconsistencia':
            where_sql += f" AND NOT {cond_oculto}"
            parametros.extend(params_oculto)

    # =========================================================
    # CONTAGEM DE PÁGINAS E BUSCA DINÂMICA
    # =========================================================
    if mes_historico:
        # 🧊 MODO HISTÓRICO
        query_count = f'''
            SELECT COUNT(DISTINCT h.id) FROM historico_fechamento_mes h
            LEFT JOIN imoveis i ON h.imovel = i.imovel AND i.ativo = 1
            LEFT JOIN temp.vinc_norm v ON v.chave = chave_adm(h.administradora)
            {where_sql}
        '''
        total_registros = conexao.execute(query_count, parametros).fetchone()[0]
        total_paginas = (total_registros + itens_por_pagina - 1) // itens_por_pagina

        limite_str = "LIMIT ? OFFSET ?"
        params_finais = parametros + ordem_busca_params + [itens_por_pagina, offset]

        query_imoveis = f'''
            SELECT 
                -h.id as id, h.imovel, h.vencimento, h.administradora, h.status_interno, 
                IFNULL(v.responsavel, h.responsavel) as colaborador, 
                IFNULL(v.email, '') as email_adm,
                IFNULL(i.carteira, '-') as carteira, IFNULL(i.d_lote, '-') as d_lote, 
                IFNULL(i.empreendimento, '-') as empreendimento, IFNULL(i.endereco, '-') as endereco, 
                IFNULL(i.sacado, '-') as sacado, IFNULL(i.cpf_cnpj, '-') as cpf_cnpj, 
                IFNULL(i.acesso, '-') as acesso, IFNULL(i.media_emissao, '-') as media_emissao
            FROM historico_fechamento_mes h
            LEFT JOIN imoveis i ON h.imovel = i.imovel AND i.ativo = 1
            LEFT JOIN temp.vinc_norm v ON v.chave = chave_adm(h.administradora)
            {where_sql}
            GROUP BY h.id
            ORDER BY {ordem_busca_sql}substr(h.vencimento, 1, 2) ASC, h.id ASC
            {limite_str}
        '''
        imoveis_raw = conexao.execute(query_imoveis, params_finais).fetchall()

    else:
        # 🔥 MODO BASE ATIVA ORIGINAL
        query_count = f'''
            SELECT COUNT(i.id) FROM imoveis i
            LEFT JOIN temp.vinc_norm v ON v.chave = chave_adm(i.administradora)
            {SQL_JOIN_AUTO_INFO}
            {where_sql}
        '''
        total_registros = conexao.execute(query_count, parametros).fetchone()[0]
        total_paginas = (total_registros + itens_por_pagina - 1) // itens_por_pagina

        limite_str = "LIMIT ? OFFSET ?"
        params_finais = parametros + ordem_busca_params + [itens_por_pagina, offset]

        # (i.id no ORDER BY = desempate estável, senão a mesma linha pode repetir/sumir entre uma página e outra)
        query_imoveis = f'''
            SELECT i.*,
                   IFNULL(v.responsavel, 'Não Atribuído') as colaborador,
                   IFNULL(v.email, '') as email_adm,
                   a.portal as auto_portal, a.erro as auto_erro, a.detalhe as auto_detalhe
            FROM imoveis i
            LEFT JOIN temp.vinc_norm v ON v.chave = chave_adm(i.administradora)
            {SQL_JOIN_AUTO_INFO}
            {where_sql}
            ORDER BY {ordem_busca_sql}substr(i.vencimento, 1, 2) ASC, i.id ASC
            {limite_str}
        '''
        imoveis_raw = conexao.execute(query_imoveis, params_finais).fetchall()


    # =========================================================
    # 🚀 BALA DE PRATA V2: INJEÇÃO DE HISTÓRICO ULTRA RÁPIDA
    # =========================================================
    dict_notas = {}
    dict_coletores = {}

    if imoveis_raw:
        if mes_historico:
            chaves_imovel = list(set([str(r['imovel']) for r in imoveis_raw]))
            chunk_size = 900
            for i in range(0, len(chaves_imovel), chunk_size):
                chunk = chaves_imovel[i:i + chunk_size]
                placeholders = ','.join(['?'] * len(chunk))
                
                query_n = f"""
                    SELECT old_i.imovel, old_i.vencimento, h_obs.texto 
                    FROM historico_observacoes h_obs 
                    JOIN imoveis old_i ON h_obs.imovel_id = old_i.id 
                    WHERE h_obs.id IN (
                        SELECT MAX(h.id) FROM historico_observacoes h
                        JOIN imoveis o ON h.imovel_id = o.id
                        WHERE o.imovel IN ({placeholders})
                        GROUP BY o.imovel, o.vencimento
                    )
                """
                for n in conexao.execute(query_n, chunk).fetchall():
                    dict_notas[f"{n['imovel']}_{n['vencimento']}"] = n['texto']
                    
                query_c = f"""
                    SELECT old_i.imovel, old_i.vencimento, p.usuario 
                    FROM produtividade p 
                    JOIN imoveis old_i ON p.imovel_id = old_i.id 
                    WHERE p.id IN (
                        SELECT MAX(p2.id) FROM produtividade p2
                        JOIN imoveis o2 ON p2.imovel_id = o2.id
                        WHERE p2.acao = 'COLETOU BOLETO' AND o2.imovel IN ({placeholders})
                        GROUP BY o2.imovel, o2.vencimento
                    )
                """
                for c in conexao.execute(query_c, chunk).fetchall():
                    dict_coletores[f"{c['imovel']}_{c['vencimento']}"] = c['usuario']
        else:
            # 🎯 CORREÇÃO (Observações sumindo após atualizar a base):
            # Cada upload da base cria uma linha NOVA (id novo) para o mesmo imóvel, mas o histórico de
            # observações continuava preso ao id ANTIGO. Por isso a coluna zerava depois da atualização.
            # Agora a "última observação" é buscada pelo Nº do imóvel + mês/ano do vencimento (o mesmo
            # critério que o upload usa para manter o status), então sobrevive às atualizações da base
            # e zera sozinha só na virada de mês.
            chaves_imovel = list(set([str(r['imovel']) for r in imoveis_raw]))
            chunk_size = 900
            for i in range(0, len(chaves_imovel), chunk_size):
                chunk = chaves_imovel[i:i + chunk_size]
                placeholders = ','.join(['?'] * len(chunk))
                
                query_n = f"""
                    SELECT o.imovel, substr(IFNULL(o.vencimento, ''), 4, 7) AS ciclo, h.texto
                    FROM historico_observacoes h
                    JOIN imoveis o ON h.imovel_id = o.id
                    WHERE h.id IN (
                        SELECT MAX(h2.id) FROM historico_observacoes h2
                        JOIN imoveis o2 ON h2.imovel_id = o2.id
                        WHERE o2.imovel IN ({placeholders})
                        GROUP BY o2.imovel, substr(IFNULL(o2.vencimento, ''), 4, 7)
                    )
                """
                for n in conexao.execute(query_n, chunk).fetchall():
                    dict_notas[f"{n['imovel']}_{n['ciclo']}"] = n['texto']
                    
                query_c = f"""
                    SELECT o.imovel, substr(IFNULL(o.vencimento, ''), 4, 7) AS ciclo, p.usuario
                    FROM produtividade p
                    JOIN imoveis o ON p.imovel_id = o.id
                    WHERE p.id IN (
                        SELECT MAX(p2.id) FROM produtividade p2
                        JOIN imoveis o2 ON p2.imovel_id = o2.id
                        WHERE p2.acao = 'COLETOU BOLETO' AND o2.imovel IN ({placeholders})
                        GROUP BY o2.imovel, substr(IFNULL(o2.vencimento, ''), 4, 7)
                    )
                """
                for c in conexao.execute(query_c, chunk).fetchall():
                    dict_coletores[f"{c['imovel']}_{c['ciclo']}"] = c['usuario']

    # =========================================================
    # 🎯 PROCESSAMENTO VISUAL DA TELA
    # =========================================================
    imoveis_processados = []
    hoje_automacao = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    for row in imoveis_raw:
        item = dict(row)
        
        if mes_historico:
            chave = f"{item['imovel']}_{item['vencimento']}"
            item['ultima_nota'] = dict_notas.get(chave, '')
            item['coletor_mes'] = dict_coletores.get(chave, '')
        else:
            chave = f"{item['imovel']}_{str(item.get('vencimento') or '')[3:10]}"
            item['ultima_nota'] = dict_notas.get(chave, '')
            item['coletor_mes'] = dict_coletores.get(chave, '')

        nota = str(item.get('ultima_nota') or '').strip()
        item['inc'] = None if mes_historico else inc_por_id.get(item.get('id'))      # devolução pendente do Bradesco neste boleto

        # 🤖 Selo nas linhas de automação: robô falhou (precisa de gente) ou caso oculto que só aparece por causa do botão
        item['auto_tipo'], item['auto_aviso'] = '', ''
        if not mes_historico and item.get('auto_portal') \
                and str(item.get('status_interno') or '').strip().upper() not in STATUS_FINALIZADOS:
            if item.get('auto_erro'):
                item['auto_tipo'] = 'erro'
                item['auto_aviso'] = f"Robô {item['auto_portal']} falhou: {item['auto_erro']} — precisa de você"
            else:
                d_venc = _data_br(item.get('vencimento'))
                if d_venc and (d_venc - hoje_automacao).days > auto_dias:
                    item['auto_tipo'] = 'oculto'
                    extra_auto = f" ({item['auto_detalhe']})" if item.get('auto_detalhe') else ''
                    item['auto_aviso'] = (f"Automação {item['auto_portal']}{extra_auto} — fica oculto até "
                                          f"{(d_venc - timedelta(days=auto_dias)).strftime('%d/%m')} (10 dias antes do vencimento)"
                                          .replace('10 dias', f'{auto_dias} dias'))

        if item.get('status_interno') == 'COLETADO':
            coletor = str(item.get('coletor_mes') or '').strip().title()
            quem = coletor if coletor else "Automação"
            if quem == "Jéssica": quem = "Jessica"
            icone = "🤖" if "Automação" in quem or "Sistema" in quem or "Rob" in quem else "👤"

            if "Coletado por" not in nota and "Processado via automação" not in nota:
                if nota and nota != '-' and nota != 'None':
                    item['ultima_nota'] = f"{icone} Coletado por: {quem} | {nota}"
                else:
                    item['ultima_nota'] = f"{icone} Coletado por: {quem}"

        elif item.get('status_interno') == 'SOLICITADO':
            if nota and nota != '-' and nota != 'None' and "✉️" not in nota:
                item['ultima_nota'] = f"✉️ {nota}"

        elif item.get('status_interno') in ['PAGO NA ADM', 'PAGO NA 275']:
             if nota and nota != '-' and nota != 'None' and "✅" not in nota:
                item['ultima_nota'] = f"✅ {nota}"

        imoveis_processados.append(item)

    imoveis = imoveis_processados

    # 🎯 SE FOR O AJAX (rolagem infinita / busca / "carregar tudo"), DEVOLVA SÓ AS LINHAS!
    # Retorna ANTES de montar cards, lista de responsáveis, histórico de meses etc. (só a tela completa precisa deles).
    if eh_ajax:
        conexao.close()
        resposta = make_response(render_template('linhas_tabela.html', imoveis=imoveis,
                                                 mes_historico=mes_historico, usuario_atual=usuario_atual,
                                                 filtro_resp=filtro_resp, filtro_per=filtro_per, filtro_status=filtro_status))
        resposta.headers['X-Total-Registros'] = str(total_registros)
        resposta.headers['X-Total-Paginas'] = str(total_paginas)
        resposta.headers['X-Linhas-Recebidas'] = str(len(imoveis))
        resposta.headers['Cache-Control'] = 'no-store'
        return resposta

    resp_raw = conexao.execute("SELECT DISTINCT TRIM(responsavel) AS colaborador FROM vinculos_adm WHERE responsavel IS NOT NULL AND TRIM(responsavel) != '' AND TRIM(responsavel) != 'Não Atribuído'").fetchall()
    
    resp_unicos = set()
    responsaveis = []
    for r in resp_raw:
        nome = r['colaborador'].strip().title()
        if nome == 'Jéssica': 
            nome = 'Jessica'
            
        if nome not in resp_unicos:
            resp_unicos.add(nome)
            responsaveis.append({'colaborador': nome})
            
    responsaveis = sorted(responsaveis, key=lambda x: x['colaborador'])
    
    ultimo_upload = conexao.execute("SELECT usuario, data_hora, acao FROM produtividade WHERE acao LIKE 'UPLOAD BASE|%' ORDER BY id DESC LIMIT 1").fetchone()

    emissao_hoje_count = 0
    emissao_hoje_lista = []
    
    casos_vencidos = []
    casos_vencem_hoje = []
    casos_proximos_7_dias = []
    casos_proximos_15_dias = []
    
    if usuario_atual and not mes_historico:
        hoje = datetime.now()
        hoje_dia_str = hoje.strftime('%d')
        hoje_datetime = hoje.replace(hour=0, minute=0, second=0, microsecond=0)
        sete_dias_frente = hoje_datetime + timedelta(days=7)
        quinze_dias_frente = hoje_datetime + timedelta(days=15)

        query_meus_casos_pendentes = '''
            SELECT i.imovel, i.administradora, i.vencimento, i.status_interno, i.media_emissao
            FROM imoveis i
            LEFT JOIN temp.vinc_norm v ON v.chave = chave_adm(i.administradora)
            WHERE i.ativo = 1 AND i.media_emissao IS NOT NULL
              AND (i.status_interno IS NULL OR i.status_interno NOT IN ('PAGO', 'COLETADO'))
              AND v.responsavel LIKE ?
        ''' + SQL_UMA_LINHA_POR_BOLETO + '''
            ORDER BY i.vencimento ASC
        '''
        todos_meus_casos = conexao.execute(query_meus_casos_pendentes, (f"%{usuario_atual}%",)).fetchall()

        for row in todos_meus_casos:
            item = dict(row)
            
            media_emissao = str(item.get('media_emissao', '')).strip()
            dia_sem_zero = str(hoje.day)
            hoje_completo = hoje.strftime('%d/%m/%Y')
            
            if media_emissao in [hoje_dia_str, dia_sem_zero, hoje_completo] and not fora_da_automacao(item.get('status_interno')):
                emissao_hoje_lista.append(item)      # 🔒 PAGO NA ADM / PAGO NA 275 / CHEQUE-DOC também ficam fora do radar

            try:
                vencimento_str = item['vencimento']
                if len(vencimento_str.split('/')[2]) == 2:
                    vencimento_str = vencimento_str[:-2] + '20' + vencimento_str[-2:]

                vencimento_datetime = datetime.strptime(vencimento_str, '%d/%m/%Y')
                
                if vencimento_datetime < hoje_datetime:
                    casos_vencidos.append(item)
                elif vencimento_datetime == hoje_datetime:
                    casos_vencem_hoje.append(item)
                elif hoje_datetime < vencimento_datetime <= sete_dias_frente:
                    casos_proximos_7_dias.append(item)
                elif hoje_datetime < vencimento_datetime <= quinze_dias_frente:
                    casos_proximos_15_dias.append(item)
            except (ValueError, IndexError, TypeError):
                continue

        emissao_hoje_count = len(emissao_hoje_lista)
    
    precisa_confirmar_rpa = False
    
    if usuario_atual and usuario_atual != 'arthur' and datetime.now().hour >= 8 and not mes_historico:
        hoje_str = datetime.now().strftime('%Y-%m-%d')
        ja_rodou = conexao.execute("SELECT COUNT(*) FROM log_disparos_diarios WHERE data = ? AND LOWER(TRIM(responsavel)) = ?", (hoje_str, usuario_atual.lower())).fetchone()[0]
        
        if ja_rodou == 0 and emissao_hoje_count > 0:
            precisa_confirmar_rpa = True

    meses_arquivados = [row['mes_referencia'] for row in conexao.execute("SELECT DISTINCT mes_referencia FROM historico_fechamento_mes ORDER BY id DESC").fetchall()]

    # ⚠️ Inconsistências do Bradesco: painel (só no atalho), última leitura da planilha e atualização em segundo plano
    inc_total, inc_painel, inc_leitura, inc_dias = len(inc_lista), [], {'quando': '', 'falha': ''}, INC_DIAS_PASSADO
    inc_meses_trabalho = inc_meses_de_trabalho()           # (mês civil atual, mês seguinte) -> p/ os "OK" e pro rótulo do checkbox
    if not mes_historico:
        try:
            inc_dias = inc_config(conexao)[1]
            if rapido == 'inconsistencia':
                meses_ok = set(inc_meses_trabalho) if ver_ok_inc else None
                inc_painel = inc_pendentes(conexao, enriquecer=True, incluir_ok_meses=meses_ok)
            inc_leitura['quando'], inc_leitura['falha'] = inc_status_da_leitura(conexao)
            if inc_precisa_atualizar(conexao):
                inc_atualizar_em_segundo_plano()
        except Exception as erro_inc:
            print(f'⚠️ Painel das inconsistências do Bradesco: {erro_inc}', flush=True)

    # 🤖⚠️ Erros do robô (coleta errada): contador sempre; a lista só quando o atalho está ligado
    erro_robo_total, erro_robo_painel = 0, []
    if not mes_historico:
        try:
            garantir_tabela_erros_robo(conexao)
            erro_robo_total = conexao.execute("SELECT COUNT(*) FROM erros_robo WHERE IFNULL(tratado, 0) = 0").fetchone()[0]
            if rapido == 'erro_robo':
                erro_robo_painel = listar_erros_robo(conexao, tratado=0)
        except Exception as erro_er:
            print(f'⚠️ Painel de erros do robô: {erro_er}', flush=True)

    # Números da faixa do topo: ainda com a conexão aberta, porque usam a tabela temporária de vínculos.
    resumo_topo = resumo_carteira_do_topo(conexao, usuario_atual) if not mes_historico else None

    conexao.close()
    pode_disparar_todas = usuario_eh_gestor(usuario_atual)

    return render_template('index.html', 
                           rapido=rapido, resumo_topo=resumo_topo,
                           inc_total=inc_total, inc_painel=inc_painel, inc_leitura=inc_leitura, inc_dias=inc_dias,
                           inc_obs_padrao=[{'texto': a, 'situacao': b, 'data': c} for a, b, c in INC_OBS_PADRAO],
                           ver_ok_inc=ver_ok_inc, inc_nomes_meses_trabalho=inc_nomes_meses_trabalho(),
                           erro_robo_total=erro_robo_total, erro_robo_painel=erro_robo_painel,
                           mes_historico=mes_historico, meses_arquivados=meses_arquivados, 
                           imoveis=imoveis, responsaveis=responsaveis, 
                           filtro_resp=filtro_resp, filtro_per=filtro_per, filtro_status=filtro_status, 
                           ultimo_upload=ultimo_upload,
                           emissao_hoje_count=emissao_hoje_count, emissao_hoje_lista=emissao_hoje_lista, 
                           filtro_automatico=filtro_automatico, usuario_atual=usuario_atual,
                           casos_vencidos=casos_vencidos, casos_vencem_hoje=casos_vencem_hoje,
                           casos_proximos_7_dias=casos_proximos_7_dias, casos_proximos_15_dias=casos_proximos_15_dias,
                           pagina_atual=pagina_atual, total_paginas=total_paginas,
                           total_registros=total_registros,
                           auto_ativo=auto_ativo, auto_ocultos=auto_ocultos, auto_com_erro=auto_com_erro,
                           auto_dias=auto_dias, ver_ocultos=ver_ocultos,
                           auto_portais_txt=auto_portais_txt,
                           precisa_confirmar_rpa=precisa_confirmar_rpa,
                           pode_disparar_todas=pode_disparar_todas,
                           aviso_virada=session.pop('aviso_virada', None))


@app.route('/api/robo/marcar_erro_coleta', methods=['POST'])
@login_obrigatorio
def api_marcar_erro_coleta():
    """A tela já perguntou "o robô coletou errado?" e a resposta foi sim: registra o(s) caso(s)
    para a equipe revisar. Não duplica: se já existe um caso em aberto para o mesmo boleto, não cria outro."""
    dados = request.get_json() or {}
    ids = dados.get('ids') or ([dados.get('id')] if dados.get('id') else [])
    status_novo = str(dados.get('status_novo') or '').strip().upper()
    usuario_atual = session.get('usuario', 'Sistema')
    data_hora_atual = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    if not ids:
        return jsonify({'sucesso': False, 'msg': 'Nenhum imóvel informado.'}), 400

    conexao = conectar_banco()
    garantir_tabela_erros_robo(conexao)
    inseridos = 0
    for vid in ids:
        try:
            id_inteiro = int(vid)
        except Exception:
            continue
        caso = conexao.execute('SELECT id, imovel, vencimento FROM imoveis WHERE id = ?', (id_inteiro,)).fetchone()
        if not caso:
            continue
        ja_existe = conexao.execute(
            "SELECT id FROM erros_robo WHERE imovel_id = ? AND IFNULL(tratado, 0) = 0", (id_inteiro,)).fetchone()
        if ja_existe:
            continue
        conexao.execute(
            """INSERT INTO erros_robo (imovel_id, imovel, vencimento, status_anterior, status_novo, usuario, data_hora)
               VALUES (?, ?, ?, 'COLETADO', ?, ?, ?)""",
            (id_inteiro, caso['imovel'], caso['vencimento'], status_novo, usuario_atual, data_hora_atual))
        inseridos += 1
    conexao.commit()
    conexao.close()
    return jsonify({'sucesso': True, 'inseridos': inseridos})


@app.route('/api/robo/erros/<int:erro_id>/tratar', methods=['POST'])
@login_obrigatorio
def api_tratar_erro_robo(erro_id):
    usuario_atual = session.get('usuario', 'Sistema')
    data_hora_atual = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    conexao = conectar_banco()
    garantir_tabela_erros_robo(conexao)
    caso = conexao.execute('SELECT id FROM erros_robo WHERE id = ?', (erro_id,)).fetchone()
    if not caso:
        conexao.close()
        return jsonify({'sucesso': False, 'msg': 'Caso não encontrado.'}), 404
    conexao.execute(
        "UPDATE erros_robo SET tratado = 1, data_tratado = ?, tratado_por = ? WHERE id = ?",
        (data_hora_atual, usuario_atual, erro_id))
    conexao.commit()
    conexao.close()
    return jsonify({'sucesso': True})


@app.route('/atualizar_status_imovel', methods=['POST'])
@login_obrigatorio
def atualizar_status_imovel():
    dados = request.get_json()
    
    if not dados:
        return jsonify({'sucesso': False, 'msg': 'Payload vazio.'}), 400

    valor_recebido = dados.get('id')
    novo_status = dados.get('status')
    usuario_atual = session.get('usuario', 'Sistema')
    data_hora_atual = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    if not valor_recebido or novo_status is None:
        return jsonify({'sucesso': False, 'msg': 'ID ou status não enviados.'})

    # 🎯 A MÁGICA DE CORREÇÃO: Força a string a ficar maiúscula e sem espaços fantasmas
    status_banco = None if novo_status.strip() == "" else novo_status.strip().upper()

    try:
        id_inteiro = int(valor_recebido)
    except Exception as e:
        return jsonify({'sucesso': False, 'msg': 'ID inválido'}), 400

    try:
        conexao = conectar_banco()
        usuario_atual_lower = session.get('usuario', '').lower().strip()
        eh_master = usuario_eh_gestor(usuario_atual_lower)

        if id_inteiro < 0:
            # 🧊 MODO HISTÓRICO
            id_hist = abs(id_inteiro)
            caso_hist = conexao.execute('SELECT id, imovel, status_interno, vencimento FROM historico_fechamento_mes WHERE id = ?', (id_hist,)).fetchone()
            
            if caso_hist:
                status_atual = str(caso_hist['status_interno'] or '').upper()
                if status_atual in STATUS_TRAVADOS_PARA_USUARIO and not eh_master:
                    conexao.close()
                    return jsonify({'sucesso': False, 'msg': '🔒 Operação Bloqueada! Apenas administradores podem alterar o status de um boleto PAGO NA ADM ou PAGO NA 275.'})

                num_imovel = caso_hist['imovel']
                conexao.execute('UPDATE historico_fechamento_mes SET status_interno = ? WHERE id = ?', (status_banco, id_hist))
                
                # Procura o ID real ativo DESTE boleto (imóvel + vencimento) para salvar histórico visual pra equipe
                id_boleto = id_ativo_do_boleto(conexao, num_imovel, caso_hist['vencimento'])
                caso_ativo = {'id': id_boleto} if id_boleto else None
                if caso_ativo:
                    id_real = caso_ativo['id']
                    if status_banco not in ["PAGO", "PAGO NA 275"]:
                        conexao.execute("DELETE FROM produtividade WHERE imovel_id = ? AND acao = 'COLETOU BOLETO'", (id_real,))
                    
                    if status_banco == "COLETADO":
                        conexao.execute('INSERT INTO produtividade (usuario, acao, imovel_id, data_hora) VALUES (?, ?, ?, ?)', (usuario_atual, "COLETOU BOLETO", id_real, data_hora_atual))
                        conexao.execute('INSERT INTO historico_observacoes (imovel_id, data_hora, usuario, texto) VALUES (?, ?, ?, ?)', (id_real, data_hora_atual, usuario_atual, "Status alterado retroativamente para COLETADO."))
                    elif status_banco == "PAGO":
                        conexao.execute('INSERT INTO produtividade (usuario, acao, imovel_id, data_hora) VALUES (?, ?, ?, ?)', (usuario_atual, "PAGOU BOLETO", id_real, data_hora_atual))
                        conexao.execute('INSERT INTO historico_observacoes (imovel_id, data_hora, usuario, texto) VALUES (?, ?, ?, ?)', (id_real, data_hora_atual, usuario_atual, "Status alterado retroativamente para PAGO."))
                    elif status_banco == "PAGO NA 275":
                        conexao.execute('INSERT INTO produtividade (usuario, acao, imovel_id, data_hora) VALUES (?, ?, ?, ?)', (usuario_atual, "PAGOU BOLETO NA 275", id_real, data_hora_atual))
                        conexao.execute('INSERT INTO historico_observacoes (imovel_id, data_hora, usuario, texto) VALUES (?, ?, ?, ?)', (id_real, data_hora_atual, usuario_atual, "Status alterado retroativamente para PAGO NA 275."))
                    elif status_banco is None:
                        conexao.execute('INSERT INTO historico_observacoes (imovel_id, data_hora, usuario, texto) VALUES (?, ?, ?, ?)', (id_real, data_hora_atual, usuario_atual, "Status interno retroativo reiniciado para o padrão."))
                    else:
                        conexao.execute('INSERT INTO produtividade (usuario, acao, imovel_id, data_hora) VALUES (?, ?, ?, ?)', (usuario_atual, f"ALTEROU STATUS PARA {status_banco}", id_real, data_hora_atual))
                        conexao.execute('INSERT INTO historico_observacoes (imovel_id, data_hora, usuario, texto) VALUES (?, ?, ?, ?)', (id_real, data_hora_atual, usuario_atual, f"Status alterado retroativamente para {status_banco}."))
                
                conexao.commit()
                conexao.close()
                return jsonify({'sucesso': True})
            else:
                conexao.close()
                return jsonify({'sucesso': False, 'msg': 'Registro histórico não localizado no banco.'})

        else:
            # 🔥 MODO ATIVO (Mês Atual) - Requer a coluna "vencimento" para não vazar pra outros meses no cofre
            caso = conexao.execute('SELECT id, imovel, status_interno, vencimento FROM imoveis WHERE id = ?', (id_inteiro,)).fetchone()
            
            if caso:
                status_atual = str(caso['status_interno'] or '').upper()
                if status_atual in STATUS_TRAVADOS_PARA_USUARIO and not eh_master:
                    conexao.close()
                    return jsonify({'sucesso': False, 'msg': '🔒 Operação Bloqueada! Apenas administradores podem alterar o status de um boleto PAGO NA ADM ou PAGO NA 275.'})

                num_imovel = caso['imovel']

                # 🔐 Caso com erro de login no Raio-X: COLETADO só depois de informar como o robô entra
                # (login, senha, unidade e bloco) ou que o imóvel não tem mais login.
                # (liberação temporária acima desativa essa checagem até o prazo)
                if status_banco == 'COLETADO' and status_atual != 'COLETADO' and \
                        not coleta_sem_login_liberada() and \
                        erros_ate_resolver_pendentes(conexao, [num_imovel], ('LOGIN',)):
                    conexao.close()
                    return jsonify({'sucesso': False, 'exige_dados_acesso': True, 'imovel': str(num_imovel),
                                    'msg': 'Este imóvel está com erro de login no Raio-X RPA. Para marcar COLETADO, informe '
                                           'login, senha, unidade e bloco (ou marque que não possui mais login).'})

                id_real = id_ativo_do_boleto(conexao, num_imovel, caso['vencimento'], padrao=caso['id'], preferido=caso['id'])

                # A grande correção: O cofre de histórico (historico_fechamento_mes) agora SÓ atualiza meses com o vencimento idêntico
                marco_mud = marco_das_mudancas(conexao)
                # Só a linha clicada muda: linha repetida (mesmo vencimento) é outro boleto, e o vencimento alternativo (05 x 15) também.
                marcar_status_do_boleto(conexao, caso, status_banco)
                assinar_mudancas(conexao, marco_mud, usuario_atual, [num_imovel])
                conexao.execute('UPDATE historico_fechamento_mes SET status_interno = ? WHERE imovel = ? AND vencimento = ?', (status_banco, num_imovel, caso['vencimento']))
                
                # (O restante dos logs de produtividade / historico_observacoes para modo ativo continua igual)
                if status_banco not in ["PAGO", "PAGO NA 275"]:
                    conexao.execute("DELETE FROM produtividade WHERE imovel_id = ? AND acao = 'COLETOU BOLETO'", (id_real,))
                
                if status_banco == "COLETADO":
                    conexao.execute('INSERT INTO produtividade (usuario, acao, imovel_id, data_hora) VALUES (?, ?, ?, ?)', (usuario_atual, "COLETOU BOLETO", id_real, data_hora_atual))
                    conexao.execute('INSERT INTO historico_observacoes (imovel_id, data_hora, usuario, texto) VALUES (?, ?, ?, ?)', (id_real, data_hora_atual, usuario_atual, "Status alterado manualmente para COLETADO."))
                elif status_banco == "PAGO":
                    conexao.execute('INSERT INTO produtividade (usuario, acao, imovel_id, data_hora) VALUES (?, ?, ?, ?)', (usuario_atual, "PAGOU BOLETO", id_real, data_hora_atual))
                    conexao.execute('INSERT INTO historico_observacoes (imovel_id, data_hora, usuario, texto) VALUES (?, ?, ?, ?)', (id_real, data_hora_atual, usuario_atual, "Status alterado manualmente para PAGO."))
                elif status_banco == "PAGO NA 275":
                    conexao.execute('INSERT INTO produtividade (usuario, acao, imovel_id, data_hora) VALUES (?, ?, ?, ?)', (usuario_atual, "PAGOU BOLETO NA 275", id_real, data_hora_atual))
                    conexao.execute('INSERT INTO historico_observacoes (imovel_id, data_hora, usuario, texto) VALUES (?, ?, ?, ?)', (id_real, data_hora_atual, usuario_atual, "Status alterado manualmente para PAGO NA 275."))
                elif status_banco is None:
                    conexao.execute('INSERT INTO historico_observacoes (imovel_id, data_hora, usuario, texto) VALUES (?, ?, ?, ?)', (id_real, data_hora_atual, usuario_atual, "Status interno reiniciado para o padrão ('Status...')."))
                else:
                    conexao.execute('INSERT INTO produtividade (usuario, acao, imovel_id, data_hora) VALUES (?, ?, ?, ?)', (usuario_atual, f"ALTEROU STATUS PARA {status_banco}", id_real, data_hora_atual))
                    conexao.execute('INSERT INTO historico_observacoes (imovel_id, data_hora, usuario, texto) VALUES (?, ?, ?, ?)', (id_real, data_hora_atual, usuario_atual, f"Status alterado manualmente para {status_banco}."))
                    
                conexao.commit()
                conexao.close()
                return jsonify({'sucesso': True})
            else:
                conexao.close()
                return jsonify({'sucesso': False, 'msg': 'Registro não localizado no banco.'})
            
    except Exception as e:
        print(f"🚨 ERRO FATAL AO SALVAR STATUS: {e}", flush=True)
        return jsonify({'sucesso': False, 'msg': str(e)}), 500

@app.route('/atualizar_media_emissao', methods=['POST'])
@login_obrigatorio
def atualizar_media_emissao():
    dados = request.get_json()
    imovel_id = dados.get('id')
    nova_media = dados.get('media')
    
    conexao = conectar_banco()
    
    # 1. Atualiza na tela/mês atual
    conexao.execute('UPDATE imoveis SET media_emissao = ? WHERE id = ?', (nova_media, imovel_id))
    
    # 2. 🎯 MÁGICA DA CORREÇÃO: Garante que o imóvel exista no cadastro mestre antes de atualizar!
    num_imovel_row = conexao.execute('SELECT imovel FROM imoveis WHERE id = ?', (imovel_id,)).fetchone()
    if num_imovel_row:
        imovel_num = num_imovel_row['imovel']
        conexao.execute('INSERT OR IGNORE INTO cadastro_mestre_imoveis (imovel) VALUES (?)', (imovel_num,))
        conexao.execute('UPDATE cadastro_mestre_imoveis SET media_emissao = ? WHERE imovel = ?', (nova_media, imovel_num))
        
    conexao.commit()
    conexao.close()
    return jsonify({'sucesso': True})

@app.route('/api/sistema/atualizar_status_lote', methods=['POST'])
@login_obrigatorio
def atualizar_status_lote():
    dados = request.get_json()
    ids = dados.get('ids', [])
    novo_status = dados.get('status')
    usuario_atual = session.get('usuario', 'Sistema')
    data_hora_atual = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    if not ids or not novo_status: 
        return jsonify({'sucesso': False})

    bloqueados = []
    travados = []
    eh_gestor_lote = usuario_eh_gestor(str(usuario_atual).lower().strip())
    try:
        conexao = conectar_banco()
        # 🔐 COLETADO em lote não passa por cima de erro de login: esses casos precisam dos dados de acesso, um a um
        # (liberação temporária acima desativa essa checagem até o prazo)
        login_pendente = {}
        if novo_status == 'COLETADO' and not coleta_sem_login_liberada():
            numeros = []
            for valor_recebido in ids:
                try:
                    r = conexao.execute('SELECT imovel FROM imoveis WHERE id = ?', (int(valor_recebido),)).fetchone()
                except (TypeError, ValueError):
                    r = None
                if r:
                    numeros.append(str(r['imovel']))
            login_pendente = erros_ate_resolver_pendentes(conexao, numeros, ('LOGIN',))
        for valor_recebido in ids:
            try:
                id_inteiro = int(valor_recebido)
            except:
                continue

            caso = conexao.execute('SELECT id, imovel, vencimento, status_interno FROM imoveis WHERE id = ?', (id_inteiro,)).fetchone()
            if caso:
                id_real = caso['id']
                num_imovel = caso['imovel']
                if str(caso['status_interno'] or '').strip().upper() in STATUS_TRAVADOS_PARA_USUARIO and not eh_gestor_lote:
                    travados.append(str(num_imovel))      # PAGO NA ADM / PAGO NA 275: só gestor muda (igual ao status individual)
                    continue
                if novo_status == 'COLETADO' and str(caso['status_interno'] or '').upper() != 'COLETADO' and \
                        _chave_imovel_raio_x(str(num_imovel).strip()) in login_pendente:
                    bloqueados.append(str(num_imovel))
                    continue

               # 🎯 Só a linha marcada muda: linha repetida e vencimento alternativo são boletos diferentes
                marco_mud = marco_das_mudancas(conexao)
                marcar_status_do_boleto(conexao, caso, novo_status)
                assinar_mudancas(conexao, marco_mud, usuario_atual, [num_imovel])
                
                # 🎯 NOVO: Garante que o lote também atualize meses passados visualizados!
                conexao.execute('UPDATE historico_fechamento_mes SET status_interno = ? WHERE imovel = ? AND vencimento IS ?', (novo_status, num_imovel, caso['vencimento']))
                
                if novo_status != "PAGO":
                    conexao.execute("DELETE FROM produtividade WHERE imovel_id = ? AND acao = 'COLETOU BOLETO'", (id_real,))
                
                if novo_status == "COLETADO":
                    conexao.execute('INSERT INTO produtividade (usuario, acao, imovel_id, data_hora) VALUES (?, ?, ?, ?)', (usuario_atual, "COLETOU BOLETO", id_real, data_hora_atual))
                    conexao.execute('INSERT INTO historico_observacoes (imovel_id, data_hora, usuario, texto) VALUES (?, ?, ?, ?)', (id_real, data_hora_atual, usuario_atual, "Status alterado em lote para COLETADO."))
                elif novo_status == "PAGO":
                    conexao.execute('INSERT INTO produtividade (usuario, acao, imovel_id, data_hora) VALUES (?, ?, ?, ?)', (usuario_atual, "PAGOU BOLETO", id_real, data_hora_atual))
                    conexao.execute('INSERT INTO historico_observacoes (imovel_id, data_hora, usuario, texto) VALUES (?, ?, ?, ?)', (id_real, data_hora_atual, usuario_atual, "Status alterado em lote para PAGO."))
                else:
                    conexao.execute('INSERT INTO produtividade (usuario, acao, imovel_id, data_hora) VALUES (?, ?, ?, ?)', (usuario_atual, f"ALTEROU STATUS EM LOTE PARA {novo_status}", id_real, data_hora_atual))
        conexao.commit()
    except Exception as e:
        print(f"Erro fatal no lote: {e}")
    finally:
        conexao.close()

    if bloqueados or travados:
        unicos = sorted(set(bloqueados))
        fixos = sorted(set(travados))
        partes = []
        if unicos:
            partes.append(f"{len(unicos)} imóvel(is) com erro de login no Raio-X ficaram de fora: "
                          f"{', '.join(unicos[:15])}{' ...' if len(unicos) > 15 else ''}. "
                          "Marque esses um a um para informar login, senha, unidade e bloco.")
        if fixos:
            partes.append(f"🔒 {len(fixos)} imóvel(is) PAGO NA ADM / PAGO NA 275 ficaram de fora "
                          f"({', '.join(fixos[:15])}{' ...' if len(fixos) > 15 else ''}): só gestor altera esses status.")
        return jsonify({'sucesso': True, 'bloqueados': unicos + fixos, 'msg': ' '.join(partes)})
    return jsonify({'sucesso': True})


def _resolver_imovel_e_ciclo(conexao, id_linha):
    """id POSITIVO = linha da tabela imoveis (base ativa); id NEGATIVO = linha do cofre
    (modo histórico, o painel manda -id). Devolve (número do imóvel, 'mm/aaaa' do vencimento)."""
    if id_linha < 0:
        r = conexao.execute('SELECT imovel, vencimento FROM historico_fechamento_mes WHERE id = ?',
                            (abs(id_linha),)).fetchone()
    else:
        r = conexao.execute('SELECT imovel, vencimento FROM imoveis WHERE id = ?', (id_linha,)).fetchone()
    if not r:
        return None, ''
    return r['imovel'], str(r['vencimento'] or '')[3:10]

# 🎯 <int(signed=True)>: no modo histórico o painel envia o id da linha como NEGATIVO (-id do cofre).
# O conversor <int:...> padrão do Flask não aceita número negativo (dava 404 e o painel mostrava
# "Falha ao carregar as anotações do servidor").
@app.route('/buscar_observacoes/<int(signed=True):imovel_id>')
@login_obrigatorio
def buscar_observacoes(imovel_id):
    conexao = conectar_banco()
    try:
        # Busca as anotações do MESMO imóvel no MESMO mês/ano de vencimento (cada atualização da base cria
        # uma linha nova em imoveis, então não dá para usar só o id). Serve para a base ativa e para o histórico.
        imovel_num, ciclo = _resolver_imovel_e_ciclo(conexao, imovel_id)
        if imovel_num is None:
            return jsonify([])
        linhas = conexao.execute('''
            SELECT h.id, h.data_hora, h.usuario, h.texto
            FROM historico_observacoes h
            WHERE h.imovel_id IN (
                    SELECT o2.id FROM imoveis o2
                    WHERE o2.imovel = ?
                      AND substr(IFNULL(o2.vencimento, ''), 4, 7) = ?
                  )
               OR h.imovel_id = ?
            ORDER BY h.data_hora DESC, h.id DESC
        ''', (imovel_num, ciclo, imovel_id)).fetchall()
        return jsonify([dict(l) for l in linhas])
    finally:
        conexao.close()

@app.route('/salvar_observacao', methods=['POST'])
@login_obrigatorio
def salvar_observacao():
    dados = request.get_json(silent=True) or {}
    usuario_atual = session.get('usuario')
    data_hora_atual = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    try:
        id_linha = int(str(dados.get('imovel_id')).strip())
    except ValueError:
        return jsonify({'sucesso': False, 'msg': 'Imóvel inválido.'}), 400
    conexao = conectar_banco()
    try:
        id_gravar = id_linha
        if id_linha < 0:
            # Modo histórico: grava a nota no registro real (imoveis) do mesmo imóvel/mês,
            # para ela aparecer junto com as demais anotações daquele vencimento.
            imovel_num, ciclo = _resolver_imovel_e_ciclo(conexao, id_linha)
            if imovel_num is not None:
                real = conexao.execute(
                    "SELECT id FROM imoveis WHERE imovel = ? AND substr(IFNULL(vencimento, ''), 4, 7) = ? "
                    "ORDER BY ativo DESC, id DESC LIMIT 1", (imovel_num, ciclo)).fetchone()
                if real:
                    id_gravar = real['id']
        conexao.execute('INSERT INTO historico_observacoes (imovel_id, data_hora, usuario, texto) VALUES (?, ?, ?, ?)',
                        (id_gravar, data_hora_atual, usuario_atual, dados.get('texto')))
        conexao.execute('INSERT INTO produtividade (usuario, acao, imovel_id, data_hora) VALUES (?, ?, ?, ?)',
                        (usuario_atual, "ADICIONOU OBSERVACAO", id_gravar, data_hora_atual))
        conexao.commit()
        return jsonify({'sucesso': True})
    finally:
        conexao.close()

@app.route('/disparar_emails', methods=['POST'])
@login_obrigatorio
def disparar_emails():
    dados = request.get_json()
    valor_recebido = dados.get('id')
    usuario_atual = session.get('usuario')
    data_hora_atual = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    try:
        conexao = conectar_banco()
        # 1. Busca os dados do imóvel no banco para descobrir o vencimento
        caso = conexao.execute('SELECT id, imovel, vencimento, status_interno FROM imoveis WHERE (id = ? OR imovel = ?) AND ativo = 1 LIMIT 1', (valor_recebido, str(valor_recebido))).fetchone()

        # 🔒 Boleto já resolvido não recebe pedido à administradora (e o status não é trocado por SOLICITADO)
        if caso and str(caso['status_interno'] or '').strip().upper() in STATUS_FINALIZADOS:
            conexao.close()
            return jsonify({'sucesso': False, 'msg': f"🔒 Bloqueado: o caso já está {str(caso['status_interno']).strip().upper()}. Não se pede boleto à administradora e o status não é alterado."})

        mes_ref = ""
        imovel_id_real = None
        num_imovel_disparo = ''
        
        if caso:
            imovel_id_real = caso['id']
            num_imovel_disparo = caso['imovel']
            venc_str = str(caso['vencimento'] or '')
            
            # 2. Extrai o mês e ano do vencimento (Ex: 08/2026)
            if '/' in venc_str:
                partes = venc_str.split('/')
                if len(partes) >= 2:
                    mes_ref = f"{partes[1]}/{partes[2] if len(partes) == 3 else '2026'}"
            elif '-' in venc_str:
                partes = venc_str.split('-')
                if len(partes) == 3:
                    mes_ref = f"{partes[1]}/{partes[0]}"
        
        # 3. Intercepta e INJETA o Mês no Assunto
        assunto_original = dados.get('assunto', 'Solicitação de Boleto')
        if mes_ref:
            assunto_final = f"Solicitação de Boleto - Envio - Referência de Cobrança ({mes_ref})"
        else:
            assunto_final = assunto_original

        # 4. Intercepta e INJETA o Mês no Corpo do E-mail (HTML)
        mensagem_original = dados.get('mensagem', '')
        if mes_ref and "Referente ao mês" not in mensagem_original:
            if "boleto de condomínio" in mensagem_original:
                mensagem_final = mensagem_original.replace("boleto de condomínio", f"boleto de condomínio <b>(Referente ao mês {mes_ref})</b>")
            else:
                # Fallback caso a mensagem tenha sido alterada no frontend
                mensagem_final = f"<p style='color:#334155; font-weight:bold;'>Referência de Cobrança: Mês {mes_ref}</p>" + mensagem_original
        else:
            mensagem_final = mensagem_original

        # 5. Prepara o E-mail e Dispara (Com suporte a múltiplos e-mails blindado)
        destinatario_cru = str(dados.get('destinatario', ''))
        lista_emails = re.findall(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', destinatario_cru)
        
        if lista_emails:
            destinatario_formatado = ', '.join(lista_emails)
            msg = MIMEMultipart()
            msg['From'] = EMAIL_REMETENTE
            msg['To'] = destinatario_formatado
            msg['Subject'] = assunto_final
            msg.attach(MIMEText(mensagem_final, 'html'))
            
            server = smtplib.SMTP('smtp.gmail.com', 587)
            server.starttls()
            server.login(EMAIL_REMETENTE, SENHA_APP_GMAIL)
            server.sendmail(EMAIL_REMETENTE, lista_emails, msg.as_string())
            server.quit()
        else:
            raise Exception("Nenhum e-mail válido foi encontrado no cadastro da administradora.")
        
        # 6. Registra no Prontuário e Produtividade
        if imovel_id_real:
            conexao.execute("DELETE FROM produtividade WHERE imovel_id = ? AND acao = 'COLETOU BOLETO'", (imovel_id_real,))
            marco_mud = marco_das_mudancas(conexao)
            conexao.execute('UPDATE imoveis SET status_interno = "SOLICITADO" WHERE id = ?', (imovel_id_real,))
            assinar_mudancas(conexao, marco_mud, usuario_atual, [num_imovel_disparo])
            conexao.execute('INSERT INTO historico_observacoes (imovel_id, data_hora, usuario, texto) VALUES (?, ?, ?, ?)', (imovel_id_real, data_hora_atual, usuario_atual, f"E-mail de cobrança enviado para: {dados.get('destinatario')}"))
            conexao.execute('INSERT INTO historico_emails (colaborador, administradora, qtd_imoveis, data_hora) VALUES (?, ?, 1, ?)', (usuario_atual, dados.get('adm'), data_hora_atual))
            conexao.execute('INSERT INTO produtividade (usuario, acao, imovel_id, data_hora) VALUES (?, ?, ?, ?)', (usuario_atual, "DISPAROU EMAIL COBRANCA", imovel_id_real, data_hora_atual))
            
        conexao.commit()
        conexao.close()
        return jsonify({'sucesso': True})
    except Exception as e:
        return jsonify({'sucesso': False, 'msg': str(e)})

@app.route('/api/sistema/resumo_disparos_hoje')
@login_obrigatorio
def api_resumo_disparos_hoje():
    hoje = datetime.now().strftime('%Y-%m-%d')
    conexao = conectar_banco()
    try:
        linhas = conexao.execute(
            'SELECT responsavel, SUM(qtd_imoveis) AS qtd_imoveis FROM log_disparos_diarios WHERE data = ? GROUP BY responsavel ORDER BY qtd_imoveis DESC, responsavel',
            (hoje,)
        ).fetchall()
        total = sum(linha['qtd_imoveis'] or 0 for linha in linhas)
        return jsonify({
            'sucesso': True,
            'data': hoje,
            'total': total,
            'por_responsavel': [{
                'responsavel': linha['responsavel'] or 'Não Atribuído',
                'qtd_imoveis': linha['qtd_imoveis'] or 0
            } for linha in linhas]
        })
    finally:
        conexao.close()


@app.route('/api/sistema/emissoes_hoje_colaboradores')
@login_obrigatorio
def api_emissoes_hoje_colaboradores():
    """Para cada colaborador pedido (?responsavel=A&responsavel=B): quantos casos têm possível emissão hoje,
    quantos já tiveram e-mail disparado hoje, quantos não têm e-mail cadastrado e quantos ainda podem ser enviados."""
    nomes = [n.strip() for n in request.args.getlist('responsavel') if n and n.strip()]
    agora = datetime.now()
    data_hoje = agora.strftime('%Y-%m-%d')

    conexao = conectar_banco()
    try:
        registros = buscar_emissoes_hoje(conexao, agora)
        ja_feitos = imoveis_ja_disparados_hoje(conexao, data_hoje)
        adm_com_email = {
            normalizar_texto(l['administradora'])
            for l in conexao.execute("SELECT administradora FROM vinculos_adm WHERE email IS NOT NULL AND TRIM(email) != ''").fetchall()
        }

        por_resp = {}
        for r in registros:
            por_resp.setdefault(normalizar_texto(r['responsavel']), []).append(r)

        resumo = []
        for nome in nomes:
            casos = por_resp.get(normalizar_texto(nome), [])
            ja = sem_email = 0
            for c in casos:
                if c['id'] in ja_feitos:
                    ja += 1
                elif normalizar_texto(c['administradora']) not in adm_com_email:
                    sem_email += 1
            resumo.append({
                'responsavel': nome,
                'total': len(casos),
                'ja_disparados': ja,
                'sem_email': sem_email,
                'enviaveis': len(casos) - ja - sem_email
            })
        return jsonify({'sucesso': True, 'resumo': resumo})
    finally:
        conexao.close()


def _alvo_do_pedido(tipo, dados, usuario_atual):
    """Decide de quem é a carteira do disparo. Devolve (alvo, chave, erro).
    EMISSAO: sem 'responsavel' = carteira de quem está logado; com 'responsavel' = carteira de outro colaborador.
    LOGIN  : por padrão a carteira de quem está logado; gestor pode escolher outra ou 'todas'."""
    nome_pedido = str(dados.get('responsavel') or '').strip()
    gestor = usuario_eh_gestor(usuario_atual)

    if tipo == 'LOGIN':
        if dados.get('todas'):
            if not gestor:
                return None, None, 'Somente gestores podem solicitar login para todas as carteiras de uma vez.'
            return None, 'TODAS', None
        nome = nome_pedido or usuario_atual
        alvo = resolver_nome_responsavel(nome)
        if not alvo:
            return None, None, f'Colaborador "{nome}" não encontrado nos vínculos de administradoras.'
        if not gestor and normalizar_texto(alvo) != normalizar_texto(usuario_atual):
            return None, None, 'Você só pode solicitar login/senha das administradoras da sua própria carteira.'
        return alvo, normalizar_texto(alvo), None

    alvo = None
    if nome_pedido:
        alvo = resolver_nome_responsavel(nome_pedido)
        if not alvo:
            return None, None, f'Colaborador "{nome_pedido}" não encontrado nos vínculos de administradoras.'
    return alvo, normalizar_texto(alvo or usuario_atual), None


@app.route('/api/sistema/disparos/iniciar', methods=['POST'])
@login_obrigatorio
def api_disparos_iniciar():
    """Começa o disparo em SEGUNDO PLANO e responde na hora com o número do job (a tela acompanha pelo /status)."""
    usuario_atual = session.get('usuario', '')
    dados = request.get_json(silent=True) or {}
    tipo = 'LOGIN' if str(dados.get('tipo') or '').upper() == 'LOGIN' else 'EMISSAO'

    alvo, chave, erro = _alvo_do_pedido(tipo, dados, usuario_atual)
    if erro:
        return jsonify({'sucesso': False, 'msg': erro})
    try:
        job_id, existente = iniciar_job_disparo(tipo, chave, usuario_atual, alvo)
    except Exception as e:
        print(f'🚨 Não foi possível iniciar o disparo ({tipo}, {chave}): {e}', flush=True)
        return jsonify({'sucesso': False, 'msg': f'Não foi possível iniciar o disparo: {e}'})

    if job_id is None:
        # Já há um disparo igual rodando: a tela só passa a acompanhar esse mesmo.
        return jsonify({'sucesso': True, 'ja_rodando': True, 'job_id': existente, 'job': ler_job(existente),
                        'msg': 'Já existe um disparo em andamento para essa carteira. Acompanhe pelo painel.'})
    return jsonify({'sucesso': True, 'job_id': job_id, 'job': ler_job(job_id)})


@app.route('/api/sistema/disparos/status')
@login_obrigatorio
def api_disparos_status():
    job = ler_job(request.args.get('id', type=int) or 0)
    if not job:
        return jsonify({'sucesso': False, 'msg': 'Disparo não encontrado.'}), 404
    return jsonify({'sucesso': True, 'job': job})


@app.route('/api/sistema/disparos/ativos')
@login_obrigatorio
def api_disparos_ativos():
    """Disparos que estão rodando agora (a tela retoma o painel ao recarregar a página)."""
    conexao = conectar_banco()
    try:
        garantir_tabelas_disparo(conexao)
        expirar_jobs_travados(conexao)
        linhas = conexao.execute("SELECT * FROM disparo_jobs WHERE status = 'rodando' ORDER BY id").fetchall()
        usuario = (session.get('usuario') or '').strip().lower()
        jobs = []
        for l in linhas:
            j = _job_para_dict(l)
            j['meu'] = (j.get('usuario') or '').strip().lower() == usuario
            jobs.append(j)
        return jsonify({'sucesso': True, 'jobs': jobs})
    finally:
        conexao.close()


@app.route('/api/sistema/disparos/cancelar', methods=['POST'])
@login_obrigatorio
def api_disparos_cancelar():
    usuario = (session.get('usuario') or '').strip().lower()
    job_id = (request.get_json(silent=True) or {}).get('id')
    try:
        job_id = int(job_id)
    except (TypeError, ValueError):
        return jsonify({'sucesso': False, 'msg': 'Disparo inválido.'})
    conexao = conectar_banco()
    try:
        job = conexao.execute('SELECT usuario, status FROM disparo_jobs WHERE id = ?', (job_id,)).fetchone()
        if not job:
            return jsonify({'sucesso': False, 'msg': 'Disparo não encontrado.'})
        if job['status'] != 'rodando':
            return jsonify({'sucesso': True, 'msg': 'Esse disparo já terminou.'})
        if (job['usuario'] or '').strip().lower() != usuario and not usuario_eh_gestor(usuario):
            return jsonify({'sucesso': False, 'msg': 'Só quem iniciou o disparo (ou um gestor) pode interrompê-lo.'})
        conexao.execute('UPDATE disparo_jobs SET cancelar = 1 WHERE id = ?', (job_id,))
        conexao.commit()
        return jsonify({'sucesso': True, 'msg': 'Interrompendo... os e-mails que já saíram ficam registrados.'})
    finally:
        conexao.close()


@app.route('/api/sistema/solicitacao_acessos/resumo')
@login_obrigatorio
def api_solicitacao_acessos_resumo():
    """Prévia do 'Solicitar logins': quantos casos sem login/senha, quantos dá para enviar e por que o resto fica de fora."""
    usuario_atual = session.get('usuario', '')
    gestor = usuario_eh_gestor(usuario_atual)
    todas = request.args.get('todas') in ('1', 'true', 'on')
    nome = (request.args.get('responsavel') or '').strip() or usuario_atual
    if todas:
        if not gestor:
            return jsonify({'sucesso': False, 'msg': 'Somente gestores podem ver todas as carteiras.'})
        alvo = None
    else:
        alvo = resolver_nome_responsavel(nome)
        if not alvo:
            return jsonify({'sucesso': False, 'msg': f'Colaborador "{nome}" não encontrado nos vínculos de administradoras.'})
        if not gestor and normalizar_texto(alvo) != normalizar_texto(usuario_atual):
            return jsonify({'sucesso': False, 'msg': 'Você só pode ver a sua própria carteira.'})
    try:
        resumo = resumir_solicitacao_acessos(alvo)
    except Exception as e:
        print(f'🚨 Erro ao montar o resumo de solicitação de acessos: {e}', flush=True)
        return jsonify({'sucesso': False, 'msg': str(e)})
    resumo['responsavel'] = alvo
    resumo['pode_todas'] = gestor
    return jsonify(resumo)


@app.route('/api/sistema/enviar_disparos_hoje', methods=['POST'])
@login_obrigatorio
def api_enviar_disparos_hoje():
    """Rota antiga, mantida só por compatibilidade (páginas abertas com a versão anterior da tela).
    Agora inicia o disparo em segundo plano e espera até ~25s pelo resultado; se demorar mais, responde 'em andamento'."""
    usuario_atual = session.get('usuario', '')
    dados = request.get_json(silent=True) or {}
    alvo, chave, erro = _alvo_do_pedido('EMISSAO', dados, usuario_atual)
    if erro:
        return jsonify({'sucesso': False, 'msg': erro})
    try:
        job_id, existente = iniciar_job_disparo('EMISSAO', chave, usuario_atual, alvo)
    except Exception as e:
        return jsonify({'sucesso': False, 'msg': str(e)})
    if job_id is None:
        return jsonify({'sucesso': False, 'msg': f'Já existe um disparo em andamento para a carteira de {(alvo or usuario_atual).title()}. Ele continua rodando em segundo plano; atualize a página em instantes.'})
    for _ in range(50):
        time.sleep(0.5)
        job = ler_job(job_id)
        if job and not job['em_andamento']:
            return jsonify(job.get('resultado') or {'sucesso': job['status'] == 'concluido', 'msg': job.get('msg') or ''})
    return jsonify({'sucesso': True, 'em_andamento': True, 'job_id': job_id, 'qtd_enviados': 0, 'qtd': 0,
                    'msg': 'O disparo continua em segundo plano. Você pode fechar esta janela e seguir trabalhando.'})


# =================================================================
# ROTAS DE CONEXÃO DA API PARA OS AUTOMAÇÃOS EXTERNOS
# =================================================================


@app.route('/api/automacao/consultar_imovel', methods=['POST'])
def api_automacao_consultar_imovel():
    dados = request.get_json()
    if not dados or dados.get('token') != TOKEN_API_ROBO: return jsonify({'sucesso': False, 'msg': 'Acesso Negado'}), 401
    num_imovel = dados.get('imovel')
    conexao = conectar_banco()
    resultado = conexao.execute('SELECT i.imovel, i.administradora, i.status_interno, v.responsavel as colaborador FROM imoveis i LEFT JOIN vinculos_adm v ON v.rowid = vinculo_de(i.administradora) WHERE i.imovel = ? AND i.ativo = 1', (num_imovel,)).fetchone()
    conexao.close()
    if resultado: return jsonify({'sucesso': True, 'valido': True, 'administradora': resultado['administradora'], 'responsavel': resultado['colaborador'] or 'Não Atribuído', 'status_atual': resultado['status_interno'] or 'EM BRANCO'})
    return jsonify({'sucesso': True, 'valido': False, 'msg': 'Imovel nao consta.'})

@app.route('/api/automacao/descobrir_imovel', methods=['POST'])
def api_automacao_descobrir_imovel():
    dados = request.get_json()
    if not dados or dados.get('token') != TOKEN_API_ROBO: return jsonify({'sucesso': False}), 401
    
    # 🧹 FAXINA 1: Limpa o nome do prédio
    def limpar_empreendimento(nome):
        if not nome: return ""
        import unicodedata
        nome = ''.join(c for c in unicodedata.normalize('NFD', str(nome)) if unicodedata.category(c) != 'Mn')
        nome = re.sub(r'[^A-Z0-9 ]', ' ', nome.upper())
        palavras_ignoradas = {'CONDOMINIO', 'COND', 'EDIFICIO', 'EDIF', 'RESIDENCIAL', 'RES', 'CONJUNTO', 'CONJ'}
        return ' '.join([p for p in nome.split() if p not in palavras_ignoradas])

    # 🧹 FAXINA 2: Limpa a Unidade/Bloco
    def limpar_unidade(texto):
        if not texto: return ""
        import unicodedata
        t = ''.join(c for c in unicodedata.normalize('NFD', str(texto)) if unicodedata.category(c) != 'Mn')
        t = re.sub(r'[^A-Z0-9 ]', ' ', t.upper())
        palavras_ignoradas = {'BLOCO', 'BL', 'APTO', 'AP', 'APARTAMENTO', 'UNIDADE', 'UNID', 'CASA', 'CS'}
        return ' '.join([p for p in t.split() if p not in palavras_ignoradas])

    sac_ia = str(dados.get('sacado', '')).strip().upper()
    emp_ia_limpo = limpar_empreendimento(dados.get('empreendimento', ''))
    und_ia_limpa = limpar_unidade(dados.get('unidade', ''))

    conexao = conectar_banco()
    
    # 🚀 OTIMIZAÇÃO EXTREMA: Busca rápida na memória sem CPF
    mestre_raw = conexao.execute("SELECT imovel, administradora, empreendimento FROM cadastro_mestre_imoveis").fetchall()
    imoveis_raw = conexao.execute("SELECT imovel, endereco, sacado, status_interno, ativo, id FROM imoveis").fetchall()
    conexao.close()
    
    dict_imoveis_last = {}
    dict_imoveis_ativo = {}

    for row in imoveis_raw:
        r = dict(row)
        imv_limpo = str(r['imovel']).lstrip('0')
        dict_imoveis_last[imv_limpo] = r
        if r['ativo'] == 1:
            dict_imoveis_ativo[imv_limpo] = r

    candidatos_memoria = []
    for m in mestre_raw:
        m_dict = dict(m)
        imv_mestre_limpo = str(m_dict['imovel']).lstrip('0')
        
        last_data = dict_imoveis_last.get(imv_mestre_limpo, {})
        ativo_data = dict_imoveis_ativo.get(imv_mestre_limpo, {})

        candidatos_memoria.append({
            'imovel': m_dict['imovel'],
            'administradora': m_dict['administradora'],
            'empreendimento': m_dict['empreendimento'],
            'endereco': last_data.get('endereco', ''),
            'sacado': last_data.get('sacado', ''),
            'status_interno': ativo_data.get('status_interno', ''),
            'id_ativo_mes': ativo_data.get('id', None)
        })

    melhor_match = None
    maior_score = 0.0

    # 📡 SISTEMA DE PESOS (0 a 100 pontos) - Totalmente focado em Texto
    for cand in candidatos_memoria:
        score_final = 0.0
            
        # 1. Nota do Sacado (Vale até 30 Pontos)
        score_sacado = 0.0
        sac_banco = str(cand['sacado'] or '').strip().upper()
        if sac_ia and sac_banco:
            score_sacado = difflib.SequenceMatcher(None, sac_ia, sac_banco).ratio() * 30.0
            
        # 2. Nota do Empreendimento (Vale até 40 Pontos)
        score_emp = 0.0
        emp_banco_limpo = limpar_empreendimento(cand['empreendimento'])
        if emp_ia_limpo and emp_banco_limpo:
            score_emp = difflib.SequenceMatcher(None, emp_ia_limpo, emp_banco_limpo).ratio() * 40.0
            
        # 3. Nota da Unidade/Bloco (Vale até 30 Pontos)
        score_und = 0.0
        end_banco_limpo = limpar_unidade(cand['endereco'])
        if und_ia_limpa and end_banco_limpo:
            # Se bater exato (ex: "B 66" dentro de "RUA TAL, APTO B 66")
            if und_ia_limpa in end_banco_limpo or end_banco_limpo in und_ia_limpa:
                score_und = 30.0 
            else:
                score_und = difflib.SequenceMatcher(None, und_ia_limpa, end_banco_limpo).ratio() * 30.0
            
        # 4. SOMA FINAL DO BOLETO (0 a 100)
        score_final = score_sacado + score_emp + score_und
        
        if score_final > maior_score:
            maior_score = score_final
            melhor_match = cand

    # Se atingiu o score mínimo de aprovação (>= 72 pontos)
    if melhor_match and maior_score >= 72.0:
        con2 = conectar_banco()
        v = con2.execute("SELECT responsavel FROM vinculos_adm WHERE rowid = vinculo_de(?)", (str(melhor_match['administradora'] or ''),)).fetchone()
        resp = v['responsavel'] if v else 'Não Atribuído'
        con2.close()
        
        status_final = melhor_match['status_interno']
        if not melhor_match['id_ativo_mes']:
            status_final = 'FORA DA CARTEIRA DO MÊS'
        elif not status_final or status_final.strip() == '':
            status_final = 'PENDENTE'
            
        return jsonify({
            'sucesso': True,
            'valido': True,
            'imovel_real': melhor_match['imovel'],
            'administradora': melhor_match['administradora'],
            'responsavel': resp,
            'status_atual': status_final,
            'score': round(maior_score, 2)
        })
        
    # Se não atingiu 72, recusa mas devolve o ID para a auditoria
    if melhor_match:
        return jsonify({'sucesso': True, 'valido': False, 'msg': f"Maior similaridade foi {round(maior_score, 2)}% com o ID {melhor_match['imovel']} (Abaixo dos 72% exigidos)."})
        
    return jsonify({'sucesso': True, 'valido': False, 'msg': 'Nenhuma similaridade encontrada no Cadastro Mestre.'})


@app.route('/api/automacao/pendencias', methods=['POST'])
def api_automacao_pendencias():
    dados = request.get_json()
    if not dados or dados.get('token') != TOKEN_API_ROBO: return jsonify({'sucesso': False}), 401
    conexao = conectar_banco()
    query = "SELECT i.imovel, i.administradora, i.vencimento, i.empreendimento, i.sacado, i.cpf_cnpj, i.d_lote, i.endereco FROM imoveis i LEFT JOIN vinculos_adm v ON v.rowid = vinculo_de(i.administradora) WHERE i.ativo = 1 AND " + sql_livre_para_automacao('i.status_interno')
    params = []
    if dados.get('colaborador'):
        query += " AND TRIM(v.responsavel) = ?"
        params.append(dados.get('colaborador').strip())
    query += SQL_UMA_LINHA_POR_BOLETO       # boleto com desconto = 1 item; vencimento alternativo = itens separados
    resultado = conexao.execute(query, params).fetchall()
    todos = _vencimentos_por_imovel(conexao)
    # 🔐 CPF que o colaborador informou no Raio-X para abrir o PDF (o FinMail tenta esse primeiro)
    garantir_colunas_acesso(conexao)
    cpf_pdf = {_chave_imovel_raio_x(str(r['imovel']).strip()): (r['cpf_senha_pdf'], r['regra_senha_pdf'] or '')
               for r in conexao.execute("SELECT imovel, cpf_senha_pdf, regra_senha_pdf FROM cadastro_mestre_imoveis "
                                        "WHERE TRIM(IFNULL(cpf_senha_pdf, '')) <> ''").fetchall()}
    conexao.close()
    lista = []
    for row in resultado:
        item = dict(row)
        item['vencimentos_base'] = todos.get(item['imovel'], [])     # todos os vencimentos do imóvel, inclusive os já resolvidos
        cpf_info = cpf_pdf.get(_chave_imovel_raio_x(str(item['imovel']).strip()))
        if cpf_info:
            item['cpf_senha_pdf'], item['regra_senha_pdf'] = cpf_info
        lista.append(item)
    return jsonify({'sucesso': True, 'quantidade': len(lista), 'dados': lista})

@app.route('/api/automacao/mes_alvo', methods=['POST'])
def api_automacao_mes_alvo():
    """Mês (e ano) da base ativa = mês de vencimento dominante. O robô de e-mail usa isso como alvo
    quando não recebe um mês pelo painel — assim acompanha a virada de mês sem editar código."""
    dados = request.get_json(silent=True)
    if not dados or dados.get('token') != TOKEN_API_ROBO:
        return jsonify({'sucesso': False, 'msg': 'Acesso Negado'}), 401
    conexao = conectar_banco()
    try:
        dominante, qtd, total = mes_alvo_da_base_ativa(conexao)
    finally:
        conexao.close()
    if not dominante:
        return jsonify({'sucesso': False, 'msg': 'Base ativa sem vencimentos válidos.'})
    return jsonify({'sucesso': True, 'mes': f"{dominante[1]:02d}", 'ano': dominante[0],
                    'rotulo': virada_mes.rotulo_mes(dominante[0], dominante[1]), 'qtd': qtd, 'total': total})

@app.route('/api/automacao/dados_liax', methods=['POST'])
def api_automacao_dados_liax():
    dados = request.get_json()
    
    # Verifica o token de segurança usando a variável global do seu Flask
    if not dados or dados.get('token') != TOKEN_API_ROBO: 
        return jsonify({'sucesso': False, 'msg': 'Acesso Negado'}), 401
    
    num_imovel = dados.get('imovel')
    conexao = conectar_banco()
    
    caso = conexao.execute('''
        SELECT empreendimento, endereco, sacado, cpf_cnpj, administradora 
        FROM imoveis 
        WHERE LTRIM(imovel, '0') = LTRIM(?, '0') AND ativo = 1 LIMIT 1
    ''', (num_imovel,)).fetchone()
    
    conexao.close()
    
    if caso:
        return jsonify({
            'sucesso': True,
            'empreendimento': caso['empreendimento'] or '',
            'endereco': caso['endereco'] or '',
            'sacado': caso['sacado'] or '',
            'cpf_cnpj': caso['cpf_cnpj'] or '',
            'administradora': caso['administradora'] or ''
        })
        
    return jsonify({'sucesso': False, 'msg': 'Imóvel não encontrado'})

@app.route('/api/sistema/corrigir_coletas_passadas')
@login_obrigatorio
def corrigir_coletas_passadas():
    conexao = conectar_banco()
    try:
        # 🎯 CAÇA-FANTASMAS PELO LINK: Procura o rastro (http) na coluna 'observacao' (a sua Obs Ficha)
        query_update = """
            UPDATE imoveis 
            SET status_interno = 'COLETADO'
            WHERE ativo = 1 
            AND UPPER(TRIM(IFNULL(status_interno, ''))) NOT IN """ + _SQL_FORA_DA_AUTOMACAO + """
            AND observacao LIKE '%http%'
        """
        
        cursor = conexao.execute(query_update)
        linhas_afetadas = cursor.rowcount
        conexao.commit()
        
        return f"""
            <div style='font-family: Arial; text-align: center; margin-top: 50px;'>
                <h1 style='color: #10b981;'>✔️ Varredura por Links Concluída!</h1>
                <p><b>{linhas_afetadas}</b> boletos com link na Obs Ficha foram atualizados para COLETADO na tela da equipe!</p>
                <a href='/' style='display: inline-block; margin-top: 20px; padding: 10px 20px; background-color: #0f172a; color: white; text-decoration: none; border-radius: 6px;'>Voltar ao Painel</a>
            </div>
        """
    except Exception as e:
        return f"Erro ao corrigir base: {e}"
    finally:
        conexao.close()

# ==========================================================================================
# 🤖 ROBÔ WINKER: pendentes com a unidade já vinculada ao site (cadastro_mestre_imoveis.winker_status = 'TEM')
# Só devolve imóveis ATIVOS do ciclo que ainda NÃO estão COLETADO/PAGO. Quem já foi coletado nunca volta aqui.
# (A coluna winker_status é criada pelo script importar_winker_para_banco.py)
# ==========================================================================================
@app.route('/api/automacao/pendencias_winker', methods=['POST'])
def api_automacao_pendencias_winker():
    dados = request.get_json()
    if not dados or dados.get('token') != TOKEN_API_ROBO:
        return jsonify({'sucesso': False, 'msg': 'Acesso Negado'}), 401

    encerrados = _SQL_FORA_DA_AUTOMACAO         # 🔒 resolvidos + CHEQUE/DOC: o robô nem recebe esses casos
    filtro_colab, params = "", []
    if dados.get('colaborador'):
        filtro_colab = " AND TRIM(v.responsavel) = ?"
        params.append(dados.get('colaborador').strip())

    # Query otimizada sem LTRIM no JOIN e sem subquery NOT EXISTS redundante
    query = f"""
        SELECT i.imovel, GROUP_CONCAT(DISTINCT i.vencimento) AS venc_pendentes, i.administradora,
               m.empreendimento AS empreendimento, m.winker_condominio, m.winker_predio, m.winker_unidade
        FROM imoveis i
        JOIN cadastro_mestre_imoveis m ON m.imovel = i.imovel
        LEFT JOIN vinculos_adm v ON v.rowid = vinculo_de(i.administradora)
        WHERE i.ativo = 1
          AND UPPER(TRIM(m.portal)) = 'WINKER'
          AND m.winker_status = 'TEM'
          AND COALESCE(TRIM(m.winker_condominio), '') <> ''
          AND COALESCE(TRIM(m.winker_unidade), '') <> ''
          AND UPPER(TRIM(IFNULL(i.status_interno, ''))) NOT IN {encerrados}
          {filtro_colab}
        GROUP BY i.imovel
        ORDER BY m.winker_condominio, i.imovel
    """
    conexao = conectar_banco()
    try:
        resultado = conexao.execute(query, params).fetchall()
        todos = _vencimentos_por_imovel(conexao)
    except Exception as e:
        return jsonify({'sucesso': False, 'msg': f'Consulta falhou: {e}'}), 500
    finally:
        conexao.close()
    lista = []
    for row in resultado:
        item = dict(row)
        # Vencimentos que ainda faltam coletar neste imóvel (os já coletados/pagos nem entram aqui).
        # 'vencimento' = o mais próximo, por compatibilidade com o robô antigo.
        pend = sorted({_venc_texto(v) or v.strip() for v in str(item.pop('venc_pendentes') or '').split(',') if v.strip()},
                      key=lambda v: (_data_br(v) or datetime.max, v))
        item['vencimentos_pendentes'] = pend
        item['vencimentos_base'] = todos.get(item['imovel'], [])      # todos os vencimentos do imóvel, inclusive os já resolvidos
        item['vencimento'] = pend[0] if pend else ''
        lista.append(item)
    return jsonify({'sucesso': True, 'quantidade': len(lista), 'dados': lista})

@app.route('/api/automacao/coletar', methods=['POST'])
def api_automacao_coletar():
    """O robô avisa que baixou um boleto. O boleto é identificado por IMÓVEL + VENCIMENTO:
    - linhas repetidas (mesmo vencimento)   -> são boletos diferentes: o robô fecha UMA linha por aviso;
    - vencimento alternativo (05 e 15)      -> só o vencimento baixado muda; o outro continua pendente.
    O robô deve mandar 'vencimento' (dd/mm/aaaa). Sem ele, só dá para marcar quando o imóvel
    tem um único vencimento pendente; com dois, a resposta pede o vencimento em vez de chutar."""
    dados = request.get_json()
    if not dados or dados.get('token') != TOKEN_API_ROBO: 
        return jsonify({'sucesso': False}), 401
    
    # 1. Limpeza bruta do número do imóvel
    imovel_cru = str(dados.get('imovel', '')).strip()
    status_novo = str(dados.get('status', 'COLETADO')).strip().upper()
    nova_observacao = str(dados.get('observacao', '')).strip()
    nome_robo = str(dados.get('nome_robo', 'AUTOMAÇÃO DESCONHECIDO')).strip().upper()
    vencimento_robo = str(dados.get('vencimento', '') or '').strip()

    if not imovel_cru:
        return jsonify({'sucesso': False, 'msg': 'ID do imóvel vazio.'}), 400

    conexao = conectar_banco()
    
    try:
        # 2. Linhas ativas do imóvel, ignorando zeros à esquerda (ex: 0303102 = 303102)
        linhas = [dict(l) for l in conexao.execute(
            "SELECT id, imovel, vencimento, status_interno, observacao FROM imoveis "
            "WHERE LTRIM(imovel, '0') = LTRIM(?, '0') AND ativo = 1 ORDER BY id", (imovel_cru,)).fetchall()]
        escolha = escolher_linhas_do_boleto(linhas, vencimento_robo)
        base_resposta = {'vencimento': escolha['vencimento'], 'vencimentos_pendentes': escolha['pendentes']}

        if escolha['erro']:
            return jsonify({'sucesso': False, 'msg': escolha['erro'], **base_resposta})
        if escolha['ja_resolvido']:
            # Já estava coletado/pago: não sobrescreve (antes o robô podia rebaixar um PAGO para COLETADO)
            return jsonify({'sucesso': True, 'ja_resolvido': True, 'cheque': escolha.get('cheque', False),
                            'aviso': escolha['aviso'], **base_resposta})

        por_id = {l['id']: l for l in linhas}
        ids = escolha['ids']
        imovel_id = max(ids)                       # onde a produtividade e o prontuário ficam (a linha que acabou de fechar)
        rotulo_venc = f" (vencimento {escolha['vencimento']})" if escolha['vencimento'] else ''
        data_hora = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        # 3. Atualiza só as linhas DESTE boleto, mesclando a observação (não apaga o que o humano escreveu)
        marco_mud = marco_das_mudancas(conexao)
        for id_linha in ids:
            obs_antiga = por_id[id_linha]['observacao'] or ""
            obs_final = obs_antiga
            if nova_observacao and nova_observacao not in obs_antiga:
                obs_final = f"{obs_antiga} | {nova_observacao}" if obs_antiga else nova_observacao
            # 🔒 a trava vale também no UPDATE: se alguém resolveu (ou marcou cheque) neste instante, não sobrescreve
            conexao.execute("UPDATE imoveis SET status_interno = ?, observacao = ? WHERE id = ? AND " + sql_livre_para_automacao(),
                            (status_novo, obs_final, id_linha))
        assinar_mudancas(conexao, marco_mud, nome_robo, [linhas[0]['imovel']])

        # Limpa produtividade velha se for um recadastro
        conexao.execute("DELETE FROM produtividade WHERE imovel_id = ? AND acao = 'COLETOU BOLETO'", (imovel_id,))
        
        # Histórico do Prontuário
        texto = f"Processado via automação. Status atribuído: {status_novo}{rotulo_venc}"
        if escolha['aviso']:
            texto += f" | {escolha['aviso']}"
        conexao.execute('INSERT INTO historico_observacoes (imovel_id, data_hora, usuario, texto) VALUES (?, ?, ?, ?)', 
                        (imovel_id, data_hora, nome_robo, texto))
        
        # Produtividade Real
        if status_novo == "COLETADO":
            conexao.execute('INSERT INTO produtividade (usuario, acao, imovel_id, data_hora) VALUES (?, ?, ?, ?)', 
                            (nome_robo, "COLETOU BOLETO", imovel_id, data_hora))
            
        conexao.commit()
        restantes = [v for v in escolha['pendentes'] if v != escolha['vencimento']]
        if escolha.get('linhas_abertas', 0) > 1:      # ainda sobra outra linha (outro boleto) neste mesmo vencimento
            restantes = sorted(restantes + [escolha['vencimento']], key=lambda v: (_data_br(v) or datetime.max, v))
        return jsonify({'sucesso': True, 'aviso': escolha['aviso'], 'linhas_atualizadas': len(ids),
                        'vencimento': escolha['vencimento'], 'vencimentos_pendentes': restantes})
            
    except Exception as e:
        print(f"Erro fatal na coleta: {e}", flush=True)
        return jsonify({'sucesso': False, 'msg': str(e)}), 500
    finally:
        conexao.close()

# ==========================================================================================
# 📄 PAINEL DE AUTOMAÇÕES: lista os boletos/imóveis de um robô (pendentes, coletados, pagos)
# Usado pelo botão "Ver boletos" da tela de Automações (pop-up).  GET /api/automacao/boletos_robo?robo=WINKER
# ==========================================================================================
@app.route('/api/automacao/boletos_robo')
@login_obrigatorio
def api_automacao_boletos_robo():
    robo = (request.args.get('robo') or '').strip().upper()
    if robo not in ('WINKER', 'WEBWARE', 'SUPERLOGICA'):
        return jsonify({'sucesso': False, 'msg': 'Robô sem lista de boletos.'}), 400

    # No cadastro o portal aparece como 'SUPERLÓGICA' (com acento, que o UPPER do SQLite não mexe),
    # e alguns 'DIVERSOS' com link do superlogica.net: mesmo critério do robô.
    if robo == 'SUPERLOGICA':
        filtro_portal = ("(UPPER(TRIM(m.portal)) LIKE 'SUPERL%' "
                         "OR IFNULL(m.link_portal, '') LIKE '%superlogica.net%')")
        parametros = ()
    else:
        filtro_portal = "UPPER(TRIM(m.portal)) = ?"
        parametros = (robo,)

    query = """
        SELECT MIN(i.id) AS rid, i.imovel, MAX(i.vencimento) AS vencimento, i.administradora,
               COALESCE(NULLIF(TRIM(m.empreendimento), ''), MAX(i.empreendimento)) AS empreendimento,
               m.winker_condominio, m.winker_predio, m.winker_unidade, m.winker_status,
               m.super_condominio, m.super_bloco, m.super_unidade, m.super_status,
               CASE
                 -- com vencimento alternativo (05 e 15): basta UM vencimento sem coleta/pagamento para o imóvel seguir pendente
                 WHEN EXISTS (SELECT 1 FROM imoveis x WHERE x.ativo = 1 AND LTRIM(x.imovel, '0') = LTRIM(i.imovel, '0')
                              AND IFNULL(x.status_interno, '') NOT IN ('PAGO', 'PAGO NA ADM', 'PAGO NA 275', 'COLETADO')
                              AND NOT EXISTS (SELECT 1 FROM imoveis y WHERE y.ativo = 1 AND LTRIM(y.imovel, '0') = LTRIM(i.imovel, '0')
                                              AND IFNULL(TRIM(y.vencimento), '') = IFNULL(TRIM(x.vencimento), '')
                                              AND y.status_interno IN ('PAGO', 'PAGO NA ADM', 'PAGO NA 275', 'COLETADO'))) THEN 'PENDENTE'
                 WHEN EXISTS (SELECT 1 FROM imoveis x WHERE x.ativo = 1 AND LTRIM(x.imovel, '0') = LTRIM(i.imovel, '0')
                              AND x.status_interno IN ('PAGO', 'PAGO NA ADM', 'PAGO NA 275')) THEN 'PAGO'
                 WHEN EXISTS (SELECT 1 FROM imoveis x WHERE x.ativo = 1 AND LTRIM(x.imovel, '0') = LTRIM(i.imovel, '0')
                              AND x.status_interno = 'COLETADO') THEN 'COLETADO'
                 ELSE 'PENDENTE'
               END AS situacao,
               MAX(i.status_interno) AS status_interno, MAX(i.observacao) AS observacao
        FROM imoveis i
        JOIN cadastro_mestre_imoveis m ON LTRIM(m.imovel, '0') = LTRIM(i.imovel, '0')
        WHERE i.ativo = 1 AND __FILTRO_PORTAL__
        GROUP BY i.imovel
        ORDER BY empreendimento, i.imovel
    """.replace('__FILTRO_PORTAL__', filtro_portal)
    conexao = conectar_banco()
    try:
        linhas = [dict(r) for r in conexao.execute(query, parametros).fetchall()]
        # quem coletou e quando (último registro de produtividade de cada imóvel)
        coleta = {}
        for r in conexao.execute("SELECT imovel_id, usuario, data_hora FROM produtividade "
                                 "WHERE acao = 'COLETOU BOLETO' ORDER BY id"):
            coleta[str(r['imovel_id'])] = (r['usuario'], r['data_hora'])
    except Exception as e:
        return jsonify({'sucesso': False, 'msg': f'Consulta falhou: {e}'}), 500
    finally:
        conexao.close()

    resumo = {'PENDENTE': 0, 'COLETADO': 0, 'PAGO': 0}
    for l in linhas:
        resumo[l['situacao']] += 1
        por, quando = coleta.get(str(l.pop('rid')), ('', ''))
        l['coletado_por'], l['coletado_em'] = por, quando
        obs = l.get('observacao') or ''
        l['observacao'] = obs[:160]
        # vínculo com o site (só faz sentido para a Winker): o robô só visita imóveis com vínculo "TEM"
        if robo == 'WINKER':
            l['pronto_robo'] = (l.get('winker_status') == 'TEM' and bool((l.get('winker_condominio') or '').strip())
                                and bool((l.get('winker_unidade') or '').strip()))
        elif robo == 'SUPERLOGICA':
            l['site'] = ' / '.join(x.strip() for x in (l.get('super_condominio'), l.get('super_bloco'),
                                                       l.get('super_unidade')) if x and x.strip())
            status_site = unicodedata.normalize('NFKD', str(l.get('super_status') or '')).encode(
                'ascii', 'ignore').decode('ascii').strip().upper()
            # o robô pula 'NÃO TEM' e precisa da unidade para buscar o boleto certo
            l['pronto_robo'] = status_site != 'NAO TEM' and bool((l.get('super_unidade') or '').strip())
    return jsonify({'sucesso': True, 'robo': robo, 'total': len(linhas),
                    'resumo': {'pendentes': resumo['PENDENTE'], 'coletados': resumo['COLETADO'], 'pagos': resumo['PAGO']},
                    'dados': linhas})


@app.route('/api/automacao/pagamentos_pendentes', methods=['POST'])
def api_automacao_pagamentos_pendentes():
    dados = request.get_json()
    if not dados or dados.get('token') != TOKEN_API_ROBO: return jsonify({'sucesso': False}), 401
    
    conexao = conectar_banco()
    
    # 🎯 CORREÇÃO: Agora o Python manda o nome do responsável para o automação poder filtrar!
    # Cada linha da planilha é um boleto: o robô recebe o 'id' da linha para avisar EXATAMENTE qual pagou.
    query = """
        SELECT i.id, i.imovel, i.administradora, i.vencimento, i.empreendimento, i.sacado, i.cpf_cnpj, IFNULL(v.responsavel, 'Não Atribuído') as responsavel 
        FROM imoveis i 
        LEFT JOIN vinculos_adm v ON v.rowid = vinculo_de(i.administradora) 
        WHERE i.ativo = 1 AND i.status_interno = 'COLETADO'
        ORDER BY i.imovel, i.id
    """
    
    resultado = conexao.execute(query).fetchall()
    conexao.close()
    
    lista = [dict(row) for row in resultado]
    return jsonify({'sucesso': True, 'quantidade': len(lista), 'dados': lista})

STATUS_PAGOS = ('PAGO', 'PAGO NA ADM', 'PAGO NA 275')


def escolher_linha_para_pagar(linhas, id_linha=None, vencimento_robo=''):
    """Qual linha o robô de pagamentos acabou de lançar? Função PURA (testável) usada por /api/automacao/pagar.
    linhas = linhas ativas do imóvel [{'id','vencimento','status_interno'}]. Cada linha é um boleto com status próprio.
    Devolve {'id', 'ja_resolvido', 'erro'}:
      - com 'id' (o robô novo manda): é aquela linha; se ela não estiver mais COLETADO (alguém mexeu, ou o aviso
        chegou duas vezes) NADA é sobrescrito -> ja_resolvido;
      - sem 'id' ou com id de linha antiga (base reimportada): acha pelo vencimento entre as COLETADO; sem vencimento,
        a primeira COLETADO (o robô antigo avisa uma vez por linha, então cada aviso fecha a seguinte)."""
    status = lambda l: str(l['status_interno'] or '').strip().upper()   # noqa: E731
    r = {'id': None, 'ja_resolvido': False, 'erro': ''}
    if not linhas:
        r['erro'] = 'Imóvel não localizado.'
        return r
    if id_linha not in (None, ''):
        try:
            id_linha = int(id_linha)
        except (TypeError, ValueError):
            id_linha = None
        por_id = {int(l['id']): l for l in linhas}
        if id_linha in por_id:
            if status(por_id[id_linha]) == 'COLETADO':
                r['id'] = id_linha
            else:
                r['id'], r['ja_resolvido'] = id_linha, True
            return r
    abertas = sorted((l for l in linhas if status(l) == 'COLETADO'), key=lambda l: int(l['id']))
    informado = _venc_texto(vencimento_robo)
    if informado:
        do_venc = [l for l in abertas if (_venc_texto(l['vencimento']) or str(l['vencimento'] or '').strip()) == informado]
        if do_venc:
            r['id'] = int(do_venc[0]['id'])
            return r
        if any((_venc_texto(l['vencimento']) or '') == informado and status(l) in STATUS_PAGOS for l in linhas):
            r['ja_resolvido'] = True
            return r
    if abertas:
        if informado and len(abertas) > 1:
            r['erro'] = (f'O boleto lançado vence em {informado}, que não bate com nenhuma das {len(abertas)} linhas '
                         f'COLETADO deste imóvel. Marque pelo painel.')
            return r
        r['id'] = int(abertas[0]['id'])
        return r
    if any(status(l) in STATUS_PAGOS or status(l) == 'ERRO PAGAMENTO' for l in linhas):
        r['ja_resolvido'] = True
        return r
    r['erro'] = 'Nenhuma linha deste imóvel está COLETADO: nada foi alterado.'
    return r


@app.route('/api/automacao/pagar', methods=['POST'])
def api_automacao_pagar():
    """O robô de pagamentos avisa o resultado de UM boleto. Cada linha da planilha é um boleto: o aviso fecha só a
    linha certa (pelo 'id' que veio na fila, ou imóvel + vencimento) e NUNCA sobrescreve uma linha que já foi
    resolvida por outra via. Avisar duas vezes é inofensivo (o robô repete o aviso quando o site demora)."""
    dados = request.get_json()
    if not dados or dados.get('token') != TOKEN_API_ROBO: return jsonify({'sucesso': False}), 401

    num_imovel = str(dados.get('imovel') or '').strip()
    deu_certo = bool(dados.get('sucesso'))
    msg_erro = dados.get('erro_msg') or 'Motivo não especificado.'
    nome_robo = dados.get('nome_robo', 'AUTOMAÇÃO PAGAMENTOS')
    id_linha = dados.get('id')
    vencimento_robo = str(dados.get('vencimento') or '').strip()
    if not num_imovel and id_linha in (None, ''):
        return jsonify({'sucesso': False, 'msg': 'Informe o imóvel (ou o id da linha).'}), 400

    conexao = conectar_banco()
    try:
        if not num_imovel:
            achou = conexao.execute('SELECT imovel FROM imoveis WHERE id = ?', (id_linha,)).fetchone()
            num_imovel = str(achou['imovel']) if achou else ''
        linhas = [dict(l) for l in conexao.execute(
            "SELECT id, imovel, vencimento, status_interno FROM imoveis "
            "WHERE LTRIM(imovel, '0') = LTRIM(?, '0') AND ativo = 1 ORDER BY id", (num_imovel,)).fetchall()]
        escolha = escolher_linha_para_pagar(linhas, id_linha, vencimento_robo)
        if escolha['erro']:
            codigo = 404 if escolha['erro'].startswith('Imóvel não localizado') else 409
            return jsonify({'sucesso': False, 'msg': escolha['erro']}), codigo
        if escolha['ja_resolvido']:
            return jsonify({'sucesso': True, 'ja_resolvido': True, 'id': escolha['id']})

        por_id = {int(l['id']): l for l in linhas}
        caso = por_id.get(escolha['id']) or linhas[0]
        marco_mud = marco_das_mudancas(conexao)
        ids = ids_alvo_da_linha(conexao, caso)
        imovel_id = max(ids)                       # onde o histórico e a produtividade ficam
        data_hora = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        rotulo_venc = f" (vencimento {_venc_texto(caso['vencimento'])})" if _venc_texto(caso['vencimento']) else ''
        novo_status = 'PAGO' if deu_certo else 'ERRO PAGAMENTO'
        conexao.execute(f"UPDATE imoveis SET status_interno = ? WHERE id IN ({','.join('?' * len(ids))})", [novo_status] + ids)
        assinar_mudancas(conexao, marco_mud, nome_robo, [caso['imovel']])
        if deu_certo:
            conexao.execute('INSERT INTO historico_observacoes (imovel_id, data_hora, usuario, texto) VALUES (?, ?, ?, ?)',
                            (imovel_id, data_hora, nome_robo, f"Boleto liquidado/pago com sucesso via automação{rotulo_venc}."))
            conexao.execute('INSERT INTO produtividade (usuario, acao, imovel_id, data_hora) VALUES (?, ?, ?, ?)',
                            (nome_robo, "PAGOU BOLETO", imovel_id, data_hora))
        else:
            conexao.execute('INSERT INTO historico_observacoes (imovel_id, data_hora, usuario, texto) VALUES (?, ?, ?, ?)',
                            (imovel_id, data_hora, nome_robo, f"⚠️ FALHA NO PAGAMENTO{rotulo_venc}: {msg_erro}"))
        conexao.commit()
        return jsonify({'sucesso': True, 'id': escolha['id'], 'linhas_atualizadas': len(ids)})
    except Exception as e:
        print(f"Erro fatal na baixa de pagamento: {e}", flush=True)
        return jsonify({'sucesso': False, 'msg': str(e)}), 500
    finally:
        conexao.close()

# =================================================================
# ORQUESTRADOR DE AUTOMAÇÃOS (CENTRAL DE RPA MULTI-VIAS)
# =================================================================
@app.route('/modulo_automacoes')
@login_obrigatorio
def modulo_automacoes():
    conexao = conectar_banco()
    # Garante que a tabela de configurações gerais existe
    conexao.execute('CREATE TABLE IF NOT EXISTS config_sistema (chave TEXT PRIMARY KEY, valor TEXT)')
    garantir_agenda_3x(conexao)

    # Puxa os horários salvos do banco
    horarios_raw = conexao.execute("SELECT chave, valor FROM config_sistema WHERE chave LIKE 'horario_robo_%'").fetchall()
    horarios = {row['chave']: row['valor'] for row in horarios_raw}
    
    # 🎯 NOVO: Busca as administradoras em ordem alfabética para o menu suspenso
    adms_raw = conexao.execute("SELECT DISTINCT administradora FROM vinculos_adm WHERE administradora IS NOT NULL AND administradora != '' ORDER BY administradora ASC").fetchall()
    lista_adms = [row['administradora'] for row in adms_raw]
    
    conexao.close()
    
    # Envia a lista_adms para o HTML desenhar o menu
    return render_template('automacoes.html', horarios=horarios, lista_adms=lista_adms,
                           agenda=proximas_execucoes())

@app.route('/api/sistema/agenda_robos')
@login_obrigatorio
def api_agenda_robos():
    return jsonify({'sucesso': True, 'agenda': proximas_execucoes()})


@app.route('/api/sistema/salvar_horarios_robos', methods=['POST'])
@login_obrigatorio
def salvar_horarios_robos():
    dados = request.get_json() or {}
    conexao = conectar_banco()
    conexao.execute('CREATE TABLE IF NOT EXISTS config_sistema (chave TEXT PRIMARY KEY, valor TEXT)')
    garantir_agenda_3x(conexao)      # a regra de 3x ao dia nunca pode voltar por cima do que a pessoa gravar agora

    try:
        # Salva o horário de cada automação individualmente
        for robo, horario in dados.items():
            texto = str(horario or '').strip()
            if robo == 'finmail_minutos':
                conexao.execute("INSERT OR REPLACE INTO config_sistema (chave, valor) VALUES ('intervalo_robo_finmail', ?)",
                                (texto if texto.isdigit() else '',))
                continue
            horas, invalidos = _horas_validas(texto)
            if invalidos:
                return jsonify({'sucesso': False, 'msg': f"Horário inválido para {robo}: '{', '.join(invalidos)}'. "
                                                         "Use HH:MM (vários separados por vírgula, ex.: 09:00, 14:00, 18:00) ou deixe vazio."})
            conexao.execute("INSERT OR REPLACE INTO config_sistema (chave, valor) VALUES (?, ?)",
                            (f'horario_robo_{robo}', _texto_dos_horarios(horas)))
        conexao.commit()
        resumo = aplicar_agenda_dos_robos()       # vale na hora, sem reiniciar o servidor
        return jsonify({'sucesso': True, 'resumo': resumo, 'agenda': proximas_execucoes()})
    except Exception as e:
        return jsonify({'sucesso': False, 'msg': str(e)})
    finally:
        conexao.close()

@app.route('/modulo_finmail')
@login_obrigatorio
def modulo_finmail():
    return render_template('finmail.html')

@app.route('/api/robo/status')
def api_robo_status():
    global FINMAIL_PAUSA_ATE
    # Lê a trava e injeta no status para o HTML saber que está pausado
    if FINMAIL_PAUSA_ATE and datetime.now() < FINMAIL_PAUSA_ATE:
        STATUS_ROBOS["FINMAIL"]["pausado_ate"] = FINMAIL_PAUSA_ATE.strftime('%d/%m %H:%M')
    else:
        STATUS_ROBOS["FINMAIL"]["pausado_ate"] = None
        
    return jsonify(STATUS_ROBOS)

@app.route('/api/robo/pausar_finmail', methods=['POST'])
@login_obrigatorio
def api_pausar_finmail():
    global FINMAIL_PAUSA_ATE
    dados = request.get_json() or {}
    horas = int(dados.get('horas', 0))
    
    if horas > 0:
        FINMAIL_PAUSA_ATE = datetime.now() + timedelta(hours=horas)
        msg = f"O robô foi pausado e só voltará a rodar em {FINMAIL_PAUSA_ATE.strftime('%d/%m/%Y às %H:%M')}."
    else:
        FINMAIL_PAUSA_ATE = None
        msg = "Pausa removida! O robô voltará a rodar automaticamente a cada 5 minutos."
        
    return jsonify({"sucesso": True, "msg": msg})

import tempfile
ARQUIVO_TRAVA_LIAX = os.path.join(tempfile.gettempdir(), "trava_liax.flag")

@app.route('/api/robo/continuar_liax', methods=['POST'])
@login_obrigatorio
def api_continuar_liax():
    # Quando você aperta o botão na tela, o servidor deleta a trava do Windows
    if os.path.exists(ARQUIVO_TRAVA_LIAX):
        try: os.remove(ARQUIVO_TRAVA_LIAX)
        except: pass
    return jsonify({"sucesso": True})

@app.route('/api/robo/iniciar', methods=['POST'])
@login_obrigatorio
def api_robo_iniciar():
    dados = request.get_json()
    robo_id = dados.get('robo')
    filtro = dados.get('filtro', '')
    
    if robo_id not in STATUS_ROBOS:
        return jsonify({"sucesso": False, "msg": "Automação não reconhecida."})

    if STATUS_ROBOS[robo_id]["rodando"]: 
        return jsonify({"sucesso": False, "msg": f"A automação {robo_id} já está em execução no momento!"})
    
    STATUS_ROBOS[robo_id]["rodando"] = True
    STATUS_ROBOS[robo_id]["sucessos"] = 0
    STATUS_ROBOS[robo_id]["falhas"] = 0
    STATUS_ROBOS[robo_id]["logs"] = [] 
    log_robo_console(robo_id, f"🚀 Inicialização de motor autorizada pelo usuário {session.get('usuario')}.", "info")
    
    if robo_id == 'WINKER': 
        threading.Thread(target=executar_script_externo, args=("scripts/robos/robo_winker.py", filtro, "WINKER"), daemon=True).start()
    elif robo_id == 'WEBWARE': 
        # 👇 Atualizado para o arquivo monolítico novo
        threading.Thread(target=executar_script_externo, args=("scripts/robos/robo_webware.py", filtro, "WEBWARE"), daemon=True).start()
    elif robo_id == 'PAGAMENTOS': 
        threading.Thread(target=executar_script_externo, args=("scripts/robos/robo_pagamentos.py", filtro, "PAGAMENTOS"), daemon=True).start()
    elif robo_id == 'FINMAIL': 
        threading.Thread(target=executar_script_externo, args=("scripts/robos/robo_email_geral.py", filtro, "FINMAIL"), daemon=True).start()
    elif robo_id == 'SCANNER':
        threading.Thread(target=executar_script_externo, args=("scripts/robos/robo_scanner_fisico.py", filtro, "SCANNER"), daemon=True).start()
    elif robo_id == 'SUPERLOGICA':
        threading.Thread(target=executar_script_externo, args=("scripts/robos/robo_superlogica.py", "", "SUPERLOGICA"), daemon=True).start()
    elif robo_id == 'BASE_LELLO':
        threading.Thread(target=executar_script_externo, args=("scripts/robos/robo_base_lello.py", "", "BASE_LELLO"), daemon=True).start()
    elif robo_id == 'LIAX':
        # O filtro aqui vem com "usuario,senha"
        threading.Thread(target=executar_script_externo, args=("scripts/robos/LIAX_BASE.py", filtro, "LIAX"), daemon=True).start()
    
    return jsonify({"sucesso": True})

def executar_script_externo(nome_arquivo, filtro, robo_id, args_extra=None):
    global PROCESSOS_ATIVOS
    codigo_saida = None
    try:
        log_robo_console(robo_id, f"Abrindo terminal invisível para '{nome_arquivo}'...", "warn")
        
        # Apontando para o Python do seu ambiente virtual
        caminho_python = r"C:\Users\ryan.lima\Documents\SistemaImoveis\.venv-1\Scripts\python.exe"
        comando = [caminho_python, nome_arquivo]
        if filtro: comando.append(filtro)
        if args_extra: comando.extend(args_extra)   # ex.: ['--imoveis', '123,456', '--sem-marcar']

        ambiente_windows = os.environ.copy()
        ambiente_windows["PYTHONIOENCODING"] = "utf-8"
        ambiente_windows["PYTHONUNBUFFERED"] = "1"   # cada print do robô chega na hora no console do portal

        processo = subprocess.Popen(
            comando, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding='utf-8', errors='replace', env=ambiente_windows
        )
        
        PROCESSOS_ATIVOS[robo_id] = processo

        for linha in processo.stdout:
            texto = linha.strip()
            if texto:
                tipo = "info"
                
                # Definição das cores baseada em palavras-chave e emojis
                if "SUCESSO" in texto.upper() or "✔️" in texto or "✅" in texto or "🎯" in texto: 
                    tipo = "success"
                elif "ERRO" in texto.upper() or "FALHA" in texto.upper() or "🚨" in texto or "❌" in texto or "REJEITADO:" in texto.upper(): 
                    tipo = "error"
                elif "⚠️" in texto: 
                    tipo = "warn"
                
                log_robo_console(robo_id, texto, tipo)
                
                texto_min = texto.lower()
                
                # 📊 PARSE INTEGRADO: Identifica as ações do robô em tempo real
                if robo_id == "FINMAIL":
                    if "boleto injetado com sucesso" in texto_min or "coletado no sistema" in texto_min:
                        STATUS_ROBOS[robo_id]["sucessos"] += 1
                    elif "senha" in texto_min or "credencial" in texto_min or "bloqueio" in texto_min:
                        STATUS_ROBOS[robo_id]["erro_senha"] += 1
                    elif "comunicado" in texto_min or "aviso" in texto_min:
                        STATUS_ROBOS[robo_id]["comunicados"] += 1
                    elif "ata " in texto_min or "assembleia" in texto_min or "reunião" in texto_min:
                        STATUS_ROBOS[robo_id]["atas"] += 1
                    elif "🚨" in texto or any(p in texto_min for p in ["erro", "falha", "rejeitado:"]):
                        STATUS_ROBOS[robo_id]["falhas"] += 1
                else:
                    # Regra genérica para os outros motores
                    if "alterado para coletado" in texto_min or "sucesso" in texto_min or "🎯" in texto:
                        STATUS_ROBOS[robo_id]["sucessos"] += 1
                    if "🚨" in texto or any(palavra in texto_min for palavra in ["erro", "falha", "barrou", "ausente"]):
                        STATUS_ROBOS[robo_id]["falhas"] += 1

        processo.wait()
        codigo_saida = processo.returncode

    except Exception as e:
        log_robo_console(robo_id, f"🚨 Erro crítico ao tentar abrir o script {nome_arquivo}: {str(e)}", "error")
    finally:
        log_robo_console(robo_id, f"🏁 Execução concluída.", "warn")
        STATUS_ROBOS[robo_id]["rodando"] = False
        if robo_id in PROCESSOS_ATIVOS:
            del PROCESSOS_ATIVOS[robo_id]
    return codigo_saida


SEM_RESPONSAVEL = 'Não Atribuído'


def _chave_nome(texto):
    """Nome sem acento, sem maiúscula e sem espaço sobrando: 'José ' == 'jose'."""
    base = unicodedata.normalize('NFKD', str(texto or ''))
    return ''.join(c for c in base if not unicodedata.combining(c)).strip().lower()


def responsaveis_da_carteira(conexao):
    """Quem tem carteira (vinculos_adm), sem repetir, na grafia cadastrada."""
    vistos, nomes = set(), []
    for r in conexao.execute("SELECT responsavel FROM vinculos_adm WHERE TRIM(IFNULL(responsavel, '')) <> '' ORDER BY responsavel"):
        nome = str(r['responsavel']).strip()
        if _chave_nome(nome) not in vistos:
            vistos.add(_chave_nome(nome))
            nomes.append(nome)
    return nomes


def resumo_fila_pagamentos(conexao):
    """A fila do robô de pagamentos (linhas COLETADO) por responsável e por dia de vencimento — a mesma lógica do robô:
    o filtro de dias deixa passar quem não tem vencimento ('sem_data') e pula o vencimento ilegível."""
    operadores = {}
    for nome in responsaveis_da_carteira(conexao):
        operadores[_chave_nome(nome)] = {'nome': nome, 'total': 0, 'por_dia': {}, 'sem_data': 0,
                                         'sem_responsavel': _chave_nome(nome) == _chave_nome(SEM_RESPONSAVEL)}
    dias, total = {}, 0
    linhas = conexao.execute("""
        SELECT IFNULL(v.responsavel, ?) AS responsavel, i.vencimento
        FROM imoveis i LEFT JOIN vinculos_adm v ON v.rowid = vinculo_de(i.administradora)
        WHERE i.ativo = 1 AND i.status_interno = 'COLETADO'""", (SEM_RESPONSAVEL,)).fetchall()
    for l in linhas:
        nome = str(l['responsavel'] or '').strip()
        if not nome:                      # responsável em branco: nenhum filtro do robô alcança essa linha
            continue
        chave = _chave_nome(nome)
        op = operadores.setdefault(chave, {'nome': nome, 'total': 0, 'por_dia': {}, 'sem_data': 0,
                                           'sem_responsavel': chave == _chave_nome(SEM_RESPONSAVEL)})
        op['total'] += 1
        total += 1
        data = _data_br(l['vencimento'])
        if data:
            d = f"{data.day:02d}"
            op['por_dia'][d] = op['por_dia'].get(d, 0) + 1
            dias[d] = dias.get(d, 0) + 1
        elif not str(l['vencimento'] or '').strip():
            op['sem_data'] += 1
    lista = sorted(operadores.values(), key=lambda o: (o['sem_responsavel'], -o['total'], _chave_nome(o['nome'])))
    return {'total': total, 'operadores': lista, 'dias': dict(sorted(dias.items()))}


@app.route('/api/automacao/pagamentos_resumo')
@login_obrigatorio
def api_automacao_pagamentos_resumo():
    """Alimenta o seletor da Força Tarefa: quantos boletos COLETADOS cada responsável tem na fila (e por dia)."""
    conexao = conectar_banco()
    try:
        resumo = resumo_fila_pagamentos(conexao)
    finally:
        conexao.close()
    return jsonify({'sucesso': True, 'rodando': bool(STATUS_ROBOS['PAGAMENTOS']['rodando']), **resumo})


def montar_filtros_forca_tarefa(pedidos, dias_brutos, conhecidos):
    """Função PURA (testável). pedidos = nomes marcados na tela; conhecidos = {chave: nome cadastrado}.
    Devolve {'operadores': [nomes na grafia cadastrada], 'dias': ['05', ...], 'filtros': {nome: 'RESP:..|EMP:|DIAS:..'}, 'erro': ''}.
    Nada de '|' ou ':' passando para o filtro do robô, nome que não existe na base nem dia fora de 1–31."""
    r = {'operadores': [], 'dias': [], 'filtros': {}, 'erro': ''}
    if not isinstance(pedidos, list) or not pedidos:
        r['erro'] = 'Nenhum operador foi selecionado!'
        return r
    desconhecidos = []
    for p in pedidos:
        chave = _chave_nome(p)
        nome = conhecidos.get(chave) or (SEM_RESPONSAVEL if chave == _chave_nome(SEM_RESPONSAVEL) else None)
        if not nome:
            desconhecidos.append(str(p)[:40])
        elif nome not in r['operadores']:
            r['operadores'].append(nome)
    if desconhecidos:
        r['erro'] = f"Responsável não encontrado na base: {', '.join(desconhecidos)}."
        return r
    dias = set()
    for d in (dias_brutos or []):
        texto = str(d).strip()
        if not texto.isdigit() or not 1 <= int(texto) <= 31:
            r['erro'] = f"Dia de vencimento inválido: '{texto[:10]}'."
            return r
        dias.add(f"{int(texto):02d}")
    r['dias'] = sorted(dias)
    for nome in r['operadores']:
        r['filtros'][nome] = f"RESP:{nome}" + (f"|EMP:|DIAS:{','.join(r['dias'])}" if r['dias'] else "")
    return r


@app.route('/api/iniciar_dois_robos', methods=['POST'])
@login_obrigatorio
def api_iniciar_dois_robos():
    """Força Tarefa: um robô de pagamentos por responsável marcado, todos ao mesmo tempo.
    Corpo: {"operadores": ["Ryan", "Jessica"], "dias": ["05", "10"]}  ('dias' é opcional: vazio = todos os vencimentos)."""
    dados = request.get_json() or {}
    robo_id = "PAGAMENTOS"

    # 1. Verifica se já tem algo rodando
    if STATUS_ROBOS[robo_id]["rodando"]:
        return jsonify({"sucesso": False, "msg": "A automação de pagamentos já está em execução!"})

    conexao = conectar_banco()
    try:
        conhecidos = {_chave_nome(n): n for n in responsaveis_da_carteira(conexao)}
    finally:
        conexao.close()
    plano = montar_filtros_forca_tarefa(dados.get('operadores', []), dados.get('dias', []), conhecidos)
    if plano['erro']:
        return jsonify({"sucesso": False, "msg": plano['erro']})
    operadores = plano['operadores']

    STATUS_ROBOS[robo_id]["rodando"] = True
    STATUS_ROBOS[robo_id]["sucessos"] = 0
    STATUS_ROBOS[robo_id]["falhas"] = 0
    STATUS_ROBOS[robo_id]["logs"] = []
    
    nomes_formatados = ", ".join(operadores)
    txt_dias = f" (vencimentos {', '.join(plano['dias'])})" if plano['dias'] else ""
    log_robo_console(robo_id, f"🚀 FORÇA TAREFA: Motores de {nomes_formatados} acionados simultaneamente{txt_dias}!", "warn")

    global PROCESSOS_ATIVOS
    PROCESSOS_ATIVOS[robo_id] = [] 

    def run_thread(filtro, nome_thread):
        try:
            caminho_python = r"C:\Users\ryan.lima\Documents\SistemaImoveis\.venv-1\Scripts\python.exe"
            comando = [caminho_python, "scripts/robos/robo_pagamentos.py", filtro]
            
            ambiente = os.environ.copy()
            ambiente["PYTHONIOENCODING"] = "utf-8"
            ambiente["PYTHONUNBUFFERED"] = "1"
            
            processo = subprocess.Popen(comando, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace', env=ambiente)
            
            PROCESSOS_ATIVOS[robo_id].append(processo)

            for linha in processo.stdout:
                texto = linha.strip()
                if texto:
                    tipo = "info"
                    if "SUCESSO" in texto.upper() or "✔️" in texto or "✅" in texto: tipo = "success"
                    elif "ERRO" in texto.upper() or "FALHA" in texto.upper() or "🚨" in texto: tipo = "error"
                    elif "⚠️" in texto: tipo = "warn"
                    
                    log_robo_console(robo_id, f"[{nome_thread}] {texto}", tipo)
                    
                    texto_min = texto.lower()
                    if "alterado para coletado" in texto_min or "sucesso" in texto_min or "🎯" in texto:
                        STATUS_ROBOS[robo_id]["sucessos"] += 1
                    if "🚨" in texto or any(palavra in texto_min for palavra in ["erro", "falha", "barrou", "ausente"]):
                        STATUS_ROBOS[robo_id]["falhas"] += 1
            processo.wait()
        except Exception as e:
            log_robo_console(robo_id, f"🚨 Erro no robô {nome_thread}: {e}", "error")

    def orquestrador_duplo():
        threads = []
        
        # 2. Cria um robô para cada pessoa que foi marcada no HTML!
        for op in operadores:
            # Passa a sigla do filtro e o nome para a tag do Console
            t = threading.Thread(target=run_thread, args=(plano['filtros'][op], op.upper()))
            threads.append(t)
            t.start()
        
        # Aguarda todos os robôs dessa lista terminarem
        for t in threads:
            t.join()
        
        log_robo_console(robo_id, "🏁 Força Tarefa concluída.", "warn")
        STATUS_ROBOS[robo_id]["rodando"] = False
        if robo_id in PROCESSOS_ATIVOS:
            del PROCESSOS_ATIVOS[robo_id]

    # Dispara o orquestrador
    threading.Thread(target=orquestrador_duplo, daemon=True).start()
    
    return jsonify({"sucesso": True, "msg": "Robôs iniciados em paralelo!"})

@app.route('/api/robo/parar', methods=['POST'])
@login_obrigatorio
def api_robo_parar():
    global PROCESSOS_ATIVOS
    dados = request.get_json()
    robo_id = dados.get('robo')
    
    if robo_id in STATUS_ROBOS and STATUS_ROBOS[robo_id]["rodando"]:
        if robo_id in PROCESSOS_ATIVOS:
            processos = PROCESSOS_ATIVOS[robo_id]
            # Se for a força tarefa, mata a lista inteira (Ryan e Jessica)
            if isinstance(processos, list):
                for p in processos:
                    try: p.terminate()
                    except: pass
            # Se for um robô normal (Webware, Winker, etc)
            else:
                try: processos.terminate()
                except: pass
                
            del PROCESSOS_ATIVOS[robo_id]
            
        STATUS_ROBOS[robo_id]["rodando"] = False
        log_robo_console(robo_id, "🛑 EXECUÇÃO CANCELADA FORÇADAMENTE PELO USUÁRIO.", "error")
        return jsonify({"sucesso": True})
    return jsonify({"sucesso": False})

# =================================================================
# OUTRAS ABAS (PRODUTIVIDADE, CONFIGURAÇÕES)
# =================================================================

# =================================================================
# 📈 PRODUTIVIDADE POR MÊS (página "Inteligência de Produtividade")
# -----------------------------------------------------------------
# A tabela imoveis guarda UMA LINHA POR UPLOAD de planilha (o mesmo boleto aparece várias vezes, uma por upload).
# Contar linhas/imóveis soltos fazia o mesmo boleto entrar mais de uma vez (Total Coletado > Volume Inicial).
# Aqui a unidade é o BOLETO = (imóvel, vencimento), olhando só a linha mais recente de cada um.
#   • Volume       = boletos com vencimento no mês (ativos + os que já saíram da planilha)
#   • Pendente     = ainda na planilha ativa, sem status final
#   • Coletado     = status final (PAGO / COLETADO / PAGO NA 275 / PAGO NA ADM) OU saiu da planilha
#                    (a planilha é o relatório de boletos NÃO recebidos: sair dela = foi recebido/pago)
#   • Quem coletou = autor do último evento de entrega do boleto em qualquer upload
# =================================================================
ACOES_ENTREGA_BOLETO = ('COLETOU BOLETO', 'PAGOU BOLETO', 'PAGOU BOLETO NA 275',
                        'ALTEROU STATUS PARA PAGO NA ADM', 'ALTEROU STATUS PARA PAGO NA 275')
ACOES_NAO_EMITIDO = ('ALTEROU STATUS PARA NÃO EMITIDO', 'ALTEROU STATUS EM LOTE PARA NÃO EMITIDO')
ACAO_DISPARO = 'DISPAROU EMAIL COBRANCA'
MESES_NOMES_PT = ['Janeiro', 'Fevereiro', 'Março', 'Abril', 'Maio', 'Junho', 'Julho', 'Agosto', 'Setembro', 'Outubro', 'Novembro', 'Dezembro']
MESES_ABREV_PT = ['Jan', 'Fev', 'Mar', 'Abr', 'Mai', 'Jun', 'Jul', 'Ago', 'Set', 'Out', 'Nov', 'Dez']
GRUPOS_ROBO = ('finmail', 'webware', 'winker', 'scanner', 'pagamentos', 'outros_robos')


def _mes_do_vencimento(venc):
    """'05/10/2026' ou '05/10/26' -> '2026-10' (ordenável). None se não der para ler."""
    partes = str(venc or '').strip().split('/')
    if len(partes) < 3 or not partes[1].strip().isdigit():
        return None
    ano = partes[2].strip()
    if len(ano) == 2:
        ano = '20' + ano
    if len(ano) != 4 or not ano.isdigit():
        return None
    mes = int(partes[1])
    return f'{ano}-{mes:02d}' if 1 <= mes <= 12 else None


def _rotulo_mes(chave, curto=False):
    ano, mes = chave.split('-')
    return f"{(MESES_ABREV_PT if curto else MESES_NOMES_PT)[int(mes) - 1]}/{ano}"


def _mes_chave_do_rotulo(rotulo):
    """'Novembro/2026' -> '2026-11'."""
    partes = str(rotulo or '').split('/')
    if len(partes) != 2:
        return None
    nome, ano = partes[0].strip().lower(), partes[1].strip()
    if len(ano) != 4 or not ano.isdigit():
        return None
    for i, m in enumerate(MESES_NOMES_PT):
        if m.lower() == nome:
            return f'{ano}-{i + 1:02d}'
    return None


def bases_congeladas(conexao, guardar=True):
    """Base fixa de cada competência. Devolve {'AAAA-MM': quantidade} e, no detalhe,
    de onde cada número veio. Mês sem base congelada fica de fora.

    A ordem importa, porque um mês pode ter mais de uma base:
      1) arquivo_meta|AAAA-MM  -> a PRIMEIRA planilha daquele ciclo, guardada na virada;
      2) base_oficial|AAAA-MM  -> a base que ficou valendo para o mês quando a equipe
         subiu uma correção depois da virada (novembro/2026 é esse caso: 5126, não 5060);
      3) meta_dia_XX           -> o ciclo em ANDAMENTO manda no próprio mês, porque é
         ali que a correção aparece primeiro.
    Enquanto o mês é o ciclo vivo, o valor dele é copiado para base_oficial|AAAA-MM. Assim,
    quando o ciclo virar, o mês continua com a base que estava valendo e não volta para a
    primeira planilha. Os outros meses não são tocados: seguem com a base que já tinham."""
    bases, origem = {}, {}
    try:
        for r in conexao.execute("SELECT chave, valor FROM config_sistema WHERE chave LIKE 'arquivo_meta|%'").fetchall():
            guardado = json.loads(r['valor'] or '{}')
            chave = _mes_chave_do_rotulo(guardado.get('rotulo')) or str(r['chave']).split('|')[-1].strip()
            total = sum(int(v) for v in (guardado.get('metas') or {}).values() if str(v).strip().isdigit())
            if chave and total:
                bases[chave], origem[chave] = total, 'primeira planilha do ciclo'
    except Exception as erro:
        print(f'⚠️ Não consegui ler as bases arquivadas: {erro}', flush=True)

    try:
        for r in conexao.execute("SELECT chave, valor FROM config_sistema WHERE chave LIKE 'base_oficial|%'").fetchall():
            chave = str(r['chave']).split('|')[-1].strip()
            if chave and str(r['valor']).strip().isdigit() and int(r['valor']) > 0:
                bases[chave], origem[chave] = int(r['valor']), 'base que ficou valendo no mês'
    except sqlite3.OperationalError:
        pass
    except Exception as erro:
        print(f'⚠️ Não consegui ler as bases oficiais: {erro}', flush=True)

    try:
        ref = conexao.execute("SELECT valor FROM config_sistema WHERE chave = 'meta_mes_ref'").fetchone()
        chave_atual = _mes_chave_do_rotulo(ref['valor']) if ref else None
        if chave_atual:
            viva = sum(int(r['valor']) for r in conexao.execute("SELECT valor FROM config_sistema WHERE chave LIKE 'meta_dia_%'").fetchall()
                       if str(r['valor']).strip().isdigit())
            if viva:
                bases[chave_atual] = viva     # o ciclo em andamento vale mais do que o arquivo
                origem[chave_atual] = 'meta travada do ciclo em andamento'
                if guardar:
                    _guardar_base_oficial(conexao, chave_atual, viva)
    except Exception as erro:
        print(f'⚠️ Não consegui ler a meta travada: {erro}', flush=True)

    bases['_origem'] = origem
    return bases


def _guardar_base_oficial(conexao, chave_mes, total):
    """Deixa registrado qual base está valendo no mês do ciclo vivo. Quando o ciclo virar,
    este número é que continua respondendo pelo mês — e não a primeira planilha."""
    try:
        atual = conexao.execute("SELECT valor FROM config_sistema WHERE chave = ?",
                                (f'base_oficial|{chave_mes}',)).fetchone()
        if atual and str(atual['valor']).strip() == str(total):
            return
        conexao.execute("INSERT OR REPLACE INTO config_sistema (chave, valor) VALUES (?, ?)",
                        (f'base_oficial|{chave_mes}', str(total)))
        conexao.commit()
    except Exception as erro:
        print(f'⚠️ Não consegui guardar a base oficial de {chave_mes}: {erro}', flush=True)


def _classificar_executor(usuario):
    """Quem entregou o boleto -> (grupo, rótulo). Robôs (AUTOMAÇÃO.../ROBÔ...) nunca entram como 'equipe manual'."""
    bruto = ' '.join(str(usuario or '').split())
    up = unicodedata.normalize('NFKD', bruto).encode('ascii', 'ignore').decode('ascii').upper()
    if not up or 'SISTEMA' in up or 'PLANILHA' in up:
        return 'sistema', 'Sistema / Planilha'
    robo = up.startswith('AUTOMA') or up.startswith('ROB') or up.startswith('RPA')
    if 'WEBWARE' in up:
        return 'webware', 'Automação Webware'
    if 'WINKER' in up:
        return 'winker', 'Automação Winker'
    if 'SCANNER' in up:
        return 'scanner', 'Scanner Físico'
    if 'FINMAIL' in up or 'GERAL' in up or (robo and 'MAIL' in up):
        return 'finmail', 'Automação FinMail'
    if robo and 'PAGAMENTO' in up:
        return 'pagamentos', 'Automação Pagamentos'
    if robo:
        return 'outros_robos', bruto.title()
    return 'manual', _nome_colaborador_padrao(bruto) or 'Desconhecido'


def montar_produtividade_mensal(conexao):
    """Lê a base uma vez (boletos, eventos, observações) e devolve {chave 'AAAA-MM': dados do mês}."""
    from collections import Counter, defaultdict
    mapa_vinc = mapa_vinculos_normalizados(conexao)

    # 1) Boletos = última linha de cada (imóvel, vencimento)
    boletos = {}
    for r in conexao.execute("""
            SELECT i.imovel, TRIM(i.vencimento) AS venc, i.administradora, UPPER(TRIM(IFNULL(i.status_interno, ''))) AS status, i.ativo
            FROM imoveis i
            JOIN (SELECT MAX(id) AS mid FROM imoveis
                  WHERE administradora IS NOT NULL AND TRIM(administradora) != ''
                    AND LENGTH(TRIM(IFNULL(vencimento, ''))) >= 10
                  GROUP BY imovel, TRIM(vencimento)) u ON u.mid = i.id"""):
        mes = _mes_do_vencimento(r['venc'])
        if not mes:
            continue
        vinc = mapa_vinc.get(normalizar_texto(r['administradora']))
        resp = (vinc['responsavel'] or '').strip() if vinc else ''
        dono = 'Não Atribuído' if (not resp or 'ATRIBU' in resp.upper()) else (_nome_colaborador_padrao(resp) or 'Não Atribuído')
        boletos[(r['imovel'], r['venc'])] = {'mes': mes, 'dono': dono, 'status': r['status'], 'ativo': r['ativo']}

    # 2) Eventos de produtividade ligados a esses boletos (qualquer upload do mesmo imóvel+vencimento), do mais antigo ao mais novo
    ultimo_autor, nao_emitido = {}, Counter()
    disparos = defaultdict(Counter)                     # mês -> {pessoa: e-mails}
    marcas = ','.join('?' * (len(ACOES_ENTREGA_BOLETO) + len(ACOES_NAO_EMITIDO) + 1))
    for e in conexao.execute(f"""
            SELECT p.usuario, p.acao, i.imovel, TRIM(i.vencimento) AS venc
            FROM produtividade p JOIN imoveis i ON i.id = p.imovel_id
            WHERE p.acao IN ({marcas}) ORDER BY p.id""", ACOES_ENTREGA_BOLETO + ACOES_NAO_EMITIDO + (ACAO_DISPARO,)):
        chave = (e['imovel'], e['venc'])
        b = boletos.get(chave)
        if b is None:
            continue
        if e['acao'] == ACAO_DISPARO:
            disparos[b['mes']][_nome_colaborador_padrao(e['usuario']) or 'Desconhecido'] += 1
        elif e['acao'] in ACOES_NAO_EMITIDO:
            nao_emitido[chave] += 1
        else:
            ultimo_autor[chave] = e['usuario']

    # 3) Plano B (como a página sempre fez): última observação que cita PAGO/COLETADO
    ultimo_obs = {}
    for o in conexao.execute("""
            SELECT h.usuario, i.imovel, TRIM(i.vencimento) AS venc
            FROM historico_observacoes h JOIN imoveis i ON i.id = h.imovel_id
            WHERE UPPER(h.texto) LIKE '%PAGO%' OR UPPER(h.texto) LIKE '%COLETADO%' ORDER BY h.id"""):
        if o['usuario']:
            ultimo_obs[(o['imovel'], o['venc'])] = o['usuario']

    # 4) Agrega por mês
    meses = {}
    for chave, b in boletos.items():
        m = meses.setdefault(b['mes'], {
            'volume': 0, 'entregues': 0, 'pendentes': 0, 'grupos': Counter(), 'manual': Counter(),
            'grupo_dono': defaultdict(Counter), 'carteiras': {}, 'nao_emitido': 0, 'retrabalho': 0})
        final = b['status'] in STATUS_FINALIZADOS
        entregue = final or b['ativo'] == 0
        m['volume'] += 1
        cart = m['carteiras'].setdefault(b['dono'], {'base': 0, 'entregues': 0, 'detalhes': Counter()})
        cart['base'] += 1
        if chave in nao_emitido:
            m['nao_emitido'] += 1
            if nao_emitido[chave] >= 2:
                m['retrabalho'] += 1
        if not entregue:
            m['pendentes'] += 1
            m['grupos']['pendente'] += 1
            continue
        autor = ultimo_autor.get(chave) or ultimo_obs.get(chave)
        if autor:
            grupo, rotulo = _classificar_executor(autor)
        else:
            grupo, rotulo = ('sistema', 'Sistema / Planilha') if final else ('saiu', 'Saiu da planilha')
        m['entregues'] += 1
        cart['entregues'] += 1
        m['grupos'][grupo] += 1
        m['grupo_dono'][grupo][b['dono']] += 1
        if grupo == 'manual':
            m['manual'][rotulo] += 1
            cart['detalhes'][rotulo] += 1
        elif grupo == 'outros_robos':
            cart['detalhes'][rotulo] += 1
        elif grupo in GRUPOS_ROBO:
            cart['detalhes'][rotulo] += 1
        elif grupo == 'sistema':
            cart['detalhes']['Sistema / Planilha'] += 1
        else:
            cart['detalhes']['Saiu da planilha'] += 1

    bases = bases_congeladas(conexao)
    origem_base = bases.pop('_origem', {})
    for chave, m in meses.items():
        m['disparos'] = disparos.get(chave, Counter())
        # Volume do mês = a base congelada. Sem base congelada, vale o que dá para contar.
        m['base'] = bases.get(chave) or m['volume']
        m['base_congelada'] = chave in bases
        m['origem_base'] = origem_base.get(chave, 'contagem dos boletos do mês')
        m['fora_da_base'] = max(0, m['base'] - m['entregues'] - m['pendentes'])
    return meses


def _pct(parte, todo, casas=1):
    return round(100.0 * parte / todo, casas) if todo else 0.0


def _faixa_calor(valor, maximo):
    """0 = sem cor (zero), 1..6 = degraus de uma única cor (azul), proporcionais ao maior valor da tabela."""
    if not valor or not maximo:
        return 0
    return max(1, min(6, int(round(6 * valor / maximo))))


@app.route('/relatorio_avancado')
@login_obrigatorio
def relatorio_avancado():
    conexao = conectar_banco()

    # 🎯 AJUSTE RÁPIDO: Força tudo no banco a virar "Jessica" (Sem acento) de forma retroativa
    try:
        conexao.execute("UPDATE produtividade SET usuario = 'Jessica' WHERE usuario = 'Jéssica'")
        conexao.execute("UPDATE historico_observacoes SET usuario = 'Jessica' WHERE usuario = 'Jéssica'")
        conexao.execute("UPDATE vinculos_adm SET responsavel = 'Jessica' WHERE responsavel = 'Jéssica'")
        conexao.execute("UPDATE historico_fechamento_mes SET responsavel = 'Jessica' WHERE responsavel = 'Jéssica'")
        conexao.commit()
    except Exception as e:
        print("Erro ao rodar correção automática para Jessica sem acento:", e)

    try:
        meses = montar_produtividade_mensal(conexao)
        ciclo = _matriz_ciclo_alvo(conexao)          # 'mm/aaaa' que a meta travada representa
        meta_travada = 0
        try:
            for r in conexao.execute("SELECT valor FROM config_sistema WHERE chave LIKE 'meta_dia_%'").fetchall():
                if str(r['valor']).strip().isdigit():
                    meta_travada += int(r['valor'])
        except Exception:
            meta_travada = 0
    finally:
        conexao.close()

    # ---- seletor de competência (mais recente primeiro) ----
    chaves_desc = sorted(meses.keys(), reverse=True)
    meses_disponiveis = [{'valor_mes': k[5:], 'valor_ano': k[:4], 'label': _rotulo_mes(k)} for k in chaves_desc]

    chave_sel = None
    try:  # mês/ano vêm da URL: qualquer coisa fora de 1-12 / 4 dígitos é ignorada e vale a competência mais recente
        mes_req, ano_req = int(str(request.args.get('mes', '')).strip()), str(request.args.get('ano', '')).strip()
        if 1 <= mes_req <= 12 and len(ano_req) == 4 and ano_req.isascii() and ano_req.isdigit():
            chave_sel = f"{ano_req}-{mes_req:02d}"
    except ValueError:
        pass
    if not chave_sel:
        chave_sel = chaves_desc[0] if chaves_desc else datetime.now().strftime('%Y-%m')
    mes_selecionado, ano_selecionado = chave_sel[5:], chave_sel[:4]

    vazio = {'volume': 0, 'entregues': 0, 'pendentes': 0, 'grupos': {}, 'manual': {}, 'grupo_dono': {}, 'carteiras': {},
             'nao_emitido': 0, 'retrabalho': 0, 'disparos': {}, 'base': 0, 'base_congelada': False, 'fora_da_base': 0}
    atual = meses.get(chave_sel, vazio)
    g = atual['grupos']
    robos_total = sum(g.get(k, 0) for k in GRUPOS_ROBO)
    manual_total = g.get('manual', 0)

    # ---- resumo do mês ----
    resumo = {
        'volume': atual['base'], 'contados': atual['volume'], 'entregues': atual['entregues'], 'pendentes': atual['pendentes'],
        'base_congelada': atual['base_congelada'], 'fora_da_base': atual['fora_da_base'],
        'cobertura': _pct(atual['entregues'], atual['base']),
        'meta_travada': meta_travada if (meta_travada and ciclo and f"{ciclo[3:]}-{ciclo[:2]}" == chave_sel) else None,
    }

    # ---- cartões: somam exatamente o Total Coletado ----
    def lista_dono(grupo):
        d = atual['grupo_dono'].get(grupo, {})
        return [{'nome': n, 'qtd': q} for n, q in sorted(d.items(), key=lambda kv: kv[1], reverse=True)]

    definicao = [
        ('finmail', 'Automação FinMail', 'Varredura de e-mails', '📧', 'primary', 'Origem dos imóveis (carteira):'),
        ('webware', 'Automação Webware', 'Extração de portal', '🌐', 'success', 'Origem dos imóveis (carteira):'),
        ('winker', 'Automação Winker', 'Extração de portal', '🏢', 'warning', 'Origem dos imóveis (carteira):'),
        ('scanner', 'Scanner Físico', 'Boletos digitalizados', '📄', 'purple', 'Origem dos imóveis (carteira):'),
        ('pagamentos', 'Automação Pagamentos', 'Baixa por robô de pagamentos', '💳', 'teal', 'Origem dos imóveis (carteira):'),
        ('outros_robos', 'Outras automações', 'Robôs não classificados', '🤖', 'slate', 'Origem dos imóveis (carteira):'),
        ('manual', 'Equipe', 'Coleta feita por uma pessoa', '👥', 'indigo', 'Coletas por colaborador:'),
        ('sistema', 'Sistema / Planilha', 'Baixa sem autor registrado', '⚙️', 'gray', 'Origem dos imóveis (carteira):'),
        ('saiu', 'Saiu da planilha', 'Saiu sem status final', '↗️', 'gray', 'Origem dos imóveis (carteira):'),
    ]
    cartoes = []
    for chave, titulo, sub, icone, cor, titulo_lista in definicao:
        qtd = g.get(chave, 0)
        if chave == 'outros_robos' and not qtd:
            continue                                   # só aparece se existir
        if chave == 'manual':
            lista = [{'nome': n, 'qtd': q} for n, q in sorted(atual['manual'].items(), key=lambda kv: kv[1], reverse=True)]
        else:
            lista = lista_dono(chave)
        cartoes.append({'chave': chave, 'titulo': titulo, 'subtitulo': sub, 'icone': icone, 'cor': cor, 'titulo_lista': titulo_lista,
                        'qtd': qtd, 'pct': _pct(qtd, atual['entregues']), 'lista': lista})

    # ---- ranking por carteira (agora com base e % de cobertura) ----
    ranking_carteiras = []
    for nome, c in atual['carteiras'].items():
        ranking_carteiras.append({
            'nome': nome, 'base': c['base'], 'entregues': c['entregues'], 'pendentes': c['base'] - c['entregues'],
            'pct': _pct(c['entregues'], c['base']),
            'detalhes': [{'ex': ex, 'q': q} for ex, q in sorted(c['detalhes'].items(), key=lambda kv: kv[1], reverse=True)]})
    ranking_carteiras.sort(key=lambda x: (x['nome'] == 'Não Atribuído', -x['pct'], -x['base']))

    ranking_disparos = [{'nome': n, 'qtd': q} for n, q in sorted(atual['disparos'].items(), key=lambda kv: kv[1], reverse=True)]

    # ---- evolução mês a mês (do mais antigo para o mais novo; últimos 12) ----
    chaves_asc = sorted(meses.keys())[-12:]
    evolucao, anterior = [], None
    for k in chaves_asc:
        m = meses[k]
        gm = m['grupos']
        robos = sum(gm.get(x, 0) for x in GRUPOS_ROBO)
        manual = gm.get('manual', 0)
        planilha = gm.get('sistema', 0) + gm.get('saiu', 0)
        vol = m['base'] or 1
        linha = {
            'chave': k, 'rotulo': _rotulo_mes(k, curto=True), 'selecionado': k == chave_sel,
            'volume': m['base'], 'base_congelada': m['base_congelada'], 'fora_da_base': m['fora_da_base'],
            'entregues': m['entregues'], 'pendentes': m['pendentes'],
            'cobertura': _pct(m['entregues'], m['base']),
            'robos': robos, 'manual': manual, 'planilha': planilha,
            'pct_robos': _pct(robos, robos + manual),
            'seg_robos': _pct(robos, vol, 2), 'seg_manual': _pct(manual, vol, 2), 'seg_planilha': _pct(planilha, vol, 2),
            'seg_pendentes': _pct(m['pendentes'], vol, 2), 'seg_fora': _pct(m['fora_da_base'], vol, 2),
            'disparos': sum(m['disparos'].values()), 'nao_emitido': m['nao_emitido'], 'retrabalho': m['retrabalho'],
            'em_andamento': bool(ciclo and f"{ciclo[3:]}-{ciclo[:2]}" == k),
            'delta_cobertura': None, 'delta_robos': None,
        }
        if anterior:
            linha['delta_cobertura'] = round(linha['cobertura'] - anterior['cobertura'], 1)
            linha['delta_robos'] = round(linha['pct_robos'] - anterior['pct_robos'], 1)
        evolucao.append(linha)
        anterior = linha

    # ---- matrizes pessoa x mês (mapa de calor de uma cor) ----
    def montar_matriz(extrair):
        por_pessoa = {}
        for k in chaves_asc:
            for nome, q in extrair(meses[k]).items():
                por_pessoa.setdefault(nome, {})[k] = q
        maximo = max([q for d in por_pessoa.values() for q in d.values()] or [0])
        linhas = []
        for nome, d in por_pessoa.items():
            valores = [{'q': d.get(k, 0), 'faixa': _faixa_calor(d.get(k, 0), maximo), 'selecionado': k == chave_sel} for k in chaves_asc]
            linhas.append({'nome': nome, 'valores': valores, 'total': sum(d.values())})
        linhas.sort(key=lambda x: x['total'], reverse=True)
        totais = [sum(l['valores'][i]['q'] for l in linhas) for i in range(len(chaves_asc))]
        return {'meses': [_rotulo_mes(k, curto=True) for k in chaves_asc], 'selecionados': [k == chave_sel for k in chaves_asc],
                'linhas': linhas, 'totais': totais, 'total_geral': sum(totais)}

    matriz_coletas = montar_matriz(lambda m: m['manual'])
    matriz_disparos = montar_matriz(lambda m: m['disparos'])

    return render_template('relatorio_avancado.html',
                           mes=_rotulo_mes(chave_sel), mes_selecionado=mes_selecionado, ano_selecionado=ano_selecionado,
                           meses_disponiveis=meses_disponiveis, resumo=resumo, cartoes=cartoes,
                           ranking_carteiras=ranking_carteiras, ranking_disparos=ranking_disparos,
                           evolucao=evolucao, matriz_coletas=matriz_coletas, matriz_disparos=matriz_disparos,
                           pct_robos_mes=_pct(robos_total, robos_total + manual_total),
                           nao_emitido_mes=atual['nao_emitido'], retrabalho_mes=atual['retrabalho'])


@app.route('/api/sistema/disparos_diarios_colaborador')
@login_obrigatorio
def api_disparos_diarios_colaborador():
    conexao = conectar_banco()
    query = '''SELECT SUBSTR(data_hora, 1, 10) as dia, usuario, COUNT(*) as qtd FROM produtividade WHERE acao = 'DISPAROU EMAIL COBRANCA' GROUP BY dia, usuario ORDER BY dia DESC, qtd DESC LIMIT 300'''
    dados = conexao.execute(query).fetchall()
    conexao.close()
    resultados = []
    for row in dados:
        partes = row['dia'].split('-')
        dia_formatado = f"{partes[2]}/{partes[1]}/{partes[0]}" if len(partes) == 3 else row['dia']
        resultados.append({"dia": dia_formatado, "colaborador": row['usuario'], "qtd": row['qtd']})
    return jsonify(resultados)

@app.route('/relatorio_emails')
@login_obrigatorio
def relatorio_emails():
    conexao = conectar_banco()
    
    # 🎯 CAPTURA A DATA SELECIONADA NO CALENDÁRIO. SE NÃO HOUVER, USA HOJE.
    data_sel = request.args.get('data', '').strip()
    if not data_sel:
        data_sel = datetime.now().strftime('%Y-%m-%d')
        
    total_disparos = conexao.execute('SELECT COUNT(*) FROM historico_emails WHERE data_hora LIKE ?', (data_sel + '%',)).fetchone()[0]
    total_imoveis_cobr = conexao.execute('SELECT SUM(qtd_imoveis) FROM historico_emails WHERE data_hora LIKE ?', (data_sel + '%',)).fetchone()[0] or 0
    coletas_hoje = conexao.execute("SELECT usuario, COUNT(*) as qtd FROM produtividade WHERE acao = 'COLETOU BOLETO' AND data_hora LIKE ? GROUP BY usuario ORDER BY qtd DESC", (data_sel + '%',)).fetchall()
    historico = conexao.execute('SELECT * FROM historico_emails WHERE data_hora LIKE ? ORDER BY id DESC', (data_sel + '%',)).fetchall()
    conexao.close()
    
    # Formata a data para exibir bonito no texto (ex: de 2026-06-23 para 23/06/2026)
    try:
        dt_obj = datetime.strptime(data_sel, '%Y-%m-%d')
        data_formatada = dt_obj.strftime('%d/%m/%Y')
    except:
        data_formatada = data_sel
        
    return render_template('relatorio_emails.html', 
                           historico=historico, 
                           periodo_filtro='hoje', 
                           total_disparos=total_disparos, 
                           total_imoveis_cobr=total_imoveis_cobr, 
                           disparos_hoje=total_disparos, 
                           coletas_hoje=coletas_hoje, 
                           icones_robos=ICONES_ROBOS,
                           data_selecionada=data_sel,
                           data_formatada=data_formatada)

@app.route('/api/sistema/historico_avancado_emails')
@login_obrigatorio
def api_historico_avancado_emails():
    conexao = conectar_banco()
    historico = conexao.execute('SELECT colaborador, administradora, qtd_imoveis, data_hora FROM historico_emails ORDER BY id DESC').fetchall()
    conexao.close()
    return jsonify([dict(row) for row in historico])

@app.route('/configuracoes')
@login_obrigatorio
def configuracoes():
    usuario_atual = session.get('usuario', '').lower().strip()
    conexao = conectar_banco()
    
    # 🎯 VERIFICAÇÃO DINÂMICA: Descobre se o operador atual é Master no banco
    eh_master = usuario_eh_gestor(usuario_atual)

    # 💡 SQL MELHORADO: Usa TRIM para já matar espaços em branco nas pontas
    query = '''SELECT DISTINCT TRIM(i.administradora) as administradora, 
                      IFNULL(v.responsavel, 'Não Atribuído') as colaborador, 
                      IFNULL(v.email, '') as email 
               FROM imoveis i 
               LEFT JOIN vinculos_adm v ON v.rowid = vinculo_de(i.administradora) 
               WHERE i.administradora IS NOT NULL AND TRIM(i.administradora) != '' AND i.ativo = 1 
               ORDER BY TRIM(i.administradora)'''
    vinculos_raw = conexao.execute(query).fetchall()
    
    # 🎯 INTELIGÊNCIA: FUSÃO DE DUPLICATAS + PREENCHIMENTO AUTOMÁTICO
    df = pd.DataFrame([dict(row) for row in vinculos_raw])
    if not df.empty:
        import unicodedata
        # 1. Cria uma função rápida para limpar acentos (Imóveis = Imoveis)
        def limpa_nome(nome):
            if not nome: return ''
            txt = str(nome).strip().upper()
            txt = unicodedata.normalize('NFKD', txt)
            return ''.join(ch for ch in txt if not unicodedata.combining(ch))

        df['adm_limpa'] = df['administradora'].apply(limpa_nome)

        # 2. Ordena dando prioridade para quem TEM operador (A Ellen ganha do "Não Atribuído")
        df['peso'] = df['colaborador'].apply(lambda x: 0 if x == 'Não Atribuído' else 1)
        df = df.sort_values(['adm_limpa', 'peso'], ascending=[True, False])

        # 3. FUSÃO: Remove as duplicatas (Fica só a linha que já tem a Ellen)
        df = df.drop_duplicates(subset=['adm_limpa'], keep='first')
        df = df.drop(columns=['adm_limpa', 'peso'])
        df = df.sort_values('administradora')

        # 4. Guarda o estado original para o preenchimento automático
        df['colaborador_original'] = df['colaborador']
        
        # 5. Troca o texto por um nulo real que o Pandas entenda
        df['colaborador'] = df['colaborador'].replace('Não Atribuído', pd.NA)
        
        # 6. Preenche o nulo puxando o valor de cima (com limite de 1 pulo)
        df['colaborador'] = df['colaborador'].ffill(limit=1)
        
        # 7. 🚀 A MÁGICA DEFINITIVA: Filtra quem foi preenchido automaticamente agora
        novas_atribuicoes = df[(df['colaborador_original'] == 'Não Atribuído') & (df['colaborador'].notna())]
        
        if not novas_atribuicoes.empty:
            for _, row in novas_atribuicoes.iterrows():
                adm = row['administradora']
                novo_resp = row['colaborador']
                email_atual = row['email'] if pd.notna(row['email']) else ''
                
                # Salva o vínculo de forma permanente na tabela mestre (sem duplicar grafia)
                gravar_vinculo_adm(conexao, adm, responsavel=novo_resp,
                                   email=email_atual if email_atual else None)
            conexao.commit() # Confirma as gravações
            
        # 8. Remove a coluna auxiliar
        df = df.drop(columns=['colaborador_original'])
        
        # 9. Se ainda sobrar alguém sem dono, devolve a tag padrão
        df['colaborador'] = df['colaborador'].fillna('Não Atribuído')
        
        vinculos = df.to_dict('records')
    else:
        vinculos = []
    
    ultimo_upload = conexao.execute("SELECT usuario, data_hora, acao FROM produtividade WHERE acao LIKE 'UPLOAD BASE|%' ORDER BY id DESC LIMIT 1").fetchone()
    usuarios_salvos = conexao.execute("SELECT * FROM usuarios_painel ORDER BY nome ASC").fetchall()
    responsaveis = responsaveis_conhecidos(conexao)
    # quantas administradoras estão gravadas com duas grafias (dá para juntar com um clique)
    vistas, repetidas = set(), 0
    for r in conexao.execute('SELECT administradora FROM vinculos_adm').fetchall():
        chave = _chave_vinculo(r['administradora'])
        if chave in vistas:
            repetidas += 1
        vistas.add(chave)
    conexao.close()
    
    return render_template('configuracoes.html', vinculos=vinculos, ultimo_upload=ultimo_upload,
                           usuarios_salvos=usuarios_salvos, usuario_atual=usuario_atual, eh_master=eh_master,
                           responsaveis=responsaveis, repetidas=repetidas)


# ==============================================================================
# 🔗 VÍNCULOS DAS ADMINISTRADORAS (responsável + e-mail)
# ==============================================================================
# POR QUE ISTO EXISTE: a tela de configurações lista a administradora com a grafia da
# PLANILHA ("ADCIP Adm. e Corretora de Imóveis Paulista Ltda") e o vínculo está gravado
# com outra grafia ("ADCIP ADM. E CORRETORA DE IMóVEIS PAULISTA LTDA"). São 526 das 747
# nessa situação — diferem só em caixa/acento. A listagem já casava as duas com
# TRIM(UPPER(...)), mas a gravação usava INSERT OR REPLACE com a grafia da tela e criava
# uma SEGUNDA linha. Resultado: o e-mail era gravado, a tela continuava mostrando a linha
# velha e parecia que "não gravou". Daqui para frente toda escrita passa por aqui, que
# acha a linha pela chave normalizada e funde as repetidas.
RE_EMAIL_SOLTO = re.compile(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}')
SEM_RESPONSAVEL = 'Não Atribuído'


def _chave_vinculo(texto):
    """Mesma administradora escrita de outro jeito (caixa, acento, espaço) = mesma chave."""
    return _sem_acento_minusculo(str(texto or '')).strip()


def emails_do_texto(texto):
    """Pega os e-mails de qualquer bagunça digitada, sem repetir e na ordem em que vieram."""
    vistos, lista = set(), []
    for email in RE_EMAIL_SOLTO.findall(str(texto or '')):
        limpo = email.strip().lower()
        if limpo not in vistos:
            vistos.add(limpo)
            lista.append(limpo)
    return lista


def _linhas_do_vinculo(conexao, administradora):
    """Todas as linhas de vinculos_adm que são essa administradora, da mais antiga para a mais nova."""
    chave = _chave_vinculo(administradora)
    if not chave:
        return []
    linhas = conexao.execute(
        'SELECT rowid AS linha_id, administradora, responsavel, email FROM vinculos_adm ORDER BY rowid').fetchall()
    return [dict(l) for l in linhas if _chave_vinculo(l['administradora']) == chave]


def _fundir(linhas, administradora):
    """Junta as linhas repetidas em um registro só: fica o responsável de verdade e a
    união dos e-mails (duas grafias podem ter contatos diferentes; perder um é pior)."""
    responsavel, emails = '', []
    for l in linhas:
        resp = str(l.get('responsavel') or '').strip()
        if not responsavel and resp and resp != SEM_RESPONSAVEL:
            responsavel = resp
        for e in emails_do_texto(l.get('email')):
            if e not in emails:
                emails.append(e)
    return {'administradora': administradora, 'responsavel': responsavel or SEM_RESPONSAVEL,
            'email': ', '.join(emails)}


def gravar_vinculo_adm(conexao, administradora, responsavel=None, email=None):
    """Grava responsável e/ou e-mail SEM criar linha repetida.
    responsavel/email em None = não mexe no que já está lá.
    Devolve o registro como ficou."""
    administradora = str(administradora or '').strip()
    if not administradora:
        return None

    linhas = _linhas_do_vinculo(conexao, administradora)
    atual = _fundir(linhas, linhas[0]['administradora'] if linhas else administradora)

    if responsavel is not None:
        novo_resp = str(responsavel).strip()
        if novo_resp:
            atual['responsavel'] = novo_resp
    if email is not None:
        encontrados = emails_do_texto(email)
        # texto em branco apaga; texto sem e-mail nenhum é guardado como veio (ex.: "só por WhatsApp")
        atual['email'] = ', '.join(encontrados) if encontrados else str(email).strip()

    if linhas:
        principal = linhas[0]['linha_id']
        conexao.execute('UPDATE vinculos_adm SET responsavel = ?, email = ? WHERE rowid = ?',
                        (atual['responsavel'], atual['email'], principal))
        repetidas = [l['linha_id'] for l in linhas[1:]]
        if repetidas:
            conexao.execute(
                f"DELETE FROM vinculos_adm WHERE rowid IN ({', '.join('?' * len(repetidas))})", repetidas)
    else:
        conexao.execute('INSERT INTO vinculos_adm (administradora, responsavel, email) VALUES (?, ?, ?)',
                        (administradora, atual['responsavel'], atual['email']))
    return atual


def juntar_vinculos_repetidos(conexao):
    """Passa o pente em toda a tabela: a mesma administradora escrita de dois jeitos vira
    uma linha só. Devolve o que foi juntado, para a tela mostrar."""
    grupos = {}
    for l in conexao.execute(
            'SELECT rowid AS linha_id, administradora, responsavel, email FROM vinculos_adm ORDER BY rowid').fetchall():
        grupos.setdefault(_chave_vinculo(l['administradora']), []).append(dict(l))

    juntados = []
    for chave, linhas in grupos.items():
        if len(linhas) < 2:
            continue
        juntado = _fundir(linhas, linhas[0]['administradora'])
        conexao.execute('UPDATE vinculos_adm SET responsavel = ?, email = ? WHERE rowid = ?',
                        (juntado['responsavel'], juntado['email'], linhas[0]['linha_id']))
        apagar = [l['linha_id'] for l in linhas[1:]]
        conexao.execute(f"DELETE FROM vinculos_adm WHERE rowid IN ({', '.join('?' * len(apagar))})", apagar)
        juntados.append({'administradora': juntado['administradora'],
                         'grafias': [l['administradora'] for l in linhas],
                         'responsavel': juntado['responsavel'], 'email': juntado['email']})
    conexao.commit()
    return juntados


def responsaveis_conhecidos(conexao):
    """Os nomes que o sistema já usa, do jeito que estão gravados. Nada de inventar grafia
    nova ('Jéssica' com acento vira outra pessoa para todo o resto do sistema)."""
    nomes = []
    try:
        for r in conexao.execute(
                "SELECT DISTINCT TRIM(responsavel) AS n FROM vinculos_adm "
                "WHERE IFNULL(TRIM(responsavel), '') != '' AND TRIM(responsavel) != ? ORDER BY 1",
                (SEM_RESPONSAVEL,)).fetchall():
            if r['n'] and r['n'] not in nomes:
                nomes.append(r['n'])
    except Exception:
        pass
    try:
        for r in conexao.execute('SELECT nome FROM usuarios_painel ORDER BY nome').fetchall():
            bonito = str(r['nome'] or '').strip().title()
            if bonito and not any(_chave_vinculo(bonito) == _chave_vinculo(n) for n in nomes):
                nomes.append(bonito)
    except Exception:
        pass
    return nomes


# ==============================================================================
# 📊 RELATÓRIO: MINHA CARTEIRA POR PORTAL (Excel)
# ==============================================================================
# Uma linha por administradora, uma coluna por portal de coleta, igual à planilha que a
# equipe montava na mão. A origem do portal é o Cadastro Mestre do imóvel — a mesma que o
# Raio-X e a matriz usam, então os números conversam entre as telas.
# Imóvel sem portal preenchido (ou sem linha no cadastro) cai em DIVERSOS, que é a regra
# que o resto do sistema já segue; o rodapé da planilha diz quantos foram parar lá.
ORDEM_PORTAIS = ['WINKER', 'WEBWARE', 'CTRL', 'SUPERLÓGICA', 'AUTÔNOMA',
                 'WHATSAPP', 'DOC', 'PROPRIETÁRIO', 'DIVERSOS']
ROTULO_PORTAL = {'SUPERLÓGICA': 'Superlógica', 'AUTÔNOMA': 'Autônoma', 'WHATSAPP': 'Whatsapp',
                 'PROPRIETÁRIO': 'Proprietário', 'WINKER': 'Winker', 'WEBWARE': 'Webware',
                 'CTRL': 'CTRL', 'DOC': 'DOC', 'DIVERSOS': 'Diversos'}


def levantar_carteira_por_portal(conexao, responsavel=''):
    """Carteira por portal em cima da BASE CONGELADA do mês, não da planilha de hoje.

    A planilha encolhe durante o mês: boleto lançado no portal da Lello sai dela. Contar só
    o que está ativo hoje mostraria a carteira pela metade. Aqui a base é a mesma que a
    matriz usa — o que está na planilha MAIS o que já saiu dela neste ciclo —, então os dois
    relatórios dão o mesmo número e ninguém precisa escolher em qual acreditar.
    Devolve (linhas, portais, detalhe, sem_portal, resumo).
    """
    _preparar_vinculos_norm(conexao)
    # compara sem acento e sem diferença de maiúscula ('Jéssica' e 'Jessica' são a mesma pessoa)
    conexao.create_function('norm', 1, _sem_acento_minusculo, deterministic=True)
    alvo = _sem_acento_minusculo(responsavel).strip()
    filtro = '' if alvo in ('', 'todos') else " AND norm(IFNULL(v.responsavel, '')) = ? "

    ciclo = _matriz_ciclo_alvo(conexao)
    origem_sql, origem_params = _matriz_origem('todos', ciclo)
    parametros = list(origem_params) + ([] if not filtro else [alvo])

    chave_ciclo = f'{str(ciclo)[3:]}-{str(ciclo)[:2]}' if ciclo and len(str(ciclo)) == 7 else ''
    base_congelada = 0
    try:
        base_congelada = int(bases_congeladas(conexao, guardar=False).get(chave_ciclo) or 0)
    except Exception:
        base_congelada = 0

    sql = f"""
        SELECT TRIM(i.administradora)                                   AS administradora,
               IFNULL(TRIM(v.responsavel), 'Não Atribuído')             AS responsavel,
               IFNULL(TRIM(v.email), '')                                AS email,
               i.imovel                                                 AS imovel,
               IFNULL(TRIM(cm.empreendimento), '')                      AS empreendimento,
               UPPER(TRIM(IFNULL(NULLIF(TRIM(cm.portal), ''), 'DIVERSOS'))) AS portal,
               UPPER(TRIM(IFNULL(i.status_interno, '')))                AS situacao,
               IFNULL(i.saiu_da_base, 0)                                AS saiu
        FROM ({origem_sql}) i
        LEFT JOIN cadastro_mestre_imoveis cm ON LTRIM(cm.imovel, '0') = LTRIM(i.imovel, '0')
        LEFT JOIN temp.vinc_norm v ON v.chave = chave_adm(i.administradora)
        WHERE IFNULL(TRIM(i.administradora), '') != '' {filtro}
        ORDER BY TRIM(i.administradora), i.imovel"""

    por_adm, detalhe, vistos_portais = {}, [], set()
    sem_portal = resolvidos = fora_da_planilha = 0
    for linha in conexao.execute(sql, parametros).fetchall():
        portal = linha['portal'] or 'DIVERSOS'
        if portal in ('REMOVIDO', ''):
            portal = 'DIVERSOS'
        if portal == 'DIVERSOS':
            sem_portal += 1
        vistos_portais.add(portal)

        entregue = linha['situacao'] in STATUS_FINALIZADOS or int(linha['saiu'] or 0) == 1
        resolvidos += 1 if entregue else 0
        fora_da_planilha += 1 if int(linha['saiu'] or 0) == 1 else 0

        registro = por_adm.setdefault(linha['administradora'], {
            'administradora': linha['administradora'], 'responsavel': linha['responsavel'],
            'email': linha['email'], 'portais': {}, 'total': 0})
        registro['portais'][portal] = registro['portais'].get(portal, 0) + 1
        registro['total'] += 1

        if int(linha['saiu'] or 0) == 1:
            situacao = 'Saiu da planilha (lançado no portal da Lello)'
        elif linha['situacao'] in STATUS_FINALIZADOS:
            situacao = f"Resolvido ({linha['situacao'].title()})"
        else:
            situacao = 'Pendente' + (f" ({linha['situacao'].title()})" if linha['situacao'] else '')
        detalhe.append({'imovel': linha['imovel'], 'administradora': linha['administradora'],
                        'empreendimento': linha['empreendimento'], 'portal': portal,
                        'situacao': situacao, 'responsavel': linha['responsavel']})

    portais = [p for p in ORDEM_PORTAIS if p in vistos_portais]
    portais += sorted(p for p in vistos_portais if p not in ORDEM_PORTAIS)
    linhas = sorted(por_adm.values(), key=lambda x: x['administradora'].upper())
    resumo = {'ciclo': ciclo or '-', 'base_congelada': base_congelada,
              'contados': sum(l['total'] for l in linhas), 'resolvidos': resolvidos,
              'fora_da_planilha': fora_da_planilha, 'sem_portal': sem_portal,
              'geral': bool(alvo in ('', 'todos'))}
    return linhas, portais, detalhe, sem_portal, resumo


def montar_excel_carteira_portal(linhas, portais, detalhe, sem_portal, quem, resumo=None):
    """Monta a planilha. Os totais são FÓRMULA, não número colado: se alguém mexer numa
    célula, a soma acompanha — e dá para conferir a conta dentro do próprio Excel."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    fonte = 'Arial'
    tinta_cabecalho = PatternFill('solid', fgColor='0F172A')
    tinta_total = PatternFill('solid', fgColor='E2E8F0')
    borda = Border(*[Side(style='thin', color='CBD5E1')] * 4)

    livro = Workbook()
    aba = livro.active
    aba.title = 'Carteira por portal'

    aba['A1'] = f'Carteira por portal — {quem}'
    aba['A1'].font = Font(name=fonte, size=14, bold=True)
    resumo = resumo or {}
    contados = sum(l['total'] for l in linhas)
    aba['A2'] = (f'Base congelada de {resumo.get("ciclo", "-")} · {len(linhas)} administradoras · '
                 f'{contados} boletos · {resumo.get("resolvidos", 0)} já resolvidos · '
                 f'gerado em {datetime.now().strftime("%d/%m/%Y %H:%M")}')
    aba['A2'].font = Font(name=fonte, size=9, color='64748B')

    cabecalhos = ['Administradora'] + [ROTULO_PORTAL.get(p, p.title()) for p in portais] + ['TOTAL', 'Tem e-mail?']
    linha_cabecalho = 4
    for coluna, titulo in enumerate(cabecalhos, start=1):
        celula = aba.cell(row=linha_cabecalho, column=coluna, value=titulo)
        celula.font = Font(name=fonte, size=10, bold=True, color='FFFFFF')
        celula.fill = tinta_cabecalho
        celula.alignment = Alignment(horizontal='center' if coluna > 1 else 'left', vertical='center')
        celula.border = borda

    primeira = linha_cabecalho + 1
    col_total = len(portais) + 2
    for numero, registro in enumerate(linhas, start=primeira):
        aba.cell(row=numero, column=1, value=registro['administradora']).font = Font(name=fonte, size=10)
        for indice, portal in enumerate(portais, start=2):
            quantidade = registro['portais'].get(portal)
            celula = aba.cell(row=numero, column=indice, value=quantidade if quantidade else None)
            celula.font = Font(name=fonte, size=10)
            celula.alignment = Alignment(horizontal='center')
            celula.border = borda
        inicio = get_column_letter(2)
        fim = get_column_letter(len(portais) + 1)
        total = aba.cell(row=numero, column=col_total, value=f'=SUM({inicio}{numero}:{fim}{numero})')
        total.font = Font(name=fonte, size=10, bold=True)
        total.alignment = Alignment(horizontal='center')
        total.border = borda
        email = aba.cell(row=numero, column=col_total + 1, value='sim' if registro['email'].strip() else 'SEM E-MAIL')
        email.font = Font(name=fonte, size=9, bold=not registro['email'].strip(),
                          color='B91C1C' if not registro['email'].strip() else '64748B')
        email.alignment = Alignment(horizontal='center')
        aba.cell(row=numero, column=1).border = borda

    ultima = primeira + len(linhas) - 1
    linha_total = ultima + 1
    aba.cell(row=linha_total, column=1, value='TOTAL').font = Font(name=fonte, size=10, bold=True)
    aba.cell(row=linha_total, column=1).fill = tinta_total
    for indice in range(2, col_total + 1):
        letra = get_column_letter(indice)
        celula = aba.cell(row=linha_total, column=indice,
                          value=f'=SUM({letra}{primeira}:{letra}{ultima})' if len(linhas) else 0)
        celula.font = Font(name=fonte, size=10, bold=True)
        celula.fill = tinta_total
        celula.alignment = Alignment(horizontal='center')
        celula.border = borda

    aba.column_dimensions['A'].width = 48
    for indice in range(2, col_total + 2):
        aba.column_dimensions[get_column_letter(indice)].width = 13
    aba.freeze_panes = f'B{primeira}'
    aba.auto_filter.ref = f'A{linha_cabecalho}:{get_column_letter(col_total + 1)}{ultima}'

    nota = linha_total + 2
    aba.cell(row=nota, column=1, value='Como ler esta planilha').font = Font(name=fonte, size=10, bold=True)
    congelada = int(resumo.get('base_congelada') or 0)
    fora = int(resumo.get('fora_da_planilha') or 0)
    explicacoes = [
        'A base é a BASE CONGELADA do mês, não a planilha de hoje: inclui o que já foi lançado '
        'no portal da Lello e saiu da planilha. É a mesma base que a matriz e o relatório de produtividade usam.',
        f'Destes, {fora} já saíram da planilha (lançados no portal) e {resumo.get("resolvidos", 0)} '
        f'estão resolvidos no total. A aba "Detalhe" mostra a situação de cada um.',
        'O portal de cada imóvel vem do Cadastro Mestre — a mesma origem que o Raio-X e a matriz usam.',
        f'Diversos reúne {sem_portal} imóvel(is): os que estão sem portal preenchido no cadastro ou sem linha no cadastro mestre.',
        'A coluna TOTAL e a linha TOTAL são fórmulas: se você filtrar ou editar, a soma acompanha.',
        '"SEM E-MAIL" marca a administradora que não tem e-mail cadastrado — o disparo de cobrança não alcança essas.',
    ]
    if resumo.get('geral') and congelada and congelada != contados:
        explicacoes.insert(2, f'A base congelada do mês tem {congelada} boletos e esta planilha conta {contados}. '
                              f'A diferença de {congelada - contados} saiu da planilha sem deixar de qual '
                              f'administradora era — a base congelada guarda só a quantidade por dia de vencimento.')
    for deslocamento, texto in enumerate(explicacoes):
        aba.cell(row=nota + 1 + deslocamento, column=1, value='• ' + texto).font = Font(name=fonte, size=9, color='475569')

    # ---- aba de conferência ----
    detalhada = livro.create_sheet('Detalhe')
    titulos = ['Nº Imóvel', 'Administradora', 'Empreendimento', 'Portal', 'Situação', 'Responsável']
    for coluna, titulo in enumerate(titulos, start=1):
        celula = detalhada.cell(row=1, column=coluna, value=titulo)
        celula.font = Font(name=fonte, size=10, bold=True, color='FFFFFF')
        celula.fill = tinta_cabecalho
    for numero, item in enumerate(detalhe, start=2):
        for coluna, chave in enumerate(['imovel', 'administradora', 'empreendimento', 'portal',
                                        'situacao', 'responsavel'], start=1):
            detalhada.cell(row=numero, column=coluna, value=item[chave]).font = Font(name=fonte, size=9)
    for coluna, largura in zip('ABCDEF', (14, 44, 34, 16, 40, 18)):
        detalhada.column_dimensions[coluna].width = largura
    detalhada.freeze_panes = 'A2'
    if detalhe:
        detalhada.auto_filter.ref = f'A1:F{len(detalhe) + 1}'
    return livro


@app.route('/relatorio/carteira_por_portal.xlsx')
@login_obrigatorio
def relatorio_carteira_por_portal():
    pedido = request.args.get('responsavel', '').strip()
    quem = session.get('usuario', '').strip() if not pedido else pedido
    if normalizar_texto(quem) == 'TODOS':
        quem = 'todos'

    conexao = conectar_banco()
    try:
        linhas, portais, detalhe, sem_portal, resumo = levantar_carteira_por_portal(conexao, quem)
    finally:
        conexao.close()

    rotulo = 'Operação inteira' if quem == 'todos' else quem
    livro = montar_excel_carteira_portal(linhas, portais, detalhe, sem_portal, rotulo, resumo)
    memoria = BytesIO()
    livro.save(memoria)
    memoria.seek(0)
    apelido = _sem_acento_minusculo(rotulo).lower().replace(' ', '_') or 'carteira'
    return send_file(memoria, as_attachment=True,
                     download_name=f'carteira_por_portal_{apelido}_{datetime.now().strftime("%Y%m%d")}.xlsx',
                     mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')


@app.route('/api/sistema/salvar_vinculo_adm', methods=['POST'])
@login_obrigatorio
def api_salvar_vinculo_adm():
    """Gravação da linha sem recarregar a página."""
    dados = request.get_json(silent=True) or {}
    administradora = str(dados.get('administradora') or '').strip()
    if not administradora:
        return jsonify({'sucesso': False, 'msg': 'Administradora não informada.'}), 400

    conexao = conectar_banco()
    try:
        registro = gravar_vinculo_adm(
            conexao, administradora,
            responsavel=dados.get('colaborador') if dados.get('colaborador') is not None else None,
            email=dados.get('email') if dados.get('email') is not None else None)
        conexao.commit()
    except Exception as erro:
        conexao.close()
        return jsonify({'sucesso': False, 'msg': str(erro)}), 500
    conexao.close()

    invalidos = [p.strip() for p in str(dados.get('email') or '').replace(';', ',').split(',')
                 if p.strip() and not RE_EMAIL_SOLTO.fullmatch(p.strip())]
    return jsonify({'sucesso': True, 'registro': registro,
                    'emails': emails_do_texto(registro['email']), 'ignorados': invalidos})


@app.route('/api/sistema/juntar_vinculos', methods=['POST'])
@login_obrigatorio
def api_juntar_vinculos():
    conexao = conectar_banco()
    try:
        juntados = juntar_vinculos_repetidos(conexao)
    finally:
        conexao.close()
    return jsonify({'sucesso': True, 'qtd': len(juntados), 'juntados': juntados[:50]})


@app.route('/api/sistema/importar_emails_adm', methods=['POST'])
@login_obrigatorio
def api_importar_emails_adm():
    """Cola em lote: cada linha tem o nome da administradora e um ou mais e-mails.
    Com conferir=True só mostra o que vai acontecer — ninguém grava 300 linhas às cegas."""
    dados = request.get_json(silent=True) or {}
    texto = str(dados.get('texto') or '')
    so_conferir = bool(dados.get('conferir'))

    conexao = conectar_banco()
    try:
        conhecidas = {}
        for r in conexao.execute(
                "SELECT DISTINCT TRIM(administradora) AS a FROM imoveis "
                "WHERE ativo = 1 AND IFNULL(TRIM(administradora), '') != ''").fetchall():
            conhecidas.setdefault(_chave_vinculo(r['a']), r['a'])
        for r in conexao.execute('SELECT administradora FROM vinculos_adm').fetchall():
            conhecidas.setdefault(_chave_vinculo(r['administradora']), str(r['administradora']).strip())

        casados, sem_dono, sem_email = [], [], []
        for linha in texto.splitlines():
            if not linha.strip():
                continue
            emails = emails_do_texto(linha)
            nome = RE_EMAIL_SOLTO.sub(' ', linha)
            nome = nome.replace(';', ' ').replace(',', ' ').replace('\t', ' ').strip(' -–—|:')
            nome = ' '.join(nome.split())
            if not emails:
                sem_email.append(linha.strip()[:90])
                continue
            chave = _chave_vinculo(nome)
            alvo = conhecidas.get(chave)
            if not alvo and chave:                      # nome escrito pela metade: tenta por pedaço
                parecidas = [v for k, v in conhecidas.items() if chave in k or k in chave]
                alvo = parecidas[0] if len(parecidas) == 1 else None
            if alvo:
                casados.append({'administradora': alvo, 'emails': emails})
            else:
                sem_dono.append({'linha': linha.strip()[:90], 'emails': emails})

        if not so_conferir:
            for c in casados:
                gravar_vinculo_adm(conexao, c['administradora'], email=', '.join(c['emails']))
            conexao.commit()
    except Exception as erro:
        conexao.close()
        return jsonify({'sucesso': False, 'msg': str(erro)}), 500
    conexao.close()
    return jsonify({'sucesso': True, 'conferindo': so_conferir, 'qtd': len(casados),
                    'casados': casados[:200], 'sem_dono': sem_dono[:50], 'sem_email': sem_email[:20]})


@app.route('/atualizar_configuracoes_adm', methods=['POST'])
@login_obrigatorio
def atualizar_configuracoes_adm():
    adm = str(request.form.get('administradora') or '').strip()
    colab = request.form.get('colaborador')
    email_bruto = request.form.get('email')

    conexao = conectar_banco()
    try:
        # gravar_vinculo_adm acha a linha pela chave normalizada: não cria grafia repetida
        gravar_vinculo_adm(conexao, adm,
                           responsavel=colab if colab else None,
                           email=email_bruto if email_bruto is not None else None)
        conexao.commit()
    finally:
        conexao.close()
    return redirect(url_for('configuracoes'))

@app.route('/api/sistema/atualizar_responsaveis_lote', methods=['POST'])
@login_obrigatorio
def atualizar_responsaveis_lote():
    dados = request.get_json()
    adms = dados.get('adms', [])
    novo_colab = dados.get('colaborador')

    if not adms or not novo_colab:
        return jsonify({'sucesso': False, 'msg': 'Dados incompletos'})

    conexao = conectar_banco()
    try:
        for adm in adms:
            # só troca o responsável; o e-mail que já existe fica onde está
            gravar_vinculo_adm(conexao, adm, responsavel=novo_colab)
        conexao.commit()
        return jsonify({'sucesso': True})
    except Exception as e:
        return jsonify({'sucesso': False, 'msg': str(e)})
    finally:
        conexao.close()

# ==============================================================================
# 📅 UPLOAD DA BASE POR MÊS DE VENCIMENTO
# ==============================================================================
# Antes: toda planilha desativava TODA a base ativa. Subir dezembro e depois querer atualizar novembro derrubava
# dezembro. Agora a planilha só troca o(s) mês(es) de vencimento que ela traz:
#   - planilha de mês NOVO (mais novo que o ativo)  -> igual a sempre: virada de mês (arquiva o antigo no cofre);
#   - planilha do MESMO mês da base ativa           -> igual a sempre: troca a base e herda status/obs;
#   - planilha de mês ANTERIOR ao mais novo ativo   -> atualiza só aquele mês; o mês mais novo continua como estava.
# Nada disso mexe no cofre (historico_fechamento_mes), nas metas nem em base_oficial|AAAA-MM: a congelada permanece.
# Mês "da planilha" = mês com pelo menos 10% das linhas (algumas linhas atrasadas de outro mês não contam como mês).
FRACAO_MINIMA_MES_NA_PLANILHA = 0.10


def _rotulo_ano_mes(ano, mes):
    return f'{int(ano):04d}-{int(mes):02d}'


def _meses_principais(contagem):
    """{(ano, mes): linhas} -> conjunto dos meses que valem (>= 10% das linhas com data válida)."""
    total = sum(contagem.values())
    if not total:
        return set()
    corte = max(1, total * FRACAO_MINIMA_MES_NA_PLANILHA)
    principais = {m for m, q in contagem.items() if q >= corte}
    return principais or {max(contagem, key=contagem.get)}


def contagem_meses_ativos(conexao):
    """{(ano, mes): linhas} da base ativa, pelo mês do vencimento."""
    contagem = {}
    for r in conexao.execute(
            "SELECT substr(vencimento, 7, 4) AS a, substr(vencimento, 4, 2) AS m, COUNT(*) AS q "
            "FROM imoveis WHERE ativo = 1 GROUP BY 1, 2").fetchall():
        if str(r['a']).isdigit() and str(r['m']).isdigit() and 1 <= int(r['m']) <= 12:
            contagem[(int(r['a']), int(r['m']))] = r['q']
    return contagem


def aviso_se_ha_mais_de_um_mes_ativo(conexao):
    """Texto de aviso quando a base ativa tem dois meses ao mesmo tempo (um mês anterior foi atualizado depois da
    virada). Arquivar/congelar meta à mão olha a base ativa INTEIRA e misturaria os meses: nesse caso trava."""
    principais = _meses_principais(contagem_meses_ativos(conexao))
    if len(principais) <= 1:
        return ''
    nomes = ' e '.join(_nome_mes_pt(a, m) for a, m in sorted(principais))
    return (f"A base ativa tem mais de um mês ao mesmo tempo ({nomes}). Arquivar ou congelar a meta à mão olharia os "
            f"dois juntos. A virada normal (subir a planilha do mês novo) já separa tudo sozinha.")


def mes_alvo_da_base_ativa(conexao):
    """((ano, mes), linhas do mês, total) do mês que o robô de e-mail deve perseguir.
    Com dois meses ativos (um mês anterior foi atualizado depois da virada) vale o MAIS NOVO, como era antes,
    quando só existia um mês ativo por vez. Sem base ativa: (None, 0, 0)."""
    contagem = contagem_meses_ativos(conexao)
    principais = _meses_principais(contagem)
    if not principais:
        return None, 0, 0
    alvo = max(principais)
    return alvo, contagem[alvo], sum(contagem.values())


def planejar_upload_por_mes(conexao, vencimentos_planilha):
    """Decide como a planilha entra. Devolve um dicionário:
      modo: 'primeira' | 'mes_novo' | 'mesmo_mes' | 'mes_anterior' | 'sem_data'
      escopo: meses (ano, mes) que a planilha traz;  ativos: meses principais da base ativa agora
      usar_virada: se o fluxo de virada de mês (arquivar/meta/backup) deve rodar, como sempre rodou
      manter: meses da base ativa que NÃO podem ser desativados nesta carga
      limpar_antes: meses antigos esquecidos na base ativa que precisam sair ANTES da virada"""
    contagem_planilha = {}
    for venc in vencimentos_planilha:
        d = _data_br(venc)
        if d:
            contagem_planilha[(d.year, d.month)] = contagem_planilha.get((d.year, d.month), 0) + 1
    escopo = _meses_principais(contagem_planilha)

    contagem_ativos = contagem_meses_ativos(conexao)
    ativos = _meses_principais(contagem_ativos)

    plano = {'escopo': escopo, 'ativos': ativos, 'usar_virada': True, 'manter': set(), 'limpar_antes': set(),
             'modo': 'sem_data' if not escopo else ('primeira' if not ativos else 'mesmo_mes')}
    if not escopo or not ativos:
        return plano                                    # sem referência para comparar: comportamento de sempre

    mais_novo_ativo, mais_novo_planilha = max(ativos), max(escopo)
    if mais_novo_planilha > mais_novo_ativo:
        plano['modo'] = 'mes_novo'
        plano['limpar_antes'] = {m for m in ativos if m < mais_novo_ativo}   # sobras de uma atualização antiga
    elif len(ativos) == 1 and mais_novo_planilha == mais_novo_ativo:
        plano['modo'] = 'mesmo_mes'                     # a troca de base de sempre
    else:
        plano['modo'] = 'mes_anterior'                  # atualiza só o(s) mês(es) da planilha
        plano['usar_virada'] = False
        plano['manter'] = ativos - escopo
    return plano


def desativar_base_para_upload(conexao, plano):
    """Tira da base ativa só o que esta planilha substitui. Sem nada a manter = o UPDATE de sempre."""
    manter = {_rotulo_ano_mes(a, m) for a, m in plano['manter']}
    if not manter:
        conexao.execute("UPDATE imoveis SET ativo = 0")
        return
    marcas = ', '.join('?' for _ in manter)
    conexao.execute(
        f"UPDATE imoveis SET ativo = 0 WHERE ativo = 1 AND "
        f"substr(IFNULL(vencimento, ''), 7, 4) || '-' || substr(IFNULL(vencimento, ''), 4, 2) NOT IN ({marcas})",
        tuple(sorted(manter)))


def _nome_mes_pt(ano, mes):
    nomes = ['Janeiro', 'Fevereiro', 'Março', 'Abril', 'Maio', 'Junho', 'Julho', 'Agosto', 'Setembro', 'Outubro',
             'Novembro', 'Dezembro']
    return f'{nomes[mes - 1]}/{ano}'


class PlanilhaRejeitada(Exception):
    """A planilha não pode entrar (mês errado, vazia, muito menor que a base...). Nada foi alterado no banco."""


def _assinatura_da_planilha(df):
    """Impressão digital do CONTEÚDO que o sistema aproveita da planilha (não depende da ordem das linhas).
    Serve para o robô da base não regravar 5 mil linhas quando nada mudou desde a última importação."""
    colunas = [c for c in ('Vencimento', 'Dt.ComDesc', 'Administradora', 'Carteira', 'D.Lote', 'Imovel',
                           'Empreendimento', 'Endereço do Imóvel', 'Sacado', 'CPF/CNPJ', 'Acesso') if c in df.columns]
    linhas = sorted('|'.join(str(v).strip() for v in r) for r in df[colunas].itertuples(index=False, name=None))
    return hashlib.sha256('\n'.join(linhas).encode('utf-8')).hexdigest()


def importar_planilha_base(caminho_temp, nome_seguro, usuario, mes_esperado=None, pular_se_igual=False,
                           permitir_virada=True, fracao_minima=None):
    """A importação da base (a mesma de sempre), separada da tela para o robô da base poder usar.
    Os 4 últimos parâmetros são TRAVAS que só o robô liga; a importação manual não passa nenhuma:
      mes_esperado   (ano, mes): a planilha tem que ser desse mês, senão nada é alterado;
      pular_se_igual: se o conteúdo é igual ao da última importação, não regrava nada;
      permitir_virada: False = se a planilha for de um mês NOVO (virada), recusa em vez de virar o mês;
      fracao_minima: recusa planilha com menos que essa fração das linhas ativas do mês (relatório incompleto).
    Devolve {'ignorado', 'linhas', 'modo', 'aviso', 'escopo'}. Erro de verdade sobe como exceção (já com rollback)."""
    conexao = None
    virada_info = None
    try:
        if nome_seguro.endswith('.csv'): df_nova_base = pd.read_csv(caminho_temp, skiprows=2)
        else: df_nova_base = pd.read_excel(caminho_temp, skiprows=2)

        df_nova_base.columns = df_nova_base.columns.str.strip()
        df_nova_base = df_nova_base.dropna(subset=['Imovel'])

        dict_media = {}
        if not nome_seguro.endswith('.csv'):
            try:
                df_media = pd.read_excel(caminho_temp, sheet_name='MÉDIA POR ID')
                for _, linha_media in df_media.iterrows():
                    id_imov = str(linha_media.iloc[0]).replace('.0', '').strip()
                    val_media = str(linha_media.iloc[1]).strip()
                    
                    if val_media.upper() == 'SEM INFORMAÇÃO DE DATA' or val_media.lower() == 'nan':
                        val_media = ''
                    elif val_media.replace('.0', '').isdigit():
                        val_media = f"{int(val_media.replace('.0', '')):02d}"
                        
                    if id_imov and id_imov != 'nan':
                        dict_media[id_imov] = val_media
            except Exception as e:
                print(f"⚠️ Aba MÉDIA POR ID não localizada no Excel: {e}", flush=True)

        conexao = conectar_banco()
        
        imoveis_robo = set()
        imoveis_ctrl = set()             # CTRL com login
        imoveis_precisa_prova = set()    # login cadastrado em portal SEM robô próprio: só vira AUTOMAÇÃO se o robô de e-mail já pega
        imoveis_superlogica = set()      # Superlógica saiu da automação: o login fica cadastrado, o status fica em branco
        cursor_robos = conexao.execute("SELECT imovel, portal, link_portal, login_acesso FROM cadastro_mestre_imoveis")
        for row in cursor_robos.fetchall():
            if cadastro_e_superlogica(row['portal'], row['link_portal']):
                imoveis_superlogica.add(row['imovel'])
            elif str(row['login_acesso'] or '').strip():
                imoveis_robo.add(row['imovel'])
                if _portal_normal(row['portal']) == 'CTRL':
                    imoveis_ctrl.add(row['imovel'])
                if _portal_normal(row['portal']) not in ('WINKER', 'WEBWARE'):
                    imoveis_precisa_prova.add(row['imovel'])

        # 🚀 A "FOTOGRAFIA ATRASADA" FOI REMOVIDA DAQUI! 
        
        # 📸 Vencimentos da planilha (também dizem a QUAL mês ela pertence).
        vencimentos_novos = []
        for _, linha_pre in df_nova_base.iterrows():
            imovel_pre = str(linha_pre.get('Imovel', '')).replace('.0', '').strip()
            if imovel_pre and imovel_pre != 'nan':
                vencimentos_novos.append((imovel_pre, vencimento_da_linha_da_planilha(linha_pre)[0]))

        # 📅 A planilha é de qual mês? Mês novo = virada; mesmo mês = troca de base; mês anterior = só aquele mês.
        plano = planejar_upload_por_mes(conexao, [v for _, v in vencimentos_novos])

        # 🛡️ TRAVAS DO ROBÔ (antes de qualquer gravação). A importação manual não passa por nenhuma delas.
        conexao.execute('CREATE TABLE IF NOT EXISTS config_sistema (chave TEXT PRIMARY KEY, valor TEXT)')
        assinatura = _assinatura_da_planilha(df_nova_base)
        if mes_esperado is not None and plano['escopo'] != {mes_esperado}:
            trouxe = ' e '.join(_nome_mes_pt(a, m) for a, m in sorted(plano['escopo'])) or 'nenhuma data válida'
            raise PlanilhaRejeitada(f"A planilha traz {trouxe}, mas o mês esperado era {_nome_mes_pt(*mes_esperado)}. "
                                    f"Nada foi alterado.")
        if plano['modo'] == 'mes_novo' and not permitir_virada:
            raise PlanilhaRejeitada(
                f"A planilha é de {' e '.join(_nome_mes_pt(a, m) for a, m in sorted(plano['escopo']))}, mês mais novo que a base "
                f"ativa. O robô não faz a virada de mês sozinho: escolha o mês em Configurações ou suba a planilha na mão. "
                f"Nada foi alterado.")
        if plano['modo'] in ('mesmo_mes', 'mes_anterior'):
            if pular_se_igual:
                guardada = conexao.execute("SELECT valor FROM config_sistema WHERE chave = 'base_planilha_assinatura'").fetchone()
                if guardada and guardada['valor'] == assinatura:
                    conexao.close()
                    conexao = None
                    return {'ignorado': True, 'linhas': len(df_nova_base), 'modo': plano['modo'], 'aviso': None,
                            'escopo': sorted(plano['escopo'])}
            if fracao_minima:
                ativas = sum(q for mes_ativo, q in contagem_meses_ativos(conexao).items() if mes_ativo in plano['escopo'])
                if ativas >= 200 and len(df_nova_base) < ativas * fracao_minima:
                    raise PlanilhaRejeitada(
                        f"A planilha veio com {len(df_nova_base)} boletos e a base ativa do mês tem {ativas}: menos de "
                        f"{int(fracao_minima * 100)}%. Parece um relatório incompleto. Nada foi alterado "
                        f"(se for isso mesmo, rode o robô com --forcar ou suba a planilha na mão).")

        if plano['limpar_antes']:
            # sobras de meses antigos (atualizações feitas depois da virada) saem antes de a virada arquivar a base
            # ativa, para não entrarem misturadas no cofre do mês que está virando. Já têm a cópia congelada deles.
            for ano_l, mes_l in sorted(plano['limpar_antes']):
                conexao.execute(
                    "UPDATE imoveis SET ativo = 0 WHERE ativo = 1 AND substr(IFNULL(vencimento, ''), 7, 4) = ? "
                    "AND substr(IFNULL(vencimento, ''), 4, 2) = ?", (f'{ano_l:04d}', f'{mes_l:02d}'))
            conexao.commit()
        print(f"[Upload base] modo={plano['modo']} planilha={sorted(plano['escopo'])} "
              f"ativos={sorted(plano['ativos'])} manter={sorted(plano['manter'])}", flush=True)

        # 🔄 VIRADA DE MÊS AUTOMÁTICA: se a planilha é de um mês POSTERIOR ao da base ativa, arquiva a base
        # antiga no cofre (com backup do banco) ANTES de desativá-la. Se falhar, o upload aborta sem mexer em nada.
        # Atualizar um mês ANTERIOR não é virada: não arquiva, não mexe em meta nem no cofre (a congelada permanece).
        if plano['usar_virada']:
            virada_info = virada_mes.preparar_virada_upload(conexao, df_nova_base, usuario=usuario, arquivo=nome_seguro)
        heranca = HerancaDeStatus(conexao, vencimentos_novos)

        # 🤖 Fora Winker/Webware, só vira AUTOMAÇÃO o imóvel que o robô de e-mail já pega sozinho (a mesma regra de ocultar: 2+
        # meses seguidos, contando a partir do mês que esta planilha traz); o resto fica em BRANCO. Se não der para consultar o
        # histórico, mantém como era.
        sem_prova = set()
        try:
            if imoveis_precisa_prova:
                n_ctrl = meses_seguidos_exigidos(conexao, 'CTRL')
                provados = imoveis_que_o_robo_pega_sozinho(conexao, n_ctrl or MESES_SEGUIDOS_DO_ROBO['CTRL'],
                                                           janela_historico_robo(conexao),
                                                           alvo=max(plano['escopo']) if plano['escopo'] else None) or {}
                for i in imoveis_precisa_prova:
                    if str(i or '').strip().lstrip('0') in provados:
                        continue
                    if n_ctrl == 0 and i in imoveis_ctrl:
                        continue                  # configurado: todo CTRL conta como automação
                    sem_prova.add(i)
        except Exception as erro_prova:
            print(f'⚠️ Não consegui consultar o histórico do robô para o status AUTOMAÇÃO: {erro_prova}', flush=True)

        # Desativa da base antiga (mas mantém no banco para histórico) só o que esta planilha substitui
        desativar_base_para_upload(conexao, plano)
        # 🆕 guarda de onde começa esta carga: a conciliação conta as linhas de cada boleto dentro da mesma carga
        id_antes_da_carga = conexao.execute("SELECT IFNULL(MAX(id), 0) FROM imoveis").fetchone()[0]
        inseridas = 0
        
        for index, row in df_nova_base.iterrows():
            num_imovel = str(row.get('Imovel', '')).replace('.0', '').strip()
            if not num_imovel or num_imovel == 'nan': continue
            
            adm  = str(row.get('Administradora', '')).strip()
            cart = str(row.get('Carteira', '')).replace('.0', '').strip()
            lote = str(row.get('D.Lote', '')).replace('.0', '').strip()
            emp  = str(row.get('Empreendimento', '')).strip()
            end  = str(row.get('Endereço do Imóvel', '')).strip()
            sac  = str(row.get('Sacado', '')).strip()
            cpf  = str(row.get('CPF/CNPJ', '')).replace('.0', '').strip()
            
            acesso = str(row.get('Acesso', '')).replace('nan', '').strip()
            venc, dt_comdesc = vencimento_da_linha_da_planilha(row)
            
            media_emissao = dict_media.get(num_imovel, '')
            
            if not media_emissao:
                mestre_row = conexao.execute("SELECT media_emissao FROM cadastro_mestre_imoveis WHERE imovel = ?", (num_imovel,)).fetchone()
                if mestre_row and mestre_row['media_emissao']:
                    media_emissao = mestre_row['media_emissao']
            else:
                conexao.execute("UPDATE cadastro_mestre_imoveis SET media_emissao = ? WHERE imovel = ?", (media_emissao, num_imovel))

            # =========================================================================
            # 🎯 NOVA LÓGICA: BUSCA O STATUS EM TEMPO REAL NO BANCO DE DADOS
            # =========================================================================
            # Cada boleto (imóvel + vencimento) herda o PRÓPRIO status: desconto (mesmo vencimento) herda igual;
            # vencimento alternativo (05 e 15) não copia o status do outro. Virada de mês = boleto novo = em branco.
            status_salvo, obs_salva = heranca.herdar(num_imovel, venc)

            if status_salvo and str(status_salvo).strip().upper() == 'AUTOMAÇÃO' and \
                    (num_imovel in imoveis_superlogica or num_imovel in sem_prova):
                status_salvo = None          # ficou AUTOMAÇÃO de uma base anterior: Superlógica e quem não tem prova voltam para o branco
            if not status_salvo and num_imovel in imoveis_robo and num_imovel not in sem_prova:
                status_salvo = "AUTOMAÇÃO"

            query_ins = 'INSERT INTO imoveis (imovel, vencimento, administradora, carteira, d_lote, empreendimento, endereco, sacado, cpf_cnpj, ativo, status_interno, observacao, dt_comdesc, acesso, media_emissao) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?)'
            conexao.execute(query_ins, (num_imovel, venc, adm, cart, lote, emp, end, sac, cpf, status_salvo, obs_salva, dt_comdesc, acesso, media_emissao))
            inseridas += 1

        # 🔄 Fecha a virada: guarda a meta do mês que acabou e congela a meta do mês novo
        if virada_info is not None:
            virada_mes.finalizar_virada_upload(conexao, virada_info)

        data_hora_atual = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        conexao.execute('INSERT INTO produtividade (usuario, acao, imovel_id, data_hora) VALUES (?, ?, NULL, ?)', 
                        (usuario, f"UPLOAD BASE|{nome_seguro}", data_hora_atual))
        try:
            conciliacao_bw.registrar_carga_base(conexao, id_antes_da_carga, nome_seguro, data_hora_atual)
        except Exception as erro_carga:
            print(f"[Upload base] Não consegui registrar a carga para a conciliação: {erro_carga}", flush=True)
        # impressão digital do que acabou de entrar (o robô compara com ela para não regravar o que não mudou)
        conexao.execute("INSERT OR REPLACE INTO config_sistema (chave, valor) VALUES ('base_planilha_assinatura', ?)", (assinatura,))

        conexao.commit()
        conexao.close()
        conexao = None
        aviso = virada_mes.montar_aviso(virada_info) if virada_info is not None else None
        if plano['modo'] == 'mes_anterior':
            nomes = ' e '.join(_nome_mes_pt(a, m) for a, m in sorted(plano['escopo']))
            ficam = ' e '.join(_nome_mes_pt(a, m) for a, m in sorted(plano['manter'])) or 'os outros meses'
            aviso = (f"Base de {nomes} atualizada. {ficam} continua como estava, e a base congelada de cada mês "
                     f"não foi alterada.")
        return {'ignorado': False, 'linhas': inseridas, 'modo': plano['modo'], 'aviso': aviso,
                'escopo': sorted(plano['escopo'])}
    except Exception:
        try:
            if conexao is not None:
                conexao.rollback()
                conexao.close()
        except Exception:
            pass
        raise


@app.route('/api/sistema/upload_base', methods=['POST'])
@login_obrigatorio
def api_sistema_upload_base():
    caminho_temp = None
    if 'arquivo_base' not in request.files: return "Nenhum arquivo enviado", 400
    arquivo = request.files['arquivo_base']
    if arquivo.filename == '': return "Nome de arquivo inválido", 400

    try:
        nome_seguro = secure_filename(arquivo.filename)
        caminho_temp = os.path.join(os.getcwd(), nome_seguro)
        arquivo.save(caminho_temp)

        resultado = importar_planilha_base(caminho_temp, nome_seguro, session.get('usuario'))
        if resultado['aviso']:
            session['aviso_virada'] = resultado['aviso']
        return redirect(url_for('index'))
    
    except Exception as e:
        return f"Erro fatal ao processar a planilha: {str(e)}", 500
    finally:
        try:
            if caminho_temp and os.path.exists(caminho_temp):
                os.remove(caminho_temp)
        except Exception:
            pass


# ==============================================================================
# 🌐 BASE LELLO AUTOMÁTICA
# ==============================================================================
# O robô (scripts/robos/robo_base_lello.py) entra no portal da Lello, baixa o relatório "Boletos Cond. não
# recebidos" do mês de vencimento que estamos trabalhando e manda para cá. Daqui para dentro é a MESMA importação
# da tela de Configurações (importar_planilha_base), com travas a mais, porque ninguém está olhando:
#   - o mês da planilha tem que ser o mês esperado (senão nada é alterado);
#   - o robô nunca faz a virada de mês sozinho (só se alguém escolheu o mês novo em Configurações);
#   - planilha igual à última importação não regrava nada;
#   - planilha muito menor que a base do mês (relatório incompleto) é recusada.
HORARIOS_BASE_LELLO = '09:00, 11:00, 13:00, 15:00, 17:00'
FRACAO_MINIMA_BASE_LELLO = 0.30
CHAVE_MES_BASE_LELLO = 'base_lello_mes'


def _config_base_lello(conexao, chave):
    try:
        r = conexao.execute("SELECT valor FROM config_sistema WHERE chave = ?", (chave,)).fetchone()
    except Exception:
        return ''
    return str(r['valor'] or '') if r else ''


def alvo_base_lello(conexao):
    """(ano, mes, origem) do mês de vencimento que o robô busca.
      'manual'      = alguém escolheu o mês em Configurações (serve para a virada de mês: vale até a base ativa chegar nele);
      'base ativa'  = o mês da base que está sendo trabalhada agora (o normal: o robô só ATUALIZA o mês em andamento);
      'mês seguinte' = não há base ativa ainda: vencimento principal = o mês depois do atual."""
    conexao.execute('CREATE TABLE IF NOT EXISTS config_sistema (chave TEXT PRIMARY KEY, valor TEXT)')
    alvo, _linhas, _total = mes_alvo_da_base_ativa(conexao)
    manual = re.fullmatch(r'(\d{4})-(\d{2})', _config_base_lello(conexao, CHAVE_MES_BASE_LELLO).strip())
    if manual and 1 <= int(manual.group(2)) <= 12:
        escolhido = (int(manual.group(1)), int(manual.group(2)))
        if alvo is None or escolhido > alvo:
            return escolhido[0], escolhido[1], 'manual'
        conexao.execute("DELETE FROM config_sistema WHERE chave = ?", (CHAVE_MES_BASE_LELLO,))   # a virada já aconteceu
        conexao.commit()
    if alvo:
        return alvo[0], alvo[1], 'base ativa'
    hoje = datetime.now()
    ano, mes = (hoje.year, hoje.month + 1) if hoje.month < 12 else (hoje.year + 1, 1)
    return ano, mes, 'mês seguinte'


def _token_robo_valido(valor):
    """Token do robô, comparado sem vazar tempo. Sem TOKEN_SEGURANCA no servidor ninguém entra."""
    import hmac
    return bool(TOKEN_API_ROBO) and bool(valor) and hmac.compare_digest(str(valor), str(TOKEN_API_ROBO))


def registrar_resultado_base_lello(ok, msg, mes='', origem='', linhas=0, ignorado=False):
    """Guarda como foi a última tentativa (e a última que deu certo) para a tela de Configurações mostrar."""
    registro = {'quando': datetime.now().strftime('%Y-%m-%d %H:%M:%S'), 'ok': bool(ok), 'msg': str(msg or '')[:500],
                'mes': mes, 'origem': origem, 'linhas': int(linhas or 0), 'ignorado': bool(ignorado)}
    texto = json.dumps(registro, ensure_ascii=False)
    conexao = conectar_banco()
    try:
        conexao.execute('CREATE TABLE IF NOT EXISTS config_sistema (chave TEXT PRIMARY KEY, valor TEXT)')
        conexao.execute("INSERT OR REPLACE INTO config_sistema (chave, valor) VALUES ('base_lello_ultima', ?)", (texto,))
        if ok:
            conexao.execute("INSERT OR REPLACE INTO config_sistema (chave, valor) VALUES ('base_lello_ultima_ok', ?)", (texto,))
        conexao.commit()
    except Exception as erro:
        print(f"⚠️ Não consegui guardar o resultado da base Lello: {erro}", flush=True)
    finally:
        conexao.close()


@app.route('/api/automacao/base_lello/plano', methods=['POST'])
def api_base_lello_plano():
    """O robô pergunta: qual mês de vencimento devo buscar no portal?"""
    dados = request.get_json(silent=True) or {}
    if not _token_robo_valido(dados.get('token')):
        return jsonify({'sucesso': False}), 401
    conexao = conectar_banco()
    try:
        ano, mes, origem = alvo_base_lello(conexao)
    finally:
        conexao.close()
    return jsonify({'sucesso': True, 'ano': ano, 'mes': mes, 'rotulo': _nome_mes_pt(ano, mes), 'origem': origem})


@app.route('/api/automacao/base_lello/resultado', methods=['POST'])
def api_base_lello_resultado():
    """O robô conta que não conseguiu (login recusado, portal fora do ar...): fica à vista em Configurações."""
    dados = request.get_json(silent=True) or {}
    if not _token_robo_valido(dados.get('token')):
        return jsonify({'sucesso': False}), 401
    registrar_resultado_base_lello(bool(dados.get('ok')), dados.get('msg'), mes=str(dados.get('mes') or '')[:7],
                                   origem=str(dados.get('origem') or '')[:60])
    return jsonify({'sucesso': True})


@app.route('/api/automacao/upload_base', methods=['POST'])
def api_automacao_upload_base():
    """O robô entrega o Excel baixado do portal. Mesma importação da tela, com as travas de robô."""
    if not _token_robo_valido(request.form.get('token')):
        return jsonify({'sucesso': False, 'msg': 'Token inválido.'}), 401
    arquivo = request.files.get('arquivo_base')
    if arquivo is None or not arquivo.filename:
        return jsonify({'sucesso': False, 'msg': 'Nenhum arquivo enviado.'}), 400
    origem = (request.form.get('origem') or 'robô').strip()[:60]
    forcar = request.form.get('forcar') == '1'

    conexao = conectar_banco()
    try:
        ano, mes, origem_mes = alvo_base_lello(conexao)
    finally:
        conexao.close()
    rotulo = _nome_mes_pt(ano, mes)
    pedido = re.fullmatch(r'(\d{4})-(\d{2})', (request.form.get('mes_esperado') or '').strip())
    if pedido and (int(pedido.group(1)), int(pedido.group(2))) != (ano, mes):
        msg = (f"O robô trouxe {pedido.group(2)}/{pedido.group(1)}, mas o mês esperado agora é {rotulo} "
               f"(a escolha do mês mudou durante a execução, ou foi pedido outro mês na linha de comando). Nada foi alterado.")
        registrar_resultado_base_lello(False, msg, mes=f'{ano:04d}-{mes:02d}', origem=origem)
        return jsonify({'sucesso': False, 'msg': msg}), 409

    nome = secure_filename(arquivo.filename) or 'base_lello.xlsx'
    pasta = tempfile.mkdtemp(prefix='base_lello_')
    caminho = os.path.join(pasta, nome)
    try:
        arquivo.save(caminho)
        r = importar_planilha_base(caminho, nome, 'AUTOMAÇÃO BASE LELLO', mes_esperado=(ano, mes), pular_se_igual=True,
                                   permitir_virada=(origem_mes == 'manual'),
                                   fracao_minima=None if forcar else FRACAO_MINIMA_BASE_LELLO)
        if r['ignorado']:
            msg = f"Base de {rotulo} sem mudanças desde a última atualização ({r['linhas']} boletos); nada foi regravado."
        else:
            msg = f"Base de {rotulo} atualizada: {r['linhas']} boletos importados."
        registrar_resultado_base_lello(True, msg, mes=f'{ano:04d}-{mes:02d}', origem=origem, linhas=r['linhas'],
                                       ignorado=r['ignorado'])
        return jsonify({'sucesso': True, 'msg': msg, 'ignorado': r['ignorado'], 'linhas': r['linhas'],
                        'modo': r['modo'], 'aviso': r['aviso']})
    except PlanilhaRejeitada as e:
        registrar_resultado_base_lello(False, str(e), mes=f'{ano:04d}-{mes:02d}', origem=origem)
        return jsonify({'sucesso': False, 'msg': str(e)}), 422
    except Exception as e:
        msg = f"Erro ao processar a planilha: {e}"
        print(f"🚨 [Base Lello] {msg}", flush=True)
        registrar_resultado_base_lello(False, msg, mes=f'{ano:04d}-{mes:02d}', origem=origem)
        return jsonify({'sucesso': False, 'msg': msg}), 500
    finally:
        shutil.rmtree(pasta, ignore_errors=True)


def _estado_base_lello():
    """Tudo que a tela de Configurações mostra sobre a atualização automática da base."""
    conexao = conectar_banco()
    try:
        ano, mes, origem = alvo_base_lello(conexao)

        def _json(chave):
            try:
                return json.loads(_config_base_lello(conexao, chave) or 'null')
            except Exception:
                return None
        horas, _invalidos = _horas_validas(ler_agenda_salva(conexao).get('base_lello', ''))
        ultima, ultima_ok = _json('base_lello_ultima'), _json('base_lello_ultima_ok')
        ativa = mes_alvo_da_base_ativa(conexao)[0]
    finally:
        conexao.close()
    status = STATUS_ROBOS['BASE_LELLO']
    return {'sucesso': True, 'rodando': bool(status['rodando']),
            'mes': {'valor': f'{ano:04d}-{mes:02d}', 'rotulo': _nome_mes_pt(ano, mes), 'origem': origem},
            'ativa': ({'valor': f'{ativa[0]:04d}-{ativa[1]:02d}', 'rotulo': _nome_mes_pt(*ativa)} if ativa else None),
            'horarios': [f'{h:02d}:{m:02d}' for h, m in horas], 'proxima': _proxima_do_robo('base_lello'),
            'ultima': ultima, 'ultima_ok': ultima_ok, 'logs': status['logs'][:6],
            'credenciais': bool(os.getenv('LELLO_USUARIO') and os.getenv('LELLO_SENHA')),
            'agendador': AGENDADOR is not None, 'gestor': bool(usuario_eh_gestor(session.get('usuario')))}


@app.route('/api/sistema/base_lello/status')
@login_obrigatorio
def api_base_lello_status():
    return jsonify(_estado_base_lello())


@app.route('/api/sistema/base_lello/atualizar', methods=['POST'])
@login_obrigatorio
def api_base_lello_atualizar():
    """Botão "Atualizar agora": sobe o robô na hora (se ele já não estiver rodando)."""
    if not (os.getenv('LELLO_USUARIO') and os.getenv('LELLO_SENHA')):
        return jsonify({'sucesso': False, 'msg': 'Faltam LELLO_USUARIO e LELLO_SENHA no .env (depois de gravar, reinicie o sistema).'})
    iniciou = disparar_robo_agendado('BASE_LELLO', 'scripts/robos/robo_base_lello.py', 'Base Lello',
                                     f"manual ({session.get('usuario')})",
                                     args_extra=['--origem', f"manual — {session.get('usuario')}"])
    if not iniciou:
        return jsonify({'sucesso': False, 'msg': 'A atualização já está rodando.'})
    return jsonify({'sucesso': True, 'msg': 'Robô iniciado.'})


@app.route('/api/sistema/base_lello/mes', methods=['POST'])
@gestor_obrigatorio
def api_base_lello_mes():
    """Escolhe o mês que o robô busca ('2026-12') ou volta ao automático (vazio = mês da base ativa).
    Escolher um mês MAIS NOVO que o da base ativa é a virada de mês: o robô faz a importação como se a planilha
    fosse subida na mão (arquiva o mês que está acabando e congela a meta do novo)."""
    dados = request.get_json(silent=True) or {}
    texto = str(dados.get('mes') or '').strip()
    conexao = conectar_banco()
    try:
        conexao.execute('CREATE TABLE IF NOT EXISTS config_sistema (chave TEXT PRIMARY KEY, valor TEXT)')
        if texto:
            achou = re.fullmatch(r'(\d{4})-(\d{2})', texto)
            if not achou or not 1 <= int(achou.group(2)) <= 12:
                return jsonify({'sucesso': False, 'msg': 'Escolha um mês válido.'})
            conexao.execute("INSERT OR REPLACE INTO config_sistema (chave, valor) VALUES (?, ?)", (CHAVE_MES_BASE_LELLO, texto))
        else:
            conexao.execute("DELETE FROM config_sistema WHERE chave = ?", (CHAVE_MES_BASE_LELLO,))
        conexao.commit()
    finally:
        conexao.close()
    return jsonify(_estado_base_lello())


@app.route('/api/sistema/historico_geral_completo')
@login_obrigatorio
def api_historico_geral_completo():
    conexao = conectar_banco()
    query = '''SELECT p.data_hora, p.usuario, p.acao, i.imovel, i.administradora, v.responsavel as colaborador FROM produtividade p LEFT JOIN imoveis i ON p.imovel_id = i.id LEFT JOIN vinculos_adm v ON v.rowid = vinculo_de(i.administradora) WHERE p.acao IN ('COLETOU BOLETO', 'DISPAROU EMAIL COBRANCA') ORDER BY p.data_hora DESC LIMIT 1000'''
    historico = conexao.execute(query).fetchall()
    conexao.close()
    resultados = []
    for row in historico:
        tipo = "COLETA" if row['acao'] == 'COLETOU BOLETO' else "DISPARO"
    resultados.append({"data_hora": row['data_hora'], "operador": row['usuario'].title() if row['usuario'] else "Desconhecido", "tipo": tipo, "imovel": row['imovel'] or "N/A", "administradora": row['administradora'] or "N/A", "responsavel": row['colaborador'].title() if row['colaborador'] else "Geral"})
    return jsonify(resultados)

@app.route('/api/sistema/coletas_diarias_colaborador')
@login_obrigatorio
def api_coletas_diarias_colaborador():
    conexao = conectar_banco()
    query = '''SELECT SUBSTR(data_hora, 1, 10) as dia, usuario, COUNT(*) as qtd FROM produtividade WHERE acao = 'COLETOU BOLETO' GROUP BY dia, usuario ORDER BY dia DESC, qtd DESC LIMIT 300'''
    dados = conexao.execute(query).fetchall()
    conexao.close()
    resultados = []
    for row in dados:
        partes = row['dia'].split('-')
        dia_formatado = f"{partes[2]}/{partes[1]}/{partes[0]}" if len(partes) == 3 else row['dia']
        resultados.append({"dia": dia_formatado, "colaborador": row['usuario'], "qtd": row['qtd']})
    return jsonify(resultados)

@app.route('/api/visualizar_boleto/<imovel>')
@login_obrigatorio
def visualizar_boleto(imovel):
    import os
    from datetime import datetime
    raiz_rede = r"\\192.168.100.13\Lello2\ARQUIVOS-DIGITALIZACAO\financeiro_imoveis\Condomínio"
    pasta_provisoria = os.path.join(raiz_rede, "boleto__Cond_antes liax")
    meses_traducao = {1: "JAN", 2: "FEV", 3: "MAR", 4: "ABR", 5: "MAI", 6: "JUN", 7: "JUL", 8: "AGO", 9: "SET", 10: "OUT", 11: "NOV", 12: "DEZ"}
    hoje = datetime.now()
    mes_atual = hoje.month
    ano_atual = hoje.strftime('%y')
    mes_prox = mes_atual + 1 if mes_atual < 12 else 1
    ano_prox = ano_atual if mes_atual < 12 else str(int(ano_atual) + 1)
    
    pastas_busca = [pasta_provisoria, os.path.join(raiz_rede, f"{meses_traducao[mes_prox]}{ano_prox} - NOVA GESTÃO"), os.path.join(raiz_rede, f"{meses_traducao[mes_atual]}{ano_atual} - NOVA GESTÃO")]
    
    # 🎯 CORREÇÃO DE EXATIDÃO DO ID: Monta o nome exato do arquivo esperado
    nome_arquivo_esperado = f"{imovel}.pdf".lower()
    
    for pasta in pastas_busca:
        caminho_exato = os.path.join(pasta, f"{imovel}.pdf")
        
        # 1. Tenta achar o arquivo exato direto (o mais rápido)
        if os.path.exists(caminho_exato): 
            return send_file(caminho_exato, mimetype='application/pdf')
            
        # 2. Se não achar, faz a busca na pasta garantindo que termina com '.pdf' ou o nome exato (Ex: '20241.pdf')
        if os.path.exists(pasta):
            try:
                for arquivo in os.listdir(pasta):
                    # Força a checagem: o nome do arquivo (em minúsculas) precisa ser exatamente igual ao esperado
                    # Ou, em casos de sufixos na rede, garantir que a raiz exata do número esteja separada (ex: '20241-1.pdf')
                    arq_min = arquivo.lower()
                    if arq_min == nome_arquivo_esperado or arq_min.startswith(f"{imovel}-") or arq_min.startswith(f"{imovel}_"):
                        return send_file(os.path.join(pasta, arquivo), mimetype='application/pdf')
            except: 
                pass 
                
    return f"Boleto não localizado.", 404

# =================================================================
# MÓDULO DE RELACIONAMENTO (CRM)
# =================================================================
@app.route('/api/automacao/relacionamento', methods=['POST'])
def api_automacao_relacionamento():
    dados = request.get_json()
    if not dados or dados.get('token') != TOKEN_API_ROBO: return jsonify({'sucesso': False}), 401
    
    lista_imoveis = dados.get('imoveis', [])
    tipo = dados.get('tipo', 'COMUNICADO')
    assunto = dados.get('assunto', '')
    link = dados.get('link', '')
    data_hora = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    conexao = conectar_banco()
    for imovel in lista_imoveis:
        conexao.execute('INSERT INTO relacionamento (imovel, tipo, assunto, link, data_hora) VALUES (?, ?, ?, ?, ?)', (str(imovel), tipo, assunto, link, data_hora))
    conexao.commit()
    conexao.close()
    return jsonify({'sucesso': True})

@app.route('/modulo_relacionamento')
@login_obrigatorio
def modulo_relacionamento():
    conexao = conectar_banco()
    # 🎯 ATUALIZADO: Incluído o campo i.carteira na seleção do banco de dados
    query = '''
        SELECT r.*, i.administradora, i.empreendimento, i.carteira,
               IFNULL(v.responsavel, 'Não Atribuído') as colaborador 
        FROM relacionamento r 
        LEFT JOIN imoveis i ON r.imovel = i.imovel 
        LEFT JOIN vinculos_adm v ON v.rowid = vinculo_de(i.administradora) 
        WHERE i.ativo = 1 
        ORDER BY r.id DESC 
        LIMIT 1000
    '''
    registros = conexao.execute(query).fetchall()
    conexao.close()
    return render_template('relacionamento.html', registros=registros)

@app.route('/api/relacionamento/excluir/<int:registro_id>', methods=['POST'])
@login_obrigatorio
def api_relacionamento_excluir(registro_id):
    conexao = conectar_banco()
    try:
        conexao.execute('DELETE FROM relacionamento WHERE id = ?', (registro_id,))
        conexao.commit()
        return jsonify({'sucesso': True})
    except Exception as e:
        conexao.rollback()
        return jsonify({'sucesso': False, 'msg': str(e)})
    finally:
        conexao.close()

# =================================================================
# DASHBOARD GERENCIAL E METAS (ACESSO RESTRITO)
# =================================================================
@app.route('/api/sistema/congelar_metas', methods=['POST'])
@login_obrigatorio
def congelar_metas():
    conexao = conectar_banco()
    try:
        virada_mes.garantir_estrutura_virada(conexao)
        misturado = aviso_se_ha_mais_de_um_mes_ativo(conexao)
        if misturado:
            return jsonify({'sucesso': False, 'msg': misturado}), 409
        resultado = virada_mes.congelar_meta(conexao)
        conexao.commit()
        return jsonify({'sucesso': True, 'total': resultado['total'], 'mes': resultado['rotulo']})
    except Exception as e:
        conexao.rollback()
        return jsonify({'sucesso': False, 'msg': str(e)}), 500
    finally:
        conexao.close()

@app.route('/api/sistema/descongelar_metas', methods=['POST'])
@login_obrigatorio
def descongelar_metas():
    conexao = conectar_banco()
    try:
        virada_mes.garantir_estrutura_virada(conexao)
        virada_mes.descongelar_meta(conexao)  # guarda uma cópia da meta antes de apagar
        conexao.commit()
        return jsonify({'sucesso': True})
    except Exception as e:
        conexao.rollback()
        return jsonify({'sucesso': False, 'msg': str(e)}), 500
    finally:
        conexao.close()

@app.route('/api/sistema/registrar_emissao_hoje', methods=['POST'])
@login_obrigatorio
def registrar_emissao_hoje():
    dados = request.get_json() or {}
    count = int(dados.get('count', 0))
    usuario_atual = session.get('usuario', '')
    data_hora_atual = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    conexao = conectar_banco()
    conexao.execute('INSERT INTO produtividade (usuario, acao, imovel_id, data_hora) VALUES (?, ?, NULL, ?)',
                    (usuario_atual, f"EMISSAO HOJE|{count}", data_hora_atual))
    conexao.commit()
    conexao.close()
    return jsonify({'sucesso': True, 'count': count})

# =================================================================
# GESTÃO DE JORNADA (FÉRIAS, FOLGAS E FERIADOS - COM INTERVALO)
# =================================================================
@app.route('/api/sistema/excecoes', methods=['POST', 'DELETE'])
@login_obrigatorio
def gerenciar_excecoes():
    conexao = conectar_banco()
    conexao.execute('CREATE TABLE IF NOT EXISTS excecoes_jornada (id INTEGER PRIMARY KEY AUTOINCREMENT, data_excecao TEXT, usuario TEXT, tipo TEXT)')
    
    if request.method == 'POST':
        dados = request.get_json()
        data_inicio = dados.get('data_inicio')
        data_fim = dados.get('data_fim')
        
        # Se preencheu só o início, considera que é apenas 1 dia
        if not data_fim:
            data_fim = data_inicio
            
        usuario = dados.get('usuario', 'TODOS')
        tipo = dados.get('tipo')
        
        try:
            start_dt = datetime.strptime(data_inicio, '%Y-%m-%d')
            end_dt = datetime.strptime(data_fim, '%Y-%m-%d')
            delta = end_dt - start_dt
            
            # Laço inteligente: Cadastra todos os dias do intervalo de férias
            for i in range(delta.days + 1):
                dia_atual = (start_dt + timedelta(days=i)).strftime('%Y-%m-%d')
                
                # Deleta se já existir para não duplicar
                conexao.execute("DELETE FROM excecoes_jornada WHERE data_excecao = ? AND usuario = ?", (dia_atual, usuario))
                # Insere o novo
                conexao.execute("INSERT INTO excecoes_jornada (data_excecao, usuario, tipo) VALUES (?, ?, ?)", 
                                (dia_atual, usuario, tipo))
            
            conexao.commit()
            return jsonify({'sucesso': True})
        except Exception as e:
            return jsonify({'sucesso': False, 'msg': str(e)})
        finally:
            conexao.close()
            
    elif request.method == 'DELETE':
        dados = request.get_json()
        conexao.execute("DELETE FROM excecoes_jornada WHERE id = ?", (dados.get('id'),))
        conexao.commit()
        conexao.close()
        return jsonify({'sucesso': True})

@app.route('/api/sistema/excecoes_lista', methods=['GET'])
@login_obrigatorio
def listar_excecoes():
    mes = request.args.get('mes', datetime.now().strftime('%Y-%m'))
    conexao = conectar_banco()
    try:
        lista = conexao.execute("SELECT * FROM excecoes_jornada WHERE data_excecao LIKE ? ORDER BY data_excecao ASC", (f"{mes}%",)).fetchall()
        return jsonify([dict(r) for r in lista])
    except:
        return jsonify([])

# =================================================================
# HOME OFFICE DOS COLABORADORES (EDITÁVEL PELO DASHBOARD)
# =================================================================
TIME_OPERACIONAL_BASE = ['Ellen', 'Jessica', 'Victor', 'Ryan', 'Arthur', 'Joao', 'Geovanna', 'Heloisa']
# Dias de home office que já valiam antes de ficar editável (0=Seg, 1=Ter, 2=Qua, 3=Qui, 4=Sex).
HOME_OFFICE_PADRAO = {'Ellen': [1, 3], 'Jessica': [0, 2], 'Arthur': [0, 3], 'Ryan': [1, 3, 4]}
SQL_STATUS_COLETADO = "('PAGO', 'COLETADO', 'PAGO NA 275', 'PAGO NA ADM')"

def _nome_colaborador_padrao(nome):
    """Padroniza o nome igual à matriz de produtividade: sem acento e com Inicial Maiúscula."""
    nome = ' '.join(str(nome or '').split())
    if not nome:
        return ''
    nome = unicodedata.normalize('NFKD', nome).encode('ascii', 'ignore').decode('ascii').title()
    if nome == 'Giovanna':
        nome = 'Geovanna'
    return nome

def _garantir_home_office(conexao):
    """Cria a tabela de home office e, na primeira vez, grava os dias que já valiam antes."""
    conexao.execute('CREATE TABLE IF NOT EXISTS home_office_colaboradores (usuario TEXT NOT NULL, dia_semana INTEGER NOT NULL, PRIMARY KEY (usuario, dia_semana))')
    conexao.execute('CREATE TABLE IF NOT EXISTS config_sistema (chave TEXT PRIMARY KEY, valor TEXT)')
    ja_semeado = conexao.execute("SELECT 1 FROM config_sistema WHERE chave = 'home_office_semeado'").fetchone()
    if not ja_semeado:
        for nome, dias in HOME_OFFICE_PADRAO.items():
            for d in dias:
                conexao.execute('INSERT OR IGNORE INTO home_office_colaboradores (usuario, dia_semana) VALUES (?, ?)', (nome, d))
        conexao.execute("DELETE FROM config_sistema WHERE chave = 'home_office_semeado'")
        conexao.execute("INSERT INTO config_sistema (chave, valor) VALUES ('home_office_semeado', '1')")
        conexao.commit()

def _mapa_home_office(conexao):
    """Retorna {nome: {dias_da_semana}}. Se algo falhar, usa os dias padrão para a matriz nunca quebrar."""
    try:
        _garantir_home_office(conexao)
        mapa = {}
        for r in conexao.execute('SELECT usuario, dia_semana FROM home_office_colaboradores').fetchall():
            mapa.setdefault(r['usuario'], set()).add(int(r['dia_semana']))
        return mapa
    except Exception:
        return {n: set(d) for n, d in HOME_OFFICE_PADRAO.items()}

@app.route('/api/sistema/home_office', methods=['GET', 'POST', 'DELETE'])
@login_obrigatorio
def api_home_office():
    if not usuario_eh_gestor(session.get('usuario')):
        return jsonify({'sucesso': False, 'msg': 'Acesso restrito à gestão.'}), 403

    conexao = conectar_banco()
    try:
        if request.method == 'GET':
            mapa = _mapa_home_office(conexao)
            nomes = list(TIME_OPERACIONAL_BASE) + sorted(n for n in mapa if n not in TIME_OPERACIONAL_BASE)
            return jsonify({'sucesso': True,
                            'colaboradores': [{'usuario': n, 'dias': sorted(mapa.get(n, [])), 'fixo': n in TIME_OPERACIONAL_BASE} for n in nomes]})

        dados = request.get_json(silent=True) or {}
        nome = _nome_colaborador_padrao(dados.get('usuario'))
        if not nome or len(nome) > 40 or nome.upper() in ('TODOS', 'ROBOS'):
            return jsonify({'sucesso': False, 'msg': 'Informe um nome de colaborador válido.'}), 400

        _garantir_home_office(conexao)
        # POST substitui os dias do colaborador; DELETE remove todos.
        conexao.execute('DELETE FROM home_office_colaboradores WHERE usuario = ?', (nome,))
        if request.method == 'POST':
            dias = {int(d) for d in (dados.get('dias') or []) if str(d).isdigit() and 0 <= int(d) <= 4}
            for d in sorted(dias):
                conexao.execute('INSERT INTO home_office_colaboradores (usuario, dia_semana) VALUES (?, ?)', (nome, d))
        conexao.commit()
        return jsonify({'sucesso': True, 'usuario': nome})
    except Exception as e:
        conexao.rollback()
        return jsonify({'sucesso': False, 'msg': str(e)}), 500
    finally:
        conexao.close()

def _montar_carteira(linhas):
    """Junta nomes equivalentes (Jéssica/Jessica) e calcula coletados, pendentes e % por responsável."""
    agrupado = {}
    for r in linhas:
        bruto = str(r['nome'] or '').strip()
        if not bruto or 'ATRIBU' in bruto.upper():
            nome = 'Não atribuído'
        else:
            nome = _nome_colaborador_padrao(bruto) or 'Não atribuído'
        reg = agrupado.setdefault(nome, {'nome': nome, 'base': 0, 'coletados': 0})
        reg['base'] += int(r['base'] or 0)
        reg['coletados'] += int(r['coletados'] or 0)
    saida = []
    for reg in agrupado.values():
        reg['pendentes'] = reg['base'] - reg['coletados']
        reg['pct'] = round(reg['coletados'] / reg['base'] * 100, 1) if reg['base'] > 0 else 0
        saida.append(reg)
    saida.sort(key=lambda x: (x['nome'] == 'Não atribuído', -x['base']))
    return saida

def _montar_status_pendentes(linhas):
    """Transforma status internos em rótulos curtos para o painel de pendências."""
    rotulos = {'AUTOMAÇÃO': 'Automação', 'NÃO EMITIDO': 'Não emitido', 'SOLICITADO': 'Solicitado',
               'EMAIL RECEBIDO': 'E-mail recebido', 'CHEQUE/DOC': 'Cheque / DOC', 'ERRO DE REDE': 'Erro de rede',
               '': 'Sem status'}
    saida = []
    for r in linhas:
        bruto = str(r['status'] or '').strip().upper()
        saida.append({'nome': rotulos.get(bruto, bruto.capitalize()), 'qtd': int(r['qtd'])})
    return saida

# --- Dashboard gerencial > Automático x Manual (analise_manual.py + templates/analise_manual.html) ---
try:
    from . import analise_manual
except ImportError:
    import analise_manual
analise_manual.registrar_rotas(app, gestor_obrigatorio, _pasta_do_banco())

# --- Dashboard gerencial > Base × Relatório × BW (conciliacao_bw.py + templates/conciliacao_bw.html) ---
try:
    from . import conciliacao_bw
except ImportError:
    import conciliacao_bw
conciliacao_bw.registrar_rotas(app, gestor_obrigatorio, lambda: conectar_banco())


# ==============================================================================
# ⚠️ INCONSISTÊNCIAS BRADESCO (planilha "Inc. Bradesco", abas Loc574) NA LISTA DE PENDÊNCIAS
# ==============================================================================
# A aba Loc574 é onde o Bradesco devolve o pagamento de um boleto de condomínio (ocorrências ZE, YA, AM, ZI...). A
# conciliação do BW já lê essa planilha (tabela conc_inc). Aqui ela vira trabalho do dia a dia:
#   - a lista de pendências mostra um selo nos boletos que têm devolução PENDENTE e o atalho "Inconsistência";
#   - o atalho abre um painel com TODAS as devoluções pendentes (vencimento recente, de qualquer mês) para a equipe atuar;
#   - atuar = trocar a Situação (PENDENTE -> OK) e a Observação da própria linha da planilha, direto no Google Sheets;
#   - "Enviar p/ Internet" abre o módulo Internet já preenchido: ao enviar a planilha ganha "INTERNET dd/mm" e, quando a
#     gestão aprova o pagamento, a Situação vira OK.
# A linha da planilha é conferida (Referência + Doc. Pagfor) antes de gravar, porque a equipe insere/ordena linhas lá.
# Se o Google recusar (sem permissão de editor, sem rede), a ação fica guardada (tabela inc_acoes) e o sistema tenta de
# novo sozinho; enquanto isso a tela já mostra o que a equipe lançou.
# config_sistema: 'inc_ativo' = '0' desliga tudo; 'inc_dias_passado' (padrão 60) = só entram devoluções com vencimento
# nos últimos N dias (as de 2024/2025 que ficaram PENDENTE na planilha não poluem a tela).
INC_DIAS_PASSADO = 60
# Ocorrências (sigla do Bradesco) que NÃO são nossas e por isso nem aparecem no painel, no filtro, no contador nem no selo da lista.
# Ex.: 'AM' = Agência do Favorecido Inválida (a devolução é de outra operação). Troca/inclui em config_sistema 'inc_ocorrencias_ignoradas'
# (siglas separadas por vírgula; vazio = não ignora nenhuma). A conciliação do BW continua lendo a planilha inteira.
INC_OCORRENCIAS_IGNORADAS = ('AM',)
# Só entram no painel, no filtro, no contador e no selo as devoluções cuja coluna CONTA (da planilha) é uma destas.
# Fora dessa lista, a linha é desconsiderada aqui (a conciliação do BW continua lendo a planilha inteira).
INC_CONTAS_VALIDAS = {'231', '33', '211', '212', '132', '275', '276'}
# (texto, Situação sugerida, pede data?)  '{dd/mm}' vira a data escolhida (padrão: hoje). Frases que a equipe já usa na planilha.
INC_OBS_PADRAO = [
    ('INTERNET {dd/mm}', 'OK', True),
    ('AJUSTADO LANÇADO', 'OK', False),
    ('PAGO NA ADM', 'OK', False),
    ('PAGO ADM', 'OK', False),
    ('PAGO NA 275', 'OK', False),
    ('CORRIGIDO E ADICIONADO NA PASTA LIAX NOVAMENTE', 'OK', False),
    ('REPROGRAMADO', 'OK', False),
    ('LANÇADO NO PORTAL', 'OK', False),
    ('CONSTA PAGO ADM', 'OK', False),
    ('PP PAGOU', 'OK', False),
    ('VERIFICANDO NA ADM', 'PENDENTE', False),
    ('AGUARDANDO ADM {dd/mm}', 'PENDENTE', True),
    ('EMAIL ENVIADO {dd/mm}', 'PENDENTE', True),
]
_INC_FRASES_VALIDAS = {t for t, _s, _d in INC_OBS_PADRAO}
_INC_CACHE_ABAS = {'quando': 0.0, 'titulos': {}}
_INC_THREAD = {'t': None, 'ultimo_inicio': 0.0, 'ultimo_reenvio': 0.0}
# Cruzar as pendências do Inc. Bradesco com a base ativa inteira (milhares de imóveis) é caro para
# fazer em toda carga de tela (não só quando o atalho Inconsistência está aberto) - por isso o resultado
# fica neste cache por até SEGUNDOS_CACHE_INC, e é descartado na hora quando algo de fato muda
# (ação da equipe num caso, ou planilha lida de novo) - ver esquecer_cache_inconsistencia().
SEGUNDOS_CACHE_INC = 90
_CACHE_INC_LISTA = {'quando': 0.0, 'lista': [], 'por_id': {}}
_TRAVA_CACHE_INC_LISTA = threading.Lock()


def inc_config(conexao):
    """(ativo, dias do passado)."""
    ativo, dias = True, INC_DIAS_PASSADO
    try:
        cfg = {r['chave']: r['valor'] for r in conexao.execute(
            "SELECT chave, valor FROM config_sistema WHERE chave IN ('inc_ativo', 'inc_dias_passado')").fetchall()}
        if str(cfg.get('inc_ativo', '1')).strip() == '0':
            ativo = False
        if str(cfg.get('inc_dias_passado') or '').strip().isdigit():
            dias = int(cfg['inc_dias_passado'])
    except Exception:
        pass
    return ativo, dias


def inc_ocorrencias_ignoradas(conexao):
    """Conjunto de siglas de ocorrência (maiúsculas) que o painel desconsidera."""
    try:
        r = conexao.execute("SELECT valor FROM config_sistema WHERE chave = 'inc_ocorrencias_ignoradas'").fetchone()
        if r is not None and r['valor'] is not None:
            return {x.strip().upper() for x in str(r['valor']).split(',') if x.strip()}
    except Exception:
        pass
    return set(INC_OCORRENCIAS_IGNORADAS)


def inc_garantir_tabelas(conexao):
    """inc_acoes (histórico + fila do que ainda não chegou ao Google) e as colunas do vínculo na fila da Internet."""
    conexao.execute('''CREATE TABLE IF NOT EXISTS inc_acoes (
        id INTEGER PRIMARY KEY AUTOINCREMENT, aba TEXT, linha INTEGER, referencia TEXT, doc_pagfor TEXT, venc TEXT,
        chave TEXT, usuario TEXT, data_hora TEXT, situacao_antes TEXT, obs_antes TEXT, situacao_nova TEXT, obs_nova TEXT,
        origem TEXT DEFAULT 'SISTEMA', pendencia_internet_id INTEGER, enviado INTEGER DEFAULT 0, enviado_em TEXT,
        erro TEXT DEFAULT '')''')
    conexao.execute('CREATE INDEX IF NOT EXISTS idx_inc_acoes_caso ON inc_acoes (aba, referencia, doc_pagfor, venc)')
    try:
        colunas = {r[1] for r in conexao.execute('PRAGMA table_info(pendencias_internet)')}
        if colunas:
            for coluna, tipo in (('inc_aba', 'TEXT'), ('inc_linha', 'INTEGER'), ('inc_ref', 'TEXT'),
                                 ('inc_doc', 'TEXT'), ('inc_venc', 'TEXT')):
                if coluna not in colunas:
                    conexao.execute(f'ALTER TABLE pendencias_internet ADD COLUMN {coluna} {tipo}')
    except Exception as erro:
        print(f'⚠️ Não consegui preparar as colunas do vínculo Inconsistência x Internet: {erro}', flush=True)
    try:
        conciliacao_bw.garantir_tabelas(conexao)     # conc_inc (a mesma tabela da conciliação do BW)
    except Exception as erro:
        print(f'⚠️ Não consegui preparar a tabela do Inc. Bradesco: {erro}', flush=True)


def _inc_chave(valor):
    return str(valor or '').strip().lstrip('0')


def _inc_ref(valor):
    return re.sub(r'\s+', '', str(valor or '')).upper()


def _inc_digitos(valor):
    return re.sub(r'\D', '', re.sub(r'\.0$', '', str(valor or '').strip())).lstrip('0')


def _inc_norm(texto):
    t = unicodedata.normalize('NFKD', str(texto or '')).encode('ascii', 'ignore').decode('ascii')
    return re.sub(r'\s+', ' ', t).strip().upper()


def _inc_iso_para_br(iso):
    m = re.match(r'^(\d{4})-(\d{2})-(\d{2})', str(iso or ''))
    return f'{m[3]}/{m[2]}/{m[1]}' if m else ''


def _inc_data_texto(valor):
    """'10/11/2026', ' 11/11/2026', '2026-11-10', serial do Google/Excel -> 'aaaa-mm-dd' ('' se não for data)."""
    if valor is None or valor == '':
        return ''
    if isinstance(valor, (int, float)) and not isinstance(valor, bool) and 20000 < valor < 80000:
        return (datetime(1899, 12, 30) + timedelta(days=int(valor))).strftime('%Y-%m-%d')
    t = str(valor).strip()
    m = re.match(r'^(\d{1,2})/(\d{1,2})/(\d{4})', t)
    if m:
        return f'{m[3]}-{int(m[2]):02d}-{int(m[1]):02d}'
    m = re.match(r'^(\d{4})-(\d{2})-(\d{2})', t)
    return f'{m[1]}-{m[2]}-{m[3]}' if m else ''


# ------------------------------------------------------------------ leitura: devoluções PENDENTES
def inc_pendentes(conexao, enriquecer=True, incluir_ok_meses=None):
    """Devoluções PENDENTES da planilha com vencimento recente (ou futuro), mais antigas primeiro. Já considera o que a equipe
    lançou aqui e ainda não chegou ao Google. enriquecer=True junta o imóvel/administradora/responsável da base e o vínculo
    com a Internet (só o painel precisa disso). incluir_ok_meses: conjunto opcional de 'AAAA-MM' - além das PENDENTE, também
    traz os casos já marcados OK cujo vencimento cai num desses meses (painel: "mostrar também os já OK")."""
    ativo, dias = inc_config(conexao)
    if not ativo:
        return []
    limite = (datetime.now() - timedelta(days=dias)).strftime('%Y-%m-%d')
    try:
        linhas = conexao.execute(
            "SELECT aba, linha, data, referencia, carteira, chave, doc_pagfor, venc, valor, ocorrencia, descricao, situacao, obs, conta "
            "FROM conc_inc WHERE venc >= ? ORDER BY venc, aba, linha", (limite,)).fetchall()
    except sqlite3.OperationalError:
        return []                                     # a tabela do Inc. Bradesco ainda não existe neste banco
    local = {}
    try:
        # o que acabou de ser gravado no Google vale por cima da leitura se a leitura é de antes (ou de segundos depois) da gravação:
        # protege da leitura em segundo plano que começou antes e terminou depois; uma leitura bem posterior já traz tudo.
        recente = (datetime.now() - timedelta(minutes=3)).strftime('%Y-%m-%d %H:%M:%S')
        ultima_leitura = conexao.execute("SELECT MAX(carregado_em) FROM conc_inc_meta").fetchone()[0]
        if ultima_leitura:
            recente = (datetime.strptime(ultima_leitura, '%Y-%m-%d %H:%M:%S') - timedelta(seconds=30)).strftime('%Y-%m-%d %H:%M:%S')
        for a in conexao.execute("SELECT aba, referencia, doc_pagfor, venc, situacao_nova, obs_nova, enviado FROM inc_acoes "
                                 "WHERE enviado = 0 OR (enviado = 1 AND enviado_em >= ?) ORDER BY id", (recente,)).fetchall():
            local[(a['aba'], _inc_ref(a['referencia']), _inc_digitos(a['doc_pagfor']), a['venc'])] = a
    except sqlite3.OperationalError:
        pass
    ignoradas = inc_ocorrencias_ignoradas(conexao)
    saida = []
    for r in linhas:
        if str(r['ocorrencia'] or '').strip().upper() in ignoradas:
            continue                                  # ex.: AM (agência do favorecido inválida) não é nosso
        conta = re.sub(r'\.0$', '', str(r['conta'] or '').strip())
        if conta not in INC_CONTAS_VALIDAS:
            continue                                  # conta fora da lista (231/33/211/212/132/275/276): não é nosso
        situacao, obs, no_google = str(r['situacao'] or '').strip().upper(), str(r['obs'] or ''), True
        acao = local.get((r['aba'], _inc_ref(r['referencia']), _inc_digitos(r['doc_pagfor']), r['venc']))
        if acao:
            if acao['situacao_nova']:
                situacao = acao['situacao_nova']
            if acao['obs_nova'] is not None:
                obs = acao['obs_nova']
            no_google = bool(acao['enviado'])
        if situacao != 'PENDENTE':
            if not (incluir_ok_meses and situacao == 'OK' and r['venc'][:7] in incluir_ok_meses):
                continue
        saida.append({
            'aba': r['aba'], 'linha': r['linha'], 'referencia': r['referencia'], 'carteira': r['carteira'],
            'chave': _inc_chave(r['chave']), 'doc_pagfor': r['doc_pagfor'] or '', 'venc': r['venc'], 'conta': conta,
            'venc_br': _inc_iso_para_br(r['venc']), 'valor': r['valor'], 'ocorrencia': r['ocorrencia'] or '',
            'descricao': r['descricao'] or '', 'situacao': situacao, 'obs': obs, 'aguardando_google': not no_google,
            'situacao_planilha': str(r['situacao'] or '').strip().upper(), 'obs_planilha': str(r['obs'] or ''),
            'imovel_id': None, 'administradora': '', 'responsavel': '', 'status_interno': '', 'sacado': '',
            'endereco': '', 'empreendimento': '', 'na_base_ativa': False, 'internet_id': None, 'internet_status': ''})
    if enriquecer and saida:
        _inc_enriquecer(conexao, saida)
    return saida


def _inc_enriquecer(conexao, lista):
    """Junta, para cada devolução, o boleto da base (o da base ativa; senão o mais recente) e o pedido na Internet, se houver."""
    for c in lista:
        base = None
        if c['chave']:
            base = conexao.execute(
                "SELECT id, imovel, administradora, status_interno, sacado, endereco, empreendimento, ativo FROM imoveis "
                "WHERE LTRIM(imovel, '0') = ? AND substr(vencimento, 1, 10) = ? ORDER BY ativo DESC, id DESC LIMIT 1",
                (c['chave'], c['venc_br'])).fetchone()
        elif c['carteira']:
            base = conexao.execute(
                "SELECT id, imovel, administradora, status_interno, sacado, endereco, empreendimento, ativo FROM imoveis "
                "WHERE CAST(carteira AS TEXT) = ? AND substr(vencimento, 1, 10) = ? ORDER BY ativo DESC, id DESC LIMIT 1",
                (str(c['carteira']), c['venc_br'])).fetchone()
            if base:
                c['chave'] = _inc_chave(base['imovel'])
        if base:
            c.update(imovel_id=base['id'], administradora=base['administradora'] or '', status_interno=base['status_interno'] or '',
                     sacado=base['sacado'] or '', endereco=base['endereco'] or '', empreendimento=base['empreendimento'] or '',
                     na_base_ativa=bool(base['ativo']))
            v = conexao.execute("SELECT v.responsavel FROM vinculos_adm v WHERE v.rowid = vinculo_de(?)",
                                (base['administradora'],)).fetchone()
            c['responsavel'] = (v['responsavel'] if v and v['responsavel'] else '') or ''
        try:
            p = conexao.execute("SELECT id, status FROM pendencias_internet WHERE inc_aba = ? AND inc_linha = ? "
                                "ORDER BY id DESC LIMIT 1", (c['aba'], c['linha'])).fetchone()
            if p:
                c['internet_id'], c['internet_status'] = p['id'], p['status']
        except sqlite3.OperationalError:
            pass


def inc_casar_com_base_ativa(conexao, lista):
    """{imoveis.id da base ativa: devolução pendente}: alimenta o selo nas linhas da lista e o atalho "Inconsistência".
    Casa pelo nº do imóvel (coluna ID da planilha) + vencimento; sem ID, pela carteira (Referência antes da barra) + vencimento.
    Uma passada só pela base ativa (não uma consulta por caso)."""
    if not lista:
        return {}
    por_chave, por_carteira = {}, {}
    for c in lista:
        if c['chave']:
            por_chave.setdefault((c['chave'], c['venc_br']), c)
        elif c['carteira']:
            por_carteira.setdefault((str(c['carteira']).lstrip('0'), c['venc_br']), c)
    achados = {}
    for r in conexao.execute("SELECT id, imovel, carteira, substr(vencimento, 1, 10) AS venc FROM imoveis WHERE ativo = 1"):
        c = por_chave.get((_inc_chave(r['imovel']), r['venc']))
        if c is None and por_carteira:
            c = por_carteira.get((str(r['carteira'] or '').strip().lstrip('0'), r['venc']))
        if c is not None:
            achados[r['id']] = c
    return achados


def inc_lista_e_por_id_em_cache(conexao):
    """(inc_lista, inc_por_id) para o selo nas linhas, o contador do atalho e o filtro "Inconsistência" - a mesma conta que
    antes rodava do zero em TODA carga da tela principal. Fica em cache por até SEGUNDOS_CACHE_INC; esquecer_cache_inconsistencia()
    descarta na hora quando algo muda de fato (ação num caso ou planilha lida de novo)."""
    with _TRAVA_CACHE_INC_LISTA:
        fresco = time.time() - _CACHE_INC_LISTA['quando'] < SEGUNDOS_CACHE_INC
        if fresco:
            return _CACHE_INC_LISTA['lista'], _CACHE_INC_LISTA['por_id']
    lista = inc_pendentes(conexao, enriquecer=False)
    por_id = inc_casar_com_base_ativa(conexao, lista)
    with _TRAVA_CACHE_INC_LISTA:
        _CACHE_INC_LISTA.update(quando=time.time(), lista=lista, por_id=por_id)
    return lista, por_id


def esquecer_cache_inconsistencia():
    """Chamado depois de uma ação num caso (inc_aplicar) ou de uma leitura nova da planilha: o próximo acesso recalcula."""
    with _TRAVA_CACHE_INC_LISTA:
        _CACHE_INC_LISTA['quando'] = 0.0


_INC_MESES_PT = ('janeiro', 'fevereiro', 'março', 'abril', 'maio', 'junho', 'julho',
                 'agosto', 'setembro', 'outubro', 'novembro', 'dezembro')


def inc_meses_de_trabalho():
    """(mês civil atual 'AAAA-MM', mês seguinte 'AAAA-MM') - a equipe trabalha adiantada (em novembro já fecha dezembro),
    então o painel pode mostrar os "OK" desses dois meses em vez de só do mês corrente."""
    hoje = datetime.now()
    atual = hoje.strftime('%Y-%m')
    seguinte = f'{hoje.year + 1}-01' if hoje.month == 12 else f'{hoje.year}-{hoje.month + 1:02d}'
    return (atual, seguinte)


def inc_nomes_meses_trabalho():
    """Nomes em português dos dois meses de inc_meses_de_trabalho(), para o rótulo do checkbox no painel."""
    return tuple(_INC_MESES_PT[int(m.split('-')[1]) - 1] for m in inc_meses_de_trabalho())


def inc_status_da_leitura(conexao):
    """('há X min' do último Google lido, mensagem de falha recente ou '')."""
    quando, falha = '', ''
    try:
        r = conexao.execute("SELECT carregado_em, origem FROM conc_inc_meta ORDER BY id DESC LIMIT 1").fetchone()
        if r and r['carregado_em']:
            quando = r['carregado_em']
    except sqlite3.OperationalError:
        pass
    try:
        falhou = conciliacao_bw._INC_FALHA
        if falhou.get('em') and datetime.now() - falhou['em'] < timedelta(minutes=30):
            falha = falhou.get('msg') or ''
    except Exception:
        pass
    return quando, falha


def inc_atualizar_em_segundo_plano(forcar=False):
    """Lê o Google (no máximo a cada 10 min) e reenvia o que ficou preso, sem segurar a tela. No máximo uma vez por minuto."""
    t = _INC_THREAD.get('t')
    if t is not None and t.is_alive():
        return
    if not forcar and time.time() - _INC_THREAD['ultimo_inicio'] < 60:
        return
    _INC_THREAD['ultimo_inicio'] = time.time()

    def _rodar():
        try:
            conexao_bg = conectar_banco()
            try:
                inc_garantir_tabelas(conexao_bg)
                conciliacao_bw.sincronizar_inc(conexao_bg, forcar=forcar)          # nunca levanta erro
                esquecer_cache_inconsistencia()           # a leitura pode ter trazido casos novos/mudados
                if forcar or time.time() - _INC_THREAD['ultimo_reenvio'] >= 120:   # ação presa: tenta de novo a cada 2 min
                    _INC_THREAD['ultimo_reenvio'] = time.time()
                    inc_reenviar_pendentes(conexao_bg)
            finally:
                conexao_bg.close()
        except Exception as erro:
            print(f'⚠️ Atualização em segundo plano do Inc. Bradesco: {erro}', flush=True)

    t = threading.Thread(target=_rodar, name='inc-bradesco', daemon=True)
    _INC_THREAD['t'] = t
    t.start()


def inc_precisa_atualizar(conexao):
    """True se a última leitura do Google tem mais de 10 minutos (ou nunca houve) ou há ação presa esperando o Google."""
    try:
        if not inc_config(conexao)[0]:
            return False
        r = conexao.execute("SELECT carregado_em FROM conc_inc_meta WHERE origem = 'google' ORDER BY id DESC LIMIT 1").fetchone()
        if not r or not r['carregado_em']:
            return True
        if datetime.now() - datetime.strptime(r['carregado_em'], '%Y-%m-%d %H:%M:%S') > timedelta(minutes=10):
            return True
        return bool(conexao.execute("SELECT 1 FROM inc_acoes WHERE enviado = 0 LIMIT 1").fetchone())
    except Exception:
        return False


# ------------------------------------------------------------------ escrita na planilha
def _inc_col_letra(i):
    s, i = '', i + 1
    while i:
        i, resto = divmod(i - 1, 26)
        s = chr(65 + resto) + s
    return s


def inc_localizar_linha(valores, linha, referencia, doc_pagfor, venc_iso):
    """Acha a linha do caso na aba (lista de listas do Google). Primeiro tenta o nº de linha que a leitura guardou; se a equipe
    inseriu/ordenou linhas, procura pela Referência + Doc. Pagfor (+ vencimento). Devolve (nº da linha, {coluna: índice}, valores
    da linha) ou levanta ValueError."""
    cab, cab_n = None, 0
    for n, r in enumerate(valores, 1):
        nomes = [_inc_norm(c) for c in r]
        if 'REFERENCIA' in nomes and 'VENCIMENTO' in nomes:
            cab = {}
            for i, nome in enumerate(nomes):
                cab.setdefault(nome, i)
            cab_n = n
            break
    if cab is None:
        raise ValueError('não achei o cabeçalho (Referência e Vencimento) na aba da planilha')
    if 'SITUACAO' not in cab or 'OBSERVACAO' not in cab:
        raise ValueError('não achei as colunas Situação e Observação na aba da planilha')
    ref_alvo, doc_alvo = _inc_ref(referencia), _inc_digitos(doc_pagfor)

    def valor(r, nome):
        i = cab.get(nome)
        return r[i] if i is not None and i < len(r) else ''

    def bate(r):
        if _inc_ref(valor(r, 'REFERENCIA')) != ref_alvo:
            return False
        if doc_alvo and _inc_digitos(valor(r, 'DOC. PAGFOR')) != doc_alvo:
            return False
        v = _inc_data_texto(valor(r, 'VENCIMENTO'))
        return not (venc_iso and v and v != venc_iso)

    if cab_n < linha <= len(valores) and bate(valores[linha - 1]):
        return linha, cab, valores[linha - 1]
    candidatas = [n for n in range(cab_n + 1, len(valores) + 1) if bate(valores[n - 1])]
    if not candidatas:
        raise ValueError('a linha deste caso não está mais na planilha (Referência/Doc. Pagfor não encontrados)')
    n = min(candidatas, key=lambda x: abs(x - linha))
    return n, cab, valores[n - 1]


def _inc_titulo_real_da_aba(servico, planilha_id, aba):
    """O título guardado na leitura vem sem espaços nas pontas; o Google precisa do nome exato."""
    agora = time.time()
    if agora - _INC_CACHE_ABAS['quando'] > 3600 or aba not in _INC_CACHE_ABAS['titulos']:
        meta = servico.spreadsheets().get(spreadsheetId=planilha_id, fields='sheets.properties.title').execute()
        _INC_CACHE_ABAS['titulos'] = {s['properties']['title'].strip(): s['properties']['title'] for s in meta.get('sheets', [])}
        _INC_CACHE_ABAS['quando'] = agora
    if aba not in _INC_CACHE_ABAS['titulos']:
        raise ValueError(f'a aba "{aba}" não existe mais na planilha')
    return _INC_CACHE_ABAS['titulos'][aba]


def _inc_servico_sheets():
    """Serviço do Google Sheets com tempo limite (uma queda do Google não pode prender a aprovação/envio da tela)."""
    try:
        import httplib2
        from google_auth_httplib2 import AuthorizedHttp
        creds = service_account.Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
        return build('sheets', 'v4', http=AuthorizedHttp(creds, http=httplib2.Http(timeout=25)), cache_discovery=False)
    except Exception:
        return obter_servico_sheets()          # sem as bibliotecas de tempo limite (ou credencial ausente: o erro volta, claro, daqui)


def inc_escrever_planilha(aba, linha, referencia, doc_pagfor, venc_iso, situacao, obs, antes=None, forcar=False):
    """Grava Situação (se não for None) e Observação na linha do caso, no Google Sheets.
    Devolve {'ok': True, 'linha': n, 'anterior': {'situacao', 'obs'}} ou, se a linha mudou desde que a tela abriu e `forcar`
    não foi pedido, {'ok': False, 'conflito': True, 'atual': {...}}. Erros do Google/da planilha levantam exceção."""
    servico = _inc_servico_sheets()
    planilha_id = conciliacao_bw.PLANILHA_INC_ID
    titulo = _inc_titulo_real_da_aba(servico, planilha_id, aba)
    faixa = "'" + titulo.replace("'", "''") + "'"
    valores = servico.spreadsheets().values().get(
        spreadsheetId=planilha_id, range=faixa, valueRenderOption='UNFORMATTED_VALUE',
        dateTimeRenderOption='SERIAL_NUMBER').execute().get('values', [])
    numero, cab, atual = inc_localizar_linha(valores, int(linha or 0), referencia, doc_pagfor, venc_iso)

    def celula(nome):
        i = cab[nome]
        return str(atual[i]).strip() if i < len(atual) and atual[i] is not None else ''
    anterior = {'situacao': celula('SITUACAO'), 'obs': celula('OBSERVACAO')}
    if antes is not None and not forcar:
        if _inc_norm(anterior['situacao']) != _inc_norm(antes.get('situacao')) or \
                _inc_norm(anterior['obs'])[:300] != _inc_norm(antes.get('obs'))[:300]:
            return {'ok': False, 'conflito': True, 'atual': anterior}
    dados = []
    if situacao is not None:
        dados.append({'range': f"{faixa}!{_inc_col_letra(cab['SITUACAO'])}{numero}", 'values': [[situacao]]})
    dados.append({'range': f"{faixa}!{_inc_col_letra(cab['OBSERVACAO'])}{numero}", 'values': [[obs]]})
    servico.spreadsheets().values().batchUpdate(
        spreadsheetId=planilha_id, body={'valueInputOption': 'RAW', 'data': dados}).execute()
    return {'ok': True, 'linha': numero, 'anterior': anterior}


def _inc_msg_erro_escrita(erro):
    status = getattr(getattr(erro, 'resp', None), 'status', None)
    if isinstance(erro, FileNotFoundError):
        return 'não achei o arquivo de credenciais do Google (credenciais.json) no servidor'
    if status == 403:
        return ('a conta de serviço do sistema (robo-planilha@n8n-automacao-leads-465612.iam.gserviceaccount.com) '
                'precisa ser EDITORA da planilha Inc. Bradesco')
    if status == 404:
        return 'o Google não achou a planilha Inc. Bradesco (confira o compartilhamento)'
    if isinstance(erro, ValueError):
        return str(erro)
    return f'{erro.__class__.__name__}: {str(erro)[:160]}'


def inc_aplicar(conexao, caso, usuario, situacao, obs, origem='SISTEMA', pendencia_id=None, forcar=False):
    """Lança a atuação: grava na planilha (e conferindo o que estava lá), guarda no histórico e atualiza a cópia local.
    caso = {aba, linha, referencia, doc_pagfor, venc (aaaa-mm-dd), situacao_antes, obs_antes}. situacao None = só a Observação.
    Devolve {'sucesso', 'no_google', 'msg', 'conflito'?, 'atual'?}. Se o Google falhar, a ação fica guardada e o sistema tenta de novo."""
    inc_garantir_tabelas(conexao)
    agora = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    antes = None
    if caso.get('situacao_antes') is not None or caso.get('obs_antes') is not None:
        antes = {'situacao': caso.get('situacao_antes'), 'obs': caso.get('obs_antes')}
    no_google, erro_txt, anterior = False, '', {'situacao': caso.get('situacao_antes'), 'obs': caso.get('obs_antes')}
    try:
        res = inc_escrever_planilha(caso['aba'], caso['linha'], caso['referencia'], caso['doc_pagfor'], caso.get('venc') or '',
                                    situacao, obs, antes=antes, forcar=forcar)
        if res.get('conflito'):
            return {'sucesso': False, 'conflito': True, 'atual': res['atual'], 'no_google': False,
                    'msg': 'A linha foi alterada na planilha depois que você abriu a tela.'}
        no_google, anterior = True, res.get('anterior') or anterior
    except Exception as erro:
        erro_txt = _inc_msg_erro_escrita(erro)
        print(f"⚠️ Inc. Bradesco: não gravei na planilha ({caso.get('referencia')}): {erro_txt}", flush=True)
        if isinstance(erro, ValueError):
            # a linha/aba/colunas não existem mais na planilha: repetir não adianta. Fica só no histórico (enviado = 2).
            conexao.execute(
                "INSERT INTO inc_acoes (aba, linha, referencia, doc_pagfor, venc, chave, usuario, data_hora, situacao_antes, obs_antes, "
                "situacao_nova, obs_nova, origem, pendencia_internet_id, enviado, enviado_em, erro) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,2,NULL,?)",
                (caso['aba'], caso['linha'], caso['referencia'], caso['doc_pagfor'], caso.get('venc') or '', caso.get('chave') or '',
                 usuario, agora, anterior.get('situacao'), anterior.get('obs'), situacao, obs, origem, pendencia_id, erro_txt))
            conexao.commit()
            esquecer_cache_inconsistencia()
            return {'sucesso': False, 'no_google': False,
                    'msg': f'Não consegui gravar na planilha: {erro_txt}. Clique em "Atualizar da planilha" e confira o caso.'}
    conexao.execute(
        "INSERT INTO inc_acoes (aba, linha, referencia, doc_pagfor, venc, chave, usuario, data_hora, situacao_antes, obs_antes, "
        "situacao_nova, obs_nova, origem, pendencia_internet_id, enviado, enviado_em, erro) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (caso['aba'], caso['linha'], caso['referencia'], caso['doc_pagfor'], caso.get('venc') or '', caso.get('chave') or '',
         usuario, agora, anterior.get('situacao'), anterior.get('obs'), situacao, obs, origem, pendencia_id,
         1 if no_google else 0, agora if no_google else None, erro_txt))
    if no_google:
        _inc_atualizar_copia_local(conexao, caso, situacao, obs)
    try:
        base = None
        if caso.get('chave') and caso.get('venc'):
            base = conexao.execute("SELECT id FROM imoveis WHERE LTRIM(imovel, '0') = ? AND substr(vencimento, 1, 10) = ? "
                                   "ORDER BY ativo DESC, id DESC LIMIT 1", (_inc_chave(caso['chave']), _inc_iso_para_br(caso['venc']))).fetchone()
        if base:
            texto = f"⚠️ Inconsistência Bradesco ({caso.get('ocorrencia') or 'devolução'}): " + \
                    (f"Situação {situacao}, " if situacao else '') + f"Observação \"{obs}\"" + \
                    ('' if origem == 'SISTEMA' else f" [{origem.replace('_', ' ').title()}]")
            conexao.execute("INSERT INTO historico_observacoes (imovel_id, data_hora, usuario, texto) VALUES (?, ?, ?, ?)",
                            (base['id'], agora, usuario, texto))
    except Exception as erro:
        print(f'⚠️ Inc. Bradesco: não consegui anotar no prontuário: {erro}', flush=True)
    conexao.commit()
    esquecer_cache_inconsistencia()
    if no_google:
        return {'sucesso': True, 'no_google': True, 'msg': 'Planilha Inc. Bradesco atualizada.'}
    return {'sucesso': True, 'no_google': False,
            'msg': (f'Salvo no sistema, mas não consegui gravar na planilha: {erro_txt}. '
                    'O sistema tenta de novo sozinho (a cada 10 min) e a tela já mostra o que você lançou.')}


def _inc_atualizar_copia_local(conexao, caso, situacao, obs):
    sql, par = "UPDATE conc_inc SET obs = ?", [obs]
    if situacao is not None:
        sql += ", situacao = ?"
        par.append(situacao)
    base = " WHERE aba = ? AND referencia = ? AND doc_pagfor = ? AND venc = ?"
    chave = [caso['aba'], caso['referencia'], caso['doc_pagfor'], caso.get('venc') or '']
    if caso.get('linha'):
        if conexao.execute(sql + base + " AND linha = ?", par + chave + [caso['linha']]).rowcount:
            return
    conexao.execute(sql + base, par + chave)


def inc_reenviar_pendentes(conexao):
    """Tenta de novo as ações que ficaram presas (o Google estava fora ou sem permissão). Se a linha foi mexida na planilha
    nesse meio tempo, abandona a ação (vale o que está lá) em vez de sobrescrever."""
    enviadas = 0
    for a in conexao.execute("SELECT * FROM inc_acoes WHERE enviado = 0 ORDER BY id LIMIT 30").fetchall():
        antes = {'situacao': a['situacao_antes'], 'obs': a['obs_antes']}
        try:
            res = inc_escrever_planilha(a['aba'], a['linha'], a['referencia'], a['doc_pagfor'], a['venc'],
                                        a['situacao_nova'], a['obs_nova'], antes=antes if a['situacao_antes'] is not None else None)
        except Exception as erro:
            # linha/aba sumiu da planilha (ValueError): repetir não adianta; outros erros (Google fora, sem permissão): tenta de novo depois
            conexao.execute("UPDATE inc_acoes SET erro = ?, enviado = ? WHERE id = ?",
                            (_inc_msg_erro_escrita(erro), 2 if isinstance(erro, ValueError) else 0, a['id']))
            continue
        if res.get('conflito'):
            conexao.execute("UPDATE inc_acoes SET enviado = 2, erro = ? WHERE id = ?",
                            ('a planilha foi alterada por outra pessoa; vale o que está lá', a['id']))
        else:
            conexao.execute("UPDATE inc_acoes SET enviado = 1, enviado_em = ?, erro = '' WHERE id = ?",
                            (datetime.now().strftime('%Y-%m-%d %H:%M:%S'), a['id']))
            _inc_atualizar_copia_local(conexao, {'aba': a['aba'], 'referencia': a['referencia'], 'doc_pagfor': a['doc_pagfor'],
                                                 'venc': a['venc']}, a['situacao_nova'], a['obs_nova'])
            enviadas += 1
    conexao.commit()
    return enviadas


def _inc_montar_obs(padrao, data_br, complemento):
    """Texto final da Observação: frase padrão (com a data escolhida) + complemento. 'OUTRO' = só o complemento."""
    padrao = str(padrao or '').strip()
    complemento = re.sub(r'[\r\n\t]+', ' ', str(complemento or '')).strip()
    data_br = str(data_br or '').strip()
    if not re.match(r'^\d{2}/\d{2}$', data_br):
        data_br = datetime.now().strftime('%d/%m')
    if padrao == 'OUTRO':
        return complemento[:200]
    if padrao not in _INC_FRASES_VALIDAS:
        return ''
    texto = padrao.replace('{dd/mm}', data_br)
    return (f'{texto} - {complemento}' if complemento else texto)[:200]


def _inc_caso_do_banco(conexao, aba, linha, referencia, doc_pagfor):
    try:
        return conexao.execute(
            "SELECT aba, linha, referencia, carteira, chave, doc_pagfor, venc, ocorrencia, descricao, situacao, obs FROM conc_inc "
            "WHERE aba = ? AND referencia = ? AND doc_pagfor = ? ORDER BY ABS(linha - ?) LIMIT 1",
            (aba, referencia, doc_pagfor, int(linha or 0))).fetchone()
    except (sqlite3.OperationalError, ValueError, TypeError):
        return None


@app.route('/api/inconsistencia/atuar', methods=['POST'])
@login_obrigatorio
def api_inconsistencia_atuar():
    dados = request.get_json(silent=True) or {}
    usuario = session.get('usuario', 'Sistema').title()
    situacao = str(dados.get('situacao') or '').strip().upper()
    if situacao not in ('OK', 'PENDENTE'):
        return jsonify({'sucesso': False, 'msg': 'Escolha a Situação (OK ou PENDENTE).'})
    obs = _inc_montar_obs(dados.get('obs_padrao'), dados.get('data_obs'), dados.get('complemento'))
    if not obs:
        return jsonify({'sucesso': False, 'msg': 'Escolha a Observação (ou escolha "Outro" e escreva o texto).'})
    conexao = conectar_banco()
    try:
        inc_garantir_tabelas(conexao)
        linha = _inc_caso_do_banco(conexao, str(dados.get('aba') or ''), dados.get('linha'), str(dados.get('referencia') or ''),
                                   str(dados.get('doc_pagfor') or ''))
        if not linha:
            return jsonify({'sucesso': False, 'msg': 'Não achei este caso na leitura da planilha. Clique em "Atualizar da planilha" e tente de novo.'})
        caso = dict(linha)
        caso['situacao_antes'] = dados.get('situacao_antes') if dados.get('situacao_antes') is not None else caso['situacao']
        caso['obs_antes'] = dados.get('obs_antes') if dados.get('obs_antes') is not None else caso['obs']
        res = inc_aplicar(conexao, caso, usuario, situacao, obs, forcar=bool(dados.get('forcar')))
        return jsonify(res)
    except Exception as erro:
        print(f'⚠️ Inc. Bradesco (atuar): {erro}', flush=True)
        return jsonify({'sucesso': False, 'msg': f'Erro ao atuar no caso: {erro}'})
    finally:
        conexao.close()


@app.route('/api/inconsistencia/atualizar', methods=['POST'])
@login_obrigatorio
def api_inconsistencia_atualizar():
    """Botão "Atualizar da planilha": lê o Google agora (sem esperar os 10 minutos)."""
    conexao = conectar_banco()
    try:
        inc_garantir_tabelas(conexao)
        res = conciliacao_bw.sincronizar_inc(conexao, forcar=True, usuario=session.get('usuario', 'Sistema'))
        esquecer_cache_inconsistencia()            # botão "Atualizar da planilha": a próxima tela já recalcula
        reenviadas = inc_reenviar_pendentes(conexao) if res.get('ok') else 0
        return jsonify({'sucesso': bool(res.get('ok')), 'msg': res.get('msg') or '', 'reenviadas': reenviadas})
    finally:
        conexao.close()


# ------------------------------------------------------------------ vínculo com o módulo Internet
def inc_caso_para_internet(conexao, aba, linha, referencia, doc_pagfor):
    """Dados do caso para pré-preencher o formulário da Internet (None se não achar)."""
    r = _inc_caso_do_banco(conexao, aba, linha, referencia, doc_pagfor)
    if not r:
        return None
    base = None
    chave = _inc_chave(r['chave'])
    venc_br = _inc_iso_para_br(r['venc'])
    if chave:
        base = conexao.execute("SELECT imovel, carteira, empreendimento FROM imoveis WHERE LTRIM(imovel, '0') = ? "
                               "AND substr(vencimento, 1, 10) = ? ORDER BY ativo DESC, id DESC LIMIT 1", (chave, venc_br)).fetchone()
    carteira = str(r['carteira'] or '') or (str(base['carteira']) if base and base['carteira'] else '')
    return {'aba': r['aba'], 'linha': r['linha'], 'referencia': r['referencia'], 'doc_pagfor': r['doc_pagfor'], 'venc': r['venc'],
            'venc_br': venc_br, 'mes_ref': (r['venc'] or '')[:7], 'imovel': chave, 'carteira': carteira,
            'ocorrencia': r['ocorrencia'], 'descricao': r['descricao'],
            'obs_sugerida': f"Inconsistência Bradesco ({r['ocorrencia']} - {r['descricao']}): boleto devolvido, pagar pela Internet."}


def inc_vincular_internet(conexao, pendencia_id, form, data_pagamento_br, usuario):
    """Chamado ao salvar a solicitação da Internet que veio de uma inconsistência: guarda o vínculo e põe 'INTERNET dd/mm' na
    Observação da planilha (a Situação segue PENDENTE até o pagamento ser aprovado). Devolve um texto curto para a tela."""
    aba = str(form.get('inc_aba') or '').strip()
    if not aba:
        return ''
    inc_garantir_tabelas(conexao)
    r = _inc_caso_do_banco(conexao, aba, form.get('inc_linha'), str(form.get('inc_ref') or ''), str(form.get('inc_doc') or ''))
    if not r:
        return ' (Aviso: não achei a inconsistência na planilha para vincular.)'
    conexao.execute("UPDATE pendencias_internet SET inc_aba = ?, inc_linha = ?, inc_ref = ?, inc_doc = ?, inc_venc = ? WHERE id = ?",
                    (r['aba'], r['linha'], r['referencia'], r['doc_pagfor'], r['venc'], pendencia_id))
    conexao.commit()
    caso = dict(r)
    caso.update(situacao_antes=None, obs_antes=None)          # a Internet só escreve a Observação: sem checagem do "antes"
    res = inc_aplicar(conexao, caso, usuario, None, f"INTERNET {data_pagamento_br[:5]}", origem='INTERNET', pendencia_id=pendencia_id)
    if res.get('no_google'):
        return f" Planilha Inc. Bradesco: Observação \"INTERNET {data_pagamento_br[:5]}\" gravada."
    return ' (Aviso: não consegui gravar na planilha Inc. Bradesco agora; o sistema tenta de novo sozinho.)'


def _inc_ddmm(texto_data):
    d = str(texto_data or '')[:10]
    return f'{d[8:10]}/{d[5:7]}' if re.match(r'^\d{4}-\d{2}-\d{2}$', d) else datetime.now().strftime('%d/%m')


def inc_desfecho_internet(item, usuario, desfecho):
    """O pedido da Internet vinculado a uma inconsistência teve um desfecho: atualiza a planilha em segundo plano (uma queda do
    Google não pode prender a tela da gestão; se falhar, a ação fica na fila e o sistema tenta de novo).
      'APROVADO'  -> Situação OK,       Observação 'INTERNET dd/mm' (data do pagamento aprovado)
      'ERRO'      -> Situação PENDENTE, Observação 'ERRO PAGAMENTO INTERNET dd/mm' (volta para a fila de quem atua)
      'RECUSADO'  -> Situação intacta,  Observação 'INTERNET RECUSADO dd/mm' (para não ficar 'INTERNET dd/mm' de um pedido recusado)"""
    try:
        aba = item['inc_aba']
    except (KeyError, IndexError):
        return
    if not aba:
        return
    dd_mm = _inc_ddmm(item['data_pagamento'])
    situacao, obs = {'APROVADO': ('OK', f'INTERNET {dd_mm}'),
                     'ERRO': ('PENDENTE', f'ERRO PAGAMENTO INTERNET {dd_mm}'),
                     'RECUSADO': (None, f'INTERNET RECUSADO {datetime.now().strftime("%d/%m")}')}[desfecho]
    caso = {'aba': aba, 'linha': item['inc_linha'], 'referencia': item['inc_ref'], 'doc_pagfor': item['inc_doc'],
            'venc': item['inc_venc'], 'chave': _inc_chave(item['imovel']), 'situacao_antes': None, 'obs_antes': None}
    pendencia_id = item['id']

    def _rodar():
        try:
            conexao_bg = conectar_banco()
            try:
                inc_aplicar(conexao_bg, caso, usuario, situacao, obs, origem='INTERNET_' + desfecho, pendencia_id=pendencia_id)
            finally:
                conexao_bg.close()
        except Exception as erro:
            print(f'⚠️ Inc. Bradesco ({desfecho}): {erro}', flush=True)
    threading.Thread(target=_rodar, name='inc-internet', daemon=True).start()


@app.route('/dashboard_gerencial')
@login_obrigatorio
def dashboard_gerencial():
    if not usuario_eh_gestor(session.get('usuario')):
        return "Acesso Negado: Esta área é restrita à gestão.", 403
        
    conexao = conectar_banco()
    
    conexao.execute('''
        CREATE TABLE IF NOT EXISTS historico_fechamento_mes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mes_referencia TEXT,
            imovel TEXT,
            administradora TEXT,
            vencimento TEXT,
            status_interno TEXT,
            responsavel TEXT,
            data_arquivamento DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    meses_arquivados = [row['mes_referencia'] for row in conexao.execute("SELECT DISTINCT mes_referencia FROM historico_fechamento_mes ORDER BY id DESC").fetchall()]
    mes_ref = request.args.get('mes_ref', '').strip()
    
    # 🎯 BLINDAGEM: Variáveis iniciadas no topo para NUNCA dar erro de UnboundLocalError
    metas_db = []
    total = 0
    base_atualizada = 0
    coletados = 0
    emitidos = 0
    vencendo_hoje = 0
    stats_robos = {"WEBWARE": 0, "WINKER": 0, "FINMAIL": 0, "SCANNER": 0}
    ranking_manual = []
    lista_coletados = []
    lista_pendentes = []
    lista_vencem_hoje = []
    resumo_diario = []
    base_congelada = False
    breakdown_total = {}
    breakdown_faltam = {}
    nome_mes_real = ""
    pendentes_total = 0          # boletos da base que ainda não foram coletados
    status_pendentes = []        # composição das pendências por situação interna
    carteira_responsaveis = []   # base / coletados / pendentes por responsável
    ritmo_coletas = []           # coletas dos últimos dias úteis (manual x robôs)
    ritmo_media = 0
    coletas_hoje = 0

    if mes_ref:
        # =========================================================
        # 📦 MODO HISTÓRICO
        # =========================================================
        query_bt_hist = """
            SELECT 
                CASE 
                    WHEN UPPER(TRIM(c.portal)) = 'WINKER' THEN 'WINKER'
                    WHEN UPPER(TRIM(c.portal)) = 'WEBWARE' THEN 'WEBWARE'
                    ELSE 'DIVERSOS'
                END as portal_agrupado, 
                COUNT(h.id) as qtd 
            FROM historico_fechamento_mes h 
            LEFT JOIN cadastro_mestre_imoveis c ON h.imovel = c.imovel 
            WHERE h.mes_referencia = ? AND h.administradora != '' 
            GROUP BY portal_agrupado
        """
        breakdown_total = {row['portal_agrupado']: row['qtd'] for row in conexao.execute(query_bt_hist, (mes_ref,)).fetchall()}
        
        query_bf_hist = """
            SELECT 
                CASE 
                    WHEN UPPER(TRIM(c.portal)) = 'WINKER' THEN 'WINKER'
                    WHEN UPPER(TRIM(c.portal)) = 'WEBWARE' THEN 'WEBWARE'
                    ELSE 'DIVERSOS'
                END as portal_agrupado, 
                COUNT(h.id) as qtd 
            FROM historico_fechamento_mes h 
            LEFT JOIN cadastro_mestre_imoveis c ON h.imovel = c.imovel 
            WHERE h.mes_referencia = ? AND h.administradora != '' 
              AND (h.status_interno IS NULL OR h.status_interno NOT IN ('PAGO', 'COLETADO', 'PAGO NA 275', 'PAGO NA ADM'))
            GROUP BY portal_agrupado
        """
        breakdown_faltam = {row['portal_agrupado']: row['qtd'] for row in conexao.execute(query_bf_hist, (mes_ref,)).fetchall()}

        total = conexao.execute("SELECT COUNT(*) FROM historico_fechamento_mes WHERE mes_referencia = ? AND administradora != ''", (mes_ref,)).fetchone()[0]
        base_atualizada = conexao.execute("SELECT COUNT(*) FROM historico_fechamento_mes WHERE mes_referencia = ? AND administradora != '' AND (status_interno IS NULL OR status_interno NOT IN ('PAGO', 'COLETADO'))", (mes_ref,)).fetchone()[0]
        coletados = conexao.execute("SELECT COUNT(*) FROM historico_fechamento_mes WHERE mes_referencia = ? AND administradora != '' AND status_interno IN ('PAGO', 'COLETADO')", (mes_ref,)).fetchone()[0]
        emitidos = conexao.execute("SELECT COUNT(*) FROM historico_fechamento_mes WHERE mes_referencia = ? AND administradora != '' AND status_interno IS NOT NULL AND status_interno NOT IN ('NÃO EMITIDO', 'PENDENTE', 'FORA DA CARTEIRA', '')", (mes_ref,)).fetchone()[0]
        
        query_total_dia = "SELECT SUBSTR(TRIM(vencimento), 1, 2) as dia, COUNT(*) as qtd FROM historico_fechamento_mes WHERE mes_referencia = ? AND administradora != '' GROUP BY dia"
        total_dias_dict = {row['dia']: int(row['qtd']) for row in conexao.execute(query_total_dia, (mes_ref,)).fetchall() if row['dia'] and row['dia'].isdigit()}
        
        query_pendentes_dia = "SELECT SUBSTR(TRIM(vencimento), 1, 2) as dia, COUNT(*) as qtd FROM historico_fechamento_mes WHERE mes_referencia = ? AND administradora != '' AND (status_interno IS NULL OR status_interno NOT IN ('PAGO', 'COLETADO')) GROUP BY dia"
        pendentes_dias_dict = {row['dia']: int(row['qtd']) for row in conexao.execute(query_pendentes_dia, (mes_ref,)).fetchall() if row['dia'] and row['dia'].isdigit()}
        
        todos_os_dias = set(list(total_dias_dict.keys()) + list(pendentes_dias_dict.keys()))
        for dia in sorted(list(todos_os_dias), key=lambda x: int(x)):
            tot_dia = total_dias_dict.get(dia, 0)
            pend_dia = pendentes_dias_dict.get(dia, 0)
            dif_dia = tot_dia - pend_dia
            pct = round((dif_dia / tot_dia * 100), 2) if tot_dia > 0 else 0
            if tot_dia > 0:
                resumo_diario.append({"dia": f"Dia {dia}", "total": tot_dia, "atual": pend_dia, "diferenca": dif_dia, "pendentes": pend_dia, "pct": format(pct, ".2f").replace('.', ',')})
        
        query_coletados = "SELECT p.usuario as nome, COUNT(DISTINCT h.imovel) as qtd FROM produtividade p JOIN imoveis i_hist ON p.imovel_id = i_hist.id JOIN historico_fechamento_mes h ON i_hist.imovel = h.imovel WHERE p.acao = 'COLETOU BOLETO' AND h.mes_referencia = ? AND h.status_interno IN ('PAGO', 'COLETADO') GROUP BY p.usuario ORDER BY qtd DESC"
        lista_coletados_raw = conexao.execute(query_coletados, (mes_ref,)).fetchall()
        
        ranking_manual_dict = {}
        for row in lista_coletados_raw:
            u = str(row['nome']).upper()
            q = row['qtd']
            lista_coletados.append({"nome": row['nome'], "qtd": q})
            if "WEBWARE" in u: stats_robos["WEBWARE"] += q
            elif "WINKER" in u: stats_robos["WINKER"] += q
            elif "FINMAIL" in u or "GERAL" in u: stats_robos["FINMAIL"] += q
            elif "SCANNER" in u: stats_robos["SCANNER"] += q
            else: ranking_manual_dict[row['nome']] = ranking_manual_dict.get(row['nome'], 0) + q

        ranking_manual = [{"nome": k, "qtd": v} for k, v in ranking_manual_dict.items()]
        ranking_manual.sort(key=lambda x: x['qtd'], reverse=True)
        lista_pendentes = [{"nome": row['responsavel'] if row['responsavel'] else 'Carteira Não Atribuída', "qtd": row['qtd']} for row in conexao.execute("SELECT responsavel, COUNT(*) as qtd FROM historico_fechamento_mes WHERE mes_referencia = ? AND administradora != '' AND (status_interno IS NULL OR status_interno NOT IN ('PAGO', 'COLETADO')) GROUP BY responsavel ORDER BY qtd DESC", (mes_ref,)).fetchall()]
        base_congelada = True
        pendentes_total = base_atualizada
        status_pendentes = _montar_status_pendentes(conexao.execute(
            "SELECT UPPER(TRIM(IFNULL(status_interno, ''))) as status, COUNT(*) as qtd FROM historico_fechamento_mes "
            "WHERE mes_referencia = ? AND administradora != '' AND (status_interno IS NULL OR status_interno NOT IN ('PAGO', 'COLETADO')) "
            "GROUP BY 1 ORDER BY qtd DESC", (mes_ref,)).fetchall())
        carteira_responsaveis = _montar_carteira(conexao.execute(
            "SELECT IFNULL(TRIM(responsavel), '') as nome, COUNT(*) as base, "
            "SUM(CASE WHEN status_interno IN ('PAGO', 'COLETADO') THEN 1 ELSE 0 END) as coletados "
            "FROM historico_fechamento_mes WHERE mes_referencia = ? AND administradora != '' GROUP BY 1", (mes_ref,)).fetchall())
    else:
        # =========================================================
        # 🟢 MODO ATUAL (TEMPO REAL)
        # =========================================================
        conexao.execute('CREATE TABLE IF NOT EXISTS config_sistema (chave TEXT PRIMARY KEY, valor TEXT)')
        metas_db = conexao.execute("SELECT chave, valor FROM config_sistema WHERE chave LIKE 'meta_dia_%'").fetchall()
        metas_dias = {row['chave'].replace('meta_dia_', ''): int(row['valor']) for row in metas_db}

        query_total_atual = "SELECT SUBSTR(TRIM(vencimento), 1, 2) as dia, COUNT(*) as qtd FROM imoveis WHERE ativo=1 AND administradora != '' GROUP BY dia"
        total_atual_dias = {row['dia']: int(row['qtd']) for row in conexao.execute(query_total_atual).fetchall() if row['dia'] and row['dia'].isdigit()}

        query_pendentes_atual = "SELECT SUBSTR(TRIM(vencimento), 1, 2) as dia, COUNT(*) as qtd FROM imoveis WHERE ativo=1 AND administradora != '' AND (status_interno IS NULL OR status_interno NOT IN ('PAGO', 'COLETADO', 'PAGO NA 275', 'PAGO NA ADM')) GROUP BY dia"
        pendentes_atual_dias = {row['dia']: int(row['qtd']) for row in conexao.execute(query_pendentes_atual).fetchall() if row['dia'] and row['dia'].isdigit()}

        todos_os_dias = set(list(metas_dias.keys()) + list(total_atual_dias.keys()))
        total_global_fixo = total_global_atual = total_global_pendente = 0

        for dia in sorted(list(todos_os_dias), key=lambda x: int(x)):
            meta_dia = metas_dias.get(dia, total_atual_dias.get(dia, 0)) if len(metas_dias) > 0 else total_atual_dias.get(dia, 0)
            # 🎯 BASE ATUAL = tudo que ainda existe na base ativa hoje (era a meta congelada e vai diminuindo
            # conforme os boletos saem da planilha). Os "pendentes" (ainda sem coleta) ficam em coluna própria.
            base_dia = total_atual_dias.get(dia, 0)
            pend_dia = pendentes_atual_dias.get(dia, 0)
            diferenca_dia = meta_dia - base_dia  # quanto já saiu da base desde o congelamento

            total_global_fixo += meta_dia
            total_global_atual += base_dia
            total_global_pendente += pend_dia

            pct = round((diferenca_dia / meta_dia * 100), 2) if meta_dia > 0 else 0
            if pct < 0: pct = 0

            if meta_dia > 0 or base_dia > 0:
                resumo_diario.append({"dia": f"Dia {dia}", "total": meta_dia, "atual": base_dia, "diferenca": diferenca_dia, "pendentes": pend_dia, "pct": format(pct, ".2f").replace('.', ',')})

        total = total_global_fixo
        base_atualizada = total_global_atual
        pendentes_total = total_global_pendente
        coletados = conexao.execute("SELECT COUNT(*) FROM imoveis WHERE ativo = 1 AND status_interno IN ('PAGO', 'COLETADO', 'PAGO NA 275', 'PAGO NA ADM')").fetchone()[0]
        emitidos = conexao.execute("SELECT COUNT(*) FROM imoveis WHERE ativo = 1 AND status_interno IS NOT NULL AND status_interno NOT IN ('NÃO EMITIDO', 'PENDENTE', 'FORA DA CARTEIRA', '')").fetchone()[0]

        query_bt = """
            SELECT 
                CASE 
                    WHEN UPPER(TRIM(c.portal)) = 'WINKER' THEN 'WINKER'
                    WHEN UPPER(TRIM(c.portal)) = 'WEBWARE' THEN 'WEBWARE'
                    ELSE 'DIVERSOS'
                END as portal_agrupado, 
                COUNT(i.id) as qtd 
            FROM imoveis i 
            LEFT JOIN cadastro_mestre_imoveis c ON i.imovel = c.imovel 
            WHERE i.ativo = 1 AND i.administradora != '' 
            GROUP BY portal_agrupado
        """
        breakdown_total = {row['portal_agrupado']: row['qtd'] for row in conexao.execute(query_bt).fetchall()}

        query_bf = """
            SELECT 
                CASE 
                    WHEN UPPER(TRIM(c.portal)) = 'WINKER' THEN 'WINKER'
                    WHEN UPPER(TRIM(c.portal)) = 'WEBWARE' THEN 'WEBWARE'
                    ELSE 'DIVERSOS'
                END as portal_agrupado, 
                COUNT(i.id) as qtd 
            FROM imoveis i 
            LEFT JOIN cadastro_mestre_imoveis c ON i.imovel = c.imovel 
            WHERE i.ativo = 1 AND i.administradora != '' 
              AND (i.status_interno IS NULL OR i.status_interno NOT IN ('PAGO', 'COLETADO', 'PAGO NA 275', 'PAGO NA ADM'))
            GROUP BY portal_agrupado
        """
        breakdown_faltam = {row['portal_agrupado']: row['qtd'] for row in conexao.execute(query_bf).fetchall()}

        hoje_dia_calendario = datetime.now().strftime('%d')
        hoje_mes_calendario = datetime.now().strftime('%m')
        hoje_str_busca = f"{hoje_dia_calendario}/{hoje_mes_calendario}"

        exemplo_vencimento = conexao.execute("SELECT vencimento FROM imoveis WHERE ativo = 1 AND vencimento != '' AND vencimento != '-' LIMIT 1").fetchone()
        
        mes_para_nome = hoje_mes_calendario
        ano_para_nome = datetime.now().strftime('%Y')
        if exemplo_vencimento and '/' in str(exemplo_vencimento['vencimento']):
            partes = exemplo_vencimento['vencimento'].split('/')
            if len(partes) >= 3:
                mes_para_nome = partes[1].zfill(2)
                ano_para_nome = partes[2]
                if len(ano_para_nome) == 2: ano_para_nome = "20" + ano_para_nome

        meses_nomes = {'01': 'Janeiro', '02': 'Fevereiro', '03': 'Março', '04': 'Abril', '05': 'Maio', '06': 'Junho', '07': 'Julho', '08': 'Agosto', '09': 'Setembro', '10': 'Outubro', '11': 'Novembro', '12': 'Dezembro'}
        nome_mes_real = f"{meses_nomes.get(mes_para_nome, mes_para_nome)}/{ano_para_nome}"

        query_vencem_hoje = "SELECT i.imovel, i.administradora, IFNULL(i.status_interno, 'PENDENTE') as status_interno, IFNULL(v.responsavel, 'Não Atribuído') as responsavel FROM imoveis i LEFT JOIN vinculos_adm v ON v.rowid = vinculo_de(i.administradora) WHERE i.ativo = 1 AND (i.status_interno IS NULL OR i.status_interno NOT IN ('PAGO', 'COLETADO')) AND i.vencimento LIKE ? ORDER BY i.imovel ASC"
        lista_vencem_hoje = [dict(row) for row in conexao.execute(query_vencem_hoje, (f"{hoje_str_busca}%",)).fetchall()]
        vencendo_hoje = len(lista_vencem_hoje)

        query_donos = """
            SELECT IFNULL(TRIM(v.responsavel), 'Não Atribuído') as nome, COUNT(i.id) as qtd
            FROM imoveis i
            LEFT JOIN vinculos_adm v ON v.rowid = vinculo_de(i.administradora)
            WHERE i.ativo = 1 AND i.status_interno IN ('PAGO', 'COLETADO', 'PAGO NA 275', 'PAGO NA ADM')
            GROUP BY nome ORDER BY qtd DESC
        """
        ranking_manual_dict = {}
        for row in conexao.execute(query_donos).fetchall():
            nome_dono = str(row['nome']).title()
            if nome_dono == 'Jéssica': nome_dono = 'Jessica'
            lista_coletados.append({"nome": nome_dono, "qtd": row['qtd']})
            ranking_manual.append({"nome": nome_dono, "qtd": row['qtd']})

        query_executores = """
            SELECT 
                IFNULL(
                    (SELECT p.usuario FROM produtividade p WHERE p.imovel_id = i.id AND p.acao IN ('COLETOU BOLETO', 'PAGOU BOLETO', 'PAGOU BOLETO NA 275', 'ALTEROU STATUS PARA PAGO NA ADM') ORDER BY p.id DESC LIMIT 1),
                    'Sistema / Planilha'
                ) as executor, COUNT(i.id) as qtd
            FROM imoveis i
            WHERE i.ativo = 1 AND i.status_interno IN ('PAGO', 'COLETADO', 'PAGO NA 275', 'PAGO NA ADM')
            GROUP BY executor
        """
        for row in conexao.execute(query_executores).fetchall():
            u = str(row['executor']).upper()
            q = row['qtd']
            if "WEBWARE" in u: stats_robos["WEBWARE"] += q
            elif "WINKER" in u: stats_robos["WINKER"] += q
            elif "FINMAIL" in u or "GERAL" in u: stats_robos["FINMAIL"] += q
            elif "SCANNER" in u: stats_robos["SCANNER"] += q

        lista_pendentes = [{"nome": row['nome'] if row['nome'] else 'Carteira Não Atribuída', "qtd": row['qtd']} for row in conexao.execute("SELECT IFNULL(v.responsavel, 'Não Atribuído') as nome, COUNT(*) as qtd FROM imoveis i LEFT JOIN vinculos_adm v ON v.rowid = vinculo_de(i.administradora) WHERE i.ativo = 1 AND i.administradora IS NOT NULL AND i.administradora != '' AND (i.status_interno IS NULL OR i.status_interno NOT IN ('PAGO', 'COLETADO', 'PAGO NA 275', 'PAGO NA ADM')) GROUP BY v.responsavel ORDER BY qtd DESC").fetchall()]
        base_congelada = len(metas_db) > 0

        # ---- Composição das pendências por situação interna
        status_pendentes = _montar_status_pendentes(conexao.execute(
            f"SELECT UPPER(TRIM(IFNULL(status_interno, ''))) as status, COUNT(*) as qtd FROM imoveis "
            f"WHERE ativo = 1 AND administradora != '' AND (status_interno IS NULL OR status_interno NOT IN {SQL_STATUS_COLETADO}) "
            f"GROUP BY 1 ORDER BY qtd DESC").fetchall())

        # ---- Carteira por responsável (base, coletados e pendentes)
        carteira_responsaveis = _montar_carteira(conexao.execute(
            f"SELECT IFNULL(TRIM(v.responsavel), '') as nome, COUNT(i.id) as base, "
            f"SUM(CASE WHEN i.status_interno IN {SQL_STATUS_COLETADO} THEN 1 ELSE 0 END) as coletados "
            f"FROM imoveis i LEFT JOIN vinculos_adm v ON v.rowid = vinculo_de(i.administradora) "
            f"WHERE i.ativo = 1 AND i.administradora != '' GROUP BY 1").fetchall())

        # ---- Ritmo de coletas dos últimos dias úteis (manual x robôs)
        try:
            hoje_data = datetime.now().date()
            inicio_janela = (hoje_data - timedelta(days=20)).strftime('%Y-%m-%d')
            por_dia = {}
            for row in conexao.execute(
                "SELECT SUBSTR(data_hora, 1, 10) as d, usuario, COUNT(DISTINCT imovel_id) as q FROM produtividade "
                "WHERE acao = 'COLETOU BOLETO' AND data_hora >= ? GROUP BY 1, 2", (inicio_janela,)).fetchall():
                u = str(row['usuario'] or '').upper()
                eh_robo = any(x in u for x in ['AUTOM', 'ROB', 'WEBWARE', 'WINKER', 'FINMAIL', 'SISTEMA', 'SCANNER', 'GERAL'])
                reg = por_dia.setdefault(row['d'], {'manual': 0, 'robo': 0})
                reg['robo' if eh_robo else 'manual'] += int(row['q'] or 0)

            serie = []
            for i in range(20, -1, -1):
                dia_d = hoje_data - timedelta(days=i)
                reg = por_dia.get(dia_d.strftime('%Y-%m-%d'), {'manual': 0, 'robo': 0})
                total_d = reg['manual'] + reg['robo']
                if dia_d.weekday() >= 5 and total_d == 0:
                    continue  # fim de semana sem coleta não entra no gráfico
                serie.append({'data': dia_d.strftime('%Y-%m-%d'), 'rotulo': dia_d.strftime('%d/%m'),
                              'manual': reg['manual'], 'robo': reg['robo'], 'total': total_d, 'hoje': i == 0})
            ritmo_coletas = serie[-14:]
            coletas_hoje = next((s['total'] for s in ritmo_coletas if s['hoje']), 0)
            fechados = [s['total'] for s in ritmo_coletas if not s['hoje']]
            ritmo_media = round(sum(fechados) / len(fechados)) if fechados else 0
        except Exception:
            ritmo_coletas, ritmo_media, coletas_hoje = [], 0, 0

    diferenca_total = total - base_atualizada
    percentual_baixado_total = round(((total - base_atualizada) / total * 100), 2) if total > 0 else 0
    if percentual_baixado_total < 0: percentual_baixado_total = 0

    # =========================================================================
    # 🗓️ MATRIZ DE PRODUTIVIDADE COM INTELIGÊNCIA DE JORNADA (HOME / FOLGA)
    # =========================================================================
    mes_ref_2 = request.args.get('mes_ref_2', '').strip()
    import datetime as dt # Para ler o calendário real (Segunda, Terça, etc)

    def gerar_matriz_operadores(referencia):
        meses_nomes = {'01': 'JANEIRO', '02': 'FEVEREIRO', '03': 'MARÇO', '04': 'ABRIL', '05': 'MAIO', '06': 'JUNHO', '07': 'JULHO', '08': 'AGOSTO', '09': 'SETEMBRO', '10': 'OUTUBRO', '11': 'NOVEMBRO', '12': 'DEZEMBRO'}
        
        if referencia and referencia != 'atual':
            mes_nome = referencia.split('/')[0].lower()
            ano_alvo = referencia.split('/')[1]
            meses_map = {'janeiro': '01', 'fevereiro': '02', 'março': '03', 'abril': '04', 'maio': '05', 'junho': '06', 'julho': '07', 'agosto': '08', 'setembro': '09', 'outubro': '10', 'novembro': '11', 'dezembro': '12'}
            mes_alvo = meses_map.get(mes_nome, datetime.now().strftime('%m'))
            titulo_mes = meses_nomes.get(mes_alvo, 'MÊS').upper()
        else:
            mes_alvo = datetime.now().strftime('%m')
            ano_alvo = datetime.now().strftime('%Y')
            titulo_mes = meses_nomes.get(mes_alvo, 'MÊS ATUAL').upper()

        # 🎯 1. Puxa as exceções (Férias e Feriados) gravadas no banco
        conexao.execute('CREATE TABLE IF NOT EXISTS excecoes_jornada (id INTEGER PRIMARY KEY AUTOINCREMENT, data_excecao TEXT, usuario TEXT, tipo TEXT)')
        excecoes_raw = conexao.execute("SELECT data_excecao, usuario, tipo FROM excecoes_jornada WHERE data_excecao LIKE ?", (f"{ano_alvo}-{mes_alvo}-%",)).fetchall()
        
        mapa_excecoes = {}
        for ex in excecoes_raw:
            d = str(int(ex['data_excecao'].split('-')[2])) # Pega o dia sem zero
            u = ex['usuario'].title() if ex['usuario'] != 'TODOS' else 'TODOS'
            if d not in mapa_excecoes: mapa_excecoes[d] = {}
            mapa_excecoes[d][u] = ex['tipo']

        like_mes_calendario = f"{ano_alvo}-{mes_alvo}-%"
        
        query_calendario = """
            SELECT usuario, SUBSTR(data_hora, 9, 2) as dia, COUNT(DISTINCT imovel_id) as qtd
            FROM produtividade WHERE acao = 'COLETOU BOLETO' AND data_hora LIKE ? GROUP BY usuario, dia
        """
        dados_calendario = conexao.execute(query_calendario, (like_mes_calendario,)).fetchall()

        time_op = list(TIME_OPERACIONAL_BASE) + ['Robôs']
        dados_agrup = {}
        usu_extras = set()

        # 🏠 Home office configurado pela gestão no dashboard (tabela home_office_colaboradores)
        home_office = _mapa_home_office(conexao)
        for nome_home in home_office:
            if home_office[nome_home] and nome_home not in time_op:
                usu_extras.add(nome_home)  # colaborador novo ganha coluna na matriz
        
        for row in dados_calendario:
            nome_raw = str(row['usuario']).title()
            if nome_raw == 'Jéssica': nome_raw = 'Jessica'
            elif 'João' in nome_raw or 'Joao' in nome_raw: nome_raw = 'Joao'
            elif 'Geovanna' in nome_raw or 'Giovanna' in nome_raw: nome_raw = 'Geovanna'
            elif 'Heloisa' in nome_raw: nome_raw = 'Heloisa'
            
            if any(x in nome_raw.upper() for x in ['AUTOM', 'ROB', 'WEBWARE', 'WINKER', 'FINMAIL', 'SISTEMA']):
                nome_raw = 'Robôs'
                
            try: dia = str(int(row['dia']))
            except: continue
                
            qtd = row['qtd']
            if dia not in dados_agrup: dados_agrup[dia] = {}
            dados_agrup[dia][nome_raw] = dados_agrup[dia].get(nome_raw, 0) + qtd
            if nome_raw not in time_op: usu_extras.add(nome_raw)

        time_op.extend(sorted(list(usu_extras)))
        matriz = []
        totais_usu = {nome: 0 for nome in time_op}
        tot_geral = 0

       # 🎯 2. LAÇO INTELIGENTE: Passa do dia 1 ao 31 e valida o calendário e o futuro
        hoje_real = dt.date.today()
        
        for dia in range(1, 32):
            try:
                # Se o dia não existe no mês (ex: 31 de Abril), o Python pula!
                data_obj = dt.date(int(ano_alvo), int(mes_alvo), dia)
                dia_semana = data_obj.weekday() # 0=Segunda, 1=Terça... 6=Domingo
            except ValueError:
                continue 
            
            # 🎯 MÁGICA AQUI: O Python descobre se o dia de hoje já chegou ou não
            is_futuro = data_obj > hoje_real
                
            dia_str = str(dia)
            linha_dia = {'dia': dia_str, 'colaboradores': [], 'total_dia': 0, 'is_futuro': is_futuro, 'is_hoje': data_obj == hoje_real}
            
            for nome in time_op:
                qtd = dados_agrup.get(dia_str, {}).get(nome, 0)
                status_dia = 'NORMAL'
                
                # HOME OFFICE (dias definidos no dashboard)
                if dia_semana in home_office.get(nome, ()): status_dia = 'HOME'

                if dia_semana in [5, 6]: status_dia = 'FDS' # Fim de semana
                
                # REGRAS EXCEPCIONAIS (FÉRIAS, FOLGA, FERIADO) - Sobrescreve as de cima
                if dia_str in mapa_excecoes:
                    if 'TODOS' in mapa_excecoes[dia_str] and mapa_excecoes[dia_str]['TODOS'] == 'FERIADO':
                        status_dia = 'FERIADO'
                    elif nome in mapa_excecoes[dia_str]:
                        status_dia = mapa_excecoes[dia_str][nome] # 'FERIAS' ou 'FOLGA'
                
                linha_dia['colaboradores'].append({
                    'nome': nome,
                    'qtd': qtd,
                    'status': status_dia,
                    'is_futuro': is_futuro
                })
                
                # Se ainda não chegou a data, não somamos 0 nas estatísticas, mantemos puras
                linha_dia['total_dia'] += qtd
                totais_usu[nome] += qtd
                tot_geral += qtd
                
            matriz.append(linha_dia)
            
        rodape = [totais_usu[nome] for nome in time_op]
        return matriz, time_op, rodape, tot_geral, titulo_mes, f"{ano_alvo}-{mes_alvo}"

    # Gera a matriz enviando o novo dado extra de filtro do mês
    retorno_1 = gerar_matriz_operadores(mes_ref)
    matriz_calendario, time_operacional, totais_rodape, total_geral_mes, titulo_matriz_1, base_data_1 = retorno_1
    
    if mes_ref_2:
        retorno_2 = gerar_matriz_operadores(mes_ref_2)
        matriz_calendario_2, time_operacional_2, totais_rodape_2, total_geral_mes_2, titulo_matriz_2, base_data_2 = retorno_2
    else:
        matriz_calendario_2, time_operacional_2, totais_rodape_2, total_geral_mes_2, titulo_matriz_2, base_data_2 = None, [], [], 0, "", ""

    # Quem está em home office / ausente hoje (só faz sentido na matriz do mês corrente)
    home_hoje, ausentes_hoje = [], []
    if base_data_1 == datetime.now().strftime('%Y-%m'):
        for linha in matriz_calendario:
            if linha['dia'] == str(datetime.now().day):
                for c in linha['colaboradores']:
                    if c['nome'] == 'Robôs': continue
                    if c['status'] == 'HOME': home_hoje.append(c['nome'])
                    elif c['status'] in ('FERIAS', 'FOLGA'): ausentes_hoje.append(c['nome'])

    base_pct_coletado = round(coletados / (total if mes_ref else base_atualizada) * 100, 1) if (total if mes_ref else base_atualizada) > 0 else 0

    meta_mes_ref = virada_mes.meta_mes_ref_atual(conexao)
    conexao.close()
    return render_template('dashboard.html', meta_mes_ref=meta_mes_ref,
                           pendentes_total=pendentes_total, status_pendentes=status_pendentes, carteira_responsaveis=carteira_responsaveis,
                           ritmo_coletas=ritmo_coletas, ritmo_media=ritmo_media, coletas_hoje=coletas_hoje,
                           home_hoje=home_hoje, ausentes_hoje=ausentes_hoje, pct_coletado=base_pct_coletado,
                           total=total, coletados=coletados, emitidos=emitidos, base_atualizada=base_atualizada, diferenca_total=diferenca_total,
                           percentual_baixado_total=percentual_baixado_total, vencendo_hoje=vencendo_hoje, stats_robos=stats_robos, 
                           ranking_manual=ranking_manual, lista_coletados=lista_coletados, lista_pendentes=lista_pendentes, lista_vencem_hoje=lista_vencem_hoje,
                           resumo_diario=resumo_diario, base_congelada=base_congelada, meses_arquivados=meses_arquivados, mes_ref=mes_ref,
                           breakdown_total=breakdown_total, breakdown_faltam=breakdown_faltam, nome_mes_real=nome_mes_real,
                           # DADOS DA MATRIZ:
                           matriz_calendario=matriz_calendario, time_operacional=time_operacional,
                           totais_rodape=totais_rodape, total_geral_mes=total_geral_mes, titulo_matriz_1=titulo_matriz_1, base_data_1=base_data_1,
                           mes_ref_2=mes_ref_2, matriz_calendario_2=matriz_calendario_2,
                           time_operacional_2=time_operacional_2, totais_rodape_2=totais_rodape_2,
                           total_geral_mes_2=total_geral_mes_2, titulo_matriz_2=titulo_matriz_2, base_data_2=base_data_2)

@app.route('/api/dashboard/resumo_por_dia')
@login_obrigatorio
def api_dashboard_resumo_por_dia():
    dia_param = request.args.get('dia', '').strip() # Pode receber "01" ou "1-4"
    mes_ref = request.args.get('mes_ref', '').strip()
    
    conexao = conectar_banco()
    parametros = []
    
    # 🎯 MÁGICA: Se tiver um "-", ele entende que é um Período Agrupado!
    if '-' in dia_param:
        p_inicio, p_fim = dia_param.split('-')
        filtro_data = "CAST(SUBSTR(vencimento, 1, 2) AS INTEGER) BETWEEN ? AND ?"
        parametros.extend([int(p_inicio), int(p_fim)])
    else:
        filtro_data = "vencimento LIKE ?"
        parametros.append(f"{dia_param.zfill(2)}/%")
    
    if mes_ref:
        query = f'''
            SELECT IFNULL(TRIM(responsavel), 'Não Atribuído') as responsavel,
                   COUNT(id) as total_inicial,
                   SUM(CASE WHEN status_interno IS NULL OR status_interno NOT IN ('PAGO', 'COLETADO') THEN 1 ELSE 0 END) as base_atualizada
            FROM historico_fechamento_mes
            WHERE mes_referencia = ? AND {filtro_data}
            GROUP BY TRIM(UPPER(responsavel))
            ORDER BY total_inicial DESC
        '''
        params_finais = [mes_ref] + parametros
        linhas_raw = conexao.execute(query, params_finais).fetchall()
    else:
        query = f'''
            SELECT IFNULL(TRIM(v.responsavel), 'Não Atribuído') as responsavel,
                   COUNT(i.id) as total_inicial,
                   SUM(CASE WHEN i.status_interno IS NULL OR i.status_interno NOT IN {SQL_STATUS_COLETADO} THEN 1 ELSE 0 END) as base_atualizada
            FROM imoveis i
            LEFT JOIN vinculos_adm v ON v.rowid = vinculo_de(i.administradora)
            WHERE i.ativo = 1 AND {filtro_data}
            GROUP BY TRIM(UPPER(v.responsavel))
            ORDER BY total_inicial DESC
        '''
        linhas_raw = conexao.execute(query, parametros).fetchall()
        
    conexao.close()

    linhas = []
    for row in linhas_raw:
        resp = row['responsavel'] if row['responsavel'] else 'Não Atribuído'
        tot = row['total_inicial']
        atual = row['base_atualizada']  # aqui "atual" = ainda sem coleta (pendentes)
        dif = tot - atual

        # Chaves novas (base_atual / coletados / pendentes) usadas pelo dashboard; as antigas seguem para não quebrar nada.
        linhas.append({'responsavel': resp, 'total_inicial': tot, 'base_atualizada': atual, 'diferenca': dif,
                       'base_atual': tot, 'coletados': dif, 'pendentes': atual})

    return jsonify({'sucesso': True, 'dia': dia_param, 'dados': linhas})

# =================================================================
# CENTRAL DO IMÓVEL (CADASTRO, HISTÓRICO E CREDENCIAIS)
# =================================================================

def inicializar_banco_imoveis():
    conexao = conectar_banco()
    conexao.execute('''
        CREATE TABLE IF NOT EXISTS cadastro_mestre_imoveis (
            imovel TEXT PRIMARY KEY,
            empreendimento TEXT,
            administradora TEXT,
            responsavel_atual TEXT,
            login_acesso TEXT DEFAULT '',
            senha_acesso TEXT DEFAULT '',
            status_cadastro TEXT DEFAULT 'ATIVO'
        )
    ''')
    try: conexao.execute("ALTER TABLE cadastro_mestre_imoveis ADD COLUMN media_emissao TEXT")
    except: pass

    conexao.execute('''
        CREATE TABLE IF NOT EXISTS cadastro_mestre_imoveis (
            imovel TEXT PRIMARY KEY,
            login_acesso TEXT,
            senha_acesso TEXT,
            portal TEXT
        )
    ''')
    
    try:
        conexao.execute("ALTER TABLE cadastro_mestre_imoveis ADD COLUMN portal TEXT DEFAULT ''")
    except Exception:
        pass 

    conexao.execute('''
        CREATE TABLE IF NOT EXISTS auditoria_robos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            robo TEXT,
            imovel TEXT,
            status_tentativa TEXT,
            detalhe TEXT,
            data_hora TEXT
        )
    ''')
    try:
        conexao.execute("ALTER TABLE auditoria_robos ADD COLUMN status_tentativa TEXT")
    except Exception:
        pass
    try:
        conexao.execute("ALTER TABLE auditoria_robos ADD COLUMN detalhe TEXT")
    except Exception:
        pass

    conexao.execute('''
        CREATE TABLE IF NOT EXISTS historico_mensal_imoveis (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            imovel TEXT,
            mes_ano TEXT,
            status_liquidacao TEXT,
            coletado_por TEXT,
            data_registro TEXT
        )
    ''')
    conexao.commit()
    conexao.close()

inicializar_banco_imoveis()

# ==============================================================================
# 🏢 CENTRAL DE IMÓVEIS — pesquisa e prontuário
# ==============================================================================
IMOVEIS_POR_PAGINA = 60

# Como a unidade aparece no endereço da planilha:
# 'Rua Jaci, 90 - AP. 52 -  Andar 5 -  Bl. 2'
RE_END_UNIDADE = re.compile(r'^(AP|APTO|APART\w*|SL|SALA|CJ|CONJ\w*|CASA|LOJA|UN|UNID\w*|BOX|VAGA|GAR\w*)\.?\s*(.+)$', re.IGNORECASE)
RE_END_ANDAR = re.compile(r'^(ANDAR|AND)\.?\s*(.+)$', re.IGNORECASE)
RE_END_BLOCO = re.compile(r'^(BL|BLOCO|TORRE|TR|ED|EDIF\w*)\.?\s*(.+)$', re.IGNORECASE)


def detalhar_endereco(endereco):
    """Separa o endereço da planilha em logradouro, unidade, andar e bloco."""
    partes = [p.strip() for p in str(endereco or '').split(' - ') if p.strip()]
    saida = {'logradouro': partes[0] if partes else '', 'unidade': '', 'andar': '', 'bloco': '', 'extras': []}
    for pedaco in partes[1:]:
        achou = RE_END_UNIDADE.match(pedaco)
        if achou and not saida['unidade']:
            saida['unidade'] = f'{achou.group(1).upper().rstrip(".")}. {achou.group(2).strip()}'
            continue
        achou = RE_END_ANDAR.match(pedaco)
        if achou and not saida['andar']:
            saida['andar'] = achou.group(2).strip()
            continue
        achou = RE_END_BLOCO.match(pedaco)
        if achou and not saida['bloco']:
            saida['bloco'] = achou.group(2).strip()
            continue
        saida['extras'].append(pedaco)
    return saida


def _primeiro_preenchido(*valores):
    for v in valores:
        texto = str(v or '').strip()
        if texto and texto.lower() not in ('none', 'nan', '-'):
            return texto
    return ''


def identificacao_no_condominio(mestre, endereco):
    """Junta o que a planilha diz com o que está cadastrado nos portais.
    Devolve bloco/unidade/condomínio e de onde cada um saiu."""
    do_endereco = detalhar_endereco(endereco)
    m = mestre or {}
    unidade = _primeiro_preenchido(do_endereco['unidade'], m.get('winker_unidade'), m.get('webware_unidade'),
                                   m.get('super_unidade'), m.get('unidade_site'))
    bloco = _primeiro_preenchido(do_endereco['bloco'], m.get('winker_predio'), m.get('super_bloco'), m.get('bloco_site'))
    condominio_portal = _primeiro_preenchido(m.get('winker_condominio'), m.get('webware_empreendimento'),
                                             m.get('super_condominio'), m.get('emp_site'))
    fonte = 'planilha' if do_endereco['unidade'] else (
        'portal Winker' if _primeiro_preenchido(m.get('winker_unidade')) else (
            'portal Webware' if _primeiro_preenchido(m.get('webware_unidade')) else (
                'Superlógica' if _primeiro_preenchido(m.get('super_unidade')) else '')))
    return {
        'logradouro': do_endereco['logradouro'], 'unidade': unidade, 'bloco': bloco, 'andar': do_endereco['andar'],
        'complementos': do_endereco['extras'], 'condominio_portal': condominio_portal, 'fonte_unidade': fonte,
    }


def _mapa_responsaveis(conexao):
    return {normalizar_texto(v['administradora']): v['responsavel']
            for v in conexao.execute('SELECT administradora, responsavel FROM vinculos_adm').fetchall()
            if v['administradora']}


@app.route('/imoveis')
@login_obrigatorio
def lista_imoveis():
    termo = request.args.get('pesquisa', '').strip()
    portal = request.args.get('portal', '').strip().upper()
    carteira = request.args.get('carteira', '').strip()
    situacao = request.args.get('situacao', '').strip()         # '', 'sem_login', 'com_login', 'na_planilha'
    try:
        pagina = max(1, int(request.args.get('pagina', 1)))
    except ValueError:
        pagina = 1

    conexao = conectar_banco()
    try:
        conexao.create_function('norm', 1, _sem_acento_minusculo, deterministic=True)
        portal_sql = "COALESCE(NULLIF(TRIM(c.portal), ''), 'DIVERSOS')"
        tem_login = ("(TRIM(IFNULL(c.login_acesso, '')) != '' AND TRIM(IFNULL(c.senha_acesso, '')) != ''"
                     " AND c.login_acesso NOT LIKE '[X]%')")
        filtros, parametros = [], []

        if termo:
            # pesquisa: número, condomínio, administradora, endereço, sacado, documento, login e unidade do portal
            alvo = f'%{_sem_acento_minusculo(termo)}%'
            filtros.append("""(norm(c.imovel) LIKE ? OR norm(c.empreendimento) LIKE ? OR norm(c.administradora) LIKE ?
                               OR norm(i.endereco) LIKE ? OR norm(i.sacado) LIKE ? OR norm(i.cpf_cnpj) LIKE ?
                               OR norm(c.login_acesso) LIKE ? OR norm(c.winker_condominio) LIKE ?
                               OR norm(c.webware_empreendimento) LIKE ?)""")
            parametros.extend([alvo] * 9)
        if portal and portal != 'ALL':
            filtros.append(f'UPPER({portal_sql}) = ?')
            parametros.append(portal)
        if carteira and carteira != 'ALL':
            filtros.append("""norm(IFNULL((SELECT v.responsavel FROM vinculos_adm v
                              WHERE v.rowid = vinculo_de(c.administradora) LIMIT 1), '')) = ?""")
            parametros.append(_sem_acento_minusculo(carteira))
        if situacao == 'sem_login':
            filtros.append(f'NOT {tem_login}')
        elif situacao == 'com_login':
            filtros.append(tem_login)
        elif situacao == 'na_planilha':
            filtros.append('i.imovel IS NOT NULL')

        onde = ('WHERE ' + ' AND '.join(filtros)) if filtros else ''
        juncao = """FROM cadastro_mestre_imoveis c
                    LEFT JOIN (SELECT imovel, MAX(id) AS mid FROM imoveis WHERE ativo = 1 GROUP BY imovel) a ON a.imovel = c.imovel
                    LEFT JOIN imoveis i ON i.id = a.mid"""

        total = conexao.execute(f'SELECT COUNT(DISTINCT c.imovel) {juncao} {onde}', parametros).fetchone()[0]
        paginas = max(1, (total + IMOVEIS_POR_PAGINA - 1) // IMOVEIS_POR_PAGINA)
        pagina = min(pagina, paginas)

        registros = [dict(r) for r in conexao.execute(f"""
            SELECT c.imovel,
                   COALESCE(NULLIF(TRIM(IFNULL(c.empreendimento, '')), ''), NULLIF(TRIM(IFNULL(i.empreendimento, '')), '')) AS empreendimento,
                   COALESCE(NULLIF(TRIM(IFNULL(c.administradora, '')), ''), NULLIF(TRIM(IFNULL(i.administradora, '')), '')) AS administradora,
                   c.winker_predio, c.winker_unidade,
                   c.webware_unidade, c.super_bloco, c.super_unidade, c.unidade_site, c.bloco_site,
                   c.login_acesso, c.senha_acesso, c.status_cadastro, c.media_emissao,
                   {portal_sql} AS portal, {tem_login} AS tem_credencial,
                   i.endereco, i.vencimento, i.status_interno, (i.imovel IS NOT NULL) AS na_planilha
            {juncao} {onde}
            GROUP BY c.imovel
            ORDER BY (i.imovel IS NOT NULL) DESC, NULLIF(TRIM(IFNULL(c.empreendimento, i.empreendimento)), '') IS NULL, TRIM(IFNULL(c.empreendimento, i.empreendimento)), c.imovel
            LIMIT ? OFFSET ?""", parametros + [IMOVEIS_POR_PAGINA, (pagina - 1) * IMOVEIS_POR_PAGINA]).fetchall()]

        mapa_responsavel = _mapa_responsaveis(conexao)
        for r in registros:
            r['responsavel'] = mapa_responsavel.get(normalizar_texto(r.get('administradora'))) or 'Não Atribuído'
            r['unidade'] = _primeiro_preenchido(detalhar_endereco(r.get('endereco'))['unidade'], r.get('winker_unidade'),
                                                r.get('webware_unidade'), r.get('super_unidade'), r.get('unidade_site'))
            r['bloco'] = _primeiro_preenchido(detalhar_endereco(r.get('endereco'))['bloco'], r.get('winker_predio'),
                                              r.get('super_bloco'), r.get('bloco_site'))
            # o portal às vezes repete o nome do condomínio no campo do bloco: não vale a pena mostrar duas vezes
            if r['bloco'] and len(r['bloco']) > 3 and normalizar_texto(r['bloco']) in normalizar_texto(r.get('empreendimento') or '-----'):
                r['bloco'] = ''
            r['sem_portal'] = str(r.get('login_acesso') or '').startswith('[X]')

        portais = [p[0] for p in conexao.execute(f"""SELECT DISTINCT {portal_sql} p FROM cadastro_mestre_imoveis c
                                                     WHERE TRIM(IFNULL(c.portal, '')) != '' ORDER BY 1""").fetchall()]
        carteiras = sorted({v for v in mapa_responsavel.values() if v and v != 'Não Atribuído'})
        resumo = {
            'total_base': conexao.execute('SELECT COUNT(*) FROM cadastro_mestre_imoveis').fetchone()[0],
            'na_planilha': conexao.execute('SELECT COUNT(DISTINCT imovel) FROM imoveis WHERE ativo = 1').fetchone()[0],
            'sem_credencial': conexao.execute(f'SELECT COUNT(*) FROM cadastro_mestre_imoveis c WHERE NOT {tem_login}').fetchone()[0],
        }
    finally:
        conexao.close()

    return render_template('imoveis.html', registros=registros, termo=termo, portal=portal or 'ALL',
                           carteira=carteira or 'ALL', situacao=situacao, portais=portais, carteiras=carteiras,
                           total_registros=total, pagina=pagina, paginas=paginas, resumo=resumo, imovel=None)


@app.route('/imovel/<imovel>')
@login_obrigatorio
def visualizar_imovel(imovel):
    conexao = conectar_banco()
    try:
        mestre = conexao.execute("""
            SELECT c.*, COALESCE(NULLIF(TRIM(c.portal), ''), 'DIVERSOS') AS portal
            FROM cadastro_mestre_imoveis c WHERE c.imovel = ?""", (imovel,)).fetchone()
        if not mestre:
            conexao.close()
            return redirect(url_for('lista_imoveis', pesquisa=imovel))

        dados = dict(mestre)
        planilha = conexao.execute('SELECT * FROM imoveis WHERE imovel = ? ORDER BY ativo DESC, id DESC LIMIT 1',
                                   (imovel,)).fetchone()
        for campo in ('empreendimento', 'administradora'):   # cadastro mestre incompleto: a planilha completa
            if not str(dados.get(campo) or '').strip() and planilha and planilha[campo]:
                dados[campo] = planilha[campo]
        id_real = planilha['id'] if planilha else 0
        dados['id_imovel_real'] = id_real or None
        dados['na_planilha'] = bool(planilha and planilha['ativo'] == 1)

        mapa_responsavel = _mapa_responsaveis(conexao)
        dados['responsavel_atual'] = (mapa_responsavel.get(normalizar_texto(dados.get('administradora')))
                                      or dados.get('responsavel_atual') or 'Não Atribuído')
        dados['responsavel'] = dados['responsavel_atual']
        dados['email_administradora'] = _primeiro_preenchido(*[
            v['email'] for v in conexao.execute('SELECT administradora, email FROM vinculos_adm').fetchall()
            if normalizar_texto(v['administradora']) == normalizar_texto(dados.get('administradora'))])

        campos_planilha = ['vencimento', 'carteira', 'd_lote', 'endereco', 'sacado', 'cpf_cnpj', 'telefone',
                           'status_interno', 'dt_comdesc', 'ini_vig_contr']
        for campo in campos_planilha:
            dados[campo] = (str(planilha[campo]).strip() if planilha and planilha[campo] is not None else '')
        dados['observacao_planilha'] = (planilha['observacao'] if planilha and planilha['observacao'] else '')
        dados['acesso_planilha'] = (planilha['acesso'] if planilha and planilha['acesso'] else '')
        dados['status_interno'] = dados['status_interno'] or ('PENDENTE' if dados['na_planilha'] else 'FORA DA PLANILHA')
        dados['sem_portal'] = str(dados.get('login_acesso') or '').startswith('[X]')

        dados['local'] = identificacao_no_condominio(dados, dados.get('endereco'))
        if len(dados['local']['bloco']) > 3 and normalizar_texto(dados['local']['bloco']) in normalizar_texto(dados.get('empreendimento') or '-----'):
            dados['local']['bloco'] = ''

        # ---- linha do tempo mensal (mesma regra de antes) ----
        vencimento_mes_ano = ''
        partes = str(dados['vencimento'] or '').split(' ')[0].replace('-', '/').split('/')
        if len(partes) >= 3 and partes[1].isdigit():
            ano = partes[2] if len(partes[2]) == 4 else '20' + partes[2]
            vencimento_mes_ano = f'{MESES_NOMES_PT[int(partes[1]) - 1]}/{ano}'

        historico_total = conexao.execute("""
            SELECT NULL AS id_obs, h.data_hora AS data_registro, h.usuario AS coletado_por, TRIM(i.vencimento) AS venc, NULL AS mes_ano,
                   CASE WHEN UPPER(h.texto) LIKE '%COLETADO%' THEN 'COLETADO'
                        WHEN UPPER(h.texto) LIKE '%PAGO NA ADM%' THEN 'PAGO NA ADM'
                        WHEN UPPER(h.texto) LIKE '%PAGO%' THEN 'PAGO' END AS status_liquidacao
            FROM historico_observacoes h JOIN imoveis i ON i.id = h.imovel_id
            WHERE i.imovel = ? AND (UPPER(h.texto) LIKE '%COLETADO%' OR UPPER(h.texto) LIKE '%PAGO%')
            UNION ALL
            SELECT NULL, p.data_hora, p.usuario, TRIM(i.vencimento), NULL,
                   CASE p.acao WHEN 'COLETOU BOLETO' THEN 'COLETADO' WHEN 'PAGOU BOLETO' THEN 'PAGO'
                        WHEN 'PAGOU BOLETO NA 275' THEN 'PAGO' WHEN 'ALTEROU STATUS PARA PAGO NA ADM' THEN 'PAGO NA ADM' END
            FROM produtividade p JOIN imoveis i ON i.id = p.imovel_id
            WHERE i.imovel = ? AND p.acao IN ('COLETOU BOLETO', 'PAGOU BOLETO', 'PAGOU BOLETO NA 275', 'ALTEROU STATUS PARA PAGO NA ADM')
            UNION ALL
            SELECT hm.id, hm.data_registro, hm.coletado_por, NULL, hm.mes_ano,
                   CASE WHEN UPPER(hm.status_liquidacao) LIKE '%COLETADO%' THEN 'COLETADO'
                        WHEN UPPER(hm.status_liquidacao) LIKE '%PAGO NA ADM%' THEN 'PAGO NA ADM'
                        WHEN UPPER(hm.status_liquidacao) LIKE '%PAGO%' THEN 'PAGO' ELSE hm.status_liquidacao END
            FROM historico_mensal_imoveis hm WHERE LTRIM(hm.imovel, '0') = LTRIM(?, '0')
            UNION ALL
            SELECT NULL, hf.data_arquivamento, hf.responsavel, NULL, hf.mes_referencia, hf.status_interno
            FROM historico_fechamento_mes hf
            WHERE LTRIM(hf.imovel, '0') = LTRIM(?, '0') AND hf.status_interno IN ('PAGO', 'COLETADO', 'PAGO NA 275', 'PAGO NA ADM')
            ORDER BY data_registro DESC""", (imovel, imovel, imovel, imovel)).fetchall()

        def _competencia(venc):
            p = str(venc or '').split(' ')[0].replace('-', '/').split('/')
            if len(p) >= 3 and p[1].isdigit() and 1 <= int(p[1]) <= 12:
                ano = p[2] if len(p[2]) == 4 else '20' + p[2]
                return f'{MESES_NOMES_PT[int(p[1]) - 1]}/{ano}'
            return ''

        historico, ciclos_vistos = [], set()
        for h in historico_total:
            rotulo = h['mes_ano'] or _competencia(h['venc']) or vencimento_mes_ano or ''
            if not rotulo and h['data_registro']:
                p = str(h['data_registro']).split(' ')[0].split('-')
                if len(p) >= 2 and p[1].isdigit():
                    rotulo = f'{MESES_NOMES_PT[int(p[1]) - 1]}/{p[0]}'
            if rotulo in ciclos_vistos:
                continue
            ciclos_vistos.add(rotulo)
            historico.append({'id_obs': h['id_obs'], 'mes_ano': rotulo, 'status_liquidacao': h['status_liquidacao'],
                              'coletado_por': h['coletado_por'], 'data_registro': h['data_registro']})

        # ---- o que aconteceu com este imóvel, em ordem ----
        movimentos = []
        if id_real:
            for o in conexao.execute("""SELECT data_hora, usuario, texto FROM historico_observacoes
                                        WHERE imovel_id IN (SELECT id FROM imoveis WHERE imovel = ?)
                                        ORDER BY id DESC LIMIT 25""", (imovel,)).fetchall():
                movimentos.append({'quando': o['data_hora'], 'quem': o['usuario'] or 'Sistema',
                                   'o_que': ' '.join(str(o['texto'] or '').split())[:260], 'tipo': 'nota'})
            for p in conexao.execute("""SELECT data_hora, usuario, acao FROM produtividade
                                        WHERE imovel_id IN (SELECT id FROM imoveis WHERE imovel = ?)
                                        ORDER BY id DESC LIMIT 25""", (imovel,)).fetchall():
                movimentos.append({'quando': p['data_hora'], 'quem': p['usuario'] or 'Sistema',
                                   'o_que': str(p['acao'] or '').capitalize(), 'tipo': 'acao'})
        movimentos.sort(key=lambda x: (str(x['quando'] or ''), len(x['o_que'])), reverse=True)
        enxutos, vistos = [], set()
        for mov in movimentos:          # a nota e a ação do mesmo clique viram uma linha só
            marca = (str(mov['quando'] or '')[:19], mov['quem'])
            if marca in vistos:
                continue
            vistos.add(marca)
            enxutos.append(mov)
        movimentos = enxutos[:18]

        hist_credenciais = [dict(r) for r in conexao.execute(
            'SELECT * FROM historico_credenciais WHERE imovel = ? ORDER BY id DESC LIMIT 15', (imovel,)).fetchall()]
        try:
            relacionados = [dict(r) for r in conexao.execute(
                'SELECT tipo, assunto, link, data_hora FROM relacionamento WHERE imovel = ? ORDER BY id DESC LIMIT 10',
                (imovel,)).fetchall()]
        except Exception:
            relacionados = []
        try:
            contas = [dict(r) for r in conexao.execute(
                """SELECT mes_ref, conta, link_drive, usuario, data_registro FROM contas_internet
                   WHERE imovel = ? ORDER BY id DESC LIMIT 10""", (imovel,)).fetchall()]
        except Exception:
            contas = []
    finally:
        conexao.close()

    return render_template('imoveis.html', imovel=dados, historico=historico, hist_credenciais=hist_credenciais,
                           movimentos=movimentos, relacionados=relacionados, contas=contas, termo='')

@app.route('/api/imovel/salvar_credenciais', methods=['POST'])
@login_obrigatorio
def api_salvar_credenciais():
    dados = request.get_json() or {}
    imovel_num = dados.get('imovel')
    novo_login = dados.get('login', '').strip()
    nova_senha = dados.get('senha', '').strip()
    novo_portal = dados.get('portal', 'DIVERSOS')
    novo_link = dados.get('link', '').strip() # 🎯 NOVO CAMPO CAPTURADO
    # 🔐 "Não possui login": o imóvel sai dos robôs de site e cai em DIVERSOS
    sem_login = _sim(dados.get('sem_login'))
    if sem_login:
        novo_login, nova_senha, novo_portal = '', '', 'DIVERSOS'
    
    usuario_atual = session.get('usuario', 'Sistema')
    from datetime import datetime
    agora_tela = datetime.now().strftime('%d/%m/%Y %H:%M')

    conexao = conectar_banco()
    try:
        # 1. Cria a tabela independente para o histórico de credenciais se não existir
        conexao.execute('CREATE TABLE IF NOT EXISTS historico_credenciais (id INTEGER PRIMARY KEY AUTOINCREMENT, imovel TEXT, data_hora TEXT, usuario TEXT, texto TEXT)')

        # 2. 🎯 GARANTE QUE A COLUNA DO LINK EXISTE NO BANCO DE DADOS
        try: conexao.execute("ALTER TABLE cadastro_mestre_imoveis ADD COLUMN link_portal TEXT DEFAULT ''")
        except: pass
        try: conexao.execute("ALTER TABLE cadastro_mestre_imoveis ADD COLUMN cred_user TEXT")
        except: pass
        try: conexao.execute("ALTER TABLE cadastro_mestre_imoveis ADD COLUMN cred_data TEXT")
        except: pass

        garantir_colunas_acesso(conexao)
        conexao.execute('INSERT OR IGNORE INTO cadastro_mestre_imoveis (imovel) VALUES (?)', (imovel_num,))
        antigo = conexao.execute('SELECT portal, login_acesso, senha_acesso, link_portal, possui_login FROM cadastro_mestre_imoveis WHERE imovel = ?', (imovel_num,)).fetchone()
        
        detalhes_mudanca = []
        if sem_login and (not antigo or str(antigo['possui_login'] or '').upper() != 'NAO'):
            detalhes_mudanca.append("Marcou: não possui login (portal DIVERSOS)")
        if antigo:
            velho_portal = antigo['portal'] or 'DIVERSOS'
            velho_login = antigo['login_acesso'] or ''
            velha_senha = antigo['senha_acesso'] or ''
            velho_link = antigo['link_portal'] or '' # 🎯 LÊ O LINK ANTIGO
            
            if velho_portal != novo_portal: detalhes_mudanca.append(f"Portal p/ '{novo_portal}'")
            
            if velho_login == '' and novo_login != '': detalhes_mudanca.append(f"Add Login '{novo_login}'")
            elif velho_login != '' and novo_login == '': detalhes_mudanca.append(f"Removeu Login (era '{velho_login}')")
            elif velho_login != novo_login: detalhes_mudanca.append(f"Login p/ '{novo_login}'")
            
            if velha_senha != nova_senha: detalhes_mudanca.append("Senha alterada")
            
            # 🎯 INTELIGÊNCIA DE AUDITORIA DO LINK
            if velho_link != novo_link:
                if velho_link == '' and novo_link != '': detalhes_mudanca.append("Add URL do Portal")
                elif velho_link != '' and novo_link == '': detalhes_mudanca.append("Removeu URL do Portal")
                else: detalhes_mudanca.append("Alterou URL do Portal")

        # 3. Atualiza a tabela MESTRE (agora gravando o link_portal)
        conexao.execute('''
            UPDATE cadastro_mestre_imoveis 
            SET login_acesso = ?, senha_acesso = ?, portal = ?, cred_user = ?, cred_data = ?, link_portal = ?
            WHERE imovel = ?
        ''', (novo_login, nova_senha, novo_portal, usuario_atual, agora_tela, novo_link, imovel_num))
        if sem_login:
            conexao.execute("UPDATE cadastro_mestre_imoveis SET possui_login = 'NAO' WHERE imovel = ?", (imovel_num,))
        elif novo_login and nova_senha:
            conexao.execute("UPDATE cadastro_mestre_imoveis SET possui_login = 'SIM' WHERE imovel = ?", (imovel_num,))

        # Login/senha corrigidos aqui também resolvem o erro de login do Raio-X (é o que o robô precisava)
        trocou_credencial = sem_login or (novo_login and nova_senha and (
            not antigo or (antigo['login_acesso'] or '') != novo_login or (antigo['senha_acesso'] or '') != nova_senha))
        if trocou_credencial and erros_ate_resolver_pendentes(conexao, [imovel_num], ('LOGIN',)):
            texto_aud = (f"Sem login: informado no painel por {usuario_atual}. O imóvel passou para DIVERSOS."
                         if sem_login else f"Login/senha atualizados no painel por {usuario_atual} ({novo_portal}).")
            conexao.execute("INSERT INTO auditoria_robos (robo, imovel_id, status, detalhe, data_hora) VALUES (?, ?, 'AJUSTADO', ?, ?)",
                            (novo_portal, str(imovel_num), texto_aud, datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
            esquecer_resumo_raio_x()
        
        # 4. SALVA NO HISTÓRICO SEPARADO
        if detalhes_mudanca:
            texto_historico = "Modificações: " + ", ".join(detalhes_mudanca)
            conexao.execute('''
                INSERT INTO historico_credenciais (imovel, data_hora, usuario, texto)
                VALUES (?, ?, ?, ?)
            ''', (imovel_num, agora_tela, usuario_atual, texto_historico))
        
        conexao.commit()
        return jsonify({'sucesso': True})
    except Exception as e:
        print("Erro crítico ao salvar credenciais:", e)
        return jsonify({'sucesso': False, 'msg': str(e)})
    finally:
        conexao.close()

@app.route('/api/automacao/auditar', methods=['POST'])
def api_automacao_auditar():
    dados = request.get_json()
    if not dados:
        return jsonify({"sucesso": False, "erro": "Dados não enviados"}), 400
        
    robo = dados.get('robo', 'AUTOMAÇÃO DESCONHECIDO')
    imovel = dados.get('imovel')
    status = dados.get('status')
    detalhe = dados.get('detalhe', '')
    
    if not imovel:
        return jsonify({"sucesso": False, "erro": "ID do imóvel é obrigatório"}), 400

    conexao = conectar_banco()
    agora = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    try:
        # 🛡️ BLINDAGEM 1: Cria a tabela de histórico se ela não existir
        conexao.execute('''
            CREATE TABLE IF NOT EXISTS auditoria_robos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                robo TEXT,
                imovel_id TEXT,
                status TEXT,
                detalhe TEXT,
                data_hora TEXT
            )
        ''')

        # 🛡️ BLINDAGEM 2: Garante a coluna imovel_id na tabela de auditoria
        try: conexao.execute("ALTER TABLE auditoria_robos ADD COLUMN imovel_id TEXT;")
        except: pass

        # 🛡️ BLINDAGEM 3: Garante a coluna 'notas' na tabela de imóveis
        try: conexao.execute("ALTER TABLE imoveis ADD COLUMN notas TEXT DEFAULT '';")
        except: pass

        # Grava no histórico geral de auditoria técnica
        conexao.execute('''
            INSERT INTO auditoria_robos (robo, imovel_id, status, detalhe, data_hora)
            VALUES (?, ?, ?, ?, ?)
        ''', (robo, str(imovel), status, detalhe, agora))
        
        # 🟢 SE ESTIVER PAGO OU SE FOR ERRO, GRAVA NAS NOTAS DO OPERADOR
        if status != "Sucesso":
            if status == "Não Disponível":
                mensagem_nota = " O automação verificou este imóvel e não há boletos disponíveis para download."
            else:
                mensagem_nota = "⚠️ Ocorreu um erro na captura automática pelo automação."
            
            # Puxa o imóvel fazendo o tratamento de nulos direto no SQL com COALESCE
            imovel_atual = conexao.execute("SELECT COALESCE(notas, '') AS notas_limpas FROM imoveis WHERE imovel = ?", (str(imovel),)).fetchone()
            
            if imovel_atual:
                notas_antigas = imovel_atual['notas_limpas'].strip()
                
                # Evita duplicar a mesma mensagem seguida na tela
                if mensagem_nota in notas_antigas.split('\n')[0]:
                    nota_nova = notas_antigas
                else:
                    nota_nova = f"{mensagem_nota}\n{notas_antigas}" if notas_antigas else mensagem_nota
                    
                conexao.execute("UPDATE imoveis SET notas = ? WHERE imovel = ?", (nota_nova, str(imovel)))
                print(f"📝 [NOTAS PORTAL] Gravado com sucesso para o imóvel {imovel}: -> {status}")
            else:
                print(f"⚠️ [NOTAS PORTAL] ID {imovel} não localizado na tabela 'imoveis'.")
                
        conexao.commit()
        return jsonify({"sucesso": True, "mensagem": "Processado com sucesso!"}), 200
        
    except Exception as e:
        print(f"🚨 ERRO CRÍTICO NA API DE AUDITORIA: {e}")
        return jsonify({"sucesso": False, "erro": str(e)}), 500
    finally:
        conexao.close()

@app.route('/api/sistema/auditoria_logs')
@login_obrigatorio
def api_auditoria_logs():
    conexao = conectar_banco()
    logs = conexao.execute('SELECT * FROM auditoria_robos ORDER BY id DESC LIMIT 300').fetchall()
    conexao.close()
    return jsonify([dict(row) for row in logs])

@app.route('/obter_detalhes_imovel')
@login_obrigatorio
def obter_detalhes_imovel():
    imovel_num = request.args.get('imovel', '').strip()
    conexao = conectar_banco()
    
    # Busca os dados mestres e cruza o operador em tempo real
    query_mestre = """
        SELECT c.*, IFNULL(v.responsavel, 'Não Atribuído') AS responsavel_atual
        FROM cadastro_mestre_imoveis c
        LEFT JOIN vinculos_adm v ON v.rowid = vinculo_de(c.administradora)
        WHERE c.imovel = ?
    """
    imovel_mestre = conexao.execute(query_mestre, (imovel_num,)).fetchone()
    
    if not imovel_mestre:
        conexao.close()
        return jsonify({'sucesso': False, 'msg': 'Imóvel não localizado no cadastro mestre.'})
        
    # Removemos o "AND ativo = 1" para nunca mais dar erro quando o automação atualizar o imóvel
    imovel_planilha = conexao.execute("SELECT * FROM imoveis WHERE imovel = ? ORDER BY ativo DESC, id DESC LIMIT 1", (imovel_num,)).fetchone()
    
    imovel_dados = dict(imovel_mestre)
    vinculos = conexao.execute('SELECT administradora, responsavel FROM vinculos_adm').fetchall()
    mapa_responsavel = {normalizar_texto(v['administradora']): v['responsavel'] for v in vinculos if v['administradora']}
    imovel_dados['responsavel_atual'] = imovel_dados.get('responsavel_atual', None)
    if not imovel_dados['responsavel_atual'] or imovel_dados['responsavel_atual'] == 'Não Atribuído':
        imovel_dados['responsavel_atual'] = mapa_responsavel.get(normalizar_texto(imovel_dados.get('administradora')))
    imovel_dados['responsavel_atual'] = imovel_dados['responsavel_atual'] or 'Não Atribuído'
    
    # Força os dois formatos de campo para o HTML/JS nunca ficarem vazios
    imovel_dados['responsavel'] = imovel_dados['responsavel_atual']
    imovel_dados['responsavel_atual'] = imovel_dados['responsavel_atual']
    
    # 🎯 PEGA O ID DO MÊS ATUAL (Ou 0 se não existir para não dar erro)
    id_real = imovel_planilha['id'] if imovel_planilha else 0
    
    if imovel_planilha:
        # 🎯 AQUI ESTÁ A MÁGICA: Enviando o ID real da planilha para o Frontend!
        imovel_dados['id'] = id_real
        imovel_dados['id_imovel_real'] = id_real
        
        imovel_dados['vencimento'] = imovel_planilha['vencimento'] if imovel_planilha['vencimento'] else '-'
        imovel_dados['carteira'] = imovel_planilha['carteira'] if imovel_planilha['carteira'] else '-'
        imovel_dados['d_lote'] = imovel_planilha['d_lote'] if imovel_planilha['d_lote'] else '-'
        imovel_dados['endereco'] = imovel_planilha['endereco'] if imovel_planilha['endereco'] else 'Não informado'
        imovel_dados['sacado'] = imovel_planilha['sacado'] if imovel_planilha['sacado'] else '-'
        imovel_dados['cpf_cnpj'] = imovel_planilha['cpf_cnpj'] if imovel_planilha['cpf_cnpj'] else '-'
        imovel_dados['status_interno'] = imovel_planilha['status_interno'] if imovel_planilha['status_interno'] else 'PENDENTE'
        imovel_dados['observacao_planilha'] = imovel_planilha['observacao'] if imovel_planilha['observacao'] else 'Sem anotações de planilha'
        imovel_dados['dt_comdesc'] = imovel_planilha['dt_comdesc'] if imovel_planilha['dt_comdesc'] else '-'
        imovel_dados['acesso_planilha'] = imovel_planilha['acesso'] if imovel_planilha['acesso'] else '-'
    else:
        # 🎯 Proteção caso a planilha não tenha dados
        imovel_dados['id'] = None
        imovel_dados['id_imovel_real'] = None
        
        imovel_dados['vencimento'] = '-'
        imovel_dados['carteira'] = '-'
        imovel_dados['d_lote'] = '-'
        imovel_dados['endereco'] = 'Este imóvel não possui dados ativos na planilha do mês.'
        imovel_dados['sacado'] = '-'
        imovel_dados['cpf_cnpj'] = '-'
        imovel_dados['status_interno'] = 'FORA DA CARTEIRA'
        imovel_dados['observacao_planilha'] = '-'
        imovel_dados['dt_comdesc'] = '-'
        imovel_dados['acesso_planilha'] = '-'

    # 🎯 QUERY ISOLADA: Usa o id_real para histórico atual e imovel_num para histórico mensal geral
    query_combinada = """
        SELECT h.id as id_obs, h.data_hora AS data_registro, h.usuario AS coletado_por, h.texto AS status_liquidacao
        FROM historico_observacoes h
        WHERE h.imovel_id = ?

        UNION ALL

        SELECT NULL as id_obs, p.data_hora AS data_registro, p.usuario AS coletado_por, 'Ação do Sistema: ' || p.acao AS status_liquidacao
        FROM produtividade p
        WHERE p.imovel_id = ?
          AND p.acao IN ('COLETOU BOLETO', 'PAGOU BOLETO', 'PAGOU BOLETO NA 275', 'ALTEROU STATUS PARA PAGO NA ADM')

        UNION ALL

        SELECT NULL as id_obs, hm.data_registro AS data_registro, hm.coletado_por AS coletado_por, hm.status_liquidacao AS status_liquidacao
        FROM historico_mensal_imoveis hm
        WHERE hm.imovel = ?

        ORDER BY data_registro DESC
    """
    
    # 🎯 Passa o id_real duas vezes, e depois o imovel_num
    historico_total = conexao.execute(query_combinada, (id_real, id_real, imovel_num)).fetchall()
    
    lista_historico = []
    for h in historico_total:
        lista_historico.append({
            'id_obs': h['id_obs'],
            'mes_ano': '-', 
            'status_liquidacao': h['status_liquidacao'], 
            'coletado_por': h['coletado_por'],
            'data_registro': h['data_registro']
        })

    conexao.close()
    
    return jsonify({
        'sucesso': True,
        'imovel': imovel_dados,
        'historico': lista_historico
    })


# --- ROTAS DE HISTÓRICO DE VIRADA DE MÊS ---

@app.route('/api/sistema/mes_ativo_detectado', methods=['GET'])
@login_obrigatorio
def api_mes_ativo_detectado():
    """Informa o mês da base ativa (mês de vencimento dominante) e se ele já foi arquivado."""
    conexao = conectar_banco()
    try:
        virada_mes.garantir_estrutura_virada(conexao)
        conexao.commit()
        return jsonify(virada_mes.detectar_mes_ativo(conexao))
    finally:
        conexao.close()

@app.route('/api/arquivar_mes', methods=['POST'])
@login_obrigatorio
def arquivar_mes():
    """Arquivamento MANUAL (plano B). Na rotina normal a virada é automática no upload da planilha."""
    conexao = None
    try:
        dados = request.get_json(silent=True) or {}
        mes_referencia = (dados.get('mes_referencia') or '').strip()
        substituir = bool(dados.get('substituir'))
        confirmar_divergencia = bool(dados.get('confirmar_divergencia'))

        conexao = conectar_banco()
        virada_mes.garantir_estrutura_virada(conexao)
        conexao.commit()

        misturado = aviso_se_ha_mais_de_um_mes_ativo(conexao)
        if misturado:
            return jsonify({"status": "erro", "mensagem": misturado}), 409

        detectado = virada_mes.detectar_mes_ativo(conexao)
        if not mes_referencia:
            mes_referencia = detectado['rotulo']
        if not mes_referencia:
            return jsonify({"status": "erro", "mensagem": "Não consegui identificar o mês da base ativa. Selecione o mês manualmente."}), 400
        if virada_mes.rotulo_para_tupla(mes_referencia) is None:
            return jsonify({"status": "erro", "mensagem": "Mês de referência inválido."}), 400

        # Trava contra erro de digitação: arquivar Outubro com o nome de Setembro, por exemplo
        if detectado['rotulo'] and detectado['rotulo'] != mes_referencia and not confirmar_divergencia:
            return jsonify({"status": "divergente", "detectado": detectado['rotulo'],
                            "mensagem": f"A base ativa parece ser de {detectado['rotulo']}, mas você selecionou {mes_referencia}."}), 409

        conexao.execute('BEGIN IMMEDIATE')
        res = virada_mes.arquivar_base_ativa(conexao, mes_referencia, origem='MANUAL',
                                             usuario=session.get('usuario'), substituir=substituir)
        if res['status'] == 'ja_existe':
            conexao.rollback()
            return jsonify({"status": "ja_existe", "qtd": res['qtd'],
                            "mensagem": f"{mes_referencia} já foi arquivado ({res['qtd']} registros)."}), 409
        if res['status'] == 'vazio':
            conexao.rollback()
            return jsonify({"status": "erro", "mensagem": "Não há base ativa para arquivar."}), 400
        conexao.commit()
        return jsonify({"status": "sucesso",
                        "mensagem": f"Base de {mes_referencia} arquivada com sucesso ({res['qtd']} imóveis)! "
                                    f"Você já pode subir a base do mês seguinte."})
    except Exception as e:
        try:
            if conexao is not None:
                conexao.rollback()
        except Exception:
            pass
        return jsonify({"status": "erro", "mensagem": str(e)}), 500
    finally:
        if conexao is not None:
            try:
                conexao.close()
            except Exception:
                pass

@app.route('/api/consultar_historico/<path:mes_ref>', methods=['GET'])
@login_obrigatorio
def consultar_historico(mes_ref):
    conexao = conectar_banco()
    # Busca apenas os casos que ficaram pendentes ou específicos, ou tudo se preferir (aqui trazemos tudo)
    historico = conexao.execute('''
        SELECT imovel, administradora, vencimento, status_interno, responsavel 
        FROM historico_fechamento_mes 
        WHERE mes_referencia = ?
        ORDER BY administradora ASC
    ''', (mes_ref,)).fetchall()
    conexao.close()
    
    lista_historico = [dict(row) for row in historico]
    return jsonify(lista_historico)

@app.route('/api/meses_disponiveis_historico', methods=['GET'])
@login_obrigatorio
def meses_disponiveis_historico():
    try:
        conexao = conectar_banco()
        # Busca apenas os nomes dos meses que já foram salvos, sem repetir
        meses = conexao.execute('SELECT DISTINCT mes_referencia FROM historico_fechamento_mes ORDER BY id DESC').fetchall()
        conexao.close()
        
        lista_meses = [row['mes_referencia'] for row in meses]
        return jsonify(lista_meses)
    except Exception as e:
        return jsonify([])  

# =================================================================
# MATRIZ EMISSÃO x VENCIMENTO
# modo_visao:
#   'pendentes' -> tempo real, só o que ainda falta entregar
#   'todos'     -> base travada completa (inclui PAGO / COLETADO / PAGO NA 275 / PAGO NA ADM)
# =================================================================
_MATRIZ_STATUS_ENTREGUE = "('PAGO', 'COLETADO', 'PAGO NA 275', 'PAGO NA ADM')"

# Um único responsável por administradora. Evita contar o mesmo imóvel 2x
# caso exista vínculo repetido em vinculos_adm (o JOIN antigo duplicava).
# A comparação da administradora ignora acento e maiúscula (vinculo_de, registrada em conectar_banco): a planilha traz
# 'Administração' e o vínculo está gravado como 'ADMINISTRACAO' — comparar com UPPER() do SQLite não casava e o boleto
# caía em 'Não Atribuído' mesmo com a administradora vinculada.
_MATRIZ_SQL_RESP = """IFNULL(NULLIF(TRIM((
    SELECT v.responsavel FROM vinculos_adm v WHERE v.rowid = vinculo_de(i.administradora)
)), ''), 'Não Atribuído')"""

_MATRIZ_SQL_BASE = "FROM imoveis i WHERE i.ativo = 1 AND i.administradora IS NOT NULL AND TRIM(i.administradora) != ''"

def _matriz_filtro_status(modo_visao):
    """Fragmento SQL (começa com AND). Em 'todos' não filtra status nenhum."""
    if modo_visao == 'todos':
        return ""
    return (" AND (i.status_interno IS NULL OR TRIM(i.status_interno) = '' "
            f"OR UPPER(TRIM(i.status_interno)) NOT IN {_MATRIZ_STATUS_ENTREGUE})")

# -----------------------------------------------------------------------------------------
# BASE TOTAL OFICIAL
# Cada upload de planilha faz UPDATE imoveis SET ativo = 0 e reinsere só quem AINDA consta na planilha.
# Quem foi pago some da planilha nova -> some da base ativa, mas continua na meta travada (ex.: 5.126).
# Para a "Base Total" bater com a meta, trazemos de volta os imóveis do mesmo ciclo (mês/ano de vencimento)
# cuja última linha ficou ativo = 0, ou seja, que saíram da base porque foram pagos. Eles entram como entregues.
# -----------------------------------------------------------------------------------------
_MATRIZ_SQL_SAIDOS = f"""SELECT o.imovel, o.administradora, o.vencimento, o.media_emissao,
    CASE WHEN UPPER(TRIM(IFNULL(o.status_interno, ''))) IN {_MATRIZ_STATUS_ENTREGUE}
         THEN o.status_interno ELSE 'SAIU DA BASE (PAGO)' END AS status_interno,
    1 AS saiu_da_base
FROM imoveis o
WHERE o.ativo = 0
  AND o.administradora IS NOT NULL AND TRIM(o.administradora) != ''
  AND SUBSTR(TRIM(IFNULL(o.vencimento, '')), 4, 7) = ?
  AND o.id = (SELECT MAX(o2.id) FROM imoveis o2 WHERE o2.imovel = o.imovel)"""

def _matriz_ciclo_alvo(conexao):
    """Mês/ano de vencimento ('mm/aaaa') dominante na base ativa = ciclo que a meta travada representa."""
    r = conexao.execute(
        f"SELECT SUBSTR(TRIM(i.vencimento), 4, 7) AS mes_ano, COUNT(*) AS q {_MATRIZ_SQL_BASE} "
        "AND LENGTH(TRIM(IFNULL(i.vencimento, ''))) >= 10 GROUP BY 1 ORDER BY q DESC LIMIT 1"
    ).fetchone()
    return r['mes_ano'] if r and r['mes_ano'] else None

def _matriz_mapa_portal(conexao):
    """Origem (portal) de cada imóvel, lida 1x do cadastro mestre: {imovel sem zeros à esquerda: 'WINKER' | 'WEBWARE' | ...}.
    Portal vazio ou imóvel fora do cadastro = 'DIVERSOS'."""
    mapa = {}
    for r in conexao.execute("SELECT imovel, portal FROM cadastro_mestre_imoveis ORDER BY rowid").fetchall():
        if r['imovel'] is None:
            continue
        chave = str(r['imovel']).strip().lstrip('0')
        portal = str(r['portal'] or '').strip().upper() or 'DIVERSOS'
        # se houver duplicata (ex.: com e sem zeros à esquerda), prefere a que tem portal definido
        if chave not in mapa or mapa[chave] == 'DIVERSOS':
            mapa[chave] = portal
    return mapa

def _matriz_registrar_portal(conexao, mapa_portal):
    """Expõe o mapa ao SQLite como função portal_de(imovel) (rápido: evita JOIN com LTRIM nas duas pontas)."""
    conexao.create_function(
        "portal_de", 1,
        lambda imv: mapa_portal.get(str(imv).strip().lstrip('0'), 'DIVERSOS') if imv is not None else 'DIVERSOS'
    )

def _matriz_origem(modo_visao, mes_ano_ciclo):
    """Devolve (sql, params) de uma tabela derivada com as colunas imovel, administradora, vencimento,
    media_emissao, status_interno, saiu_da_base. Use como: FROM ({sql}) i WHERE 1=1"""
    ativos = ("SELECT i.imovel, i.administradora, i.vencimento, i.media_emissao, i.status_interno, "
              f"0 AS saiu_da_base {_MATRIZ_SQL_BASE}")
    if modo_visao == 'todos':
        if mes_ano_ciclo:
            return f"{ativos} UNION ALL {_MATRIZ_SQL_SAIDOS}", [mes_ano_ciclo]
        return ativos, []
    return f"{ativos} {_matriz_filtro_status('pendentes')}", []

@app.route('/api/relatorio_matriz', methods=['POST'])
@login_obrigatorio
def api_relatorio_matriz():
    try:
        dados = request.get_json() or {}
        responsaveis_filtro = dados.get('responsaveis', [])
        modo_visao = 'todos' if dados.get('modo_visao') == 'todos' else 'pendentes'
        
        conexao = conectar_banco()
        
        # Puxa os responsáveis para desenhar os filtros
        resp_query = "SELECT DISTINCT IFNULL(TRIM(responsavel), 'Não Atribuído') as resp FROM vinculos_adm WHERE responsavel IS NOT NULL AND TRIM(responsavel) != ''"
        lista_resp = [row['resp'] for row in conexao.execute(resp_query).fetchall()]
        
        # 🎯 Respeita o modo: pendentes (tira pagos/coletados) ou todos (Base Total OFICIAL = ativa + quem já saiu da planilha)
        mes_ano_ciclo = _matriz_ciclo_alvo(conexao)
        origem_sql, origem_params = _matriz_origem(modo_visao, mes_ano_ciclo)

        # 🎯 ORIGEM do boleto (portal do cadastro mestre: WINKER, WEBWARE, DIVERSOS...)
        _matriz_registrar_portal(conexao, _matriz_mapa_portal(conexao))

        query = f"""
            SELECT
                IFNULL(i.media_emissao, '0') as emissao,
                IFNULL(SUBSTR(TRIM(i.vencimento), 1, 2), '0') as vencimento,
                IFNULL(SUBSTR(TRIM(i.vencimento), 4, 2), '0') as mes_vencimento,
                {_MATRIZ_SQL_RESP} as responsavel,
                portal_de(i.imovel) as portal,
                COUNT(*) as qtd
            FROM ({origem_sql}) i WHERE 1=1
        """
        params = list(origem_params)

        if responsaveis_filtro:
            placeholders = ', '.join(['?'] * len(responsaveis_filtro))
            query += f" AND {_MATRIZ_SQL_RESP} IN ({placeholders})"
            params.extend(responsaveis_filtro)

        query += " GROUP BY 1, 2, 3, 4, 5"
        
        resultados = conexao.execute(query, params).fetchall()
        dados_formatados = [dict(row) for row in resultados]

        # ---------------------------------------------------------
        # 🔎 VALIDAÇÃO: base travada (meta) x base ativa x entregues x pendentes
        # (sempre global, sem filtro de operador)
        # ---------------------------------------------------------
        total_base = conexao.execute(f"SELECT COUNT(*) {_MATRIZ_SQL_BASE}").fetchone()[0]
        total_pendentes = conexao.execute(f"SELECT COUNT(*) {_MATRIZ_SQL_BASE} {_matriz_filtro_status('pendentes')}").fetchone()[0]
        # Quem saiu da planilha ao longo do mês (foi pago) e continua na meta travada
        total_saidos = 0
        if mes_ano_ciclo:
            total_saidos = conexao.execute(f"SELECT COUNT(*) FROM ({_MATRIZ_SQL_SAIDOS})", [mes_ano_ciclo]).fetchone()[0]
        base_oficial = total_base + total_saidos
        meta_travada = 0
        try:
            for r in conexao.execute("SELECT valor FROM config_sistema WHERE chave LIKE 'meta_dia_%'").fetchall():
                if str(r['valor']).strip().isdigit():
                    meta_travada += int(r['valor'])
        except Exception:
            meta_travada = 0

        # ---------------------------------------------------------
        # 🔒 BASE CONGELADA DO CICLO
        # Vem de bases_congeladas(), a MESMA fonte do relatório de produtividade. É ela que
        # sabe que novembro/2026 vale pela 2ª base (5126) e que os outros meses valem pela
        # primeira planilha do ciclo. Assim a matriz e o relatório falam o mesmo número, e a
        # regra mora em um lugar só — mexer aqui não faz os dois discordarem.
        # ---------------------------------------------------------
        base_congelada, origem_base, chave_ciclo = 0, '', ''
        try:
            if mes_ano_ciclo and len(str(mes_ano_ciclo)) == 7:
                chave_ciclo = f'{str(mes_ano_ciclo)[3:]}-{str(mes_ano_ciclo)[:2]}'   # '11/2026' -> '2026-11'
                congeladas = bases_congeladas(conexao, guardar=False)
                base_congelada = int(congeladas.get(chave_ciclo) or 0)
                origem_base = (congeladas.get('_origem') or {}).get(chave_ciclo, '')
        except Exception as erro:
            print(f'⚠️ Matriz: não consegui ler a base congelada do ciclo: {erro}', flush=True)

        conexao.close()

        # ---------------------------------------------------------
        # 🎯 DESCOBRIR OS MESES REAIS DA BASE
        # ---------------------------------------------------------
        # 1. Conta qual é o mês de vencimento dominante na base atual
        contagem_meses = {}
        for row in dados_formatados:
            mv = row['mes_vencimento']
            if mv and mv != '0':
                contagem_meses[mv] = contagem_meses.get(mv, 0) + int(row['qtd'])
        
        if contagem_meses:
            # Pega o mês que tem mais boletos (o mês "Alvo" do vencimento)
            mes_principal = int(max(contagem_meses, key=contagem_meses.get))
        else:
            # Fallback de segurança para o mês atual do calendário caso a base esteja vazia
            from datetime import datetime
            mes_principal = int(datetime.now().strftime('%m'))
            
        # 2. Define o mês de emissão (mês anterior) e o mês de vencimento
        mes_anterior = mes_principal - 1 if mes_principal > 1 else 12
        
        meses_traducao = {
            1: 'Janeiro', 2: 'Fevereiro', 3: 'Março', 4: 'Abril', 
            5: 'Maio', 6: 'Junho', 7: 'Julho', 8: 'Agosto', 
            9: 'Setembro', 10: 'Outubro', 11: 'Novembro', 12: 'Dezembro'
        }
        
        # 3. Monta a dupla perfeita (Ex: se o alvo é Setembro, retorna ['Agosto', 'Setembro'])
        nomes_meses_ativos = [meses_traducao[mes_anterior], meses_traducao[mes_principal]]

        return jsonify({
            'sucesso': True, 
            'dados': dados_formatados, 
            'responsaveis_disponiveis': lista_resp,
            'meses_ativos': nomes_meses_ativos, # 🚀 Enviando a dupla de meses pro Frontend!
            'modo_visao': modo_visao,
            'resumo': {
                'meta_travada': meta_travada,
                'total_base': total_base,                 # base ativa hoje no sistema
                'saidos_da_base': total_saidos,           # saíram da planilha (pagos) e seguem na meta
                'base_oficial': base_oficial,             # ativa + saídos -> reconstrução a partir das linhas de hoje
                'base_congelada': base_congelada,         # 🔒 a base do mês que vale (a mesma do relatório)
                'origem_base': origem_base,               # de onde esse número veio, em português
                'ciclo': mes_ano_ciclo,
                'ciclo_chave': chave_ciclo,
                'pendentes': total_pendentes,
                'entregues': base_oficial - total_pendentes
            }
        })        
    except Exception as e:
        print(f"ERRO CRÍTICO NA MATRIZ: {str(e)}")
        return jsonify({'sucesso': False, 'msg': str(e)})

# -----------------------------------------------------------------------------------------
# DIAGNÓSTICO: por que a Base oficial (ativa + saídos) não fecha com a meta travada?
# Abra logado:  /api/matriz/diagnostico_base
# - por_dia: meta_dia_XX x (ativos + saídos) -> mostra em QUAL dia de vencimento está a diferença
# - candidatos_perdidos: boletos (imóvel + vencimento) do mesmo ciclo que saíram da planilha e NÃO
#   entram na conta porque a regra "uma linha por imóvel" (MAX(id)) descarta as demais linhas do mesmo imóvel
# - ativos_sem_administradora: linhas ativas ignoradas por administradora vazia
# -----------------------------------------------------------------------------------------
@app.route('/api/matriz/diagnostico_base')
@login_obrigatorio
def api_matriz_diagnostico_base():
    conexao = conectar_banco()
    try:
        ciclo = _matriz_ciclo_alvo(conexao)

        metas = {}
        for r in conexao.execute("SELECT chave, valor FROM config_sistema WHERE chave LIKE 'meta_dia_%'").fetchall():
            if str(r['valor']).strip().isdigit():
                metas[r['chave'].replace('meta_dia_', '')] = int(r['valor'])

        ativos_dia = {r['dia']: r['q'] for r in conexao.execute(
            f"SELECT SUBSTR(TRIM(i.vencimento), 1, 2) AS dia, COUNT(*) AS q {_MATRIZ_SQL_BASE} GROUP BY 1").fetchall()}
        saidos_dia = {}
        if ciclo:
            saidos_dia = {r['dia']: r['q'] for r in conexao.execute(
                f"SELECT SUBSTR(TRIM(vencimento), 1, 2) AS dia, COUNT(*) AS q FROM ({_MATRIZ_SQL_SAIDOS}) GROUP BY 1", [ciclo]).fetchall()}

        por_dia, tot_meta, tot_of = [], 0, 0
        for dia in sorted(set(metas) | set(ativos_dia) | set(saidos_dia), key=lambda x: int(x) if str(x).isdigit() else 99):
            m, a, sd = metas.get(dia, 0), ativos_dia.get(dia, 0), saidos_dia.get(dia, 0)
            tot_meta += m
            tot_of += a + sd
            if m != a + sd:
                por_dia.append({'dia': dia, 'meta': m, 'ativos': a, 'saidos': sd, 'diferenca': (a + sd) - m})

        perdidos = []
        if ciclo:
            perdidos = [dict(r) for r in conexao.execute("""
                SELECT o.imovel, TRIM(o.vencimento) AS vencimento, o.administradora, o.status_interno,
                       COUNT(*) AS linhas_no_historico
                FROM imoveis o
                WHERE o.ativo = 0
                  AND o.administradora IS NOT NULL AND TRIM(o.administradora) != ''
                  AND SUBSTR(TRIM(IFNULL(o.vencimento, '')), 4, 7) = ?
                  AND o.id != (SELECT MAX(o2.id) FROM imoveis o2 WHERE o2.imovel = o.imovel)
                  AND NOT EXISTS (SELECT 1 FROM imoveis a WHERE a.ativo = 1 AND a.imovel = o.imovel
                                  AND TRIM(a.vencimento) = TRIM(o.vencimento))
                  AND NOT EXISTS (SELECT 1 FROM imoveis m WHERE m.imovel = o.imovel AND m.ativo = 0
                                  AND m.id = (SELECT MAX(x.id) FROM imoveis x WHERE x.imovel = o.imovel)
                                  AND TRIM(m.vencimento) = TRIM(o.vencimento))
                GROUP BY o.imovel, TRIM(o.vencimento)
                ORDER BY o.imovel""", [ciclo]).fetchall()]

        sem_adm = conexao.execute(
            "SELECT COUNT(*) FROM imoveis WHERE ativo = 1 AND (administradora IS NULL OR TRIM(administradora) = '')").fetchone()[0]

        return jsonify({
            'ciclo': ciclo,
            'meta_travada': tot_meta,
            'base_oficial': tot_of,
            'diferenca_total': tot_of - tot_meta,
            'dias_com_diferenca': por_dia,
            'qtd_candidatos_perdidos': len(perdidos),
            'candidatos_perdidos': perdidos,
            'ativos_sem_administradora': sem_adm,
        })
    except Exception as e:
        return jsonify({'sucesso': False, 'msg': str(e)}), 500
    finally:
        conexao.close()

@app.route('/api/matriz/detalhes_celula', methods=['POST'])
@login_obrigatorio
def api_matriz_detalhes_celula():
    try:
        dados = request.get_json() or {}
        emi = dados.get('emissao')
        ven = dados.get('vencimento')
        responsaveis = dados.get('responsaveis', [])
        modo_visao = 'todos' if dados.get('modo_visao') == 'todos' else 'pendentes'

        conexao = conectar_banco()
        
        # Busca os imóveis exatos que formam aquele número clicado (mesmo critério da matriz, inclusive a Base Total oficial)
        origem_sql, origem_params = _matriz_origem(modo_visao, _matriz_ciclo_alvo(conexao))
        query = f"""
            SELECT i.imovel, i.administradora, IFNULL(i.status_interno, 'EM BRANCO') as status_interno, 
                   {_MATRIZ_SQL_RESP} as responsavel, i.vencimento
            FROM ({origem_sql}) i WHERE 1=1
              AND CAST(IFNULL(i.media_emissao, '0') AS INTEGER) = ?
              AND CAST(IFNULL(SUBSTR(TRIM(i.vencimento), 1, 2), '0') AS INTEGER) = ?
        """
        params = list(origem_params) + [int(emi), int(ven)]

        # Aplica o mesmo filtro de operadores que está marcado na tela
        if responsaveis:
            placeholders = ', '.join(['?'] * len(responsaveis))
            query += f" AND {_MATRIZ_SQL_RESP} IN ({placeholders})"
            params.extend(responsaveis)
            
        query += " ORDER BY i.imovel"
        resultados = conexao.execute(query, params).fetchall()
        conexao.close()
        
        return jsonify({'sucesso': True, 'dados': [dict(row) for row in resultados]})
    except Exception as e:
        return jsonify({'sucesso': False, 'msg': str(e)})

@app.route('/api/sistema/transferir_inverter_carteira', methods=['POST'])
@login_obrigatorio
def transferir_inverter_carteira():
    dados = request.get_json() or {}
    op_a = dados.get('operador_a')
    op_b = dados.get('operador_b')
    acao = dados.get('acao') # 'transferir' ou 'inverter'

    if not op_a or not op_b:
        return jsonify({'sucesso': False, 'msg': 'Selecione ambos os operadores.'})
    if op_a == op_b:
        return jsonify({'sucesso': False, 'msg': 'Os operadores devem ser diferentes.'})

    conexao = conectar_banco()
    try:
        if acao == 'transferir':
            # Transfere tudo do Operador A para o Operador B
            conexao.execute("UPDATE vinculos_adm SET responsavel = ? WHERE responsavel = ?", (op_b, op_a))
            
        elif acao == 'inverter':
            # Usa uma flag temporária para fazer a troca simultânea perfeita sem misturar as bases
            placeholder_temp = "TEMP_SWAP_HOLD_2026"
            conexao.execute("UPDATE vinculos_adm SET responsavel = ? WHERE responsavel = ?", (placeholder_temp, op_a))
            conexao.execute("UPDATE vinculos_adm SET responsavel = ? WHERE responsavel = ?", (op_a, op_b))
            conexao.execute("UPDATE vinculos_adm SET responsavel = ? WHERE responsavel = ?", (op_b, placeholder_temp))
            
        conexao.commit()
        return jsonify({'sucesso': True})
    except Exception as e:
        return jsonify({'sucesso': False, 'msg': str(e)})
    finally:
        conexao.close()


@app.route('/api/sistema/adicionar_usuario', methods=['POST'])
@login_obrigatorio
def api_adicionar_usuario():
    usuario_atual = session.get('usuario', '').lower().strip()
    conexao = conectar_banco()
    eh_master = usuario_eh_gestor(usuario_atual)
    
    if not eh_master:
        conexao.close()
        return jsonify({'sucesso': False, 'msg': 'Acesso negado'}), 403
        
    dados = request.get_json() or {}
    novo_nome = str(dados.get('nome', '')).strip().lower()
    email = str(dados.get('email', '')).strip().lower()

    # Aceita o e-mail inteiro no campo do nome: 'henrique.miranda@lello.com.br' vira
    # usuário 'henrique' + e-mail guardado (é assim que os outros usuários já são chamados).
    if '@' in novo_nome:
        email = email or novo_nome
        novo_nome = novo_nome.split('@')[0].split('.')[0].strip()

    if not novo_nome:
        conexao.close()
        return jsonify({'sucesso': False, 'msg': 'Por favor, digite um nome válido.'})
    if email and not re.match(r'^[^@\s]+@[^@\s]+\.[a-zA-Z]{2,}$', email):
        conexao.close()
        return jsonify({'sucesso': False, 'msg': 'Esse e-mail não parece válido.'})

    try:
        conexao.execute("INSERT INTO usuarios_painel (nome, is_master) VALUES (?, 0)", (novo_nome,))
        conexao.commit()
        # Completa o cadastro AGORA (usuário para digitar e nome de exibição 'Henrique'), em vez
        # de esperar o próximo reinício do sistema — senão a pessoa entraria como 'henrique'.
        garantir_tabelas_login(conexao)
        criado = conexao.execute("SELECT id FROM usuarios_painel WHERE nome = ?", (novo_nome,)).fetchone()
        if criado and email:
            conexao.execute("UPDATE usuarios_painel SET email = ? WHERE id = ?", (email, criado[0]))
            conexao.commit()
        conexao.close()
        return jsonify({'sucesso': True, 'id': criado[0] if criado else None})
    except sqlite3.IntegrityError:
        conexao.close()
        return jsonify({'sucesso': False, 'msg': 'Este usuário já está cadastrado no sistema.'})
    except Exception as e:
        conexao.close()
        return jsonify({'sucesso': False, 'msg': str(e)})

@app.route('/api/sistema/remover_usuario', methods=['POST'])
@login_obrigatorio
def api_remover_usuario():
    usuario_atual = session.get('usuario', '').lower().strip()
    conexao = conectar_banco()
    eh_master = usuario_eh_gestor(usuario_atual)
    
    if not eh_master:
        conexao.close()
        return jsonify({'sucesso': False, 'msg': 'Acesso negado'}), 403
        
    dados = request.get_json() or {}
    id_usuario = dados.get('id')
    nome_usuario = dados.get('nome', '').lower().strip()
    
    if nome_usuario in ['ryan', 'marcio', 'paulo', 'alessandra']:
        conexao.close()
        return jsonify({'sucesso': False, 'msg': 'Por segurança, não é permitido remover um administrador padrão do sistema.'})
        
    try:
        conexao.execute("DELETE FROM usuarios_painel WHERE id = ?", (id_usuario,))
        conexao.commit()
        conexao.close()
        return jsonify({'sucesso': True})
    except Exception as e:
        conexao.close()
        return jsonify({'sucesso': False, 'msg': str(e)})

@app.route('/api/sistema/alterar_permissao_usuario', methods=['POST'])
@login_obrigatorio
def api_alterar_permissao_usuario():
    usuario_atual = session.get('usuario', '').lower().strip()
    conexao = conectar_banco()
    eh_master = usuario_eh_gestor(usuario_atual)
    
    if not eh_master:
        conexao.close()
        return jsonify({'sucesso': False, 'msg': 'Acesso negado'}), 403
        
    dados = request.get_json() or {}
    id_usuario = dados.get('id')
    tornar_master = int(dados.get('is_master', 0))
    nome_usuario = dados.get('nome', '').lower().strip()
    
    # Trava de segurança para os fundadores da plataforma
    if nome_usuario in ['ryan', 'marcio', 'paulo', 'alessandra'] and tornar_master == 0:
        conexao.close()
        return jsonify({'sucesso': False, 'msg': 'Operação Bloqueada! Não é permitido remover o status de Master dos administradores fundadores.'})
        
    try:
        conexao.execute("UPDATE usuarios_painel SET is_master = ? WHERE id = ?", (tornar_master, id_usuario))
        conexao.commit()
        conexao.close()
        return jsonify({'sucesso': True})
    except Exception as e:
        conexao.close()
        return jsonify({'sucesso': False, 'msg': str(e)})
    
import random

MESES_PT_CARREGAMENTO = {'01': 'Janeiro', '02': 'Fevereiro', '03': 'Março', '04': 'Abril', '05': 'Maio', '06': 'Junho',
                          '07': 'Julho', '08': 'Agosto', '09': 'Setembro', '10': 'Outubro', '11': 'Novembro', '12': 'Dezembro'}

def _descobrir_mes_trabalho(conexao):
    """
    Descobre qual MÊS DE VENCIMENTO (formato 'MM/AAAA') a equipe está
    trabalhando de fato agora, e não o mês do calendário: olha o
    vencimento dos boletos tocados no último dia com movimento
    registrado em produtividade e pega o mês de vencimento mais
    frequente nesse dia. Assim acompanha a equipe quando ela vira de
    um mês de vencimento pro outro (ex: terminando novembro e já
    adiantando dezembro), sem precisar supor "mês civil" ou "+1 mês".
    Retorna (mes_trabalho 'MM/AAAA', nome_mes_trabalho) ou (None, None)
    se não houver dados suficientes.
    """
    try:
        ultimo_dia = conexao.execute("SELECT MAX(substr(data_hora,1,10)) as d FROM produtividade").fetchone()
        if not ultimo_dia or not ultimo_dia['d']:
            return None, None
        linha_mes = conexao.execute("""
            SELECT substr(i.vencimento,4,7) as mesano, COUNT(*) as qtd
            FROM produtividade p
            JOIN imoveis i ON i.id = p.imovel_id
            WHERE substr(p.data_hora,1,10) = ? AND i.vencimento IS NOT NULL
            GROUP BY mesano ORDER BY qtd DESC LIMIT 1
        """, (ultimo_dia['d'],)).fetchone()
        if not linha_mes or not linha_mes['mesano']:
            return None, None
        mes_trabalho = linha_mes['mesano']  # 'MM/AAAA'
        nome_mes_trabalho = MESES_PT_CARREGAMENTO.get(mes_trabalho.split('/')[0])
        return mes_trabalho, nome_mes_trabalho
    except Exception as erro:
        print(f'⚠️ Erro ao descobrir mês de trabalho (curiosidades_carregamento): {erro}', flush=True)
        return None, None

# As frases da tela de carregamento rodam até 6 consultas (2 com JOIN produtividade x imoveis) - caro
# para calcular a cada tela carregada, já que esta tela recarrega inteira a cada filtro/atalho clicado.
# O "mês de trabalho" e os números de produtividade não mudam rápido o bastante pra valer a pena recalcular
# a cada F5/navegação: ficam em cache por alguns minutos (o texto é só distração, não precisa ser exato ao segundo).
SEGUNDOS_CACHE_CURIOSIDADES = 300
_CACHE_CURIOSIDADES = {'quando': 0.0, 'frases': None}


@app.route('/api/sistema/curiosidades_carregamento')
@login_obrigatorio
def api_curiosidades_carregamento():
    agora = time.time()
    if _CACHE_CURIOSIDADES['frases'] is not None and agora - _CACHE_CURIOSIDADES['quando'] < SEGUNDOS_CACHE_CURIOSIDADES:
        frases_em_cache = list(_CACHE_CURIOSIDADES['frases'])
        random.shuffle(frases_em_cache)
        return jsonify({'sucesso': True, 'frases': frases_em_cache})

    conexao = conectar_banco()
    hoje = datetime.now().strftime('%Y-%m-%d')
    mes_atual = datetime.now().strftime('%Y-%m')

    frases = []

    # 🎯 Mês de trabalho real (pelo vencimento dos boletos), não o mês civil
    mes_trabalho, nome_mes_trabalho = _descobrir_mes_trabalho(conexao)
    ref_mes_trabalho = f" de {nome_mes_trabalho}" if nome_mes_trabalho else " do mês"

    # 1. Top Coletor do mês de trabalho (pelo VENCIMENTO do boleto)
    if mes_trabalho:
        top_coletor = conexao.execute("""
            SELECT p.usuario, COUNT(*) as qtd
            FROM produtividade p
            JOIN imoveis i ON i.id = p.imovel_id
            WHERE p.acao = 'COLETOU BOLETO' AND substr(i.vencimento,4,7) = ?
            GROUP BY p.usuario ORDER BY qtd DESC LIMIT 1
        """, (mes_trabalho,)).fetchone()
    else:
        # Sem dados pra descobrir o mês de trabalho: cai no comportamento antigo (mês civil)
        top_coletor = conexao.execute("SELECT usuario, COUNT(*) as qtd FROM produtividade WHERE acao = 'COLETOU BOLETO' AND data_hora LIKE ? GROUP BY usuario ORDER BY qtd DESC LIMIT 1", (f"{mes_atual}%",)).fetchone()
    if top_coletor and top_coletor['qtd'] > 0:
        nome = str(top_coletor['usuario']).title()
        frases.append(f"🏆 Sabia que {nome} lidera a coleta{ref_mes_trabalho} com {top_coletor['qtd']} boletos coletados?")

    # 2. Top Disparos de E-mail Hoje
    top_email = conexao.execute("SELECT usuario, COUNT(*) as qtd FROM produtividade WHERE acao = 'DISPAROU EMAIL COBRANCA' AND data_hora LIKE ? GROUP BY usuario ORDER BY qtd DESC LIMIT 1", (f"{hoje}%",)).fetchone()
    if top_email and top_email['qtd'] > 0:
        nome = str(top_email['usuario']).title()
        frases.append(f"🚀 {nome} está voando: já notificou {top_email['qtd']} imóveis hoje!")

    # 3. Total Liquidados pela equipe no mês de trabalho (pelo VENCIMENTO do boleto)
    if mes_trabalho:
        total_pagos = conexao.execute("""
            SELECT COUNT(*) as qtd
            FROM produtividade p
            JOIN imoveis i ON i.id = p.imovel_id
            WHERE p.acao = 'PAGOU BOLETO' AND substr(i.vencimento,4,7) = ?
        """, (mes_trabalho,)).fetchone()
    else:
        total_pagos = conexao.execute("SELECT COUNT(*) as qtd FROM produtividade WHERE acao = 'PAGOU BOLETO' AND data_hora LIKE ?", (f"{mes_atual}%",)).fetchone()
    if total_pagos and total_pagos['qtd'] > 0:
        frases.append(f"💰 A equipe já registrou {total_pagos['qtd']} pagamentos{ref_mes_trabalho}. Que ritmo!")

    # 4. 🤖 Humanos x Máquinas (DADOS DOS AUTOMAÇÃOS HOJE)
    robos_hoje = conexao.execute("SELECT COUNT(*) as qtd FROM produtividade WHERE acao = 'COLETOU BOLETO' AND UPPER(usuario) LIKE '%AUTOMAÇÃO%' AND data_hora LIKE ?", (f"{hoje}%",)).fetchone()
    if robos_hoje and robos_hoje['qtd'] > 0:
        frases.append(f"🤖 Nossos automaçãos automáticos já coletaram {robos_hoje['qtd']} boletos hoje!")

    conexao.close()

    # 5. Mistura com algumas frases engraçadas fixas para sempre ter variedade
    frases_padrao = [
        " Tomando aquele café enquanto a base carrega...",
        " Otimizando os filtros para trazer os dados rapidinho...",
        "🙏 Rezando pro site da administradora não estar fora do ar..."
    ]

    todas_frases = frases + frases_padrao
    _CACHE_CURIOSIDADES.update(quando=agora, frases=list(todas_frases))
    random.shuffle(todas_frases) # Embaralha as frases

    return jsonify({'sucesso': True, 'frases': todas_frases})


import time
from werkzeug.utils import secure_filename

# Pasta onde os prints e comprovantes da ADM ficarão guardados
PASTA_ANEXOS_ADM = os.path.join(app.root_path, 'static', 'anexos_adm')
os.makedirs(PASTA_ANEXOS_ADM, exist_ok=True)

@app.route('/api/relatorio_finmail', methods=['POST'])
@login_obrigatorio
def api_relatorio_finmail():
    dados = request.get_json() or {}
    # Pega o mês selecionado na tela. Se não houver, usa o mês atual
    mes_escolhido = dados.get('mes', datetime.now().strftime('%m'))
    ano_atual = datetime.now().strftime('%Y')
    periodo = f"{ano_atual}-{mes_escolhido.zfill(2)}"

    conexao = conectar_banco()
    
    try:
        # 🎯 REAPROVEITAMENTO HISTÓRICO: Puxa o passado de coletas bem-sucedidas
        query_prod = "SELECT data_hora, imovel_id, 'Sucesso' as status, acao as detalhe FROM produtividade WHERE (UPPER(usuario) LIKE '%FINMAIL%' OR UPPER(usuario) LIKE '%ROB%') AND data_hora LIKE ? ORDER BY data_hora DESC"
        prod_raw = conexao.execute(query_prod, (f"{periodo}%",)).fetchall()
        
        # 🎯 REAPROVEITAMENTO HISTÓRICO: Puxa os logs de erros/avisos que o automação salvou nos prontuários
        query_obs = "SELECT data_hora, imovel_id, 'Análise' as status, texto as detalhe FROM historico_observacoes WHERE (UPPER(usuario) LIKE '%FINMAIL%' OR UPPER(usuario) LIKE '%ROB%') AND data_hora LIKE ? ORDER BY data_hora DESC"
        obs_raw = conexao.execute(query_obs, (f"{periodo}%",)).fetchall()
        
        # Puxa dados da tabela de auditoria (para novos registros futuros)
        try:
            query_aud = "SELECT data_hora, imovel_id, status, detalhe FROM auditoria_robos WHERE robo LIKE '%FINMAIL%' AND data_hora LIKE ? ORDER BY data_hora DESC"
            aud_raw = conexao.execute(query_aud, (f"{periodo}%",)).fetchall()
        except:
            aud_raw = []

        # Unifica todo o passado e presente do automação em um fluxo único
        logs_completos = [dict(r) for r in prod_raw] + [dict(r) for r in obs_raw] + [dict(r) for r in aud_raw]
        
        # Remove registros duplicados no mesmo segundo para manter a precisão matemática
        logs_unicos = []
        vistos = set()
        for log in logs_completos:
            chave = f"{log['imovel_id']}_{str(log['detalhe'])[:20]}_{log['data_hora'][:13]}" # Agrupa por hora/conteúdo
            if chave not in vistos:
                vistos.add(chave)
                logs_unicos.append(log)

        logs_unicos.sort(key=lambda x: x['data_hora'], reverse=True)
        
        # Contadores analíticos
        sucessos = 0
        erro_senha = 0
        fora_base = 0
        comunicados = 0
        atas = 0
        outras_falhas = 0
        
        # 🧠 MINERAÇÃO DE TEXTO: Classifica o passado do automação nas novas caixas operacionais
        for log in logs_unicos:
            txt = str(log['detalhe']).lower()
            status_upper = str(log['status']).upper()
            
            if 'coletou boleto' in txt or 'sucesso' in txt or status_upper == 'SUCESSO': 
                sucessos += 1
                log['status'] = 'Sucesso'
            elif 'senha' in txt or 'login' in txt or 'bloqueio' in txt or 'credencial' in txt: 
                erro_senha += 1
                log['status'] = 'Erro de Senha'
            elif 'fora da base' in txt or 'não pertence' in txt or 'não localizado' in txt: 
                fora_base += 1
                log['status'] = 'Fora da Base'
            elif 'comunicado' in txt or 'aviso' in txt: 
                comunicados += 1
                log['status'] = 'Comunicado'
            elif 'ata ' in txt or 'assembleia' in txt or 'reunião' in txt: 
                atas += 1
                log['status'] = 'Ata/Documento'
            else:
                outras_falhas += 1
                log['status'] = 'Falha Leitura'

        total_lidos = sucessos + erro_senha + fora_base + comunicados + atas + outras_falhas
        
    except Exception as e:
        print(f"🚨 Erro na mineração do AuditoriaMail: {e}")
        total_lidos = sucessos = erro_senha = fora_base = comunicados = atas = outras_falhas = 0
        logs_unicos = []
        
    conexao.close()
    
    return jsonify({
        'sucesso': True,
        'metricas': {
            'total': total_lidos,
            'sucessos': sucessos,
            'erro_senha': erro_senha,
            'fora_base': fora_base,
            'comunicados': comunicados,
            'atas': atas,
            'falhas': outras_falhas
        },
        'logs': logs_unicos[:500] # Limita em 500 para performance máxima da renderização
    })

@app.route('/api/sistema/status_pago_adm', methods=['POST'])
@login_obrigatorio
def status_pago_adm():
    imovel_id_str = request.form.get('id')
    motivo = request.form.get('motivo')
    usuario_atual = session.get('usuario', 'Sistema')
    data_hora_atual = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    if not imovel_id_str or not motivo:
        return jsonify({'sucesso': False, 'msg': 'O motivo é obrigatório.'})

    try:
        imovel_id = int(imovel_id_str)
    except:
        return jsonify({'sucesso': False, 'msg': 'ID inválido'}), 400

    arquivo = request.files.get('anexo')
    
    # 🎯 TRAVA NO BACKEND: Garante que o arquivo foi enviado antes de prosseguir!
    if not arquivo or arquivo.filename == '':
        return jsonify({'sucesso': False, 'msg': '⚠️ O comprovante em anexo é OBRIGATÓRIO para registrar o pagamento na ADM!'})
    
    texto_anexo = ""
    if arquivo and arquivo.filename:
        nome_seguro = secure_filename(arquivo.filename)
        nome_arquivo = f"adm_{imovel_id}_{int(time.time())}_{nome_seguro}"
        caminho = os.path.join(PASTA_ANEXOS_ADM, nome_arquivo)
        arquivo.save(caminho)
        texto_anexo = f" | Comprovante: <a href='/static/anexos_adm/{nome_arquivo}' target='_blank' class='badge bg-info text-white text-decoration-none'>Visualizar Anexo 📎</a>"

    try:
        conexao = conectar_banco()
        
        if imovel_id < 0:
            # 🧊 MODO HISTÓRICO (Retroativo)
            id_hist = abs(imovel_id)
            caso = conexao.execute('SELECT id, imovel, vencimento FROM historico_fechamento_mes WHERE id = ?', (id_hist,)).fetchone()
            
            if caso:
                num_imovel = caso['imovel']
                vencimento = caso['vencimento'] or ''
                
                conexao.execute('UPDATE historico_fechamento_mes SET status_interno = "PAGO NA ADM" WHERE id = ?', (id_hist,))
                
                id_boleto = id_ativo_do_boleto(conexao, num_imovel, vencimento)
                caso_ativo = {'id': id_boleto} if id_boleto else None
                if caso_ativo:
                    id_real = caso_ativo['id']
                    mensagem_prontuario = f"Alterado retroativamente (Histórico) para PAGO NA ADM. Motivo: {motivo}{texto_anexo}"
                    conexao.execute('INSERT INTO historico_observacoes (imovel_id, data_hora, usuario, texto) VALUES (?, ?, ?, ?)', (id_real, data_hora_atual, usuario_atual, mensagem_prontuario))
                    conexao.execute('INSERT INTO produtividade (usuario, acao, imovel_id, data_hora) VALUES (?, ?, ?, ?)', (usuario_atual, "ALTEROU STATUS PARA PAGO NA ADM", id_real, data_hora_atual))
                
                mes_ano = "Mês Vigente"
                if '/' in vencimento:
                    partes = vencimento.split('/')
                    if len(partes) >= 3:
                        meses_traducao = {'01': 'Janeiro', '02': 'Fevereiro', '03': 'Março', '04': 'Abril', '05': 'Maio', '06': 'Junho', '07': 'Julho', '08': 'Agosto', '09': 'Setembro', '10': 'Outubro', '11': 'Novembro', '12': 'Dezembro'}
                        mes_num = partes[1].zfill(2)
                        ano_str = partes[2]
                        if len(ano_str) == 2: ano_str = "20" + ano_str
                        mes_ano = f"{meses_traducao.get(mes_num, mes_num)}/{ano_str}"
                
                conexao.execute('INSERT INTO historico_mensal_imoveis (imovel, mes_ano, status_liquidacao, coletado_por, data_registro) VALUES (?, ?, ?, ?, ?)', 
                               (num_imovel, mes_ano, 'PAGO NA ADM', usuario_atual, data_hora_atual))
        else:
            # 🔥 MODO ATIVO (Mês atual)
            caso = conexao.execute('SELECT id, imovel, vencimento FROM imoveis WHERE id = ?', (imovel_id,)).fetchone()
            
            if caso:
                id_real = caso['id']
                num_imovel = caso['imovel']
                vencimento = caso['vencimento'] or ''
                
                marco_mud = marco_das_mudancas(conexao)
                marcar_status_do_boleto(conexao, caso, "PAGO NA ADM")
                assinar_mudancas(conexao, marco_mud, usuario_atual, [num_imovel])
                conexao.execute('UPDATE historico_fechamento_mes SET status_interno = "PAGO NA ADM" WHERE imovel = ? AND vencimento = ?', (num_imovel, vencimento))
                
                mensagem_prontuario = f"Alterado manualmente para PAGO NA ADM. Motivo: {motivo}{texto_anexo}"
                conexao.execute('INSERT INTO historico_observacoes (imovel_id, data_hora, usuario, texto) VALUES (?, ?, ?, ?)', (id_real, data_hora_atual, usuario_atual, mensagem_prontuario))
                conexao.execute('INSERT INTO produtividade (usuario, acao, imovel_id, data_hora) VALUES (?, ?, ?, ?)', (usuario_atual, "ALTEROU STATUS PARA PAGO NA ADM", id_real, data_hora_atual))
                
                mes_ano = "Mês Vigente"
                if '/' in vencimento:
                    partes = vencimento.split('/')
                    if len(partes) >= 3:
                        meses_traducao = {'01': 'Janeiro', '02': 'Fevereiro', '03': 'Março', '04': 'Abril', '05': 'Maio', '06': 'Junho', '07': 'Julho', '08': 'Agosto', '09': 'Setembro', '10': 'Outubro', '11': 'Novembro', '12': 'Dezembro'}
                        mes_num = partes[1].zfill(2)
                        ano_str = partes[2]
                        if len(ano_str) == 2: ano_str = "20" + ano_str
                        mes_ano = f"{meses_traducao.get(mes_num, mes_num)}/{ano_str}"
                
                conexao.execute('INSERT INTO historico_mensal_imoveis (imovel, mes_ano, status_liquidacao, coletado_por, data_registro) VALUES (?, ?, ?, ?, ?)', 
                               (num_imovel, mes_ano, 'PAGO NA ADM', usuario_atual, data_hora_atual))
                
        conexao.commit()
    except Exception as e:
        return jsonify({'sucesso': False, 'msg': str(e)})
    finally:
        conexao.close()
        
    return jsonify({'sucesso': True})

@app.route('/api/sistema/detalhe_pago_adm/<parametro>')
@login_obrigatorio
def detalhe_pago_adm(parametro):
    conexao = conectar_banco()
    try:
        imovel_clean = str(parametro).strip().lstrip('0')
        
        # 1. Busca no historico_observacoes ligando por id OU por imovel_id
        query = '''
            SELECT h.id, h.data_hora, h.usuario, h.texto 
            FROM historico_observacoes h
            LEFT JOIN imoveis i ON h.imovel_id = i.id
            WHERE (LTRIM(i.imovel, '0') = ? OR LTRIM(CAST(h.imovel_id AS TEXT), '0') = ?)
              AND UPPER(h.texto) LIKE '%PAGO NA ADM%'
            ORDER BY h.id DESC LIMIT 1
        '''
        obs = conexao.execute(query, (imovel_clean, imovel_clean)).fetchone()
        
        # 2. Fallback caso não haja JOIN direto (procura texto do imóvel no registro)
        if not obs:
            query_fallback = '''
                SELECT id, data_hora, usuario, texto 
                FROM historico_observacoes 
                WHERE UPPER(texto) LIKE '%PAGO NA ADM%' 
                  AND (texto LIKE ? OR imovel_id IN (SELECT id FROM imoveis WHERE LTRIM(imovel, '0') = ?))
                ORDER BY id DESC LIMIT 1
            '''
            obs = conexao.execute(query_fallback, (f"%{imovel_clean}%", imovel_clean)).fetchone()

        if obs:
            return jsonify({
                'sucesso': True,
                'usuario': obs['usuario'] or 'Sistema',
                'data_hora': obs['data_hora'] or '',
                'motivo': obs['texto'] or 'Sem detalhes informados.'
            })
            
        return jsonify({'sucesso': False, 'msg': f'Nenhum detalhe de PAGO NA ADM encontrado para o imóvel {imovel_clean}.'})
    except Exception as e:
        print(f"Erro detalhe_pago_adm: {e}")
        return jsonify({'sucesso': False, 'msg': str(e)}), 500
    finally:
        conexao.close()

@app.route('/api/sistema/status_pago_275', methods=['POST'])
@login_obrigatorio
def status_pago_275():
    imovel_id_str = request.form.get('id')
    motivo = request.form.get('motivo', '').strip()
    usuario_atual = session.get('usuario', 'Sistema')
    data_hora_atual = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    if not imovel_id_str or not motivo:
        return jsonify({'sucesso': False, 'msg': 'O motivo/texto é obrigatório.'})

    try:
        imovel_id = int(imovel_id_str)
    except:
        return jsonify({'sucesso': False, 'msg': 'ID inválido'}), 400

    arquivo = request.files.get('anexo')
    texto_anexo = ""
    
    # 🎯 COMO É OPCIONAL, SÓ PROCESSA SE O ARQUIVO REALMENTE TIVER SIDO ENVIADO
    if arquivo and arquivo.filename:
        nome_seguro = secure_filename(arquivo.filename)
        nome_arquivo = f"275_{imovel_id}_{int(time.time())}_{nome_seguro}"
        caminho = os.path.join(PASTA_ANEXOS_ADM, nome_arquivo) # Salvamos na mesma pasta para facilitar
        arquivo.save(caminho)
        texto_anexo = f" | Comprovante: <a href='/static/anexos_adm/{nome_arquivo}' target='_blank' class='badge bg-info text-white text-decoration-none'>Visualizar Anexo 📎</a>"

    try:
        conexao = conectar_banco()
        caso = conexao.execute('SELECT id, imovel, vencimento FROM imoveis WHERE id = ?', (imovel_id,)).fetchone()
        
        if caso:
            id_real = caso['id']
            num_imovel = caso['imovel']
            marco_mud = marco_das_mudancas(conexao)
            marcar_status_do_boleto(conexao, caso, "PAGO NA 275")
            assinar_mudancas(conexao, marco_mud, usuario_atual, [num_imovel])
            
            mensagem_prontuario = f"Alterado manualmente para PAGO NA 275. Motivo: {motivo}{texto_anexo}"
            
            conexao.execute('INSERT INTO historico_observacoes (imovel_id, data_hora, usuario, texto) VALUES (?, ?, ?, ?)', (id_real, data_hora_atual, usuario_atual, mensagem_prontuario))
            conexao.execute('INSERT INTO produtividade (usuario, acao, imovel_id, data_hora) VALUES (?, ?, ?, ?)', (usuario_atual, "ALTEROU STATUS PARA PAGO NA 275", id_real, data_hora_atual))
            
        conexao.commit()
    except Exception as e:
        return jsonify({'sucesso': False, 'msg': str(e)})
    finally:
        conexao.close()
        
    return jsonify({'sucesso': True})

@app.route('/matriz_emissao')
@login_obrigatorio
def matriz_emissao():
    # Rota que chama a nossa nova página dedicada e maravilhosa!
    return render_template('matriz.html')

# 👇 COLOQUE A ROTA NOVA EXATAMENTE AQUI 👇
@app.route('/api/matriz/gerar_via_planilha', methods=['POST'])
@login_obrigatorio
def api_matriz_gerar_via_planilha():
    if 'arquivo' not in request.files:
        return jsonify({'sucesso': False, 'msg': 'Nenhum arquivo enviado.'})
    
    arquivo = request.files['arquivo']
    if arquivo.filename == '':
        return jsonify({'sucesso': False, 'msg': 'Arquivo inválido.'})

    try:
        nome_seguro = secure_filename(arquivo.filename)
        caminho_temp = os.path.join(tempfile.gettempdir(), nome_seguro)
        arquivo.save(caminho_temp)

        # Lê a planilha pulando o cabeçalho padronizado da Lello
        if nome_seguro.endswith('.csv'): 
            df = pd.read_csv(caminho_temp, skiprows=2)
        else: 
            df = pd.read_excel(caminho_temp, skiprows=2)

        df.columns = df.columns.str.strip()
        df = df.dropna(subset=['Imovel'])

        conexao = conectar_banco()
        
        # 1. Puxa todos os responsáveis ativos
        vinculos = conexao.execute("SELECT administradora as adm, TRIM(responsavel) as resp FROM vinculos_adm WHERE responsavel IS NOT NULL AND TRIM(responsavel) != '' ORDER BY rowid").fetchall()
        mapa_resp = {}
        for v in vinculos:                                   # sem acento/maiúscula; havendo repetida, vale a mais antiga
            mapa_resp.setdefault(normalizar_texto(v['adm']), v['resp'])
        lista_resp = list(set(mapa_resp.values()))
        
        # 2. Puxa o "Cadastro Mestre" para descobrir a Média de Emissão de cada imóvel
        mestres = conexao.execute("SELECT imovel, media_emissao FROM cadastro_mestre_imoveis WHERE media_emissao IS NOT NULL AND media_emissao != ''").fetchall()
        mapa_media = {str(m['imovel']).lstrip('0'): m['media_emissao'] for m in mestres}
        mapa_portal = _matriz_mapa_portal(conexao)   # 🎯 origem (portal) de cada imóvel

        resultados_agrupados = {}
        contagem_meses = {}

        # 3. Varre a planilha inteira calculando as somas e cruzando com o banco
        for index, row in df.iterrows():
            num_imovel = str(row.get('Imovel', '')).replace('.0', '').strip().lstrip('0')
            if not num_imovel or num_imovel == 'nan': continue
            
            venc_bruto = str(row.get('Vencimento', '')).strip()
            adm = str(row.get('Administradora', '')).strip().upper()
            
            venc = venc_bruto
            dt_comdesc = str(row.get('Dt.ComDesc', '')).replace('nan', '').strip()
            if dt_comdesc and dt_comdesc.lower() not in ['nan', 'nat', 'none', '-', '']:
                if len(dt_comdesc) >= 10 and '-' in dt_comdesc:
                    try:
                        partes = dt_comdesc.split(' ')[0].split('-')
                        if len(partes) == 3: dt_comdesc = f"{partes[2]}/{partes[1]}/{partes[0]}"
                    except: pass
                elif ' ' in dt_comdesc:
                    dt_comdesc = dt_comdesc.split(' ')[0]
                venc = dt_comdesc
            else:
                if venc and len(venc) >= 10 and '-' in venc:
                    try:
                        partes = venc.split(' ')[0].split('-')
                        if len(partes) == 3: venc = f"{partes[2]}/{partes[1]}/{partes[0]}"
                    except: pass
                elif venc and ' ' in venc:
                    venc = venc.split(' ')[0]
                    
            if venc.lower() in ['nan', 'nat', 'none']: venc = ''
            
            dia_venc = venc[:2] if len(venc) >= 2 else '0'
            mes_venc = venc[3:5] if len(venc) >= 5 else '0'
            
            # Consulta a Média de Emissão e Responsável
            media_emissao = mapa_media.get(num_imovel, '0')
            if not media_emissao or str(media_emissao).strip() == '': media_emissao = '0'
            responsavel = mapa_resp.get(normalizar_texto(adm), 'Não Atribuído')
            
            portal = mapa_portal.get(num_imovel, 'DIVERSOS')

            chave = (str(media_emissao), dia_venc, mes_venc, responsavel, portal)
            resultados_agrupados[chave] = resultados_agrupados.get(chave, 0) + 1
            
            if mes_venc and mes_venc != '0':
                contagem_meses[mes_venc] = contagem_meses.get(mes_venc, 0) + 1

        dados_formatados = []
        for (emi, ven, m_ven, resp, portal), qtd in resultados_agrupados.items():
            dados_formatados.append({
                'emissao': emi,
                'vencimento': ven,
                'mes_vencimento': m_ven,
                'responsavel': resp,
                'portal': portal,
                'qtd': qtd
            })

        if contagem_meses:
            mes_principal = int(max(contagem_meses, key=contagem_meses.get))
        else:
            from datetime import datetime
            mes_principal = int(datetime.now().strftime('%m'))
            
        mes_anterior = mes_principal - 1 if mes_principal > 1 else 12
        meses_traducao = {1: 'Janeiro', 2: 'Fevereiro', 3: 'Março', 4: 'Abril', 5: 'Maio', 6: 'Junho', 7: 'Julho', 8: 'Agosto', 9: 'Setembro', 10: 'Outubro', 11: 'Novembro', 12: 'Dezembro'}
        nomes_meses_ativos = [meses_traducao[mes_anterior], meses_traducao[mes_principal]]

        conexao.close()
        os.remove(caminho_temp)

        return jsonify({
            'sucesso': True, 
            'dados': dados_formatados, 
            'responsaveis_disponiveis': lista_resp,
            'meses_ativos': nomes_meses_ativos
        })
        
    except Exception as e:
        return jsonify({'sucesso': False, 'msg': f"Erro ao processar planilha: {str(e)}"})
    
# =================================================================
# MÓDULO DE INTELIGÊNCIA ARTIFICIAL: COGNITIVE REPORT ASSISTANT
# =================================================================
@app.route('/ia_assistant', methods=['GET', 'POST'])
@login_obrigatorio
def ia_assistant():
    resultado_linhas = []
    colunas = []
    query_executada = ""
    pergunta_usuario = ""
    erro_ia = ""

    if request.method == 'POST':
        pergunta_usuario = request.form.get('pergunta', '').strip()
        
        if pergunta_usuario:
            API_KEY_GEMINI = os.getenv("GEMINI_API_KEY_APP")
            url_gemini = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={API_KEY_GEMINI}"
            
            # 🎯 Nota técnica: O Python captura a data exata do servidor no Brasil
            hoje_exato = datetime.now().strftime('%Y-%m-%d')
            mes_exato = datetime.now().strftime('%Y-%m')
            
           # 🧠 Prompt Blindado com Regras de Negócio e Cruzamento de Dados
            prompt_sistema = f"""
            Você é o Sistema de Análise de Dados da plataforma de cobrança Lello.
            Sua função é traduzir a pergunta do usuário para uma query SQL (SQLite) perfeita.

            ESTRUTURA DO BANCO DE DADOS:
            1. imoveis (id, imovel, vencimento, administradora, carteira, d_lote, empreendimento, endereco, sacado, cpf_cnpj, ativo, status_interno, observacao, dt_comdesc, acesso, media_emissao)
               - Contém a base mestre de cobranças.
            2. vinculos_adm (administradora, responsavel, email) 
               - Tabela CRÍTICA: Mapeia quem é o operador humano ('responsavel') dono/gestor da carteira de cada 'administradora'.
            3. produtividade (id, usuario, acao, imovel_id, data_hora) 
               - Registra estatísticas e histórico de quem (automação ou humano) fez o quê e quando.
            4. historico_observacoes (id, imovel_id, data_hora, usuario, texto)
            5. relacionamento (id, imovel, tipo, assunto, link, data_hora)

            REGRAS CRÍTICAS DE BUSCA (OBRIGATÓRIO):
            - BUSCA INTELIGENTE DE TEXTO: Os usuários não digitam nomes exatos. SEMPRE que buscar por administradoras (Ex: "BBZ", "Lello", "Neon"), responsáveis (Ex: "Ryan", "Jessica"), condomínios ou portais, use UPPER() e LIKE.
              * Exemplo de Pergunta: "Quem administra a bbz?"
              * Sua Query ideal: SELECT administradora AS nome_administradora, responsavel AS gestor_da_carteira FROM vinculos_adm WHERE UPPER(administradora) LIKE '%BBZ%'
            - JOINS: Se o usuário pedir os imóveis de um operador específico, você deve cruzar a tabela `imoveis` com a tabela `vinculos_adm` usando a coluna `administradora`. Ex: JOIN vinculos_adm v ON UPPER(TRIM(i.administradora)) = UPPER(TRIM(v.administradora)).
            - DATAS: Hoje é '{hoje_exato}'. O mês atual é '{mes_exato}'. 
              * Se pedir "hoje", use: data_hora LIKE '{hoje_exato}%'
              * Se pedir "este mês", use: data_hora LIKE '{mes_exato}%'
              * NUNCA use strftime() ou 'now'.
            - AÇÕES DA PRODUTIVIDADE: Capturar/baixar boleto é 'COLETOU BOLETO'.
            - ESTÉTICA: SEMPRE use 'AS' para dar nomes profissionais às colunas (Ex: SELECT responsavel AS operador_responsavel).

            Você só tem permissão para gerar comandos SELECT.
            Retorne APENAS a string SQL pura. Não use formatação Markdown (como ```sql ou ```), não explique o código. Retorne SOMENTE o código SQL.
            
            Pergunta do usuário: "{pergunta_usuario}"
            """
            
            payload = {"contents": [{"parts": [{"text": prompt_sistema}]}]}
            
            try:
                # 🎯 TEMPO AUMENTADO PARA 45 SEGUNDOS: Deixa a IA pensar com calma!
                resposta = requests.post(url_gemini, json=payload, timeout=45)
                dados_ia = resposta.json()
                
                # Mudado de 'contents' para 'candidates' conforme padrão da API
                if 'candidates' in dados_ia and dados_ia['candidates']:
                    query_executada = dados_ia['candidates'][0]['content']['parts'][0]['text'].strip()
                elif 'error' in dados_ia:
                    raise Exception(f"Erro na API do Gemini: {dados_ia['error']['message']}")
                else:
                    raise Exception("A API não retornou uma estrutura de texto válida.")
                    
                # Limpeza de segurança caso a IA insira markdown acidentalmente
                query_executada = query_executada.replace("```sql", "").replace("```", "").strip()
                
                # 🛡️ GUARDIÃO DE SEGURANÇA MÁXIMA (READ-ONLY)
                query_analise = query_executada.upper().strip()
                
                if not query_analise.startswith("SELECT"):
                    raise Exception("Ação Bloqueada! A IA tentou gerar um comando que não é de visualização.")
                
                palavras_proibidas = ["DROP", "DELETE", "UPDATE", "INSERT", "ALTER", "REPLACE", "CREATE"]
                if any(p in query_analise for p in palavras_proibidas):
                    raise Exception("Ação Bloqueada! Detectada tentativa de modificação no banco de dados.")
                
                conexao = conectar_banco()
                cursor = conexao.execute(query_executada)
                
                colunas = [desc[0] for cursor_desc in [cursor.description] if cursor_desc for desc in cursor_desc]
                resultado_linhas = [dict(row) for row in cursor.fetchall()]
                conexao.close()
                
            except requests.exceptions.Timeout:
                erro_ia = " O servidor da IA demorou muito para responder. Tente fazer a pergunta novamente!"
            except Exception as e:
                erro_ia = f"Não consegui processar a consulta operacional. Detalhe técnico: {str(e)}"

    return render_template('ia_assistant.html', 
                           pergunta=pergunta_usuario, 
                           colunas=colunas, 
                           resultados=resultado_linhas, 
                           query_sql=query_executada,
                           erro=erro_ia)

@app.route('/api/buscar_imovel_global', methods=['GET'])
@login_obrigatorio
def buscar_imovel_global():
    numero = request.args.get('numero', '').strip()
    if not numero:
        return jsonify([])
        
    conexao = conectar_banco()
    # Query idêntica à principal, mas caça o número exato em toda a base de dados
    query = '''
        SELECT i.*, v.responsavel as colaborador, v.email_adm
        FROM imoveis i
        LEFT JOIN vinculos_adm v ON i.administradora = v.administradora
        WHERE i.imovel LIKE ? AND i.ativo = 1
        LIMIT 10
    '''
    # Usa o LIKE para se o usuário digitar apenas o começo do número já ir prevendo o resultado
    rows = conexao.execute(query, (f"%{numero}%",)).fetchall()
    conexao.close()
    
    return jsonify([dict(r) for r in rows])


@app.route('/api/sistema/editar_observacao', methods=['POST'])
@login_obrigatorio
def api_editar_observacao():
    dados = request.get_json()
    id_obs = dados.get('id_obs')
    novo_texto = dados.get('texto')
    
    if not id_obs or not novo_texto:
        return jsonify({'sucesso': False, 'msg': 'Dados incompletos.'})
        
    try:
        conexao = conectar_banco()
        # Garante que vai salvar com uma tagzinha de que foi editado
        conexao.execute('UPDATE historico_observacoes SET texto = ? WHERE id = ?', (novo_texto.strip(), id_obs))
        conexao.commit()
        conexao.close()
        return jsonify({'sucesso': True})
    except Exception as e:
        return jsonify({'sucesso': False, 'msg': str(e)})

@app.route('/api/sistema/atualizar_acesso', methods=['POST'])
@login_obrigatorio
def api_atualizar_acesso():
    dados = request.get_json() or {}
    imovel_id = dados.get('id')
    novo_acesso = dados.get('acesso', '').strip()
    
    if not imovel_id:
        return jsonify({'sucesso': False, 'msg': 'ID do imóvel ausente.'})
        
    try:
        conexao = conectar_banco()
        # Faz a atualização direta na coluna de credenciais secretas do banco
        conexao.execute('UPDATE imoveis SET acesso = ? WHERE id = ?', (novo_acesso, imovel_id))
        conexao.commit()
        conexao.close()
        return jsonify({'sucesso': True})
    except Exception as e:
        print(f"Erro ao salvar acesso: {e}")
        return jsonify({'sucesso': False, 'msg': str(e)})

@app.route('/api/imovel/dados_completos/<imovel_numero>')
@login_obrigatorio
def api_dados_completos_imovel(imovel_numero):
    conexao = conectar_banco()
    # Puxa o histórico de TUDO que já subiu para este imóvel (do mais novo pro mais antigo)
    registros = conexao.execute('SELECT * FROM imoveis WHERE imovel = ? ORDER BY id DESC', (imovel_numero,)).fetchall()
    conexao.close()
    
    if not registros:
        return jsonify({'sucesso': False})
        
    # 🧠 MESCLA INTELIGENTE: Pega o registro de hoje e preenche os buracos (None) com dados do passado
    imovel_mestre = dict(registros[0])
    for reg in registros:
        for chave in imovel_mestre.keys():
            if imovel_mestre[chave] in [None, '', 'None', 'NaN']:
                imovel_mestre[chave] = reg[chave]
                
    return jsonify({'sucesso': True, 'imovel': imovel_mestre})

@app.route('/api/sistema/corrigir_nomes_maiusculos')
@login_obrigatorio
def corrigir_nomes_maiusculos():
    conexao = conectar_banco()
    
    # 1. Arruma o nome de todos os responsáveis na tabela mestre de vínculos
    vinculos = conexao.execute("SELECT administradora, responsavel FROM vinculos_adm").fetchall()
    for v in vinculos:
        if v['responsavel'] and v['responsavel'] != 'Não Atribuído':
            # A função .title() do Python força a primeira letra a ser Maiúscula e o resto minúscula
            nome_bonito = v['responsavel'].title() 
            conexao.execute("UPDATE vinculos_adm SET responsavel = ? WHERE administradora = ?", (nome_bonito, v['administradora']))
    
    # 2. Arruma os nomes até nos relatórios de meses passados que já foram fechados
    try:
        historico = conexao.execute("SELECT id, responsavel FROM historico_fechamento_mes").fetchall()
        for h in historico:
            if h['responsavel'] and h['responsavel'] != 'Não Atribuído':
                nome_bonito = h['responsavel'].title()
                conexao.execute("UPDATE historico_fechamento_mes SET responsavel = ? WHERE id = ?", (nome_bonito, h['id']))
    except:
        pass # Ignora se a tabela do histórico ainda estiver vazia
        
    conexao.commit()
    conexao.close()
    
    return """
        <div style='font-family: Arial; text-align: center; margin-top: 50px;'>
            <h1 style='color: #10b981;'>✔️ Mágica Feita com Sucesso!</h1>
            <p>Todos os nomes do banco de dados foram convertidos para o padrão (ex: Victor).<br>
            Você já pode fechar esta página e voltar para o sistema.</p>
        </div>
    """

@app.route('/api/sistema/importar_provisoria', methods=['POST'])
@login_obrigatorio
def api_importar_provisoria():
    if 'arquivo_prov' not in request.files: 
        return "Nenhum arquivo enviado", 400
        
    arquivo = request.files['arquivo_prov']
    if arquivo.filename == '': 
        return "Nome de arquivo inválido", 400

    try:
        nome_seguro = secure_filename(arquivo.filename)
        caminho_temp = os.path.join(os.getcwd(), nome_seguro)
        arquivo.save(caminho_temp)

        conexao = conectar_banco()
        agora = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        usuario_sistema = session.get('usuario', 'Sistema')
        sucessos = 0

        mapa_responsaveis = {}
        cursor = conexao.execute('''
            SELECT i.imovel, v.responsavel
            FROM imoveis i
            LEFT JOIN vinculos_adm v ON v.rowid = vinculo_de(i.administradora)
            WHERE i.ativo = 1
        ''')
        for row in cursor.fetchall():
            mapa_responsaveis[str(row['imovel'])] = row['responsavel'] if row['responsavel'] else "Não Atribuído"

        # Função interna para limpar o email blindada sem acento
        def limpar_email(email_cru, imovel_num):
            if pd.isna(email_cru) or not isinstance(email_cru, str) or email_cru.strip() == '':
                nome = mapa_responsaveis.get(imovel_num, 'Geral')
                nome_limpo = nome.strip().title() if nome else 'Geral'
                if nome_limpo == "Jéssica": nome_limpo = "Jessica"
                return nome_limpo
            
            parte_local = email_cru.split('@')[0]
            primeiro_nome = parte_local.split('.')[0]
            nome_limpo = primeiro_nome.title()
            
            if nome_limpo == "Jéssica": 
                nome_limpo = "Jessica"
            return nome_limpo

        dfs = pd.read_excel(caminho_temp, sheet_name=None)

        if 'JULHO - 01 a 04' in dfs:
            df1 = dfs['JULHO - 01 a 04']
            for idx, row in df1.iterrows():
                try:
                    imovel = str(row.iloc[3]).replace('.0', '').strip()
                    status = str(row.iloc[9]).strip().upper()
                    coletor_raw = row.iloc[10]
                    
                    if status == 'COLETADO':
                        operador = limpar_email(coletor_raw, imovel)
                        imovel_bd = conexao.execute('SELECT id FROM imoveis WHERE imovel = ? AND ativo = 1', (imovel,)).fetchone()
                        
                        if imovel_bd:
                            imovel_id = imovel_bd['id']
                            marco_mud = marco_das_mudancas(conexao)
                            conexao.execute('UPDATE imoveis SET status_interno = "COLETADO" WHERE id = ?', (imovel_id,))
                            assinar_mudancas(conexao, marco_mud, operador, [imovel])
                            conexao.execute("DELETE FROM produtividade WHERE imovel_id = ? AND acao = 'COLETOU BOLETO'", (imovel_id,))
                            conexao.execute('INSERT INTO produtividade (usuario, acao, imovel_id, data_hora) VALUES (?, ?, ?, ?)', (operador, "COLETOU BOLETO", imovel_id, agora))
                            conexao.execute('INSERT INTO historico_observacoes (imovel_id, data_hora, usuario, texto) VALUES (?, ?, ?, ?)', (imovel_id, agora, usuario_sistema, f"Status alterado para COLETADO (Importação Planilha Provisória) - Operador: {operador}"))
                            sucessos += 1
                except:
                    continue

        if 'JULHO - 05 a 31' in dfs:
            df2 = dfs['JULHO - 05 a 31']
            for idx, row in df2.iterrows():
                try:
                    imovel = str(row.iloc[3]).replace('.0', '').strip()
                    status = str(row.iloc[10]).strip().upper()
                    coletor_raw = row.iloc[11]
                    
                    if status == 'COLETADO':
                        operador = limpar_email(coletor_raw, imovel)
                        imovel_bd = conexao.execute('SELECT id FROM imoveis WHERE imovel = ? AND ativo = 1', (imovel,)).fetchone()
                        
                        if imovel_bd:
                            imovel_id = imovel_bd['id']
                            marco_mud = marco_das_mudancas(conexao)
                            conexao.execute('UPDATE imoveis SET status_interno = "COLETADO" WHERE id = ?', (imovel_id,))
                            assinar_mudancas(conexao, marco_mud, operador, [imovel])
                            conexao.execute("DELETE FROM produtividade WHERE imovel_id = ? AND acao = 'COLETOU BOLETO'", (imovel_id,))
                            conexao.execute('INSERT INTO produtividade (usuario, acao, imovel_id, data_hora) VALUES (?, ?, ?, ?)', (operador, "COLETOU BOLETO", imovel_id, agora))
                            conexao.execute('INSERT INTO historico_observacoes (imovel_id, data_hora, usuario, texto) VALUES (?, ?, ?, ?)', (imovel_id, agora, usuario_sistema, f"Status alterado para COLETADO (Importação Planilha Provisória) - Operador: {operador}"))
                            sucessos += 1
                except:
                    continue

        conexao.commit()
        conexao.close()
        os.remove(caminho_temp)
        
        return f"""
            <div style='font-family: Arial; text-align: center; margin-top: 50px;'>
                <h1 style='color: #10b981;'>✔️ Importação Concluída com Sucesso!</h1>
                <p>Foram importados e contabilizados <b>{sucessos}</b> imóveis como 'COLETADO'.<br>
                Toda a produtividade já foi enviada para os respectivos operadores.</p>
                <a href='/configuracoes' style='display: inline-block; margin-top: 20px; padding: 10px 20px; background-color: #0f172a; color: white; text-decoration: none; border-radius: 6px;'>Voltar ao Painel</a>
            </div>
        """
    except Exception as e:
        return f"Erro fatal ao processar a planilha provisória: {str(e)}", 500


# ==============================================================================
# ROTA: IMPORTAÇÃO DO RELATÓRIO DE LIQUIDAÇÕES (BAIXA EM LOTE)
# ==============================================================================
@app.route('/api/importar_baixas', methods=['POST'])
@login_obrigatorio
def importar_baixas():
    if 'arquivo' not in request.files:
        return jsonify({'sucesso': False, 'msg': 'Nenhum arquivo enviado.'})
        
    arquivo = request.files['arquivo']
    if arquivo.filename == '':
        return jsonify({'sucesso': False, 'msg': 'Nenhum arquivo selecionado.'})

    try:
        # Lê o arquivo forçando TUDO para texto para não quebrar no Pandas
        if arquivo.filename.lower().endswith('.csv'):
            df = pd.read_csv(arquivo, sep=';', encoding='latin1', header=None, on_bad_lines='skip', dtype=str)
        else:
            df = pd.read_excel(arquivo, header=None, dtype=str)
            
        conexao = conectar_banco()
        cursor = conexao.cursor()
        
        agora = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        qtd_atualizados = 0

        # Dicionário para traduzir o mês para o Prontuário
        meses_traducao = {'01': 'Janeiro', '02': 'Fevereiro', '03': 'Março', '04': 'Abril', '05': 'Maio', '06': 'Junho', '07': 'Julho', '08': 'Agosto', '09': 'Setembro', '10': 'Outubro', '11': 'Novembro', '12': 'Dezembro'}

        for index, row in df.iterrows():
            try:
                # Proteção: Ignora cabeçalhos ou linhas que não chegam até a Coluna K
                if len(row) <= 10:
                    continue
                    
                imovel_raw = str(row[0]).replace('.0', '').strip() # Coluna A
                flag_raw = str(row[10]).strip().upper() # Coluna K
                
                # Só processa se a flag for 'L' e a coluna imóvel tiver números (ignora a linha de títulos)
                if flag_raw == 'L' and imovel_raw.isdigit():
                    
                    # 💡 MÁGICA DA CORREÇÃO: Removemos os zeros à esquerda para o cruzamento ser perfeito!
                    imovel_limpo = imovel_raw.lstrip('0')
                    
                    # Cruza os dados também ignorando os zeros no Banco de Dados
                    cursor.execute("SELECT id, imovel, vencimento FROM imoveis WHERE LTRIM(imovel, '0') = ? AND ativo = 1", (imovel_limpo,))
                    registro = cursor.fetchone()
                    
                    if registro:
                        imovel_id = registro['id']
                        venc_str = str(registro['vencimento']) if registro['vencimento'] else ''
                        
                        # Descobre o Mês/Ano para a Linha do Tempo (ex: Julho/2026)
                        mes_ano = "Mês Vigente"
                        if '/' in venc_str:
                            partes = venc_str.split('/')
                            if len(partes) >= 3:
                                mes_num = partes[1].zfill(2)
                                ano_str = partes[2]
                                if len(ano_str) == 2: ano_str = "20" + ano_str
                                mes_ano = f"{meses_traducao.get(mes_num, mes_num)}/{ano_str}"
                        
                        # 1. Atualiza status no painel
                        marco_mud = marco_das_mudancas(conexao)
                        cursor.execute("UPDATE imoveis SET status_interno = 'PAGO' WHERE id = ?", (imovel_id,))
                        assinar_mudancas(conexao, marco_mud, 'Sistema (Baixa Financeira)', [registro['imovel']])
                        
                        # 2. Salva na tabela específica da Linha do Tempo do Prontuário
                        usuario_origem = "Sistema (Baixa Financeira)"
                        cursor.execute("""
                            INSERT INTO historico_mensal_imoveis (imovel, mes_ano, status_liquidacao, coletado_por, data_registro)
                            VALUES (?, ?, ?, ?, ?)
                        """, (imovel_raw, mes_ano, 'PAGO', usuario_origem, agora))
                        
                        # 3. Salva no feed geral de observações (Prontuário)
                        texto_obs = "✔️ Liquidação identificada e processada através de importação da planilha financeira."
                        cursor.execute("INSERT INTO historico_observacoes (imovel_id, data_hora, usuario, texto) VALUES (?, ?, ?, ?)", 
                                       (imovel_id, agora, "SISTEMA", texto_obs))
                        
                        qtd_atualizados += 1
            except Exception as e:
                # Pula linha zoada e vai pra próxima
                continue

        conexao.commit()
        conexao.close()

        return jsonify({'sucesso': True, 'qtd': qtd_atualizados})

    except Exception as e:
        return jsonify({'sucesso': False, 'msg': f"Erro ao processar arquivo: {str(e)}"})

# ==============================================================================
# ⚙️ MOTOR DE AGENDAMENTO (APSCHEDULER)
# ==============================================================================
# Os horários ficam em config_sistema e são editados na tela de Automações.
# Mudou o horário lá, a agenda é refeita na hora — não precisa reiniciar o servidor.
# (chave usada na tela, id em STATUS_ROBOS, script, nome amigável)
ROBOS_AGENDAVEIS = [
    ('webware',    'WEBWARE',    'scripts/robos/robo_webware.py',        'Webware'),
    ('winker',     'WINKER',     'scripts/robos/robo_winker.py',         'Winker'),
    ('pagamentos', 'PAGAMENTOS', 'scripts/robos/robo_pagamentos.py',     'Pagamentos'),
    ('scanner',    'SCANNER',    'scripts/robos/robo_scanner_fisico.py', 'Scanner Físico'),
    ('superlogica', 'SUPERLOGICA', 'scripts/robos/robo_superlogica.py',  'Superlógica'),
    ('base_lello', 'BASE_LELLO',  'scripts/robos/robo_base_lello.py',     'Base Lello'),
]
FINMAIL_MINUTOS_PADRAO = 5
# Uma porta qualquer só para servir de tranca: o 1º processo que conseguir abri-la é o dono do agendador.
PORTA_TRAVA_AGENDADOR = int(os.getenv('PORTA_TRAVA_AGENDADOR', '47351'))

AGENDADOR = None
_TRAVA_AGENDADOR = None


def _hora_valida(texto):
    """'18:00' -> (18, 0). Devolve None para vazio ou valor inválido (= automação desligada)."""
    achou = re.match(r'^\s*(\d{1,2}):(\d{2})\s*$', str(texto or ''))
    if not achou:
        return None
    hora, minuto = int(achou.group(1)), int(achou.group(2))
    return (hora, minuto) if 0 <= hora <= 23 and 0 <= minuto <= 59 else None


# Um robô pode rodar várias vezes no dia: '09:00, 14:00, 18:00'. Quanto mais cedo o robô passa,
# menos caso de automação sobra para a equipe pegar na mão.
HORARIOS_PADRAO_3X = '09:00, 14:00, 18:00'
ROBOS_3X_AO_DIA = ('winker', 'webware')
MARCA_MIGRACAO_3X = 'agenda_3x_winker_webware_2026_11'


def _horas_validas(texto):
    """'09:00, 14:00 18h30' -> ([(9, 0), (14, 0), (18, 30)], []). O 2º item lista o que não deu para ler.
    Aceita vírgula, ponto e vírgula, barra, espaço ou quebra de linha entre os horários; '18h' vale 18:00."""
    horas, invalidos = [], []
    for pedaco in re.split(r'[,;/|\s]+', str(texto or '').strip()):
        if not pedaco:
            continue
        normal = pedaco.lower().replace('h', ':', 1)
        if normal.endswith(':'):
            normal += '00'
        if re.fullmatch(r'\d{1,2}', normal):      # '18' = 18:00
            normal += ':00'
        hora = _hora_valida(normal)
        if hora is None:
            invalidos.append(pedaco)
        elif hora not in horas:
            horas.append(hora)
    return sorted(horas), invalidos


def _texto_dos_horarios(horas):
    return ', '.join(f'{h:02d}:{m:02d}' for h, m in horas)


MARCA_AGENDA_BASE_LELLO = 'agenda_base_lello_2026_11'


def garantir_agenda_base_lello(conexao):
    """Uma vez só: o robô da base Lello passa a rodar 09h, 11h, 13h, 15h e 17h. Depois disso vale o que estiver
    gravado em config_sistema (horario_robo_base_lello); a marca impede que o padrão volte por cima."""
    try:
        if conexao.execute("SELECT 1 FROM config_sistema WHERE chave = ?", (MARCA_AGENDA_BASE_LELLO,)).fetchone():
            return
        conexao.execute("INSERT OR IGNORE INTO config_sistema (chave, valor) VALUES ('horario_robo_base_lello', ?)",
                        (HORARIOS_BASE_LELLO,))
        conexao.execute("INSERT OR REPLACE INTO config_sistema (chave, valor) VALUES (?, ?)",
                        (MARCA_AGENDA_BASE_LELLO, datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
        conexao.commit()
        print(f'⏰ [AGENDADOR] Base Lello agora roda {HORARIOS_BASE_LELLO}.', flush=True)
    except Exception as erro:
        print(f'⚠️ Não consegui aplicar a agenda da base Lello: {erro}', flush=True)


def garantir_agenda_3x(conexao):
    """Uma vez só: Winker e Webware passam a rodar 09h, 14h e 18h. Depois disso vale o que a
    equipe gravar na tela de Automações (a marca impede que a regra volte por cima)."""
    garantir_agenda_base_lello(conexao)
    try:
        if conexao.execute("SELECT 1 FROM config_sistema WHERE chave = ?", (MARCA_MIGRACAO_3X,)).fetchone():
            return
        for chave in ROBOS_3X_AO_DIA:
            conexao.execute("INSERT OR REPLACE INTO config_sistema (chave, valor) VALUES (?, ?)",
                            (f'horario_robo_{chave}', HORARIOS_PADRAO_3X))
        conexao.execute("INSERT OR REPLACE INTO config_sistema (chave, valor) VALUES (?, ?)",
                        (MARCA_MIGRACAO_3X, datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
        conexao.commit()
        print(f'⏰ [AGENDADOR] Winker e Webware agora rodam {HORARIOS_PADRAO_3X}.', flush=True)
    except Exception as erro:
        print(f'⚠️ Não consegui aplicar a agenda 3x ao dia: {erro}', flush=True)


def ler_agenda_salva(conexao=None):
    """O que está gravado: horário(s) de cada robô e de quanto em quanto tempo o FinMail roda."""
    fechar = conexao is None
    if fechar:
        conexao = conectar_banco()
    try:
        conexao.execute('CREATE TABLE IF NOT EXISTS config_sistema (chave TEXT PRIMARY KEY, valor TEXT)')
        garantir_agenda_3x(conexao)
        guardado = {r['chave']: str(r['valor'] or '').strip() for r in conexao.execute(
            "SELECT chave, valor FROM config_sistema WHERE chave LIKE 'horario_robo_%' "
            "OR chave IN ('intervalo_robo_finmail', 'horario_backup')").fetchall()}
    except Exception as erro:
        print(f'⚠️ Não consegui ler a agenda das automações: {erro}', flush=True)
        guardado = {}
    finally:
        if fechar:
            conexao.close()

    agenda = {chave: guardado.get(f'horario_robo_{chave}', '') for chave, _i, _s, _n in ROBOS_AGENDAVEIS}
    bruto = guardado.get('intervalo_robo_finmail', '')
    agenda['finmail_minutos'] = int(bruto) if bruto.isdigit() and int(bruto) > 0 else (
        0 if bruto.isdigit() else FINMAIL_MINUTOS_PADRAO)
    # backup: nunca gravado = usa o horário padrão; gravado vazio = desligado de propósito
    agenda['backup'] = guardado.get('horario_backup', HORA_BACKUP_PADRAO)
    return agenda


def disparar_robo_agendado(id_robo, script, nome, origem, args_extra=None):
    """Sobe o robô se ele não estiver rodando. Usado pelo agendador (e nunca dois ao mesmo tempo)."""
    if STATUS_ROBOS.get(id_robo, {}).get('rodando'):
        print(f'⚠️ [AGENDADOR] {nome} já está rodando; ignorando o disparo das {origem}.', flush=True)
        return False
    STATUS_ROBOS[id_robo]['rodando'] = True
    STATUS_ROBOS[id_robo]['sucessos'] = 0
    STATUS_ROBOS[id_robo]['falhas'] = 0
    STATUS_ROBOS[id_robo]['logs'] = []
    for extra in ('erro_senha', 'comunicados', 'atas'):
        if extra in STATUS_ROBOS[id_robo]:
            STATUS_ROBOS[id_robo][extra] = 0
    log_robo_console(id_robo, f'🚀 Início automático pelo agendador ({origem}).', 'info')
    print(f'⏰ [AGENDADOR] Subindo {nome} ({origem}).', flush=True)
    threading.Thread(target=executar_script_externo, args=(script, '', id_robo, args_extra), daemon=True).start()
    return True


def job_finmail_continuo():
    global FINMAIL_PAUSA_ATE
    if FINMAIL_PAUSA_ATE and datetime.now() < FINMAIL_PAUSA_ATE:
        print(f"⏸️ [AGENDADOR] FinMail pausado até {FINMAIL_PAUSA_ATE.strftime('%d/%m/%Y %H:%M')}. Ignorando ciclo.", flush=True)
        return
    disparar_robo_agendado('FINMAIL', 'scripts/robos/robo_email_geral.py', 'FinMail', 'escuta contínua')


def aplicar_agenda_dos_robos():
    """(Re)monta os agendamentos a partir do que está salvo. Chamada ao ligar o servidor
    e toda vez que alguém grava os horários na tela de Automações."""
    if AGENDADOR is None:
        return []
    agenda = ler_agenda_salva()
    resumo = []
    for chave, id_robo, script, nome in ROBOS_AGENDAVEIS:
        for job in list(AGENDADOR.get_jobs()):
            if job.id == f'robo_{chave}' or job.id.startswith(f'robo_{chave}__'):
                AGENDADOR.remove_job(job.id)
        horas, _invalidos = _horas_validas(agenda.get(chave))
        if not horas:
            resumo.append(f'{nome}: desligado')
            continue
        for hora in horas:
            # um job por horário: robo_winker__0900, robo_winker__1400...
            AGENDADOR.add_job(disparar_robo_agendado, trigger='cron', hour=hora[0], minute=hora[1],
                              id=f'robo_{chave}__{hora[0]:02d}{hora[1]:02d}', replace_existing=True,
                              coalesce=True, max_instances=1, misfire_grace_time=3600,
                              args=(id_robo, script, nome, f'{hora[0]:02d}:{hora[1]:02d}') +
                                   ((['--origem', f'robô {hora[0]:02d}:{hora[1]:02d}'],) if chave == 'base_lello' else ()))
        resumo.append(f'{nome}: {_texto_dos_horarios(horas)}')

    # 💾 Backup diário do banco, no mesmo agendador dos robôs
    if AGENDADOR.get_job('backup_banco'):
        AGENDADOR.remove_job('backup_banco')
    hora_bkp = _hora_valida(agenda.get('backup') or HORA_BACKUP_PADRAO)
    if hora_bkp:
        AGENDADOR.add_job(fazer_backup_banco, trigger='cron', hour=hora_bkp[0], minute=hora_bkp[1],
                          id='backup_banco', replace_existing=True, coalesce=True, max_instances=1,
                          misfire_grace_time=7200, args=('agendado', 'Sistema'))
        resumo.append(f'Backup: {hora_bkp[0]:02d}:{hora_bkp[1]:02d}')
    else:
        resumo.append('Backup: desligado')

    if AGENDADOR.get_job('robo_finmail'):
        AGENDADOR.remove_job('robo_finmail')
    minutos = agenda.get('finmail_minutos') or 0
    if minutos > 0:
        AGENDADOR.add_job(job_finmail_continuo, trigger='interval', minutes=minutos, id='robo_finmail',
                          replace_existing=True, coalesce=True, max_instances=1)
        resumo.append(f'FinMail: a cada {minutos} min')
    else:
        resumo.append('FinMail: desligado')
    print('⏰ [AGENDADOR] Agenda aplicada — ' + ' | '.join(resumo), flush=True)
    return resumo


# ==============================================================================
# 💾 BACKUP DO BANCO
# ==============================================================================
# Copiar banco.db no Explorer com o sistema rodando NÃO serve: o SQLite está em modo WAL,
# parte do que foi gravado vive no banco.db-wal e a cópia sai pela metade. O jeito certo é
# pedir ao próprio SQLite: 'VACUUM INTO' escreve um banco novo, inteiro e já compactado,
# mesmo com robô e equipe gravando no mesmo instante (medido: 0,5 s com 144 escritas em
# paralelo, integrity_check ok). Depois é comprimido: 130 MB viram ~38 MB.
#
# E tem a parte que quase todo mundo esquece: backup que nunca foi restaurado não é backup.
# Por isso toda cópia é conferida (integrity_check + contagem de linhas) antes de contar
# como válida, e a tela mostra o resultado dessa conferência — não só "deu certo".
PASTA_BACKUP_PADRAO = 'backups'
DIAS_GUARDA_BACKUP = 14          # além destes, fica 1 por mês
HORA_BACKUP_PADRAO = '12:30'
_TRAVA_BACKUP = threading.Lock()
_ESTADO_BACKUP = {'rodando': False, 'etapa': '', 'desde': None}


def _caminho_do_banco():
    """Onde o banco está DE VERDADE: pergunta para a própria conexão em vez de remontar
    o caminho na mão. Assim o backup copia exatamente o arquivo que o sistema está usando."""
    try:
        conexao = conectar_banco()
        try:
            for _seq, nome, arquivo in conexao.execute('PRAGMA database_list').fetchall():
                if nome == 'main' and arquivo:
                    return arquivo
        finally:
            conexao.close()
    except Exception:
        pass
    base_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(os.path.dirname(base_dir), 'banco.db')


def pasta_de_backup(conexao=None):
    """Onde os backups ficam. Dá para apontar para a rede em Automações; sem isso,
    uma pasta ao lado do banco (melhor que nada, pior que outro disco)."""
    destino = ''
    try:
        fechar = conexao is None
        conexao = conexao or conectar_banco()
        linha = conexao.execute("SELECT valor FROM config_sistema WHERE chave = 'pasta_backup'").fetchone()
        destino = str(linha['valor']).strip() if linha and linha['valor'] else ''
        if fechar:
            conexao.close()
    except Exception:
        destino = ''
    if not destino:
        destino = os.path.join(os.path.dirname(_caminho_do_banco()), PASTA_BACKUP_PADRAO)
    return destino


def _conferir_copia(caminho):
    """Abre a cópia e vê se ela presta. Devolve (ok, mensagem, contagens)."""
    try:
        conferencia = sqlite3.connect(f'file:{caminho}?mode=ro', uri=True, timeout=30)
        try:
            resultado = conferencia.execute('PRAGMA integrity_check').fetchone()[0]
            if str(resultado).lower() != 'ok':
                return False, f'integrity_check: {resultado}', {}
            contagens = {}
            for tabela in ('imoveis', 'cadastro_mestre_imoveis', 'vinculos_adm', 'historico_observacoes'):
                try:
                    contagens[tabela] = conferencia.execute(f'SELECT COUNT(*) FROM {tabela}').fetchone()[0]
                except sqlite3.OperationalError:
                    contagens[tabela] = None
            if not contagens.get('imoveis'):
                return False, 'a cópia abriu mas está sem imóveis', contagens
            return True, 'ok', contagens
        finally:
            conferencia.close()
    except Exception as erro:
        return False, f'não consegui abrir a cópia: {erro}', {}


def _limpar_backups_antigos(pasta):
    """Guarda os últimos DIAS_GUARDA_BACKUP dias e o primeiro backup de cada mês."""
    apagados = []
    try:
        arquivos = sorted(a for a in os.listdir(pasta) if a.startswith('banco_') and a.endswith('.db.gz'))
        limite = (datetime.now() - timedelta(days=DIAS_GUARDA_BACKUP)).strftime('%Y%m%d')
        primeiro_do_mes = {}
        for nome in arquivos:                       # banco_AAAAMMDD_HHMM.db.gz, já em ordem
            primeiro_do_mes.setdefault(nome[6:12], nome)
        for nome in arquivos:
            dia = nome[6:14]
            if primeiro_do_mes.get(dia[:6]) == nome:
                continue                            # o primeiro backup de cada mês fica guardado
            if dia >= limite:
                continue                            # dentro da janela dos últimos dias
            try:
                os.remove(os.path.join(pasta, nome))
                apagados.append(nome)
            except OSError:
                pass
    except Exception:
        pass
    return apagados


def _pasta_local_de_trabalho():
    """Rascunho SEMPRE no disco local, ao lado do banco."""
    pasta = os.path.join(os.path.dirname(_caminho_do_banco()), '_backup_em_andamento')
    os.makedirs(pasta, exist_ok=True)
    return pasta


def _varrer_parciais(pasta):
    """Tira restos de tentativas que não terminaram."""
    try:
        for nome in os.listdir(pasta):
            if nome.startswith('_parcial_') or nome.startswith('_teste_'):
                try:
                    os.remove(os.path.join(pasta, nome))
                except OSError:
                    pass
    except Exception:
        pass


def fazer_backup_banco(motivo='agendado', usuario='Sistema'):
    """Cópia consistente + conferida + comprimida + levada para a pasta de destino.

    O rascunho é feito NO DISCO LOCAL, nunca direto na pasta da rede. Mandar o SQLite
    escrever um banco de 130 MB direto num compartilhamento de rede é
    pedir problema: a rede não garante as travas de arquivo que o SQLite precisa, e a
    tentativa morre no meio deixando um _parcial lá. Para a rede vai só o .gz pronto,
    que é uma cópia de arquivo comum — isso o Windows faz bem.
    """
    if not _TRAVA_BACKUP.acquire(blocking=False):
        return {'sucesso': False, 'msg': 'Já existe um backup em andamento.'}
    inicio = time.time()
    relatorio = {'sucesso': False, 'motivo': motivo, 'usuario': usuario,
                 'quando': datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
    rascunho = comprimido_local = ''
    _ESTADO_BACKUP.update({'rodando': True, 'etapa': 'começando', 'desde': relatorio['quando']})
    try:
        origem = _caminho_do_banco()
        if not os.path.exists(origem):
            relatorio['msg'] = f'Não encontrei o banco em {origem}'
            return relatorio

        trabalho = _pasta_local_de_trabalho()
        _varrer_parciais(trabalho)

        carimbo = datetime.now().strftime('%Y%m%d_%H%M')
        rascunho = os.path.join(trabalho, f'_parcial_{carimbo}.db')
        comprimido_local = os.path.join(trabalho, f'banco_{carimbo}.db.gz')
        nome_final = f'banco_{carimbo}.db.gz'

        # 1) cópia consistente, no disco local, com o sistema rodando
        _ESTADO_BACKUP['etapa'] = 'copiando o banco'
        conexao = sqlite3.connect(origem, timeout=60)
        try:
            try:
                conexao.execute('VACUUM INTO ?', (rascunho,))
            except sqlite3.OperationalError:
                copia = sqlite3.connect(rascunho)      # SQLite antigo: dá no mesmo, só não compacta
                conexao.backup(copia)
                copia.close()
        finally:
            conexao.close()

        # 2) a cópia presta?
        _ESTADO_BACKUP['etapa'] = 'conferindo a cópia'
        boa, aviso, contagens = _conferir_copia(rascunho)
        relatorio['conferencia'] = aviso
        relatorio['contagens'] = contagens
        if not boa:
            relatorio['msg'] = f'A cópia saiu com problema e foi descartada ({aviso}).'
            return relatorio

        # 3) comprime (ainda local)
        _ESTADO_BACKUP['etapa'] = 'comprimindo'
        with open(rascunho, 'rb') as entrada, gzip.open(comprimido_local, 'wb', compresslevel=6) as saida:
            shutil.copyfileobj(entrada, saida, 1024 * 1024)
        os.remove(rascunho)
        rascunho = ''

        # 4) só agora vai para a pasta escolhida (rede, outro disco, o que for)
        _ESTADO_BACKUP['etapa'] = 'guardando na pasta de destino'
        pasta = pasta_de_backup()
        final = os.path.join(pasta, nome_final)
        guardado_na_rede = True
        try:
            os.makedirs(pasta, exist_ok=True)
            shutil.copyfile(comprimido_local, final)
            os.remove(comprimido_local)
            _varrer_parciais(pasta)                     # restos de versões anteriores
        except Exception as erro:
            guardado_na_rede = False
            final = comprimido_local                    # fica no disco local e a tela avisa
            comprimido_local = ''                       # ...e a limpeza do fim NÃO pode apagá-lo
            relatorio['aviso_pasta'] = (f'Não consegui gravar em "{pasta}" ({erro}). '
                                        f'O backup ficou em {final}.')
            print(f"⚠️ {relatorio['aviso_pasta']}", flush=True)

        relatorio.update({
            'sucesso': True, 'arquivo': final, 'pasta': os.path.dirname(final),
            'na_pasta_escolhida': guardado_na_rede,
            'tamanho_mb': round(os.path.getsize(final) / 1e6, 1),
            'tamanho_banco_mb': round(os.path.getsize(origem) / 1e6, 1),
            'segundos': round(time.time() - inicio, 1),
            'apagados': _limpar_backups_antigos(os.path.dirname(final)),
            'msg': 'Backup conferido e guardado.' if guardado_na_rede
                   else 'Backup feito, mas ficou no disco local.'})

        try:
            conexao = conectar_banco()
            conexao.execute('INSERT OR REPLACE INTO config_sistema (chave, valor) VALUES (?, ?)',
                            ('ultimo_backup', json.dumps(relatorio, ensure_ascii=False, default=str)))
            conexao.execute('INSERT INTO produtividade (usuario, acao, imovel_id, data_hora) VALUES (?, ?, ?, ?)',
                            (usuario, f'BACKUP BANCO|{os.path.basename(final)}', 0, relatorio['quando']))
            conexao.commit()
            conexao.close()
        except Exception as erro:
            print(f'⚠️ Backup feito, mas não consegui registrar: {erro}', flush=True)

        print(f"💾 Backup ok: {final} ({relatorio['tamanho_mb']} MB em {relatorio['segundos']}s)", flush=True)
        return relatorio
    except Exception as erro:
        relatorio['msg'] = f'Falhou: {erro}'
        print(f'🚨 Backup falhou: {erro}', flush=True)
        return relatorio
    finally:
        for sobra in (rascunho, comprimido_local):
            if sobra and os.path.exists(sobra):
                try:
                    os.remove(sobra)
                except OSError:
                    pass
        _ESTADO_BACKUP.update({'rodando': False, 'etapa': ''})
        try:
            conexao = conectar_banco()
            conexao.execute('INSERT OR REPLACE INTO config_sistema (chave, valor) VALUES (?, ?)',
                            ('ultimo_backup', json.dumps(relatorio, ensure_ascii=False, default=str)))
            conexao.commit()
            conexao.close()
        except Exception:
            pass
        _TRAVA_BACKUP.release()


def backups_existentes():
    """Lista o que está guardado, do mais novo para o mais velho."""
    pasta = pasta_de_backup()
    lista = []
    try:
        for nome in sorted(os.listdir(pasta), reverse=True):
            if not (nome.startswith('banco_') and nome.endswith('.db.gz')):
                continue
            caminho = os.path.join(pasta, nome)
            lista.append({'nome': nome, 'tamanho_mb': round(os.path.getsize(caminho) / 1e6, 1),
                          'quando': datetime.fromtimestamp(os.path.getmtime(caminho)).strftime('%d/%m/%Y %H:%M')})
    except Exception:
        pass
    return lista


def restaurar_para_conferir(nome_arquivo=''):
    """Descompacta um backup num arquivo à parte e confere se ele abre e tem os dados.
    NÃO toca no banco de produção: serve para provar que o backup presta de verdade."""
    pasta = pasta_de_backup()
    lista = backups_existentes()
    if not lista:
        return {'sucesso': False, 'msg': 'Não há nenhum backup guardado ainda.'}
    nome = nome_arquivo or lista[0]['nome']
    origem = os.path.join(pasta, nome)
    if not os.path.exists(origem):
        return {'sucesso': False, 'msg': f'Não encontrei {nome}.'}

    destino = os.path.join(_pasta_local_de_trabalho(), '_teste_restauracao.db')
    try:
        with gzip.open(origem, 'rb') as entrada, open(destino, 'wb') as saida:
            shutil.copyfileobj(entrada, saida, 1024 * 1024)
        boa, aviso, contagens = _conferir_copia(destino)
        return {'sucesso': boa, 'arquivo': nome, 'msg': aviso, 'contagens': contagens,
                'tamanho_mb': round(os.path.getsize(destino) / 1e6, 1)}
    except Exception as erro:
        return {'sucesso': False, 'arquivo': nome, 'msg': f'Não consegui restaurar: {erro}'}
    finally:
        for sobra in (destino, destino + '-wal', destino + '-shm'):
            if os.path.exists(sobra):
                try:
                    os.remove(sobra)
                except OSError:
                    pass


@app.route('/api/sistema/backup/estado')
@login_obrigatorio
def api_backup_estado():
    conexao = conectar_banco()
    try:
        ultimo, hora, pasta_config = {}, HORA_BACKUP_PADRAO, ''
        for chave, valor in conexao.execute(
                "SELECT chave, valor FROM config_sistema WHERE chave IN ('ultimo_backup','horario_backup','pasta_backup')").fetchall():
            if chave == 'ultimo_backup':
                try:
                    ultimo = json.loads(valor or '{}')
                except Exception:
                    ultimo = {}
            elif chave == 'horario_backup':
                hora = str(valor or '').strip()
            else:
                pasta_config = str(valor or '').strip()
    finally:
        conexao.close()
    return jsonify({'sucesso': True, 'ultimo': ultimo, 'horario': hora,
                    'pasta': pasta_de_backup(), 'pasta_configurada': pasta_config,
                    'rodando': bool(_ESTADO_BACKUP.get('rodando')),
                    'etapa': _ESTADO_BACKUP.get('etapa') or '',
                    'arquivos': backups_existentes()[:20]})


@app.route('/api/sistema/backup/agora', methods=['POST'])
@gestor_obrigatorio
def api_backup_agora():
    """Começa o backup e responde na hora. Antes a resposta só saía no fim: em pasta de
    rede lenta o navegador desistia antes e aparecia 'o servidor não respondeu', mesmo
    com o backup rodando. A tela acompanha pelo /estado."""
    if _ESTADO_BACKUP.get('rodando'):
        return jsonify({'sucesso': False, 'msg': 'Já existe um backup em andamento.'})
    quem = session.get('usuario', 'Sistema')
    threading.Thread(target=fazer_backup_banco, args=('manual', quem), daemon=True).start()
    return jsonify({'sucesso': True, 'iniciado': True, 'msg': 'Backup começou. Acompanhe aqui mesmo.'})


@app.route('/api/sistema/backup/conferir', methods=['POST'])
@gestor_obrigatorio
def api_backup_conferir():
    dados = request.get_json(silent=True) or {}
    return jsonify(restaurar_para_conferir(str(dados.get('arquivo') or '').strip()))


@app.route('/api/sistema/backup/configurar', methods=['POST'])
@gestor_obrigatorio
def api_backup_configurar():
    dados = request.get_json(silent=True) or {}
    hora = str(dados.get('horario') or '').strip()
    pasta = str(dados.get('pasta') or '').strip()
    if hora and not _hora_valida(hora):
        return jsonify({'sucesso': False, 'msg': 'Horário inválido. Use HH:MM, por exemplo 12:30.'})
    if pasta:
        try:
            os.makedirs(pasta, exist_ok=True)
            teste = os.path.join(pasta, '_teste_escrita.tmp')
            with open(teste, 'w') as f:
                f.write('ok')
            os.remove(teste)
        except Exception as erro:
            return jsonify({'sucesso': False, 'msg': f'Não consigo gravar em "{pasta}": {erro}'})

    conexao = conectar_banco()
    try:
        conexao.execute('CREATE TABLE IF NOT EXISTS config_sistema (chave TEXT PRIMARY KEY, valor TEXT)')
        conexao.execute('INSERT OR REPLACE INTO config_sistema (chave, valor) VALUES (?, ?)', ('horario_backup', hora))
        conexao.execute('INSERT OR REPLACE INTO config_sistema (chave, valor) VALUES (?, ?)', ('pasta_backup', pasta))
        conexao.commit()
    finally:
        conexao.close()
    # O que importa já foi gravado. Se o agendador engasgar, a tela não pode dizer
    # "não respondeu" para uma configuração que entrou.
    resumo, agenda = [], {}
    try:
        resumo = aplicar_agenda_dos_robos()
        agenda = proximas_execucoes()
        json.dumps(agenda)            # se a agenda não virar JSON, ela fica de fora e o resto vai
    except Exception as erro:
        agenda = {}
        if not resumo:
            resumo = [f'gravado, mas não consegui reaplicar a agenda agora: {erro}']
    return jsonify({'sucesso': True, 'resumo': resumo, 'pasta': pasta_de_backup(), 'agenda': agenda})


def _proxima_do_job(id_job):
    """Quando este agendamento roda de novo ('25/10 18:00'), ou None se não existe."""
    try:
        job = AGENDADOR.get_job(id_job) if AGENDADOR else None
        return job.next_run_time.strftime('%d/%m %H:%M') if (job and job.next_run_time) else None
    except Exception:
        return None


def _proxima_do_robo(chave):
    """O robô tem um job por horário (robo_winker__0900, __1400...): vale o que roda primeiro."""
    try:
        jobs = [j for j in (AGENDADOR.get_jobs() if AGENDADOR else [])
                if (j.id == f'robo_{chave}' or j.id.startswith(f'robo_{chave}__')) and j.next_run_time]
        if not jobs:
            return None
        return min(jobs, key=lambda j: j.next_run_time).next_run_time.strftime('%d/%m %H:%M')
    except Exception:
        return None


def proximas_execucoes():
    """Para a tela de Automações: quando cada automação roda de novo."""
    saida = {}
    agenda = ler_agenda_salva()
    for chave, _id, _s, nome in ROBOS_AGENDAVEIS:
        horas, _inv = _horas_validas(agenda.get(chave, ''))
        saida[chave] = {'nome': nome, 'horario': _texto_dos_horarios(horas),
                        'horarios': [f'{h:02d}:{m:02d}' for h, m in horas],
                        'proxima': _proxima_do_robo(chave)}
    saida['finmail'] = {'nome': 'FinMail', 'minutos': agenda.get('finmail_minutos', 0),
                        'proxima': _proxima_do_job('robo_finmail')}
    saida['_agendador_ativo'] = bool(AGENDADOR is not None and getattr(AGENDADOR, 'running', False))
    saida['_agendador_outro_processo'] = bool(AGENDADOR is None and AGENDADOR_OUTRO_PROCESSO)
    return saida


def _sou_o_dono_do_agendador():
    """Abre uma porta local e segura até o processo morrer. Se outro processo já segurou,
    é ele quem tem o agendador — assim o robô nunca sobe duas vezes (era o que abria duas janelas)."""
    global _TRAVA_AGENDADOR
    try:
        tranca = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        tranca.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
        tranca.bind(('127.0.0.1', PORTA_TRAVA_AGENDADOR))
        tranca.listen(1)
        _TRAVA_AGENDADOR = tranca
        return True
    except OSError:
        return False


_TRAVA_INICIO_AGENDADOR = threading.Lock()
_ULTIMA_TENTATIVA_AGENDADOR = 0.0
AGENDADOR_OUTRO_PROCESSO = False


def iniciar_agendador_interno():
    global AGENDADOR, AGENDADOR_OUTRO_PROCESSO, _ULTIMA_TENTATIVA_AGENDADOR
    with _TRAVA_INICIO_AGENDADOR:
        if AGENDADOR is not None:
            return
        _ULTIMA_TENTATIVA_AGENDADOR = time.time()
        if not _sou_o_dono_do_agendador():
            AGENDADOR_OUTRO_PROCESSO = True
            print('⏰ [AGENDADOR] Outro processo do sistema já está com o agendador; este aqui não vai duplicar os robôs. '
                  'Se a tela de Automações não mostra os robôs automáticos, feche os outros Python do sistema e suba de novo.',
                  flush=True)
            return
        AGENDADOR_OUTRO_PROCESSO = False
        AGENDADOR = BackgroundScheduler(daemon=True, timezone='America/Sao_Paulo')
        AGENDADOR.start()
        aplicar_agenda_dos_robos()
        print(f'⏰ [SISTEMA] Motor APScheduler acoplado e rodando com sucesso! (processo {os.getpid()}, o mesmo do portal)', flush=True)
        atexit.register(lambda: AGENDADOR.shutdown(wait=False))


@app.before_request
def _agendador_no_processo_do_portal():
    """O agendador tem que morar no MESMO processo que atende o portal: é ali que ficam o status e os logs dos
    robôs que a tela de Automações mostra. Com o reloader do Flask há dois processos (o 'vigia' e o servidor);
    se o vigia pegasse o agendador, o robô automático rodava nele e o portal nunca via. Por isso o agendador
    só sobe em quem atende requisição. Se outro processo segurou a tranca, tenta de novo a cada minuto."""
    if AGENDADOR is None and time.time() - _ULTIMA_TENTATIVA_AGENDADOR > 60:
        iniciar_agendador_interno()


@app.route('/api/sistema/agendador_ping')
def api_agendador_ping():
    """Chamado pelo próprio servidor logo depois de subir, para o agendador nascer sem esperar alguém abrir o portal."""
    return jsonify({'ok': True, 'pid': os.getpid(), 'agendador': AGENDADOR is not None,
                    'outro_processo': AGENDADOR_OUTRO_PROCESSO})


def _cutucar_agendador_ao_subir():
    """Faz uma requisição ao próprio portal: quem responde é o processo servidor, e é nele que o agendador sobe."""
    porta = os.getenv('PORTA_SERVIDOR', '5000')

    def cutucar():
        import urllib.request
        for _ in range(90):              # até ~3 min esperando o servidor aceitar conexões
            time.sleep(2)
            if AGENDADOR is not None:
                return
            try:
                with urllib.request.urlopen(f'http://127.0.0.1:{porta}/api/sistema/agendador_ping', timeout=5) as r:
                    if r.status == 200:
                        return
            except Exception:
                continue
    threading.Thread(target=cutucar, daemon=True, name='cutuca-agendador').start()


@app.route('/api/sistema/sincronizar_pdfs_z')
@login_obrigatorio
def sincronizar_pdfs_z():
    import os
    import re
    from datetime import datetime
    
    pasta = r"Z:\financeiro_imoveis\Condomínio\boleto__Cond_antes liax\boletos agosto - ellen"
    
    if not os.path.exists(pasta):
        return f"<h1 style='color: red;'>Erro:</h1> A pasta não foi encontrada. Verifique se o servidor tem acesso ao disco Z:."
        
    imoveis_encontrados = set()
    
    # 1. Lê a pasta e extrai apenas os números iniciais dos PDFs
    try:
        for arquivo in os.listdir(pasta):
            if arquivo.lower().endswith('.pdf'):
                # Pega SÓ os números antes de qualquer traço ou letra
                match = re.match(r'^(\d+)', arquivo)
                if match:
                    imoveis_encontrados.add(match.group(1))
    except Exception as e:
        return f"<h1 style='color: red;'>Erro ao ler a pasta:</h1> {str(e)}"

    if not imoveis_encontrados:
        return "Nenhum PDF com numeração válida foi encontrado na pasta."
        
    conexao = conectar_banco()
    sucessos = 0
    data_hora = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    try:
        # 2. Faz o DE/PARA com o Banco de Dados
        for imovel_str in imoveis_encontrados:
            
            # 🎯 NOVO: Cruza com vinculos_adm para descobrir quem é a dona da carteira
            caso = conexao.execute("""
                SELECT i.id, i.imovel, v.responsavel 
                FROM imoveis i
                LEFT JOIN vinculos_adm v ON v.rowid = vinculo_de(i.administradora)
                WHERE LTRIM(i.imovel, '0') = LTRIM(?, '0') 
                  AND i.ativo = 1 
                  AND UPPER(TRIM(IFNULL(i.status_interno, ''))) NOT IN """ + _SQL_FORA_DA_AUTOMACAO + """
            """, (imovel_str,)).fetchone()
            
            if caso:
                imovel_id = caso['id']
                
                # 🎯 Descobre o nome da responsável e dá uma limpada (deixa bonito igual ao painel)
                operador_dono = caso['responsavel'] if caso['responsavel'] else 'Não Atribuído'
                operador_limpo = operador_dono.strip().title()
                if operador_limpo == 'Jéssica': 
                    operador_limpo = 'Jessica' # Nossa velha correção preventiva de acentos
                
                # Atualiza o status para COLETADO
                marco_mud = marco_das_mudancas(conexao)
                conexao.execute("UPDATE imoveis SET status_interno = 'COLETADO' WHERE id = ?", (imovel_id,))
                assinar_mudancas(conexao, marco_mud, nome_robo, [caso['imovel']])
                
                # Insere a nova produtividade PARA A RESPONSÁVEL DA CARTEIRA
                conexao.execute("DELETE FROM produtividade WHERE imovel_id = ? AND acao = 'COLETOU BOLETO'", (imovel_id,))
                conexao.execute("INSERT INTO produtividade (usuario, acao, imovel_id, data_hora) VALUES (?, ?, ?, ?)", 
                                (operador_limpo, "COLETOU BOLETO", imovel_id, data_hora))
                
                # Deixa um registro no prontuário avisando o que houve
                conexao.execute("INSERT INTO historico_observacoes (imovel_id, data_hora, usuario, texto) VALUES (?, ?, ?, ?)", 
                                (imovel_id, data_hora, "SISTEMA", f"Status alterado para COLETADO (Leitura de PDF na pasta Z). Produtividade atribuída a: {operador_limpo}"))
                
                sucessos += 1
                
        conexao.commit()
        
        return f"""
            <div style='font-family: Arial; text-align: center; margin-top: 50px;'>
                <h1 style='color: #10b981;'>✔️ Sincronização e Produtividade Concluídas!</h1>
                <p>O sistema leu a pasta Z e encontrou <b>{len(imoveis_encontrados)}</b> PDFs válidos.</p>
                <p>Desses, <b>{sucessos}</b> imóveis pendentes foram atualizados para COLETADO e a produtividade foi dada para as donas de cada carteira!</p>
                <a href='/' style='display: inline-block; margin-top: 20px; padding: 10px 20px; background-color: #0f172a; color: white; text-decoration: none; border-radius: 6px;'>Voltar ao Painel</a>
            </div>
        """
    except Exception as e:
        return f"<h1 style='color: red;'>Erro ao atualizar o banco:</h1> {str(e)}"
    finally:
        conexao.close()

def otimizar_pdf_fitz(caminho_ou_bytes):
    """
    Motor Híbrido de Compressão:
    1. Tenta limpar a estrutura do código do PDF mantendo textos.
    2. Se ainda ficar acima de 450KB, ativa a Compressão Extrema (Rasterização),
       transformando o PDF em imagens JPEG otimizadas para esmagar o tamanho.
    """
    doc = fitz.open(stream=caminho_ou_bytes, filetype="pdf") if isinstance(caminho_ou_bytes, bytes) else fitz.open(caminho_ou_bytes)
    
    saida = io.BytesIO()
    
    # Tentativa 1: Otimização Padrão (Limpeza de lixo interno e metadados)
    doc.save(saida, garbage=4, deflate=True, clean=True)
    tamanho_atual = len(saida.getvalue())
    
    # Se o arquivo AINDA estiver pesado (maior que ~450 KB), ativamos o modo agressivo
    if tamanho_atual > 450 * 1024:
        saida = io.BytesIO()
        novo_doc = fitz.open()
        
        for pagina in doc:
            # Transforma a página em imagem (matriz 1.5 é um bom equilíbrio entre legibilidade e peso)
            matriz_zoom = fitz.Matrix(1.5, 1.5)
            pix = pagina.get_pixmap(matrix=matriz_zoom)
            
            # Converte para JPEG com 40% a 50% de qualidade (Derruba o tamanho drasticamente)
            img_bytes = pix.tobytes("jpeg", 40) 
            
            # Cria uma página em branco com as mesmas dimensões e cola a imagem esmagada
            nova_pag = novo_doc.new_page(width=pagina.rect.width, height=pagina.rect.height)
            nova_pag.insert_image(nova_pag.rect, stream=img_bytes)
            
        # Salva o novo documento esmagado
        novo_doc.save(saida, garbage=4, deflate=True)
        novo_doc.close()

    doc.close()
    saida.seek(0)
    return saida.read()


@app.route('/api/pdf/comprimir_manual', methods=['POST'])
@login_obrigatorio
def api_comprimir_manual():
    import zipfile  # Importado aqui para garantir que o ZIP funcione
    
    # 🎯 AGORA PEGA A LISTA DE ARQUIVOS (Múltiplos)
    arquivos = request.files.getlist('arquivos')
    
    if not arquivos or arquivos[0].filename == '':
        return "Nenhum arquivo enviado", 400
        
    try:
        # CENÁRIO 1: Apenas 1 arquivo
        if len(arquivos) == 1:
            arquivo = arquivos[0]
            pdf_bytes = arquivo.read()
            pdf_comprimido = otimizar_pdf_fitz(pdf_bytes)
            
            return send_file(
                io.BytesIO(pdf_comprimido),
                mimetype='application/pdf',
                as_attachment=True,
                download_name=f"comprimido_{arquivo.filename}"
            )
            
        # CENÁRIO 2: Vários arquivos (Zip)
        else:
            memoria_zip = io.BytesIO()
            
            with zipfile.ZipFile(memoria_zip, 'w', zipfile.ZIP_DEFLATED) as zf:
                for arquivo in arquivos:
                    if arquivo.filename.lower().endswith('.pdf'):
                        pdf_bytes = arquivo.read()
                        pdf_comprimido = otimizar_pdf_fitz(pdf_bytes)
                        # Salva o PDF esmagado dentro do pacote ZIP
                        zf.writestr(f"comprimido_{arquivo.filename}", pdf_comprimido)
            
            memoria_zip.seek(0)
            
            return send_file(
                memoria_zip,
                mimetype='application/zip',
                as_attachment=True,
                download_name="arquivos_otimizados.zip"
            )
            
    except Exception as e:
        return f"Erro na compressão: {e}", 500


@app.route('/api/pdf/comprimir_lote', methods=['POST'])
@login_obrigatorio
def api_comprimir_lote():
    dados = request.get_json()
    
    # 🎯 AGORA RECEBE A LISTA DE ADMINISTRADORAS DO FRONTEND
    adms = dados.get('administradoras', [])
    
    if not adms or len(adms) == 0:
        return jsonify({'sucesso': False, 'msg': 'Nenhuma administradora selecionada.'})
        
    conexao = conectar_banco()
    try:
        # 🎯 PREPARA A QUERY COM MÚLTIPLOS PARÂMETROS
        placeholders = ', '.join(['?'] * len(adms))
        
        query = f'''
            SELECT imovel, vencimento 
            FROM imoveis 
            WHERE ativo = 1 
              AND TRIM(UPPER(administradora)) IN ({placeholders}) 
              AND status_interno = 'COLETADO'
        '''
        
        parametros = [a.strip().upper() for a in adms]
        imoveis_coletados = conexao.execute(query, parametros).fetchall()
        
        if not imoveis_coletados:
            return jsonify({'sucesso': False, 'msg': 'Nenhum boleto com status COLETADO encontrado para as administradoras selecionadas.'})
            
        # Raiz da rede atualizada conforme sua instrução
        raiz_rede = r"Z:\financeiro_imoveis\Condomínio"
        
        # Dicionário de tradução para bater EXATAMENTE com o nome das suas pastas
        meses_traducao = {
            '01': 'JAN', '02': 'FEV', '03': 'MAR', '04': 'ABR', 
            '05': 'MAI', '06': 'JUN', '07': 'JUL', '08': 'AGO', 
            '09': 'SET', '10': 'OUT', '11': 'NOV', '12': 'DEZ'
        }
        
        qtd_comprimidos = 0
        
        for row in imoveis_coletados:
            imovel_id = str(row['imovel'])
            venc_str = str(row['vencimento'] or '').strip()
            
            # 🎯 INTELIGÊNCIA: Descobre o nome da pasta baseado no VENCIMENTO do imóvel
            pasta_alvo = None
            if '/' in venc_str:
                partes = venc_str.split('/')
                if len(partes) >= 3:
                    mes_num = partes[1].zfill(2) # Pega o '08'
                    ano_str = partes[2][-2:]     # Pega o '26' de '2026'
                    sigla_mes = meses_traducao.get(mes_num, 'JAN') # Vira 'AGO'
                    
                    # Monta o nome perfeito: "AGO26 - NOVA GESTÃO"
                    nome_pasta = f"{sigla_mes}{ano_str} - NOVA GESTÃO"
                    pasta_alvo = os.path.join(raiz_rede, nome_pasta)
            
            # Fallback (Plano B): Caso não ache na pasta do mês, procura na pasta provisória
            pasta_secundaria = os.path.join(raiz_rede, "boleto__Cond_antes liax")
            
            pastas_para_buscar = []
            if pasta_alvo:
                pastas_para_buscar.append(pasta_alvo)
            pastas_para_buscar.append(pasta_secundaria)
            
            caminho_pdf = None
            
            # Tenta achar o arquivo nas pastas mapeadas
            for pasta in pastas_para_buscar:
                if os.path.exists(pasta):
                    teste_caminho = os.path.join(pasta, f"{imovel_id}.pdf")
                    if os.path.exists(teste_caminho):
                        caminho_pdf = teste_caminho
                        break # Achou o arquivo, para de procurar
            
            # Se o arquivo existir, inicia o processo de compressão
            if caminho_pdf:
                tamanho_original = os.path.getsize(caminho_pdf)
                
                # Só comprime se for maior que 450 KB (Para não gastar processamento à toa)
                if tamanho_original > 450 * 1024:
                    with open(caminho_pdf, 'rb') as f:
                        bytes_originais = f.read()
                        
                    # Chama sua função híbrida de compressão extrema
                    bytes_comprimidos = otimizar_pdf_fitz(bytes_originais)
                    
                    # Sobrescreve o arquivo na rede APENAS se realmente ficou menor
                    if len(bytes_comprimidos) < tamanho_original:
                        with open(caminho_pdf, 'wb') as f:
                            f.write(bytes_comprimidos)
                        qtd_comprimidos += 1
                        
        return jsonify({'sucesso': True, 'qtd_comprimidos': qtd_comprimidos})
        
    except Exception as e:
        print(f"Erro na compressão em lote: {e}")
        return jsonify({'sucesso': False, 'msg': f'Erro técnico: {str(e)}'})
    finally:
        conexao.close()

@app.route('/api/sistema/dividir_pdf', methods=['POST'])
def dividir_pdf():
    if 'arquivo' not in request.files:
        return jsonify({'sucesso': False, 'msg': 'Nenhum arquivo enviado.'})

    file = request.files['arquivo']
    if file.filename == '':
        return jsonify({'sucesso': False, 'msg': 'Arquivo inválido.'})

    try:
        # Lê o PDF enviado
        reader = PdfReader(file)
        num_pages = len(reader.pages)

        # Prepara um arquivo ZIP na memória (para não sujar o servidor com arquivos soltos)
        memory_zip = io.BytesIO()
        with zipfile.ZipFile(memory_zip, 'w', zipfile.ZIP_DEFLATED) as zf:
            
            # Faz um loop cortando página por página
            for i in range(num_pages):
                writer = PdfWriter()
                writer.add_page(reader.pages[i])

                # Salva a página cortada na memória
                page_io = io.BytesIO()
                writer.write(page_io)
                page_io.seek(0)

                # Dá um nome amigável para cada página (Ex: boleto_pag_1.pdf, boleto_pag_2.pdf)
                base_name = file.filename.rsplit('.pdf', 1)[0]
                page_name = f"{base_name}_pag_{i+1}.pdf"

                # Joga o PDF cortado para dentro do ZIP
                zf.writestr(page_name, page_io.read())

        memory_zip.seek(0)

        # Devolve o ZIP prontinho para o usuário baixar
        return send_file(
            memory_zip,
            mimetype='application/zip',
            as_attachment=True,
            download_name=f"{file.filename.rsplit('.pdf', 1)[0]}_dividido.zip"
        )

    except Exception as e:
        print("Erro ao dividir PDF:", e)
        return jsonify({'sucesso': False, 'msg': 'Erro ao processar o arquivo.'})

from flask import send_file, request, jsonify
from werkzeug.utils import secure_filename
import tempfile
import os

# =================================================================
# MÓDULO: INTERNET E UPLOAD INTELIGENTE PRO DRIVE
# =================================================================

# ⚠️ Configure as credenciais do seu projeto do Google Cloud usando raw string (r"")
SCOPES = ['https://www.googleapis.com/auth/drive']
SERVICE_ACCOUNT_FILE = r'C:\Users\ryan.lima\Documents\SistemaImoveis\credenciais.json'

# IDs das pastas de ANO dentro do Drive Compartilhado (Financeiro - Locação > PAGAMENTOS INTERNET > 2026)
# Estrutura: ANO > MÊS (ex: "9.Setembro- ok") > DIA > COND / CHEQUE COND
ID_PASTAS_ANO = {
    2026: '1FOD0ElLNKDIXazdQ3JnvU2UA4vxuKsup',
    # 2027: 'COLE_AQUI_O_ID_DA_PASTA_2027',
}

# Formato do nome da pasta do dia quando ela precisar ser CRIADA (ajuste se o padrão de vocês for outro)
FORMATO_PASTA_DIA = '%d/%m/%Y'

def obter_servico_drive():
    creds = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE, scopes=SCOPES)
    return build('drive', 'v3', credentials=creds)

# -----------------------------------------------------------------
# 🕒 DATA DE PAGAMENTO + PERMISSÕES DO MÓDULO INTERNET
# -----------------------------------------------------------------
from datetime import timezone

FUSO_BRASIL = timezone(timedelta(hours=-3))          # Brasília (sem horário de verão)
# Motivos que podem virar sufixo no nome do PDF da Internet (podem ser combinados)
MOTIVOS_INTERNET_VALIDOS = ('INCO', 'N FIN', 'BAIXAR NA PLANILHA', 'RELACIONAMENTO')

USUARIOS_EDITORES_INTERNET = {'ryan', 'marcio', 'arthur'}
PERMITIR_PAGAMENTO_HOJE = True   # True: aceita hoje em diante | False: só a partir de amanhã
CONTAS_INTERNET_VALIDAS = {'33', '132', '209', '211', '212', '231', '275', '276'}

def hoje_brasil():
    return datetime.now(FUSO_BRASIL).date()

def menor_data_pagamento_permitida():
    hoje = hoje_brasil()
    return hoje if PERMITIR_PAGAMENTO_HOJE else hoje + timedelta(days=1)

def usuario_pode_editar_internet():
    return str(session.get('usuario', '')).strip().lower() in USUARIOS_EDITORES_INTERNET

def validar_data_pagamento(data_iso):
    """Recebe 'YYYY-MM-DD'. Retorna (ok, mensagem, date)."""
    try:
        dt = datetime.strptime(str(data_iso).strip()[:10], '%Y-%m-%d').date()
    except Exception:
        return False, 'Data de pagamento inválida.', None
    limite = menor_data_pagamento_permitida()
    if dt < limite:
        if PERMITIR_PAGAMENTO_HOJE:
            msg = f"A data de pagamento ({dt.strftime('%d/%m/%Y')}) já passou. Escolha hoje ({limite.strftime('%d/%m/%Y')}) ou uma data futura."
        else:
            msg = f"A data de pagamento ({dt.strftime('%d/%m/%Y')}) não é válida. Escolha a partir de {limite.strftime('%d/%m/%Y')}."
        return False, msg, dt
    return True, '', dt

# -----------------------------------------------------------------
# 📁 GOOGLE DRIVE: localizar ANO > MÊS > DIA > COND / CHEQUE COND
# -----------------------------------------------------------------
MESES_PT = {1: 'janeiro', 2: 'fevereiro', 3: 'marco', 4: 'abril', 5: 'maio', 6: 'junho',
            7: 'julho', 8: 'agosto', 9: 'setembro', 10: 'outubro', 11: 'novembro', 12: 'dezembro'}

def _sem_acento(txt):
    return ''.join(c for c in unicodedata.normalize('NFD', str(txt)) if unicodedata.category(c) != 'Mn')

def _opcoes_listagem(drive_service, id_pai):
    """Se a pasta estiver num Drive Compartilhado, lista só dentro dele (mais confiável que allDrives)."""
    try:
        info = drive_service.files().get(fileId=id_pai, fields='id, driveId', supportsAllDrives=True).execute()
        drive_id = info.get('driveId')
    except Exception as e:
        print(f"⚠️ Não consegui ler o driveId da pasta {id_pai}: {e}", flush=True)
        drive_id = None
    if drive_id:
        return {'corpora': 'drive', 'driveId': drive_id, 'includeItemsFromAllDrives': True, 'supportsAllDrives': True}
    return {'corpora': 'allDrives', 'includeItemsFromAllDrives': True, 'supportsAllDrives': True}

def _listar_subpastas(drive_service, id_pai):
    """Lista as subpastas (id, name) de uma pasta. Atalhos para pastas também são considerados."""
    opcoes = _opcoes_listagem(drive_service, id_pai)
    pastas, token = [], None
    while True:
        resp = drive_service.files().list(
            q=(f"'{id_pai}' in parents and trashed = false and "
               "(mimeType = 'application/vnd.google-apps.folder' or mimeType = 'application/vnd.google-apps.shortcut')"),
            fields='nextPageToken, files(id, name, mimeType, shortcutDetails)',
            pageSize=1000,
            pageToken=token,
            **opcoes
        ).execute()
        for f in resp.get('files', []):
            if f.get('mimeType') == 'application/vnd.google-apps.shortcut':
                det = f.get('shortcutDetails') or {}
                if det.get('targetMimeType') != 'application/vnd.google-apps.folder' or not det.get('targetId'):
                    continue
                pastas.append({'id': det['targetId'], 'name': f['name']})
            else:
                pastas.append({'id': f['id'], 'name': f['name']})
        token = resp.get('nextPageToken')
        if not token:
            return pastas

def _criar_pasta(drive_service, nome, id_pai):
    return drive_service.files().create(
        body={'name': nome, 'mimeType': 'application/vnd.google-apps.folder', 'parents': [id_pai]},
        fields='id', supportsAllDrives=True
    ).execute()['id']

def _numero_do_mes(nome_pasta):
    """'01.Janeiro - ok' -> 1 | '9.Setembro- ok' -> 9 | '10.outubro' -> 10 | '10 - outubro' -> 10"""
    m = re.match(r'^\s*(\d{1,2})(?!\d)', str(nome_pasta))
    return int(m.group(1)) if m else None

def _pasta_mes_confere(nome_pasta, mes):
    """Acha a pasta do mês pelo número no início do nome; se não tiver número, pelo nome do mês."""
    numero = _numero_do_mes(nome_pasta)
    if numero is not None:
        return numero == mes
    return MESES_PT[mes] in _sem_acento(nome_pasta).lower()

def _pasta_dia_confere(nome_pasta, dt):
    """Aceita '25/08/2026', '25.08.2026', '25-08-2026', '25/08', '25.08', '25-08' (com ou sem texto depois)."""
    nome = nome_pasta.replace(' ', '')
    for fmt in ('%d/%m/%Y', '%d.%m.%Y', '%d-%m-%Y', '%d/%m', '%d.%m', '%d-%m'):
        alvo = dt.strftime(fmt)
        if nome.startswith(alvo) and not nome[len(alvo):len(alvo)+1].isdigit():
            return True
    return False

import threading

# 🧠 MEMÓRIA CACHE E TRAVA DE SEGURANÇA PARA O GOOGLE DRIVE
CACHE_PASTAS_DRIVE = {}
TRAVA_CRIACAO_DRIVE = threading.Lock()

def encontrar_pasta_cond_por_data(data_formatada_br, nome_subpasta="COND"):
    """
    Navega dinamicamente: Raiz -> ANO -> MÊS -> DIA -> subpasta.
    Usa Memória Cache e Trava (Lock) para evitar que a demora do Google Drive 
    crie pastas duplicadas quando o usuário aprova itens rapidamente.
    """
    dt = datetime.strptime(data_formatada_br, '%d/%m/%Y')
    chave_cache_final = f"{dt.year}_{dt.month}_{dt.day}_{nome_subpasta.upper()}"
    
    # 1. Consulta supersônica na memória (Bypass do Google Drive)
    if chave_cache_final in CACHE_PASTAS_DRIVE:
        return CACHE_PASTAS_DRIVE[chave_cache_final]

    # 2. Trava a execução para outras aprovações esperarem (fila indiana)
    with TRAVA_CRIACAO_DRIVE:
        # Checa de novo caso a pasta tenha sido criada enquanto estava esperando na fila
        if chave_cache_final in CACHE_PASTAS_DRIVE:
            return CACHE_PASTAS_DRIVE[chave_cache_final]

        id_base = ID_PASTAS_ANO.get(dt.year)
        if not id_base:
            raise Exception(f"Não há pasta configurada em ID_PASTAS_ANO para o ano {dt.year}.")

        drive_service = obter_servico_drive()

        # --- 1. PASTA DO ANO ---
        chave_ano = f"ANO_{dt.year}"
        if chave_ano in CACHE_PASTAS_DRIVE:
            id_ano = CACHE_PASTAS_DRIVE[chave_ano]
        else:
            pastas_base = _listar_subpastas(drive_service, id_base)
            pasta_ano = next((p for p in pastas_base if p['name'] == str(dt.year)), None)
            
            if pasta_ano:
                id_ano = pasta_ano['id']
            else:
                try:
                    info_base = drive_service.files().get(fileId=id_base, fields='name', supportsAllDrives=True).execute()
                    if info_base.get('name') == str(dt.year):
                        id_ano = id_base
                    else:
                        print(f"Pasta do ano {dt.year} não encontrada. Criando...", flush=True)
                        id_ano = _criar_pasta(drive_service, str(dt.year), id_base)
                except:
                    id_ano = _criar_pasta(drive_service, str(dt.year), id_base)
            CACHE_PASTAS_DRIVE[chave_ano] = id_ano

        # --- 2. PASTA DO MÊS ---
        chave_mes = f"MES_{dt.year}_{dt.month}"
        if chave_mes in CACHE_PASTAS_DRIVE:
            id_mes = CACHE_PASTAS_DRIVE[chave_mes]
        else:
            pastas_ano_conteudo = _listar_subpastas(drive_service, id_ano)
            pasta_mes = next((p for p in pastas_ano_conteudo if _pasta_mes_confere(p['name'], dt.month)), None)
            
            if pasta_mes:
                id_mes = pasta_mes['id']
            else:
                nome_mes_novo = f"{dt.month:02d}.{MESES_PT[dt.month]}"
                print(f"Pasta do mês '{nome_mes_novo}' não encontrada. Criando...", flush=True)
                id_mes = _criar_pasta(drive_service, nome_mes_novo, id_ano)
            CACHE_PASTAS_DRIVE[chave_mes] = id_mes

        # --- 3. PASTA DO DIA ---
        chave_dia = f"DIA_{dt.year}_{dt.month}_{dt.day}"
        if chave_dia in CACHE_PASTAS_DRIVE:
            id_dia = CACHE_PASTAS_DRIVE[chave_dia]
        else:
            pastas_mes_conteudo = _listar_subpastas(drive_service, id_mes)
            pasta_dia = next((p for p in pastas_mes_conteudo if _pasta_dia_confere(p['name'], dt)), None)
            
            if pasta_dia:
                id_dia = pasta_dia['id']
            else:
                nome_dia = dt.strftime(FORMATO_PASTA_DIA)
                print(f"Pasta do dia '{nome_dia}' não encontrada. Criando...", flush=True)
                id_dia = _criar_pasta(drive_service, nome_dia, id_mes)
            CACHE_PASTAS_DRIVE[chave_dia] = id_dia

        # --- 4. SUBPASTA (COND / CHEQUE COND) ---
        pastas_dia_conteudo = _listar_subpastas(drive_service, id_dia)
        sub = next((p for p in pastas_dia_conteudo if p['name'].strip().upper() == nome_subpasta.upper()), None)
        
        if sub:
            id_final = sub['id']
        else:
            print(f"Subpasta '{nome_subpasta}' não encontrada. Criando...", flush=True)
            id_final = _criar_pasta(drive_service, nome_subpasta, id_dia)

        # Salva o ID da pasta final na memória
        CACHE_PASTAS_DRIVE[chave_cache_final] = id_final
        return id_final

    
@app.route('/modulo_internet', methods=['GET'])
@login_obrigatorio
def modulo_internet():
    usuario_atual = session.get('usuario', '').title()
    conexao = conectar_banco()
    
    # Garantia da tabela
    conexao.execute('''
        CREATE TABLE IF NOT EXISTS pendencias_internet (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            imovel TEXT, carteira TEXT, mes_ref TEXT, conta TEXT, nome_arquivo TEXT,
            arquivo_bytes BLOB, observacao_solicitante TEXT, usuario_solicitante TEXT,
            status TEXT DEFAULT 'PENDENTE', data_solicitacao TEXT, data_pagamento TEXT,
            tipo_doc TEXT DEFAULT 'BOLETO', valor TEXT DEFAULT '', usuario_avaliador TEXT DEFAULT '',
            data_avaliacao TEXT DEFAULT '', motivo_recusa TEXT DEFAULT ''
        )
    ''')
    conexao.commit()

    # 1. FILA DE PENDENTES
    pendentes_raw = conexao.execute("SELECT * FROM pendencias_internet WHERE status = 'PENDENTE' ORDER BY COALESCE(data_pagamento, '9999-12-31') ASC, id ASC").fetchall()
    pendentes = [dict(p) for p in pendentes_raw]
    for item in pendentes:
        item['data_pagamento'] = item.get('data_pagamento', '')

    # 2. 🎯 BUSCA DOS ERROS DE PAGAMENTO (CRUZANDO DADOS)
    query_erros = """
        SELECT p.*, 
               IFNULL(i.empreendimento, c.empreendimento) as empreendimento,
               IFNULL(i.administradora, c.administradora) as administradora,
               IFNULL(i.endereco, 'Endereço não cadastrado') as endereco
        FROM pendencias_internet p
        LEFT JOIN imoveis i ON LTRIM(p.imovel, '0') = LTRIM(i.imovel, '0') AND i.ativo = 1
        LEFT JOIN cadastro_mestre_imoveis c ON LTRIM(p.imovel, '0') = LTRIM(c.imovel, '0')
        WHERE p.status = 'ERRO PAGAMENTO'
        ORDER BY p.id DESC
    """
    erros_raw = conexao.execute(query_erros).fetchall()
    erros_pagamento = [dict(e) for e in erros_raw]

    # 3. FILTROS DO HISTÓRICO (AGORA COM PERÍODO DE DATAS)
    data_inicio = request.args.get('data_inicio', '')
    data_fim = request.args.get('data_fim', '')
    filtro_mes = request.args.get('filtro_mes', '')
    hoje_str = datetime.now().strftime('%Y-%m-%d')

    query_params = []
    condicao_sql = ""

    if filtro_mes:
        condicao_sql = "data_avaliacao LIKE ?"
        query_params = [f"{filtro_mes}-%"]
        periodo_nome = f"Mês: {filtro_mes[5:7]}/{filtro_mes[0:4]}"
        data_inicio = ""
        data_fim = ""
    elif data_inicio or data_fim:
        # Se preencheu só o início ou só o fim, iguala os dois (filtra só 1 dia)
        d_inicio = data_inicio if data_inicio else data_fim
        d_fim = data_fim if data_fim else data_inicio
        
        # Filtra do minuto 00:00:00 do primeiro dia até 23:59:59 do último dia
        condicao_sql = "data_avaliacao >= ? AND data_avaliacao <= ?"
        query_params = [f"{d_inicio} 00:00:00", f"{d_fim} 23:59:59"]
        
        try:
            dt_i = datetime.strptime(d_inicio, '%Y-%m-%d').strftime('%d/%m/%Y')
            dt_f = datetime.strptime(d_fim, '%Y-%m-%d').strftime('%d/%m/%Y')
            periodo_nome = f"Período: {dt_i} a {dt_f}" if dt_i != dt_f else f"Dia: {dt_i}"
        except:
            periodo_nome = f"Período: {d_inicio} a {d_fim}"
            
        data_inicio = d_inicio
        data_fim = d_fim
    else:
        # Padrão: Hoje
        data_inicio = hoje_str
        data_fim = hoje_str
        condicao_sql = "data_avaliacao LIKE ?"
        query_params = [f"{hoje_str}%"]
        periodo_nome = "Hoje"

    aprovados_periodo = conexao.execute(f"SELECT COUNT(*) FROM pendencias_internet WHERE status = 'APROVADO' AND {condicao_sql}", query_params).fetchone()[0]
    recusados_periodo = conexao.execute(f"SELECT COUNT(*) FROM pendencias_internet WHERE status IN ('RECUSADO', 'ERRO PAGAMENTO') AND {condicao_sql}", query_params).fetchone()[0]
    
    historico_periodo = [dict(h) for h in conexao.execute(f"SELECT * FROM pendencias_internet WHERE status IN ('APROVADO', 'RECUSADO', 'ERRO PAGAMENTO', 'RESOLVIDO') AND {condicao_sql} ORDER BY data_avaliacao DESC LIMIT 500", query_params).fetchall()]

    inc_caso = None
    if request.args.get('inc_aba'):
        try:
            inc_garantir_tabelas(conexao)
            inc_caso = inc_caso_para_internet(conexao, request.args.get('inc_aba', ''), request.args.get('inc_linha'),
                                              request.args.get('inc_ref', ''), request.args.get('inc_doc', ''))
        except Exception as erro_inc:
            print(f'⚠️ Não consegui abrir a inconsistência no módulo Internet: {erro_inc}', flush=True)

    conexao.close()
    return render_template('internet.html', inc_caso=inc_caso, pendentes=pendentes, erros_pagamento=erros_pagamento, aprovados_periodo=aprovados_periodo, recusados_periodo=recusados_periodo, historico_periodo=historico_periodo, periodo_nome=periodo_nome, data_inicio=data_inicio, data_fim=data_fim, filtro_mes=filtro_mes, data_hoje=hoje_str, usuario_atual=usuario_atual, data_min_pagamento=menor_data_pagamento_permitida().isoformat(), pode_editar=usuario_pode_editar_internet())


@app.route('/api/internet/excluir_pendencia', methods=['POST'])
@login_obrigatorio
def excluir_pendencia_internet():
    dados = request.get_json(silent=True) or {}
    pendencia_id = dados.get('id')
    conexao = conectar_banco()
    conexao.execute("DELETE FROM pendencias_internet WHERE id = ?", (pendencia_id,))
    conexao.commit()
    conexao.close()
    return jsonify({'sucesso': True, 'msg': 'Registro excluído com sucesso.'})


@app.route('/api/internet/erro_pagamento', methods=['POST'])
@login_obrigatorio
def erro_pagamento_internet():
    dados = request.get_json(silent=True) or {}
    pendencia_id = dados.get('id')
    motivo = dados.get('motivo', 'Falha no processamento bancário.').strip()
    usuario_atual = session.get('usuario', 'Sistema').title()
    data_hora = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    conexao = conectar_banco()
    item = conexao.execute("SELECT * FROM pendencias_internet WHERE id = ?", (pendencia_id,)).fetchone()
    
    if item:
        imovel_num = item['imovel']
        solicitante = str(item['usuario_solicitante']).strip()
        solicitante_clean = solicitante.lower()

        # Altera o status para ERRO PAGAMENTO
        conexao.execute("UPDATE pendencias_internet SET status = 'ERRO PAGAMENTO', motivo_recusa = ?, usuario_avaliador = ?, data_avaliacao = ? WHERE id = ?", (motivo, usuario_atual, data_hora, pendencia_id))
        try:
            inc_desfecho_internet(item, usuario_atual, 'ERRO')           # inconsistência do Bradesco: volta a PENDENTE na planilha
        except Exception as erro_inc:
            print(f'⚠️ Inc. Bradesco (erro de pagamento): {erro_inc}', flush=True)
        
        # Registra no prontuário do imóvel
        imovel_bd = conexao.execute("SELECT id FROM imoveis WHERE LTRIM(imovel, '0') = LTRIM(?, '0') AND ativo = 1 LIMIT 1", (imovel_num,)).fetchone()
        if imovel_bd:
            conexao.execute("INSERT INTO historico_observacoes (imovel_id, data_hora, usuario, texto) VALUES (?, ?, ?, ?)", (imovel_bd['id'], data_hora, usuario_atual, f"🚨 ALERTA BANCÁRIO: Pagamento do boleto/cheque falhou. Motivo: {motivo}"))
        
        conexao.commit()
        conexao.close()

        # E-mail de notificação para quem solicitou
        if solicitante_clean in MAPA_EMAILS_USUARIOS: email_solicitante = MAPA_EMAILS_USUARIOS[solicitante_clean]
        elif '@' in solicitante_clean: email_solicitante = solicitante_clean
        else: email_solicitante = f"{solicitante_clean}@lello.com.br"

        emails_notificacao = list(set([email_solicitante, MAPA_EMAILS_USUARIOS.get('arthur', 'arthur.varo@lello.com.br')]))
        destinatarios_str = ", ".join(emails_notificacao)
        
        assunto_email = f"🚨 URGENTE: Falha no Pagamento Bancário - Imóvel {imovel_num}"
        corpo_html = f"""
        <div style="font-family: Arial, sans-serif; color: #334155; max-width: 600px; padding: 20px; border: 1px solid #e2e8f0; border-radius: 12px; background-color: #f8fafc;">
            <h3 style="color: #dc2626; margin-top: 0;">🚨 Falha de Pagamento Reportada</h3>
            <p>Atenção, <b>{solicitante.title()}</b>!</p>
            <p>O boleto/documento enviado anteriormente para o imóvel <b>{imovel_num}</b> apresentou um <b>ERRO NO PAGAMENTO BANCÁRIO</b> e precisa ser verificado urgentemente.</p>
            
            <table style="width: 100%; border-collapse: collapse; margin: 15px 0; background-color: #ffffff; border-radius: 8px; overflow: hidden; border: 1px solid #cbd5e1;">
                <tr style="background-color: #0f172a; color: #ffffff;"><th colspan="2" style="padding: 10px; text-align: left;">Detalhes da Falha</th></tr>
                <tr><td style="padding: 8px 12px; border-bottom: 1px solid #e2e8f0; font-weight: bold;">Nº Imóvel:</td><td style="padding: 8px 12px; border-bottom: 1px solid #e2e8f0;">{imovel_num}</td></tr>
                <tr><td style="padding: 8px 12px; border-bottom: 1px solid #e2e8f0; font-weight: bold;">Conta:</td><td style="padding: 8px 12px; border-bottom: 1px solid #e2e8f0;">{item['conta']} ({item['mes_ref']})</td></tr>
                <tr><td style="padding: 8px 12px; font-weight: bold; color: #dc2626;">Motivo / Retorno:</td><td style="padding: 8px 12px; color: #dc2626; font-weight: bold;">{motivo}</td></tr>
            </table>
            
            <p style="font-size: 0.85rem; color: #64748b;">Acesse o Módulo Internet > Aba "Falhas de Pagamento" para conferir os dados.</p>
            <hr style="border: none; border-top: 1px solid #cbd5e1; margin-top: 20px;">
            <small style="color: #94a3b8;">Lello Orchestrator - Notificação Automática</small>
        </div>
        """
        threading.Thread(target=enviar_email_smtp, args=(destinatarios_str, assunto_email, corpo_html), daemon=True).start()
        return jsonify({'sucesso': True, 'msg': 'Falha reportada e e-mail enviado ao solicitante.'})

    conexao.close()
    return jsonify({'sucesso': False, 'msg': 'Item não encontrado.'})


@app.route('/api/internet/resolver_erro', methods=['POST'])
@login_obrigatorio
def resolver_erro_internet():
    dados = request.get_json(silent=True) or {}
    pendencia_id = dados.get('id')
    conexao = conectar_banco()
    # Limpa a pendência visual (Status RESOLVIDO)
    conexao.execute("UPDATE pendencias_internet SET status = 'RESOLVIDO' WHERE id = ?", (pendencia_id,))
    conexao.commit()
    conexao.close()
    return jsonify({'sucesso': True, 'msg': 'Problema marcado como resolvido.'})

@app.route('/api/internet/buscar_dados_imovel', methods=['POST'])
@login_obrigatorio
def buscar_dados_imovel_internet():
    dados = request.get_json()
    num_imovel = dados.get('imovel', '').strip().lstrip('0')
    
    if not num_imovel:
        return jsonify({'sucesso': False, 'msg': 'Imóvel não informado.'})
        
    conexao = conectar_banco()
    
    # 1. Busca primeiro na base do mês
    query_mes = '''
        SELECT i.carteira, IFNULL(v.responsavel, 'Não Atribuído') as responsavel 
        FROM imoveis i 
        LEFT JOIN vinculos_adm v ON v.rowid = vinculo_de(i.administradora)
        WHERE LTRIM(i.imovel, '0') = ? AND i.ativo = 1 LIMIT 1
    '''
    caso = conexao.execute(query_mes, (num_imovel,)).fetchone()
    
    # 2. Se não achar no mês, busca no Cadastro Mestre Geral
    if not caso:
        query_mestre = '''
            SELECT c.empreendimento, IFNULL(v.responsavel, 'Não Atribuído') as responsavel,
                   (SELECT carteira FROM imoveis WHERE LTRIM(imovel, '0') = ? LIMIT 1) as carteira
            FROM cadastro_mestre_imoveis c
            LEFT JOIN vinculos_adm v ON v.rowid = vinculo_de(c.administradora)
            WHERE LTRIM(c.imovel, '0') = ? LIMIT 1
        '''
        caso = conexao.execute(query_mestre, (num_imovel, num_imovel)).fetchone()
    
    conexao.close()
    
    if caso:
        carteira_encontrada = caso['carteira'] if 'carteira' in caso.keys() and caso['carteira'] else 'Sem Carteira'
        return jsonify({
            'sucesso': True, 
            'carteira': carteira_encontrada,
            'responsavel': str(caso['responsavel']).title()
        })
        
    return jsonify({'sucesso': False, 'msg': 'Imóvel não localizado no cadastro geral.'})

def enviar_notificacao_google_chat(imovel, conta, mes_ref, solicitante, justificativa, data_pagamento_br, tipo_doc, valor=""):
    url_webhook = "https://chat.googleapis.com/v1/spaces/AAQAgNP5PEc/messages?key=AIzaSyDdI0hCZtE6vySjMm-WEfRq3CPzqKqqsHI&token=Gd7fgaTDW6UlX6qE7twFulf4UnJy9sCeNASUNmTgHcA"
    
    txt_tipo = "🏦 *SOLICITAÇÃO DE CHEQUE*" if tipo_doc == 'CHEQUE' else "📄 *BOLETO DE INTERNET*"
    txt_valor = f"\n• *Valor:* R$ {valor}" if valor else ""
    
    mensagem = (
        f"🚨 {txt_tipo} *REGISTRADO PARA APROVAÇÃO!*\n\n"
        f"• *Imóvel:* {imovel}\n"
        f"• *Data Pagamento:* {data_pagamento_br}\n"
        f"• *Solicitante:* {solicitante}\n"
        f"• *Conta/Ref:* {conta} ({mes_ref}){txt_valor}\n"
        f"• *Justificativa:* {justificativa if justificativa else 'Sem justificativa.'}\n\n"
        f"👉 _Acesse a Fila de Aprovação no Lello Orchestrator para analisar e liberar o upload._"
    )
    
    try:
        threading.Thread(
            target=lambda: requests.post(url_webhook, json={"text": mensagem}, timeout=10),
            daemon=True
        ).start()
    except Exception as e:
        print(f"⚠️ Erro no webhook Google Chat: {e}")

@app.route('/api/internet/salvar', methods=['POST'])
@login_obrigatorio
def salvar_conta_internet():
    tipo_operacao = request.form.get('tipo_operacao')
    usuario_atual = session.get('usuario', 'Sistema')
    data_hora = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    data_hoje_br = datetime.now().strftime('%d/%m/%Y')

    try:
        # ==========================================
        # FLUXO 1: BOLETO NORMAL OU CHEQUE
        # ==========================================
        if tipo_operacao in ['internet', 'cheque']:
            imovel = request.form.get('imovel')
            carteira = request.form.get('carteira')
            complemento = request.form.get('complemento', '').strip()
            mes_ref = request.form.get('mes_ref')
            observacao = request.form.get('observacao_solicitante', '').strip()
            data_pagamento_raw = request.form.get('data_pagamento') # Vem 'YYYY-MM-DD'
            arquivo = request.files.get('boleto')

            imovel = (imovel or '').strip()
            carteira = (carteira or '').strip()
            if not imovel or not carteira:
                return jsonify({'sucesso': False, 'msg': 'Informe o Nº do Imóvel e a Carteira (pode digitar manualmente se o imóvel não estiver no cadastro).'})

            # 🎯 Imóvel fora do cadastro: a tela libera o preenchimento manual e marca este lançamento
            # para a gestão enxergar na Fila de Aprovação.
            if request.form.get('fora_cadastro') == '1':
                observacao = ('⚠️ IMÓVEL FORA DO CADASTRO (imóvel/carteira informados manualmente). ' + observacao).strip()

            if not arquivo or arquivo.filename == '':
                return jsonify({'sucesso': False, 'msg': 'O anexo do documento em PDF é obrigatório.'})

            if not data_pagamento_raw:
                return jsonify({'sucesso': False, 'msg': 'A Data de Pagamento é obrigatória.'})

            # 🛡️ Não permite agendar pagamento no passado (validação no servidor)
            data_ok, msg_data, _ = validar_data_pagamento(data_pagamento_raw)
            if not data_ok:
                return jsonify({'sucesso': False, 'msg': msg_data})

            # Converte YYYY-MM-DD para DD/MM/YYYY
            dt_p = datetime.strptime(data_pagamento_raw, '%Y-%m-%d')
            data_pagamento_br = dt_p.strftime('%d/%m/%Y')

            # Mês abreviado
            ano_str, mes_str = mes_ref.split('-')
            ano_abrev = ano_str[-2:]
            meses_abrev = {'01': 'JAN', '02': 'FEV', '03': 'MAR', '04': 'ABR', '05': 'MAI', '06': 'JUN', '07': 'JUL', '08': 'AGO', '09': 'SET', '10': 'OUT', '11': 'NOV', '12': 'DEZ'}
            mes_extenso = meses_abrev.get(mes_str, mes_str)

            if tipo_operacao == 'cheque':
                conta = "231" # 🎯 CHEQUE É SEMPRE CONTA 231
                valor = request.form.get('valor_cheque', '').strip()
                tipo_doc = "CHEQUE"
                
                # 🎯 Limpa espaços para o nome do arquivo ficar perfeito
                valor_arq = valor.replace(" ", "") if valor else "0,00"
                
                if complemento:
                    nome_final = f"CHEQUE-{imovel}-{carteira}-{complemento}-{mes_extenso}-{ano_abrev}-231-R${valor_arq}.pdf"
                else:
                    nome_final = f"CHEQUE-{imovel}-{carteira}-{mes_extenso}-{ano_abrev}-231-R${valor_arq}.pdf"
            else:
                conta = request.form.get('conta')
                valor = ""
                tipo_doc = "BOLETO"
                
                if complemento:
                    nome_final = f"{imovel}-{carteira}-{complemento}-{mes_extenso}-{ano_abrev}-{conta}.pdf"
                else:
                    nome_final = f"{imovel}-{carteira}-{mes_extenso}-{ano_abrev}-{conta}.pdf"

            # 🎯 Sufixo opcional no fim do nome do PDF. Dá para marcar MAIS DE UM motivo
            # (ex.: BAIXAR NA PLANILHA + N FIN) -> "...-N FIN-BAIXAR NA PLANILHA.pdf".
            # Ordem fixa (a mesma da tela), sem repetir, ignorando valores inválidos.
            campo_motivo = 'motivo_cheque' if tipo_operacao == 'cheque' else 'motivo_int'
            motivos_marcados = {m.strip().upper() for m in request.form.getlist(campo_motivo)}
            motivos_sel = [m for m in MOTIVOS_INTERNET_VALIDOS if m in motivos_marcados]
            if motivos_sel:
                nome_final = nome_final[:-4] + "-" + "-".join(motivos_sel) + ".pdf"

            arquivo_bytes = arquivo.read()

            conexao = conectar_banco()
            # Insere na fila de pendências salvando a data de pagamento para ordenação
            cursor_novo = conexao.execute('''
                INSERT INTO pendencias_internet 
                (imovel, carteira, mes_ref, conta, nome_arquivo, arquivo_bytes, observacao_solicitante, usuario_solicitante, data_solicitacao, data_pagamento, tipo_doc, valor) 
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (imovel, carteira, mes_ref, conta, nome_final, arquivo_bytes, observacao, usuario_atual, data_hora, data_pagamento_raw, tipo_doc, valor))
            pendencia_nova_id = cursor_novo.lastrowid
            conexao.commit()

            # ⚠️ Veio de uma inconsistência do Bradesco: vincula e põe "INTERNET dd/mm" na Observação da planilha
            msg_inc = ''
            if request.form.get('inc_aba'):
                try:
                    msg_inc = inc_vincular_internet(conexao, pendencia_nova_id, request.form, data_pagamento_br, usuario_atual.title())
                except Exception as erro_inc:
                    print(f'⚠️ Inc. Bradesco (envio pela Internet): {erro_inc}', flush=True)
                    msg_inc = ' (Aviso: não consegui vincular à planilha Inc. Bradesco; atue por lá.)'
            conexao.close()
            
            # Dispara webhook no Google Chat
            enviar_notificacao_google_chat(imovel, conta, mes_ref, usuario_atual, observacao, data_pagamento_br, tipo_doc, valor)
            
            return jsonify({'sucesso': True, 'msg': f'{tipo_doc.title()} enviado para a Fila de Aprovação da Gestão em ordem de pagamento!' + msg_inc})

        # ==========================================
        # FLUXO 2: SLIP MANUAL
        # ==========================================
        elif tipo_operacao == 'slip':
            valor = request.form.get('valor_multa')
            ref_deb = "LELLO" 
            conta_deb = request.form.get('conta_debito')
            ref_cred = (request.form.get('ref_credito') or '').strip()
            if not ref_cred:
                return jsonify({'sucesso': False, 'msg': 'Informe a Carteira do lançamento de Slip.'})
            conta_cred = request.form.get('conta_credito')
            hist = request.form.get('historico_texto')
            resp_juros_raw = request.form.get('resp_juros', 'JUROS LELLO')
            observacao = request.form.get('observacao_slip', '')
            resp_juros_final = "" if resp_juros_raw == "JUROS LELLO" else resp_juros_raw

            dados_debito = {'data_solicitacao': data_hoje_br, 'referencia': ref_deb, 'conta': conta_deb, 'valor': valor, 'historico': hist}
            dados_credito = {'referencia': ref_cred, 'conta': conta_cred, 'valor': valor, 'historico': hist}
            
            injetar_linha_planilha(dados_debito, dados_credito, resp_juros_final, observacao)
            return jsonify({'sucesso': True, 'msg': 'Lançamento de Slip efetuado na planilha com sucesso!'})

        # ==========================================
        # FLUXO 3: BAIXA MANUAL 275/276
        # ==========================================
        elif tipo_operacao == 'baixa_manual':
            carteira = request.form.get('carteira_baixa')
            valor = request.form.get('valor_baixa')
            conta = request.form.get('conta_baixa') 
            desc_historico = request.form.get('desc_baixa') 
            mes_ref = request.form.get('mes_ref_baixa') 

            if not all([carteira, valor, conta, desc_historico, mes_ref]):
                return jsonify({'sucesso': False, 'msg': 'Preencha todos os campos da baixa manual.'})

            ano_str, mes_str = mes_ref.split('-')
            ano_abrev = ano_str[-2:]
            meses_abrev = {'01': 'JAN', '02': 'FEV', '03': 'MAR', '04': 'ABR', '05': 'MAI', '06': 'JUN', '07': 'JUL', '08': 'AGO', '09': 'SET', '10': 'OUT', '11': 'NOV', '12': 'DEZ'}
            mes_extenso = meses_abrev.get(mes_str, mes_str)
            
            hist_debito_final = f"{desc_historico} {mes_extenso}-{ano_abrev}"
            hoje = datetime.now()
            data_solicitacao = hoje.strftime('%d/%m/%Y')
            dia_mes_ponto = hoje.strftime('%d.%m') 
            hist_credito_final = f"REG INTERNET {dia_mes_ponto}"

            dados_debito = {'data_solicitacao': data_solicitacao, 'referencia': carteira, 'conta': conta, 'valor': valor, 'historico': hist_debito_final}
            dados_credito = {'referencia': 'BRADESCO', 'conta': None, 'valor': valor, 'historico': hist_credito_final}

            injetar_linha_planilha(dados_debito, dados_credito, resp_juros=None, observacao=None)
            return jsonify({'sucesso': True, 'msg': 'Baixa registrada com sucesso na planilha de Slips!'})

        return jsonify({'sucesso': False, 'msg': 'Operação inválida.'})

    except Exception as e:
        print(f"Erro Internet/Cheque/Slip: {e}", flush=True)
        return jsonify({'sucesso': False, 'msg': str(e)})


@app.route('/api/slip/extrair_pdf', methods=['POST'])
@login_obrigatorio
def extrair_pdf_slip():
    arquivo = request.files.get('pdf_lote')
    if not arquivo or arquivo.filename == '':
        return jsonify({'sucesso': False, 'msg': 'Nenhum PDF enviado.'})
        
    try:
        pdf_bytes = arquivo.read()
        
        # Extrai o texto cru do PDF
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        texto_completo = ""
        for page in doc:
            texto_completo += page.get_text("text") + "\n"
        doc.close()

        # 🚀 RODÍZIO DE CHAVES DA API GEMINI (Load Balancer)
        CHAVES_GEMINI = [
            os.getenv("GEMINI_API_KEY_1"),
            os.getenv("GEMINI_API_KEY_2"),
            os.getenv("GEMINI_API_KEY_3"),
            os.getenv("GEMINI_API_KEY_APP")
        ]
        # Remove as chaves vazias caso não estejam configuradas no .env
        chaves_validas = [k for k in CHAVES_GEMINI if k]
        
        if not chaves_validas:
            return jsonify({'sucesso': False, 'msg': 'Nenhuma chave do Gemini configurada. Verifique o arquivo .env.'})
            
        # Sorteia uma chave para dividir a carga e não estourar o limite
        chave_escolhida = random.choice(chaves_validas)
        url_gemini = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={chave_escolhida}"
        
       # 🧠 PROMPT INTELIGENTE PARA A IA LER O PDF
        prompt = f"""
        Você é um assistente especialista em extração de dados financeiros.
        Vou te enviar o texto de um PDF com comprovantes de pagamento do Bradesco.
        
        Sua tarefa:
        1. Localize o campo "Descrição:" de cada comprovante (ele traz o formato 'carteira-imovel', ex: 51302-1 ou 40364-1).
        2. Extraia o valor COMPLETO da "Descrição", incluindo o traço e o dígito final. (Exemplo: se estiver '51302-1', retorne exatamente '51302-1').
        3. Localize os valores de "Juros:" e "Multa:" de cada comprovante.
        4. Some os valores de Juros e Multa.
        5. Se a SOMA for maior que ZERO, inclua na resposta. Formate o valor com vírgula (ex: "21,95").
        
        IMPORTANTE: Me retorne APENAS o JSON puro (um array de objetos). Sem introdução, sem marcação markdown (como ```json). 
        
        Formato obrigatório de saída:
        [
            {{"carteira": "51302-1", "valor": "21,95"}},
            {{"carteira": "40364-1", "valor": "13,40"}}
        ]
        
        Texto do PDF a ser analisado:
        {texto_completo}
        """
        
        payload = {"contents": [{"parts": [{"text": prompt}]}]}
        
       
        resposta = requests.post(url_gemini, json=payload, timeout=120)
        dados_ia = resposta.json()
        
        if 'candidates' in dados_ia and dados_ia['candidates']:
            texto_resposta = dados_ia['candidates'][0]['content']['parts'][0]['text'].strip()
            
            # Limpa qualquer formatação markdown residual que a IA tente colocar
            texto_resposta = texto_resposta.replace("```json", "").replace("```", "").strip()
            
            # Converte a String de volta para Array do Python
            resultados = json.loads(texto_resposta)
            
            if not resultados:
                return jsonify({'sucesso': False, 'msg': 'Nenhum boleto com Juros/Multa (acima de R$ 0,00) foi encontrado no PDF.'})
                
            return jsonify({'sucesso': True, 'dados': resultados})
        else:
            erro_msg = dados_ia.get('error', {}).get('message', 'Erro desconhecido na IA.')
            return jsonify({'sucesso': False, 'msg': f"Falha na IA: {erro_msg}"})
        
    except json.JSONDecodeError:
        return jsonify({'sucesso': False, 'msg': 'A IA não conseguiu formatar os dados corretamente. Tente novamente.'})
    except Exception as e:
        print(f"Erro ao extrair PDF via IA: {e}", flush=True)
        return jsonify({'sucesso': False, 'msg': f"Erro técnico: {str(e)}"})


# Adicione o escopo do Sheets junto com o do Drive
SCOPES = [
    'https://www.googleapis.com/auth/drive',
    'https://www.googleapis.com/auth/spreadsheets' # <-- NOVO
]

# ID da sua planilha (peguei da URL da sua imagem)
SPREADSHEET_ID = '1_fR87N2MLUh7eBfesAHlnLt6v51tpBTW6CaoQNOlUic'

def obter_servico_sheets():
    creds = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE, scopes=SCOPES)
    return build('sheets', 'v4', credentials=creds)

def injetar_linha_planilha(dados_debito, dados_credito, resp_juros="", observacao=""):
    """Insere uma nova linha na aba principal da planilha pulando a coluna protegida M"""
    sheets_service = obter_servico_sheets()
    
    # PASSO 1: Monta a linha apenas da Coluna A até a Coluna L
    linha_ate_L = [
        dados_debito['data_solicitacao'], # A
        None,                             # B (Pula)
        dados_debito['referencia'],       # C
        None,                             # D (Pula oculta)
        dados_debito['conta'],            # E
        dados_debito['valor'],            # F
        dados_debito['historico'],        # G
        dados_credito['referencia'],      # H
        dados_credito['conta'],           # I
        dados_credito['valor'],           # J
        dados_credito['historico'],       # K
        resp_juros                        # L
    ]
    
    body = {'values': [linha_ate_L]}
    
    # O robô faz o append travado na coluna L (A:L). Assim a proteção da M nem apita!
    result = sheets_service.spreadsheets().values().append(
        spreadsheetId=SPREADSHEET_ID,
        range="2026!A:L", 
        valueInputOption='USER_ENTERED',
        body=body
    ).execute()
    
    # PASSO 2: Se tiver observação, injetamos ela cirurgicamente na Coluna N
    if observacao:
        # Descobrimos qual linha o Google usou (ex: "2026!A150:L150")
        range_atualizado = result.get('updates', {}).get('updatedRange', '')
        
        if range_atualizado:
            import re
            # Puxamos só o número da linha (ex: 150)
            numero_linha = re.search(r'\d+', range_atualizado.split(':')[0]).group()
            
            # Montamos o alvo exato: 2026!N150
            range_obs = f"2026!N{numero_linha}"
            body_obs = {'values': [[observacao]]}
            
            # Usamos UPDATE em vez de APPEND para atirar a observação direto lá
            sheets_service.spreadsheets().values().update(
                spreadsheetId=SPREADSHEET_ID,
                range=range_obs,
                valueInputOption='USER_ENTERED',
                body=body_obs
            ).execute()
            
    return result


@app.route('/api/baixa_manual/extrair_pdf', methods=['POST'])
@login_obrigatorio
def extrair_pdf_baixa_manual():
    """Lê o PDF de retorno e usa a API REST do Gemini 2.5 Flash com rotação de chaves para classificar (275/276)"""
    arquivo = request.files.get('pdf_baixa_lote')
    if not arquivo:
        return jsonify({'sucesso': False, 'msg': 'Selecione um arquivo PDF.'})

    try:
        import pdfplumber
        import json
        import os
        import random
        import requests

        # 🔑 ROTAÇÃO DE CHAVES DE API
        chaves = [
            os.getenv('GEMINI_API_KEY_1'),
            os.getenv('GEMINI_API_KEY_2'),
            os.getenv('GEMINI_API_KEY_3')
        ]
        chaves_validas = [k for k in chaves if k]

        if not chaves_validas:
            return jsonify({'sucesso': False, 'msg': 'Nenhuma chave de API (GEMINI_API_KEY_1, 2 ou 3) encontrada no ambiente.'})

        # Sorteia uma chave válida a cada requisição para distribuir o uso
        chave_escolhida = random.choice(chaves_validas)

        # 1. Extrai texto bruto do PDF
        texto_pdf = ""
        with pdfplumber.open(arquivo) as pdf:
            for pagina in pdf.pages:
                texto = pagina.extract_text()
                if texto:
                    texto_pdf += texto + "\n"

        if not texto_pdf.strip():
            return jsonify({'sucesso': False, 'msg': 'O PDF está vazio ou não possui texto selecionável.'})

        # 2. Monta o Prompt com as regras de soma e separação
        prompt = f"""
        Você é um assistente financeiro especialista em conciliação imobiliária corporativa.
        Analise o texto abaixo extraído de um PDF de retorno/baixa e extraia as cobranças.

        🎯 REGRAS DE SOMA E CLASSIFICAÇÃO:
        Para cada 'Carteira' (ou referência de imóvel) encontrada, você deve separar as despesas do boleto em DUAS categorias e SOMAR os valores dentro delas:

        1. CONTA 275 (INQUILINO):
           - O que entra: Condomínio, 13º salário, cotas ordinárias, consumo (água, gás, luz), multas, juros e despesas padrão do morador.
           - Ação: SOMA TODOS esses valores em um único total.
           - Conta: "275"
           - Descrição obrigatória: "PAG COTA"

        2. CONTA 276 (PROPRIETÁRIO):
           - O que entra: Fundo de Reserva, Obras, Benfeitorias, Pintura, rateios extraordinários e despesas estruturais do proprietário.
           - Ação: SOMA TODOS esses valores em um único total separado.
           - Conta: "276"
           - Descrição obrigatória: "PAG FDO DE RESERVA"

        * Se uma mesma carteira tiver despesas de inquilino e de proprietário, retorne DOIS blocos separados para essa mesma carteira: um com a soma da 275 e outro com a soma da 276.

        Retorne APENAS um array JSON válido, sem NENHUM texto adicional ou formatação markdown, seguindo exatamente esta estrutura:
        [
            {{
                "carteira": "numero da referencia ou carteira",
                "valor": "valor SOMADO com vírgula (ex: 450,00)",
                "conta": "275",
                "descricao": "PAG COTA"
            }},
            {{
                "carteira": "numero da referencia ou carteira",
                "valor": "valor SOMADO com vírgula (ex: 50,00)",
                "conta": "276",
                "descricao": "PAG FDO DE RESERVA"
            }}
        ]

        Texto do PDF para análise:
        {texto_pdf}
        """

        # 3. Requisição HTTP direta para o Gemini 2.5 Flash
        url_gemini = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={chave_escolhida}"
        headers = {'Content-Type': 'application/json'}
        payload = {
            "contents": [
                {
                    "parts": [{"text": prompt}]
                }
            ]
        }

        response = requests.post(url_gemini, headers=headers, json=payload, timeout=60)

        if response.status_code != 200:
            return jsonify({'sucesso': False, 'msg': f'Erro HTTP {response.status_code} na API do Gemini: {response.text}'})

        res_data = response.json()
        texto_resposta = res_data['candidates'][0]['content']['parts'][0]['text'].strip()

        # Limpeza de blocos de código Markdown
        if texto_resposta.startswith("```json"):
            texto_resposta = texto_resposta[7:-3]
        elif texto_resposta.startswith("```"):
            texto_resposta = texto_resposta[3:-3]

        dados_extraidos = json.loads(texto_resposta.strip())

        if not dados_extraidos:
            return jsonify({'sucesso': False, 'msg': 'A IA não conseguiu identificar lançamentos válidos neste PDF.'})

        return jsonify({'sucesso': True, 'dados': dados_extraidos})

    except Exception as e:
        print(f"Erro IA Baixa Lote (REST): {e}", flush=True)
        return jsonify({'sucesso': False, 'msg': f'Erro no processamento da IA: {str(e)}'})


@app.route('/api/sistema/recuperar_emissoes')
@login_obrigatorio
def recuperar_emissoes_perdidas():
    conexao = conectar_banco()
    
    # 1. Pega todas as emissões válidas que ficaram perdidas no histórico dos meses passados
    query = """
        SELECT imovel, media_emissao 
        FROM imoveis 
        WHERE media_emissao IS NOT NULL 
          AND media_emissao != '' 
        ORDER BY id ASC
    """
    historico = conexao.execute(query).fetchall()
    
    recuperados = 0
    for row in historico:
        imovel = row['imovel']
        media = row['media_emissao']
        
        # Insere no mestre garantindo que o imóvel exista definitivamente
        conexao.execute('INSERT OR IGNORE INTO cadastro_mestre_imoveis (imovel) VALUES (?)', (imovel,))
        conexao.execute('UPDATE cadastro_mestre_imoveis SET media_emissao = ? WHERE imovel = ?', (media, imovel))
        
        # Aproveita e já injeta a data de volta na tela dos operadores para o mês atual
        cursor = conexao.execute("UPDATE imoveis SET media_emissao = ? WHERE imovel = ? AND ativo = 1 AND (media_emissao IS NULL OR media_emissao = '')", (media, imovel))
        if cursor.rowcount > 0:
            recuperados += 1
            
    conexao.commit()
    conexao.close()
    
    return f"""
        <div style='font-family: Arial; text-align: center; margin-top: 50px;'>
            <h1 style='color: #10b981;'>✔️ Emissões Recuperadas com Sucesso!</h1>
            <p>O sistema varreu todo o histórico de meses anteriores e recuperou as datas de emissão de <b>{recuperados}</b> imóveis que estavam invisíveis no painel atual.</p>
            <p>O Cadastro Mestre foi atualizado e elas nunca mais sumirão nas próximas viradas de mês.</p>
            <a href='/' style='display: inline-block; margin-top: 20px; padding: 10px 20px; background-color: #0f172a; color: white; text-decoration: none; border-radius: 6px;'>Voltar ao Painel Operacional</a>
        </div>
    """

@app.route('/api/internet/visualizar_pdf_pendente/<int:pendencia_id>')
@login_obrigatorio
def visualizar_pdf_pendente_internet(pendencia_id):
    conexao = conectar_banco()
    item = conexao.execute("SELECT arquivo_bytes, nome_arquivo FROM pendencias_internet WHERE id = ?", (pendencia_id,)).fetchone()
    conexao.close()
    
    if item and item['arquivo_bytes']:
        return send_file(BytesIO(item['arquivo_bytes']), mimetype='application/pdf', download_name=item['nome_arquivo'])
    return "Arquivo não localizado", 404

@app.route('/api/internet/aprovar_pendencia', methods=['POST'])
@login_obrigatorio
def aprovar_pendencia_internet():
    dados = request.get_json(silent=True) or {}
    pendencia_id = dados.get('id')
    usuario_atual = session.get('usuario', 'Sistema').title()
    data_hora = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    conexao = conectar_banco()
    item = conexao.execute("SELECT * FROM pendencias_internet WHERE id = ?", (pendencia_id,)).fetchone()
    
    if not item:
        conexao.close()
        return jsonify({'sucesso': False, 'msg': 'Solicitação não encontrada.'})

    if item['status'] != 'PENDENTE':
        conexao.close()
        return jsonify({'sucesso': False, 'msg': f"Esta solicitação já foi processada (status: {item['status']}). Atualize a página."})

    # 🔒 TRAVA ATÔMICA: só quem conseguir mudar PENDENTE -> PROCESSANDO segue adiante.
    # Se alguém clicar 2x (ou em outra aba), o segundo pedido não passa daqui e o arquivo não duplica no Drive.
    claim = conexao.execute("UPDATE pendencias_internet SET status = 'PROCESSANDO' WHERE id = ? AND status = 'PENDENTE'", (pendencia_id,))
    conexao.commit()
    if claim.rowcount == 0:
        conexao.close()
        return jsonify({'sucesso': False, 'msg': 'Esta solicitação já está sendo aprovada (outro clique ou outra aba). Atualize a página.'})

    upload_feito = False
    link_drive = None

    def devolver_para_fila():
        try:
            conexao.execute("UPDATE pendencias_internet SET status = 'PENDENTE' WHERE id = ? AND status = 'PROCESSANDO'", (pendencia_id,))
            conexao.commit()
        except Exception as e_rev:
            print(f"Erro ao devolver pendência {pendencia_id} para a fila: {e_rev}", flush=True)

    try:
        if item['data_pagamento'] and len(str(item['data_pagamento'])) >= 10:
            data_ok, msg_data, _ = validar_data_pagamento(item['data_pagamento'])
            if not data_ok:
                devolver_para_fila()
                return jsonify({'sucesso': False, 'msg': msg_data + ' Clique em ✏️ Editar, corrija a data e aprove novamente.'})
            dt_p = datetime.strptime(str(item['data_pagamento'])[:10], '%Y-%m-%d')
            data_pagamento_br = dt_p.strftime('%d/%m/%Y')
        else:
            data_pagamento_br = datetime.now().strftime('%d/%m/%Y')

        # 🎯 MÁGICA DA SEPARAÇÃO: Define o nome da pasta com base no tipo de documento
        tipo_doc_bd = item['tipo_doc'] if 'tipo_doc' in item.keys() and item['tipo_doc'] else 'BOLETO'
        nome_subpasta_drive = "CHEQUE COND" if tipo_doc_bd == "CHEQUE" else "COND"

        # Envio definitivo ao Google Drive (Passando o nome da subpasta)
        id_pasta_destino = encontrar_pasta_cond_por_data(data_pagamento_br, nome_subpasta_drive)
        drive_service = obter_servico_drive()
        
        file_metadata = {'name': item['nome_arquivo'], 'parents': [id_pasta_destino]}
        media = MediaIoBaseUpload(BytesIO(item['arquivo_bytes']), mimetype='application/pdf', resumable=True)
        
        arquivo_enviado = drive_service.files().create(
            body=file_metadata, media_body=media, fields='id, webViewLink', supportsAllDrives=True
        ).execute()
        link_drive = arquivo_enviado.get('webViewLink')
        upload_feito = True

        # Grava no histórico geral do sistema
        conexao.execute('''
            INSERT INTO contas_internet (imovel, carteira, mes_ref, conta, nome_arquivo, link_drive, usuario, data_registro) 
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (item['imovel'], item['carteira'], item['mes_ref'], item['conta'], item['nome_arquivo'], link_drive, item['usuario_solicitante'], data_hora))
        
        # Atualiza o prontuário do imóvel com a informação de qual pasta foi
        imovel_bd = conexao.execute("SELECT id FROM imoveis WHERE LTRIM(imovel, '0') = LTRIM(?, '0') AND ativo = 1 LIMIT 1", (item['imovel'],)).fetchone()
        if imovel_bd:
            conexao.execute("INSERT INTO historico_observacoes (imovel_id, data_hora, usuario, texto) VALUES (?, ?, ?, ?)", 
                            (imovel_bd['id'], data_hora, usuario_atual, f"{tipo_doc_bd.title()} APROVADO ({item['conta']}) p/ pagamento em {data_pagamento_br}. Enviado p/ Drive (Pasta: {nome_subpasta_drive}) por {usuario_atual}."))

        # ATUALIZA A FILA COM OS DADOS DE QUEM APROVOU E QUANDO
        conexao.execute(
            "UPDATE pendencias_internet SET status = 'APROVADO', usuario_avaliador = ?, data_avaliacao = ? WHERE id = ?", 
            (usuario_atual, data_hora, pendencia_id)
        )
        conexao.commit()
        try:
            inc_desfecho_internet(item, usuario_atual, 'APROVADO')       # inconsistência do Bradesco: Situação OK na planilha
        except Exception as erro_inc:
            print(f'⚠️ Inc. Bradesco (aprovação): {erro_inc}', flush=True)
        
        # =========================================================================
        # 📩 DISPARO DE E-MAIL DE APROVAÇÃO (Assíncrono)
        # =========================================================================
        solicitante = str(item['usuario_solicitante']).strip()
        solicitante_clean = solicitante.lower()
        imovel_num = item['imovel']
        conta_tipo = item['conta']
        mes_ref = item['mes_ref']

        # Descobre o e-mail de quem pediu
        if solicitante_clean in MAPA_EMAILS_USUARIOS:
            email_solicitante = MAPA_EMAILS_USUARIOS[solicitante_clean]
        elif '@' in solicitante_clean:
            email_solicitante = solicitante_clean
        else:
            email_solicitante = f"{solicitante_clean}@lello.com.br"

        # Junta Solicitante + Arthur em cópia
        emails_notificacao = list(set([email_solicitante, MAPA_EMAILS_USUARIOS.get('arthur', 'arthur.varo@lello.com.br')]))
        destinatarios_str = ", ".join(emails_notificacao)

        assunto_email = f"✅ Aprovação de {tipo_doc_bd.title()} - Imóvel {imovel_num}"
        
        corpo_html = f"""
        <div style="font-family: Arial, sans-serif; color: #334155; max-width: 600px; padding: 20px; border: 1px solid #e2e8f0; border-radius: 12px; background-color: #f8fafc;">
            <h3 style="color: #10b981; margin-top: 0;">✅ {tipo_doc_bd.title()} Aprovado</h3>
            <p>A solicitação de envio de <b>{tipo_doc_bd.lower()}</b> para o imóvel <b>{imovel_num}</b> foi <b>APROVADA</b> pela gestão e o arquivo foi salvo no Google Drive.</p>
            
            <table style="width: 100%; border-collapse: collapse; margin: 15px 0; background-color: #ffffff; border-radius: 8px; overflow: hidden; border: 1px solid #cbd5e1;">
                <tr style="background-color: #0f172a; color: #ffffff;">
                    <th colspan="2" style="padding: 10px; text-align: left;">Detalhes do Lançamento</th>
                </tr>
                <tr>
                    <td style="padding: 8px 12px; border-bottom: 1px solid #e2e8f0; font-weight: bold;">Nº Imóvel:</td>
                    <td style="padding: 8px 12px; border-bottom: 1px solid #e2e8f0;">{imovel_num}</td>
                </tr>
                <tr>
                    <td style="padding: 8px 12px; border-bottom: 1px solid #e2e8f0; font-weight: bold;">Solicitante:</td>
                    <td style="padding: 8px 12px; border-bottom: 1px solid #e2e8f0;">{solicitante.title()}</td>
                </tr>
                <tr>
                    <td style="padding: 8px 12px; border-bottom: 1px solid #e2e8f0; font-weight: bold;">Conta / Mês:</td>
                    <td style="padding: 8px 12px; border-bottom: 1px solid #e2e8f0;">Conta {conta_tipo} ({mes_ref})</td>
                </tr>
                <tr>
                    <td style="padding: 8px 12px; border-bottom: 1px solid #e2e8f0; font-weight: bold;">Data de Pagamento:</td>
                    <td style="padding: 8px 12px; border-bottom: 1px solid #e2e8f0; font-weight: bold;">{data_pagamento_br}</td>
                </tr>
                <tr>
                    <td style="padding: 8px 12px; font-weight: bold;">Aprovador (Gestão):</td>
                    <td style="padding: 8px 12px;">{usuario_atual}</td>
                </tr>
            </table>
            
            <p style="font-size: 0.85rem; color: #64748b;">Este documento já foi processado e encaminhado para a pasta correspondente <b>({nome_subpasta_drive})</b>.</p>
            <hr style="border: none; border-top: 1px solid #cbd5e1; margin-top: 20px;">
            <small style="color: #94a3b8;">Lello Orchestrator - Notificação Automática</small>
        </div>
        """
        
        # Dispara o e-mail em background sem travar a tela
        threading.Thread(
            target=enviar_email_smtp, 
            args=(destinatarios_str, assunto_email, corpo_html), 
            daemon=True
        ).start()
        # =========================================================================

        return jsonify({'sucesso': True, 'msg': f'Documento aprovado e enviado para a pasta {nome_subpasta_drive} de {data_pagamento_br} no Drive!'})

    except Exception as e:
        print(f"Erro ao aprovar internet: {e}", flush=True)
        try: conexao.rollback()
        except Exception: pass
        devolver_para_fila()
        if upload_feito:
            msg_erro = f"O arquivo JÁ FOI enviado ao Drive ({link_drive}), mas deu erro ao registrar: {str(e)}. Confira a pasta antes de aprovar de novo para não duplicar."
        else:
            msg_erro = f"Erro no envio pro Drive: {str(e)}"
        return jsonify({'sucesso': False, 'msg': msg_erro})
    finally:
        conexao.close()


@app.route('/api/internet/editar_pendencia', methods=['POST'])
@login_obrigatorio
def editar_pendencia_internet():
    """Permite à gestão corrigir uma solicitação PENDENTE (data de pagamento, imóvel, conta etc.)."""
    if not usuario_pode_editar_internet():
        return jsonify({'sucesso': False, 'msg': 'Apenas Ryan, Marcio e Arthur podem editar solicitações.'}), 403

    dados = request.get_json(silent=True) or {}
    pendencia_id = dados.get('id')
    usuario_atual = session.get('usuario', 'Sistema').title()
    data_hora = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    conexao = conectar_banco()
    try:
        item = conexao.execute("SELECT * FROM pendencias_internet WHERE id = ?", (pendencia_id,)).fetchone()
        if not item:
            return jsonify({'sucesso': False, 'msg': 'Solicitação não encontrada.'})
        if item['status'] != 'PENDENTE':
            return jsonify({'sucesso': False, 'msg': 'Só é possível editar solicitações que ainda estão pendentes.'})

        tipo_doc = item['tipo_doc'] if 'tipo_doc' in item.keys() and item['tipo_doc'] else 'BOLETO'
        eh_cheque = (tipo_doc == 'CHEQUE')

        campos_editaveis = ['data_pagamento', 'imovel', 'carteira', 'mes_ref', 'conta', 'valor', 'nome_arquivo', 'observacao_solicitante']
        novos = {c: str(dados.get(c) if dados.get(c) is not None else '').strip() for c in campos_editaveis if c in dados}

        # Cheque: conta é sempre 231 e valor só existe no cheque
        if eh_cheque:
            novos.pop('conta', None)
        else:
            novos.pop('valor', None)

        if 'imovel' in novos and not novos['imovel'].isdigit():
            return jsonify({'sucesso': False, 'msg': 'O Nº do imóvel deve conter apenas números.'})
        if 'mes_ref' in novos and not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])', novos['mes_ref']):
            return jsonify({'sucesso': False, 'msg': 'Mês de referência inválido.'})
        if 'conta' in novos and novos['conta'] not in CONTAS_INTERNET_VALIDAS:
            return jsonify({'sucesso': False, 'msg': 'Conta inválida.'})
        if 'observacao_solicitante' in novos and not novos['observacao_solicitante']:
            return jsonify({'sucesso': False, 'msg': 'A justificativa não pode ficar vazia.'})
        if 'nome_arquivo' in novos:
            nome = novos['nome_arquivo']
            if not nome:
                return jsonify({'sucesso': False, 'msg': 'O nome do arquivo não pode ficar vazio.'})
            if re.search(r'[\\/:*?"<>|]', nome):
                return jsonify({'sucesso': False, 'msg': 'O nome do arquivo tem caracteres inválidos (\\ / : * ? " < > |).'})
            if not nome.lower().endswith('.pdf'):
                novos['nome_arquivo'] = nome + '.pdf'

        # A data só é revalidada se foi alterada (assim dá pra editar outros campos de um item antigo)
        if 'data_pagamento' in novos:
            if novos['data_pagamento'] == str(item['data_pagamento'] or '')[:10]:
                novos.pop('data_pagamento')
            else:
                data_ok, msg_data, _ = validar_data_pagamento(novos['data_pagamento'])
                if not data_ok:
                    return jsonify({'sucesso': False, 'msg': msg_data})

        alteracoes = {c: v for c, v in novos.items() if str(item[c] if item[c] is not None else '').strip() != v}
        if not alteracoes:
            return jsonify({'sucesso': False, 'msg': 'Nenhuma alteração foi feita.'})

        sets = ', '.join(f"{c} = ?" for c in alteracoes)
        conexao.execute(f"UPDATE pendencias_internet SET {sets} WHERE id = ?", (*alteracoes.values(), pendencia_id))

        # Registra no prontuário do imóvel o que foi alterado
        rotulos = {'data_pagamento': 'Data Pgto', 'imovel': 'Imóvel', 'carteira': 'Carteira', 'mes_ref': 'Mês Ref',
                   'conta': 'Conta', 'valor': 'Valor', 'nome_arquivo': 'Arquivo', 'observacao_solicitante': 'Justificativa'}

        def _fmt(campo, valor):
            valor = '' if valor is None else str(valor)
            if campo == 'data_pagamento' and len(valor) >= 10:
                return f"{valor[8:10]}/{valor[5:7]}/{valor[0:4]}"
            return valor or '(vazio)'

        resumo = '; '.join(f"{rotulos[c]}: {_fmt(c, item[c])} → {_fmt(c, v)}" for c, v in alteracoes.items())
        imovel_ref = alteracoes.get('imovel', item['imovel'])
        imovel_bd = conexao.execute("SELECT id FROM imoveis WHERE LTRIM(imovel, '0') = LTRIM(?, '0') AND ativo = 1 LIMIT 1", (imovel_ref,)).fetchone()
        if imovel_bd:
            conexao.execute("INSERT INTO historico_observacoes (imovel_id, data_hora, usuario, texto) VALUES (?, ?, ?, ?)",
                            (imovel_bd['id'], data_hora, usuario_atual, f"✏️ Solicitação de {tipo_doc.title()} editada por {usuario_atual} antes da aprovação. {resumo}"))

        conexao.commit()
        return jsonify({'sucesso': True, 'msg': 'Solicitação atualizada com sucesso!'})

    except Exception as e:
        print(f"Erro ao editar pendência internet: {e}", flush=True)
        return jsonify({'sucesso': False, 'msg': f'Erro ao editar: {str(e)}'})
    finally:
        conexao.close()

# Mapeamento oficial de e-mails corporativos da equipe
MAPA_EMAILS_USUARIOS = {
    'arthur': 'arthur.varo@lello.com.br',
    'ryan': 'ryan.lima@lello.com.br',
    'marcio': 'marcio.teixeira@lello.com.br',
    'jessica': 'jessica.rufino@lello.com.br',
    'ellen': 'ellen.soares@lello.com.br',
    'victor': 'victor.lucca@lello.com.br',
    'heloisa': 'heloisa.oliveira@lello.com.br',
    'joao': 'joao.santana@lello.com.br'
}

@app.route('/api/internet/recusar_pendencia', methods=['POST'])
@login_obrigatorio
def recusar_pendencia_internet():
    dados = request.get_json(silent=True) or {}
    pendencia_id = dados.get('id')
    motivo = dados.get('motivo', 'Não especificado').strip()
    usuario_atual = session.get('usuario', 'Sistema').title()
    data_hora = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    conexao = conectar_banco()
    item = conexao.execute("SELECT * FROM pendencias_internet WHERE id = ?", (pendencia_id,)).fetchone()
    
    if item:
        solicitante = str(item['usuario_solicitante']).strip()
        solicitante_clean = solicitante.lower()
        imovel_num = item['imovel']
        conta_tipo = item['conta']
        mes_ref = item['mes_ref']
        
        # Registra no prontuário
        imovel_bd = conexao.execute("SELECT id FROM imoveis WHERE LTRIM(imovel, '0') = LTRIM(?, '0') AND ativo = 1 LIMIT 1", (imovel_num,)).fetchone()
        if imovel_bd:
            conexao.execute(
                "INSERT INTO historico_observacoes (imovel_id, data_hora, usuario, texto) VALUES (?, ?, ?, ?)", 
                (imovel_bd['id'], data_hora, usuario_atual, f"⚠️ Solicitação de Boleto Internet RECUSADA por {usuario_atual}. Motivo: {motivo}")
            )
            
        # 🎯 ATUALIZA A FILA REGISTRANDO A RECUSA, QUEM RECUSOU E O MOTIVO
        conexao.execute(
            "UPDATE pendencias_internet SET status = 'RECUSADO', usuario_avaliador = ?, data_avaliacao = ?, motivo_recusa = ? WHERE id = ?", 
            (usuario_atual, data_hora, motivo, pendencia_id)
        )
        try:
            inc_desfecho_internet(item, usuario_atual, 'RECUSADO')       # inconsistência do Bradesco: tira o "INTERNET dd/mm"
        except Exception as erro_inc:
            print(f'⚠️ Inc. Bradesco (recusa): {erro_inc}', flush=True)
        conexao.commit()
        conexao.close()

        # E-mails de notificação
        if solicitante_clean in MAPA_EMAILS_USUARIOS:
            email_solicitante = MAPA_EMAILS_USUARIOS[solicitante_clean]
        elif '@' in solicitante_clean:
            email_solicitante = solicitante_clean
        else:
            email_solicitante = f"{solicitante_clean}@lello.com.br"

        emails_notificacao = list(set([email_solicitante, MAPA_EMAILS_USUARIOS['arthur']]))
        destinatarios_str = ", ".join(emails_notificacao)

        assunto_email = f"⚠️ Solicitação de Boleto Internet Recusada - Imóvel {imovel_num}"
        
        corpo_html = f"""
        <div style="font-family: Arial, sans-serif; color: #334155; max-width: 600px; padding: 20px; border: 1px solid #e2e8f0; border-radius: 12px; background-color: #f8fafc;">
            <h3 style="color: #ef4444; margin-top: 0;">⚠️ Solicitação de Boleto Recusada</h3>
            <p>A solicitação de envio de boleto/documento para o imóvel <b>{imovel_num}</b> foi <b>RECUSADA</b> pela gestão.</p>
            
            <table style="width: 100%; border-collapse: collapse; margin: 15px 0; background-color: #ffffff; border-radius: 8px; overflow: hidden; border: 1px solid #cbd5e1;">
                <tr style="background-color: #0f172a; color: #ffffff;">
                    <th colspan="2" style="padding: 10px; text-align: left;">Detalhes da Solicitação</th>
                </tr>
                <tr>
                    <td style="padding: 8px 12px; border-bottom: 1px solid #e2e8f0; font-weight: bold;">Nº Imóvel:</td>
                    <td style="padding: 8px 12px; border-bottom: 1px solid #e2e8f0;">{imovel_num}</td>
                </tr>
                <tr>
                    <td style="padding: 8px 12px; border-bottom: 1px solid #e2e8f0; font-weight: bold;">Solicitante:</td>
                    <td style="padding: 8px 12px; border-bottom: 1px solid #e2e8f0;">{solicitante.title()}</td>
                </tr>
                <tr>
                    <td style="padding: 8px 12px; border-bottom: 1px solid #e2e8f0; font-weight: bold;">Conta / Mês:</td>
                    <td style="padding: 8px 12px; border-bottom: 1px solid #e2e8f0;">Conta {conta_tipo} ({mes_ref})</td>
                </tr>
                <tr>
                    <td style="padding: 8px 12px; border-bottom: 1px solid #e2e8f0; font-weight: bold;">Avaliador:</td>
                    <td style="padding: 8px 12px; border-bottom: 1px solid #e2e8f0;">{usuario_atual}</td>
                </tr>
                <tr>
                    <td style="padding: 8px 12px; font-weight: bold; color: #dc2626;">Motivo da Recusa:</td>
                    <td style="padding: 8px 12px; color: #dc2626; font-weight: bold;">{motivo}</td>
                </tr>
            </table>
            
            <p style="font-size: 0.85rem; color: #64748b;">Acesse o Módulo Internet para conferir o prontuário ou ajustar os dados do lançamento.</p>
            <hr style="border: none; border-top: 1px solid #cbd5e1; margin-top: 20px;">
            <small style="color: #94a3b8;">Lello Orchestrator - Notificação Automática</small>
        </div>
        """
        
        threading.Thread(
            target=enviar_email_smtp, 
            args=(destinatarios_str, assunto_email, corpo_html), 
            daemon=True
        ).start()

        return jsonify({'sucesso': True, 'msg': f'Solicitação recusada. Notificação enviada para {destinatarios_str}.'})

    conexao.close()
    return jsonify({'sucesso': False, 'msg': 'Item não encontrado.'})


@app.route('/api/sistema/relatorio_lotes')
@login_obrigatorio
def api_relatorio_lotes():
    conexao = conectar_banco()
    
    # Verifica se o usuário ligou o botão de filtro antecipado na interface
    antecipado = request.args.get('antecipado', 'false') == 'true'
    
    # Tenta descobrir o mês principal de vencimento da base atual (ex: 09/2026)
    try:
        mes_vencimento = conexao.execute("SELECT SUBSTR(vencimento, 4, 2) as m, SUBSTR(vencimento, 7, 4) as a FROM imoveis WHERE ativo=1 AND vencimento != '' AND vencimento IS NOT NULL LIMIT 1").fetchone()
    except:
        mes_vencimento = None
        
    data_corte = '2099-12-31 23:59:59' # Padrão (Sem corte)
    
    if antecipado and mes_vencimento:
        m = str(mes_vencimento['m'] or '').strip()
        a = str(mes_vencimento['a'] or '').strip()
        if a and len(a) == 2: a = "20" + a
        if m and a and m.isdigit() and a.isdigit():
            # O limite é o primeiro dia do mês de vencimento (ex: 2026-09-01 00:00:00)
            data_corte = f"{a}-{m.zfill(2)}-01 00:00:00"

    if antecipado:
        # Conta apenas coletas que ocorreram ANTES do mês de vencimento virar (No histórico de Produtividade)
        query = """
            SELECT 
                CASE 
                    WHEN CAST(i.d_lote AS INTEGER) BETWEEN 1 AND 5 THEN '01-05'
                    WHEN CAST(i.d_lote AS INTEGER) BETWEEN 6 AND 10 THEN '06-10'
                    WHEN CAST(i.d_lote AS INTEGER) BETWEEN 11 AND 15 THEN '11-15'
                    WHEN CAST(i.d_lote AS INTEGER) BETWEEN 16 AND 20 THEN '16-20'
                    WHEN CAST(i.d_lote AS INTEGER) BETWEEN 21 AND 29 THEN '21-29'
                    ELSE '30 - Aluguel Tranquilo' 
                END as lote_agrupado,
                IFNULL(NULLIF(SUBSTR(TRIM(i.media_emissao), 1, 2), ''), '0') as emissao,
                IFNULL(NULLIF(SUBSTR(TRIM(i.vencimento), 1, 2), ''), '0') as vencimento,
                COUNT(DISTINCT i.id) as total_imoveis,
                COUNT(DISTINCT CASE WHEN p.data_hora < ? THEN i.id END) as total_coletados
            FROM imoveis i
            LEFT JOIN produtividade p ON i.id = p.imovel_id AND p.acao IN ('COLETOU BOLETO', 'PAGOU BOLETO', 'PAGOU BOLETO NA 275', 'ALTEROU STATUS PARA PAGO NA ADM')
            WHERE i.ativo = 1
              AND i.administradora IS NOT NULL 
              AND TRIM(i.administradora) != ''
            GROUP BY lote_agrupado, emissao, vencimento
            ORDER BY lote_agrupado ASC, CAST(emissao AS INTEGER) ASC, CAST(vencimento AS INTEGER) ASC
        """
        dados_raw = conexao.execute(query, (data_corte,)).fetchall()
    else:
        # Conta o volume total coletado independente da data (Visão padrão do dia a dia)
        query = """
            SELECT 
                CASE 
                    WHEN CAST(i.d_lote AS INTEGER) BETWEEN 1 AND 5 THEN '01-05'
                    WHEN CAST(i.d_lote AS INTEGER) BETWEEN 6 AND 10 THEN '06-10'
                    WHEN CAST(i.d_lote AS INTEGER) BETWEEN 11 AND 15 THEN '11-15'
                    WHEN CAST(i.d_lote AS INTEGER) BETWEEN 16 AND 20 THEN '16-20'
                    WHEN CAST(i.d_lote AS INTEGER) BETWEEN 21 AND 29 THEN '21-29'
                    ELSE '30 - Aluguel Tranquilo' 
                END as lote_agrupado,
                IFNULL(NULLIF(SUBSTR(TRIM(i.media_emissao), 1, 2), ''), '0') as emissao,
                IFNULL(NULLIF(SUBSTR(TRIM(i.vencimento), 1, 2), ''), '0') as vencimento,
                COUNT(i.id) as total_imoveis,
                SUM(CASE WHEN UPPER(TRIM(i.status_interno)) IN ('PAGO', 'COLETADO', 'PAGO NA 275', 'PAGO NA ADM') THEN 1 ELSE 0 END) as total_coletados
            FROM imoveis i
            WHERE i.ativo = 1
              AND i.administradora IS NOT NULL 
              AND TRIM(i.administradora) != ''
            GROUP BY lote_agrupado, emissao, vencimento
            ORDER BY lote_agrupado ASC, CAST(emissao AS INTEGER) ASC, CAST(vencimento AS INTEGER) ASC
        """
        dados_raw = conexao.execute(query).fetchall()
        
    conexao.close()
    
    relatorio = []
    for row in dados_raw:
        tot = int(row['total_imoveis'] or 0)
        col = int(row['total_coletados'] or 0)
        pend = tot - col
        pct = round((col / tot * 100), 2) if tot > 0 else 0
        
        relatorio.append({
            'lote': str(row['lote_agrupado']),
            'emissao': str(row['emissao']).zfill(2),
            'vencimento': str(row['vencimento']).zfill(2),
            'total': tot,
            'coletados': col,
            'pendentes': pend,
            'pct': format(pct, ".2f").replace('.', ',')
        })
        
    return jsonify({'sucesso': True, 'dados': relatorio})

@app.route('/api/sistema/visao_fim_de_mes')
@login_obrigatorio
def api_visao_fim_de_mes():
    conexao = conectar_banco()
    
    # 1. Obter meses disponíveis para o Dropdown (Ex: 09/2026, 10/2026)
    meses_raw = conexao.execute("SELECT DISTINCT SUBSTR(vencimento, 4, 2) as m, SUBSTR(vencimento, 7, 4) as a FROM imoveis WHERE vencimento != '' AND vencimento IS NOT NULL").fetchall()
    
    meses_disponiveis = []
    for r in meses_raw:
        m = str(r['m']).strip().zfill(2)
        a = str(r['a']).strip()
        if len(a) == 2: a = "20" + a
        if m.isdigit() and a.isdigit():
            meses_disponiveis.append(f"{m}/{a}")
            
    # Ordena do mais recente para o mais antigo
    meses_disponiveis = sorted(list(set(meses_disponiveis)), key=lambda x: (x.split('/')[1], x.split('/')[0]), reverse=True)
    
    # 2. Definir o Mês Alvo (Vencimento)
    mes_alvo = request.args.get('mes_alvo', '').strip()
    if not mes_alvo and meses_disponiveis:
        mes_alvo = meses_disponiveis[0]
        
    if not mes_alvo:
        conexao.close()
        return jsonify({'sucesso': False, 'msg': 'Nenhum dado disponível.'})
        
    # 3. 🎯 Determinar o período exato do MÊS ANTERIOR
    # Ex: Se o vencimento é 10/2026, queremos coletas apenas entre 01/09/2026 e 30/09/2026
    m_alvo, a_alvo = mes_alvo.split('/')
    m_int, a_int = int(m_alvo), int(a_alvo)
    
    m_ant = m_int - 1
    a_ant = a_int
    if m_ant == 0:
        m_ant = 12
        a_ant -= 1
        
    data_inicio = f"{a_ant}-{str(m_ant).zfill(2)}-01 00:00:00"
    data_fim = f"{a_alvo}-{str(m_alvo).zfill(2)}-01 00:00:00"
    
    like_completo = f"%{str(m_alvo).zfill(2)}/{a_alvo}%"
    like_curto = f"%{str(m_alvo).zfill(2)}/{str(a_alvo)[-2:]}%"
    
    query = """
        SELECT 
            CASE 
                WHEN CAST(i.d_lote AS INTEGER) BETWEEN 1 AND 5 THEN '01-05'
                WHEN CAST(i.d_lote AS INTEGER) BETWEEN 6 AND 10 THEN '06-10'
                WHEN CAST(i.d_lote AS INTEGER) BETWEEN 11 AND 15 THEN '11-15'
                WHEN CAST(i.d_lote AS INTEGER) BETWEEN 16 AND 20 THEN '16-20'
                WHEN CAST(i.d_lote AS INTEGER) BETWEEN 21 AND 29 THEN '21-29'
                ELSE '30 - Aluguel Tranquilo' 
            END as lote_agrupado,
            COUNT(DISTINCT LTRIM(i.imovel, '0')) as total_imoveis,
            COUNT(DISTINCT CASE WHEN p.data_hora >= ? AND p.data_hora < ? THEN LTRIM(i.imovel, '0') END) as total_coletados
        FROM imoveis i
        LEFT JOIN produtividade p ON i.id = p.imovel_id AND p.acao IN ('COLETOU BOLETO', 'PAGOU BOLETO', 'PAGOU BOLETO NA 275', 'ALTEROU STATUS PARA PAGO NA ADM')
        WHERE (i.vencimento LIKE ? OR i.vencimento LIKE ?)
          AND i.administradora IS NOT NULL 
          AND TRIM(i.administradora) != ''
        GROUP BY lote_agrupado
        ORDER BY 
            CASE lote_agrupado
                WHEN '01-05' THEN 1
                WHEN '06-10' THEN 2
                WHEN '11-15' THEN 3
                WHEN '16-20' THEN 4
                WHEN '21-29' THEN 5
                ELSE 6
            END ASC
    """
    
    # 🎯 Injeta a Data Inicial, Data Final e Vencimento na Query
    dados_raw = conexao.execute(query, (data_inicio, data_fim, like_completo, like_curto)).fetchall()
    conexao.close()
    
    relatorio = []
    for row in dados_raw:
        tot = int(row['total_imoveis'] or 0)
        col = int(row['total_coletados'] or 0)
        pct = round((col / tot * 100), 2) if tot > 0 else 0
        
        relatorio.append({
            'lote': str(row['lote_agrupado']),
            'total': tot,
            'coletados': col,
            'pct': format(pct, ".2f").replace('.', ',')
        })
        
    return jsonify({
        'sucesso': True, 
        'dados': relatorio, 
        'meses_disponiveis': meses_disponiveis,
        'mes_ativo': mes_alvo
    })

@app.route('/gestao_logins')
@login_obrigatorio
def gestao_logins():
    conexao = conectar_banco()
    
    query = """
        SELECT c.imovel, 
               IFNULL(i.empreendimento, c.empreendimento) AS empreendimento,
               IFNULL(i.administradora, c.administradora) AS administradora,
               IFNULL(i.endereco, '-') AS endereco,
               IFNULL(c.login_acesso, '') AS usuario,
               IFNULL(c.senha_acesso, '') AS senha,
               COALESCE(NULLIF(TRIM(c.winker_condominio), ''), NULLIF(TRIM(c.webware_empreendimento), ''), '') AS emp_site,
               IFNULL(c.winker_predio, '') AS bloco_site,
               COALESCE(NULLIF(TRIM(c.winker_unidade), ''), NULLIF(TRIM(c.webware_unidade), ''), '') AS unidade_site,
               IFNULL(c.link_portal, '') AS link_portal,
               IFNULL(c.portal, 'DIVERSOS') AS portal,
               IFNULL(v.responsavel, c.responsavel_atual) AS responsavel,
               CASE 
                   WHEN c.login_acesso IS NOT NULL AND c.login_acesso != '' AND c.senha_acesso IS NOT NULL AND c.senha_acesso != '' THEN 'TEM' 
                   WHEN c.login_acesso LIKE '%[%' THEN 'E-MAIL/WPP'
                   ELSE 'FALTA' 
                   END AS status_login
        FROM cadastro_mestre_imoveis c
        LEFT JOIN imoveis i ON c.imovel = i.imovel AND i.ativo = 1
        LEFT JOIN vinculos_adm v ON v.rowid = vinculo_de(c.administradora)
        GROUP BY c.imovel
        ORDER BY administradora ASC, c.imovel ASC
    """
    registros = conexao.execute(query).fetchall()
    conexao.close()
    
    return render_template('gestao_logins.html', registros=[dict(r) for r in registros])

# 🚀 NOVA ROTA API: Salva o dado editado direto na célula da planilha!
@app.route('/api/imovel/salvar_linha_planilha', methods=['POST'])
@login_obrigatorio
def api_salvar_linha_planilha():
    dados = request.get_json() or {}
    imovel = dados.get('imovel')
    campo = dados.get('campo')
    valor = dados.get('valor', '')
    portal = dados.get('portal', 'WEBWARE') # Precisamos saber o portal para salvar no campo certo
    
    # Mapeamento de segurança dinâmico
    mapa_campos = {
        'usuario': 'login_acesso',
        'senha': 'senha_acesso',
        'link_portal': 'link_portal',
        'forma_coleta': 'forma_coleta'
    }
    
    # Tratativa para os campos que variam por portal
    if campo in ['emp_site', 'bloco_site', 'unidade_site']:
        if portal == 'WEBWARE':
            if campo == 'emp_site': coluna_bd = 'webware_empreendimento'
            elif campo == 'unidade_site': coluna_bd = 'webware_unidade'
            else: return jsonify({'sucesso': False, 'msg': 'O portal Webware não utiliza bloco.'})
        elif portal == 'WINKER':
            if campo == 'emp_site': coluna_bd = 'winker_condominio'
            elif campo == 'bloco_site': coluna_bd = 'winker_predio'
            elif campo == 'unidade_site': coluna_bd = 'winker_unidade'
        else:
            return jsonify({'sucesso': False, 'msg': 'Portal não suportado para esses campos específicos.'})
    elif campo in mapa_campos:
        coluna_bd = mapa_campos[campo]
    else:
        return jsonify({'sucesso': False, 'msg': 'Campo inválido'})
        
    try:
        conexao = conectar_banco()
        conexao.execute(f"UPDATE cadastro_mestre_imoveis SET {coluna_bd} = ? WHERE imovel = ?", (valor, imovel))
        conexao.commit()
        conexao.close()
        return jsonify({'sucesso': True})
    except Exception as e:
        return jsonify({'sucesso': False, 'msg': str(e)})

# ==============================================================================
# 🚀 INICIALIZAÇÃO DO SISTEMA
# ==============================================================================
def limpar_automacao_da_superlogica():
    """Superlógica saiu da automação: os casos dela que estão como AUTOMAÇÃO na base ativa voltam para o branco, UMA vez
    (a marca fica em config_sistema; o login/senha do cadastro não são tocados). Devolve quantos casos limpou (None = já feito)."""
    limpos = None
    try:
        conexao_sl = conectar_banco()
        conexao_sl.execute('CREATE TABLE IF NOT EXISTS config_sistema (chave TEXT PRIMARY KEY, valor TEXT)')
        if not conexao_sl.execute("SELECT 1 FROM config_sistema WHERE chave = 'migr_superlogica_sem_automacao'").fetchone():
            chaves_sl = set()
            for r in conexao_sl.execute("SELECT imovel, portal, link_portal FROM cadastro_mestre_imoveis").fetchall():
                if cadastro_e_superlogica(r['portal'], r['link_portal']):
                    chaves_sl.add(str(r['imovel'] or '').strip().lstrip('0'))
            limpos = 0
            for r in conexao_sl.execute("SELECT id, imovel FROM imoveis WHERE ativo = 1 "
                                        "AND UPPER(TRIM(IFNULL(status_interno, ''))) = 'AUTOMAÇÃO'").fetchall():
                if str(r['imovel'] or '').strip().lstrip('0') in chaves_sl:
                    conexao_sl.execute("UPDATE imoveis SET status_interno = NULL WHERE id = ?", (r['id'],))
                    limpos += 1
            conexao_sl.execute("INSERT OR REPLACE INTO config_sistema (chave, valor) VALUES ('migr_superlogica_sem_automacao', ?)",
                               (datetime.now().strftime('%Y-%m-%d %H:%M'),))
            conexao_sl.commit()
            print(f"🧹 Superlógica fora da automação: {limpos} caso(s) voltaram para o status em branco.", flush=True)
        conexao_sl.close()
    except Exception as e:
        print(f"⚠️ Não consegui limpar o status AUTOMAÇÃO da Superlógica: {e}", flush=True)
    return limpos


def limpar_automacao_sem_comprovacao():
    """Uma vez só: caso da base ativa que está AUTOMAÇÃO em portal SEM robô próprio (CTRL, Autônoma, WhatsApp, Diversos, sem
    portal...) e que o robô de e-mail NÃO comprovou (menos de 2 meses seguidos) volta para o branco. Winker/Webware e quem o
    robô já pega sozinho continuam AUTOMAÇÃO. Login/senha do cadastro não são tocados.
    Devolve quantos casos limpou (None = já feito, sem base ativa ou sem histórico)."""
    limpos = None
    try:
        conexao_ct = conectar_banco()
        conexao_ct.execute('CREATE TABLE IF NOT EXISTS config_sistema (chave TEXT PRIMARY KEY, valor TEXT)')
        if not conexao_ct.execute("SELECT 1 FROM config_sistema WHERE chave = 'migr_automacao_so_com_prova'").fetchone():
            n_ctrl = meses_seguidos_exigidos(conexao_ct, 'CTRL')
            alvo, _, _ = mes_alvo_da_base_ativa(conexao_ct)
            if alvo:
                provados = imoveis_que_o_robo_pega_sozinho(conexao_ct, n_ctrl or MESES_SEGUIDOS_DO_ROBO['CTRL'],
                                                           janela_historico_robo(conexao_ct)) or {}
                sem_robo_proprio, ctrl = set(), set()
                for r in conexao_ct.execute("SELECT imovel, portal FROM cadastro_mestre_imoveis").fetchall():
                    k, pn = str(r['imovel'] or '').strip().lstrip('0'), _portal_normal(r['portal'])
                    if pn not in ('WINKER', 'WEBWARE'):
                        sem_robo_proprio.add(k)
                    if pn == 'CTRL':
                        ctrl.add(k)
                limpos = 0
                for r in conexao_ct.execute("SELECT id, imovel FROM imoveis WHERE ativo = 1 "
                                            "AND UPPER(TRIM(IFNULL(status_interno, ''))) = 'AUTOMAÇÃO'").fetchall():
                    k = str(r['imovel'] or '').strip().lstrip('0')
                    if k not in sem_robo_proprio or k in provados or (n_ctrl == 0 and k in ctrl):
                        continue
                    conexao_ct.execute("UPDATE imoveis SET status_interno = NULL WHERE id = ?", (r['id'],))
                    limpos += 1
                conexao_ct.execute("INSERT OR REPLACE INTO config_sistema (chave, valor) VALUES ('migr_automacao_so_com_prova', ?)",
                                   (datetime.now().strftime('%Y-%m-%d %H:%M'),))
                conexao_ct.commit()
                print(f"🧹 AUTOMAÇÃO só com comprovação do robô: {limpos} caso(s) voltaram para o status em branco.", flush=True)
        conexao_ct.close()
    except Exception as e:
        print(f"⚠️ Não consegui limpar o status AUTOMAÇÃO sem comprovação: {e}", flush=True)
    return limpos


def inicializar_app():
    print("🚀 Inicializando base de dados em alta performance...", flush=True)
    inicializar_banco_producao()
    limpar_automacao_da_superlogica()
    limpar_automacao_sem_comprovacao()
    try:
        _conexao_inc = conectar_banco()
        inc_garantir_tabelas(_conexao_inc)           # inconsistências do Bradesco + vínculo com a Internet
        _conexao_inc.commit()
        _conexao_inc.close()
    except Exception as e:
        print(f'⚠️ Não consegui preparar as tabelas das inconsistências do Bradesco: {e}', flush=True)
    try:
        _conexao_mural = conectar_banco()
        garantir_tabela_mudancas(_conexao_mural)     # mural que faz a tela se atualizar sozinha
        limpar_mural_de_mudancas(_conexao_mural)
        _conexao_mural.commit()
        _conexao_mural.close()
    except Exception as e:
        print(f'⚠️ Não foi possível preparar o mural de mudanças: {e}', flush=True)

    try:
        _conexao_virada = conectar_banco()
        virada_mes.garantir_estrutura_virada(_conexao_virada)
        _conexao_virada.commit()
        _conexao_virada.close()
    except Exception as e:
        print(f"⚠️ Não foi possível preparar a estrutura da virada de mês: {e}", flush=True)

    # Se o servidor caiu no meio de uma aprovação, devolve a solicitação para a fila (não fica presa em PROCESSANDO)
    try:
        _conexao_fila = conectar_banco()
        _conexao_fila.execute("UPDATE pendencias_internet SET status = 'PENDENTE' WHERE status = 'PROCESSANDO'")
        _conexao_fila.commit()
        _conexao_fila.close()
    except Exception as e:
        print(f"⚠️ Não foi possível recuperar pendências em processamento: {e}", flush=True)
    
    # O agendador só sobe em UM processo (a tranca de _sou_o_dono_do_agendador) e tem que ser o processo que
    # ATENDE o portal: antes ele subia aqui, na importação, e com o reloader quem pegava era o processo "vigia"
    # — o robô automático rodava lá e a tela de Automações não via status nem logs. Agora ele sobe na primeira
    # requisição (before_request); esta cutucada faz essa primeira requisição logo depois de o servidor subir.
    _cutucar_agendador_ao_subir()


# ==============================================================================
# 🩺 MÓDULO: RAIO-X RPA (DIAGNÓSTICO E AJUSTE RÁPIDO DE FALHAS)
# ==============================================================================

# ==============================================================================
# 🩺 MÓDULO: RAIO-X RPA (COM IDENTIFICAÇÃO DE RESPONSÁVEL E CICLO DE AJUSTE)
# ==============================================================================

@app.route('/raio_x_rpa')
@login_obrigatorio
def raio_x_rpa():
    conexao = conectar_banco()
    conexao.execute('''
        CREATE TABLE IF NOT EXISTS auditoria_robos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            robo TEXT,
            imovel_id TEXT,
            status TEXT,
            detalhe TEXT,
            data_hora TEXT
        )
    ''')
    conexao.commit()
    conexao.close()
    return render_template('raio_x_rpa.html')


# ==============================================================================
# 🩺 RAIO-X RPA: CLASSIFICAÇÃO DOS ERROS DOS ROBÔS
# Regra: o STATUS que o robô grava é a fonte principal (ex.: "Falha de Login", "Condomínio Não Encontrado").
# O DETALHE só é usado quando o status é genérico (ex.: "ERRO") e SEM o trecho que o Webware anexa em todo erro
# "(Empreendimento: ... | Unidade: ...)" nem URLs — senão toda falha parecia "Unidade/Bloco".
# ==============================================================================
# ==============================================================================
# ⚡ RAIO-X RPA: CARGA RÁPIDA DA BASE
# A consulta antiga juntava 4 tabelas comparando FUNÇÕES (LTRIM/TRIM/UPPER) nas duas pontas do JOIN. Assim o SQLite
# não consegue usar índice e compara cada imóvel com todas as linhas das outras tabelas (12 mil imóveis = ~30 s).
# Aqui cada tabela é lida uma vez e a ligação é feita por dicionário (segundos -> milissegundos), reproduzindo
# exatamente as regras do SQLite: UPPER só em ASCII, TRIM só de espaços, MAX ignorando NULL.
# ==============================================================================
_SQLITE_UPPER_ASCII = {ord(c): ord(c.upper()) for c in 'abcdefghijklmnopqrstuvwxyz'}


def _chave_imovel_raio_x(valor):
    """Equivale a LTRIM(imovel, '0')."""
    return None if valor is None else str(valor).lstrip('0')


def _chave_adm_raio_x(valor):
    """Equivale a TRIM(UPPER(administradora)) do SQLite (UPPER só mexe em letras ASCII)."""
    return None if valor is None else str(valor).translate(_SQLITE_UPPER_ASCII).strip(' ')


def _max_sqlite(valores):
    """Equivale ao MAX() do SQLite: ignora NULL e ordena número < texto < binário."""
    candidatos = [v for v in valores if v is not None]
    if not candidatos:
        return None

    def ordem(v):
        if isinstance(v, (int, float)):
            return (0, v)
        if isinstance(v, (bytes, bytearray)):
            return (2, bytes(v))
        return (1, str(v))
    return max(candidatos, key=ordem)


def carregar_base_raio_x(conexao, data_corte):
    """Mesmo resultado da consulta SQL antiga (uma linha por imóvel), sem JOINs lentos."""
    encerrados = ('PAGO', 'PAGO NA ADM', 'PAGO NA 275', 'COLETADO')
    garantir_colunas_acesso(conexao)

    # 1) Imóveis ativos e ainda não encerrados, agrupados pelo número sem zeros à esquerda
    grupos = {}
    for r in conexao.execute(
        """SELECT id, imovel, administradora, empreendimento, endereco, vencimento, status_interno, sacado, cpf_cnpj
           FROM imoveis
           WHERE ativo = 1 AND (status_interno IS NULL OR status_interno NOT IN (?, ?, ?, ?))""", encerrados
    ).fetchall():
        chave = _chave_imovel_raio_x(r['imovel'])
        if chave is None:
            continue
        grupos.setdefault(chave, []).append(r)

    # 2) Cadastro mestre por imóvel
    cadastro = {}
    for r in conexao.execute(
        """SELECT imovel, empreendimento, portal, login_acesso, senha_acesso, winker_condominio, winker_predio,
                  winker_unidade, webware_empreendimento, webware_unidade, possui_login, cpf_senha_pdf, regra_senha_pdf
           FROM cadastro_mestre_imoveis"""
    ).fetchall():
        chave = _chave_imovel_raio_x(r['imovel'])
        if chave in grupos:
            cadastro.setdefault(chave, []).append(r)

    # 3) Responsáveis por administradora
    vinculos = {}
    for r in conexao.execute("SELECT administradora, responsavel FROM vinculos_adm").fetchall():
        vinculos.setdefault(_chave_adm_raio_x(r['administradora']), []).append(r['responsavel'])

    # 4) Último registro de auditoria de cada imóvel dentro do ciclo (junção só pela chave primária: rápido).
    #    Na MESMA passada sai o último registro de todos os tempos: se ele for erro de login/senha do PDF,
    #    o caso continua no Raio-X mesmo depois de a base ser atualizada (só sai quando alguém resolve).
    ultimos = {}
    for r in conexao.execute(
        """SELECT LTRIM(imovel_id, '0') AS chave, MAX(id) AS id_geral,
                  MAX(CASE WHEN data_hora >= ? THEN id END) AS id_ciclo
           FROM auditoria_robos GROUP BY LTRIM(imovel_id, '0')""", (data_corte,)
    ).fetchall():
        if r['chave'] in grupos:
            ultimos[r['chave']] = (r['id_ciclo'], r['id_geral'])
    ids_buscar = sorted({i for par in ultimos.values() for i in par if i is not None})
    registros = {}
    for inicio in range(0, len(ids_buscar), 800):
        lote = ids_buscar[inicio:inicio + 800]
        ph = ','.join('?' for _ in lote)
        for r in conexao.execute(
                f"SELECT id, robo, imovel_id, status, detalhe, data_hora FROM auditoria_robos WHERE id IN ({ph})", lote):
            registros[r['id']] = r
    auditoria, persistentes = {}, set()
    for chave, (id_ciclo, id_geral) in ultimos.items():
        geral = registros.get(id_geral)
        if geral is not None and id_geral != id_ciclo and \
                classificar_erro_raio_x(geral['status'], geral['detalhe']) in CATEGORIAS_ATE_RESOLVER:
            auditoria[chave] = geral          # erro de credencial de um ciclo anterior, ainda sem solução
            persistentes.add(chave)
        elif id_ciclo is not None and id_ciclo in registros:
            auditoria[chave] = registros[id_ciclo]

    linhas = []
    for chave, rows in grupos.items():
        base = min(rows, key=lambda x: x['id'])
        cads = cadastro.get(chave, [])
        aud = auditoria.get(chave)

        resps = []
        for adm_chave in {_chave_adm_raio_x(x['administradora']) for x in rows}:
            resps.extend(vinculos.get(adm_chave, []))
        resp_max = _max_sqlite(resps)
        resp_txt = None if resp_max is None else str(resp_max).strip(' ')

        emp_cad = _max_sqlite([x['empreendimento'] for x in cads])
        emp_cad = None if emp_cad is None else str(emp_cad).strip(' ')
        emp_final = emp_cad if emp_cad not in (None, '') else _max_sqlite([x['empreendimento'] for x in rows])

        def campo_cad(nome, padrao=''):
            v = _max_sqlite([x[nome] for x in cads])
            return padrao if v is None else v

        linhas.append({
            'id_imovel_base': base['id'],
            'imovel': base['imovel'],
            'administradora': _max_sqlite([x['administradora'] for x in rows]),
            'empreendimento': emp_final,
            'endereco': _max_sqlite([('-' if x['endereco'] is None else x['endereco']) for x in rows]),
            'vencimento': _max_sqlite([x['vencimento'] for x in rows]),
            'status_interno': _max_sqlite([x['status_interno'] for x in rows]),
            'responsavel': resp_txt if resp_txt else 'Não Atribuído',
            'portal': campo_cad('portal', 'DIVERSOS'),
            'login_acesso': campo_cad('login_acesso'),
            'senha_acesso': campo_cad('senha_acesso'),
            'winker_condominio': campo_cad('winker_condominio'),
            'winker_predio': campo_cad('winker_predio'),
            'winker_unidade': campo_cad('winker_unidade'),
            'webware_empreendimento': campo_cad('webware_empreendimento'),
            'webware_unidade': campo_cad('webware_unidade'),
            'possui_login': campo_cad('possui_login'),
            'cpf_senha_pdf': campo_cad('cpf_senha_pdf'),
            'regra_senha_pdf': campo_cad('regra_senha_pdf'),
            'sacado': _max_sqlite([x['sacado'] for x in rows]),
            'cpf_base': _max_sqlite([x['cpf_cnpj'] for x in rows]),
            'persistente': chave in persistentes,
            'id_auditoria': aud['id'] if aud else None,
            'ultimo_robo': aud['robo'] if aud else None,
            'ultimo_status_erro': aud['status'] if aud else None,
            'ultimo_detalhe_erro': aud['detalhe'] if aud else None,
            'data_ultima_falha': aud['data_hora'] if aud else None,
        })

    # Mesma ordem de antes: falha mais recente primeiro (sem falha por último) e, no empate, número do imóvel
    linhas.sort(key=lambda r: str(r['imovel']))
    linhas.sort(key=lambda r: (r['data_ultima_falha'] is not None, r['data_ultima_falha'] or ''), reverse=True)
    return linhas


RAIO_X_VERSAO = '2026-11-05.1'   # a tela compara com esta versão e avisa se o servidor ainda está rodando código antigo

# Status que o robô grava quando DÁ CERTO (o Winker audita 'BAIXADO'): não são erro e não entram na lista do Raio-X
STATUS_SUCESSO_ROBO = ('BAIXADO', 'SUCESSO', 'COLETADO', 'OK', 'DISPENSADO', 'IGNORADO', 'RESOLVIDO')

RAIO_X_CATEGORIAS = {
    'AJUSTADO':      ('ajustados',     'Ajustado (Pronto para o robô tentar)'),
    'FALTAM_DADOS':  ('faltam_dados',  'Faltam dados do site no cadastro'),
    'UNIDADE_BLOCO': ('unidade_bloco', 'Unidade ou Bloco não encontrado'),
    'CONDOMINIO':    ('condominio',    'Condomínio não localizado no site'),
    'LOGIN':         ('login',         'Falha de Login / Senha'),
    'SENHA_PDF':     ('senha_pdf',     'PDF com senha: nenhum CPF abriu'),
    'OUTROS':        ('outros',        None),
}

# Erro de credencial não some quando a base é atualizada: só sai do Raio-X quando alguém resolve
# (ajusta o cadastro, informa o CPF, marca "não possui login") ou quando o robô volta a passar.
CATEGORIAS_ATE_RESOLVER = ('LOGIN', 'SENHA_PDF')

# Portais em que o login é a conta compartilhada da Lello (não é do imóvel): ninguém precisa
# digitar usuário/senha por imóvel, mas condomínio/bloco/unidade continuam valendo.
PORTAIS_LOGIN_COMPARTILHADO = ('WINKER', 'SUPERLOGICA')

STATUS_SENHA_PDF = 'Erro de Senha do PDF'      # o que o FinMail grava quando nenhum CPF abre o PDF

REGRAS_SENHA_PDF = {
    '':            'Automático (o robô tenta todas as variações do CPF)',
    'CPF_COMPLETO': 'CPF/CNPJ completo',
    'PRIMEIROS_3': '3 primeiros dígitos',
    'PRIMEIROS_4': '4 primeiros dígitos',
    'PRIMEIROS_5': '5 primeiros dígitos',
    'PRIMEIROS_6': '6 primeiros dígitos',
    'ULTIMOS_4':   '4 últimos dígitos',
    'ULTIMOS_6':   '6 últimos dígitos',
    'CNPJ_RAIZ':   '8 primeiros dígitos do CNPJ (raiz)',
}

COLUNAS_ACESSO_EXTRAS = (
    ('possui_login', "TEXT DEFAULT ''"),       # 'SIM' / 'NAO' / '' (não informado)
    ('cpf_senha_pdf', "TEXT DEFAULT ''"),      # CPF/CNPJ que abre o PDF do boleto (informado no Raio-X)
    ('regra_senha_pdf', "TEXT DEFAULT ''"),    # chave de REGRAS_SENHA_PDF
    ('cpf_senha_user', "TEXT DEFAULT ''"),
    ('cpf_senha_data', "TEXT DEFAULT ''"),
    # já existem nos bancos atuais; ficam aqui só para um banco antigo não quebrar o ajuste
    ('cred_user', 'TEXT'), ('cred_data', 'TEXT'),
    ('emp_site', "TEXT DEFAULT ''"), ('bloco_site', "TEXT DEFAULT ''"), ('unidade_site', "TEXT DEFAULT ''"),
    ('super_condominio', 'TEXT'), ('super_bloco', 'TEXT'), ('super_unidade', 'TEXT'),
)


def garantir_colunas_acesso(conexao):
    """Colunas e tabela novas (possui login, CPF da senha do PDF, e-mails com PDF protegido sem imóvel)."""
    try:
        existentes = {r[1] for r in conexao.execute("PRAGMA table_info(cadastro_mestre_imoveis)").fetchall()}
    except Exception:
        existentes = set()
    if existentes:
        for coluna, tipo in COLUNAS_ACESSO_EXTRAS:
            if coluna not in existentes:
                try:
                    conexao.execute(f"ALTER TABLE cadastro_mestre_imoveis ADD COLUMN {coluna} {tipo}")
                except Exception:
                    pass
    conexao.execute('''
        CREATE TABLE IF NOT EXISTS finmail_pdf_protegido (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            data_hora TEXT, message_id TEXT, assunto TEXT, remetente TEXT, link TEXT, regra TEXT,
            status TEXT DEFAULT 'PENDENTE', imovel TEXT, resolvido_por TEXT, resolvido_em TEXT
        )''')
    conexao.execute('''
        CREATE TABLE IF NOT EXISTS auditoria_robos (
            id INTEGER PRIMARY KEY AUTOINCREMENT, robo TEXT, imovel_id TEXT, status TEXT, detalhe TEXT, data_hora TEXT
        )''')


def so_digitos(valor):
    return re.sub(r'\D', '', str(valor or ''))


def mascarar_documento(valor):
    """'12345678901' -> '123.***.***-01'. Nunca devolve o documento inteiro para a lista."""
    d = so_digitos(valor)
    if len(d) == 11:
        return f'{d[:3]}.***.***-{d[-2:]}'
    if len(d) == 14:
        return f'{d[:2]}.***.***/****-{d[-2:]}'
    if len(d) >= 4:
        return f'{d[:2]}{"*" * (len(d) - 4)}{d[-2:]}'
    return '*' * len(d)


def _portal_normal(portal):
    return normalizar_texto(portal).replace('-', '').replace(' ', '')


def cadastro_e_superlogica(portal, link=''):
    """Superlógica: pelo portal cadastrado ou pelo link do portal (alguns vêm como 'Diversos' com link superlogica.net)."""
    return _portal_normal(portal).startswith('SUPERLOGICA') or 'superlogica' in str(link or '').lower()


def login_e_compartilhado(portal):
    return _portal_normal(portal) in PORTAIS_LOGIN_COMPARTILHADO


# 🟢🟡🔴 Dificuldade do caso para a equipe: verde = automação pega sozinha; amarela = tem login,
# mas em portal sem robô (alguém entra e baixa); vermelha = sem login / sem acesso.
# (Superlógica saiu da automação: o login continua cadastrado, mas o caso é de quem entra e baixa à mão.)
PORTAIS_AUTOMACAO = {'WINKER': 'Winker', 'WEBWARE': 'Webware',
                     'CTRL': 'E-mail (FinMail)', 'EMAIL': 'E-mail (FinMail)'}


def classificar_dificuldade(portal, login, senha, possui_login=''):
    """('FACIL' | 'MEDIO' | 'DIFICIL', explicação curta)."""
    chave = _portal_normal(portal)
    nome_portal = str(portal or '').strip() or 'sem portal'
    if chave in PORTAIS_AUTOMACAO:
        return 'FACIL', f'Automação: {PORTAIS_AUTOMACAO[chave]}'
    if normalizar_texto(possui_login) == 'NAO':
        return 'DIFICIL', f'Não possui login ({nome_portal})'
    if str(login or '').strip() and str(senha or '').strip():
        return 'MEDIO', f'Tem login, mas {nome_portal} não tem automação'
    return 'DIFICIL', f'Sem login/acesso ({nome_portal})'


_RX_SUFIXO_CADASTRO = re.compile(r"\s*\(\s*Empreendimento\s*:.*\)\s*$", re.IGNORECASE | re.DOTALL)
_RX_URL = re.compile(r"https?://\S+", re.IGNORECASE)

_PALAVRAS_SENHA_PDF = ('SENHA DO PDF', 'SENHA PDF', 'PDF PROTEGIDO', 'PDF COM SENHA')
_PALAVRAS_LOGIN = ('LOGIN', 'SENHA', 'CREDENCIAL', 'BLOQUE')
_PALAVRAS_UNIDADE = ('UNIDADE', 'DIVISAO', 'BLOCO')
_PALAVRAS_CONDOMINIO = ('CONDOMINIO', 'EMPREENDIMENTO', 'VINCULO', 'DIVERGENTE')
_STATUS_TECNICOS = ('NAVEGACAO', 'DOWNLOAD', 'PAGINA DESCONHECIDA', 'ERRO AO MARCAR')


def classificar_erro_raio_x(status, detalhe, dados_faltando=False):
    """Devolve a categoria do erro: AJUSTADO, FALTAM_DADOS, UNIDADE_BLOCO, CONDOMINIO, LOGIN ou OUTROS."""
    st = normalizar_texto(status)
    det_limpo = _RX_SUFIXO_CADASTRO.sub('', str(detalhe or ''))
    det_limpo = _RX_URL.sub(' ', det_limpo)
    det = normalizar_texto(det_limpo)

    if st == 'AJUSTADO':
        return 'AJUSTADO'

    # PDF do e-mail com senha que nenhum CPF abriu: é do FinMail, não tem nada a ver com dado de site
    if any(k in st for k in _PALAVRAS_SENHA_PDF):
        return 'SENHA_PDF'

    if dados_faltando or 'FALTAM DADOS' in st or 'SEM DADOS' in st or 'FALTAM DADOS' in det or 'SEM DADOS DO SITE' in det:
        return 'FALTAM_DADOS'

    # 1) O status que o robô gravou diz o tipo do erro
    if any(k in st for k in _PALAVRAS_LOGIN):
        return 'LOGIN'
    if any(k in st for k in _PALAVRAS_UNIDADE):
        return 'UNIDADE_BLOCO'
    if any(k in st for k in _PALAVRAS_CONDOMINIO):
        return 'CONDOMINIO'

    # 2) Falhas técnicas conhecidas (navegação, download, página inesperada...) não são erro de cadastro
    if any(k in st for k in _STATUS_TECNICOS):
        return 'OUTROS'

    # 3) Status genérico ("ERRO"): olha o detalhe já limpo
    if any(k in det for k in _PALAVRAS_UNIDADE):
        return 'UNIDADE_BLOCO'
    if any(k in det for k in _PALAVRAS_CONDOMINIO):
        return 'CONDOMINIO'
    if any(k in det for k in _PALAVRAS_LOGIN):
        return 'LOGIN'
    return 'OUTROS'


def data_corte_do_ciclo(conexao):
    """Desde quando vale o ciclo atual: a hora do último upload da base."""
    linha = conexao.execute(
        "SELECT data_hora FROM produtividade WHERE acao LIKE 'UPLOAD BASE|%' ORDER BY id DESC LIMIT 1").fetchone()
    return linha['data_hora'] if linha and linha['data_hora'] else '2000-01-01 00:00:00'


def levantar_casos_raio_x(conexao, data_corte):
    """Todos os casos do ciclo já classificados, SEM filtro nenhum.
    A tela do Raio-X e o resumo do painel leem daqui — assim os dois nunca discordam."""
    termos_sem_boleto = (
        'SEM BOLETO', 'SEM BOLETOS', 'SEM BOLETO DO MÊS', 'SEM BOLETO DO MES',
        'NENHUM BOLETO', 'NÃO DISPONÍVEL', 'NAO DISPONIVEL', 'PAGO')
    casos, vistos = [], set()

    for item in carregar_base_raio_x(conexao, data_corte):
        chave_imovel = str(item['imovel']).strip().lstrip('0')
        if chave_imovel in vistos:
            continue
        vistos.add(chave_imovel)

        portal = (item.get('portal') or '').upper()
        if 'WINKER' in portal:
            emp_site = item.get('winker_condominio') or ''
            bloco_site = item.get('winker_predio') or ''
            unid_site = item.get('winker_unidade') or ''
        elif 'WEBWARE' in portal:
            emp_site = item.get('webware_empreendimento') or ''
            bloco_site = ''
            unid_site = item.get('webware_unidade') or ''
        else:
            emp_site = item.get('winker_condominio') or item.get('webware_empreendimento') or ''
            bloco_site = item.get('winker_predio') or ''
            unid_site = item.get('winker_unidade') or item.get('webware_unidade') or ''
        item['emp_site'], item['bloco_site'], item['unid_site'] = emp_site, bloco_site, unid_site

        st = (item.get('ultimo_status_erro') or '').strip().upper()
        det = (item.get('ultimo_detalhe_erro') or '').strip().upper()

        # Sem boleto / não emitido não é falha de robô
        if any(x in st for x in termos_sem_boleto) or 'NENHUM BOLETO' in det or 'NÃO HÁ BOLETOS' in det or 'TODOS OS' in det:
            continue

        dados_faltando = (portal in ('WINKER', 'WEBWARE') and (not emp_site or not unid_site))
        teve_erro_recente = bool(item.get('ultimo_status_erro'))

        if normalizar_texto(item.get('ultimo_status_erro')) in STATUS_SUCESSO_ROBO and not dados_faltando:
            continue
        if not teve_erro_recente and not dados_faltando:
            continue

        categoria = classificar_erro_raio_x(item.get('ultimo_status_erro'), item.get('ultimo_detalhe_erro'), dados_faltando)
        item['categoria_erro'] = categoria
        item['motivo_resumido'] = RAIO_X_CATEGORIAS[categoria][1] or item.get('ultimo_status_erro') or 'Falha Técnica de Coleta'
        item['esta_ajustado'] = (categoria == 'AJUSTADO')
        item['fica_ate_resolver'] = categoria in CATEGORIAS_ATE_RESOLVER
        item['login_compartilhado'] = login_e_compartilhado(item.get('portal'))
        item['cpf_base_mascarado'] = mascarar_documento(item.get('cpf_base')) if item.get('cpf_base') else ''
        item['cpf_senha_pdf_mascarado'] = mascarar_documento(item.get('cpf_senha_pdf')) if item.get('cpf_senha_pdf') else ''
        item['regra_senha_pdf_rotulo'] = REGRAS_SENHA_PDF.get(item.get('regra_senha_pdf') or '', item.get('regra_senha_pdf') or '')
        item.pop('cpf_base', None)
        item.pop('cpf_senha_pdf', None)          # a lista só leva o documento mascarado
        nivel, motivo_nivel = classificar_dificuldade(item.get('portal'), item.get('login_acesso'),
                                                      item.get('senha_acesso'), item.get('possui_login'))
        item['dificuldade'], item['dificuldade_motivo'] = nivel, motivo_nivel
        casos.append(item)
    return casos


def erros_ate_resolver_pendentes(conexao, numeros, categorias=('LOGIN',)):
    """{chave do imóvel: último registro} para os imóveis cujo ÚLTIMO registro de auditoria (de qualquer
    ciclo) ainda é um erro dessas categorias. Uma passada só na auditoria, mesmo para um lote grande."""
    chaves = sorted({_chave_imovel_raio_x(str(n).strip()) for n in numeros if str(n or '').strip()} - {None, ''})
    if not chaves:
        return {}
    garantir_colunas_acesso(conexao)
    achados = {}
    for inicio in range(0, len(chaves), 500):
        lote = chaves[inicio:inicio + 500]
        ph = ','.join('?' for _ in lote)
        for r in conexao.execute(
                f"""SELECT a.id, a.robo, a.imovel_id, a.status, a.detalhe, a.data_hora FROM auditoria_robos a
                    INNER JOIN (SELECT MAX(id) AS max_id FROM auditoria_robos
                                WHERE LTRIM(imovel_id, '0') IN ({ph}) GROUP BY LTRIM(imovel_id, '0')) b
                    ON a.id = b.max_id""", lote).fetchall():
            if classificar_erro_raio_x(r['status'], r['detalhe']) in categorias:
                achados[_chave_imovel_raio_x(r['imovel_id'])] = dict(r)
    return achados


def dados_acesso_do_cadastro(conexao, numero):
    """O que o cadastro tem hoje para o robô entrar no site (sem a senha)."""
    garantir_colunas_acesso(conexao)
    cad = conexao.execute(
        "SELECT * FROM cadastro_mestre_imoveis WHERE LTRIM(imovel, '0') = LTRIM(?, '0') LIMIT 1", (str(numero),)).fetchone()
    cad = dict(cad) if cad else {}
    portal = str(cad.get('portal') or '').strip()
    p = _portal_normal(portal)
    if p == 'WINKER':
        emp, bloco, unid = cad.get('winker_condominio'), cad.get('winker_predio'), cad.get('winker_unidade')
    elif p == 'WEBWARE':
        emp, bloco, unid = cad.get('webware_empreendimento'), cad.get('bloco_site'), cad.get('webware_unidade')
    elif p == 'SUPERLOGICA':
        emp, bloco, unid = cad.get('super_condominio'), cad.get('super_bloco'), cad.get('super_unidade')
    else:
        emp = cad.get('emp_site') or cad.get('winker_condominio') or cad.get('webware_empreendimento')
        bloco = cad.get('bloco_site') or cad.get('winker_predio')
        unid = cad.get('unidade_site') or cad.get('winker_unidade') or cad.get('webware_unidade')
    return {
        'portal': portal or 'DIVERSOS',
        'emp_site': str(emp or '').strip(), 'bloco_site': str(bloco or '').strip(), 'unid_site': str(unid or '').strip(),
        'login_acesso': str(cad.get('login_acesso') or '').strip(),
        'tem_senha': bool(str(cad.get('senha_acesso') or '').strip()),
        'possui_login': str(cad.get('possui_login') or '').strip().upper(),
        'login_compartilhado': login_e_compartilhado(portal),
    }


@app.route('/api/raio_x/dados', methods=['GET'])
@login_obrigatorio
def api_raio_x_dados():
    termo = request.args.get('pesquisa', '').strip().lower()
    filtro_portal = request.args.get('portal', '').strip().upper()
    filtro_erro = request.args.get('tipo_erro', '').strip().upper()
    filtro_resp = request.args.get('responsavel', '').strip()
    ver_ajustados = request.args.get('ver_ajustados', '0') == '1'

    conexao = conectar_banco()
    try:
        data_corte = data_corte_do_ciclo(conexao)
        casos = levantar_casos_raio_x(conexao, data_corte)
        resp_unicos = sorted({c['responsavel'].title() for c in casos
                              if c['responsavel'] and c['responsavel'] != 'Não Atribuído'})
        emails_pdf_pendentes = conexao.execute(
            "SELECT COUNT(*) FROM finmail_pdf_protegido WHERE status = 'PENDENTE'").fetchone()[0]
    finally:
        conexao.close()

    registros = []
    totais = {
        'total': 0,
        'unidade_bloco': 0,
        'condominio': 0,
        'login': 0,
        'senha_pdf': 0,
        'faltam_dados': 0,
        'ajustados': 0,
        'outros': 0
    }

    # Filtros preparados uma vez só (comparação sem acento e sem diferença de maiúscula/minúscula)
    resp_alvo = '' if filtro_resp.lower() in ('', 'todos') else normalizar_texto(filtro_resp)
    portal_alvo = '' if filtro_portal in ('', 'TODOS') else normalizar_texto(filtro_portal)
    termo_norm = normalizar_texto(termo)
    erro_alvo = '' if filtro_erro in ('', 'TODOS') else filtro_erro
    mostrar_ajustados = ver_ajustados or erro_alvo == 'AJUSTADO'

    for item in casos:
        portal = (item.get('portal') or '').upper()
        emp_site, bloco_site, unid_site = item['emp_site'], item['bloco_site'], item['unid_site']
        categoria = item['categoria_erro']
        motivo_curto = item['motivo_resumido']

        # --- Filtros de CONTEXTO: valem para a lista E para os contadores dos cards ---
        if portal_alvo and portal_alvo not in normalizar_texto(portal):
            continue
        if resp_alvo and resp_alvo != normalizar_texto(item['responsavel']):
            continue
        if termo_norm:
            campos_busca = [
                item['imovel'], item.get('empreendimento'), item.get('administradora'), item.get('responsavel'),
                emp_site, bloco_site, unid_site, item.get('portal'),
                item.get('ultimo_status_erro'), item.get('ultimo_detalhe_erro'), motivo_curto
            ]
            if not any(termo_norm in normalizar_texto(c) for c in campos_busca):
                continue

        # --- Contadores dos cards (refletem Operador / Portal / Busca; não a categoria clicada) ---
        chave_total = RAIO_X_CATEGORIAS[categoria][0]
        totais[chave_total] += 1
        if categoria != 'AJUSTADO':
            totais['total'] += 1

        # --- Filtros da LISTA ---
        if categoria == 'AJUSTADO' and not mostrar_ajustados:
            continue
        if erro_alvo and erro_alvo != categoria:
            continue

        registros.append(item)

    data_corte_fmt = data_corte
    try:
        data_corte_fmt = datetime.strptime(data_corte[:19], '%Y-%m-%d %H:%M:%S').strftime('%d/%m/%Y às %H:%M')
    except Exception:
        pass

    return jsonify({
        'sucesso': True,
        'versao': RAIO_X_VERSAO,
        'ciclo_desde': data_corte_fmt,
        'totais': totais,
        'emails_pdf_pendentes': emails_pdf_pendentes,
        'regras_senha_pdf': [{'valor': k, 'rotulo': v} for k, v in REGRAS_SENHA_PDF.items()],
        'responsaveis': resp_unicos,
        'registros': registros
    })


# ==============================================================================
# 📟 RESUMO DO RAIO-X — o que o robô deixou parado, resumido para quem está olhando
# ==============================================================================
# A tela do Raio-X só é vista por quem lembra de abrir, e falha de robô é boleto não
# coletado: tem que chegar na pessoa, não esperar a pessoa ir atrás. Este resumo é a
# fonte única que alimenta a faixa fixa do painel e o resuminho do dia.
# O levantamento custa ~150 ms sobre a base inteira — caro demais para cada tela perguntar
# de minuto em minuto. Por isso fica guardado por um tempinho; qualquer ajuste ou remoção
# derruba o guardado na hora, para ninguém ver número velho depois de resolver um caso.
SEGUNDOS_CACHE_RAIO_X = 90
_CACHE_RAIO_X = {'quando': 0.0, 'casos': None, 'data_corte': ''}
_TRAVA_CACHE_RAIO_X = threading.Lock()


def esquecer_resumo_raio_x():
    """Chamado depois de ajustar/remover um caso: o próximo resumo é recalculado."""
    with _TRAVA_CACHE_RAIO_X:
        _CACHE_RAIO_X['casos'] = None


def casos_raio_x_em_cache(conexao):
    with _TRAVA_CACHE_RAIO_X:
        fresco = (_CACHE_RAIO_X['casos'] is not None
                  and (time.time() - _CACHE_RAIO_X['quando']) < SEGUNDOS_CACHE_RAIO_X)
        if fresco:
            return _CACHE_RAIO_X['casos'], _CACHE_RAIO_X['data_corte']

    data_corte = data_corte_do_ciclo(conexao)
    casos = levantar_casos_raio_x(conexao, data_corte)
    with _TRAVA_CACHE_RAIO_X:
        _CACHE_RAIO_X.update({'quando': time.time(), 'casos': casos, 'data_corte': data_corte})
    return casos, data_corte


@app.route('/api/raio_x/resumo', methods=['GET'])
@login_obrigatorio
def api_raio_x_resumo():
    """Números do Raio-X já separados entre 'da operação' e 'seus'."""
    eu = str(session.get('usuario') or '').strip()
    eu_norm = normalizar_texto(eu)
    conexao = conectar_banco()
    try:
        casos, data_corte = casos_raio_x_em_cache(conexao)
    finally:
        conexao.close()

    hoje = datetime.now().strftime('%Y-%m-%d')    # é assim que o robô grava a hora da falha
    por_categoria, administradoras = {}, {}
    total = meus = novos_hoje = meus_hoje = 0

    for caso in casos:
        if caso['categoria_erro'] == 'AJUSTADO':        # já resolvido, não é pendência
            continue
        meu = normalizar_texto(caso.get('responsavel')) == eu_norm and bool(eu_norm)
        de_hoje = str(caso.get('data_ultima_falha') or '').startswith(hoje)
        total += 1
        meus += 1 if meu else 0
        novos_hoje += 1 if de_hoje else 0
        meus_hoje += 1 if (meu and de_hoje) else 0

        cat = por_categoria.setdefault(caso['categoria_erro'], {'qtd': 0, 'meus': 0})
        cat['qtd'] += 1
        cat['meus'] += 1 if meu else 0

        nome_adm = str(caso.get('administradora') or 'Sem administradora').strip()
        adm = administradoras.setdefault(nome_adm, {'qtd': 0, 'meus': 0, 'categorias': set()})
        adm['qtd'] += 1
        adm['meus'] += 1 if meu else 0
        adm['categorias'].add(caso['categoria_erro'])

    lista_categorias = [{'chave': chave,
                         'rotulo': RAIO_X_CATEGORIAS.get(chave, (None, chave))[1] or 'Falha técnica',
                         'qtd': v['qtd'], 'meus': v['meus']}
                        for chave, v in sorted(por_categoria.items(), key=lambda x: -x[1]['qtd'])]

    # As administradoras que mais travam: resolver uma destas tira vários boletos da fila de uma vez
    lista_adms = sorted(
        ({'nome': nome, 'qtd': v['qtd'], 'meus': v['meus'], 'categorias': sorted(v['categorias'])}
         for nome, v in administradoras.items() if v['qtd'] > 1),
        key=lambda x: (-x['qtd'], x['nome']))[:5]

    corte_bonito = data_corte
    try:
        corte_bonito = datetime.strptime(data_corte[:19], '%Y-%m-%d %H:%M:%S').strftime('%d/%m/%Y às %H:%M')
    except Exception:
        pass

    return jsonify({
        'sucesso': True, 'eu': eu, 'total': total, 'meus': meus,
        'novos_hoje': novos_hoje, 'meus_hoje': meus_hoje,
        'categorias': lista_categorias, 'administradoras': lista_adms,
        'ciclo_desde': corte_bonito, 'dia': datetime.now().strftime('%Y-%m-%d')})


@app.route('/api/raio_x/diagnostico', methods=['GET'])
@login_obrigatorio
def api_raio_x_diagnostico():
    """Radiografia dos dados reais que alimentam o Raio-X (para entender por que um filtro/card não bate)."""
    encerrados = ('PAGO', 'PAGO NA ADM', 'PAGO NA 275', 'COLETADO')
    marcas_sem_boleto = ('SEM BOLETO', 'SEM BOLETOS', 'NENHUM BOLETO', 'NÃO DISPONÍVEL', 'NAO DISPONIVEL', 'PAGO')
    conexao = conectar_banco()
    try:
        upload_row = conexao.execute(
            "SELECT data_hora FROM produtividade WHERE acao LIKE 'UPLOAD BASE|%' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        data_corte = upload_row['data_hora'] if upload_row and upload_row['data_hora'] else '2000-01-01 00:00:00'

        aud_total = conexao.execute("SELECT COUNT(*) AS c FROM auditoria_robos").fetchone()['c']
        aud_ciclo = conexao.execute("SELECT COUNT(*) AS c FROM auditoria_robos WHERE data_hora >= ?", (data_corte,)).fetchone()['c']
        ultima_aud = conexao.execute("SELECT MAX(data_hora) AS d FROM auditoria_robos").fetchone()['d']

        linhas_status = conexao.execute(
            """SELECT robo, status, COUNT(*) AS qtd, MAX(detalhe) AS exemplo
               FROM auditoria_robos WHERE data_hora >= ?
               GROUP BY robo, status ORDER BY qtd DESC LIMIT 80""", (data_corte,)).fetchall()
        status = []
        for r in linhas_status:
            st_up = (r['status'] or '').strip().upper()
            if normalizar_texto(r['status']) in STATUS_SUCESSO_ROBO:
                categoria = 'SUCESSO (não é erro)'
            elif any(m in st_up for m in marcas_sem_boleto):
                categoria = 'IGNORADO (sem boleto)'
            else:
                categoria = classificar_erro_raio_x(r['status'], r['exemplo'])
            status.append({'robo': r['robo'] or '-', 'status': r['status'] or '(vazio)', 'qtd': r['qtd'], 'categoria': categoria})

        marcas = ','.join('?' for _ in encerrados)
        portais = [
            {'portal': r['p'] or '(vazio)', 'qtd': r['q']}
            for r in conexao.execute(
                f"""SELECT UPPER(TRIM(IFNULL(c.portal, ''))) AS p, COUNT(DISTINCT LTRIM(i.imovel, '0')) AS q
                    FROM imoveis i LEFT JOIN cadastro_mestre_imoveis c ON LTRIM(c.imovel, '0') = LTRIM(i.imovel, '0')
                    WHERE i.ativo = 1 AND (i.status_interno IS NULL OR i.status_interno NOT IN ({marcas}))
                    GROUP BY p ORDER BY q DESC""", encerrados).fetchall()
        ]
        operadores = [
            {'responsavel': r['r'], 'qtd': r['q']}
            for r in conexao.execute(
                f"""SELECT COALESCE(NULLIF(TRIM(v.responsavel), ''), 'Não Atribuído') AS r, COUNT(DISTINCT LTRIM(i.imovel, '0')) AS q
                    FROM imoveis i LEFT JOIN vinculos_adm v ON v.rowid = vinculo_de(i.administradora)
                    WHERE i.ativo = 1 AND (i.status_interno IS NULL OR i.status_interno NOT IN ({marcas}))
                    GROUP BY r ORDER BY q DESC""", encerrados).fetchall()
        ]
        return jsonify({
            'sucesso': True, 'versao': RAIO_X_VERSAO, 'data_corte': data_corte,
            'auditoria_total': aud_total, 'auditoria_ciclo': aud_ciclo, 'ultima_auditoria': ultima_aud,
            'status': status, 'portais': portais, 'operadores': operadores
        })
    except Exception as e:
        return jsonify({'sucesso': False, 'msg': str(e)}), 500
    finally:
        conexao.close()


@app.route('/api/raio_x/salvar_ajuste', methods=['POST'])
@login_obrigatorio
def api_raio_x_salvar_ajuste():
    esquecer_resumo_raio_x()      # o número do painel tem que cair junto com o caso
    dados = request.get_json(silent=True) or {}
    usuario = session.get('usuario', 'Sistema')
    origem = str(dados.get('origem') or 'raio_x').strip().lower()
    # 'salvar': só grava os dados, o caso continua na fila de erros.
    # 'tirar_fila' (padrão, compatível com quem não manda 'acao'): grava E marca AJUSTADO.
    acao = str(dados.get('acao') or 'tirar_fila').strip().lower()
    marcar_ajustado = (acao != 'salvar')

    conexao = conectar_banco()
    try:
        imovel = numero_do_imovel_pedido(conexao, dados)
        if not imovel:
            return jsonify({'sucesso': False, 'msg': 'Número do imóvel ausente.'}), 400
        ok, msg = aplicar_ajuste_de_acesso(conexao, imovel, dados, usuario, origem, marcar_ajustado=marcar_ajustado)
        if not ok:
            conexao.rollback()
            return jsonify({'sucesso': False, 'msg': msg, 'campos_obrigatorios': True}), 400
        conexao.commit()
        return jsonify({'sucesso': True, 'msg': msg, 'ajustado': marcar_ajustado})
    except Exception as e:
        conexao.rollback()
        return jsonify({'sucesso': False, 'msg': str(e)}), 500
    finally:
        conexao.close()


def numero_do_imovel_pedido(conexao, dados):
    """Aceita o número do imóvel ('imovel') ou o id da linha do painel ('id'; negativo = cofre)."""
    numero = str(dados.get('imovel') or '').strip()
    if numero:
        return numero
    try:
        id_linha = int(dados.get('id'))
    except (TypeError, ValueError):
        return ''
    if id_linha < 0:
        r = conexao.execute('SELECT imovel FROM historico_fechamento_mes WHERE id = ?', (abs(id_linha),)).fetchone()
    else:
        r = conexao.execute('SELECT imovel FROM imoveis WHERE id = ?', (id_linha,)).fetchone()
    return str(r['imovel']).strip() if r and r['imovel'] is not None else ''


def _sim(valor):
    return valor is True or str(valor or '').strip().lower() in ('1', 'true', 'sim', 'on', 'yes')


def validar_dados_de_acesso(dados, portal):
    """Quando o caso tem erro de login, a pessoa TEM que deixar o caminho pronto para o robô:
    login e senha + condomínio/bloco/unidade como estão no site, ou dizer que não existe mais login."""
    if _sim(dados.get('sem_login')):
        return ''
    faltando = []
    p = _portal_normal(portal)
    if not p:
        faltando.append('portal')
    if p in ('WINKER', 'WEBWARE', 'SUPERLOGICA') and not str(dados.get('emp_site') or '').strip():
        faltando.append('condomínio (como está no site)')
    if not str(dados.get('unid_site') or '').strip():
        faltando.append('unidade')
    if not str(dados.get('bloco_site') or '').strip() and not _sim(dados.get('sem_bloco')):
        faltando.append('bloco (ou marque "sem bloco")')
    if not login_e_compartilhado(portal):
        if not str(dados.get('login_acesso') or '').strip():
            faltando.append('login')
        if not str(dados.get('senha_acesso') or '').strip():
            faltando.append('senha')
    if faltando:
        return ('Caso com erro de login: informe ' + ', '.join(faltando) +
                ' — ou marque "Não possui mais login" (o imóvel passa para DIVERSOS).')
    return ''


def aplicar_ajuste_de_acesso(conexao, imovel, dados, usuario, origem='raio_x', marcar_ajustado=True):
    """Grava no cadastro o que a pessoa informou. Quando marcar_ajustado=True (botão "Tirar da Fila"),
    também registra AJUSTADO na auditoria — isso tira o erro do Raio-X, inclusive o de login que
    atravessa a atualização da base. Quando marcar_ajustado=False (botão "Salvar"), só grava os dados:
    o caso continua na fila de erros até alguém clicar em "Tirar da Fila". Devolve (ok, mensagem)."""
    garantir_colunas_acesso(conexao)
    agora = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    sem_login = _sim(dados.get('sem_login'))
    sem_bloco = _sim(dados.get('sem_bloco'))
    portal = 'DIVERSOS' if sem_login else str(dados.get('portal') or '').strip().upper()
    emp_site = str(dados.get('emp_site') or '').strip()
    bloco_site = '' if sem_bloco else str(dados.get('bloco_site') or '').strip()
    unid_site = str(dados.get('unid_site') or '').strip()
    login_acesso = '' if sem_login else str(dados.get('login_acesso') or '').strip()
    senha_acesso = '' if sem_login else str(dados.get('senha_acesso') or '').strip()

    # Erro de login pendente: os dados passam a ser obrigatórios — mas só quando a ação de fato tira
    # da fila. Um "Salvar" simples pode gravar dados incompletos; quem exige o pacote completo é o
    # "Tirar da Fila" (vale no Raio-X e ao marcar COLETADO).
    if marcar_ajustado:
        pendente = erros_ate_resolver_pendentes(conexao, [imovel], ('LOGIN',))
        if pendente or origem == 'coletado':
            erro = validar_dados_de_acesso(dict(dados, portal=portal), portal)
            if erro:
                return False, erro

    conexao.execute("INSERT OR IGNORE INTO cadastro_mestre_imoveis (imovel) VALUES (?)", (imovel,))
    p = _portal_normal(portal)
    if sem_login:
        conexao.execute("""
            UPDATE cadastro_mestre_imoveis
            SET portal = 'DIVERSOS', login_acesso = '', senha_acesso = '', possui_login = 'NAO',
                cred_user = ?, cred_data = ?
            WHERE LTRIM(imovel, '0') = LTRIM(?, '0')
        """, (usuario, datetime.now().strftime('%d/%m/%Y %H:%M'), imovel))
    elif p == 'WINKER':
        conexao.execute("""
            UPDATE cadastro_mestre_imoveis
            SET portal = 'WINKER',
                winker_condominio = ?,
                winker_predio = ?,
                winker_unidade = ?,
                winker_status = 'TEM',
                login_acesso = COALESCE(NULLIF(?, ''), login_acesso),
                senha_acesso = COALESCE(NULLIF(?, ''), senha_acesso)
            WHERE LTRIM(imovel, '0') = LTRIM(?, '0')
        """, (emp_site, bloco_site, unid_site, login_acesso, senha_acesso, imovel))
    elif p == 'WEBWARE':
        conexao.execute("""
            UPDATE cadastro_mestre_imoveis
            SET portal = 'WEBWARE',
                webware_empreendimento = ?,
                webware_unidade = ?,
                bloco_site = ?,
                login_acesso = COALESCE(NULLIF(?, ''), login_acesso),
                senha_acesso = COALESCE(NULLIF(?, ''), senha_acesso)
            WHERE LTRIM(imovel, '0') = LTRIM(?, '0')
        """, (emp_site, unid_site, bloco_site, login_acesso, senha_acesso, imovel))
    elif p == 'SUPERLOGICA':
        conexao.execute("""
            UPDATE cadastro_mestre_imoveis
            SET portal = ?,
                super_condominio = COALESCE(NULLIF(?, ''), super_condominio),
                super_bloco = ?,
                super_unidade = COALESCE(NULLIF(?, ''), super_unidade),
                login_acesso = COALESCE(NULLIF(?, ''), login_acesso),
                senha_acesso = COALESCE(NULLIF(?, ''), senha_acesso)
            WHERE LTRIM(imovel, '0') = LTRIM(?, '0')
        """, (portal, emp_site, bloco_site, unid_site, login_acesso, senha_acesso, imovel))
    else:
        conexao.execute("""
            UPDATE cadastro_mestre_imoveis
            SET portal = ?,
                emp_site = COALESCE(NULLIF(?, ''), emp_site),
                bloco_site = ?,
                unidade_site = COALESCE(NULLIF(?, ''), unidade_site),
                login_acesso = COALESCE(NULLIF(?, ''), login_acesso),
                senha_acesso = COALESCE(NULLIF(?, ''), senha_acesso)
            WHERE LTRIM(imovel, '0') = LTRIM(?, '0')
        """, (portal or 'DIVERSOS', emp_site, bloco_site, unid_site, login_acesso, senha_acesso, imovel))

    if not sem_login:
        atual = conexao.execute("SELECT login_acesso, senha_acesso FROM cadastro_mestre_imoveis "
                                "WHERE LTRIM(imovel, '0') = LTRIM(?, '0') LIMIT 1", (imovel,)).fetchone()
        tem_credencial = bool(atual and str(atual['login_acesso'] or '').strip() and str(atual['senha_acesso'] or '').strip())
        if tem_credencial or login_e_compartilhado(portal):
            conexao.execute("UPDATE cadastro_mestre_imoveis SET possui_login = 'SIM' WHERE LTRIM(imovel, '0') = LTRIM(?, '0')",
                            (imovel,))

    quando = ' ao marcar COLETADO' if origem == 'coletado' else ''
    if sem_login:
        resumo_ajuste = f"Sem login{quando}: informado por {usuario}. O imóvel passou para DIVERSOS (fora dos robôs de site)."
    else:
        resumo_ajuste = (f"Dados ajustados{quando} por {usuario} ({portal or 'DIVERSOS'}). Condomínio: '{emp_site}' | "
                         f"Bloco: '{bloco_site or ('sem bloco' if sem_bloco else '')}' | Unidade: '{unid_site}'"
                         + (" | login e senha informados" if (login_acesso and senha_acesso) else ''))

    if marcar_ajustado:
        # Registra o evento AJUSTADO na auditoria (isso tira o imóvel da lista de erros ativos / entra em "Já Ajustados")
        conexao.execute("""
            INSERT INTO auditoria_robos (robo, imovel_id, status, detalhe, data_hora)
            VALUES (?, ?, 'AJUSTADO', ?, ?)
        """, (portal or 'DIVERSOS', str(imovel), resumo_ajuste, agora))
        icone = '✏️'
    else:
        # Só salva: não grava AJUSTADO, então o caso continua classificado pelo último erro do robô
        # e aparece normalmente na fila (sem o filtro "Mostrar também os já ajustados").
        icone = '💾'

    # Registra no prontuário do imóvel para visualização da equipe
    caso = conexao.execute("SELECT id FROM imoveis WHERE LTRIM(imovel, '0') = LTRIM(?, '0') AND ativo = 1 LIMIT 1", (imovel,)).fetchone()
    if caso:
        conexao.execute("INSERT INTO historico_observacoes (imovel_id, data_hora, usuario, texto) VALUES (?, ?, ?, ?)",
                        (caso['id'], agora, usuario, f"{icone} {resumo_ajuste}"))
    esquecer_resumo_raio_x()

    if not marcar_ajustado:
        return True, 'Dados salvos! O caso continua na fila de erros — clique em "Tirar da Fila" quando quiser marcá-lo como resolvido.'
    if sem_login:
        return True, 'Marcado como "não possui login": o imóvel foi para DIVERSOS e saiu da fila de erros.'
    return True, 'Cadastro atualizado com sucesso! O caso foi marcado como ajustado.'


@app.route('/api/raio_x/pendencia_login', methods=['GET'])
@login_obrigatorio
def api_raio_x_pendencia_login():
    """O painel pergunta antes de deixar marcar COLETADO: este imóvel tem erro de login no Raio-X?"""
    conexao = conectar_banco()
    try:
        imovel = numero_do_imovel_pedido(conexao, request.args)
        if not imovel:
            return jsonify({'sucesso': False, 'msg': 'Imóvel não informado.'}), 400
        # liberação temporária: nesta janela ninguém é parado para informar login/senha
        if coleta_sem_login_liberada():
            return jsonify({'sucesso': True, 'imovel': imovel, 'pendente': False})
        achado = erros_ate_resolver_pendentes(conexao, [imovel], ('LOGIN',))
        reg = next(iter(achado.values()), None)
        resposta = {'sucesso': True, 'imovel': imovel, 'pendente': bool(reg)}
        if reg:
            resposta['erro'] = {'robo': reg.get('robo'), 'status': reg.get('status'),
                                'detalhe': _RX_SUFIXO_CADASTRO.sub('', str(reg.get('detalhe') or ''))[:400],
                                'data_hora': reg.get('data_hora')}
            resposta['cadastro'] = dados_acesso_do_cadastro(conexao, imovel)
        return jsonify(resposta)
    finally:
        conexao.close()

# ==============================================================================
# 🔐 SENHA DO PDF (FinMail): o colaborador informa o CPF certo e o robô usa nos próximos meses
# ==============================================================================
def _normalizar_cpf_informado(valor):
    """Só aceita CPF (11) ou CNPJ (14). Devolve (dígitos, erro)."""
    d = so_digitos(valor)
    if len(d) not in (11, 14):
        return '', 'Informe um CPF (11 dígitos) ou CNPJ (14 dígitos).'
    if len(set(d)) == 1:
        return '', 'Esse CPF/CNPJ não parece válido.'
    return d, ''


def gravar_cpf_senha_pdf(conexao, imovel, cpf, regra, usuario, origem_txt='Raio-X'):
    garantir_colunas_acesso(conexao)
    agora = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    regra = regra if regra in REGRAS_SENHA_PDF else ''
    conexao.execute("INSERT OR IGNORE INTO cadastro_mestre_imoveis (imovel) VALUES (?)", (imovel,))
    conexao.execute("""UPDATE cadastro_mestre_imoveis SET cpf_senha_pdf = ?, regra_senha_pdf = ?,
                              cpf_senha_user = ?, cpf_senha_data = ?
                       WHERE LTRIM(imovel, '0') = LTRIM(?, '0')""",
                    (cpf, regra, usuario, agora, imovel))
    resumo = (f"CPF da senha do PDF informado por {usuario} ({origem_txt}): {mascarar_documento(cpf)} | "
              f"regra: {REGRAS_SENHA_PDF.get(regra, regra)}. O FinMail passa a tentar esse CPF primeiro.")
    conexao.execute("INSERT INTO auditoria_robos (robo, imovel_id, status, detalhe, data_hora) VALUES (?, ?, 'AJUSTADO', ?, ?)",
                    ('AUTOMAÇÃO E-MAIL GERAL', str(imovel), resumo, agora))
    caso = conexao.execute("SELECT id FROM imoveis WHERE LTRIM(imovel, '0') = LTRIM(?, '0') AND ativo = 1 LIMIT 1",
                           (imovel,)).fetchone()
    if caso:
        conexao.execute("INSERT INTO historico_observacoes (imovel_id, data_hora, usuario, texto) VALUES (?, ?, ?, ?)",
                        (caso['id'], agora, usuario, f"🔐 {resumo}"))
    esquecer_resumo_raio_x()


@app.route('/api/raio_x/salvar_cpf_pdf', methods=['POST'])
@login_obrigatorio
def api_raio_x_salvar_cpf_pdf():
    dados = request.get_json(silent=True) or {}
    usuario = session.get('usuario', 'Sistema')
    cpf, erro = _normalizar_cpf_informado(dados.get('cpf'))
    if erro:
        return jsonify({'sucesso': False, 'msg': erro}), 400
    regra = str(dados.get('regra') or '').strip().upper()
    conexao = conectar_banco()
    try:
        imovel = numero_do_imovel_pedido(conexao, dados)
        if not imovel:
            return jsonify({'sucesso': False, 'msg': 'Informe o número do imóvel.'}), 400
        existe = conexao.execute("SELECT 1 FROM imoveis WHERE LTRIM(imovel, '0') = LTRIM(?, '0') LIMIT 1", (imovel,)).fetchone() or \
            conexao.execute("SELECT 1 FROM cadastro_mestre_imoveis WHERE LTRIM(imovel, '0') = LTRIM(?, '0') LIMIT 1", (imovel,)).fetchone()
        if not existe:
            return jsonify({'sucesso': False, 'msg': f'O imóvel {imovel} não existe na base nem no cadastro.'}), 400

        id_email = dados.get('id_email')
        origem_txt = 'Raio-X'
        if id_email not in (None, ''):
            email_row = conexao.execute("SELECT * FROM finmail_pdf_protegido WHERE id = ?", (id_email,)).fetchone()
            if not email_row:
                return jsonify({'sucesso': False, 'msg': 'E-mail não encontrado.'}), 404
            origem_txt = f"Raio-X, e-mail '{str(email_row['assunto'] or '')[:80]}'"
        gravar_cpf_senha_pdf(conexao, imovel, cpf, regra, usuario, origem_txt)
        if id_email not in (None, ''):
            conexao.execute("""UPDATE finmail_pdf_protegido SET status = 'RESOLVIDO', imovel = ?, resolvido_por = ?, resolvido_em = ?
                               WHERE id = ?""", (imovel, usuario, datetime.now().strftime('%Y-%m-%d %H:%M:%S'), id_email))
        conexao.commit()
        return jsonify({'sucesso': True, 'msg': f'CPF gravado para o imóvel {imovel}. O robô de e-mail usa esse CPF já na próxima leitura.'})
    except Exception as e:
        conexao.rollback()
        return jsonify({'sucesso': False, 'msg': str(e)}), 500
    finally:
        conexao.close()


@app.route('/api/raio_x/emails_pdf_protegido', methods=['GET'])
@login_obrigatorio
def api_raio_x_emails_pdf_protegido():
    """E-mails cujo PDF tem senha e que o robô não conseguiu ligar a nenhum imóvel."""
    conexao = conectar_banco()
    try:
        garantir_colunas_acesso(conexao)
        linhas = conexao.execute("""SELECT id, data_hora, assunto, remetente, link, regra FROM finmail_pdf_protegido
                                    WHERE status = 'PENDENTE' ORDER BY id DESC LIMIT 300""").fetchall()
    finally:
        conexao.close()
    return jsonify({'sucesso': True, 'emails': [dict(r) for r in linhas]})


@app.route('/api/raio_x/emails_pdf_protegido/descartar', methods=['POST'])
@login_obrigatorio
def api_raio_x_emails_pdf_protegido_descartar():
    dados = request.get_json(silent=True) or {}
    usuario = session.get('usuario', 'Sistema')
    conexao = conectar_banco()
    try:
        garantir_colunas_acesso(conexao)
        cur = conexao.execute("""UPDATE finmail_pdf_protegido SET status = 'DESCARTADO', resolvido_por = ?, resolvido_em = ?
                                 WHERE id = ? AND status = 'PENDENTE'""",
                              (usuario, datetime.now().strftime('%Y-%m-%d %H:%M:%S'), dados.get('id_email')))
        conexao.commit()
        return jsonify({'sucesso': cur.rowcount > 0, 'msg': 'E-mail tirado da lista.' if cur.rowcount else 'Nada para descartar.'})
    finally:
        conexao.close()


@app.route('/api/automacao/pdf_protegido', methods=['POST'])
def api_automacao_pdf_protegido():
    """O FinMail avisa: o PDF deste e-mail tem senha e nenhum CPF que ele conhece abriu.
    Com imóvel identificado vira caso do Raio-X (categoria Senha do PDF); sem imóvel, entra na lista
    de e-mails para o colaborador dizer de quem é e qual o CPF."""
    dados = request.get_json(silent=True) or {}
    if dados.get('token') != TOKEN_API_ROBO:
        return jsonify({'sucesso': False, 'msg': 'Acesso Negado'}), 401
    agora = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    assunto = str(dados.get('assunto') or '').strip()[:300]
    remetente = str(dados.get('remetente') or '').strip()[:200]
    link = str(dados.get('link') or '').strip()[:600]
    regra = str(dados.get('regra') or '').strip()[:120]
    message_id = str(dados.get('message_id') or '').strip()[:300]
    imovel = str(dados.get('imovel') or '').strip()
    conexao = conectar_banco()
    try:
        garantir_colunas_acesso(conexao)
        if imovel:
            detalhe = (f"O PDF do e-mail '{assunto}' tem senha e nenhum CPF cadastrado abriu"
                       + (f" (regra no e-mail: {regra})" if regra else '') +
                       ". Informe no Raio-X o CPF/CNPJ que abre o PDF." + (f" {link}" if link else ''))
            conexao.execute("INSERT INTO auditoria_robos (robo, imovel_id, status, detalhe, data_hora) VALUES (?, ?, ?, ?, ?)",
                            ('AUTOMAÇÃO E-MAIL GERAL', imovel, STATUS_SENHA_PDF, detalhe, agora))
            conexao.commit()
            esquecer_resumo_raio_x()
            return jsonify({'sucesso': True, 'destino': 'imovel'})
        ja = None
        if message_id:
            ja = conexao.execute("SELECT id FROM finmail_pdf_protegido WHERE message_id = ? AND status = 'PENDENTE'",
                                 (message_id,)).fetchone()
        if not ja:
            conexao.execute("""INSERT INTO finmail_pdf_protegido (data_hora, message_id, assunto, remetente, link, regra, status)
                               VALUES (?, ?, ?, ?, ?, ?, 'PENDENTE')""", (agora, message_id, assunto, remetente, link, regra))
            conexao.commit()
        return jsonify({'sucesso': True, 'destino': 'lista_emails'})
    finally:
        conexao.close()


# ==============================================================================
# 🟢🟡🔴 DIFICULDADE DE CADA CASO NO PAINEL
# ==============================================================================
@app.route('/api/painel/dificuldade', methods=['POST'])
@login_obrigatorio
def api_painel_dificuldade():
    """{ids das linhas do painel} -> {id: {nivel, motivo}}. Id negativo = linha do cofre (modo histórico)."""
    dados = request.get_json(silent=True) or {}
    ids = []
    for v in (dados.get('ids') or [])[:2000]:
        try:
            ids.append(int(v))
        except (TypeError, ValueError):
            continue
    if not ids:
        return jsonify({'sucesso': True, 'mapa': {}})
    conexao = conectar_banco()
    try:
        garantir_colunas_acesso(conexao)
        numero_por_id = {}
        for tabela, lista in (('imoveis', [i for i in ids if i > 0]), ('historico_fechamento_mes', [-i for i in ids if i < 0])):
            for inicio in range(0, len(lista), 800):
                lote = lista[inicio:inicio + 800]
                ph = ','.join('?' for _ in lote)
                for r in conexao.execute(f"SELECT id, imovel FROM {tabela} WHERE id IN ({ph})", lote):
                    numero_por_id[r['id'] if tabela == 'imoveis' else -r['id']] = _chave_imovel_raio_x(str(r['imovel'] or '').strip())
        chaves = sorted({c for c in numero_por_id.values() if c})
        cadastro = {}
        for inicio in range(0, len(chaves), 800):
            lote = chaves[inicio:inicio + 800]
            ph = ','.join('?' for _ in lote)
            for r in conexao.execute(
                    f"""SELECT LTRIM(imovel, '0') AS chave, portal, login_acesso, senha_acesso, possui_login
                        FROM cadastro_mestre_imoveis WHERE LTRIM(imovel, '0') IN ({ph})""", lote):
                cadastro.setdefault(r['chave'], r)
        agenda = ler_agenda_salva(conexao)
    finally:
        conexao.close()

    rodadas = {}
    for chave in ('winker', 'webware', 'superlogica'):
        horas, _inv = _horas_validas(agenda.get(chave, ''))
        rodadas[chave] = _texto_dos_horarios(horas)
    mapa = {}
    for id_linha, chave in numero_por_id.items():
        cad = cadastro.get(chave)
        if cad is None:
            nivel, motivo = 'DIFICIL', 'Sem cadastro de acesso'
        else:
            nivel, motivo = classificar_dificuldade(cad['portal'], cad['login_acesso'], cad['senha_acesso'], cad['possui_login'])
            robo = _portal_normal(cad['portal']).lower()
            if nivel == 'FACIL' and rodadas.get(robo):
                motivo += f' (robô roda {rodadas[robo]})'
            elif nivel == 'FACIL' and robo in ('ctrl', 'email'):
                motivo += ' (lido sozinho da caixa ctrlcond)'
        mapa[str(id_linha)] = {'nivel': nivel, 'motivo': motivo}
    return jsonify({'sucesso': True, 'mapa': mapa})


# ==============================================================================
# 🧪 RAIO-X RPA: TESTAR NO ROBÔ APENAS OS CASOS SELECIONADOS
# Depois de ajustar o cadastro, a pessoa seleciona os casos na tela e o sistema roda o robô (Winker/Webware)
# somente neles (opção --imoveis dos robôs) e mostra o resultado de cada um.
# ==============================================================================
TESTES_ROBO = {}
TESTES_ROBO_LOCK = threading.Lock()
ROBO_TESTE_SCRIPTS = {'WINKER': 'scripts/robos/robo_winker.py', 'WEBWARE': 'scripts/robos/robo_webware.py'}
ENCERRADOS_TESTE_ROBO = ('PAGO', 'PAGO NA ADM', 'PAGO NA 275', 'COLETADO')
MAX_IMOVEIS_TESTE_ROBO = 200
_RX_NUMERO_IMOVEL = re.compile(r'^[0-9A-Za-z._-]{1,30}$')


def _texto_vazio(v):
    return v is None or str(v).strip() == ''


def analisar_casos_teste_robo(conexao, pedidos):
    """Diz, para cada imóvel pedido, se o robô consegue pegá-lo agora (e, se não, por quê).
    Devolve também todas as grafias do número que existem no banco (os robôs comparam o texto exato)."""
    chaves, ignorados = [], []
    for p in pedidos:
        txt = str(p).strip()
        if not _RX_NUMERO_IMOVEL.match(txt):
            if txt:
                ignorados.append({'imovel': txt[:30], 'motivo': 'Número de imóvel inválido'})
            continue
        k = _chave_imovel_raio_x(txt)
        if k and k not in chaves:
            chaves.append(k)

    resultado = {'elegiveis': [], 'ignorados': ignorados, 'variantes': {'WINKER': [], 'WEBWARE': []}}
    if not chaves:
        return resultado

    ph = ','.join('?' for _ in chaves)
    imoveis_por_chave, cadastro_por_chave = {}, {}
    for r in conexao.execute(
        f"SELECT id, imovel, empreendimento, status_interno FROM imoveis WHERE ativo = 1 AND LTRIM(imovel, '0') IN ({ph})", chaves
    ).fetchall():
        imoveis_por_chave.setdefault(_chave_imovel_raio_x(r['imovel']), []).append(r)
    for r in conexao.execute(
        f"SELECT * FROM cadastro_mestre_imoveis WHERE LTRIM(imovel, '0') IN ({ph})", chaves
    ).fetchall():
        cadastro_por_chave.setdefault(_chave_imovel_raio_x(r['imovel']), []).append(dict(r))

    for k in chaves:
        rows = imoveis_por_chave.get(k, [])
        cads = cadastro_por_chave.get(k, [])
        if not rows:
            ignorados.append({'imovel': k, 'motivo': 'Imóvel não encontrado entre os ativos da base'})
            continue

        nome = str(rows[0]['empreendimento'] or '').strip() or next((str(c.get('empreendimento') or '').strip() for c in cads if c.get('empreendimento')), '')

        if any(str(r['status_interno'] or '').strip().upper() in ENCERRADOS_TESTE_ROBO for r in rows):
            ignorados.append({'imovel': k, 'empreendimento': nome, 'motivo': 'Já está COLETADO/PAGO: o robô só busca imóveis pendentes'})
            continue

        portais = {str(c.get('portal') or '').strip().upper() for c in cads}
        robo, motivo = None, ''
        if 'WINKER' in portais:
            cw = [c for c in cads if str(c.get('portal') or '').strip().upper() == 'WINKER']
            if any(str(c.get('winker_status') or '').strip().upper() == 'TEM'
                   and not _texto_vazio(c.get('winker_condominio')) and not _texto_vazio(c.get('winker_unidade')) for c in cw):
                robo = 'WINKER'
            elif not any(str(c.get('winker_status') or '').strip().upper() == 'TEM' for c in cw):
                motivo = "Winker: o cadastro ainda não está vinculado ao site (winker_status diferente de 'TEM')"
            else:
                motivo = 'Winker: faltam Condomínio ou Unidade do site no cadastro'
        elif 'WEBWARE' in portais:
            cw = [c for c in cads if str(c.get('portal') or '').strip().upper() == 'WEBWARE']
            if any(not _texto_vazio(c.get('login_acesso')) and not _texto_vazio(c.get('webware_empreendimento'))
                   and not _texto_vazio(c.get('webware_unidade')) for c in cw):
                robo = 'WEBWARE'
            elif all(_texto_vazio(c.get('login_acesso')) for c in cw):
                motivo = 'Webware: falta o login de acesso no cadastro'
            else:
                motivo = 'Webware: faltam Empreendimento ou Unidade do site no cadastro'
        else:
            nome_portal = next((p for p in portais if p), '')
            motivo = f'O portal {nome_portal} não tem robô de coleta' if nome_portal else 'Portal não definido no cadastro'

        if not robo:
            ignorados.append({'imovel': k, 'empreendimento': nome, 'motivo': motivo})
            continue

        variantes = {str(r['imovel']).strip() for r in rows}
        resultado['variantes'][robo].extend(v for v in variantes if _RX_NUMERO_IMOVEL.match(v) and v not in resultado['variantes'][robo])
        resultado['elegiveis'].append({'chave': k, 'imovel': str(rows[0]['imovel']).strip(), 'empreendimento': nome, 'robo': robo})
    return resultado


def _lista_pedidos_teste_robo(dados):
    pedidos = dados.get('imoveis') or []
    if not isinstance(pedidos, list):
        return None, 'Lista de imóveis inválida.'
    pedidos = [str(p).strip() for p in pedidos if str(p).strip()]
    if not pedidos:
        return None, 'Nenhum imóvel foi selecionado.'
    if len(pedidos) > MAX_IMOVEIS_TESTE_ROBO:
        return None, f'Selecione no máximo {MAX_IMOVEIS_TESTE_ROBO} imóveis por teste.'
    return pedidos, ''


@app.route('/api/raio_x/teste_robo/previa', methods=['POST'])
@login_obrigatorio
def api_raio_x_teste_robo_previa():
    pedidos, erro = _lista_pedidos_teste_robo(request.get_json(silent=True) or {})
    if erro:
        return jsonify({'sucesso': False, 'msg': erro})
    conexao = conectar_banco()
    try:
        analise = analisar_casos_teste_robo(conexao, pedidos)
    finally:
        conexao.close()
    ocupados = {r: bool(STATUS_ROBOS[r]['rodando']) for r in ROBO_TESTE_SCRIPTS}
    return jsonify({'sucesso': True, 'elegiveis': analise['elegiveis'], 'ignorados': analise['ignorados'], 'robos_ocupados': ocupados})


def _rodar_teste_robo(job_id, robo_id):
    job = TESTES_ROBO.get(job_id)
    if not job:
        return
    info = job['robos'][robo_id]
    argumentos = ['--imoveis', ','.join(info['variantes'])]
    if not job['marcar']:
        argumentos.append('--sem-marcar')
    info['estado'] = 'RODANDO'
    try:
        info['codigo'] = executar_script_externo(ROBO_TESTE_SCRIPTS[robo_id], '', robo_id, args_extra=argumentos)
    except Exception as e:
        info['msg'] = str(e)
    finally:
        info['estado'] = 'CONCLUIDO'


@app.route('/api/raio_x/teste_robo/iniciar', methods=['POST'])
@login_obrigatorio
def api_raio_x_teste_robo_iniciar():
    dados = request.get_json(silent=True) or {}
    pedidos, erro = _lista_pedidos_teste_robo(dados)
    if erro:
        return jsonify({'sucesso': False, 'msg': erro})
    marcar = bool(dados.get('marcar', True))
    usuario = session.get('usuario', 'Sistema')

    conexao = conectar_banco()
    try:
        analise = analisar_casos_teste_robo(conexao, pedidos)
        # Marco do início: só contam como resultado do teste as auditorias gravadas DEPOIS deste id (não depende de relógio)
        auditoria_id_inicial = conexao.execute("SELECT IFNULL(MAX(id), 0) AS m FROM auditoria_robos").fetchone()['m']
    finally:
        conexao.close()
    if not analise['elegiveis']:
        return jsonify({'sucesso': False, 'msg': 'Nenhum dos casos selecionados pode ser pego pelo robô agora.', 'ignorados': analise['ignorados']})

    por_robo = {}
    for e in analise['elegiveis']:
        por_robo.setdefault(e['robo'], []).append(e)

    job_id = uuid.uuid4().hex[:10]
    job = {
        'id': job_id, 'usuario': usuario, 'marcar': marcar,
        'criado': datetime.now(), 'iniciado_em': datetime.now().strftime('%Y-%m-%d %H:%M:%S'), 'auditoria_id_inicial': auditoria_id_inicial,
        'casos': {e['chave']: e for e in analise['elegiveis']}, 'ignorados': analise['ignorados'], 'robos': {}
    }
    iniciados, recusados = [], []
    with TESTES_ROBO_LOCK:
        for robo, itens in por_robo.items():
            info = {'chaves': [i['chave'] for i in itens], 'variantes': analise['variantes'][robo], 'estado': 'AGUARDANDO', 'msg': '', 'codigo': None}
            job['robos'][robo] = info
            if STATUS_ROBOS[robo]['rodando']:
                info['estado'] = 'RECUSADO'
                info['msg'] = f'O robô {robo.title()} já está em execução (rodada geral ou outro teste). Aguarde terminar e tente de novo.'
                recusados.append(info['msg'])
                continue
            STATUS_ROBOS[robo]['rodando'] = True
            STATUS_ROBOS[robo]['sucessos'] = 0
            STATUS_ROBOS[robo]['falhas'] = 0
            STATUS_ROBOS[robo]['logs'] = []
            iniciados.append(robo)
        TESTES_ROBO[job_id] = job
        # guarda só os testes mais recentes
        if len(TESTES_ROBO) > 30:
            for antigo in sorted(TESTES_ROBO, key=lambda j: TESTES_ROBO[j]['criado'])[:len(TESTES_ROBO) - 30]:
                TESTES_ROBO.pop(antigo, None)

    if not iniciados:
        return jsonify({'sucesso': False, 'msg': ' '.join(recusados) or 'Nenhum robô foi iniciado.'})

    for robo in iniciados:
        qtd = len(job['robos'][robo]['chaves'])
        log_robo_console(robo, f"🧪 Teste de casos selecionados no Raio-X por {usuario}: {qtd} imóvel(is) | marcar COLETADO: {'SIM' if marcar else 'NÃO'}.", "info")
        threading.Thread(target=_rodar_teste_robo, args=(job_id, robo), daemon=True).start()

    return jsonify({'sucesso': True, 'job_id': job_id, 'avisos': recusados, 'ignorados': analise['ignorados']})


@app.route('/api/raio_x/teste_robo/<job_id>', methods=['GET'])
@login_obrigatorio
def api_raio_x_teste_robo_status(job_id):
    job = TESTES_ROBO.get(job_id)
    if not job:
        return jsonify({'sucesso': False, 'msg': 'Teste não encontrado (o servidor pode ter sido reiniciado).'}), 404

    chaves = list(job['casos'].keys())
    ph = ','.join('?' for _ in chaves)
    conexao = conectar_banco()
    try:
        ultimo = {}
        for a in conexao.execute(
            f"""SELECT id, robo, imovel_id, status, detalhe, data_hora FROM auditoria_robos
                WHERE id > ? AND UPPER(IFNULL(status, '')) != 'AJUSTADO' AND LTRIM(imovel_id, '0') IN ({ph})
                ORDER BY id""", [job['auditoria_id_inicial']] + chaves
        ).fetchall():
            ultimo[_chave_imovel_raio_x(a['imovel_id'])] = a
        coletados = {
            _chave_imovel_raio_x(r['imovel'])
            for r in conexao.execute(
                f"SELECT imovel FROM imoveis WHERE ativo = 1 AND status_interno = 'COLETADO' AND LTRIM(imovel, '0') IN ({ph})", chaves
            ).fetchall()
        }
    finally:
        conexao.close()

    em_andamento = any(i['estado'] in ('AGUARDANDO', 'RODANDO') for i in job['robos'].values())
    resultados, resumo = [], {}
    for chave, caso in job['casos'].items():
        info = job['robos'][caso['robo']]
        r = {'chave': chave, 'imovel': caso['imovel'], 'empreendimento': caso['empreendimento'], 'robo': caso['robo'],
             'estado': '', 'titulo': '', 'detalhe': '', 'categoria': ''}
        a = ultimo.get(chave)
        if chave in coletados:
            r.update(estado='OK', titulo='Boleto coletado', detalhe='O robô baixou o boleto e o imóvel foi marcado como COLETADO.')
        elif a is not None and normalizar_texto(a['status']) in STATUS_SUCESSO_ROBO:
            r.update(estado='OK', titulo='Boleto baixado',
                     detalhe=(str(a['detalhe'] or '') + ' ' if a['detalhe'] else '') + ('' if job['marcar'] else '(teste sem marcar como coletado)'))
        elif a is not None:
            categoria = classificar_erro_raio_x(a['status'], a['detalhe'])
            titulo = RAIO_X_CATEGORIAS[categoria][1] or (a['status'] or 'Falha técnica')
            r.update(estado='ERRO', categoria=categoria, titulo=titulo, detalhe=_RX_SUFIXO_CADASTRO.sub('', str(a['detalhe'] or '')).strip(),
                     status_robo=a['status'])
        elif info['estado'] in ('AGUARDANDO', 'RODANDO'):
            r.update(estado='AGUARDANDO', titulo='Aguardando o robô')
        elif info['estado'] == 'RECUSADO':
            r.update(estado='NAO_INICIADO', titulo='Robô ocupado', detalhe=info['msg'])
        elif info['codigo'] not in (None, 0):
            r.update(estado='FALHA_ROBO', titulo='O robô terminou com erro',
                     detalhe=f"Código de saída {info['codigo']}. Veja o console do robô para o motivo.")
        elif caso['robo'] == 'WEBWARE' and not job['marcar']:
            r.update(estado='SEM_REGISTRO', titulo='Sem erro registrado',
                     detalhe='O Webware só registra falhas. Como foi um teste sem marcar, se o boleto foi baixado ele está na pasta de boletos.')
        else:
            r.update(estado='NAO_PROCESSADO', titulo='O robô não processou este imóvel',
                     detalhe='O robô terminou sem registrar nada para ele. Confira o console do robô.')
        resultados.append(r)
        resumo[r['estado']] = resumo.get(r['estado'], 0) + 1

    return jsonify({
        'sucesso': True, 'job_id': job_id, 'concluido': not em_andamento, 'marcar': job['marcar'], 'iniciado_em': job['iniciado_em'],
        'robos': {k: {'estado': v['estado'], 'msg': v['msg'], 'codigo': v['codigo']} for k, v in job['robos'].items()},
        'resultados': resultados, 'resumo': resumo, 'ignorados': job['ignorados']
    })

@app.route('/api/raio_x/remover_erro', methods=['POST'])
@login_obrigatorio
def api_raio_x_remover_erro():
    esquecer_resumo_raio_x()
    """Remove erros do Raio-X gravando DISPENSADO. Se o robô falhar na próxima rodada, reaparece."""
    dados = request.get_json(silent=True) or {}
    imoveis = dados.get('imoveis')
    if not imoveis:
        unico = dados.get('imovel')
        if unico:
            imoveis = [unico]
    if not imoveis:
        return jsonify({'sucesso': False, 'msg': 'Nenhum imóvel informado.'}), 400

    usuario = session.get('usuario', 'Sistema')
    agora = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    motivo = str(dados.get('motivo') or 'Erro desconsiderado pelo usuário').strip()

    conexao = conectar_banco()
    try:
        qtd = 0
        # Erro de login não sai na base do "remover": só some depois de resolvido (cadastro ajustado
        # ou "não possui login"). Assim ele não volta mês que vem com o mesmo login errado.
        com_login_pendente = erros_ate_resolver_pendentes(conexao, [str(i) for i in imoveis], ('LOGIN',))
        bloqueados = []
        for imv in imoveis:
            imv_str = str(imv).strip()
            if not imv_str:
                continue
            if _chave_imovel_raio_x(imv_str) in com_login_pendente:
                bloqueados.append(imv_str)
                continue

            cad = conexao.execute(
                "SELECT portal FROM cadastro_mestre_imoveis WHERE LTRIM(imovel, '0') = LTRIM(?, '0') LIMIT 1",
                (imv_str,)
            ).fetchone()
            portal = cad['portal'] if cad and cad['portal'] else 'AUTOMAÇÃO'

            detalhe = f"{motivo} por {usuario}."
            conexao.execute("""
                INSERT INTO auditoria_robos (robo, imovel_id, status, detalhe, data_hora)
                VALUES (?, ?, 'DISPENSADO', ?, ?)
            """, (portal, imv_str, detalhe, agora))

            imovel_bd = conexao.execute(
                "SELECT id FROM imoveis WHERE LTRIM(imovel, '0') = LTRIM(?, '0') AND ativo = 1 LIMIT 1",
                (imv_str,)
            ).fetchone()
            if imovel_bd:
                conexao.execute(
                    "INSERT INTO historico_observacoes (imovel_id, data_hora, usuario, texto) VALUES (?, ?, ?, ?)",
                    (imovel_bd['id'], agora, usuario, f"🗑️ Erro do robô removido do Raio-X por {usuario}.")
                )
            qtd += 1

        conexao.commit()
        msg = f'{qtd} erro(s) removido(s) com sucesso.'
        if bloqueados:
            msg += (f" {len(bloqueados)} caso(s) de login/senha não foram removidos: erro de login só sai "
                    "quando o cadastro é ajustado (login, senha, unidade e bloco) ou marcado como 'não possui login'.")
        return jsonify({'sucesso': qtd > 0 or not bloqueados, 'qtd': qtd, 'bloqueados': bloqueados, 'msg': msg})
    except Exception as e:
        conexao.rollback()
        return jsonify({'sucesso': False, 'msg': str(e)}), 500
    finally:
        conexao.close()

# A antiga "Conciliação de retorno" (/api/financeiro/conciliacao_retorno + robô conversor) foi substituída pela tela
# Dashboard gerencial > Base × Relatório × BW (conciliacao_bw.py): lê o BW (D-1) da mesma pasta, acha o imóvel pela
# carteira + valor no relBolMov e alimenta o de_para_retorno sozinha.

# É vital chamar a função aqui na raiz para o run.py enxergar ao subir o servidor
inicializar_app()

if __name__ == '__main__':
    print("🚀 Servidor em MODO DESENVOLVIMENTO (Atualização na hora)...", flush=True)
    app.run(host='0.0.0.0', port=5000, debug=True, use_reloader=False)