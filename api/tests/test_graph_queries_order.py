"""The explorer queries cut at LIMIT after an ORDER BY: a tie must be broken by natural keys, never by Neo4j's internal ids,
so the same graph always serves the same rows."""
import re
import unittest
from unittest import mock

from app import graph_queries


class _Result:
    def single(self):
        return None

    def data(self):
        return []

    def __iter__(self):
        return iter(())


class _Session:
    def __init__(self, captured):
        self.captured = captured

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def run(self, cypher, **params):
        self.captured.append(cypher)
        return _Result()


def _cypher_of(call):
    """The Cypher text a query function sends to the driver (the driver is a fake: nothing is executed)."""
    captured = []
    driver = mock.Mock()
    driver.session.side_effect = lambda: _Session(captured)
    with mock.patch.object(graph_queries, "get_driver", return_value=driver):
        call()
    assert len(captured) == 1, captured
    return captured[0]


def _order_by(cypher):
    """Every ORDER BY key list in the query, whitespace collapsed (up to LIMIT, a closing brace or the next clause)."""
    flat = " ".join(cypher.split())
    return [m.group(1) for m in re.finditer(r"ORDER BY (.+?)(?= LIMIT | WITH | RETURN | \}\}| \}|$)", flat)]


def _cyphers_of(call):
    """Every Cypher text a query function sends (some send several, or wrap the text in a `Query`)."""
    captured = []
    driver = mock.Mock()
    driver.session.side_effect = lambda: _Session(captured)
    with mock.patch.object(graph_queries, "get_driver", return_value=driver):
        call()
    return [getattr(c, "text", c) for c in captured]


def _all_order_by(call):
    return [keys for cypher in _cyphers_of(call) for keys in _order_by(cypher)]


class TieBreakTest(unittest.TestCase):
    def test_overview_breaks_degree_ties_by_entity_id(self):
        self.assertIn("coalesce(n.degree,0) DESC, n.entity_id", _order_by(_cypher_of(graph_queries.overview)))

    def test_neighborhood_breaks_degree_ties_by_entity_id(self):
        self.assertIn("coalesce(m.degree,0) DESC, m.entity_id",
                      _order_by(_cypher_of(lambda: graph_queries.neighborhood("Person:zz-a"))))

    def test_node_detail_sources_break_ties_by_document_and_chunk(self):
        self.assertIn("d.title, ch.page_start, d.doc_id, ch.chunk_id",
                      _order_by(_cypher_of(lambda: graph_queries.node_detail("Person:zz-a"))))

    def test_timeline_breaks_date_ties_by_endpoints_type_and_provenance(self):
        keys = _order_by(_cypher_of(graph_queries.timeline))[0]
        self.assertEqual(keys, "coalesce(r.date_norm, toString(r.date_year)), a.entity_id, b.entity_id, rt, r.date_year, "
                               "r.source_doc, r.source_page, r.source_quote, r.amount, r.currency")

    def test_the_representative_of_parallel_edges_is_the_first_in_natural_key_order(self):
        """`_link_tail` serves one link per (source, target, type) from `rs[0]`: the parallel edges are ordered by everything it serves before
        they are collected, so the quote, document, status and amount do not depend on how the graph was stored."""
        for call in (graph_queries.overview, lambda: graph_queries.neighborhood("Person:zz-a")):
            cypher = " ".join(_cypher_of(call).split())
            keys = ("r.source_doc, r.source_page, r.source_quote, r.amount, r.currency, r.date_norm, r.date, r.date_year, r.source_kind, "
                    "r.note, r.status, r.status_fuente_tipo, r.status_fecha, r.status_fuente, r.outlet, r.url")
            self.assertIn(f"WITH ns, a, b, r ORDER BY {keys} WITH ns, a, b, type(r) AS rt,", cypher)

    def test_the_links_come_out_ordered_by_source_target_and_type_also_with_carry(self):
        """The final aggregation has its own total order, so the array of links (and the first link of a pair, which the MCP path tool shows) does
        not depend on how the rows reach it. paths_between carries `pathSeqs` through the same tail."""
        with mock.patch.object(graph_queries, "_rel_types_cache", ["ASSOCIATED_WITH"]):
            carried = " ".join(_cypher_of(lambda: graph_queries.paths_between("Person:zz-a", "Person:zz-b")).split())
        for flat in (" ".join(_cypher_of(graph_queries.overview).split()),
                     " ".join(_cypher_of(lambda: graph_queries.neighborhood("Person:zz-a")).split()), carried):
            self.assertIn("rt, rs ORDER BY a.entity_id, b.entity_id, rt WITH ns", flat)
        self.assertIn("WITH ns, pathSeqs, a, b, rt, rs ORDER BY a.entity_id, b.entity_id, rt WITH ns, pathSeqs, collect(", carried)
        self.assertIn("WITH ns, pathSeqs, a, b, r ORDER BY r.source_doc", carried)

    def test_the_remaining_cuts_and_collections_have_a_total_order(self):
        """Each of these queries cuts at LIMIT, or collects in row order, after an ORDER BY whose keys used to admit ties (an amount, a score,
        a date, a count): the natural keys that follow make the served rows a function of the graph alone."""
        t = "r.source_doc, r.source_page, r.source_quote"
        expected = (
            (lambda: graph_queries.search_entities("zz"), "score DESC, degree DESC, id"),
            (lambda: graph_queries.relation_detail("Person:zz-a", "Person:zz-b", "ASSOCIATED_WITH"),
             f"r.date_norm IS NULL, r.date_norm DESC, type(r), {t}"),
            (lambda: graph_queries.relation_pair("Person:zz-a", "Person:zz-b"), f"r.date_norm IS NULL, r.date_norm DESC, {t}"),
            (lambda: graph_queries.relation_pair("Person:zz-a", "Person:zz-b"), "total DESC, rt, sn.entity_id, en.entity_id"),
            (lambda: graph_queries.entity_claims("Person:zz-a"), f"type(r), r.date_norm, h.entity_id, {t}"),
            (lambda: graph_queries.keyword_search_chunks("zz"), "score DESC, chunkId"),
            (graph_queries.money_flows, "r.amount DESC, sourceId, targetId, type, date, doc, page, r.source_quote"),
            (graph_queries.money_flows, "amount DESC, sourceId, targetId, date, doc, page, coalesce(r2.source_quote, r1.source_quote), label"),
            (graph_queries.centrality, "score DESC, id"),
            (graph_queries.communities, "coalesce(n.degree,0) DESC, n.entity_id"),
            (graph_queries.communities, "size DESC, community"),
            (graph_queries.recent_documents, "d.doc_date DESC, d.doc_id"),
        )
        for call, keys in expected:
            self.assertIn(keys, _all_order_by(call))

    def test_ego_triples_breaks_ties_by_everything_it_serves(self):
        flat = " ".join(" ".join(_cyphers_of(lambda: graph_queries.ego_triples("Person:zz-a"))).split())
        self.assertIn("ELSE 3 END, g.rel, g.kind, g.s, g.o, g.s_seud, g.o_seud LIMIT $limit", flat)

    def test_no_order_depends_on_internal_ids(self):
        for call in (graph_queries.overview, lambda: graph_queries.neighborhood("Person:zz-a"),
                     lambda: graph_queries.node_detail("Person:zz-a"), graph_queries.timeline):
            for keys in _order_by(_cypher_of(call)):
                self.assertNotRegex(keys, r"\b(elementId|id)\(")


class AnonymisedRelativeQuoteTest(unittest.TestCase):
    """A relation that touches an anonymised relative (`Person:familiar-de-…`) never serves its literal quote or free-text note: they come
    from documents that name the relative in clear."""

    @staticmethod
    def _guard(expr, *nodes):
        cond = " OR ".join(f"{n}.entity_id STARTS WITH 'Person:familiar-de-'" for n in nodes)
        return f"CASE WHEN {cond} THEN null ELSE {expr} END"

    def test_every_surface_that_serves_a_quote_or_note_guards_it(self):
        g = self._guard
        with mock.patch.object(graph_queries, "_rel_types_cache", ["ASSOCIATED_WITH"]):
            paths = _cyphers_of(lambda: graph_queries.paths_between("Person:zz-a", "Person:zz-b"))
        surfaces = (
            (_cyphers_of(graph_queries.overview), [g("rs[0].source_quote", "a", "b"), g("rs[0].note", "a", "b")]),
            (_cyphers_of(lambda: graph_queries.neighborhood("Person:zz-a")), [g("rs[0].source_quote", "a", "b"), g("rs[0].note", "a", "b")]),
            (paths, [g("rs[0].source_quote", "a", "b"), g("rs[0].note", "a", "b"), g("rs[0].url", "a", "b")]),
            (_cyphers_of(lambda: graph_queries.relation_detail("Person:zz-a", "Person:zz-b", "ASSOCIATED_WITH")),
             [g("r.source_quote", "a", "b"), g("r.note", "a", "b"), g("r.url", "a", "b")]),
            (_cyphers_of(lambda: graph_queries.relation_pair("Person:zz-a", "Person:zz-b")),
             [g("r.source_quote", "sn", "en"), g("r.note", "sn", "en"), g("r.url", "sn", "en")]),
            (_cyphers_of(lambda: graph_queries.entity_claims("Person:zz-a")), [g("r.source_quote", "h")]),
            (_cyphers_of(graph_queries.timeline), [g("r.source_quote", "a", "b")]),
            (_cyphers_of(graph_queries.money_flows),
             [g("r.source_quote", "a", "b"), g("coalesce(r2.source_quote, r1.source_quote)", "a", "b")]),
        )
        for cyphers, guards in surfaces:
            text = " ".join(cyphers)
            for guard in guards:
                self.assertIn(guard, text)

    def test_the_undated_chunk_count_has_the_same_guards_as_the_search(self):
        self.assertEqual(_cyphers_of(lambda: graph_queries.count_chunks_missing_date("zz", mentions="Person:familiar-de-zz")), [])
        counted = " ".join(_cyphers_of(lambda: graph_queries.count_chunks_missing_date("zz", mentions="Person:zz-a")))
        self.assertIn("NOT EXISTS { MATCH (p {entity_id:$mentions}) WHERE coalesce(p.is_pseudonym, false) }", counted)

    def test_no_relation_surface_serves_a_bare_quote_or_note(self):
        """A tripwire, not a proof: it catches a new `quote:`/`note:` projection of `r`/`rs[0]` without the guard. It does not see other
        aliases; the charges of the accused (`acc.source_quote`) and the official registry layers (the four `note: r.note` below and
        `nota: r.note` in the BOE layer) are served bare on purpose — no anonymised relative has charges or registry edges."""
        import inspect
        src = inspect.getsource(graph_queries)
        bare = [ln.strip() for ln in src.splitlines()
                if re.search(r"(quote|note)\s*:\s*(rs\[0\]|r)\.(source_quote|note)\b", ln) or re.search(r"\.source_quote\)? AS quote", ln)]
        self.assertEqual(bare, [
            "denominacion_bdns: r.denominacion_bdns, note: r.note,",
            "rol: r.rol, cargo_desde: r.desde, borme_id: r.borme_id, note: r.note",
            "denominacion_placsp: r.denominacion_placsp, note: r.note,",
            "denominacion: r.denominacion, note: r.note,",
        ])


class PersonWithoutRelationsTest(unittest.TestCase):
    """A person with no relation to any other entity is not served by any entity surface. These tests only pin that each known entry
    point carries the predicate; that it hides exactly those persons and nobody else is checked by running the queries on a real graph
    (not in CI). A posture on a Claim counts as a relation."""

    @staticmethod
    def _served(v):
        return (f"(NOT {v}:Person OR EXISTS {{ ({v})-[_sr]-(_sm) WHERE (_sm.entity_id IS NOT NULL OR _sm:Claim) "
                f"AND NOT type(_sr) IN ['MENTIONED_IN', 'PART_OF'] }})")

    def test_every_entry_point_carries_the_predicate(self):
        s = self._served
        entry_points = (
            (lambda: graph_queries.search_entities("zz"), f"AND {s('node')} RETURN node.entity_id AS id"),
            (graph_queries.overview, f"AND {s('n')} WITH n ORDER BY coalesce(n.degree,0) DESC"),
            (lambda: graph_queries.neighborhood("Person:zz-a"), f"MATCH (c {{entity_id:$id}}) WHERE {s('c')} CALL"),
            (lambda: graph_queries.node_detail("Person:zz-a"), f"MATCH (n {{entity_id:$id}}) WHERE {s('n')} OPTIONAL MATCH"),
            (graph_queries.indexable_entities, f"AND NOT coalesce(n.is_pseudonym, false) AND {s('n')} AND (("),
            (lambda: graph_queries.ficha_slug("Person:zz-a"), f"AND NOT coalesce(n.is_pseudonym, false) AND {s('n')} AND (("),
            (lambda: graph_queries.keyword_search_chunks("zz", mentions="Person:zz-a"),
             f"EXISTS {{ MATCH (q {{entity_id:$mentions}}) WHERE {s('q')} }}"),
            (lambda: graph_queries.count_chunks_missing_date("zz", mentions="Person:zz-a"),
             f"EXISTS {{ MATCH (q {{entity_id:$mentions}}) WHERE {s('q')} }}"),
            (graph_queries.centrality, f"AND {s('n')} RETURN n.entity_id AS id"),
            (graph_queries.communities, f"AND {s('n')} WITH n ORDER BY coalesce(n.degree,0) DESC, n.entity_id"),
            (lambda: graph_queries.entities_with_charges(["Person:zz-a"]), f"MATCH (n) WHERE n.entity_id IN $ids AND {s('n')}"),
            (graph_queries.mercantil_layer, f"coalesce(m.is_mercantil, false) AND {s('m')}"),
            (graph_queries.contratacion_layer, f"coalesce(n.is_contratacion, false) AND {s('n')}"),
            (graph_queries.contratacion_layer, f"coalesce(m.placsp_sin_menores, false) AND {s('m')}"),
            (graph_queries.contratacion_ampliada_layer, f"coalesce(n.is_contratacion_amp, false) AND {s('n')}"),
            (graph_queries.contratacion_ampliada_layer, f"coalesce(m.amp_sin_adjudicaciones, false) AND {s('m')}"),
            (graph_queries.boe_layer, f"coalesce(n.is_boe, false) AND {s('n')}"),
        )
        for call, fragment in entry_points:
            self.assertIn(fragment, " ".join(" ".join(_cyphers_of(call)).split()))


if __name__ == "__main__":
    unittest.main()
