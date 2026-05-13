"""Allow ``python -m lfp_aligner`` invocation."""
from .cli import main
import sys
sys.exit(main())
