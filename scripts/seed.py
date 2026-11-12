"""Demo data seeding — placeholder until stage 5.

Runs inside the `seed` compose profile. Fails loudly (exit code 1)
instead of pretending to succeed: a green-but-empty database is worse
than a visible failure.
"""

import sys


def main() -> int:
    print("seed not implemented yet", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
