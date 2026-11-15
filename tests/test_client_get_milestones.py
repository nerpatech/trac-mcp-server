"""TracClient.get_milestones: ticket.milestone.get through
system.multicall batches.

``FakeTrac`` stands in for the XML-RPC endpoint, installed as the
client's ``_rpc_request`` the way tests/test_client_get_tickets.py does,
so the batching, the fault handling and the fallback to single gets are
the real code.
"""

import xmlrpc.client

from trac_mcp_server.core.client import (
    MILESTONE_FETCH_BATCH,
    TracClient,
)

MISSING_METHOD = xmlrpc.client.Fault(
    -32601, 'RPC method "system.multicall" not found'
)


class FakeTrac:
    def __init__(self, existing=()):
        self.existing = set(existing)
        self.multicall_enabled = True
        self.calls: list[str] = []  # top-level methods, in order
        self.batches: list[list[str]] = []  # names per multicall
        self.singles: list[str] = []  # names read by plain get

    @staticmethod
    def milestone(name: str) -> dict:
        return {
            "name": name,
            "due": 0,
            "completed": "20260101T00:00:00",
            "description": "",
        }

    def _get(self, name: str) -> dict:
        if name not in self.existing:
            raise xmlrpc.client.Fault(
                1, f"Milestone {name} does not exist."
            )
        return self.milestone(name)

    def rpc(self, service: str, method: str, *params):
        full = f"{service}.{method}"
        self.calls.append(full)
        if full == "system.multicall":
            if not self.multicall_enabled:
                raise MISSING_METHOD
            calls = params[0]
            assert all(
                c["methodName"] == "ticket.milestone.get" for c in calls
            )
            self.batches.append([c["params"][0] for c in calls])
            out: list = []
            for call in calls:
                try:
                    out.append([self._get(call["params"][0])])
                except xmlrpc.client.Fault as err:
                    out.append(
                        {
                            "faultCode": err.faultCode,
                            "faultString": err.faultString,
                        }
                    )
            return out
        assert full == "ticket.milestone.get"
        self.singles.append(params[0])
        return self._get(params[0])


def make_client(mock_config, trac: FakeTrac) -> TracClient:
    client = TracClient(mock_config)
    client._rpc_request = trac.rpc  # type: ignore[method-assign]
    return client


def names(n: int) -> list[str]:
    return [f"v{i}" for i in range(n)]


def test_batch_size_is_fifty():
    assert MILESTONE_FETCH_BATCH == 50


def test_120_names_make_three_multicalls(mock_config):
    all_names = names(120)
    trac = FakeTrac(all_names)
    client = make_client(mock_config, trac)

    result = client.get_milestones(all_names)

    assert result == {n: FakeTrac.milestone(n) for n in all_names}
    assert trac.calls == ["system.multicall"] * 3
    assert [len(b) for b in trac.batches] == [50, 50, 20]
    assert trac.singles == []


def test_the_value_is_what_get_milestone_returns(mock_config):
    trac = FakeTrac(["v1"])
    client = make_client(mock_config, trac)

    assert client.get_milestones(["v1"])["v1"] == client.get_milestone(
        "v1"
    )


def test_an_empty_list_makes_no_rpc(mock_config):
    trac = FakeTrac()
    client = make_client(mock_config, trac)

    assert client.get_milestones([]) == {}
    assert trac.calls == []


def test_duplicate_names_are_fetched_once_in_order(mock_config):
    trac = FakeTrac(["b", "a"])
    client = make_client(mock_config, trac)

    result = client.get_milestones(["b", "a", "b"])

    assert trac.batches == [["b", "a"]]
    assert list(result) == ["b", "a"]


def test_a_per_entry_fault_leaves_the_name_absent(mock_config):
    trac = FakeTrac(["v1", "v3"])
    client = make_client(mock_config, trac)

    result = client.get_milestones(["v1", "v2", "v3"])

    assert list(result) == ["v1", "v3"]
    assert trac.singles == []


def test_without_multicall_the_names_go_singly(mock_config):
    trac = FakeTrac(["v1", "v3"])
    trac.multicall_enabled = False
    client = make_client(mock_config, trac)

    result = client.get_milestones(["v1", "v2", "v3"])

    assert list(result) == ["v1", "v3"]
    assert trac.singles == ["v1", "v2", "v3"]
