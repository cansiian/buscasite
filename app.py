import asyncio
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
                '--single-process',
                '--blink-settings=imagesEnabled=false' # Desativa imagens para velocidade máxima
            ]
        )
        try:
            context = await browser.new_context(
                locale="pt-BR",
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            )
            
            # Bloqueia fontes e mídias pesadas para não gastar tempo/processador
            page = await context.new_page()
            await page.route("**/*.{png,jpg,jpeg,svg,gif,woff,woff2}", lambda route: route.abort())

            url = f"https://www.google.com/maps/search/{termo_busca.replace(' ', '+')}"
            await page.goto(url, wait_until="domcontentloaded", timeout=15000)

            # Tenta fechar banner de cookies rapidamente se existir
            try:
                btn_cookie = page.locator('button[aria-label*="Aceitar"]').first
                if await btn_cookie.count() > 0:
                    await btn_cookie.click(timeout=2000)
            except Exception:
                pass

            painel = page.locator('div[role="feed"]')
            try:
                await painel.wait_for(state="visible", timeout=8000)
            except Exception:
                return empresas_sem_site

            # Rola a lista apenas o necessário
            await painel.evaluate("node => node.scrollBy(0, 1500)")
            await asyncio.sleep(0.5)

            cards = page.locator('div[role="feed"] > div > div[role="article"]')
            total_cards = await cards.count()

            for i in range(min(total_cards, max_resultados)):
                card = cards.nth(i)
                try:
                    await card.click()
                    await asyncio.sleep(0.6) # Aguarda painel lateral carregar

                    # Nome da empresa
                    nome = "Não identificado"
                    h1 = page.locator('h1.DUwDvf').first
                    if await h1.count() > 0:
                        nome = (await h1.inner_text()).strip()

                    # Verifica se possui botão/link de Website
                    site_btn = page.locator('a[data-item-id="authority"]')
                    tem_site = await site_btn.count() > 0

                    if not tem_site:
                        # Pega telefone
                        tel_el = page.locator('button[data-item-id^="phone:tel:"]')
                        telefone = "Não informado"
                        if await tel_el.count() > 0:
                            lbl = await tel_el.get_attribute("aria-label")
                            telefone = lbl.replace("Telefone: ", "").strip() if lbl else "Não informado"

                        # Pega endereço
                        end_el = page.locator('button[data-item-id="address"]')
                        endereco = "Não informado"
                        if await end_el.count() > 0:
                            lbl = await end_el.get_attribute("aria-label")
                            endereco = lbl.replace("Endereço: ", "").strip() if lbl else "Não informado"

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
    data = request.get_json()
    profissao = data.get('profissao', '')
    cidade = data.get('cidade', '')

    termo = f"{profissao} em {cidade}"
    
    try:
        # Executa a busca assíncrona
        resultados = asyncio.run(rodar_scrapper_logic(termo, max_resultados=8))
        return jsonify(resultados)
    except Exception as e:
        return jsonify({"erro": f"Falha na raspagem: {str(e)}"}), 500

@app.route('/api/download', methods=['POST'])
def download():
    data = request.get_json()
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