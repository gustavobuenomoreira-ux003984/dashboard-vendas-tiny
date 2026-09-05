"""
Cliente da API v2 do ERP Tiny.

Documentacao: https://tiny.com.br/api-docs/
Base: https://api.tiny.com.br/api2/

Endpoints usados:
  - pedidos.pesquisa.php -> lista paginada de pedidos do periodo
  - pedido.obter.php     -> detalhe do pedido (itens + vendedor)
"""

from __future__ import annotations

import time
from datetime import date, datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

import requests

BASE_URL = "https://api.tiny.com.br/api2/"
PAUSA_PADRAO = 0.4          # segundos entre chamadas (respeita o rate limit)
TIMEOUT_PADRAO = 30         # segundos
MAX_TENTATIVAS = 3
MAX_PAGINAS = 1000          # trava de seguranca contra loop infinito

# Codigos numericos de situacao usados por algumas contas do Tiny.
MAPA_SITUACAO = {
    "0": "Em aberto",
    "1": "Faturado",
    "2": "Cancelado",
    "3": "Aprovado",
    "4": "Preparando envio",
    "5": "Enviado",
    "6": "Entregue",
    "7": "Pronto para envio",
    "8": "Dados incompletos",
    "9": "Nao entregue",
}


# --------------------------------------------------------------------------
# Erros
# --------------------------------------------------------------------------
class TinyAPIError(Exception):
    """Erro da API do Tiny com mensagem amigavel para mostrar na tela."""

    def __init__(self, mensagem: str, codigo: Optional[str] = None):
        super().__init__(mensagem)
        self.mensagem = mensagem
        self.codigo = codigo


class TinyTokenError(TinyAPIError):
    """Token ausente, invalido ou sem permissao."""


class TinyRateLimitError(TinyAPIError):
    """Limite de requisicoes excedido / API bloqueada temporariamente."""


class TinyTimeoutError(TinyAPIError):
    """A API demorou demais para responder."""


class TinySemRegistros(TinyAPIError):
    """A consulta foi feita com sucesso, mas nao retornou registros."""


# --------------------------------------------------------------------------
# Helpers de conversao
# --------------------------------------------------------------------------
def para_float(valor: Any) -> float:
    """Converte valores da API ('1234.56', '1.234,56', 1234.56) para float."""
    if valor is None:
        return 0.0
    if isinstance(valor, (int, float)):
        return float(valor)
    texto = str(valor).strip()
    if not texto:
        return 0.0
    texto = texto.replace("R$", "").replace(" ", "")
    if "," in texto and "." in texto:
        texto = texto.replace(".", "").replace(",", ".")
    elif "," in texto:
        texto = texto.replace(",", ".")
    try:
        return float(texto)
    except ValueError:
        return 0.0


def para_data(valor: Any) -> Optional[date]:
    """Converte '31/12/2026' ou '2026-12-31' em date."""
    if not valor:
        return None
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor
    texto = str(valor).strip()[:10]
    for formato in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(texto, formato).date()
        except ValueError:
            continue
    return None


def formatar_data_api(valor: date) -> str:
    """A API do Tiny espera datas em dd/mm/aaaa."""
    return valor.strftime("%d/%m/%Y")


def normalizar_situacao(valor: Any) -> str:
    texto = str(valor or "").strip()
    if not texto:
        return "Sem situacao"
    if texto.isdigit():
        return MAPA_SITUACAO.get(texto, f"Situacao {texto}")
    return texto


def _primeiro_texto(dados: Dict[str, Any], chaves: Tuple[str, ...]) -> str:
    for chave in chaves:
        valor = dados.get(chave)
        if isinstance(valor, dict):
            valor = valor.get("nome") or valor.get("descricao")
        if valor is not None and str(valor).strip():
            return str(valor).strip()
    return ""


# --------------------------------------------------------------------------
# Cliente
# --------------------------------------------------------------------------
class TinyClient:
    def __init__(
        self,
        token: str,
        pausa: float = PAUSA_PADRAO,
        timeout: int = TIMEOUT_PADRAO,
        max_tentativas: int = MAX_TENTATIVAS,
    ):
        if not token or not str(token).strip():
            raise TinyTokenError(
                "Token da API do Tiny nao encontrado. Coloque TINY_TOKEN no arquivo .env "
                "e reinicie o aplicativo."
            )
        self.token = str(token).strip()
        self.pausa = pausa
        self.timeout = timeout
        self.max_tentativas = max_tentativas
        self.sessao = requests.Session()

    # -- infraestrutura -----------------------------------------------------
    def _interpretar_erro(self, retorno: Dict[str, Any]) -> Optional[TinyAPIError]:
        status = str(retorno.get("status", "")).strip().lower()
        processamento = str(retorno.get("status_processamento", "")).strip()
        codigo = str(retorno.get("codigo_erro", "")).strip()

        if status == "ok" and processamento in ("", "3"):
            return None

        mensagens: List[str] = []
        erros = retorno.get("erros") or []
        if isinstance(erros, dict):
            erros = [erros]
        for item in erros:
            if isinstance(item, dict):
                mensagens.append(str(item.get("erro") or item.get("mensagem") or "").strip())
            else:
                mensagens.append(str(item).strip())
        texto = "; ".join(m for m in mensagens if m) or "A API do Tiny retornou um erro sem descricao."
        minusculo = texto.lower()

        if codigo == "20" or "nao retornou registros" in minusculo or "não retornou registros" in minusculo:
            return TinySemRegistros("Nenhum pedido encontrado para esse periodo.", codigo)
        if codigo in ("1", "2") or processamento == "2" or "token" in minusculo:
            return TinyTokenError(
                "Token invalido ou sem permissao. Confira o valor de TINY_TOKEN no arquivo .env "
                "e se a API esta habilitada na sua conta do Tiny.",
                codigo,
            )
        if (
            codigo in ("3", "4", "5", "6")
            or "bloquead" in minusculo
            or "limite" in minusculo
            or "excedid" in minusculo
        ):
            return TinyRateLimitError(
                "Limite de requisicoes da API do Tiny excedido (ou API bloqueada temporariamente). "
                "Aguarde alguns minutos e tente de novo. Detalhe: " + texto,
                codigo,
            )
        return TinyAPIError(texto, codigo)

    def _chamar(self, endpoint: str, parametros: Dict[str, Any]) -> Dict[str, Any]:
        corpo = {"token": self.token, "formato": "json"}
        corpo.update({k: v for k, v in parametros.items() if v not in (None, "")})

        ultimo_erro: Optional[TinyAPIError] = None
        for tentativa in range(1, self.max_tentativas + 1):
            try:
                resposta = self.sessao.post(BASE_URL + endpoint, data=corpo, timeout=self.timeout)
            except requests.Timeout:
                ultimo_erro = TinyTimeoutError(
                    f"A API do Tiny nao respondeu em {self.timeout}s (timeout). "
                    "Verifique sua internet e tente novamente."
                )
            except requests.RequestException as exc:
                ultimo_erro = TinyAPIError(f"Falha de conexao com a API do Tiny: {exc}")
            else:
                if resposta.status_code == 429:
                    ultimo_erro = TinyRateLimitError(
                        "Limite de requisicoes da API do Tiny excedido (HTTP 429). "
                        "Aguarde alguns minutos e tente de novo."
                    )
                elif resposta.status_code >= 500:
                    ultimo_erro = TinyAPIError(
                        f"A API do Tiny esta instavel no momento (HTTP {resposta.status_code})."
                    )
                elif resposta.status_code != 200:
                    raise TinyAPIError(
                        f"A API do Tiny respondeu HTTP {resposta.status_code}. "
                        f"Trecho da resposta: {resposta.text[:200]}"
                    )
                else:
                    try:
                        dados = resposta.json()
                    except ValueError:
                        raise TinyAPIError(
                            "A API do Tiny devolveu uma resposta que nao e JSON. "
                            f"Trecho: {resposta.text[:200]}"
                        )
                    retorno = dados.get("retorno") if isinstance(dados, dict) else None
                    if not isinstance(retorno, dict):
                        raise TinyAPIError("Resposta da API do Tiny em formato inesperado.")

                    erro = self._interpretar_erro(retorno)
                    if erro is None:
                        time.sleep(self.pausa)
                        return retorno
                    if isinstance(erro, TinyRateLimitError) and tentativa < self.max_tentativas:
                        ultimo_erro = erro
                    else:
                        raise erro

            if tentativa < self.max_tentativas:
                time.sleep(self.pausa * (2 ** tentativa) + 1)

        raise ultimo_erro or TinyAPIError("Nao foi possivel falar com a API do Tiny.")

    # -- endpoints ----------------------------------------------------------
    def pesquisar_pedidos(
        self,
        data_inicial: date,
        data_final: date,
        situacao: Optional[str] = None,
        ao_progredir: Optional[Callable[[int, Optional[int], int], None]] = None,
    ) -> List[Dict[str, Any]]:
        """Percorre todas as paginas de pedidos.pesquisa.php no periodo."""
        pedidos: List[Dict[str, Any]] = []
        pagina = 1
        total_paginas: Optional[int] = None

        while pagina <= MAX_PAGINAS:
            try:
                retorno = self._chamar(
                    "pedidos.pesquisa.php",
                    {
                        "dataInicial": formatar_data_api(data_inicial),
                        "dataFinal": formatar_data_api(data_final),
                        "pagina": pagina,
                        "situacao": situacao,
                    },
                )
            except TinySemRegistros:
                break

            if total_paginas is None:
                try:
                    total_paginas = int(retorno.get("numero_paginas") or 1)
                except (TypeError, ValueError):
                    total_paginas = None

            brutos = retorno.get("pedidos") or []
            if not brutos:
                break

            for envelope in brutos:
                pedido = envelope.get("pedido") if isinstance(envelope, dict) else None
                if isinstance(pedido, dict):
                    pedidos.append(pedido)

            if ao_progredir:
                ao_progredir(pagina, total_paginas, len(pedidos))

            if total_paginas is not None and pagina >= total_paginas:
                break
            pagina += 1

        return pedidos

    def obter_pedido(self, id_pedido: str) -> Dict[str, Any]:
        """Detalhe completo do pedido (itens e vendedor)."""
        retorno = self._chamar("pedido.obter.php", {"id": str(id_pedido)})
        pedido = retorno.get("pedido")
        if not isinstance(pedido, dict):
            raise TinyAPIError(f"A API nao retornou o detalhe do pedido {id_pedido}.")
        return pedido

    def testar_token(self) -> None:
        """Faz uma chamada barata so para validar o token."""
        hoje = date.today()
        try:
            self._chamar(
                "pedidos.pesquisa.php",
                {
                    "dataInicial": formatar_data_api(hoje),
                    "dataFinal": formatar_data_api(hoje),
                    "pagina": 1,
                },
            )
        except TinySemRegistros:
            return


# --------------------------------------------------------------------------
# Normalizacao (API -> formato do cache)
# --------------------------------------------------------------------------
def normalizar_pedido(
    detalhe: Dict[str, Any],
    resumo: Optional[Dict[str, Any]] = None,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """Converte o JSON do Tiny em um dicionario simples + lista de itens."""
    resumo = resumo or {}

    itens: List[Dict[str, Any]] = []
    quantidade_total = 0.0
    total_itens = 0.0
    for envelope in detalhe.get("itens") or []:
        item = envelope.get("item") if isinstance(envelope, dict) else envelope
        if not isinstance(item, dict):
            continue
        quantidade = para_float(item.get("quantidade"))
        unitario = para_float(item.get("valor_unitario"))
        quantidade_total += quantidade
        total_itens += quantidade * unitario
        itens.append(
            {
                "codigo": str(item.get("codigo") or "").strip(),
                "descricao": str(item.get("descricao") or "").strip(),
                "quantidade": quantidade,
                "valor_unitario": unitario,
                "valor_total": quantidade * unitario,
            }
        )

    cliente = detalhe.get("cliente")
    nome_cliente = cliente.get("nome", "") if isinstance(cliente, dict) else str(cliente or "")

    vendedor = _primeiro_texto(
        detalhe, ("nome_vendedor", "vendedor", "nome_vendedor_pedido")
    ) or _primeiro_texto(resumo, ("nome_vendedor", "vendedor"))

    total_pedido = para_float(detalhe.get("total_pedido") or resumo.get("valor"))
    total_produtos = para_float(detalhe.get("total_produtos")) or total_itens

    data_pedido = para_data(detalhe.get("data_pedido") or resumo.get("data_pedido"))

    pedido = {
        "id": str(detalhe.get("id") or resumo.get("id") or "").strip(),
        "numero": str(detalhe.get("numero") or resumo.get("numero") or "").strip(),
        "data": data_pedido.isoformat() if data_pedido else "",
        "situacao": normalizar_situacao(resumo.get("situacao") or detalhe.get("situacao")),
        "cliente": str(nome_cliente or "").strip(),
        "vendedor": vendedor or "Sem vendedor",
        "total_pedido": total_pedido,
        "total_produtos": total_produtos,
        "quantidade_itens": quantidade_total,
    }
    return pedido, itens
