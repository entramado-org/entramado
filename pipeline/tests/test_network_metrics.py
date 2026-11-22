"""network_metrics: the community partition is reproducible, whatever order Neo4j returns the graph in and whatever state `random` is in."""
import collections
import random
import unittest

from pipeline.src import network_metrics


def _graph(n=240, blocks=6, seed=7):
    """Planted partition over synthetic ids: dense inside a block, sparse across. Unseeded Louvain splits it differently
    on almost every run."""
    rng = random.Random(seed)
    ids = [f"Person:zz-nodo-{i:04d}" for i in range(n)]
    edges = [(ids[i], ids[j], 1 + (rng.random() < 0.2))
             for i in range(n) for j in range(i + 1, n)
             if rng.random() < (0.12 if i * blocks // n == j * blocks // n else 0.01)]
    return [{"id": x, "name": x, "degree": 0} for x in ids], edges


def _partition(rows):
    """The communities as sets of ids, without depending on their numbers."""
    groups = collections.defaultdict(list)
    for row in rows:
        groups[row["community"]].append(row["id"])
    return sorted(sorted(members) for members in groups.values())


class CommunitiesTest(unittest.TestCase):
    def setUp(self):
        self.nodes, self.edges = _graph()

    def test_the_same_graph_gives_the_same_rows_whatever_the_callers_random_state(self):
        """Not just run after run in one process: a fresh process starts `random` from a different state."""
        first = network_metrics.compute(self.nodes, self.edges)
        for seed in range(5):
            random.seed(seed)
            self.assertEqual(network_metrics.compute(self.nodes, self.edges), first)

    def test_the_order_of_nodes_and_pairs_does_not_change_the_partition(self):
        reference = _partition(network_metrics.compute(self.nodes, self.edges))
        rng = random.Random(1)
        for _ in range(5):
            nodes, edges = self.nodes[:], [(b, a, w) if rng.random() < 0.5 else (a, b, w) for a, b, w in self.edges]
            rng.shuffle(nodes)
            rng.shuffle(edges)
            self.assertEqual(_partition(network_metrics.compute(nodes, edges)), reference)

    def test_the_callers_random_state_is_left_as_it_was(self):
        random.seed(123)
        before = random.getstate()
        network_metrics.compute(self.nodes, self.edges)
        self.assertEqual(random.getstate(), before)


if __name__ == "__main__":
    unittest.main()
