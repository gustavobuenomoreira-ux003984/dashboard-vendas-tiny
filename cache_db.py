"""
Cache local em SQLite.

Guardamos aqui os pedidos ja baixados da API do Tiny para nao precisar
rebaixar o detalhe de cada pedido toda vez que o dashboard abrir.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

CAMINHO_PADRAO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dados", "cache_tiny.sqlite")

ESQUEMA = """
CREATE TABLE IF NOT EXISTS pedidos (
    id                TEXT PRIMARY KEY,
    numero            TEXT,
    data              TEXT,
    situacao          TEXT,
    cliente           TEXT,
    vendedor          TEXT,
    total_pedido      REAL,
    total_produtos    REAL,
    quantidade_itens  REAL,
    atualizado_em     TEXT
);

CREATE TABLE IF NOT EXISTS itens (
    id_pedido      TEXT,
    codigo         TEXT,
    descricao      TEXT,
    quantidade     REAL,
    valor_unitario REAL,
    valor_total    REAL
);

CREATE INDEX IF NOT EXISTS idx_itens_pedido ON itens(id_pedido);
CREATE INDEX IF NOT EXISTS idx_pedidos_data ON pedidos(data);

CREATE TABLE IF NOT EXISTS meta (
    chave TEXT PRIMARY KEY,
    valor TEXT
);
"""


def conectar(caminho: str = CAMINHO_PADRAO) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(caminho), exist_ok=True)
    conexao = sqlite3.connect(caminho, check_same_thread=False)
    conexao.row_factory = sqlite3.Row
    conexao.executescript(ESQUEMA)
    conexao.commit()
    return conexao


def situacoes_em_cache(conexao: sqlite3.Connection) -> Dict[str, str]:
    """{id_pedido: situacao} de tudo que ja esta salvo."""
    cursor = conexao.execute("SELECT id, situacao FROM pedidos")
    return {linha["id"]: linha["situacao"] for linha in cursor.fetchall()}


def salvar_pedido(
    conexao: sqlite3.Connection,
    pedido: Dict[str, Any],
    itens: List[Dict[str, Any]],
) -> None:
    conexao.execute(
        """
        INSERT OR REPLACE INTO pedidos
            (id, numero, data, situacao, cliente, vendedor,
             total_pedido, total_produtos, quantidade_itens, atualizado_em)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            pedido["id"],
            pedido.get("numero", ""),
            pedido.get("data", ""),
            pedido.get("situacao", ""),
            pedido.get("cliente", ""),
            pedido.get("vendedor", ""),
            float(pedido.get("total_pedido") or 0),
            float(pedido.get("total_produtos") or 0),
            float(pedido.get("quantidade_itens") or 0),
            datetime.now().isoformat(timespec="seconds"),
        ),
    )
    conexao.execute("DELETE FROM itens WHERE id_pedido = ?", (pedido["id"],))
    if itens:
        conexao.executemany(
            """
            INSERT INTO itens (id_pedido, codigo, descricao, quantidade, valor_unitario, valor_total)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    pedido["id"],
                    item.get("codigo", ""),
                    item.get("descricao", ""),
                    float(item.get("quantidade") or 0),
                    float(item.get("valor_unitario") or 0),
                    float(item.get("valor_total") or 0),
                )
                for item in itens
            ],
        )


def commit(conexao: sqlite3.Connection) -> None:
    conexao.commit()


def carregar_pedidos(
    conexao: sqlite3.Connection,
    data_inicial: Optional[date] = None,
    data_final: Optional[date] = None,
) -> pd.DataFrame:
    consulta = "SELECT * FROM pedidos WHERE data <> ''"
    parametros: List[Any] = []
    if data_inicial:
        consulta += " AND data >= ?"
        parametros.append(data_inicial.isoformat())
    if data_final:
        consulta += " AND data <= ?"
        parametros.append(data_final.isoformat())
    consulta += " ORDER BY data DESC"

    df = pd.read_sql_query(consulta, conexao, params=parametros)
    if df.empty:
        return pd.DataFrame(
            columns=[
                "id", "numero", "data", "situacao", "cliente", "vendedor",
                "total_pedido", "total_produtos", "quantidade_itens", "atualizado_em",
            ]
        )
    df["data"] = pd.to_datetime(df["data"], errors="coerce").dt.date
    for coluna in ("total_pedido", "total_produtos", "quantidade_itens"):
        df[coluna] = pd.to_numeric(df[coluna], errors="coerce").fillna(0.0)
    df["vendedor"] = df["vendedor"].fillna("").replace("", "Sem vendedor")
    return df


def carregar_itens(conexao: sqlite3.Connection, ids: List[str]) -> pd.DataFrame:
    if not ids:
        return pd.DataFrame(columns=["id_pedido", "codigo", "descricao", "quantidade", "valor_unitario", "valor_total"])
    marcadores = ",".join("?" for _ in ids)
    return pd.read_sql_query(
        f"SELECT * FROM itens WHERE id_pedido IN ({marcadores})", conexao, params=ids
    )


def resumo_cache(conexao: sqlite3.Connection) -> Dict[str, Any]:
    linha = conexao.execute(
        "SELECT COUNT(*) AS total, MIN(data) AS primeira, MAX(data) AS ultima FROM pedidos WHERE data <> ''"
    ).fetchone()
    ultima_sync = conexao.execute(
        "SELECT valor FROM meta WHERE chave = 'ultima_sincronizacao'"
    ).fetchone()
    return {
        "total": linha["total"] or 0,
        "primeira": linha["primeira"],
        "ultima": linha["ultima"],
        "ultima_sincronizacao": ultima_sync["valor"] if ultima_sync else None,
    }


def registrar_sincronizacao(conexao: sqlite3.Connection) -> None:
    conexao.execute(
        "INSERT OR REPLACE INTO meta (chave, valor) VALUES ('ultima_sincronizacao', ?)",
        (datetime.now().strftime("%d/%m/%Y %H:%M"),),
    )
    conexao.commit()


def limpar_cache(conexao: sqlite3.Connection) -> None:
    conexao.executescript("DELETE FROM pedidos; DELETE FROM itens; DELETE FROM meta;")
    conexao.commit()
