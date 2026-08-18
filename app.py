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
                '--disable-blink-features=AutomationControlled',
                '--disable-gpu',
                '--no-first-run'
            ]
        )
        
        try:
            context = await browser.new_context(
                locale="pt-BR",
                timezone_id="America/Sao_Paulo",
                viewport={"width": 1280, "height": 800},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
            )
            
            page = await context.new_page()

            # Esconde flag de automação do robô
            await page.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => undefined
                });
            """)

            url = f"https://www.google.com/maps/search/{termo_busca.replace(' ', '+')}"
            await page.goto(url, wait_until="networkidle", timeout=25000)

            # Aceitar cookies se houver
            try:
                btn_aceitar = page.locator('button:has-text("Aceitar tudo"), button:has-text("Concordo")')
                if await btn_aceitar.count() > 0:
                    await btn_aceitar.first.click(timeout=2000)
            except Exception:
                pass

            painel = page.locator('div[role="feed"]')
            try:
                await painel.wait_for(state="visible", timeout=12000)
            except Exception:
                pass

            # Rola para carregar os cards
            try:
                await page.mouse.wheel(0, 1000)
                await asyncio.sleep(1)
            except Exception:
                pass

            cards = page.locator('div[role="feed"] > div > div[role="article"]')
            total_cards = await cards.count()

            if total_cards == 0:
                cards = page.locator('div[role="article"]')
                total_cards = await cards.count()

            for i in range(min(total_cards, max_resultados)):
                card = cards.nth(i)
                try:
                    texto_card = await card.inner_text()
                    if not texto_card or len(texto_card.strip()) < 3:
                        continue

                    # 1. Verifica no card do feed se tem site
                    has_website = False
                    links = card.locator('a')
                    num_links = await links.count()
                    for l_idx in range(num_links):
                        href = await links.nth(l_idx).get_attribute('href') or ''
                        aria_label = await links.nth(l_idx).get_attribute('aria-label') or ''
                        if 'http' in href and not 'google.com/maps' in href:
                            has_website = True
                            break
                        if 'website' in aria_label.lower() or 'site' in aria_label.lower():
                            has_website = True
                            break

                    if 'website' in texto_card.lower() or 'site' in texto_card.lower():
                        has_website = True

                    # Se NÃO tem site, clica para extrair os detalhes exatos
                    if not has_website:
                        await card.click(timeout=3000)
                        await asyncio.sleep(1.2) # Aguarda painel lateral carregar

                        # Extrai Nome do cabeçalho H1 do painel lateral
                        nome = "Não identificado"
                        h1 = page.locator('h1.DUwDvf, h1').first
                        if await h1.count() > 0:
                            nome_text = (await h1.inner_text()).strip()
                            if nome_text:
                                nome = nome_text

                        # Extrai Endereço do atributo aria-label do botão de endereço
                        endereco = "Não informado"
                        btn_end = page.locator('button[data-item-id="address"]')
                        if await btn_end.count() > 0:
                            aria_end = await btn_end.get_attribute("aria-label") or ""
                            if aria_end:
                                endereco = aria_end.replace("Endereço: ", "").replace("Endereço ; ", "").strip()

                        # Extrai Telefone do botão de chamada
                        telefone = "Não informado"
                        btn_tel = page.locator('button[data-item-id^="phone:tel:"]')
                        if await btn_tel.count() > 0:
                            aria_tel = await btn_tel.get_attribute("aria-label") or ""
                            if aria_tel:
                                telefone = aria_tel.replace("Telefone: ", "").strip()
                        else:
                            # Tenta via regex no painel lateral
                            painel_detalhes = page.locator('div[role="main"]')
                            if await painel_detalhes.count() > 0:
                                txt_detalhes = await painel_detalhes.inner_text()
                                match_tel = re.search(r'\(?\d{2}\)?\s?\d{4,5}[-\s]?\d{4}', txt_detalhes)
                                if match_tel:
                                    telefone = match_tel.group(0)

                        if not any(e['Nome'] == nome for e in empresas_sem_site):
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