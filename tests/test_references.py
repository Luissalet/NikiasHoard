"""Looking a character up by the figure's name: which pages are asked, in which order, and what comes back."""

from __future__ import annotations

import httpx

from nikias_hoard import references


def test_names_and_pokemon_forms():
    assert references.clean_name("103-Alolan Exeggutor") == "Alolan Exeggutor" and references.clean_name("025_Pikachu") == "Pikachu"
    assert references.pokemon_slugs("103-Alolan Exeggutor", "Pokemon/Regional") == ["exeggutor-alola", "exeggutor"]
    assert references.pokemon_slugs("006 Charizard", "Pokemon/Gigantamax") == ["charizard-gmax", "charizard"]
    assert references.pokemon_slugs("Mr. Mime", "Pokemon") == ["mr-mime"]


def serve(monkeypatch, handler):
    real = httpx.Client
    monkeypatch.setattr(references.httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handler), **{k: v for k, v in kw.items() if k != "transport"}))


def test_a_collection_with_a_wiki_asks_it_by_title_and_sends_the_referer(monkeypatch):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.host, dict(request.url.params), request.headers.get("referer")))
        if request.url.path == "/api.php":
            assert request.url.params["titles"] == "Amy" and request.url.params["redirects"] == "1"
            return httpx.Response(200, json={"query": {"pages": {"7": {"title": "Amy Rose", "thumbnail": {"source": "https://static.example/amy.png"}}}}})
        return httpx.Response(200 if request.headers.get("referer") else 403, content=b"PNGDATA")
    serve(monkeypatch, handler)
    hit = references.find("Amy", "Sonic")
    assert hit["title"] == "Amy Rose" and hit["data"] == b"PNGDATA" and hit["page"] == "https://sonic.fandom.com/wiki/Amy_Rose"
    assert seen[0][0] == "sonic.fandom.com" and seen[-1][2] == "https://sonic.fandom.com/"


def test_pokemon_take_their_artwork_and_fall_back_to_the_plain_form(monkeypatch):
    asked = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(str(request.url))
        if request.url.path.endswith("/pokemon/exeggutor-alola"):
            return httpx.Response(404)
        if request.url.path.endswith("/pokemon/exeggutor"):
            return httpx.Response(200, json={"name": "exeggutor", "sprites": {"other": {"official-artwork": {"front_default": "https://art.example/103.png"}}}})
        return httpx.Response(200, content=b"ART")
    serve(monkeypatch, handler)
    hit = references.find("103-Alolan Exeggutor", "Pokemon/Regional")
    assert hit["title"] == "exeggutor" and hit["data"] == b"ART" and len(asked) == 3


def test_nothing_found_is_none_and_an_unknown_collection_asks_the_encyclopedia(monkeypatch):
    hosts = []

    def handler(request: httpx.Request) -> httpx.Response:
        hosts.append(request.url.host)
        return httpx.Response(200, json={"query": {"pages": {}}})
    serve(monkeypatch, handler)
    assert references.find("Nadie", "Cosas mías") is None and hosts == ["en.wikipedia.org"]
