# -*- coding: utf-8 -*-
"""
Carga historica: baixa varios meses do Tiny e grava no Neon (via HTTPS) e no
SQLite local.

Rodar:  .venv/bin/python carga_historica.py 2025-02 2026-09

Pode ser interrompido (Ctrl+C) e rodado de novo: ele pula o que ja gravou.
Usamos HTTPS porque a porta 5432 do Postgres esta bloqueada nesta rede.
"""

from __future__ import annotations

import calendar
import os
import sys
import time
from datetime import date, datetime

import requests
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

import cache_db
from tiny_api import TinyClient, TinyAPIError, TinyRateLimitError, normalizar_pedido, normalizar_situacao

LOTE = 40  # pedidos gravados por requisicao no Neon


def url_do_banco() -> str:
    url = os.getenv("DATABASE_URL", "").strip().strip('"')
    if not url:
        # a linha pode estar comentada no .env (a porta 5432 e bloqueada aqui)
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"), encoding="utf-8") as f:
            for linha in f:
                if linha.strip().startswith("#DATABASE_URL="):
                    url = linha.split("=", 1)[1].strip().strip('"')
                    break
    if not url:
        raise SystemExit("DATABASE_URL nao encontrada no .env")
    return url


class Neon:
    """Executa SQL no Neon pelo endpoint HTTPS (porta 443)."""

    def __init__(self, url: str):
        self.url = url
        host = url.split("@")[1].split("/")[0]
        self.endpoint = f"https://{host}/sql"
        self.sessao = requests.Session()

    def executar(self, query: str, params=None, tentativas: int = 4):
        for tentativa in range(1, tentativas + 1):
            try:
                r = self.sessao.post(
                    self.endpoint,
                    headers={
                        "Neon-Connection-String": self.url,
                        "Neon-Raw-Text-Output": "true",
                        "Neon-Array-Mode": "true",
                    },
                    json={"query": query, "params": params or []},
                    timeout=90,
                )
            except requests.RequestException as erro:
                if tentativa == tentativas:
                    raise
                print(f"    (rede instavel: {erro}; tentando de novo)", flush=True)
                time.sleep(3 * tentativa)
                continue

            if r.status_code == 200:
                return r.json().get("rows", [])
            if tentativa == tentativas:
                raise RuntimeError(f"Neon HTTP {r.status_code}: {r.text[:300]}")
            print(f"    (Neon respondeu {r.status_code}; tentando de novo)", flush=True)
            time.sleep(3 * tentativa)

    def criar_tabelas(self):
        for comando in cache_db.ESQUEMA:
            self.executar(comando)

    def ids_existentes(self) -> dict:
        linhas = self.executar("SELECT id, situacao FROM pedidos")
        return {str(l[0]): l[1] for l in linhas}

    def gravar_lote(self, pedidos: list, itens_por_pedido: dict):
        if not pedidos:
            return
        # --- pedidos -------------------------------------------------------
        valores, params = [], []
        for i, p in enumerate(pedidos):
            base = i * 10
            valores.append("(" + ",".join(f"${base + n}" for n in range(1, 11)) + ")")
            params += [
                p["id"], p.get("numero", ""), p.get("data", ""), p.get("situacao", ""),
                p.get("cliente", ""), p.get("vendedor", ""),
                float(p.get("total_pedido") or 0), float(p.get("total_produtos") or 0),
                float(p.get("quantidade_itens") or 0),
                datetime.now().isoformat(timespec="seconds"),
            ]
        self.executar(
            "INSERT INTO pedidos (id, numero, data, situacao, cliente, vendedor, "
            "total_pedido, total_produtos, quantidade_itens, atualizado_em) VALUES "
            + ",".join(valores) +
            " ON CONFLICT (id) DO UPDATE SET numero=EXCLUDED.numero, data=EXCLUDED.data, "
            "situacao=EXCLUDED.situacao, cliente=EXCLUDED.cliente, vendedor=EXCLUDED.vendedor, "
            "total_pedido=EXCLUDED.total_pedido, total_produtos=EXCLUDED.total_produtos, "
            "quantidade_itens=EXCLUDED.quantidade_itens, atualizado_em=EXCLUDED.atualizado_em",
            params,
        )

        # --- itens ---------------------------------------------------------
        ids = [p["id"] for p in pedidos]
        marcadores = ",".join(f"${n}" for n in range(1, len(ids) + 1))
        self.executar(f"DELETE FROM itens WHERE id_pedido IN ({marcadores})", ids)

        valores, params = [], []
        for p in pedidos:
            for item in itens_por_pedido.get(p["id"], []):
                base = len(params)
                valores.append("(" + ",".join(f"${base + n}" for n in range(1, 7)) + ")")
                params += [
                    p["id"], item.get("codigo", ""), item.get("descricao", ""),
                    float(item.get("quantidade") or 0), float(item.get("valor_unitario") or 0),
                    float(item.get("valor_total") or 0),
                ]
        if valores:
            self.executar(
                "INSERT INTO itens (id_pedido, codigo, descricao, quantidade, valor_unitario, valor_total) "
                "VALUES " + ",".join(valores),
                params,
            )


def meses_entre(inicio: str, fim: str):
    ai, mi = (int(x) for x in inicio.split("-"))
    af, mf = (int(x) for x in fim.split("-"))
    meses = []
    ano, mes = ai, mi
    while (ano, mes) <= (af, mf):
        meses.append((ano, mes))
        mes += 1
        if mes == 13:
            mes, ano = 1, ano + 1
    return list(reversed(meses))  # do mais recente para o mais antigo


def main():
    inicio = sys.argv[1] if len(sys.argv) > 1 else "2025-01"
    fim = sys.argv[2] if len(sys.argv) > 2 else date.today().strftime("%Y-%m")

    neon = Neon(url_do_banco())
    neon.criar_tabelas()
    local = cache_db.conectar("")  # SQLite, para o app rodando no Mac
    ja_tem = neon.ids_existentes()
    print(f"Neon ja possui {len(ja_tem)} pedidos. Carregando {inicio} ate {fim}.\n", flush=True)

    # Pausa entre chamadas. Acima de ~60/min o Tiny bloqueia e a espera de 60s
    # sai mais cara do que ir devagar. Pode ser passada como 3o argumento.
    pausa = float(sys.argv[3]) if len(sys.argv) > 3 else 0.75
    print(f"Pausa entre chamadas: {pausa}s (~{60/(pausa+0.4):.0f} pedidos/min)\n", flush=True)
    cliente = TinyClient(os.getenv("TINY_TOKEN"), pausa=pausa)
    inicio_geral = time.time()
    gravados_total = 0
    meses_com_falha = []

    for ano, mes in meses_entre(inicio, fim):
        d1 = date(ano, mes, 1)
        d2 = min(date(ano, mes, calendar.monthrange(ano, mes)[1]), date.today())
        if d1 > date.today():
            continue

        # A busca do mes tambem pode bater no limite da API. Se desistirmos
        # aqui, o mes inteiro fica faltando sem ninguem perceber - entao
        # insistimos, e o que nao der guardamos para avisar no final.
        resumos = None
        for tentativa in range(1, 6):
            try:
                resumos = cliente.pesquisar_pedidos(d1, d2)
                break
            except TinyRateLimitError:
                espera = 60 * tentativa
                print(f"{ano}-{mes:02d}: limite na busca; aguardando {espera}s "
                      f"(tentativa {tentativa} de 5)", flush=True)
                time.sleep(espera)
            except TinyAPIError as erro:
                print(f"{ano}-{mes:02d}: erro na pesquisa -> {erro.mensagem[:90]}", flush=True)
                break
        if resumos is None:
            meses_com_falha.append(f"{ano}-{mes:02d}")
            print(f"{ano}-{mes:02d}: NAO FOI POSSIVEL BUSCAR - ficara faltando", flush=True)
            continue

        pendentes = [
            r for r in resumos
            if str(r.get("id", "")).strip()
            and (str(r["id"]) not in ja_tem or ja_tem[str(r["id"])] != normalizar_situacao(r.get("situacao")))
        ]
        print(f"{ano}-{mes:02d}: {len(resumos)} pedidos, {len(pendentes)} a baixar", flush=True)
        if not pendentes:
            continue

        lote, itens_lote, gravados_mes = [], {}, 0
        for indice, resumo in enumerate(pendentes, start=1):
            detalhe = None
            for tentativa in range(1, 7):
                try:
                    detalhe = cliente.obter_pedido(resumo["id"])
                    break
                except TinyRateLimitError:
                    espera = 60 * tentativa
                    print(f"    limite da API atingido; aguardando {espera}s", flush=True)
                    time.sleep(espera)
                except TinyAPIError as erro:
                    # Queda de rede e temporaria: se desistirmos do pedido aqui,
                    # ele fica faltando para sempre. Esperamos a rede voltar.
                    texto = erro.mensagem.lower()
                    transitorio = any(
                        t in texto for t in ("conexao", "conexão", "timeout", "instavel", "instável")
                    )
                    if transitorio and tentativa < 6:
                        espera = 30 * tentativa
                        print(f"    rede indisponivel; aguardando {espera}s "
                              f"(tentativa {tentativa} de 6)", flush=True)
                        time.sleep(espera)
                        continue
                    print(f"    pedido {resumo['id']} falhou: {erro.mensagem[:70]}", flush=True)
                    break
            if not detalhe:
                continue

            pedido, itens = normalizar_pedido(detalhe, resumo)
            if not pedido["id"]:
                continue
            lote.append(pedido)
            itens_lote[pedido["id"]] = itens
            cache_db.salvar_pedido(local, pedido, itens)

            if len(lote) >= LOTE:
                neon.gravar_lote(lote, itens_lote)
                cache_db.commit(local)
                gravados_mes += len(lote)
                gravados_total += len(lote)
                decorrido = time.time() - inicio_geral
                print(
                    f"    {gravados_mes}/{len(pendentes)} do mes | "
                    f"{gravados_total} no total | {decorrido/60:.0f} min decorridos",
                    flush=True,
                )
                lote, itens_lote = [], {}

        if lote:
            neon.gravar_lote(lote, itens_lote)
            cache_db.commit(local)
            gravados_mes += len(lote)
            gravados_total += len(lote)

        ja_tem = neon.ids_existentes()
        print(f"{ano}-{mes:02d}: concluido ({gravados_mes} gravados)\n", flush=True)

    total = neon.executar("SELECT COUNT(*) FROM pedidos")[0][0]
    itens = neon.executar("SELECT COUNT(*) FROM itens")[0][0]
    if meses_com_falha:
        print(f"\nATENCAO: estes meses nao puderam ser buscados e ficaram "
              f"faltando: {', '.join(meses_com_falha)}", flush=True)
        print("Rode o script de novo para completar.", flush=True)
    print(f"\nFIM. Neon tem {total} pedidos e {itens} itens. "
          f"Tempo total: {(time.time()-inicio_geral)/60:.0f} minutos.", flush=True)


if __name__ == "__main__":
    main()
