# -*- coding: utf-8 -*-
"""
ROBÔ BASE LELLO — atualiza a base do sistema sozinho, direto do portal da Lello.

O QUE FAZ (o mesmo que a equipe fazia à mão, de ponta a ponta)
---------------------------------------------------------------
1) Pergunta ao sistema qual mês de vencimento está sendo trabalhado (ex.: Dezembro/2026).
2) Entra no portal da Lello, abre "Rel.Boletos Cond. não recebidos" e filtra:
       Resp. Pagto = Lello   |   Vencimento: do dia 01 até o último dia desse mês
3) Consulta, clica em "Exportar resultado para Excel" e guarda o arquivo.
4) Confere o arquivo (colunas, quantidade e mês) e manda para o sistema, que faz a MESMA importação
   de Configurações > "Importar Base Padrão de Imóveis" (herda status, respeita as bases congeladas).

O robô nunca faz a virada de mês sozinho: ele acompanha o mês da base ativa. Para puxar um mês novo,
escolha o mês em Configurações (seletor "Mês que o robô busca") ou suba a planilha na mão, como sempre.

QUEM CHAMA
----------
O agendador do sistema (09h, 11h, 13h, 15h e 17h) e o botão "Atualizar agora" de Configurações.
Ele sobe sem janela (invisível). Para ver o robô trabalhando, rode na mão com --visivel.

CONFIGURAÇÃO (.env, na pasta do sistema)
----------------------------------------
    LELLO_USUARIO=...        usuário do portal da Lello
    LELLO_SENHA=...          senha do portal da Lello
    TOKEN_SEGURANCA=...      o mesmo dos outros robôs
  opcionais:
    API_BASE=http://127.0.0.1:5000       onde o sistema está (padrão: porta de PORTA_SERVIDOR ou 5000)
    LELLO_URL_PORTAL=...                 endereço base do portal (padrão: https://pi.lelloimoveis.com.br/portalImoveis)
    LELLO_PASTA_DOWNLOADS=...            onde guardar os Excel baixados (padrão: <sistema>\\downloads_base_lello)
    LELLO_CANAL=chrome                   'chrome' (padrão) usa o Google Chrome instalado; vazio usa o Chromium do Playwright

COMANDOS
--------
    python robo_base_lello.py                    execução normal (é assim que o sistema chama)
    python robo_base_lello.py --visivel          com a janela do Chrome aberta, para acompanhar
    python robo_base_lello.py --so-baixar        entra, consulta e baixa; NÃO envia ao sistema
    python robo_base_lello.py --arquivo X.xlsx   pula o portal: confere o arquivo X e envia
    python robo_base_lello.py --testar-login     só testa usuário e senha do portal
    python robo_base_lello.py --mes 2026-12      pede esse mês (o sistema só aceita o mês que ele espera)
    python robo_base_lello.py --forcar           envia mesmo se a planilha vier muito menor que a base atual

SEGURANÇA
---------
- Senha nunca vai para o log. Se o portal recusar o login, o robô para na hora e NÃO tenta de novo com o mesmo usuário e senha
  (5 tentativas por dia poderiam bloquear o acesso). Corrija o .env e reinicie o sistema; ou confira com --testar-login.
- Quando algo dá errado, ficam na pasta de downloads, em "falhas\\", uma foto da tela e o HTML da página.
- Só roda um robô por vez (arquivo de trava).
"""
import argparse
import calendar
import hashlib
import os
import re
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

load_dotenv()

try:                                    # emoji no console do Windows
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# =======================================================================
# CONFIGURAÇÕES
# =======================================================================
URL_PORTAL = (os.getenv("LELLO_URL_PORTAL") or "https://pi.lelloimoveis.com.br/portalImoveis").rstrip("/")
URL_LOGIN = URL_PORTAL + "/pages/login/login.xhtml"
URL_RELATORIO = (URL_PORTAL + "/pages/relatorios/relatorioBoletoCondominioNaoRecebido/"
                 "relatorioBoletoCondominioNaoRecebido.xhtml")

API_BASE = (os.getenv("API_BASE") or f"http://127.0.0.1:{os.getenv('PORTA_SERVIDOR', '5000')}").rstrip("/")
TOKEN = os.getenv("TOKEN_SEGURANCA")
USUARIO = os.getenv("LELLO_USUARIO")
SENHA = os.getenv("LELLO_SENHA")

RAIZ = Path(__file__).resolve().parents[2]          # <sistema>\scripts\robos\este_arquivo -> <sistema>
PASTA = Path(os.getenv("LELLO_PASTA_DOWNLOADS") or (RAIZ / "downloads_base_lello"))
CANAL = os.getenv("LELLO_CANAL", "chrome")

TENTATIVAS = 3                       # portal fora do ar / lento: tenta de novo (menos no login e sem dados)
ESPERA_ENTRE_TENTATIVAS = int(os.getenv("LELLO_ESPERA_TENTATIVAS", "25"))   # s (x o número da tentativa)
TEMPO_CONSULTA = int(os.getenv("LELLO_TEMPO_CONSULTA", "240"))             # s esperando o resultado da consulta
TEMPO_DOWNLOAD = int(os.getenv("LELLO_TEMPO_DOWNLOAD", "300"))             # s esperando o Excel
FRACAO_MINIMA_NO_MES = 0.90          # pelo menos 90% das linhas têm que ser do mês pedido
GUARDAR_ARQUIVOS = 12
GUARDAR_FALHAS = 10

ICONE_EXCEL = 'img[title="Exportar resultado para Excel"], img[src*="excel.png"]'
FILTRO = "formFiltro"


SEM_RESULTADO = re.compile(r"nenhum|não (foi )?encontr|sem (registro|resultado|boleto)|0 registro", re.I)


class ErroLogin(Exception):
    recusado = False                                      # True = o próprio portal disse "usuário/senha inválidos"
    """Usuário/senha recusados ou portal não saiu da tela de login. Não insiste."""


class SemDados(Exception):
    """A consulta não trouxe nada: não adianta tentar de novo."""


class ErroPortal(Exception):
    """O portal respondeu de um jeito inesperado (vale tentar de novo)."""


_TRAVA_ARQUIVO = None


def bater_trava():
    """Mostra que o robô está vivo (a trava de um robô morto expira sozinha em poucos minutos)."""
    try:
        if _TRAVA_ARQUIVO is not None:
            os.utime(_TRAVA_ARQUIVO, None)
    except Exception:
        pass


def log(msg):
    bater_trava()
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


# =======================================================================
# SISTEMA (API)
# =======================================================================
def perguntar_mes_ao_sistema():
    """Mês de vencimento que o sistema está trabalhando: {'ano', 'mes', 'rotulo', 'origem'}."""
    try:
        r = requests.post(f"{API_BASE}/api/automacao/base_lello/plano", json={"token": TOKEN}, timeout=30)
    except requests.RequestException as e:
        raise ErroPortal(f"não consegui falar com o sistema em {API_BASE} ({e.__class__.__name__}). O servidor está ligado?")
    if r.status_code == 401:
        raise ErroPortal("o sistema recusou o token (TOKEN_SEGURANCA do .env diferente do servidor?)")
    try:
        dados = r.json()
    except ValueError:
        raise ErroPortal(f"resposta inesperada do sistema (HTTP {r.status_code})")
    if not dados.get("sucesso"):
        raise ErroPortal(f"o sistema não informou o mês: {dados.get('msg', r.status_code)}")
    return dados


def avisar_falha_ao_sistema(msg, ano=None, mes=None, origem=""):
    """Deixa a falha à vista na tela de Configurações (a importação com sucesso o próprio sistema registra)."""
    try:
        requests.post(f"{API_BASE}/api/automacao/base_lello/resultado", timeout=20, json={
            "token": TOKEN, "ok": False, "msg": str(msg)[:500],
            "mes": f"{ano:04d}-{mes:02d}" if ano and mes else "", "origem": origem})
    except Exception:
        pass


def enviar_ao_sistema(caminho, ano, mes, forcar, origem):
    """Manda o Excel para a mesma importação da tela de Configurações. Devolve o JSON do sistema."""
    with open(caminho, "rb") as f:
        try:
            r = requests.post(
                f"{API_BASE}/api/automacao/upload_base", timeout=900,
                data={"token": TOKEN, "mes_esperado": f"{ano:04d}-{mes:02d}", "origem": origem,
                      "forcar": "1" if forcar else "0"},
                files={"arquivo_base": (caminho.name, f, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
        except requests.RequestException as e:
            raise ErroPortal(f"não consegui enviar a planilha ao sistema ({e.__class__.__name__})")
    try:
        dados = r.json()
    except ValueError:
        raise ErroPortal(f"o sistema respondeu algo inesperado (HTTP {r.status_code}): {r.text[:160]!r}")
    return dados


# =======================================================================
# PORTAL
# =======================================================================
def intervalo_do_mes(ano, mes):
    return f"01/{mes:02d}/{ano:04d}", f"{calendar.monthrange(ano, mes)[1]:02d}/{mes:02d}/{ano:04d}"


def estar_no_login(page):
    return "/pages/login/" in page.url or page.locator('input[type="password"]#senha').count() > 0


def esperar_ajax(page, extra=0.5):
    """Espera o PrimeFaces terminar o que estiver pedindo ao servidor (e dá um respiro)."""
    try:
        page.wait_for_function(
            """() => {
                try { if (window.PrimeFaces && PrimeFaces.ajax && PrimeFaces.ajax.Queue
                         && PrimeFaces.ajax.Queue.isEmpty && !PrimeFaces.ajax.Queue.isEmpty()) return false; } catch (e) {}
                try { if (window.jQuery && jQuery.active > 0) return false; } catch (e) {}
                return true;
            }""", timeout=60000)
    except Exception:
        pass
    time.sleep(extra)


def texto_de_mensagem(page):
    """Mensagem de erro/aviso que o portal estiver mostrando (para explicar a falha)."""
    for seletor in (".ui-messages-error", ".ui-message-error", ".ui-growl-message", ".ui-messages-warn",
                    ".ui-datatable-empty-message", ".ui-dialog:visible .ui-dialog-content", ".ui-messages"):
        try:
            alvo = page.locator(seletor).first
            if alvo.count() and alvo.is_visible():
                t = re.sub(r"\s+", " ", alvo.inner_text()).strip()
                if t:
                    return t[:200]
        except Exception:
            continue
    return ""


def _marca_das_credenciais():
    return hashlib.sha256(f"{USUARIO}\0{SENHA}".encode("utf-8")).hexdigest()[:16]


def login_recusado_antes():
    """True se o portal já recusou ESTAS credenciais: não insistimos (5 tentativas por dia bloqueariam o usuário)."""
    try:
        return (PASTA / ".login_recusado").read_text().strip() == _marca_das_credenciais()
    except Exception:
        return False


def marcar_login_recusado():
    try:
        PASTA.mkdir(parents=True, exist_ok=True)
        (PASTA / ".login_recusado").write_text(_marca_das_credenciais())
    except Exception:
        pass


def limpar_login_recusado():
    try:
        (PASTA / ".login_recusado").unlink(missing_ok=True)
    except Exception:
        pass


def fazer_login(page):
    log("Entrando no portal da Lello...")
    page.goto(URL_LOGIN, wait_until="domcontentloaded")
    page.wait_for_selector("input#usuario", timeout=30000)
    page.locator("input#usuario").fill(USUARIO)
    page.locator("input#senha").fill(SENHA)
    page.locator("#formLogin button[type=submit], button:has-text('Entrar')").first.click()
    limite = time.time() + 45
    visto_em = None
    while time.time() < limite:
        if "/pages/login/" not in page.url:
            page.wait_for_load_state("domcontentloaded")
            log("Login feito.")
            limpar_login_recusado()
            return
        motivo = texto_de_mensagem(page)
        if motivo and visto_em is None:
            visto_em = time.time()                        # o portal mostrou um aviso: dá 2 s para ver se ainda entra
        if motivo and time.time() - visto_em > 2:
            erro = ErroLogin(f"o portal recusou o login: {motivo}")
            erro.recusado = True
            raise erro
        time.sleep(0.5)
        bater_trava()
    raise ErroLogin("o portal não entrou depois de 45 s (usuário ou senha do .env conferem? A senha pode ter vencido.)")


def abrir_relatorio(page):
    """Abre o relatório já logado; se o portal mandar para o login, entra e abre de novo."""
    page.goto(URL_RELATORIO, wait_until="domcontentloaded")
    if estar_no_login(page):
        fazer_login(page)
        page.goto(URL_RELATORIO, wait_until="domcontentloaded")
        if estar_no_login(page):
            raise ErroLogin("o portal voltou para a tela de login logo depois de entrar")
    page.wait_for_selector(f'[id="{FILTRO}"]', timeout=60000)
    log("Relatório 'Boletos Cond. não recebidos' aberto.")


def escolher_responsavel_lello(page):
    rotulo = page.locator(f'[id="{FILTRO}:responsavelPagamento_label"]')
    if rotulo.inner_text().strip().lower() == "lello":
        return
    raiz = page.locator(f'[id="{FILTRO}:responsavelPagamento"]')
    raiz.click()
    painel = page.locator(f'[id="{FILTRO}:responsavelPagamento_panel"]')
    painel.wait_for(state="visible", timeout=15000)
    item = painel.locator("li").filter(has_text=re.compile(r"^\s*Lello\s*$")).first
    item.click()
    esperar_ajax(page)
    if rotulo.inner_text().strip().lower() != "lello":
        raise ErroPortal("não consegui marcar Resp. Pagto = Lello")


def preencher_data(page, campo, valor):
    """Digita a data e tira o foco: é o que dispara o 'change' do PrimeFaces (que, no início, limpa o final)."""
    alvo = page.locator(f'[id="{FILTRO}:{campo}_input"]')
    for _ in range(3):
        alvo.click()
        alvo.fill("")
        alvo.fill(valor)
        alvo.press("Tab")
        esperar_ajax(page, 0.7)
        if alvo.input_value().strip() == valor:
            return
    raise ErroPortal(f"o portal não aceitou a data {valor} no campo {campo}")


def preparar_filtros(page, ano, mes):
    inicio, fim = intervalo_do_mes(ano, mes)
    log(f"Filtros: Resp. Pagto = Lello | Vencimento {inicio} a {fim}")
    escolher_responsavel_lello(page)
    preencher_data(page, "periodoInicial", inicio)      # o 'change' do início limpa o final: por isso vem antes
    preencher_data(page, "periodoFinal", fim)
    achados = (page.locator(f'[id="{FILTRO}:periodoInicial_input"]').input_value().strip(),
               page.locator(f'[id="{FILTRO}:periodoFinal_input"]').input_value().strip())
    if achados != (inicio, fim):
        raise ErroPortal(f"as datas não ficaram como pedido (ficaram {achados[0]} e {achados[1]})")


def consultar(page):
    log("Consultando...")
    page.locator("button:has-text('Consultar')").first.click()
    inicio = time.time()
    limite = inicio + TEMPO_CONSULTA
    while time.time() < limite:
        if page.locator(ICONE_EXCEL).first.is_visible():
            esperar_ajax(page, 0.8)
            log("Consulta pronta.")
            return
        if estar_no_login(page):
            raise ErroPortal("a sessão do portal caiu durante a consulta")
        if time.time() - inicio > 4:                      # dá tempo do portal responder; depois, lê o que ele disse
            aviso = texto_de_mensagem(page)
            if aviso and SEM_RESULTADO.search(aviso):
                raise SemDados(f"o portal respondeu: {aviso}")
            if aviso and page.locator(".ui-messages-error, .ui-message-error").first.is_visible():
                raise ErroPortal(f"o portal recusou a consulta: {aviso}")
        time.sleep(1)
        bater_trava()
    motivo = texto_de_mensagem(page)
    raise ErroPortal("a consulta não terminou a tempo" + (f" (portal diz: {motivo})" if motivo else ""))


def confirmar_pergunta_do_portal(page):
    """Se o portal perguntar algo antes de exportar (ex.: 'Deseja exportar?'), responde Sim."""
    try:
        botao = page.locator(".ui-dialog:visible button:has-text('Sim'), .ui-confirm-dialog:visible button:has-text('Sim')").first
        if botao.count() and botao.is_visible():
            botao.click()
            return True
    except Exception:
        pass
    return False


def exportar_excel(page, ano, mes):
    PASTA.mkdir(parents=True, exist_ok=True)
    baixados = []
    page.on("download", lambda d: baixados.append(d))
    log("Exportando para Excel...")
    page.locator(ICONE_EXCEL).first.click()
    limite = time.time() + TEMPO_DOWNLOAD
    while not baixados and time.time() < limite:
        time.sleep(1)
        bater_trava()
        confirmar_pergunta_do_portal(page)
    if not baixados:
        motivo = texto_de_mensagem(page)
        raise ErroPortal("o Excel não foi baixado" + (f" (portal diz: {motivo})" if motivo else ""))
    d = baixados[0]
    ext = Path(d.suggested_filename or "base.xlsx").suffix.lower() or ".xlsx"
    destino = PASTA / f"base_lello_{ano:04d}-{mes:02d}_{datetime.now():%Y%m%d_%H%M%S}{ext}"
    d.save_as(str(destino))
    if d.failure():
        raise ErroPortal(f"o download falhou: {d.failure()}")
    if not destino.exists() or destino.stat().st_size < 2000:
        raise ErroPortal("o Excel baixado veio vazio")
    log(f"Excel baixado: {destino.name} ({destino.stat().st_size / 1024:.0f} KB)")
    return destino


def guardar_evidencia(page):
    """Foto da tela + HTML da página: é o que falta para entender o que o portal mostrou."""
    try:
        pasta = PASTA / "falhas"
        pasta.mkdir(parents=True, exist_ok=True)
        base = pasta / f"falha_{datetime.now():%Y%m%d_%H%M%S}"
        page.screenshot(path=str(base) + ".png", full_page=True)
        Path(str(base) + ".html").write_text(page.content(), encoding="utf-8")
        log(f"Guardei a tela da falha em {base.name}.png/.html (pasta {pasta})")
        arquivos = sorted(pasta.glob("falha_*"), key=lambda p: p.stat().st_mtime, reverse=True)
        for velho in arquivos[GUARDAR_FALHAS * 2:]:
            velho.unlink(missing_ok=True)
    except Exception:
        pass


def abrir_navegador(p, visivel):
    opcoes = dict(headless=not visivel, args=["--disable-dev-shm-usage"])
    navegador = None
    if CANAL:
        try:
            navegador = p.chromium.launch(channel=CANAL, **opcoes)
        except Exception:
            log(f"Não achei o canal '{CANAL}'; usando o Chromium do Playwright.")
    if navegador is None:
        navegador = p.chromium.launch(**opcoes)
    contexto = navegador.new_context(accept_downloads=True, viewport={"width": 1366, "height": 900},
                                     locale="pt-BR", timezone_id="America/Sao_Paulo")
    contexto.set_default_timeout(60000)
    return navegador, contexto, contexto.new_page()


def baixar_do_portal(ano, mes, visivel, so_login=False):
    """Uma tentativa completa no portal: devolve o caminho do Excel baixado (ou None no --testar-login)."""
    with sync_playwright() as p:
        navegador, contexto, page = abrir_navegador(p, visivel)
        try:
            if so_login:
                fazer_login(page)
                return None
            abrir_relatorio(page)
            preparar_filtros(page, ano, mes)
            consultar(page)
            return exportar_excel(page, ano, mes)
        except Exception:
            guardar_evidencia(page)
            raise
        finally:
            try:
                contexto.close()
                navegador.close()
            except Exception:
                pass


# =======================================================================
# CONFERÊNCIA DO ARQUIVO
# =======================================================================
def _mes_do_texto(valor):
    if hasattr(valor, "year") and hasattr(valor, "month"):
        return (valor.year, valor.month)
    achou = re.search(r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})", str(valor or ""))
    if achou:
        return (int(achou.group(3)), int(achou.group(2)))
    achou = re.search(r"(\d{4})-(\d{2})-\d{2}", str(valor or ""))
    return (int(achou.group(1)), int(achou.group(2))) if achou else None


def conferir_planilha(caminho, ano, mes):
    """Lê o Excel do jeito que o sistema lê (2 linhas de cabeçalho a pular) e confere o essencial."""
    import pandas as pd
    try:
        df = pd.read_csv(caminho, skiprows=2) if str(caminho).lower().endswith(".csv") else pd.read_excel(caminho, skiprows=2)
    except Exception as e:
        raise ErroPortal(f"não consegui abrir o Excel baixado ({e.__class__.__name__}: {e})")
    df.columns = df.columns.astype(str).str.strip()
    faltam = [c for c in ("Vencimento", "Imovel", "Administradora") if c not in df.columns]
    if faltam:
        raise ErroPortal(f"o Excel não tem as colunas {faltam}. O layout do relatório mudou? Colunas lidas: {list(df.columns)[:8]}")
    df = df.dropna(subset=["Imovel"])
    if len(df) == 0:
        raise SemDados("a consulta não trouxe nenhum boleto para esse mês")
    meses = Counter(_mes_do_texto(v) for v in df["Vencimento"])
    do_mes = meses.get((ano, mes), 0)
    if do_mes / len(df) < FRACAO_MINIMA_NO_MES:
        principais = ", ".join(f"{k[1]:02d}/{k[0]}: {q}" for k, q in meses.most_common(3) if k)
        raise ErroPortal(f"o Excel não é de {mes:02d}/{ano} (linhas por mês: {principais}). Os filtros de data não pegaram?")
    return {"linhas": len(df), "administradoras": df["Administradora"].nunique()}


# =======================================================================
# EXECUÇÃO
# =======================================================================
class Trava:
    """Só um robô por vez (o painel já evita, mas alguém pode rodar na mão enquanto o agendador roda).
    O robô "bate" a trava a cada passo; se ela ficar 3 minutos parada, o robô dono morreu (ex.: botão Parar)."""
    def __init__(self):
        PASTA.mkdir(parents=True, exist_ok=True)
        self.caminho = PASTA / ".robo_base_lello.lock"
        self.ok = False

    def __enter__(self):
        global _TRAVA_ARQUIVO
        if self.caminho.exists() and time.time() - self.caminho.stat().st_mtime > 180:
            self.caminho.unlink(missing_ok=True)
        try:
            fd = os.open(str(self.caminho), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            self.ok = True
            _TRAVA_ARQUIVO = str(self.caminho)
        except FileExistsError:
            self.ok = False
        return self

    def __exit__(self, *a):
        if self.ok:
            self.caminho.unlink(missing_ok=True)


def limpar_antigos():
    try:
        arquivos = sorted([p for p in PASTA.glob("base_lello_*") if p.is_file()], key=lambda p: p.stat().st_mtime, reverse=True)
        for velho in arquivos[GUARDAR_ARQUIVOS:]:
            velho.unlink(missing_ok=True)
    except Exception:
        pass


def ler_argumentos():
    ap = argparse.ArgumentParser(description="Atualiza a base do sistema a partir do portal da Lello.")
    ap.add_argument("--visivel", action="store_true", help="abre a janela do Chrome")
    ap.add_argument("--so-baixar", action="store_true", help="baixa o Excel e para (não envia ao sistema)")
    ap.add_argument("--arquivo", help="usa este Excel em vez de baixar do portal")
    ap.add_argument("--testar-login", action="store_true", help="só testa usuário e senha do portal")
    ap.add_argument("--mes", help="mês de vencimento AAAA-MM (o sistema só aceita o mês que ele espera)")
    ap.add_argument("--forcar", action="store_true", help="envia mesmo se vier muito menos linhas que a base atual")
    ap.add_argument("--origem", default="robô", help="texto que aparece em Configurações como origem")
    return ap.parse_args()


def executar(args):
    faltando = [n for n, v in (("TOKEN_SEGURANCA", TOKEN),) if not v]
    if not args.arquivo:
        faltando += [n for n, v in (("LELLO_USUARIO", USUARIO), ("LELLO_SENHA", SENHA)) if not v]
    if faltando:
        log("🚨 FALHA: faltam no .env: " + ", ".join(faltando))
        return 1

    if not args.testar_login and not args.arquivo and login_recusado_antes():
        msg = ("o portal já recusou este usuário e senha; o robô não tenta de novo para não bloquear o acesso. "
               "Corrija LELLO_USUARIO/LELLO_SENHA no .env e reinicie o sistema (ou confira com --testar-login).")
        log(f"🚨 FALHA no login: {msg}")
        avisar_falha_ao_sistema(f"Login no portal: {msg}", None, None, args.origem)
        return 1

    if args.testar_login:
        try:
            baixar_do_portal(0, 0, args.visivel, so_login=True)
            log("✅ SUCESSO: usuário e senha do portal conferem.")
            return 0
        except ErroLogin as e:
            if e.recusado:
                marcar_login_recusado()
            log(f"🚨 FALHA: {e}")
            return 1

    ano = mes = None
    try:
        if args.mes:
            m = re.fullmatch(r"(\d{4})-(\d{2})", args.mes.strip())
            if not m or not 1 <= int(m.group(2)) <= 12:
                log("🚨 FALHA: use --mes AAAA-MM (ex.: 2026-12)")
                return 1
            ano, mes, origem_mes = int(m.group(1)), int(m.group(2)), "linha de comando"
        else:
            plano = perguntar_mes_ao_sistema()
            ano, mes, origem_mes = int(plano["ano"]), int(plano["mes"]), plano.get("origem", "")
        log(f"Mês de vencimento: {mes:02d}/{ano} ({origem_mes})")

        caminho = None
        if args.arquivo:
            caminho = Path(args.arquivo)
            if not caminho.exists():
                raise ErroPortal(f"arquivo não encontrado: {caminho}")
        else:
            ultimo = None
            for tentativa in range(1, TENTATIVAS + 1):
                try:
                    log(f"Tentativa {tentativa} de {TENTATIVAS}")
                    caminho = baixar_do_portal(ano, mes, args.visivel)
                    break
                except (ErroLogin, SemDados):
                    raise
                except Exception as e:
                    ultimo = e
                    log(f"⚠️ Tentativa {tentativa} não deu certo: {e}")
                    if tentativa < TENTATIVAS:
                        time.sleep(ESPERA_ENTRE_TENTATIVAS * tentativa)
            if caminho is None:
                raise ErroPortal(f"não consegui baixar a base depois de {TENTATIVAS} tentativas: {ultimo}")

        resumo = conferir_planilha(caminho, ano, mes)
        log(f"Arquivo conferido: {resumo['linhas']} boletos de {resumo['administradoras']} administradoras.")
        if args.so_baixar:
            log(f"✅ SUCESSO: arquivo guardado em {caminho} (não enviado, por causa do --so-baixar).")
            return 0

        resposta = enviar_ao_sistema(caminho, ano, mes, args.forcar, args.origem)
        if resposta.get("sucesso"):
            log("✅ SUCESSO: " + str(resposta.get("msg") or "base atualizada"))
            if resposta.get("aviso"):
                log("⚠️ " + str(resposta["aviso"]))
            return 0
        raise ErroPortal("o sistema não aceitou a planilha: " + str(resposta.get("msg") or resposta))
    except SemDados as e:
        log(f"🚨 FALHA: {e}")
        avisar_falha_ao_sistema(str(e), ano, mes, args.origem)
    except ErroLogin as e:
        if e.recusado:
            marcar_login_recusado()
        log(f"🚨 FALHA no login: {e}")
        avisar_falha_ao_sistema(f"Login no portal: {e}", ano, mes, args.origem)
    except ErroPortal as e:
        log(f"🚨 FALHA: {e}")
        # o sistema já registra quando é ELE quem recusa a planilha; o resto o robô avisa
        if "o sistema não aceitou" not in str(e):
            avisar_falha_ao_sistema(str(e), ano, mes, args.origem)
    except Exception as e:
        log(f"🚨 FALHA inesperada: {e.__class__.__name__}: {e}")
        avisar_falha_ao_sistema(f"{e.__class__.__name__}: {e}", ano, mes, args.origem)
    return 1


def main():
    args = ler_argumentos()
    with Trava() as trava:
        if not trava.ok:
            log("⚠️ Já tem outro robô da base rodando; este não vai duplicar.")
            return 0
        codigo = executar(args)
        limpar_antigos()
        return codigo


if __name__ == "__main__":
    sys.exit(main())