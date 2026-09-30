"""Aplicação Flask Principal - Guia do Turista Inteligente (API Gateway em Python)."""

import json
import os
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx
from flask import Flask, jsonify, redirect, render_template, request, session, url_for

from config import (
    DATA_DIR,
    ESTADOS_BRASIL,
    GOOGLE_CLIENT_ID,
    PORT,
    VIAGENS_FILE,
)
from planejamento import obter_guia_destino_com_diagnostico
from services import (
    buscar_coordenadas,
    obter_clima,
    obter_percurso,
    verificar_token_google,
)

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "guia-turista-secret-key-2026-python")


def agora_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

# Controle de concorrência para leitura e escrita segura no arquivo JSON
DATA_DIR.mkdir(parents=True, exist_ok=True)
lock_arquivo_json = threading.Lock()

# Armazenamento volátil de roteiros em memória para sessões de visitantes
viagens_visitante_memoria: dict[str, list[dict[str, Any]]] = {}

# Controle de concorrência e idempotência contra cliques duplicados
requisicoes_ativas: set[str] = set()
requisicoes_recentes: dict[str, float] = {}
lock_requisicoes = threading.Lock()


# ==============================================================================
# 👤 RESPONSABILIDADE DO ALUNO 4: Persistência JSON, Sanitização e Manipulação
# ==============================================================================


def sanitizar_entrada(texto: str, max_len: int = 80) -> str:
    """Higieniza entradas de texto removendo tags HTML, caracteres de controle e espaços extras."""
    texto = re.sub(r"<[^>]*>", "", str(texto))
    texto = re.sub(r"[\x00-\x1f\x7f]", "", texto)
    return re.sub(r"\s+", " ", texto).strip()[:max_len]


def criar_estrutura_padrao_viagens() -> dict[str, Any]:
    """Retorna a estrutura inicial do payload JSON de viagens com metadados e provedores."""
    return {
        "versao_schema": "1.0",
        "descricao": "Base consolidada de roteiros turísticos e telemetria por usuário",
        "atualizado_em": agora_iso(),
        "total_usuarios": 0,
        "total_roteiros": 0,
        "provedores": {
            "geocoding": "Open-Meteo Geocoding API",
            "previsao_tempo": "Open-Meteo Forecast API",
            "roteamento": "OSRM Routing Engine",
            "inteligencia_artificial": "Google Gemini (gemini-3.6-flash)",
        },
        "usuarios": {},
    }


def carregar_dados_viagens_json() -> dict[str, Any]:
    """Lê a base completa de viagens de static/data/viagens.json de forma thread-safe com lock_arquivo_json."""
    with lock_arquivo_json:
        return _carregar_dados_sem_lock()


def _carregar_dados_sem_lock() -> dict[str, Any]:
    try:
        with VIAGENS_FILE.open("r", encoding="utf-8") as arquivo:
            dados = json.load(arquivo)
        return dados if isinstance(dados, dict) else criar_estrutura_padrao_viagens()
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return criar_estrutura_padrao_viagens()


def salvar_dados_viagens_json(dados_completos: dict[str, Any]) -> None:
    """Persiste a base hierárquica em static/data/viagens.json com lock_arquivo_json e indentação de 2 espaços."""
    with lock_arquivo_json:
        _salvar_dados_sem_lock(dados_completos)


def _salvar_dados_sem_lock(dados_completos: dict[str, Any]) -> None:
    VIAGENS_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporario = VIAGENS_FILE.with_suffix(".tmp")
    with temporario.open("w", encoding="utf-8") as arquivo:
        json.dump(dados_completos, arquivo, ensure_ascii=False, indent=2)
    temporario.replace(VIAGENS_FILE)


def obter_viagens_usuario(user_id: str) -> list[dict[str, Any]]:
    """Recupera a lista de roteiros: da memória para visitantes ou do arquivo JSON para logados."""
    if user_id.startswith("demo:"):
        return list(viagens_visitante_memoria.get(user_id, []))
    dados = carregar_dados_viagens_json()
    return list(dados.get("usuarios", {}).get(user_id, {}).get("roteiros", []))


def adicionar_viagem_usuario(
    user_id: str,
    item: dict[str, Any],
    perfil_usuario: dict[str, Any] | None = None,
) -> None:
    """Adiciona um novo roteiro: na memória para visitante ou grava no JSON para usuário logado."""
    if user_id.startswith("demo:"):
        viagens_visitante_memoria.setdefault(user_id, []).insert(0, item)
        return
    with lock_arquivo_json:
        dados = _carregar_dados_sem_lock()
        usuarios = dados.setdefault("usuarios", {})
        agora = agora_iso()
        usuario = usuarios.setdefault(
            user_id,
            {"perfil": perfil_usuario or {}, "metadados": {"criado_em": agora}, "roteiros": []},
        )
        usuario.setdefault("roteiros", []).insert(0, item)
        usuario["metadados"]["total_roteiros"] = len(usuario["roteiros"])
        usuario["metadados"]["atualizado_em"] = agora
        dados["total_usuarios"] = len(usuarios)
        dados["total_roteiros"] = sum(len(valor.get("roteiros", [])) for valor in usuarios.values())
        dados["atualizado_em"] = agora
        _salvar_dados_sem_lock(dados)


def remover_viagem_usuario(user_id: str, viagem_id: str) -> None:
    """Remove um roteiro específico pelo ID."""
    if user_id.startswith("demo:"):
        viagens_visitante_memoria[user_id] = [
            item for item in viagens_visitante_memoria.get(user_id, []) if item.get("id") != viagem_id
        ]
        return
    with lock_arquivo_json:
        dados = _carregar_dados_sem_lock()
        usuario = dados.get("usuarios", {}).get(user_id)
        if not usuario:
            return
        usuario["roteiros"] = [item for item in usuario.get("roteiros", []) if item.get("id") != viagem_id]
        usuario.setdefault("metadados", {})["total_roteiros"] = len(usuario["roteiros"])
        dados["total_roteiros"] = sum(len(valor.get("roteiros", [])) for valor in dados["usuarios"].values())
        dados["atualizado_em"] = agora_iso()
        _salvar_dados_sem_lock(dados)


# ==============================================================================
# 👤 RESPONSABILIDADE DO ALUNO 3: Backend Gateway, Sessões, Rotas & Idempotência
# ==============================================================================


@app.route("/", methods=["GET"])
def index():
    """Renderiza a página principal (SSR com Jinja2)."""
    usuario = session.get("usuario")
    viagens = obter_viagens_usuario(usuario["id"]) if usuario else []
    return render_template(
        "index.html", usuario=usuario, viagens=viagens, ufs=sorted(ESTADOS_BRASIL), client_id=GOOGLE_CLIENT_ID
    )


@app.route("/auth/google/callback", methods=["GET", "POST"])
def google_callback():
    """Recebe a credencial JWT do Google e valida 100% no Python."""
    token = sanitizar_entrada(request.form.get("credential", ""), 5000)
    with httpx.Client() as client:
        usuario = verificar_token_google(client, token)
    if usuario is None:
        return redirect(url_for("index"))
    session["usuario"] = usuario
    return redirect(url_for("index"))


@app.route("/auth/demo", methods=["GET", "POST"])
def login_demo():
    """Modo Visitante para desenvolvimento e testes locais."""
    user_id = f"demo:{uuid.uuid4().hex}"
    viagens_visitante_memoria[user_id] = []
    session["usuario"] = {"id": user_id, "nome": "Viajante Convidado", "email": "Modo demonstração", "foto": ""}
    return redirect(url_for("index"))


@app.route("/auth/logout", methods=["GET", "POST"])
def logout():
    """Encerra a sessão e descarta a memória de visitante."""
    usuario = session.pop("usuario", None)
    if usuario and usuario.get("id", "").startswith("demo:"):
        viagens_visitante_memoria.pop(usuario["id"], None)
    return redirect(url_for("index"))


@app.route("/viagens/criar", methods=["POST"])
def criar_viagem():
    """Processa o formulário de criação com deduplicação (locks) e orquestração de APIs."""
    usuario = session.get("usuario")
    if not usuario:
        return redirect(url_for("index"))
    chave = usuario["id"]
    with lock_requisicoes:
        agora = time.time()
        if chave in requisicoes_ativas or agora - requisicoes_recentes.get(chave, 0) < 1.0:
            return redirect(url_for("index"))
        requisicoes_ativas.add(chave)
        requisicoes_recentes[chave] = agora
    try:
        origem = sanitizar_entrada(request.form.get("origem_cidade", ""))
        destino = sanitizar_entrada(request.form.get("destino_cidade", ""))
        uf_origem = sanitizar_entrada(request.form.get("origem_uf", ""), 2).upper()
        uf_destino = sanitizar_entrada(request.form.get("destino_uf", ""), 2).upper()
        if not origem or not destino or uf_origem not in ESTADOS_BRASIL or uf_destino not in ESTADOS_BRASIL:
            return redirect(url_for("index"))
        with httpx.Client() as client:
            lat_o, lon_o, uf_o_real = buscar_coordenadas(client, origem, uf_origem)
            lat_d, lon_d, uf_d_real = buscar_coordenadas(client, destino, uf_destino)
            clima = obter_clima(client, lat_d, lon_d)
            percurso = obter_percurso(client, lat_o, lon_o, lat_d, lon_d)
        destino_formatado = f"{destino} - {uf_d_real or uf_destino}"
        guia, diagnostico_ia = obter_guia_destino_com_diagnostico(
            destino_formatado,
            origem=f"{origem} - {uf_o_real or uf_origem}",
            clima=clima,
            percurso=percurso,
        )
        status_servicos = {
            "geocoding": "sucesso" if (lat_o, lon_o) != (0.0, 0.0) and (lat_d, lon_d) != (0.0, 0.0) else "fallback",
            "clima": "sucesso" if all(valor != "N/D" for valor in clima.values()) else "fallback",
            "percurso": "sucesso" if percurso.get("modal") != "indisponível" else "fallback",
            "inteligencia_artificial": diagnostico_ia,
        }
        item = {
            "id": uuid.uuid4().hex[:8], "criado_em": agora_iso(),
            "origem": f"{origem} - {uf_o_real or uf_origem}", "destino": destino_formatado,
            "geolocalizacao": {"origem": {"cidade": origem, "uf": uf_o_real or uf_origem, "latitude": lat_o, "longitude": lon_o},
                               "destino": {"cidade": destino, "uf": uf_d_real or uf_destino, "latitude": lat_d, "longitude": lon_d}},
            "telemetria": {"clima": clima, "percurso": percurso},
            "dicas_destino": guia,
            "metadados": {"status_requisicao": "sucesso", "status_servicos": status_servicos},
        }
        adicionar_viagem_usuario(chave, item, usuario)
    finally:
        with lock_requisicoes:
            requisicoes_ativas.discard(chave)
    return redirect(url_for("index"))


@app.route("/viagens/deletar/<string:viagem_id>", methods=["POST"])
def deletar_viagem(viagem_id: str):
    """Exclui um roteiro da lista do usuário."""
    usuario = session.get("usuario")
    if usuario:
        remover_viagem_usuario(usuario["id"], sanitizar_entrada(viagem_id, 40))
    return redirect(url_for("index"))


# ==============================================================================
# 👤 RESPONSABILIDADE DO ALUNO 4: Endpoint REST e Error Handlers Globais
# ==============================================================================


@app.route("/viagens/json", methods=["GET"])
@app.route("/api/viagens/json", methods=["GET"])
@app.route("/api/viagens", methods=["GET"])
def ver_viagens_json():
    """Retorna a base consolidada de static/data/viagens.json com suporte dinâmico a visitantes."""
    dados = carregar_dados_viagens_json()
    usuario = session.get("usuario")
    if usuario and usuario["id"].startswith("demo:"):
        roteiros = obter_viagens_usuario(usuario["id"])
        dados["usuarios"] = {
            usuario["id"]: {
                "perfil": usuario,
                "metadados": {"total_roteiros": len(roteiros)},
                "roteiros": roteiros,
            }
        }
        dados["total_usuarios"] = 1
        dados["total_roteiros"] = len(roteiros)
    return jsonify(dados)


@app.errorhandler(405)
def metodo_nao_permitido(error):
    """Fallback para acessos GET em rotas POST (ex: digitar /viagens/criar na barra de endereços)."""
    return redirect(url_for("index"))


@app.errorhandler(404)
def pagina_nao_encontrada(error):
    """Fallback para rotas inexistentes redirecionando suavemente para a página principal."""
    return redirect(url_for("index"))


if __name__ == "__main__":
    print(f"🌍 Servidor Flask Guia do Turista rodando em http://localhost:{PORT}")
    app.run(host="0.0.0.0", port=PORT, debug=True)
