"""Stage 1 — ground-truth worlds.

Structure (entities, predicates, segment schedules, change events) is decided
by a SEEDED SCAFFOLD in pure code; a single LLM call per world only names
things and writes the lore paragraph. Every gold label downstream derives
from the returned World — never from the LLM again.
"""

from __future__ import annotations

import logging
import random
from datetime import date, timedelta

from pydantic import BaseModel, Field

from .llm import structured
from .schema import Entity, Predicate, Segment, Timeline, World

WINDOW_START = date(2022, 1, 1)
WINDOW_END = date(2026, 6, 30)

# head/tail type names double as LLM naming slots.
PREDICATE_CATALOG: list[Predicate] = [
    Predicate(id="founded_in", label="was founded in", volatility="immutable",
              functional=True, head_type="company", tail_type="year"),
    Predicate(id="headquartered_in", label="is headquartered in", volatility="durable",
              functional=True, head_type="company", tail_type="city"),
    Predicate(id="owned_by_team", label="is owned by", volatility="durable",
              functional=True, head_type="service", tail_type="team"),
    Predicate(id="vendor_contract_with", label="has a vendor contract with",
              volatility="durable", functional=True, head_type="company",
              tail_type="vendor"),
    Predicate(id="runs_on_database", label="runs on", volatility="medium",
              functional=True, head_type="service", tail_type="database"),
    Predicate(id="deployed_on_cloud", label="is deployed on", volatility="medium",
              functional=True, head_type="service", tail_type="cloud"),
    Predicate(id="led_by", label="is led by", volatility="medium",
              functional=True, head_type="team", tail_type="person"),
    Predicate(id="has_ceo", label="has CEO", volatility="medium",
              functional=True, head_type="company", tail_type="person"),
    Predicate(id="member_of_team", label="is on team", volatility="medium",
              functional=True, head_type="person", tail_type="team"),
    Predicate(id="depends_on", label="depends on", volatility="medium",
              functional=False, head_type="service", tail_type="service"),
    Predicate(id="runbook_owner", label="has runbook owner", volatility="ephemeral",
              functional=True, head_type="service", tail_type="person"),
]


class WorldNaming(BaseModel):
    """LLM output schema: names + lore for a scaffolded world."""

    company: str = Field(min_length=3)
    services: list[str] = Field(min_length=3)
    teams: list[str] = Field(min_length=3)
    people: list[str] = Field(min_length=5)
    databases: list[str] = Field(min_length=3)
    clouds: list[str] = Field(min_length=2)
    cities: list[str] = Field(min_length=2)
    vendors: list[str] = Field(min_length=2)
    lore: str = ""


NAMING_INSTRUCTIONS = """You are naming a fictional mid-size tech company for a synthetic
training corpus. Constraints:
- services: lowercase kebab-case internal service names (like "billing-api", "auth-gateway")
- teams: short team names (like "Payments", "Platform Infrastructure")
- people: realistic full names, diverse origins
- databases: REAL database/product names (like PostgreSQL, MySQL, DynamoDB, Cassandra)
- clouds: real cloud providers (AWS, GCP, Azure — pick two)
- cities: real major cities
- vendors: two real enterprise vendor/tooling names (like Datadog, Snowflake)
- lore: 2-3 sentences describing what the company does and its engineering culture.
Return {counts}."""


def _rand_date(rng: random.Random, start: date, end: date) -> date:
    span = (end - start).days
    if span <= 0:
        return start
    return start + timedelta(days=rng.randrange(span + 1))


def _close_functional(timelines: dict[str, list[Segment]]) -> None:
    """Make functional segments contiguous PER (predicate, head): each
    valid_to = next segment's valid_from. Segments of different heads in
    the same timeline must never close each other."""
    for pred_id, segs in timelines.items():
        if not next(p for p in PREDICATE_CATALOG if p.id == pred_id).functional:
            continue
        by_head: dict[str, list[Segment]] = {}
        for s in segs:
            by_head.setdefault(s.head_id, []).append(s)
        for head_segs in by_head.values():
            head_segs.sort(key=lambda s: s.valid_from)
            for cur, nxt in zip(head_segs, head_segs[1:]):
                if cur.valid_to is None:
                    cur.valid_to = nxt.valid_from


def build_scaffold(rng: random.Random) -> dict:
    """Decide world structure deterministically: slots + segment schedules."""
    n_services = rng.randint(4, 6)
    n_teams = rng.randint(3, 4)
    n_people = rng.randint(5, 7)
    services = [f"SERVICE_{i}" for i in range(n_services)]
    teams = [f"TEAM_{i}" for i in range(n_teams)]
    people = [f"PERSON_{i}" for i in range(n_people)]
    databases = [f"DB_{i}" for i in range(3)]
    clouds = [f"CLOUD_{i}" for i in range(2)]
    cities = [f"CITY_{i}" for i in range(2)]
    vendors = [f"VENDOR_{i}" for i in range(1)]
    company = "COMPANY"

    slots: dict[str, list[str]] = {
        "company": [company], "service": services, "team": teams,
        "person": people, "database": databases, "cloud": clouds,
        "city": cities, "vendor": vendors,
        "year": [f"YEAR_{y}" for y in (2015, 2016, 2017, 2018, 2019)],
    }

    # slot -> slot assignment pools (tail candidates by type)
    pool = {
        "service": services, "team": teams, "person": people,
        "database": databases, "cloud": clouds, "city": cities,
        "vendor": vendors, "year": slots["year"],
    }

    def add(timelines: dict[str, list[Segment]], pred: str, head: str,
            tail: str, valid_from: date, valid_to: date | None = None) -> None:
        timelines.setdefault(pred, []).append(
            Segment(head_id=head, tail_id=tail, valid_from=valid_from, valid_to=valid_to)
        )

    def change_point(rng: random.Random, start: date = WINDOW_START,
                     end: date = WINDOW_END) -> date:
        return _rand_date(rng, start + timedelta(days=200), end - timedelta(days=200))

    timelines: dict[str, list[Segment]] = {}

    # immutable / durable predicates: one long-lived segment, occasional swap
    add(timelines, "founded_in", company, pool["year"][rng.randrange(len(pool["year"]))],
        date(2015, 1, 1))
    add(timelines, "headquartered_in", company, cities[rng.randrange(2)],
        date(2015, 1, 1))
    if rng.random() < 0.3:  # rare HQ move
        first = timelines["headquartered_in"][0].tail_id
        add(timelines, "headquartered_in", company,
            cities[0] if first != cities[0] else cities[1], change_point(rng))
    vendor0 = vendors[0]
    add(timelines, "vendor_contract_with", company, vendor0, date(2015, 1, 1))
    if rng.random() < 0.25:
        t = change_point(rng)
        add(timelines, "vendor_contract_with", company, "VENDOR_ALT", t)

    service_team: dict[str, str] = {}
    for s in services:
        team = teams[rng.randrange(n_teams)]
        service_team[s] = team
        add(timelines, "owned_by_team", s, team, date(2015, 1, 1))
        if rng.random() < 0.2:  # occasional ownership transfer
            add(timelines, "owned_by_team", s, teams[rng.randrange(n_teams)],
                change_point(rng))

    # medium predicates: 0-2 changes each; guarantee some transitions for eval
    db_changes = 0
    for s in services:
        cur = databases[rng.randrange(3)]
        add(timelines, "runs_on_database", s, cur, date(2015, 1, 1))
        n_changes = rng.choices([0, 1, 2], weights=[0.5, 0.35, 0.15])[0]
        for _ in range(n_changes):
            t = change_point(rng)
            nxt = rng.choice([d for d in databases if d != cur])
            add(timelines, "runs_on_database", s, nxt, t)
            cur, db_changes = nxt, db_changes + 1
    if db_changes == 0:  # force at least one migration (the core tKG scenario)
        s = rng.choice(services)
        add(timelines, "runs_on_database", s, databases[1],
            change_point(rng))

    for s in services:
        cur = clouds[rng.randrange(2)]
        add(timelines, "deployed_on_cloud", s, cur, date(2015, 1, 1))
        if rng.random() < 0.3:
            t = change_point(rng)
            nxt = clouds[(cur == clouds[0]) and 1 or 0]
            add(timelines, "deployed_on_cloud", s, nxt, t)

    for t_ in teams:
        cur = people[rng.randrange(n_people)]
        add(timelines, "led_by", t_, cur, date(2015, 1, 1))
        for _ in range(rng.choices([0, 1, 2], weights=[0.55, 0.3, 0.15])[0]):
            t = change_point(rng)
            nxt = rng.choice([p for p in people if p != cur])
            add(timelines, "led_by", t_, nxt, t)
            cur = nxt

    # CEO succession — the bush→obama→trump case; 1-2 transitions
    cur = people[rng.randrange(n_people)]
    add(timelines, "has_ceo", company, cur, date(2015, 1, 1))
    for _ in range(rng.choices([1, 2], weights=[0.6, 0.4])[0]):
        t = change_point(rng)
        nxt = rng.choice([p for p in people if p != cur])
        add(timelines, "has_ceo", company, nxt, t)
        cur = nxt

    for p in people:
        cur = teams[rng.randrange(n_teams)]
        add(timelines, "member_of_team", p, cur, date(2015, 1, 1))
        if rng.random() < 0.35:
            add(timelines, "member_of_team", p,
                rng.choice([t_ for t_ in teams if t_ != cur]), change_point(rng))

    # non-functional: concurrent additive edges
    for s in services:
        others = [o for o in services if o != s]
        rng.shuffle(others)
        for dep in others[: rng.randint(1, 2)]:
            add(timelines, "depends_on", s, dep, date(2015, 1, 1))
        if rng.random() < 0.3 and len(others) > 2:
            add(timelines, "depends_on", s, others[2], change_point(rng))

    # ephemeral: 2-3 rotations for a subset of services
    for s in services:
        if rng.random() < 0.5:
            cur = people[rng.randrange(n_people)]
            t0 = _rand_date(rng, WINDOW_START, WINDOW_START + timedelta(days=500))
            add(timelines, "runbook_owner", s, cur, t0)
            for _ in range(rng.randint(1, 2)):
                t = change_point(rng, start=t0)
                nxt = rng.choice([p for p in people if p != cur])
                add(timelines, "runbook_owner", s, nxt, t)
                cur, t0 = nxt, t

    # close functional segment chains per (predicate, head)
    _close_functional(timelines)

    return {"slots": slots, "timelines": timelines}


FAKE_NAMES: dict[str, list[str]] = {
    "company": ["Northbeam Labs"], "service": ["billing-api", "auth-gateway", "search-svc", "notify-worker", "ledger-api", "insights-etl"],
    "team": ["Payments", "Platform", "Identity", "Growth"],
    "person": ["Maya Chen", "Diego Alvarez", "Priya Nair", "Tom Okafor", "Sara Lindqvist", "Jun Park", "Amara Diallo"],
    "database": ["PostgreSQL", "MySQL", "DynamoDB"],
    "cloud": ["AWS", "GCP"],
    "city": ["Austin", "Toronto"],
    "vendor": ["Datadog", "Snowflake"],
    "year": ["2015", "2016", "2017", "2018", "2019"],
}


def _default_naming(scaffold: dict) -> WorldNaming:
    slots = scaffold["slots"]
    return WorldNaming(
        company=FAKE_NAMES["company"][0],
        services=FAKE_NAMES["service"][: len(slots["service"])],
        teams=FAKE_NAMES["team"][: len(slots["team"])],
        people=FAKE_NAMES["person"][: len(slots["person"])],
        databases=FAKE_NAMES["database"][: len(slots["database"])],
        clouds=FAKE_NAMES["cloud"][: len(slots["cloud"])],
        cities=FAKE_NAMES["city"][: len(slots["city"])],
        vendors=FAKE_NAMES["vendor"][:2],
        lore="A fictional mid-size B2B SaaS company used for synthetic data generation.",
    )


async def name_world(scaffold: dict, *, use_llm: bool = True) -> WorldNaming:
    """Fill a scaffold's naming slots. Falls back to deterministic fake names."""
    if not use_llm:
        return _default_naming(scaffold)
    slots = scaffold["slots"]
    counts = (
        f"- 1 company\n"
        f"- {len(slots['service'])} services\n"
        f"- {len(slots['team'])} teams\n"
        f"- {len(slots['person'])} people\n"
        f"- {len(slots['database'])} databases\n"
        f"- {len(slots['cloud'])} clouds\n"
        f"- {len(slots['city'])} cities\n"
        f"- {len(slots['vendor'])} vendors\n"
        f"- lore: short description"
    )
    return await structured(
        WorldNaming,
        system=NAMING_INSTRUCTIONS,
        user=f"Name this fictional company. Provide exactly:\n{counts}",
        temperature=0.9,
    )


# scaffold slot type → WorldNaming field (plural) holding its name pool
NAMING_FIELD = {
    "company": "company", "service": "services", "team": "teams",
    "person": "people", "database": "databases", "cloud": "clouds",
    "city": "cities", "vendor": "vendors",
}


def instantiate_world(world_id: str, seed: int, scaffold: dict,
                      naming: WorldNaming) -> World:
    """Substitute real names for placeholder slots → a validated World."""
    slots = scaffold["slots"]
    names: dict[str, str] = {}
    for slot_type, slot_list in slots.items():
        if slot_type == "year":
            pool = FAKE_NAMES["year"]  # literal years, not LLM-named
        else:
            pool = getattr(naming, NAMING_FIELD[slot_type])
        if not isinstance(pool, list):  # singular fields (company) are str
            pool = [pool]
        for i, slot_id in enumerate(slot_list):
            names[slot_id] = pool[i % len(pool)]

    entities: list[Entity] = []
    for slot_type, slot_list in slots.items():
        for slot_id in slot_list:
            entities.append(Entity(id=slot_id, name=names[slot_id],
                                   type=slot_type))

    # VENDOR_ALT is the rare second vendor (rare contract swap in the scaffold)
    used_ids = {s.head_id for segs in scaffold["timelines"].values()
                for s in segs} | {s.tail_id for segs
                                  in scaffold["timelines"].values() for s in segs}
    if "VENDOR_ALT" in used_ids:
        alt_name = naming.vendors[1 % len(naming.vendors)]
        names["VENDOR_ALT"] = alt_name
        entities.append(Entity(id="VENDOR_ALT", name=alt_name, type="vendor"))

    timelines = [
        Timeline(predicate_id=pred_id,
                 segments=[s.model_copy() for s in segs])
        for pred_id, segs in scaffold["timelines"].items()
    ]
    return World(
        id=world_id, seed=seed, lore=naming.lore,
        start=WINDOW_START, end=WINDOW_END,
        entities=entities, predicates=[p.model_copy() for p in PREDICATE_CATALOG],
        timelines=timelines,
    )


async def generate_world(world_id: str, seed: int, *,
                         use_llm: bool = True) -> World:
    rng = random.Random(seed)
    scaffold = build_scaffold(rng)
    try:
        naming = await name_world(scaffold, use_llm=use_llm)
    except Exception as exc:  # naming failure → deterministic fallback names
        logging.getLogger(__name__).warning(
            "world naming failed for %s (%s); using default names",
            world_id, str(exc)[:120])
        naming = _default_naming(scaffold)
    return instantiate_world(world_id, seed, scaffold, naming)
