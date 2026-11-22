"""The whole-tree PII scanner (pipeline/scripts/pii_tree_scan.py) must flag real-looking identifiers anywhere,
skip the known non-identifier shapes and accept only the synthetic fixtures by value."""
import contextlib
import importlib.util
import io
import os
import sys
import tempfile
import unittest
from collections import Counter
from unittest import mock

_SCRIPT = os.path.join(os.path.dirname(__file__), "..", "..", "pipeline", "scripts", "pii_tree_scan.py")
_spec = importlib.util.spec_from_file_location("pii_tree_scan", _SCRIPT)
scan = importlib.util.module_from_spec(_spec)
sys.modules["pii_tree_scan"] = scan  # dataclasses resolve annotations through sys.modules
_spec.loader.exec_module(scan)


def _j(*parts: str) -> str:
    """Values that must be FLAGGED are assembled at runtime: this file is part of the tracked tree the
    scanner checks in CI, so it must not contain a literal identifier-shaped string itself."""
    return "".join(parts)


DNI = _j("47.123.456", "-X")           # not the canonical fake, invalid control letter
PHONE = _j("+34 699 123", " 456")
JID = _j("3469912", "3456@s.whatsapp.net")
IBAN = _j("ES7620770024003102", "575766")
IMEI = _j("4901542032", "37518")
CADASTRAL = _j("7654321XY9876", "Z")
MAIL = _j("zz9pruebaficti", "cia hotm", "ail com")  # flattened like a generated vigía key


def _labels(text: str, high_only: bool = True):
    return sorted(h.label for h in scan.scan_text("f.yaml", text) if h.high or not high_only)


def _flagged(text: str):
    """High-severity labels with the synthetic allowlist switched off: the canonical fakes (valid check
    characters, nobody's identifier) then stand in for real values without this file carrying one."""
    with mock.patch.object(scan, "SYNTHETIC", set()):
        return _labels(text)


class HighSeverity(unittest.TestCase):
    def test_real_looking_identifiers_are_flagged(self):
        text = f"alias: {DNI}\ntel: {PHONE}\njid: {JID}\niban: {IBAN}\nimei: {IMEI}\nfinca: {CADASTRAL}\n"
        self.assertEqual(_labels(text), ["DNI", "IBAN", "IMEI", "referencia catastral", "teléfono", "teléfono"])

    def test_report_shows_the_masked_line_only(self):
        hits = scan.scan_text("f.yaml", f"alias: {DNI}\n")
        self.assertEqual(hits[0].line, 1)
        self.assertEqual(hits[0].masked, "alias: [DNI oculto]")

    def test_line_numbers(self):
        hits = scan.scan_text("f.yaml", f"a: 1\nb: 2\nc: {IBAN}\n")
        self.assertEqual([(h.line, h.label) for h in hits], [(3, "IBAN")])


class Allowlist(unittest.TestCase):
    def test_synthetic_fixtures_pass_by_value(self):
        text = ('redact_text("Nombre (12.345.678-Z), NIE X1234567L, ES9121000418450200051332, IMEI 356938035643809")\n'
                'redact_text("móvil núm. + 34 612 34 56 78")\n'
                'redact_text("34612345678@s.whatsapp.net")\n'
                'redact_text("parcela 1234567AB1234C")\n')
        self.assertEqual(_labels(text), [])

    def test_a_real_value_next_to_a_synthetic_one_still_fails(self):
        text = 'redact_text("12345678Z y ' + _j("47123456", "X") + '")\n'
        self.assertEqual(_labels(text), ["DNI"])


class FalsePositives(unittest.TestCase):
    def test_hashes_urls_and_file_numbers_are_skipped(self):
        text = ("unidad: 1079f9c890e045dfae6ec309323901376637c88b8ef941b385905e1313cd85d9\n"
                "- https://vlex.es/vid/763539769\n"
                "- https://www.canarias7.es/x/ramon-20250618202706-ntrc.html\n"
                "name: 2021/EA26/00001152E\n"
                "roundPrefix: 20260906-L\n")
        self.assertEqual(_labels(text), [])

    def test_offsets_survive_earlier_replacements(self):
        # The IBAN is masked first, which shifts the text; the URL after it must still be recognised as a URL.
        text = f"iban: {IBAN}\n- https://vlex.es/vid/763539769\n"
        self.assertEqual(_labels(text), ["IBAN"])


class LowSeverity(unittest.TestCase):
    def test_addresses_and_plates_are_low(self):
        hits = scan.scan_text("f.yaml", "domicilio: Calle Olivo 10, Madrid\nplaca 1234 BCD\n")
        self.assertEqual(sorted(h.label for h in hits), ["domicilio", "matrícula"])
        self.assertFalse(any(h.high for h in hits))


class Urls(unittest.TestCase):
    """Only the scheme, host and path of a URL are skipped; the markup around it, the query and the fragment
    are scanned."""

    def test_markup_next_to_a_url_is_scanned(self):
        self.assertEqual(_flagged('<a href="https://example.org/">12345678Z</a>\n'), ["DNI"])
        self.assertEqual(_flagged("[ficha](https://example.org/ficha)612345678\n"), ["teléfono"])
        self.assertEqual(_flagged("[12345678Z](https://example.org/ficha)\n"), ["DNI"])
        self.assertEqual(_flagged("<https://example.org/ficha>X1234567L\n"), ["NIE"])

    def test_query_and_fragment_are_scanned(self):
        self.assertEqual(_flagged("https://example.org/buscar?dni=12345678Z&tel=612345678\n"), ["DNI", "teléfono"])
        self.assertEqual(_flagged("https://example.org/ficha#612345678\n"), ["teléfono"])

    def test_the_masked_line_hides_an_identifier_in_a_query(self):
        with mock.patch.object(scan, "SYNTHETIC", set()):
            hits = scan.scan_text("f.md", "ver https://example.org/buscar?tel=612-34-56-78\n")
        self.assertEqual([h.masked for h in hits], ["ver https://example.org/buscar?tel=[teléfono oculto]"])

    def test_ids_in_a_path_or_a_boe_query_are_not_identifiers(self):
        text = ("- https://vlex.es/vid/763539769\n"
                "- https://www.boe.es/diario_boe/txt.php?id=BOE-A-2024-12345\n"
                "- https://contrataciondelestado.es/wps/poc?uri=deeplink:detalle_licitacion&idEvl=0gbqAWUeB9iXQV0W\n")
        self.assertEqual(_flagged(text), [])


class Formats(unittest.TestCase):
    """Identifiers written in lower case or grouped with dots, dashes or (non-breaking) spaces."""

    def test_grouped_phones(self):
        for phone in ("612-34-56-78", "612.345.678", "612 345 678", "612\u00a0345\u00a0678",
                      "612\u202f34\u202f56\u202f78", "+34 612-345-678", "0034.612.345.678"):
            with self.subTest(phone=phone):
                self.assertEqual(_flagged(f"móvil: {phone}\n"), ["teléfono"])

    def test_nie_with_dots_or_in_lower_case(self):
        for nie in ("X.1234567.L", "X-1.234.567-L", "x1234567l", "x-1234567-l"):
            with self.subTest(nie=nie):
                self.assertEqual(_flagged(f"nie: {nie}\n"), ["NIE"])

    def test_dni_and_iban_in_lower_case(self):
        for text, label in (("dni: 12345678z", "DNI"), ("dni: 12.345.678-z", "DNI"), ("dni: 12345678.Z", "DNI"),
                            ("iban: es9121000418450200051332", "IBAN"), ("iban: es91 2100 0418 4502 0005 1332", "IBAN")):
            with self.subTest(text=text):
                self.assertEqual(_flagged(text + "\n"), [label])

    def test_iban_grouped_with_non_breaking_spaces(self):
        for sep in ("\u00a0", "\u202f"):
            with self.subTest(sep=hex(ord(sep))):
                iban = sep.join(("ES91", "2100", "0418", "4502", "0005", "1332"))
                self.assertEqual(_flagged(f"iban: {iban}\n"), ["IBAN"])

    def test_formatted_synthetic_fixtures_stay_allowlisted(self):
        text = "612-34-56-78 · x-1.234.567-l · es91\u00a02100\u00a00418\u00a04502\u00a00005\u00a01332 · 12345678z\n"
        self.assertEqual(_labels(text), [])

    def test_a_hit_only_a_variant_finds_needs_a_valid_check_character(self):
        # 12345678 takes Z. As written, the API's upper-case shape is flagged whatever its letter; a letter
        # that only becomes a control letter once upper-cased must be the right one.
        self.assertEqual(_flagged("ref 12345678A\n"), ["DNI"])
        self.assertEqual(_flagged("ref 12345678a\n"), [])

    def test_the_masked_line_also_hides_a_shape_dropped_for_its_check_character(self):
        with mock.patch.object(scan, "SYNTHETIC", set()):
            hits = scan.scan_text("f.sh", "ids: dni-12345678z, 12345678a\n")
        self.assertEqual([(h.label, h.masked) for h in hits], [("DNI", "ids: dni-[DNI oculto], [DNI oculto]")])


class Mail(unittest.TestCase):
    """Personal e-mail addresses, also spelled out or flattened into a normalised key (07-10-2026: a flattened one,
    with no @ or dots, sat in a generated file where no rule could see it)."""

    SPELLINGS = ("usuario_ficticio@gmail.com", "USUARIO_FICTICIO@GMAIL.COM", "usuario_ficticio arroba gmail punto com",
                 "usuario_ficticio (at) gmail.com", "usuario_ficticio gmail com", "usuario_ficticiogmailcom")

    def test_every_spelling_is_flagged(self):
        for text in self.SPELLINGS:
            with self.subTest(text=text):
                self.assertEqual(_flagged(f"keys: [{text}]\n"), ["correo"])

    def test_the_synthetic_address_passes_by_value_in_every_spelling(self):
        self.assertEqual(_labels("".join(f"keys: [{text}]\n" for text in self.SPELLINGS)), [])

    def test_a_real_looking_address_fails(self):
        self.assertEqual(_labels(f'"keys": ["{MAIL}"]\n'), ["correo"])

    def test_every_provider_and_domain(self):
        for provider in ("gmail", "hotmail", "outlook", "yahoo", "protonmail", "icloud"):
            for tld in ("com", "es", "net"):
                with self.subTest(provider=provider, tld=tld):
                    self.assertEqual(_labels(_j("zz9pruebaficticia@", provider, ".", tld) + "\n"), ["correo"])

    def test_a_provider_without_a_mailbox_is_not_an_address(self):
        text = ("gmail.com\nGmail\n@gmail.com\nuna cuenta de Gmail\nescribió desde su cuenta gmail.com\n"
                "abc@gmail.com\nmail.yahoo.com\nhttps://outlook.live.com/mail/\nis_gmail_com = True\n")
        self.assertEqual(_flagged(text), [])

    def test_the_report_masks_the_address(self):
        hits = scan.scan_text("f.json", f'   "{MAIL}"\n')
        self.assertEqual([(h.label, h.masked) for h in hits], [("correo", '"[correo oculto]"')])

    def test_any_domain_written_with_an_at_is_flagged(self):
        for domain in ("telefonica.net", "empresa-ficticia.es", "despacho.abogados.com", "uni.edu"):
            with self.subTest(domain=domain):
                self.assertEqual(_labels(_j("zz9pruebaficticia@", domain) + "\n"), ["correo"])

    def test_project_fixture_and_jid_domains_are_not_personal_mailboxes(self):
        text = _j("contacto@", "entramado.org") + "\n" + _j("alguien@", "example.invalid") + "\n" + f"jid: {JID}\n"
        self.assertEqual(_labels(text), ["teléfono"])  # the JID is a phone number, caught as such; no "correo"


class NotIdentifiers(unittest.TestCase):
    """Negative controls: shapes the normalised variants must not turn into identifiers."""

    def test_amounts(self):
        text = ("importe: 600.000.000 €\n600.000.000 de euros\n€ 699.000.000\n$699.000.000\n6.500.000 €\n"
                "1.600.000.000\n600 millones\n699 000 000 euros\n699.000.000,50 EUR\n")
        self.assertEqual(_flagged(text), [])

    def test_dates(self):
        text = ("entre el 18.06.2021 y el 19.06.2021\ndel 18-06-2021 a 20-06-2021\n2021-06-18 y 2021-07-01\n"
                "2026-10-06T22:07:36Z\n")
        self.assertEqual(_flagged(text), [])

    def test_a_date_is_never_collapsed(self):
        # 01092025 takes P: joined, the date and the page reference would read as a DNI with a valid letter.
        self.assertEqual(_flagged("auto del 01-09-2025 p.12\nauto del 01.09.2025 p.12\n"), [])

    def test_ids_built_on_a_date(self):
        # Joined by the dash variant they would read as 14-digit IMEIs.
        text = "# run 20260622-153012\nentity_id: LegalCase:2023-11-20-004321\nname: 2023-11-20-004321/1\n"
        self.assertEqual(_flagged(text), [])

    def test_file_numbers(self):
        text = ("BOE-A-2024-12345\nECLI:ES:TS:2024:1234\nSTS 1234/2024\nId Cendoj: 28079120012024100123\n"
                "NIG 28079 43 2 2023 0012345\nexpediente 2021/EA26/00001152E\n")
        self.assertEqual(_flagged(text), [])

    def test_a_slugged_file_number_is_not_a_dni(self):
        # 00001152 takes W: once upper-cased, the tail of the slug reads as a DNI with a valid letter.
        self.assertEqual(_flagged('entity_id: "LegalCase:2021-ea26-00001152w"\n'), [])
        self.assertEqual(_flagged('entity_id: "LegalCase:2021-dni-12345678z"\n'), ["DNI"])

    def test_artifact_sizes_in_a_lockfile(self):
        text = ('wheels = [{ url = "https://files.pythonhosted.org/packages/x/torch-2.14.1-cp312.whl", '
                'size = 654321098, upload-time = "2026-09-30T10:00:00.123Z" }]\n')
        self.assertEqual(_flagged(text), [])

    def test_coordinates(self):
        self.assertEqual(_flagged("40.416775, -3.703790\n41.385064 2.173403\nlat: 40.4168 lon: -3.7038\n"), [])


class Files(unittest.TestCase):
    """Every file is read as text except the binary formats listed by extension; text that cannot be read
    fails the gate instead of being skipped."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, name: str, data: bytes) -> str:
        with open(os.path.join(self.dir, name), "wb") as f:
            f.write(data)
        return name

    def _scan(self, *names: str):
        with mock.patch.object(scan, "SYNTHETIC", set()):
            return scan.scan_paths(list(names), root=self.dir)

    def test_utf16_latin1_svg_and_a_stray_nul_are_read(self):
        names = [self._write("a.txt", "Teléfono: 612345678\n".encode("utf-16")),
                 self._write("b.md", "Dirección y teléfono: 612345678\n".encode("latin-1")),
                 self._write("c.svg", b"<svg><text>12345678Z</text></svg>\n"),
                 self._write("d.tsx", b"const key = `${a}\x00${b}`; // 612345678\n")]
        result = self._scan(*names)
        self.assertEqual(sorted((h.path, h.label) for h in result.hits),
                         [("a.txt", "teléfono"), ("b.md", "teléfono"), ("c.svg", "DNI"), ("d.tsx", "teléfono")])
        self.assertEqual(result.analysed, Counter({"utf-8": 2, "utf-16": 1, "latin-1": 1}))
        self.assertEqual(result.unreadable, [])

    def test_binaries_are_skipped_by_extension_only(self):
        names = [self._write("x.pdf", b"%PDF-1.7\n\x00\x01\x02"),
                 self._write("y.png", b"\x89PNG\r\n\x1a\n\x00\x00"),
                 self._write("z.txt", "612345678\n".encode("utf-16-le")),  # UTF-16 without BOM
                 self._write("w.json", bytes(range(256)))]
        result = self._scan(*names)
        self.assertEqual(result.skipped, Counter({".pdf": 1, ".png": 1}))
        self.assertEqual(sorted(path for path, _ in result.unreadable), ["w.json", "z.txt"])
        self.assertEqual(result.hits, [])

    def test_an_unreadable_file_fails_the_gate_and_the_summary_counts_files(self):
        names = [self._write("ok.md", b"nada\n"), self._write("x.pdf", b"%PDF"),
                 self._write("z.txt", "hola\n".encode("utf-16-le"))]
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = scan.main([os.path.join(self.dir, n) for n in names])
        self.assertEqual(code, 1)
        summary = out.getvalue()
        self.assertIn("z.txt", summary)
        self.assertIn("1 analizados", summary)
        self.assertIn("1 saltados (.pdf 1)", summary)
        self.assertIn("1 ilegibles", summary)


if __name__ == "__main__":
    unittest.main()
