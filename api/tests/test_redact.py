"""Regression tests for `app.redact`.

Every value below is synthetic. The SHAPES mirror what the corpus produces (dotted DNIs, "+ 34" with a
space, footnote markers glued to plates, number-first foreign addresses...), but no identifier, address,
plate or phone number is copied from a real document: this file is public.

Run from api/: uv run python -m unittest discover -s tests -v"""
import unittest

from app.redact import redact, redact_text


class Identifiers(unittest.TestCase):
    def test_dni_nie_iban_imei(self):
        out = redact_text("Nombre (12.345.678-Z), NIE X1234567L, ES9121000418450200051332, IMEI 356938035643809")
        self.assertEqual(out, "Nombre ([DNI oculto]), NIE [NIE oculto], [IBAN oculto], IMEI [IMEI oculto]")
        self.assertEqual(redact_text("IMEI 1: 356938035643809 e IMEI 2: 35693803564380"), "IMEI 1: [IMEI oculto] e IMEI 2: [IMEI oculto]")
        # A digit run inside a hex hash / chunk id is not an IMEI.
        self.assertEqual(redact_text("unidad: 1079f9c890e045dfae6ec12345678901234c88b8"), "unidad: 1079f9c890e045dfae6ec12345678901234c88b8")

    def test_bank_account_ccc(self):
        # 20-digit Spanish account number (the CCC inside the official example IBAN), compact or grouped.
        for raw in ("21000418450200051332", "2100 0418 45 0200051332", "2100-0418-45-0200051332"):
            self.assertEqual(redact_text(f"cuenta nº {raw} de la entidad"), "cuenta nº [cuenta bancaria oculta] de la entidad")
        self.assertEqual(redact_text("titular de la 21000418450200051332."), "titular de la [cuenta bancaria oculta].")
        # The CENDOJ document id and a 20-digit article id inside a URL path are not accounts; an IBAN stays an IBAN.
        self.assertEqual(redact_text("Id Cendoj: 28079120012025201234 Órgano"), "Id Cendoj: 28079120012025201234 Órgano")
        url = "ver https://www.example.com/noticia/20260101123456789012.html y"
        self.assertEqual(redact_text(url), url)
        self.assertEqual(redact_text("ES9121000418450200051332"), "[IBAN oculto]")

    def test_company_cif_untouched(self):
        self.assertEqual(redact_text("CIF: B12345674."), "CIF: B12345674.")

    def test_recursive(self):
        self.assertEqual(redact({"a": ["tel 612345678"], "n": 3}), {"a": ["tel [teléfono oculto]"], "n": 3})


class Cadastral(unittest.TestCase):
    def test_urban_and_rustic(self):
        # Rustic (20 chars, letter in 6th position) and urban references, followed by footnote numbers.
        self.assertEqual(redact_text("475 Inmueble con referencia catastral 12345A001000010000AB\n476 La"),
                         "475 Inmueble con referencia catastral [referencia catastral oculta]\n476 La")
        self.assertEqual(redact_text("catastral:\n1234567AB1234C0001DE , así como"),
                         "catastral:\n[referencia catastral oculta] , así como")

    def test_parcel_only(self):
        self.assertEqual(redact_text("parcela 1234567AB1234C"), "parcela [referencia catastral oculta]")


class Addresses(unittest.TestCase):
    def test_corpus_shaped_addresses(self):
        cases = {
            # street + number glued to a footnote marker (475) and a town
            "✓ Camino de Valdeolmos 23, de Baza 475 (Granada).": "✓ [domicilio oculto], de Baza 475 (Granada).",
            "con domicilio en Avda. Lisboa\nnúmero 20 de Alcobendas": "con domicilio en [domicilio oculto] de Alcobendas",
            "domicilio social en C/ Olmo nº14 Edificio": "domicilio social en [domicilio oculto] Edificio",
            "inmueble sito en Paseo de Alameda nº. 164, valorado": "inmueble sito en [domicilio oculto], valorado",
            "Calle Pedro Olivo 10, 3º A, de Bilbao": "[domicilio oculto], de Bilbao",
            "Calle Rosa Jazmín nº 10, bajo, puerta I2.": "[domicilio oculto].",
            "CALLE FERRAZ NÚMERO 68-70": "[domicilio oculto]",
            "Avenida de Córdoba, s/n": "[domicilio oculto]",
            "domicilio en Portugal en LG OUTEIRO, 10A, Elvas.": "domicilio en Portugal en [domicilio oculto], Elvas.",
            "la oficina sita en Carlos III, 32. Adicionalmente": "la oficina sita en [domicilio oculto]. Adicionalmente",
            "Sun Tower, 1000 N Palm Hwy, Suite 100, Hallandale": "Sun Tower, [domicilio oculto], Hallandale",
        }
        for src, want in cases.items():
            with self.subTest(src=src):
                self.assertEqual(redact_text(src), want)

    def test_not_addresses(self):
        for src in [
            "Juzgado de Instrucción, Plaza nº 2 de Madrid",
            "la plaza número 2 del Juzgado",
            "PLAZA Nº 2",
            "en el camino de 5 años",
            "la Plaza de España en 2019",
            "obra en la autovía A-12 del\nCamino de Santiago421.",  # footnote glued to a name
            "firmado por Javier GARCÍA y Camino\nLÓPEZ PÉREZ221, fechado",  # Camino is a first name
            "variante de la CARRETERA N-330 en Teruel",  # public works, not a home
            "Dirección General de Carreteras 2",
            "ROMERO PÉREZ\n\n2",
            "domicilio social en Madrid, 28 de abril de 2020",
            "Diligencias Previas núm. 147/2024",
        ]:
            with self.subTest(src=src):
                self.assertEqual(redact_text(src), src)


class Plates(unittest.TestCase):
    def test_footnote_glued_and_trailer(self):
        # Footnote markers 476-478 glued to the plates; R = DGT trailer prefix.
        src = ("placas de matrícula\n1234BCD476, otro vehículo con placas\nde matrícula 5678FGH477 con un remolque "
               "con placa de matrícula R1234BBC478.")
        want = ("placas de matrícula\n[matrícula oculta]476, otro vehículo con placas\nde matrícula [matrícula oculta]477"
                " con un remolque con placa de matrícula [matrícula oculta]478.")
        self.assertEqual(redact_text(src), want)
        self.assertEqual(redact_text("placas 1234 BCD y 1234-BCD"), "placas [matrícula oculta] y [matrícula oculta]")

    def test_provincial(self):
        self.assertEqual(redact_text("M-1234-AB, M1234AB, M 1234 AB"),
                         "[matrícula oculta], [matrícula oculta], [matrícula oculta]")

    def test_not_plates(self):
        for src in [
            "Vuelo 01/02/2021 BCN-HAV\nVuelo 09/02/2021 LPL-HAV",  # a year before a flight route
            "Documents/57 T1234FN3~net-whatsapp",  # iOS container id
            "Ordenador marca HP con numeración CZC1133XH1.",
            "Codificado como R10DOC06",
        ]:
            with self.subTest(src=src):
                self.assertEqual(redact_text(src), src)


class Phones(unittest.TestCase):
    def test_trailing_punctuation(self):
        self.assertEqual(redact_text("llamó al 612 34 56 78, luego al 612345678."),
                         "llamó al [teléfono oculto], luego al [teléfono oculto].")

    def test_prefixes(self):
        # "+ 34" with a space, and across a line break.
        self.assertEqual(redact_text("móvil núm. + 34 612 34 56 78, intervenido"), "móvil núm. [teléfono oculto], intervenido")
        self.assertEqual(redact_text("núm. +\n34 612 34 56 78"), "núm. [teléfono oculto]")
        self.assertEqual(redact_text("psoe, +34612345678. Fecha"), "psoe, [teléfono oculto]. Fecha")
        self.assertEqual(redact_text("Accounts/34612345678/backup"), "Accounts/[teléfono oculto]/backup")

    def test_whatsapp_ids(self):
        # Spanish JIDs, a foreign number, a group id and OCR-mangled suffixes.
        self.assertEqual(redact_text("34612345678@s.whatsapp.net:\nNombre"), "[teléfono oculto]@s.whatsapp.net:\nNombre")
        self.assertEqual(redact_text("TO: 34623456789 @s.whatsapp.net"), "TO: [teléfono oculto] @s.whatsapp.net")
        self.assertEqual(redact_text("51912345678@s.whalsapp.net"), "[teléfono oculto]@s.whalsapp.net")
        self.assertEqual(redact_text("ID 34634567890-1612345678@g.us."), "ID [teléfono oculto]-[teléfono oculto]@g.us.")

    def test_amounts_and_references_untouched(self):
        for src in [
            "Importe 1.234.567,89 € y 612.345.678,00",
            "Subtotal 2021\n252.212,40\n€",
            "Diligencias Previas núm. 147/2024",
            "Rollo 20775/2024 y Auto 612/2025",
            "cuenta 0123456789",
            "Protocolo notarial nº 850",
            "total 612345678,50 euros",
        ]:
            with self.subTest(src=src):
                self.assertEqual(redact_text(src), src)


if __name__ == "__main__":
    unittest.main()


class RegressionsSecondReview(unittest.TestCase):
    def test_urls_and_ids_are_untouched(self):
        url = "https://www.elespanol.com/espana/20240711/informe-uco-contratos/869413601_0.html"
        self.assertEqual(redact(url), url)
        self.assertEqual(redact({"chunkId": "852586246c2e755d"}), {"chunkId": "852586246c2e755d"})
        self.assertEqual(redact_text("852586246c2e755d"), "852586246c2e755d")
        self.assertEqual(redact({"summarySources": [url]}), {"summarySources": [url]})

    def test_address_does_not_swallow_a_footnote_across_paragraphs(self):
        t = "calle Alfarero Ramón\n\n230 «BOE» núm. 225, de 17 de septiembre de 2024"
        self.assertIn("230 «BOE»", redact_text(t))

    def test_number_first_foreign_address_after_residence_cue(self):
        t = ("de septiembre de 2020. Reside en 123/45 RIVERVIEW TOWER MANSION,\n"
             "UNIT B7 CHAROEN NAKHON RD, BANGKOK.\n\n15. Pasaporte")
        out = redact_text(t)
        self.assertNotIn("RIVERVIEW", out)
        self.assertNotIn("CHAROEN", out)
        self.assertIn("Reside en [domicilio oculto]", out)
        self.assertIn("15. Pasaporte", out)

    def test_phone_followed_by_punctuation_still_masked(self):
        self.assertIn("[teléfono oculto],", redact_text("llamó al 612 34 56 78, ayer"))
