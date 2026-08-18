from flask import Flask, render_template, request, jsonify, send_file
import asyncio
import random
import pandas as pd
import os
import io
from playwright.async_api import async_playwright

app = Flask(__name__)


async def pausa_humana(min_seg=1.0, max_seg=2.5):
    tempo = random.uniform(min_seg, max_seg)
    await asyncio.sleep(tempo)


async def aceitar_cookies_se_aparecer(page):
    """Google costuma mostrar um banner de consentimento antes do mapa carregar."""
    seletores_possiveis = [
        'button:has-text("Aceitar tudo")',
        'button:has-text("Accept all")',
        'button[aria-label="Aceitar tudo"]',
        'form[action*="consent"] button',
    ]
    for seletor in seletores_possiveis:
        try:
            botao = page.locator(seletor).first
            if await botao.count() > 0 and await botao.is_visible():
                await botao.click()
                await pausa_humana(0.5, 1.0)
                return
        except Exception:
            continue


async def rodar_scrapper_logic(termo_busca, max_resultados=10):
    empresas_sem_site = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                '--no-sandbox',
                '--disable-setuid-sandbox',
                '--disable-dev-shm-usage',
                '--disable-gpu',
                '--single-process'
            ]
        )
        try:
            context = await browser.new_context(
                locale="pt-BR",
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
            page = await context.new_page()

            url = f"https://www.google.com/maps/search/{termo_busca.replace(' ', '+')}"
            await page.goto(url, timeout=20000)

            await aceitar_cookies_se_aparecer(page)

            painel_resultados = page.locator('div[role="feed"]')
            try:
                await painel_resultados.wait_for(state="visible", timeout=10000)
            except Exception:
                return empresas_sem_site

            # Scroll mais rápido
            for _ in range(2):
                await painel_resultados.evaluate("node => node.scrollBy(0, 1000)")
                await asyncio.sleep(0.8)

            cards = page.locator('div[role="feed"] div[role="article"]')
            total_cards = await cards.count()

            for i in range(min(total_cards, max_resultados)):
                card = cards.nth(i)
                try:
                    await card.click()
                    await asyncio.sleep(1.0) # Pausa mais curta para economizar tempo

                    nome = "Não identificado"
                    try:
                        h1_detalhe = page.locator('h1.DUwDvf').first
                        if await h1_detalhe.count() > 0:
                            nome = (await h1_detalhe.inner_text()).strip()
                    except Exception:
                        pass

                    site_el = page.locator('a[data-item-id="authority"]')
                    tem_site = await site_el.count() > 0

                    if not tem_site:
                        tel_el = page.locator('button[data-item-id^="phone:tel:"]')
                        telefone = await tel_el.get_attribute("aria-label") if await tel_el.count() > 0 else "Não informado"
                        telefone = telefone.replace("Telefone: ", "") if telefone else "Não informado"

                        end_el = page.locator('button[data-item-id="address"]')
                        endereco = await end_el.get_attribute("aria-label") if await end_el.count() > 0 else "Não informado"
                        endereco = endereco.replace("Endereço: ", "") if endereco else "Não informado"

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
    data = request.get_json(silent=True) or {}
    profissao = data.get('profissao')
    cidade = data.get('cidade')

    if not profissao or not cidade:
        return jsonify({"erro": "Preencha todos os campos."}), 400

    termo = f"{profissao} em {cidade}"

    try:
        # Cria e executa o loop assíncrono isolado para cada requisição
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        resultados = loop.run_until_complete(rodar_scrapper_logic(termo))
        loop.close()
        return jsonify(resultados)
    except Exception as e:
        return jsonify({"erro": str(e)}), 500


@app.route('/api/download', methods=['POST'])
def download():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"erro": "Nenhum dado para baixar."}), 400

    df = pd.DataFrame(data)
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Leads')
    output.seek(0)

    return send_file(
        output,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name='leads_sem_site.xlsx',
    )


if __name__ == '__main__':
    os.makedirs('templates', exist_ok=True)
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)