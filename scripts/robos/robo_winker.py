# -*- coding: utf-8 -*-
"""
COLETA DE BOLETOS - WINKER (Selenium) - ligada ao banco de dados e Raio-X RPA

De onde vêm os imóveis:
  - PADRÃO: do sistema (API do Flask), só os PENDENTES da Winker.
  - TESTE: --planilha arquivo.xlsx

Fluxo por imóvel:
  1. Login único na Winker.
  2. Mantém a guia principal estática em escolherPortal (sem recarregar a cada coleta).
  3. Abre o empreendimento em NOVA GUIA via window.open (não navega a guia principal).
  4. Na nova guia: acessa Boletos, aplica os filtros (Divisão/Unidade) e baixa o PDF.
  5. Fecha a nova guia e volta o foco para a lista de empreendimentos intacta.
  6. Marca o imóvel como COLETADO no sistema (/api/automacao/coletar).
  7. Audita qualquer sucesso ou falha na rota /api/automacao/auditar (Raio-X RPA).
"""
import argparse
import base64
import csv
import datetime as dt
import io
import os
import re
import sys
import time
import unicodedata

import pandas as pd
import requests
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass
from selenium import webdriver
from selenium.common.exceptions import (ElementClickInterceptedException, StaleElementReferenceException,
                                        TimeoutException, WebDriverException)
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# =============================================================================
# CONFIGURAÇÃO
# =============================================================================
PLANILHA_PADRAO = ""
ABA_PADRAO = "Winker"
PASTA_BOLETOS = r"Z:\financeiro_imoveis\Condomínio\boleto__Cond_antes liax"

LOGIN_WINKER = os.getenv("WINKER_LOGIN", "ctrlcond@lello.com.br")
SENHA_WINKER = os.getenv("WINKER_SENHA", "Lello123@")

URL_LOGIN = "https://app.winker.com.br/intra/default/login"
URL_ESCOLHER_PORTAL = "https://app.winker.com.br/intra/default/escolherPortal"
URL_BOLETOS = "https://app.winker.com.br/intra/meuCondominio/boleto"

API_BASE = os.getenv("API_BASE", "http://127.0.0.1:5000")
TOKEN_API = os.getenv("TOKEN_SEGURANCA")
NOME_ROBO = "AUTOMAÇÃO WINKER"
EXIGIR_MES_DO_VENCIMENTO = True
# Se não houver nome IGUAL no site, aceita um card que COMECE com o nome (ex.: "JAU - Administradora X"),
# desde que seja um único candidato. Nunca aceita nome que apenas "contém" o texto (JAU != SLA JAUBA).
ACEITAR_PARCIAL = True

# A rota /api/automacao/auditar só trata como sucesso o texto "Sucesso" e como "sem boleto" o texto
# "Não Disponível"; qualquer outro status vira nota de erro no imóvel.
STATUS_AUDITORIA = {"BAIXADO": "Sucesso", "SEM BOLETO": "Não Disponível", "SEM BOLETO DO MÊS": "Não Disponível"}

BASE = os.path.dirname(os.path.abspath(__file__))
PASTA_DIAG = os.path.join(BASE, "winker_diagnostico")
PASTA_PRINTS = os.path.join(BASE, "winker_prints")

FRASE_UNICO = "Selecione uma referência ou clique em Filtrar"
FRASE_MULTI = "Necessário informar um(a) Divisão e Unidade"
DIV_VAZIA = {"", "NAO E NECESSARIO"}

XP_FILTRAR = "//div[@id='boletos']//button[contains(normalize-space(.), 'Filtrar')]"

# Opções de um select2 aberto
JS_OPCOES = """
    const dropdown = arguments[0];
    let itens = dropdown.querySelectorAll('li.select2-result-selectable > div.select2-result-label, li.select2-results__option, .select2-result-label, li[role="option"]');
    return Array.from(itens).map(e => [e, e.textContent]);
"""

JS_MOUSEDOWN = """
    const el = arguments[0];
    for (const tipo of ['mousedown', 'mouseup', 'click']) {
        el.dispatchEvent(new MouseEvent(tipo, {bubbles: true, cancelable: true, view: window}));
    }
"""

JS_API_SELECT2 = """
    const id = arguments[0].replace(/^s2id_/, '');
    if (window.jQuery) { jQuery('#' + id).select2('open'); }
"""

JS_SELECIONAR = """
    const idc = arguments[0].replace(/^s2id_/, ''), alvo = arguments[1].replace(/\\s+/g, ' ').trim();
    const $ = window.jQuery;
    if (!$) return 'sem_jquery';
    let dado = null;
    $('.select2-drop li.select2-result-selectable').each(function () {
        const t = $(this).text().replace(/\\s+/g, ' ').trim();
        if (t === alvo) { dado = $(this).data('select2-data'); return false; }
    });
    if (!dado) return 'item_nao_achado';
    const campo = $('#' + idc);
    campo.select2('data', dado, true);
    try { campo.select2('close'); } catch (e) {}
    return 'ok';
"""

ANG_PREFIXO = """
    const _el = document.querySelector('[ng-controller="BoletoController"]');
    if (!window.angular || !_el) return null;
    const sc = angular.element(_el).scope();
    if (!sc || !sc.pesquisa) return null;
"""
JS_ANG_DIVS = ANG_PREFIXO + "return (sc.my_divisions || []).map(function (d) { return [d.id_division, d.division]; });"
JS_ANG_SET_DIV = ANG_PREFIXO + """
    const id = arguments[0];
    try {
        sc.$apply(function () {
            sc.pesquisa.divisao.id_divisao = id;
            if (sc.isAdmBoletos && typeof sc.pesquisa.divisao.change === 'function') sc.pesquisa.divisao.change();
            else if (typeof sc.pesquisa.divisao.changeByMyUnits === 'function') sc.pesquisa.divisao.changeByMyUnits();
        });
    } catch (e) { return 'erro: ' + e; }
    return 'ok';
"""
JS_ANG_UNIDADES = ANG_PREFIXO + """
    let o = sc.pesquisa.unidade && sc.pesquisa.unidade.options;
    if (!o) return [];
    if (!Array.isArray(o)) o = Object.keys(o).map(function (k) { return o[k]; });
    return o.map(function (u) { return [u.id_unit, u.name_without_division]; });
"""
JS_ANG_SET_UNID = ANG_PREFIXO + """
    const id = arguments[0];
    try {
        sc.$apply(function () {
            sc.pesquisa.unidade.id_unidade = id;
            if (typeof sc.pesquisa.clearBilling === 'function') sc.pesquisa.clearBilling();
        });
    } catch (e) { return 'erro: ' + e; }
    return 'ok';
"""

JS_ITENS = r"""
    const raiz = document.querySelector('#boletos') || document.body;
    return Array.from(raiz.querySelectorAll('.list-group-item'))
        .filter(li => !li.closest('.well') && /\d{2}\/\d{2}\/\d{4}/.test(li.textContent))
        .map(li => {
            const ruim = x => !x.href || /^(javascript|#)/i.test(x.getAttribute('href') || '') || /copiar|linha|d[ií]gito|pix/i.test(x.textContent);
            const propria = li.tagName === 'A' && !ruim(li) ? li : null;
            const a = propria || Array.from(li.querySelectorAll('a')).find(x => !ruim(x));
            const pago = Array.from(li.querySelectorAll('a, span, label, b, strong, .badge')).some(e => /^\s*pago\b/i.test(e.textContent));
            return [li.textContent.replace(/\s+/g, ' ').trim(), a ? a.href : '', pago];
        });"""

JS_LINKS = r"""
    const raiz = document.querySelector('#boletos') || document.body;
    return Array.from(raiz.querySelectorAll('a'))
        .filter(a => a.href && !/^javascript/i.test(a.getAttribute('href') || '') && !a.closest('.well')
                     && /boleto|2.{0,3}\s*via/i.test(a.textContent) && !/copiar|linha|d[ií]gito|pix/i.test(a.textContent))
        .map(a => { const c = a.closest('li, .list-group-item, tr, .panel') || a.parentElement;
                    return [a.href, a.textContent.trim(), c ? c.textContent : '']; });"""


# =============================================================================
# UTILIDADES
# =============================================================================
class FalhaColeta(Exception):
    def __init__(self, status, mensagem=""):
        super().__init__(mensagem)
        self.status = status
        self.mensagem = mensagem


def log(msg):
    print(f"[{dt.datetime.now():%H:%M:%S}] {msg}", flush=True)


def colapsar(txt):
    return " ".join(str(txt or "").split())


def resumir_erro(e, limite=300):
    """'NomeDoErro: mensagem'. Em clique bloqueado, mostra QUEM está por cima do botão (a parte que antes era cortada)."""
    txt = " ".join(str(e).split())
    txt = re.sub(r"^Message:\s*", "", txt)
    m = re.search(r"Other element would receive the click:\s*(<[^>]*>)", txt)
    if m:
        return f"{type(e).__name__}: clique bloqueado por {m.group(1)}"[:limite]
    return f"{type(e).__name__}: {txt}"[:limite]


def norm(txt):
    t = unicodedata.normalize("NFKD", str(txt or ""))
    t = "".join(c for c in t if not unicodedata.combining(c))
    return " ".join(t.upper().split())


def xp(texto):
    t = str(texto)
    if "'" not in t:
        return f"'{t}'"
    if '"' not in t:
        return f'"{t}"'
    return "concat(" + ", \"'\", ".join(f"'{p}'" for p in t.split("'")) + ")"


def diagnosticar(driver, nome, html=False):
    try:
        os.makedirs(PASTA_DIAG, exist_ok=True)
        seguro = re.sub(r"[^A-Za-z0-9_]+", "_", nome)[:60]
        base = os.path.join(PASTA_DIAG, dt.datetime.now().strftime("%H%M%S") + "_" + seguro)
        try:
            driver.save_screenshot(base + ".png")
        except Exception:
            pass
        if html:
            with open(base + ".html", "w", encoding="utf-8") as f:
                f.write(driver.page_source)
        log(f"   [DIAG] {base}.png" + (" + .html" if html else ""))
    except Exception:
        pass


def mes_ano(data_str):
    m = re.search(r"(\d{2})/(\d{2})/(\d{4})", str(data_str or ""))
    return (int(m.group(2)), int(m.group(3))) if m else None


def listar_itens(driver):
    itens = []
    for texto, href, pago in driver.execute_script(JS_ITENS):
        itens.append({"texto": texto, "href": href, "pago": bool(pago), "venc": extrair_vencimento(texto)})
    if not itens:
        for href, _txt, ctx in driver.execute_script(JS_LINKS):
            itens.append({"texto": colapsar(ctx), "href": href,
                          "pago": bool(re.search(r"\bpago\b", ctx or "", re.IGNORECASE)),
                          "venc": extrair_vencimento(ctx)})
    return itens


def extrair_vencimento(contexto):
    m = (re.search(r"venc\w*\D{0,25}(\d{2}/\d{2}/\d{4})", contexto or "", re.IGNORECASE)
         or re.search(r"(\d{2}/\d{2}/\d{4})", contexto or ""))
    return m.group(1) if m else "sem_data"


def auditar_sistema(imovel, status, detalhe):
    """Notifica a API central e alimenta a tela Raio-X RPA imediatamente."""
    try:
        requests.post(f"{API_BASE}/api/automacao/auditar", json={
            "robo": NOME_ROBO,
            "imovel": str(imovel),
            "status": STATUS_AUDITORIA.get(status, status),
            "detalhe": detalhe
        }, timeout=5)
    except Exception:
        pass


# =============================================================================
# PLANILHA
# =============================================================================
def ler_planilha(origem, aba):
    if not origem:
        sys.exit("Informe a planilha: --planilha <URL do Google Sheets ou caminho do .xlsx>")
    if origem.lower().startswith("http"):
        m = re.search(r"/spreadsheets/d/([\w-]+)", origem)
        if not m:
            sys.exit("URL do Google Sheets inválida.")
        gid = re.search(r"[#&?]gid=(\d+)", origem)
        url = f"https://docs.google.com/spreadsheets/d/{m.group(1)}/export?format=csv" + (f"&gid={gid.group(1)}" if gid else "")
        r = requests.get(url, timeout=60)
        if r.status_code != 200 or "csv" not in r.headers.get("Content-Type", "").lower():
            sys.exit("Não consegui ler o Google Sheets.")
        return pd.read_csv(io.StringIO(r.content.decode("utf-8")), dtype=str, keep_default_na=False)

    if not os.path.exists(origem):
        sys.exit(f"Arquivo não encontrado: {origem}")
    if origem.lower().endswith((".xlsx", ".xlsm")):
        try:
            return pd.read_excel(origem, sheet_name=aba, dtype=str, keep_default_na=False)
        except ValueError:
            nomes = pd.ExcelFile(origem).sheet_names
            sys.exit(f"Aba '{aba}' não existe. Abas do arquivo: {nomes}")
    return pd.read_csv(origem, dtype=str, keep_default_na=False)


def localizar_colunas(df):
    cols = {}
    for c in df.columns:
        n = norm(c)
        if "IMOVEL" in n and "EMPREEND" not in n and "NOME" not in n:
            cols.setdefault("imovel", c)
        elif "EMPREENDIMENTO" in n and "SITE" in n:
            cols.setdefault("emp", c)
        elif "DIVISAO" in n and "SITE" in n:
            cols.setdefault("div", c)
        elif "UNIDADE" in n and "SITE" in n:
            cols.setdefault("unid", c)
        elif n == "STATUS":
            cols.setdefault("status", c)
        elif n.startswith("ADMINISTRADORA"):
            cols.setdefault("adm", c)
    faltando = [k for k in ("imovel", "emp", "div", "unid", "status") if k not in cols]
    if faltando:
        sys.exit(f"Colunas não encontradas na planilha: {faltando}")
    return cols


def montar_tarefas(df, cols, filtro_imoveis, limite):
    tarefas, pulados_status, sem_dados = [], 0, []
    for _, r in df.iterrows():
        imovel = colapsar(r[cols["imovel"]])
        if not imovel or imovel in ("Id_Imovel", "Sem_ID"):
            continue
        if filtro_imoveis and imovel not in filtro_imoveis:
            continue
        if norm(r[cols["status"]]) != "TEM":
            pulados_status += 1
            continue
        t = {
            "imovel": imovel,
            "emp": colapsar(r[cols["emp"]]),
            "div": colapsar(r[cols["div"]]),
            "unid": colapsar(r[cols["unid"]]),
            "adm": colapsar(r[cols["adm"]]) if "adm" in cols else "",
            "venc_base": "",
        }
        if not t["emp"] or not t["unid"]:
            sem_dados.append(t)
            continue
        tarefas.append(t)
    if limite:
        tarefas = tarefas[:limite]
    return tarefas, pulados_status, sem_dados


# =============================================================================
# NAVEGADOR / LOGIN
# =============================================================================
def criar_driver(headless):
    op = webdriver.ChromeOptions()
    if headless:
        op.add_argument("--headless=new")
    op.add_argument("--window-size=1366,900")
    op.add_argument("--start-maximized")
    op.add_argument("--disable-notifications")
    op.add_argument("--lang=pt-BR")
    driver = webdriver.Chrome(options=op)
    driver.set_script_timeout(90)
    return driver


def aguardar_pronto(driver, timeout=30):
    try:
        WebDriverWait(driver, timeout).until(lambda d: d.execute_script("return document.readyState") == "complete")
    except TimeoutException:
        pass


def achar_visivel(driver, localizadores, timeout=20):
    fim = time.time() + timeout
    while time.time() < fim:
        for by, sel in localizadores:
            for el in driver.find_elements(by, sel):
                try:
                    if el.is_displayed():
                        return el
                except WebDriverException:
                    pass
        time.sleep(0.3)
    return None


def fazer_login(driver):
    driver.get(URL_LOGIN)
    aguardar_pronto(driver)
    campo_user = achar_visivel(driver, [
        (By.ID, "LoginForm_username"), (By.CSS_SELECTOR, "input[name='LoginForm[username]']"),
        (By.CSS_SELECTOR, "input[type='email']"), (By.CSS_SELECTOR, "input[type='text']")])
    campo_senha = achar_visivel(driver, [
        (By.ID, "LoginForm_password"), (By.CSS_SELECTOR, "input[name='LoginForm[password]']"),
        (By.CSS_SELECTOR, "input[type='password']")], 5)
    if not campo_user or not campo_senha:
        diagnosticar(driver, "login_campos_nao_achados", html=True)
        raise FalhaColeta("ERRO LOGIN", f"Campos de login não encontrados em {driver.current_url}")

    campo_user.clear()
    campo_user.send_keys(LOGIN_WINKER)
    campo_senha.clear()
    campo_senha.send_keys(SENHA_WINKER)
    botao = achar_visivel(driver, [
        (By.CSS_SELECTOR, "input[type='submit']"), (By.CSS_SELECTOR, "button[type='submit']"),
        (By.XPATH, "//button[contains(normalize-space(.), 'Entrar')]")], 5)
    if botao:
        botao.click()
    else:
        campo_senha.send_keys(Keys.ENTER)

    try:
        WebDriverWait(driver, 30).until(lambda d: "/default/login" not in d.current_url.lower())
    except TimeoutException:
        diagnosticar(driver, "login_nao_concluiu", html=True)
        raise FalhaColeta("ERRO LOGIN", f"O login não concluiu em {driver.current_url}")
    aguardar_pronto(driver)
    time.sleep(2)

    if "escolherportal" not in driver.current_url.lower():
        driver.get(URL_ESCOLHER_PORTAL)
        aguardar_pronto(driver)


def iniciar_sessao(headless):
    driver = criar_driver(headless)
    try:
        fazer_login(driver)
    except Exception:
        driver.quit()
        raise
    return driver, driver.current_window_handle


def sessao_viva(driver):
    try:
        _ = driver.window_handles
        return True
    except WebDriverException:
        return False


# =============================================================================
# GUIA PRINCIPAL (escolherPortal) - Sem recarregar página
# =============================================================================
def preencher_busca(driver, texto):
    try:
        caixa = driver.execute_script("""
            return Array.from(document.querySelectorAll('input')).filter(i =>
                i.getClientRects().length > 0 && ['text', 'search', ''].includes(i.type) &&
                /pesq|busc|search|filtr/i.test((i.placeholder || '') + (i.name || '') + (i.id || '') + (i.className || ''))
            )[0] || null;""")
        if caixa:
            caixa.click()
            caixa.send_keys(Keys.CONTROL, "a")
            caixa.send_keys(Keys.BACKSPACE)
            time.sleep(0.3)
            if texto:
                caixa.send_keys(texto)
                caixa.send_keys(Keys.ENTER)
            time.sleep(1.0)
    except Exception:
        pass


def _forma(txt):
    """Texto só com palavras inteiras (sem acento/pontuação), para comparar nomes sem erro de 'contém'."""
    return " ".join(_tokens(txt))


_RX_SINDICO = re.compile(r"\bS[íi]ndic[oa]\b", re.IGNORECASE)
_RX_BAIRRO_FINAL = re.compile(r"\s*\([^()]*\)\s*$")


def _formas_do_card(txt):
    """No escolherPortal cada card é 'NOME (BAIRRO) Síndico: FULANO'. Comparar o texto inteiro nunca dá igual ao nome
    do cadastro (ex.: 'VITÓRIA'). Devolve as formas comparáveis: (nome sem bairro, nome com bairro, texto completo).
    Se o texto tem mais de um 'Síndico' é um bloco com VÁRIOS cards (não é um card): nome e nome+bairro ficam vazios."""
    bruto = colapsar(txt)
    partes = _RX_SINDICO.split(bruto)
    if len(partes) > 2:
        return "", "", _forma(bruto)
    ate_sindico = colapsar(partes[0])
    nome = colapsar(_RX_BAIRRO_FINAL.sub("", ate_sindico))
    return _forma(nome), _forma(ate_sindico), _forma(bruto)


def escolher_por_nome(textos, emp_site):
    """Decide qual texto da lista é o empreendimento pedido. Função pura (testável).
    Retorna (modo, indices, info):
      'exato'   -> o NOME do card é idêntico ao pedido (ignora acento, maiúscula, pontuação, bairro e síndico)
      'parcial' -> um único card cujo nome COMEÇA com o pedido, palavra inteira (JAU não casa com JAUBA)
      'ambiguo' -> vários cards diferentes servem; info = nomes encontrados
      'nenhum'  -> nada serve; info = nomes parecidos vistos na lista (para a mensagem de erro)
    """
    a = _forma(emp_site)
    if not a:
        return "nenhum", [], []
    cards = [_formas_do_card(t) for t in textos]
    exatos = [i for i, (nome, com_bairro, completo) in enumerate(cards) if a in (nome, com_bairro, completo)]
    if exatos:
        return "exato", exatos, []
    if ACEITAR_PARCIAL:
        cand = [(i, nome) for i, (nome, _cb, _c) in enumerate(cards) if nome.startswith(a + " ") and len(nome) <= len(a) + 80]
        distintos = sorted({nome for _, nome in cand})
        if len(distintos) == 1:
            return "parcial", [i for i, _ in cand], distintos
        if len(distintos) > 1:
            return "ambiguo", [], distintos[:5]
    vistos = sorted({nome for nome, _cb, _c in cards if nome and a in nome and len(nome) <= len(a) + 80})[:5]
    return "nenhum", [], vistos


def localizar_portal(driver, emp_site, administradora):
    """Devolve (elemento, exato, texto_escolhido, parecidos_vistos)."""
    preencher_busca(driver, emp_site)
    js = """
        return Array.from(document.querySelectorAll('a, button, [onclick], [role="button"], div.box, .list-group-item'))
            .filter(e => e.getClientRects().length > 0)
            .map(e => [e, e.textContent, e.tagName]);"""
    ultimo_modo, ultima_info = "nenhum", []
    for _ in range(3):
        pares = driver.execute_script(js)
        textos = [txt for _, txt, _ in pares]
        modo, idx, info = escolher_por_nome(textos, emp_site)
        ultimo_modo, ultima_info = modo, info
        if modo == "exato":
            exatos = [(pares[i][0], pares[i][2], pares[i][1]) for i in idx]
            exatos.sort(key=lambda x: 0 if x[1] == "A" else 1)
            candidatos = [e for e, _, _ in exatos]
            texto_de = {id(e): colapsar(t) for e, _, t in exatos}
            if len(candidatos) > 1 and administradora:
                alvo_adm = norm(administradora)
                filtrados = [e for e in candidatos if alvo_adm in norm(driver.execute_script(
                    "var c = arguments[0].closest('tr, li, .panel, .card, .list-group-item, div'); return c ? c.textContent : '';", e))]
                if filtrados:
                    candidatos = filtrados
            cards_diferentes = sorted({texto_de[id(e)] for e in candidatos})
            if len(cards_diferentes) > 1:
                log(f"   AVISO: {len(cards_diferentes)} cards com o nome '{emp_site}' na lista; usando o primeiro. Cards: {' || '.join(cards_diferentes)[:400]}")
            return candidatos[0], True, texto_de[id(candidatos[0])], []
        if modo == "parcial":
            i = min(idx, key=lambda k: len(_forma(textos[k])))
            return pares[i][0], False, colapsar(textos[i]), []
        time.sleep(1.5)
    if ultimo_modo == "ambiguo":
        raise FalhaColeta("EMPREENDIMENTO NÃO ENCONTRADO",
                          f"'{emp_site}' não existe igual no site e há mais de um parecido: {ultima_info}")
    return None, False, "", ultima_info


def abas_novas(driver, antes):
    """Abas que apareceram depois de 'antes', na ordem em que foram abertas."""
    try:
        return [h for h in driver.window_handles if h not in antes]
    except WebDriverException:
        return []


def esperar_nova_aba(driver, antes, timeout):
    """Espera uma aba nova. Devolve a PRIMEIRA que abriu (antes usava .pop(), que pegava qualquer uma)."""
    fim = time.time() + timeout
    while time.time() < fim:
        novas = abas_novas(driver, antes)
        if novas:
            return novas[0]
        time.sleep(0.2)
    return None


def manter_uma_aba(driver, antes):
    """Se mais de uma aba abriu para o mesmo condomínio (o window.open demorou e o clique reserva
    abriu outra), fica com a primeira e fecha as demais. Era isso que deixava duas janelas na tela."""
    novas = abas_novas(driver, antes)
    if len(novas) > 1:
        log(f"   AVISO: {len(novas)} abas abriram para o mesmo condomínio; fico com a primeira e fecho as outras.")
        for h in novas[1:]:
            try:
                driver.switch_to.window(h)
                driver.close()
            except WebDriverException:
                pass
        novas = abas_novas(driver, antes)
    return novas[0] if novas else None


def abrir_em_nova_aba(driver, elemento):
    """Abre o condomínio em NOVA GUIA garantindo que a guia principal NUNCA navegue."""
    antes = set(driver.window_handles)
    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", elemento)

    url_destino = driver.execute_script("""
        const el = arguments[0];
        const a = el.closest('a') || el.querySelector('a') || (el.tagName === 'A' ? el : null);
        if (a && a.href && !a.href.startsWith('javascript') && a.href !== '#' && a.href !== '') {
            return a.href;
        }
        const onc = (el.getAttribute('onclick') || '') + ' ' + (a ? (a.getAttribute('onclick') || '') : '');
        const m = onc.match(/['"](https?:\\/\\/[^'"]+|\\/intra\\/[^'"]+)['"]/);
        if (m) {
            return m[1];
        }
        return null;
    """, elemento)

    nova = None
    if url_destino:
        driver.execute_script("window.open(arguments[0], '_blank');", url_destino)
        nova = esperar_nova_aba(driver, antes, 8)

    if not nova and not abas_novas(driver, antes):
        driver.execute_script("""
            const el = arguments[0];
            const a = el.closest('a') || el;
            if (a) a.setAttribute('target', '_blank');
        """, elemento)
        try:
            ActionChains(driver).key_down(Keys.CONTROL).click(elemento).key_up(Keys.CONTROL).perform()
        except Exception:
            pass
        nova = esperar_nova_aba(driver, antes, 6)

    if not nova and not abas_novas(driver, antes):
        try:
            driver.execute_script("""
                const el = arguments[0];
                const a = el.closest('a') || el;
                if (a) a.setAttribute('target', '_blank');
                el.dispatchEvent(new MouseEvent('click', {
                    bubbles: true, cancelable: true, view: window, ctrlKey: true, metaKey: true
                }));
            """, elemento)
            nova = esperar_nova_aba(driver, antes, 6)
        except Exception:
            pass

    # a tentativa anterior pode ter aberto a aba depois do tempo de espera: fica com uma só
    nova = manter_uma_aba(driver, antes) or nova

    if not nova:
        diagnosticar(driver, "nao_abriu_nova_guia", html=True)
        raise FalhaColeta("ERRO", "O item do empreendimento não abriu em nova guia (ver winker_diagnostico)")

    driver.switch_to.window(nova)
    try:
        driver.execute_cdp_cmd("Page.bringToFront", {})
    except Exception:
        pass
    try:
        driver.execute_script("window.focus();")
    except Exception:
        pass
    return nova


def fechar_outras_abas(driver, aba_lista):
    """Fecha abas de condomínios anteriores e mantém apenas a aba principal viva."""
    for h in list(driver.window_handles):
        if h != aba_lista:
            try:
                driver.switch_to.window(h)
                driver.close()
            except Exception:
                pass
    try:
        driver.switch_to.window(aba_lista)
    except Exception:
        pass


# =============================================================================
# GUIA DO CONDOMÍNIO: Boletos -> Filtros -> Download
# =============================================================================
def ir_para_boletos(driver):
    if "/meuCondominio/boleto" not in driver.current_url:
        try:
            def _link(d):
                for a in d.find_elements(By.XPATH, "//a[contains(@href, '/intra/meuCondominio/boleto')]"):
                    try:
                        if a.is_displayed():
                            return a
                    except WebDriverException:
                        pass
                return None
            link = WebDriverWait(driver, 20).until(_link)
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", link)
            link.click()
        except (TimeoutException, WebDriverException):
            driver.get(URL_BOLETOS)
    try:
        WebDriverWait(driver, 30).until(EC.presence_of_element_located((By.ID, "boletos")))
    except TimeoutException:
        diagnosticar(driver, "pagina_boletos_nao_abriu", html=True)
        raise FalhaColeta("ERRO", f"A página de Boletos não carregou (URL: {driver.current_url})")


def frase_visivel(driver, frase):
    try:
        return any(e.is_displayed() for e in driver.find_elements(By.XPATH, f"//span[contains(., {xp(frase)})]"))
    except WebDriverException:
        return False


def existe_botao_filtrar(driver):
    try:
        return any(b.is_displayed() and b.is_enabled()
                   for b in driver.find_elements(By.XPATH, "//button[contains(normalize-space(.), 'Filtrar')]"))
    except WebDriverException:
        return False


def aguardar_modo(driver):
    fim = time.time() + 45
    while time.time() < fim:
        if frase_visivel(driver, FRASE_MULTI):
            return "MULTI"
        if frase_visivel(driver, FRASE_UNICO):
            return "UNICO"
        if listar_itens(driver):
            return "LISTA"
        time.sleep(0.5)
    diagnosticar(driver, "frase_nao_apareceu", html=True)
    # Plano B: nenhuma frase apareceu, mas se o botão "Filtrar" está na tela segue pelo caminho simples
    if existe_botao_filtrar(driver):
        log("   AVISO: nenhuma frase de filtro apareceu em 45s; vou clicar em Filtrar e procurar o boleto.")
        return "FILTRAR"
    raise FalhaColeta("ERRO", "Nenhuma das frases de filtro apareceu em 45s")


def esperar_lista(driver, segundos):
    """True quando a lista de boletos apareceu (ou o site avisou que não há boletos); False se nada mudou."""
    fim = time.time() + segundos
    while time.time() < fim:
        if listar_itens(driver):
            return True
        texto = driver.execute_script("var r = document.querySelector('#boletos'); return r ? r.innerText : '';") or ""
        if re.search(r"nenhum boleto|n[ãa]o (h[áa]|existem|foram encontrados) boletos|sem boletos", texto, re.IGNORECASE):
            return True
        time.sleep(0.7)
    return False


def tem_divisao_na_tela(driver):
    try:
        if driver.execute_script(JS_ANG_DIVS):
            return True
    except WebDriverException:
        pass
    try:
        return any(e.is_displayed() for e in driver.find_elements(By.ID, "s2id_divisao"))
    except WebDriverException:
        return False


def filtrar_direto(driver, t):
    """Plano B (nenhuma frase de filtro apareceu): clica em Filtrar e procura o boleto. Se nada listar e a tela
    pedir Divisão/Unidade, filtra por elas e tenta de novo."""
    clicar_filtrar(driver)
    if esperar_lista(driver, 15):
        return
    if frase_visivel(driver, FRASE_MULTI) or tem_divisao_na_tela(driver):
        log("   A tela pede Divisão e Unidade; filtrando por elas.")
        filtrar_multi(driver, t)
        if esperar_lista(driver, 15):
            return
    raise FalhaColeta("ERRO", "Nenhuma das frases de filtro apareceu em 45s e, ao clicar em Filtrar, nenhum boleto foi listado")


def drop_aberto(driver):
    xpaths = [
        "//div[contains(@class, 'select2-drop-active') and not(contains(@class, 'select2-display-none'))]",
        "//div[contains(@class, 'select2-dropdown')]",
        "//div[contains(@class, 'select2-drop') and contains(@style, 'display: block')]",
        "//div[contains(@class, 'select2-drop') and not(contains(@class, 'select2-display-none'))]",
    ]
    for xp_str in xpaths:
        for d in driver.find_elements(By.XPATH, xp_str):
            try:
                if d.is_displayed():
                    return d
            except WebDriverException:
                pass
    return None


def fechar_select2(driver):
    try:
        driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
    except Exception:
        pass


def abrir_select2(driver, id_container):
    xpath = (f"//div[@id={xp(id_container)} and not(contains(@class, 'select2-container-disabled'))]"
             f"//*[contains(@class, 'select2-choice') or contains(@class, 'select2-selection')]")
    try:
        gatilho = WebDriverWait(driver, 20).until(EC.element_to_be_clickable((By.XPATH, xpath)))
    except TimeoutException:
        diagnosticar(driver, f"filtro_indisponivel_{id_container}")
        raise FalhaColeta("ERRO", f"O filtro '{id_container}' não ficou disponível")

    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", gatilho)
    time.sleep(0.5)

    tentativas = [
        lambda: ActionChains(driver).move_to_element(gatilho).click().perform(),
        lambda: driver.execute_script(JS_MOUSEDOWN, gatilho),
        lambda: driver.execute_script(JS_API_SELECT2, id_container),
    ]
    for tentar in tentativas:
        try:
            tentar()
        except WebDriverException:
            continue
        try:
            return WebDriverWait(driver, 4).until(drop_aberto)
        except TimeoutException:
            continue

    diagnosticar(driver, f"select2_nao_abriu_{id_container}", html=True)
    raise FalhaColeta("ERRO", f"A lista do filtro '{id_container}' não abriu")


def _tokens(txt):
    return re.sub(r"[^A-Z0-9 ]", " ", norm(txt)).split()


def _parecido(a, b):
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return False
    curto, longo = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    if len(longo) - len(curto) > 1:
        return False
    for tk in curto:
        if not any(tk == o or (len(tk) >= 3 and not tk.isdigit() and o.startswith(tk)) for o in longo):
            return False
    return True


def _enter_na_opcao(driver, drop, el):
    driver.execute_script(
        "arguments[0].dispatchEvent(new MouseEvent('mouseover', {bubbles: true})); "
        "arguments[0].dispatchEvent(new MouseEvent('mousemove', {bubbles: true}));", el)
    time.sleep(0.2)
    campo = drop.find_elements(By.CSS_SELECTOR, "input.select2-input")
    alvo = campo[0] if campo and campo[0].is_displayed() else driver.switch_to.active_element
    alvo.send_keys(Keys.ENTER)


def _digitar_enter(driver, drop, texto):
    campo = drop.find_elements(By.CSS_SELECTOR, "input.select2-input")
    alvo = campo[0] if campo and campo[0].is_displayed() else driver.switch_to.active_element
    alvo.click()
    alvo.send_keys(Keys.CONTROL, "a")
    alvo.send_keys(Keys.DELETE)
    alvo.send_keys(colapsar(texto))
    time.sleep(1.0)
    alvo.send_keys(Keys.ENTER)


def achar_opcao(opcoes, alvos, flexivel=False):
    for i, alvo in enumerate(alvos):
        for el, txt in opcoes:
            if norm(txt) == alvo:
                return el, txt, i > 0
    if flexivel:
        for alvo in alvos:
            ach = [(el, txt) for el, txt in opcoes if _parecido(txt, alvo)]
            if len(ach) == 1:
                return ach[0][0], ach[0][1], True
    return None


def escolher_opcao(driver, id_container, valor, rotulo, alternativas=()):
    status_nf = "DIVISÃO NÃO ENCONTRADA" if rotulo.startswith("Divis") else "UNIDADE NÃO ENCONTRADA"
    alvos = [norm(valor)] + [norm(a) for a in alternativas]

    drop, opcoes = None, []
    for _ in range(3):
        drop = abrir_select2(driver, id_container)
        fim = time.time() + 12
        while time.time() < fim:
            opcoes = driver.execute_script(JS_OPCOES, drop)
            if opcoes:
                break
            time.sleep(0.4)
        if opcoes:
            break
        fechar_select2(driver)
        time.sleep(1.5)
    if not opcoes:
        diagnosticar(driver, f"lista_vazia_{id_container}")
        raise FalhaColeta(status_nf, f"A lista de {rotulo} veio vazia")

    flexivel = rotulo.startswith("Divis")
    achada = achar_opcao(opcoes, alvos, flexivel)
    if not achada and flexivel and len(opcoes) == 1:
        achada = (opcoes[0][0], opcoes[0][1], True)
    if not achada:
        campo = drop.find_elements(By.CSS_SELECTOR, "input.select2-input")
        if campo and campo[0].is_displayed():
            campo[0].send_keys(valor)
            time.sleep(0.8)
            achada = achar_opcao(driver.execute_script(JS_OPCOES, drop), alvos, flexivel)
    if not achada:
        vistos = [colapsar(txt) for _, txt in opcoes][:20]
        fechar_select2(driver)
        raise FalhaColeta(status_nf, f"{rotulo} '{valor}' não existe no site ({len(opcoes)} opções; ex.: {vistos})")

    el, texto, usou_alternativa = achada
    if usou_alternativa:
        log(f"   AVISO: {rotulo} '{valor}' não existe igual; usei '{colapsar(texto)}'.")

    def confirmado():
        for _ in range(6):
            time.sleep(0.3)
            atual = driver.execute_script(
                "var s = document.querySelector('#' + arguments[0] + ' .select2-chosen'); return s ? s.textContent : '';",
                id_container)
            if norm(atual) == norm(texto):
                return True, atual
        return False, atual

    driver.execute_script("arguments[0].scrollIntoView({block: 'nearest'});", el)
    metodos = [
        lambda: _digitar_enter(driver, drop, texto),
        lambda: driver.execute_script(JS_SELECIONAR, id_container, texto),
        lambda: ActionChains(driver).move_to_element(el).pause(0.2).click().perform(),
        lambda: driver.execute_script(JS_MOUSEDOWN, el),
        lambda: _enter_na_opcao(driver, drop, el),
    ]
    escolhido = ""
    for i, metodo in enumerate(metodos):
        if i > 0 and not drop_aberto(driver):
            drop = abrir_select2(driver, id_container)
            el2 = achar_opcao(driver.execute_script(JS_OPCOES, drop), alvos, flexivel)
            if not el2:
                break
            el = el2[0]
        try:
            metodo()
        except WebDriverException:
            continue
        ok, escolhido = confirmado()
        if ok:
            return
    raise FalhaColeta("ERRO", f"A seleção de {rotulo} não confirmou (ficou '{colapsar(escolhido)}')")


# Devolve '' se o botão está livre para clique; senão, um resumo do elemento que está por cima dele.
JS_COBERTURA = r"""
    const b = arguments[0];
    const r = b.getBoundingClientRect();
    if (!r.width || !r.height) return 'botão sem tamanho na tela';
    const topo = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
    if (!topo || b === topo || b.contains(topo)) return '';
    return (topo.outerHTML || topo.tagName || '').replace(/\s+/g, ' ').slice(0, 160);
"""


def _botoes_filtrar(driver):
    for xpath in (XP_FILTRAR, "//button[contains(normalize-space(.), 'Filtrar')]"):
        botoes = []
        for b in driver.find_elements(By.XPATH, xpath):
            try:
                if b.is_displayed() and b.is_enabled():
                    botoes.append(b)
            except StaleElementReferenceException:
                continue
        if botoes:
            return botoes
    return []


def clicar_filtrar(driver):
    """Clica em 'Filtrar'. Se algo estiver por cima do botão (tela de carregando, lista select2 aberta, aviso),
    espera sair; se persistir, fecha o select2 e tenta de novo; como último recurso clica via JavaScript
    (o ng-click do Angular dispara normalmente)."""
    if not _botoes_filtrar(driver):
        diagnosticar(driver, "sem_botao_filtrar", html=True)
        raise FalhaColeta("ERRO", "Botão 'Filtrar' não encontrado")

    cobre = ""
    for tentativa in (1, 2):
        try:
            botoes = _botoes_filtrar(driver)
            if not botoes:
                time.sleep(1.0)
                continue
            botao = botoes[0]
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", botao)
            time.sleep(0.3)
            fim = time.time() + 10
            while time.time() < fim:
                cobre = driver.execute_script(JS_COBERTURA, botao)
                if not cobre:
                    break
                time.sleep(0.4)
            if not cobre:
                botao.click()
                time.sleep(1.5)
                return
            log(f"   AVISO: 'Filtrar' está coberto por: {cobre}")
        except (ElementClickInterceptedException, StaleElementReferenceException) as e:
            log(f"   AVISO: clique em 'Filtrar' não passou ({type(e).__name__}); tentativa {tentativa}/2")
        fechar_select2(driver)
        time.sleep(1.0)

    botoes = _botoes_filtrar(driver)
    if not botoes:
        diagnosticar(driver, "sem_botao_filtrar", html=True)
        raise FalhaColeta("ERRO", "Botão 'Filtrar' não encontrado")
    log("   AVISO: clicando em 'Filtrar' via JavaScript (clique normal bloqueado)")
    diagnosticar(driver, "filtrar_coberto", html=True)
    driver.execute_script("arguments[0].click();", botoes[0])
    time.sleep(1.5)


def _alvos_unidade(unid):
    bases = [unid] + ([unid.split(" - ", 1)[1]] if " - " in unid else [])
    alvos = []
    for b in bases:
        for v in (b, b.lstrip("0")):
            if v and norm(v) not in alvos:
                alvos.append(norm(v))
    return alvos


def filtrar_angular(driver, t):
    try:
        divs = driver.execute_script(JS_ANG_DIVS)
    except WebDriverException:
        return False
    if not divs:
        return False

    alvo = norm(t["div"])
    ach = [d for d in divs if norm(d[1]) == alvo] or [d for d in divs if _parecido(d[1], t["div"])]
    if not ach and len(divs) == 1:
        ach = divs
    if not ach:
        raise FalhaColeta("DIVISÃO NÃO ENCONTRADA",
                          f"Divisão '{t['div']}' não existe no site")
    div_id, div_nome = ach[0]
    log(f"   Divisão (Angular): '{colapsar(div_nome)}'")
    if driver.execute_script(JS_ANG_SET_DIV, div_id) != "ok":
        return False

    unidades = []
    fim = time.time() + 15
    while time.time() < fim:
        unidades = driver.execute_script(JS_ANG_UNIDADES) or []
        if unidades:
            break
        time.sleep(0.5)
    if not unidades:
        return False

    alvos = _alvos_unidade(t["unid"])
    achada = None
    for a in alvos:
        achada = next((u for u in unidades if norm(u[1]) == a), None)
        if achada:
            break
    if not achada:
        raise FalhaColeta("UNIDADE NÃO ENCONTRADA",
                          f"Unidade '{t['unid']}' não existe no site")
    log(f"   Unidade (Angular): '{colapsar(achada[1])}'")
    if driver.execute_script(JS_ANG_SET_UNID, achada[0]) != "ok":
        return False
    time.sleep(1.0)
    return True


def filtrar_multi(driver, t):
    if norm(t["div"]) in DIV_VAZIA:
        raise FalhaColeta("DIVISÃO NÃO ENCONTRADA",
                          "O site pediu Divisão e Unidade, mas o cadastro está sem Divisão")
    if filtrar_angular(driver, t):
        clicar_filtrar(driver)
        return
    log("   (Angular indisponível; usando select2 em tela)")
    log(f"   Divisão: '{t['div']}'")
    escolher_opcao(driver, "s2id_divisao", t["div"], "Divisão/Bloco")
    log(f"   Unidade: '{t['unid']}'")
    escolher_opcao(driver, "s2id_unidade", t["unid"], "Unidade", _alvos_unidade_alt(t["unid"]))
    clicar_filtrar(driver)


def _alvos_unidade_alt(unid):
    return [a for a in _alvos_unidade(unid) if a != norm(unid)]


def baixar_pdf(driver, url):
    sessao = requests.Session()
    for c in driver.get_cookies():
        sessao.cookies.set(c["name"], c["value"])
    try:
        sessao.headers.update({"User-Agent": driver.execute_script("return navigator.userAgent"),
                               "Referer": driver.current_url})
        r = sessao.get(url, allow_redirects=True, timeout=60)
        if r.ok and r.content[:4] == b"%PDF":
            return r.content
    except requests.RequestException:
        pass
    try:
        b64 = driver.execute_async_script("""
            const url = arguments[0], done = arguments[arguments.length - 1];
            fetch(url, {credentials: 'include'}).then(r => r.arrayBuffer()).then(buf => {
                let bin = ''; const bytes = new Uint8Array(buf);
                for (let i = 0; i < bytes.length; i += 32768) bin += String.fromCharCode.apply(null, bytes.subarray(i, i + 32768));
                done(btoa(bin));
            }).catch(() => done(''));""", url)
        if b64:
            dados = base64.b64decode(b64)
            if dados[:4] == b"%PDF":
                return dados
    except Exception:
        pass
    raise FalhaColeta("ERRO", "O link do boleto não devolveu um PDF válido")


def aguardar_itens(driver):
    fim = time.time() + 45
    while time.time() < fim:
        itens = listar_itens(driver)
        if itens:
            time.sleep(1.2)
            for _ in range(2):
                driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                time.sleep(1.0)
            return listar_itens(driver) or itens
        texto = driver.execute_script("var r = document.querySelector('#boletos'); return r ? r.innerText : '';") or ""
        if re.search(r"nenhum boleto|n[ãa]o (h[áa]|existem|foram encontrados) boletos|sem boletos", texto, re.IGNORECASE):
            return []
        time.sleep(0.7)
    return []


def data_para_ordenar(venc):
    try:
        return dt.datetime.strptime(venc, "%d/%m/%Y")
    except ValueError:
        return dt.datetime.min


def escolher_boletos(candidatos, pendentes, resolvidos=()):
    """Quais boletos do site baixar. O sistema trata cada VENCIMENTO como um boleto (imóvel com vencimento
    alternativo, dia 05 e dia 15, tem dois). 'pendentes' = vencimentos que ainda faltam coletar (dd/mm/aaaa);
    'resolvidos' = vencimentos do imóvel que já estão coletados/pagos (não baixar de novo).
    Devolve a lista de itens do site, do vencimento mais cedo para o mais tarde (vazia = nenhum serve)."""
    candidatos = [i for i in candidatos if i["venc"] not in set(resolvidos)]
    if not candidatos:
        return []
    pendentes = sorted({p for p in pendentes if p}, key=data_para_ordenar)
    por_venc = {}
    for i in candidatos:
        atual = por_venc.get(i["venc"])
        if atual is None or (i["href"] and not atual["href"]):
            por_venc[i["venc"]] = i
    exatos = [por_venc[p] for p in pendentes if p in por_venc]
    if exatos:
        return exatos
    if len(pendentes) <= 1:              # comportamento de sempre: o mais novo do mês (a data do site pode diferir da da base)
        com_link = [i for i in candidatos if i["href"]] or candidatos
        return [max(com_link, key=lambda i: data_para_ordenar(i["venc"]))]
    if len(por_venc) == len(pendentes):  # as datas diferem, mas a contagem bate: pareia em ordem de data
        return [por_venc[v] for v in sorted(por_venc, key=data_para_ordenar)]
    return []                            # vários pendentes e nenhum casa: não se adivinha qual é qual


def nome_do_pdf(imovel, venc, mais_cedo, varios):
    """<imovel>.pdf como sempre; só quando o imóvel tem mais de um boleto aberto o(s) seguinte(s)
    ganha(m) o vencimento no nome, para um não sobrescrever o outro."""
    if not varios or venc == mais_cedo or not re.match(r"\d{2}/\d{2}/\d{4}$", venc or ""):
        return f"{imovel}.pdf"
    return f"{imovel}_{venc.replace('/', '-')}.pdf"


def coletar_boleto(driver, t, destino):
    """Baixa o(s) boleto(s) pendente(s) do imóvel. Devolve ([{'venc','arquivo'}...], observação)."""
    itens = aguardar_itens(driver)
    if not itens:
        diagnosticar(driver, f"sem_boleto_{t['imovel']}", html=True)
        raise FalhaColeta("SEM BOLETO", "Nenhum boleto listado após o filtro")

    abertos = [i for i in itens if not i["pago"]]
    if not abertos:
        raise FalhaColeta("SEM BOLETO", f"Todos os {len(itens)} boletos listados estão Pagos")

    candidatos = abertos
    alvo = mes_ano(t.get("venc_base"))
    if alvo and EXIGIR_MES_DO_VENCIMENTO:
        do_mes = [i for i in abertos if mes_ano(i["venc"]) == alvo]
        if not do_mes:
            datas = ", ".join(i["venc"] for i in abertos)
            raise FalhaColeta("SEM BOLETO DO MÊS",
                              f"Boletos em aberto ({datas}); nenhum do mês {alvo[0]:02d}/{alvo[1]} (base: {t['venc_base']})")
        candidatos = do_mes

    pendentes = t.get("venc_pend") or ([t["venc_base"]] if t.get("venc_base") else [])
    resolvidos = [v for v in (t.get("venc_todos") or []) if v not in pendentes]
    escolhidos = escolher_boletos(candidatos, pendentes, resolvidos)
    if not escolhidos:
        datas = ", ".join(i["venc"] for i in candidatos)
        raise FalhaColeta("SEM BOLETO DO VENCIMENTO",
                          f"Pendente(s) no sistema: {', '.join(pendentes) or '-'}; em aberto no site: {datas}")
    mais_cedo = min((i["venc"] for i in candidatos), key=data_para_ordenar)
    varios = len({i["venc"] for i in candidatos}) > 1 or len(pendentes) > 1

    baixados = []
    for escolhido in escolhidos:
        if not escolhido["href"]:
            diagnosticar(driver, f"aberto_sem_link_{t['imovel']}", html=True)
            raise FalhaColeta("ERRO", f"Boleto {escolhido['venc']} em aberto sem link para download")
        pdf = baixar_pdf(driver, escolhido["href"])
        nome = nome_do_pdf(t["imovel"], escolhido["venc"], mais_cedo, varios)
        caminho = os.path.join(destino, nome)
        temporario = caminho + ".tmp"
        with open(temporario, "wb") as f:
            f.write(pdf)
        os.replace(temporario, caminho)
        log(f"   Boleto salvo: {caminho} (venc {escolhido['venc']})")
        baixados.append({"venc": escolhido["venc"], "arquivo": nome})

    obs = ""
    nao_baixados = [i["venc"] for i in abertos if i["venc"] not in {b["venc"] for b in baixados}]
    if len(abertos) > 1:
        obs = f"{len(abertos)} em aberto; baixado(s) o(s) de {', '.join(b['venc'] for b in baixados)}."
        if nao_baixados:
            obs += f" Outros: {', '.join(nao_baixados)}"
    faltou = [p for p in pendentes if p not in {b['venc'] for b in baixados}] if len(baixados) < len(pendentes) else []
    if faltou:
        obs += (" | " if obs else "") + f"Ainda pendente no sistema: {', '.join(faltou)}"
    return baixados, obs


# =============================================================================
# SISTEMA (API do Flask)
# =============================================================================
def carregar_tarefas_api(colaborador, filtro_imoveis, limite):
    if not TOKEN_API:
        sys.exit("TOKEN_SEGURANCA não encontrado no .env")
    try:
        r = requests.post(f"{API_BASE}/api/automacao/pendencias_winker",
                          json={"token": TOKEN_API, "colaborador": colaborador or ""}, timeout=120)
    except requests.RequestException as e:
        sys.exit(f"Não consegui falar com o sistema em {API_BASE} (o Flask está rodando?): {e}")
    if r.status_code == 404:
        sys.exit("A rota /api/automacao/pendencias_winker não existe no Flask.")
    dados = r.json() if r.headers.get("Content-Type", "").startswith("application/json") else {}
    if not dados.get("sucesso"):
        sys.exit(f"O sistema recusou o pedido: {dados.get('msg') or r.status_code}")

    tarefas = []
    for d in dados["dados"]:
        imovel = colapsar(d.get("imovel"))
        if filtro_imoveis and imovel not in filtro_imoveis:
            continue
        pend = [colapsar(v) for v in (d.get("vencimentos_pendentes") or []) if colapsar(v)]
        tarefas.append({
            "imovel": imovel, "emp": colapsar(d.get("winker_condominio")), "div": colapsar(d.get("winker_predio")),
            "unid": colapsar(d.get("winker_unidade")), "adm": colapsar(d.get("administradora")),
            "venc_base": colapsar(d.get("vencimento")) or (pend[0] if pend else ""),
            # vencimentos que ainda faltam coletar e todos os do imóvel (o sistema trata cada vencimento como um boleto)
            "venc_pend": pend, "venc_todos": [colapsar(v) for v in (d.get("vencimentos_base") or []) if colapsar(v)]})
    return tarefas[:limite] if limite else tarefas


def marcar_coletado(imovel, vencimento, observacao=""):
    obs = f"Winker: boleto venc {vencimento} coletado" + (f" | {observacao}" if observacao else "")
    try:
        r = requests.post(f"{API_BASE}/api/automacao/coletar", timeout=30, json={
            "token": TOKEN_API, "imovel": imovel, "status": "COLETADO", "observacao": obs, "nome_robo": NOME_ROBO,
            "vencimento": vencimento if re.match(r"\d{2}/\d{2}/\d{4}$", vencimento or "") else ""})
        return bool(r.ok and r.json().get("sucesso"))
    except (requests.RequestException, ValueError):
        return False


# =============================================================================
# PROCESSAMENTO DE UM IMÓVEL
# =============================================================================
def processar_tarefa(driver, aba_lista, t, destino):
    res = {"status": "ERRO", "mensagem": "", "vencimento": "", "arquivo": "", "boletos": []}
    try:
        fechar_outras_abas(driver, aba_lista)

        if "escolherportal" not in driver.current_url.lower():
            log("   (Aba principal fora de escolherPortal; restabelecendo...)")
            driver.get(URL_ESCOLHER_PORTAL)
            aguardar_pronto(driver)

        elemento, exato, texto_site, parecidos = localizar_portal(driver, t["emp"], t["adm"])
        if elemento is None:
            diagnosticar(driver, f"emp_nao_encontrado_{t['imovel']}", html=True)
            extra = f" (no site há parecidos, mas não iguais: {parecidos})" if parecidos else ""
            raise FalhaColeta("EMPREENDIMENTO NÃO ENCONTRADO", f"'{t['emp']}' não está na lista do escolherPortal{extra}")
        log(f"   Empreendimento no site: '{texto_site}'" + ("" if exato else "  (AVISO: nome não era idêntico; o card começa com o nome pedido)"))

        abrir_em_nova_aba(driver, elemento)
        aguardar_pronto(driver)
        ir_para_boletos(driver)

        modo = aguardar_modo(driver)
        log(f"   Tela de boletos: {modo}")
        if modo == "MULTI":
            filtrar_multi(driver, t)
        elif modo == "UNICO":
            clicar_filtrar(driver)
        elif modo == "FILTRAR":
            filtrar_direto(driver, t)

        baixados, obs = coletar_boleto(driver, t, destino)
        res.update(status="BAIXADO", boletos=baixados, mensagem=obs,
                   vencimento=" + ".join(b["venc"] for b in baixados), arquivo=" + ".join(b["arquivo"] for b in baixados))
    except FalhaColeta as e:
        res.update(status=e.status, mensagem=e.mensagem)
    except Exception as e:
        res.update(status="ERRO", mensagem=resumir_erro(e))
        diagnosticar(driver, f"erro_{t['imovel']}", html=True)
    finally:
        try:
            os.makedirs(PASTA_PRINTS, exist_ok=True)
            driver.save_screenshot(os.path.join(PASTA_PRINTS, f"{res['status'].replace(' ', '_')}_{t['imovel']}.png"))
        except Exception:
            pass
        try:
            fechar_outras_abas(driver, aba_lista)
        except Exception:
            pass
    return res


# =============================================================================
# PRINCIPAL
# =============================================================================
def preparar_destino(caminho):
    if os.path.isdir(caminho):
        try:
            teste = os.path.join(caminho, ".teste_escrita")
            open(teste, "w").close()
            os.remove(teste)
            return caminho
        except OSError as e:
            motivo = str(e)
    else:
        motivo = "pasta não encontrada (disco Z: mapeado?)"
    alt = os.path.join(BASE, "boletos_teste")
    os.makedirs(alt, exist_ok=True)
    log(f"AVISO: não consegui usar '{caminho}' ({motivo}). Salvando em: {alt}")
    return alt


def main():
    ap = argparse.ArgumentParser(description="Coleta de boletos Winker")
    ap.add_argument("--planilha", default=PLANILHA_PADRAO)
    ap.add_argument("--aba", default=ABA_PADRAO)
    ap.add_argument("--destino", default=PASTA_BOLETOS)
    ap.add_argument("--imoveis", default="")
    ap.add_argument("--colaborador", default="")
    ap.add_argument("--limite", type=int, default=0)
    ap.add_argument("--sem-marcar", action="store_true")
    ap.add_argument("--headless", action="store_true")
    args = ap.parse_args()

    if not SENHA_WINKER:
        sys.exit("WINKER_SENHA não encontrada no .env.")

    filtro = {x.strip() for x in args.imoveis.split(",") if x.strip()}
    if args.planilha:
        df = ler_planilha(args.planilha, args.aba)
        cols = localizar_colunas(df)
        tarefas, pulados_status, sem_dados = montar_tarefas(df, cols, filtro, args.limite)
        origem = f"planilha ({len(df)} linhas)"
        marcar = False
    else:
        tarefas = carregar_tarefas_api(args.colaborador, filtro, args.limite)
        sem_dados = []
        origem = "sistema (pendentes da Winker)"
        marcar = not args.sem_marcar
    destino = preparar_destino(args.destino)

    log(f"Fonte: {origem} | a processar: {len(tarefas)} | marcar COLETADO: {'SIM' if marcar else 'NÃO'}")
    log(f"Boletos serão salvos em: {destino}")

    arq_csv = os.path.join(BASE, f"winker_resultado_{dt.datetime.now():%Y%m%d_%H%M}.csv")
    campos = ["data_hora", "imovel", "empreendimento_site", "divisao_bloco_site", "unidade_site",
              "status", "vencimento", "arquivo", "marcado_no_sistema", "mensagem"]
    contagem = {}

    with open(arq_csv, "w", newline="", encoding="utf-8-sig") as fcsv:
        escritor = csv.DictWriter(fcsv, fieldnames=campos, delimiter=";")
        escritor.writeheader()

        def registrar(t, status, mensagem="", vencimento="", arquivo="", marcado="-"):
            contagem[status] = contagem.get(status, 0) + 1
            escritor.writerow({
                "data_hora": dt.datetime.now().strftime("%d/%m/%Y %H:%M:%S"), "imovel": t["imovel"],
                "empreendimento_site": t["emp"], "divisao_bloco_site": t["div"], "unidade_site": t["unid"],
                "status": status, "vencimento": vencimento, "arquivo": arquivo,
                "marcado_no_sistema": marcado, "mensagem": mensagem})
            fcsv.flush()
            # Envia diretamente para o Raio-X RPA
            auditar_sistema(t["imovel"], status, mensagem)

        for t in sem_dados:
            registrar(t, "PULADO - SEM DADOS DO SITE", "Empreendimento (site) ou Unidade (site) vazios")

        driver = None
        try:
            if tarefas:
                log(f"Login: {LOGIN_WINKER}")
                try:
                    driver, aba_lista = iniciar_sessao(args.headless)
                except FalhaColeta as e:
                    log(f"ERRO NO LOGIN: {e.mensagem}")
                    for t in tarefas:
                        registrar(t, "ERRO LOGIN", e.mensagem)
                    tarefas = []

            for n, t in enumerate(tarefas, start=1):
                log(f"[{n}/{len(tarefas)}] Imóvel {t['imovel']} | {t['emp']} | {t['div'] or '-'} | {t['unid']}")

                if not sessao_viva(driver):
                    try:
                        driver.quit()
                    except Exception:
                        pass
                    try:
                        driver, aba_lista = iniciar_sessao(args.headless)
                    except Exception as e:
                        registrar(t, "ERRO LOGIN", f"Não consegui reabrir a sessão: {type(e).__name__}")
                        continue

                res = processar_tarefa(driver, aba_lista, t, destino)
                marcado = "-"
                if res["status"] == "BAIXADO" and marcar:
                    # cada boleto baixado é marcado com o SEU vencimento (dia 05 não marca o dia 15)
                    ok = all([marcar_coletado(t["imovel"], b["venc"], res["mensagem"]) for b in res["boletos"]])
                    marcado = "SIM" if ok else "NÃO"
                    if not ok:
                        res["mensagem"] = (res["mensagem"] + " | " if res["mensagem"] else "") + \
                            "PDF salvo, mas NÃO consegui marcar COLETADO no sistema"
                if res["status"] == "BAIXADO":
                    log(f"   ✅ SUCESSO: boleto do imóvel {t['imovel']} baixado (venc {res['vencimento']}) {res['mensagem']}"
                        + (f" | marcado no sistema: {marcado}" if marcar else ""))
                else:
                    log(f"   => {res['status']} {res['mensagem']}" + (f" | marcado no sistema: {marcado}" if marcar else ""))
                registrar(t, res["status"], res["mensagem"], res["vencimento"], res["arquivo"], marcado)
                time.sleep(1)
        finally:
            if driver:
                try:
                    driver.quit()
                except Exception:
                    pass

    log("RESUMO: " + " | ".join(f"{k}: {v}" for k, v in sorted(contagem.items())))
    log(f"Log completo: {arq_csv}")


if __name__ == "__main__":
    main()