"""
Cache dos pedidos ja baixados do Tiny.

Funciona com dois bancos, escolhidos automaticamente:

  - PostgreSQL (Neon), quando existe a configuracao DATABASE_URL.
    E o modo usado na nuvem: os dados sobrevivem quando o app hiberna.
  - SQLite em arquivo, quando nao existe DATABASE_URL.
    E o modo usado no seu computador, sem precisar instalar nada.

O resto do programa nao precisa saber qual dos dois esta em uso.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Sequence

import pandas as pd

CAMINHO_SQLITE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "dados", "cache_tiny.sqlite"
)

COLUNAS_PEDIDO = [
    "id", "numero", "data", "situacao", "cliente", "vendedor",
    "total_pedido", "total_produtos", "quantidade_itens", "atualizado_em",
]

# O tipo de texto muda de nome entre os dois bancos; o resto do SQL e igual.
ESQUEMA = [
    """
    CREATE TABLE IF NOT EXISTS pedidos (
        id                TEXT PRIMARY KEY,
        numero            TEXT,
        data              TEXT,
        situacao          TEXT,
        cliente           TEXT,
        vendedor          TEXT,
        total_pedido      DOUBLE PRECISION,
        total_produtos    DOUBLE PRECISION,
        quantidade_itens  DOUBLE PRECISION,
        atualizado_em     TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS itens (
        id_pedido      TEXT,
        codigo         TEXT,
        descricao      TEXT,
        quantidade     DOUBLE PRECISION,
        valor_unitario DOUBLE PRECISION,
        valor_total    DOUBLE PRECISION
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_itens_pedido ON itens(id_pedido)",
    "CREATE INDEX IF NOT EXISTS idx_pedidos_data ON pedidos(data)",
    "CREATE TABLE IF NOT EXISTS meta (chave TEXT PRIMARY KEY, valor TEXT)",
]


class Conexao:
    """Camada fininha por cima do banco, para o app nao se preocupar com qual e."""

    def __init__(self, url: str = "", timeout: int = 5):
        self.postgres = False
        self.aviso = ""  # mensagem para a tela, quando o Postgres nao responde
        self.url_configurada = bool(url)

        if url:
            try:
                import psycopg2  # importado so quando realmente for usado

                # connect_timeout evita o app ficar travado para sempre se a
                # porta 5432 estiver bloqueada ou o banco fora do ar.
                self.raw = psycopg2.connect(url, connect_timeout=timeout)
                self.postgres = True
            except Exception as erro:
                self.aviso = (
                    "Não consegui conectar no banco Postgres "
                    f"({type(erro).__name__}: {str(erro).strip()[:160]}). "
                    "O app está usando o cache local, que se perde quando o servidor reinicia."
                )

        if not self.postgres:
            os.makedirs(os.path.dirname(CAMINHO_SQLITE), exist_ok=True)
            self.raw = sqlite3.connect(CAMINHO_SQLITE, check_same_thread=False)

        self._criar_tabelas()

    # -- infraestrutura -----------------------------------------------------
    def _traduzir(self, sql: str) -> str:
        """SQLite usa '?' como marcador de parametro; o Postgres usa '%s'."""
        return sql.replace("?", "%s") if self.postgres else sql

    def executar(self, sql: str, parametros: Sequence[Any] = ()) -> List[tuple]:
        cursor = self.raw.cursor()
        cursor.execute(self._traduzir(sql), tuple(parametros))
        linhas = cursor.fetchall() if cursor.description else []
        cursor.close()
        return linhas

    def executar_varios(self, sql: str, lote: List[Sequence[Any]]) -> None:
        if not lote:
            return
        cursor = self.raw.cursor()
        cursor.executemany(self._traduzir(sql), [tuple(p) for p in lote])
        cursor.close()

    def commit(self) -> None:
        self.raw.commit()

    def _criar_tabelas(self) -> None:
        for comando in ESQUEMA:
            self.executar(comando)
        self.commit()

    @property
    def descricao(self) -> str:
        if self.postgres:
            return "PostgreSQL (Neon)"
        if not self.url_configurada:
            return "SQLite local — DATABASE_URL não configurada nos Secrets"
        return "SQLite local — a conexão com o Postgres falhou"


def conectar(url: str = "") -> Conexao:
    """Abre o banco: Postgres se houver DATABASE_URL, senao SQLite no arquivo."""
    return Conexao(url or os.getenv("DATABASE_URL", ""))


# --------------------------------------------------------------------------
# Leitura
# --------------------------------------------------------------------------
def situacoes_em_cache(conexao: Conexao) -> Dict[str, str]:
    """{id_pedido: situacao} de tudo que ja esta salvo."""
    return {str(linha[0]): linha[1] for linha in conexao.executar("SELECT id, situacao FROM pedidos")}


def carregar_pedidos(
    conexao: Conexao,
    data_inicial: Optional[date] = None,
    data_final: Optional[date] = None,
) -> pd.DataFrame:
    consulta = f"SELECT {', '.join(COLUNAS_PEDIDO)} FROM pedidos WHERE data <> ''"
    parametros: List[Any] = []
    if data_inicial:
        consulta += " AND data >= ?"
        parametros.append(data_inicial.isoformat())
    if data_final:
        consulta += " AND data <= ?"
        parametros.append(data_final.isoformat())
    consulta += " ORDER BY data DESC"

    linhas = conexao.executar(consulta, parametros)
    df = pd.DataFrame(linhas, columns=COLUNAS_PEDIDO)
    if df.empty:
        return df
    df["data"] = pd.to_datetime(df["data"], errors="coerce").dt.date
    for coluna in ("total_pedido", "total_produtos", "quantidade_itens"):
        df[coluna] = pd.to_numeric(df[coluna], errors="coerce").fillna(0.0)
    df["vendedor"] = df["vendedor"].fillna("").replace("", "Sem vendedor")
    return df


def carregar_itens(conexao: Conexao, ids: List[str]) -> pd.DataFrame:
    colunas = ["id_pedido", "codigo", "descricao", "quantidade", "valor_unitario", "valor_total"]
    if not ids:
        return pd.DataFrame(columns=colunas)
    marcadores = ",".join("?" for _ in ids)
    linhas = conexao.executar(
        f"SELECT {', '.join(colunas)} FROM itens WHERE id_pedido IN ({marcadores})", ids
    )
    return pd.DataFrame(linhas, columns=colunas)


def resumo_cache(conexao: Conexao) -> Dict[str, Any]:
    linha = conexao.executar(
        "SELECT COUNT(*), MIN(data), MAX(data) FROM pedidos WHERE data <> ''"
    )[0]
    sync = conexao.executar("SELECT valor FROM meta WHERE chave = 'ultima_sincronizacao'")
    return {
        "total": linha[0] or 0,
        "primeira": linha[1],
        "ultima": linha[2],
        "ultima_sincronizacao": sync[0][0] if sync else None,
        "onde": conexao.descricao,
    }


# --------------------------------------------------------------------------
# Escrita
# --------------------------------------------------------------------------
def salvar_pedido(conexao: Conexao, pedido: Dict[str, Any], itens: List[Dict[str, Any]]) -> None:
    valores = (
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
    )
    if conexao.postgres:
        conexao.executar(
            """
            INSERT INTO pedidos (id, numero, data, situacao, cliente, vendedor,
                                 total_pedido, total_produtos, quantidade_itens, atualizado_em)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (id) DO UPDATE SET
                numero = EXCLUDED.numero,
                data = EXCLUDED.data,
                situacao = EXCLUDED.situacao,
                cliente = EXCLUDED.cliente,
                vendedor = EXCLUDED.vendedor,
                total_pedido = EXCLUDED.total_pedido,
                total_produtos = EXCLUDED.total_produtos,
                quantidade_itens = EXCLUDED.quantidade_itens,
                atualizado_em = EXCLUDED.atualizado_em
            """,
            valores,
        )
    else:
        conexao.executar(
            """
            INSERT OR REPLACE INTO pedidos (id, numero, data, situacao, cliente, vendedor,
                                            total_pedido, total_produtos, quantidade_itens, atualizado_em)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            valores,
        )

    conexao.executar("DELETE FROM itens WHERE id_pedido = ?", (pedido["id"],))
    conexao.executar_varios(
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


def commit(conexao: Conexao) -> None:
    conexao.commit()


def registrar_sincronizacao(conexao: Conexao) -> None:
    agora = datetime.now().strftime("%d/%m/%Y %H:%M")
    if conexao.postgres:
        conexao.executar(
            "INSERT INTO meta (chave, valor) VALUES ('ultima_sincronizacao', ?) "
            "ON CONFLICT (chave) DO UPDATE SET valor = EXCLUDED.valor",
            (agora,),
        )
    else:
        conexao.executar(
            "INSERT OR REPLACE INTO meta (chave, valor) VALUES ('ultima_sincronizacao', ?)",
            (agora,),
        )
    conexao.commit()


def limpar_cache(conexao: Conexao) -> None:
    for tabela in ("pedidos", "itens", "meta"):
        conexao.executar(f"DELETE FROM {tabela}")
    conexao.commit()
