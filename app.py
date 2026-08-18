import asyncio
import re
from flask import Flask, render_template, request, jsonify, send_file
from playwright.async_api import async_playwright
import pandas as pd
import io

app = Flask(__name__)

async def rodar_scrapper_logic(termo_busca, max_resultados=8):
    empresas_sem_site = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                '--no-sandbox',
                '--disable-setuid-sandbox',
                '--disable-dev-shm-usage',
                '--disable-gpu',
                '--no-first-run',
                '--no-zygote',
                '--single-process'
            ]
        )
        
        try:
            context = await browser.new_context(
                locale="pt-BR",
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
            
            page = await context.new_page()
            
            # Bloqueia imagens e fontes para economizar RAM e CPU
            await page.route("**/*.{png,jpg,jpeg,svg,gif,woff,woff2}", lambda route: route.abort())

            url = f"https://www.google.com/maps/search/{termo_busca.replace(' ', '+')}"
            await page.goto(url, wait_until="domcontentloaded", timeout=20000)

            painel = page.locator('div[role="feed"]')
            try:
                await painel.wait_for(state="visible", timeout=12000)
            except Exception:
                return empresas_sem_site

            # Rola a lista para carregar mais itens
            for _ in range(2):
                await painel.evaluate("node => node.scrollBy(0, 1500)")
                await asyncio.sleep(1)

            cards = page.locator('div[role="feed"] > div > div[role="article"]')
            total_cards = await cards.count()

            for i in range(min(total_cards, max_resultados)):
                card = cards.nth(i)
                try:
                    # Captura todo o texto visível dentro do card do resultado
                    texto_card = await card.inner_text()
                    linhas = [linha.strip() for linha in texto_card.split('\n') if linha.strip()]

                    if not linhas:
                        continue

                    # Verifica se o card tem o botão de website explicitamente
                    tem_site_link = await card.locator('a[href*="http"]').count() > 0
                    contem_palavra_site = "website" in texto_card.lower() or "site" in texto_card.lower()

                    if not tem_site_link and not contem_palavra_site:
                        nome = linhas[0]  # O nome é sempre a primeira linha do card
                        
                        # Expressão regular para identificar telefone brasileiro
                        telefone = "Não informado"
                        match_tel = re.search(r'\(?\d{2}\)?\s?\d{4,5}[-\s]?\d{4}', texto_card)
                        if match_tel:
                            telefone = match_tel.group(0)

                        # Tenta obter o endereço a partir do texto das linhas restantes
                        endereco = "Não informado"
                        for linha in linhas[1:]:
                            if ("Rua" in linha or "Av." in linha or "Avenida" in linha or "Alameda" in linha or "Praça" in linha or " - " in linha) and not ("Fechado" in linha or "Aberto" in linha or "★" in linha):
                                endereco = linha
                                break

                        empresas_sem_site.append({
                            "Nome": nome,
                            "Telefone": telefone,
                            "Endereço": endereco
                        })
                except Exception:
                    continue

        finally:
            await browser.close()

    return empresas_sem_site

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/buscar', methods=['POST'])
def buscar():
    data = request.get_json() or {}
    profissao = data.get('profissao', '')
    cidade = data.get('cidade', '')

    termo = f"{profissao} em {cidade}"
    
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        resultados = loop.run_until_complete(rodar_scrapper_logic(termo, max_resultados=8))
        loop.close()
        return jsonify(resultados)
    except Exception as e:
        return jsonify({"erro": f"Erro na busca: {str(e)}"}), 500

@app.route('/api/download', methods=['POST'])
def download():
    data = request.get_json() or []
    df = pd.DataFrame(data)
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Leads')
    output.seek(0)
    return send_file(
        output,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name='leads_sem_site.xlsx'
    )

if __name__ == '__main__':
    app.run(debug=True)
    # gunicorn -w 1 --timeout 120 app:app