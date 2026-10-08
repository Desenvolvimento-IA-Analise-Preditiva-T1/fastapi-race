import json
import random
import datetime
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

# -----------------------------------------------------------------------------
# APLICAÇÃO FASTAPI
# -----------------------------------------------------------------------------
# FastAPI cria a aplicação web e também gera documentação automática em:
#   - http://127.0.0.1:8000/docs      (Swagger UI)
#   - http://127.0.0.1:8000/redoc     (ReDoc)
#
# Neste exemplo há dois estilos de API:
#   1. HTTP/REST: GET / e GET /api/status
#   2. WebSocket: /ws, para eventos bidirecionais em tempo real
# -----------------------------------------------------------------------------
app = FastAPI(
    title="Corrida para a Ponte Hercílio Luz - FastAPI Showcase",
    description=(
        "Exemplo didático de uma API FastAPI com rota HTTP, "
        "endpoint REST e comunicação WebSocket."
    ),
    version="1.0.0",
)

# Coordenadas do objetivo: Cabeceira da Ponte Hercílio Luz em Florianópolis
PONTE_HERCILIO_LUZ = [-27.5938, -48.5661]

# Pontos de largada possíveis. Um ponto é escolhido aleatoriamente a cada
# nova conexão WebSocket, permitindo que cada rodada comece em um local
# diferente de Florianópolis.
SPAWN_POINTS = {
    "player_1": [
        [-27.5849, -48.5451],  # Beiramar Shopping
        [-27.5894, -48.5517],  # Centro / Avenida Beira-Mar Norte
        [-27.5788, -48.5417],  # Agronômica
        [-27.5962, -48.5489],  # Prainha
    ],
    "player_2": [
        [-27.6015, -48.5645],  # Parque de Coqueiros
        [-27.6052, -48.5713],  # Itaguaçu
        [-27.5939, -48.5796],  # Abraão
        [-27.6082, -48.5570],  # Estreito
    ],
}

# Rotas urbanas de contingência pelas ruas de Florianópolis
FALLBACK_ROUTES = {
    "player_1": [
        [-27.5849, -48.5451], [-27.5845, -48.5468], [-27.5843, -48.5492],
        [-27.5841, -48.5518], [-27.5844, -48.5542], [-27.5850, -48.5566],
        [-27.5860, -48.5590], [-27.5874, -48.5612], [-27.5891, -48.5630],
        [-27.5912, -48.5645], [-27.5926, -48.5654], [-27.5938, -48.5661]
    ],
    "player_2": [
        [-27.6015, -48.5645], [-27.6003, -48.5662], [-27.5989, -48.5678],
        [-27.5975, -48.5694], [-27.5962, -48.5710], [-27.5972, -48.5690],
        [-27.5983, -48.5665], [-27.5992, -48.5635], [-27.5997, -48.5608],
        [-27.5985, -48.5595], [-27.5968, -48.5615], [-27.5952, -48.5635],
        [-27.5942, -48.5652], [-27.5938, -48.5661]
    ]
}


def get_random_spawn(player_key: str) -> list[float]:
    """Escolhe um ponto de saída aleatório para o jogador.

    Um pequeno deslocamento adicional evita que duas rodadas iniciadas no
    mesmo ponto tenham exatamente as mesmas coordenadas.
    """
    base_lat, base_lon = random.choice(SPAWN_POINTS[player_key])
    return [
        round(base_lat + random.uniform(-0.0003, 0.0003), 6),
        round(base_lon + random.uniform(-0.0003, 0.0003), 6),
    ]


def get_fallback_route(player_key: str, spawn_coords: list[float]) -> list[list[float]]:
    """Retorna a rota reserva começando no ponto aleatório sorteado."""
    route = [point[:] for point in FALLBACK_ROUTES[player_key]]
    route[0] = spawn_coords[:]
    return route

def interpolate_route(coords, target_steps=50):
    """Gera exatamente 50 passos para que cada clique avance de forma precisa."""
    if not coords or len(coords) < 2:
        return [PONTE_HERCILIO_LUZ] * (target_steps + 1)
    
    result = []
    total_segments = len(coords) - 1
    for step in range(target_steps + 1):
        t = step / target_steps
        raw_idx = t * total_segments
        idx = min(int(raw_idx), total_segments - 1)
        sub_t = raw_idx - idx
        
        lat = coords[idx][0] + (coords[idx + 1][0] - coords[idx][0]) * sub_t
        lon = coords[idx][1] + (coords[idx + 1][1] - coords[idx][1]) * sub_t
        result.append([round(lat, 6), round(lon, 6)])
    
    result[-1] = PONTE_HERCILIO_LUZ
    return result

# Estado do jogo em memória no servidor FastAPI
game_state = {
    "game_over": False,
    "winner": None,
    "started": False,
    "player_1": {"lat": 0, "lon": 0, "route": [], "current_index": 0, "clicks": 0, "max_clicks": 50, "ws": None},
    "player_2": {"lat": 0, "lon": 0, "route": [], "current_index": 0, "clicks": 0, "max_clicks": 50, "ws": None},
}

async def send_route_log(route_name: str, method: str, detail: str):
    """Envia log ESTRITAMENTE quando uma rota do FastAPI é acionada."""
    now = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
    payload = {
        "type": "route_log",
        "timestamp": now,
        "route": route_name,
        "method": method,
        "detail": detail
    }
    await broadcast(payload)

@app.get("/")
async def get_game() -> HTMLResponse:
    p_lat = PONTE_HERCILIO_LUZ[0]
    p_lon = PONTE_HERCILIO_LUZ[1]

    page_content = f"""
    <!DOCTYPE html>
    <html lang="pt-BR">
    <head>
        <meta charset="UTF-8" />
        <title>Corrida para a Ponte Hercílio Luz - Monitor de Rotas FastAPI</title>
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        
        <!-- CDNs Oficiais do Leaflet -->
        <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
        <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>

        <style>
            * {{ box-sizing: border-box; }}
            body {{ margin: 0; padding: 0; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background: #090d16; color: #f8fafc; overflow: hidden; }}
            #game-container {{ display: flex; flex-direction: column; height: 100vh; position: relative; }}
            
            /* Cabeçalho */
            #header {{ padding: 10px 18px; background: #0f172a; border-bottom: 1px solid #1e293b; display: flex; justify-content: space-between; align-items: center; z-index: 10; }}
            #header h1 {{ margin: 0; font-size: 16px; color: #fbbf24; display: flex; align-items: center; gap: 8px; }}
            #header-right {{ display: flex; align-items: center; gap: 12px; }}
            .badge {{ font-size: 11px; padding: 4px 8px; border-radius: 6px; font-weight: 700; }}
            .badge-conn {{ background: #1e293b; color: #94a3b8; border: 1px solid #334155; }}
            
            /* Layout Principal: Mapa + Console de Rotas do FastAPI */
            #main-content {{ display: flex; flex-grow: 1; height: calc(100vh - 140px); position: relative; }}
            #map {{ flex: 1; height: 100%; }}
            
            /* Painel: Exibe SOMENTE execução de rotas do FastAPI */
            #logs-panel {{ width: 440px; background: #030712; border-left: 1px solid #1f2937; display: flex; flex-direction: column; height: 100%; z-index: 5; }}
            #logs-header {{ padding: 10px 14px; background: #0b0f19; border-bottom: 1px solid #1f2937; display: flex; justify-content: space-between; align-items: center; }}
            #logs-header-title {{ font-size: 12px; font-weight: 800; color: #38bdf8; display: flex; align-items: center; gap: 6px; }}
            #logs-header-sub {{ font-size: 10px; color: #94a3b8; font-weight: normal; }}
            #logs-list {{ flex-grow: 1; overflow-y: auto; padding: 10px; font-family: 'Consolas', 'Courier New', monospace; font-size: 11px; line-height: 1.55; }}
            .log-entry {{ margin-bottom: 6px; padding: 6px 8px; border-radius: 5px; background: #0c1220; border-left: 3px solid #38bdf8; word-break: break-word; }}
            .log-entry.ws-route {{ border-left-color: #a855f7; }}
            .log-entry.http-route {{ border-left-color: #38bdf8; }}
            .log-time {{ color: #64748b; margin-right: 6px; font-size: 10px; }}
            .log-route-badge {{ font-weight: 800; padding: 1px 5px; border-radius: 3px; font-size: 10px; margin-right: 6px; }}
            .badge-http {{ background: rgba(56, 189, 248, 0.15); color: #38bdf8; border: 1px solid rgba(56, 189, 248, 0.3); }}
            .badge-ws {{ background: rgba(168, 85, 247, 0.15); color: #c084fc; border: 1px solid rgba(168, 85, 247, 0.3); }}
            .log-detail {{ color: #e2e8f0; }}

            /* Barra de Controles */
            #controls {{ height: 80px; padding: 12px 20px; background: #0f172a; border-top: 1px solid #1e293b; display: flex; justify-content: space-between; align-items: center; z-index: 10; }}
            #status-block {{ display: flex; flex-direction: column; justify-content: center; }}
            #status {{ font-size: 15px; font-weight: 700; color: #f8fafc; }}
            #click-count {{ font-size: 12px; color: #94a3b8; font-family: monospace; margin-top: 3px; }}

            /* Botão de PUSH */
            #push-btn {{ height: 54px; padding: 0 45px; font-size: 20px; font-weight: 800; background: linear-gradient(135deg, #22c55e, #16a34a); color: white; border: 2px solid #4ade80; border-radius: 10px; cursor: pointer; transition: transform 0.05s ease, background 0.1s ease; box-shadow: 0 4px 14px rgba(34,197,94,0.4); text-shadow: 0 1px 2px rgba(0,0,0,0.5); }}
            #push-btn:active {{ transform: scale(0.95); background: #15803d; }}
            #push-btn:disabled {{ background: #334155; border-color: #475569; color: #94a3b8; cursor: not-allowed; box-shadow: none; }}

            /* Modal Centralizado de Fim de Jogo */
            #victory-modal {{ display: none; position: absolute; inset: 0; background: rgba(0,0,0,0.8); backdrop-filter: blur(5px); z-index: 1000; align-items: center; justify-content: center; }}
            #victory-card {{ background: #0f172a; border: 2px solid #fbbf24; border-radius: 16px; padding: 28px; width: 90%; max-width: 440px; text-align: center; box-shadow: 0 20px 40px rgba(0,0,0,0.8); }}
            #victory-title {{ font-size: 22px; font-weight: 900; margin-bottom: 8px; }}
            #victory-desc {{ font-size: 13px; color: #94a3b8; margin-bottom: 24px; line-height: 1.5; }}
            
            #restart-btn {{ width: 100%; padding: 14px; font-size: 16px; font-weight: 800; background: linear-gradient(135deg, #f59e0b, #d97706); color: #090d16; border: none; border-radius: 10px; cursor: pointer; transition: all 0.15s ease; box-shadow: 0 4px 12px rgba(245,158,11,0.4); }}
            #restart-btn:hover {{ background: #fbbf24; transform: translateY(-2px); }}
            #restart-btn:active {{ transform: scale(0.97); }}
            #restart-btn:disabled {{ opacity: 0.5; cursor: not-allowed; transform: none; }}
            #anti-spam-hint {{ font-size: 11px; color: #64748b; margin-top: 8px; }}

            @media (max-width: 768px) {{
                #logs-panel {{ display: none; }}
            }}
        </style>
    </head>
    <body>
        <div id="game-container">
            <!-- Cabeçalho -->
            <div id="header">
                <h1>
                    <span>🌉</span> Corrida para a Ponte Hercílio Luz
                    <span style="font-size: 10px; background: #0284c7; color: #fff; padding: 2px 6px; border-radius: 4px; font-weight: bold; margin-left: 6px;">FastAPI Routes Only</span>
                </h1>
                <div id="header-right">
                    <span id="role-badge" class="badge" style="background: #1e293b; color: #e2e8f0;">Aguardando entrada...</span>
                    <span id="connection-badge" class="badge badge-conn">Conectando...</span>
                </div>
            </div>

            <!-- Conteúdo Principal: Mapa e Painel de Rotas do FastAPI -->
            <div id="main-content">
                <div id="map"></div>
                
                <!-- Console: SOMENTE quando as rotas do FastAPI são chamadas -->
                <div id="logs-panel">
                    <div id="logs-header">
                        <div>
                            <div id="logs-header-title">⚡ EXECUÇÃO DE ROTAS (FASTAPI)</div>
                            <div id="logs-header-sub">HTTP: /, /api/status, /api/health · WS: /ws</div>
                        </div>
                        <button onclick="clearLogs()" style="background: none; border: 1px solid #334155; color: #94a3b8; font-size: 10px; padding: 2px 6px; border-radius: 4px; cursor: pointer;">Limpar</button>
                    </div>
                    <div id="logs-list">
                        <!-- Log inicial da rota HTTP GET / que renderizou esta página -->
                        <div class="log-entry http-route">
                            <span class="log-time">{datetime.datetime.now().strftime("%H:%M:%S")}</span>
                            <span class="log-route-badge badge-http">@app.get("/")</span>
                            <span class="log-detail">Rota raiz executada -> HTMLResponse 200 OK enviada ao navegador.</span>
                        </div>
                    </div>
                </div>
            </div>

            <!-- Controles -->
            <div id="controls">
                <div id="status-block">
                    <div id="status">Conectando à API FastAPI...</div>
                    <div id="click-count">Aguardando início da corrida</div>
                </div>
                <div>
                    <button id="push-btn" onclick="sendPush()" disabled>PUSH! ⚡</button>
                </div>
            </div>

            <!-- Modal Exclusivo de Vitória -->
            <div id="victory-modal">
                <div id="victory-card">
                    <div style="font-size: 44px; margin-bottom: 6px;">🏆</div>
                    <div id="victory-title">FIM DE CORRIDA!</div>
                    <div id="victory-desc">Chegada consagrada na cabeceira da Ponte Hercílio Luz em Florianópolis!</div>
                    
                    <button id="restart-btn" onclick="sendRestart()">
                        JOGAR NOVAMENTE 🔄
                    </button>
                    <div id="anti-spam-hint">Proteção anti-clique ativa: liberado em 1.2s</div>
                </div>
            </div>
        </div>

        <script>
            let ws;
            let myRole = null;
            let p1Marker, p2Marker, targetMarker;
            let mapInstance;
            let p1RouteLine, p2RouteLine;
            const targetLat = {p_lat};
            const targetLon = {p_lon};

            // Adiciona log SOMENTE para rotas do FastAPI
            function addRouteLog(route, method, detail, timeStr) {{
                const logsList = document.getElementById("logs-list");
                const entry = document.createElement("div");
                const isWs = method === "WEBSOCKET" || route.includes("websocket");
                entry.className = "log-entry " + (isWs ? "ws-route" : "http-route");
                
                const time = timeStr || new Date().toTimeString().split(' ')[0];
                const badgeClass = isWs ? "badge-ws" : "badge-http";
                
                entry.innerHTML = `
                    <span class="log-time">${{time}}</span>
                    <span class="log-route-badge ${{badgeClass}}">${{route}}</span>
                    <span class="log-detail">${{detail}}</span>
                `;
                logsList.appendChild(entry);
                logsList.scrollTop = logsList.scrollHeight;
            }}

            function clearLogs() {{
                document.getElementById("logs-list").innerHTML = "";
            }}

            function createCustomMarker(emoji, label, color) {{
                return L.divIcon({{
                    className: '',
                    html: `
                        <div style="display: flex; flex-direction: column; align-items: center; transform: translate(-50%, -100%);">
                            <span style="background: ${{color}}; color: white; padding: 2px 7px; font-size: 10px; font-weight: bold; border-radius: 4px; white-space: nowrap; box-shadow: 0 2px 6px rgba(0,0,0,0.5);">${{label}}</span>
                            <div style="font-size: 28px; line-height: 1; filter: drop-shadow(0 2px 4px rgba(0,0,0,0.5));">${{emoji}}</div>
                        </div>
                    `,
                    iconSize: [0, 0]
                }});
            }}

            function initMap() {{
                mapInstance = L.map('map').setView([targetLat, targetLon], 14);

                L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
                    attribution: '© OpenStreetMap contributors',
                    maxZoom: 19
                }}).addTo(mapInstance);

                targetMarker = L.marker([targetLat, targetLon], {{
                    icon: createCustomMarker('🏁', 'Ponte Hercílio Luz', '#eab308')
                }}).addTo(mapInstance);

                p1Marker = L.marker([targetLat, targetLon], {{
                    icon: createCustomMarker('🏎️', 'P1 (Ilha)', '#ef4444')
                }}).addTo(mapInstance);

                p2Marker = L.marker([targetLat, targetLon], {{
                    icon: createCustomMarker('🚙', 'P2 (Continente)', '#3b82f6')
                }}).addTo(mapInstance);

                connectWebSocket();
            }}

            window.onload = function() {{
                if (typeof L !== 'undefined') {{
                    initMap();
                }}
            }};

            function fetchRouteFromOSRM(player, start, end) {{
                const url = "https://router.project-osrm.org/route/v1/driving/" + start[1] + "," + start[0] + ";" + end[1] + "," + end[0] + "?overview=full&geometries=geojson";
                
                fetch(url)
                    .then(r => {{
                        if(!r.ok) throw new Error("HTTP " + r.status);
                        return r.json();
                    }})
                    .then(data => {{
                        if (data && data.routes && data.routes.length > 0 && data.routes[0].geometry) {{
                            const coordinates = data.routes[0].geometry.coordinates.map(coord => [coord[1], coord[0]]);
                            renderRouteAndSend(player, coordinates);
                        }} else {{
                            throw new Error("Sem coordenadas");
                        }}
                    }})
                    .catch(err => {{
                        ws.send(JSON.stringify({{"type": "init_route_fallback", "player": player}}));
                    }});
            }}

            function renderRoute(player, coordinates) {{
                const color = player === "player_1" ? "#ef4444" : "#3b82f6";
                if(player === "player_1" && p1RouteLine) mapInstance.removeLayer(p1RouteLine);
                if(player === "player_2" && p2RouteLine) mapInstance.removeLayer(p2RouteLine);

                const line = L.polyline(coordinates, {{
                    color: color, 
                    weight: 5, 
                    opacity: 0.8, 
                    dashArray: '6, 8'
                }}).addTo(mapInstance);

                if(player === "player_1") p1RouteLine = line;
                else p2RouteLine = line;
            }}

            function renderRouteAndSend(player, coordinates) {{
                renderRoute(player, coordinates);
                ws.send(JSON.stringify({{"type": "init_route", "player": player, "coords": coordinates}}));
            }}

            function connectWebSocket() {{
                const protocol = window.location.protocol === "https:" ? "wss://" : "ws://";
                const wsUrl = protocol + window.location.host + "/ws";

                ws = new WebSocket(wsUrl);

                ws.onopen = function() {{
                    document.getElementById("connection-badge").innerText = "● WebSocket Conectado";
                    document.getElementById("connection-badge").style.color = "#4ade80";
                }};

                ws.onmessage = function(event) {{
                    const data = JSON.parse(event.data);

                    // Mensagens exclusivas de execução de rota do FastAPI
                    if (data.type === "route_log") {{
                        addRouteLog(data.route, data.method, data.detail, data.timestamp);
                        return;
                    }}
                    
                    if (data.role) {{
                        myRole = data.role;
                        const isP1 = myRole === "player_1";
                        const roleBadge = document.getElementById("role-badge");
                        roleBadge.innerText = isP1 ? "Você: P1 (Ilha - saída aleatória)" : "Você: P2 (Continente - saída aleatória)";
                        roleBadge.style.background = isP1 ? "#ef4444" : "#3b82f6";
                        roleBadge.style.color = "#fff";

                        document.getElementById("status").innerText = "Traçando rota a partir de um ponto aleatório até a Ponte...";
                        
                        if(isP1) p1Marker.setLatLng([data.spawn_lat, data.spawn_lon]);
                        else p2Marker.setLatLng([data.spawn_lat, data.spawn_lon]);

                        mapInstance.fitBounds([[data.spawn_lat, data.spawn_lon], [targetLat, targetLon]], {{ padding: [60, 60] }});
                        fetchRouteFromOSRM(myRole, [data.spawn_lat, data.spawn_lon], [targetLat, targetLon]);
                        return;
                    }}

                    if (data.type === "route_ready") {{
                        renderRoute(data.player, data.coords);
                        return;
                    }}

                    if (data.waiting) {{
                        document.getElementById("status").innerText = "Aguardando Jogador 2 entrar na corrida...";
                        return;
                    }}

                    if (data.ready) {{
                        document.getElementById("status").innerText = "⚠️ LARGADA AUTORIZADA! Aperte PUSH ou tecle [ESPAÇO]!";
                        document.getElementById("push-btn").disabled = false;
                        document.getElementById("victory-modal").style.display = "none";
                        return;
                    }}

                    if (data.player_1 && data.player_2) {{
                        p1Marker.setLatLng([data.player_1.lat, data.player_1.lon]);
                        p2Marker.setLatLng([data.player_2.lat, data.player_2.lon]);
                        
                        if (myRole) {{
                            const myClicks = data[myRole].clicks;
                            document.getElementById("click-count").innerText = `Seus Cliques: ${{myClicks}}/50  |  P1: ${{data.player_1.clicks}}  |  P2: ${{data.player_2.clicks}}`;
                        }}
                    }}

                    if (data.game_over) {{
                        document.getElementById("push-btn").disabled = true;
                        
                        const modal = document.getElementById("victory-modal");
                        const title = document.getElementById("victory-title");
                        const desc = document.getElementById("victory-desc");
                        const restartBtn = document.getElementById("restart-btn");
                        const hint = document.getElementById("anti-spam-hint");

                        if (data.winner === myRole) {{
                            title.innerHTML = "🎉 VOCÊ VENCEU!";
                            title.style.color = "#4ade80";
                            desc.innerText = "Parabéns! Seu veículo foi o primeiro a atravessar a cabeceira da Ponte Hercílio Luz!";
                        }} else if (data.winner === "DRAW") {{
                            title.innerHTML = "🏁 EMPATE TÉCNICO!";
                            title.style.color = "#fbbf24";
                            desc.innerText = "Ambos os corredores cruzaram a marca ao mesmo tempo.";
                        }} else {{
                            title.innerHTML = "❌ VOCÊ PERDEU!";
                            title.style.color = "#f87171";
                            desc.innerText = "O adversário chegou na Ponte Hercílio Luz primeiro.";
                        }}

                        modal.style.display = "flex";

                        // Proteção anti-clique acidental: desativa o botão por 1.2 segundos
                        restartBtn.disabled = true;
                        let cooldown = 1.2;
                        hint.innerText = "Proteção anti-clique: liberando em " + cooldown.toFixed(1) + "s...";
                        
                        const interval = setInterval(() => {{
                            cooldown -= 0.3;
                            if (cooldown <= 0) {{
                                clearInterval(interval);
                                restartBtn.disabled = false;
                                hint.innerText = "Pronto! Clique para iniciar uma nova rodada.";
                                hint.style.color = "#4ade80";
                            }} else {{
                                hint.innerText = "Proteção anti-clique: liberando em " + cooldown.toFixed(1) + "s...";
                            }}
                        }}, 300);
                    }}
                }};

                ws.onclose = function() {{
                    document.getElementById("connection-badge").innerText = "● Desconectado";
                    document.getElementById("connection-badge").style.color = "#ef4444";
                    document.getElementById("status").innerText = "Conexão WebSocket finalizada pelo FastAPI.";
                    document.getElementById("push-btn").disabled = true;
                }};
            }}

            function sendPush() {{
                if (ws && ws.readyState === WebSocket.OPEN) {{
                    ws.send(JSON.stringify({{"type": "push"}}));
                }}
            }}

            function sendRestart() {{
                if (ws && ws.readyState === WebSocket.OPEN) {{
                    ws.send(JSON.stringify({{"type": "restart"}}));
                }}
            }}

            // Suporte para tecla Espaço e Enter
            window.addEventListener("keydown", function(e) {{
                if (e.code === "Space" || e.key === "Enter") {{
                    if (!document.getElementById("push-btn").disabled) {{
                        e.preventDefault();
                        sendPush();
                    }}
                }}
            }});
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=page_content)


def get_public_game_state() -> dict[str, Any]:
    """Monta uma versão segura e serializável do estado do jogo.

    O estado interno contém ``WebSocket`` e listas de rotas. Esses objetos são
    úteis no servidor, mas não devem ser enviados diretamente em uma resposta
    JSON. A API pública expõe apenas dados necessários para o cliente. Os
    jogadores ficam no nível principal porque esse é o formato que o JavaScript
    já consome nas mensagens enviadas pelo WebSocket.
    """
    p1_clicks = game_state["player_1"]["clicks"]
    p2_clicks = game_state["player_2"]["clicks"]

    # ``winner`` só recebe valor quando a corrida termina. Durante a corrida,
    # ``leader`` informa quem está na frente naquele momento.
    if p1_clicks > p2_clicks:
        current_leader = "player_1"
    elif p2_clicks > p1_clicks:
        current_leader = "player_2"
    else:
        current_leader = "DRAW"

    public_state: dict[str, Any] = {
        "game_over": game_state["game_over"],
        "winner": game_state["winner"],
        "leader": None if game_state["game_over"] else current_leader,
        "started": game_state["started"],
    }

    for player_id in ("player_1", "player_2"):
        public_state[player_id] = {
                "lat": game_state[player_id]["lat"],
                "lon": game_state[player_id]["lon"],
                "clicks": game_state[player_id]["clicks"],
                "max_clicks": game_state[player_id]["max_clicks"],
                "connected": game_state[player_id]["ws"] is not None,
                "route_ready": bool(game_state[player_id]["route"]),
            }

    return public_state


# Endpoint REST adicional para demonstração didática de rotas HTTP do FastAPI.
# Ao acessar /docs, esta rota aparece automaticamente no contrato OpenAPI.
@app.get(
    "/api/status",
    summary="Consultar o estado público da corrida",
    tags=["API REST"],
)
async def get_status() -> dict[str, Any]:
    """Rota HTTP GET que retorna o estado público do servidor."""
    public_state = get_public_game_state()
    detail = (
        "Status 200 OK -> "
        f"started: {public_state['started']}, "
        f"game_over: {public_state['game_over']}"
    )
    await send_route_log("@app.get('/api/status')", "GET", detail)
    return {"status": "ok", "game_state": public_state}


@app.get("/api/health", summary="Verificar se a API está disponível", tags=["API REST"])
async def health_check() -> dict[str, str]:
    """Endpoint simples de health check para monitoramento."""
    await send_route_log("@app.get('/api/health')", "GET", "Health check executado com sucesso.")
    return {"status": "ok", "service": "fastapi-race"}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """Gerencia uma conexão WebSocket de um jogador.

    Diferentemente de uma rota HTTP, esta função permanece ativa em um loop:
    recebe mensagens do navegador, atualiza o jogo e transmite eventos para os
    dois jogadores conectados.
    """
    # 1) Aceita o handshake WebSocket.
    await websocket.accept()
    
    player_key = None
    if game_state["player_1"]["ws"] is None:
        player_key = "player_1"
    elif game_state["player_2"]["ws"] is None:
        player_key = "player_2"
    
    if player_key is None:
        await send_route_log("@app.websocket('/ws')", "WEBSOCKET", "Conexão rejeitada: Sala cheia (código 1008).")
        await websocket.close(code=1008, reason="Sala cheia")
        return

    # Sorteia a largada somente no momento em que o jogador entra na sala.
    spawn_lat, spawn_lon = get_random_spawn(player_key)
    
    game_state[player_key]["ws"] = websocket
    game_state[player_key]["lat"] = spawn_lat
    game_state[player_key]["lon"] = spawn_lon
    game_state[player_key]["clicks"] = 0
    game_state[player_key]["current_index"] = 0
    game_state[player_key]["route"] = []

    player_label = "Jogador 1 (Ilha)" if player_key == "player_1" else "Jogador 2 (Continente)"
    await send_route_log("@app.websocket('/ws')", "WEBSOCKET", f"Handshake aceito via websocket.accept() -> {player_label} registrado.")

    await websocket.send_text(json.dumps({
        "role": player_key, 
        "spawn_lat": spawn_lat, 
        "spawn_lon": spawn_lon
    }))

    other_key = "player_2" if player_key == "player_1" else "player_1"
    if game_state[other_key]["ws"] is None:
        await websocket.send_text(json.dumps({"waiting": True}))
        await send_route_log("@app.websocket('/ws')", "WEBSOCKET", "Aguardando 2º jogador conectar para liberar a rota.")
    else:
        await send_route_log("@app.websocket('/ws')", "WEBSOCKET", "Ambos os jogadores conectados! Aguardando envio das rotas.")

    try:
        while True:
            # Mensagem recebida na rota WebSocket
            msg = await websocket.receive_text()
            data = json.loads(msg)
            msg_type = data.get("type")

            # As mensagens abaixo são o "contrato" da API WebSocket:
            # init_route, init_route_fallback, push e restart.

            if msg_type == "init_route":
                p_target = data["player"]
                raw_coords = data.get("coords", [])
                sampled_route = interpolate_route(raw_coords, target_steps=50)
                game_state[p_target]["route"] = sampled_route
                
                await send_route_log("@app.websocket('/ws')", "WEBSOCKET", f"Mensagem 'init_route' processada para {p_target} ({len(sampled_route)} waypoints interpolados).")
                await broadcast({"type": "route_ready", "player": p_target, "coords": sampled_route})
                await check_and_start_race()

            elif msg_type == "init_route_fallback":
                p_target = data["player"]
                spawn_coords = [
                    game_state[p_target]["lat"],
                    game_state[p_target]["lon"],
                ]
                sampled_route = interpolate_route(
                    get_fallback_route(p_target, spawn_coords),
                    target_steps=50,
                )
                game_state[p_target]["route"] = sampled_route
                
                await send_route_log("@app.websocket('/ws')", "WEBSOCKET", f"Mensagem 'init_route_fallback' processada para {p_target} (trajeto pré-definido).")
                await broadcast({"type": "route_ready", "player": p_target, "coords": sampled_route})
                await check_and_start_race()

            elif msg_type == "push" and not game_state["game_over"] and game_state["started"]:
                player = game_state[player_key]
                
                if player["clicks"] < player["max_clicks"]:
                    player["clicks"] += 1
                    route = player["route"]
                    
                    if route and len(route) > 0:
                        target_idx = min(len(route) - 1, player["clicks"])
                        player["current_index"] = target_idx
                        player["lat"] = route[target_idx][0]
                        player["lon"] = route[target_idx][1]
                    
                    # Log da execução do push na rota WebSocket (no primeiro clique, a cada 10 cliques ou na vitória)
                    if player["clicks"] == 1 or player["clicks"] % 10 == 0 or player["clicks"] >= player["max_clicks"]:
                        await send_route_log("@app.websocket('/ws')", "WEBSOCKET", f"Mensagem 'push' processada para {player_key} -> Clicks: {player['clicks']}/50 | Pos: [{player['lat']}, {player['lon']}]")

                    if player["clicks"] >= player["max_clicks"] or player["current_index"] >= len(route) - 1:
                        game_state["game_over"] = True
                        game_state["winner"] = player_key
                        winner_label = "Jogador 1 (Ilha)" if player_key == "player_1" else "Jogador 2 (Continente)"
                        await send_route_log("@app.websocket('/ws')", "WEBSOCKET", f"Vitória confirmada pelo servidor para {winner_label}! Enviando 'game_over' via broadcast.")
                    
                    await broadcast_game_state()

            elif msg_type == "restart":
                await send_route_log("@app.websocket('/ws')", "WEBSOCKET", f"Mensagem 'restart' executada por {player_key} -> Resetando estado de jogo.")
                game_state["game_over"] = False
                game_state["winner"] = None
                game_state["started"] = False

                for pk in ["player_1", "player_2"]:
                    game_state[pk]["clicks"] = 0
                    game_state[pk]["current_index"] = 0
                    if game_state[pk]["route"]:
                        game_state[pk]["lat"] = game_state[pk]["route"][0][0]
                        game_state[pk]["lon"] = game_state[pk]["route"][0][1]

                await broadcast_game_state()
                await check_and_start_race()

    except WebSocketDisconnect:
        p_name = "Jogador 1" if player_key == "player_1" else "Jogador 2"
        await send_route_log("@app.websocket('/ws')", "WEBSOCKET", f"WebSocketDisconnect disparado: {p_name} encerrou a conexão.")
        game_state[player_key]["ws"] = None
        game_state[player_key]["route"] = []
        game_state["started"] = False
        
        if game_state[other_key]["ws"]:
            try:
                await game_state[other_key]["ws"].send_text(json.dumps({
                    "waiting": True,
                    "status": "O adversário desconectou-se. Aguardando novo desafiante..."
                }))
            except Exception:
                pass

async def check_and_start_race():
    """Inicia a corrida se ambos os jogadores tiverem enviado suas rotas."""
    p1 = game_state["player_1"]
    p2 = game_state["player_2"]
    
    if len(p1["route"]) > 0 and len(p2["route"]) > 0 and p1["ws"] and p2["ws"]:
        game_state["started"] = True
        game_state["game_over"] = False
        await send_route_log("@app.websocket('/ws')", "WEBSOCKET", "Condição de início satisfeita! Disparado broadcast 'ready: True' para ambos.")
        await broadcast({"ready": True})

async def broadcast(data_dict):
    payload = json.dumps(data_dict)
    for p_key in ["player_1", "player_2"]:
        ws = game_state[p_key]["ws"]
        if ws:
            try:
                await ws.send_text(payload)
            except Exception:
                pass

async def broadcast_game_state():
    # Reutiliza o mesmo formato público da API REST, evitando duplicação.
    await broadcast(get_public_game_state())

if __name__ == "__main__":
    import uvicorn
    print("\n" + "="*60)
    print(" 🌉 FastAPI Game: Corrida para a Ponte Hercílio Luz")
    print(" API HTTP:  http://127.0.0.1:8000")
    print(" Swagger:   http://127.0.0.1:8000/docs")
    print(" ReDoc:     http://127.0.0.1:8000/redoc")
    print(" WebSocket: ws://127.0.0.1:8000/ws")
    print("="*60 + "\n")
    uvicorn.run(app, host="0.0.0.0", port=8000)
