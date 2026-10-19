# -*- coding: utf-8 -*-
"""
COLETA DE BOLETOS - WEBWARE (Selenium) - ligada ao banco de dados e Raio-X RPA

Cadastro usado (cadastro_mestre_imoveis): login_acesso, senha_acesso, webware_empreendimento, webware_unidade
(nomes IDÊNTICOS aos do site; a comparação é exata, só ignora maiúsculas, acentos e zeros à esquerda).

Fluxo por conta (login):
  1. Login em https://webware.com.br/bin/administradora/
  2. O site cai em um de dois lugares:
       a) DIRETO  -> painel do condomínio (aInicioSkin.asp).
       b) LISTA   -> wmVinculoLista.asp (cards). Acha o card com o empreendimento (+ unidade).
  3. Abre "2ª via de boleto". Se houver lista de unidades, seleciona a do cadastro.
  4. Identifica os boletos em aberto e baixa o PDF como <nº do imóvel>.pdf.
  5. Marca o imóvel como COLETADO no banco de dados.
  6. Audita as falhas automaticamente no Raio-X RPA via /api/automacao/auditar.
"""
import argparse
import csv
import os
import re
import sqlite3
import sys
import time
import unicodedata
from datetime import datetime
from urllib.parse import urljoin

import requests
from selenium import webdriver
from selenium.common.exceptions import (NoSuchElementException, StaleElementReferenceException,
                                        TimeoutException, WebDriverException)
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import Select, WebDriverWait

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# =============================================================================
# CONFIGURAÇÃO
# =============================================================================
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if not os.path.exists(os.path.join(BASE_DIR, "banco.db")):
    BASE_DIR = os.getcwd()
CAMINHO_BANCO = os.path.join(BASE_DIR, "banco.db")

PASTA_BOLETOS = r"Z:\financeiro_imoveis\Condomínio\boleto__Cond_antes liax"
PASTA_ERROS_LOGIN = os.path.join(BASE_DIR, "screenshots", "erros_login")
PASTA_ERROS_BOLETO = os.path.join(BASE_DIR, "screenshots", "erros_boleto")
PASTA_LOGS = os.path.join(BASE_DIR, "logs")
PASTA_DOWNLOADS = os.path.join(BASE_DIR, "webware_downloads")

URL_LOGIN_WEBWARE = "https://webware.com.br/bin/administradora/"
URL_BOLETO_NOVO = "https://portalwm.webware.com.br/relatorioboleto/BoletoSegundaViaNovo"
API_BASE = os.getenv("API_BASE", "http://127.0.0.1:5000")
NOME_ROBO = "AUTOMAÇÃO WEBWARE"

EXIGIR_MES_DO_VENCIMENTO = True
VALIDAR_TITULO_NO_ACESSO_DIRETO = True
ENCERRADOS = "('PAGO', 'PAGO NA ADM', 'PAGO NA 275', 'COLETADO')"
TIPOS_UNIDADE = {"AP", "APART", "APTO", "APARTAMENTO", "CJ", "CONJ", "SL", "SALA", "LJ", "LOJA", "CS", "CASA"}

XP_2VIA = "//div[contains(@onclick, 'u_2via.asp')] | //a[contains(@href, 'u_2via.asp')]"
XP_CONSULTAR = "//button[contains(translate(normalize-space(.), 'consultar', 'CONSULTAR'), 'CONSULTAR')]"

JS_ESTADO = r"""
    const t = document.body ? document.body.innerText : '';
    const vis = e => e.offsetParent !== null;
    return {
        vselect: Array.from(document.querySelectorAll('.v-select')).some(vis) || /selecione\s+unidade/i.test(t),
        nativo: Array.from(document.querySelectorAll('select')).some(vis),
        gerar: /gerar\s*boleto/i.test(t),
        vazio: /nenhum\s+boleto|n[ãa]o\s+(h[áa]|existem|foram\s+encontrad)\w*\s+(boleto|cota|d[ée]bito)|sem\s+boletos/i.test(t)
    };"""

JS_VS_ATUAL = "var s = document.querySelector('.v-select__selection--comma'); return s ? s.textContent.trim() : '';"

JS_VS_OPCOES = r"""
    const raiz = document.querySelector('.menuable__content__active') || document;
    return Array.from(raiz.querySelectorAll('.v-list-item'))
        .filter(e => e.offsetParent !== null)
        .map(e => [e, (e.textContent || '').trim()]);"""

JS_MOUSE = """
    const el = arguments[0];
    for (const tipo of ['mousedown', 'mouseup', 'click']) {
        el.dispatchEvent(new MouseEvent(tipo, {bubbles: true, cancelable: true, view: window}));
    }"""

JS_CARDS = r"""
    const re = /gerar\s*boleto/i, vis = e => e.offsetParent !== null;
    const sel = 'button, a, [role="button"]';
    const bots = raiz => {
        const l = Array.from(raiz.querySelectorAll(sel)).filter(e => re.test(e.textContent || '') && vis(e));
        return l.filter(e => !l.some(o => o !== e && e.contains(o)));
    };
    return bots(document).map(btn => {
        let card = btn;
        while (card.parentElement && card.tagName !== 'BODY') {
            const p = card.parentElement;
            if (bots(p).length > 1) break;
            card = p;
        }
        return [btn, (card.innerText || '').replace(/\s+/g, ' ').trim(), btn.getAttribute('href') || ''];
    });"""


# =============================================================================
# UTILIDADES
# =============================================================================
class FalhaColeta(Exception):
    def __init__(self, tipo, detalhe=""):
        super().__init__(detalhe)
        self.tipo = tipo
        self.detalhe = detalhe


def log(msg):
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def sem_acento(txt):
    t = unicodedata.normalize("NFKD", str(txt or ""))
    return "".join(c for c in t if not unicodedata.combining(c))


def norm_nome(txt):
    return " ".join(re.sub(r"[^A-Z0-9]+", " ", sem_acento(txt).upper()).split())


def _tokens(txt):
    saida = []
    for p in re.split(r"[^A-Z0-9]+", sem_acento(txt).upper()):
        if p:
            saida.append((p.lstrip("0") or "0") if p.isdigit() else p)
    return saida


def chave_unidade(txt):
    return "/".join(_tokens(txt))


def chaves_no_texto(texto):
    achados = re.findall(r"[A-Za-z0-9]+(?:\s*/\s*[A-Za-z0-9]+)+", sem_acento(texto))
    return {chave_unidade(a) for a in achados}


def unidade_confere(alvo, texto):
    ka = chave_unidade(alvo)
    if not ka:
        return False
    if "/" in ka:
        return ka in chaves_no_texto(texto)
    return set(ka.split("/")).issubset(set(_tokens(texto)))


def unidade_confere_frouxo(alvo, texto):
    uteis = [t for t in _tokens(alvo) if t not in TIPOS_UNIDADE and t != "0"]
    return bool(uteis) and set(uteis).issubset(set(_tokens(texto)))


def mes_ano(data_str):
    m = re.search(r"(\d{2})/(\d{2})/(\d{4})", str(data_str or ""))
    return (int(m.group(2)), int(m.group(3))) if m else None


def data_ord(venc):
    try:
        return datetime.strptime(venc, "%d/%m/%Y")
    except ValueError:
        return datetime.min


def aguardar_pronto(driver, timeout=30):
    try:
        WebDriverWait(driver, timeout).until(lambda d: d.execute_script("return document.readyState") == "complete")
        time.sleep(1.0)
    except Exception:
        pass


def diagnosticar(driver, nome, html=True):
    try:
        pasta = os.path.join(PASTA_ERROS_BOLETO, datetime.now().strftime("%d-%m-%Y"))
        os.makedirs(pasta, exist_ok=True)
        base = os.path.join(pasta, re.sub(r"[^A-Za-z0-9_]+", "_", nome)[:70])
        driver.save_screenshot(base + ".png")
        if html:
            with open(base + ".html", "w", encoding="utf-8") as f:
                f.write(driver.page_source)
    except Exception:
        pass


def registrar_erro(imovel, login, emp, unid, tipo, detalhe):
    """Linha no CSV de falhas e notificação imediata para o Raio-X RPA."""
    os.makedirs(PASTA_LOGS, exist_ok=True)
    arq = os.path.join(PASTA_LOGS, "relatorio_falhas_webware.csv")
    novo = not os.path.exists(arq)
    try:
        with open(arq, "a", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f, delimiter=";")
            if novo:
                w.writerow(["Data/Hora", "Nº Imóvel", "Login Utilizado", "Empreendimento Buscado",
                            "Unidade Buscada", "Status/Erro", "Detalhe do Erro"])
            w.writerow([datetime.now().strftime("%d/%m/%Y %H:%M:%S"), imovel, login, emp, unid, tipo, detalhe])
    except OSError as e:
        log(f"   ! não consegui escrever no relatório de falhas: {e}")

    try:
        requests.post(f"{API_BASE}/api/automacao/auditar", json={
            "robo": NOME_ROBO,
            "imovel": str(imovel),
            "status": tipo,
            "detalhe": f"{detalhe} (Empreendimento: '{emp}' | Unidade: '{unid}')"
        }, timeout=5)
    except Exception:
        pass


# =============================================================================
# BANCO
# =============================================================================
def buscar_pendencias(filtro_imoveis, filtro_login):
    con = sqlite3.connect(CAMINHO_BANCO)
    con.row_factory = sqlite3.Row
    try:
        linhas = con.execute(f"""
            SELECT MIN(i.id) AS id, i.imovel, MAX(i.vencimento) AS vencimento,
                   c.login_acesso, c.senha_acesso, c.webware_empreendimento, c.webware_unidade
            FROM imoveis i
            JOIN cadastro_mestre_imoveis c ON LTRIM(c.imovel, '0') = LTRIM(i.imovel, '0')
            WHERE i.ativo = 1
              AND UPPER(TRIM(c.portal)) = 'WEBWARE'
              AND COALESCE(TRIM(c.login_acesso), '') <> ''
              AND (i.status_interno IS NULL OR i.status_interno NOT IN {ENCERRADOS})
              AND NOT EXISTS (SELECT 1 FROM imoveis x WHERE x.ativo = 1
                              AND LTRIM(x.imovel, '0') = LTRIM(i.imovel, '0') AND x.status_interno IN {ENCERRADOS})
            GROUP BY i.imovel
            ORDER BY c.login_acesso, c.webware_empreendimento, i.imovel
        """).fetchall()
    finally:
        con.close()
    tarefas = []
    for r in linhas:
        t = {k: (str(r[k]).strip() if r[k] is not None else "") for k in r.keys()}
        if filtro_imoveis and t["imovel"] not in filtro_imoveis:
            continue
        if filtro_login and t["login_acesso"].lower() != filtro_login.lower():
            continue
        tarefas.append(t)
    return tarefas


def marcar_coletado(imovel, observacao):
    con = sqlite3.connect(CAMINHO_BANCO)
    con.row_factory = sqlite3.Row
    try:
        caso = con.execute("SELECT id, observacao FROM imoveis WHERE LTRIM(imovel, '0') = LTRIM(?, '0') "
                           "AND ativo = 1 LIMIT 1", (imovel,)).fetchone()
        if not caso:
            return False
        antiga = caso["observacao"] or ""
        obs = antiga if (not observacao or observacao in antiga) else (f"{antiga} | {observacao}" if antiga else observacao)
        agora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        con.execute("UPDATE imoveis SET status_interno = 'COLETADO', observacao = ? WHERE id = ?", (obs, caso["id"]))
        con.execute("DELETE FROM produtividade WHERE imovel_id = ? AND acao = 'COLETOU BOLETO'", (caso["id"],))
        con.execute("INSERT INTO historico_observacoes (imovel_id, data_hora, usuario, texto) VALUES (?, ?, ?, ?)",
                    (caso["id"], agora, NOME_ROBO, "Processado via automação. Status atribuído: COLETADO"))
        con.execute("INSERT INTO produtividade (usuario, acao, imovel_id, data_hora) VALUES (?, ?, ?, ?)",
                    (NOME_ROBO, "COLETOU BOLETO", caso["id"], agora))
        con.commit()
        return True
    except sqlite3.Error as e:
        log(f"   ! erro ao atualizar o banco: {e}")
        return False
    finally:
        con.close()


# =============================================================================
# NAVEGADOR / LOGIN
# =============================================================================
def criar_driver(headless):
    os.makedirs(PASTA_DOWNLOADS, exist_ok=True)
    op = webdriver.ChromeOptions()
    if headless:
        op.add_argument("--headless=new")
    op.add_argument("--start-maximized")
    op.add_argument("--window-size=1366,900")
    op.add_argument("--lang=pt-BR")
    op.add_experimental_option("prefs", {
        "download.default_directory": os.path.abspath(PASTA_DOWNLOADS),
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
        "plugins.always_open_pdf_externally": True,
    })
    driver = webdriver.Chrome(options=op)
    driver.set_page_load_timeout(90)
    driver.set_script_timeout(60)
    return driver


def realizar_login(driver, usuario, senha):
    try:
        driver.delete_all_cookies()
        driver.get(URL_LOGIN_WEBWARE)
        wait = WebDriverWait(driver, 15)
        u = wait.until(EC.presence_of_element_located((By.NAME, "mem")))
        u.clear()
        u.send_keys(usuario)
        p = driver.find_element(By.NAME, "pass")
        p.clear()
        p.send_keys(senha)
        url_antes = driver.current_url
        driver.find_element(By.XPATH, "//input[@value='Entrar']").click()
        try:
            WebDriverWait(driver, 15).until(EC.url_changes(url_antes))
        except TimeoutException:
            pass
        aguardar_pronto(driver)
        url = driver.current_url.lower()
        if "login.asp" in url or "erro" in url or url.rstrip("/") == url_antes.lower().rstrip("/"):
            os.makedirs(PASTA_ERROS_LOGIN, exist_ok=True)
            driver.save_screenshot(os.path.join(PASTA_ERROS_LOGIN, f"{re.sub(r'[^A-Za-z0-9_.-]', '_', usuario)}.png"))
            return False
        return True
    except Exception as e:
        log(f"   ! erro técnico no login: {type(e).__name__}")
        return False


def detectar_tela(driver, timeout=25):
    fim = time.time() + timeout
    while time.time() < fim:
        url = driver.current_url.lower()
        if "wmvinculolista.asp" in url:
            return "LISTA"
        if "ainicioskin.asp" in url or driver.find_elements(By.XPATH, XP_2VIA):
            return "DIRETO"
        if driver.find_elements(By.CSS_SELECTOR, "div.box a.bt"):
            return "LISTA"
        time.sleep(0.5)
    return "DESCONHECIDA"


# =============================================================================
# LISTA DE VÍNCULOS
# =============================================================================
def clicar_vinculo(driver, emp, unid):
    try:
        WebDriverWait(driver, 20).until(EC.presence_of_all_elements_located((By.CSS_SELECTOR, "div.box")))
    except TimeoutException:
        return False
    alvo = norm_nome(emp)
    do_emp = []
    for card in driver.find_elements(By.CSS_SELECTOR, "div.box"):
        try:
            titulo = card.find_element(By.CLASS_NAME, "txt-titulo").text
            corpo = card.find_element(By.CLASS_NAME, "card-body").text
        except (NoSuchElementException, StaleElementReferenceException):
            continue
        if norm_nome(titulo) == alvo:
            do_emp.append((card, corpo))

    achados = [c for c, corpo in do_emp if unidade_confere(unid, corpo)]
    if not achados:
        frouxos = [c for c, corpo in do_emp if unidade_confere_frouxo(unid, corpo)]
        if len(frouxos) == 1:
            log("   AVISO: unidade casada pelo plano B (sem tipo/bloco 0); confira no cadastro.")
            achados = frouxos
    if not achados:
        return False
    if len(achados) > 1:
        log(f"   AVISO: {len(achados)} cards iguais; usando o primeiro.")
    botao = achados[0].find_element(By.CSS_SELECTOR, "a.bt")
    driver.execute_script("arguments[0].click();", botao)
    aguardar_pronto(driver)
    return True


# =============================================================================
# TELA DE BOLETOS
# =============================================================================
def abrir_tela_boletos(driver):
    clicou = False
    for el in driver.find_elements(By.XPATH, XP_2VIA):
        try:
            if el.is_displayed():
                driver.execute_script("arguments[0].click();", el)
                clicou = True
                break
        except WebDriverException:
            continue
    if not clicou:
        driver.get(urljoin(driver.current_url, "/bin/aplic/u_2via.asp"))
    aguardar_pronto(driver)

    src = ""
    fim = time.time() + 20
    while time.time() < fim and not src:
        for fr in driver.find_elements(By.CSS_SELECTOR, "iframe"):
            s = fr.get_attribute("src") or ""
            if "webware.com.br" in s.lower():
                src = s
                try:
                    driver.switch_to.frame(fr)
                    WebDriverWait(driver, 15).until(lambda d: d.execute_script("return document.readyState") == "complete")
                except Exception:
                    pass
                finally:
                    driver.switch_to.default_content()
                break
        if not src:
            time.sleep(0.5)

    for destino in ([URL_BOLETO_NOVO] + ([src] if src else [])):
        try:
            driver.get(destino)
        except TimeoutException:
            pass
        aguardar_pronto(driver)
        modo = esperar_tela(driver)
        if modo:
            return modo
    diagnosticar(driver, "tela_boletos_nao_carregou")
    raise FalhaColeta("Falha de Navegação", "A tela de 2ª via não carregou (ver screenshots/erros_boleto)")


def esperar_tela(driver, timeout=25):
    fim = time.time() + timeout
    while time.time() < fim:
        try:
            e = driver.execute_script(JS_ESTADO)
        except WebDriverException:
            e = {}
        if e.get("vselect"):
            return "VSELECT"
        if e.get("nativo"):
            return "NATIVO"
        if e.get("gerar") or e.get("vazio"):
            return "DIRETO"
        time.sleep(0.7)
    return None


def abrir_vselect(driver):
    gatilhos = [g for g in driver.find_elements(By.CSS_SELECTOR, ".v-select .v-input__slot") if g.is_displayed()]
    if not gatilhos:
        gatilhos = [g for g in driver.find_elements(By.CSS_SELECTOR, ".v-select") if g.is_displayed()]
    if not gatilhos:
        raise FalhaColeta("Falha de Navegação", "Lista de unidades (v-select) não está visível")
    g = gatilhos[0]
    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", g)
    tentativas = [lambda: g.click(),
                  lambda: driver.execute_script(JS_MOUSE, g),
                  lambda: ActionChains(driver).move_to_element(g).click().perform()]
    for tentar in tentativas:
        try:
            tentar()
        except WebDriverException:
            continue
        fim = time.time() + 5
        while time.time() < fim:
            ops = driver.execute_script(JS_VS_OPCOES)
            if ops:
                return ops
            time.sleep(0.3)
    raise FalhaColeta("Falha de Navegação", "A lista de unidades não abriu")


def selecionar_unidade_vselect(driver, unid):
    alvo = chave_unidade(unid)
    atual = driver.execute_script(JS_VS_ATUAL)
    if atual and chave_unidade(atual) == alvo:
        return
    opcoes = abrir_vselect(driver)
    achada = next(((el, txt) for el, txt in opcoes if chave_unidade(txt) == alvo), None)
    if not achada:
        vistas = [t for _, t in opcoes][:15]
        driver.find_element(By.TAG_NAME, "body").send_keys("\ue00c")
        diagnosticar(driver, f"unidade_nao_achada_{alvo.replace('/', '_')}")
        raise FalhaColeta("Unidade Não Encontrada",
                          f"'{unid}' não está na lista do site ({len(opcoes)} opções; ex.: {vistas})")
    el, texto = achada
    driver.execute_script("arguments[0].scrollIntoView({block: 'nearest'});", el)
    for tentar in (lambda: el.click(), lambda: driver.execute_script("arguments[0].click();", el)):
        try:
            tentar()
        except WebDriverException:
            continue
        fim = time.time() + 5
        while time.time() < fim:
            if chave_unidade(driver.execute_script(JS_VS_ATUAL)) == alvo:
                return
            time.sleep(0.3)
    raise FalhaColeta("Unidade Não Encontrada", f"Selecionei '{texto}' mas o site não confirmou a seleção")


def selecionar_unidade_nativa(driver, unid):
    alvo = chave_unidade(unid)
    sel = Select(next(s for s in driver.find_elements(By.TAG_NAME, "select") if s.is_displayed()))
    for i, op in enumerate(sel.options):
        if chave_unidade(op.text) == alvo:
            sel.select_by_index(i)
            return
    vistas = [o.text for o in sel.options][:15]
    raise FalhaColeta("Unidade Não Encontrada", f"'{unid}' não está na lista do site (ex.: {vistas})")


def clicar_consultar(driver):
    fim = time.time() + 10
    botao = None
    while time.time() < fim:
        for b in driver.find_elements(By.XPATH, XP_CONSULTAR):
            try:
                if b.is_displayed() and b.is_enabled() and b.get_attribute("disabled") is None:
                    botao = b
                    break
            except WebDriverException:
                continue
        if botao:
            break
        time.sleep(0.4)
    if not botao:
        diagnosticar(driver, "consultar_indisponivel")
        raise FalhaColeta("Falha de Navegação", "Botão Consultar não ficou disponível")
    driver.execute_script("arguments[0].click();", botao)


def listar_cards(driver):
    cards = []
    for el, texto, href in driver.execute_script(JS_CARDS):
        m = re.search(r"vencimento\D{0,10}(\d{2}/\d{2}/\d{4})", texto, re.IGNORECASE) or re.search(r"(\d{2}/\d{2}/\d{4})", texto)
        cards.append({"el": el, "texto": texto, "href": href, "venc": m.group(1) if m else ""})
    return cards


def aguardar_cards(driver, timeout=45):
    fim = time.time() + timeout
    while time.time() < fim:
        cards = listar_cards(driver)
        if cards:
            time.sleep(1.0)
            return listar_cards(driver) or cards
        try:
            if driver.execute_script(JS_ESTADO).get("vazio"):
                return []
        except WebDriverException:
            pass
        time.sleep(0.7)
    return []


def conferir_unidade_exibida(driver, unid):
    texto = driver.execute_script("return document.body ? document.body.innerText : '';") or ""
    m = re.search(r"Unidade:\s*([^\n\r]+)", texto)
    if m and "/" in m.group(1) and chave_unidade(m.group(1)) != chave_unidade(unid):
        raise FalhaColeta("Unidade Divergente", f"O site mostra '{m.group(1).strip()}' e o cadastro é '{unid}'")


def escolher_boleto(cards, venc_base):
    candidatos = cards
    alvo = mes_ano(venc_base)
    if EXIGIR_MES_DO_VENCIMENTO and alvo and any(c["venc"] for c in cards):
        candidatos = [c for c in cards if mes_ano(c["venc"]) == alvo]
        if not candidatos:
            datas = ", ".join(c["venc"] or "sem data" for c in cards)
            raise FalhaColeta("Sem Boleto do Mês",
                              f"Boletos no site: {datas}; nenhum do mês {alvo[0]:02d}/{alvo[1]} (base: {venc_base})")
    return max(candidatos, key=lambda c: data_ord(c["venc"]))


# =============================================================================
# DOWNLOAD
# =============================================================================
def _pdf_valido(dados):
    return bool(dados) and dados[:4] == b"%PDF"


def baixar_via_requests(driver, url):
    try:
        s = requests.Session()
        for c in driver.get_cookies():
            s.cookies.set(c["name"], c["value"])
        s.headers.update({"User-Agent": driver.execute_script("return navigator.userAgent"),
                          "Referer": driver.current_url})
        r = s.get(url, allow_redirects=True, timeout=60)
        if r.ok and _pdf_valido(r.content):
            return r.content
    except (requests.RequestException, WebDriverException):
        pass
    return None


def _limpar_downloads():
    for f in os.listdir(PASTA_DOWNLOADS):
        try:
            os.remove(os.path.join(PASTA_DOWNLOADS, f))
        except OSError:
            pass


def _pdf_da_pasta():
    arqs = os.listdir(PASTA_DOWNLOADS)
    pdfs = [f for f in arqs if f.lower().endswith(".pdf")]
    if not pdfs or any(f.lower().endswith((".crdownload", ".tmp")) for f in arqs):
        return None
    caminho = os.path.join(PASTA_DOWNLOADS, pdfs[0])
    tam = os.path.getsize(caminho)
    time.sleep(1.0)
    if os.path.getsize(caminho) != tam:
        return None
    with open(caminho, "rb") as f:
        dados = f.read()
    os.remove(caminho)
    return dados if _pdf_valido(dados) else None


def baixar_boleto(driver, card):
    if card.get("href") and card["href"].lower().startswith("http"):
        dados = baixar_via_requests(driver, card["href"])
        if dados:
            return dados

    _limpar_downloads()
    aba = driver.current_window_handle
    antes = set(driver.window_handles)
    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", card["el"])
    try:
        card["el"].click()
    except WebDriverException:
        driver.execute_script("arguments[0].click();", card["el"])

    fim = time.time() + 60
    try:
        while time.time() < fim:
            dados = _pdf_da_pasta()
            if dados:
                return dados
            for h in set(driver.window_handles) - antes:
                try:
                    driver.switch_to.window(h)
                    url = driver.current_url
                    if url and url.lower().startswith("http"):
                        dados = baixar_via_requests(driver, url)
                        if dados:
                            driver.close()
                            return dados
                except WebDriverException:
                    pass
            time.sleep(0.5)
    finally:
        for h in set(driver.window_handles) - antes:
            try:
                driver.switch_to.window(h)
                driver.close()
            except WebDriverException:
                pass
        try:
            driver.switch_to.window(aba)
        except WebDriverException:
            pass
    diagnosticar(driver, "download_falhou")
    raise FalhaColeta("Falha no Download", "Cliquei em Gerar Boleto mas nenhum PDF chegou em 60s")


def salvar_pdf(dados, destino, imovel):
    nome = f"{imovel}.pdf"
    caminho = os.path.join(destino, nome)
    tmp = caminho + ".tmp"
    with open(tmp, "wb") as f:
        f.write(dados)
    os.replace(tmp, caminho)
    return caminho


# =============================================================================
# UM IMÓVEL
# =============================================================================
def coletar_imovel(driver, t, destino):
    modo = abrir_tela_boletos(driver)
    log(f"   Tela de boletos: {'com lista de unidades' if modo in ('VSELECT', 'NATIVO') else 'direta'}")

    if modo == "VSELECT":
        selecionar_unidade_vselect(driver, t["webware_unidade"])
        clicar_consultar(driver)
    elif modo == "NATIVO":
        selecionar_unidade_nativa(driver, t["webware_unidade"])
        clicar_consultar(driver)

    cards = aguardar_cards(driver)
    if not cards:
        diagnosticar(driver, f"sem_boleto_{t['imovel']}")
        raise FalhaColeta("Sem Boletos", "Nenhum boleto em aberto na tela de 2ª via")
    conferir_unidade_exibida(driver, t["webware_unidade"])

    escolhido = escolher_boleto(cards, t["vencimento"])
    dados = baixar_boleto(driver, escolhido)
    caminho = salvar_pdf(dados, destino, t["imovel"])
    log(f"   Boleto salvo: {caminho} (venc {escolhido['venc'] or '?'})")
    return escolhido["venc"], caminho


def processar(driver, t, destino, marcar, contagem):
    try:
        venc, _ = coletar_imovel(driver, t, destino)
        ok = marcar_coletado(t["imovel"], f"Webware: boleto venc {venc or '?'} baixado") if marcar else None
        if marcar and not ok:
            registrar_erro(t["imovel"], t["login_acesso"], t["webware_empreendimento"], t["webware_unidade"],
                           "Erro ao Marcar", "PDF salvo, mas não consegui marcar COLETADO no banco")
        contagem["BAIXADO"] = contagem.get("BAIXADO", 0) + 1
        log(f"   => BAIXADO" + (" e marcado COLETADO" if ok else (" (NÃO marcado)" if marcar else " (sem marcar)")))
    except FalhaColeta as e:
        diagnosticar(driver, f"falha_{t['imovel']}")
        registrar_erro(t["imovel"], t["login_acesso"], t["webware_empreendimento"], t["webware_unidade"], e.tipo, e.detalhe)
        contagem[e.tipo] = contagem.get(e.tipo, 0) + 1
        log(f"   => {e.tipo}: {e.detalhe}")
    except WebDriverException as e:
        diagnosticar(driver, f"erro_{t['imovel']}")
        msg = (str(e).strip().splitlines() or [""])[0][:200]
        registrar_erro(t["imovel"], t["login_acesso"], t["webware_empreendimento"], t["webware_unidade"], "Erro", msg)
        contagem["Erro"] = contagem.get("Erro", 0) + 1
        log(f"   => Erro: {msg}")


def preparar_destino(caminho):
    try:
        os.makedirs(caminho, exist_ok=True)
        teste = os.path.join(caminho, ".teste_escrita")
        open(teste, "w").close()
        os.remove(teste)
        return caminho
    except OSError as e:
        alt = os.path.join(BASE_DIR, "boletos_teste")
        os.makedirs(alt, exist_ok=True)
        log(f"AVISO: não consegui usar '{caminho}' ({e}). Salvando em: {alt}")
        return alt


# =============================================================================
# PRINCIPAL
# =============================================================================
def main():
    ap = argparse.ArgumentParser(description="Coleta de boletos Webware")
    ap.add_argument("--imoveis", default="", help="lista separada por vírgula para rodar só esses")
    ap.add_argument("--login", default="", help="roda só os imóveis desse login")
    ap.add_argument("--limite", type=int, default=0, help="processa só os N primeiros (teste)")
    ap.add_argument("--sem-marcar", action="store_true", help="baixa o PDF mas NÃO marca COLETADO")
    ap.add_argument("--headless", action="store_true", help="Chrome invisível")
    ap.add_argument("--destino", default=PASTA_BOLETOS)
    args = ap.parse_args()

    filtro = {x.strip() for x in args.imoveis.split(",") if x.strip()}
    tarefas = buscar_pendencias(filtro, args.login.strip())
    marcar = not args.sem_marcar
    contagem = {}

    validas = []
    for t in tarefas:
        if not t["webware_empreendimento"] or not t["webware_unidade"]:
            registrar_erro(t["imovel"], t["login_acesso"], t["webware_empreendimento"], t["webware_unidade"],
                           "Faltam Dados", "Cadastro sem 'Empreendimento (site)' ou 'Unidade (site)' do Webware")
            contagem["Faltam Dados"] = contagem.get("Faltam Dados", 0) + 1
        else:
            validas.append(t)
    if args.limite:
        validas = validas[:args.limite]
    if not validas:
        log("Nenhuma pendência da WEBWARE para processar." + (f" ({contagem.get('Faltam Dados', 0)} sem dados)" if contagem else ""))
        return

    contas = {}
    for t in validas:
        contas.setdefault((t["login_acesso"], t["senha_acesso"]), []).append(t)
    log(f"{len(validas)} imóveis em {len(contas)} contas | marcar COLETADO: {'SIM' if marcar else 'NÃO'}")

    destino = preparar_destino(args.destino)
    log(f"Boletos serão salvos em: {destino}")
    driver = criar_driver(args.headless)
    try:
        for (usuario, senha), lista in contas.items():
            log(f"\n=== Conta {usuario} ({len(lista)} imóveis) ===")
            if not realizar_login(driver, usuario, senha):
                log("   ! falha de login")
                for t in lista:
                    registrar_erro(t["imovel"], usuario, t["webware_empreendimento"], t["webware_unidade"],
                                   "Falha de Login", "Credenciais incorretas ou acesso bloqueado")
                contagem["Falha de Login"] = contagem.get("Falha de Login", 0) + len(lista)
                continue

            tela = detectar_tela(driver)
            url_inicio = driver.current_url
            log(f"   Entrada: {'direto no painel do condomínio' if tela == 'DIRETO' else 'lista de vínculos' if tela == 'LISTA' else 'tela desconhecida'}")

            if tela == "DESCONHECIDA":
                diagnosticar(driver, f"pagina_desconhecida_{usuario}")
                for t in lista:
                    registrar_erro(t["imovel"], usuario, t["webware_empreendimento"], t["webware_unidade"],
                                   "Página Desconhecida", f"Login levou a uma página fora do padrão: {url_inicio}")
                contagem["Página Desconhecida"] = contagem.get("Página Desconhecida", 0) + len(lista)
                continue

            for n, t in enumerate(lista, start=1):
                log(f"[{n}/{len(lista)}] Imóvel {t['imovel']} | {t['webware_empreendimento']} | {t['webware_unidade']}")
                try:
                    if n > 1:
                        driver.get(url_inicio)
                        aguardar_pronto(driver)

                    if tela == "LISTA":
                        if not clicar_vinculo(driver, t["webware_empreendimento"], t["webware_unidade"]):
                            diagnosticar(driver, f"vinculo_nao_achado_{t['imovel']}")
                            registrar_erro(t["imovel"], usuario, t["webware_empreendimento"], t["webware_unidade"],
                                           "Condomínio Não Encontrado",
                                           "Nenhum card com este empreendimento (idêntico) e esta unidade na lista de vínculos")
                            contagem["Condomínio Não Encontrado"] = contagem.get("Condomínio Não Encontrado", 0) + 1
                            log("   => Condomínio Não Encontrado")
                            continue
                    elif VALIDAR_TITULO_NO_ACESSO_DIRETO:
                        titulo, emp = norm_nome(driver.title), norm_nome(t["webware_empreendimento"])
                        if titulo and emp and emp not in titulo and titulo not in emp:
                            registrar_erro(t["imovel"], usuario, t["webware_empreendimento"], t["webware_unidade"],
                                           "Condomínio Divergente", f"O acesso direto abriu '{driver.title}'")
                            contagem["Condomínio Divergente"] = contagem.get("Condomínio Divergente", 0) + 1
                            log(f"   => Condomínio Divergente: site mostra '{driver.title}'")
                            continue

                    processar(driver, t, destino, marcar, contagem)
                except WebDriverException as e:
                    registrar_erro(t["imovel"], usuario, t["webware_empreendimento"], t["webware_unidade"],
                                   "Erro", (str(e).strip().splitlines() or [""])[0][:200])
                    contagem["Erro"] = contagem.get("Erro", 0) + 1
                    log("   => Erro de navegador")
    finally:
        try:
            driver.quit()
        except Exception:
            pass
    log("\nRESUMO: " + " | ".join(f"{k}: {v}" for k, v in sorted(contagem.items())))
    log(f"Falhas em: {os.path.join(PASTA_LOGS, 'relatorio_falhas_webware.csv')}")


if __name__ == "__main__":
    main()