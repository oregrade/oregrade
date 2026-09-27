"""oregrade command line.

    oregrade dossier grafana/grafana --at 2017-12-31
    oregrade dossier grafana/grafana --at 2017-12-31 --format markdown
    oregrade dossier grafana/grafana --gharchive --github
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from pathlib import Path

from . import __version__
from .dossier import DEFAULT_CACHE, Options, build
from .render import RENDERERS


def _date(text: str) -> date:
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"{text!r} is not a date. Use YYYY-MM-DD.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="oregrade",
        description="Point-in-time fact sheet for an open source repository. "
                    "No score, no ranking, no verdict.")
    parser.add_argument("--version", action="version",
                        version=f"oregrade {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    dossier = sub.add_parser(
        "dossier", help="assemble a dossier for one repository at one date")
    dossier.add_argument("repo", help="owner/name, e.g. grafana/grafana")
    dossier.add_argument("--at", type=_date, default=date.today(),
                         metavar="YYYY-MM-DD",
                         help="the date, inclusive. Defaults to today.")
    dossier.add_argument("--format", choices=sorted(RENDERERS), default="yaml")
    dossier.add_argument("--out", type=Path,
                         help="write to a file instead of stdout")
    dossier.add_argument("--gharchive", action="store_true",
                         help="add the adoption block. Needs BigQuery "
                              "credentials; costs money on deep history.")
    dossier.add_argument("--github", action="store_true",
                         help="add present-day GitHub metadata. Needs a "
                              "token for anything beyond 60 requests/hour. "
                              "Never point-in-time.")
    dossier.add_argument("--offline", action="store_true",
                         help="no network at all: skip repo-id resolution and "
                              "use an existing clone only")
    dossier.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE)
    dossier.add_argument("--clone-dir", type=Path)
    dossier.add_argument("--token", help="GitHub token; falls back to "
                                         "$GITHUB_TOKEN")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.at > date.today():
        print(f"--at {args.at} is in the future.", file=sys.stderr)
        return 2
    if args.offline and (args.github or args.gharchive):
        print("--offline cannot be combined with --github or --gharchive.",
              file=sys.stderr)
        return 2

    doc = build(args.repo, Options(
        at=args.at, gharchive=args.gharchive, github=args.github,
        cache_dir=args.cache_dir, clone_dir=args.clone_dir,
        token=args.token, offline=args.offline))

    text = RENDERERS[args.format](doc)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text)
        print(f"wrote {args.out}", file=sys.stderr)
    else:
        sys.stdout.write(text)

    return 0 if doc.get("status") == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
