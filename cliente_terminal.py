"""Cliente de terminal para a Corrida FastAPI.

Este cliente não usa HTML, JavaScript, Leaflet nem interface gráfica.
Ele consome:

- GET /api/status: consulta o estado público da corrida;
- WebSocket /ws: envia comandos e recebe atualizações em tempo real.

Execute este arquivo em dois terminais diferentes para conectar os dois
jogadores da mesma corrida.
"""

import argparse
import asyncio
import json
import time
from urllib.request import urlopen

import websockets


async def consultar_status(api_url: str) -> dict:
    """Consulta o endpoint HTTP REST /api/status."""
    with urlopen(f"{api_url}/api/status", timeout=5) as resposta:
        return json.loads(resposta.read().decode("utf-8"))


async def imprimir_status(api_url: str, titulo: str) -> None:
    """Mostra no terminal o estado atual retornado pela API REST."""
    estado = await consultar_status(api_url)
    jogo = estado["game_state"]
    p1 = jogo["player_1"]
    p2 = jogo["player_2"]

    print(f"\n[{titulo}] GET /api/status")
    print(
        f"corrida iniciada={jogo['started']} | "
        f"finalizada={jogo['game_over']} | vencedor={jogo['winner']}"
    )
    print(f"P1: cliques={p1['clicks']} conectado={p1['connected']}")
    print(f"P2: cliques={p2['clicks']} conectado={p2['connected']}")


async def receber_ate(websocket, condicao, descricao: str) -> dict:
    """Aguarda mensagens WebSocket até que uma condição seja satisfeita."""
    while True:
        mensagem = json.loads(await websocket.recv())
        tipo = mensagem.get("type", "estado")

        if tipo == "route_log":
            print(f"[LOG API] {mensagem['detail']}")
        elif tipo == "route_ready":
            print(f"[API] Rota pronta para {mensagem['player']}")
        elif mensagem.get("waiting"):
            print(f"[API] {mensagem.get('status', 'Aguardando outro jogador...')}")
        elif mensagem.get("ready"):
            print("[API] Corrida autorizada!")
        elif mensagem.get("player_1") and mensagem.get("player_2"):
            p1 = mensagem["player_1"]
            p2 = mensagem["player_2"]
            print(
                f"[API] P1={p1['clicks']} cliques | "
                f"P2={p2['clicks']} cliques"
            )
        elif mensagem.get("game_over"):
            print(f"[API] Corrida encerrada. Vencedor: {mensagem.get('winner')}")

        if condicao(mensagem):
            return mensagem

        print(f"[aguardando] {descricao}")


async def receber_estado_da_corrida(websocket) -> dict:
    """Ignora logs intermediários e retorna o próximo estado da partida."""
    while True:
        mensagem = json.loads(await websocket.recv())

        if mensagem.get("type") == "route_log":
            print(f"[LOG API] {mensagem['detail']}")
            continue

        if mensagem.get("player_1") and mensagem.get("player_2"):
            p1 = mensagem["player_1"]
            p2 = mensagem["player_2"]
            print(
                f"[API] P1={p1['clicks']} cliques | "
                f"P2={p2['clicks']} cliques"
            )
            return mensagem

        if mensagem.get("game_over"):
            return mensagem


async def receber_identificacao(websocket) -> dict:
    """Aguarda a mensagem de identificação do jogador."""
    while True:
        mensagem = json.loads(await websocket.recv())

        if mensagem.get("type") == "route_log":
            print(f"[LOG API] {mensagem['detail']}")
            continue

        if mensagem.get("role"):
            return mensagem


async def executar_corrida(api_url: str, quantidade_pushes: int, intervalo: float) -> None:
    """Conecta um jogador e executa uma corrida automática pelo terminal."""
    ws_url = api_url.replace("https://", "wss://").replace("http://", "ws://") + "/ws"

    print(f"Conectando à API WebSocket: {ws_url}")

    async with websockets.connect(ws_url) as websocket:
        # A primeira mensagem informa a identidade e o ponto de saída sorteado.
        identificacao = await receber_identificacao(websocket)
        player = identificacao["role"]
        spawn = [identificacao["spawn_lat"], identificacao["spawn_lon"]]

        print(f"Você foi registrado como: {player}")
        print(f"Ponto de saída sorteado: {spawn}")

        # Para eliminar a dependência de mapa e OSRM, usamos a rota reserva.
        await websocket.send(
            json.dumps(
                {
                    "type": "init_route_fallback",
                    "player": player,
                }
            )
        )
        print("[CLIENTE] init_route_fallback enviado")

        # A corrida somente começa quando os dois clientes enviarem suas rotas.
        await receber_ate(
            websocket,
            lambda mensagem: mensagem.get("ready") is True,
            "aguardando o segundo jogador e as duas rotas",
        )

        # Consulta REST antes de começar a enviar comandos pelo WebSocket.
        await imprimir_status(api_url, "antes dos PUSH")

        print(f"\nIniciando {quantidade_pushes} comandos PUSH...")
        for numero in range(1, quantidade_pushes + 1):
            await websocket.send(json.dumps({"type": "push"}))
            print(f"[CLIENTE] push {numero}/{quantidade_pushes} enviado")

            # O servidor envia o novo estado por broadcast.
            mensagem = await receber_estado_da_corrida(websocket)
            if mensagem.get("game_over"):
                print(f"Resultado recebido: vencedor={mensagem.get('winner')}")
                break

            await asyncio.sleep(intervalo)

        await asyncio.sleep(0.2)
        await imprimir_status(api_url, "depois dos PUSH")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Cliente de terminal da Corrida FastAPI"
    )
    parser.add_argument(
        "--api-url",
        default="http://127.0.0.1:8000",
        help="URL base da API (padrão: http://127.0.0.1:8000)",
    )
    parser.add_argument(
        "--pushes",
        type=int,
        default=50,
        help="Quantidade máxima de PUSHs enviados (padrão: 50)",
    )
    parser.add_argument(
        "--intervalo",
        type=float,
        default=0.08,
        help="Intervalo entre PUSHs, em segundos (padrão: 0.08)",
    )
    args = parser.parse_args()

    if args.pushes < 1:
        parser.error("--pushes deve ser maior que zero")
    if args.intervalo < 0:
        parser.error("--intervalo não pode ser negativo")

    try:
        asyncio.run(executar_corrida(args.api_url.rstrip("/"), args.pushes, args.intervalo))
    except ConnectionRefusedError:
        print("Não foi possível conectar. O servidor FastAPI está executando?")
    except websockets.exceptions.InvalidStatus as erro:
        print(f"A API rejeitou a conexão WebSocket: {erro}")
    except KeyboardInterrupt:
        print("\nCliente interrompido pelo usuário.")


if __name__ == "__main__":
    main()
