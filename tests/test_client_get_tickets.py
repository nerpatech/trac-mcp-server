"""TracClient.get_tickets: ticket.get through system.multicall batches
(Trac /trac_mcp_server #158).

``FakeTrac`` stands in for the XML-RPC endpoint, installed as the
client's ``_rpc_request`` the way tests/test_wiki_search_client.py does
for the wiki, so the batching, the fault handling and the fallback to
single gets are the real code.
"""

import os
import xmlrpc.client

import pytest

from trac_mcp_server.core.client import TICKET_FETCH_BATCH, TracClient

MISSING_METHOD = xmlrpc.client.Fault(
    -32601, 'RPC method "system.multicall" not found'
)


class FakeTrac:
    def __init__(self, existing=()):
        self.existing = set(existing)
        self.multicall_enabled = True
        self.multicall_result = None  # returned instead of real answers
        self.failing: set[int] = set()  # ticket.get raises a Fault
        self.calls: list[str] = []  # top-level methods, in order
        self.batches: list[list[int]] = []  # ids per multicall
        self.singles: list[int] = []  # ids read by plain ticket.get

    @staticmethod
    def ticket(tid: int) -> list:
        return [tid, "t", "c", {"summary": f"T{tid}"}]

    def _get(self, tid: int) -> list:
        if tid in self.failing or tid not in self.existing:
            raise xmlrpc.client.Fault(
                1, f"Ticket {tid} does not exist."
            )
        return self.ticket(tid)

    def rpc(self, service: str, method: str, *params):
        full = f"{service}.{method}"
        self.calls.append(full)
        if full == "system.multicall":
            if not self.multicall_enabled:
                raise MISSING_METHOD
            calls = params[0]
            assert all(c["methodName"] == "ticket.get" for c in calls)
            self.batches.append([c["params"][0] for c in calls])
            if self.multicall_result is not None:
                return self.multicall_result
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
        assert full == "ticket.get"
        self.singles.append(params[0])
        return self._get(params[0])


def make_client(mock_config, trac: FakeTrac) -> TracClient:
    client = TracClient(mock_config)
    client._rpc_request = trac.rpc  # type: ignore[method-assign]
    return client


def fail_second_multicall(client, trac, error):
    """Wrap the fake so the second ``system.multicall`` raises."""
    real = trac.rpc
    seen: list[int] = []

    def rpc(service, method, *params):
        if f"{service}.{method}" == "system.multicall":
            seen.append(1)
            if len(seen) == 2:
                trac.calls.append("system.multicall")
                raise error
        return real(service, method, *params)

    client._rpc_request = rpc  # type: ignore[method-assign]
    return seen


def expected(ids):
    return {tid: FakeTrac.ticket(tid) for tid in ids}


def test_batch_size_is_fifty():
    assert TICKET_FETCH_BATCH == 50


def test_120_ids_make_three_multicalls(mock_config):
    ids = list(range(1, 121))
    trac = FakeTrac(ids)
    client = make_client(mock_config, trac)

    assert client.get_tickets(ids) == expected(ids)
    assert trac.calls == ["system.multicall"] * 3
    assert [len(b) for b in trac.batches] == [50, 50, 20]
    assert trac.batches[0] == ids[:50]
    assert trac.singles == []


def test_the_value_is_what_get_ticket_returns(mock_config):
    trac = FakeTrac([7])
    client = make_client(mock_config, trac)

    assert client.get_tickets([7])[7] == client.get_ticket(7)


def test_an_empty_list_makes_no_rpc(mock_config):
    trac = FakeTrac()
    client = make_client(mock_config, trac)

    assert client.get_tickets([]) == {}
    assert trac.calls == []


def test_duplicate_ids_are_fetched_once_in_order(mock_config):
    trac = FakeTrac([5, 3, 9])
    client = make_client(mock_config, trac)

    result = client.get_tickets([5, 3, 5, 9, 3])

    assert trac.batches == [[5, 3, 9]]
    assert list(result) == [5, 3, 9]


def test_a_per_entry_fault_leaves_the_id_absent_without_retry(
    mock_config,
):
    trac = FakeTrac([1, 2, 3])
    trac.failing = {2}
    client = make_client(mock_config, trac)

    result = client.get_tickets([1, 2, 3, 999])

    assert result == expected([1, 3])
    assert trac.calls == ["system.multicall"]
    assert trac.singles == []


def test_a_missing_multicall_is_remembered_and_the_rest_goes_singly(
    mock_config,
):
    ids = list(range(1, 121))
    trac = FakeTrac(ids)
    trac.multicall_enabled = False
    client = make_client(mock_config, trac)

    assert client.get_tickets(ids) == expected(ids)
    assert trac.calls.count("system.multicall") == 1
    assert trac.singles == ids

    trac.calls.clear()
    trac.singles.clear()
    assert client.get_tickets([1, 2]) == expected([1, 2])
    assert trac.calls == ["ticket.get", "ticket.get"]


def test_a_missing_multicall_after_a_good_batch_fetches_the_rest_singly(
    mock_config,
):
    ids = list(range(1, 121))
    trac = FakeTrac(ids)
    client = make_client(mock_config, trac)
    seen = fail_second_multicall(client, trac, MISSING_METHOD)

    assert client.get_tickets(ids) == expected(ids)
    assert len(seen) == 2  # no third multicall
    assert trac.singles == ids[50:]


def test_a_missing_multicall_is_one_memory_for_wiki_and_tickets(
    mock_config,
):
    trac = FakeTrac([1])
    trac.multicall_enabled = False
    client = make_client(mock_config, trac)

    client.get_tickets([1])

    assert not client._multicall.available()


@pytest.mark.parametrize(
    "error",
    [
        xmlrpc.client.Fault(1, "boom"),
        xmlrpc.client.Fault(1, "no such page: system.multicall"),
        RuntimeError("connection dropped"),
        TimeoutError("timed out"),
    ],
)
def test_any_other_failure_sends_that_batch_singly(mock_config, error):
    ids = list(range(1, 121))
    trac = FakeTrac(ids)
    client = make_client(mock_config, trac)
    seen = fail_second_multicall(client, trac, error)

    assert client.get_tickets(ids) == expected(ids)
    assert len(seen) == 3  # batch 3 still went by multicall
    assert trac.singles == ids[50:100]
    assert client._multicall.available()


@pytest.mark.parametrize(
    "malformed",
    ["nope", [], [[FakeTrac.ticket(1)]] * 3, {"faultCode": 1}],
)
def test_a_malformed_result_sends_that_batch_singly(
    mock_config, malformed
):
    trac = FakeTrac([1, 2])
    trac.multicall_result = malformed
    client = make_client(mock_config, trac)

    assert client.get_tickets([1, 2]) == expected([1, 2])
    assert trac.singles == [1, 2]
    assert client._multicall.available()


def test_single_gets_skip_failures(mock_config):
    trac = FakeTrac([1, 3])
    trac.multicall_enabled = False
    client = make_client(mock_config, trac)

    assert client.get_tickets([1, 2, 3]) == expected([1, 3])


@pytest.mark.live
def test_live_get_tickets_matches_get_ticket_on_trac_test():
    """Live, read-only: ``get_tickets`` on /trac_test returns what
    ``get_ticket`` returns for the tickets that exist and leaves a
    nonexistent id out.

    Requires --run-live and TRAC_URL/USERNAME/PASSWORD env vars.
    """
    from tests.live_helpers import TEST_INSTANCE, trac_test_registry
    from trac_mcp_server.config import Config

    config = Config(
        trac_url=os.environ["TRAC_URL"],
        username=os.environ["TRAC_USERNAME"],
        password=os.environ["TRAC_PASSWORD"],
        insecure=os.environ.get("TRAC_INSECURE", "").lower()
        in ("1", "true", "yes"),
    )
    client = trac_test_registry(config).get_client(TEST_INSTANCE)

    found = list(client.search_tickets("max=3&order=id"))
    assert found, "/trac_test has no tickets to read"
    missing = 999999

    result = client.get_tickets([*found, missing])

    assert missing not in result
    assert sorted(result) == sorted(found)
    for tid in found:
        assert result[tid] == client.get_ticket(tid)
