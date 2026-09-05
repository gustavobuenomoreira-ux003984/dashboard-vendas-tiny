"""Calculo das metricas do dashboard e formatacao em padrao brasileiro."""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional

import pandas as pd

TODAS = "Todas"


# --------------------------------------------------------------------------
# Formatacao brasileira
# --------------------------------------------------------------------------
def formatar_brl(valor: Any) -> str:
    """1234.5 -> 'R$ 1.234,50'"""
    try:
        numero = float(valor)
    except (TypeError, ValueError):
        numero = 0.0
    if pd.isna(numero):
        numero = 0.0
    sinal = "-" if numero < 0 else ""
    texto = f"{abs(numero):,.2f}".replace(",", "\x00").replace(".", ",").replace("\x00", ".")
    return f"{sinal}R$ {texto}"


def formatar_numero(valor: Any, casas: int = 2) -> str:
    """1234.5 -> '1.234,50'"""
    try:
        numero = float(valor)
    except (TypeError, ValueError):
        numero = 0.0
    if pd.isna(numero):
        numero = 0.0
    sinal = "-" if numero < 0 else ""
    texto = f"{abs(numero):,.{casas}f}".replace(",", "\x00").replace(".", ",").replace("\x00", ".")
    return f"{sinal}{texto}"


def formatar_inteiro(valor: Any) -> str:
    return formatar_numero(valor, casas=0)


# --------------------------------------------------------------------------
# Filtros
# --------------------------------------------------------------------------
def aplicar_filtros(
    df: pd.DataFrame,
    data_inicial: Optional[date] = None,
    data_final: Optional[date] = None,
    situacoes: Optional[List[str]] = None,
    vendedores: Optional[List[str]] = None,
    coluna_valor: str = "total_pedido",
) -> pd.DataFrame:
    if df.empty:
        return df.copy()

    filtrado = df.copy()
    if data_inicial is not None:
        filtrado = filtrado[filtrado["data"] >= data_inicial]
    if data_final is not None:
        filtrado = filtrado[filtrado["data"] <= data_final]
    if situacoes:
        alvo = {s.strip().lower() for s in situacoes}
        filtrado = filtrado[filtrado["situacao"].astype(str).str.strip().str.lower().isin(alvo)]
    if vendedores and TODAS not in vendedores:
        filtrado = filtrado[filtrado["vendedor"].isin(vendedores)]

    filtrado = filtrado.copy()
    filtrado["valor"] = pd.to_numeric(filtrado[coluna_valor], errors="coerce").fillna(0.0)
    filtrado["quantidade_itens"] = pd.to_numeric(
        filtrado["quantidade_itens"], errors="coerce"
    ).fillna(0.0)
    return filtrado


# --------------------------------------------------------------------------
# Metricas
# --------------------------------------------------------------------------
def _dividir(numerador: float, denominador: float) -> float:
    return float(numerador) / float(denominador) if denominador else 0.0


def metricas_gerais(df: pd.DataFrame) -> Dict[str, float]:
    """Faturamento, ticket medio, PA e preco medio por peca do recorte inteiro."""
    if df.empty:
        return {
            "faturamento": 0.0,
            "pedidos": 0,
            "pecas": 0.0,
            "ticket_medio": 0.0,
            "pa": 0.0,
            "preco_medio_peca": 0.0,
        }
    faturamento = float(df["valor"].sum())
    pedidos = int(len(df))
    pecas = float(df["quantidade_itens"].sum())
    return {
        "faturamento": faturamento,
        "pedidos": pedidos,
        "pecas": pecas,
        "ticket_medio": _dividir(faturamento, pedidos),
        "pa": _dividir(pecas, pedidos),
        "preco_medio_peca": _dividir(faturamento, pecas),
    }


def metricas_por_vendedor(df: pd.DataFrame) -> pd.DataFrame:
    """Uma linha por vendedora com valor total, ticket medio, PA e preco medio por peca."""
    colunas = [
        "Vendedora", "Valor Total de Vendas", "Pedidos", "Peças",
        "Ticket Médio", "PA", "Preço Médio por Peça",
    ]
    if df.empty:
        return pd.DataFrame(columns=colunas)

    agrupado = (
        df.groupby("vendedor", dropna=False)
        .agg(valor_total=("valor", "sum"), pedidos=("id", "count"), pecas=("quantidade_itens", "sum"))
        .reset_index()
    )
    agrupado["ticket_medio"] = agrupado.apply(
        lambda linha: _dividir(linha["valor_total"], linha["pedidos"]), axis=1
    )
    agrupado["pa"] = agrupado.apply(
        lambda linha: _dividir(linha["pecas"], linha["pedidos"]), axis=1
    )
    agrupado["preco_medio_peca"] = agrupado.apply(
        lambda linha: _dividir(linha["valor_total"], linha["pecas"]), axis=1
    )
    agrupado = agrupado.sort_values("valor_total", ascending=False).reset_index(drop=True)
    agrupado.columns = ["Vendedora", "Valor Total de Vendas", "Pedidos", "Peças",
                        "Ticket Médio", "PA", "Preço Médio por Peça"]
    return agrupado[colunas]


def tabela_formatada(por_vendedor: pd.DataFrame) -> pd.DataFrame:
    """Versao da tabela por vendedora com os numeros ja em texto (R$ brasileiro)."""
    if por_vendedor.empty:
        return por_vendedor
    formatada = pd.DataFrame()
    formatada["Vendedora"] = por_vendedor["Vendedora"]
    formatada["Valor Total de Vendas"] = por_vendedor["Valor Total de Vendas"].map(formatar_brl)
    formatada["Pedidos"] = por_vendedor["Pedidos"].map(formatar_inteiro)
    formatada["Peças"] = por_vendedor["Peças"].map(lambda v: formatar_numero(v, 0))
    formatada["Ticket Médio"] = por_vendedor["Ticket Médio"].map(formatar_brl)
    formatada["PA"] = por_vendedor["PA"].map(lambda v: formatar_numero(v, 2))
    formatada["Preço Médio por Peça"] = por_vendedor["Preço Médio por Peça"].map(formatar_brl)
    return formatada
