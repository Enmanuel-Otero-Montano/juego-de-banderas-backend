"""Prueba vertical de Carrera contra un staging público.

Variables obligatorias:
    STAGING_API_URL
    STAGING_PLAYER_ONE_USERNAME / STAGING_PLAYER_ONE_PASSWORD
    STAGING_PLAYER_TWO_USERNAME / STAGING_PLAYER_TWO_PASSWORD

Opcional:
    STAGING_ANDROID_PACKAGE (por defecto com.enmanuelotero.atlasflags.staging)

El script no crea cuentas ni imprime credenciales. Crea una sala temporal,
conecta dos WebSockets, completa las 12 respuestas con el jugador uno y trata
de eliminar ambos miembros al terminar.
"""

from __future__ import annotations

import base64
from datetime import datetime, timezone
import json
import os
import sys
import time
from typing import Callable
from urllib.parse import urlparse
from uuid import uuid4

import requests
from websockets.sync.client import ClientConnection, connect


def required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"Falta la variable {name}.")
    return value


BASE_URL = required("STAGING_API_URL").rstrip("/")
if urlparse(BASE_URL).scheme != "https":
    raise SystemExit("STAGING_API_URL debe usar HTTPS.")

PLAYER_ONE = (required("STAGING_PLAYER_ONE_USERNAME"), required("STAGING_PLAYER_ONE_PASSWORD"))
PLAYER_TWO = (required("STAGING_PLAYER_TWO_USERNAME"), required("STAGING_PLAYER_TWO_PASSWORD"))
ANDROID_PACKAGE = os.environ.get(
    "STAGING_ANDROID_PACKAGE",
    "com.enmanuelotero.atlasflags.staging",
).strip()
HTTP_TIMEOUT = 15


def api_request(method: str, path: str, *, expected: tuple[int, ...] = (200,), **kwargs):
    response = requests.request(method, f"{BASE_URL}{path}", timeout=HTTP_TIMEOUT, **kwargs)
    if response.status_code not in expected:
        body = response.text[:500].replace("\n", " ")
        raise RuntimeError(f"{method} {path}: HTTP {response.status_code}: {body}")
    return response


def login(credentials: tuple[str, str]) -> dict:
    response = api_request(
        "POST",
        "/token",
        data={"username": credentials[0], "password": credentials[1]},
    )
    payload = response.json()
    if not payload.get("access_token") or not payload.get("user_id"):
        raise RuntimeError("La respuesta de /token no contiene access_token y user_id.")
    return payload


def auth(token_payload: dict) -> dict[str, str]:
    return {"Authorization": f"Bearer {token_payload['access_token']}"}


def encoded_token(token: str) -> str:
    return base64.urlsafe_b64encode(token.encode()).decode().rstrip("=")


def socket_url(room_id: str) -> str:
    parsed = urlparse(BASE_URL)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    prefix = parsed.path.rstrip("/")
    return f"{scheme}://{parsed.netloc}{prefix}/race-rooms/{room_id}/socket"


def receive_until(
    websocket: ClientConnection,
    predicate: Callable[[dict], bool],
    description: str,
    timeout: float = 12,
) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        remaining = max(0.1, deadline - time.monotonic())
        raw = websocket.recv(timeout=remaining)
        message = json.loads(raw)
        if message.get("type") == "error":
            raise RuntimeError(f"WebSocket devolvió error: {message}")
        if predicate(message):
            return message
    raise TimeoutError(f"No se recibió {description} dentro de {timeout} segundos.")


def parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def main() -> None:
    room: dict | None = None
    first: dict | None = None
    second: dict | None = None
    first_socket: ClientConnection | None = None
    second_socket: ClientConnection | None = None
    print("1/6 Verificando salud y Android App Links…")
    api_request("GET", "/health/live")
    api_request("GET", "/health/ready")
    assetlinks = api_request("GET", "/.well-known/assetlinks.json").json()
    packages = {entry.get("target", {}).get("package_name") for entry in assetlinks}
    if ANDROID_PACKAGE not in packages:
        raise RuntimeError(
            f"assetlinks.json no publica {ANDROID_PACKAGE}; publica: {sorted(packages)}"
        )

    try:
        print("2/6 Autenticando dos cuentas verificadas…")
        first = login(PLAYER_ONE)
        second = login(PLAYER_TWO)
        if first["user_id"] == second["user_id"]:
            raise RuntimeError("Las dos credenciales pertenecen a la misma cuenta.")

        print("3/6 Creando sala y uniendo al segundo jugador…")
        room = api_request(
            "POST",
            "/race-rooms",
            expected=(201,),
            headers=auth(first),
            json={"scope": "World", "difficulty": "easy"},
        ).json()
        joined = api_request(
            "POST",
            "/race-rooms/join",
            headers=auth(second),
            json={"code": room["code"]},
        ).json()
        if len(joined.get("members", [])) != 2:
            raise RuntimeError("La sala no contiene exactamente dos miembros.")

        print("4/6 Conectando WebSockets y marcando ambos jugadores como listos…")
        first_socket = connect(
            socket_url(room["id"]),
            subprotocols=["atlas-race-v1", f"auth.{encoded_token(first['access_token'])}"],
            open_timeout=HTTP_TIMEOUT,
            close_timeout=5,
        )
        second_socket = connect(
            socket_url(room["id"]),
            subprotocols=["atlas-race-v1", f"auth.{encoded_token(second['access_token'])}"],
            open_timeout=HTTP_TIMEOUT,
            close_timeout=5,
        )
        ready_message = json.dumps({"type": "ready", "ready": True, "protocol_version": 1})
        first_socket.send(ready_message)
        second_socket.send(ready_message)
        everyone_ready = lambda message: (
            message.get("type") == "lobby_state"
            and len(message.get("room", {}).get("members", [])) == 2
            and all(member.get("ready") for member in message["room"]["members"])
        )
        receive_until(first_socket, everyone_ready, "el lobby listo del jugador uno")
        receive_until(second_socket, everyone_ready, "el lobby listo del jugador dos")

        print("5/6 Iniciando y completando una carrera autoritativa de 12 banderas…")
        race_round = api_request(
            "POST",
            f"/race-rooms/{room['id']}/rounds",
            expected=(201,),
            headers=auth(first),
        ).json()
        wait_seconds = max(0.0, (parse_utc(race_round["starts_at"]) - datetime.now(timezone.utc)).total_seconds())
        time.sleep(wait_seconds + 0.1)
        plan = race_round.get("plan") or []
        if len(plan) != 12:
            raise RuntimeError(f"El plan contiene {len(plan)} preguntas en vez de 12.")
        final_result = None
        for sequence, question in enumerate(plan, start=1):
            event_id = f"staging-{uuid4()}"
            first_socket.send(json.dumps({
                "type": "answer",
                "protocol_version": 1,
                "event_id": event_id,
                "sequence": sequence,
                "country_code": question["country_code"],
                "selected_code": question["country_code"],
            }))
            response = receive_until(
                first_socket,
                lambda message, current=event_id: (
                    message.get("type") == "race_finished"
                    or (message.get("type") == "answer_result" and message.get("event_id") == current)
                ),
                f"la confirmación de la respuesta {sequence}",
            )
            if sequence < len(plan) and response.get("type") != "answer_result":
                raise RuntimeError(f"La carrera terminó antes de la respuesta {len(plan)}.")
            if response.get("type") == "race_finished":
                final_result = response
        if not final_result or final_result.get("winner_user_id") != first["user_id"]:
            raise RuntimeError("La carrera no terminó con el jugador uno como ganador.")
        receive_until(
            second_socket,
            lambda message: message.get("type") == "race_finished",
            "el resultado difundido al jugador dos",
        )
        print("6/6 Smoke test aprobado: REST, WebSocket, plan, respuestas y ganador funcionan.")
    finally:
        for websocket in (first_socket, second_socket):
            if websocket:
                try:
                    websocket.close()
                except Exception:
                    pass
        if room and first and second:
            time.sleep(0.2)
            for player in (second, first):
                try:
                    api_request(
                        "POST",
                        f"/race-rooms/{room['id']}/leave",
                        expected=(204,),
                        headers=auth(player),
                    )
                except Exception as error:
                    print(f"Aviso: no se pudo limpiar un miembro de la sala temporal: {error}", file=sys.stderr)


if __name__ == "__main__":
    main()
