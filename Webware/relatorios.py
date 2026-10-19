import pandas as pd
import os

def salvar_extracao_diaria(dados, caminho):
    df_novas = pd.DataFrame(dados)
    df_novas = df_novas[df_novas['Status_Coleta'].notna()]
    if not df_novas.empty:
        if os.path.exists(caminho):
            df_old = pd.read_excel(caminho, sheet_name='Dados_Extraidos')
            df_final = pd.concat([df_old, df_novas], ignore_index=True)
        else:
            df_final = df_novas
        with pd.ExcelWriter(caminho, engine='openpyxl') as writer:
            df_final.to_excel(writer, sheet_name='Dados_Extraidos', index=False)