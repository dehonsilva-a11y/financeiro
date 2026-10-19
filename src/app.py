from dotenv import load_dotenv
load_dotenv()
from flask import Flask, render_template, request, redirect, url_for, jsonify, session, send_file
from flask import send_file, request, jsonify
from werkzeug.middleware.proxy_fix import ProxyFix
import sqlite3
import difflib
import re
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime, timedelta
import functools
import threading
import time
import os
import sys
import random
import unicodedata
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



# Virada de mês (arquivar / meta por mês / backup) — arquivo virada_mes.py.
# Funciona com o arquivo na mesma pasta do app.py (src/) ou na raiz do projeto (ao lado do run.py).
try:
    from . import virada_mes
except ImportError:
    import virada_mes

app = Flask(__name__)
app.secret_key = os.getenv("APP_SECRET_KEY")

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
    "FINMAIL": {
        "rodando": False, 
        "sucessos": 0,       # Representa os Boletos Lidos
        "falhas": 0,         # Recusas / Falhas gerais
        "erro_senha": 0,     # Erros de login/senha bloqueada
        "comunicados": 0,    # Comunicados internos
        "atas": 0,           # Atas de assembleia detectadas
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
# BASE DE DADOS E INFRAESTRUTURA
# =================================================================
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

    conexao.commit()
    conexao.close()




def normalizar_texto(valor):
    if valor is None:
        return ''
    texto = str(valor).strip().upper()
    texto = unicodedata.normalize('NFKD', texto)
    texto = ''.join(ch for ch in texto if not unicodedata.combining(ch))
    return texto


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
        LEFT JOIN vinculos_adm v ON TRIM(UPPER(i.administradora)) = TRIM(UPPER(v.administradora))
        WHERE i.ativo = 1
          AND i.media_emissao IS NOT NULL
          AND (i.status_interno IS NULL OR i.status_interno NOT IN ('PAGO', 'COLETADO'))
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
    return registros


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


def executar_processamento_disparos_diarios(send_emails=False, usuario_solicitante=None, responsavel_alvo=None):
    """usuario_solicitante = quem apertou o botão (assina a ação no prontuário/produtividade).
    responsavel_alvo      = de quem é a carteira que será disparada (se vazio, é a do próprio usuário)."""
    agora = datetime.now()
    data_hoje = agora.strftime('%Y-%m-%d')

    conexao = conectar_banco()
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
            'qtd_ja_disparados': qtd_ja_disparados, 'responsaveis': []
        }
        if not registros:
            return resultado

        data_hora_atual = agora.strftime('%Y-%m-%d %H:%M:%S')
        por_responsavel = {}

        # Quem assina a ação no prontuário? O operador logado ou o robô.
        nome_operador = usuario_solicitante.title() if usuario_solicitante else 'RPA ATIVO'

        for item in registros:
            responsavel = item['responsavel']
            por_responsavel[responsavel] = por_responsavel.get(responsavel, 0) + 1

            if not send_emails:
                continue

            adm = item['administradora']
            vinc = conexao.execute('SELECT responsavel, email FROM vinculos_adm WHERE UPPER(TRIM(administradora)) = UPPER(TRIM(?))', (adm,)).fetchone()

            destinatario = None
            if vinc and vinc['email']:
                destinatario = str(vinc['email']).strip()

            if not destinatario:
                resultado['qtd_sem_email'] += 1
                conexao.execute('INSERT INTO historico_observacoes (imovel_id, data_hora, usuario, texto) VALUES (?, ?, ?, ?)', (item['id'], data_hora_atual, nome_operador, f'⚠️ Email NÃO enviado: A administradora "{adm}" não possui e-mail cadastrado nas Configurações.'))
                conexao.commit()
                continue

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

            enviado = enviar_email_smtp(destinatario, assunto, corpo)

            if enviado:
                resultado['qtd_enviados'] += 1
                conexao.execute('INSERT INTO historico_emails (colaborador, administradora, qtd_imoveis, data_hora) VALUES (?, ?, 1, ?)', (nome_operador, adm, data_hora_atual))
                try:
                    conexao.execute('INSERT INTO produtividade (usuario, acao, imovel_id, data_hora) VALUES (?, ?, ?, ?)', (nome_operador, 'DISPAROU EMAIL COBRANCA', item['id'], data_hora_atual))
                except Exception:
                    pass

                conexao.execute('UPDATE imoveis SET status_interno = ? WHERE id = ?', ('SOLICITADO', item['id']))

                # Deixa claro no prontuário quando alguém disparou a carteira de outro colaborador
                if responsavel_alvo and normalizar_texto(responsavel) != normalizar_texto(nome_operador):
                    texto_obs = f'✉️ Status alterado para SOLICITADO via disparo em lote feito por {nome_operador} na carteira de {responsavel}.'
                else:
                    texto_obs = '✉️ Status alterado automaticamente para SOLICITADO via disparo em lote.'
                conexao.execute('INSERT INTO historico_observacoes (imovel_id, data_hora, usuario, texto) VALUES (?, ?, ?, ?)', (item['id'], data_hora_atual, nome_operador, texto_obs))
            else:
                resultado['qtd_falhas'] += 1
                conexao.execute('INSERT INTO historico_observacoes (imovel_id, data_hora, usuario, texto) VALUES (?, ?, ?, ?)', (item['id'], data_hora_atual, nome_operador, '⚠️ Falha: Erro no servidor do Gmail ao enviar.'))

            # 💾 Salva a cada e-mail: se o servidor cair no meio, o que já foi enviado fica registrado (e não é reenviado depois)
            conexao.commit()

        # Salva que essa carteira já foi processada hoje
        for resp, qtd in por_responsavel.items():
            conexao.execute('INSERT INTO log_disparos_diarios (data, responsavel, qtd_imoveis, data_hora) VALUES (?, ?, ?, ?)', (data_hoje, resp, qtd, data_hora_atual))

        conexao.commit()
        resultado['responsaveis'] = [{'responsavel': r, 'qtd_imoveis': q} for r, q in sorted(por_responsavel.items())]
        return resultado
    finally:
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


def login_obrigatorio(f):
    @functools.wraps(f)
    def wrap(*args, **kwargs):
        if 'usuario' not in session: return redirect(url_for('login'))
        return f(*args, **kwargs)
    return wrap

@app.route('/login', methods=['GET', 'POST'])
def login():
    # 🛡️ Função interna para blindar e padronizar o nome
    def padronizar_nome(nome_cru):
        if not nome_cru:
            return ""
            
        # Força a Primeira Letra Maiúscula (Ex: ryan -> Ryan)
        nome = nome_cru.strip().title()
        
        # Corrige as variações conhecidas
        if nome == 'Jéssica':
            nome = 'Jessica'
        elif nome == 'Giovanna':
            nome = 'Geovanna'
        elif nome == 'Márcio':
            nome = 'Marcio'
            
        return nome

    # 1. Tenta capturar o e-mail invisível que o Cloudflare envia
    email_cloudflare = request.headers.get('Cf-Access-Authenticated-User-Email')
    
    if email_cloudflare:
        # Exemplo: de "ryan.lima@lello.com.br" para "ryan.lima"
        parte_antes_do_arroba = email_cloudflare.split('@')[0]
        
        # Exemplo: de "ryan.lima" para "ryan"
        primeiro_nome = parte_antes_do_arroba.split('.')[0]
        
        # 💡 Aplica a blindagem de nomes!
        usuario_final = padronizar_nome(primeiro_nome)
        
        # Salva o nome certinho na sessão e manda direto para o painel!
        session['usuario'] = usuario_final
        return redirect(url_for('index'))
        
    # 2. MODO DESENVOLVIMENTO / FALLBACK
    # (Mantemos o login manual apenas para quando você for testar o código 
    # direto no VS Code do seu computador, sem passar pelo túnel do Cloudflare)
    if request.method == 'POST':
        nome_usuario = request.form.get('usuario')
        
        if nome_usuario:
            # 💡 Aplica a mesma blindagem no login manual!
            usuario_final = padronizar_nome(nome_usuario)
            
            session['usuario'] = usuario_final
            return redirect(url_for('index'))
        else:
            return render_template('login.html', erro="Informe um usuário de painel válido.")
            
    return render_template('login.html')

@app.route('/logout')
def logout():
    # 1. Limpa a memória interna do seu Python (Flask)
    session.pop('usuario', None)
    
    # 2. Redireciona o usuário para a rota de saída da Cloudflare
    return redirect('/cdn-cgi/access/logout')

@app.route('/')
@login_obrigatorio
def index():
    conexao = conectar_banco()
    usuario_atual = session.get('usuario', '').strip().lower()
    
    # 🎯 Captura a busca via texto (Nº Imóvel) e Modo Histórico
    filtro_busca = request.args.get('busca', '').strip()
    mes_historico = request.args.get('mes_historico', '').strip()

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
            gestores = ['marcio', 'ryan', 'alessandra', 'paulo']
            if usuario_atual and usuario_atual not in gestores and not session.get('ignorar_automatico') and not mes_historico:
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
    gestores = ['marcio', 'ryan', 'alessandra', 'paulo']
    
    if filtro_busca:
        filtro_resp = []
        filtro_per = []
        filtro_status = []
        filtro_automatico = False
    elif usuario_atual and usuario_atual not in gestores and set(filtro_resp) == {usuario_atual.title()} and not session.get('ignorar_automatico') and not mes_historico:
        filtro_automatico = True
        
    try:
        pagina_atual = int(request.args.get('pagina', 1))
    except ValueError:
        pagina_atual = 1
        
    # Paginação: carregar de X em X registros (padrão 300)
    itens_por_pagina = 100
    offset = (pagina_atual - 1) * itens_por_pagina

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
        where_sql = "WHERE i.administradora IS NOT NULL AND i.administradora != '' AND i.ativo = 1"
        parametros = []
    
    if filtro_busca:
        where_sql += f" AND {prefixo}imovel = ?"
        parametros.append(filtro_busca)
        
    if len(filtro_resp) > 0:
        placeholders = ', '.join(['?'] * len(filtro_resp))
        if mes_historico:
            where_sql += f" AND LOWER(TRIM(IFNULL(v.responsavel, h.responsavel))) IN ({placeholders})"
        else:
            where_sql += f" AND LOWER(TRIM(v.responsavel)) IN ({placeholders})"
        parametros.extend([r.strip().lower() for r in filtro_resp])
        
    if len(filtro_per) > 0:
        placeholders = ', '.join(['?'] * len(filtro_per))
        where_sql += f" AND substr({prefixo}vencimento, 1, 2) IN ({placeholders})"
        parametros.extend([str(p).zfill(2) for p in filtro_per])

    if len(filtro_status) > 0:
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

    # =========================================================
    # CONTAGEM DE PÁGINAS E BUSCA DINÂMICA
    # =========================================================
    tem_filtro_ativo = bool(filtro_busca or filtro_resp or filtro_per or filtro_status)

    if mes_historico:
        # 🧊 MODO HISTÓRICO
        query_count = f'''
            SELECT COUNT(h.id) FROM historico_fechamento_mes h
            LEFT JOIN vinculos_adm v ON TRIM(UPPER(h.administradora)) = TRIM(UPPER(v.administradora)) 
            {where_sql}
        '''
        total_registros = conexao.execute(query_count, parametros).fetchone()[0]
        total_paginas = (total_registros + itens_por_pagina - 1) // itens_por_pagina

        limite_str = "" if tem_filtro_ativo else "LIMIT ? OFFSET ?"
        params_finais = parametros if tem_filtro_ativo else parametros + [itens_por_pagina, offset]
        
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
            LEFT JOIN vinculos_adm v ON TRIM(UPPER(h.administradora)) = TRIM(UPPER(v.administradora))
            {where_sql}
            GROUP BY h.id
            ORDER BY substr(h.vencimento, 1, 2) ASC 
            {limite_str}
        '''
        imoveis_raw = conexao.execute(query_imoveis, params_finais).fetchall()
        if tem_filtro_ativo: total_paginas = 1

    else:
        # 🔥 MODO BASE ATIVA ORIGINAL
        query_count = f'''
            SELECT COUNT(i.id) FROM imoveis i 
            LEFT JOIN vinculos_adm v ON TRIM(UPPER(i.administradora)) = TRIM(UPPER(v.administradora)) 
            {where_sql}
        '''
        total_registros = conexao.execute(query_count, parametros).fetchone()[0]
        total_paginas = (total_registros + itens_por_pagina - 1) // itens_por_pagina

        limite_str = "" if tem_filtro_ativo else "LIMIT ? OFFSET ?"
        params_finais = parametros if tem_filtro_ativo else parametros + [itens_por_pagina, offset]

        query_imoveis = f'''
            SELECT i.*, 
                   IFNULL(v.responsavel, 'Não Atribuído') as colaborador, 
                   IFNULL(v.email, '') as email_adm
            FROM imoveis i 
            LEFT JOIN vinculos_adm v ON TRIM(UPPER(i.administradora)) = TRIM(UPPER(v.administradora))
            {where_sql}
            ORDER BY substr(i.vencimento, 1, 2) ASC 
            {limite_str}
        '''
        imoveis_raw = conexao.execute(query_imoveis, params_finais).fetchall()
        if tem_filtro_ativo: total_paginas = 1


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
            LEFT JOIN vinculos_adm v ON TRIM(UPPER(i.administradora)) = TRIM(UPPER(v.administradora))
            WHERE i.ativo = 1 AND i.media_emissao IS NOT NULL
              AND (i.status_interno IS NULL OR i.status_interno NOT IN ('PAGO', 'COLETADO'))
              AND v.responsavel LIKE ?
            ORDER BY i.vencimento ASC
        '''
        todos_meus_casos = conexao.execute(query_meus_casos_pendentes, (f"%{usuario_atual}%",)).fetchall()

        for row in todos_meus_casos:
            item = dict(row)
            
            media_emissao = str(item.get('media_emissao', '')).strip()
            dia_sem_zero = str(hoje.day)
            hoje_completo = hoje.strftime('%d/%m/%Y')
            
            if media_emissao in [hoje_dia_str, dia_sem_zero, hoje_completo]:
                emissao_hoje_lista.append(item)

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

    conexao.close()
    
    # 🎯 SE FOR O AJAX ROLANDO A TELA PARA BAIXO, DEVOLVA SÓ AS LINHAS NOVAS!
    if request.args.get('ajax') == 'true':
        return render_template('linhas_tabela.html', imoveis=imoveis)

    # Caso contrário, carrega a tela completa (primeiro acesso)
    return render_template('index.html', 
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
                           precisa_confirmar_rpa=precisa_confirmar_rpa,
                           aviso_virada=session.pop('aviso_virada', None))


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
        user_row = conexao.execute("SELECT is_master FROM usuarios_painel WHERE nome = ?", (usuario_atual_lower,)).fetchone()
        eh_master = True if (user_row and user_row['is_master'] == 1) or usuario_atual_lower in ['ryan', 'marcio', 'paulo', 'alessandra'] else False

        if id_inteiro < 0:
            # 🧊 MODO HISTÓRICO
            id_hist = abs(id_inteiro)
            caso_hist = conexao.execute('SELECT id, imovel, status_interno FROM historico_fechamento_mes WHERE id = ?', (id_hist,)).fetchone()
            
            if caso_hist:
                status_atual = str(caso_hist['status_interno'] or '').upper()
                if status_atual in ['PAGO', 'PAGO NA ADM', 'PAGO NA 275'] and not eh_master:
                    conexao.close()
                    return jsonify({'sucesso': False, 'msg': '🔒 Operação Bloqueada! Apenas administradores podem alterar o status de um boleto que já foi liquidado.'})

                num_imovel = caso_hist['imovel']
                conexao.execute('UPDATE historico_fechamento_mes SET status_interno = ? WHERE id = ?', (status_banco, id_hist))
                
                # Procura o ID real ativo para salvar histórico visual pra equipe
                caso_ativo = conexao.execute('SELECT id FROM imoveis WHERE imovel = ? AND ativo = 1 ORDER BY id DESC LIMIT 1', (num_imovel,)).fetchone()
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
                if status_atual in ['PAGO', 'PAGO NA ADM', 'PAGO NA 275'] and not eh_master:
                    conexao.close()
                    return jsonify({'sucesso': False, 'msg': '🔒 Operação Bloqueada! Apenas administradores podem alterar o status de um boleto que já foi liquidado.'})

                num_imovel = caso['imovel']
                caso_ativo = conexao.execute('SELECT id FROM imoveis WHERE imovel = ? AND ativo = 1 ORDER BY id DESC LIMIT 1', (num_imovel,)).fetchone()
                id_real = caso_ativo['id'] if caso_ativo else caso['id']
                
                # A grande correção: O cofre de histórico (historico_fechamento_mes) agora SÓ atualiza meses com o vencimento idêntico
                conexao.execute('UPDATE imoveis SET status_interno = ? WHERE id = ? OR (imovel = ? AND ativo = 1)', (status_banco, caso['id'], num_imovel))
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

    try:
        conexao = conectar_banco()
        for valor_recebido in ids:
            try:
                id_inteiro = int(valor_recebido)
            except:
                continue
                
            caso = conexao.execute('SELECT id, imovel, vencimento FROM imoveis WHERE id = ?', (id_inteiro,)).fetchone()
            if caso:
                id_real = caso['id']
                num_imovel = caso['imovel']
                
               # 🎯 SINCRONIZAÇÃO: Atualiza a linha e replica para cópias ativas do mesmo imóvel
                conexao.execute('UPDATE imoveis SET status_interno = ? WHERE id = ? OR (imovel = ? AND ativo = 1)', (novo_status, id_real, num_imovel))
                
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
        caso = conexao.execute('SELECT id, vencimento FROM imoveis WHERE (id = ? OR imovel = ?) AND ativo = 1 LIMIT 1', (valor_recebido, str(valor_recebido))).fetchone()
        
        mes_ref = ""
        imovel_id_real = None
        
        if caso:
            imovel_id_real = caso['id']
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
            conexao.execute('UPDATE imoveis SET status_interno = "SOLICITADO" WHERE id = ?', (imovel_id_real,))
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


@app.route('/api/sistema/enviar_disparos_hoje', methods=['POST'])
@login_obrigatorio
def api_enviar_disparos_hoje():
    usuario_atual = session.get('usuario', '')
    dados = request.get_json(silent=True) or {}
    nome_pedido = str(dados.get('responsavel') or '').strip()

    # Sem 'responsavel' = carteira de quem está logado (comportamento original).
    # Com 'responsavel' = dispara a carteira de outro colaborador (ex.: ele não entrou hoje).
    alvo = None
    if nome_pedido:
        alvo = resolver_nome_responsavel(nome_pedido)
        if not alvo:
            return jsonify({'sucesso': False, 'msg': f'Colaborador "{nome_pedido}" não encontrado nos vínculos de administradoras.'})

    chave = normalizar_texto(alvo or usuario_atual)
    with DISPAROS_LOCK:
        if chave in DISPAROS_EM_ANDAMENTO:
            return jsonify({'sucesso': False, 'msg': f'Já existe um disparo em andamento para a carteira de {(alvo or usuario_atual).title()}. Aguarde terminar e atualize a página.'})
        DISPAROS_EM_ANDAMENTO.add(chave)

    try:
        resultado = executar_processamento_disparos_diarios(send_emails=True, usuario_solicitante=usuario_atual, responsavel_alvo=alvo)
        return jsonify(resultado)
    except Exception as e:
        print(f"🚨 Erro no disparo em lote ({alvo or usuario_atual}): {e}", flush=True)
        return jsonify({'sucesso': False, 'msg': str(e)})
    finally:
        with DISPAROS_LOCK:
            DISPAROS_EM_ANDAMENTO.discard(chave)
# =================================================================
# ROTAS DE CONEXÃO DA API PARA OS AUTOMAÇÃOS EXTERNOS
# =================================================================


@app.route('/api/automacao/consultar_imovel', methods=['POST'])
def api_automacao_consultar_imovel():
    dados = request.get_json()
    if not dados or dados.get('token') != TOKEN_API_ROBO: return jsonify({'sucesso': False, 'msg': 'Acesso Negado'}), 401
    num_imovel = dados.get('imovel')
    conexao = conectar_banco()
    resultado = conexao.execute('SELECT i.imovel, i.administradora, i.status_interno, v.responsavel as colaborador FROM imoveis i LEFT JOIN vinculos_adm v ON TRIM(i.administradora) = TRIM(v.administradora) WHERE i.imovel = ? AND i.ativo = 1', (num_imovel,)).fetchone()
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
        v = con2.execute("SELECT responsavel FROM vinculos_adm WHERE TRIM(UPPER(administradora)) = TRIM(UPPER(?))", (str(melhor_match['administradora'] or ''),)).fetchone()
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
    query = "SELECT i.imovel, i.administradora, i.vencimento, i.empreendimento, i.sacado, i.cpf_cnpj, i.d_lote, i.endereco FROM imoveis i LEFT JOIN vinculos_adm v ON TRIM(i.administradora) = TRIM(v.administradora) WHERE i.ativo = 1 AND (i.status_interno IS NULL OR i.status_interno NOT IN ('PAGO', 'COLETADO'))"
    params = []
    if dados.get('colaborador'):
        query += " AND TRIM(v.responsavel) = ?"
        params.append(dados.get('colaborador').strip())
    resultado = conexao.execute(query, params).fetchall()
    conexao.close()
    lista = [dict(row) for row in resultado]
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
        dominante, qtd, total = virada_mes.mes_dominante_base_ativa(conexao)
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
            AND (status_interno IS NULL OR status_interno NOT IN ('PAGO', 'PAGO NA ADM', 'PAGO NA 275', 'COLETADO'))
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

    encerrados = "('PAGO', 'PAGO NA ADM', 'PAGO NA 275', 'COLETADO')"
    filtro_colab, params = "", []
    if dados.get('colaborador'):
        filtro_colab = " AND TRIM(v.responsavel) = ?"
        params.append(dados.get('colaborador').strip())

    # Query otimizada sem LTRIM no JOIN e sem subquery NOT EXISTS redundante
    query = f"""
        SELECT i.imovel, MAX(i.vencimento) AS vencimento, i.administradora,
               m.empreendimento AS empreendimento, m.winker_condominio, m.winker_predio, m.winker_unidade
        FROM imoveis i
        JOIN cadastro_mestre_imoveis m ON m.imovel = i.imovel
        LEFT JOIN vinculos_adm v ON TRIM(UPPER(i.administradora)) = TRIM(UPPER(v.administradora))
        WHERE i.ativo = 1
          AND UPPER(TRIM(m.portal)) = 'WINKER'
          AND m.winker_status = 'TEM'
          AND COALESCE(TRIM(m.winker_condominio), '') <> ''
          AND COALESCE(TRIM(m.winker_unidade), '') <> ''
          AND (i.status_interno IS NULL OR i.status_interno NOT IN {encerrados})
          {filtro_colab}
        GROUP BY i.imovel
        ORDER BY m.winker_condominio, i.imovel
    """
    conexao = conectar_banco()
    try:
        resultado = conexao.execute(query, params).fetchall()
    except Exception as e:
        return jsonify({'sucesso': False, 'msg': f'Consulta falhou: {e}'}), 500
    finally:
        conexao.close()
    lista = [dict(row) for row in resultado]
    return jsonify({'sucesso': True, 'quantidade': len(lista), 'dados': lista})

@app.route('/api/automacao/coletar', methods=['POST'])
def api_automacao_coletar():
    dados = request.get_json()
    if not dados or dados.get('token') != TOKEN_API_ROBO: 
        return jsonify({'sucesso': False}), 401
    
    # 1. Limpeza bruta do número do imóvel
    imovel_cru = str(dados.get('imovel', '')).strip()
    status_novo = str(dados.get('status', 'COLETADO')).strip().upper()
    nova_observacao = str(dados.get('observacao', '')).strip()
    nome_robo = str(dados.get('nome_robo', 'AUTOMAÇÃO DESCONHECIDO')).strip().upper()

    if not imovel_cru:
        return jsonify({'sucesso': False, 'msg': 'ID do imóvel vazio.'}), 400

    conexao = conectar_banco()
    
    try:
        # 2. Busca o ID REAL ignorando zeros à esquerda (ex: 0303102 = 303102)
        caso = conexao.execute("SELECT id, observacao FROM imoveis WHERE LTRIM(imovel, '0') = LTRIM(?, '0') AND ativo = 1 LIMIT 1", (imovel_cru,)).fetchone()
        
        if caso:
            imovel_id = caso['id']
            obs_antiga = caso['observacao'] or ""

            # 3. Mescla as observações (Não apaga o que o humano escreveu)
            obs_final = obs_antiga
            if nova_observacao and nova_observacao not in obs_antiga:
                obs_final = f"{obs_antiga} | {nova_observacao}" if obs_antiga else nova_observacao

            # 4. Atualiza EXATAMENTE a linha do ID Real (Sem chance de erro)
            conexao.execute("UPDATE imoveis SET status_interno = ?, observacao = ? WHERE id = ?", (status_novo, obs_final, imovel_id))
            
            data_hora = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            
            # Limpa produtividade velha se for um recadastro
            conexao.execute("DELETE FROM produtividade WHERE imovel_id = ? AND acao = 'COLETOU BOLETO'", (imovel_id,))
            
            # Histórico do Prontuário
            conexao.execute('INSERT INTO historico_observacoes (imovel_id, data_hora, usuario, texto) VALUES (?, ?, ?, ?)', 
                            (imovel_id, data_hora, nome_robo, f"Processado via automação. Status atribuído: {status_novo}"))
            
            # Produtividade Real
            if status_novo == "COLETADO":
                conexao.execute('INSERT INTO produtividade (usuario, acao, imovel_id, data_hora) VALUES (?, ?, ?, ?)', 
                                (nome_robo, "COLETOU BOLETO", imovel_id, data_hora))
                
            conexao.commit()
            return jsonify({'sucesso': True})
        else:
            return jsonify({'sucesso': False, 'msg': 'Imóvel não localizado.'})
            
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
    if robo not in ('WINKER', 'WEBWARE'):
        return jsonify({'sucesso': False, 'msg': 'Robô sem lista de boletos.'}), 400

    query = """
        SELECT MIN(i.id) AS rid, i.imovel, MAX(i.vencimento) AS vencimento, i.administradora,
               COALESCE(NULLIF(TRIM(m.empreendimento), ''), MAX(i.empreendimento)) AS empreendimento,
               m.winker_condominio, m.winker_predio, m.winker_unidade, m.winker_status,
               CASE
                 WHEN EXISTS (SELECT 1 FROM imoveis x WHERE x.ativo = 1 AND LTRIM(x.imovel, '0') = LTRIM(i.imovel, '0')
                              AND x.status_interno IN ('PAGO', 'PAGO NA ADM', 'PAGO NA 275')) THEN 'PAGO'
                 WHEN EXISTS (SELECT 1 FROM imoveis x WHERE x.ativo = 1 AND LTRIM(x.imovel, '0') = LTRIM(i.imovel, '0')
                              AND x.status_interno = 'COLETADO') THEN 'COLETADO'
                 ELSE 'PENDENTE'
               END AS situacao,
               MAX(i.status_interno) AS status_interno, MAX(i.observacao) AS observacao
        FROM imoveis i
        JOIN cadastro_mestre_imoveis m ON LTRIM(m.imovel, '0') = LTRIM(i.imovel, '0')
        WHERE i.ativo = 1 AND UPPER(TRIM(m.portal)) = ?
        GROUP BY i.imovel
        ORDER BY empreendimento, i.imovel
    """
    conexao = conectar_banco()
    try:
        linhas = [dict(r) for r in conexao.execute(query, (robo,)).fetchall()]
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
    return jsonify({'sucesso': True, 'robo': robo, 'total': len(linhas),
                    'resumo': {'pendentes': resumo['PENDENTE'], 'coletados': resumo['COLETADO'], 'pagos': resumo['PAGO']},
                    'dados': linhas})


@app.route('/api/automacao/pagamentos_pendentes', methods=['POST'])
def api_automacao_pagamentos_pendentes():
    dados = request.get_json()
    if not dados or dados.get('token') != TOKEN_API_ROBO: return jsonify({'sucesso': False}), 401
    
    conexao = conectar_banco()
    
    # 🎯 CORREÇÃO: Agora o Python manda o nome do responsável para o automação poder filtrar!
    query = """
        SELECT i.imovel, i.administradora, i.vencimento, i.empreendimento, i.sacado, i.cpf_cnpj, IFNULL(v.responsavel, 'Não Atribuído') as responsavel 
        FROM imoveis i 
        LEFT JOIN vinculos_adm v ON TRIM(UPPER(i.administradora)) = TRIM(UPPER(v.administradora)) 
        WHERE i.ativo = 1 AND i.status_interno = 'COLETADO'
    """
    
    resultado = conexao.execute(query).fetchall()
    conexao.close()
    
    lista = [dict(row) for row in resultado]
    return jsonify({'sucesso': True, 'quantidade': len(lista), 'dados': lista})

@app.route('/api/automacao/pagar', methods=['POST'])
def api_automacao_pagar():
    dados = request.get_json()
    if not dados or dados.get('token') != TOKEN_API_ROBO: return jsonify({'sucesso': False}), 401
    
    num_imovel = dados.get('imovel')
    deu_certo = dados.get('sucesso')
    msg_erro = dados.get('erro_msg', 'Motivo não especificado.')
    nome_robo = dados.get('nome_robo', 'AUTOMAÇÃO PAGAMENTOS')
    
    conexao = conectar_banco()
    caso = conexao.execute('SELECT id FROM imoveis WHERE imovel = ? AND ativo = 1', (num_imovel,)).fetchone()
    
    if caso:
        imovel_id = caso['id']
        data_hora = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        if deu_certo:
            conexao.execute('UPDATE imoveis SET status_interno = "PAGO" WHERE id = ?', (imovel_id,))
            conexao.execute('INSERT INTO historico_observacoes (imovel_id, data_hora, usuario, texto) VALUES (?, ?, ?, ?)', (imovel_id, data_hora, nome_robo, "Boleto liquidado/pago com sucesso via automação."))
            conexao.execute('INSERT INTO produtividade (usuario, acao, imovel_id, data_hora) VALUES (?, ?, ?, ?)', (nome_robo, "PAGOU BOLETO", imovel_id, data_hora))
        else:
            conexao.execute('UPDATE imoveis SET status_interno = "ERRO PAGAMENTO" WHERE id = ?', (imovel_id,))
            conexao.execute('INSERT INTO historico_observacoes (imovel_id, data_hora, usuario, texto) VALUES (?, ?, ?, ?)', (imovel_id, data_hora, nome_robo, f"⚠️ FALHA NO PAGAMENTO: {msg_erro}"))
        conexao.commit()
        conexao.close()
        return jsonify({'sucesso': True})
    conexao.close()
    return jsonify({'sucesso': False}), 404

# =================================================================
# ORQUESTRADOR DE AUTOMAÇÃOS (CENTRAL DE RPA MULTI-VIAS)
# =================================================================
@app.route('/modulo_automacoes')
@login_obrigatorio
def modulo_automacoes():
    conexao = conectar_banco()
    # Garante que a tabela de configurações gerais existe
    conexao.execute('CREATE TABLE IF NOT EXISTS config_sistema (chave TEXT PRIMARY KEY, valor TEXT)')
    
    # Puxa os horários salvos do banco
    horarios_raw = conexao.execute("SELECT chave, valor FROM config_sistema WHERE chave LIKE 'horario_robo_%'").fetchall()
    horarios = {row['chave']: row['valor'] for row in horarios_raw}
    
    # 🎯 NOVO: Busca as administradoras em ordem alfabética para o menu suspenso
    adms_raw = conexao.execute("SELECT DISTINCT administradora FROM vinculos_adm WHERE administradora IS NOT NULL AND administradora != '' ORDER BY administradora ASC").fetchall()
    lista_adms = [row['administradora'] for row in adms_raw]
    
    conexao.close()
    
    # Envia a lista_adms para o HTML desenhar o menu
    return render_template('automacoes.html', horarios=horarios, lista_adms=lista_adms)

@app.route('/api/sistema/salvar_horarios_robos', methods=['POST'])
@login_obrigatorio
def salvar_horarios_robos():
    dados = request.get_json() or {}
    conexao = conectar_banco()
    conexao.execute('CREATE TABLE IF NOT EXISTS config_sistema (chave TEXT PRIMARY KEY, valor TEXT)')
    
    try:
        # Salva o horário de cada automação individualmente
        for robo, horario in dados.items():
            chave = f"horario_robo_{robo}"
            conexao.execute("INSERT OR REPLACE INTO config_sistema (chave, valor) VALUES (?, ?)", (chave, horario))
        conexao.commit()
        return jsonify({'sucesso': True})
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


@app.route('/api/iniciar_dois_robos', methods=['POST'])
@login_obrigatorio
def api_iniciar_dois_robos():
    dados = request.get_json() or {}
    operadores = dados.get('operadores', [])
    
    if not operadores:
        return jsonify({"sucesso": False, "msg": "Nenhum operador foi selecionado!"})

    robo_id = "PAGAMENTOS"
    
    # 1. Verifica se já tem algo rodando
    if STATUS_ROBOS[robo_id]["rodando"]:
        return jsonify({"sucesso": False, "msg": "A automação de pagamentos já está em execução!"})
        
    STATUS_ROBOS[robo_id]["rodando"] = True
    STATUS_ROBOS[robo_id]["sucessos"] = 0
    STATUS_ROBOS[robo_id]["falhas"] = 0
    STATUS_ROBOS[robo_id]["logs"] = []
    
    nomes_formatados = ", ".join([op.title() for op in operadores])
    log_robo_console(robo_id, f"🚀 FORÇA TAREFA: Motores de {nomes_formatados} acionados simultaneamente!", "warn")

    global PROCESSOS_ATIVOS
    PROCESSOS_ATIVOS[robo_id] = [] 

    def run_thread(filtro, nome_thread):
        try:
            caminho_python = r"C:\Users\ryan.lima\Documents\SistemaImoveis\.venv-1\Scripts\python.exe"
            comando = [caminho_python, "scripts/robos/robo_pagamentos.py", filtro]
            
            ambiente = os.environ.copy()
            ambiente["PYTHONIOENCODING"] = "utf-8"
            
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
            t = threading.Thread(target=run_thread, args=(f"RESP:{op}", op.upper()))
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

    # 1. Busca os meses disponíveis BASEADO NO VENCIMENTO DOS IMÓVEIS (Regime de Competência)
    vencimentos = conexao.execute("SELECT DISTINCT vencimento FROM imoveis WHERE vencimento IS NOT NULL AND vencimento != '-'").fetchall()
    
    meses_set = set()
    for row in vencimentos:
        v = row['vencimento'].strip()
        parts = v.split('/')
        if len(parts) >= 3:
            m = parts[1].zfill(2)
            a = parts[2]
            if len(a) == 2: a = '20' + a # Converte '26' para '2026'
            meses_set.add((a, m))
            
    # Ordena do mais recente para o mais antigo
    meses_ordenados = sorted(list(meses_set), key=lambda x: (x[0], x[1]), reverse=True)
    
    meses_disponiveis = []
    meses_nomes = {'01': 'Janeiro', '02': 'Fevereiro', '03': 'Março', '04': 'Abril', '05': 'Maio', '06': 'Junho', '07': 'Julho', '08': 'Agosto', '09': 'Setembro', '10': 'Outubro', '11': 'Novembro', '12': 'Dezembro'}
    
    for ano_bd, mes_bd in meses_ordenados:
        nome_mes = meses_nomes.get(mes_bd, mes_bd)
        meses_disponiveis.append({
            'valor_mes': mes_bd,
            'valor_ano': ano_bd,
            'label': f"{nome_mes}/{ano_bd}"
        })
    
    mes = request.args.get('mes')
    ano = request.args.get('ano')

    # 2. Define o mês de exibição selecionado
    if mes and ano:
        mes_selecionado = mes.zfill(2)
        ano_selecionado = ano
        mes_formatado = f'{mes_selecionado}/{ano_selecionado}'
    else:
        if meses_disponiveis:
            mes_selecionado = meses_disponiveis[0]['valor_mes']
            ano_selecionado = meses_disponiveis[0]['valor_ano']
            mes_formatado = f"{mes_selecionado}/{ano_selecionado}"
        else:
            data_atual = datetime.now()
            mes_formatado = data_atual.strftime('%m/%Y')
            mes_selecionado = data_atual.strftime('%m')
            ano_selecionado = data_atual.strftime('%Y')

    # Cria as variáveis de filtro LIKE para buscar com precisão o vencimento na query
    ano_curto = ano_selecionado[-2:]
    like_completo = f"%/{mes_selecionado}/{ano_selecionado}"
    like_curto = f"%/{mes_selecionado}/{ano_curto}"

    # -----------------------------------------------------------------
    # 🔥 CONSULTA DE DISPAROS
    # -----------------------------------------------------------------
    query_disparos = """
        SELECT p.usuario, COUNT(*) as qtd 
        FROM produtividade p
        JOIN imoveis i ON p.imovel_id = i.id
        WHERE p.acao = 'DISPAROU EMAIL COBRANCA' 
          AND (i.vencimento LIKE ? OR i.vencimento LIKE ?)
        GROUP BY p.usuario
    """
    dados_disparos = conexao.execute(query_disparos, (like_completo, like_curto)).fetchall()

    # -----------------------------------------------------------------
    # 🔥 CONSULTA MESTRA (COLETAS VINCULADAS AO MÊS DE VENCIMENTO)
    # -----------------------------------------------------------------
    # 🎯 ATUALIZADO: Agora usa a "Mágica do Alinhamento" para bater os números perfeitamente!
    query_carteiras = """
        SELECT 
            IFNULL(TRIM(v.responsavel), 'Não Atribuído') as dono_carteira,
            IFNULL(
                (SELECT p.usuario 
                 FROM produtividade p 
                 WHERE p.imovel_id = i.id 
                   AND p.acao IN ('COLETOU BOLETO', 'PAGOU BOLETO', 'PAGOU BOLETO NA 275', 'ALTEROU STATUS PARA PAGO NA ADM') 
                 ORDER BY p.id DESC LIMIT 1),
                IFNULL(
                    (SELECT h.usuario 
                     FROM historico_observacoes h 
                     WHERE h.imovel_id = i.id 
                       AND (UPPER(h.texto) LIKE '%PAGO%' OR UPPER(h.texto) LIKE '%COLETADO%') 
                     ORDER BY h.id DESC LIMIT 1),
                    'Sistema / Planilha'
                )
            ) as executor,
            COUNT(DISTINCT i.imovel) as qtd
        FROM imoveis i
        LEFT JOIN vinculos_adm v ON TRIM(UPPER(i.administradora)) = TRIM(UPPER(v.administradora))
        WHERE (i.vencimento LIKE ? OR i.vencimento LIKE ?)
          AND i.status_interno IN ('PAGO', 'COLETADO', 'PAGO NA 275', 'PAGO NA ADM')
        GROUP BY dono_carteira, executor
    """
    dados_carteiras = conexao.execute(query_carteiras, (like_completo, like_curto)).fetchall()

    ranking_carteiras_dict = {}
    tooltips_cards = {"finmail": {}, "webware": {}, "winker": {}, "scanner": {}, "manual": {}}

    for row in dados_carteiras:
        dono_cru = row['dono_carteira'].strip() if row['dono_carteira'] else 'Não Atribuído'
        dono = 'Não Atribuído' if dono_cru.upper() in ['NÃO ATRIBUÍDA', 'NÃO ATRIBUÍDO', ''] else dono_cru.title()
        
        # Alinhamento preventivo de acentos
        if dono == "Jéssica": dono = "Jessica"
        
        executor = row['executor']
        qtd = row['qtd']

        # Alimenta o Ranking Geral por Carteira
        if dono not in ranking_carteiras_dict:
            ranking_carteiras_dict[dono] = {'total': 0, 'detalhes': {}}
        ranking_carteiras_dict[dono]['total'] += qtd

        # Identifica quem operou e alimenta os Tooltips dos Cards do Topo
        if "WEBWARE" in executor.upper(): 
            nome_exec = "🤖 Automação Webware"
            tooltips_cards["webware"][dono] = tooltips_cards["webware"].get(dono, 0) + qtd
        elif "WINKER" in executor.upper(): 
            nome_exec = "🤖 Automação Winker"
            tooltips_cards["winker"][dono] = tooltips_cards["winker"].get(dono, 0) + qtd
        elif "FINMAIL" in executor.upper() or "GERAL" in executor.upper(): 
            nome_exec = "🤖 Automação FinMail"
            tooltips_cards["finmail"][dono] = tooltips_cards["finmail"].get(dono, 0) + qtd
        elif "SCANNER" in executor.upper(): 
            nome_exec = "📄 Scanner Físico"
            tooltips_cards["scanner"][dono] = tooltips_cards["scanner"].get(dono, 0) + qtd
            
        # 🎯 NOVA REGRA: Barra o "Sistema/Planilha" de entrar na conta da Equipe Manual!
        elif "SISTEMA" in executor.upper() or "PLANILHA" in executor.upper(): 
            nome_exec = "⚙️ Sistema / Planilha"
            if "sistema" not in tooltips_cards: tooltips_cards["sistema"] = {}
            tooltips_cards["sistema"][dono] = tooltips_cards["sistema"].get(dono, 0) + qtd
            
        else: 
            nome_exec = f"👤 {executor.title()}"
            if "Jéssica" in nome_exec: nome_exec = nome_exec.replace("Jéssica", "Jessica")
            
            # 🎯 MÁGICA AQUI: O card 'manual' agora agrupa SÓ os humanos!
            tooltips_cards["manual"][nome_exec] = tooltips_cards["manual"].get(nome_exec, 0) + qtd

        # 🎯 MÁGICA AQUI: Essa linha garante que os detalhes ao clicar no ranking continuem funcionando!
        ranking_carteiras_dict[dono]['detalhes'][nome_exec] = ranking_carteiras_dict[dono]['detalhes'].get(nome_exec, 0) + qtd

    ranking_carteiras = []
    for dono, dados in ranking_carteiras_dict.items():
        detalhes_list = [{"ex": ex, "q": q} for ex, q in sorted(dados['detalhes'].items(), key=lambda item: item[1], reverse=True)]
        ranking_carteiras.append({
            'nome': dono,
            'total': dados['total'],
            'detalhes': detalhes_list
        })
    ranking_carteiras.sort(key=lambda x: x['total'], reverse=True)

    def formatar_lista(dicionario):
        return [{"nome": d, "qtd": q} for d, q in sorted(dicionario.items(), key=lambda item: item[1], reverse=True)]

    # Estrutura final
    stats = {
        "finmail": sum(tooltips_cards["finmail"].values()),
        "webware": sum(tooltips_cards["webware"].values()),
        "winker": sum(tooltips_cards["winker"].values()),
        "scanner": sum(tooltips_cards["scanner"].values()),
        "equipe_manual": sum(tooltips_cards["manual"].values()),
        "sistema_planilha": sum(tooltips_cards.get("sistema", {}).values()), # Guarda os valores da planilha em separado
        
        "lista_finmail": formatar_lista(tooltips_cards["finmail"]),
        "lista_webware": formatar_lista(tooltips_cards["webware"]),
        "lista_winker": formatar_lista(tooltips_cards["winker"]),
        "lista_scanner": formatar_lista(tooltips_cards["scanner"]),
        "lista_manual": formatar_lista(tooltips_cards["manual"]),
        
        "ranking_carteiras": ranking_carteiras,
        "ranking_disparos": []
    }
    
    # Adiciona o volume de planilhas no total geral para a matemática do gráfico continuar batendo
    stats["total_geral"] = stats["finmail"] + stats["webware"] + stats["winker"] + stats["scanner"] + stats["equipe_manual"] + stats.get("sistema_planilha", 0)

    # 🎯 BUSCA A META INICIAL DA BASE DO MÊS
    historico_base = conexao.execute("SELECT COUNT(*) FROM historico_fechamento_mes WHERE mes_referencia = ? AND administradora != ''", (mes_formatado,)).fetchone()[0]
    
    if historico_base > 0:
        stats["total_meta_base"] = historico_base
    else:
        try:
            conexao.execute('CREATE TABLE IF NOT EXISTS config_sistema (chave TEXT PRIMARY KEY, valor TEXT)')
            metas_db = conexao.execute("SELECT valor FROM config_sistema WHERE chave LIKE 'meta_dia_%'").fetchall()
            if metas_db:
                stats["total_meta_base"] = sum([int(r['valor']) for r in metas_db if r['valor'].isdigit()])
            else:
                stats["total_meta_base"] = conexao.execute("SELECT COUNT(*) FROM imoveis WHERE ativo=1 AND administradora != '' AND (vencimento LIKE ? OR vencimento LIKE ?)", (like_completo, like_curto)).fetchone()[0]
        except:
            stats["total_meta_base"] = 0

    # 🎯 AGRUPA OS NOMES COM LETRAS MAIÚSCULAS E MINÚSCULAS NO PYTHON
    agrupamento_disparos = {}
    for linha in dados_disparos:
        nome_cru = linha['usuario'] if linha['usuario'] else 'Desconhecido'
        nome_limpo = nome_cru.strip().title()
        if nome_limpo == "Jéssica": nome_limpo = "Jessica"
        agrupamento_disparos[nome_limpo] = agrupamento_disparos.get(nome_limpo, 0) + linha['qtd']

    for nome, qtd in agrupamento_disparos.items():
        stats["ranking_disparos"].append({"nome": nome, "qtd": qtd})
        
    stats["ranking_disparos"].sort(key=lambda x: x['qtd'], reverse=True)

    conexao.close()

    return render_template('relatorio_avancado.html', 
                           stats=stats, 
                           mes=mes_formatado, 
                           mes_selecionado=mes_selecionado, 
                           ano_selecionado=ano_selecionado, 
                           meses_disponiveis=meses_disponiveis)

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
    user_row = conexao.execute("SELECT is_master FROM usuarios_painel WHERE nome = ?", (usuario_atual,)).fetchone()
    eh_master = True if (user_row and user_row['is_master'] == 1) or usuario_atual in ['ryan', 'marcio', 'paulo', 'alessandra'] else False

    # 💡 SQL MELHORADO: Usa TRIM para já matar espaços em branco nas pontas
    query = '''SELECT DISTINCT TRIM(i.administradora) as administradora, 
                      IFNULL(v.responsavel, 'Não Atribuído') as colaborador, 
                      IFNULL(v.email, '') as email 
               FROM imoveis i 
               LEFT JOIN vinculos_adm v ON TRIM(UPPER(i.administradora)) = TRIM(UPPER(v.administradora)) 
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
                
                # Salva o vínculo de forma permanente na tabela mestre!
                conexao.execute(
                    'INSERT OR REPLACE INTO vinculos_adm (administradora, responsavel, email) VALUES (?, ?, ?)',
                    (adm, novo_resp, email_atual)
                )
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
    conexao.close()
    
    return render_template('configuracoes.html', vinculos=vinculos, ultimo_upload=ultimo_upload, usuarios_salvos=usuarios_salvos, usuario_atual=usuario_atual, eh_master=eh_master)


@app.route('/atualizar_configuracoes_adm', methods=['POST'])
@login_obrigatorio
def atualizar_configuracoes_adm():
    adm = request.form.get('administradora').strip()
    colab = request.form.get('colaborador')
    email_bruto = request.form.get('email')
    
    conexao = conectar_banco()
    
    # Pega os dados atuais para não apagar o que já existe caso envie vazio
    atual = conexao.execute("SELECT responsavel, email FROM vinculos_adm WHERE administradora = ?", (adm,)).fetchone()
    resp_atual = atual['responsavel'] if atual else "Não Atribuído"
    email_atual = atual['email'] if atual else ""
    
    novo_colab = colab if colab else resp_atual
    
    # 🎯 MÁGICA DA GRAVAÇÃO: Caça todos os e-mails digitados e formata perfeitamente com vírgula!
    if email_bruto is not None:
        if email_bruto.strip() == "":
            novo_email = "" # Permite apagar o e-mail se enviar em branco
        else:
            # Extrai os e-mails independente da bagunça digitada
            lista_emails = re.findall(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', email_bruto)
            if lista_emails:
                novo_email = ', '.join(lista_emails) # Salva no banco como: email1@a.com, email2@a.com
            else:
                novo_email = email_bruto.strip() # Se não achar padrão de e-mail, salva o que veio (fallback)
    else:
        novo_email = email_atual
        
    conexao.execute('INSERT OR REPLACE INTO vinculos_adm (administradora, responsavel, email) VALUES (?, ?, ?)', (adm, novo_colab, novo_email))
    conexao.commit()
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
            # Pega o email atual para não apagar caso já exista
            atual = conexao.execute("SELECT email FROM vinculos_adm WHERE administradora = ?", (adm,)).fetchone()
            email_atual = atual['email'] if atual else ""
            
            # Insere ou atualiza mantendo o e-mail intacto
            conexao.execute('INSERT OR REPLACE INTO vinculos_adm (administradora, responsavel, email) VALUES (?, ?, ?)', (adm, novo_colab, email_atual))
        conexao.commit()
        return jsonify({'sucesso': True})
    except Exception as e:
        return jsonify({'sucesso': False, 'msg': str(e)})
    finally:
        conexao.close()

@app.route('/api/sistema/upload_base', methods=['POST'])
@login_obrigatorio
def api_sistema_upload_base():
    conexao = None
    caminho_temp = None
    virada_info = None
    if 'arquivo_base' not in request.files: return "Nenhum arquivo enviado", 400
    arquivo = request.files['arquivo_base']
    if arquivo.filename == '': return "Nome de arquivo inválido", 400

    try:
        nome_seguro = secure_filename(arquivo.filename)
        caminho_temp = os.path.join(os.getcwd(), nome_seguro)
        arquivo.save(caminho_temp)

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
        cursor_robos = conexao.execute("SELECT imovel FROM cadastro_mestre_imoveis WHERE login_acesso IS NOT NULL AND login_acesso != ''")
        for row in cursor_robos.fetchall():
            imoveis_robo.add(row['imovel'])

        # 🚀 A "FOTOGRAFIA ATRASADA" FOI REMOVIDA DAQUI! 
        
        # 🔄 VIRADA DE MÊS AUTOMÁTICA: se a planilha é de um mês POSTERIOR ao da base ativa, arquiva a base
        # antiga no cofre (com backup do banco) ANTES de desativá-la. Se falhar, o upload aborta sem mexer em nada.
        virada_info = virada_mes.preparar_virada_upload(conexao, df_nova_base, usuario=session.get('usuario'), arquivo=nome_seguro)

        # Desativa a base antiga (mas mantém ela no banco para histórico)
        conexao.execute("UPDATE imoveis SET ativo = 0")
        
        for index, row in df_nova_base.iterrows():
            num_imovel = str(row.get('Imovel', '')).replace('.0', '').strip()
            if not num_imovel or num_imovel == 'nan': continue
            
            venc_bruto = str(row.get('Vencimento', '')).strip()
            adm  = str(row.get('Administradora', '')).strip()
            cart = str(row.get('Carteira', '')).replace('.0', '').strip()
            lote = str(row.get('D.Lote', '')).replace('.0', '').strip()
            emp  = str(row.get('Empreendimento', '')).strip()
            end  = str(row.get('Endereço do Imóvel', '')).strip()
            sac  = str(row.get('Sacado', '')).strip()
            cpf  = str(row.get('CPF/CNPJ', '')).replace('.0', '').strip()
            
            dt_comdesc = str(row.get('Dt.ComDesc', '')).replace('nan', '').strip()
            acesso = str(row.get('Acesso', '')).replace('nan', '').strip()
            
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
            dados_antigos = conexao.execute(
                "SELECT status_interno, observacao, vencimento FROM imoveis WHERE imovel = ? ORDER BY id DESC LIMIT 1", 
                (num_imovel,)
            ).fetchone()

            status_salvo, obs_salva = None, None
            
            if dados_antigos and (dados_antigos['status_interno'] or dados_antigos['observacao']):
                status_antigo = dados_antigos["status_interno"]
                obs_salva = dados_antigos["observacao"]
                venc_antigo = str(dados_antigos["vencimento"] or "")
                
                # 🎯 INTELIGÊNCIA: É o mesmo mês ou a base virou?
                def extrair_mes_ano(data_str):
                    if not data_str or str(data_str).lower() in ['nan', 'nat', 'none', '-', '']: return ""
                    p = str(data_str).split(' ')[0].split('/')
                    if len(p) >= 3: return f"{p[1]}/{p[2][-2:]}" # Ex: 08/26
                    return str(data_str)
                
                mes_ano_velho = extrair_mes_ano(venc_antigo)
                mes_ano_novo = extrair_mes_ano(venc)

                if mes_ano_velho == mes_ano_novo and mes_ano_novo != "":
                    # ATUALIZAÇÃO NO MESMO MÊS: Mantém o status que está no banco NESTE SEGUNDO!
                    status_salvo = status_antigo
                else:
                    # VIRADA DE MÊS: Zera os status para recomeçar o ciclo (Tudo em branco)
                    status_salvo = None
                    obs_salva = None

            if not status_salvo and num_imovel in imoveis_robo:
                status_salvo = "AUTOMAÇÃO"

            query_ins = 'INSERT INTO imoveis (imovel, vencimento, administradora, carteira, d_lote, empreendimento, endereco, sacado, cpf_cnpj, ativo, status_interno, observacao, dt_comdesc, acesso, media_emissao) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?)'
            conexao.execute(query_ins, (num_imovel, venc, adm, cart, lote, emp, end, sac, cpf, status_salvo, obs_salva, dt_comdesc, acesso, media_emissao))

        # 🔄 Fecha a virada: guarda a meta do mês que acabou e congela a meta do mês novo
        virada_mes.finalizar_virada_upload(conexao, virada_info)

        usuario_atual = session.get('usuario')
        data_hora_atual = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        conexao.execute('INSERT INTO produtividade (usuario, acao, imovel_id, data_hora) VALUES (?, ?, NULL, ?)', 
                        (usuario_atual, f"UPLOAD BASE|{nome_seguro}", data_hora_atual))

        conexao.commit()
        conexao.close()
        os.remove(caminho_temp)
        aviso = virada_mes.montar_aviso(virada_info)
        if aviso:
            session['aviso_virada'] = aviso
        return redirect(url_for('index'))
    
    except Exception as e:
        try:
            if conexao is not None:
                conexao.rollback()
                conexao.close()
        except Exception:
            pass
        try:
            if caminho_temp and os.path.exists(caminho_temp):
                os.remove(caminho_temp)
        except Exception:
            pass
        return f"Erro fatal ao processar a planilha: {str(e)}", 500
    
@app.route('/api/sistema/historico_geral_completo')
@login_obrigatorio
def api_historico_geral_completo():
    conexao = conectar_banco()
    query = '''SELECT p.data_hora, p.usuario, p.acao, i.imovel, i.administradora, v.responsavel as colaborador FROM produtividade p LEFT JOIN imoveis i ON p.imovel_id = i.id LEFT JOIN vinculos_adm v ON TRIM(i.administradora) = TRIM(v.administradora) WHERE p.acao IN ('COLETOU BOLETO', 'DISPAROU EMAIL COBRANCA') ORDER BY p.data_hora DESC LIMIT 1000'''
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
        LEFT JOIN vinculos_adm v ON TRIM(i.administradora) = TRIM(v.administradora) 
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

@app.route('/dashboard_gerencial')
@login_obrigatorio
def dashboard_gerencial():
    usuarios_autorizados = ['marcio', 'ryan', 'alessandra', 'paulo']
    usuario_atual = session.get('usuario', '').lower()
    
    if usuario_atual not in usuarios_autorizados:
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
                resumo_diario.append({"dia": f"Dia {dia}", "total": tot_dia, "atual": pend_dia, "diferenca": dif_dia, "pct": format(pct, ".2f").replace('.', ',')})
        
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
        total_global_fixo = total_global_atual = total_global_diferenca = 0

        for dia in sorted(list(todos_os_dias), key=lambda x: int(x)):
            meta_dia = metas_dias.get(dia, total_atual_dias.get(dia, 0)) if len(metas_dias) > 0 else total_atual_dias.get(dia, 0)
            atual_dia = pendentes_atual_dias.get(dia, 0)
            diferenca_dia = meta_dia - atual_dia
            
            total_global_fixo += meta_dia
            total_global_atual += atual_dia
            total_global_diferenca += diferenca_dia
            
            pct = round(((meta_dia - atual_dia) / meta_dia * 100), 2) if meta_dia > 0 else 0
            if pct < 0: pct = 0
            
            if meta_dia > 0 or atual_dia > 0:
                resumo_diario.append({"dia": f"Dia {dia}", "total": meta_dia, "atual": atual_dia, "diferenca": diferenca_dia, "pct": format(pct, ".2f").replace('.', ',')})

        total = total_global_fixo 
        base_atualizada = total_global_atual
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

        query_vencem_hoje = "SELECT i.imovel, i.administradora, IFNULL(i.status_interno, 'PENDENTE') as status_interno, IFNULL(v.responsavel, 'Não Atribuído') as responsavel FROM imoveis i LEFT JOIN vinculos_adm v ON TRIM(i.administradora) = TRIM(v.administradora) WHERE i.ativo = 1 AND (i.status_interno IS NULL OR i.status_interno NOT IN ('PAGO', 'COLETADO')) AND i.vencimento LIKE ? ORDER BY i.imovel ASC"
        lista_vencem_hoje = [dict(row) for row in conexao.execute(query_vencem_hoje, (f"{hoje_str_busca}%",)).fetchall()]
        vencendo_hoje = len(lista_vencem_hoje)

        query_donos = """
            SELECT IFNULL(TRIM(v.responsavel), 'Não Atribuído') as nome, COUNT(i.id) as qtd
            FROM imoveis i
            LEFT JOIN vinculos_adm v ON TRIM(UPPER(i.administradora)) = TRIM(UPPER(v.administradora))
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

        lista_pendentes = [{"nome": row['nome'] if row['nome'] else 'Carteira Não Atribuída', "qtd": row['qtd']} for row in conexao.execute("SELECT IFNULL(v.responsavel, 'Não Atribuído') as nome, COUNT(*) as qtd FROM imoveis i LEFT JOIN vinculos_adm v ON TRIM(i.administradora) = TRIM(v.administradora) WHERE i.ativo = 1 AND i.administradora IS NOT NULL AND i.administradora != '' AND (i.status_interno IS NULL OR i.status_interno NOT IN ('PAGO', 'COLETADO', 'PAGO NA 275', 'PAGO NA ADM')) GROUP BY v.responsavel ORDER BY qtd DESC").fetchall()]
        base_congelada = len(metas_db) > 0

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

        time_op = ['Ellen', 'Jessica', 'Victor', 'Ryan', 'Arthur', 'Joao', 'Geovanna', 'Heloisa', 'Robôs']
        dados_agrup = {}
        usu_extras = set()
        
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
            linha_dia = {'dia': dia_str, 'colaboradores': [], 'total_dia': 0, 'is_futuro': is_futuro}
            
            for nome in time_op:
                qtd = dados_agrup.get(dia_str, {}).get(nome, 0)
                status_dia = 'NORMAL'
                
                # REGRAS FIXAS DE HOME OFFICE
                if nome == 'Ellen' and dia_semana in [1, 3]: status_dia = 'HOME'
                elif nome == 'Jessica' and dia_semana in [0, 2]: status_dia = 'HOME'
                elif nome == 'Arthur' and dia_semana in [0, 3]: status_dia = 'HOME'
                elif nome == 'Ryan' and dia_semana in [1, 3, 4]: status_dia = 'HOME'
                
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

    meta_mes_ref = virada_mes.meta_mes_ref_atual(conexao)
    conexao.close()
    return render_template('dashboard.html', meta_mes_ref=meta_mes_ref, 
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
                   SUM(CASE WHEN i.status_interno IS NULL OR i.status_interno NOT IN ('PAGO', 'COLETADO') THEN 1 ELSE 0 END) as base_atualizada
            FROM imoveis i
            LEFT JOIN vinculos_adm v ON TRIM(UPPER(i.administradora)) = TRIM(UPPER(v.administradora))
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
        atual = row['base_atualizada']
        dif = tot - atual 
        
        linhas.append({'responsavel': resp, 'total_inicial': tot, 'base_atualizada': atual, 'diferenca': dif})

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

@app.route('/imoveis')
@login_obrigatorio
def lista_imoveis():
    termo = request.args.get('pesquisa', '').strip()
    portal = request.args.get('portal', '').strip().upper()
    conexao = conectar_banco()

    portal_sql = "COALESCE(NULLIF(TRIM(c.portal), ''), 'DIVERSOS')"
    filtros = []
    parametros = []

    if termo:
        # 🎯 Agora ele pesquisa também pelo endereço!
        filtros.append("(c.imovel LIKE ? OR c.empreendimento LIKE ? OR c.administradora LIKE ? OR i.endereco LIKE ?)")
        parametros.extend((f'%{termo}%', f'%{termo}%', f'%{termo}%', f'%{termo}%'))

    if portal and portal not in ['ALL', '']:
        filtros.append(f"UPPER({portal_sql}) = ?")
        parametros.append(portal)

    if filtros:
        where_clause = 'WHERE ' + ' AND '.join(filtros)
    else:
        where_clause = ''

    # 1. Conta o total de registros reais para exibir no Totalizador
    query_count = f"""
        SELECT COUNT(DISTINCT c.imovel) 
        FROM cadastro_mestre_imoveis c
        LEFT JOIN imoveis i ON c.imovel = i.imovel AND i.ativo = 1
        {where_clause}
    """
    total_registros = conexao.execute(query_count, parametros).fetchone()[0]

    # 2. 🎯 MÁGICA: Se houver pesquisa, tira o limite! Traz os 10 mil se precisar. 
    # Se não houver pesquisa, traz só 100 pra não travar a tela inicial à toa.
    limite_sql = "" if (termo or (portal and portal not in ['ALL', ''])) else "LIMIT 100"

    # 3. Faz o cruzamento para pegar o Endereço (i.endereco)
    query = f"""
        SELECT c.*, 
        {portal_sql} AS portal,
        IFNULL((SELECT v.responsavel FROM vinculos_adm v 
                WHERE TRIM(UPPER(v.administradora)) = TRIM(UPPER(c.administradora)) 
                LIMIT 1), c.responsavel_atual) AS responsavel_atual,
        i.endereco
        FROM cadastro_mestre_imoveis c
        LEFT JOIN imoveis i ON c.imovel = i.imovel AND i.ativo = 1
        {where_clause}
        GROUP BY c.imovel
        ORDER BY c.imovel ASC
        {limite_sql}
    """

    registros = conexao.execute(query, parametros).fetchall()
    registros = [dict(r) for r in registros]
    
    vinculos = conexao.execute('SELECT administradora, responsavel FROM vinculos_adm').fetchall()
    mapa_responsavel = {normalizar_texto(v['administradora']): v['responsavel'] for v in vinculos if v['administradora']}
    
    for registro in registros:
        if not registro.get('responsavel_atual') or registro.get('responsavel_atual') == 'Não Atribuído':
            registro['responsavel_atual'] = mapa_responsavel.get(normalizar_texto(registro.get('administradora'))) or registro.get('responsavel_atual') or 'Não Atribuído'

    conexao.close()
    return render_template('imoveis.html', registros=registros, termo=termo, portal=portal, total_registros=total_registros)

@app.route('/imovel/<imovel>')
@login_obrigatorio
def visualizar_imovel(imovel):
    conexao = conectar_banco()
    query_mestre = """
        SELECT c.*, COALESCE(NULLIF(TRIM(c.portal), ''), 'DIVERSOS') AS portal, IFNULL(v.responsavel, 'Não Atribuído') AS responsavel_atual
        FROM cadastro_mestre_imoveis c
        LEFT JOIN vinculos_adm v ON TRIM(UPPER(c.administradora)) = TRIM(UPPER(v.administradora))
        WHERE c.imovel = ?
    """
    imovel_mestre = conexao.execute(query_mestre, (imovel,)).fetchone()

    if not imovel_mestre:
        conexao.close()
        return render_template('imoveis.html', registros=[], termo='', imovel=None)

    imovel_planilha = conexao.execute("SELECT * FROM imoveis WHERE imovel = ? ORDER BY ativo DESC, id DESC LIMIT 1", (imovel,)).fetchone()
    imovel_dados = dict(imovel_mestre)
    
    # 🎯 PEGA O ID DO MÊS ATUAL (Ou 0 se não existir)
    id_real = imovel_planilha['id'] if imovel_planilha else 0
    
    # Adiciona o ID real do imóvel da tabela 'imoveis' se existir
    if imovel_planilha:
        imovel_dados['id_imovel_real'] = id_real
    else:
        imovel_dados['id_imovel_real'] = None
        
    vinculos = conexao.execute('SELECT administradora, responsavel FROM vinculos_adm').fetchall()
    mapa_responsavel = {normalizar_texto(v['administradora']): v['responsavel'] for v in vinculos if v['administradora']}
    imovel_dados['responsavel_atual'] = imovel_dados.get('responsavel_atual', None)
    if not imovel_dados['responsavel_atual'] or imovel_dados['responsavel_atual'] == 'Não Atribuído':
        imovel_dados['responsavel_atual'] = mapa_responsavel.get(normalizar_texto(imovel_dados.get('administradora')))
    imovel_dados['responsavel_atual'] = imovel_dados['responsavel_atual'] or 'Não Atribuído'
    
    # Força os dois formatos de campo para o HTML/JS nunca ficarem vazios
    imovel_dados['responsavel'] = imovel_dados['responsavel_atual']
    imovel_dados['responsavel_atual'] = imovel_dados['responsavel_atual']
    
    if imovel_planilha:
        imovel_dados['vencimento'] = imovel_planilha['vencimento'] if imovel_planilha['vencimento'] else '-'
        imovel_dados['carteira'] = imovel_planilha['carteira'] if imovel_planilha['carteira'] else '-'
        imovel_dados['d_lote'] = imovel_planilha['d_lote'] if imovel_planilha['d_lote'] else '-'
        imovel_dados['endereco'] = imovel_planilha['endereco'] if imovel_planilha['endereco'] else 'Não informado'
        imovel_dados['sacado'] = imovel_planilha['sacado'] if imovel_planilha['sacado'] else '-'
        imovel_dados['cpf_cnpj'] = imovel_planilha['cpf_cnpj'] if imovel_planilha['cpf_cnpj'] else '-'
        imovel_dados['status_interno'] = imovel_planilha['status_interno'] if imovel_planilha['status_interno'] else 'PENDENTE'
        imovel_dados['observacao_planilha'] = imovel_planilha['observacao'] if imovel_planilha['observacao'] else 'Sem anotações na ficha'
        imovel_dados['dt_comdesc'] = imovel_planilha['dt_comdesc'] if imovel_planilha['dt_comdesc'] else '-'
        imovel_dados['acesso_planilha'] = imovel_planilha['acesso'] if imovel_planilha['acesso'] else '-'
        vencimento_mes_ano = imovel_dados['vencimento']
    else:
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
        vencimento_mes_ano = None

    def mes_ano_por_vencimento(vencimento):
        if not vencimento or vencimento in ['-', '']:
            return ''

        mes_nomes = {
            '01': 'Janeiro', '02': 'Fevereiro', '03': 'Março', '04': 'Abril',
            '05': 'Maio', '06': 'Junho', '07': 'Julho', '08': 'Agosto',
            '09': 'Setembro', '10': 'Outubro', '11': 'Novembro', '12': 'Dezembro'
        }

        partes = str(vencimento).strip().split(' ')[0].replace('-', '/').split('/')
        if len(partes) >= 3:
            mes = partes[1].zfill(2)
            ano = partes[2]
            if len(ano) == 2:
                ano = '20' + ano
            return f"{mes_nomes.get(mes, mes)}/{ano}"

        return ''

    vencimento_mes_ano = mes_ano_por_vencimento(imovel_dados['vencimento'])

    # 🎯 QUERY ATUALIZADA: Agora busca o mês atual E os meses passados usando LTRIM para ignorar zeros à esquerda!
    query_historico = """
        SELECT NULL AS id_obs, h.data_hora AS data_registro, h.usuario AS coletado_por,
               CASE
                   WHEN UPPER(h.texto) LIKE '%COLETADO%' THEN 'COLETADO'
                   WHEN UPPER(h.texto) LIKE '%PAGO NA 275%' THEN 'PAGO'
                   WHEN UPPER(h.texto) LIKE '%PAGO NA ADM%' THEN 'PAGO NA ADM'
                   WHEN UPPER(h.texto) LIKE '%PAGO%' THEN 'PAGO'
                   ELSE NULL
               END AS status_liquidacao,
               NULL AS mes_ano
        FROM historico_observacoes h
        WHERE h.imovel_id = ?
          AND (UPPER(h.texto) LIKE '%COLETADO%' OR UPPER(h.texto) LIKE '%PAGO%')

        UNION ALL

        SELECT NULL AS id_obs, p.data_hora AS data_registro, p.usuario AS coletado_por,
               CASE p.acao
                   WHEN 'COLETOU BOLETO' THEN 'COLETADO'
                   WHEN 'PAGOU BOLETO' THEN 'PAGO'
                   WHEN 'PAGOU BOLETO NA 275' THEN 'PAGO'
                   WHEN 'ALTEROU STATUS PARA PAGO NA ADM' THEN 'PAGO NA ADM'
                   ELSE NULL
               END AS status_liquidacao,
               NULL AS mes_ano
        FROM produtividade p
        WHERE p.imovel_id = ?
          AND p.acao IN ('COLETOU BOLETO', 'PAGOU BOLETO', 'PAGOU BOLETO NA 275', 'ALTEROU STATUS PARA PAGO NA ADM')

        UNION ALL

        SELECT hm.id AS id_obs, hm.data_registro AS data_registro, hm.coletado_por AS coletado_por,
               CASE
                   WHEN UPPER(hm.status_liquidacao) LIKE '%COLETADO%' THEN 'COLETADO'
                   WHEN UPPER(hm.status_liquidacao) LIKE '%PAGO NA ADM%' THEN 'PAGO NA ADM'
                   WHEN UPPER(hm.status_liquidacao) LIKE '%PAGO%' THEN 'PAGO'
                   ELSE hm.status_liquidacao
               END AS status_liquidacao,
               hm.mes_ano AS mes_ano
        FROM historico_mensal_imoveis hm
        WHERE LTRIM(hm.imovel, '0') = LTRIM(?, '0')
        
        UNION ALL
        
        -- 🎯 A MÁGICA AQUI: Puxa o histórico dos meses que já foram fechados ignorando os zeros!
        SELECT NULL AS id_obs, hf.data_arquivamento AS data_registro, hf.responsavel AS coletado_por,
               hf.status_interno AS status_liquidacao, hf.mes_referencia AS mes_ano
        FROM historico_fechamento_mes hf
        WHERE LTRIM(hf.imovel, '0') = LTRIM(?, '0') 
          AND hf.status_interno IN ('PAGO', 'COLETADO', 'PAGO NA 275', 'PAGO NA ADM')

        ORDER BY data_registro DESC
    """
    
    # 🎯 Executa passando os 4 parâmetros necessários agora (id_real e imovel_txt)
    historico_total = conexao.execute(query_historico, (id_real, id_real, imovel, imovel)).fetchall()

    historico = []
    ciclos_vistos = set()
    meses_traducao = {
        '01': 'Janeiro', '02': 'Fevereiro', '03': 'Março', '04': 'Abril',
        '05': 'Maio', '06': 'Junho', '07': 'Julho', '08': 'Agosto',
        '09': 'Setembro', '10': 'Outubro', '11': 'Novembro', '12': 'Dezembro'
    }

    for h in historico_total:
        mes_ano_from_db = h['mes_ano']
        data_registro_str = h['data_registro']

        # 🎯 CORREÇÃO: Se veio nulo do banco, é o boleto do mês ATIVO. Puxa a data correta do boleto!
        mes_ano_exibicao = mes_ano_from_db or vencimento_mes_ano or ''

        # Se por algum motivo o boleto não tiver vencimento, usa a data do clique como último recurso
        if not mes_ano_exibicao and data_registro_str:
            partes_data = str(data_registro_str).split(' ')[0].split('-') 
            if len(partes_data) >= 2:
                mes_num = partes_data[1].zfill(2)
                ano = partes_data[0]
                mes_ano_exibicao = f"{meses_traducao.get(mes_num, mes_num)}/{ano}"
        
        if mes_ano_exibicao in ciclos_vistos:
            continue
        ciclos_vistos.add(mes_ano_exibicao)

        historico.append({
            'id_obs': h['id_obs'],
            'mes_ano': mes_ano_exibicao,
            'status_liquidacao': h['status_liquidacao'],
            'coletado_por': h['coletado_por'],
            'data_registro': h['data_registro']
        })

    # 🎯 Busca o histórico SEPARADO das credenciais
    try:
        hist_credenciais = conexao.execute("SELECT * FROM historico_credenciais WHERE imovel = ? ORDER BY id DESC", (imovel,)).fetchall()
        hist_credenciais = [dict(r) for r in hist_credenciais]
    except:
        hist_credenciais = []

    conexao.close()
    
    return render_template('imoveis.html', imovel=imovel_dados, historico=historico, hist_credenciais=hist_credenciais, termo='')

@app.route('/api/imovel/salvar_credenciais', methods=['POST'])
@login_obrigatorio
def api_salvar_credenciais():
    dados = request.get_json() or {}
    imovel_num = dados.get('imovel')
    novo_login = dados.get('login', '').strip()
    nova_senha = dados.get('senha', '').strip()
    novo_portal = dados.get('portal', 'DIVERSOS')
    novo_link = dados.get('link', '').strip() # 🎯 NOVO CAMPO CAPTURADO
    
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

        antigo = conexao.execute('SELECT portal, login_acesso, senha_acesso, link_portal FROM cadastro_mestre_imoveis WHERE imovel = ?', (imovel_num,)).fetchone()
        
        detalhes_mudanca = []
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
        LEFT JOIN vinculos_adm v ON TRIM(UPPER(c.administradora)) = TRIM(UPPER(v.administradora))
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

@app.route('/api/relatorio_matriz', methods=['POST'])
@login_obrigatorio
def api_relatorio_matriz():
    try:
        dados = request.get_json() or {}
        responsaveis_filtro = dados.get('responsaveis', [])
        
        conexao = conectar_banco()
        
        # Puxa os responsáveis para desenhar os filtros
        resp_query = "SELECT DISTINCT IFNULL(TRIM(responsavel), 'Não Atribuído') as resp FROM vinculos_adm WHERE responsavel IS NOT NULL AND TRIM(responsavel) != ''"
        lista_resp = [row['resp'] for row in conexao.execute(resp_query).fetchall()]
        
        # MÁGICA AQUI: Filtro que bloqueia os pagos e coletados
        query = """
            SELECT 
                IFNULL(i.media_emissao, '0') as emissao, 
                IFNULL(SUBSTR(TRIM(i.vencimento), 1, 2), '0') as vencimento, 
                IFNULL(SUBSTR(TRIM(i.vencimento), 4, 2), '0') as mes_vencimento,
                IFNULL(TRIM(v.responsavel), 'Não Atribuído') as responsavel,
                COUNT(*) as qtd
            FROM imoveis i
            LEFT JOIN vinculos_adm v ON TRIM(UPPER(i.administradora)) = TRIM(UPPER(v.administradora))
            WHERE i.ativo = 1
              AND i.administradora IS NOT NULL 
              AND TRIM(i.administradora) != ''
              AND (
                  i.status_interno IS NULL 
                  OR TRIM(i.status_interno) = '' 
                  OR UPPER(TRIM(i.status_interno)) NOT IN ('PAGO', 'COLETADO', 'PAGO NA 275', 'PAGO NA ADM')
              )
        """
        params = []
        
        if responsaveis_filtro:
            placeholders = ', '.join(['?'] * len(responsaveis_filtro))
            query += f" AND IFNULL(TRIM(v.responsavel), 'Não Atribuído') IN ({placeholders})"
            params.extend(responsaveis_filtro)
            
        query += " GROUP BY emissao, vencimento, mes_vencimento, responsavel"
        
        resultados = conexao.execute(query, params).fetchall()
        conexao.close()
        
        dados_formatados = [dict(row) for row in resultados]

        # ---------------------------------------------------------
        # 🎯 NOVA LÓGICA: DESCOBRIR OS MESES REAIS DA BASE
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
            'meses_ativos': nomes_meses_ativos # 🚀 Enviando a dupla de meses pro Frontend!
        })        
    except Exception as e:
        print(f"ERRO CRÍTICO NA MATRIZ: {str(e)}")
        return jsonify({'sucesso': False, 'msg': str(e)})

@app.route('/api/matriz/detalhes_celula', methods=['POST'])
@login_obrigatorio
def api_matriz_detalhes_celula():
    try:
        dados = request.get_json() or {}
        emi = dados.get('emissao')
        ven = dados.get('vencimento')
        responsaveis = dados.get('responsaveis', [])

        conexao = conectar_banco()
        
        # Busca os imóveis exatos que formam aquele número clicado
        query = """
            SELECT i.imovel, i.administradora, IFNULL(i.status_interno, 'EM BRANCO') as status_interno, 
                   IFNULL(TRIM(v.responsavel), 'Não Atribuído') as responsavel, i.vencimento
            FROM imoveis i
            LEFT JOIN vinculos_adm v ON TRIM(UPPER(i.administradora)) = TRIM(UPPER(v.administradora))
            WHERE i.ativo = 1
              AND i.administradora IS NOT NULL AND TRIM(i.administradora) != ''
              AND (i.status_interno IS NULL OR TRIM(i.status_interno) = '' OR UPPER(TRIM(i.status_interno)) NOT IN ('PAGO', 'COLETADO', 'PAGO NA 275', 'PAGO NA ADM'))
              AND CAST(IFNULL(i.media_emissao, '0') AS INTEGER) = ?
              AND CAST(IFNULL(SUBSTR(TRIM(i.vencimento), 1, 2), '0') AS INTEGER) = ?
        """
        params = [int(emi), int(ven)]

        # Aplica o mesmo filtro de operadores que está marcado na tela
        if responsaveis:
            placeholders = ', '.join(['?'] * len(responsaveis))
            query += f" AND IFNULL(TRIM(v.responsavel), 'Não Atribuído') IN ({placeholders})"
            params.extend(responsaveis)
            
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
    user_row = conexao.execute("SELECT is_master FROM usuarios_painel WHERE nome = ?", (usuario_atual,)).fetchone()
    eh_master = True if (user_row and user_row['is_master'] == 1) or usuario_atual in ['ryan', 'marcio', 'paulo', 'alessandra'] else False
    
    if not eh_master:
        conexao.close()
        return jsonify({'sucesso': False, 'msg': 'Acesso negado'}), 403
        
    dados = request.get_json() or {}
    novo_nome = dados.get('nome', '').strip().lower()
    
    if not novo_nome:
        conexao.close()
        return jsonify({'sucesso': False, 'msg': 'Por favor, digite um nome válido.'})
        
    try:
        conexao.execute("INSERT INTO usuarios_painel (nome, is_master) VALUES (?, 0)", (novo_nome,))
        conexao.commit()
        conexao.close()
        return jsonify({'sucesso': True})
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
    user_row = conexao.execute("SELECT is_master FROM usuarios_painel WHERE nome = ?", (usuario_atual,)).fetchone()
    eh_master = True if (user_row and user_row['is_master'] == 1) or usuario_atual in ['ryan', 'marcio', 'paulo', 'alessandra'] else False
    
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
    user_row = conexao.execute("SELECT is_master FROM usuarios_painel WHERE nome = ?", (usuario_atual,)).fetchone()
    eh_master = True if (user_row and user_row['is_master'] == 1) or usuario_atual in ['ryan', 'marcio', 'paulo', 'alessandra'] else False
    
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

@app.route('/api/sistema/curiosidades_carregamento')
@login_obrigatorio
def api_curiosidades_carregamento():
    conexao = conectar_banco()
    hoje = datetime.now().strftime('%Y-%m-%d')
    mes_atual = datetime.now().strftime('%Y-%m')
    
    frases = []
    
    # 1. Top Coletor do Mês
    top_coletor = conexao.execute("SELECT usuario, COUNT(*) as qtd FROM produtividade WHERE acao = 'COLETOU BOLETO' AND data_hora LIKE ? GROUP BY usuario ORDER BY qtd DESC LIMIT 1", (f"{mes_atual}%",)).fetchone()
    if top_coletor and top_coletor['qtd'] > 0:
        nome = str(top_coletor['usuario']).title()
        frases.append(f"🏆 Sabia que {nome} lidera o mês com {top_coletor['qtd']} boletos coletados?")

    # 2. Top Disparos de E-mail Hoje
    top_email = conexao.execute("SELECT usuario, COUNT(*) as qtd FROM produtividade WHERE acao = 'DISPAROU EMAIL COBRANCA' AND data_hora LIKE ? GROUP BY usuario ORDER BY qtd DESC LIMIT 1", (f"{hoje}%",)).fetchone()
    if top_email and top_email['qtd'] > 0:
        nome = str(top_email['usuario']).title()
        frases.append(f"🚀 {nome} está voando: já notificou {top_email['qtd']} imóveis hoje!")

    # 3. Total Liquidados pela equipe no mês
    total_pagos = conexao.execute("SELECT COUNT(*) as qtd FROM produtividade WHERE acao = 'PAGOU BOLETO' AND data_hora LIKE ?", (f"{mes_atual}%",)).fetchone()
    if total_pagos and total_pagos['qtd'] > 0:
        frases.append(f"💰 A equipe já registrou {total_pagos['qtd']} pagamentos este mês. Que ritmo!")

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
                
                caso_ativo = conexao.execute('SELECT id FROM imoveis WHERE imovel = ? AND ativo = 1 ORDER BY id DESC LIMIT 1', (num_imovel,)).fetchone()
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
                
                conexao.execute('UPDATE imoveis SET status_interno = "PAGO NA ADM" WHERE id = ? OR (imovel = ? AND ativo = 1)', (id_real, num_imovel))
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
        caso = conexao.execute('SELECT id, imovel FROM imoveis WHERE id = ?', (imovel_id,)).fetchone()
        
        if caso:
            id_real = caso['id']
            num_imovel = caso['imovel']
            conexao.execute('UPDATE imoveis SET status_interno = "PAGO NA 275" WHERE id = ? OR (imovel = ? AND ativo = 1)', (id_real, num_imovel))
            
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
        vinculos = conexao.execute("SELECT UPPER(TRIM(administradora)) as adm, TRIM(responsavel) as resp FROM vinculos_adm WHERE responsavel IS NOT NULL AND TRIM(responsavel) != ''").fetchall()
        mapa_resp = {v['adm']: v['resp'] for v in vinculos}
        lista_resp = list(set(mapa_resp.values()))
        
        # 2. Puxa o "Cadastro Mestre" para descobrir a Média de Emissão de cada imóvel
        mestres = conexao.execute("SELECT imovel, media_emissao FROM cadastro_mestre_imoveis WHERE media_emissao IS NOT NULL AND media_emissao != ''").fetchall()
        mapa_media = {str(m['imovel']).lstrip('0'): m['media_emissao'] for m in mestres}
        
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
            responsavel = mapa_resp.get(adm, 'Não Atribuído')
            
            chave = (str(media_emissao), dia_venc, mes_venc, responsavel)
            resultados_agrupados[chave] = resultados_agrupados.get(chave, 0) + 1
            
            if mes_venc and mes_venc != '0':
                contagem_meses[mes_venc] = contagem_meses.get(mes_venc, 0) + 1

        dados_formatados = []
        for (emi, ven, m_ven, resp), qtd in resultados_agrupados.items():
            dados_formatados.append({
                'emissao': emi,
                'vencimento': ven,
                'mes_vencimento': m_ven,
                'responsavel': resp,
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
            LEFT JOIN vinculos_adm v ON TRIM(i.administradora) = TRIM(v.administradora)
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
                            conexao.execute('UPDATE imoveis SET status_interno = "COLETADO" WHERE id = ?', (imovel_id,))
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
                            conexao.execute('UPDATE imoveis SET status_interno = "COLETADO" WHERE id = ?', (imovel_id,))
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
                    cursor.execute("SELECT id, vencimento FROM imoveis WHERE LTRIM(imovel, '0') = ? AND ativo = 1", (imovel_limpo,))
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
                        cursor.execute("UPDATE imoveis SET status_interno = 'PAGO' WHERE id = ?", (imovel_id,))
                        
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
def job_winker_diario():
    print("⏰ [AGENDADOR] Deu 18h! Disparando a automação da Winker...", flush=True)
    if not STATUS_ROBOS["WINKER"]["rodando"]:
        STATUS_ROBOS["WINKER"]["rodando"] = True
        STATUS_ROBOS["WINKER"]["sucessos"] = 0
        STATUS_ROBOS["WINKER"]["falhas"] = 0
        STATUS_ROBOS["WINKER"]["logs"] = [] 
        log_robo_console("WINKER", "🚀 Inicialização automática de motor (Agendador - 18:00).", "info")
        threading.Thread(target=executar_script_externo, args=("scripts/robos/robo_winker.py", "", "WINKER"), daemon=True).start()
    else:
        print("⚠️ [AGENDADOR] O Winker já está rodando. Ignorando disparo duplo.", flush=True)

def job_finmail_continuo():
    global FINMAIL_PAUSA_ATE
    
    # 🎯 TRAVA DE PAUSA MANUAL (Verifica se está pausado antes de fazer qualquer coisa)
    if FINMAIL_PAUSA_ATE and datetime.now() < FINMAIL_PAUSA_ATE:
        print(f"⏸️ [AGENDADOR] FinMail pausado pelo usuário até {FINMAIL_PAUSA_ATE.strftime('%d/%m/%Y %H:%M')}. Ignorando ciclo.", flush=True)
        return
        
    print("🎧 [AGENDADOR] Checando novos e-mails (Ciclo de 5 min)...", flush=True)
    if not STATUS_ROBOS["FINMAIL"]["rodando"]:
        STATUS_ROBOS["FINMAIL"]["rodando"] = True
        STATUS_ROBOS["FINMAIL"]["sucessos"] = 0
        STATUS_ROBOS["FINMAIL"]["falhas"] = 0
        STATUS_ROBOS["FINMAIL"]["erro_senha"] = 0
        STATUS_ROBOS["FINMAIL"]["comunicados"] = 0
        STATUS_ROBOS["FINMAIL"]["atas"] = 0
        STATUS_ROBOS["FINMAIL"]["logs"] = []
        log_robo_console("FINMAIL", "🚀 Escuta contínua acionada (Agendador - 5 min).", "info")
        threading.Thread(target=executar_script_externo, args=("scripts/robos/robo_email_geral.py", "", "FINMAIL"), daemon=True).start()
    else:
        print("⚠️ [AGENDADOR] O FinMail ainda está processando a leva anterior. Ignorando ciclo.", flush=True)

def iniciar_agendador_interno():
    scheduler = BackgroundScheduler(daemon=True)
    
    # Tarefa Winker (Às 18:00)
    scheduler.add_job(
        func=job_winker_diario,
        trigger='cron',
        hour=18,
        minute=0,
        id='job_winker_18h',
        replace_existing=True
    )
    
    # Tarefa FinMail (A cada 5 minutos)
    scheduler.add_job(
        func=job_finmail_continuo,
        trigger='interval',
        minutes=5,
        id='job_finmail_5min',
        replace_existing=True
    )
    
    scheduler.start()
    print("⏰ [SISTEMA] Motor APScheduler acoplado e rodando com sucesso!", flush=True)
    atexit.register(lambda: scheduler.shutdown())


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
                SELECT i.id, v.responsavel 
                FROM imoveis i
                LEFT JOIN vinculos_adm v ON TRIM(i.administradora) = TRIM(v.administradora)
                WHERE LTRIM(i.imovel, '0') = LTRIM(?, '0') 
                  AND i.ativo = 1 
                  AND (i.status_interno IS NULL OR i.status_interno NOT IN ('PAGO', 'PAGO NA ADM', 'PAGO NA 275', 'COLETADO'))
            """, (imovel_str,)).fetchone()
            
            if caso:
                imovel_id = caso['id']
                
                # 🎯 Descobre o nome da responsável e dá uma limpada (deixa bonito igual ao painel)
                operador_dono = caso['responsavel'] if caso['responsavel'] else 'Não Atribuído'
                operador_limpo = operador_dono.strip().title()
                if operador_limpo == 'Jéssica': 
                    operador_limpo = 'Jessica' # Nossa velha correção preventiva de acentos
                
                # Atualiza o status para COLETADO
                conexao.execute("UPDATE imoveis SET status_interno = 'COLETADO' WHERE id = ?", (imovel_id,))
                
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

    conexao.close()
    return render_template('internet.html', pendentes=pendentes, erros_pagamento=erros_pagamento, aprovados_periodo=aprovados_periodo, recusados_periodo=recusados_periodo, historico_periodo=historico_periodo, periodo_nome=periodo_nome, data_inicio=data_inicio, data_fim=data_fim, filtro_mes=filtro_mes, data_hoje=hoje_str, usuario_atual=usuario_atual, data_min_pagamento=menor_data_pagamento_permitida().isoformat(), pode_editar=usuario_pode_editar_internet())


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
        LEFT JOIN vinculos_adm v ON TRIM(UPPER(i.administradora)) = TRIM(UPPER(v.administradora))
        WHERE LTRIM(i.imovel, '0') = ? AND i.ativo = 1 LIMIT 1
    '''
    caso = conexao.execute(query_mes, (num_imovel,)).fetchone()
    
    # 2. Se não achar no mês, busca no Cadastro Mestre Geral
    if not caso:
        query_mestre = '''
            SELECT c.empreendimento, IFNULL(v.responsavel, 'Não Atribuído') as responsavel,
                   (SELECT carteira FROM imoveis WHERE LTRIM(imovel, '0') = ? LIMIT 1) as carteira
            FROM cadastro_mestre_imoveis c
            LEFT JOIN vinculos_adm v ON TRIM(UPPER(c.administradora)) = TRIM(UPPER(v.administradora))
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

            # 🎯 Sufixo opcional no fim do nome do PDF (INCO, N FIN ou RELACIONAMENTO)
            campo_motivo = 'motivo_cheque' if tipo_operacao == 'cheque' else 'motivo_int'
            motivo_sel = request.form.get(campo_motivo, '').strip().upper()
            if motivo_sel in ('INCO', 'N FIN', 'RELACIONAMENTO'):
                nome_final = nome_final[:-4] + f"-{motivo_sel}.pdf"

            arquivo_bytes = arquivo.read()

            conexao = conectar_banco()
            # Insere na fila de pendências salvando a data de pagamento para ordenação
            conexao.execute('''
                INSERT INTO pendencias_internet 
                (imovel, carteira, mes_ref, conta, nome_arquivo, arquivo_bytes, observacao_solicitante, usuario_solicitante, data_solicitacao, data_pagamento, tipo_doc, valor) 
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (imovel, carteira, mes_ref, conta, nome_final, arquivo_bytes, observacao, usuario_atual, data_hora, data_pagamento_raw, tipo_doc, valor))
            
            conexao.commit()
            conexao.close()
            
            # Dispara webhook no Google Chat
            enviar_notificacao_google_chat(imovel, conta, mes_ref, usuario_atual, observacao, data_pagamento_br, tipo_doc, valor)
            
            return jsonify({'sucesso': True, 'msg': f'{tipo_doc.title()} enviado para a Fila de Aprovação da Gestão em ordem de pagamento!'})

        # ==========================================
        # FLUXO 2: SLIP MANUAL
        # ==========================================
        elif tipo_operacao == 'slip':
            valor = request.form.get('valor_multa')
            ref_deb = "LELLO" 
            conta_deb = request.form.get('conta_debito')
            ref_cred = request.form.get('ref_credito')
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
        LEFT JOIN vinculos_adm v ON TRIM(UPPER(c.administradora)) = TRIM(UPPER(v.administradora))
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
def inicializar_app():
    print("🚀 Inicializando base de dados em alta performance...", flush=True)
    inicializar_banco_producao()
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
    
    # Como você usa o run.py e debug=True, isso previne que o relógio inicie 2 vezes na memória
    if os.environ.get('WERKZEUG_RUN_MAIN') == 'true' or not app.debug:
        iniciar_agendador_interno()


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

    # 1) Imóveis ativos e ainda não encerrados, agrupados pelo número sem zeros à esquerda
    grupos = {}
    for r in conexao.execute(
        """SELECT id, imovel, administradora, empreendimento, endereco, vencimento, status_interno
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
                  winker_unidade, webware_empreendimento, webware_unidade
           FROM cadastro_mestre_imoveis"""
    ).fetchall():
        chave = _chave_imovel_raio_x(r['imovel'])
        if chave in grupos:
            cadastro.setdefault(chave, []).append(r)

    # 3) Responsáveis por administradora
    vinculos = {}
    for r in conexao.execute("SELECT administradora, responsavel FROM vinculos_adm").fetchall():
        vinculos.setdefault(_chave_adm_raio_x(r['administradora']), []).append(r['responsavel'])

    # 4) Último registro de auditoria de cada imóvel dentro do ciclo (junção só pela chave primária: rápido)
    auditoria = {}
    for r in conexao.execute(
        """SELECT a1.id, a1.robo, a1.imovel_id, a1.status, a1.detalhe, a1.data_hora
           FROM auditoria_robos a1
           INNER JOIN (
               SELECT MAX(id) AS max_id FROM auditoria_robos WHERE data_hora >= ? GROUP BY LTRIM(imovel_id, '0')
           ) a2 ON a1.id = a2.max_id""", (data_corte,)
    ).fetchall():
        chave = _chave_imovel_raio_x(r['imovel_id'])
        if chave in grupos:
            auditoria[chave] = r

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


RAIO_X_VERSAO = '2026-10-16.3'   # a tela compara com esta versão e avisa se o servidor ainda está rodando código antigo

# Status que o robô grava quando DÁ CERTO (o Winker audita 'BAIXADO'): não são erro e não entram na lista do Raio-X
STATUS_SUCESSO_ROBO = ('BAIXADO', 'SUCESSO', 'COLETADO', 'OK', 'DISPENSADO', 'IGNORADO', 'RESOLVIDO')

RAIO_X_CATEGORIAS = {
    'AJUSTADO':      ('ajustados',     'Ajustado (Pronto para o robô tentar)'),
    'FALTAM_DADOS':  ('faltam_dados',  'Faltam dados do site no cadastro'),
    'UNIDADE_BLOCO': ('unidade_bloco', 'Unidade ou Bloco não encontrado'),
    'CONDOMINIO':    ('condominio',    'Condomínio não localizado no site'),
    'LOGIN':         ('login',         'Falha de Login / Senha'),
    'OUTROS':        ('outros',        None),
}

_RX_SUFIXO_CADASTRO = re.compile(r"\s*\(\s*Empreendimento\s*:.*\)\s*$", re.IGNORECASE | re.DOTALL)
_RX_URL = re.compile(r"https?://\S+", re.IGNORECASE)

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
        # Pega a data/hora do último upload da base para isolar apenas o ciclo vigente
        upload_row = conexao.execute(
            "SELECT data_hora FROM produtividade WHERE acao LIKE 'UPLOAD BASE|%' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        data_corte = upload_row['data_hora'] if upload_row and upload_row['data_hora'] else '2000-01-01 00:00:00'

        # ⚡ Carga rápida (uma linha por imóvel, sem JOINs com função): ver carregar_base_raio_x()
        linhas = carregar_base_raio_x(conexao, data_corte)
        
        resp_unicos = sorted(list(set(r['responsavel'].title() for r in linhas if r['responsavel'] and r['responsavel'] != 'Não Atribuído')))
    finally:
        conexao.close()

    registros = []
    totais = {
        'total': 0,
        'unidade_bloco': 0,
        'condominio': 0,
        'login': 0,
        'faltam_dados': 0,
        'ajustados': 0,
        'outros': 0
    }

    termos_sem_boleto = [
        'SEM BOLETO', 'SEM BOLETOS', 'SEM BOLETO DO MÊS', 'SEM BOLETO DO MES',
        'NENHUM BOLETO', 'NÃO DISPONÍVEL', 'NAO DISPONIVEL', 'PAGO'
    ]

    imoveis_processados = set()

    # Filtros preparados uma vez só (comparação sem acento e sem diferença de maiúscula/minúscula)
    resp_alvo = '' if filtro_resp.lower() in ('', 'todos') else normalizar_texto(filtro_resp)
    portal_alvo = '' if filtro_portal in ('', 'TODOS') else normalizar_texto(filtro_portal)
    termo_norm = normalizar_texto(termo)
    erro_alvo = '' if filtro_erro in ('', 'TODOS') else filtro_erro
    mostrar_ajustados = ver_ajustados or erro_alvo == 'AJUSTADO'

    for item in linhas:
        chave_imovel = str(item['imovel']).strip().lstrip('0')
        if chave_imovel in imoveis_processados:
            continue
        imoveis_processados.add(chave_imovel)

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

        item['emp_site'] = emp_site
        item['bloco_site'] = bloco_site
        item['unid_site'] = unid_site

        st = (item.get('ultimo_status_erro') or '').strip().upper()
        det = (item.get('ultimo_detalhe_erro') or '').strip().upper()

        # Ignora sem boleto/não emitido
        if any(t in st for t in termos_sem_boleto) or 'NENHUM BOLETO' in det or 'NÃO HÁ BOLETOS' in det or 'TODOS OS' in det:
            continue

        dados_faltando = (portal in ('WINKER', 'WEBWARE') and (not emp_site or not unid_site))
        teve_erro_recente = bool(item.get('ultimo_status_erro'))
        esta_ajustado = (st == 'AJUSTADO')

        # Sucesso gravado pelo robô (ex.: BAIXADO em teste "sem marcar") não é erro: não entra no Raio-X
        if normalizar_texto(item.get('ultimo_status_erro')) in STATUS_SUCESSO_ROBO and not dados_faltando:
            continue

        if not teve_erro_recente and not dados_faltando:
            continue

        categoria = classificar_erro_raio_x(item.get('ultimo_status_erro'), item.get('ultimo_detalhe_erro'), dados_faltando)
        motivo_curto = RAIO_X_CATEGORIAS[categoria][1] or item.get('ultimo_status_erro') or 'Falha Técnica de Coleta'

        item['categoria_erro'] = categoria
        item['motivo_resumido'] = motivo_curto
        item['esta_ajustado'] = (categoria == 'AJUSTADO')

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
        'responsaveis': resp_unicos,
        'registros': registros
    })


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
                    FROM imoveis i LEFT JOIN vinculos_adm v ON TRIM(UPPER(i.administradora)) = TRIM(UPPER(v.administradora))
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
    dados = request.get_json() or {}
    imovel = str(dados.get('imovel', '')).strip()
    portal = dados.get('portal', '').strip().upper()
    emp_site = dados.get('emp_site', '').strip()
    bloco_site = dados.get('bloco_site', '').strip()
    unid_site = dados.get('unid_site', '').strip()
    login_acesso = dados.get('login_acesso', '').strip()
    senha_acesso = dados.get('senha_acesso', '').strip()
    usuario = session.get('usuario', 'Sistema')
    agora = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    if not imovel:
        return jsonify({'sucesso': False, 'msg': 'Número do imóvel ausente.'}), 400

    conexao = conectar_banco()
    try:
        conexao.execute("INSERT OR IGNORE INTO cadastro_mestre_imoveis (imovel) VALUES (?)", (imovel,))

        # 1. Atualiza o cadastro mestre com os dados corretos do site
        if 'WINKER' in portal:
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
        elif 'WEBWARE' in portal:
            conexao.execute("""
                UPDATE cadastro_mestre_imoveis 
                SET portal = 'WEBWARE',
                    webware_empreendimento = ?,
                    webware_unidade = ?,
                    login_acesso = COALESCE(NULLIF(?, ''), login_acesso),
                    senha_acesso = COALESCE(NULLIF(?, ''), senha_acesso)
                WHERE LTRIM(imovel, '0') = LTRIM(?, '0')
            """, (emp_site, unid_site, login_acesso, senha_acesso, imovel))
        else:
            conexao.execute("""
                UPDATE cadastro_mestre_imoveis 
                SET portal = ?,
                    login_acesso = ?,
                    senha_acesso = ?
                WHERE LTRIM(imovel, '0') = LTRIM(?, '0')
            """, (portal, login_acesso, senha_acesso, imovel))

        # 2. Registra o evento AJUSTADO na auditoria (isso tira o imóvel da lista de erros ativos)
        resumo_ajuste = f"Dados ajustados por {usuario} ({portal}). Condomínio: '{emp_site}' | Bloco: '{bloco_site}' | Unidade: '{unid_site}'"
        conexao.execute("""
            INSERT INTO auditoria_robos (robo, imovel_id, status, detalhe, data_hora)
            VALUES (?, ?, 'AJUSTADO', ?, ?)
        """, (portal, str(imovel), resumo_ajuste, agora))

        # 3. Registra no prontuário do imóvel para visualização da equipe
        caso = conexao.execute("SELECT id FROM imoveis WHERE LTRIM(imovel, '0') = LTRIM(?, '0') AND ativo = 1 LIMIT 1", (imovel,)).fetchone()
        if caso:
            conexao.execute("INSERT INTO historico_observacoes (imovel_id, data_hora, usuario, texto) VALUES (?, ?, ?, ?)",
                            (caso['id'], agora, usuario, f"✏️ {resumo_ajuste}"))

        conexao.commit()
        return jsonify({'sucesso': True, 'msg': 'Cadastro atualizado com sucesso! O caso foi marcado como ajustado.'})
    except Exception as e:
        return jsonify({'sucesso': False, 'msg': str(e)}), 500
    finally:
        conexao.close()

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
        for imv in imoveis:
            imv_str = str(imv).strip()
            if not imv_str:
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
        return jsonify({'sucesso': True, 'qtd': qtd, 'msg': f'{qtd} erro(s) removido(s) com sucesso.'})
    except Exception as e:
        conexao.rollback()
        return jsonify({'sucesso': False, 'msg': str(e)}), 500
    finally:
        conexao.close()

# É vital chamar a função aqui na raiz para o run.py enxergar ao subir o servidor
inicializar_app()

if __name__ == '__main__':
    print("🚀 Servidor em MODO DESENVOLVIMENTO (Atualização na hora)...", flush=True)
    app.run(host='0.0.0.0', port=5000, debug=True, use_reloader=False)