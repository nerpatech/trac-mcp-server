#!/usr/bin/env python3
"""Agent Scenario Tests for trac-mcp-server permission filtering. (v2.0.0)

Tests agent persona scenarios by launching trac-mcp-server with different
permission files and verifying that tool exposure matches expectations.
Optionally runs safe read-only tool calls to validate live connectivity.

Each scenario is defined by a pair of files in scripts/scenarios/:
  - {name}.permissions     -- Trac permissions for the agent persona
  - {name}.expected_tools.txt -- Expected tool list (one per line, sorted)

Usage:
  python scripts/agent_scenarios.py                          # Run all scenarios
  python scripts/agent_scenarios.py --scenarios readonly      # Run one scenario
  python scripts/agent_scenarios.py --update-refs             # Update reference files from live
  python scripts/agent_scenarios.py --live                    # Also run safe tool calls
  python scripts/agent_scenarios.py --verbose                 # Verbose console output
"""

import argparse
import asyncio
import logging
import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import mcp.types as types
from mcp.client.session import ClientSession
from mcp.client.stdio import (
    StdioServerParameters,
    get_default_environment,
    stdio_client,
)

from trac_mcp_server import __version__ as PACKAGE_VERSION

VERSION = "2.0.0"

# Locate the scenarios directory relative to this script
SCRIPT_DIR = Path(__file__).resolve().parent
SCENARIOS_DIR = SCRIPT_DIR / "scenarios"

# Safe read-only tool calls for --live mode.
# Maps tool name to arguments dict. Tools not listed here are skipped.
SAFE_CALLS: dict[str, dict] = {
    "ping": {},
    "get_server_time": {},
    "ticket_search": {},
    "ticket_fields": {},
    "wiki_search": {"query": "wiki"},
    "wiki_recent_changes": {"since_days": 7},
    "milestone_list": {},
    "wiki_file_detect_format": {"file_path": "/dev/null"},
}


@dataclass
class ScenarioResult:
    """Result of a single scenario run."""

    name: str
    passed: bool
    expected_tools: list[str]
    actual_tools: list[str]
    extra_tools: list[str]  # in actual but not expected
    missing_tools: list[str]  # in expected but not actual
    live_results: dict[str, bool] | None = (
        None  # tool_name -> success (if --live)
    )
    error: str | None = None  # if scenario failed to run at all


@dataclass
class SuiteResult:
    """Aggregated results for all scenarios."""

    scenarios: list[ScenarioResult] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.scenarios)

    @property
    def passed(self) -> int:
        return sum(1 for s in self.scenarios if s.passed)

    @property
    def failed(self) -> int:
        return sum(1 for s in self.scenarios if not s.passed)


def discover_scenarios(scenarios_dir: Path) -> list[str]:
    """Discover scenario names from .permissions files in the scenarios directory.

    Returns:
        Sorted list of scenario names (stem of .permissions files that have
        a matching .expected_tools.txt).
    """
    names = []
    for p in sorted(scenarios_dir.glob("*.permissions")):
        expected = scenarios_dir / f"{p.stem}.expected_tools.txt"
        if expected.exists():
            names.append(p.stem)
    return names


def load_expected_tools(path: Path) -> list[str]:
    """Load expected tool names from a reference file.

    Returns:
        Sorted list of tool names (blank lines and whitespace stripped).
    """
    return sorted(
        line.strip()
        for line in path.read_text().splitlines()
        if line.strip()
    )


def write_expected_tools(path: Path, tools: list[str]) -> None:
    """Write tool names to a reference file (one per line, sorted, trailing newline)."""
    path.write_text("\n".join(sorted(tools)) + "\n")


# The Trac connection settings the server reads (config.py). Deliberately
# not every TRAC_* variable: TRAC_MCP_* settings choose the transport and
# the tools offered, and must not leak from the caller's shell into the
# subprocess this script compares against a reference.
CONNECTION_ENV_VARS = frozenset(
    {
        "TRAC_URL",
        "TRAC_USERNAME",
        "TRAC_PASSWORD",
        "TRAC_INSECURE",
        "TRAC_RPC_TIMEOUT",
    }
)


def build_server_params(
    args: argparse.Namespace, environ: Mapping[str, str]
) -> StdioServerParameters:
    """Launch parameters for the ``trac-mcp-server`` subprocess.

    ``--url``, ``--username`` and ``--insecure`` go on the command line.
    The password never does (it would show in ``ps``); it reaches the
    server as ``TRAC_PASSWORD`` in its environment. The subprocess gets
    the MCP SDK's default environment plus the connection variables in
    ``CONNECTION_ENV_VARS`` found in ``environ``: with ``env`` unset the
    SDK passes only its defaults, so an exported ``TRAC_URL`` or
    ``TRAC_PASSWORD`` would not arrive. Other ``TRAC_*`` variables are
    not passed, ``TRAC_MCP_*`` above all.
    """
    server_args: list[str] = []
    if args.url:
        server_args.extend(["--url", args.url])
    if args.username:
        server_args.extend(["--username", args.username])
    if args.insecure:
        server_args.append("--insecure")

    env = get_default_environment()
    env.update(
        {k: v for k, v in environ.items() if k in CONNECTION_ENV_VARS}
    )
    return StdioServerParameters(
        command="trac-mcp-server", args=server_args, env=env
    )


async def run_scenario(
    name: str,
    permissions_path: Path,
    expected_tools_path: Path,
    base_params: StdioServerParameters,
    live: bool = False,
    update_refs: bool = False,
    logger: logging.Logger | None = None,
) -> ScenarioResult:
    """Run a single scenario: connect, list tools, compare, optionally run calls.

    Args:
        name: Scenario name (e.g., "readonly").
        permissions_path: Absolute path to the .permissions file.
        expected_tools_path: Absolute path to the .expected_tools.txt file.
        base_params: Server launch parameters from build_server_params();
            --permissions-file for this scenario is appended to its args.
        live: If True, also run safe read-only tool calls.
        update_refs: If True, write actual tools back to expected_tools.txt.
        logger: Optional logger for debug output.

    Returns:
        ScenarioResult with comparison data.
    """
    log = logger or logging.getLogger(__name__)

    # Build server command with --permissions-file
    server_params = base_params.model_copy(
        update={
            "args": list(base_params.args)
            + ["--permissions-file", str(permissions_path)]
        }
    )

    try:
        async with stdio_client(server_params) as (
            read_stream,
            write_stream,
        ):
            async with ClientSession(
                read_stream, write_stream
            ) as session:
                init_result = await session.initialize()
                log.debug(
                    "Scenario %s: connected to %s v%s",
                    name,
                    init_result.serverInfo.name,
                    init_result.serverInfo.version,
                )

                # List tools
                tools_result = await session.list_tools()
                actual_tools = sorted(
                    t.name for t in tools_result.tools
                )

                # Update reference files if requested
                if update_refs:
                    write_expected_tools(
                        expected_tools_path, actual_tools
                    )
                    log.info(
                        "Scenario %s: updated reference file with %d tools",
                        name,
                        len(actual_tools),
                    )
                    return ScenarioResult(
                        name=name,
                        passed=True,
                        expected_tools=actual_tools,
                        actual_tools=actual_tools,
                        extra_tools=[],
                        missing_tools=[],
                    )

                # Load expected tools
                expected_tools = load_expected_tools(
                    expected_tools_path
                )

                # Compare
                actual_set = set(actual_tools)
                expected_set = set(expected_tools)
                extra = sorted(actual_set - expected_set)
                missing = sorted(expected_set - actual_set)
                passed = actual_set == expected_set

                # Live tool calls
                live_results: dict[str, bool] | None = None
                if live:
                    live_results = {}
                    for tool_name in actual_tools:
                        if tool_name not in SAFE_CALLS:
                            continue
                        call_args = SAFE_CALLS[tool_name]
                        try:
                            result = await session.call_tool(
                                tool_name, call_args
                            )
                            # Extract text to check for error indicators
                            text = "\n".join(
                                c.text
                                for c in result.content
                                if isinstance(c, types.TextContent)
                            )
                            is_ok = (
                                not result.isError
                                and "error_type" not in text.lower()
                            )
                            live_results[tool_name] = is_ok
                            log.debug(
                                "Scenario %s: %s call %s -> %s",
                                name,
                                tool_name,
                                call_args,
                                "OK" if is_ok else "FAIL",
                            )
                        except Exception as e:
                            live_results[tool_name] = False
                            log.warning(
                                "Scenario %s: %s call failed: %s",
                                name,
                                tool_name,
                                e,
                            )

                return ScenarioResult(
                    name=name,
                    passed=passed,
                    expected_tools=expected_tools,
                    actual_tools=actual_tools,
                    extra_tools=extra,
                    missing_tools=missing,
                    live_results=live_results,
                )

    except Exception as e:
        log.error("Scenario %s: fatal error: %s", name, e)
        return ScenarioResult(
            name=name,
            passed=False,
            expected_tools=load_expected_tools(expected_tools_path)
            if expected_tools_path.exists()
            else [],
            actual_tools=[],
            extra_tools=[],
            missing_tools=[],
            error=str(e),
        )


async def async_main(args: argparse.Namespace) -> int:
    """Async entry point: discover and run scenarios."""
    # Build debug log path
    if args.timestamp:
        timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_filename = f"agent_scenarios_debug_{timestamp_str}.log"
    else:
        log_filename = "agent_scenarios_debug.log"
    log_path = Path(".") / log_filename

    # Delete previous log (only when not using timestamp)
    if not args.timestamp and log_path.exists():
        log_path.unlink()

    logger = setup_logging(str(log_path), verbose=args.verbose)

    # Print header
    print(f"\n{'=' * 70}")
    print(f"{'Agent Scenario Tests':^70}")
    print(f"{'v' + VERSION:^70}")
    print(f"{'trac-mcp-server ' + PACKAGE_VERSION:^70}")
    print(f"{'=' * 70}\n")
    print(f"Debug log: {log_path}")

    # Discover scenarios
    all_scenarios = discover_scenarios(SCENARIOS_DIR)
    if not all_scenarios:
        print(f"No scenarios found in {SCENARIOS_DIR}")
        return 1

    # Filter scenarios if --scenarios specified
    if args.scenarios:
        selected = []
        for s in args.scenarios:
            if s in all_scenarios:
                selected.append(s)
            else:
                print(
                    f"Warning: scenario '{s}' not found (available: {', '.join(all_scenarios)})"
                )
        if not selected:
            print("No valid scenarios selected.")
            return 1
        scenarios_to_run = selected
    else:
        scenarios_to_run = all_scenarios

    print(f"Scenarios: {', '.join(scenarios_to_run)}")
    if args.update_refs:
        print(
            "Mode: --update-refs (writing live tool lists to reference files)"
        )
    elif args.live:
        print("Mode: --live (comparing + running safe tool calls)")
    else:
        print("Mode: compare (list_tools vs reference)")
    print()

    # Build server launch parameters from CLI options and the environment
    base_params = build_server_params(args, os.environ)

    # Run each scenario
    suite = SuiteResult()
    for name in scenarios_to_run:
        permissions_path = SCENARIOS_DIR / f"{name}.permissions"
        expected_tools_path = (
            SCENARIOS_DIR / f"{name}.expected_tools.txt"
        )

        print(f"--- Scenario: {name} ---")
        logger.info("Running scenario: %s", name)

        result = await run_scenario(
            name=name,
            permissions_path=permissions_path,
            expected_tools_path=expected_tools_path,
            base_params=base_params,
            live=args.live,
            update_refs=args.update_refs,
            logger=logger,
        )
        suite.scenarios.append(result)

        # Print result
        if result.error:
            print(f"  ERROR: {result.error}")
        elif args.update_refs:
            print(
                f"  UPDATED: {len(result.actual_tools)} tools written to {expected_tools_path.name}"
            )
        elif result.passed:
            print(
                f"  PASS: {len(result.actual_tools)} tools match expected"
            )
        else:
            print("  FAIL: tool list mismatch")
            if result.extra_tools:
                print(
                    f"    Extra (in server, not in reference): {', '.join(result.extra_tools)}"
                )
            if result.missing_tools:
                print(
                    f"    Missing (in reference, not in server): {', '.join(result.missing_tools)}"
                )

        # Print live results if any
        if result.live_results:
            live_ok = sum(1 for v in result.live_results.values() if v)
            live_total = len(result.live_results)
            print(f"  Live calls: {live_ok}/{live_total} succeeded")
            if args.verbose:
                for tool_name, ok in sorted(
                    result.live_results.items()
                ):
                    status = "OK" if ok else "FAIL"
                    print(f"    [{status}] {tool_name}")

        print()

    # Print summary
    print(f"{'=' * 70}")
    print(f"{'SUMMARY':^70}")
    print(f"{'=' * 70}")
    print(
        f"Scenarios: {suite.total} | Passed: {suite.passed} | Failed: {suite.failed}"
    )

    if suite.failed > 0:
        print("\nFailed scenarios:")
        for s in suite.scenarios:
            if not s.passed:
                if s.error:
                    print(f"  - {s.name}: {s.error}")
                else:
                    extra_str = (
                        f" +{len(s.extra_tools)}"
                        if s.extra_tools
                        else ""
                    )
                    missing_str = (
                        f" -{len(s.missing_tools)}"
                        if s.missing_tools
                        else ""
                    )
                    print(
                        f"  - {s.name}: mismatch{extra_str}{missing_str}"
                    )

    return 0 if suite.failed == 0 else 1


def setup_logging(
    log_file: str | None, verbose: bool = False
) -> logging.Logger:
    """Set up logging for scenario runner."""
    logger = logging.getLogger("AgentScenarios")
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)

    if log_file:
        fh = logging.FileHandler(log_file)
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(
            logging.Formatter(
                "%(asctime)s - %(levelname)s - %(message)s"
            )
        )
        logger.addHandler(fh)

    if verbose:
        ch = logging.StreamHandler()
        ch.setLevel(logging.DEBUG)
        ch.setFormatter(
            logging.Formatter("%(levelname)s - %(message)s")
        )
        logger.addHandler(ch)

    return logger


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Agent Scenario Tests for trac-mcp-server permission filtering",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s                                    # Run all scenarios
  %(prog)s --scenarios readonly wiki_editor   # Run specific scenarios
  %(prog)s --update-refs                      # Update reference files from live server
  %(prog)s --live                             # Also run safe read-only tool calls
  %(prog)s --live --verbose                   # Verbose with per-tool call results
  %(prog)s --url http://trac.example.com      # Override Trac URL
  %(prog)s --timestamp                        # Keep debug logs with timestamp
        """,
    )

    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {VERSION}"
    )
    parser.add_argument(
        "--url", help="Override Trac URL (passed to trac-mcp-server)"
    )
    parser.add_argument(
        "--username",
        help="Override username (passed to trac-mcp-server)",
    )
    parser.add_argument(
        "--insecure",
        action="store_true",
        help="Skip SSL verification (passed to trac-mcp-server)",
    )
    parser.add_argument(
        "--scenarios",
        nargs="+",
        metavar="NAME",
        default=None,
        help="Run only these scenarios (space-separated names, e.g., --scenarios readonly wiki_editor)",
    )
    parser.add_argument(
        "--update-refs",
        action="store_true",
        help="Update expected_tools.txt reference files from live server output (overwrite mode)",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Also run safe read-only tool calls to verify connectivity (not just list_tools comparison)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Verbose console output",
    )
    parser.add_argument(
        "--timestamp",
        action="store_true",
        help="Include timestamp in debug log filename (prevents overwrite on next run)",
    )

    return parser


def main():
    args = build_parser().parse_args()
    sys.exit(asyncio.run(async_main(args)))


if __name__ == "__main__":
    main()
