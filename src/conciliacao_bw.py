# -*- coding: utf-8 -*-
"""
Conciliação de 3 vias: BASE DO MÊS × RELATÓRIO relBolMovFinanCondominios × BW DO CAIXA (retorno .RET, linhas 02BW).

A pergunta que a tela responde: "no dia 10 a base tem X boletos; o relatório tem X lançados? o BW pagou X, no mesmo valor?"

- BASE DO MÊS: todos os boletos (imóvel + vencimento) do mês que passaram por QUALQUER carga da base
  (a base encolhe ao longo do mês porque o lançado sai da planilha; as cargas antigas continuam no banco).
- RELATÓRIO: relBolMovFinanCondominios enviado na tela (cada linha = um boleto lançado; Flag L = lançado,
  B = baixado/pago no portal, C = cancelado).
- BW: linhas "02BW" (pagamento efetuado) dos arquivos .RET do Bradesco Multipag/PAGFOR (layout de 500 posições),
  da pasta da rede ou enviados na tela. É D-1: o arquivo que chega hoje traz os pagamentos de ontem.
  Toda linha com carteira/sequência no "uso da empresa" (ex.: 50549/0001) é candidata a condomínio, mesmo sem o
  nome do condomínio. O imóvel sai do de-para (quando já conhecido) ou do relatório: mesma CARTEIRA + mesmo VALOR
  (+ vencimento). O que não casa e claramente não é condomínio (TED/crédito em conta, conta de luz, fiança,
  seguro, sequência 0000) vai para "outros pagamentos", sem virar divergência.

Uso no app.py (depois de criar o `app` e de definir `gestor_obrigatorio`):

    try:
        from . import conciliacao_bw
    except ImportError:
        import conciliacao_bw
    conciliacao_bw.registrar_rotas(app, gestor_obrigatorio, conectar_banco)
"""
import csv
import glob
import hashlib
import io
import os
import re
import threading
import time
import unicodedata
from collections import defaultdict
from datetime import date, datetime, timedelta

try:
    from . import analise_manual
except ImportError:  # pragma: no cover - quando roda fora do pacote
    import analise_manual

PASTA_BW_PADRAO = r"G:\Drives compartilhados\Financeiro - Locação\BKP ARQUIVOS BANCÁRIOS\BRADESCO RETORNO"
DIAS_ARQUIVOS_REDE = 62           # lê da rede os .RET modificados nos últimos N dias
LIMITE_BYTES_REL = 30 * 1024 * 1024
LIMITE_BYTES_BW = 20 * 1024 * 1024
LIMITE_ARQUIVOS_BW = 80
TOLERANCIA_VALOR = 0.005          # centavo
JANELA_DIAS_VENC = 20             # base x relatório: até 20 dias de diferença ainda é o mesmo boleto (vencimento alterado)
STATUS_BAIXADO = ('BAIXADO', 'PAGO')        # status interno da base que quer dizer "já foi para o banco"
JANELA_DIAS_BW = 10               # BW x relatório: data do BW até 10 dias do vencimento lançado
_TRAVA = threading.Lock()

# Cache do /api/conciliacao_bw/dia: navegar entre dias (ou recarregar a página) não relê banco/rede toda
# vez - fica "pelo menos 2-3 dias" guardado. Um refresh explícito (botão "Buscar BW do dia", ?atualizar=1
# na rota) ou qualquer upload/sincronização (relatório, BW, Inc. Bradesco, ler da rede) esquece o cache.
SEGUNDOS_CACHE_DIA = 3 * 24 * 3600
_CACHE_DIA = {}                       # dia útil (AAAA-MM-DD) -> {'quando': ts, 'hoje': 'AAAA-MM-DD', 'dado': {...}}


def _cache_dia_obter(dia_util):
    c = _CACHE_DIA.get(dia_util)
    if not c:
        return None
    # se o dia civil mudou desde que foi guardado, a relação desse dia com "hoje" pode ter mudado
    # (por exemplo: deixou de ser "hoje" e o BW já devia estar disponível) - não vale a pena confiar.
    if time.time() - c['quando'] > SEGUNDOS_CACHE_DIA or c['hoje'] != date.today().isoformat():
        return None
    return c['dado']


def _cache_dia_guardar(dia_util, dado):
    _CACHE_DIA[dia_util] = {'quando': time.time(), 'hoje': date.today().isoformat(), 'dado': dado}


def esquecer_dia(dia_util=None):
    """Chamar depois de qualquer envio ou sincronização que possa mudar o resultado de um dia:
    sem argumento esquece tudo (é o que as rotas de upload/sincronização usam, por segurança)."""
    if dia_util is None:
        _CACHE_DIA.clear()
    else:
        _CACHE_DIA.pop(dia_util, None)

# mesmo padrão da Conciliação de retorno: é a chave do de_para_retorno ("42774/0001")
RX_REF = re.compile(r'(\d{4,5})/(\d{3,4})')
RX_REF_LONGA = re.compile(r'(\d{4,5})/(\d{3,6})(?!\d)')
PALAVRAS_COND = ['condominio', 'cond ', 'cond.', 'residencial', 'edificio', 'edif']
PALAVRAS_COND_NORM = ('CONDOMIN', 'COND ', 'COND.', 'RESIDENCIAL', 'EDIFICIO', 'EDIF ', 'EDIF.')
CNPJ_LELLO = '44166627000192'

# Layout do retorno Bradesco Multipag (PAGFOR), 500 posições, registro de transação "1" (posições do manual, começando em 1)
POS = {
    'inscricao': (3, 17), 'favorecido': (18, 47), 'vencimento': (166, 173), 'valor_pago': (205, 219),
    'modalidade': (264, 265), 'efetivacao': (266, 273), 'situacao': (277, 280), 'sacador': (332, 371),
    'uso_empresa': (416, 450),
}
MODALIDADES_BOLETO = ('30', '31')     # 30 = título Bradesco, 31 = título de outro banco; 01/05/08 = crédito em conta/TED
# favorecidos que aparecem com carteira no BW mas não são condomínio (conta de consumo, fiança, seguro)
# prefeituras (IPTU, taxas): aparecem com carteira do condomínio, mas não fazem parte da conciliação
PREFEITURAS = ('PREF ', 'PREF.', 'PREFEITURA', 'MUNIC ', 'MUNIC.', 'MUNICIP', 'PMSP', 'MUN DE ', 'MUN. DE ')
FAVORECIDOS_NAO_COND = ('ELETROPAULO', 'ENEL ', 'SABESP', 'COMGAS', 'CPFL', 'LIGHT S', 'EDP ', 'LOFT', 'FIANCA',
                        'PORTO S', 'SEGURO', 'CAPITALIZ', 'TELEFONICA', 'CLARO ', 'VIVO ')

FLAG_ROTULO = {'L': 'Lançado', 'B': 'Baixado no portal', 'C': 'Cancelado'}

# Os 17 motivos técnicos ficam por baixo; quem olha a tela vê só 5 GRUPOS (semáforo). Cada categoria diz a qual grupo pertence.
# GRUPOS: chave -> (nome curto, o que significa em uma frase, ordem na tela). Os 3 primeiros são os que pedem ação.
GRUPOS = {
    'TRAS': ('Ficou para trás', 'Estava na base e não terminou o caminho: não foi lançado, o banco não pagou ou devolveu.', 1),
    'ORIGEM': ('Sem origem', 'Apareceu no relatório ou no banco e não achamos de onde veio (não tem par na base ou no lançamento).', 2),
    'DIF': ('Valor ou vencimento diferente', 'Andou, mas não igual ao esperado: outro vencimento ou outro valor pago.', 3),
    'AGUARDA': ('Aguardando', 'Lançado e esperando o banco pagar (ou falta o relatório do mês para conferir).', 4),
    'OK': ('Em ordem', 'Bateu, ou já está explicado e não precisa de ação.', 5),
    'FORA': ('Fora da conta', 'Pagamento do BW que não é condomínio (TED, conta de consumo, fiança, prefeitura...).', 6),
}
GRUPOS_ACAO = ('TRAS', 'ORIGEM', 'DIF')          # os que pedem ação (são as "divergências")

CATEGORIAS = {
    # chave: (rótulo, é divergência?, ordem, grupo)
    'OK': ('Bateu: base, relatório e banco', False, 9, 'OK'),
    'OK_PORTAL': ('Baixado no portal', False, 8, 'OK'),
    'AGUARDANDO': ('Lançado, esperando o banco', False, 7, 'AGUARDA'),
    'NAO_LANCADO': ('Não foi lançado', True, 1, 'TRAS'),
    # saiu da base = já foi lançado. Sem par no relatório é a "visão dupla": o mesmo boleto em 2 linhas da base (desconto
    # ou vencimento alternativo), então não é erro e não entra na conta de divergências
    'VISAO_DUPLA': ('Já lançado (repetido na base)', False, 8, 'OK'),
    'VENC_DIFERENTE': ('Lançado com outro vencimento', True, 3, 'DIF'),
    'VALOR_DIFERENTE': ('Pago com valor diferente', True, 2, 'DIF'),
    'NAO_PAGO_BW': ('Lançado e o banco não pagou', True, 4, 'TRAS'),
    # o Inc. Bradesco (planilha das devoluções do banco) explica por que o BW não pagou
    'DEVOLVIDO_PEND': ('Banco devolveu, ainda pendente', True, 3, 'TRAS'),
    'DEVOLVIDO_TRAT': ('Banco devolveu, já tratado', False, 8, 'OK'),
    'LANC_FORA_BASE': ('Lançado, mas não estava na base', True, 5, 'ORIGEM'),
    'BW_SEM_LANC': ('Pago no banco, sem lançamento', True, 6, 'ORIGEM'),
    # o portal/base marca o boleto como baixado: foi para o banco, então o pagamento do BW é esperado (não é erro)
    'BW_BAIXADO': ('Pago no banco, já baixado', False, 8, 'OK'),
    'LANC_A_MAIS': ('Lançamento a mais que a base', True, 5, 'ORIGEM'),
    'BOLETO_EXTRA': ('2º boleto do imóvel', False, 9, 'OK'),
    'ENTRANDO': ('Veio de outro dia', False, 8, 'OK'),
    'SAINDO': ('Lançado para outro dia', False, 3, 'DIF'),
    'BW_OUTRO': ('Pagamento que não é condomínio', False, 10, 'FORA'),
    'SEM_RELATORIO': ('Mês sem relatório enviado', False, 11, 'AGUARDA'),
    'DESCONTO': ('Pago com desconto (valor líquido)', False, 8, 'OK'),
}


def categorias_api():
    """O que a tela recebe: cada categoria com o rótulo claro e o grupo (semáforo) a que pertence."""
    return {k: {'rotulo': v[0], 'divergencia': v[1], 'ordem': v[2], 'grupo': v[3]} for k, v in CATEGORIAS.items()}


def grupos_api():
    return {k: {'nome': v[0], 'descricao': v[1], 'ordem': v[2], 'acao': k in GRUPOS_ACAO} for k, v in GRUPOS.items()}


class _SemBusca(Exception):
    """Dia de hoje ou futuro: não procura BW na pasta."""


class ErroConciliacao(Exception):
    """Erro com mensagem pronta para a tela."""


# ------------------------------------------------------------------ utilidades
def _num_txt(valor):
    """17698.0 / '17698.0' / ' 17698 ' -> '17698' (o pandas lê número de célula como float)."""
    t = str(valor if valor is not None else '').strip()
    if t.lower() == 'nan':
        return ''
    return t[:-2] if re.match(r'^\d+\.0$', t) else t


def chave_imovel(valor):
    return _num_txt(valor).lstrip('0')


def _iso(valor):
    """'10/11/2026', '2026-11-10', '2026-11-10 00:00:00', datetime -> '2026-11-10' (ou '')."""
    if valor is None:
        return ''
    if isinstance(valor, (datetime, date)):
        return valor.strftime('%Y-%m-%d')
    t = str(valor).strip()
    m = re.match(r'^(\d{1,2})/(\d{1,2})/(\d{4})', t)
    if m:
        try:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1))).isoformat()
        except ValueError:
            return ''
    m = re.match(r'^(\d{4})-(\d{2})-(\d{2})', t)
    if m:
        return f'{m.group(1)}-{m.group(2)}-{m.group(3)}'
    return ''


def _br(iso):
    return f'{iso[8:10]}/{iso[5:7]}/{iso[:4]}' if iso and len(iso) >= 10 else ''


def _valor(v):
    """'1.234,56' / '986,85' / '030,43' / 964.74 -> float (ou None)."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return None if v != v else round(float(v), 2)
    t = str(v).strip().replace('R$', '').replace(' ', '')
    if not t or t.lower() == 'nan':
        return None
    if ',' in t:
        t = t.replace('.', '').replace(',', '.')
    try:
        return round(float(t), 2)
    except ValueError:
        return None


def _rs(v):
    """1493.92 -> 'R$ 1.493,92'"""
    return 'R$ ' + f'{v or 0:,.2f}'.replace(',', '#').replace('.', ',').replace('#', '.')


def _dias(a, b):
    try:
        return abs((date.fromisoformat(a) - date.fromisoformat(b)).days)
    except (TypeError, ValueError):
        return 999


def _feriados(anos):
    try:
        return {t.date() for t in analise_manual._feriados(anos)}
    except Exception:
        return set()


def _dia_util(d, feriados):
    return d.weekday() < 5 and d not in feriados


def _ajuste_dia_util(base_iso, lanc_iso, feriados):
    """Vencimento da base cai em sábado/domingo/feriado e o lançamento foi para o dia útil seguinte (ou anterior)."""
    try:
        b, l = date.fromisoformat(base_iso), date.fromisoformat(lanc_iso)
    except (TypeError, ValueError):
        return False
    if _dia_util(b, feriados):
        return False
    prox, ant = b, b
    while not _dia_util(prox, feriados):
        prox += timedelta(days=1)
    while not _dia_util(ant, feriados):
        ant -= timedelta(days=1)
    return l in (prox, ant)


def venc_util(iso, feriados):
    """Dia em que o boleto é pago: o próprio vencimento, ou o dia útil seguinte (sábado/domingo/feriado)."""
    try:
        d = date.fromisoformat(iso)
    except (TypeError, ValueError):
        return iso or ''
    while not _dia_util(d, feriados):
        d += timedelta(days=1)
    return d.isoformat()


def _norm(txt):
    t = unicodedata.normalize('NFKD', str(txt or '')).encode('ascii', 'ignore').decode('ascii')
    return re.sub(r'\s+', ' ', t).strip().upper()


# ------------------------------------------------------------------ BW (.RET)
def _campo(linha, ini, fim):
    return linha[ini - 1:fim]


def _data8(txt):
    """'20261104' -> '2026-11-04' (ou '')."""
    if not re.fullmatch(r'20\d{6}', txt or ''):
        return ''
    try:
        return date(int(txt[:4]), int(txt[4:6]), int(txt[6:8])).isoformat()
    except ValueError:
        return ''


def _tem_nome_cond(*nomes):
    t = ' '.join(_norm(n) for n in nomes) + ' '
    return any(p in t for p in PALAVRAS_COND_NORM)


def ler_linha_bw(linha):
    """Uma linha do retorno -> dict do pagamento com carteira/sequência, ou None.
    Layout Multipag (500 posições): referência no "uso da empresa" (416-450), vencimento do boleto (166-173),
    data do pagamento (266-273), valor pago (205-219), modalidade (264-265). Só a HDI Seguros fica de fora já aqui."""
    linha = linha.rstrip('\r\n')
    if '02BW' not in linha:
        return None
    if 'hdi seguros' in linha.lower():
        return None
    # só o layout Multipag (o mesmo do PG041107.RET): 02BW na posição 277; outro tipo de .RET da pasta é ignorado
    if len(linha) < 450 or linha[:1] != '1' or _campo(linha, *POS['situacao']) != '02BW':
        return None
    uso = _campo(linha, *POS['uso_empresa'])
    m = RX_REF.search(uso)
    if not m:
        return None
    longa = RX_REF_LONGA.search(uso, m.start())
    favorecido = _campo(linha, *POS['favorecido']).strip()
    sacador = _campo(linha, *POS['sacador']).strip()
    venc = _data8(_campo(linha, *POS['vencimento']))
    pago_em = _data8(_campo(linha, *POS['efetivacao']))
    bruto = _campo(linha, *POS['valor_pago'])
    modalidade = _campo(linha, *POS['modalidade'])
    eh_lello = CNPJ_LELLO in _campo(linha, *POS['inscricao']) or favorecido.upper().startswith('LELLO')
    valor = round(int(bruto) / 100, 2) if bruto.isdigit() else None
    referencia = f'{m.group(1)}/{m.group(2)}'
    ref_longa = f'{longa.group(1)}/{longa.group(2)}' if longa and longa.start() == m.start() else ''
    if _tem_nome_cond(sacador):
        nome = sacador
    elif _tem_nome_cond(favorecido):
        nome = favorecido
    else:
        nome = ''
    return {
        'referencia': referencia,
        'ref_longa': ref_longa if ref_longa != referencia else '',
        'carteira': int(m.group(1)),
        'sufixo': (longa.group(2) if longa and longa.start() == m.start() else m.group(2)),
        'valor': valor,
        'data': venc or pago_em,          # vencimento do boleto (é o que casa com o Dt.Vencto do relatório)
        'data_pag': pago_em or venc,      # dia em que o banco pagou (D-1: chega no arquivo do dia seguinte)
        'modalidade': modalidade,
        'favorecido': favorecido,
        'nome': nome,
        'tem_nome_cond': 1 if nome else 0,
        'eh_lello': 1 if eh_lello else 0,
    }


def dias_pagos_no_arquivo(conteudo):
    """Dias de pagamento (efetivação) que o arquivo cobre: qualquer pagamento efetuado (02BW), com ou sem carteira."""
    texto = conteudo.decode('latin1', errors='replace') if isinstance(conteudo, (bytes, bytearray)) else str(conteudo)
    dias = set()
    for linha in texto.splitlines():
        if len(linha) >= 450 and linha[:1] == '1' and _campo(linha, *POS['situacao']) == '02BW':
            d = _data8(_campo(linha, *POS['efetivacao']))
            if d:
                dias.add(d)
    return dias


def motivo_nao_condominio(b):
    """Por que um pagamento do BW que NÃO casou com o relatório provavelmente não é condomínio ('' = pode ser)."""
    nomes = ' ' + _norm(b.get('favorecido')) + '  ' + _norm(b.get('nome')) + ' '
    if any(p in nomes for p in PREFEITURAS):
        return 'Prefeitura (IPTU/taxa): não entra na conciliação.'
    if b.get('tem_nome_cond') or b.get('eh_lello'):
        return ''
    if b.get('modalidade') and b['modalidade'] not in MODALIDADES_BOLETO:
        return 'Transferência (TED/crédito em conta), não é pagamento de boleto.'
    fav = _norm(b.get('favorecido')) + ' '
    if any(p in fav for p in FAVORECIDOS_NAO_COND):
        return f'Favorecido {b.get("favorecido") or "?"}: conta de consumo, fiança ou seguro.'
    if b.get('sufixo') and set(b['sufixo']) == {'0'}:
        return f'Sequência {b["sufixo"]} ({b.get("favorecido") or "sem nome"}): não aparece em condomínio.'
    return ''


def ler_arquivo_bw(conteudo, nome_arquivo):
    """bytes de um .RET -> lista de pagamentos de condomínio (com hash da linha para não duplicar)."""
    texto = conteudo.decode('latin1', errors='replace') if isinstance(conteudo, (bytes, bytearray)) else str(conteudo)
    saida = []
    for n, linha in enumerate(texto.splitlines(), start=1):
        p = ler_linha_bw(linha)
        if p:
            # mesmo arquivo lido de novo (rede ou envio) não duplica; linhas iguais em posições diferentes contam
            chave_hash = f"{os.path.basename(nome_arquivo).upper()}|{n}|{linha.rstrip(chr(13) + chr(10))}"
            p['hash'] = hashlib.sha1(chave_hash.encode('latin1', errors='replace')).hexdigest()
            p['arquivo'] = nome_arquivo
            p['linha_num'] = n
            saida.append(p)
    return saida


# ------------------------------------------------------------------ banco
def garantir_tabelas(conexao):
    conexao.execute('''CREATE TABLE IF NOT EXISTS conc_relatorio (
        id INTEGER PRIMARY KEY AUTOINCREMENT, imovel TEXT, chave TEXT, carteira INTEGER, contrato TEXT, filial TEXT,
        adm TEXT, emp TEXT, tp_pg TEXT, tp_lanc TEXT, flag TEXT, dt_venc TEXT, dt_lanc TEXT, vr_boleto REAL, vr_desc REAL, obs TEXT)''')
    conexao.execute('''CREATE TABLE IF NOT EXISTS conc_relatorio_meta (
        id INTEGER PRIMARY KEY AUTOINCREMENT, arquivo TEXT, usuario TEXT, carregado_em TEXT, linhas INTEGER, meses TEXT)''')
    colunas = {r[1] for r in conexao.execute("PRAGMA table_info(conc_bw)")}
    if colunas and 'data_pag' not in colunas:
        # versão anterior lia a data do pagamento como vencimento: apaga e relê tudo no layout certo
        conexao.execute('DROP TABLE conc_bw')
        conexao.execute('DROP TABLE IF EXISTS conc_bw_arquivos')
    conexao.execute('''CREATE TABLE IF NOT EXISTS conc_bw (
        id INTEGER PRIMARY KEY AUTOINCREMENT, hash TEXT UNIQUE, arquivo TEXT, origem TEXT, linha_num INTEGER,
        referencia TEXT, ref_longa TEXT, carteira INTEGER, sufixo TEXT, valor REAL, data TEXT, data_pag TEXT,
        modalidade TEXT, favorecido TEXT, nome TEXT, tem_nome_cond INTEGER, eh_lello INTEGER, lido_em TEXT)''')
    conexao.execute('''CREATE TABLE IF NOT EXISTS conc_bw_dias (
        data TEXT, arquivo TEXT, origem TEXT, lido_em TEXT, PRIMARY KEY (data, arquivo))''')
    conexao.execute('''CREATE TABLE IF NOT EXISTS conc_cargas_base (
        id INTEGER PRIMARY KEY AUTOINCREMENT, id_ini INTEGER, id_fim INTEGER, arquivo TEXT, data_hora TEXT)''')
    conexao.execute('''CREATE TABLE IF NOT EXISTS conc_bw_arquivos (
        caminho TEXT PRIMARY KEY, tamanho INTEGER, mtime REAL, linhas INTEGER, lido_em TEXT)''')
    conexao.execute('''CREATE TABLE IF NOT EXISTS conc_inc (
        id INTEGER PRIMARY KEY AUTOINCREMENT, aba TEXT, linha INTEGER, data TEXT, referencia TEXT, carteira INTEGER, chave TEXT,
        doc_pagfor TEXT, venc TEXT, valor REAL, desconto REAL, ocorrencia TEXT, descricao TEXT, situacao TEXT, obs TEXT, conta TEXT)''')
    if 'conta' not in {r[1] for r in conexao.execute('PRAGMA table_info(conc_inc)')}:
        conexao.execute('ALTER TABLE conc_inc ADD COLUMN conta TEXT')
    conexao.execute('''CREATE TABLE IF NOT EXISTS conc_inc_meta (
        id INTEGER PRIMARY KEY AUTOINCREMENT, arquivo TEXT, usuario TEXT, carregado_em TEXT, linhas INTEGER, abas TEXT)''')
    if 'origem' not in {r[1] for r in conexao.execute('PRAGMA table_info(conc_inc_meta)')}:
        conexao.execute('ALTER TABLE conc_inc_meta ADD COLUMN origem TEXT')
    conexao.execute('CREATE INDEX IF NOT EXISTS idx_conc_inc_venc ON conc_inc (venc)')
    conexao.execute('''CREATE TABLE IF NOT EXISTS de_para_retorno (
        referencia TEXT PRIMARY KEY, imovel_real TEXT, nome_sacado TEXT)''')
    conexao.execute('CREATE INDEX IF NOT EXISTS idx_conc_bw_data ON conc_bw (data)')
    conexao.execute('CREATE INDEX IF NOT EXISTS idx_conc_bw_data_pag ON conc_bw (data_pag)')
    conexao.execute('CREATE INDEX IF NOT EXISTS idx_conc_bw_carteira ON conc_bw (carteira)')
    conexao.execute('CREATE INDEX IF NOT EXISTS idx_conc_rel_venc ON conc_relatorio (dt_venc)')


def registrar_carga_base(conexao, id_antes, arquivo='', data_hora=''):
    """Chamado no upload da base: guarda a faixa de ids da carga. Com isso a conciliação sabe quantas LINHAS
    (boletos) cada imóvel + vencimento tem naquela carga. Cargas antigas (sem registro) contam 1 por imóvel."""
    garantir_tabelas(conexao)
    id_fim = conexao.execute('SELECT IFNULL(MAX(id), 0) FROM imoveis').fetchone()[0]
    if id_fim > (id_antes or 0):
        conexao.execute('INSERT INTO conc_cargas_base (id_ini, id_fim, arquivo, data_hora) VALUES (?,?,?,?)',
                        ((id_antes or 0) + 1, id_fim, arquivo, data_hora or datetime.now().strftime('%Y-%m-%d %H:%M:%S')))


def gravar_relatorio(conexao, conteudo, nome_arquivo, usuario):
    """Guarda o relatório. O relBolMov é o acumulado do mês: os meses que vêm no arquivo substituem os mesmos meses
    já guardados; os outros meses ficam (dá para ter outubro e novembro juntos)."""
    try:
        df = analise_manual.ler_relatorio(conteudo, nome_arquivo)
    except analise_manual.ErroRelatorio as e:
        raise ErroConciliacao(str(e))
    linhas = []
    for r in df.to_dict('records'):
        chave = chave_imovel(r.get('imovel'))
        venc = _iso(r.get('dt_venc'))
        if not chave or not venc:
            continue
        cart = chave_imovel(r.get('carteira'))
        linhas.append((
            _num_txt(r.get('imovel')), chave, int(cart) if cart.isdigit() else None,
            chave_imovel(r.get('contrato')), chave_imovel(r.get('filial')),
            str(r.get('adm') or '').strip(), str(r.get('emp') or '').strip(), str(r.get('tp_pg') or '').strip(),
            str(r.get('tp_lanc') or '').strip(), str(r.get('flag') or '').strip().upper(), venc, _iso(r.get('dt_lanc')),
            _valor(r.get('vr_boleto')), _valor(r.get('vr_desc')) or 0.0, str(r.get('obs') or '').strip()[:500]))
    if not linhas:
        raise ErroConciliacao(f'{nome_arquivo}: não achei nenhuma linha com imóvel e Dt.Vencto.')
    garantir_tabelas(conexao)
    meses = sorted({l[10][:7] for l in linhas})
    conexao.executemany('DELETE FROM conc_relatorio WHERE substr(dt_venc, 1, 7) = ?', [(m,) for m in meses])
    conexao.executemany('''INSERT INTO conc_relatorio (imovel, chave, carteira, contrato, filial, adm, emp, tp_pg, tp_lanc, flag,
                           dt_venc, dt_lanc, vr_boleto, vr_desc, obs) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''', linhas)
    conexao.execute('INSERT INTO conc_relatorio_meta (arquivo, usuario, carregado_em, linhas, meses) VALUES (?,?,?,?,?)',
                    (nome_arquivo, usuario, datetime.now().strftime('%Y-%m-%d %H:%M:%S'), len(linhas), ','.join(meses)))
    return {'linhas': len(linhas), 'meses': meses}


def _data_celula(v):
    """Célula de data: data de verdade (datetime), número de série do Excel/Google, ou texto dd/mm/aaaa -> 'aaaa-mm-dd'."""
    if v is None or v == '':
        return ''
    if isinstance(v, (datetime, date)):
        return v.strftime('%Y-%m-%d')
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        if 20000 < v < 80000:
            return (date(1899, 12, 30) + timedelta(days=int(v))).isoformat()
        return ''
    t = str(v).strip()
    m = re.match(r'^(\d{4})-(\d{2})-(\d{2})', t)
    if m:
        return f'{m[1]}-{m[2]}-{m[3]}'
    m = re.match(r'^(\d{1,2})/(\d{1,2})/(\d{4})', t)
    if m:
        return f'{m[3]}-{int(m[2]):02d}-{int(m[1]):02d}'
    return ''


def _aba_inc(titulo):
    return bool(re.match(r'^\s*loc\s*574', titulo or '', re.I))


def _linhas_inc_da_aba(titulo, linhas):
    """Linhas (lista de células) de uma aba Loc574 -> tuplas prontas para o banco. Devolve (tuplas, achou_cabeçalho)."""
    saida, cab = [], None
    for num, r in enumerate(linhas, 1):
        if cab is None:
            nomes = [_norm(c) for c in r]
            if 'REFERENCIA' in nomes and 'VENCIMENTO' in nomes:
                cab = {}
                for i, n_ in enumerate(nomes):
                    cab.setdefault(n_, i)
            continue

        def col(nome):
            i = cab.get(nome)
            return r[i] if i is not None and i < len(r) else None
        venc = _data_celula(col('VENCIMENTO'))
        ref = str(col('REFERENCIA') or '').strip()
        if not venc or not ref:
            continue
        cart = chave_imovel(ref.partition('/')[0])
        chave = chave_imovel(col('ID'))
        saida.append((titulo.strip(), num, _data_celula(col('DATA')), ref, int(cart) if cart.isdigit() else None,
                      chave if chave and chave != '0' else '', re.sub(r'\.0$', '', str(col('DOC. PAGFOR') or '').strip()), venc,
                      _valor(col('VALOR')), _valor(col('DESCONTO')) or 0.0,
                      re.sub(r'\s+', '', str(col('OCORRENCIA') or '')).upper(),
                      str(col('DESCRICAO OCORRENCIA') or '').strip(), str(col('SITUACAO') or '').strip().upper(),
                      re.sub(r'\s+', ' ', str(col('OBSERVACAO') or '')).strip()[:300],
                      re.sub(r'\.0$', '', str(col('CONTA') or '').strip())))
    return saida, cab is not None


def ler_inc_bradesco(conteudo, nome_arquivo):
    """Planilha "Inc. Bradesco" enviada em .xlsx. Usa só as abas Loc574 (boleto de condomínio devolvido):
    Referência (carteira/seq), ID (= nº do imóvel), Doc. Pagfor, Vencimento, Valor, Ocorrência, Descrição, Situação, Observação, Conta.
    As abas de repasse ao proprietário (Loc569/Loc576) e o IPTU (Cpg860) não são boleto de condomínio e ficam de fora."""
    try:
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(conteudo), read_only=True, data_only=True)
    except Exception as e:
        raise ErroConciliacao(f'{nome_arquivo}: não consegui abrir a planilha ({e.__class__.__name__}).')
    linhas, abas = [], []
    for ws in wb.worksheets:
        if not _aba_inc(ws.title):
            continue
        l, achou = _linhas_inc_da_aba(ws.title, ws.iter_rows(values_only=True))
        if achou:
            linhas += l
            abas.append(ws.title.strip())
    if not abas:
        raise ErroConciliacao(f'{nome_arquivo}: não achei nenhuma aba Loc574 com Referência e Vencimento.')
    return linhas, abas


# ---- Google Sheets (a mesma conta de serviço do sistema; a planilha precisa estar compartilhada com ela)
PLANILHA_INC_ID = os.environ.get('CONC_INC_PLANILHA_ID') or '1CYkeMgukuI8jy1ZN-3cKYBA2qMS3Qb8wPMcI_Dm9ZLc'
CREDENCIAIS_PADRAO = os.environ.get('CONC_CREDENCIAIS') or r'C:\Users\ryan.lima\Documents\SistemaImoveis\credenciais.json'   # a mesma conta de serviço do app.py
INC_MINUTOS = 10                  # não consulta o Google de novo antes disso (a não ser no botão Atualizar)
INC_FALHA_MINUTOS = 2             # depois de uma falha, não insiste a cada tela aberta
_INC_FALHA = {'em': None, 'msg': ''}


def _servico_sheets(credenciais):
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    creds = service_account.Credentials.from_service_account_file(
        credenciais, scopes=['https://www.googleapis.com/auth/spreadsheets.readonly'])
    return build('sheets', 'v4', credentials=creds, cache_discovery=False)


def ler_inc_google(planilha_id, credenciais, so_ano=None):
    """Lê as abas Loc574 direto do Google Sheets. so_ano ('2026'): só as abas cujo nome tem esse ano (as outras são histórico)."""
    servico = _servico_sheets(credenciais)
    meta = servico.spreadsheets().get(spreadsheetId=planilha_id, fields='sheets.properties.title').execute()
    titulos = [s['properties']['title'] for s in meta.get('sheets', []) if _aba_inc(s['properties']['title'])]
    if so_ano:
        recentes = [t for t in titulos if so_ano in t]
        titulos = recentes or titulos
    if not titulos:
        raise ErroConciliacao('A planilha do Google não tem nenhuma aba Loc574.')
    linhas, abas = [], []
    for t in titulos:
        valores = servico.spreadsheets().values().get(
            spreadsheetId=planilha_id, range="'" + t.replace("'", "''") + "'", valueRenderOption='UNFORMATTED_VALUE',
            dateTimeRenderOption='SERIAL_NUMBER').execute().get('values', [])
        l, achou = _linhas_inc_da_aba(t, valores)
        if achou:
            linhas += l
            abas.append(t.strip())
    if not abas:
        raise ErroConciliacao('Não achei Referência e Vencimento nas abas Loc574 do Google.')
    return linhas, abas


def _msg_erro_google(e):
    status = getattr(getattr(e, 'resp', None), 'status', None)
    if isinstance(e, FileNotFoundError):
        return 'Não achei o arquivo de credenciais do Google (credenciais.json) no servidor.'
    if isinstance(e, ImportError):
        return 'Faltam as bibliotecas do Google no servidor (google-api-python-client).'
    if status in (403, 404):
        return ('O Google negou o acesso à planilha Inc. Bradesco: confira se ela está compartilhada com a conta de serviço '
                '(robo-planilha@n8n-automacao-leads-465612.iam.gserviceaccount.com).')
    if isinstance(e, ErroConciliacao):
        return str(e)
    return f'Não consegui ler a planilha do Google ({e.__class__.__name__}).'


def gravar_inc_linhas(conexao, linhas, abas, arquivo, usuario, origem):
    garantir_tabelas(conexao)
    # a planilha é acumulada: cada aba lida substitui a mesma aba já guardada
    conexao.executemany('DELETE FROM conc_inc WHERE aba = ?', [(a,) for a in abas])
    conexao.executemany("INSERT INTO conc_inc (aba, linha, data, referencia, carteira, chave, doc_pagfor, venc, valor, desconto, "
                        "ocorrencia, descricao, situacao, obs, conta) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", linhas)
    conexao.execute('INSERT INTO conc_inc_meta (arquivo, usuario, carregado_em, linhas, abas, origem) VALUES (?,?,?,?,?,?)',
                    (arquivo, usuario, datetime.now().strftime('%Y-%m-%d %H:%M:%S'), len(linhas), ', '.join(abas), origem))
    return {'linhas': len(linhas), 'abas': abas, 'ate_venc': max((l[7] for l in linhas), default='')}


def gravar_inc(conexao, conteudo, nome_arquivo, usuario):
    linhas, abas = ler_inc_bradesco(conteudo, nome_arquivo)
    return gravar_inc_linhas(conexao, linhas, abas, nome_arquivo, usuario, 'upload')


def sincronizar_inc(conexao, credenciais=None, planilha_id=None, forcar=False, usuario='Sistema'):
    """Atualiza o Inc. Bradesco direto do Google Sheets. Nunca levanta erro: devolve {'ok', 'msg', 'lido_agora', 'linhas'}.
    Sem forçar, só consulta o Google se a última leitura tem mais de INC_MINUTOS minutos."""
    garantir_tabelas(conexao)
    ultima = conexao.execute("SELECT carregado_em, linhas FROM conc_inc_meta WHERE origem = 'google' ORDER BY id DESC LIMIT 1").fetchone()
    if ultima and not forcar:
        try:
            if datetime.now() - datetime.strptime(ultima[0], '%Y-%m-%d %H:%M:%S') < timedelta(minutes=INC_MINUTOS):
                return {'ok': True, 'msg': '', 'lido_agora': False, 'linhas': ultima[1]}
        except ValueError:
            pass
    if not forcar and _INC_FALHA['em'] and datetime.now() - _INC_FALHA['em'] < timedelta(minutes=INC_FALHA_MINUTOS):
        return {'ok': False, 'msg': _INC_FALHA['msg'], 'lido_agora': False, 'linhas': ultima[1] if ultima else 0}
    try:
        so_ano = None if not ultima else str(date.today().year)       # a primeira vez traz o histórico; depois só o ano corrente
        linhas, abas = ler_inc_google(planilha_id or PLANILHA_INC_ID, credenciais or CREDENCIAIS_PADRAO, so_ano)
        info = gravar_inc_linhas(conexao, linhas, abas, 'Google Sheets · Inc. Bradesco', usuario, 'google')
        conexao.commit()
        _INC_FALHA['em'] = None
        return {'ok': True, 'msg': f'Inc. Bradesco atualizado do Google: {info["linhas"]} devoluções ({", ".join(abas)}).',
                'lido_agora': True, 'linhas': info['linhas']}
    except Exception as e:
        try:
            conexao.rollback()
        except Exception:
            pass
        _INC_FALHA['em'], _INC_FALHA['msg'] = datetime.now(), _msg_erro_google(e)
        return {'ok': False, 'msg': _INC_FALHA['msg'], 'lido_agora': False, 'linhas': ultima[1] if ultima else 0}


def gravar_bw(conexao, pagamentos, origem, dias=None, arquivo=''):
    """Grava os pagamentos (o mesmo arquivo/linha não entra duas vezes) e os dias de pagamento que o arquivo cobre."""
    garantir_tabelas(conexao)
    agora = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    antes = conexao.execute('SELECT COUNT(*) FROM conc_bw').fetchone()[0]
    conexao.executemany('''INSERT OR IGNORE INTO conc_bw (hash, arquivo, origem, linha_num, referencia, ref_longa, carteira, sufixo,
                           valor, data, data_pag, modalidade, favorecido, nome, tem_nome_cond, eh_lello, lido_em)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                        [(p['hash'], p['arquivo'], origem, p['linha_num'], p['referencia'], p['ref_longa'], p['carteira'],
                          p['sufixo'], p['valor'], p['data'], p.get('data_pag') or p['data'], p.get('modalidade', ''),
                          p.get('favorecido', ''), p['nome'], p['tem_nome_cond'], p['eh_lello'], agora)
                         for p in pagamentos])
    todos_dias = set(dias or ()) | {p.get('data_pag') for p in pagamentos if p.get('data_pag')}
    conexao.executemany('INSERT OR IGNORE INTO conc_bw_dias (data, arquivo, origem, lido_em) VALUES (?,?,?,?)',
                        [(d, arquivo or (pagamentos[0]['arquivo'] if pagamentos else ''), origem, agora) for d in sorted(todos_dias)])
    return conexao.execute('SELECT COUNT(*) FROM conc_bw').fetchone()[0] - antes


def _pastas_rede(pastas=None):
    base = os.getenv('PASTA_RETORNO_BW', PASTA_BW_PADRAO)
    pastas = pastas or [base, os.path.join(base, 'PAGFOR')]
    return base, [p for p in pastas if os.path.isdir(p)]


def _arquivos_ret(pastas):
    """(caminho, stat) de cada .RET das pastas, sem repetir (no Windows *.RET e *.ret devolvem o mesmo arquivo)."""
    vistos, saida = set(), []
    for pasta in pastas:
        for caminho in glob.glob(os.path.join(pasta, '*.RET')) + glob.glob(os.path.join(pasta, '*.ret')):
            chave = os.path.normcase(caminho)
            if chave in vistos:
                continue
            vistos.add(chave)
            try:
                saida.append((caminho, os.stat(caminho)))
            except OSError:
                continue
    return saida


def _ler_arquivo_da_rede(conexao, caminho, st):
    """Lê um .RET da rede se ainda não foi lido (ou mudou). Devolve (leu, pagamentos novos)."""
    ja = conexao.execute('SELECT tamanho, mtime FROM conc_bw_arquivos WHERE caminho = ?', (caminho,)).fetchone()
    if ja and ja[0] == st.st_size and abs((ja[1] or 0) - st.st_mtime) < 1:
        return False, 0
    try:
        with open(caminho, 'rb') as f:
            conteudo = f.read()
    except OSError:
        return False, 0
    pags = ler_arquivo_bw(conteudo, os.path.basename(caminho))
    novos = gravar_bw(conexao, pags, 'rede', dias_pagos_no_arquivo(conteudo), os.path.basename(caminho))
    conexao.execute('INSERT OR REPLACE INTO conc_bw_arquivos (caminho, tamanho, mtime, linhas, lido_em) VALUES (?,?,?,?,?)',
                    (caminho, st.st_size, st.st_mtime, len(pags), datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
    return True, novos


def ler_bw_da_rede(conexao, pastas=None, dias=DIAS_ARQUIVOS_REDE):
    """Lê os .RET da pasta do banco (e da PAGFOR) modificados nos últimos `dias`. Arquivo já lido e sem
    mudança (mesmo tamanho e data) não é relido. Devolve um resumo para a tela."""
    garantir_tabelas(conexao)
    base, existentes = _pastas_rede(pastas)
    if not existentes:
        return {'acessivel': False, 'msg': f'Não consegui abrir a pasta do retorno ({base}). Envie o arquivo .RET pela tela.',
                'arquivos_lidos': 0, 'linhas_novas': 0}
    corte = datetime.now().timestamp() - dias * 86400
    lidos = novos = 0
    for caminho, st in _arquivos_ret(existentes):
        if st.st_mtime < corte:
            continue
        leu, n = _ler_arquivo_da_rede(conexao, caminho, st)
        lidos += 1 if leu else 0
        novos += n
    return {'acessivel': True, 'arquivos_lidos': lidos, 'linhas_novas': novos,
            'msg': f'{lidos} arquivo(s) novo(s) lido(s) da rede, {novos} pagamento(s) novo(s).' if lidos else 'Nenhum arquivo novo na pasta do retorno.'}


def buscar_bw_do_dia(conexao, dia_iso, pastas=None):
    """Busca na pasta do retorno o BW dos pagamentos de um dia (é D-1: o arquivo do dia 04 chega no fim do dia 04
    ou no dia 05). Candidatos: nome com o dia e o mês (PG041107.RET = dia 04/11) ou gravado do dia até 5 dias depois.
    Lê só o que ainda não foi lido. Devolve o que achou para a tela."""
    garantir_tabelas(conexao)
    base, existentes = _pastas_rede(pastas)
    ja_tem = [r[0] for r in conexao.execute('SELECT DISTINCT arquivo FROM conc_bw_dias WHERE data = ? ORDER BY arquivo', (dia_iso,))]
    if not existentes:
        return {'acessivel': False, 'arquivos': ja_tem, 'arquivos_lidos': 0, 'linhas_novas': 0,
                'msg': f'Não consegui abrir a pasta do retorno ({base}). Envie o arquivo .RET do dia pela tela.'}
    d = date.fromisoformat(dia_iso)
    ini = datetime(d.year, d.month, d.day).timestamp()
    rx_nome = re.compile(r'^[A-Z_]*' + d.strftime('%d%m') + r'\d{0,3}\.RET$')
    lidos = novos = 0
    for caminho, st in _arquivos_ret(existentes):
        pelo_nome = bool(rx_nome.match(os.path.basename(caminho).upper())) and abs(st.st_mtime - ini) < 40 * 86400
        pela_data = ini <= st.st_mtime < ini + 5 * 86400
        if not (pelo_nome or pela_data):
            continue
        leu, n = _ler_arquivo_da_rede(conexao, caminho, st)
        lidos += 1 if leu else 0
        novos += n
    arquivos = [r[0] for r in conexao.execute('SELECT DISTINCT arquivo FROM conc_bw_dias WHERE data = ? ORDER BY arquivo', (dia_iso,))]
    return {'acessivel': True, 'arquivos': arquivos, 'arquivos_lidos': lidos, 'linhas_novas': novos,
            'msg': (f'BW de {_br(dia_iso)[:5]}: ' + ', '.join(arquivos)) if arquivos else f'Nenhum BW com pagamentos de {_br(dia_iso)[:5]} na pasta do retorno.'}


# ------------------------------------------------------------------ cruzamento BW x relatório
def chave_de_para(pag):
    """Chave do de-para de um pagamento: a referência inteira quando a sequência tem 5 ou mais dígitos
    (ex.: 44785/22421), senão a de 4 dígitos de sempre (42774/0001). Sem isso, 44785/22421 e 44785/22425
    virariam a mesma chave (44785/2242) e um imóvel herdaria o pagamento do outro."""
    return pag.get('ref_longa') or pag['referencia']


def _valor_bate(valor_bw, rel):
    if valor_bw is None or rel.get('vr_boleto') is None:
        return False
    vr = rel['vr_boleto']
    desc = rel.get('vr_desc') or 0.0
    return abs(valor_bw - vr) <= TOLERANCIA_VALOR or (desc > 0 and abs(valor_bw - (vr - desc)) <= TOLERANCIA_VALOR)


def casar_bw_com_relatorio(bws, rels, de_para):
    """Liga cada pagamento do BW a no máximo um lançamento do relatório (e vice-versa).
    Ordem de força: imóvel já conhecido (de-para / sufixo = nº do imóvel) > mesmo valor > mesma data.
    Devolve {indice_bw: (indice_rel, pontos, ambíguo, valor_bateu)}."""
    por_carteira = defaultdict(list)
    for j, r in enumerate(rels):
        # lançamento cancelado (C) não recebe pagamento: se o BW pagou, aparece como "pago sem lançamento"
        if r.get('carteira') is not None and r.get('flag') != 'C':
            por_carteira[r['carteira']].append(j)
    pares = []
    melhores = {}
    for i, b in enumerate(bws):
        cands = por_carteira.get(b['carteira'], [])
        if not cands:
            continue
        dica = set()
        im_dp = chave_imovel(de_para.get(chave_de_para(b), ''))
        if im_dp:
            dica.add(im_dp)
        for suf in (b.get('sufixo'), (b.get('ref_longa') or '').split('/')[-1]):
            s = chave_imovel(suf)
            if s and any(rels[j]['chave'] == s for j in cands):
                dica.add(s)
        mesmo_dia = [j for j in cands if b.get('data') and rels[j]['dt_venc'] == b['data']]
        notas = []
        for j in cands:
            r = rels[j]
            dica_ok = r['chave'] in dica
            bate = _valor_bate(b.get('valor'), r)
            sem_data = not b.get('data')
            dd = 0 if sem_data else _dias(b['data'], r['dt_venc'])
            # quando aceitar o par:
            #  - mesmo valor e data perto (ou imóvel já conhecido pelo de-para/sufixo)
            #  - imóvel conhecido, valor diferente, mas data colada (até 3 dias): é o mesmo boleto pago com outro valor
            #  - único lançamento da carteira naquele dia (valor diferente): idem
            aceita = ((bate and (dd <= JANELA_DIAS_BW or dica_ok))
                      or (dica_ok and dd <= 3)
                      or (len(mesmo_dia) == 1 and j == mesmo_dia[0]))
            # TED, conta de luz, fiança, sequência 0000...: só vira condomínio se o valor bater com um lançamento
            if not aceita or (b.get('_motivo') and not bate):
                continue
            pts = (100 if dica_ok else 0) + (50 if bate else 0)
            if not sem_data:
                pts += 20 if dd == 0 else (10 if dd <= 3 else (5 if dd <= JANELA_DIAS_BW else 0))
            if r.get('flag') == 'B':
                pts += 2
            notas.append(pts)
            pares.append((pts, i, j, bate))
        notas.sort(reverse=True)
        melhores[i] = notas
    pares.sort(key=lambda x: (-x[0], x[1], x[2]))
    usados_bw, usados_rel, saida = set(), set(), {}
    for pts, i, j, bate in pares:
        if i in usados_bw or j in usados_rel:
            continue
        n = melhores.get(i, [])
        ambiguo = len(n) > 1 and n[0] == n[1] and pts >= 50
        saida[i] = (j, pts, ambiguo, bate)
        usados_bw.add(i)
        usados_rel.add(j)
    return saida


def parear_baixados(bws, rels, lig, de_para):
    """Pagamento do BW ainda sem par + lançamento B (baixado no portal) ainda sem pagamento, mesma carteira e mesmo mês:
    baixado quer dizer que o boleto foi para o banco, então são o mesmo boleto (mesmo que valor ou dia não batam).
    Só pareia quando é único dos dois lados (ou o imóvel já é conhecido): na dúvida, não pareia.
    Devolve {indice_bw: indice_rel} dos pares novos (já gravados em lig)."""
    usados_rel = {v[0] for v in lig.values()}
    livres = defaultdict(list)
    for j, r in enumerate(rels):
        if r.get('flag') == 'B' and r.get('carteira') is not None and j not in usados_rel:
            livres[r['carteira']].append(j)
    sem_par = defaultdict(list)
    for i, b in enumerate(bws):
        if i not in lig and not b.get('_motivo') and b.get('carteira') is not None and b.get('data'):
            sem_par[b['carteira']].append(i)
    novos = {}
    for cart, idx in sem_par.items():
        for i in idx:
            b = bws[i]
            conhecidos = {c for c in (chave_imovel(de_para.get(chave_de_para(b), '')), chave_imovel(b.get('sufixo'))) if c}
            cands = [j for j in livres.get(cart, []) if j not in novos.values() and rels[j]['dt_venc'][:7] == b['data'][:7]]
            if conhecidos:                       # imóvel conhecido: o lançamento B dele
                cands = [j for j in cands if rels[j]['chave'] in conhecidos]
            elif len([k for k in idx if k not in novos and bws[k]['data'][:7] == b['data'][:7]]) != 1:
                continue                         # carteira com vários pagamentos soltos e imóvel desconhecido: não arrisca
            if len(cands) == 1:
                novos[i] = cands[0]
                lig[i] = (cands[0], 1, False, _valor_bate(b.get('valor'), rels[cands[0]]))
    return novos


def aprender_de_para(conexao, bws, rels, ligacoes, de_para):
    """Referência nova (ex.: 44785/0001) ligada com segurança a um imóvel (mesma carteira + mesmo valor,
    sem empate) entra no de-para: a Conciliação de retorno deixa de mandar ela para o robô conversor."""
    novos = []
    for i, (j, pts, ambiguo, bate) in ligacoes.items():
        b = bws[i]
        chave = chave_de_para(b)
        if ambiguo or not bate or chave in de_para:
            continue
        novos.append((chave, rels[j]['imovel'], 'AUTO: carteira + valor (conciliação BW)'))
        de_para[chave] = rels[j]['imovel']
    if novos:
        conexao.executemany('INSERT OR IGNORE INTO de_para_retorno (referencia, imovel_real, nome_sacado) VALUES (?, ?, ?)', novos)
    return len(novos)


# ------------------------------------------------------------------ a conciliação
def meses_disponiveis(conexao):
    garantir_tabelas(conexao)
    meses = set()
    meta = conexao.execute('SELECT meses FROM conc_relatorio_meta ORDER BY id DESC LIMIT 1').fetchone()
    if meta and meta[0]:
        meses.update(m for m in meta[0].split(',') if m)
    try:
        for r in conexao.execute("SELECT DISTINCT substr(vencimento, 7, 4) || '-' || substr(vencimento, 4, 2) FROM imoveis WHERE ativo = 1"):
            if r[0] and re.match(r'^\d{4}-\d{2}$', r[0]):
                meses.add(r[0])
    except Exception:
        pass
    return sorted(meses)


def mes_padrao(conexao):
    """O mês com mais lançamentos no último relatório; sem relatório, o mês dominante da base ativa."""
    garantir_tabelas(conexao)
    r = conexao.execute("SELECT substr(dt_venc, 1, 7) m, COUNT(*) q FROM conc_relatorio GROUP BY m ORDER BY q DESC LIMIT 1").fetchone()
    if r and r[0]:
        return r[0]
    try:
        r = conexao.execute("""SELECT substr(vencimento, 7, 4) || '-' || substr(vencimento, 4, 2) m, COUNT(*) q
                               FROM imoveis WHERE ativo = 1 GROUP BY m ORDER BY q DESC LIMIT 1""").fetchone()
        if r and r[0]:
            return r[0]
    except Exception:
        pass
    return datetime.now().strftime('%Y-%m')


def _carregar_base(conexao, ini_iso, fim_iso):
    """Todos os boletos com vencimento no período em QUALQUER carga da base (imóvel + vencimento),
    com a foto mais nova de cada um."""
    d_ini, d_fim = date.fromisoformat(ini_iso), date.fromisoformat(fim_iso)
    meses, d = [], date(d_ini.year, d_ini.month, 1)
    while d <= d_fim:
        meses.append((d.year, d.month))
        d = date(d.year + (d.month == 12), d.month % 12 + 1, 1)
    boletos = {}
    for ano, mes in meses:
        for r in conexao.execute(
                """SELECT id, imovel, vencimento, ativo, status_interno, carteira, administradora, empreendimento, sacado
                   FROM imoveis WHERE vencimento LIKE ?""", (f'%/{mes:02d}/{ano}%',)):
            ch = chave_imovel(r['imovel'])
            venc = _iso(r['vencimento'])
            if not ch or not venc or not (ini_iso <= venc <= fim_iso):
                continue
            k = (ch, venc)
            b = boletos.get(k)
            if b is None:
                b = boletos[k] = {'chave': ch, 'imovel': str(r['imovel']).strip(), 'venc': venc, 'na_base_atual': False,
                                  'status': '', 'carteira': None, 'adm': '', 'emp': '', 'sacado': '', '_id': -1, '_ids': []}
            b['_ids'].append(r['id'])
            if r['ativo'] == 1:
                b['na_base_atual'] = True
            if r['id'] > b['_id']:
                b['_id'] = r['id']
                b['status'] = str(r['status_interno'] or '').strip()
                cart = chave_imovel(r['carteira'])
                b['carteira'] = int(cart) if cart.isdigit() else None
                b['adm'] = str(r['administradora'] or '').strip()
                b['emp'] = str(r['empreendimento'] or '').strip()
                b['sacado'] = str(r['sacado'] or '').strip()
    # Quantos boletos (LINHAS) o imóvel tem naquele vencimento: conta as linhas dentro da carga mais nova em que ele
    # aparece. Só dá para saber nas cargas registradas (a partir do ajuste de "cada linha é um boleto"); nas cargas
    # antigas conta 1 por imóvel.
    try:
        cargas = [tuple(r) for r in conexao.execute('SELECT id_ini, id_fim FROM conc_cargas_base ORDER BY id_ini')]
    except Exception:
        cargas = []
    # a base atual (ativo = 1) já veio com uma linha por boleto: vale como carga mesmo sem registro
    atual = conexao.execute('SELECT MIN(id), MAX(id) FROM imoveis WHERE ativo = 1').fetchone()
    if atual and atual[0] is not None and not any(a <= atual[1] and atual[0] <= z for a, z in cargas):
        cargas.append((atual[0], atual[1]))
    for b in boletos.values():
        b['linhas'], b['por_linha'] = 1, False
        ult = b['_id']
        faixa = next(((a, z) for a, z in cargas if a <= ult <= z), None)
        if faixa:
            b['linhas'] = max(1, sum(1 for i in b['_ids'] if faixa[0] <= i <= faixa[1]))
            b['por_linha'] = True
        else:
            # carga antiga sem registro (o boleto já saiu da base atual): as linhas do mesmo boleto entram juntas
            # na planilha, então conta as linhas coladas à mais nova (ids a até 50 de distância). Na dúvida fica 1.
            ids = sorted(b['_ids'], reverse=True)
            n = 1
            for x, y in zip(ids, ids[1:]):
                if x - y > 50:
                    break
                n += 1
            b['linhas'] = n
        del b['_ids']
    return boletos


DIAS_SEMANA = ['seg', 'ter', 'qua', 'qui', 'sex', 'sáb', 'dom']


def _rotulo_dia(iso, feriados):
    d = date.fromisoformat(iso)
    txt = DIAS_SEMANA[d.weekday()]
    return txt + (' · feriado' if d.weekday() < 5 and d in feriados else '')


def dia_util_anterior(iso, feriados):
    d = date.fromisoformat(iso) - timedelta(days=1)
    while not _dia_util(d, feriados):
        d -= timedelta(days=1)
    return d.isoformat()


def dia_util_seguinte(iso, feriados):
    d = date.fromisoformat(iso) + timedelta(days=1)
    while not _dia_util(d, feriados):
        d += timedelta(days=1)
    return d.isoformat()


def periodo_do_mes(ano, mes, feriados):
    """Vencimentos que caem nos dias úteis do mês. Sábado, domingo e feriado entram no dia útil seguinte, então o
    período começa no dia seguinte ao último dia útil do mês anterior (ex.: novembro/2026 começa no sábado 31/10,
    que entra em 03/11 junto com o domingo 01/11 e o feriado 02/11) e termina no último dia útil do mês."""
    pri = date(ano, mes, 1)
    while not _dia_util(pri, feriados):
        pri += timedelta(days=1)
    ini = date.fromisoformat(dia_util_anterior(pri.isoformat(), feriados)) + timedelta(days=1)
    ult = date(ano + (mes == 12), mes % 12 + 1, 1) - timedelta(days=1)
    while not _dia_util(ult, feriados):
        ult -= timedelta(days=1)
    return ini.isoformat(), ult.isoformat()


def conciliar_periodo(conexao, ini_iso, fim_iso, hoje=None, aprender=True):
    """Concilia base × relatório × BW para os vencimentos do período, agrupando por DIA ÚTIL: o boleto que vence no
    sábado, domingo ou feriado entra no dia útil seguinte (é quando a Lello lança e paga)."""
    garantir_tabelas(conexao)
    hoje = hoje or date.today().isoformat()
    d_ini, d_fim = date.fromisoformat(ini_iso), date.fromisoformat(fim_iso)
    feriados = _feriados({d_ini.year - 1, d_ini.year, d_fim.year, d_fim.year + 1})

    def grupo(iso):
        return venc_util(iso, feriados)

    def no_periodo(iso):
        return bool(iso) and ini_iso <= iso <= fim_iso

    janela_ini = (d_ini - timedelta(days=JANELA_DIAS_BW)).isoformat()
    janela_fim = (d_fim + timedelta(days=JANELA_DIAS_BW)).isoformat()

    base = _carregar_base(conexao, ini_iso, fim_iso)
    meses_rel = {r[0] for r in conexao.execute('SELECT DISTINCT substr(dt_venc, 1, 7) FROM conc_relatorio')}
    rels = [dict(r) for r in conexao.execute(
        '''SELECT id, imovel, chave, carteira, contrato, adm, emp, tp_pg, tp_lanc, flag, dt_venc, dt_lanc,
                  vr_boleto, vr_desc FROM conc_relatorio WHERE dt_venc BETWEEN ? AND ? ORDER BY id''', (janela_ini, janela_fim))]
    bws = [dict(r) for r in conexao.execute(
        '''SELECT id, arquivo, referencia, ref_longa, carteira, sufixo, valor, data, data_pag, modalidade, favorecido, nome,
                  tem_nome_cond, eh_lello
           FROM conc_bw WHERE data = '' OR data IS NULL OR data BETWEEN ? AND ? ORDER BY id''', (janela_ini, janela_fim))]
    for b in bws:
        b['_motivo'] = motivo_nao_condominio(b)
    # dias de pagamento que já têm BW (o arquivo é D-1: o de hoje traz os pagamentos de ontem)
    dias_pag = {r[0] for r in conexao.execute('SELECT DISTINCT data FROM conc_bw_dias WHERE data BETWEEN ? AND ?',
                                              (janela_ini, (d_fim + timedelta(days=JANELA_DIAS_BW + 5)).isoformat()))}
    de_para = {}
    for r in conexao.execute('SELECT referencia, imovel_real FROM de_para_retorno'):
        if r[0]:
            de_para[r[0]] = str(r[1] or '').strip()

    # ---------- BW x relatório
    lig = casar_bw_com_relatorio(bws, rels, de_para)
    aprendidos = aprender_de_para(conexao, bws, rels, lig, de_para) if aprender else 0
    pares_baixado = parear_baixados(bws, rels, lig, de_para)      # BW + lançamento "baixado" que o cruzamento normal deixou solto
    bw_do_rel = {j: (i, ambiguo, bate) for i, (j, pts, ambiguo, bate) in lig.items()}

    def bw_chegou(venc_iso):
        return grupo(venc_iso) in dias_pag

    # mesmo valor em 2 imóveis da mesma carteira só vale aviso se um deles ficou sem pagamento
    sem_pag_cv = {(r.get('carteira'), round(r['vr_boleto'] or 0, 2)) for j, r in enumerate(rels)
                  if j not in bw_do_rel and r['flag'] != 'C'}

    # ---------- base x relatório (por imóvel)
    rel_per = [j for j, r in enumerate(rels) if no_periodo(r['dt_venc'])]
    validos_por_im = defaultdict(list)
    canc_por_im = defaultdict(list)
    for j in rel_per:
        (canc_por_im if rels[j]['flag'] == 'C' else validos_por_im)[rels[j]['chave']].append(j)
    base_por_im = defaultdict(list)
    for b in base.values():
        b['lancs'], b['vinc'] = [], ''
        base_por_im[b['chave']].append(b)
    rel_usado = set()
    for ch, bols in base_por_im.items():
        por_data = defaultdict(list)
        for j in validos_por_im.get(ch, []):
            por_data[rels[j]['dt_venc']].append(j)
        sobra = []
        for b in sorted(bols, key=lambda x: x['venc']):
            if b['venc'] in por_data:
                b['lancs'], b['vinc'] = por_data.pop(b['venc']), 'mesmo'
            else:
                sobra.append(b)
        resto = []
        for b in sobra:            # fim de semana / feriado lançado no dia útil: é o mesmo boleto, não é divergência
            d = next((x for x in sorted(por_data) if _ajuste_dia_util(b['venc'], x, feriados)), None)
            if d:
                b['lancs'], b['vinc'] = por_data.pop(d), 'dia_util'
            else:
                resto.append(b)
        for b in resto:
            if not por_data:
                break
            d = min(por_data, key=lambda x: _dias(x, b['venc']))
            if _dias(d, b['venc']) <= JANELA_DIAS_VENC:
                b['lancs'], b['vinc'] = por_data.pop(d), 'outro_venc'
        for b in bols:
            rel_usado.update(b['lancs'])

    # ---------- Inc. Bradesco: boleto que o banco devolveu (explica o "lançado e não pago")
    inc_ativo = bool(conexao.execute('SELECT 1 FROM conc_inc LIMIT 1').fetchone())
    inc_chave, inc_cart = defaultdict(list), defaultdict(list)
    if inc_ativo:
        for r in conexao.execute('SELECT * FROM conc_inc WHERE venc BETWEEN ? AND ? ORDER BY id', (janela_ini, janela_fim)):
            r = dict(r)
            if r['chave']:
                inc_chave[(r['chave'], r['venc'])].append(r)
            if r['carteira'] is not None:
                inc_cart[(r['carteira'], r['venc'])].append(r)
    inc_lanc, inc_usados = {}, set()
    for j in rel_per:
        r = rels[j]
        if not inc_ativo or r['flag'] == 'C' or j in bw_do_rel:
            continue
        cands = [c for c in inc_chave.get((r['chave'], r['dt_venc']), []) if c['id'] not in inc_usados]
        if not cands:     # sem o ID do imóvel na planilha: carteira + vencimento + mesmo valor
            cands = [c for c in inc_cart.get((r.get('carteira'), r['dt_venc']), []) if not c['chave'] and c['id'] not in inc_usados
                     and c['valor'] is not None and abs(c['valor'] - (r['vr_boleto'] or 0)) < 0.02]
        if not cands:
            continue
        mesmo_valor = [c for c in cands if c['valor'] is not None and abs(c['valor'] - (r['vr_boleto'] or 0)) < 0.02]
        grupo_c = mesmo_valor or cands[-1:]          # o mesmo título devolvido mais de uma vez conta como um só: vale o último
        inc_usados.update(c['id'] for c in grupo_c)
        ult = grupo_c[-1]
        inc_lanc[j] = {'ocorrencia': ult['ocorrencia'], 'descricao': ult['descricao'], 'situacao': ult['situacao'],
                       'obs': ult['obs'], 'data': _br(ult['data'] or ''), 'pendente': 'PENDENTE' in (ult['situacao'] or ''),
                       'vezes': len(grupo_c)}

    def texto_inc(i):
        return (f"Banco devolveu{' em ' + i['data'][:5] if i['data'] else ''}: {i['ocorrencia']} – {i['descricao']}. "
                f"Situação: {'PENDENTE' if i['pendente'] else 'tratado'}" + (f" ({i['obs']})" if i['obs'] else '') + '.')

    # ---------- itens (um por boleto da base, por lançamento fora da base e por pagamento do BW sem lançamento)
    itens = []
    itens_conta = []      # linhas extras que a conta do dia precisa para abrir cada parcela (vieram de outro dia, não pagos por lançamento)

    def info_lanc(j):
        r = rels[j]
        par = bw_do_rel.get(j)
        bw = bws[par[0]] if par else None
        return {
            'flag': r['flag'], 'tp_lanc': r['tp_lanc'], 'venc': _br(r['dt_venc']), 'valor': r['vr_boleto'],
            'desc': r['vr_desc'] or 0.0, 'pago': bool(par), 'valor_bw': bw['valor'] if bw else None,
            'data_bw': _br(bw['data_pag'] or bw['data']) if bw else '', 'ref_bw': (bw.get('ref_longa') or bw['referencia']) if bw else '',
            'arquivo_bw': bw['arquivo'] if bw else '', 'valor_bateu': bool(par and par[2]), 'inc': inc_lanc.get(j),
            'ambiguo': bool(par and par[1] and (r.get('carteira'), round(r['vr_boleto'] or 0, 2)) in sem_pag_cv),
        }

    for b in base.values():
        g = grupo(b['venc'])
        lancs = [info_lanc(j) for j in b['lancs']]
        venc_ref = (b['lancs'] and rels[b['lancs'][0]]['dt_venc']) or b['venc']
        chegou = bw_chegou(venc_ref)
        nota, visao = '', ''
        if b['venc'] != g:
            nota = (f"Vence {_rotulo_dia(b['venc'], feriados).split(' ·')[0]} {_br(b['venc'])[:5]} "
                    f"({'feriado' if date.fromisoformat(b['venc']).weekday() < 5 else 'fim de semana'}): entra no dia útil {_br(g)[:5]}. ")
        if not lancs and b['venc'][:7] not in meses_rel:
            cat = 'SEM_RELATORIO'
            det = f"O relatório de vencimentos {b['venc'][5:7]}/{b['venc'][:4]} não foi enviado: envie o relBolMov desse mês."
        elif not lancs:
            if b['na_base_atual']:
                cat = 'NAO_LANCADO'
                det = ('Só tem lançamento cancelado (C) no relatório.' if canc_por_im.get(b['chave'])
                       else 'Ainda está na base atual: falta lançar.')
                if g < hoje:
                    det += ' O dia útil já passou.'
            else:
                # saiu da base: quer dizer que foi lançado. Sem par no relatório, é a linha repetida do mesmo boleto
                cat = 'VISAO_DUPLA'
                outros = sorted({rels[j]['dt_venc'] for j in validos_por_im.get(b['chave'], [])})
                if outros:
                    visao = 'par'
                    det = (f"Saiu da base. O mesmo imóvel foi lançado em {', '.join(_br(x)[:5] for x in outros[:3])}: "
                           'esta linha é a visão dupla (desconto ou vencimento alternativo).')
                elif canc_por_im.get(b['chave']):
                    visao = 'cancelado'
                    det = 'Saiu da base e o relatório só tem lançamento cancelado (C): não gera pagamento.'
                else:
                    visao = 'saiu'
                    det = 'Saiu da base: considerado lançado (não achei o par no relatório; provável visão dupla).'
        else:
            pagos = [l for l in lancs if l['pago']]
            g_lanc = grupo(venc_ref)
            if b['vinc'] == 'outro_venc' and g_lanc != g:
                cat = 'VENC_DIFERENTE'
                det = f"Base vence {_br(b['venc'])}, relatório lançou para {lancs[0]['venc']}."
            elif any(l['pago'] and not l['valor_bateu'] for l in lancs):
                cat = 'VALOR_DIFERENTE'
                dif = [l for l in lancs if l['pago'] and not l['valor_bateu']][0]
                det = f"Lançado {_rs(dif['valor'])} e o BW pagou {_rs(dif['valor_bw'])}."
            elif len(pagos) == len(lancs):
                cat = 'OK'
                det = 'Base, relatório e BW batem.' + (' (mesmo valor em outro imóvel da carteira que ficou sem pagamento: conferir)'
                                                       if any(l['ambiguo'] for l in lancs) else '')
            elif chegou and g_lanc < hoje:
                # o BW do dia útil já chegou: o que não veio nele (nem antes) não foi pago
                cat = 'NAO_PAGO_BW'
                faltam = [l for l in lancs if not l['pago']]
                det = (f"{len(faltam)} de {len(lancs)} lançamento(s) não aparecem no BW de {_br(g_lanc)[:5]}"
                       + (' (o portal diz baixado)' if any(l['flag'] == 'B' for l in faltam) else '') + '.')
                devolvidos = [l['inc'] for l in faltam if l.get('inc')]
                if devolvidos:
                    det += ' ' + ' '.join(texto_inc(i) for i in devolvidos[:2])
                    if len(devolvidos) == len(faltam):       # o banco devolveu todos os que faltam: está explicado
                        cat = 'DEVOLVIDO_PEND' if any(i['pendente'] for i in devolvidos) else 'DEVOLVIDO_TRAT'
            elif not chegou and all(l['pago'] or l['flag'] == 'B' for l in lancs):
                cat = 'OK_PORTAL'
                det = f"O portal marca como baixado; o BW de {_br(g_lanc)[:5]} ainda não chegou (é D-1: chega no dia seguinte)."
            else:
                cat = 'AGUARDANDO'
                det = f"Lançado; o BW de {_br(g_lanc)[:5]} ainda não chegou (é D-1: chega no dia seguinte)."
        n, m = b['linhas'], len(lancs)
        principal = {
            'origem': 'base', 'grupo': g, 'imovel': b['imovel'], 'venc': _br(b['venc']), 'carteira': b['carteira'],
            'adm': b['adm'], 'emp': b['emp'], 'na_base_atual': b['na_base_atual'], 'status_sistema': b['status'],
            'lancs': lancs, 'qtd_lanc': m, 'valor_lanc': round(sum((l['valor'] or 0) for l in lancs), 2),
            'valor_pago': round(sum((l['valor_bw'] or 0) for l in lancs if l['pago']), 2),
            'categoria': cat, 'detalhe': nota + det, 'fds': b['venc'] != g, 'linhas_base': n,
            'qtd': n if not m else min(n, m), 'visao': visao,
        }
        if cat in ('NAO_PAGO_BW', 'DEVOLVIDO_PEND', 'DEVOLVIDO_TRAT'):          # conta os lançamentos que faltam no BW (é o número que aparece no card do banco)
            principal['qtd'] = max(1, len([l for l in lancs if not l['pago']]))
        itens.append(principal)
        if m and grupo(venc_ref) != g:
            # lançado para outro dia útil: na conta deste dia ele SAI; na conta do outro dia ele ENTRA ("veio de outro dia")
            _txt = nota + f"A base vence {_br(b['venc'])}; o relatório lançou para {_br(venc_ref)}."
            itens_conta.append(dict(principal, categoria='SAINDO', qtd=min(n, m), visao='', detalhe=_txt))
            itens_conta.append(dict(principal, grupo=grupo(venc_ref), categoria='ENTRANDO', qtd=min(n, m), visao='', detalhe=_txt))
        if m and n > m:
            # a base tem mais linhas (boletos) do que o relatório lançou
            if b['na_base_atual']:
                itens.append(dict(principal, categoria='NAO_LANCADO', qtd=n - m, lancs=[], qtd_lanc=0, valor_lanc=0.0, valor_pago=0.0,
                                  visao='', detalhe=nota + f'A base tem {n} boletos (linhas) neste vencimento e o relatório lançou {m}.'))
            else:
                itens.append(dict(principal, categoria='VISAO_DUPLA', qtd=n - m, lancs=[], qtd_lanc=0, valor_lanc=0.0, valor_pago=0.0,
                                  visao='par', detalhe=nota + f'A base tem {n} linhas neste vencimento e o relatório lançou {m}: '
                                                              'a linha a mais é a visão dupla (desconto ou vencimento alternativo).'))
        elif m > n:
            g_l = grupo(venc_ref)
            if b['por_linha']:
                itens.append(dict(principal, grupo=g_l, categoria='LANC_A_MAIS', qtd=m - n, lancs=[], qtd_lanc=0,
                                  valor_lanc=0.0, valor_pago=0.0,
                                  detalhe=f'O relatório lançou {m} boletos e a base tem {n} linha(s) neste vencimento.'))
            else:
                principal['detalhe'] += f' {m} boletos lançados (a base dessa época tinha 1 linha por imóvel).'
                itens.append(dict(principal, grupo=g_l, categoria='BOLETO_EXTRA', qtd=m - n, lancs=[], qtd_lanc=0,
                                  valor_lanc=0.0, valor_pago=0.0,
                                  detalhe=(f'O imóvel tem {m} boletos lançados; a base dessa época tinha 1 linha por imóvel. '
                                           'Nas cargas novas cada boleto vem na sua linha.')))

    # lançamentos sem boleto correspondente na base
    sobra_rel = defaultdict(list)
    for j in rel_per:
        if j not in rel_usado and rels[j]['flag'] != 'C':
            sobra_rel[(rels[j]['chave'], rels[j]['dt_venc'])].append(j)
    for (ch, venc), js in sobra_rel.items():
        lancs = [info_lanc(j) for j in js]
        r0 = rels[js[0]]
        itens.append({
            'origem': 'relatorio', 'grupo': grupo(venc), 'imovel': r0['imovel'], 'venc': _br(venc), 'carteira': r0['carteira'],
            'adm': r0['adm'], 'emp': r0['emp'], 'na_base_atual': False, 'status_sistema': '',
            'lancs': lancs, 'qtd_lanc': len(lancs), 'valor_lanc': round(sum((l['valor'] or 0) for l in lancs), 2),
            'valor_pago': round(sum((l['valor_bw'] or 0) for l in lancs if l['pago']), 2),
            'categoria': 'LANC_FORA_BASE', 'fds': False, 'qtd': len(js),
            'detalhe': ('O imóvel está na base com outro vencimento; este lançamento é a mais.' if ch in base_por_im
                        else 'Imóvel não passou pela base.'),
        })

    # pagamentos do BW sem lançamento no relatório
    base_por_cart = defaultdict(set)
    for b in base.values():
        if b['carteira'] is not None:
            base_por_cart[b['carteira']].add(b['imovel'])
    canc_por_cart = defaultdict(list)
    for r in rels:
        if r['flag'] == 'C' and r.get('carteira') is not None:
            canc_por_cart[r['carteira']].append(r)
    bw_baixado = set()
    for i, b in enumerate(bws):
        if i in lig or not no_periodo(b.get('data')):
            continue
        comum = {'origem': 'bw', 'qtd': 1, 'grupo': grupo(b['data']), 'venc': _br(b['data']), 'carteira': b['carteira'],
                 'adm': f"pago em {_br(b.get('data_pag') or '')}", 'na_base_atual': False, 'status_sistema': '',
                 'lancs': [], 'qtd_lanc': 0, 'valor_lanc': 0.0, 'valor_pago': b['valor'] or 0.0, 'fds': False,
                 'ref_bw': b.get('ref_longa') or b['referencia'], 'arquivo_bw': b['arquivo']}
        if b['_motivo']:
            itens.append(dict(comum, imovel='', emp=b.get('favorecido') or '', categoria='BW_OUTRO', detalhe=b['_motivo']))
            continue
        if b['data'][:7] not in meses_rel:
            itens.append(dict(comum, imovel=de_para.get(chave_de_para(b), ''), emp=b['nome'] or (b.get('favorecido') or ''),
                              categoria='SEM_RELATORIO',
                              detalhe=f"Pago no BW; o relatório de vencimentos {b['data'][5:7]}/{b['data'][:4]} não foi enviado."))
            continue
        im = de_para.get(chave_de_para(b), '')
        cands = sorted(base_por_cart.get(b['carteira'], set()))
        alvo = {c for c in (chave_imovel(im), chave_imovel(b.get('sufixo'))) if c} or ({chave_imovel(cands[0])} if len(cands) == 1 else set())
        baixa = next((x for x in base.values() if x['chave'] in alvo and x['status'].upper() in STATUS_BAIXADO
                      and _dias(x['venc'], b['data']) <= JANELA_DIAS_BW), None) if alvo else None
        if baixa:
            bw_baixado.add(i)
            itens.append(dict(comum, imovel=baixa['imovel'], emp=b['nome'] or baixa['emp'] or (b.get('favorecido') or ''),
                              categoria='BW_BAIXADO',
                              detalhe=f'Sem lançamento no relatório, mas o boleto do imóvel {baixa["imovel"]} ({_br(baixa["venc"])}) '
                                      f'está {baixa["status"].lower()} na base: foi para o banco.'))
            continue
        canc = [r for r in canc_por_cart.get(b['carteira'], []) if _valor_bate(b.get('valor'), r)]
        if canc:
            im = im or canc[0]['imovel']
            det = (f'O lançamento do imóvel {canc[0]["imovel"]} com esse valor está CANCELADO (C) no relatório, '
                   'mas o BW pagou.')
        elif im:
            det = f'Referência {b["referencia"]} é do imóvel {im} (de-para), mas não achei lançamento com esse valor.'
        elif cands:
            det = (f'Carteira {b["carteira"]} está na base ({", ".join(cands[:4])}{"…" if len(cands) > 4 else ""}), '
                   'mas não achei lançamento com esse valor no relatório.')
        else:
            det = f'Carteira {b["carteira"]} não está no relatório nem na base.'
        itens.append(dict(comum, imovel=im or (cands[0] if len(cands) == 1 else ''),
                          emp=b['nome'] or ('LELLO (sem nome do condomínio)' if b['eh_lello'] else (b.get('favorecido') or '')),
                          categoria='BW_SEM_LANC', detalhe=det))

    # lançamentos que o BW não pagou, UM POR LANÇAMENTO: é a unidade da conta "do lançado ao banco" (um boleto pode ter mais de um)
    for j in rel_per:
        r = rels[j]
        gl = grupo(r['dt_venc'])
        if r['flag'] == 'C' or j in bw_do_rel or gl not in dias_pag:
            continue
        li = info_lanc(j)
        inc = li['inc']
        cat = 'NAO_PAGO_BW' if not inc else ('DEVOLVIDO_PEND' if inc['pendente'] else 'DEVOLVIDO_TRAT')
        det = f"Lançado ({r['flag']}) e não aparece no BW de {_br(gl)[:5]}" + (' (o portal diz baixado).' if r['flag'] == 'B' else '.')
        if inc:
            det += ' ' + texto_inc(inc)
        elif inc_ativo:
            det += ' O banco não devolveu (não está no Inc. Bradesco): sem pagamento e sem devolução.'
        itens_conta.append({
            'origem': 'relatorio', 'grupo': gl, 'imovel': r['imovel'], 'venc': _br(r['dt_venc']), 'carteira': r['carteira'],
            'adm': r['adm'], 'emp': r['emp'], 'na_base_atual': False, 'status_sistema': '',
            'lancs': [li], 'qtd_lanc': 1, 'valor_lanc': round(r['vr_boleto'] or 0.0, 2), 'valor_pago': 0.0,
            'categoria': cat, 'fds': False, 'qtd': 1, 'detalhe': det,
        })

    # ---------- resumo por DIA ÚTIL (o que a equipe confere: base X = relatório X = BW X)
    def novo_grupo():
        return {'base': 0, 'base_atual': 0, 'fds': 0, 'lanc_pares': set(), 'lanc_linhas': 0, 'lanc_valor': 0.0,
                'baixados': 0, 'cancelados': 0, 'bw_qtd': 0, 'bw_valor': 0.0, 'bw_outros': 0, 'bw_adiantados': 0,
                'saindo': 0, 'entrando': 0, 'sem_rel': 0, 'extra': 0, 'base_imoveis': 0,
                'visao': 0, 'visao_tipos': defaultdict(int), 'bw_pareados': 0, 'bw_sem_par': 0, 'bw_baixado': 0, 'valor_bw_baixado': 0.0, 'nao_pagos': 0,
                'np_pend': 0, 'np_trat': 0, 'np_sem': 0, 'vnp_pend': 0.0, 'vnp_trat': 0.0, 'vnp_sem': 0.0,
                'valor_nao_pago': 0.0, 'valor_bw_sem': 0.0, 'dif_pareados': 0.0, 'desc_qtd': 0, 'desc_valor': 0.0,
                'div': defaultdict(int), 'fds_por_data': {}}
    grupos = {}
    d = d_ini
    while d <= d_fim:
        if _dia_util(d, feriados):
            grupos[d.isoformat()] = novo_grupo()
        d += timedelta(days=1)

    def G(iso):
        if iso not in grupos:
            grupos[iso] = novo_grupo()
        return grupos[iso]

    for b in base.values():
        g = grupo(b['venc'])
        x = G(g)
        n, m = b['linhas'], len(b['lancs'])
        casados = min(n, m)
        x['base'] += n
        x['base_imoveis'] += 1
        x['base_atual'] += n if b['na_base_atual'] else 0
        g_lanc = grupo(rels[b['lancs'][0]]['dt_venc']) if b['lancs'] else None
        if g_lanc and g_lanc != g:            # lançado para outro dia útil: sai da conta deste e entra na do outro
            x['saindo'] += casados
            G(g_lanc)['entrando'] += casados
        if b['venc'] != g:                    # vence no fim de semana/feriado e entra neste dia útil
            x['fds'] += n
            f = x['fds_por_data'].setdefault(b['venc'], {'venc': _br(b['venc']), 'rotulo': _rotulo_dia(b['venc'], feriados),
                                                          'base': 0, 'no_dia_util': 0, 'na_data': 0, 'outra_data': 0, 'sem_lanc': 0,
                                                          'visao_dupla': 0, 'sem_relatorio': 0})
            f['base'] += n
            if not b['lancs'] and b['venc'][:7] not in meses_rel:
                f['sem_relatorio'] += n
            elif not b['lancs']:
                f['sem_lanc' if b['na_base_atual'] else 'visao_dupla'] += n
            else:
                dv = rels[b['lancs'][0]]['dt_venc']
                f['no_dia_util' if dv == g else ('na_data' if dv == b['venc'] else 'outra_data')] += casados
                f['sem_lanc' if b['na_base_atual'] else 'visao_dupla'] += n - casados
    for j in rel_per:
        r = rels[j]
        x = G(grupo(r['dt_venc']))
        if r['flag'] == 'C':
            x['cancelados'] += 1
            continue
        x['lanc_linhas'] += 1
        x['lanc_valor'] += r['vr_boleto'] or 0.0
        x['baixados'] += 1 if r['flag'] == 'B' else 0
        x['lanc_pares'].add((r['chave'], r['dt_venc']))
        if j not in bw_do_rel:           # lançado que o BW não pagou (só vale como "não pagou" se o BW do dia já chegou)
            x['nao_pagos'] += 1
            x['valor_nao_pago'] += r['vr_boleto'] or 0.0
            _inc = inc_lanc.get(j)
            _k = 'pend' if (_inc and _inc['pendente']) else ('trat' if _inc else 'sem')
            x['np_' + _k] += 1
            x['vnp_' + _k] += r['vr_boleto'] or 0.0
        else:                             # pago: se bateu com valor líquido (desconto), explica a diferença de valor
            _bw_idx, _ambiguo, _bate = bw_do_rel[j]
            if _bate:
                _dif = (r['vr_boleto'] or 0.0) - (bws[_bw_idx]['valor'] or 0.0)
                if abs(_dif) > 0.004:
                    x['desc_qtd'] += 1
                    x['desc_valor'] += _dif
                    _li = info_lanc(j)
                    itens_conta.append({
                        'origem': 'relatorio', 'grupo': grupo(r['dt_venc']), 'imovel': r['imovel'], 'venc': _br(r['dt_venc']),
                        'carteira': r['carteira'], 'adm': r['adm'], 'emp': r['emp'], 'na_base_atual': False, 'status_sistema': '',
                        'lancs': [_li], 'qtd_lanc': 1, 'valor_lanc': round(r['vr_boleto'] or 0.0, 2), 'valor_pago': round(_li['valor_bw'] or 0.0, 2),
                        'categoria': 'DESCONTO', 'fds': False, 'qtd': 1,
                        'detalhe': f"Lançado {_rs(r['vr_boleto'])}, banco pagou {_rs(_li['valor_bw'])} (desconto de {_rs(_dif)}).",
                    })
    for i, b in enumerate(bws):
        par = lig.get(i)
        efetiva = rels[par[0]]['dt_venc'] if par else (b.get('data') or '')
        if not no_periodo(efetiva):
            continue
        g = grupo(efetiva)
        x = G(g)
        if not par and b['_motivo']:
            x['bw_outros'] += 1          # não é condomínio: fica fora da conta do dia
            continue
        x['bw_qtd'] += 1
        x['bw_valor'] += b['valor'] or 0.0
        if par:
            x['bw_pareados'] += 1
            x['dif_pareados'] += (b['valor'] or 0.0) - (rels[par[0]]['vr_boleto'] or 0.0)
        elif i in bw_baixado:
            x['bw_baixado'] += 1
            x['valor_bw_baixado'] += b['valor'] or 0.0
        else:
            x['bw_sem_par'] += 1
            x['valor_bw_sem'] += b['valor'] or 0.0
        if (b.get('data_pag') or g) < g:
            x['bw_adiantados'] += 1
    for it in itens:
        q = it.get('qtd', 1)
        if CATEGORIAS[it['categoria']][1]:
            G(it['grupo'])['div'][it['categoria']] += q
        elif it['categoria'] == 'SEM_RELATORIO' and it['origem'] == 'base':
            G(it['grupo'])['sem_rel'] += q
        elif it['categoria'] == 'BOLETO_EXTRA':
            G(it['grupo'])['extra'] += q
        elif it['categoria'] == 'VISAO_DUPLA':
            G(it['grupo'])['visao'] += q
            G(it['grupo'])['visao_tipos'][it.get('visao') or 'saiu'] += q

    resumo = []
    for g in sorted(grupos):
        x = grupos[g]
        div = dict(x['div'])
        n_div = sum(div.values())
        lanc_imoveis = len(x['lanc_pares'])
        lanc_valor, bw_valor = round(x['lanc_valor'], 2), round(x['bw_valor'], 2)
        carregado = g in dias_pag
        desc_valor = round(x['desc_valor'], 2)
        bate_bw = carregado and x['bw_qtd'] == x['lanc_linhas'] and abs(lanc_valor - bw_valor - desc_valor) < 0.01
        if not n_div and bate_bw:
            status = 'OK3'
        elif not n_div and not carregado:
            status = 'OK2'
        elif not n_div:
            status = 'PARCIAL'
        else:
            status = 'DIV'
        # (chave, rótulo da conta, quantidade, tipo, rótulo curto do card). tipo: base | info (explicado, não é erro) | erro
        ponte = [
            ('base', 'Na base' + (f' (inclui {x["fds"]} de fim de semana/feriado)' if x['fds'] else ''), x['base'], 'base', 'Na base'),
            ('VISAO_DUPLA', 'Visão dupla: saíram da base, já lançados (desconto ou vencimento alternativo)', -x['visao'], 'info', 'Visão dupla'),
            ('NAO_LANCADO', 'Ainda na base, sem lançamento', -div.get('NAO_LANCADO', 0), 'erro', 'Faltam lançar'),
            ('SEM_RELATORIO', 'Vencem em mês sem relatório enviado', -x['sem_rel'], 'erro', 'Mês sem relatório'),
            ('saindo', 'Lançados para outro dia', -x['saindo'], 'erro', 'Lançados p/ outro dia'),
            ('entrando', 'Vieram de outro dia', x['entrando'], 'info', 'Vieram de outro dia'),
            ('BOLETO_EXTRA', '2º boleto do imóvel (base antiga tinha 1 linha)', x['extra'], 'info', '2º boleto do imóvel'),
            ('LANC_A_MAIS', 'Lançamentos a mais que a base', div.get('LANC_A_MAIS', 0), 'erro', 'Lançados a mais'),
            ('LANC_FORA_BASE', 'Lançados sem estar na base', div.get('LANC_FORA_BASE', 0), 'erro', 'Fora da base'),
        ]
        ponte = [p for p in ponte if p[2] or p[0] == 'base']
        ponte_bw = []
        if carregado:      # do lançado ao banco: o que não foi pago e o que foi pago sem lançamento
            ponte_bw = [dict(chave='lanc', rotulo='Lançados no relatório', curto='Lançados', qtd=x['lanc_linhas'], tipo='base')]
            if x['nao_pagos'] and not inc_ativo:
                ponte_bw.append(dict(chave='NAO_PAGO_BW', rotulo='Lançados que o BW não pagou', curto='Não pagos no BW',
                                     qtd=-x['nao_pagos'], tipo='erro'))
            elif x['nao_pagos']:     # com o Inc. Bradesco: separa o que o banco devolveu do que não tem explicação
                if x['np_pend']:
                    ponte_bw.append(dict(chave='DEVOLVIDO_PEND', rotulo='Lançados que o banco devolveu e ainda estão pendentes (Inc. Bradesco)',
                                         curto='Devolvidos: pendentes', qtd=-x['np_pend'], tipo='erro'))
                if x['np_trat']:
                    ponte_bw.append(dict(chave='DEVOLVIDO_TRAT', rotulo='Lançados que o banco devolveu e já foram tratados (Inc. Bradesco)',
                                         curto='Devolvidos: tratados', qtd=-x['np_trat'], tipo='info'))
                if x['np_sem']:
                    ponte_bw.append(dict(chave='NAO_PAGO_BW', rotulo='Lançados que o BW não pagou e o banco não devolveu',
                                         curto='Sem pagamento nem devolução', qtd=-x['np_sem'], tipo='erro'))
            if x['bw_baixado']:
                ponte_bw.append(dict(chave='BW_BAIXADO', rotulo='Pagos no BW sem lançamento, mas já baixados (foram para o banco)',
                                     curto='Pagos, já baixados', qtd=x['bw_baixado'], tipo='info'))
            if x['bw_sem_par']:
                ponte_bw.append(dict(chave='BW_SEM_LANC', rotulo='Pagos no BW sem lançamento no relatório', curto='Pagos sem lançamento',
                                     qtd=x['bw_sem_par'], tipo='erro'))
            if x['desc_qtd']:
                ponte_bw.append(dict(chave='DESCONTO', rotulo='Pagos com desconto (banco pagou o valor líquido)', curto='Com desconto',
                                     qtd=x['desc_qtd'], tipo='info', valorTxt=_rs(desc_valor) + ' de desconto'))
        resumo.append({
            'data': g, 'data_br': _br(g), 'dia': g[8:10], 'dia_semana': _rotulo_dia(g, feriados), 'passou': g < hoje,
            'base': x['base'], 'base_imoveis': x['base_imoveis'], 'base_atual': x['base_atual'], 'fds': x['fds'],
            'fds_detalhe': [x['fds_por_data'][k] for k in sorted(x['fds_por_data'])],
            'lanc_boletos': x['lanc_linhas'], 'lanc_imoveis': lanc_imoveis, 'lanc_linhas': x['lanc_linhas'], 'lanc_valor': lanc_valor,
            'baixados': x['baixados'], 'cancelados': x['cancelados'], 'sem_rel': x['sem_rel'],
            'bw_qtd': x['bw_qtd'], 'bw_valor': bw_valor, 'bw_outros': x['bw_outros'], 'bw_adiantados': x['bw_adiantados'],
            'bw_carregado': carregado, 'dif_valor': round(bw_valor - lanc_valor, 2) if carregado else None,
            'desc_qtd': x['desc_qtd'] if carregado else None, 'desc_valor': desc_valor if carregado else None,
            'saindo': x['saindo'], 'entrando': x['entrando'],
            'ponte': [{'chave': p[0], 'rotulo': p[1], 'qtd': p[2], 'tipo': p[3], 'curto': p[4]} for p in ponte],
            'ponte_fecha': sum(p[2] for p in ponte) == x['lanc_linhas'],
            'visao_dupla': x['visao'], 'visao_tipos': dict(x['visao_tipos']), 'base_distintos': x['base'] - x['visao'],
            'bw_pareados': x['bw_pareados'], 'bw_sem_lanc': x['bw_sem_par'], 'bw_baixado': x['bw_baixado'], 'nao_pagos': x['nao_pagos'] if carregado else None,
            'ponte_bw': ponte_bw, 'ponte_bw_fecha': (carregado and x['lanc_linhas'] - x['nao_pagos'] + x['bw_baixado'] + x['bw_sem_par'] == x['bw_qtd']),
            'valor_explica': ({'nao_pago': round(x['valor_nao_pago'], 2), 'bw_sem_lanc': round(x['valor_bw_sem'], 2), 'bw_baixado': round(x['valor_bw_baixado'], 2),
                               'valor_diferente': round(x['dif_pareados'], 2), 'np_pend': round(x['vnp_pend'], 2),
                               'np_trat': round(x['vnp_trat'], 2), 'np_sem': round(x['vnp_sem'], 2)} if carregado else None),
            'inc_ativo': inc_ativo, 'np_pend': x['np_pend'] if carregado else None, 'np_trat': x['np_trat'] if carregado else None,
            'np_sem': x['np_sem'] if carregado else None,
            'divergencias': div, 'n_div': n_div, 'status': status,
        })

    contagem = defaultdict(int)
    for it in itens:
        contagem[it['categoria']] += it.get('qtd', 1)
    totais = {
        'base': sum(b['linhas'] for b in base.values()), 'base_imoveis': len(base),
        'base_atual': sum(b['linhas'] for b in base.values() if b['na_base_atual']),
        'lanc_boletos': sum(r['lanc_boletos'] for r in resumo), 'lanc_linhas': sum(r['lanc_linhas'] for r in resumo),
        'lanc_valor': round(sum(r['lanc_valor'] for r in resumo), 2),
        'bw_qtd': sum(r['bw_qtd'] for r in resumo), 'bw_valor': round(sum(r['bw_valor'] for r in resumo), 2),
        'bw_sem_nome': sum(1 for i, b in enumerate(bws) if no_periodo(b.get('data')) and not b['tem_nome_cond']
                           and (i in lig or not b['_motivo'])),
        'bw_outros': sum(r['bw_outros'] for r in resumo),
        'ok3': contagem['OK'], 'divergencias': sum(v for k, v in contagem.items() if CATEGORIAS[k][1]),
        'por_categoria': dict(contagem), 'de_para_aprendidos': aprendidos,
    }
    itens.sort(key=lambda x: (CATEGORIAS[x['categoria']][2], x['grupo'], str(x['imovel'])))
    for it in itens:
        it['dia'] = it['grupo']
    return {'periodo': [ini_iso, fim_iso], 'hoje': hoje, 'resumo': resumo, 'itens': itens, 'itens_conta': itens_conta, 'totais': totais,
            'categorias': categorias_api(), 'grupos': grupos_api()}


def conciliar(conexao, ano, mes, hoje=None, aprender=True):
    """Mês inteiro, por dia útil (usado na exportação do mês)."""
    ini, fim = periodo_do_mes(ano, mes, _feriados({ano - 1, ano, ano + 1}))
    res = conciliar_periodo(conexao, ini, fim, hoje=hoje, aprender=aprender)
    res['mes'] = f'{ano}-{mes:02d}'
    return res


def dia_padrao(hoje=None):
    """O dia que se confere hoje: o dia útil anterior (o BW é D-1)."""
    hoje = hoje or date.today().isoformat()
    a = int(hoje[:4])
    return dia_util_anterior(hoje, _feriados({a - 1, a, a + 1}))


def conciliar_dia(conexao, dia_iso=None, hoje=None, aprender=True):
    """Um dia útil: os 3 lados daquele dia, os itens dele e a faixa com os dias úteis do mês.
    Hoje e dias futuros ainda não têm BW (é D-1: o arquivo de um dia só chega no dia útil seguinte) -
    aparecem com o mesmo layout, com base e relatório normais e o lado do banco vazio (não há BW pra
    casar ainda, então não dá pra "aprender" de-para nem para dizer que algo "não foi pago")."""
    hoje = hoje or date.today().isoformat()
    dia_iso = dia_iso or dia_padrao(hoje)
    a = int(dia_iso[:4])
    feriados = _feriados({a - 1, a, a + 1})
    g = venc_util(dia_iso, feriados)           # sábado/domingo/feriado escolhido -> dia útil seguinte
    futuro = g >= hoje
    res = conciliar(conexao, int(g[:4]), int(g[5:7]), hoje=hoje, aprender=(aprender and not futuro))
    linha = next((r for r in res['resumo'] if r['data'] == g), None)
    if futuro and linha is not None:
        # um boleto que vence hoje/no futuro pode já ter sido pago adiantado (chegou num arquivo BW de um dia
        # anterior, já lido) - o card "Foi pago no banco" não deve contar isso como "o BW de hoje chegou": até
        # o arquivo de hoje realmente chegar, esse lado fica vazio (o adiantamento aparece normalmente, como
        # sempre apareceu, no dia em que o BW dele foi lido).
        linha = dict(linha, bw_qtd=0, bw_valor=0.0, bw_outros=0, bw_adiantados=0,
                     bw_pareados=0, bw_sem_lanc=0, bw_baixado=0)
    return {
        'data': g, 'pedido': dia_iso, 'hoje': hoje, 'padrao': dia_padrao(hoje), 'mes': res['mes'],
        'anterior': dia_util_anterior(g, feriados), 'proximo': dia_util_seguinte(g, feriados),
        'bw_disponivel_em': dia_util_seguinte(g, feriados) if futuro else None, 'bloqueado': False,
        'dia': linha,
        'itens': [i for i in res['itens'] if i['grupo'] == g and i['categoria'] != 'BOLETO_EXTRA'],
        'conta_itens': [i for i in res['itens_conta'] if i['grupo'] == g] + [i for i in res['itens'] if i['grupo'] == g and i['categoria'] == 'BOLETO_EXTRA'],
        'dias': [{k: r[k] for k in ('data', 'dia', 'dia_semana', 'status', 'n_div', 'base', 'lanc_boletos', 'bw_qtd',
                                    'bw_carregado', 'passou')} for r in res['resumo']],
        'categorias': res['categorias'], 'grupos': res['grupos'], 'de_para_aprendidos': res['totais']['de_para_aprendidos'],
    }


DIAS_PREVISAO = 15          # dias úteis à frente (contando hoje)


def _lista_prev(it):
    return {k: it.get(k) for k in ('origem', 'imovel', 'carteira', 'emp', 'adm', 'venc', 'categoria', 'qtd', 'qtd_lanc', 'valor_lanc',
                                   'na_base_atual', 'status_sistema', 'detalhe', 'fds', 'lancs')}


def previsao_do_dia(conexao, g, hoje, aprender=False):
    """Um dia que ainda não chegou: números da previsão + os boletos dele (falta lançar primeiro)."""
    res = conciliar(conexao, int(g[:4]), int(g[5:7]), hoje=hoje, aprender=False)
    linha = next((r for r in res['resumo'] if r['data'] == g), None) or {}
    meses_rel = {r[0] for r in conexao.execute('SELECT DISTINCT substr(dt_venc, 1, 7) FROM conc_relatorio')}
    itens = [i for i in res['itens'] if i['grupo'] == g and i['categoria'] not in ('BOLETO_EXTRA', 'BW_OUTRO', 'BW_SEM_LANC', 'BW_BAIXADO')]
    return {'linha': _linha_previsao(linha, g[:7] in meses_rel) if linha else None,
            'itens': [_lista_prev(i) for i in itens]}


def _linha_previsao(x, tem_relatorio):
    base = x['base']
    falta = (x['divergencias'] or {}).get('NAO_LANCADO', 0) + (x.get('sem_rel') or 0)
    fora = (x['divergencias'] or {}).get('LANC_FORA_BASE', 0) + (x['divergencias'] or {}).get('LANC_A_MAIS', 0)
    lancado_base = max(0, base - falta - x['visao_dupla'] - x['saindo'] + x['entrando'])
    return {'data': x['data'], 'data_br': x['data_br'], 'dia_semana': x['dia_semana'], 'programado': base, 'fds': x['fds'],
            'lancado': x['lanc_boletos'], 'valor_lancado': x['lanc_valor'], 'falta_lancar': falta, 'lancados_fora_base': fora,
            'lancado_da_base': lancado_base, 'visao_dupla': x['visao_dupla'],
            'saindo': x['saindo'], 'entrando': x['entrando'],
            'pct': (round(100.0 * lancado_base / (lancado_base + falta), 1) if (lancado_base + falta) else None),
            'sem_relatorio': not tem_relatorio}


def previsao(conexao, hoje=None, dias_uteis=DIAS_PREVISAO):
    """O que está programado (base) e o que já foi lançado (relatório) de hoje para a frente, dia útil a dia útil."""
    hoje = hoje or date.today().isoformat()
    a = int(hoje[:4])
    feriados = _feriados({a - 1, a, a + 1, a + 2})
    dias_uteis = max(1, min(int(dias_uteis or DIAS_PREVISAO), 60))
    g0 = venc_util(hoje, feriados)
    ult = g0
    for _ in range(dias_uteis - 1):
        ult = dia_util_seguinte(ult, feriados)
    # cada mês é conciliado inteiro (um boleto que sai de um dia para o outro precisa dos dois lados na conta)
    resumo, m = [], date(int(g0[:4]), int(g0[5:7]), 1)
    while m.isoformat() <= ult:
        resumo += conciliar(conexao, m.year, m.month, hoje=hoje, aprender=False)['resumo']
        m = date(m.year + (m.month == 12), m.month % 12 + 1, 1)
    res = {'resumo': resumo}
    meses_rel = {r[0] for r in conexao.execute('SELECT DISTINCT substr(dt_venc, 1, 7) FROM conc_relatorio')}
    linhas = [_linha_previsao(x, x['data'][:7] in meses_rel) for x in res['resumo'] if g0 <= x['data'] <= ult]
    tot = {k: sum(l[k] for l in linhas) for k in ('programado', 'lancado', 'falta_lancar', 'lancados_fora_base', 'lancado_da_base')}
    tot['valor_lancado'] = round(sum(l['valor_lancado'] for l in linhas), 2)
    tot['pct'] = round(100.0 * tot['lancado_da_base'] / (tot['lancado_da_base'] + tot['falta_lancar']), 1) \
        if (tot['lancado_da_base'] + tot['falta_lancar']) else None
    return {'hoje': hoje, 'de': g0, 'ate': ult, 'dias_uteis': dias_uteis, 'dias': linhas, 'totais': tot,
            'meses_sem_relatorio': sorted({l['data'][:7] for l in linhas if l['sem_relatorio']})}


def info_fontes(conexao, mes_txt, dia_iso=''):
    garantir_tabelas(conexao)
    meses_rel = [r[0] for r in conexao.execute('SELECT DISTINCT substr(dt_venc, 1, 7) m FROM conc_relatorio ORDER BY m')]
    arqs_dia = [r[0] for r in conexao.execute('SELECT DISTINCT arquivo FROM conc_bw_dias WHERE data = ? ORDER BY arquivo',
                                              (dia_iso,))] if dia_iso else []
    meta = conexao.execute('SELECT arquivo, usuario, carregado_em, linhas, meses FROM conc_relatorio_meta ORDER BY id DESC LIMIT 1').fetchone()
    bw_arqs = conexao.execute('''SELECT arquivo, origem, COUNT(*) q, MIN(data) d0, MAX(data) d1, MAX(lido_em) l FROM conc_bw
                                 WHERE substr(data, 1, 7) = ? GROUP BY arquivo, origem ORDER BY MAX(id) DESC''', (mes_txt,)).fetchall()
    ultima_rede = conexao.execute('SELECT MAX(lido_em) FROM conc_bw_arquivos').fetchone()[0]
    # último dia de pagamento com BW (o arquivo é D-1); data futura não conta
    ultimo = conexao.execute('SELECT MAX(data) FROM conc_bw_dias WHERE data <= ?', (date.today().isoformat(),)).fetchone()[0]
    arqs_ultimo = [r[0] for r in conexao.execute('SELECT DISTINCT arquivo FROM conc_bw_dias WHERE data = ? ORDER BY arquivo',
                                                 (ultimo,))] if ultimo else []
    desde = (date(int(mes_txt[:4]), int(mes_txt[5:7]), 1) - timedelta(days=45)).isoformat()
    try:
        cargas = conexao.execute("SELECT COUNT(*), MAX(data_hora) FROM produtividade WHERE acao LIKE 'UPLOAD BASE|%' "
                                 "AND data_hora >= ?", (desde,)).fetchone()
    except Exception:
        cargas = (None, None)
    inc = conexao.execute('SELECT arquivo, carregado_em, linhas, abas, origem FROM conc_inc_meta ORDER BY id DESC LIMIT 1').fetchone()
    inc_ate = conexao.execute('SELECT MAX(venc) FROM conc_inc').fetchone()[0] if inc else None
    return {
        'inc': ({'arquivo': inc[0], 'carregado_em': inc[1], 'linhas': inc[2], 'abas': inc[3], 'origem': inc[4] or 'upload', 'venc_ate': inc_ate or ''} if inc else None),
        'relatorio': ({'arquivo': meta[0], 'usuario': meta[1], 'carregado_em': meta[2], 'linhas': meta[3], 'meses': meta[4]}
                      if meta else None),
        'bw_arquivos': [{'arquivo': r[0], 'origem': r[1], 'pagamentos': r[2], 'de': _br(r[3] or ''), 'ate': _br(r[4] or ''),
                         'lido_em': r[5]} for r in bw_arqs],
        'bw_ultima_leitura_rede': ultima_rede,
        'bw_ultimo_dia': ultimo or '', 'bw_ultimo_arquivos': arqs_ultimo, 'bw_arquivos_dia': arqs_dia,
        'relatorio_meses': meses_rel,
        'base_cargas': cargas[0] if cargas else None, 'base_ultima_carga': cargas[1] if cargas else None,
    }


def exportar_csv(resultado):
    saida = io.StringIO()
    w = csv.writer(saida, delimiter=';')
    w.writerow(['Grupo', 'Situação', 'Dia útil', 'Vencimento', 'Imóvel', 'Carteira', 'Administradora', 'Empreendimento', 'Na base atual',
                'Status no sistema', 'Lançamentos', 'Valor lançado', 'Valor pago no BW', 'Referência BW', 'Arquivo BW', 'Detalhe'])
    cats = resultado['categorias']
    grupos = resultado.get('grupos') or grupos_api()
    for it in resultado['itens']:
        refs = ', '.join(sorted({l['ref_bw'] for l in it['lancs'] if l.get('ref_bw')} | ({it['ref_bw']} if it.get('ref_bw') else set())))
        arqs = ', '.join(sorted({l['arquivo_bw'] for l in it['lancs'] if l.get('arquivo_bw')} | ({it['arquivo_bw']} if it.get('arquivo_bw') else set())))
        w.writerow([grupos[cats[it['categoria']]['grupo']]['nome'], cats[it['categoria']]['rotulo'], _br(it['grupo']), it['venc'], it['imovel'], it['carteira'] or '', it['adm'], it['emp'],
                    'Sim' if it['na_base_atual'] else 'Não', it['status_sistema'], it['qtd_lanc'],
                    f"{it['valor_lanc']:.2f}".replace('.', ','), f"{it['valor_pago']:.2f}".replace('.', ','), refs, arqs, it['detalhe']])
    return '\ufeff' + saida.getvalue()


# ------------------------------------------------------------------ rotas
def registrar_rotas(app, gestor_obrigatorio, conectar_banco):
    from flask import Response, jsonify, render_template, request, session

    def _mes_pedido(conexao):
        m = re.match(r'^(\d{4})-(\d{2})$', str(request.args.get('mes') or '').strip())
        if m and 1 <= int(m.group(2)) <= 12:
            return int(m.group(1)), int(m.group(2))
        p = mes_padrao(conexao)
        return int(p[:4]), int(p[5:7])

    def _dia_pedido():
        t = str(request.args.get('data') or '').strip()
        try:
            return date.fromisoformat(t).isoformat() if re.match(r'^\d{4}-\d{2}-\d{2}$', t) else None
        except ValueError:
            return None

    @app.route('/api/conciliacao_bw/dia')
    @gestor_obrigatorio
    def api_conciliacao_bw_dia():
        conexao = conectar_banco()
        try:
            with _TRAVA:
                pedido = _dia_pedido() or dia_padrao()
                a = int(pedido[:4])
                feriados = _feriados({a - 1, a, a + 1})
                dia_util = venc_util(pedido, feriados)
                forcar = request.args.get('atualizar') == '1'       # botão "Buscar BW do dia": pula o cache de propósito
                cache = None if forcar else _cache_dia_obter(dia_util)
                if cache is not None:
                    res = dict(cache)
                else:
                    try:
                        if dia_util >= date.today().isoformat():
                            raise _SemBusca()
                        busca = buscar_bw_do_dia(conexao, dia_util)       # escolheu o dia: busca o BW dele na pasta também
                        conexao.commit()
                    except _SemBusca:
                        busca = {'acessivel': True, 'arquivos': [], 'msg': ''}
                    except Exception as e:
                        conexao.rollback()
                        busca = {'acessivel': False, 'arquivos': [], 'msg': f'Não consegui ler a pasta do retorno ({e.__class__.__name__}).'}
                    inc_sync = sincronizar_inc(conexao, usuario=session.get('usuario', 'Sistema'))     # Inc. Bradesco direto do Google
                    res = conciliar_dia(conexao, pedido)
                    conexao.commit()
                    res['bw_busca'] = busca
                    res['inc_sync'] = inc_sync
                    res.pop('dias', None)
                    res['fontes'] = info_fontes(conexao, res['mes'], res['data'])
                    res['sucesso'] = True
                    _cache_dia_guardar(dia_util, res)
                # os campos de navegação são só cálculo de data (não tocam banco/rede): sempre recalculados,
                # vindo do cache ou não, pra nunca ficarem desatualizados.
                res = dict(res)
                res['pedido'] = pedido
                res['hoje'] = date.today().isoformat()
                res['padrao'] = dia_padrao()
                res['anterior'] = dia_util_anterior(dia_util, feriados)
                res['proximo'] = dia_util_seguinte(dia_util, feriados)
        except Exception as e:
            return jsonify({'sucesso': False, 'msg': f'Não consegui montar a conciliação ({e.__class__.__name__}: {e}).'}), 500
        finally:
            conexao.close()
        resp = jsonify(res)
        resp.headers['Cache-Control'] = 'no-store'
        return resp

    @app.route('/api/conciliacao_bw/previsao')
    @gestor_obrigatorio
    def api_conciliacao_bw_previsao():
        conexao = conectar_banco()
        try:
            with _TRAVA:
                r = previsao(conexao, dias_uteis=request.args.get('dias') or DIAS_PREVISAO)
            r['sucesso'] = True
            resp = jsonify(r)
            resp.headers['Cache-Control'] = 'no-store'
            return resp
        except Exception as e:
            return jsonify({'sucesso': False, 'msg': f'Não consegui montar a previsão ({e.__class__.__name__}: {e}).'}), 500
        finally:
            conexao.close()

    @app.route('/dashboard_gerencial/conciliacao_bw')
    @gestor_obrigatorio
    def conciliacao_bw_tela():
        return render_template('conciliacao_bw.html')

    @app.route('/api/conciliacao_bw/dados')
    @gestor_obrigatorio
    def api_conciliacao_bw_dados():
        conexao = conectar_banco()
        try:
            with _TRAVA:
                ano, mes = _mes_pedido(conexao)
                res = conciliar(conexao, ano, mes)
                conexao.commit()
            res.pop('itens_conta', None)
            res['fontes'] = info_fontes(conexao, res['mes'])
            res['meses'] = meses_disponiveis(conexao)
            res['sucesso'] = True
        except Exception as e:
            return jsonify({'sucesso': False, 'msg': f'Não consegui montar a conciliação ({e.__class__.__name__}: {e}).'}), 500
        finally:
            conexao.close()
        resp = jsonify(res)
        resp.headers['Cache-Control'] = 'no-store'
        return resp

    @app.route('/api/conciliacao_bw/relatorio', methods=['POST'])
    @gestor_obrigatorio
    def api_conciliacao_bw_relatorio():
        f = request.files.get('arquivo')
        if not f or not f.filename:
            return jsonify({'sucesso': False, 'msg': 'Escolha o relatório relBolMovFinanCondominios (.xlsx).'}), 400
        nome = os.path.basename(f.filename)
        if not nome.lower().endswith(('.xlsx', '.xls')):
            return jsonify({'sucesso': False, 'msg': f'{nome}: o relatório precisa ser uma planilha Excel (.xlsx ou .xls).'}), 400
        conteudo = f.read(LIMITE_BYTES_REL + 1)
        if len(conteudo) > LIMITE_BYTES_REL:
            return jsonify({'sucesso': False, 'msg': f'{nome}: arquivo grande demais (limite de 30 MB).'}), 400
        conexao = conectar_banco()
        try:
            with _TRAVA:
                info = gravar_relatorio(conexao, conteudo, nome, session.get('usuario', 'Sistema'))
                conexao.commit()
                esquecer_dia()
            return jsonify({'sucesso': True, 'linhas': info['linhas'], 'meses': info['meses'],
                            'msg': f'{nome}: {info["linhas"]} lançamentos carregados.'})
        except ErroConciliacao as e:
            conexao.rollback()
            return jsonify({'sucesso': False, 'msg': str(e)}), 400
        except Exception as e:
            conexao.rollback()
            return jsonify({'sucesso': False, 'msg': f'Não consegui ler o relatório ({e.__class__.__name__}: {e}).'}), 500
        finally:
            conexao.close()

    @app.route('/api/conciliacao_bw/inc', methods=['POST'])
    @gestor_obrigatorio
    def api_conciliacao_bw_inc():
        f = request.files.get('arquivo')
        if not f or not f.filename:
            return jsonify({'sucesso': False, 'msg': 'Escolha a planilha Inc. Bradesco (.xlsx).'}), 400
        nome = os.path.basename(f.filename)
        if not nome.lower().endswith('.xlsx'):
            return jsonify({'sucesso': False, 'msg': f'{nome}: a planilha Inc. Bradesco precisa ser .xlsx (no Google Sheets: Arquivo > Fazer download > Excel).'}), 400
        conteudo = f.read(LIMITE_BYTES_REL + 1)
        if len(conteudo) > LIMITE_BYTES_REL:
            return jsonify({'sucesso': False, 'msg': f'{nome}: arquivo grande demais (limite de 30 MB).'}), 400
        conexao = conectar_banco()
        try:
            with _TRAVA:
                info = gravar_inc(conexao, conteudo, nome, session.get('usuario', 'Sistema'))
                conexao.commit()
                esquecer_dia()
            return jsonify({'sucesso': True, 'linhas': info['linhas'], 'abas': info['abas'],
                            'msg': f'{nome}: {info["linhas"]} devoluções do banco carregadas ({", ".join(info["abas"])}).'})
        except ErroConciliacao as e:
            conexao.rollback()
            return jsonify({'sucesso': False, 'msg': str(e)}), 400
        except Exception as e:
            conexao.rollback()
            return jsonify({'sucesso': False, 'msg': f'Não consegui ler a planilha ({e.__class__.__name__}: {e}).'}), 500
        finally:
            conexao.close()

    @app.route('/api/conciliacao_bw/inc_google', methods=['POST'])
    @gestor_obrigatorio
    def api_conciliacao_bw_inc_google():
        conexao = conectar_banco()
        try:
            with _TRAVA:
                r = sincronizar_inc(conexao, forcar=True, usuario=session.get('usuario', 'Sistema'))
                conexao.commit()
                esquecer_dia()
            return jsonify({'sucesso': r['ok'], 'msg': r['msg'], 'linhas': r['linhas']}), (200 if r['ok'] else 502)
        except Exception as e:
            conexao.rollback()
            return jsonify({'sucesso': False, 'msg': f'Não consegui atualizar o Inc. Bradesco ({e.__class__.__name__}).'}), 500
        finally:
            conexao.close()

    @app.route('/api/conciliacao_bw/bw', methods=['POST'])
    @gestor_obrigatorio
    def api_conciliacao_bw_upload():
        envio = [f for f in request.files.getlist('arquivos') if f and f.filename]
        if not envio:
            return jsonify({'sucesso': False, 'msg': 'Escolha ao menos um arquivo de retorno (.RET).'}), 400
        if len(envio) > LIMITE_ARQUIVOS_BW:
            return jsonify({'sucesso': False, 'msg': f'Envie no máximo {LIMITE_ARQUIVOS_BW} arquivos por vez.'}), 400
        conexao = conectar_banco()
        try:
            total_pags = novos = 0
            sem_bw = []
            dias_lidos = set()
            with _TRAVA:
                for f in envio:
                    nome = os.path.basename(f.filename)
                    conteudo = f.read(LIMITE_BYTES_BW + 1)
                    if len(conteudo) > LIMITE_BYTES_BW:
                        return jsonify({'sucesso': False, 'msg': f'{nome}: arquivo grande demais (limite de 20 MB).'}), 400
                    pags = ler_arquivo_bw(conteudo, nome)
                    dias_arq = dias_pagos_no_arquivo(conteudo)
                    if not pags:
                        sem_bw.append(nome)
                    total_pags += len(pags)
                    dias_lidos |= dias_arq | {p['data_pag'] for p in pags if p.get('data_pag')}
                    novos += gravar_bw(conexao, pags, 'upload', dias_arq, nome)
                conexao.commit()
                esquecer_dia()
            msg = f'{total_pags} pagamento(s) com carteira lido(s), {novos} novo(s).'
            if dias_lidos:
                msg += ' Pagamentos de ' + ', '.join(_br(d)[:5] for d in sorted(dias_lidos)) + '.'
            if sem_bw:
                msg += f' Sem linha 02BW com carteira: {", ".join(sem_bw[:5])}.'
            return jsonify({'sucesso': True, 'pagamentos': total_pags, 'novos': novos, 'msg': msg})
        finally:
            conexao.close()

    @app.route('/api/conciliacao_bw/ler_rede', methods=['POST'])
    @gestor_obrigatorio
    def api_conciliacao_bw_ler_rede():
        conexao = conectar_banco()
        try:
            with _TRAVA:
                r = ler_bw_da_rede(conexao)
                conexao.commit()
                esquecer_dia()
            r['sucesso'] = True
            return jsonify(r)
        except Exception as e:
            return jsonify({'sucesso': False, 'msg': f'Não consegui ler a pasta do retorno ({e.__class__.__name__}).'}), 500
        finally:
            conexao.close()

    @app.route('/api/conciliacao_bw/exportar')
    @gestor_obrigatorio
    def api_conciliacao_bw_exportar():
        conexao = conectar_banco()
        try:
            dia = _dia_pedido()
            if dia:
                res = conciliar_dia(conexao, dia, aprender=False)
                nome = f'conciliacao_base_relatorio_bw_{res["data"]}.csv'
            else:
                ano, mes = _mes_pedido(conexao)
                res = conciliar(conexao, ano, mes, aprender=False)
                nome = f'conciliacao_base_relatorio_bw_{res["mes"]}.csv'
        finally:
            conexao.close()
        resp = Response(exportar_csv(res), mimetype='text/csv; charset=utf-8')
        resp.headers['Content-Disposition'] = f'attachment; filename={nome}'
        return resp