"""
Dashboard de Vendas - ERP Tiny (API v2)

Rodar com:  streamlit run app.py
"""

from __future__ import annotations

import calendar
import hmac
import os
from datetime import date, datetime
from typing import List, Optional, Tuple

import altair as alt
import pandas as pd
import streamlit as st
from dotenv import load_dotenv

import cache_db
import metrics
from tiny_api import (
    TinyAPIError,
    TinyClient,
    TinyRateLimitError,
    TinySemRegistros,
    TinyTimeoutError,
    TinyTokenError,
    normalizar_pedido,
    normalizar_situacao,
)

# --------------------------------------------------------------------------
# Configuracao
# --------------------------------------------------------------------------
st.set_page_config(page_title="Dashboard de Vendas | Tiny", page_icon="📊", layout="wide")

load_dotenv()


def segredo(chave: str, padrao: str = "") -> str:
    """Le uma configuracao do st.secrets (Streamlit Cloud) ou do .env (local)."""
    try:
        if chave in st.secrets:
            return str(st.secrets[chave]).strip()
    except Exception:
        pass  # nao existe arquivo de secrets: seguimos pelo .env
    return (os.getenv(chave) or padrao).strip()


TOKEN = segredo("TINY_TOKEN")
# Se o .env ainda estiver com o texto de exemplo, tratamos como "sem token".
if TOKEN.lower() in {"cole_aqui_o_seu_token", "seu_token_aqui", "abc123seutokenaqui"}:
    TOKEN = ""
SENHA_APP = segredo("APP_SENHA")
STATUS_PADRAO = [
    s.strip() for s in (segredo("TINY_STATUS_VENDA") or "Aprovado").split(",") if s.strip()
]
PAUSA_API = float(segredo("TINY_PAUSA_SEGUNDOS") or 0.4)

SITUACOES_CONHECIDAS = [
    "Em aberto", "Aprovado", "Preparando envio", "Faturado",
    "Pronto para envio", "Enviado", "Entregue", "Nao entregue", "Cancelado",
]

MESES = [
    "Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
    "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro",
]

AZUL = "#2563eb"

st.markdown(
    """
    <style>
      .block-container { padding-top: 2.2rem; max-width: 1400px; }
      [data-testid="stMetricValue"] {
          font-size: clamp(1.15rem, 1.9vw, 1.8rem);
          font-weight: 700;
          white-space: nowrap;
          overflow: visible;
          text-overflow: clip;
      }
      [data-testid="stMetricValue"] > div {
          overflow: visible !important;
          text-overflow: clip !important;
      }
      [data-testid="stMetricLabel"] { font-size: 0.95rem; color: #475569; }
      [data-testid="stMetric"] {
          background: #f8fafc;
          border: 1px solid #e2e8f0;
          border-radius: 12px;
          padding: 16px 18px;
      }
      h1 { font-size: 2.1rem !important; }
      .rodape-secao { color:#64748b; font-size:0.9rem; }
    </style>
    """,
    unsafe_allow_html=True,
)


# --------------------------------------------------------------------------
# Login (uma senha unica para a equipe)
# --------------------------------------------------------------------------
def tela_de_login() -> None:
    _, meio, _ = st.columns([1, 1.3, 1])
    with meio:
        st.markdown("<div style='height:10vh'></div>", unsafe_allow_html=True)
        st.markdown("## 📊 Dashboard de Vendas")
        st.caption("Acesso restrito à equipe. Peça a senha para a gerência.")
        with st.form("form_login"):
            digitada = st.text_input(
                "Senha", type="password", placeholder="Digite a senha da equipe"
            )
            entrar = st.form_submit_button("Entrar", type="primary", use_container_width=True)
        if entrar:
            if hmac.compare_digest(digitada.strip(), SENHA_APP):
                st.session_state["autenticado"] = True
                st.rerun()
            else:
                st.error("Senha incorreta. Tente de novo.")


# Sem senha configurada (uso local), o app abre direto.
if SENHA_APP and not st.session_state.get("autenticado", False):
    tela_de_login()
    st.stop()


# --------------------------------------------------------------------------
# Recursos
# --------------------------------------------------------------------------
@st.cache_resource
def obter_conexao(url: str):
    """Postgres (Neon) quando DATABASE_URL existe; senao SQLite local.

    A url entra como parametro de proposito: assim, se a configuracao mudar,
    o Streamlit cria uma conexao nova em vez de reaproveitar a antiga.
    """
    return cache_db.conectar(url)


conexao = obter_conexao(segredo("DATABASE_URL"))
if getattr(conexao, "aviso", ""):
    st.sidebar.warning("⚠️ " + conexao.aviso)


def primeiro_e_ultimo_dia(ano: int, mes: int) -> Tuple[date, date]:
    return date(ano, mes, 1), date(ano, mes, calendar.monthrange(ano, mes)[1])


def opcoes_de_mes() -> List[Tuple[int, int]]:
    """Meses disponiveis: os presentes no cache + os ultimos 18 meses."""
    hoje = date.today()
    opcoes = set()
    ano, mes = hoje.year, hoje.month
    for _ in range(18):
        opcoes.add((ano, mes))
        mes -= 1
        if mes == 0:
            mes, ano = 12, ano - 1

    # Alem dos meses recentes, oferecemos todos os meses que existem no banco.
    # Sem isso, um historico mais antigo que 18 meses ficaria inacessivel.
    resumo = cache_db.resumo_cache(conexao)
    primeira = resumo.get("primeira")
    ultima = resumo.get("ultima")
    if primeira and ultima:
        try:
            d1 = datetime.strptime(str(primeira)[:10], "%Y-%m-%d").date()
            d2 = datetime.strptime(str(ultima)[:10], "%Y-%m-%d").date()
            ano, mes = d1.year, d1.month
            while (ano, mes) <= (d2.year, d2.month):
                opcoes.add((ano, mes))
                mes += 1
                if mes == 13:
                    mes, ano = 1, ano + 1
        except ValueError:
            pass
    return sorted(opcoes, reverse=True)


def rotulo_mes(par: Tuple[int, int]) -> str:
    ano, mes = par
    return f"{MESES[mes - 1]} {ano}"


# --------------------------------------------------------------------------
# Sincronizacao com a API
# --------------------------------------------------------------------------
def sincronizar(data_inicial: date, data_final: date, forcar: bool) -> None:
    if not TOKEN:
        st.error(
            "Token nao encontrado. Crie o arquivo `.env` na pasta do projeto com "
            "`TINY_TOKEN=seu_token_aqui` e reinicie o aplicativo."
        )
        return

    try:
        cliente = TinyClient(TOKEN, pausa=PAUSA_API)
    except TinyTokenError as erro:
        st.error(erro.mensagem)
        return

    baixados = 0
    try:
        with st.status("Conectando na API do Tiny...", expanded=True) as status:
            status.write("Buscando a lista de pedidos do período (pedidos.pesquisa.php)...")

            def progresso(pagina, total_paginas, acumulado):
                total = f"/{total_paginas}" if total_paginas else ""
                status.write(f"Página {pagina}{total} — {acumulado} pedidos encontrados.")

            resumos = cliente.pesquisar_pedidos(data_inicial, data_final, ao_progredir=progresso)

            if not resumos:
                status.update(label="Nenhum pedido encontrado nesse período.", state="complete")
                st.warning("A API do Tiny não retornou pedidos para o período selecionado.")
                cache_db.registrar_sincronizacao(conexao)
                return

            em_cache = cache_db.situacoes_em_cache(conexao)
            pendentes = []
            for resumo in resumos:
                id_pedido = str(resumo.get("id") or "").strip()
                if not id_pedido:
                    continue
                situacao_atual = normalizar_situacao(resumo.get("situacao"))
                if forcar or id_pedido not in em_cache or em_cache[id_pedido] != situacao_atual:
                    pendentes.append(resumo)

            status.write(
                f"{len(resumos)} pedidos no período. "
                f"{len(resumos) - len(pendentes)} já estavam em cache, "
                f"{len(pendentes)} precisam do detalhe (pedido.obter.php)."
            )

            if pendentes:
                segundos = int(len(pendentes) * (PAUSA_API + 0.35))
                status.write(f"Tempo estimado: cerca de {max(segundos // 60, 0)} min {segundos % 60}s.")
                barra = st.progress(0.0)
                for indice, resumo in enumerate(pendentes, start=1):
                    detalhe = cliente.obter_pedido(resumo["id"])
                    pedido, itens = normalizar_pedido(detalhe, resumo)
                    if pedido["id"]:
                        cache_db.salvar_pedido(conexao, pedido, itens)
                        baixados += 1
                    if indice % 20 == 0:
                        cache_db.commit(conexao)
                    barra.progress(indice / len(pendentes), text=f"Pedido {indice} de {len(pendentes)}")
                barra.empty()

            cache_db.commit(conexao)
            cache_db.registrar_sincronizacao(conexao)
            status.update(label=f"Pronto! {baixados} pedidos atualizados.", state="complete")

    except TinyTokenError as erro:
        cache_db.commit(conexao)
        st.error("🔑 " + erro.mensagem)
        return
    except TinyRateLimitError as erro:
        cache_db.commit(conexao)
        st.error("⏳ " + erro.mensagem)
        st.info(f"{baixados} pedidos foram salvos antes do bloqueio — eles já ficam no cache.")
        return
    except TinyTimeoutError as erro:
        cache_db.commit(conexao)
        st.error("🐢 " + erro.mensagem)
        return
    except TinySemRegistros:
        cache_db.commit(conexao)
        st.warning("A API do Tiny não retornou pedidos para o período selecionado.")
        return
    except TinyAPIError as erro:
        cache_db.commit(conexao)
        st.error("❌ Erro na API do Tiny: " + erro.mensagem)
        return

    st.success(f"Dados atualizados: {baixados} pedidos baixados/atualizados.")


# --------------------------------------------------------------------------
# Sidebar - filtros
# --------------------------------------------------------------------------
st.sidebar.title("⚙️ Filtros")

if TOKEN:
    st.sidebar.caption(f"🔑 Token carregado (final: …{TOKEN[-4:]})")
else:
    st.sidebar.error(
        "Token não configurado. Abra o arquivo `.env` na pasta do projeto e coloque o seu "
        "token do Tiny em `TINY_TOKEN=`. Depois pare o app (Ctrl+C) e rode de novo."
    )

st.sidebar.subheader("1. Período")
modo_data = st.sidebar.radio(
    "Como filtrar a data", ["Por mês", "Por período"], horizontal=True, label_visibility="collapsed"
)

hoje = date.today()
if modo_data == "Por mês":
    opcoes = opcoes_de_mes()
    escolha = st.sidebar.selectbox(
        "Mês/Ano", opcoes, format_func=rotulo_mes, index=0
    )
    data_inicial, data_final = primeiro_e_ultimo_dia(*escolha)
else:
    intervalo = st.sidebar.date_input(
        "Data inicial e final",
        value=(date(hoje.year, hoje.month, 1), hoje),
        format="DD/MM/YYYY",
    )
    if isinstance(intervalo, (list, tuple)) and len(intervalo) == 2:
        data_inicial, data_final = intervalo
    elif isinstance(intervalo, (list, tuple)) and len(intervalo) == 1:
        data_inicial = data_final = intervalo[0]
    else:
        data_inicial = data_final = intervalo

st.sidebar.caption(
    f"Período: {data_inicial.strftime('%d/%m/%Y')} a {data_final.strftime('%d/%m/%Y')}"
)

forcar = st.sidebar.checkbox(
    "Rebaixar tudo (ignorar cache)",
    value=False,
    help="Marque só se desconfiar que os dados em cache estão desatualizados. Fica bem mais lento.",
)
atualizar = st.sidebar.button("🔄 Atualizar dados", type="primary", use_container_width=True)

# Dados em cache do periodo escolhido
df_periodo = cache_db.carregar_pedidos(conexao, data_inicial, data_final)

st.sidebar.subheader("2. Status do pedido")
situacoes_no_cache = sorted(
    {s for s in df_periodo["situacao"].dropna().unique().tolist() if str(s).strip()}
)
opcoes_situacao = sorted(set(SITUACOES_CONHECIDAS) | set(situacoes_no_cache) | set(STATUS_PADRAO))
padrao_situacao = [s for s in STATUS_PADRAO if s in opcoes_situacao] or opcoes_situacao[:1]
situacoes = st.sidebar.multiselect(
    "Considerar como venda válida",
    options=opcoes_situacao,
    default=padrao_situacao,
    help="Padrão definido no .env (TINY_STATUS_VENDA).",
)

st.sidebar.subheader("3. Vendedora")
vendedoras_disponiveis = sorted(
    {v for v in metrics.aplicar_filtros(df_periodo, situacoes=situacoes)["vendedor"].unique().tolist()}
) if not df_periodo.empty else []
opcoes_vendedora = [metrics.TODAS] + vendedoras_disponiveis
vendedoras = st.sidebar.multiselect(
    "Vendedoras", options=opcoes_vendedora, default=[metrics.TODAS]
)
if not vendedoras:
    vendedoras = [metrics.TODAS]

st.sidebar.subheader("4. Base do faturamento")
base_valor = st.sidebar.radio(
    "Valor considerado",
    ["Total do pedido (com frete)", "Somente produtos"],
    label_visibility="collapsed",
)
coluna_valor = "total_pedido" if base_valor.startswith("Total") else "total_produtos"

with st.sidebar.expander("🗄️ Cache local"):
    resumo = cache_db.resumo_cache(conexao)
    st.write(f"Guardado em: **{resumo.get('onde', 'SQLite local')}**")
    st.write(f"Pedidos guardados: **{resumo['total']}**")
    if resumo["primeira"]:
        st.write(f"De {resumo['primeira']} até {resumo['ultima']}")
    if resumo["ultima_sincronizacao"]:
        st.write(f"Última atualização: {resumo['ultima_sincronizacao']}")
    if st.button("Apagar cache", use_container_width=True):
        cache_db.limpar_cache(conexao)
        st.rerun()

if SENHA_APP:
    if st.sidebar.button("🚪 Sair", use_container_width=True):
        st.session_state["autenticado"] = False
        st.rerun()

if atualizar:
    sincronizar(data_inicial, data_final, forcar)
    df_periodo = cache_db.carregar_pedidos(conexao, data_inicial, data_final)


# --------------------------------------------------------------------------
# Corpo do dashboard
# --------------------------------------------------------------------------
st.title("📊 Dashboard de Vendas")
rotulo_periodo = (
    rotulo_mes((data_inicial.year, data_inicial.month))
    if modo_data == "Por mês"
    else f"{data_inicial.strftime('%d/%m/%Y')} a {data_final.strftime('%d/%m/%Y')}"
)
selecao_vendedoras = (
    "todas as vendedoras" if metrics.TODAS in vendedoras else ", ".join(vendedoras)
)
st.caption(
    f"**{rotulo_periodo}** · {selecao_vendedoras} · status: "
    f"{', '.join(situacoes) if situacoes else 'nenhum selecionado'}"
)

df_filtrado = metrics.aplicar_filtros(
    df_periodo,
    situacoes=situacoes,
    vendedores=vendedoras,
    coluna_valor=coluna_valor,
)

if df_periodo.empty:
    st.info(
        "Nenhum pedido em cache para esse período. Clique em **🔄 Atualizar dados** na barra "
        "lateral para baixar os pedidos da API do Tiny."
    )
elif df_filtrado.empty:
    st.warning(
        "Existem pedidos no período, mas nenhum bate com os filtros de status/vendedora. "
        "Tente ajustar o filtro de status na barra lateral."
    )

gerais = metrics.metricas_gerais(df_filtrado)

col1, col2, col3, col4 = st.columns(4)
col1.metric("💰 Faturamento Total", metrics.formatar_brl(gerais["faturamento"]))
col2.metric("🧾 Ticket Médio Geral", metrics.formatar_brl(gerais["ticket_medio"]))
col3.metric("👗 PA Geral (peças/atendimento)", metrics.formatar_numero(gerais["pa"], 2))
col4.metric("🏷️ Preço Médio por Peça", metrics.formatar_brl(gerais["preco_medio_peca"]))

st.caption(
    f"Base do cálculo: {gerais['pedidos']} pedidos · "
    f"{metrics.formatar_numero(gerais['pecas'], 0)} peças · valor = {base_valor.lower()}"
)

st.divider()

# ---- Comparativo por vendedora -------------------------------------------
st.subheader("👥 Comparativo por vendedora")

por_vendedor = metrics.metricas_por_vendedor(df_filtrado)

if por_vendedor.empty:
    st.info("Sem dados para comparar no recorte atual.")
else:
    visao = st.radio(
        "Visão",
        ["📈 Gráficos", "📋 Tabela"],
        horizontal=True,
        label_visibility="collapsed",
    )

    def grafico(coluna: str, titulo: str, moeda: bool, casas: int = 2):
        dados = pd.DataFrame(
            {
                "vendedora": por_vendedor["Vendedora"],
                "valor": por_vendedor[coluna].astype(float),
            }
        )
        dados["rotulo"] = dados["valor"].map(
            metrics.formatar_brl if moeda else (lambda v: metrics.formatar_numero(v, casas))
        )
        # folga a direita para o rotulo do valor nao ficar cortado
        maximo = float(dados["valor"].max() or 0) or 1.0
        base = alt.Chart(dados).encode(
            y=alt.Y(
                "vendedora:N",
                sort="-x",
                title=None,
                axis=alt.Axis(labelLimit=230, labelFontSize=12),
            ),
            x=alt.X(
                "valor:Q",
                title=None,
                scale=alt.Scale(domain=[0, maximo * 1.55], nice=False),
                axis=alt.Axis(labels=False, grid=False, ticks=False, domain=False),
            ),
            tooltip=[
                alt.Tooltip("vendedora:N", title="Vendedora"),
                alt.Tooltip("rotulo:N", title=titulo),
            ],
        )
        barras = base.mark_bar(color=AZUL, cornerRadiusEnd=4, size=26)
        textos = base.mark_text(align="left", dx=6, color="#0f172a", fontSize=11, fontWeight=600).encode(
            text="rotulo:N"
        )
        grafico_final = (barras + textos).properties(
            height=max(120, 46 * len(dados)), title=titulo
        ).configure_view(stroke=None).configure_title(fontSize=16, anchor="start")
        st.altair_chart(grafico_final, use_container_width=True)

    if visao == "📈 Gráficos":
        esquerda, direita = st.columns(2)
        with esquerda:
            grafico("Valor Total de Vendas", "Valor Total de Vendas", moeda=True)
            grafico("PA", "PA (peças por atendimento)", moeda=False, casas=2)
        with direita:
            grafico("Ticket Médio", "Ticket Médio", moeda=True)
            grafico("Preço Médio por Peça", "Preço Médio por Peça", moeda=True)

    else:
        st.dataframe(
            metrics.tabela_formatada(por_vendedor),
            width="stretch",
            hide_index=True,
            column_config={
                "Vendedora": st.column_config.TextColumn(width="medium"),
                "Valor Total de Vendas": st.column_config.TextColumn(width="medium"),
                "Ticket Médio": st.column_config.TextColumn(width="small"),
                "Preço Médio por Peça": st.column_config.TextColumn(width="medium"),
            },
        )
        st.download_button(
            "⬇️ Baixar CSV",
            data=por_vendedor.to_csv(index=False, sep=";", decimal=",").encode("utf-8-sig"),
            file_name=f"vendas_por_vendedora_{data_inicial}_{data_final}.csv",
            mime="text/csv",
        )

# ---- Pedidos do periodo ---------------------------------------------------
st.divider()
if st.checkbox(f"🧾 Mostrar os {len(df_filtrado)} pedidos do recorte"):
    if df_filtrado.empty:
        st.write("Nenhum pedido.")
    else:
        detalhe = pd.DataFrame(
            {
                "Data": pd.to_datetime(df_filtrado["data"]).dt.strftime("%d/%m/%Y"),
                "Pedido": df_filtrado["numero"],
                "Cliente": df_filtrado["cliente"],
                "Vendedora": df_filtrado["vendedor"],
                "Status": df_filtrado["situacao"],
                "Peças": df_filtrado["quantidade_itens"].map(lambda v: metrics.formatar_numero(v, 0)),
                "Valor": df_filtrado["valor"].map(metrics.formatar_brl),
            }
        )
        st.dataframe(detalhe, width="stretch", hide_index=True)

st.markdown(
    "<p class='rodape-secao'>Os dados ficam guardados em <code>dados/cache_tiny.sqlite</code>. "
    "Trocar filtros de status, vendedora ou datas dentro do que já foi baixado não gasta chamadas da API.</p>",
    unsafe_allow_html=True,
)
