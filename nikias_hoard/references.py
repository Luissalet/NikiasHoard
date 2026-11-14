"""A picture of the character when the user's folders have none.

The figure's name and its collection (the folder it sits in) are looked up where character art is published under
that name: the Pokémon artwork set for numbered Pokémon, the collection's own wiki when there is one, the encyclopedia
otherwise. What is found is kept in the project as one more reference, with the page it came from, and the user can
remove it like any other."""

from __future__ import annotations

import re
from typing import Any, Optional

import httpx

AGENT = {"User-Agent": "NikiasHoard/0.2 (local figure colouring studio)"}
# words of a collection's name -> the wiki that covers it
DEFAULT_WIKIS = {"sonic": "sonic.fandom.com", "animal crossing": "animalcrossing.fandom.com", "jojo": "jojo.fandom.com",
                 "marvel": "marvel.fandom.com", "pokemon masters": "bulbapedia.bulbagarden.net/w", "zelda": "zelda.fandom.com",
                 "mario": "mario.fandom.com", "kirby": "kirby.fandom.com", "digimon": "digimon.fandom.com", "dragon ball": "dragonball.fandom.com",
                 "one piece": "onepiece.fandom.com", "naruto": "naruto.fandom.com"}
FORMS = (("alolan", "alola"), ("galarian", "galar"), ("hisuian", "hisui"), ("paldean", "paldea"), ("gigantamax", "gmax"), ("mega", "mega"))


def clean_name(name: str) -> str:
    """The character in a figure's file name: no catalogue number, no underscores."""
    name = re.sub(r"^\s*\d+\s*[-_. ]\s*", "", name)
    return re.sub(r"[_\s]+", " ", name).strip()


def pokemon_slugs(name: str, group: str) -> list[str]:
    words = clean_name(name).lower().replace(".", "").replace("'", "").split()
    tags = [slug for word, slug in FORMS if word in words or word in group.lower()]
    base = "-".join(w for w in words if all(w != word for word, _ in FORMS))
    out = [f"{base}-{t}" for t in tags] + [base]
    return [s for s in dict.fromkeys(out) if s]


def _get(client: httpx.Client, url: str, **params) -> Any:
    r = client.get(url, params=params or None)
    r.raise_for_status()
    return r


def from_pokemon(client: httpx.Client, name: str, group: str) -> Optional[dict[str, Any]]:
    for slug in pokemon_slugs(name, group):
        try:
            data = _get(client, f"https://pokeapi.co/api/v2/pokemon/{slug}").json()
        except Exception:  # noqa: BLE001
            continue
        art = (((data.get("sprites") or {}).get("other") or {}).get("official-artwork") or {}).get("front_default")
        if art:
            return {"image": art, "title": data.get("name", slug), "page": f"https://pokeapi.co/api/v2/pokemon/{slug}"}
    return None


def from_wiki(client: httpx.Client, host: str, query: str, exact: bool = True) -> Optional[dict[str, Any]]:
    """The lead picture of the page called ``query`` (following redirects: "Amy" is "Amy Rose"), else of the first search hit."""
    base = f"https://{host}/api.php"
    site = host.split("/")[0]
    asks = ([dict(titles=query, redirects=1)] if exact else []) + [dict(generator="search", gsrsearch=query, gsrlimit=5)]
    for ask in asks:
        try:
            data = _get(client, base, action="query", prop="pageimages", piprop="thumbnail", pithumbsize=1000, format="json",
                        **({"pilicense": "any"} if "wikipedia" in host else {}), **ask).json()
        except Exception:  # noqa: BLE001
            continue
        pages = sorted(((data.get("query") or {}).get("pages") or {}).values(), key=lambda p: p.get("index", 0))
        for page in pages:
            src = (page.get("thumbnail") or {}).get("source")
            if src:
                return {"image": src, "title": page.get("title", ""), "page": f"https://{site}/wiki/{page.get('title', '').replace(' ', '_')}",
                        "referer": f"https://{site}/"}
    return None


def find(name: str, group: str, wikis: Optional[dict[str, str]] = None, timeout: float = 20.0) -> Optional[dict[str, Any]]:
    """{"data": picture bytes, "title", "page", "image"} or None when nothing was found."""
    who = clean_name(name)
    low = group.lower()
    table = {**DEFAULT_WIKIS, **{k.lower(): v for k, v in (wikis or {}).items()}}
    with httpx.Client(headers=AGENT, timeout=timeout, follow_redirects=True) as client:
        hit = None
        host = next((table[k] for k in sorted(table, key=len, reverse=True) if k in low), "")
        if "pokemon" in low.replace("é", "e") and not host:
            hit = from_pokemon(client, name, group) or from_wiki(client, "pokemon.fandom.com", who)
        if hit is None and host:
            hit = from_wiki(client, host, who)
        if hit is None:
            hit = from_wiki(client, "en.wikipedia.org/w", f"{who} {group} character".strip(), exact=False)
        if hit is None:
            return None
        try:
            hit["data"] = client.get(hit["image"], headers={"Referer": hit["referer"]} if hit.get("referer") else None).raise_for_status().content
        except Exception:  # noqa: BLE001
            return None
    return hit
