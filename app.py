import asyncio
import re
from flask import Flask, render_template, request, jsonify, send_file
from playwright.async_api import async_playwright
import pandas as pd
import io

app = Flask(__name__)

async def rodar_scrapper_logic(termo_busca, max_resultados=10):
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

            # Esconde a flag de automação
            await page.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => undefined
                });
            """)

            url = f"https://www.google.com/maps/search/{termo_busca.replace(' ', '+')}"
            await page.goto(url, wait_until="networkidle", timeout=25000)

            # Fecha banner de consentimento de cookies, se houver
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

            # Scroll suave para carregar os elementos e telefones nos cards
            try:
                for _ in range(3):
                    await page.mouse.wheel(0, 1500)
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
                    if not texto_card or len(texto_card.strip()) < 5:
                        continue

                    linhas = [l.strip() for l in texto_card.split('\n') if l.strip()]

                    # Checa presença de site no card
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

                    if not has_website:
                        nome = linhas[0] if len(linhas) > 0 else "Não identificado"
                        
                        # 1. Tenta extrair o telefone via expressão regular no texto
                        telefone = "Não informado"
                        match_tel = re.search(r'\(?\d{2}\)?\s?\d{4,5}[-\s]?\d{4}', texto_card)
                        if match_tel:
                            telefone = match_tel.group(0)
                        else:
                            # 2. Busca no atributo HTML oculto dos botões de chamada/telefone do card
                            btn_tel = card.locator('button[aria-label*="Telefone"], [data-tooltip*="Telefone"], button[aria-label*="Ligar"]')
                            if await btn_tel.count() > 0:
                                label_tel = await btn_tel.first.get_attribute("aria-label") or ""
                                match_btn = re.search(r'\(?\d{2}\)?\s?\d{4,5}[-\s]?\d{4}', label_tel)
                                if match_btn:
                                    telefone = match_btn.group(0)

                        # Extração do Endereço
                        endereco = "Não informado"
                        for linha in linhas[1:]:
                            # Identifica padrões comuns de endereço no Brasil
                            if any(term in linha.lower() for term in ["rua", "av.", "avenida", "alameda", "praça", "rodovia", "estrada", "bairro", " - "]) and not any(ign in linha.lower() for ign in ["fechado", "aberto", "★", "avaliações", "estrelas"]):
                                endereco = linha
                                break

                        # Se o endereço continuar não informado, tenta pegar da segunda/terceira linha útil
                        if endereco == "Não informado" and len(linhas) > 2:
                            for linha in linhas[1:]:
                                if not any(ign in linha.lower() for ign in ["fechado", "aberto", "★", "avaliações", "estrelas", "minuto", "hora"]) and len(linha) > 8:
                                    endereco = linha
                                    break

                        # Evita cadastrar a mesma empresa duas vezes
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
        resultados = loop.run_until_complete(rodar_scrapper_logic(termo, max_resultados=10))
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