import os
import shutil
import sqlite3
from pathlib import Path

# 1. Caminhos das Pastas
PASTA_ORIGEM = r"Z:\financeiro_imoveis\Condomínio\JUL26 - NOVA GESTÃO"

# Pasta que será criada automaticamente dentro da sua pasta SistemaImoveis
PASTA_DESTINO = r"BOLETOS_COLETADOS_SEPARADOS"

def extrair_coletados():
    print("🕵️‍♂️ Iniciando a Força-Tarefa de Extração de PDFs...")

    # 2. Conecta no banco e pega APENAS os imóveis COLETADOS
    # Usa caminho absoluto para o banco de dados
    base_dir = Path(__file__).resolve().parent.parent.parent
    caminho_banco = base_dir / 'banco.db'
    conexao = sqlite3.connect(caminho_banco)
    conexao.row_factory = sqlite3.Row
    cursor = conexao.cursor()

    cursor.execute("SELECT imovel FROM imoveis WHERE status_interno = 'COLETADO' AND ativo = 1")
    
    # Cria uma lista ultra-rápida (set) com os imóveis, removendo os zeros à esquerda para cruzar perfeitamente
    imoveis_coletados = set()
    for row in cursor.fetchall():
        imv = str(row['imovel']).strip()
        imv_limpo = imv.lstrip('0') if imv.lstrip('0') else imv
        imoveis_coletados.add(imv_limpo)

    conexao.close()
    
    qtd_banco = len(imoveis_coletados)
    print(f"📊 Total de imóveis COLETADOS ativos no banco: {qtd_banco}")

    if qtd_banco == 0:
        print("⚠️ Nenhum imóvel coletado encontrado. Encerrando.")
        return

    # 3. Prepara a pasta de destino (Cria se não existir)
    caminho_destino = Path(PASTA_DESTINO)
    caminho_destino.mkdir(parents=True, exist_ok=True)
    print(f"📁 Pasta de destino criada em: {caminho_destino.absolute()}")

    # 4. Varre a pasta do Z: atrás dos PDFs
    caminho_origem = Path(PASTA_ORIGEM)
    if not caminho_origem.exists():
        print(f" ERRO FATAL: A pasta de origem não foi encontrada:\n{PASTA_ORIGEM}")
        print("Verifique se você está conectado na VPN ou se o mapeamento do disco Z: está ativo!")
        return

    print("🔍 Vasculhando a rede (Disco Z:) atrás de PDFs... (Isso pode levar alguns segundos)")
    
    # O rglob('.pdf') varre a pasta principal e TODAS as subpastas automaticamente!
    pdfs_encontrados = list(caminho_origem.rglob('*.pdf'))
    print(f"📄 Encontrados {len(pdfs_encontrados)} arquivos PDF na rede. Cruzando dados...")

    qtd_copiados = 0
    
    for pdf in pdfs_encontrados:
        # Pega o nome do arquivo sem o ".pdf" (Ex: "00147993.pdf" vira "00147993")
        nome_arquivo = pdf.stem 
        
        # Limpa os zeros à esquerda
        imovel_limpo = nome_arquivo.lstrip('0') if nome_arquivo.lstrip('0') else nome_arquivo

        # A MÁGICA: Se o número do PDF estiver na lista dos 464 coletados...
        if imovel_limpo in imoveis_coletados:
            destino_final = caminho_destino / pdf.name
            
            try:
                # Copia o arquivo (shutil.copy2 mantém a data original de criação do arquivo)
                shutil.copy2(pdf, destino_final)
                qtd_copiados += 1
            except Exception as e:
                print(f"⚠️ Erro ao copiar o arquivo {pdf.name}: {e}")

    print("\n" + "="*50)
    print("🚀 FORÇA-TAREFA CONCLUÍDA COM SUCESSO! 🚀")
    print(f" PDFs copiados e isolados com segurança: {qtd_copiados}")
    print("="*50 + "\n")

if __name__ == '__main__':
    extrair_coletados()