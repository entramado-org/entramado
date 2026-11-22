"""RGPD: mask personal identifiers in everything the API and the MCP serve.

The corpus (police reports, autos) carries DNI/NIE, IBAN and account numbers, phone numbers, IMEI, number plates, home addresses and
cadastral references of investigated people and third parties. Documents stay readable and searchable; only these
identifiers are masked at serve time, so the graph, the seed and the PDFs are untouched. Company CIFs (letter + 8
digits) are deliberately not matched; company seats written as street addresses are masked along with homes."""
from __future__ import annotations

import re

# Footnote markers are glued to the preceding token in the extracted text ("1234BCD476", "Baza 475"), so the
# right edge of plates and cadastral references only refuses a following letter, not a digit.
_NOT_LETTER_AFTER = r"(?![^\W\d_])"

# Provincial prefixes of the pre-2000 plate format (M-1234-AB). An explicit list keeps codes like "CZC1133XH"
# or "R10DOC06" out of the dashless form.
_PROVINCES = (
    "A|AB|AL|AV|B|BA|BI|BU|C|CA|CC|CE|CO|CR|CS|CU|GC|GE|GI|GR|GU|H|HU|IB|J|L|LE|LO|LU|M|MA|ML|MU|NA|O|OR|OU|"
    "P|PM|PO|S|SA|SE|SG|SO|SS|T|TE|TF|TO|V|VA|VI|Z|ZA"
)

# Street address: street type + capitalised name + house number (+ floor/door). The name must start with a
# capital and continue only with capitalised words or particles, so "Plaza nº 2" (a court's seat number) and
# prose like "camino de 5 años" are not addresses.
_STREET_TYPE = (
    r"(?i:(?<![^\W\d_])(?:c/|av/|avda\.|av\.|p[º°]\.?|pza\.|ctra\.|urb\.|"
    r"(?:calle|avda|avenida|paseo|plaza|camino|carretera|ronda|traves[ií]a|urbanizaci[oó]n|glorieta|"
    r"callej[oó]n|carrer|r[uú]a|largo|lg)(?![^\W\d_])))"
)
_WORD = r"(?:[dlDL]['’])?[A-ZÁÉÍÓÚÑÜÇ](?:[^\W\d_]|['’-](?=[^\W\d_]))*\.?"
_PARTICLE = r"(?:de|del|la|las|los|el|y|i|d['’]|l['’])"
_NOT_NUMBER_WORD = r"(?!(?i:n\.?\s?[º°o]|núm|num|número|numero)(?![^\W\d_]))"
_STREET_NAME = rf"(?:{_PARTICLE}\s+)*{_NOT_NUMBER_WORD}{_WORD}(?:\s+(?:{_PARTICLE}\s+)*{_WORD}){{0,5}}"
_HOUSE_NUMBER = (
    r"(?:\s*,?\s*(?i:(?:n\.?\s?[º°o]|núm|num|número|numero|km)\.?)\s*\d{1,4}|\s*,?\s*(?i:s/n)|(?:[ \t,]+\n?[ \t]*|\n[ \t]*)\d{1,3})"
    r"(?:\s?[-–]\s?\d{1,3})?(?:\s?[A-Z](?![^\W\d_]))?(?![.,]?\d)"
    r"(?!\s+de\s+(?i:enero|febrero|marzo|abril|mayo|junio|julio|agosto|sep?tiembre|octubre|noviembre|diciembre))"
)
_FLOOR_DOOR = (
    r"(?:[\s,-]*(?:\d{1,2}\s?[º°ª]\.?(?:\s?[A-Z](?![^\W\d_])|\s?(?i:izq\w*|izda|dcha|dch|centro)\.?)?"
    r"|(?i:planta|piso|puerta|escalera|esc\.|portal|bloque|oficina|local|nave|parcela)\s+\w+"
    r"|(?i:bajo)(?:\s?[A-Z](?![^\W\d_]))?)){0,4}"
)

_PATTERNS = [
    # IBAN (ES + 22 digits, optionally grouped by 4) before phones so its digit runs are not split.
    (re.compile(r"\bES\d{2}(?:[ -]?\d{4}){5}\b"), "[IBAN oculto]"),
    # Spanish bank account number (CCC: 4 bank + 4 branch + 2 control + 10 account digits, optionally grouped by
    # spaces or dashes). Police reports quote accounts of investigated people and of third parties. The 20-digit
    # CENDOJ document id ("Id Cendoj: 2807912001…") and digit runs inside URL paths are not accounts.
    (re.compile(r"(?<![\w/.,])(?<!Cendoj: )(?<!Cendoj:)\d{4}[ -]?\d{4}[ -]?\d{2}[ -]?\d{10}(?!\w|[.,]\d)"),
     "[cuenta bancaria oculta]"),
    # Cadastral reference: urban (7 digits + 2 letters + 4 digits + letter) or rustic (5 digits + letter + 8
    # digits) parcel, optionally followed by the 4-digit unit and 2 control letters.
    (re.compile(r"(?<![^\W_])(?:\d{7}[A-Z]{2}\d{4}[A-Z]|\d{5}[A-Z]\d{8})(?:\d{4}[A-Z]{2})?" + _NOT_LETTER_AFTER),
     "[referencia catastral oculta]"),
    # NIE: X/Y/Z + 7 digits + control letter.
    (re.compile(r"\b[XYZ][- ]?\d{7}[- ]?[A-Z]\b"), "[NIE oculto]"),
    # DNI: 8 digits + control letter (dots allowed: 12.345.678-Z).
    (re.compile(r"\b\d{2}\.?\d{3}\.?\d{3}[- ]?[A-Z]\b"), "[DNI oculto]"),
    # IMEI: 14 or 15 digits standalone (police reports also quote the 14-digit form, without check digit).
    # Word-ish boundaries: a 14-digit run inside a hex hash or a chunk id is not an IMEI.
    (re.compile(r"(?<![\w.,])\d{14,15}(?![\w.,@])"), "[IMEI oculto]"),
    # Spanish number plates. Current format 1234 BCD (consonants only, no vowels/Ñ/Q), with the DGT trailer (R)
    # and special-vehicle (E) prefixes; a year after a date ("01/02/2021 BCN-HAV") or before a flight route
    # (AAA-AAA) is not a plate. Then the old provincial format, with or without dashes (M-1234-AB, M1234AB).
    # Vehicles of investigated people appear in surveillance reports (063 tramo 6).
    (re.compile(r"(?<![\w/])(?:[RE][ -]?)?\d{4}[ -]?[BCDFGHJKLMNPRSTVWXYZ]{3}(?!-[A-Z]{3}\b)" + _NOT_LETTER_AFTER),
     "[matrícula oculta]"),
    (re.compile(rf"(?<![\w/])(?:{_PROVINCES})(?:-\d{{4}}-|[ ]?\d{{4}}[ ]?)[A-Z]{{1,2}}(?![^\W_])"),
     "[matrícula oculta]"),
    # WhatsApp JIDs (34612345678@s.whatsapp.net, OCR-mangled suffixes included) carry the phone number of any
    # country; the suffix is kept so the conversation context stays readable.
    (re.compile(r"(?<![\w.,+])\d{8,15}(?=\s?@)"), "[teléfono oculto]"),
    # Spanish phone numbers: optional +34/0034 (or a bare 34 glued to an 11-digit number), then 6/7/8/9 + 8
    # digits, optionally grouped. A trailing comma/period is punctuation unless a digit follows (amounts).
    (re.compile(r"(?:\+\s?34 ?|(?<![\w.,+])0034 ?|(?<![\w.,+])34(?=[6789]\d{8}(?!\d))|(?<![\w.,+]))"
                r"[6789](?:\d{8}|\d{2} \d{3} \d{3}|\d{2} \d{2} \d{2} \d{2})(?!\w|[.,]\d)"),
     "[teléfono oculto]"),
    # Postal addresses, last: the street name never contains digits, so earlier masks cannot be split by it.
    (re.compile(rf"{_STREET_TYPE}\s*{_STREET_NAME}{_HOUSE_NUMBER}{_FLOOR_DOOR}"), "[domicilio oculto]"),
    # Addresses written without a street type ("oficina sita en Carlos III, 32"), only after a locative cue.
    (re.compile(r"(?i:(\b(?:sit[oa]s?|domicilio(?:\s+social)?|domiciliad[oa]s?|reside|residente|empadronad[oa]s?)"
                rf"\s+en\s+(?:(?:el|la)\s+)?)){_STREET_NAME}{_HOUSE_NUMBER}{_FLOOR_DOOR}"),
     r"\1[domicilio oculto]"),
    # Number-first addresses after a residence cue ("Reside en 123/45 RIVERVIEW TOWER MANSION, BANGKOK."): mask up to
    # the end of the sentence, across at most one line break.
    (re.compile(r"(?i:(\b(?:reside|residente|domicilio(?:\s+social)?|domiciliad[oa]s?|empadronad[oa]s?)\s+en\s+))"
                r"\d{1,5}(?:/\d{1,5})?\s+[^\n.;]*(?:\n[^\n.;]*)?"),
     r"\1[domicilio oculto]"),
    # US-style addresses (number first): "1000 N Palm Hwy, Suite 100".
    (re.compile(r"\b\d{1,5}(?:\s+[A-Z][\w.]*){1,4}\s+(?:St|Street|Ave|Avenue|Blvd|Boulevard|Rd|Road|Dr|Drive|Hwy|"
                r"Highway|Ln|Lane|Way|Ct|Court|Pl|Place)\b\.?(?:,?\s+(?:Suite|Ste\.?|Apt\.?|Unit|#)\s*\w+)?"),
     "[domicilio oculto]"),
]


def redact_text(text: str) -> str:
    for rx, repl in _PATTERNS:
        text = rx.sub(repl, text)
    return text


# Technical values are never personal data and must stay intact: source links (a phone-like run inside a URL path)
# and ids (hex chunk ids, entity ids).
_SKIP_KEYS = {"id", "chunkId", "chunk_id", "entity_id", "entityId", "url", "href", "source_url", "doc_url"}


def redact(obj):
    """Recursively mask identifiers in strings inside dicts/lists/tuples; other values pass through."""
    if isinstance(obj, str):
        return obj if obj.startswith(("http://", "https://")) else redact_text(obj)
    if isinstance(obj, dict):
        return {k: (v if k in _SKIP_KEYS and isinstance(v, str) else redact(v)) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return type(obj)(redact(v) for v in obj)
    return obj
