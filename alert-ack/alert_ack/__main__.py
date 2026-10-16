import sys

from .backend import main
from .ledger import cli

sys.exit(cli(sys.argv[1:]) if sys.argv[1:2] == ["deliveries"] else main())
