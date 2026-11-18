"""
ROBÔ SUPERLÓGICA — login via Google (ctrlcond@lello.com.br) sem pedir código toda vez.

COMO O LOGIN FUNCIONA (3 camadas, da mais comum para a mais rara)
-----------------------------------------------------------------
1) Sessão do portal ainda vale  -> entra direto, ninguém vê o Google.
2) Sessão do portal caiu, mas a do Google (no perfil salvo) vale
   -> clica "Entrar com Google", escolhe a conta e volta logado. Sem senha, sem código.
3) Sessão do Google também expirou
   -> o robô digita a senha e GERA SOZINHO o código de 6 dígitos do app autenticador
      (mesmo código que o Google Authenticator do celular mostraria).

CONFIGURAÇÃO (.env)
-------------------
    TOKEN_SEGURANCA=...                 (o mesmo do robo_email_geral.py)
    GOOGLE_EMAIL=ctrlcond@lello.com.br
    GOOGLE_SENHA=...                    (senha da conta Google)
    GOOGLE_TOTP_SECRET=...              (chave do app autenticador, ver --codigo)

COMANDOS
--------
    python robo_superlogica.py                   execução normal (o painel chama assim)
    python robo_superlogica.py --login [url]     login manual num Chrome normal; feche o Chrome ao terminar
    python robo_superlogica.py --testar-login    testa só o login em todos os portais (não baixa nada)
    python robo_superlogica.py --codigo          mostra o código de 6 dígitos atual (para cadastrar o autenticador)

OBSERVAÇÕES
-----------
- Um processo por vez usando a pasta do perfil (feche o Chrome do --login antes de rodar o robô).
- Requer Google Chrome instalado (channel="chrome").
- O relógio do Windows precisa estar certo (sincronizado): o código do autenticador depende da hora.
"""
import os
import re
import sys
import hmac
import time
import base64
import struct
import hashlib
import sqlite3
import difflib
import tempfile
import subprocess
import unicodedata
from datetime import datetime
from urllib.parse import urlparse

import pandas as pd
import requests
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

load_dotenv()

# =======================================================================
# CONFIGURAÇÕES
# =======================================================================
CAMINHO_BANCO = r"C:\Users\ryan.lima\Documents\SistemaImoveis\banco.db"
PASTA_PERFIL = r"C:\Users\ryan.lima\Documents\SistemaImoveis\perfil_superlogica"
PASTA_PRINTS = r"C:\Users\ryan.lima\Documents\SistemaImoveis\prints_superlogica"
PASTA_DESTINO = r"\\192.168.100.13\Lello2\ARQUIVOS-DIGITALIZACAO\financeiro_imoveis\Condomínio\boleto__Cond_antes liax"

URL_API_PENDENCIAS = "http://127.0.0.1:5000/api/automacao/pendencias"
URL_API_COLETA = "http://127.0.0.1:5000/api/automacao/coletar"
TOKEN_SEGURANCA = os.getenv("TOKEN_SEGURANCA")
NOME_ROBO = "AUTOMAÇÃO SUPERLÓGICA"

GOOGLE_EMAIL = (os.getenv("GOOGLE_EMAIL") or "ctrlcond@lello.com.br").strip()
GOOGLE_SENHA = os.getenv("GOOGLE_SENHA") or ""
if GOOGLE_SENHA.strip().upper().startswith("COLOQUE"):   # texto de exemplo do .env não é senha
    GOOGLE_SENHA = ""
GOOGLE_TOTP_SECRET = os.getenv("GOOGLE_TOTP_SECRET") or ""

# Se o Google bloquear a conta ("Não foi possível fazer o login"), cada nova tentativa piora o bloqueio.
# O robô então grava este arquivo e fica sem tentar o login automático por HORAS_PAUSA_BLOQUEIO horas.
ARQUIVO_BLOQUEIO = os.path.join(os.path.dirname(PASTA_PERFIL), "bloqueio_google.flag")
HORAS_PAUSA_BLOQUEIO = 6

LIMIAR_SIMILARIDADE = 0.75

# Seletores da tela do Superlógica
SEL_COMBO_CONDOMINIOS = ".combo-condominios"
SEL_LISTA_CONDOMINIOS = "#lista li a"
SEL_BUSCA_BOLETOS = 'input[placeholder="Busque por boletos"]'

# Botão "Entrar com Google" na tela de login do portal — testados em ordem.
# >>> Se o --testar-login disser que não achou o botão, ajuste aqui <<<
SELETORES_BOTAO_GOOGLE = [
    'button.btn-login-google',      # conferido na tela real do Superlógica ("Login com Google")
    'button:has-text("Google")',
    'a:has-text("Google")',
    '[class*="google" i]',
    '[id*="google" i]',
    'img[src*="google" i]',
]

# Campos das telas do Google (conferido em 28/10/2026: o e-mail é type="text" id="identifierId")
# Na tela de senha sobra um <input type="email" id="hiddenEmail" aria-hidden="true">: não pode ser usado.
SEL_GOOGLE_EMAIL = '#identifierId:visible, input[name="identifier"]:not([aria-hidden="true"]):visible'
SEL_GOOGLE_SENHA = ('input[name="Passwd"]:visible, '
                    'input[type="password"]:not([name="hiddenPassword"]):not([aria-hidden="true"]):visible')
SEL_GOOGLE_CODIGO = ('input[name="totpPin"]:visible, #totpPin:visible, '
                     'input[autocomplete="one-time-code"]:visible, input[type="tel"]:visible')

CAMINHOS_CHROME = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
]

# =======================================================================
# UTILITÁRIOS
# =======================================================================
def sem_acento(texto):
    return unicodedata.normalize("NFKD", str(texto or "")).encode("ascii", "ignore").decode("ascii")


def limpar_nome_condominio(nome):
    if not nome:
        return ""
    nome_limpo = sem_acento(nome)
    nome_limpo = re.sub(r'(?i)[-–—\s]*\bcnpj\b.*$', '', nome_limpo)          # "... --CNPJ 54.209.168/0001-30"
    nome_limpo = re.sub(
        r'(?i)\b(condominio|cond|edificio|edif|residencial|res)\b\.?', '', nome_limpo)
    nome_limpo = re.sub(r'[^\w\s]', ' ', nome_limpo)                          # pontuação vira espaço
    return " ".join(nome_limpo.split())


def calcular_similaridade(texto_banco, texto_site):
    banco_limpo = limpar_nome_condominio(texto_banco)
    site_limpo = limpar_nome_condominio(texto_site)
    if not banco_limpo or not site_limpo:
        return 0
    return difflib.SequenceMatcher(None, banco_limpo.lower(), site_limpo.lower()).ratio()


def extrair_unidade_bloco(endereco_completo):
    if not endereco_completo:
        return "", ""
    unidade, bloco = "", ""
    match_ap = re.search(r'AP(?:TO|\.)?\s*(\d+)', endereco_completo, re.IGNORECASE)
    if match_ap:
        unidade = match_ap.group(1)
    match_bl = re.search(r'BL(?:OCO|\.)?\s*([A-Z0-9]+)', endereco_completo, re.IGNORECASE)
    if match_bl:
        bloco = match_bl.group(1)
    return unidade, bloco


def norm_imovel(valor):
    return str(valor or "").strip().lstrip("0")


def limpar_url(link):
    """'https://x.superlogica.net/clients/areadocondomino - LOGIN: EMAIL DO CTRL' -> só a URL."""
    achou = re.search(r"https?://[^\s]+", str(link or ""))
    url = achou.group(0).rstrip(".,;") if achou else ""
    if re.search(r"[?&](email|salvar)=", url):          # parâmetros do formulário de login, não fazem parte do endereço
        url = url.split("?", 1)[0]
    return url


def _bloqueio_ativo():
    """Texto explicando a pausa, se o login automático estiver em pausa; senão ''."""
    try:
        gravado = os.path.getmtime(ARQUIVO_BLOQUEIO)
    except OSError:
        return ""
    fim = gravado + HORAS_PAUSA_BLOQUEIO * 3600
    if time.time() >= fim:
        return ""
    return (f"Login automático do Google em PAUSA até {datetime.fromtimestamp(fim):%d/%m %H:%M} (o Google bloqueou a conta). "
            f"Para liberar antes: faça o login manual (python robo_superlogica.py --login) ou apague {ARQUIVO_BLOQUEIO}")


def _marcar_bloqueio():
    try:
        with open(ARQUIVO_BLOQUEIO, "w", encoding="utf-8") as f:
            f.write(datetime.now().isoformat())
    except OSError:
        pass


def _limpar_bloqueio():
    try:
        os.remove(ARQUIVO_BLOQUEIO)
    except OSError:
        pass


_DIAGNOSTICOS = {}


def salvar_diagnostico(pagina, nome):
    """Print + HTML da tela, para ajustar seletores sem adivinhar. No máximo 2 de cada tipo por execução."""
    if _DIAGNOSTICOS.get(nome, 0) >= 2:
        return
    _DIAGNOSTICOS[nome] = _DIAGNOSTICOS.get(nome, 0) + 1
    try:
        os.makedirs(PASTA_PRINTS, exist_ok=True)
        base = os.path.join(PASTA_PRINTS, f"{datetime.now():%Y%m%d_%H%M%S}_{nome}")
        pagina.screenshot(path=base + ".png", full_page=True)
        with open(base + ".html", "w", encoding="utf-8") as f:
            f.write(pagina.content())
        print(f"   (diagnóstico salvo: {base}.png e .html)", flush=True)
    except Exception:
        pass


def salvar_print(pagina, nome):
    try:
        os.makedirs(PASTA_PRINTS, exist_ok=True)
        caminho = os.path.join(PASTA_PRINTS, f"{datetime.now():%Y%m%d_%H%M%S}_{nome}.png")
        pagina.screenshot(path=caminho, full_page=True)
        print(f"   (print da tela salvo em {caminho})", flush=True)
    except Exception:
        pass


# =======================================================================
# CÓDIGO DO APP AUTENTICADOR (TOTP)
# =======================================================================
def codigo_totp(segredo_base32, instante=None, digitos=6, periodo=30):
    """Mesmo cálculo do Google Authenticator (RFC 6238, HMAC-SHA1). Sem bibliotecas extras."""
    segredo = re.sub(r"[\s-]", "", segredo_base32).upper()
    segredo += "=" * (-len(segredo) % 8)
    chave = base64.b32decode(segredo)
    contador = int((time.time() if instante is None else instante) // periodo)
    resumo = hmac.new(chave, struct.pack(">Q", contador), hashlib.sha1).digest()
    pos = resumo[-1] & 0x0F
    numero = struct.unpack(">I", resumo[pos:pos + 4])[0] & 0x7FFFFFFF
    return str(numero % (10 ** digitos)).zfill(digitos)


def gerar_codigo_totp(ja_usados=()):
    """Código atual de 6 dígitos. Se faltar pouco para virar, ou se já foi usado, espera o próximo."""
    if not GOOGLE_TOTP_SECRET:
        return None
    for _ in range(3):
        restante = 30 - (time.time() % 30)
        codigo = codigo_totp(GOOGLE_TOTP_SECRET)
        if restante < 5 or codigo in ja_usados:
            time.sleep(restante + 1)
            continue
        return codigo
    return codigo_totp(GOOGLE_TOTP_SECRET)


def modo_mostrar_codigo():
    if not GOOGLE_TOTP_SECRET:
        print("Defina GOOGLE_TOTP_SECRET no .env primeiro.")
        return
    restante = int(30 - (time.time() % 30))
    print(f"Código atual: {codigo_totp(GOOGLE_TOTP_SECRET)}  (vale por mais {restante}s)")


# =======================================================================
# BANCO E API DO PAINEL
# =======================================================================
def buscar_imoveis():
    """Imóveis ativos do Superlógica, com o mapeamento já feito (super_condominio/bloco/unidade).

    Obs.: o JOIN é pelo NÚMERO do imóvel (c.imovel = i.imovel). Pelo id da linha
    (c.imovel = i.id) ele casava número com id por coincidência e trazia só ~3 imóveis.
    """
    try:
        conexao = sqlite3.connect(CAMINHO_BANCO)
        conexao.row_factory = sqlite3.Row
        linhas = conexao.execute("""
            SELECT DISTINCT
                i.imovel,
                c.link_portal,
                i.empreendimento,
                i.endereco,
                IFNULL(c.super_condominio, '') AS super_condominio,
                IFNULL(c.super_bloco, '')      AS super_bloco,
                IFNULL(c.super_unidade, '')    AS super_unidade,
                IFNULL(c.super_status, '')     AS super_status
            FROM cadastro_mestre_imoveis c
            JOIN imoveis i ON TRIM(c.imovel) = TRIM(i.imovel)
            WHERE i.ativo = 1
              AND (UPPER(c.portal) LIKE '%SUPERL%'
                   OR c.link_portal LIKE '%superlogica.net%')
        """).fetchall()
        conexao.close()
        return [dict(l) for l in linhas]
    except sqlite3.Error as e:
        print(f"ERRO ao acessar o banco de dados: {e}", flush=True)
        return []


def _data_ord(texto):
    try:
        return datetime.strptime(texto, "%d/%m/%Y")
    except (ValueError, TypeError):
        return datetime.max


def _datas_do_texto(texto):
    """'05/11/2026,15/11/2026' -> ['05/11/2026', '15/11/2026'] (ordenadas, sem repetir)."""
    achadas = {m for m in re.findall(r"\d{2}/\d{2}/\d{4}", str(texto or ""))}
    return sorted(achadas, key=_data_ord)


def buscar_pendencias_api():
    """Retorna {nº do imóvel: {'pendentes': [vencimentos que faltam], 'todos': [todos os vencimentos]}},
    ou None se a API estiver fora. O sistema trata cada VENCIMENTO como um boleto (dia 05 e dia 15 são dois)."""
    try:
        resp = requests.post(
            URL_API_PENDENCIAS,
            json={"token": TOKEN_SEGURANCA, "colaborador": ""},
            timeout=20,
        ).json()
        if resp.get("sucesso"):
            mapa = {}
            for c in resp.get("dados", []):
                chave = norm_imovel(c.get("imovel"))
                item = mapa.setdefault(chave, {"pendentes": [], "todos": []})
                item["pendentes"] = sorted(set(item["pendentes"]) | set(_datas_do_texto(c.get("vencimento"))), key=_data_ord)
                item["todos"] = sorted(set(item["todos"]) | set(_datas_do_texto(c.get("vencimentos_base"))), key=_data_ord)
            return mapa
    except Exception as e:
        print(f"ERRO de conexão com a API de pendências: {e}", flush=True)
    return None


def dar_baixa_api(numero_imovel, observacao="", vencimento=""):
    """Avisa o painel que o boleto foi coletado. 'vencimento' (dd/mm/aaaa) diz QUAL boleto: sem ele o painel só
    marca quando o imóvel tem um vencimento pendente. Retorna (ok, mensagem do painel)."""
    try:
        resp = requests.post(
            URL_API_COLETA,
            json={
                "token": TOKEN_SEGURANCA,
                "imovel": str(numero_imovel),
                "nome_robo": NOME_ROBO,
                "status": "COLETADO",
                "observacao": observacao,
                "vencimento": vencimento or "",
            },
            timeout=20,
        )
        corpo = resp.json() if resp.status_code == 200 else {}
        return bool(corpo.get("sucesso", False)), str(corpo.get("msg") or corpo.get("aviso") or "")
    except Exception as e:
        print(f"ERRO ao dar baixa na API: {e}", flush=True)
        return False, str(e)


def nome_do_pdf(numero, vencimento, todos):
    """<imóvel>.pdf como sempre; quando o imóvel tem mais de um vencimento, só o mais cedo leva o nome simples
    (os outros levam o vencimento no nome, para um não sobrescrever o outro)."""
    if len(todos) <= 1 or not vencimento or vencimento == todos[0]:
        return f"{numero}.pdf"
    return f"{numero}_{vencimento.replace('/', '-')}.pdf"


def agrupar_por_url(dados, pendentes):
    """Agrupa por URL do portal. Se `pendentes` não for None, mantém só os pendentes."""
    grupos = {}
    for d in dados:
        url = limpar_url(d["link_portal"])
        numero = str(d["imovel"] or "").strip()
        if not url or not numero:
            continue
        if pendentes is not None and norm_imovel(numero) not in pendentes:
            continue
        grupos.setdefault(url, []).append({**d, "imovel": numero})
    return grupos


# =======================================================================
# NAVEGADOR (PERFIL PERSISTENTE)
# =======================================================================
def abrir_contexto(playwright):
    os.makedirs(PASTA_PERFIL, exist_ok=True)
    return playwright.chromium.launch_persistent_context(
        PASTA_PERFIL,
        channel="chrome",
        headless=False,  # o Google costuma barrar login em modo headless
        ignore_default_args=["--enable-automation"],
        args=["--disable-blink-features=AutomationControlled"],
        accept_downloads=True,
        viewport=None,
    )


def modo_login_manual():
    """Abre um Chrome NORMAL (sem Playwright) usando a pasta do perfil do robô.
    Assim o Google não desconfia de automação e marca este computador como confiável."""
    dados = buscar_imoveis()
    urls = sorted({limpar_url(d["link_portal"]) for d in dados if limpar_url(d["link_portal"])})
    url_inicial = sys.argv[2] if len(sys.argv) > 2 else (urls[0] if urls else "https://accounts.google.com")
    os.makedirs(PASTA_PERFIL, exist_ok=True)

    print("\n>>> Vai abrir um Chrome só do robô (pasta separada do seu Chrome do dia a dia).", flush=True)
    print(f">>> 1) Canto superior direito: ícone do perfil > 'Fazer login no Chrome' e entre com {GOOGLE_EMAIL}", flush=True)
    print(">>>    (senha + código). Assim ele vira um perfil gerenciado, igual ao seu perfil 'Trabalho'.", flush=True)
    print(">>> 2) Marque 'Não perguntar novamente neste dispositivo' se aparecer.", flush=True)
    print(">>> 3) Abra o portal, clique em 'Login com Google' e chegue até a tela inicial.", flush=True)
    print(">>> 4) FECHE o Chrome para salvar a sessão.\n", flush=True)

    chrome = next((c for c in CAMINHOS_CHROME if os.path.exists(c)), None)
    if chrome:
        processo = subprocess.Popen([
            chrome, f"--user-data-dir={PASTA_PERFIL}", "--no-first-run",
            "--no-default-browser-check", url_inicial])
        processo.wait()
    else:  # sem chrome.exe nos caminhos padrão: cai no Chrome do Playwright
        with sync_playwright() as p:
            contexto = abrir_contexto(p)
            pagina = contexto.pages[0] if contexto.pages else contexto.new_page()
            pagina.goto(url_inicial)
            input(">>> Quando terminar, aperte ENTER aqui para salvar a sessão... ")
            contexto.close()
    _limpar_bloqueio()
    print("✅ Sessão salva em:", PASTA_PERFIL, flush=True)


# =======================================================================
# LOGIN
# =======================================================================
def _visivel(alvo, seletor):
    try:
        loc = alvo.locator(seletor)
        return loc.count() > 0 and loc.first.is_visible()
    except Exception:
        return False


def _clicar_texto(pagina, padrao):
    """Clica no primeiro botão/link visível cujo texto bate com o regex. True se clicou."""
    for papel in ("button", "link"):
        try:
            loc = pagina.get_by_role(papel, name=padrao)
            if loc.count() and loc.first.is_visible():
                loc.first.click()
                return True
        except Exception:
            pass
    return False


def _avancar(pg):
    """Clica em "Próxima" do Google; se não achar, aperta Enter."""
    for seletor in ("#identifierNext button", "#passwordNext button", "#totpNext button",
                    "#identifierNext", "#passwordNext", "#totpNext"):
        if _visivel(pg, seletor):
            pg.locator(seletor).first.click()
            return
    if not _clicar_texto(pg, re.compile(r"^(pr[oó]xima|avan[cç]ar|next|seguinte|verificar|verify)$", re.I)):
        pg.keyboard.press("Enter")


REGEX_OUTRO_JEITO = re.compile(
    r"tent(e|ar) de outr[oa] (jeito|maneira|forma)|outr[oa] (jeito|maneira|forma)|try another way", re.I)


def _opcoes_na_tela(pg):
    """Texto da tela atual, compacto, para o erro dizer o que o Google ofereceu."""
    try:
        linhas = [l.strip() for l in pg.locator("body").inner_text(timeout=3000).splitlines() if l.strip()]
        return " | ".join(linhas)[:300]
    except Exception:
        return "?"


def _preencher(loc, valor):
    """Espera o campo, clica nele, preenche e confere. Se o Google ignorar, digita tecla a tecla."""
    loc.wait_for(state="visible", timeout=10000)
    loc.click()
    loc.fill(valor)
    if loc.input_value() != valor:
        loc.fill("")
        loc.press_sequentially(valor, delay=60)


def _esperar_trocar_tela(pg, caminho_antes, timeout=15000):
    """Depois do "Avançar": espera o endereço mudar e a animação do Google terminar."""
    try:
        pg.wait_for_url(lambda u: u.split("?")[0] != caminho_antes, timeout=timeout)
    except Exception:
        pass
    try:
        pg.wait_for_load_state("domcontentloaded", timeout=10000)
        pg.wait_for_timeout(1200)
    except Exception:
        pass


def portal_logado(pagina, timeout):
    try:
        pagina.locator(SEL_COMBO_CONDOMINIOS).first.wait_for(state="visible", timeout=timeout)
        return True
    except PWTimeout:
        return False


def clicar_botao_google(pagina):
    for seletor in SELETORES_BOTAO_GOOGLE:
        if _visivel(pagina, seletor):
            try:
                pagina.locator(seletor).first.click(timeout=5000)
                return True
            except Exception:
                continue
    # Botão oficial do Google ("Sign in with Google") é desenhado dentro de um iframe
    try:
        frame = pagina.frame_locator('iframe[src*="accounts.google.com/gsi"]')
        botao = frame.locator('[role="button"]').first
        botao.click(timeout=3000)
        return True
    except Exception:
        return False


def fluxo_google(pg):
    """Responde cada tela do accounts.google.com até ele devolver para o portal. Retorna (ok, motivo)."""
    codigos_usados = []      # também guarda "SENHA" quando a senha já foi digitada uma vez
    sem_saida = 0          # vezes seguidas numa tela que o robô não sabe responder
    fim = time.time() + 120
    try:
        pg.wait_for_load_state("domcontentloaded", timeout=15000)   # deixa o pop-up terminar de carregar
    except Exception:
        pass
    while time.time() < fim:
        if pg.is_closed():
            return True, ""                      # pop-up fechou sozinho = Google terminou
        try:
            pg.wait_for_timeout(1500)
        except Exception:
            return True, ""                      # o pop-up fechou DURANTE a espera: também é o fim do login
        if pg.is_closed():
            return True, ""
        url = pg.url
        if "accounts.google.com" not in url:
            return True, ""
        try:
            resultado = _passo_google(pg, url, codigos_usados)
        except Exception:
            resultado = "esperar"                # tela trocou no meio da ação: tenta de novo
        if isinstance(resultado, tuple):
            return resultado
        if resultado == "sem_saida":
            sem_saida += 1
            if sem_saida >= 6:
                return False, f"Google pediu uma verificação que o robô não responde ({url.split('?')[0]})"
        elif resultado == "agiu":
            sem_saida = 0
    return False, f"Tempo esgotado no login do Google (parou em {pg.url.split('?')[0]})"


def _passo_google(pg, url, codigos_usados):
    """Um passo na tela atual. Retorna 'agiu', 'esperar', 'sem_saida' ou (False, motivo) para desistir."""
    try:
        texto = sem_acento(pg.locator("body").inner_text(timeout=3000)).lower()
    except Exception:
        texto = ""

    # --- Bloqueios que o robô não resolve sozinho ---
    if "pode nao ser seguro" in texto or "may not be secure" in texto:
        return False, "Google bloqueou o navegador automatizado. Rode: python robo_superlogica.py --login"
    if "/signin/rejected" in url or re.search(
            r"nao foi possivel fazer o login|couldn.t sign you in|contato com o administrador do seu dominio", texto):
        _marcar_bloqueio()
        return False, ('Google BLOQUEOU o login da conta ("Não foi possível fazer o login"). NÃO tente de novo agora. '
                       'Peça à TI para ver o registro de login da ctrlcond no Admin Console e, se preciso, desativar '
                       'o desafio de login por 10 min. Depois faça o login manual: python robo_superlogica.py --login')
    if _visivel(pg, 'iframe[src*="recaptcha"]') or _visivel(pg, "#captchaimg"):
        return False, "Google pediu CAPTCHA. Rode: python robo_superlogica.py --login"

    caminho = url.split("?")[0]
    em_tela_de_digitar = any(t in caminho for t in ("/identifier", "/challenge/"))

    # 1) Escolher a conta (sessão do Google ainda vale). SÓ no seletor de contas.
    # Na tela de senha existe um "chip" com o e-mail (data-email=...): clicar nele tira o foco do campo.
    if not em_tela_de_digitar:
        for seletor in (f'[data-identifier="{GOOGLE_EMAIL}"]', f'li [data-email="{GOOGLE_EMAIL}"]'):
            if _visivel(pg, seletor):
                pg.locator(seletor).first.click()
                return "agiu"

    # 2) E-mail (tela /signin/identifier)
    if "/identifier" in caminho:
        if not _visivel(pg, SEL_GOOGLE_EMAIL):
            return "esperar"
        _preencher(pg.locator(SEL_GOOGLE_EMAIL).first, GOOGLE_EMAIL)
        _avancar(pg)
        _esperar_trocar_tela(pg, caminho)
        return "agiu"

    # 3) Senha
    if "/challenge/pwd" in caminho or _visivel(pg, SEL_GOOGLE_SENHA):
        if not GOOGLE_SENHA:
            return False, "Sessão do Google expirou e GOOGLE_SENHA não está preenchida no .env"
        if "SENHA" in codigos_usados:
            # Continua na tela de senha mesmo depois de esperar: o Google recusou.
            # Não tenta de novo para não bloquear a conta.
            if "senha incorreta" in texto or "wrong password" in texto or "senha errada" in texto:
                return False, "Google recusou a senha: confira GOOGLE_SENHA no .env"
            return False, "Google não aceitou a senha (continuou na tela de senha). Veja o print."
        if not _visivel(pg, SEL_GOOGLE_SENHA):
            return "esperar"                     # animação ainda trazendo o campo
        codigos_usados.append("SENHA")
        _preencher(pg.locator(SEL_GOOGLE_SENHA).first, GOOGLE_SENHA)
        _avancar(pg)
        _esperar_trocar_tela(pg, caminho)
        return "agiu"

    # 4) Código do app autenticador
    if "/challenge/totp" in url or _visivel(pg, 'input[name="totpPin"]'):
        campo = SEL_GOOGLE_CODIGO
        if not _visivel(pg, campo):
            return "esperar"                     # tela ainda carregando
        if sum(1 for c in codigos_usados if c.isdigit()) >= 2:
            return False, "Google recusou o código do autenticador 2x (relógio do Windows certo? chave certa?)"
        codigo = gerar_codigo_totp(codigos_usados)
        if not codigo:
            return False, "Google pediu o código do autenticador, mas GOOGLE_TOTP_SECRET não está no .env"
        _preencher(pg.locator(campo).first, codigo)
        codigos_usados.append(codigo)
        try:  # "Não perguntar novamente neste computador", quando o Google oferece
            caixa = pg.get_by_label(re.compile(r"n[aã]o perguntar|don.t ask again", re.I))
            if caixa.count() and not caixa.first.is_checked():
                caixa.first.check()
        except Exception:
            pass
        _avancar(pg)
        _esperar_trocar_tela(pg, caminho)
        return "agiu"

    # 5) Tela de escolha do método: escolher o app autenticador
    if "/challenge/selection" in url:
        opcao = pg.get_by_text(re.compile(r"Authenticator|autenticador", re.I))
        if opcao.count():
            opcao.first.click()
            return "agiu"
        if not texto:
            return "esperar"                     # página ainda carregando
        # Lista só com telefones: o "Tentar de outro jeito" de baixo abre as demais opções.
        if codigos_usados.count("OUTRO_JEITO") < 2 and _clicar_texto(pg, REGEX_OUTRO_JEITO):
            codigos_usados.append("OUTRO_JEITO")
            return "agiu"
        return False, ("Google não ofereceu o app autenticador como opção. O que apareceu na tela: "
                       + _opcoes_na_tela(pg)
                       + " || Cadastre o app autenticador na conta (Segurança > Verificação em duas etapas).")

    # 6) Outro desafio (notificação no celular, SMS...): pedir outro método
    if "/challenge/" in url and "/challenge/pwd" not in url:
        if _clicar_texto(pg, REGEX_OUTRO_JEITO):
            return "agiu"
        return "sem_saida"

    # 7) Consentimento / avisos ("Continuar", "Agora não"...)
    if _clicar_texto(pg, re.compile(r"^(continuar|continue|permitir|allow|confirmar|confirm)$", re.I)):
        return "agiu"
    if _clicar_texto(pg, re.compile(r"^(agora n[aã]o|not now|pular|skip|lembrar mais tarde)$", re.I)):
        return "agiu"
    return "sem_saida"


def garantir_login(pagina, url):
    """Abre o portal e garante que está logado. Retorna (ok, motivo)."""
    pagina.goto(url, wait_until="domcontentloaded")
    if portal_logado(pagina, 15000):
        return True, ""

    pausa = _bloqueio_ativo()
    if pausa:
        return False, pausa
    print("   Sessão do portal caiu: entrando com o Google...", flush=True)
    contexto = pagina.context
    abas_antes = list(contexto.pages)

    if "accounts.google.com" not in pagina.url and not clicar_botao_google(pagina):
        salvar_print(pagina, "sem_botao_google")
        return False, "Botão 'Entrar com Google' não encontrado (ajustar SELETORES_BOTAO_GOOGLE)"

    # O Google pode abrir na mesma aba (redirecionamento) ou num pop-up
    aba_google = None
    for _ in range(20):  # até ~10s
        candidatas = [p for p in contexto.pages if p not in abas_antes] + [pagina]
        aba_google = next((p for p in candidatas if not p.is_closed() and "accounts.google.com" in p.url), None)
        if aba_google or portal_logado(pagina, 500):
            break

    if aba_google:
        ok, motivo = fluxo_google(aba_google)
        if not ok:
            salvar_print(aba_google, "login_google")
            if aba_google is not pagina:
                try:
                    aba_google.close()
                except Exception:
                    pass
            return False, motivo

    if portal_logado(pagina, 12000):
        return True, ""
    pagina.goto(url, wait_until="domcontentloaded")  # no fluxo de pop-up, às vezes precisa recarregar
    if portal_logado(pagina, 20000):
        return True, ""
    salvar_print(pagina, "portal_nao_logou")
    return False, "Passou pelo Google mas o portal não abriu logado"


def modo_testar_login():
    """Só faz login em cada portal e diz quais entraram. Não baixa nada, não dá baixa."""
    urls = sorted({limpar_url(d["link_portal"]) for d in buscar_imoveis() if limpar_url(d["link_portal"])})
    if len(sys.argv) > 2:
        urls = [sys.argv[2]]
    print(f"Testando login em {len(urls)} portal(is)...", flush=True)
    ok_total = 0
    with sync_playwright() as p:
        contexto = abrir_contexto(p)
        pagina = contexto.pages[0] if contexto.pages else contexto.new_page()
        for url in urls:
            if pagina.is_closed():
                print("🚨 FALHA: o navegador foi fechado. Parando o teste.", flush=True)
                break
            try:
                ok, motivo = garantir_login(pagina, url)
            except Exception as e:
                ok, motivo = False, f"Erro ao abrir o portal: {str(e).splitlines()[0]}"

            ok_total += ok
            print(f"{'✅' if ok else '🚨 FALHA'} {url} {motivo}", flush=True)
        contexto.close()
    print(f"\n🏁 Login OK em {ok_total}/{len(urls)} portal(is).", flush=True)


# =======================================================================
# DOWNLOAD DO BOLETO
# =======================================================================
# -----------------------------------------------------------------------------------------------
# TELA DE BOLETOS (conferida no portal real em 29/10/2026)
#   - abas "Em aberto" | "Pagos"; lista vazia mostra "Não há boletos";
#   - cada boleto é uma linha <a href="#"> com: unidade, bloco, "Vencimento em dd/mm/aaaa" (+ "Pago em ...") e valor;
#   - clicar na linha abre a janela "Segunda via" (caixa "Vencimento automático", mantida marcada) -> "Visualizar";
#   - o Visualizar abre uma ABA NOVA com a "Fatura" em HTML (sem PDF); o PDF do boleto fica no mesmo endereço trocando
#     "-FaturaHtml-flSegundaVia" por "-flSegundaVia-original" (é o que o botão Pagar > Imprimir faz). Outros portais
#     podem abrir o PDF direto ou baixar o arquivo: os três caminhos são tratados em _pdf_da_aba/_capturar_pdf;
#   - o condomínio padrão do portal pode não ter "cobranças" ("Acesso negado à ferramenta de cobranças"): nesse caso a
#     lista de condomínios só carrega na tela inicial (/clients/areadocondomino/) e o robô vai para lá.
# Os antigos seletores (href*="boleto", "Baixar"...) não casavam com nada disso.
# -----------------------------------------------------------------------------------------------
SEL_LINHA_BOLETO = 'a:visible:has-text("Vencimento em")'
TEXTO_SEM_BOLETOS = "Não há boletos"
RE_VENC_LINHA = re.compile(r"Vencimento\s+em\s+(\d{2}/\d{2}/\d{4})", re.IGNORECASE)


def _mes_ano(data_br):
    try:
        d = datetime.strptime(data_br, "%d/%m/%Y")
        return d.month, d.year
    except (ValueError, TypeError):
        return None


def _tokens(texto):
    """'TORRE B 0081' -> {'TORRE', 'B', '81'} (sem acento, maiúsculas, zeros à esquerda dos números tirados)."""
    partes = re.findall(r"[A-Z0-9]+", sem_acento(texto).upper())
    return {p.lstrip("0") or "0" if p.isdigit() else p for p in partes}


def ler_linha_boleto(texto):
    """Texto de uma linha da lista -> {'venc','pago','ident','texto'}. 'ident' = o que vem antes do vencimento
    (unidade e bloco), usado para conferir que a linha é mesmo do imóvel procurado."""
    t = " ".join(str(texto or "").split())
    m = RE_VENC_LINHA.search(t)
    return {"venc": m.group(1) if m else "",
            "pago": bool(re.search(r"\bpago\s+em\b", t, re.IGNORECASE)),
            "ident": t[:m.start()].strip() if m else t, "texto": t}


_PALAVRAS_DE_BLOCO = {"TORRE", "BLOCO", "BL", "BLC", "EDIFICIO", "ED", "EDF"}


def linha_e_da_unidade(linha, unidade, bloco=""):
    """A busca do portal é aproximada ('164' acha '1640'): confere a unidade e, quando o portal mostra o bloco na
    linha, que ele é o mesmo ('TORRE A' e 'TORRE B' não se confundem só por ambos dizerem TORRE)."""
    if not unidade:
        return True
    toks = _tokens(linha["ident"])
    tu = _tokens(unidade)
    if not tu <= toks:
        return False
    tb = _tokens(bloco) - _PALAVRAS_DE_BLOCO
    extras = (toks - tu) - _PALAVRAS_DE_BLOCO
    if tb and extras and not (tb & extras):
        return False
    return True


def escolher_linha(linhas, desejado=""):
    """linhas = [{'venc','pago',...}]. Devolve (linha, '') ou (None, motivo). Só boleto EM ABERTO e do MÊS do vencimento
    esperado (nunca um atrasado de meses anteriores). Dentro do mês: o vencimento exato; senão o único; senão o mais próximo."""
    abertas = [l for l in linhas if not l["pago"] and l["venc"]]
    if not abertas:
        return None, "nenhum boleto em aberto na lista"
    data = lambda l: datetime.strptime(l["venc"], "%d/%m/%Y")   # noqa: E731
    if not desejado:
        return max(abertas, key=data), ""
    alvo = _mes_ano(desejado)
    do_mes = [l for l in abertas if _mes_ano(l["venc"]) == alvo]
    if not do_mes:
        datas = ", ".join(l["venc"] for l in abertas)
        return None, f"em aberto no portal: {datas}; nenhum do mês {alvo[0]:02d}/{alvo[1]} (esperado {desejado})" if alvo else f"vencimento esperado ilegível: {desejado}"
    exato = [l for l in do_mes if l["venc"] == desejado]
    if exato:
        return exato[0], ""
    if len(do_mes) == 1:
        return do_mes[0], ""
    alvo_data = datetime.strptime(desejado, "%d/%m/%Y")
    return min(do_mes, key=lambda l: abs((data(l) - alvo_data).days)), ""


def garantir_aba_em_aberto(pagina):
    """Garante a aba 'Em aberto' (a lista de 'Pagos' tem as mesmas linhas, só que pagas)."""
    try:
        aba = pagina.get_by_text("Em aberto", exact=True).first
        if aba.is_visible():
            aba.click()
            pagina.wait_for_timeout(400)
    except Exception:
        pass


def _contar_linhas(pagina):
    try:
        return pagina.locator(SEL_LINHA_BOLETO).count()
    except Exception:
        return 0


def _lista_vazia(pagina):
    try:
        return pagina.get_by_text(TEXTO_SEM_BOLETOS).first.is_visible()
    except Exception:
        return False


def aguardar_lista_boletos(pagina, limite_ms=9000):
    """Espera a lista se assentar depois da busca: com linhas (estáveis) OU com a mensagem 'Não há boletos'.
    Antes era uma pausa fixa de 2,5 s, que lia a lista antes do filtro terminar."""
    pagina.wait_for_timeout(1200)
    fim = time.time() + limite_ms / 1000
    anterior = -1
    while time.time() < fim:
        n = _contar_linhas(pagina)
        if n and n == anterior:
            return
        if not n and _lista_vazia(pagina):
            return
        anterior = n
        pagina.wait_for_timeout(500)


def _eh_pdf(corpo):
    return bool(corpo) and b"%PDF-" in corpo[:1024]


def _baixar_url_pdf(contexto, url):
    """GET (com os cookies do navegador) -> bytes do PDF, ou None se a resposta não for um PDF."""
    try:
        r = contexto.request.get(url, timeout=30000)
        corpo = r.body()
        return corpo if r.ok and _eh_pdf(corpo) else None
    except Exception:
        return None


def _url_pdf_da_fatura(url):
    """A aba 'Fatura' (HTML) e o PDF do boleto têm o mesmo código no endereço:
    .../c/<id>-<hash>-FaturaHtml-flSegundaVia  ->  .../c/<id>-<hash>-flSegundaVia-original (conferido no portal real)."""
    novo = url.replace("-FaturaHtml-flSegundaVia", "-flSegundaVia-original")
    return novo if novo != url else ""


def _ler_download(download):
    caminho = os.path.join(tempfile.gettempdir(), f"superlogica_{os.getpid()}_{int(time.time() * 1000)}.pdf")
    download.save_as(caminho)
    try:
        with open(caminho, "rb") as f:
            return f.read()
    finally:
        try:
            os.remove(caminho)
        except OSError:
            pass


def _clicar_visivel(alvo, texto):
    """Clica no link/botão VISÍVEL cujo nome é exatamente `texto` (a página tem cópias escondidas). True se clicou."""
    padrao = re.compile(rf"^\s*{re.escape(texto)}\s*$", re.IGNORECASE)
    for papel in ("button", "link"):
        try:
            itens = alvo.get_by_role(papel, name=padrao).all()
        except Exception:
            continue
        for el in itens:
            try:
                if not el.is_visible():
                    continue
                try:
                    el.click(timeout=5000)
                except Exception:           # faixa de cookies por cima: dispara o clique direto no elemento
                    el.dispatch_event("click")
                return True
            except Exception:
                continue
    return False


def _confirmar_segunda_via(pagina):
    """Alguns portais abrem a janela 'Segunda via' (com 'Vencimento automático') antes do boleto: confirma em
    'Visualizar'. A caixa vem marcada (recomendado pelo portal) e é mantida."""
    return _clicar_visivel(pagina, "Visualizar")


def _pdf_da_aba(nova, contexto, downloads, outras):
    """A aba aberta pelo portal -> (bytes do PDF, ''), ou (None, motivo). A aba pode ser:
    - o próprio PDF (ou um download);
    - a 'Fatura' em HTML: o PDF está em .../...-flSegundaVia-original; senão o caminho pela tela é Pagar > Imprimir."""
    try:
        nova.wait_for_load_state("domcontentloaded", timeout=15000)
    except Exception:
        pass
    for _ in range(30):   # a aba nasce em branco e redireciona até o endereço final
        if downloads or nova.url.startswith("http"):
            break
        nova.wait_for_timeout(500)
    if downloads:
        return _ler_download(downloads[0]), ""

    url = nova.url
    corpo = _baixar_url_pdf(contexto, url)
    if corpo:
        return corpo, ""
    alt = _url_pdf_da_fatura(url)
    if alt:
        corpo = _baixar_url_pdf(contexto, alt)
        if corpo:
            return corpo, ""

    # Último recurso: o caminho pela tela da fatura (Pagar mostra as formas de pagamento; Imprimir leva ao PDF).
    _clicar_visivel(nova, "Pagar")
    nova.wait_for_timeout(800)
    if not _clicar_visivel(nova, "Imprimir"):
        return None, f"O portal abriu a fatura em HTML e não achei o botão Imprimir nem o PDF ({url[:90]})"
    for _ in range(30):
        if downloads or nova.url != url or outras:
            break
        nova.wait_for_timeout(500)
    if downloads:
        return _ler_download(downloads[0]), ""
    for alvo in [nova] + list(outras):
        if alvo.url.startswith("http") and alvo.url != url:
            corpo = _baixar_url_pdf(contexto, alvo.url)
            if corpo:
                return corpo, ""
    return None, "Cliquei em Imprimir na fatura, mas o portal não entregou um PDF"


def _capturar_pdf(pagina, botao):
    """Clica na linha do boleto e devolve (bytes do PDF, ''), ou (None, motivo)."""
    contexto = pagina.context
    downloads, novas = [], []

    def ao_download(d):
        downloads.append(d)

    def ao_nova_pagina(nova):
        novas.append(nova)
        nova.on("download", ao_download)

    pagina.on("download", ao_download)
    contexto.on("page", ao_nova_pagina)
    try:
        botao.click()
        confirmou = False
        for _ in range(40):  # até ~20 s: janela 'Segunda via' (se houver) -> aba nova / download
            if downloads or novas:
                break
            if not confirmou:
                confirmou = _confirmar_segunda_via(pagina)
            pagina.wait_for_timeout(500)
        if downloads:
            return _ler_download(downloads[0]), ""
        if not novas:
            return None, f"Clique não gerou download nem nova aba ({pagina.url[:90]})"
        try:
            return _pdf_da_aba(novas[0], contexto, downloads, novas[1:])
        finally:
            for aba in list(novas):
                try:
                    aba.close()
                except Exception:
                    pass
    finally:
        for evento, fn, alvo in (("download", ao_download, pagina), ("page", ao_nova_pagina, contexto)):
            try:
                alvo.remove_listener(evento, fn)
            except Exception:
                pass
        for aba in list(novas):          # nunca deixa aba do portal aberta (inclusive a que só disparou o download)
            try:
                if not aba.is_closed():
                    aba.close()
            except Exception:
                pass


def baixar_boleto(pagina, caminho_pdf, desejado="", info=None, unidade="", bloco=""):
    """Escolhe a linha do boleto em aberto (do vencimento desejado), clica e captura o PDF (aba nova ou download).
    Retorna (ok, motivo). info['venc'] recebe o vencimento da linha escolhida."""
    candidatas = []
    loc = pagina.locator(SEL_LINHA_BOLETO)
    for i in range(min(loc.count(), 40)):
        el = loc.nth(i)
        try:
            if not el.is_visible():
                continue
            linha = ler_linha_boleto(el.inner_text())
        except Exception:
            continue
        if linha["venc"] and linha_e_da_unidade(linha, unidade, bloco):
            linha["el"] = el
            candidatas.append(linha)
    if not candidatas:
        if _lista_vazia(pagina):
            return False, "Sem boleto em aberto no portal (a lista mostra 'Não há boletos')"
        return False, "Nenhuma linha de boleto da unidade na lista (confira a unidade/bloco do mapeamento)"
    escolhida, motivo = escolher_linha(candidatas, desejado)
    if escolhida is None:
        return False, f"Sem boleto utilizável: {motivo}"
    if info is not None:
        info["venc"] = escolhida["venc"]
        info["linha"] = escolhida["texto"]

    try:
        corpo, motivo = _capturar_pdf(pagina, escolhida["el"])
    except Exception as e:
        return False, f"Erro ao baixar: {e}"
    if corpo is None:
        return False, motivo
    if not _eh_pdf(corpo):
        return False, "Arquivo baixado não é um PDF válido"
    try:
        with open(caminho_pdf, "wb") as f:
            f.write(corpo)
    except Exception as e:
        return False, f"Erro ao salvar o PDF em {caminho_pdf}: {e}"
    return True, ""


# =======================================================================
# PROCESSO PRINCIPAL
# =======================================================================
def _acesso_negado(pagina):
    """O condomínio PADRÃO do portal pode não ter 'cobranças' para esta conta: a página vira 'Erro - Acesso negado
    à ferramenta de cobranças' (e a lista de condomínios nem carrega nela)."""
    try:
        return pagina.get_by_text(re.compile(r"Acesso negado", re.IGNORECASE)).first.is_visible()
    except Exception:
        return False


def _ir_para_inicio_portal(pagina):
    """Volta para a tela inicial do portal (onde o combo de condomínios sempre carrega)."""
    try:
        partes = urlparse(pagina.url)
        if partes.scheme.startswith("http") and partes.netloc:
            pagina.goto(f"{partes.scheme}://{partes.netloc}/clients/areadocondomino/", wait_until="domcontentloaded")
            try:
                pagina.wait_for_load_state("networkidle", timeout=10000)
            except Exception:
                pass
    except Exception:
        pass


def _lista_visivel(pagina):
    try:
        return pagina.locator("#lista").first.is_visible()
    except Exception:
        return False


def _fechar_lista(pagina):
    """Deixa o combo fechado, para o próximo imóvel começar do zero (clicar de novo reabre, não fecha)."""
    try:
        if _lista_visivel(pagina):
            pagina.keyboard.press("Escape")
            pagina.wait_for_timeout(200)
        if _lista_visivel(pagina):
            pagina.click(SEL_COMBO_CONDOMINIOS)
            pagina.wait_for_timeout(200)
    except Exception:
        pass


def _ler_opcoes_condominio(pagina, espera_ms=12000):
    """[(nome, elemento)] da lista. A lista pode chegar DEPOIS do clique (carrega por AJAX): espera aparecer."""
    limite = time.time() + espera_ms / 1000
    while True:
        opcoes = []
        for el in pagina.query_selector_all(SEL_LISTA_CONDOMINIOS):
            nome = " ".join((el.text_content() or "").split())
            if nome:
                opcoes.append((nome, el))
        if opcoes or time.time() >= limite:
            return opcoes
        pagina.wait_for_timeout(300)


def selecionar_condominio(pagina, nome_condominio, _repetiu=False):
    """Abre o combo e clica no condomínio (nome igual, senão o mais parecido). Retorna (ok, motivo)."""
    if _acesso_negado(pagina):        # parou na página de erro do condomínio padrão: a lista só carrega na inicial
        _ir_para_inicio_portal(pagina)
    pagina.wait_for_selector(SEL_COMBO_CONDOMINIOS, state="visible", timeout=10000)
    if not _lista_visivel(pagina):
        pagina.click(SEL_COMBO_CONDOMINIOS)
        pagina.wait_for_selector("#lista", state="visible", timeout=10000)
    opcoes = _ler_opcoes_condominio(pagina)
    if not opcoes and not _repetiu:
        _ir_para_inicio_portal(pagina)
        return selecionar_condominio(pagina, nome_condominio, _repetiu=True)
    if not opcoes:
        _fechar_lista(pagina)
        return False, "A lista de condomínios do portal veio vazia (esperei 12 s, também na tela inicial)"

    alvo_exato = " ".join(sem_acento(nome_condominio).upper().split())
    maior_score, melhor = 0, None
    for nome_site, el in opcoes:
        if " ".join(sem_acento(nome_site).upper().split()) == alvo_exato:
            maior_score, melhor = 1.0, (nome_site, el)
            break
        score = calcular_similaridade(nome_condominio, nome_site)
        if score > maior_score:
            maior_score, melhor = score, (nome_site, el)

    if melhor and maior_score > LIMIAR_SIMILARIDADE:
        el = melhor[1]
        id_elemento = el.get_attribute("id")
        try:
            el.click()
        except Exception:
            if not id_elemento:
                raise
            pagina.click(f'a[id="{id_elemento}"]')
        pagina.wait_for_load_state("networkidle")
        _fechar_lista(pagina)
        return True, ""

    _fechar_lista(pagina)
    nomes = " | ".join(n for n, _ in opcoes[:8]) + (" | ..." if len(opcoes) > 8 else "")
    return False, (f"Condomínio '{nome_condominio}' não encontrado (maior score: {maior_score:.2f}, "
                   f"mais parecido: '{melhor[0] if melhor else '-'}'). Na lista ({len(opcoes)}): {nomes}")


def executar_automacao():
    if not TOKEN_SEGURANCA:
        print("ERRO: TOKEN_SEGURANCA não encontrado no .env", flush=True)
        return
    if not (GOOGLE_SENHA and GOOGLE_TOTP_SECRET):
        print("⚠️ GOOGLE_SENHA/GOOGLE_TOTP_SECRET fora do .env: só funciona enquanto a sessão salva valer.", flush=True)

    print("Conectando ao banco de dados...", flush=True)
    dados = buscar_imoveis()
    if not dados:
        print("Nenhum imóvel Superlógica encontrado ou erro na conexão.", flush=True)
        return

    pendentes = buscar_pendencias_api()
    if pendentes is None:
        print("⚠️ API de pendências fora do ar: processando TODOS os imóveis Superlógica.", flush=True)
    grupos = agrupar_por_url(dados, pendentes)
    total = sum(len(v) for v in grupos.values())
    print(f"{total} imóvel(is) pendente(s) em {len(grupos)} portal(is).", flush=True)
    if not total:
        print("Nada pendente para coletar. 🏁", flush=True)
        return

    os.makedirs(PASTA_DESTINO, exist_ok=True)
    relatorio = []
    coletados = 0

    def registrar(imovel, url, status, motivo=""):
        relatorio.append({
            "Imóvel": imovel["imovel"],
            "Empreendimento": imovel["empreendimento"],
            "Condomínio no portal": imovel.get("super_condominio", ""),
            "URL": url,
            "Status": status,
            "Motivo": motivo,
        })

    with sync_playwright() as p:
        contexto = abrir_contexto(p)
        pagina = contexto.pages[0] if contexto.pages else contexto.new_page()

        falhas_login_seguidas = 0
        for url, lista in grupos.items():
            if falhas_login_seguidas >= 2:
                motivo = "Robô parado: login falhou em 2 portais seguidos"
                for im in lista:
                    registrar(im, url, "NÃO PROCESSADO", motivo)
                continue
            print(f"\n{'=' * 50}\nPortal: {url} ({len(lista)} imóvel(is))", flush=True)
            if pagina.is_closed():
                print("🚨 FALHA: o navegador foi fechado durante a execução. Parando.", flush=True)
                break

            try:
                logado, motivo_login = garantir_login(pagina, url)
            except Exception as e:
                logado, motivo_login = False, f"Erro ao abrir o portal: {str(e).splitlines()[0]}"
            if not logado:
                print(f"🚨 FALHA NO LOGIN: {motivo_login}", flush=True)
                for im in lista:
                    registrar(im, url, "FALHA", motivo_login)
                falhas_login_seguidas += 1
                if falhas_login_seguidas >= 2:
                    print("🚨 FALHA: login falhou em 2 portais seguidos. Parando para não abrir uma janela "
                          "do Google por portal. Veja os prints em prints_superlogica.", flush=True)
                continue
            falhas_login_seguidas = 0
            print("Sessão válida. Processando imóveis...", flush=True)

            for im in lista:
                if pagina.is_closed():
                    break
                numero = im["imovel"]
                if sem_acento(im.get("super_status")).upper().strip() == "NAO TEM":
                    motivo = "Mapeamento Superlógica = NÃO TEM (unidade não localizada no portal)"
                    print(f"-> Imóvel {numero}: pulado. {motivo}", flush=True)
                    registrar(im, url, "PULADO", motivo)
                    continue

                unidade = str(im.get("super_unidade") or "").strip()
                bloco = str(im.get("super_bloco") or "").strip()
                if not unidade:
                    unidade, bloco = extrair_unidade_bloco(im["endereco"])
                condominio = str(im.get("super_condominio") or "").strip() or im["empreendimento"]
                termo = f"{unidade} {bloco}".strip()
                print(f"\n-> {condominio} | Imóvel {numero} | Busca: '{termo}'", flush=True)

                if not unidade:
                    motivo = "Unidade não identificada (busca sem unidade pegaria boleto errado)"
                    print(f"FALHA: {motivo}", flush=True)
                    registrar(im, url, "FALHA", motivo)
                    continue

                try:
                    ok, motivo = selecionar_condominio(pagina, condominio)
                    if not ok:
                        salvar_diagnostico(pagina, "condominio_nao_achado")
                        print(f"FALHA: {motivo}", flush=True)
                        registrar(im, url, "FALHA", motivo)
                        continue

                    if _acesso_negado(pagina):
                        salvar_diagnostico(pagina, "acesso_negado_cobrancas")
                        motivo = (f"O portal respondeu 'Acesso negado à ferramenta de cobranças' em '{condominio}' "
                                  "(a conta ctrlcond não tem permissão de boletos nesse condomínio)")
                        print(f"FALHA: {motivo}", flush=True)
                        registrar(im, url, "FALHA", motivo)
                        continue
                    pagina.wait_for_selector(SEL_BUSCA_BOLETOS, state="visible", timeout=10000)
                    garantir_aba_em_aberto(pagina)
                    pagina.fill(SEL_BUSCA_BOLETOS, termo)
                    pagina.keyboard.press("Enter")
                    aguardar_lista_boletos(pagina)     # espera o filtro terminar (linhas estáveis ou "Não há boletos")

                    # Cada VENCIMENTO pendente é um boleto (dia 05 e dia 15 de um mesmo imóvel são dois).
                    dados_venc = pendentes.get(norm_imovel(numero)) if isinstance(pendentes, dict) else None
                    lista_venc = list((dados_venc or {}).get("pendentes") or []) or [""]
                    todos_venc = list((dados_venc or {}).get("todos") or [])
                    feitos, problemas, vistos = [], [], set()
                    for venc_pend in lista_venc:
                        info = {}
                        caminho = os.path.join(PASTA_DESTINO, nome_do_pdf(numero, venc_pend, todos_venc))
                        ok, motivo = baixar_boleto(pagina, caminho, desejado=venc_pend, info=info, unidade=unidade, bloco=bloco)
                        if not ok:
                            salvar_diagnostico(pagina, "boleto_falhou")
                            problemas.append(f"{venc_pend or 'boleto'}: {motivo}")
                            continue
                        venc_lido = info.get("venc", "")
                        if venc_lido and venc_lido in vistos:
                            problemas.append(f"o portal não mostrou o boleto de {venc_pend or '?'} (só o de {venc_lido})")
                            break
                        vistos.add(venc_lido)
                        # data ilegível no portal: com um vencimento pendente só, é ele; com dois não se adivinha
                        venc_baixa = venc_lido or (lista_venc[0] if len(lista_venc) == 1 else "")
                        obs = f"Coletado via Superlógica em {datetime.now().strftime('%d/%m/%Y %H:%M')}"
                        if venc_baixa:
                            obs += f" | venc {venc_baixa}"
                        ok_api, msg_api = dar_baixa_api(numero, obs, venc_baixa)
                        if ok_api:
                            feitos.append(venc_baixa or "-")
                        else:
                            problemas.append("PDF salvo, mas a baixa no painel falhou" + (f" ({msg_api})" if msg_api else ""))
                        if not venc_lido and len(lista_venc) > 1:
                            problemas.append("não consegui ler o vencimento no portal; os demais ficam para você marcar")
                            break

                    if feitos and not problemas:
                        coletados += 1
                        rotulo = f" (venc {', '.join(feitos)})" if todos_venc and len(todos_venc) > 1 else ""
                        print(f"✅ SUCESSO: imóvel {numero} coletado e baixado no painel{rotulo}.", flush=True)
                        registrar(im, url, "COLETADO", ", ".join(feitos) if len(feitos) > 1 else "")
                    elif feitos:
                        motivo = " | ".join(problemas)
                        print(f"⚠️ PARCIAL: imóvel {numero}: coletado {', '.join(feitos)}. {motivo}", flush=True)
                        registrar(im, url, "PARCIAL", f"coletado: {', '.join(feitos)} | {motivo}")
                    else:
                        motivo = " | ".join(problemas) or "Falha ao baixar o boleto"
                        status = "PDF SALVO / SEM BAIXA" if "baixa no painel" in motivo else "FALHA"
                        print(f"FALHA: {motivo}", flush=True)
                        registrar(im, url, status, motivo)

                except Exception as e:
                    motivo = f"Erro ao processar condomínio/unidade: {e}"
                    print(f"FALHA: {motivo}", flush=True)
                    registrar(im, url, "FALHA", motivo)

        try:
            contexto.close()
        except Exception:
            pass

    if relatorio:
        nome_arquivo = "relatorio_coletas_superlogica.xlsx"
        pd.DataFrame(relatorio).to_excel(nome_arquivo, index=False)
        print(f"\n🏁 Finalizado: {coletados}/{total} coletados. Relatório: {nome_arquivo}", flush=True)


if __name__ == "__main__":
    if "--codigo" in sys.argv:
        modo_mostrar_codigo()
    elif "--login" in sys.argv:
        modo_login_manual()
    elif "--testar-login" in sys.argv:
        modo_testar_login()
    else:
        executar_automacao()