# -*- coding: utf-8 -*-
"""
Confere, mes a mes, se o que esta no Neon bate com o que o Tiny diz existir.
Serve para pegar buracos que passariam despercebidos.

Rodar:  .venv/bin/python verificar_carga.py 2025-01 2026-09
"""
from __future__ import annotations

import calendar
import os
import sys
from datetime import date

from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

from carga_historica import Neon, meses_entre, url_do_banco
from tiny_api import TinyAPIError, TinyClient


def main():
    inicio = sys.argv[1] if len(sys.argv) > 1 else "2025-01"
    fim = sys.argv[2] if len(sys.argv) > 2 else date.today().strftime("%Y-%m")

    neon = Neon(url_do_banco())
    cliente = TinyClient(os.getenv("TINY_TOKEN"), pausa=0.75)

    print(f"{'mes':<9} {'no Tiny':>8} {'no Neon':>8}  situacao")
    faltando = []
    for ano, mes in sorted(meses_entre(inicio, fim)):
        d1 = date(ano, mes, 1)
        if d1 > date.today():
            continue
        d2 = min(date(ano, mes, calendar.monthrange(ano, mes)[1]), date.today())
        try:
            no_tiny = len(cliente.pesquisar_pedidos(d1, d2))
        except TinyAPIError as erro:
            print(f"{ano}-{mes:02d}   erro ao consultar o Tiny: {erro.mensagem[:60]}")
            continue
        no_neon = int(
            neon.executar(
                "SELECT COUNT(*) FROM pedidos WHERE data >= $1 AND data <= $2",
                [d1.isoformat(), d2.isoformat()],
            )[0][0]
        )
        if no_neon >= no_tiny:
            estado = "ok"
        else:
            estado = f"FALTAM {no_tiny - no_neon}"
            faltando.append(f"{ano}-{mes:02d}")
        print(f"{ano}-{mes:02d}   {no_tiny:>8} {no_neon:>8}  {estado}")
        sys.stdout.flush()

    print()
    if faltando:
        print("Meses incompletos:", ", ".join(faltando))
        print("Rode: .venv/bin/python carga_historica.py <mes_inicial> <mes_final> 0.75")
    else:
        print("Todos os meses conferidos estao completos.")


if __name__ == "__main__":
    main()
