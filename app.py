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


async def rodar_scrapper_logic(termo_busca, max_resultados=15):
    empresas_sem_site = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            context = await browser.new_context(
                locale="pt-BR",
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                ),
            )
            page = await context.new_page()

            url = f"https://www.google.com/maps/search/{termo_busca.replace(' ', '+')}"
            await page.goto(url, timeout=60000)

            # Trata o banner de cookies, se aparecer
            await aceitar_cookies_se_aparecer(page)

            painel_resultados = page.locator('div[role="feed"]')
            try:
                await painel_resultados.wait_for(state="visible", timeout=15000)
            except Exception:
                # feed não apareceu (bloqueio, captcha, sem resultados etc.)
                await browser.close()
                return empresas_sem_site

            # Scroll para carregar mais resultados
            for _ in range(4):
                await painel_resultados.evaluate("node => node.scrollBy(0, 1200)")
                await asyncio.sleep(1.2)

            cards = page.locator('div[role="feed"] div[role="article"]')
            total_cards = await cards.count()

            for i in range(min(total_cards, max_resultados)):
                card = cards.nth(i)
                try:
                    await card.click()
                    await pausa_humana(1.5, 3.0)

                    # --- CAPTURA RESISTENTE DO NOME DA EMPRESA ---
                    nomes_invalidos = {"resultados", "results", ""}
                    nome = "Não identificado"

                    # 1) Classe específica do título no painel de detalhes (mais confiável)
                    try:
                        h1_detalhe = page.locator('h1.DUwDvf').first
                        await h1_detalhe.wait_for(state="visible", timeout=8000)
                        candidato = (await h1_detalhe.inner_text()).strip()
                        if candidato.lower() not in nomes_invalidos:
                            nome = candidato
                    except Exception:
                        pass

                    # 2) Fallback: h1 genérico dentro do painel principal, só se não vier "Resultados"
                    if nome == "Não identificado":
                        h1_el = page.locator('div[role="main"] h1').first
                        if await h1_el.count() > 0 and await h1_el.is_visible():
                            candidato = (await h1_el.inner_text()).strip()
                            if candidato.lower() not in nomes_invalidos:
                                nome = candidato

                    # 3) Fallback: outra classe antiga de título do Maps
                    if nome == "Não identificado":
                        alt_el = page.locator('h1.fontHeadlineLarge').first
                        if await alt_el.count() > 0:
                            candidato = (await alt_el.inner_text()).strip()
                            if candidato.lower() not in nomes_invalidos:
                                nome = candidato

                    # 4) Último recurso: aria-label do próprio card na lista
                    if nome == "Não identificado":
                        label_card = await card.get_attribute("aria-label")
                        if label_card and label_card.strip().lower() not in nomes_invalidos:
                            nome = label_card.strip()

                    # Checa se possui botão de site
                    site_el = page.locator('a[data-item-id="authority"]')
                    tem_site = await site_el.count() > 0

                    if not tem_site:
                        # Telefone
                        tel_el = page.locator('button[data-item-id^="phone:tel:"]')
                        telefone = (
                            await tel_el.get_attribute("aria-label")
                            if await tel_el.count() > 0
                            else "Não informado"
                        )
                        telefone = (
                            telefone.replace("Telefone: ", "")
                            if telefone
                            else "Não informado"
                        )

                        # Endereço
                        end_el = page.locator('button[data-item-id="address"]')
                        endereco = (
                            await end_el.get_attribute("aria-label")
                            if await end_el.count() > 0
                            else "Não informado"
                        )
                        endereco = (
                            endereco.replace("Endereço: ", "")
                            if endereco
                            else "Não informado"
                        )

                        empresas_sem_site.append(
                            {
                                "Nome": nome,
                                "Telefone": telefone,
                                "Endereço": endereco,
                            }
                        )
                except Exception:
                    continue
        finally:
            # garante que o browser fecha mesmo se algo der errado no meio
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
        resultados = asyncio.run(rodar_scrapper_logic(termo))
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
    print("Servidor rodando em http://127.0.0.1:5000")
    app.run(debug=True, port=5000)