import pandas as pd

def buscar_id_externo(df_base, usuario, condominio, unidade):
    try:
        filtro = df_base[
            (df_base['usuario'] == usuario) & 
            (df_base['condominio_referencia'] == condominio) & 
            (df_base['unidade_numero'] == unidade)
        ]
        if not filtro.empty:
            val = filtro.iloc[0]['id_externo']
            return str(val) if pd.notna(val) else "SEM_ID"
        return "ID_NAO_ENCONTRADO"
    except:
        return "ERRO_LOOKUP"