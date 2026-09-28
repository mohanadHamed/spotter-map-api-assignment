"""US state metadata and place-name normalisation shared by data prep and runtime geocoding."""

import re
import unicodedata

US_STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California",
    "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware", "DC": "District of Columbia",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois",
    "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana",
    "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
    "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon",
    "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota",
    "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia",
    "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
}

STATE_NAME_TO_CODE = {name.upper(): code for code, name in US_STATES.items()}

# Leading/whole-word abbreviations that appear inconsistently between data sources.
_TOKEN_ALIASES = {
    "SAINT": "ST",
    "SAINTE": "ST",
    "STE": "ST",
    "FORT": "FT",
    "MOUNT": "MT",
}
_DIRECTION_PREFIXES = {"N": "NORTH", "S": "SOUTH", "E": "EAST", "W": "WEST"}

# Census legal/statistical area descriptions appended to place names ("Chicago city").
_CENSUS_SUFFIX = re.compile(
    r"\s+("
    r"city and borough|consolidated government|unified government|metropolitan government|"
    r"metro government|metro township|urban county|charter township|unorganized territory|census designated place|"
    r"city|town|village|borough|CDP|municipality|township|plantation|gore|grant|location|purchase|"
    r"comunidad|zona urbana|UT"
    r")$",
    re.IGNORECASE,
)


def normalize_place_name(name):
    """Return a comparison key for a place name.

    Case, punctuation, whitespace and common abbreviations are folded so that
    "Mc Alpin" == "McAlpin", "La Salle" == "LaSalle", "Saint Louis" == "St. Louis".
    """
    text = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"\(.*?\)", " ", text.upper())
    text = text.replace("'", "")
    tokens = re.sub(r"[^A-Z0-9 ]", " ", text).split()
    if not tokens:
        return ""
    tokens = [_TOKEN_ALIASES.get(token, token) for token in tokens]
    if len(tokens) > 1 and tokens[0] in _DIRECTION_PREFIXES:
        tokens[0] = _DIRECTION_PREFIXES[tokens[0]]
    return "".join(tokens)


def strip_census_suffix(name):
    """Remove the Census area description: "Chicago city" -> "Chicago", "Kansas City city" -> "Kansas City"."""
    cleaned = re.sub(r"\(.*?\)", "", name).strip()
    return _CENSUS_SUFFIX.sub("", cleaned).strip() or cleaned


_GENERIC_TRAILING_WORD = re.compile(r"\s+(city|town|village|township)$", re.IGNORECASE)


def census_name_aliases(name):
    """Secondary names a Census place is also known by.

    "Augusta-Richmond County" -> "Augusta", "Boise City" -> "Boise", "Methuen Town" -> "Methuen".
    Aliases are only used when no place carries that name as its primary name.
    """
    base = strip_census_suffix(name)
    aliases = []
    if base.upper().startswith("TOWN OF "):
        aliases.append(base[len("Town of "):])
    for separator in ("-", "/"):
        if separator in base:
            aliases.append(base.split(separator)[0].strip())
    shortened = _GENERIC_TRAILING_WORD.sub("", base).strip()
    if shortened and shortened != base:
        aliases.append(shortened)
    return [alias for alias in dict.fromkeys(aliases) if alias and alias != base]


def city_lookup_keys(city):
    """Keys to try, in order, when resolving a free-text city name against the gazetteer."""
    keys = [normalize_place_name(city)]
    shortened = normalize_place_name(_GENERIC_TRAILING_WORD.sub("", city.strip()))
    if shortened and shortened not in keys:
        keys.append(shortened)
    return keys


def resolve_state_code(value):
    """Map "IL", "il" or "Illinois" to "IL"; return None when unknown."""
    text = (value or "").strip().upper().rstrip(".")
    if text in US_STATES:
        return text
    return STATE_NAME_TO_CODE.get(text)
