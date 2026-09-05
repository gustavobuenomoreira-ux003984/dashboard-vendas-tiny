# 📊 Dashboard de Vendas — ERP Tiny

Dashboard em Streamlit que puxa os pedidos do Tiny (API v2), guarda tudo em um
cache local e calcula as métricas de venda — geral e por vendedora.

---

## 1. Instalar (só na primeira vez)

Abra o Terminal, entre na pasta do projeto e rode:

```bash
cd "$HOME/Documents/Códigos Cloud/Vídeos/dashboard-tiny" && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

Isso cria um ambiente isolado (`.venv`) e instala streamlit, pandas, requests e python-dotenv.

## 2. Colocar o seu token

1. No Tiny, vá em **Configurações → Geral → aba "Configurações da API"** e copie o **Token da API**.
2. Na pasta do projeto, faça uma cópia do arquivo `.env.example` com o nome `.env`:

```bash
cd "$HOME/Documents/Códigos Cloud/Vídeos/dashboard-tiny" && cp .env.example .env
```

3. Abra o arquivo `.env` (pode ser no TextEdit) e troque `cole_aqui_o_seu_token` pelo token real:

```
TINY_TOKEN=abc123seutokenaqui
TINY_STATUS_VENDA=Aprovado,Faturado
TINY_PAUSA_SEGUNDOS=0.4
```

O `.env` fica só no seu computador — ele está no `.gitignore` e nunca é enviado para o Git.

## 3. Rodar o dashboard

```bash
cd "$HOME/Documents/Códigos Cloud/Vídeos/dashboard-tiny" && .venv/bin/streamlit run app.py
```

O navegador abre sozinho em `http://localhost:8501`.
Para parar, volte ao Terminal e aperte `Ctrl + C`.

---

## Como usar

1. Na barra lateral, escolha o **período** (por mês ou por intervalo de datas livre).
2. Clique em **🔄 Atualizar dados**. O app baixa a lista de pedidos e depois o detalhe de
   cada pedido (é o detalhe que traz os itens e a vendedora). Uma barra mostra o progresso.
3. Depois disso, mudar filtros de **status** ou de **vendedora** é instantâneo e **não gasta
   chamadas da API** — os dados já ficaram salvos no cache local.

### Métricas calculadas

| Métrica | Cálculo |
|---|---|
| Faturamento Total | soma do valor dos pedidos do recorte |
| Ticket Médio Geral | faturamento ÷ nº de pedidos |
| PA Geral | total de peças ÷ nº de pedidos |
| Preço Médio por Peça | faturamento ÷ total de peças |
| Por vendedora | as mesmas quatro, agrupadas pelo vendedor do pedido |

### Filtros

- **Data**: por mês (`Setembro 2026`) ou por período livre (data inicial e final).
- **Status**: quais situações contam como venda. O padrão é **Aprovado + Faturado** (definido no `.env`), porque no seu Tiny o pedido nasce Aprovado e depois vira Faturado.
- **Vendedora**: "Todas" (padrão) ou uma/várias específicas. A lista é montada automaticamente
  a partir dos pedidos já baixados.

Os filtros se combinam: *Setembro 2026 + Julia* recalcula os cards do topo e a seção por vendedora
só para esse recorte.

---

## Arquivos do projeto

| Arquivo | Para que serve |
|---|---|
| `app.py` | a tela do dashboard (Streamlit) |
| `tiny_api.py` | conversa com a API do Tiny: paginação, pausa entre chamadas, tratamento de erro |
| `cache_db.py` | cache local em SQLite (`dados/cache_tiny.sqlite`) |
| `metrics.py` | cálculo das métricas e formatação em R$ brasileiro |
| `.env` | seu token (não versionado) |
| `.env.example` | modelo do `.env` |

## Cache

Fica em `dados/cache_tiny.sqlite`. Guarda pedidos e itens já baixados, então o app só busca
na API o que ainda não tem (ou pedidos cujo status mudou). Para começar do zero:
barra lateral → **🗄️ Cache local** → **Apagar cache**.

## Se der erro

| Mensagem | O que fazer |
|---|---|
| 🔑 Token inválido | confira o `TINY_TOKEN` no `.env` e se a API está habilitada na sua conta Tiny |
| ⏳ Limite de requisições excedido | espere alguns minutos; o que já baixou ficou salvo no cache |
| 🐢 Timeout | verifique a internet e clique em Atualizar de novo |
| Nenhum pedido no período | confira as datas, ou se os pedidos existem mesmo nesse intervalo |
