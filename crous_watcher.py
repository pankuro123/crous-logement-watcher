#!/usr/bin/env python3
"""Surveille les nouvelles offres d'une recherche publique CROUS.

Le script ne se connecte pas et ne réserve rien. Il lit uniquement la page de
recherche publique, mémorise les offres déjà vues et affiche les nouvelles.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import ssl
import sys
import time
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_URL = (
    "https://trouverunlogement.lescrous.fr/tools/47/search?"
    "bounds=2.0644554826719124_49.045969758350786_"
    "2.8074059221250374_48.62655511310289"
)
MINIMUM_INTERVAL_SECONDS = 60
RECOMMENDED_INTERVAL_SECONDS = 300


class SearchResultParser(HTMLParser):
    """Extrait la réponse JSON injectée par SvelteKit dans la page CROUS."""

    def __init__(self) -> None:
        super().__init__()
        self._capture = False
        self._parts: list[str] = []
        self.payloads: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "script":
            return
        attributes = dict(attrs)
        if (
            "data-sveltekit-fetched" in attributes
            and (attributes.get("data-url") or "").startswith("/api/fr/search/")
        ):
            self._capture = True
            self._parts = []

    def handle_data(self, data: str) -> None:
        if self._capture:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._capture:
            self.payloads.append("".join(self._parts))
            self._capture = False
            self._parts = []


def fetch_search_results(url: str, timeout: int = 30) -> list[dict[str, Any]]:
    """Télécharge la page publique puis retourne ses offres visibles."""
    request = Request(
        url,
        headers={
            "Accept": "text/html,application/xhtml+xml",
            "User-Agent": "CrousLogementWatcher/1.0 (personal availability monitor)",
        },
    )
    with urlopen(request, timeout=timeout) as response:
        source = response.read().decode("utf-8", errors="replace")

    parser = SearchResultParser()
    parser.feed(source)
    if not parser.payloads:
        raise ValueError("Résultats de recherche introuvables dans la page CROUS.")

    # La page peut contenir plusieurs scripts SvelteKit : on prend celui qui
    # contient bien le tableau results.items.
    for payload in parser.payloads:
        outer = json.loads(html.unescape(payload))
        body = outer.get("body")
        result = json.loads(body) if isinstance(body, str) else body
        items = result.get("results", {}).get("items") if isinstance(result, dict) else None
        if isinstance(items, list):
            return [item for item in items if isinstance(item, dict)]
    raise ValueError("Le format des résultats CROUS a changé.")


def accommodation_id(item: dict[str, Any]) -> str:
    """Construit une clé stable même si le site renomme son champ d'identifiant."""
    for key in ("id", "accommodationId", "reference", "code"):
        if item.get(key) not in (None, ""):
            return f"{key}:{item[key]}"
    stable_data = json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "hash:" + hashlib.sha256(stable_data.encode("utf-8")).hexdigest()


def accommodation_label(item: dict[str, Any]) -> str:
    """Produit un libellé lisible, tout en restant tolérant au format de l'API."""
    candidates: list[Any] = [
        item.get("name"),
        item.get("label"),
        item.get("title"),
        item.get("accommodationName"),
    ]
    residence = item.get("residence")
    if isinstance(residence, dict):
        candidates.extend([residence.get("name"), residence.get("label")])
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return f"Offre {accommodation_id(item)}"


def load_seen(state_path: Path) -> set[str] | None:
    if not state_path.exists():
        return None
    try:
        raw = json.loads(state_path.read_text(encoding="utf-8"))
        seen = raw.get("seen_ids", [])
        return {value for value in seen if isinstance(value, str)}
    except (OSError, json.JSONDecodeError):
        print("État précédent illisible : une nouvelle initialisation sera faite.", file=sys.stderr)
        return None


def save_seen(state_path: Path, seen_ids: set[str]) -> bool:
    """Enregistre l'état uniquement s'il change, pour limiter les commits GitHub."""
    previous_ids = load_seen(state_path)
    if previous_ids == seen_ids:
        return False
    state_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "seen_ids": sorted(seen_ids),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    state_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return True


def read_env_value(name: str, env_path: Path = Path(".env")) -> str | None:
    """Lit une variable de .env sans dépendance externe."""
    if value := os.environ.get(name):
        return value
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            key, separator, value = line.partition("=")
            if separator and key.strip() == name:
                return value.strip().strip('"').strip("'") or None
    except OSError:
        pass
    return None


def telegram_api(method: str, payload: dict[str, Any]) -> Any:
    """Appelle l'API Telegram sans jamais afficher le token dans les erreurs."""
    token = read_env_value("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN est absent du fichier .env.")

    encoded_payload = json.dumps(payload).encode("utf-8")
    request = Request(
        f"https://api.telegram.org/bot{token}/{method}",
        data=encoded_payload,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "CrousLogementWatcher/1.0",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=30) as response:
            answer = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        try:
            details = json.loads(error.read().decode("utf-8"))
            description = details.get("description", "erreur inconnue")
        except (OSError, ValueError, json.JSONDecodeError):
            description = "erreur inconnue"
        raise RuntimeError(f"Telegram a refusé la demande : {description}") from error
    except (URLError, ssl.SSLError, TimeoutError, OSError) as error:
        raise RuntimeError("Impossible de joindre Telegram de façon sécurisée.") from error

    if not answer.get("ok"):
        raise RuntimeError(f"Telegram a refusé la demande : {answer.get('description', 'erreur inconnue')}")
    return answer.get("result")


def telegram_chat_id() -> int:
    """Utilise le chat_id enregistré, sinon cherche un message récent du bot."""
    configured_chat_id = read_env_value("TELEGRAM_CHAT_ID")
    if configured_chat_id:
        try:
            return int(configured_chat_id)
        except ValueError as error:
            raise RuntimeError("TELEGRAM_CHAT_ID doit être un nombre entier.") from error

    updates = telegram_api("getUpdates", {})
    if not isinstance(updates, list):
        raise RuntimeError("Réponse Telegram inattendue.")
    for update in reversed(updates):
        if not isinstance(update, dict):
            continue
        message = update.get("message") or update.get("edited_message")
        chat = message.get("chat") if isinstance(message, dict) else None
        if isinstance(chat, dict) and chat.get("type") == "private" and isinstance(chat.get("id"), int):
            return chat["id"]
    raise RuntimeError("TELEGRAM_CHAT_ID est absent : ouvre le bot Telegram, appuie sur Démarrer et envoie-lui « Bonjour », puis réessaie.")


def send_telegram_notification(title: str, message: str, url: str) -> None:
    """Envoie une notification au dernier téléphone ayant démarré le bot."""
    telegram_api(
        "sendMessage",
        {
            "chat_id": telegram_chat_id(),
            "text": f"{title}\n\n{message}\n\n{url}",
            "disable_web_page_preview": True,
        },
    )


def check_once(url: str, state_path: Path) -> int:
    items = fetch_search_results(url)
    current_ids = {accommodation_id(item) for item in items}
    previous_ids = load_seen(state_path)

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if not items:
        send_telegram_notification(
            "Vérification logement CROUS",
            "Aucun logement n'est disponible dans la zone surveillée pour le moment.",
            url,
        )
        save_seen(state_path, current_ids)
        print(f"[{timestamp}] Aucun logement visible. Notification Telegram envoyée.")
        return 0

    if previous_ids is None:
        save_seen(state_path, current_ids)
        print(f"[{timestamp}] Initialisation : {len(items)} logement(s) visible(s).")
        for item in items:
            print(f"  - {accommodation_label(item)}")
        return 0

    new_items = [item for item in items if accommodation_id(item) not in previous_ids]
    if new_items:
        print(f"\a[{timestamp}] NOUVELLE(S) OFFRE(S) : {len(new_items)}")
        for item in new_items:
            print(f"  - {accommodation_label(item)}")
        print(f"  Ouvrir immédiatement : {url}")
        names = ", ".join(accommodation_label(item) for item in new_items[:3])
        if len(new_items) > 3:
            names += f" et {len(new_items) - 3} autre(s)"
        send_telegram_notification(
            "Nouveau logement CROUS détecté !",
            f"{len(new_items)} nouvelle(s) offre(s) : {names}",
            url,
        )
        save_seen(state_path, current_ids)
        print("  Notification Telegram envoyée.")
        return len(new_items)

    save_seen(state_path, current_ids)
    print(f"[{timestamp}] Rien de nouveau ({len(items)} logement(s) visible(s)).")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Surveille une recherche publique Trouver un logement CROUS.")
    parser.add_argument("--url", default=DEFAULT_URL, help="URL de recherche CROUS à surveiller.")
    parser.add_argument(
        "--interval",
        type=int,
        default=RECOMMENDED_INTERVAL_SECONDS,
        help=f"Délai entre vérifications en secondes (minimum {MINIMUM_INTERVAL_SECONDS}, défaut 300).",
    )
    parser.add_argument("--state", type=Path, default=Path("data/crous_seen.json"), help="Fichier d'état local.")
    parser.add_argument("--once", action="store_true", help="Effectue une seule vérification puis s'arrête.")
    parser.add_argument("--test-notification", action="store_true", help="Envoie un test Telegram puis s'arrête.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.test_notification:
        try:
            send_telegram_notification(
                "Test détecteur CROUS",
                "Telegram fonctionne : les alertes de logement arriveront dans cette discussion.",
                args.url,
            )
        except RuntimeError as error:
            print(f"Erreur de notification : {error}", file=sys.stderr)
            return 1
        print("Notification Telegram de test envoyée.")
        return 0
    if not args.once and args.interval < MINIMUM_INTERVAL_SECONDS:
        print(f"L'intervalle doit être d'au moins {MINIMUM_INTERVAL_SECONDS} secondes.", file=sys.stderr)
        return 2

    failures = 0
    while True:
        try:
            check_once(args.url, args.state)
            failures = 0
        except (HTTPError, URLError, TimeoutError, ValueError, OSError, RuntimeError) as error:
            failures += 1
            print(f"Erreur de vérification : {error}", file=sys.stderr)

        if args.once:
            return 1 if failures else 0

        # Après plusieurs échecs, on réduit encore la charge sur le site.
        delay = args.interval if failures < 3 else max(args.interval, 900)
        time.sleep(delay)


if __name__ == "__main__":
    raise SystemExit(main())
