import asyncio
import aiohttp
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from datetime import datetime
import os
import json
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from io import BytesIO
import numpy as np

# ========== CONFIGURAÇÃO ==========
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
BINANCE_API = "https://api.binance.com/api/v3/klines"
ALERTAS_FILE = "alertas.json"

# ========== TIMEFRAMES ==========
TIMEFRAMES = {
    "1": {"nome": "5 minutos", "intervalo": "5m", "limite": 50},
    "2": {"nome": "10 minutos", "intervalo": "10m", "limite": 50},
    "3": {"nome": "30 minutos", "intervalo": "30m", "limite": 50},
    "4": {"nome": "1 hora", "intervalo": "1h", "limite": 50},
    "5": {"nome": "2 horas", "intervalo": "2h", "limite": 50},
    "6": {"nome": "4 horas", "intervalo": "4h", "limite": 50},
    "7": {"nome": "1 dia", "intervalo": "1d", "limite": 50},
    "8": {"nome": "2 dias", "intervalo": "2d", "limite": 50},
    "9": {"nome": "7 dias", "intervalo": "1w", "limite": 50},
    "10": {"nome": "30 dias", "intervalo": "1M", "limite": 50}
}

cache_dados = {}
cache_timestamp = {}
CACHE_TTL = 2
alertas_ativos = {}

# ========== PERSISTÊNCIA ==========
def carregar_alertas():
    global alertas_ativos
    try:
        if os.path.exists(ALERTAS_FILE):
            with open(ALERTAS_FILE, 'r') as f:
                dados = json.load(f)
                alertas_ativos = {int(k): v for k, v in dados.items()}
    except:
        alertas_ativos = {}

def salvar_alertas():
    try:
        with open(ALERTAS_FILE, 'w') as f:
            json.dump(alertas_ativos, f, indent=2)
    except:
        pass

# ========== BUSCA DE DADOS ==========
async def buscar_preco_atual():
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get("https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT") as resp:
                if resp.status == 200:
                    dados = await resp.json()
                    return float(dados["price"])
    except:
        return None
    return None

async def buscar_dados(timeframe_key, limite=100):
    intervalo = TIMEFRAMES[timeframe_key]["intervalo"]
    try:
        async with aiohttp.ClientSession() as session:
            params = {"symbol": "BTCUSDT", "interval": intervalo, "limit": limite}
            async with session.get(BINANCE_API, params=params) as resp:
                if resp.status == 200:
                    dados = await resp.json()
                    if dados:
                        tempos = [datetime.fromtimestamp(candle[0]/1000) for candle in dados]
                        aberturas = [float(candle[1]) for candle in dados]
                        altas = [float(candle[2]) for candle in dados]
                        baixas = [float(candle[3]) for candle in dados]
                        fechamentos = [float(candle[4]) for candle in dados]
                        return tempos, aberturas, altas, baixas, fechamentos
    except:
        pass
    return None, None, None, None, None

async def buscar_precos_simples(timeframe_key):
    intervalo = TIMEFRAMES[timeframe_key]["intervalo"]
    limite = TIMEFRAMES[timeframe_key]["limite"]
    
    agora = datetime.now().timestamp() * 1000
    if timeframe_key in cache_timestamp:
        if (agora - cache_timestamp[timeframe_key]) / 1000 < CACHE_TTL:
            return cache_dados.get(timeframe_key)
    
    try:
        async with aiohttp.ClientSession() as session:
            params = {"symbol": "BTCUSDT", "interval": intervalo, "limit": limite}
            async with session.get(BINANCE_API, params=params) as resp:
                if resp.status == 200:
                    dados = await resp.json()
                    if dados:
                        precos = [float(candle[4]) for candle in dados]
                        cache_dados[timeframe_key] = precos
                        cache_timestamp[timeframe_key] = agora
                        return precos
    except:
        pass
    return None

# ========== CÁLCULOS TÉCNICOS ==========
def calcular_medias(precos):
    ma9 = sum(precos[-9:]) / 9 if len(precos) >= 9 else None
    ma21 = sum(precos[-21:]) / 21 if len(precos) >= 21 else None
    ma50 = sum(precos[-50:]) / 50 if len(precos) >= 50 else None
    return ma9, ma21, ma50

def calcular_suporte_resistencia(precos):
    if not precos or len(precos) < 20:
        return None, None, None, None
    ultimos_20 = precos[-20:]
    suporte = min(ultimos_20)
    resistencia = max(ultimos_20)
    suporte2 = sorted(ultimos_20)[1] if len(ultimos_20) > 1 else suporte
    resistencia2 = sorted(ultimos_20)[-2] if len(ultimos_20) > 1 else resistencia
    return suporte, resistencia, suporte2, resistencia2

def determinar_tendencia(precos, ma9, ma21, ma50):
    if not precos or not ma9 or not ma21 or not ma50:
        return "Lateral ⏸️", "Baixo"
    preco_atual = precos[-1]
    
    if preco_atual > ma9 > ma21 > ma50:
        return "Alta 🟢", "Alto"
    elif preco_atual < ma9 < ma21 < ma50:
        return "Baixa 🔴", "Alto"
    elif preco_atual > ma9 and preco_atual > ma21:
        return "Leve Alta 📈", "Médio"
    elif preco_atual < ma9 and preco_atual < ma21:
        return "Leve Baixa 📉", "Médio"
    else:
        return "Lateral ⏸️", "Baixo"

def gerar_sugestao(tendencia, preco_atual, suporte, resistencia):
    if "Alta" in tendencia and preco_atual < resistencia * 0.998:
        return "🟢 LONG (compra)"
    elif "Baixa" in tendencia and preco_atual > suporte * 1.002:
        return "🔴 SHORT (venda)"
    elif "Lateral" in tendencia:
        if preco_atual - suporte < 100:
            return "⏸️ AGUARDAR - perto do SUPORTE"
        elif resistencia - preco_atual < 100:
            return "⏸️ AGUARDAR - perto da RESISTÊNCIA"
        return "⏸️ AGUARDAR rompimento"
    return "⏸️ AGUARDAR"

def gerar_comentario(tendencia, confianca, preco_atual, suporte, resistencia, suporte2, resistencia2):
    if not suporte or not resistencia:
        return "Aguardando dados para análise completa."
    
    distancia_suporte = ((preco_atual - suporte) / suporte) * 100
    distancia_resistencia = ((resistencia - preco_atual) / preco_atual) * 100
    
    if "Lateral" in tendencia:
        if distancia_suporte < 0.15:
            return f"🔴 Preço ENCOSTADO no suporte ({suporte:,.0f}). Se perder, próximo suporte em {suporte2:,.0f}. Se segurar, pode voltar a subir."
        elif distancia_resistencia < 0.15:
            return f"🟢 Preço ENCOSTADO na resistência ({resistencia:,.0f}). Se romper, próximo alvo {resistencia2:,.0f}."
        else:
            return f"⏸️ Mercado lateral entre {suporte:,.0f} e {resistencia:,.0f}. Espere romper um dos lados. Só opere com confirmação."
    
    elif "Alta" in tendencia:
        return f"📈 Tendência de COMPRA. Preço acima das médias. Stop sugerido abaixo de {suporte:,.0f}. Confiança: {confianca}"
    
    elif "Baixa" in tendencia:
        return f"📉 Tendência de VENDA. Preço abaixo das médias. Stop sugerido acima de {resistencia:,.0f}. Confiança: {confianca}"
    
    return "🧠 Aguarde confirmação. Mercado instável no momento."

# ========== GRÁFICO ==========
async def criar_grafico(timeframe_key):
    tempos, aberturas, altas, baixas, fechamentos = await buscar_dados(timeframe_key, 50)
    
    if not tempos:
        return None
    
    plt.style.use('dark_background')
    fig, ax = plt.subplots(figsize=(12, 7))
    
    for i in range(len(tempos)):
        cor = '#00ff00' if fechamentos[i] >= aberturas[i] else '#ff0000'
        ax.plot([tempos[i], tempos[i]], [baixas[i], altas[i]], color=cor, linewidth=0.8)
        ax.bar(tempos[i], abs(fechamentos[i] - aberturas[i]), 
               bottom=min(aberturas[i], fechamentos[i]), 
               width=0.7, color=cor, alpha=0.7)
    
    if len(fechamentos) >= 9:
        ma9 = [sum(fechamentos[max(0, i-8):i+1])/min(9, i+1) for i in range(len(fechamentos))]
        ax.plot(tempos, ma9, color='#ffaa00', linewidth=1.5, label='MA9')
    if len(fechamentos) >= 21:
        ma21 = [sum(fechamentos[max(0, i-20):i+1])/min(21, i+1) for i in range(len(fechamentos))]
        ax.plot(tempos, ma21, color='#ff6b6b', linewidth=1.5, label='MA21')
    if len(fechamentos) >= 50:
        ma50 = [sum(fechamentos[max(0, i-49):i+1])/min(50, i+1) for i in range(len(fechamentos))]
        ax.plot(tempos, ma50, color='#4ecdc4', linewidth=1.5, label='MA50')
    
    ax.set_title(f'BTC/USD - {TIMEFRAMES[timeframe_key]["nome"]}', color='white', fontsize=14, fontweight='bold')
    ax.set_ylabel('Preço (USD)', color='white')
    ax.set_xlabel('Tempo', color='white')
    ax.legend(loc='upper left')
    ax.grid(True, alpha=0.3)
    ax.tick_params(colors='white')
    
    if TIMEFRAMES[timeframe_key]["intervalo"] in ['5m', '10m', '30m']:
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M'))
    else:
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%m/%d %H:%M'))
    
    plt.xticks(rotation=45)
    plt.tight_layout()
    
    buf = BytesIO()
    plt.savefig(buf, format='png', facecolor='#1e1e1e', dpi=100)
    buf.seek(0)
    plt.close()
    
    return buf

# ========== MENU ==========
def teclado_principal():
    keyboard = []
    linha = []
    for i in range(1, 11):
        nome = TIMEFRAMES[str(i)]["nome"].replace(" minutos", "m").replace(" hora", "h").replace(" dias", "d")
        if i == 10:
            nome = "30d"
        linha.append(InlineKeyboardButton(nome, callback_data=f"analise_{i}"))
        if i % 2 == 0:
            keyboard.append(linha)
            linha = []
    if linha:
        keyboard.append(linha)
    
    keyboard.append([InlineKeyboardButton("📊 Gráfico", callback_data="grafico_menu")])
    keyboard.append([InlineKeyboardButton("🔔 Meus Alertas", callback_data="meus_alertas")])
    
    return InlineKeyboardMarkup(keyboard)

def teclado_voltar():
    return InlineKeyboardMarkup([[InlineKeyboardButton("◀ Voltar ao menu", callback_data="voltar")]])

# ========== ANÁLISE ==========
async def enviar_analise(update, context, timeframe_key, chat_id=None, msg_original=None):
    if chat_id is None:
        chat_id = update.effective_chat.id
    
    if msg_original:
        await msg_original.edit_text("⏳ Buscando dados em tempo real...")
        enviar = msg_original.edit_text
    else:
        msg = await update.effective_message.reply_text("⏳ Buscando dados...")
        enviar = msg.edit_text
    
    precos = await buscar_precos_simples(timeframe_key)
    preco_atual = await buscar_preco_atual()
    
    if not precos or not preco_atual:
        await enviar("❌ Erro ao buscar dados da Binance. Tente novamente em alguns segundos.")
        return
    
    ma9, ma21, ma50 = calcular_medias(precos)
    suporte, resistencia, suporte2, resistencia2 = calcular_suporte_resistencia(precos)
    tendencia, confianca = determinar_tendencia(precos, ma9, ma21, ma50)
    sugestao = gerar_sugestao(tendencia, preco_atual, suporte, resistencia)
    comentario = gerar_comentario(tendencia, confianca, preco_atual, suporte, resistencia, suporte2, resistencia2)
    
    ma9_str = f"{ma9:,.0f}" if ma9 else "N/A"
    ma21_str = f"{ma21:,.0f}" if ma21 else "N/A"
    ma50_str = f"{ma50:,.0f}" if ma50 else "N/A"
    
    resposta = f"""📊 **BearStrength – BTC/USDT**  
⏱️ Timeframe: **{TIMEFRAMES[timeframe_key]['nome']}**

💰 Preço atual: **${preco_atual:,.0f}**  
{tendencia}

📉 **Médias Móveis:**  
MA9: {ma9_str} | MA21: {ma21_str} | MA50: {ma50_str}

🛡️ **Suporte:** ${suporte:,.0f}  
🚀 **Resistência:** ${resistencia:,.0f}

🎯 **Sugestão:** {sugestao}  
🔒 **Confiança:** {confianca}

---

🧠 **BearStrength (analista):**  
{comentario}

---
⚠️ *Isso não é recomendação de compra/venda. Você é responsável pelo seu dinheiro.*"""
    
    grafico = await criar_grafico(timeframe_key)
    
    if msg_original:
        await msg_original.delete()
        if grafico:
            await context.bot.send_photo(chat_id=chat_id, photo=grafico, caption=resposta, parse_mode="Markdown")
        else:
            await context.bot.send_message(chat_id=chat_id, text=resposta, parse_mode="Markdown")
    else:
        if grafico:
            await msg.delete()
            await update.effective_message.reply_photo(photo=grafico, caption=resposta, parse_mode="Markdown")
        else:
            await msg.edit_text(resposta, parse_mode="Markdown")

# ========== ALERTAS ==========
async def alerta(update, context):
    chat_id = update.effective_chat.id
    args = context.args
    
    if len(args) < 3:
        await update.message.reply_text(
            "🔔 **Como criar um alerta:**\n\n"
            "`/alerta [preco] [timeframe] [acima/abaixo]`\n\n"
            "**Exemplos:**\n"
            "`/alerta 80000 1h acima`\n"
            "`/alerta 70000 4h abaixo`\n\n"
            "**Timeframes:** 5m, 10m, 30m, 1h, 2h, 4h, 1d, 2d, 7d, 30d\n\n"
            "`/meusalertas` - ver seus alertas\n"
            "`/removeralerta [numero]` - remover alerta",
            parse_mode="Markdown"
        )
        return
    
    try:
        preco_alvo = float(args[0])
        timeframe_input = args[1].lower()
        direcao = args[2].lower()
        
        if direcao not in ["acima", "abaixo"]:
            await update.message.reply_text("❌ Use 'acima' ou 'abaixo'")
            return
        
        timeframe_map = {
            "5m": "1", "10m": "2", "30m": "3", "1h": "4",
            "2h": "5", "4h": "6", "1d": "7", "2d": "8",
            "7d": "9", "30d": "10"
        }
        
        if timeframe_input not in timeframe_map:
            await update.message.reply_text("❌ Timeframe inválido. Use: 5m, 10m, 1h, 4h, 1d...")
            return
        
        timeframe_key = timeframe_map[timeframe_input]
        
        if chat_id not in alertas_ativos:
            alertas_ativos[chat_id] = []
        
        alertas_ativos[chat_id].append({
            "preco": preco_alvo,
            "timeframe": timeframe_key,
            "timeframe_nome": timeframe_input,
            "direcao": direcao,
            "criado_em": datetime.now().isoformat()
        })
        salvar_alertas()
        
        await update.message.reply_text(
            f"✅ **Alerta criado!**\n\n"
            f"🔔 BTC {direcao} **${preco_alvo:,.0f}** no timeframe **{timeframe_input}**\n\n"
            f"Use `/meusalertas` para gerenciar.",
            parse_mode="Markdown"
        )
    except ValueError:
        await update.message.reply_text("❌ Preço inválido. Use números.")

async def meus_alertas(update, context):
    chat_id = update.effective_chat.id
    
    if chat_id not in alertas_ativos or not alertas_ativos[chat_id]:
        await update.message.reply_text("🔔 Você não tem alertas ativos.\n\nUse `/alerta` para criar um.", parse_mode="Markdown")
        return
    
    texto = "🔔 **Seus alertas ativos:**\n\n"
    for i, alerta in enumerate(alertas_ativos[chat_id], 1):
        texto += f"{i}️⃣ BTC {alerta['direcao']} **${alerta['preco']:,.0f}** no timeframe {alerta['timeframe_nome']}\n"
    
    texto += "\n🗑️ Para remover: `/removeralerta [numero]`"
    await update.message.reply_text(texto, parse_mode="Markdown")

async def remover_alerta(update, context):
    chat_id = update.effective_chat.id
    args = context.args
    
    if not args:
        await update.message.reply_text("❌ Use: `/removeralerta [numero]`\nVeja os números em `/meusalertas`", parse_mode="Markdown")
        return
    
    try:
        idx = int(args[0]) - 1
        if chat_id in alertas_ativos and 0 <= idx < len(alertas_ativos[chat_id]):
            removido = alertas_ativos[chat_id].pop(idx)
            if not alertas_ativos[chat_id]:
                del alertas_ativos[chat_id]
            salvar_alertas()
            await update.message.reply_text(f"✅ Alerta removido: BTC {removido['direcao']} ${removido['preco']:,.0f}")
        else:
            await update.message.reply_text("❌ Número inválido. Use `/meusalertas` para ver seus alertas.", parse_mode="Markdown")
    except ValueError:
        await update.message.reply_text("❌ Use um número.")

async def verificar_alertas(app):
    while True:
        try:
            preco_atual = await buscar_preco_atual()
            if preco_atual:
                alertas_para_remover = []
                
                for chat_id, alertas in alertas_ativos.items():
                    for i, alerta in enumerate(alertas):
                        disparou = False
                        
                        if alerta["direcao"] == "acima" and preco_atual >= alerta["preco"]:
                            disparou = True
                        elif alerta["direcao"] == "abaixo" and preco_atual <= alerta["preco"]:
                            disparou = True
                        
                        if disparou:
                            msg = f"🔔 **ALERTA ATIVADO!**\n\n💰 BTC está **${preco_atual:,.0f}**\n📊 Timeframe: {alerta['timeframe_nome']}\n🎯 Alvo: {alerta['direcao']} de **${alerta['preco']:,.0f}**\n\nDigite `{alerta['timeframe_nome']}` para ver análise completa."
                            try:
                                await app.bot.send_message(chat_id=chat_id, text=msg, parse_mode="Markdown")
                                alertas_para_remover.append((chat_id, i))
                            except:
                                pass
                
                for chat_id, idx in sorted(alertas_para_remover, key=lambda x: x[1], reverse=True):
                    if chat_id in alertas_ativos and idx < len(alertas_ativos[chat_id]):
                        alertas_ativos[chat_id].pop(idx)
                        if not alertas_ativos[chat_id]:
                            del alertas_ativos[chat_id]
                
                if alertas_para_remover:
                    salvar_alertas()
            
            await asyncio.sleep(30)
        except:
            await asyncio.sleep(30)

# ========== HANDLERS ==========
async def start(update, context):
    await update.message.reply_text(
        "🐻 **BearStrength ativado!**\n\n"
        "Eu analiso o **BTC/USDT** em tempo real com dados da Binance.\n\n"
        "📊 **Escolha o timeframe:**",
        parse_mode="Markdown",
        reply_markup=teclado_principal()
    )

async def todos(update, context):
    await update.message.reply_text("📊 Gerando resumo de todos os timeframes...\n_Isso pode levar alguns segundos._", parse_mode="Markdown")
    
    resumo = "📈 **BearStrength – Resumo BTC/USDT**\n\n"
    for key in TIMEFRAMES:
        precos = await buscar_precos_simples(key)
        if precos:
            preco_atual = precos[-1]
            ma9, ma21, _ = calcular_medias(precos)
            if ma9 and ma21:
                if preco_atual > ma9 and preco_atual > ma21:
                    sinal = "🟢 LONG"
                elif preco_atual < ma9 and preco_atual < ma21:
                    sinal = "🔴 SHORT"
                else:
                    sinal = "⏸️ AGUARDAR"
            else:
                sinal = "❓ Dados insuficientes"
            
            resumo += f"**{TIMEFRAMES[key]['nome']}**: ${preco_atual:,.0f} → {sinal}\n"
    
    resumo += "\n📌 Digite `5m`, `1h`, etc. para análise detalhada"
    await update.message.reply_text(resumo, parse_mode="Markdown")

async def callback_handler(update, context):
    query = update.callback_query
    await query.answer()
    data = query.data
    
    if data.startswith("analise_"):
        timeframe_key = data.split("_")[1]
        await enviar_analise(update, context, timeframe_key)
    
    elif data.startswith("grafico_"):
        timeframe_key = data.split("_")[1]
        grafico = await criar_grafico(timeframe_key)
        if grafico:
            await query.edit_message_caption(
                caption=f"📈 Gráfico BTC/USD - {TIMEFRAMES[timeframe_key]['nome']}",
                reply_markup=teclado_voltar()
            )
            await query.message.reply_photo(photo=grafico)
        else:
            await query.edit_message_text("❌ Erro ao gerar gráfico.", reply_markup=teclado_voltar())
    
    elif data == "grafico_menu":
        keyboard = []
        for i in range(1, 11):
            nome = TIMEFRAMES[str(i)]["nome"].replace(" minutos", "m").replace(" hora", "h").replace(" dias", "d")
            if i == 10:
                nome = "30d"
            keyboard.append([InlineKeyboardButton(nome, callback_data=f"grafico_{i}")])
        keyboard.append([InlineKeyboardButton("◀ Voltar", callback_data="voltar")])
        await query.edit_message_text("📊 **Escolha o timeframe para o gráfico:**", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
    
    elif data == "meus_alertas":
        chat_id = update.effective_chat.id
        if chat_id not in alertas_ativos or not alertas_ativos[chat_id]:
            texto = "🔔 Você não tem alertas ativos.\n\nUse `/alerta` para criar um."
        else:
            texto = "🔔 **Seus alertas ativos:**\n\n"
            for i, alerta in enumerate(alertas_ativos[chat_id], 1):
                texto += f"{i}️⃣ BTC {alerta['direcao']} **${alerta['preco']:,.0f}** em {alerta['timeframe_nome']}\n"
            texto += "\n🗑️ Para remover: `/removeralerta [numero]`"
        
        await query.edit_message_text(texto, parse_mode="Markdown")
        await asyncio.sleep(5)
        await query.edit_message_text("🐻 **BearStrength**", reply_markup=teclado_principal())
    
    elif data == "voltar":
        await query.edit_message_text("🐻 **BearStrength**", reply_markup=teclado_principal())

async def message_handler(update, context):
    texto = update.message.text.lower().strip()
    
    atalhos = {
        "5m": "1", "10m": "2", "30m": "3", "1h": "4",
        "2h": "5", "4h": "6", "1d": "7", "2d": "8",
        "7d": "9", "30d": "10"
    }
    
    if texto in atalhos:
        await enviar_analise(update, context, atalhos[texto])
    else:
        await update.message.reply_text(
            "🐻 **BearStrength**\n\n"
            "Use `/start` para ver o menu\n"
            "Ou digite um atalho: `5m`, `1h`, `4h`, `1d`, etc.\n\n"
            "Comandos disponíveis:\n"
            "• `/todos` - resumo de todos timeframes\n"
            "• `/alerta` - criar alerta de preço\n"
            "• `/meusalertas` - ver seus alertas\n"
            "• `/removeralerta` - remover alerta",
            parse_mode="Markdown"
        )

# ========== MAIN ==========
def main():
    if not TELEGRAM_TOKEN:
        print("❌ ERRO: TELEGRAM_TOKEN não configurado!")
        print("🔧 Configure a variável de ambiente TELEGRAM_TOKEN no Railway/Replit")
        return
    
    carregar_alertas()
    
    print("🐻 BearStrength iniciando...")
    print("✅ Bot rodando! Pressione Ctrl+C para parar.")
    
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("todos", todos))
    app.add_handler(CommandHandler("alerta", alerta))
    app.add_handler(CommandHandler("meusalertas", meus_alertas))
    app.add_handler(CommandHandler("removeralerta", remover_alerta))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
    
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.create_task(verificar_alertas(app))
    
    app.run_polling(allowed_updates=["message", "callback_query"])

if __name__ == "__main__":
    main()
