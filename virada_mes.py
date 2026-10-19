# -*- coding: utf-8 -*-
"""
virada_mes.py  —  Virada de mês do Painel de Boletos

Concentra tudo o que acontece quando o mês "vira":
  * detectar o mês da base (pelo mês de vencimento dominante, mesma regra que o app já usa);
  * arquivar a base ativa no cofre (historico_fechamento_mes) sem duplicar;
  * guardar/congelar a meta por mês;
  * tirar backup do banco antes de uma virada.

Todas as funções recebem uma conexão sqlite3 aberta (de preferência com
row_factory = sqlite3.Row, como a conectar_banco() do app.py) e NÃO fazem
commit sozinhas — quem chama controla a transação. As exceções são
garantir_estrutura_virada() (quem chama dá o commit) e backup_banco(), que usa
conexões próprias.
"""
import json
import os
import re
import sqlite3
from collections import Counter
from datetime import datetime

MESES_PT = {1: 'Janeiro', 2: 'Fevereiro', 3: 'Março', 4: 'Abril', 5: 'Maio', 6: 'Junho',
            7: 'Julho', 8: 'Agosto', 9: 'Setembro', 10: 'Outubro', 11: 'Novembro', 12: 'Dezembro'}
_MES_NUM = {nome: num for num, nome in MESES_PT.items()}
_RE_ROTULO = re.compile(r'^(' + '|'.join(MESES_PT.values()) + r')/(\d{4})$')

# Colunas novas do cofre (a fotografia agora guarda mais informação)
COLUNAS_EXTRAS_HISTORICO = [
    ('carteira', 'TEXT'), ('sacado', 'TEXT'), ('empreendimento', 'TEXT'),
    ('coletor', 'TEXT'), ('data_coleta', 'TEXT'), ('ultima_observacao', 'TEXT'),
]


# ---------------------------------------------------------------------------
# Rótulos e datas
# ---------------------------------------------------------------------------
def rotulo_mes(ano, mes):
    """(2026, 9) -> 'Setembro/2026' (mesmo formato já usado no cofre)."""
    return f"{MESES_PT[int(mes)]}/{int(ano)}"


def rotulo_para_tupla(rotulo):
    """'Setembro/2026' -> (2026, 9). Retorna None se o formato for inválido."""
    m = _RE_ROTULO.match(str(rotulo or '').strip())
    if not m:
        return None
    return int(m.group(2)), _MES_NUM[m.group(1)]


def _validar(dia, mes, ano):
    if 1 <= mes <= 12 and 1 <= dia <= 31 and 2000 <= ano <= 2100:
        return dia, mes, ano
    return None


def parse_data_flex(valor):
    """Aceita 'dd/mm/aaaa', 'dd/mm/aa', 'aaaa-mm-dd[ hh:mm:ss]', datetime/Timestamp.
    Retorna (dia, mes, ano) ou None."""
    if valor is None:
        return None
    if hasattr(valor, 'year') and hasattr(valor, 'month') and hasattr(valor, 'day'):
        try:
            return _validar(int(valor.day), int(valor.month), int(valor.year))
        except (ValueError, TypeError):  # NaT
            return None
    if isinstance(valor, float) and valor != valor:  # NaN
        return None
    s = str(valor).strip()
    if not s or s.lower() in ('nan', 'nat', 'none', '-'):
        return None
    s = s.split(' ')[0].split('T')[0]
    m = re.match(r'^(\d{4})-(\d{1,2})-(\d{1,2})$', s)
    if m:
        return _validar(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    m = re.match(r'^(\d{1,2})/(\d{1,2})/(\d{2,4})$', s)
    if m:
        ano = int(m.group(3))
        if ano < 100:
            ano += 2000
        return _validar(int(m.group(1)), int(m.group(2)), ano)
    return None


def mes_dominante(datas):
    """Mês com mais datas. Retorna ((ano, mes), qtd_do_mes, total_validas)."""
    cont = Counter()
    for d in datas:
        p = parse_data_flex(d)
        if p:
            cont[(p[2], p[1])] += 1
    if not cont:
        return None, 0, 0
    chave, qtd = max(cont.items(), key=lambda kv: (kv[1], kv[0]))  # empate: o mês mais novo
    return chave, qtd, sum(cont.values())


def mes_dominante_planilha(df):
    """Mês dominante da planilha. Usa a MESMA regra do upload: Dt.ComDesc vale mais que Vencimento."""
    colunas = list(df.columns)
    col_v = df['Vencimento'].tolist() if 'Vencimento' in colunas else []
    col_d = df['Dt.ComDesc'].tolist() if 'Dt.ComDesc' in colunas else []
    efetivas = []
    for i in range(max(len(col_v), len(col_d))):
        dc = col_d[i] if i < len(col_d) else None
        vb = col_v[i] if i < len(col_v) else None
        efetivas.append(dc if parse_data_flex(dc) else vb)
    return mes_dominante(efetivas)


def mes_dominante_base_ativa(conexao):
    rows = conexao.execute("SELECT vencimento FROM imoveis WHERE ativo = 1").fetchall()
    return mes_dominante([r[0] for r in rows])


# ---------------------------------------------------------------------------
# Estrutura do banco
# ---------------------------------------------------------------------------
def garantir_estrutura_virada(conexao):
    """Cria/atualiza as tabelas usadas na virada. Idempotente. Quem chama dá o commit."""
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
    existentes = {r[1] for r in conexao.execute("PRAGMA table_info(historico_fechamento_mes)").fetchall()}
    for nome, tipo in COLUNAS_EXTRAS_HISTORICO:
        if nome not in existentes:
            conexao.execute(f"ALTER TABLE historico_fechamento_mes ADD COLUMN {nome} {tipo}")
    conexao.execute('CREATE TABLE IF NOT EXISTS config_sistema (chave TEXT PRIMARY KEY, valor TEXT)')
    conexao.execute('''
        CREATE TABLE IF NOT EXISTS log_viradas_mes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            data_hora TEXT,
            mes_arquivado TEXT,
            qtd INTEGER,
            origem TEXT,
            usuario TEXT,
            arquivo TEXT,
            backup TEXT
        )
    ''')
    conexao.execute('CREATE INDEX IF NOT EXISTS idx_hfm_mes ON historico_fechamento_mes (mes_referencia)')
    conexao.execute('CREATE INDEX IF NOT EXISTS idx_hfm_imovel_venc ON historico_fechamento_mes (imovel, vencimento)')


# ---------------------------------------------------------------------------
# Arquivamento
# ---------------------------------------------------------------------------
def detectar_mes_ativo(conexao):
    """Informações da base ativa para a tela de arquivamento manual."""
    dom, qtd_mes, _ = mes_dominante_base_ativa(conexao)
    rotulo = rotulo_mes(dom[0], dom[1]) if dom else ''
    qtd_base = conexao.execute("SELECT COUNT(*) FROM imoveis WHERE ativo = 1").fetchone()[0]
    qtd_arq = 0
    if rotulo:
        qtd_arq = conexao.execute("SELECT COUNT(*) FROM historico_fechamento_mes WHERE mes_referencia = ?",
                                  (rotulo,)).fetchone()[0]
    ano_ref = dom[0] if dom else datetime.now().year
    opcoes = [rotulo_mes(a, m) for a in (ano_ref - 1, ano_ref, ano_ref + 1) for m in range(1, 13)]
    return {'rotulo': rotulo, 'qtd_base': qtd_base, 'qtd_mes': qtd_mes,
            'ja_arquivado': qtd_arq > 0, 'qtd_arquivado': qtd_arq, 'opcoes': opcoes}


def arquivar_base_ativa(conexao, mes_referencia, origem='MANUAL', usuario=None, arquivo=None,
                        substituir=False, backup=None):
    """Fotografa a base ativa (imoveis.ativo = 1) no cofre sob o rótulo mes_referencia.
    NÃO faz commit. Retorna {'status': 'ok' | 'ja_existe' | 'vazio', 'qtd': n, ...}."""
    ativos = conexao.execute('''
        SELECT i.id, i.imovel, i.administradora, i.vencimento, i.status_interno,
               i.carteira, i.sacado, i.empreendimento,
               (SELECT v.responsavel FROM vinculos_adm v
                 WHERE TRIM(v.administradora) = TRIM(i.administradora) LIMIT 1) AS responsavel
          FROM imoveis i
         WHERE i.ativo = 1
    ''').fetchall()
    if not ativos:
        return {'status': 'vazio', 'mes_referencia': mes_referencia, 'qtd': 0}

    existentes = conexao.execute('SELECT COUNT(*) FROM historico_fechamento_mes WHERE mes_referencia = ?',
                                 (mes_referencia,)).fetchone()[0]
    if existentes and not substituir:
        return {'status': 'ja_existe', 'mes_referencia': mes_referencia, 'qtd': existentes}
    if existentes:
        conexao.execute('DELETE FROM historico_fechamento_mes WHERE mes_referencia = ?', (mes_referencia,))

    # Quem coletou e quando / última observação — por (imóvel, mês-ano do vencimento)
    coletas = {}
    for r in conexao.execute('''
        SELECT o.imovel, SUBSTR(IFNULL(o.vencimento, ''), 4, 7), p.usuario, p.data_hora
          FROM produtividade p JOIN imoveis o ON p.imovel_id = o.id
         WHERE p.acao = 'COLETOU BOLETO' ORDER BY p.id
    ''').fetchall():
        coletas[(r[0], r[1])] = (r[2], r[3])
    notas = {}
    for r in conexao.execute('''
        SELECT o.imovel, SUBSTR(IFNULL(o.vencimento, ''), 4, 7), h.texto
          FROM historico_observacoes h JOIN imoveis o ON h.imovel_id = o.id
         ORDER BY h.id
    ''').fetchall():
        notas[(r[0], r[1])] = r[2]

    linhas = []
    for a in ativos:
        ciclo = str(a[3] or '')[3:10]
        coletor, data_coleta = coletas.get((a[1], ciclo), (None, None))
        linhas.append((mes_referencia, a[1], a[2], a[3], a[4], a[8], a[5], a[6], a[7],
                       coletor, data_coleta, notas.get((a[1], ciclo))))
    conexao.executemany('''
        INSERT INTO historico_fechamento_mes
            (mes_referencia, imovel, administradora, vencimento, status_interno, responsavel,
             carteira, sacado, empreendimento, coletor, data_coleta, ultima_observacao)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', linhas)
    conexao.execute('''INSERT INTO log_viradas_mes (data_hora, mes_arquivado, qtd, origem, usuario, arquivo, backup)
                       VALUES (?, ?, ?, ?, ?, ?, ?)''',
                    (datetime.now().strftime('%Y-%m-%d %H:%M:%S'), mes_referencia, len(linhas),
                     origem, usuario, arquivo, backup))
    return {'status': 'ok', 'mes_referencia': mes_referencia, 'qtd': len(linhas),
            'substituiu': bool(existentes)}


# ---------------------------------------------------------------------------
# Meta (congelar / descongelar) — compatível com o dashboard atual (meta_dia_DD)
# ---------------------------------------------------------------------------
def meta_mes_ref_atual(conexao):
    try:
        r = conexao.execute("SELECT valor FROM config_sistema WHERE chave = 'meta_mes_ref'").fetchone()
        return (r[0] or '') if r else ''
    except Exception:
        return ''


def congelar_meta(conexao, rotulo=None):
    """Congela a meta com a contagem de boletos da base ativa, por dia de vencimento."""
    conexao.execute('CREATE TABLE IF NOT EXISTS config_sistema (chave TEXT PRIMARY KEY, valor TEXT)')
    conexao.execute("DELETE FROM config_sistema WHERE chave LIKE 'meta_dia_%'")
    dados = conexao.execute('''
        SELECT SUBSTR(TRIM(vencimento), 1, 2) AS dia, COUNT(*) AS qtd
          FROM imoveis
         WHERE ativo = 1 AND administradora IS NOT NULL AND administradora != ''
         GROUP BY dia
    ''').fetchall()
    total = 0
    for r in dados:
        dia, qtd = r[0], r[1]
        if dia and str(dia).isdigit():
            conexao.execute("INSERT INTO config_sistema (chave, valor) VALUES (?, ?)",
                            (f"meta_dia_{int(dia):02d}", str(qtd)))
            total += qtd
    if not rotulo:
        dom, _, _ = mes_dominante_base_ativa(conexao)
        rotulo = rotulo_mes(dom[0], dom[1]) if dom else ''
    conexao.execute("INSERT OR REPLACE INTO config_sistema (chave, valor) VALUES ('meta_mes_ref', ?)", (rotulo,))
    return {'total': total, 'rotulo': rotulo}


def arquivar_meta_atual(conexao, rotulo_padrao=''):
    """Guarda uma cópia da meta congelada em config_sistema (chave arquivo_meta|AAAA-MM)."""
    linhas = conexao.execute("SELECT chave, valor FROM config_sistema WHERE chave LIKE 'meta_dia_%'").fetchall()
    if not linhas:
        return None
    rotulo = meta_mes_ref_atual(conexao) or rotulo_padrao or 'sem_rotulo'
    tupla = rotulo_para_tupla(rotulo)
    chave = f"arquivo_meta|{tupla[0]}-{tupla[1]:02d}" if tupla else f"arquivo_meta|{rotulo}"
    metas = {str(r[0]).replace('meta_dia_', ''): int(r[1]) for r in linhas if str(r[1]).isdigit()}
    payload = {'rotulo': rotulo, 'arquivado_em': datetime.now().strftime('%Y-%m-%d %H:%M:%S'), 'metas': metas}
    conexao.execute("INSERT OR REPLACE INTO config_sistema (chave, valor) VALUES (?, ?)",
                    (chave, json.dumps(payload, ensure_ascii=False)))
    return chave


def descongelar_meta(conexao):
    """Descongela a meta, guardando antes uma cópia dela."""
    arquivar_meta_atual(conexao)
    conexao.execute("DELETE FROM config_sistema WHERE chave LIKE 'meta_dia_%'")
    conexao.execute("DELETE FROM config_sistema WHERE chave = 'meta_mes_ref'")


# ---------------------------------------------------------------------------
# Backup
# ---------------------------------------------------------------------------
def backup_banco(conexao, manter=12, prefixo='pre_virada'):
    """Cópia consistente do banco (API de backup do SQLite) em <pasta do banco>/backups/.
    Mantém só os `manter` mais recentes. Retorna o caminho ou None (banco em memória)."""
    caminho = None
    for r in conexao.execute('PRAGMA database_list').fetchall():
        if r[1] == 'main':
            caminho = r[2]
    if not caminho:
        return None
    pasta = os.path.join(os.path.dirname(caminho), 'backups')
    os.makedirs(pasta, exist_ok=True)
    destino = os.path.join(pasta, f"banco_{prefixo}_{datetime.now():%Y%m%d_%H%M%S}.db")
    origem = sqlite3.connect(caminho, timeout=30)
    try:
        dst = sqlite3.connect(destino)
        try:
            origem.backup(dst)
        finally:
            dst.close()
    finally:
        origem.close()
    prefixo_arq = f"banco_{prefixo}_"
    arquivos = sorted(f for f in os.listdir(pasta) if f.startswith(prefixo_arq) and f.endswith('.db'))
    for velho in arquivos[:-manter] if manter > 0 else []:
        try:
            os.remove(os.path.join(pasta, velho))
        except OSError:
            pass
    return destino


# ---------------------------------------------------------------------------
# Virada automática no upload da planilha
# ---------------------------------------------------------------------------
def decidir_virada(antigo, novo):
    """antigo/novo = (ano, mes) ou None. Só há virada se o mês novo for POSTERIOR ao da base ativa."""
    info = {'virada': False, 'motivo': '', 'mes_antigo_label': '', 'mes_novo_label': ''}
    if antigo:
        info['mes_antigo_label'] = rotulo_mes(antigo[0], antigo[1])
    if novo:
        info['mes_novo_label'] = rotulo_mes(novo[0], novo[1])
    if not antigo:
        info['motivo'] = 'base ativa vazia'
    elif not novo:
        info['motivo'] = 'não foi possível ler as datas da planilha'
    elif novo > antigo:
        info['virada'] = True
        info['motivo'] = 'mês novo posterior ao da base ativa'
    elif novo == antigo:
        info['motivo'] = 'mesmo mês (atualização da base)'
    else:
        info['motivo'] = 'planilha de mês anterior ao da base ativa'
    return info


def preparar_virada_upload(conexao, df_novo, usuario=None, arquivo=None):
    """Chamar ANTES de desativar a base antiga. Se for virada de mês: faz backup, abre
    transação exclusiva e arquiva a base ativa. Se der erro, levanta exceção (o upload aborta
    sem mexer em nada). Se não for virada, não altera nada."""
    garantir_estrutura_virada(conexao)
    conexao.commit()
    antigo, _, _ = mes_dominante_base_ativa(conexao)
    novo, _, _ = mes_dominante_planilha(df_novo)
    info = decidir_virada(antigo, novo)
    info.update({'arquivado': 0, 'arquivo_status': '', 'backup': None, 'backup_erro': ''})
    if not info['virada']:
        return info
    try:
        info['backup'] = backup_banco(conexao)
    except Exception as e:  # backup é um extra: avisa, mas não impede o arquivamento
        info['backup_erro'] = str(e)
    if conexao.in_transaction:
        conexao.commit()
    conexao.execute('BEGIN IMMEDIATE')
    res = arquivar_base_ativa(conexao, info['mes_antigo_label'], origem='UPLOAD', usuario=usuario,
                              arquivo=arquivo, substituir=False, backup=info['backup'])
    info['arquivo_status'] = res['status']
    info['arquivado'] = res.get('qtd', 0)
    return info


def finalizar_virada_upload(conexao, info):
    """Chamar DEPOIS de inserir a base nova (mesma transação): guarda a meta do mês que fechou
    e congela a meta do mês novo."""
    if not info or not info.get('virada'):
        return info
    arquivar_meta_atual(conexao, info.get('mes_antigo_label', ''))
    meta = congelar_meta(conexao, info.get('mes_novo_label'))
    info['meta_total'] = meta['total']
    return info


def montar_aviso(info):
    """Texto mostrado ao usuário depois do upload (vazio se não houve virada)."""
    if not info or not info.get('virada'):
        return ''
    antigo, novo = info['mes_antigo_label'], info['mes_novo_label']
    linhas = [f"🔄 Virada de mês detectada: {antigo} → {novo}."]
    if info.get('arquivo_status') == 'ok':
        linhas.append(f"{info['arquivado']} imóveis de {antigo} foram arquivados no histórico.")
    elif info.get('arquivo_status') == 'ja_existe':
        linhas.append(f"{antigo} já estava arquivado — o arquivo existente foi mantido.")
    linhas.append(f"Status e observações começam zerados para {novo}.")
    if info.get('meta_total'):
        linhas.append(f"Meta de {novo} congelada automaticamente: {info['meta_total']} boletos.")
    if info.get('backup'):
        linhas.append("Backup do banco salvo na pasta backups/.")
    elif info.get('backup_erro'):
        linhas.append("⚠️ O backup do banco falhou (o arquivamento foi feito normalmente).")
    return "\n".join(linhas)