# Módulo de Inteligência Artificial Gemini & Fallback (Guia Turístico e Culinária)

import re
from typing import Any

import httpx
from google import genai
from google.genai import types
from google.genai.errors import APIError

from config import GEMINI_KEY

# ==============================================================================
# 👤 RESPONSABILIDADE DO ALUNO 2: Inteligência Artificial (Gemini AI) & Fallback
# ==============================================================================


def limpar_formato_texto(texto: str) -> str:
    """Remove marcações residuais de markdown (** ou *), hashtags, crases e saudações, mantendo apenas emojis."""
    texto = re.sub(r"```(?:text|markdown)?", "", texto, flags=re.IGNORECASE)
    texto = re.sub(r"[*#`]", "", texto)
    texto = re.sub(r"^\s*(olá|ola|oi)[^\n]*[,:!]\s*", "", texto, flags=re.IGNORECASE)
    return re.sub(r"\n{3,}", "\n\n", texto).strip()


def _normalizar_destino(destino: str) -> str:
    """Remove UF do nome do destino e preserva o nome da cidade."""
    cidade = re.sub(r"\s*-\s*[A-Z]{2}\s*$", "", str(destino or "")).strip()
    return cidade or "o destino"


def _guia_fallback(
    destino: str,
    clima: dict[str, str] | None = None,
    percurso: dict[str, str] | None = None,
) -> str:
    clima = clima or {}
    percurso = percurso or {}
    dados_clima = ", ".join(
        f"{nome}: {clima[chave]}"
        for chave, nome in (
            ("temperatura", "temperatura"),
            ("umidade", "umidade"),
            ("vento", "vento"),
        )
        if clima.get(chave) and clima[chave] != "N/D"
    )
    observacao_clima = f" Dados recebidos: {dados_clima}." if dados_clima else ""
    duracao = percurso.get("tempo", "")
    observacao_viagem = f" Tempo estimado de viagem: {duracao}." if duracao else ""
    cidade = _normalizar_destino(destino)
    return (
        "🏛️ Atrações\n"
        f"- Explore os principais pontos turísticos e símbolos de {cidade}.\n"
        f"- Reserve tempo para o centro histórico, as praças e os mirantes mais tradicionais de {cidade}.\n"
        f"- Ajuste a rota conforme o que mais lhe interessa: cultura, praias, museus ou vistas panorâmicas.\n\n"
        "🍽️ Gastronomia\n"
        f"- Experimente pratos típicos da culinária local de {cidade}.\n"
        "- Procure opções em mercados, restaurantes tradicionais e feiras locais para viver a gastronomia da região.\n\n"
        "💡 Dica de ouro\n"
        f"Planeje os horários das atrações considerando o tempo de viagem e o clima local.{observacao_viagem}\n\n"
        "🌤️ Considerando o clima\n"
        f"Considere as condições meteorológicas informadas antes de sair.{observacao_clima}"
    )


def obter_guia_destino_com_diagnostico(
    destino: str,
    origem: str = "",
    clima: dict[str, str] | None = None,
    percurso: dict[str, str] | None = None,
) -> tuple[str, dict[str, Any]]:
    """Invoca o Gemini com os dados da viagem e timeout HTTP de aproximadamente 6s.

    Em caso de falha da API, aciona o gerador de contingência em texto puro.
    Retorna a tupla (texto_guia, diagnostico_metadados).
    """
    modelo = "gemini-3.6-flash"
    diagnostico: dict[str, Any] = {
        "status": "fallback", "modelo": modelo, "fallback_utilizado": True, "mensagem": ""
    }
    if not GEMINI_KEY:
        diagnostico["mensagem"] = "Chave Gemini ausente"
        return _guia_fallback(destino, clima, percurso), diagnostico

    clima = clima or {}
    percurso = percurso or {}
    dados_prompt = (
        f"Origem: {origem or 'não informada'}\n"
        f"Destino: {destino}\n"
        f"Distância: {percurso.get('distancia', 'não informada')}\n"
        f"Duração estimada: {percurso.get('tempo', 'não informada')}\n"
        f"Temperatura: {clima.get('temperatura', 'não informada')}\n"
        f"Condição climática: não fornecida pelos dados disponíveis\n"
        f"Umidade: {clima.get('umidade', 'não informada')}\n"
        f"Velocidade do vento: {clima.get('vento', 'não informada')}"
    )
    prompt = (
        "Crie um guia turístico e gastronômico curto e útil para o destino informado.\n\n"
        f"Dados da viagem:\n{dados_prompt}\n\n"
        "O guia deve conter 3 atrações turísticas relevantes, 2 comidas ou pratos típicos da região, "
        "1 dica cultural ou de viagem, considerar as condições meteorológicas recebidas e o tempo de "
        "deslocamento, evitar inventar informações muito específicas, deixar claro quando uma informação "
        "for apenas uma sugestão, usar português do Brasil e alguns emojis sem exagero. "
        "Não afirme uma condição climática que não esteja entre os dados recebidos.\n\n"
        "Retorne texto simples, estruturado exatamente desta forma:\n"
        "🏛️ Atrações\n- ...\n- ...\n- ...\n\n"
        "🍽️ Gastronomia\n- ...\n- ...\n\n"
        "💡 Dica de ouro\n...\n\n"
        "🌤️ Considerando o clima\n...\n\n"
        "Não retorne Markdown complexo, JSON ou código."
    )

    try:
        cliente = genai.Client(
            api_key=GEMINI_KEY,
            http_options=types.HttpOptions(timeout=6000),
        )
        resposta = cliente.models.generate_content(model=modelo, contents=prompt)
        texto = limpar_formato_texto(str(resposta.text or ""))
        if not texto:
            raise ValueError("Resposta vazia")
        diagnostico.update({"status": "sucesso", "fallback_utilizado": False})
        return texto, diagnostico
    except (APIError, httpx.HTTPError, RuntimeError, TimeoutError, ValueError, OSError) as erro:
        codigo = getattr(erro, "code", None) or getattr(erro, "status_code", None)
        if codigo == 429:
            mensagem = "Limite de requisições Gemini atingido (HTTP 429)"
        elif codigo in (401, 403):
            mensagem = f"Chave Gemini inválida ou sem permissão (HTTP {codigo})"
        elif isinstance(erro, TimeoutError) or "timeout" in str(erro).casefold():
            mensagem = "Tempo limite da requisição Gemini excedido"
        elif isinstance(erro, ConnectionError) or "connect" in str(erro).casefold():
            mensagem = "Falha de conexão com a API Gemini"
        elif isinstance(erro, APIError):
            mensagem = f"Erro da API Gemini: {erro}"
        else:
            mensagem = str(erro) or type(erro).__name__
        diagnostico["mensagem"] = mensagem[:200]
        return _guia_fallback(destino, clima, percurso), diagnostico


def obter_guia_destino(
    destino: str,
    origem: str = "",
    clima: dict[str, str] | None = None,
    percurso: dict[str, str] | None = None,
) -> str:
    """Wrapper utilitário que retorna apenas o texto do guia."""
    texto, _ = obter_guia_destino_com_diagnostico(destino, origem, clima, percurso)
    return texto
