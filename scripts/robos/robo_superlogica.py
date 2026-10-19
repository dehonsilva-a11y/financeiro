"""
ROBÔ SUPERLÓGICA — login via Google (ctrlcond@lello.com.br) com perfil persistente.

COMO USAR
---------
1) PRIMEIRA VEZ (ou quando a sessão expirar) — login manual, uma única vez:
       python robo_superlogica.py --login
   Abre o Chrome, você entra com a conta Google + 2FA, volta ao terminal e aperta Enter.
   A sessão fica salva na pasta PASTA_PERFIL.

2) DAÍ EM DIANTE — execução normal (pelo painel ou direto):
       python robo_superlogica.py

OBSERVAÇÕES
-----------
- Feche qualquer Chrome que esteja usando a pasta do perfil antes de rodar (um processo por vez).
- Requer Google Chrome instalado (channel="chrome").
- Variáveis no .env: TOKEN_SEGURANCA  (o mesmo usado pelo robo_email_geral.py)
"""
import os
import re
import sys
import sqlite3
import difflib
from datetime import datetime

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
PASTA_DESTINO = r"\\192.168.100.13\Lello2\ARQUIVOS-DIGITALIZACAO\financeiro_imoveis\Condomínio\boleto__Cond_antes liax"

URL_API_PENDENCIAS = "http://127.0.0.1:5000/api/automacao/pendencias"
URL_API_COLETA = "http://127.0.0.1:5000/api/automacao/coletar"
TOKEN_SEGURANCA = os.getenv("TOKEN_SEGURANCA")
NOME_ROBO = "AUTOMAÇÃO SUPERLÓGICA"

LIMIAR_SIMILARIDADE = 0.75

# Seletores da tela do Superlógica (os 3 primeiros já existiam no seu código)
SEL_COMBO_CONDOMINIOS = ".combo-condominios"
SEL_LISTA_CONDOMINIOS = "#lista li a"
SEL_BUSCA_BOLETOS = 'input[placeholder="Busque por boletos"]'

# Candidatos para o botão/link que baixa o boleto — testados em ordem.
# >>> AJUSTAR conforme o HTML real da tela de boletos <<<
SELETORES_BOTAO_BOLETO = [
    'a[href*="boleto" i]',
    'a[title*="boleto" i]',
    'button[title*="boleto" i]',
    'a:has-text("2ª via")',
    'a:has-text("Baixar")',
    'button:has-text("Baixar")',
    'a:has-text("Imprimir")',
    '[class*="download" i]',
]

# =======================================================================
# UTILITÁRIOS
# =======================================================================
def limpar_nome_condominio(nome):
    if not nome:
        return ""
    nome_limpo = re.sub(
        r'(?i)\b(condominio|cond|edificio|edif|residencial|res|cond\.|edif\.|res\.)\b',
        '', str(nome))
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


# =======================================================================
# BANCO E API DO PAINEL
# =======================================================================
def buscar_imoveis():
    """Traz o NÚMERO do imóvel (imoveis.imovel), que é o que a API de coleta e o nome do PDF usam."""
    try:
        conexao = sqlite3.connect(CAMINHO_BANCO)
        cursor = conexao.cursor()
        cursor.execute("""
            SELECT DISTINCT
                i.imovel,
                c.link_portal,
                i.empreendimento,
                i.endereco
            FROM cadastro_mestre_imoveis c
            JOIN imoveis i ON c.imovel = i.id
            WHERE i.ativo = 1
              AND (c.portal LIKE '%uperlogica%'
                   OR c.portal LIKE '%uperlógica%'
                   OR c.link_portal LIKE '%superlogica.net%')
        """)
        dados = cursor.fetchall()
        conexao.close()
        return dados
    except sqlite3.Error as e:
        print(f"ERRO ao acessar o banco de dados: {e}", flush=True)
        return []


def buscar_pendencias_api():
    """Retorna o conjunto de números de imóvel ainda pendentes, ou None se a API estiver fora."""
    try:
        resp = requests.post(
            URL_API_PENDENCIAS,
            json={"token": TOKEN_SEGURANCA, "colaborador": ""},
            timeout=20,
        ).json()
        if resp.get("sucesso"):
            return {norm_imovel(c.get("imovel")) for c in resp.get("dados", [])}
    except Exception as e:
        print(f"ERRO de conexão com a API de pendências: {e}", flush=True)
    return None


def dar_baixa_api(numero_imovel, observacao=""):
    try:
        resp = requests.post(
            URL_API_COLETA,
            json={
                "token": TOKEN_SEGURANCA,
                "imovel": str(numero_imovel),
                "nome_robo": NOME_ROBO,
                "status": "COLETADO",
                "observacao": observacao,
            },
            timeout=20,
        )
        return resp.status_code == 200 and resp.json().get("sucesso", False)
    except Exception as e:
        print(f"ERRO ao dar baixa na API: {e}", flush=True)
        return False


def agrupar_por_url(dados, pendentes):
    """Agrupa por URL do portal. Se `pendentes` não for None, mantém só os pendentes."""
    grupos = {}
    for numero, url, empreendimento, endereco in dados:
        if not url or not numero:
            continue
        if pendentes is not None and norm_imovel(numero) not in pendentes:
            continue
        grupos.setdefault(url, []).append({
            "imovel": str(numero).strip(),
            "empreendimento": empreendimento,
            "endereco": endereco,
        })
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
    dados = buscar_imoveis()
    urls = sorted({d[1] for d in dados if d[1]})
    url_inicial = sys.argv[2] if len(sys.argv) > 2 else (urls[0] if urls else "https://accounts.google.com")

    print(f"Abrindo o Chrome em: {url_inicial}", flush=True)
    with sync_playwright() as p:
        contexto = abrir_contexto(p)
        pagina = contexto.pages[0] if contexto.pages else contexto.new_page()
        pagina.goto(url_inicial)
        print("\n>>> Entre com ctrlcond@lello.com.br (senha + 2FA) e chegue até a tela inicial do portal.", flush=True)
        input(">>> Quando terminar, aperte ENTER aqui para salvar a sessão... ")
        contexto.close()
    print("✅ Sessão salva em:", PASTA_PERFIL, flush=True)


def garantir_login(pagina, url):
    """Abre o portal e confirma que está logado. Tenta 'Entrar com Google' se a sessão do portal caiu."""
    pagina.goto(url, wait_until="domcontentloaded")
    try:
        pagina.locator(SEL_COMBO_CONDOMINIOS).first.wait_for(state="visible", timeout=15000)
        return True, ""
    except PWTimeout:
        pass

    # Sessão do portal caiu: se a sessão do Google ainda vale, o botão do Google reloga sozinho.
    try:
        pagina.get_by_text(re.compile(r"google", re.I)).first.click(timeout=5000)
        pagina.locator(SEL_COMBO_CONDOMINIOS).first.wait_for(state="visible", timeout=30000)
        return True, ""
    except Exception:
        return False, "SESSÃO EXPIRADA — rode: python robo_superlogica.py --login"


# =======================================================================
# DOWNLOAD DO BOLETO
# =======================================================================
def baixar_boleto(pagina, caminho_pdf):
    """Clica no botão do boleto e captura o PDF (download direto OU aba nova). Retorna (ok, motivo)."""
    botao = None
    for seletor in SELETORES_BOTAO_BOLETO:
        try:
            loc = pagina.locator(seletor)
            if loc.count() > 0 and loc.first.is_visible():
                botao = loc.first
                break
        except Exception:
            continue
    if botao is None:
        return False, "Botão do boleto não encontrado (ajustar SELETORES_BOTAO_BOLETO)"

    contexto = pagina.context
    downloads, novas = [], []

    def ao_nova_pagina(nova):
        novas.append(nova)
        nova.on("download", downloads.append)

    pagina.on("download", downloads.append)
    contexto.on("page", ao_nova_pagina)
    try:
        botao.click()
        for _ in range(40):  # até ~20s
            if downloads or novas:
                break
            pagina.wait_for_timeout(500)

        if downloads:
            downloads[0].save_as(caminho_pdf)
        elif novas:
            nova = novas[0]
            try:
                nova.wait_for_load_state("domcontentloaded", timeout=15000)
                for _ in range(10):  # a aba pode redirecionar até o PDF final
                    if downloads:
                        break
                    nova.wait_for_timeout(500)
                if downloads:
                    downloads[0].save_as(caminho_pdf)
                else:
                    resp = contexto.request.get(nova.url, timeout=30000)
                    with open(caminho_pdf, "wb") as f:
                        f.write(resp.body())
            finally:
                try:
                    nova.close()
                except Exception:
                    pass
        else:
            return False, "Clique não gerou download nem nova aba"
    except Exception as e:
        return False, f"Erro ao baixar: {e}"
    finally:
        try:
            pagina.remove_listener("download", downloads.append)
        except Exception:
            pass
        try:
            contexto.remove_listener("page", ao_nova_pagina)
        except Exception:
            pass

    # Confere se o arquivo salvo é mesmo um PDF
    try:
        with open(caminho_pdf, "rb") as f:
            cabecalho = f.read(5)
        if not cabecalho.startswith(b"%PDF"):
            os.remove(caminho_pdf)
            return False, "Arquivo baixado não é um PDF válido"
        if os.path.getsize(caminho_pdf) == 0:
            os.remove(caminho_pdf)
            return False, "PDF baixado está vazio"
    except Exception as e:
        return False, f"Erro ao validar o PDF: {e}"

    return True, ""


# =======================================================================
# PROCESSO PRINCIPAL
# =======================================================================
def selecionar_condominio(pagina, nome_condominio):
    """Abre o combo e clica no condomínio mais parecido. Retorna (ok, motivo)."""
    pagina.wait_for_selector(SEL_COMBO_CONDOMINIOS, state="visible", timeout=10000)
    pagina.click(SEL_COMBO_CONDOMINIOS)
    pagina.wait_for_selector("#lista", state="visible", timeout=10000)

    maior_score, id_melhor = 0, None
    for opcao in pagina.query_selector_all(SEL_LISTA_CONDOMINIOS):
        nome_site = opcao.inner_text().strip()
        id_elemento = opcao.get_attribute("id")
        if not nome_site or not id_elemento:
            continue
        score = calcular_similaridade(nome_condominio, nome_site)
        if score > maior_score:
            maior_score, id_melhor = score, id_elemento

    if id_melhor and maior_score > LIMIAR_SIMILARIDADE:
        pagina.click(f'a[id="{id_melhor}"]')
        pagina.wait_for_load_state("networkidle")
        return True, ""
    return False, f"Condomínio não encontrado na lista (maior score: {maior_score:.2f})"


def executar_automacao():
    if not TOKEN_SEGURANCA:
        print("ERRO: TOKEN_SEGURANCA não encontrado no .env", flush=True)
        return

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
            "URL": url,
            "Status": status,
            "Motivo": motivo,
        })

    with sync_playwright() as p:
        contexto = abrir_contexto(p)
        pagina = contexto.pages[0] if contexto.pages else contexto.new_page()

        for url, lista in grupos.items():
            print(f"\n{'=' * 50}\nPortal: {url} ({len(lista)} imóvel(is))", flush=True)

            logado, motivo_login = garantir_login(pagina, url)
            if not logado:
                print(f"FALHA: {motivo_login}", flush=True)
                for im in lista:
                    registrar(im, url, "FALHA", motivo_login)
                continue
            print("Sessão válida. Processando imóveis...", flush=True)

            for im in lista:
                numero = im["imovel"]
                unidade, bloco = extrair_unidade_bloco(im["endereco"])
                termo = f"{unidade} {bloco}".strip()
                print(f"\n-> {im['empreendimento']} | Imóvel {numero} | Busca: '{termo}'", flush=True)

                if not unidade:
                    motivo = "Unidade não identificada no endereço (busca sem unidade pegaria boleto errado)"
                    print(f"FALHA: {motivo}", flush=True)
                    registrar(im, url, "FALHA", motivo)
                    continue

                try:
                    ok, motivo = selecionar_condominio(pagina, im["empreendimento"])
                    if not ok:
                        print(f"FALHA: {motivo}", flush=True)
                        registrar(im, url, "FALHA", motivo)
                        continue

                    pagina.wait_for_selector(SEL_BUSCA_BOLETOS, state="visible", timeout=10000)
                    pagina.fill(SEL_BUSCA_BOLETOS, termo)
                    pagina.keyboard.press("Enter")
                    pagina.wait_for_timeout(2500)  # aguarda o filtro da lista

                    caminho = os.path.join(PASTA_DESTINO, f"{numero}.pdf")
                    ok, motivo = baixar_boleto(pagina, caminho)
                    if not ok:
                        print(f"FALHA: {motivo}", flush=True)
                        registrar(im, url, "FALHA", motivo)
                        continue

                    obs = f"Coletado via Superlógica em {datetime.now().strftime('%d/%m/%Y %H:%M')}"
                    if dar_baixa_api(numero, obs):
                        coletados += 1
                        print(f"✅ SUCESSO: imóvel {numero} coletado e baixado no painel.", flush=True)
                        registrar(im, url, "COLETADO")
                    else:
                        motivo = "PDF salvo, mas a baixa no painel falhou"
                        print(f"FALHA: {motivo}", flush=True)
                        registrar(im, url, "PDF SALVO / SEM BAIXA", motivo)

                except Exception as e:
                    motivo = f"Erro ao processar condomínio/unidade: {e}"
                    print(f"FALHA: {motivo}", flush=True)
                    registrar(im, url, "FALHA", motivo)

        contexto.close()

    if relatorio:
        nome_arquivo = "relatorio_coletas_superlogica.xlsx"
        pd.DataFrame(relatorio).to_excel(nome_arquivo, index=False)
        print(f"\n🏁 Finalizado: {coletados}/{total} coletados. Relatório: {nome_arquivo}", flush=True)


if __name__ == "__main__":
    if "--login" in sys.argv:
        modo_login_manual()
    else:
        executar_automacao()