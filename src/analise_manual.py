# -*- coding: utf-8 -*-
"""
Análise Automático × Manual dos relatórios relBolMovFinanCondominios.

Lê um ou mais relatórios (xlsx), agrupa pelo mês da Dt.Vencto, classifica cada lançamento manual com um
"motivo provável" e grava um JSON que a página /dashboard_gerencial/auto_manual desenha.

Uso no app.py (3 linhas, depois de criar o `app` e de definir `gestor_obrigatorio`):

    try:
        from . import analise_manual
    except ImportError:
        import analise_manual
    analise_manual.registrar_rotas(app, gestor_obrigatorio, _pasta_do_banco())

O JSON fica ao lado do banco.db (dados_analise_manual.json).
Os motivos são INFERIDOS dos campos do relatório: ele não traz coluna de motivo nem de usuário.
"""
import hashlib
import io
import json
import os
import re
import threading
import unicodedata
from datetime import datetime

import numpy as np
import pandas as pd

ARQUIVO_JSON = 'dados_analise_manual.json'
VERSAO = 1
MESES_ABREV = ['Jan', 'Fev', 'Mar', 'Abr', 'Mai', 'Jun', 'Jul', 'Ago', 'Set', 'Out', 'Nov', 'Dez']
LIMITE_ARQUIVOS = 12
LIMITE_BYTES = 30 * 1024 * 1024
_TRAVA = threading.Lock()

# ------------------------------------------------------------------ regras
# (código, rótulo, coluna booleana, confiança, grupo)
MOTIVOS = [
    ('M01', 'Pagamento fora do boleto (Internet / Ctas. Pagar)', 'f_pg', 'Alta', 'Cadastro'),
    ('M02', 'Situação "L" no lançamento', 'f_L', 'Alta', 'Cadastro'),
    ('M03', 'Cancelado (Flag C) e relançado manual no mesmo imóvel/mês (retrabalho)', 'f_relancado', 'Alta', 'Processo'),
    ('M04', 'Vencimento alternativo por verba (Vencto. Alter.)', 'f_alter', 'Alta', 'Cadastro'),
    ('M05', 'Vencimento diferente do dia da ficha', 'f_ficha_dif', 'Alta', 'Cadastro'),
    ('M06', 'Vencimento deslocado p/ dia útil (fim de semana/feriado)', 'f_ficha_util', 'Média', 'Cadastro'),
    ('M07', 'Mais de um boleto do mesmo imóvel no mês', 'f_multi', 'Média', 'Cadastro'),
    ('M08', 'Observação específica na ficha (cheque, gás, sala, vaga, atenção)', 'f_obs', 'Média', 'Cadastro'),
    ('M09', 'Lançado fora da janela do automático (após o último lote ou ≤ 2 dias do venc.)', 'f_tardio', 'Alta', 'Prazo'),
    ('M10', 'Condomínio com histórico manual (≥ 60% dos lançamentos)', 'f_cond_crit', 'Média', 'Padrão'),
    ('M11', 'Administradora com alto índice manual (≥ 40%)', 'f_adm_crit', 'Média', 'Padrão'),
    ('M12', 'Imóvel recorrente (manual em 2+ meses da base)', 'f_recorrente', 'Média', 'Padrão'),
    ('M99', 'Pontual, sem fator identificado nos dados', None, '—', 'Sem padrão'),
]
ACAO = {
    'M01': 'Rever se o pagamento por Internet/Ctas. Pagar ainda é necessário; se não, voltar a forma para Boleto na ficha.',
    'M02': 'Entender o que a situação "L" significa na rotina e se o lançamento pode seguir pelo automático.',
    'M03': 'Descobrir por que o boleto automático foi cancelado (valor, vencimento, verba) e corrigir a ficha antes do lote.',
    'M04': 'Conferir se o vencimento alternativo por verba ainda vale; alinhar o dia da verba com o dia cadastrado.',
    'M05': 'Corrigir o "Dia Vencto" da ficha (Ad. Cond.) para o dia real cobrado pela administradora.',
    'M06': 'Revisar vencimentos que caem em fim de semana/feriado: a administradora desloca e a ficha não acompanha.',
    'M07': 'Imóvel com 2+ boletos no mês (cota + extra/fundo): revisar se a ficha comporta os dois lançamentos.',
    'M08': 'Observação cita cheque/gás/sala/vaga/atenção: tratar exceção na ficha ou documentar o fluxo manual.',
    'M09': 'Antecipar a chegada dos boletos (administradora/e-mail) ou rodar um 2º lote automático antes do vencimento.',
    'M10': 'Reunião com a administradora/condomínio: padronizar envio do boleto e vencimento; revisar cadastro do condomínio.',
    'M11': 'Tratar a administradora como um todo (formato do boleto, vencimento, canal de envio).',
    'M12': 'Imóvel manual todo mês sem causa visível no relatório: investigar uma vez na ficha do imóvel e corrigir a origem.',
    'M99': 'Sem sinal nos dados: provável atraso pontual do boleto ou intervenção da equipe. Acompanhar se repete.',
}
OBS_GRUPOS = {
    'Cheque': r'CHEQUE',
    'Gás / rateio': r'\bG[ÁA]S\b|RATEIO',
    'Sala / loja / casa': r'\bSALAS?\b|\bSL\.?\s?\d|\bLOJA\b|\bCASA\b',
    'Vaga / garagem / box': r'\bVAGAS?\b|GARAGEM|GARAG\b|\bBOX\b',
    'Atenção / erro / restrição': r'ATEN[ÇC][ÃA]O|\bERRO\b|RESTRI[ÇC][ÃA]O|CUIDADO',
}
FAIXAS = ['Após o vencimento', 'No dia', '1-2 dias', '3-5 dias', '6-10 dias', '11-15 dias', '16-20 dias', '21+ dias']
HIST = ['Sempre manual', 'Reincidente', 'Pontual', 'Só 1 mês na base', 'Sempre automático']

# nome normalizado da coluna -> nome interno
COLUNAS = {
    'imovel': 'imovel', 'filial': 'filial', 'contrato': 'contrato', 'carteira': 'carteira',
    'administradora': 'adm', 'cnpjadm': 'cnpj_adm', 'empreendimento': 'emp', 'cnpjempreend': 'cnpj_emp',
    'tppgbol': 'tp_pg', 'tplanctobol': 'tp_lanc', 'flagsit': 'flag', 'resppagto': 'resp',
    'diavencto': 'dia_vencto', 'venctoalter': 'vencto_alter', 'dtvencto': 'dt_venc', 'dtlancto': 'dt_lanc',
    'vrboleto': 'vr_boleto', 'vrdescbol': 'vr_desc', 'observacao': 'obs',
}
OBRIGATORIAS = ['imovel', 'adm', 'emp', 'tp_pg', 'tp_lanc', 'flag', 'dia_vencto', 'vencto_alter', 'dt_venc', 'dt_lanc', 'vr_boleto']
ROTULO_COLUNA = {'imovel': 'Imóvel', 'adm': 'Administradora', 'emp': 'Empreendimento', 'tp_pg': 'Tp.Pg.Bol', 'tp_lanc': 'Tp. Lancto. Bol',
                 'flag': 'Flag.Sit.', 'dia_vencto': 'Dia Vencto', 'vencto_alter': 'Vencto.Alter.', 'dt_venc': 'Dt.Vencto',
                 'dt_lanc': 'Dt. Lancto', 'vr_boleto': 'Vr. Boleto'}


class ErroRelatorio(Exception):
    """Erro de leitura com mensagem pronta para mostrar ao usuário."""


# ------------------------------------------------------------------ leitura
def _norm(txt):
    t = unicodedata.normalize('NFKD', str(txt or '')).encode('ascii', 'ignore').decode('ascii')
    return re.sub(r'[^a-z0-9]', '', t.lower())


def ler_relatorio(origem, nome='relatório'):
    """origem: caminho ou bytes de um xlsx/xls do relBolMovFinanCondominios. Devolve DataFrame com colunas internas."""
    buf = io.BytesIO(origem) if isinstance(origem, (bytes, bytearray)) else origem
    try:
        bruto = pd.read_excel(buf, header=None, dtype=str)
    except Exception as e:
        raise ErroRelatorio(f'{nome}: não consegui abrir como planilha Excel ({e.__class__.__name__}).')
    cab = None
    for i in range(min(25, len(bruto))):
        n = {_norm(v) for v in bruto.iloc[i].tolist()}
        if 'imovel' in n and 'dtvencto' in n:
            cab = i
            break
    if cab is None:
        raise ErroRelatorio(f'{nome}: não achei a linha de cabeçalho (precisa ter "Imóvel" e "Dt.Vencto"). É o relBolMovFinanCondominios?')
    df = bruto.iloc[cab + 1:].copy()
    df.columns = [COLUNAS.get(_norm(c), None) or f'_extra_{j}' for j, c in enumerate(bruto.iloc[cab].tolist())]
    faltam = [ROTULO_COLUNA[c] for c in OBRIGATORIAS if c not in df.columns]
    if faltam:
        raise ErroRelatorio(f'{nome}: faltam as colunas {", ".join(faltam)}.')
    for c in COLUNAS.values():
        if c not in df.columns:
            df[c] = ''
    df = df[list(COLUNAS.values())].copy()
    df['imovel'] = df['imovel'].fillna('').astype(str).str.strip()
    df = df[df['imovel'] != ''].copy()
    df['arquivo'] = nome
    return df


def _numero(serie):
    s = serie.fillna('').astype(str).str.strip()
    s = s.where(~(s.str.contains(',') & s.str.contains(r'\.')), s.str.replace('.', '', regex=False))
    return pd.to_numeric(s.str.replace(',', '.', regex=False), errors='coerce')


# ------------------------------------------------------------------ feriados
def _feriados(anos):
    from dateutil.easter import easter
    h = set()
    for a in anos:
        p = pd.Timestamp(easter(a))
        h.update([pd.Timestamp(a, 1, 1), pd.Timestamp(a, 4, 21), pd.Timestamp(a, 5, 1), pd.Timestamp(a, 7, 9), pd.Timestamp(a, 9, 7),
                  pd.Timestamp(a, 10, 12), pd.Timestamp(a, 11, 2), pd.Timestamp(a, 11, 15), pd.Timestamp(a, 11, 20), pd.Timestamp(a, 12, 25),
                  p - pd.Timedelta(days=48), p - pd.Timedelta(days=47), p - pd.Timedelta(days=2), p + pd.Timedelta(days=60)])
    return h


def _rotulo_mes(chave):
    return f'{MESES_ABREV[int(chave[5:7]) - 1]}/{chave[:4]}'


# ------------------------------------------------------------------ preparo e fatores
def preparar(df):
    df = df.copy()
    for c in ['filial', 'adm', 'emp', 'cnpj_emp', 'tp_pg', 'tp_lanc', 'flag', 'dia_vencto', 'carteira']:
        df[c] = df[c].fillna('').astype(str).str.strip()
    df['venc'] = pd.to_datetime(df['dt_venc'].astype(str).str.strip(), format='%d/%m/%Y', errors='coerce')
    df['lanc'] = pd.to_datetime(df['dt_lanc'].astype(str).str.strip(), format='%d/%m/%Y', errors='coerce')
    ruins = int((df['venc'].isna() | df['lanc'].isna()).sum())
    df = df[df['venc'].notna() & df['lanc'].notna()].copy()
    df['tp_lanc'] = df['tp_lanc'].str.title()
    df = df[df['tp_lanc'].isin(['Auto', 'Manu'])].copy()
    df['valor'] = _numero(df['vr_boleto']).fillna(0.0)
    df['desc'] = _numero(df['vr_desc']).fillna(0.0)
    df['obs'] = df['obs'].fillna('').astype(str).str.replace(r'\s+', ' ', regex=True).str.strip()
    df['mes'] = df['venc'].dt.strftime('%Y-%m')
    df['manual'] = df['tp_lanc'] == 'Manu'
    df['dia_venc_real'] = df['venc'].dt.day
    df['antecedencia'] = (df['venc'] - df['lanc']).dt.days
    df['dia_ficha'] = pd.to_numeric(df['dia_vencto'], errors='coerce')
    return df.reset_index(drop=True), ruins


def construir_fatores(df):
    df = df.copy()
    # Vencto.Alter. no formato "dia verba/ / /"
    a = df['vencto_alter'].fillna('').astype(str).str.strip()
    m = a.str.extract(r'^(\d+)\s+(\d+)')
    df['alter_dia'] = pd.to_numeric(m[0], errors='coerce')
    df['f_alter'] = df['alter_dia'].notna()
    # ficha x vencimento real
    feriados = _feriados(range(int(df['venc'].dt.year.min()) - 1, int(df['venc'].dt.year.max()) + 2))

    def prox_util(d):
        while d.weekday() >= 5 or d in feriados:
            d += pd.Timedelta(days=1)
        return d

    def cls(r):
        if pd.isna(r.dia_ficha):
            return 'Ficha sem dia'
        if r.dia_ficha == r.dia_venc_real:
            return 'Confere'
        try:
            d = pd.Timestamp(year=r.venc.year, month=r.venc.month, day=int(r.dia_ficha))
        except Exception:
            return 'Ficha com dia inválido p/ o mês'
        if prox_util(d) == r.venc:
            return 'Deslocado p/ próx. dia útil'
        return 'Vencimento depois do dia da ficha' if r.venc > d else 'Vencimento antes do dia da ficha'

    df['ficha_cls'] = df.apply(cls, axis=1)
    df['f_ficha_dif'] = df['ficha_cls'].isin(['Vencimento depois do dia da ficha', 'Vencimento antes do dia da ficha', 'Ficha com dia inválido p/ o mês'])
    df['f_ficha_util'] = df['ficha_cls'] == 'Deslocado p/ próx. dia útil'
    # forma de pagamento / situação
    df['f_pg'] = df['tp_pg'].str.lower() != 'boleto'
    df['f_L'] = df['flag'] == 'L'
    # boletos múltiplos e cancelamento/relançamento no mesmo imóvel+mês
    df['_c'] = (df['flag'] == 'C').astype(int)
    df['_ac'] = ((df['flag'] == 'C') & (~df['manual'])).astype(int)
    g = df.groupby(['imovel', 'mes'])
    df['k'] = g['imovel'].transform('size')
    df['tem_C_grupo'] = g['_c'].transform('max') > 0
    df['tem_auto_C_grupo'] = g['_ac'].transform('max') > 0
    df['f_relancado'] = (df['flag'] != 'C') & df['tem_C_grupo']
    df['f_multi'] = (df['k'] >= 2) & ~df['tem_C_grupo']
    # observação da ficha
    up = df['obs'].str.upper()
    for nome, rx in OBS_GRUPOS.items():
        df['obs::' + nome] = up.str.contains(rx, regex=True)
    df['f_obs'] = df[['obs::' + n for n in OBS_GRUPOS]].any(axis=1)
    # janela do lote automático (por mês de vencimento): último dia com 20+ lançamentos automáticos
    fim = {}
    for mm, gg in df.groupby('mes'):
        x = gg[~gg.manual].groupby('lanc').size()
        x = x[x >= 20]
        fim[mm] = x.index.max() if len(x) else pd.NaT
    df['janela_fim'] = df['mes'].map(fim)
    df['f_fora_janela'] = df['janela_fim'].notna() & (df['lanc'] > df['janela_fim'])
    df['f_tardio'] = (df['antecedencia'] <= 2) | df['f_fora_janela']
    df['faixa_prazo'] = pd.cut(df['antecedencia'], [-99999, -1, 0, 2, 5, 10, 15, 20, 99999], labels=FAIXAS).astype(str)
    # histórico do imóvel nos meses da base
    meses = sorted(df['mes'].unique())
    pm = df.groupby(['imovel', 'mes'])['manual'].any().unstack().reindex(columns=meses)
    n_meses = pm.notna().sum(axis=1)
    n_man = pm.fillna(False).astype(int).sum(axis=1)
    df['n_meses_imovel'] = df['imovel'].map(n_meses)
    df['n_meses_manual'] = df['imovel'].map(n_man)

    def hist(r):
        if r.n_meses_manual == 0:
            return 'Sempre automático'
        if r.n_meses_manual == r.n_meses_imovel and r.n_meses_imovel >= 2:
            return 'Sempre manual'
        if r.n_meses_imovel == 1:
            return 'Só 1 mês na base'
        return 'Reincidente' if r.n_meses_manual >= 2 else 'Pontual'

    df['hist_imovel'] = df[['n_meses_manual', 'n_meses_imovel']].apply(hist, axis=1)
    prev = {}
    for im, row in pm.iterrows():
        for i, mes in enumerate(meses):
            if i > 0 and pd.notna(row[mes]) and pd.notna(row[meses[i - 1]]):
                prev[(im, mes)] = bool(row[meses[i - 1]])
    df['prev_manual'] = [prev.get((i, m), np.nan) for i, m in zip(df['imovel'], df['mes'])]
    df['f_recorrente'] = df['n_meses_manual'] >= 2
    # administradora e condomínio com índice alto (no conjunto da base)
    a_ = df.groupby('adm')['manual'].agg(['size', 'mean'])
    c_ = df.groupby('cnpj_emp')['manual'].agg(['size', 'mean'])
    df['f_adm_crit'] = df['adm'].isin(set(a_[(a_['size'] >= 30) & (a_['mean'] >= 0.40)].index))
    df['f_cond_crit'] = df['cnpj_emp'].isin(set(c_[(c_['size'] >= 6) & (c_['mean'] >= 0.60)].index))
    df['dow_venc'] = df['venc'].dt.dayofweek
    return df


def classificar(df):
    man = df['manual']
    prim = pd.Series('M99', index=df.index)
    for cod, rot, col, _, _g in reversed(MOTIVOS[:-1]):
        prim = prim.mask(man & df[col], cod)
    df['motivo_cod'] = prim.where(man, '')
    return df


def retrabalho(df):
    """Imóvel/mês com boleto automático cancelado (C) e lançamento manual válido no lugar.
    Quando há vários manuais no mês (cota + extras), o par do cancelado é o manual de valor mais próximo (mesmo vencimento primeiro)."""
    linhas = []
    sub = df[df.tem_auto_C_grupo]
    for (im, mes), g in sub.groupby(['imovel', 'mes']):
        ac = g[(g.flag == 'C') & (~g.manual)].sort_values(['lanc', 'valor'], kind='stable')
        mv = g[(g.flag != 'C') & (g.manual)]
        if mv.empty or ac.empty:
            continue
        a = ac.iloc[-1]
        mv = mv.assign(_d=(mv.valor - a.valor).abs(), _v=(mv.venc != a.venc).astype(int)).sort_values(['_v', '_d', 'lanc'], kind='stable')
        m = mv.iloc[0]
        linhas.append(dict(imovel=im, mes=mes, adm=m.adm, emp=m.emp, venc_auto=a.venc, venc_manual=m.venc, valor_auto=float(a.valor), valor_manual=float(m.valor),
                           lanc_auto=a.lanc, lanc_manual=m.lanc, mesmo_venc=bool(a.venc == m.venc), mesmo_valor=bool(abs(a.valor - m.valor) < 0.005)))
    return pd.DataFrame(linhas, columns=['imovel', 'mes', 'adm', 'emp', 'venc_auto', 'venc_manual', 'valor_auto', 'valor_manual', 'lanc_auto', 'lanc_manual', 'mesmo_venc', 'mesmo_valor'])


# ------------------------------------------------------------------ textos
def _pct(x, d=1):
    return f'{x * 100:.{d}f}%'.replace('.', ',')


def _n(x):
    return f'{int(x):,}'.replace(',', '.')


def periodo_texto(meses):
    if len(meses) == 1:
        return _rotulo_mes(meses[0])
    a, b = meses[0], meses[-1]
    if a[:4] == b[:4]:
        return f'{MESES_ABREV[int(a[5:7]) - 1]}–{MESES_ABREV[int(b[5:7]) - 1]}/{a[:4]}'
    return f'{_rotulo_mes(a)} a {_rotulo_mes(b)}'


def achados(df):
    m = df[df.manual]
    tot, nm = len(df), len(m)
    meses = sorted(df.mes.unique())
    por_mes = df.groupby('mes').manual.mean()
    cad = m[['f_pg', 'f_L', 'f_relancado', 'f_alter', 'f_ficha_dif', 'f_ficha_util', 'f_multi', 'f_obs']].any(axis=1)
    estr = (~cad) & m[['f_tardio', 'f_cond_crit', 'f_adm_crit', 'f_recorrente']].any(axis=1)
    sem = (m.motivo_cod == 'M99')
    dias = df.groupby('lanc').manual.sum().sort_values(ascending=False)
    top12 = int(dias.head(12).sum())
    h = df.drop_duplicates('imovel')
    sm = int((h.hist_imovel == 'Sempre manual').sum())
    rt = retrabalho(df)
    A = []
    A.append(f'De {_n(tot)} lançamentos (Dt.Vencto {periodo_texto(meses)}), {_n(tot - nm)} subiram automático ({_pct(1 - nm / tot)}) e {_n(nm)} manual ({_pct(nm / tot)}). '
             f'Índice manual por mês: ' + ' → '.join(f'{_rotulo_mes(k)} {_pct(por_mes[k])}' for k in meses) + '.')
    if nm:
        A.append(f'O manual é concentrado, não espalhado: {_n(m.imovel.nunique())} imóveis ({_pct(m.imovel.nunique() / df.imovel.nunique())} da base) geram os {_n(nm)} casos'
                 + (f', e {_n(sm)} imóveis sobem manual em todos os meses da base (respondem por {_n((m.hist_imovel == "Sempre manual").sum())} lançamentos, {_pct((m.hist_imovel == "Sempre manual").mean(), 0)} do manual).' if len(meses) >= 2 else '.'))
        A.append(f'{_n(cad.sum())} casos manuais ({_pct(cad.mean(), 0)}) têm causa visível no cadastro/processo: Internet/Ctas. Pagar, cancelado e relançado, vencimento alternativo, dia da ficha ≠ vencimento, dia útil, 2+ boletos ou observação específica.')
        A.append(f'Mais {_n(estr.sum())} casos ({_pct(estr.mean(), 0)}) não têm causa direta, mas seguem padrão previsível: lançados fora da janela do automático, condomínio/administradora/imóvel que sobe manual todo mês. Só {_n(sem.sum())} ({_pct(sem.mean(), 0)}) ficam sem nenhum sinal.')
        fj = df[df.f_fora_janela]
        A.append(f'O manual acontece em mutirões: 12 dias de lançamento concentram {_n(top12)} dos {_n(nm)} casos ({_pct(top12 / nm, 0)}).'
                 + (f' Depois do último lote automático do mês, {_pct(fj.manual.mean(), 0)} do que sobe é manual.' if len(fj) else ''))
    if len(rt):
        corr = int(((~rt.mesmo_venc) | (~rt.mesmo_valor)).sum())
        top = rt.adm.value_counts()
        A.append(f'Retrabalho: {_n(len(rt))} imóveis/mês tiveram boleto automático cancelado (Flag C) e substituído por manual; {_n(corr)} deles com correção de valor e/ou vencimento. '
                 f'A administradora com mais casos é {top.index[0]} ({_n(top.iloc[0])}).')
    a = df.groupby('adm').manual.agg(['size', 'sum', 'mean'])
    a = a[a['size'] >= 100].sort_values('mean', ascending=False)
    if len(a) >= 3:
        A.append(f'Administradoras fora da curva (≥100 lançamentos): ' + ', '.join(f'{a.index[i]} ({_pct(a.iloc[i]["mean"], 0)} manual)' for i in range(3))
                 + '. Valor, desconto e variação de valor não explicam o manual.')
    return A


def hipoteses(df):
    base = df.manual.mean()
    H = []

    def p(mask):
        s = df.loc[mask, 'manual']
        return s.mean() if len(s) else 0.0

    H.append(('Lançamento em cima do vencimento sobe manual', 'Confirmada',
              f'Lançados com até 2 dias do vencimento (ou depois): {_pct(p(df.antecedencia <= 2))} manuais; com 11+ dias de antecedência: {_pct(p(df.antecedencia >= 11))}.'))
    pos = df[df.f_fora_janela]
    if len(pos):
        H.append(('Depois do último lote automático do mês só sobe manual', 'Confirmada',
                  f'{_n(pos.manual.sum())} de {_n(len(pos))} lançamentos feitos após o último lote automático são manuais ({_pct(pos.manual.mean())}); dentro da janela o índice é {_pct(df.loc[~df.f_fora_janela, "manual"].mean())}.'))
    H.append(('Dia da ficha diferente do dia do vencimento', 'Confirmada',
              f'Quando o dia não confere: {_pct(p(df.f_ficha_dif))} manuais (n={_n(df.f_ficha_dif.sum())}); quando confere: {_pct(p(df.ficha_cls == "Confere"))}.'))
    H.append(('Vencimento deslocado para dia útil (fim de semana/feriado)', 'Confirmada',
              f'{_pct(p(df.f_ficha_util))} manuais (n={_n(df.f_ficha_util.sum())}) contra {_pct(base)} da base.'))
    H.append(('Vencimento alternativo por verba (Vencto. Alter.)', 'Confirmada',
              f'{_pct(p(df.f_alter))} manuais (n={_n(df.f_alter.sum())}) contra {_pct(p(~df.f_alter))} sem vencimento alternativo.'))
    H.append(('Pagamento por Internet / Ctas. Pagar', 'Confirmada',
              f'{_n(df.f_pg.sum())} lançamentos, {_pct(p(df.f_pg), 0)} manuais.'))
    H.append(('Cancelado e relançado (Flag C no mesmo imóvel/mês)', 'Confirmada',
              f'{_n(df.f_relancado.sum())} lançamentos válidos vieram depois de um boleto cancelado no mesmo imóvel/mês; {_pct(p(df.f_relancado))} são manuais contra {_pct(p(~df.f_relancado))} nos demais.'))
    H.append(('Mais de um boleto do mesmo imóvel no mês', 'Confirmada (parcial)',
              f'1 boleto: {_pct(p(df.k == 1))}; 2 boletos: {_pct(p(df.k == 2))}; 3 ou mais: {_pct(p(df.k >= 3))}. Parte do efeito vem de cancelamentos.'))
    H.append(('Observação da ficha cita cheque, sala/loja, vaga/garagem, gás ou atenção', 'Confirmada (parcial)',
              f'{_pct(p(df.f_obs))} manuais (n={_n(df.f_obs.sum())}); "cheque" {_pct(p(df["obs::Cheque"]))}, "sala/loja/casa" {_pct(p(df["obs::Sala / loja / casa"]))}.'))
    if (df.prev_manual == True).any() and (df.prev_manual == False).any():
        H.append(('Imóvel que subiu manual no mês anterior repete', 'Confirmada',
                  f'Manual no mês anterior: {_pct(df.loc[df.prev_manual == True, "manual"].mean())} manuais; automático no mês anterior: {_pct(df.loc[df.prev_manual == False, "manual"].mean())}.'))
    a = df.groupby('adm').manual.agg(['size', 'mean'])
    a = a[a['size'] >= 100].sort_values('mean', ascending=False)
    if len(a):
        H.append(('Administradoras específicas concentram manual', 'Confirmada' if a.iloc[0]['mean'] > base * 1.5 else 'Descartada',
                  f'{a.index[0]} {_pct(a.iloc[0]["mean"], 0)} (n={_n(a.iloc[0]["size"])}); a média geral é {_pct(base)}.'))
    # descartadas (calculadas, não escritas à mão)
    g = df.groupby(['imovel', 'mes']).agg(valor=('valor', 'sum'), k=('valor', 'size'), manual=('manual', 'any')).reset_index()
    g['ord'] = g['mes'].str[:4].astype(int) * 12 + g['mes'].str[5:7].astype(int)
    g = g.sort_values(['imovel', 'ord'])
    g['pv'] = g.groupby('imovel')['valor'].shift()
    g['po'] = g.groupby('imovel')['ord'].shift()
    g = g[(g.ord - g.po == 1) & (g.k == 1) & (g.pv > 0)]
    if len(g) >= 50:
        var = (g.valor / g.pv - 1).abs()
        H.append(('Variação do valor do boleto contra o mês anterior', 'Descartada',
                  f'Valor igual ao mês anterior: {_pct(g.loc[var <= 0.0001, "manual"].mean())} manuais; variação acima de 25%: {_pct(g.loc[var > 0.25, "manual"].mean())}.'))
    H.append(('Boleto com desconto', 'Descartada', f'Com desconto: {_pct(p(df["desc"] > 0))}; sem desconto: {_pct(p(df["desc"] == 0))}.'))
    try:
        q = pd.qcut(df.valor, 8, duplicates='drop')
        qq = df.groupby(q).manual.mean()
        H.append(('Faixa de valor do boleto', 'Descartada (efeito fraco)',
                  f'Das {len(qq)} faixas de valor, {len(qq) - 1} ficam entre {_pct(qq.iloc[1:].min(), 0)} e {_pct(qq.iloc[1:].max(), 0)}; a menor faixa (até R$ {int(round(df.valor.quantile(1 / 8)))}) fica em {_pct(qq.iloc[0], 0)}.'))
    except Exception:
        pass
    dw = df.groupby('dow_venc').manual.mean()
    H.append(('Dia da semana do vencimento', 'Descartada',
              f'Varia de {_pct(dw.min(), 0)} a {_pct(dw.max(), 0)} sem padrão consistente; o que pesa é a data específica.'))
    f = df.groupby('filial').manual.agg(['size', 'mean']).sort_values('size', ascending=False)
    f = f[f.index != '']
    if len(f) >= 4:
        grandes = f.head(5)
        fora = f[(f['size'] >= 300) & (f['mean'] > base * 1.5)]
        txt = f'As {len(grandes)} maiores filiais ficam entre {_pct(grandes["mean"].min(), 0)} e {_pct(grandes["mean"].max(), 0)}'
        if len(fora):
            txt += '; destoa(m): ' + ', '.join(f'filial {i} ({_pct(r["mean"], 0)})' for i, r in fora.iterrows()) + '.'
            H.append(('Filial', 'Descartada (exceto ' + ', '.join(f'filial {i}' for i in fora.index) + ')', txt))
        else:
            H.append(('Filial', 'Descartada', txt + '.'))
    return H


# ------------------------------------------------------------------ montagem do JSON
def construir_dados(arquivos):
    """arquivos: lista de (nome, bytes). Devolve o dicionário que a página desenha."""
    if not arquivos:
        raise ErroRelatorio('Nenhum arquivo enviado.')
    partes, meta_arq, vistos, repetidos = [], [], set(), []
    for nome, conteudo in arquivos:
        h = hashlib.sha1(conteudo).hexdigest()
        if h in vistos:
            repetidos.append(nome)
            continue
        vistos.add(h)
        d = ler_relatorio(conteudo, nome)
        partes.append(d)
    bruto = pd.concat(partes, ignore_index=True)
    df, ruins = preparar(bruto)
    if df.empty:
        raise ErroRelatorio('Os arquivos não têm nenhum lançamento válido (Tp. Lancto. Auto/Manu com datas dd/mm/aaaa).')
    df = construir_fatores(df)
    df = classificar(df)
    df = df.sort_values(['venc', 'imovel', 'lanc']).reset_index(drop=True)

    meses = sorted(df.mes.unique())
    adms = sorted(df.adm.unique())
    conds = sorted(df.emp.unique())
    pgs = ['Boleto'] + sorted(x for x in df.tp_pg.unique() if x.lower() != 'boleto')
    pgs = [x for x in pgs if x in set(df.tp_pg.unique()) or x == 'Boleto']
    flags = sorted(df.flag.unique())
    vd = sorted(df.venc.dt.strftime('%Y-%m-%d').unique())
    ld = sorted(df.lanc.dt.strftime('%Y-%m-%d').unique())
    ia = {a: i for i, a in enumerate(adms)}
    ic = {c: i for i, c in enumerate(conds)}
    ih = {h: i for i, h in enumerate(HIST)}
    ix = {f: i for i, f in enumerate(FAIXAS)}
    iv = {d: i for i, d in enumerate(vd)}
    il = {d: i for i, d in enumerate(ld)}
    imes = {m: i for i, m in enumerate(meses)}
    mot_idx = {m[0]: i for i, m in enumerate(MOTIVOS)}
    colunas_fator = [m[2] for m in MOTIVOS[:-1]]
    mask = np.zeros(len(df), dtype=np.int64)
    for b, c in enumerate(colunas_fator):
        mask |= (df[c].astype(int).values.astype(np.int64) << b)
    rows = []
    for i, r in enumerate(df.itertuples()):
        rows.append([r.imovel, imes[r.mes], iv[r.venc.strftime('%Y-%m-%d')], il[r.lanc.strftime('%Y-%m-%d')], int(r.manual), ia[r.adm], ic[r.emp],
                     mot_idx[r.motivo_cod] if r.manual else -1, ih[r.hist_imovel], ix[r.faixa_prazo], int(r.antecedencia), round(float(r.valor), 2), int(mask[i]),
                     pgs.index(r.tp_pg) if r.tp_pg in pgs else 0, flags.index(r.flag), 0 if pd.isna(r.dia_ficha) else int(r.dia_ficha),
                     (r.obs[:160] if r.manual else '')])
    aviso = []
    if repetidos:
        aviso.append('Arquivo(s) idêntico(s) a outro já enviado, ignorado(s): ' + ', '.join(repetidos) + '.')
    if ruins:
        aviso.append(f'{ruins} linha(s) ignorada(s) por data inválida.')
    cobertura = df.groupby('arquivo').agg(linhas=('imovel', 'size')).reset_index()
    for _, r in cobertura.iterrows():
        meta_arq.append({'nome': r['arquivo'], 'linhas': int(r['linhas'])})
    # meses que aparecem em mais de um arquivo podem estar duplicados
    dupl = df.groupby('mes').arquivo.nunique()
    for mes, k in dupl.items():
        if k > 1:
            aviso.append(f'O mês {_rotulo_mes(mes)} aparece em {k} arquivos: confira se não há linhas repetidas.')
    return {
        'versao': VERSAO,
        'meta': {'gerado_em': datetime.now().strftime('%d/%m/%Y %H:%M'), 'arquivos': meta_arq, 'linhas': int(len(df)),
                 'periodo': periodo_texto(meses), 'avisos': aviso},
        'meses': [_rotulo_mes(m) for m in meses], 'mkeys': meses,
        'adms': adms, 'conds': conds, 'hist': HIST, 'faixas': FAIXAS, 'tppg': pgs, 'flags': flags, 'vd': vd, 'ld': ld,
        'motivos': [{'cod': c, 'rot': r, 'conf': cf, 'grupo': g, 'acao': ACAO[c]} for c, r, _, cf, g in MOTIVOS],
        'fatores': [{'cod': c, 'rot': r, 'grupo': g} for c, r, _, cf, g in MOTIVOS[:-1]],
        'ach': achados(df), 'hip': [{'h': h, 'r': r, 'e': e} for h, r, e in hipoteses(df)],
        'rows': rows,
    }


def salvar(dados, pasta):
    os.makedirs(pasta, exist_ok=True)
    destino = os.path.join(pasta, ARQUIVO_JSON)
    tmp = destino + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(dados, f, ensure_ascii=False, separators=(',', ':'))
    os.replace(tmp, destino)
    return destino


# ------------------------------------------------------------------ rotas Flask
def registrar_rotas(app, gestor_obrigatorio, pasta_dados):
    from flask import render_template, jsonify, request, Response
    caminho = os.path.join(pasta_dados, ARQUIVO_JSON)

    @app.route('/dashboard_gerencial/auto_manual')
    @gestor_obrigatorio
    def dashboard_auto_manual():
        return render_template('analise_manual.html')

    @app.route('/api/dashboard/auto_manual/dados')
    @gestor_obrigatorio
    def api_auto_manual_dados():
        if not os.path.exists(caminho):
            return jsonify({'vazio': True})
        with open(caminho, 'rb') as f:
            corpo = f.read()
        resp = Response(corpo, mimetype='application/json')
        resp.headers['Cache-Control'] = 'no-store'
        return resp

    @app.route('/api/dashboard/auto_manual/atualizar', methods=['POST'])
    @gestor_obrigatorio
    def api_auto_manual_atualizar():
        envio = [f for f in request.files.getlist('arquivos') if f and f.filename]
        if not envio:
            return jsonify({'sucesso': False, 'msg': 'Escolha ao menos um relatório (.xlsx).'}), 400
        if len(envio) > LIMITE_ARQUIVOS:
            return jsonify({'sucesso': False, 'msg': f'Envie no máximo {LIMITE_ARQUIVOS} arquivos por vez.'}), 400
        lidos = []
        for f in envio:
            nome = os.path.basename(f.filename)
            if not nome.lower().endswith(('.xlsx', '.xls')):
                return jsonify({'sucesso': False, 'msg': f'{nome}: só aceito planilhas Excel (.xlsx ou .xls).'}), 400
            conteudo = f.read(LIMITE_BYTES + 1)
            if len(conteudo) > LIMITE_BYTES:
                return jsonify({'sucesso': False, 'msg': f'{nome}: arquivo grande demais (limite de 30 MB).'}), 400
            lidos.append((nome, conteudo))
        with _TRAVA:
            try:
                dados = construir_dados(lidos)
                salvar(dados, pasta_dados)
            except ErroRelatorio as e:
                return jsonify({'sucesso': False, 'msg': str(e)}), 400
            except Exception as e:
                return jsonify({'sucesso': False, 'msg': f'Não consegui processar os relatórios ({e.__class__.__name__}: {e}).'}), 500
        return jsonify({'sucesso': True, 'linhas': dados['meta']['linhas'], 'periodo': dados['meta']['periodo'], 'avisos': dados['meta']['avisos']})