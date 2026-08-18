import asyncio
from flask import Flask, render_template, request, jsonify, send_file
from playwright.async_api import async_playwright
import pandas as pd
import io

app = Flask(__name__)

async def rodar_scrapper_logic(termo_busca, max_resultados=6):
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
            
            # Bloqueia apenas imagens e fontes para garantir velocidade sem quebrar o CSS
            await page.route("**/*.{png,jpg,jpeg,svg,gif,woff,woff2}", lambda route: route.abort())

            url = f"https://www.google.com/maps/search/{termo_busca.replace(' ', '+')}"
            await page.goto(url, wait_until="domcontentloaded", timeout=20000)

            painel = page.locator('div[role="feed"]')
            try:
                await painel.wait_for(state="visible", timeout=10000)
            except Exception:
                return empresas_sem_site

            # Scroll no feed para carregar itens
            await painel.evaluate("node => node.scrollBy(0, 800)")
            await asyncio.sleep(1)

            cards = page.locator('div[role="feed"] > div > div[role="article"]')
            total_cards = await cards.count()

            for i in range(min(total_cards, max_resultados)):
                card = cards.nth(i)
                try:
                    await card.click(timeout=5000)
                    await asyncio.sleep(1.2) # Aguarda painel lateral carregar dados

                    # 1. Nome da empresa
                    nome = "Não identificado"
                    h1 = page.locator('h1.DUwDvf').first
                    if await h1.count() > 0:
                        nome = (await h1.inner_text()).strip()

                    # 2. Verifica se possui site
                    site_btn = page.locator('a[data-item-id="authority"]')
                    tem_site = await site_btn.count() > 0

                    if not tem_site:
                        # 3. Telefone
                        telefone = "Não informado"
                        tel_el = page.locator('button[data-item-id^="phone:tel:"]')
                        if await tel_el.count() > 0:
                            lbl = await tel_el.get_attribute("aria-label")
                            if lbl:
                                telefone = lbl.replace("Telefone: ", "").strip()

                        # 4. Endereço
                        endereco = "Não informado"
                        end_el = page.locator('button[data-item-id="address"]')
                        if await end_el.count() > 0:
                            lbl = await end_el.get_attribute("aria-label")
                            if lbl:
                                endereco = lbl.replace("Endereço: ", "").strip()

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

# Rota mantida como def síncrona para compatibilidade com o Flask
@app.route('/api/buscar', methods=['POST'])
def buscar():
    data = request.get_json() or {}
    profissao = data.get('profissao', '')
    cidade = data.get('cidade', '')

    termo = f"{profissao} em {cidade}"
    
    try:
        # Gerencia a execução do Playwright com evento limpo
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        resultados = loop.run_until_complete(rodar_scrapper_logic(termo, max_resultados=6))
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