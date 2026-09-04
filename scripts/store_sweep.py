#!/usr/bin/env python3
"""Replay ``build_warnings`` over a whole Trac store (ticket #79).

The recall gate ticket #79 section 6 asks for: every wiki page, ticket
description and ticket comment on a store, fetched with its stored
TracWiki source and the HTML Trac renders for it, then run through the
same assembly ``convert_preview`` runs on a TracWiki-declared write.

Two phases, deliberately separate:

``fetch``
    One pass over the XML-RPC API, writing every document to a local
    cache directory. Slow, and the only phase that needs a server.

``replay``
    Pure, offline, and repeatable against that cache. Run it once on
    the current tree and once on a stashed baseline to compare -- the
    same corpus both times, per ``Rules/testing/PreserveTheBaseline``.
    Regenerating the corpus between the two runs would destroy the
    thing being compared.

THE CACHE IS NOT CHECKED IN, and must not be. This repository is
public; the ``auto_pm`` store carries internal host addresses and
home-directory paths. So this harness is checked in and the corpus it
reads is produced locally, which is why the 169-authored-findings gate
is reproduced by running this rather than by a fixture.

Usage:

    python scripts/store_sweep.py fetch  --cache ~/.cache/trac-sweep
    python scripts/store_sweep.py replay --cache ~/.cache/trac-sweep

``replay --json FILE`` writes the tallies for a mechanical diff against
another run.
"""

import argparse
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from trac_mcp_server.config import Config  # noqa: E402
from trac_mcp_server.core.client import TracClient  # noqa: E402
from trac_mcp_server.preview.checks import (  # noqa: E402
    build_warnings,
)
from trac_mcp_server.preview.facts import extract_facts  # noqa: E402
from trac_mcp_server.preview.gate import is_blocking  # noqa: E402

#: The two stores ticket #79 section 3 measured. Named by Trac instance
#: path; the base URL comes from TRAC_URL with its path replaced, so one
#: set of credentials reaches both.
DEFAULT_INSTANCES = ("auto_pm", "trac_mcp_server")


def _credentials() -> tuple[str, str, str]:
    """Read TRAC_URL/USERNAME/PASSWORD, .env included.

    The fetch phase is the only one that needs them, and it used to read
    ``os.environ`` directly and die with a bare ``KeyError: 'TRAC_URL'``.
    Sourcing .env from a shell is not a workaround: this project's .env
    carries ``TRAC_URL= http://...`` with a leading space, which
    python-dotenv strips and ``.  ./.env`` does not -- it assigns the
    empty string and then tries to run the URL as a command.
    """
    load_dotenv()
    missing = [
        name
        for name in ("TRAC_URL", "TRAC_USERNAME", "TRAC_PASSWORD")
        if not os.environ.get(name)
    ]
    if missing:
        raise SystemExit(
            "store_sweep: live Trac credentials are not available. "
            f"Missing: {', '.join(missing)}. Set them in .env (see "
            ".env.example) or export them; the fetch phase cannot run "
            "without them."
        )
    return (
        os.environ["TRAC_URL"].strip().rstrip("/"),
        os.environ["TRAC_USERNAME"].strip(),
        os.environ["TRAC_PASSWORD"].strip(),
    )


def _client(instance: str) -> TracClient:
    url, username, password = _credentials()
    base = url.rsplit("/", 1)[0]
    return TracClient(
        Config(
            trac_url=f"{base}/{instance}",
            username=username,
            password=password,
        )
    )


def _render(client: TracClient, source: str) -> str | None:
    """Trac's HTML for ``source``, or None if it could not be rendered.

    An empty body is skipped rather than sent: ``wiki.wikiToHtml``
    faults on it. A fault on a non-empty body is REPORTED by the
    caller rather than swallowed -- a document silently dropped from
    the corpus is a document silently dropped from the recall gate.
    """
    if not source.strip():
        return ""
    return client.wiki_to_html(source)


def _write(cache: Path, instance: str, ref: str, doc: dict) -> None:
    out = cache / instance
    out.mkdir(parents=True, exist_ok=True)
    safe = ref.replace("/", "__")
    (out / f"{safe}.json").write_text(json.dumps(doc))


def _store(
    cache: Path,
    client: TracClient,
    instance: str,
    failed: list[str],
    ref: str,
    kind: str,
    label: str,
    source: str | None,
) -> bool:
    """Cache one document, or record why it could not be cached."""
    source = source or ""
    try:
        html = _render(client, source)
    except Exception as exc:  # noqa: BLE001
        failed.append(f"{label}: {exc}")
        return False
    _write(
        cache,
        instance,
        ref,
        {"kind": kind, "ref": label, "source": source, "html": html},
    )
    return True


def _fetch_instance(cache: Path, instance: str) -> None:
    client = _client(instance)
    failed: list[str] = []
    count = 0

    for page in client.list_wiki_pages():
        if _store(
            cache,
            client,
            instance,
            failed,
            f"wiki:{page}",
            "wiki",
            page,
            client.get_wiki_page(page),
        ):
            count += 1

    # `ticket.query` returns bare ids, so each ticket is fetched for
    # its description -- (id, created, changed, attributes).
    for tid in client.search_tickets("max=0&order=id"):
        ticket = client.get_ticket(tid)
        if _store(
            cache,
            client,
            instance,
            failed,
            f"ticket:{tid}",
            "description",
            f"#{tid}",
            (ticket[3] or {}).get("description", ""),
        ):
            count += 1

        # A comment EDIT appears in the changelog as a second entry
        # with the same comment number, so the entries have to be
        # collapsed to the latest text per number before they are
        # cached. Writing them all cost 10 documents to filename
        # collisions on the first run of this script -- silently, which
        # is the failure mode a recall gate can least afford.
        latest: dict[str, str] = {}
        for entry in client.get_ticket_changelog(tid):
            # (time, author, field, oldvalue, newvalue, permanent)
            if entry[2] != "comment":
                continue
            latest[str(entry[3] or "?")] = entry[4] or ""
        for num, body in latest.items():
            if not body.strip():
                continue
            if _store(
                cache,
                client,
                instance,
                failed,
                f"comment:{tid}:{num}",
                "comment",
                f"#{tid} comment {num}",
                body,
            ):
                count += 1

    # Reported from the filesystem, not from the loop counter: the two
    # agreeing is what proves no document was overwritten on its way in.
    written = len(list((cache / instance).glob("*.json")))
    print(
        f"{instance}: cached {count} documents ({written} files)",
        file=sys.stderr,
    )
    if written != count:
        print(
            f"{instance}: MISMATCH -- {count - written} document(s) "
            "overwrote another; the corpus is not what it reports.",
            file=sys.stderr,
        )
    if failed:
        print(
            f"{instance}: {len(failed)} NOT cached -- the gate is "
            f"incomplete by that many documents:",
            file=sys.stderr,
        )
        for line in failed:
            print(f"  {line}", file=sys.stderr)


def fetch(cache: Path, instances: tuple[str, ...]) -> None:
    for instance in instances:
        _fetch_instance(cache, instance)


# Whether a finding refuses a write is asked of `preview.gate`, never
# re-listed here. This script used to carry its own copy of ticket #64
# section 4's error column, and by the time the policy became real code
# that copy was already wrong in two places: it omitted
# `escaped_link_target` (ruling 1) and `target_check_capped` (ruling 2),
# so it would have reported a refusal count for a gate nobody was
# shipping. A second copy of a rule is a rule that goes stale silently,
# and a measurement taken against the stale copy is worse than no
# measurement -- it looks like evidence.


def replay(cache: Path, instances: tuple[str, ...]) -> dict:
    report: dict = {"instances": {}, "totals": {}}
    grand_codes: dict[str, int] = {}
    grand_docs = 0
    grand_refused = 0

    for instance in instances:
        directory = cache / instance
        if not directory.is_dir():
            continue
        codes: dict[str, int] = {}
        docs = 0
        refused = 0
        examples: dict[str, list[str]] = {}

        for path in sorted(directory.glob("*.json")):
            doc = json.loads(path.read_text())
            warnings = build_warnings(
                markdown_source=None,
                tracwiki=doc["source"],
                facts=extract_facts(doc["html"]),
                probes={},
                check_targets=False,
                source_format="tracwiki",
            )
            docs += 1
            found = {w["code"] for w in warnings}
            for warning in warnings:
                codes[warning["code"]] = (
                    codes.get(warning["code"], 0) + 1
                )
            # Sorted, because `found` is a set and its iteration order
            # varies with the interpreter's hash seed -- which put the
            # `examples` keys in a different order on every run and made
            # a `diff` of two --json reports show changes where there
            # were none. This script exists to be diffed mechanically.
            for code in sorted(found):
                examples.setdefault(code, [])
                if len(examples[code]) < 5:
                    examples[code].append(doc["ref"])
            if any(is_blocking(w) for w in warnings):
                refused += 1

        report["instances"][instance] = {
            "documents": docs,
            "documents_refused": refused,
            "codes": codes,
            "examples": examples,
        }
        grand_docs += docs
        grand_refused += refused
        for code, n in codes.items():
            grand_codes[code] = grand_codes.get(code, 0) + n

    report["totals"] = {
        "documents": grand_docs,
        "documents_refused": grand_refused,
        "codes": grand_codes,
    }
    return report


def _print(report: dict) -> None:
    for instance, data in report["instances"].items():
        print(
            f"\n{instance}: {data['documents']} documents, "
            f"{data['documents_refused']} would be refused"
        )
        for code, n in sorted(
            data["codes"].items(), key=lambda kv: -kv[1]
        ):
            print(f"  {n:6d}  {code}")
    totals = report["totals"]
    print(
        f"\nTOTAL: {totals['documents']} documents, "
        f"{totals['documents_refused']} would be refused"
    )
    for code, n in sorted(
        totals["codes"].items(), key=lambda kv: -kv[1]
    ):
        print(f"  {n:6d}  {code}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("fetch", "replay"))
    parser.add_argument("--cache", required=True, type=Path)
    parser.add_argument(
        "--instances",
        default=",".join(DEFAULT_INSTANCES),
        help="Comma-separated Trac instance paths.",
    )
    parser.add_argument(
        "--json",
        type=Path,
        help="Write the replay tallies here for a mechanical diff.",
    )
    args = parser.parse_args()
    instances = tuple(
        i.strip() for i in args.instances.split(",") if i.strip()
    )

    if args.phase == "fetch":
        fetch(args.cache, instances)
        return 0

    report = replay(args.cache, instances)
    _print(report)
    if args.json:
        args.json.write_text(json.dumps(report, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
